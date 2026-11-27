// Luces para rkbx-practise-tool en un ESP32 (ESP32-WROOM-32D / "ESP32D").
//
// Recibe comandos JSON por UDP (docs/protocolo_luces.md) y los ejecuta en 3 tiras × 3 secciones.
//   · Recepción: AsyncUDP (por eventos, en la tarea de red) → análisis con ArduinoJson → cola.
//   · Dibujo: en loop() (otro núcleo) se vacía la cola, se calcula cada LED (lights_core) y
//     FastLED lo envía por RMT a las 3 tiras.
//   · Red: Wi-Fi de casa si está configurada y conecta; si no, Wi-Fi propia (config.h).
//   · Wi-Fi sin ahorro de energía (el ahorro añade decenas de ms de latencia).
// También acepta los mismos JSON por el puerto serie (una línea por comando) para probar sin Wi-Fi.
#include <Arduino.h>
#include <ArduinoJson.h>
#include <AsyncUDP.h>
#include <ESPmDNS.h>
#include <Preferences.h>
#include <FastLED.h>
#include <WiFi.h>
#include <esp_wifi.h>

#include "config.h"
#include "lights_core.h"

static_assert(sizeof(CRGB) == sizeof(rl::RGB), "CRGB y rl::RGB deben tener la misma forma (r, g, b)");
static_assert(DEFAULT_STRIPS >= 1 && DEFAULT_STRIPS <= rl::MAX_STRIPS, "DEFAULT_STRIPS entre 1 y 4");

// Búferes de tamaño máximo en los 4 canales; cada controlador envía solo los píxeles del montaje.
static CRGB leds[rl::MAX_STRIPS][rl::MAX_PIXELS];
static rl::RGB* ledPtrs[rl::MAX_STRIPS];
static CLEDController* controllers[rl::MAX_STRIPS];
static uint8_t hwPins[rl::MAX_STRIPS];      // pin de cada controlador creado al arrancar
static int hwStrips = 0;                    // controladores creados (no se pueden quitar en marcha)
static uint32_t restartAt = 0;              // reinicio pendiente para aplicar pines nuevos
static const uint8_t kDefaultPins[rl::MAX_STRIPS] = DEFAULT_PINS;
// Copia del montaje para interpretar «zones» en la tarea de red (se actualiza desde loop()).
static rl::StripLayout zoneLayout[rl::MAX_STRIPS];
static volatile int zoneLayoutN = 0;
static Preferences prefs;
static rl::Engine engine;
static AsyncUDP udp;
static QueueHandle_t commandQueue;

static volatile uint32_t packetsReceived = 0, packetsInvalid = 0, packetsDropped = 0;

// Registro de comandos en el monitor serie (se escribe desde loop(), nunca desde la tarea de red).
// 0 = nada · 1 = comandos (sin «sync») · 2 = todo. Se cambia escribiendo «v» en el monitor.
static volatile uint8_t logLevel = LOG_COMMANDS;
struct LogLine {
  uint32_t ms;
  bool ok;
  char text[120];
};
static QueueHandle_t logQueue;
static uint32_t statusLedOffAt = 0;

// ---------------------------------------------------------------------------
// JSON → rl::Command
// ---------------------------------------------------------------------------
static bool readColor(JsonVariantConst v, rl::RGB& out) {
  if (v.is<const char*>()) return rl::parseColor(v.as<const char*>(), out);
  if (v.is<JsonArrayConst>()) {
    JsonArrayConst a = v.as<JsonArrayConst>();
    if (a.size() < 3) return false;
    out = rl::RGB{(uint8_t)(a[0] | 0), (uint8_t)(a[1] | 0), (uint8_t)(a[2] | 0)};
    return true;
  }
  return false;
}

