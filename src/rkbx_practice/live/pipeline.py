"""Canalización en vivo: de una instantánea a eventos (carga de temas y detecciones)."""

from __future__ import annotations

from typing import Callable

from ..config import AppConfig
from ..detection import BassSwapDetector, FaderCutDetector, TransitionDetector, build_frame
from ..detection.frame import Frame
from ..events import LiveEvent, TrackLoaded
from ..library.anlz import BeatGrid
from ..state import LiveSnapshot

GridLookup = Callable[[str | None], BeatGrid | None]


class LivePipeline:
    def __init__(self, cfg: AppConfig, grids: GridLookup | None = None):
        self.cfg = cfg
        self.grids = grids
        self.reset()

    def reset(self) -> None:
        """Al (re)conectar: el estado inicial de los decks no genera eventos de carga."""
        self._loaded: dict[int, str | None] | None = None
        self.transitions = TransitionDetector(self.cfg.detection)
        self.cuts = FaderCutDetector(self.cfg.detection, self.grids)
        self.swaps = BassSwapDetector(self.cfg.detection, self.grids,
                                      self.cfg.detection.bass_swap_bar_tolerance_beats)
        self.last_frame: Frame | None = None

    def process(self, snap: LiveSnapshot) -> list[LiveEvent]:
        events: list[LiveEvent] = []
        current = {d.deck: (d.track_id or d.track_path) for d in snap.decks}
        if self._loaded is None:
            self._loaded = current
        else:
            for d in snap.decks:
                key = current[d.deck]
                if key and key != self._loaded.get(d.deck):
                    events.append(TrackLoaded(deck=d.deck, track_id=d.track_id,
                                              track_path=d.track_path, at=snap.wall, t=snap.t))
                self._loaded[d.deck] = key
        frame = build_frame(snap, self.cfg.mixer)
        events += self.transitions.update(frame)
        events += self.cuts.update(frame)
        events += self.swaps.update(frame)
        self.last_frame = frame
        return events

    def flush(self) -> list[LiveEvent]:
        """Cierra lo pendiente (al parar la grabación)."""
        events: list[LiveEvent] = []
        events += self.transitions.flush(self.last_frame)
        events += self.cuts.flush()
        return events
