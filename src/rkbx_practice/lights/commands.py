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
    """`color=rojo dur_ms=200` → {"color": "rojo", "dur_ms": 200}. Los números se convierten."""
    out: dict = {}
    for part in text.replace(",", " ").split():
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
