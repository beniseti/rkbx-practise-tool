"""Detección de fader cuts.

- Un deck que YA estaba sonando baja por debajo de `fader_zero_threshold` y
  vuelve a subir en menos de `cut_max_beats` (beats del propio deck).
- Cortes separados por menos de `cut_group_gap_beats` se agrupan en una ráfaga.
- Cada corte guarda si cayó a tiempo según la rejilla de beats (instante de la
  bajada, tolerancia `on_beat_tolerance_ms`).

Se usa el volumen efectivo (incluye crossfader si está activado), así también
se detectan cortes hechos con el crossfader.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from ..config import DetectionConfig
from ..events import CutBurst, FaderCut
from ..library.anlz import BeatGrid
from .frame import Frame

GridLookup = Callable[[str | None], BeatGrid | None]


@dataclass
class _DeckCutState:
    up_since: float | None = None
    down_at: float | None = None
    down_position: float | None = None
    burst: list[FaderCut] = field(default_factory=list)
    last_cut_end: float | None = None
    track_id: str | None = None


class FaderCutDetector:
    def __init__(self, cfg: DetectionConfig, grids: GridLookup | None = None):
        self.cfg = cfg
        self.grids = grids or (lambda _p: None)
        self._state: dict[int, _DeckCutState] = {}

    def update(self, frame: Frame) -> list[CutBurst]:
        out: list[CutBurst] = []
        thr = self.cfg.fader_zero_threshold
        tol = self.cfg.on_beat_tolerance_ms / 1000.0
        for d, df in frame.decks.items():
            st = self._state.setdefault(d, _DeckCutState())
            if df.volume is None:
                continue
            beat = df.beat_s(self.cfg.default_bpm)
            on = df.volume > thr
            if st.track_id != df.track_id:
                out += self._flush_deck(d, st)
                st.track_id = df.track_id
            if on:
                if st.down_at is not None:
                    dur = frame.t - st.down_at
                    if dur <= self.cfg.cut_max_beats * beat:
                        grid = self.grids(df.track_path)
                        offset = (grid.beat_offset_s(st.down_position)
                                  if grid is not None and st.down_position is not None else None)
                        cut = FaderCut(t=st.down_at, at=frame.wall_at(st.down_at), duration_s=dur,
                                       on_beat=None if offset is None else abs(offset) <= tol,
                                       beat_offset_ms=None if offset is None else offset * 1000.0)
                        if (st.burst and st.last_cut_end is not None
                                and st.down_at - st.last_cut_end >= self.cfg.cut_group_gap_beats * beat):
                            out += self._flush_deck(d, st)
                        st.burst.append(cut)
                        st.last_cut_end = frame.t
                    st.down_at = None
                    st.down_position = None
                    if st.up_since is None:
                        st.up_since = frame.t
                elif st.up_since is None:
                    st.up_since = frame.t
            else:
                # Solo cuenta si el deck ya estaba sonando (al menos un beat arriba).
                if st.up_since is not None and st.down_at is None and frame.t - st.up_since >= beat:
                    st.down_at = frame.t
                    st.down_position = df.position_s
                elif st.down_at is None:
                    st.up_since = None
                if st.down_at is not None and frame.t - st.down_at > self.cfg.cut_max_beats * beat:
                    st.down_at = None           # bajada larga: no es un corte
                    st.up_since = None
            if (st.burst and st.last_cut_end is not None and st.down_at is None
                    and frame.t - st.last_cut_end >= self.cfg.cut_group_gap_beats * beat):
                out += self._flush_deck(d, st)
        return out

    @staticmethod
    def _flush_deck(d: int, st: _DeckCutState) -> list[CutBurst]:
        if not st.burst:
            return []
        burst = CutBurst(deck=d, track_id=st.track_id, cuts=tuple(st.burst))
        st.burst = []
        st.last_cut_end = None
        return [burst]

    def flush(self) -> list[CutBurst]:
        out = []
        for d, st in self._state.items():
            out += self._flush_deck(d, st)
        return out
