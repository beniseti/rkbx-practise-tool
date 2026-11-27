"""Montaje de las luces: canales (tiras), píxeles y segmentos de cada una.

Pedido del usuario: un mantenimiento para cambiar el montaje sin reprogramar (un día 3 tiras,
otro 4; un día 3 segmentos por tira, otro 4). Se guarda en `lights.layout` de config.json y se
envía al ESP32 con el comando `layout`, que lo guarda en su memoria.

Formato en config.json:

```json
"layout": {"strips": [{"leds": 14, "segments": 3, "pin": 25}, {"leds": 14, "segments": 3, "pin": 26}]}
```

- `leds`: píxeles direccionables de la tira (en WS2811 de 12 V, 1 píxel = grupo de 3 LEDs).
- `segments`: zonas en que se divide la tira (a partes iguales).
- Las zonas se numeran tira a tira: tira 1 → 1..s1, tira 2 → s1+1.., etc.
- `pin`: GPIO del ESP32 donde va el cable de datos (se elige una vez; de `SAFE_PINS`). Cambiar
  píxeles o partes se aplica al momento; cambiar pines o el número de tiras reinicia el ESP32.

También se acepta el formato antiguo `{"strips": 3, "sections_per_strip": 3, "leds_per_section": 20}`.
"""

from __future__ import annotations

from dataclasses import dataclass

MAX_STRIPS = 8           # mismos límites que el firmware (lights_core.h)
MAX_SEGMENTS = 8
MAX_LEDS = 1000
MAX_ZONES = 64
CHANNEL_PINS = (25, 26, 27, 14, 32, 33, 13, 4)          # pin de fábrica de cada tira
# Pines que se pueden elegir (seguros como salida en un ESP32-WROOM-32D; igual que main.cpp).
SAFE_PINS = (4, 13, 14, 16, 17, 18, 19, 21, 22, 23, 25, 26, 27, 32, 33)


@dataclass(frozen=True)
class StripLayout:
    leds: int
    segments: int
    pin: int | None = None      # None = el de fábrica de su posición


def normalize_layout(raw) -> list[StripLayout]:
    """Lista de tiras válida (con los límites del firmware) a partir de config.json."""
    strips: list[StripLayout] = []
    if isinstance(raw, dict) and isinstance(raw.get("strips"), list):
        for s in raw["strips"]:
            if isinstance(s, dict):
                pin = s.get("pin")
                strips.append(StripLayout(int(s.get("leds", s.get("pixels", 14)) or 1),
                                          int(s.get("segments", 3) or 1),
                                          int(pin) if pin is not None else None))
    elif isinstance(raw, dict):            # formato antiguo
        n = int(raw.get("strips", 3) or 3)
        seg = int(raw.get("sections_per_strip", 3) or 3)
        leds = int(raw.get("leds_per_section", 5) or 5) * seg
        strips = [StripLayout(leds, seg) for _ in range(n)]
    if not strips:
        strips = [StripLayout(14, 3) for _ in range(3)]
    out, zones = [], 0
    for s in strips[:MAX_STRIPS]:
        leds = max(1, min(MAX_LEDS, s.leds))
        seg = max(1, min(MAX_SEGMENTS, s.segments, leds, MAX_ZONES - zones))
        if zones >= MAX_ZONES:
            break
        out.append(StripLayout(leds, seg, s.pin))
        zones += seg
    return _fix_pins(out)


def _fix_pins(strips: list[StripLayout]) -> list[StripLayout]:
    """Pines válidos y sin repetir (igual que fixPins en el firmware)."""
    order = list(CHANNEL_PINS) + [p for p in SAFE_PINS if p not in CHANNEL_PINS]
    out, used = [], set()
    for i, s in enumerate(strips):
        pin = s.pin if s.pin in SAFE_PINS and s.pin not in used else CHANNEL_PINS[i]
        if pin in used:
            pin = next(p for p in order if p not in used)
        used.add(pin)
        out.append(StripLayout(s.leds, s.segments, pin))
    return out


def free_pins(strips: list[StripLayout], index: int) -> list[int]:
    """Pines que puede usar la tira `index` (los suyos y los que no usan las demás)."""
    taken = {s.pin for i, s in enumerate(strips) if i != index}
    return [p for p in SAFE_PINS if p not in taken]


def needs_restart(old: list[StripLayout], new: list[StripLayout]) -> bool:
    """Cambiar pines o el número de tiras obliga al ESP32 a reiniciarse (un segundo)."""
    return [s.pin for s in old] != [s.pin for s in new]


def layout_to_config(strips: list[StripLayout]) -> dict:
    return {"strips": [{"leds": s.leds, "segments": s.segments, "pin": s.pin} for s in strips]}


def total_zones(strips: list[StripLayout]) -> int:
    return sum(s.segments for s in strips)


def zone_table(strips: list[StripLayout]) -> list[tuple[int, int, int, int]]:
    """(zona, tira, segmento, nº de píxeles) de cada zona, con el mismo reparto que el firmware."""
    rows, z = [], 1
    for si, s in enumerate(strips, start=1):
        for c in range(s.segments):
            a, b = c * s.leds // s.segments, (c + 1) * s.leds // s.segments
            rows.append((z, si, c + 1, b - a))
            z += 1
    return rows


def describe(strips: list[StripLayout]) -> str:
    parts = [f"T{i}: {s.leds} píx · {s.segments} seg · pin {s.pin}" for i, s in enumerate(strips, start=1)]
    return f"{len(strips)} tiras · {total_zones(strips)} zonas ({'; '.join(parts)})"


def layout_command(strips: list[StripLayout]):
    from .commands import LightCommand
    return LightCommand("layout", layout_to_config(strips))