static bool parseCommand(const char* data, size_t len, rl::Command& c) {
  JsonDocument doc;
  if (deserializeJson(doc, data, len)) return false;
  const char* fx = doc["fx"] | "";
  if (!*fx) return false;
  c = rl::Command();
  c.fx = rl::parseFx(fx);
  c.hasColor = readColor(doc["color"], c.color);
  c.hasColor2 = readColor(doc["color2"], c.color2);
  c.bpm = doc["bpm"] | 0.0f;                 // "live" ya llega resuelto desde el PC
  c.beats = doc["beats"] | 0.0f;
  c.hz = doc["hz"] | 0.0f;
  c.speed = doc["speed"] | 0.0f;
  c.density = doc["density"] | 0.0f;
  c.durMs = doc["dur_ms"] | (doc["duration_ms"] | 0u);
  c.periodMs = doc["period_ms"] | 0u;
  if (doc["phase"].is<float>()) c.phase = doc["phase"].as<float>();
  JsonVariantConst z = doc["zones"];
  if (z.is<const char*>()) {
    c.zoneMask = rl::parseZones(z.as<const char*>(), zoneLayout, zoneLayoutN);
  } else if (z.is<JsonArrayConst>()) {
    int list[rl::MAX_ZONES];
    int n = 0;
    for (JsonVariantConst v : z.as<JsonArrayConst>())
      if (n < rl::MAX_ZONES) list[n++] = v | 0;
    c.zoneMask = rl::zonesFromList(list, n, engine.zoneCount());
  } else if (z.is<int>()) {
    int one = z.as<int>();
    c.zoneMask = rl::zonesFromList(&one, 1, engine.zoneCount());
  }
  if (c.fx == rl::Fx::Layout) {
    // {"fx":"layout","strips":[{"leds":14,"segments":3}, …]}
    JsonArrayConst arr = doc["strips"].as<JsonArrayConst>();
    for (JsonVariantConst v : arr) {
      if (c.layoutCount >= rl::MAX_STRIPS) break;
      int px = v["leds"] | (v["pixels"] | 0);
      int seg = v["segments"] | 1;
      int pin = v["pin"] | 0;
      c.layout[c.layoutCount++] = rl::StripLayout{(uint16_t)(px < 0 ? 0 : px), (uint8_t)(seg < 0 ? 0 : seg),
                                                  (uint8_t)(pin < 0 ? 0 : pin)};
    }
    return c.layoutCount > 0;
  }
  return c.zoneMask != 0;
}

static void logPacket(const char* data, size_t len, bool ok, const rl::Command& c) {
  if (logLevel == 0 || !logQueue) return;
  if (ok && c.fx == rl::Fx::Sync && logLevel < 2) return;
  LogLine l;
  l.ms = millis();
  l.ok = ok;
  size_t n = len < sizeof(l.text) - 1 ? len : sizeof(l.text) - 1;
  memcpy(l.text, data, n);
  l.text[n] = 0;
  xQueueSend(logQueue, &l, 0);                // si la cola está llena se pierde la línea, no el comando
}

static void enqueue(const char* data, size_t len) {
  rl::Command c;
  ++packetsReceived;
  bool ok = parseCommand(data, len, c);
  logPacket(data, len, ok, c);
  if (!ok) {
    ++packetsInvalid;
    return;
  }
  if (xQueueSend(commandQueue, &c, 0) != pdTRUE) ++packetsDropped;
}

static void printLog() {
  LogLine l;
  while (logQueue && xQueueReceive(logQueue, &l, 0) == pdTRUE)
    Serial.printf("[%lu.%03lu] %s%s\n", (unsigned long)(l.ms / 1000), (unsigned long)(l.ms % 1000),
                  l.ok ? "" : "INVÁLIDO: ", l.text);
}

// ---------------------------------------------------------------------------
// Red
// ---------------------------------------------------------------------------
static bool stationMode = false;

static bool connectStation() {
  if (sizeof(WIFI_STA_SSID) <= 1) {
    Serial.println("Sin Wi-Fi de casa configurada (include/secrets.h)");
    return false;
  }
  WiFi.mode(WIFI_STA);
  WiFi.setHostname(HOSTNAME);
#ifdef WIFI_STA_STATIC_IP
  WiFi.config(IPAddress(WIFI_STA_STATIC_IP), IPAddress(WIFI_STA_GATEWAY), IPAddress(WIFI_STA_SUBNET),
              IPAddress(WIFI_STA_GATEWAY));
#endif
  WiFi.begin(WIFI_STA_SSID, WIFI_STA_PASSWORD);
  Serial.printf("Conectando a la Wi-Fi %s", WIFI_STA_SSID);
  uint32_t start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < WIFI_STA_TIMEOUT_MS) {
    delay(250);
    Serial.print('.');
  }
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("\nNo conecta: se crea la Wi-Fi propia");
    WiFi.disconnect(true);
    delay(100);
    return false;
  }
  WiFi.setAutoReconnect(true);                // si se cae el router, vuelve sola
  Serial.printf("\nConectado a %s · IP %s\n", WIFI_STA_SSID, WiFi.localIP().toString().c_str());
  return true;
}

