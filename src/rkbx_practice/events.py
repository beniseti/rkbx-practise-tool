"""Eventos que produce el bucle en vivo (tachado, detecciones) y que consumen UI e histórico."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class TrackLoaded:
    deck: int
    track_id: str | None        # None si la ruta no se encontró en master.db
    track_path: str | None
    at: datetime
    t: float


@dataclass(frozen=True)
class TransitionEvent:
    out_deck: int
    in_deck: int
    out_track_id: str | None
    in_track_id: str | None
    out_bpm: float | None       # BPM en el momento de la transición (con pitch)
    in_bpm: float | None
    start: datetime
    end: datetime
    t_start: float
    t_end: float

    @property
    def duration_s(self) -> float:
        return max(0.0, self.t_end - self.t_start)


@dataclass(frozen=True)
class FaderCut:
    t: float
    at: datetime
    duration_s: float              # tiempo con el fader abajo
    on_beat: bool | None           # None si no hay rejilla de beats
    beat_offset_ms: float | None


@dataclass(frozen=True)
class CutBurst:
    deck: int
    track_id: str | None
    cuts: tuple[FaderCut, ...] = field(default_factory=tuple)

    @property
    def start(self) -> datetime:
        return self.cuts[0].at

    @property
    def count(self) -> int:
        return len(self.cuts)


@dataclass(frozen=True)
class BassSwap:
    at: datetime
    t: float
    from_deck: int                 # deck cuyos graves bajan
    to_deck: int                   # deck cuyos graves suben
    from_track_id: str | None
    to_track_id: str | None
    gap_beats: float               # separación entre bajada y subida
    on_bar: bool | None            # coincidió con inicio de compás (deck entrante)
    on_phrase: bool | None         # coincidió con inicio de frase (deck entrante)


LiveEvent = TrackLoaded | TransitionEvent | CutBurst | BassSwap
