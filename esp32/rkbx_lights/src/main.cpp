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
#include <FastLED.h>
#include <WiFi.h>
#include <esp_wifi.h>

#include "config.h"
#include "lights_core.h"

static_assert(sizeof(CRGB) == sizeof(rl::RGB), "CRGB y rl::RGB deben tener la misma forma (r, g, b)");
static_assert(STRIPS >= 1 && STRIPS <= 4, "STRIPS entre 1 y 4");

constexpr int LEDS_PER_STRIP = SECTIONS_PER_STRIP * LEDS_PER_SECTION;

static CRGB leds[STRIPS][LEDS_PER_STRIP];
static rl::RGB* ledPtrs[STRIPS];
static rl::Engine engine;
static AsyncUDP udp;
static QueueHandle_t commandQueue;

static volatile uint32_t packetsReceived = 0, packetsInvalid = 0, packetsDropped = 0;
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
    c.zoneMask = rl::parseZones(z.as<const char*>(), STRIPS, SECTIONS_PER_STRIP);
  } else if (z.is<JsonArrayConst>()) {
    int list[rl::MAX_ZONES];
    int n = 0;
    for (JsonVariantConst v : z.as<JsonArrayConst>())
      if (n < rl::MAX_ZONES) list[n++] = v | 0;
    c.zoneMask = rl::zonesFromList(list, n, STRIPS, SECTIONS_PER_STRIP);
  } else if (z.is<int>()) {
    int one = z.as<int>();
    c.zoneMask = rl::zonesFromList(&one, 1, STRIPS, SECTIONS_PER_STRIP);
  }
  return c.zoneMask != 0;
}

static void enqueue(const char* data, size_t len) {
  rl::Command c;
  ++packetsReceived;
  if (!parseCommand(data, len, c)) {
    ++packetsInvalid;
    return;
  }
  if (xQueueSend(commandQueue, &c, 0) != pdTRUE) ++packetsDropped;
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
      if (used) {
        enqueue(line, used);
        used = 0;
      }
    } else if (used < sizeof(line) - 1) {
      line[used++] = ch;
    }
  }
}

// ---------------------------------------------------------------------------
static void applyCommand(const rl::Command& c, uint32_t now) {
  engine.apply(c, now);
  if (c.fx != rl::Fx::Sync) {
    digitalWrite(STATUS_LED_PIN, HIGH);
    statusLedOffAt = now + 30;
  }
}

static void bootAnimation() {
  // Barrido corto para comprobar el cableado: tira 1 roja, 2 verde, 3 azul.
  const CRGB colors[4] = {CRGB::Red, CRGB::Green, CRGB::Blue, CRGB::White};
  for (int s = 0; s < STRIPS; ++s) {
    for (int i = 0; i < LEDS_PER_STRIP; ++i) {
      leds[s][i] = colors[s];
      FastLED.show();
      delay(3);
    }
  }
  delay(300);
  FastLED.clear(true);
}

void setup() {
  Serial.begin(115200);
  pinMode(STATUS_LED_PIN, OUTPUT);

  FastLED.addLeds<LED_TYPE, PIN_STRIP_1, COLOR_ORDER>(leds[0], LEDS_PER_STRIP);
#if STRIPS >= 2
  FastLED.addLeds<LED_TYPE, PIN_STRIP_2, COLOR_ORDER>(leds[1], LEDS_PER_STRIP);
#endif
#if STRIPS >= 3
  FastLED.addLeds<LED_TYPE, PIN_STRIP_3, COLOR_ORDER>(leds[2], LEDS_PER_STRIP);
#endif
#if STRIPS >= 4
  FastLED.addLeds<LED_TYPE, PIN_STRIP_4, COLOR_ORDER>(leds[3], LEDS_PER_STRIP);
#endif
  FastLED.setBrightness(BRIGHTNESS);
  FastLED.setMaxPowerInVoltsAndMilliamps(POWER_VOLTS, POWER_MAX_MILLIAMPS);
  FastLED.setDither(DISABLE_DITHER);          // sin parpadeo de tramado a alta tasa de refresco
  for (int s = 0; s < STRIPS; ++s) ledPtrs[s] = reinterpret_cast<rl::RGB*>(leds[s]);

  engine.begin(STRIPS, SECTIONS_PER_STRIP, LEDS_PER_SECTION);
  commandQueue = xQueueCreate(64, sizeof(rl::Command));

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
  Serial.printf("Listo: %d tiras × %d secciones × %d LEDs\n", STRIPS, SECTIONS_PER_STRIP, LEDS_PER_SECTION);
}

void loop() {
  static uint32_t lastFrameUs = 0, lastStats = 0, frames = 0;
  uint32_t now = millis();

  rl::Command c;
  while (xQueueReceive(commandQueue, &c, 0) == pdTRUE) applyCommand(c, now);
  readSerial();

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