static void startAccessPoint() {
  WiFi.mode(WIFI_AP);
  IPAddress ip(WIFI_AP_IP);
  WiFi.softAPConfig(ip, ip, IPAddress(255, 255, 255, 0));
  WiFi.softAP(WIFI_AP_SSID, WIFI_AP_PASSWORD, WIFI_AP_CHANNEL, 0, 4);
  Serial.printf("Wi-Fi propia: %s (clave %s) · IP %s\n", WIFI_AP_SSID, WIFI_AP_PASSWORD,
                WiFi.softAPIP().toString().c_str());
}

static void startNetwork() {
  stationMode = connectStation();
  if (!stationMode) startAccessPoint();
  if (MDNS.begin(HOSTNAME)) {
    MDNS.addService("rkbx-luces", "udp", UDP_PORT);
    Serial.printf("mDNS: %s.local\n", HOSTNAME);
  }
  WiFi.setSleep(false);
  esp_wifi_set_ps(WIFI_PS_NONE);              // sin ahorro de energía: menos latencia

  if (udp.listen(UDP_PORT)) {
    Serial.printf("Escuchando UDP en el puerto %d\n", UDP_PORT);
    udp.onPacket([](AsyncUDPPacket packet) {
      enqueue(reinterpret_cast<const char*>(packet.data()), packet.length());
    });
  } else {
    Serial.println("ERROR: no se pudo abrir el puerto UDP");
  }
}

// ---------------------------------------------------------------------------
// Serie: los mismos JSON, una línea por comando (para probar sin Wi-Fi).
// ---------------------------------------------------------------------------
static void readSerial() {
  static char line[512];
  static size_t used = 0;
  while (Serial.available()) {
    char ch = (char)Serial.read();
    if (ch == '\n' || ch == '\r') {
      if (used == 1 && (line[0] == 'v' || line[0] == 'V')) {
        logLevel = (logLevel + 1) % 3;
        Serial.printf("Registro de comandos: %s\n",
                      logLevel == 0 ? "desactivado" : logLevel == 1 ? "comandos (sin sync)" : "todo (con sync)");
      } else if (used) {
        enqueue(line, used);
      }
      used = 0;
    } else if (used < sizeof(line) - 1) {
      line[used++] = ch;
    }
  }
}

// ---------------------------------------------------------------------------
// ---------------------------------------------------------------------------
// Montaje (tiras, píxeles y segmentos): se guarda en la memoria del ESP32 (NVS)
// ---------------------------------------------------------------------------
// FastLED necesita el pin al compilar: un caso por cada pin que se puede elegir en la app
// (misma lista que CHANNEL_PINS / PINES en src/rkbx_practice/lights/layout.py).
static CLEDController* addStrip(uint8_t pin, CRGB* data) {
#define RKBX_PIN(P) \
  case P:           \
    return &FastLED.addLeds<LED_TYPE, P, COLOR_ORDER>(data, 1);
  switch (pin) {
    RKBX_PIN(4) RKBX_PIN(13) RKBX_PIN(14) RKBX_PIN(16) RKBX_PIN(17) RKBX_PIN(18) RKBX_PIN(19)
    RKBX_PIN(21) RKBX_PIN(22) RKBX_PIN(23) RKBX_PIN(25) RKBX_PIN(26) RKBX_PIN(27) RKBX_PIN(32)
    RKBX_PIN(33)
    default: return nullptr;
  }
#undef RKBX_PIN
}

static bool validPin(uint8_t pin) {
  static const uint8_t ok[] = {4, 13, 14, 16, 17, 18, 19, 21, 22, 23, 25, 26, 27, 32, 33};
  for (uint8_t p : ok)
    if (p == pin) return true;
  return false;
}

