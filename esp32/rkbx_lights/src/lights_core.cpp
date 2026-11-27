// Núcleo de efectos (ver lights_core.h). Sin Arduino: compila también en el PC para las pruebas.
#include "lights_core.h"

#include <ctype.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>

namespace rl {

namespace {

constexpr float PI_F = 3.14159265f;
constexpr uint32_t BASE_FADE_MS = 150;   // fundido entre fondos

struct NamedColor {
  const char* name;
  RGB rgb;
};

const NamedColor kColors[] = {
    {"rojo", {255, 0, 0}},      {"red", {255, 0, 0}},        {"verde", {0, 255, 0}},
    {"green", {0, 255, 0}},     {"azul", {0, 80, 255}},      {"blue", {0, 80, 255}},
    {"blanco", {255, 255, 255}}, {"white", {255, 255, 255}}, {"amarillo", {255, 220, 0}},
    {"yellow", {255, 220, 0}},  {"naranja", {255, 120, 0}},  {"orange", {255, 120, 0}},
    {"morado", {150, 0, 255}},  {"purple", {150, 0, 255}},   {"violeta", {150, 0, 255}},
    {"rosa", {255, 60, 160}},   {"pink", {255, 60, 160}},    {"cian", {0, 230, 255}},
    {"cyan", {0, 230, 255}},    {"magenta", {255, 0, 255}},  {"negro", {0, 0, 0}},
    {"black", {0, 0, 0}},
};

bool ieq(const char* a, const char* b) {
  while (*a && *b) {
    if (tolower((unsigned char)*a) != tolower((unsigned char)*b)) return false;
    ++a;
    ++b;
  }
  return *a == *b;
}

inline float clamp01(float v) { return v < 0 ? 0 : (v > 1 ? 1 : v); }

inline RGB scale(RGB c, float k) {
  k = clamp01(k);
  return RGB{(uint8_t)(c.r * k + 0.5f), (uint8_t)(c.g * k + 0.5f), (uint8_t)(c.b * k + 0.5f)};
}

inline RGB mix(RGB a, RGB b, float k) {
  k = clamp01(k);
  return RGB{(uint8_t)(a.r + (b.r - a.r) * k + 0.5f), (uint8_t)(a.g + (b.g - a.g) * k + 0.5f),
             (uint8_t)(a.b + (b.b - a.b) * k + 0.5f)};
}

RGB hsv(float h, float s, float v) {
  h -= floorf(h);
  int i = (int)(h * 6);
  float f = h * 6 - i, p = v * (1 - s), q = v * (1 - f * s), t = v * (1 - (1 - f) * s);
  float r, g, b;
  switch (i % 6) {
    case 0: r = v, g = t, b = p; break;
    case 1: r = q, g = v, b = p; break;
    case 2: r = p, g = v, b = t; break;
    case 3: r = p, g = q, b = v; break;
    case 4: r = t, g = p, b = v; break;
    default: r = v, g = p, b = q; break;
  }
  return RGB{(uint8_t)(r * 255), (uint8_t)(g * 255), (uint8_t)(b * 255)};
}

// Pseudoaleatorio estable (0..1) para los destellos.
inline float hash01(uint32_t a, uint32_t b) {
  uint32_t x = a * 0x9E3779B1u ^ (b + 0x7F4A7C15u) * 0x85EBCA77u;
  x ^= x >> 15;
  x *= 0x2C1B3C6Du;
  x ^= x >> 12;
  x *= 0x297A2D39u;
  x ^= x >> 15;
  return (x & 0xFFFFFF) / 16777216.0f;
}

inline float beatMs(float bpm) { return 60000.0f / (bpm > 1 ? bpm : 128.0f); }

}  // namespace

// ---------------------------------------------------------------------------
Fx parseFx(const char* n) {
  if (!n) return Fx::None;
  struct { const char* name; Fx fx; } table[] = {
      {"color", Fx::Color},   {"off", Fx::Off},         {"pulse", Fx::Pulse},
      {"breathe", Fx::Breathe}, {"alternate", Fx::Alternate}, {"chase", Fx::Chase},
      {"wave", Fx::Wave},     {"twinkle", Fx::Twinkle}, {"flash", Fx::Flash},
      {"strobe", Fx::Strobe}, {"rainbow", Fx::Rainbow}, {"build", Fx::Build}, {"prebuild", Fx::Prebuild},
      {"identify", Fx::Identify}, {"identificar", Fx::Identify}, {"layout", Fx::Layout},
      {"sync", Fx::Sync},
  };
  for (auto& e : table)
    if (ieq(n, e.name)) return e.fx;
  return Fx::Unknown;
}

bool parseColor(const char* t, RGB& out) {
  if (!t) return false;
  while (*t == ' ') ++t;
  for (auto& c : kColors) {
    if (ieq(t, c.name)) {
      out = c.rgb;
      return true;
    }
  }
  if (*t == '#') ++t;
  if (strlen(t) == 6) {
    char* end = nullptr;
    unsigned long v = strtoul(t, &end, 16);
    if (end && *end == 0) {
      out = RGB{(uint8_t)(v >> 16), (uint8_t)(v >> 8), (uint8_t)v};
      return true;
    }
  }
  return false;
}

int sanitizeLayout(StripLayout* strips, int n) {
  if (n > MAX_STRIPS) n = MAX_STRIPS;
  if (n < 1) n = 1;
  int zones = 0;
  for (int i = 0; i < n; ++i) {
    StripLayout& l = strips[i];
    if (l.pixels < 1) l.pixels = 1;
    if (l.pixels > MAX_PIXELS) l.pixels = MAX_PIXELS;
    if (l.segments < 1) l.segments = 1;
    if (l.segments > MAX_SECTIONS) l.segments = MAX_SECTIONS;
    if (l.segments > l.pixels) l.segments = (uint8_t)l.pixels;
    if (zones + l.segments > MAX_ZONES) l.segments = (uint8_t)(MAX_ZONES - zones);
    if (l.segments == 0) return i;   // no caben más zonas
    zones += l.segments;
  }
  return n;
}

static uint64_t bit(int z) { return (uint64_t)1 << (z - 1); }

uint64_t parseZones(const char* spec, const StripLayout* strips, int n) {
  int total = 0;
  for (int i = 0; i < n; ++i) total += strips[i].segments;
  uint64_t all = total >= 64 ? ~(uint64_t)0 : (((uint64_t)1 << total) - 1);
  if (!spec || !*spec || ieq(spec, "all") || ieq(spec, "todas")) return all;
  if (ieq(spec, "odd") || ieq(spec, "impares")) {
    uint64_t m = 0;
    for (int z = 1; z <= total; z += 2) m |= bit(z);
    return m;
  }
  if (ieq(spec, "even") || ieq(spec, "pares")) {
    uint64_t m = 0;
    for (int z = 2; z <= total; z += 2) m |= bit(z);
    return m;
  }
  const char* colon = strchr(spec, ':');
  if (colon) {
    char kind[16] = {0};
    size_t len = (size_t)(colon - spec);
    if (len >= sizeof(kind)) return 0;
    memcpy(kind, spec, len);
    int k = atoi(colon + 1);
    bool strip = ieq(kind, "strip") || ieq(kind, "tira");
    bool section = ieq(kind, "section") || ieq(kind, "seccion") || ieq(kind, "sección") ||
                   ieq(kind, "segment") || ieq(kind, "segmento");
    if (!strip && !section) return 0;
    uint64_t m = 0;
    int z = 1;
    for (int s = 0; s < n; ++s)
      for (int c = 1; c <= strips[s].segments; ++c, ++z)
        if ((strip && s + 1 == k) || (section && c == k)) m |= bit(z);
    return m;
  }
  uint64_t m = 0;   // lista "1,5,9"
  const char* p = spec;
  while (*p) {
    while (*p == ' ' || *p == ',') ++p;
    if (!*p) break;
    char* end = nullptr;
    long z = strtol(p, &end, 10);
    if (end == p) return 0;
    if (z >= 1 && z <= total) m |= bit((int)z);
    p = end;
  }
  return m;
}

uint64_t zonesFromList(const int* list, int n, int total) {
  uint64_t m = 0;
  for (int i = 0; i < n; ++i)
    if (list[i] >= 1 && list[i] <= total && list[i] <= MAX_ZONES) m |= bit(list[i]);
  return m;
}

static int uniform(StripLayout* out, int strips, int sections, int pixels) {
  if (strips > MAX_STRIPS) strips = MAX_STRIPS;
  for (int i = 0; i < strips; ++i) out[i] = StripLayout{(uint16_t)pixels, (uint8_t)sections, 0};
  return strips;
}

uint64_t parseZones(const char* spec, int strips, int sections) {
  StripLayout l[MAX_STRIPS];
  int n = uniform(l, strips, sections, MAX_PIXELS);
  return parseZones(spec, l, n);
}

uint64_t zonesFromList(const int* list, int n, int strips, int sections) {
  return zonesFromList(list, n, strips * sections);
}

// ---------------------------------------------------------------------------
void Engine::begin(int strips, int sections, int pixelsPerStrip) {
  StripLayout l[MAX_STRIPS];
  int n = uniform(l, strips < 1 ? 1 : strips, sections, pixelsPerStrip);
  setLayout(l, n);
}

void Engine::setLayout(const StripLayout* strips, int n) {
  StripLayout l[MAX_STRIPS] = {};
  if (n > MAX_STRIPS) n = MAX_STRIPS;
  for (int i = 0; i < n; ++i) l[i] = strips[i];
  n = sanitizeLayout(l, n);
  strips_ = n;
  zones_n_ = 0;
  totalPixels_ = 0;
  for (int i = 0; i < MAX_STRIPS; ++i) {
    layout_[i] = i < n ? l[i] : StripLayout{0, 0, 0};
    zoneStart_[i] = zones_n_;
    zones_n_ += layout_[i].segments;
    totalPixels_ += layout_[i].pixels;
  }
  zoneStart_[MAX_STRIPS] = zones_n_;
  for (auto& z : zones_) z = Zone();
}

bool Engine::hasOverlay(int zone, uint32_t now) const {
  const Overlay& o = zones_[zone].ov;
  return o.fx != Fx::None && (uint32_t)(now - o.start) < o.durMs;
}

void Engine::applySync(const Command& c, uint32_t now) {
  // Re-alinea el reloj de beats de los fondos rítmicos sin cambiar efecto ni color.
  float bpm = c.bpm > 1 ? c.bpm : 128.0f;
  float newBeat = beatMs(bpm);
  int64_t anchor = (int64_t)now - (int64_t)((c.phase > 0 ? c.phase : 0) * newBeat);
  for (int zi = 0; zi < zoneCount(); ++zi) {
    Base& b = zones_[zi].base;
    if (b.mode == Mode::Solid || b.mode == Mode::Breathe) continue;
    float oldBeat = beatMs(b.bpm);
    int64_t beats = llroundf((float)(anchor - (int64_t)b.since) / oldBeat);
    b.since = (uint32_t)(anchor - (int64_t)(beats * newBeat));
    b.bpm = bpm;
  }
  lastSyncBpm_ = bpm;
}

void Engine::apply(const Command& c, uint32_t now) {
  ++applied_;
  if (c.fx == Fx::Sync) {
    applySync(c, now);
    return;
  }
  if (c.fx == Fx::Layout) {
    if (c.layoutCount) setLayout(c.layout, c.layoutCount);
    return;
  }
  float bpm = c.bpm > 1 ? c.bpm : 128.0f;
  // `phase`: punto del beat (0..1) en que se aplica → el reloj de beats empieza antes.
  uint32_t t0 = c.phase >= 0 ? now - (uint32_t)(c.phase * beatMs(bpm)) : now;
  int total = zoneCount();
  int count = 0;
  for (int zi = 0; zi < total; ++zi)
    if (c.zoneMask & ((uint64_t)1 << zi)) ++count;
  int pos = 0;
  for (int zi = 0; zi < total; ++zi) {
    if (!(c.zoneMask & ((uint64_t)1 << zi))) continue;
    Zone& z = zones_[zi];
    auto setBase = [&](Base b) {
      z.prev = z.base;
      b.since = t0;
      z.base = b;
      z.baseAt = now;
      z.ov.fx = Fx::None;
    };
    auto over = [&](Fx fx, uint32_t dflt, RGB dfltColor) {
      z.ov.fx = fx;
      z.ov.start = now;
      z.ov.durMs = c.durMs ? c.durMs : dflt;
      z.ov.color = c.hasColor ? c.color : dfltColor;
      z.ov.hz = c.hz > 0 ? c.hz : 12;
      z.ov.speed = c.speed > 0 ? c.speed : 0.5f;
    };
    Base b;
    b.bpm = bpm;
    b.color = c.hasColor ? c.color : RGB{0, 80, 255};
    b.color2 = c.hasColor2 ? c.color2 : RGB{0, 0, 0};
    switch (c.fx) {
      case Fx::Color: b.mode = Mode::Solid; setBase(b); break;
      case Fx::Off: b.mode = Mode::Solid; b.color = RGB{0, 0, 0}; setBase(b); break;
      case Fx::Pulse:
        b.mode = Mode::Pulse;
        if (!c.hasColor) b.color = RGB{150, 0, 255};
        setBase(b);
        break;
      case Fx::Breathe:
        b.mode = Mode::Breathe;
        b.periodMs = c.periodMs ? c.periodMs : 4000;
        setBase(b);
        break;
      case Fx::Alternate:
        b.mode = Mode::Alternate;
        if (!c.hasColor) b.color = RGB{150, 0, 255};
        setBase(b);
        break;
      case Fx::Chase:
        b.mode = Mode::Chase;
        if (!c.hasColor) b.color = RGB{255, 120, 0};
        b.beats = c.beats > 0 ? c.beats : 1;
        b.pos = (uint8_t)pos;
        b.count = (uint8_t)(count ? count : 1);
        setBase(b);
        break;
      case Fx::Wave:
        b.mode = Mode::Wave;
        b.beats = c.beats > 0 ? c.beats : 4;
        setBase(b);
        break;
      case Fx::Twinkle:
        b.mode = Mode::Twinkle;
        b.density = c.density > 0 ? c.density : 0.15f;
        setBase(b);
        break;
      case Fx::Flash: over(Fx::Flash, 200, RGB{255, 255, 255}); break;
      case Fx::Strobe: over(Fx::Strobe, 1000, RGB{255, 255, 255}); break;
      case Fx::Rainbow: over(Fx::Rainbow, 4000, RGB{255, 255, 255}); break;
      case Fx::Build: over(Fx::Build, 8000, RGB{255, 255, 255}); break;
      case Fx::Prebuild: over(Fx::Prebuild, 16000, RGB{0, 80, 255}); break;
      case Fx::Identify: over(Fx::Identify, 6000, RGB{255, 255, 255}); break;
      case Fx::Unknown:
      default: over(Fx::Unknown, 600, RGB{160, 0, 255}); break;
    }
    ++pos;
  }
}

// ---------------------------------------------------------------------------
RGB Engine::baseColor(const Base& b, uint32_t now, int zone, float x, int strip, uint32_t k) const {
  float bm = beatMs(b.bpm);
  float el = (float)(uint32_t)(now - b.since);
  switch (b.mode) {
    case Mode::Pulse: {                                   // golpe en cada beat que decae
      float ph = fmodf(el, bm) / bm;
      float d = 1 - ph;
      return scale(b.color, 0.25f + 0.75f * d * d * d);
    }
    case Mode::Breathe: {
      float ph = fmodf(el, (float)b.periodMs) / b.periodMs;
      return scale(b.color, 0.15f + 0.85f * (0.5f - 0.5f * cosf(ph * 2 * PI_F)));
    }
    case Mode::Alternate: {                               // pares e impares se turnan en cada beat
      uint32_t beat = (uint32_t)(el / bm);
      float ph = fmodf(el, bm) / bm;
      bool mine = ((uint32_t)(zone + 1) + beat) % 2 == 0;
      return mine ? scale(b.color, 0.35f + 0.65f * (1 - ph) * (1 - ph)) : scale(b.color2, 0.35f);
    }
    case Mode::Chase: {                                   // la luz recorre las zonas
      float stepMs = bm * b.beats;
      uint32_t step = (uint32_t)(el / stepMs);
      float ph = fmodf(el, stepMs) / stepMs;
      return (step % b.count) == b.pos ? scale(b.color, 0.4f + 0.6f * (1 - ph)) : scale(b.color, 0.06f);
    }
    case Mode::Wave: {                                    // ola que recorre cada tira
      float cycle = bm * b.beats;
      float ph = fmodf(el, cycle) / cycle;
      float v = 0.5f + 0.5f * sinf(2 * PI_F * (x * 1.5f - ph) + strip * 0.9f);
      return mix(scale(b.color2, 0.25f), b.color, v * v);
    }
    case Mode::Twinkle: {                                 // destellos aleatorios sobre fondo tenue
      float half = bm / 2;
      uint32_t slot = (uint32_t)(el / half);
      float ph = fmodf(el, half) / half;
      RGB bg = scale(b.color2, 0.12f + 0.08f * sinf(el / 900.0f + k));
      return hash01(k, slot) < b.density ? mix(bg, b.color, 1 - ph) : bg;
    }
    case Mode::Solid:
    default:
      return b.color;
  }
}

RGB Engine::zoneBase(const Zone& z, uint32_t now, int zone, float x, int strip, uint32_t k) const {
  uint32_t since = now - z.baseAt;
  RGB cur = baseColor(z.base, now, zone, x, strip, k);
  if (since >= BASE_FADE_MS) return cur;
  return mix(baseColor(z.prev, now, zone, x, strip, k), cur, (float)since / BASE_FADE_MS);
}

RGB Engine::ledColor(const Zone& z, uint32_t now, int zone, int i, int n, float x, int strip,
                     uint32_t k) const {
  RGB base = zoneBase(z, now, zone, x, strip, k);
  const Overlay& o = z.ov;
  if (o.fx == Fx::None) return base;
  uint32_t el = now - o.start;
  if (el >= o.durMs) return base;
  float p = (float)el / o.durMs;
  switch (o.fx) {
    case Fx::Flash: return mix(base, o.color, powf(1 - p, 1.6f));
    case Fx::Strobe: {
      uint32_t halfMs = (uint32_t)(1000.0f / o.hz / 2);
      bool on = halfMs == 0 || (el / halfMs) % 2 == 0;
      return on ? o.color : scale(base, 0.15f);
    }
    case Fx::Rainbow: {
      float t = el / 1000.0f * o.speed;
      float fade = clamp01((1 - p) * 6);
      float hue = (float)k / (totalPixels_ > 0 ? totalPixels_ : 1) + t;
      return mix(base, hsv(hue, 1, 1), fade);
    }
    case Fx::Prebuild: {                                  // carga: la tira se llena hasta el final
      const float edge = 0.06f;
      RGB dim = scale(base, 0.2f);
      if (x > p) return dim;
      float lvl = 0.35f + 0.65f * p;
      if (p - x < edge) return mix(scale(o.color, lvl), RGB{255, 255, 255}, 0.6f * (1 - (p - x) / edge));
      return mix(dim, o.color, lvl);
    }
    case Fx::Build: {                                     // tensión: más brillo y parpadeo más rápido
      float hz = 1 + 15 * p * p;
      bool on = ((uint32_t)(el / 1000.0f * hz * 2)) % 2 == 0;
      float lit = n * (0.2f + 0.8f * p);
      RGB dim = scale(base, 0.3f);
      return fabsf(i - (n - 1) / 2.0f) <= lit / 2 ? mix(dim, o.color, on ? 0.25f + 0.75f * p : 0.1f * p) : dim;
    }
    case Fx::Identify: {                                  // cada zona con su color; 1.er píxel blanco
      if (i == 0) return RGB{255, 255, 255};
      bool on = (el / 400) % 2 == 0;
      // Mismos colores que las partes en la ventana «Mis tiras» de la app.
      static const RGB kParts[8] = {{255, 92, 92},  {255, 169, 77}, {255, 212, 59}, {105, 219, 124},
                                    {56, 217, 169}, {77, 171, 247}, {116, 143, 252}, {218, 119, 242}};
      return on ? kParts[zone % 8] : scale(base, 0.1f);
    }
    default: return mix(base, o.color, sinf(p * PI_F));
  }
}

int Engine::segmentStart(int strip, int seg) const {
  const StripLayout& l = layout_[strip];
  return l.segments ? seg * l.pixels / l.segments : 0;
}

void Engine::render(uint32_t now, RGB* const* out) const {
  // Los segmentos se reparten los píxeles de su tira lo más igual posible (14 → 4, 5, 5).
  uint32_t k0 = 0;
  for (int s = 0; s < strips_; ++s) {
    RGB* strip = out[s];
    const StripLayout& l = layout_[s];
    if (strip) {
      for (int c = 0; c < l.segments; ++c) {
        int zone = zoneStart_[s] + c;
        const Zone& z = zones_[zone];
        int a = segmentStart(s, c), n = segmentStart(s, c + 1) - a;
        for (int i = 0; i < n; ++i) {
          float x = (a + i + 0.5f) / (float)l.pixels;        // posición a lo largo de la tira
          strip[a + i] = ledColor(z, now, zone, i, n, x, s, k0 + a + i);
        }
      }
    }
    k0 += l.pixels;
  }
}

}  // namespace rl
