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

constexpr int MAX_STRIPS = 4;
constexpr int MAX_SECTIONS = 4;
constexpr int MAX_ZONES = MAX_STRIPS * MAX_SECTIONS;
constexpr uint16_t ALL_ZONES = 0xFFFF;

enum class Fx : uint8_t {
  None, Color, Off, Pulse, Breathe, Alternate, Chase, Wave, Twinkle,   // fondos
  Flash, Strobe, Rainbow, Build, Prebuild, Unknown,                              // temporales
  Sync                                                                 // re-alinear el beat
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
  uint16_t zoneMask = ALL_ZONES;
};

Fx parseFx(const char* name);
bool parseColor(const char* text, RGB& out);           // nombre (es/en) o #rrggbb
// "all", "odd"/"impares", "even"/"pares", "strip:N"/"tira:N", "section:N"/"seccion:N", "1,5,9".
// Devuelve 0 si no hay ninguna zona válida.
uint16_t parseZones(const char* spec, int strips, int sections);
uint16_t zonesFromList(const int* list, int n, int strips, int sections);

class Engine {
 public:
  // pixelsPerStrip: píxeles direccionables de cada tira (en WS2811 de 12 V, 1 píxel = 3 LEDs).
  void begin(int strips, int sections, int pixelsPerStrip);
  void apply(const Command& c, uint32_t nowMs);
  // out[s] = píxeles de la tira s (pixelsPerStrip, secciones seguidas).
  void render(uint32_t nowMs, RGB* const* out) const;

  int zoneCount() const { return strips_ * sections_; }
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

  int sectionStart(int section) const;
  int strips_ = 3, sections_ = 3, pixels_ = 60;
  Zone zones_[MAX_ZONES];
  float lastSyncBpm_ = 0;
  uint32_t applied_ = 0;
};

}  // namespace rl