// Pines válidos y sin repetir (si no, el de fábrica de ese canal o el primero libre).
static void fixPins(rl::StripLayout* l, int n) {
  static const uint8_t ok[] = {25, 26, 27, 14, 32, 33, 13, 4, 16, 17, 18, 19, 21, 22, 23};
  for (int s = 0; s < n; ++s) {
    auto used = [&](uint8_t p) {
      for (int k = 0; k < s; ++k)
        if (l[k].pin == p) return true;
      return false;
    };
    if (!validPin(l[s].pin) || used(l[s].pin)) l[s].pin = kDefaultPins[s];
    if (used(l[s].pin))
      for (uint8_t p : ok)
        if (!used(p)) {
          l[s].pin = p;
          break;
        }
  }
}

// Crea un controlador por tira con su pin (solo al arrancar: FastLED no permite quitarlos).
static void createControllers() {
  const rl::StripLayout* l = engine.layout();
  hwStrips = 0;
  for (int s = 0; s < engine.stripCount(); ++s) {
    controllers[s] = addStrip(l[s].pin, leds[s]);
    hwPins[s] = l[s].pin;
    if (controllers[s]) ++hwStrips;
  }
}

static bool needsRestart(const rl::StripLayout* l, int n) {
  if (n != hwStrips) return true;
  for (int s = 0; s < n; ++s)
    if (l[s].pin != hwPins[s]) return true;
  return false;
}

// Aplica píxeles y partes al momento (los pines no cambian hasta reiniciar).
static void useLayout() {
  int n = engine.stripCount();
  const rl::StripLayout* l = engine.layout();
  for (int s = 0; s < rl::MAX_STRIPS; ++s) {
    fill_solid(leds[s], rl::MAX_PIXELS, CRGB::Black);
    if (s < hwStrips && controllers[s]) controllers[s]->setLeds(leds[s], s < n ? l[s].pixels : 1);
    zoneLayout[s] = s < n ? l[s] : rl::StripLayout{0, 0, 0};
  }
  zoneLayoutN = n;
  FastLED.show();
}

static void printLayout(const char* why) {
  int n = engine.stripCount();
  Serial.printf("Montaje (%s): %d tiras, %d zonas:", why, n, engine.zoneCount());
  for (int s = 0; s < n; ++s)
    Serial.printf(" [tira %d: pin %d, %d píxeles, %d partes]", s + 1, engine.layout()[s].pin,
                  engine.layout()[s].pixels, engine.layout()[s].segments);
  Serial.println();
}

// NVS «layout2»: n, y por tira: píxeles (2 bytes), partes, pin.
static void saveLayout(const rl::StripLayout* l, int n) {
  uint8_t buf[1 + rl::MAX_STRIPS * 4];
  buf[0] = (uint8_t)n;
  for (int s = 0; s < n; ++s) {
    buf[1 + s * 4] = l[s].pixels & 0xFF;
    buf[2 + s * 4] = l[s].pixels >> 8;
    buf[3 + s * 4] = l[s].segments;
    buf[4 + s * 4] = l[s].pin;
  }
  prefs.putBytes("layout2", buf, 1 + n * 4);
}

static void loadLayout() {
  rl::StripLayout l[rl::MAX_STRIPS];
  int n = 0;
  uint8_t buf[1 + rl::MAX_STRIPS * 4];
  size_t got = prefs.getBytes("layout2", buf, sizeof(buf));
  if (got >= 5 && buf[0] >= 1 && buf[0] <= rl::MAX_STRIPS && got >= (size_t)(1 + buf[0] * 4)) {
    n = buf[0];
    for (int s = 0; s < n; ++s)
      l[s] = rl::StripLayout{(uint16_t)(buf[1 + s * 4] | (buf[2 + s * 4] << 8)), buf[3 + s * 4],
                             buf[4 + s * 4]};
  } else {
    n = DEFAULT_STRIPS;
    for (int s = 0; s < n; ++s) l[s] = rl::StripLayout{DEFAULT_PIXELS, DEFAULT_SEGMENTS, kDefaultPins[s]};
  }
  fixPins(l, n);
  engine.setLayout(l, n);
}

