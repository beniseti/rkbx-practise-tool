"""Estado en vivo de los decks y del mezclador (compartido por memoria, live, detección y UI)."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum


@dataclass(frozen=True)
class DeckState:
    deck: int                          # 1..4
    track_path: str | None = None      # ruta ANLZ leída de memoria
    track_id: str | None = None        # ID en master.db (resuelto o leído directamente)
    bpm: float | None = None           # BPM actual (con pitch)
    position_s: float | None = None    # posición de reproducción
    is_master: bool | None = None
    fader: float | None = None         # 0..1
    eq_low: float | None = None        # 0..1 (0 = graves cortados, 0.5 = centro)
    eq_mid: float | None = None        # 0..1 (medios)
    eq_high: float | None = None       # 0..1 (agudos)
    jumped: bool = False               # salto de posición (loop, hot cue, arrastre)
    playing: bool | None = None        # la posición avanza

    def with_(self, **kw) -> "DeckState":
        return replace(self, **kw)


@dataclass(frozen=True)
class MixerState:
    crossfader: float | None = None    # 0 = lado A, 1 = lado B


@dataclass(frozen=True)
class LiveSnapshot:
    t: float                           # reloj monotónico (s)
    wall: datetime                     # hora real
    decks: tuple[DeckState, ...]
    mixer: MixerState = field(default_factory=MixerState)

    def deck(self, n: int) -> DeckState | None:
        for d in self.decks:
            if d.deck == n:
                return d
        return None


class ConnectionMode(str, Enum):
    DISCONNECTED = "disconnected"   # Rekordbox no está abierto / no se pudo conectar
    LIMITED = "limited"             # conectado, pero sin offsets para esta versión
    FULL = "full"                   # lectura en vivo activa

    @property
    def label_es(self) -> str:
        return {"disconnected": "desconectado", "limited": "modo limitado",
                "full": "modo completo"}[self.value]


@dataclass(frozen=True)
class ConnectionStatus:
    mode: ConnectionMode
    version: str | None = None
    message: str = ""
