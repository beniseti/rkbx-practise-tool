"""Propuesta de formato de comandos UDP para el ESP32 (pendiente de confirmar, CLAUDE.md §11/§13).

Cada efecto tiene su propio conjunto de parámetros, así que el comando es un
objeto JSON con `fx` y parámetros libres:

    {"fx": "flash", "color": "rojo", "dur_ms": 200}
    {"fx": "strobe", "dur_ms": 1000}
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass(frozen=True)
class LightCommand:
    fx: str
    params: dict = field(default_factory=dict)

    def to_json(self) -> bytes:
        return json.dumps({"fx": self.fx, **self.params}, ensure_ascii=False,
                          separators=(",", ":")).encode("utf-8")

    @classmethod
    def from_dict(cls, data: dict) -> "LightCommand":
        data = dict(data)
        return cls(fx=data.pop("fx"), params=data)


def parse_params(text: str) -> dict:
    """`color=rojo dur_ms=200 zones=1,5,9` → {"color": "rojo", "dur_ms": 200, "zones": "1,5,9"}.
    Los números se convierten; las listas con comas se quedan como texto."""
    out: dict = {}
    for part in text.split():
        part = part.rstrip(",;")
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        k, v = k.strip(), v.strip()
        if not k:
            continue
        try:
            out[k] = int(v)
        except ValueError:
            try:
                out[k] = float(v)
            except ValueError:
                out[k] = v
    return out


def format_params(params: dict) -> str:
    return " ".join(f"{k}={v}" for k, v in params.items())


def resolve_live(params: dict, bpm: float | None) -> dict:
    """Sustituye los valores "live" por el BPM actual (si no se sabe, se quita el parámetro)."""
    out = {}
    for k, v in params.items():
        if v == "live":
            if not bpm:
                continue
            v = round(float(bpm), 2)
        out[k] = v
    return out


def expand_zones(spec, strips: int = 3, sections: int = 3) -> list[int]:
    """Zonas (1-based) a las que va un efecto. Zona = (tira - 1) × secciones + sección.

    `all` (o vacío) · `odd`/`impares` · `even`/`pares` · `strip:2`/`tira:2` · `section:2`/`seccion:2` ·
    lista `1,5,9` (o [1, 5, 9]). Las zonas fuera de rango se ignoran.
    """
    total = strips * sections
    every = list(range(1, total + 1))
    if spec is None or spec == "" or spec == "all" or spec == "todas":
        return every
    if isinstance(spec, (list, tuple)):
        items = [int(x) for x in spec]
    else:
        text = str(spec).strip().lower()
        if text in ("odd", "impares"):
            return [z for z in every if z % 2 == 1]
        if text in ("even", "pares"):
            return [z for z in every if z % 2 == 0]
        if ":" in text:
            kind, _, num = text.partition(":")
            n = int(num)
            if kind in ("strip", "tira"):
                return [z for z in every if (z - 1) // sections + 1 == n]
            if kind in ("section", "seccion", "sección"):
                return [z for z in every if (z - 1) % sections + 1 == n]
            raise ValueError(f"zona no válida: {spec}")
        items = [int(x) for x in text.replace(" ", "").split(",") if x]
    return [z for z in items if 1 <= z <= total]