static void applyCommand(const rl::Command& c, uint32_t now) {
  if (c.fx == rl::Fx::Layout) {
    rl::StripLayout l[rl::MAX_STRIPS];
    int n = c.layoutCount;
    for (int s = 0; s < n; ++s) l[s] = c.layout[s];
    n = rl::sanitizeLayout(l, n);
    fixPins(l, n);
    saveLayout(l, n);
    if (needsRestart(l, n)) {                 // otros pines u otro nº de tiras: reiniciar
      Serial.println("Montaje con otros pines u otro número de tiras: guardado, reiniciando…");
      restartAt = now + 300;
      return;
    }
    engine.setLayout(l, n);
    useLayout();
    printLayout("nuevo, guardado");
    return;
  }
  engine.apply(c, now);
  if (c.fx != rl::Fx::Sync) {
    digitalWrite(STATUS_LED_PIN, HIGH);
    statusLedOffAt = now + 30;
  }
}

static void bootAnimation() {
  // Barrido corto para comprobar el cableado: tira 1 roja, 2 verde, 3 azul, 4 blanca…
  const CRGB colors[rl::MAX_STRIPS] = {CRGB::Red,    CRGB::Green, CRGB::Blue,   CRGB::White,
                                       CRGB::Yellow, CRGB::Cyan,  CRGB::Purple, CRGB::Orange};
  for (int s = 0; s < engine.stripCount(); ++s) {
    for (int i = 0; i < engine.layout()[s].pixels; ++i) {
      leds[s][i] = colors[s];
      FastLED.show();
      delay(engine.layout()[s].pixels > 100 ? 2 : 40);   // barrido visible sin tardar mucho
    }
  }
  delay(300);
  FastLED.clear(true);
}

void setup() {
  Serial.begin(115200);
  pinMode(STATUS_LED_PIN, OUTPUT);

  prefs.begin("rkbx", false);
  loadLayout();                               // el último montaje enviado desde la app
  createControllers();                        // un controlador por tira, con su pin
  FastLED.setBrightness(BRIGHTNESS);
  FastLED.setMaxPowerInVoltsAndMilliamps(POWER_VOLTS, POWER_MAX_MILLIAMPS);
  FastLED.setDither(DISABLE_DITHER);          // sin parpadeo de tramado a alta tasa de refresco
  for (int s = 0; s < rl::MAX_STRIPS; ++s) ledPtrs[s] = reinterpret_cast<rl::RGB*>(leds[s]);

  useLayout();
  commandQueue = xQueueCreate(64, sizeof(rl::Command));
  logQueue = xQueueCreate(32, sizeof(LogLine));

  bootAnimation();
  startNetwork();

  // Reposo hasta que llegue el primer comando: destellos suaves (como lights.idle en la app).
  rl::Command idle;
  idle.fx = rl::Fx::Twinkle;
  rl::parseColor("blanco", idle.color);
  idle.hasColor = true;
  rl::parseColor("azul", idle.color2);
  idle.hasColor2 = true;
  idle.density = 0.06f;
  engine.apply(idle, millis());
  Serial.println("Escribe «v» + Intro para cambiar el registro de comandos (nada / comandos / todo)");
  printLayout("al arrancar");
  Serial.println("Listo");
}

void loop() {
  static uint32_t lastFrameUs = 0, lastStats = 0, frames = 0;
  uint32_t now = millis();

  rl::Command c;
  while (xQueueReceive(commandQueue, &c, 0) == pdTRUE) applyCommand(c, now);
  readSerial();
  printLog();

  if (restartAt && (int32_t)(now - restartAt) >= 0) ESP.restart();   // pines nuevos

  if (statusLedOffAt && (int32_t)(now - statusLedOffAt) >= 0) {
    digitalWrite(STATUS_LED_PIN, LOW);
    statusLedOffAt = 0;
  }

  uint32_t us = micros();
  if (us - lastFrameUs >= 1000000u / FRAMES_PER_SECOND) {
    lastFrameUs = us;
    engine.render(now, ledPtrs);
    FastLED.show();
    ++frames;
  }

  if (now - lastStats >= 10000) {
    Serial.printf("[%lus] paquetes %lu · inválidos %lu · perdidos %lu · %lu fps · sync %.1f BPM\n",
                  (unsigned long)(now / 1000), (unsigned long)packetsReceived,
                  (unsigned long)packetsInvalid, (unsigned long)packetsDropped,
                  (unsigned long)(frames * 1000 / (now - lastStats + 1)), engine.lastSyncBpm());
    frames = 0;
    lastStats = now;
  }
}
