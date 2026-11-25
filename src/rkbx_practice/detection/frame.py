"""Vista simplificada de una instantánea para los detectores."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from ..config import MixerConfig
from ..state import LiveSnapshot
from .volume import effective_volume


@dataclass(frozen=True)
class DeckFrame:
    deck: int
    volume: float | None        # volumen efectivo (fader × crossfader)
    audible: float | None       # volumen si el deck está reproduciendo, 0 si está parado
    track_id: str | None
    track_path: str | None
    bpm: float | None
    position_s: float | None
    eq_low: float | None
    jumped: bool

    def beat_s(self, default_bpm: float) -> float:
        bpm = self.bpm if self.bpm and self.bpm > 0 else default_bpm
        return 60.0 / bpm


@dataclass(frozen=True)
class Frame:
    t: float
    wall: datetime
    decks: dict[int, DeckFrame]

    def wall_at(self, t: float) -> datetime:
        """Hora real correspondiente a un instante monotónico anterior."""
        return self.wall - timedelta(seconds=self.t - t)


def build_frame(snap: LiveSnapshot, mixer_cfg: MixerConfig) -> Frame:
    decks = {}
    for d in snap.decks:
        vol = effective_volume(d, snap.mixer, mixer_cfg)
        audible = None if vol is None else (0.0 if d.playing is False else vol)
        decks[d.deck] = DeckFrame(
            deck=d.deck, volume=vol, audible=audible, track_id=d.track_id,
            track_path=d.track_path, bpm=d.bpm, position_s=d.position_s,
            eq_low=d.eq_low, jumped=d.jumped,
        )
    return Frame(t=snap.t, wall=snap.wall, decks=decks)
