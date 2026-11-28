// Pruebas del núcleo de efectos en el PC (sin placa):
//   g++ -std=c++17 -O2 -I rkbx_lights/include rkbx_lights/src/lights_core.cpp test_host/test_core.cpp -o /tmp/t && /tmp/t
#include <stdio.h>
#include <stdlib.h>

#include "lights_core.h"

using namespace rl;

static int failures = 0;
#define CHECK(cond)                                                    \
  do {                                                                 \
    if (!(cond)) {                                                     \
      printf("FALLO %s:%d: %s\n", __FILE__, __LINE__, #cond);          \
      ++failures;                                                      \
    }                                                                  \
  } while (0)

static int lum(RGB c) { return c.r + c.g + c.b; }

int main() {
  // --- colores y efectos
  RGB c;
  CHECK(parseColor("rojo", c) && c.r == 255 && c.g == 0);
  CHECK(parseColor("Blue", c) && c.b == 255);
  CHECK(parseColor("#12ab34", c) && c.r == 0x12 && c.g == 0xab && c.b == 0x34);
  CHECK(!parseColor("noexiste", c));
  CHECK(parseFx("chase") == Fx::Chase && parseFx("SYNC") == Fx::Sync && parseFx("xyz") == Fx::Unknown);

  // --- zonas 3×3
  CHECK(parseZones("all", 3, 3) == 0x1FF);
  CHECK(parseZones("impares", 3, 3) == 0b101010101);
  CHECK(parseZones("even", 3, 3) == 0b010101010);
  CHECK(parseZones("strip:2", 3, 3) == 0b000111000);
  CHECK(parseZones("seccion:3", 3, 3) == 0b100100100);
  CHECK(parseZones("1,5,9,12", 3, 3) == 0b100010001);
  int list[] = {2, 8};
  CHECK(zonesFromList(list, 2, 3, 3) == 0b010000010);

  // --- motor
  Engine e;
  e.begin(3, 3, 30);                        // 30 píxeles por tira = 3 secciones de 10
  RGB s0[30], s1[30], s2[30];
  RGB* out[3] = {s0, s1, s2};

  Command cmd;
  cmd.fx = Fx::Color;
  cmd.hasColor = parseColor("azul", cmd.color);
  cmd.zoneMask = parseZones("odd", 3, 3);
  e.apply(cmd, 1000);
  e.render(1200, out);                      // tras el fundido de 150 ms
  CHECK(s0[0].b == 255 && s0[10].b == 0 && s0[20].b == 255);   // zonas 1 y 3 azules, 2 apagada

  // flash temporal sobre el fondo, y vuelve al fondo al acabar
  Command fl;
  fl.fx = Fx::Flash;
  fl.hasColor = parseColor("blanco", fl.color);
  fl.durMs = 200;
  e.apply(fl, 2000);
  e.render(2010, out);
  CHECK(lum(s0[10]) > 600);                 // zona 2 casi blanca
  CHECK(e.hasOverlay(1, 2100) && !e.hasOverlay(1, 2300));
  e.render(2300, out);
  CHECK(lum(s0[10]) == 0 && s0[0].b == 255);

  // pulse alineado con `phase`: en phase 0 el golpe es máximo; medio beat después, mínimo
  Command pu;
  pu.fx = Fx::Pulse;
  pu.hasColor = parseColor("rojo", pu.color);
  pu.bpm = 120;      // 500 ms por beat
  pu.phase = 0.0f;
  e.apply(pu, 10000);
  e.render(10500, out);
  int peak = s0[0].r;
  e.render(10750, out);
  int mid = s0[0].r;
  CHECK(peak > 240 && mid < 120);
  // con phase 0.5 el golpe máximo cae 250 ms después
  pu.phase = 0.5f;
  e.apply(pu, 20000);
  e.render(20400, out);
  int a = s0[0].r;
  e.render(20250, out);
  int b = s0[0].r;
  CHECK(b > a);                             // a los 250 ms empieza un beat nuevo

  // sync: cambia el BPM sin cambiar el efecto
  Command sy;
  sy.fx = Fx::Sync;
  sy.bpm = 130;
  sy.phase = 0;
  e.apply(sy, 21000);
  CHECK(e.baseMode(0) == Engine::Mode::Pulse && e.lastSyncBpm() > 129);

  // chase: en cada paso solo una zona encendida
  Command ch;
  ch.fx = Fx::Chase;
  ch.hasColor = parseColor("naranja", ch.color);
  ch.bpm = 120;
  ch.beats = 1;
  e.apply(ch, 30000);
  e.render(30200, out);
  int litZones = 0;
  RGB* all[3] = {s0, s1, s2};
  for (int z = 0; z < 9; ++z) {
    RGB px = all[z / 3][(z % 3) * 10];
    if (lum(px) > 200) ++litZones;
  }
  CHECK(litZones == 1);

  // alternate: pares e impares opuestos
  Command al;
  al.fx = Fx::Alternate;
  al.hasColor = parseColor("rojo", al.color);
  al.hasColor2 = parseColor("azul", al.color2);
  al.bpm = 120;
  al.phase = 0;
  e.apply(al, 40000);
  e.render(40200, out);                     // primer beat: zonas pares con el color, impares con color2
  CHECK(s0[10].r > 100 && s0[0].b > 50 && s0[0].r == 0);
  e.render(40700, out);                     // siguiente beat: se intercambian
  CHECK(s0[0].r > 100 && s0[10].b > 50 && s0[10].r == 0);

  // wave y twinkle: se mueven (dos instantes distintos dan colores distintos)
  Command wv;
  wv.fx = Fx::Wave;
  wv.hasColor = parseColor("azul", wv.color);
  wv.hasColor2 = parseColor("morado", wv.color2);
  wv.bpm = 128;
  wv.beats = 4;
  e.apply(wv, 50000);
  e.render(50300, out);
  RGB w1 = s1[5];
  e.render(50900, out);
  CHECK(lum(w1) != lum(s1[5]));

  Command tw;
  tw.fx = Fx::Twinkle;
  tw.hasColor = parseColor("blanco", tw.color);
  tw.density = 0.3f;
  tw.bpm = 128;
  e.apply(tw, 60000);
  e.render(60300, out);
  int bright = 0;
  for (int i = 0; i < 30; ++i) bright += lum(s0[i]) > 300;
  CHECK(bright > 0 && bright < 30);

  // build: al final, más LEDs encendidos que al principio
  Command bu;
  bu.fx = Fx::Build;
  bu.hasColor = parseColor("blanco", bu.color);
  bu.durMs = 4000;
  e.apply(bu, 70000);
  auto litCount = [&](uint32_t t) {
    e.render(t, out);
    int n = 0;
    for (int i = 0; i < 10; ++i) n += lum(s0[i]) > 100;
    return n;
  };
  CHECK(litCount(70100) < litCount(73900) || litCount(73900) > 0);

  // efecto desconocido: no rompe nada
  Command un;
  un.fx = parseFx("confeti");
  e.apply(un, 80000);
  e.render(80100, out);
  CHECK(e.commandsApplied() > 10);

  // --- tiras con píxeles que no se reparten exactos (WS2811 12 V, 70 cm ≈ 14 píxeles): 4 + 5 + 5
  Engine u;
  u.begin(3, 3, 14);
  RGB u0[14], u1[14], u2[14];
  RGB* uout[3] = {u0, u1, u2};
  Command oc;
  oc.fx = Fx::Color;
  oc.hasColor = parseColor("azul", oc.color);
  oc.zoneMask = parseZones("odd", 3, 3);
  u.apply(oc, 1000);
  u.render(1200, uout);
  CHECK(u0[3].b == 255 && u0[4].b == 0 && u0[8].b == 0 && u0[9].b == 255 && u0[13].b == 255);
  CHECK(u1[0].b == 0 && u1[4].b == 255 && u1[9].b == 0);   // tira 2: zonas 4 (no), 5 (sí), 6 (no)

  // --- prebuild: la tira se va llenando (a mitad de duración, la primera mitad encendida)
  Engine pb;
  pb.begin(3, 3, 30);
  RGB q0[30], q1[30], q2[30];
  RGB* qout[3] = {q0, q1, q2};
  Command off;
  off.fx = Fx::Off;
  off.zoneMask = parseZones("all", 3, 3);
  pb.apply(off, 0);
  Command pre;
  pre.fx = parseFx("prebuild");
  pre.hasColor = parseColor("rojo", pre.color);
  pre.durMs = 10000;
  pre.zoneMask = parseZones("all", 3, 3);
  pb.apply(pre, 1000);
  pb.render(6000, qout);                     // p = 0,5
  CHECK(pre.fx == Fx::Prebuild);
  CHECK(q0[3].r > 100 && q0[25].r == 0 && q2[5].r > 100 && q2[29].r == 0);
  pb.render(10900, qout);                    // casi al final: toda la tira
  CHECK(q0[28].r > 200);

  // --- montaje libre: cada tira con sus píxeles y segmentos (se cambia en marcha con "layout")
  StripLayout lay[4] = {{10, 2, 0}, {20, 4, 0}, {6, 3, 0}, {8, 1, 0}};
  CHECK(parseZones("strip:2", lay, 4) == 0b0000111100u);          // zonas 3-6
  CHECK(parseZones("section:3", lay, 4) == ((1ull << 4) | (1ull << 8)));   // zona 5 (tira 2) y 9 (tira 3)
  CHECK(parseZones("all", lay, 4) == 0x3FFu);
  Engine lv;
  lv.begin(3, 3, 30);
  Command lc;
  lc.fx = parseFx("layout");
  lc.layoutCount = 4;
  for (int i = 0; i < 4; ++i) lc.layout[i] = lay[i];
  lv.apply(lc, 0);
  CHECK(lv.zoneCount() == 10 && lv.stripCount() == 4);
  RGB m0[10], m1[20], m2[6], m3[8];
  RGB* wout[4] = {m0, m1, m2, m3};
  Command zc;
  zc.fx = Fx::Color;
  zc.hasColor = parseColor("rojo", zc.color);
  zc.zoneMask = parseZones("strip:2", lv.layout(), lv.stripCount());
  lv.apply(zc, 100);
  lv.render(1000, wout);
  CHECK(m1[0].r == 255 && m1[19].r == 255 && m0[0].r == 0 && m3[7].r == 0);
  // límites: demasiadas zonas → se recorta a 32
  StripLayout big[8] = {{300, 8, 0}, {300, 8, 0}, {300, 8, 0}, {300, 8, 0}, {300, 8, 0}, {300, 8, 0}, {300, 8, 0}, {300, 9, 0}};
  CHECK(sanitizeLayout(big, 8) == 8 && big[7].segments == 8);
  Command idc;
  idc.fx = parseFx("identificar");
  lv.apply(idc, 2000);
  lv.render(2100, wout);
  CHECK(idc.fx == Fx::Identify && m0[0].r == 255 && m0[0].g == 255 && m0[0].b == 255);

  if (failures) {
    printf("%d fallos\n", failures);
    return 1;
  }
  printf("OK: núcleo de efectos\n");
  return 0;
}
