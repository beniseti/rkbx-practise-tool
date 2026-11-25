"""Paleta de colores por tema (pedido del usuario: mismos tonos dentro de una canción).

El color principal sale de la tonalidad en la rueda Camelot (12 posiciones → 12 tonos), así temas
armónicamente vecinos tienen colores vecinos; menores (A) más saturados, mayores (B) más claros.
Sin tonalidad, el tono sale de un hash estable del ID del tema.

Las reglas usan los marcadores `$primary`, `$secondary` y `$accent`, que se sustituyen al generar.
"""

from __future__ import annotations

import colorsys
import hashlib

ROLES = ("primary", "secondary", "accent")


def _hex(h: float, s: float, v: float) -> str:
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, max(0.0, min(1.0, s)), max(0.0, min(1.0, v)))
    return "#{:02x}{:02x}{:02x}".format(round(r * 255), round(g * 255), round(b * 255))


def palette_for(camelot: str | None = None, track_id: str | None = None) -> dict[str, str]:
    hue = None
    minor = True
    if camelot:
        try:
            n = int(str(camelot)[:-1])
            minor = str(camelot)[-1].upper() == "A"
            hue = ((n - 1) / 12.0) % 1.0
        except (ValueError, IndexError):
            hue = None
    if hue is None:
        digest = hashlib.sha1(str(track_id or "").encode()).digest()
        hue = digest[0] / 256.0
    sat = 1.0 if minor else 0.8
    return {
        "primary": _hex(hue, sat, 1.0),
        "secondary": _hex(hue + 0.08, sat * 0.9, 0.85),   # tono vecino
        "accent": _hex(hue + 0.5, sat, 1.0),              # complementario, para los momentos fuertes
    }


def apply_palette(params: dict, palette: dict[str, str] | None) -> dict:
    """Sustituye `$primary`/`$secondary`/`$accent` por los colores del tema."""
    if not palette:
        return dict(params)
    out = {}
    for k, v in params.items():
        if isinstance(v, str) and v.startswith("$") and v[1:] in palette:
            v = palette[v[1:]]
        out[k] = v
    return out
