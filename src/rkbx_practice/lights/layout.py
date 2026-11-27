"""Montaje de las luces: canales (tiras), píxeles y segmentos de cada una.

Pedido del usuario: un mantenimiento para cambiar el montaje sin reprogramar (un día 3 tiras,
otro 4; un día 3 segmentos por tira, otro 4). Se guarda en `lights.layout` de config.json y se
envía al ESP32 con el comando `layout`, que lo guarda en su memoria.

Formato en config.json:

```json
"layout": {"strips": [{"leds": 14, "segments": 3}, {"leds": 14, "segments": 3}]}
```

- `leds`: píxeles direccionables de la tira (en WS2811 de 12 V, 1 píxel = grupo de 3 LEDs).
- `segments`: zonas en que se divide la tira (a partes iguales).
- Las zonas se numeran tira a tira: tira 1 → 1..s1, tira 2 → s1+1.., etc.
- Cada canal tiene un pin fijo en el ESP32 (`CHANNEL_PINS`, igual que `config.h`).

También se acepta el formato antiguo `{"strips": 3, "sections_per_strip": 3, "leds_per_section": 20}`.
"""

from __future__ import annotations

from dataclasses import dataclass

MAX_STRIPS = 4           # mismos límites que el firmware (lights_core.h)
MAX_SEGMENTS = 8
MAX_LEDS = 300
MAX_ZONES = 32
CHANNEL_PINS = (25, 26, 27, 14)


@dataclass(frozen=True)
class StripLayout:
    leds: int
    segments: int


def normalize_layout(raw) -> list[StripLayout]:
    """Lista de tiras válida (con los límites del firmware) a partir de config.json."""
    strips: list[StripLayout] = []
    if isinstance(raw, dict) and isinstance(raw.get("strips"), list):
        for s in raw["strips"]:
            if isinstance(s, dict):
                strips.append(StripLayout(int(s.get("leds", s.get("pixels", 14)) or 1),
                                          int(s.get("segments", 3) or 1)))
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
        out.append(StripLayout(leds, seg))
        zones += seg
    return out


def layout_to_config(strips: list[StripLayout]) -> dict:
    return {"strips": [{"leds": s.leds, "segments": s.segments} for s in strips]}


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
    parts = [f"T{i}: {s.leds} píx · {s.segments} seg" for i, s in enumerate(strips, start=1)]
    return f"{len(strips)} tiras · {total_zones(strips)} zonas ({'; '.join(parts)})"


def layout_command(strips: list[StripLayout]):
    from .commands import LightCommand
    return LightCommand("layout", layout_to_config(strips))
