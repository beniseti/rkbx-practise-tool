// Núcleo de efectos de luces (sin dependencias de Arduino: se prueba también en el PC).
// Implementa el protocolo de docs/protocolo_luces.md con la misma lógica que el simulador
// (src/rkbx_practice/lights/simulator.html), para que el ESP32 se vea igual que el simulador.
#pragma once
#include <stddef.h>
#include <stdint.h>

namespace rl {

struct RGB {
  uint8_t r, g, b;
};

constexpr int MAX_STRIPS = 8;           // tiras (una salida RMT y un pin cada una)
constexpr int MAX_SECTIONS = 8;         // segmentos por tira
constexpr int MAX_PIXELS = 1000;        // píxeles direccionables por tira
constexpr int MAX_ZONES = 64;           // máscara de 64 bits
constexpr uint64_t ALL_ZONES = ~(uint64_t)0;

// Montaje de una tira (pedido del usuario: poder cambiar tiras, segmentos y LEDs sin reprogramar).
struct StripLayout {
  uint16_t pixels;    // píxeles direccionables (WS2811 de 12 V: 1 píxel = 3 LEDs)
  uint8_t segments;   // zonas en que se divide la tira (a partes iguales)
  uint8_t pin;        // GPIO de datos (lo usa main.cpp; 0 = el de fábrica de ese canal)
  // (sin valores por defecto: el compilador del ESP32 usa C++11 y la struct debe ser un agregado)
};

enum class Fx : uint8_t {
  None, Color, Off, Pulse, Breathe, Alternate, Chase, Wave, Twinkle,   // fondos
  Flash, Strobe, Rainbow, Build, Prebuild, Identify, Unknown,         // temporales
  Sync,                                                                // re-alinear el beat
  Layout                                                               // cambiar el montaje
};

// Comando ya interpretado (el JSON se analiza en main.cpp con ArduinoJson).
struct Command {
  Fx fx = Fx::None;
  RGB color{0, 0, 0};
  bool hasColor = false;
  RGB color2{0, 0, 0};
  bool hasColor2 = false;
  float bpm = 0;        // 0 = no indicado
  float beats = 0;      // chase / wave
  float phase = -1;     // <0 = no indicado; 0..1 punto del beat en que se aplica
  float hz = 0;         // strobe
  float speed = 0;      // rainbow
  float density = 0;    // twinkle
  uint32_t durMs = 0;
  uint32_t periodMs = 0;
  uint64_t zoneMask = ALL_ZONES;
  // Solo para Fx::Layout
  uint8_t layoutCount = 0;
  StripLayout layout[MAX_STRIPS] = {};
};

Fx parseFx(const char* name);
bool parseColor(const char* text, RGB& out);           // nombre (es/en) o #rrggbb
// "all", "odd"/"impares", "even"/"pares", "strip:N"/"tira:N", "section:N"/"seccion:N", "1,5,9".
// Las zonas se numeran tira a tira (tira 1: 1..s1, tira 2: s1+1..). «section:N» = el segmento N
// de cada tira que lo tenga. Devuelve 0 si no hay ninguna zona válida.
uint64_t parseZones(const char* spec, const StripLayout* strips, int n);
uint64_t zonesFromList(const int* list, int n, int totalZones);
// Atajos para montajes uniformes (n tiras × s segmentos).
uint64_t parseZones(const char* spec, int strips, int sections);
uint64_t zonesFromList(const int* list, int n, int strips, int sections);
// Normaliza un montaje (límites) y devuelve el nº de tiras válidas.
int sanitizeLayout(StripLayout* strips, int n);

class Engine {
 public:
  // Montaje uniforme: strips tiras × sections segmentos, pixelsPerStrip píxeles cada una.
  void begin(int strips, int sections, int pixelsPerStrip);
  // Montaje libre: cada tira con sus píxeles y segmentos. Reinicia los efectos.
  void setLayout(const StripLayout* strips, int n);
  void apply(const Command& c, uint32_t nowMs);
  // out[s] = píxeles de la tira s (layout[s].pixels, segmentos seguidos).
  void render(uint32_t nowMs, RGB* const* out) const;

  int zoneCount() const { return zones_n_; }
  int stripCount() const { return strips_; }
  const StripLayout* layout() const { return layout_; }
  float lastSyncBpm() const { return lastSyncBpm_; }
  uint32_t commandsApplied() const { return applied_; }

  // Para pruebas y diagnóstico.
  enum class Mode : uint8_t { Solid, Pulse, Breathe, Alternate, Chase, Wave, Twinkle };
  Mode baseMode(int zone) const { return zones_[zone].base.mode; }
  bool hasOverlay(int zone, uint32_t nowMs) const;

 private:
  struct Base {
    Mode mode = Mode::Solid;
    RGB color{0, 0, 0}, color2{0, 0, 0};
    float bpm = 128, beats = 1, density = 0.15f;
    uint32_t periodMs = 4000;
    uint32_t since = 0;      // inicio del reloj de beats (ms)
    uint8_t pos = 0, count = 1;   // chase: posición de la zona en la lista y nº de zonas
  };
  struct Overlay {
    Fx fx = Fx::None;
    RGB color{255, 255, 255};
    uint32_t start = 0, durMs = 0;
    float hz = 12, speed = 0.5f;
  };
  struct Zone {
    Base base, prev;
    uint32_t baseAt = 0;
    Overlay ov;
  };

  RGB baseColor(const Base& b, uint32_t now, int zone, float x, int strip, uint32_t k) const;
  RGB zoneBase(const Zone& z, uint32_t now, int zone, float x, int strip, uint32_t k) const;
  RGB ledColor(const Zone& z, uint32_t now, int zone, int i, int n, float x, int strip, uint32_t k) const;
  void applySync(const Command& c, uint32_t now);

  int segmentStart(int strip, int seg) const;
  StripLayout layout_[MAX_STRIPS] = {{60, 3, 0}, {60, 3, 0}, {60, 3, 0}};
  int strips_ = 3, zones_n_ = 9, totalPixels_ = 180;
  int zoneStart_[MAX_STRIPS + 1] = {0, 3, 6, 9, 9, 9, 9, 9, 9};
  Zone zones_[MAX_ZONES];
  float lastSyncBpm_ = 0;
  uint32_t applied_ = 0;
};

}  // namespace rl
