"""Detección de bass swaps.

El EQ de graves de un deck baja (cruza `eq_low_threshold` hacia abajo) mientras
el de otro deck sube (lo cruza hacia arriba), y ambos cruces ocurren dentro de
`bass_swap_window_beats`. Solo cuentan decks que suenan (volumen efectivo por
encima de `fader_zero_threshold`) o, para el que sube, que están entrando.

Se guarda si coincidió con inicio de compás o de frase del deck entrante (el
que recibe los graves), usando la rejilla y las frases de Rekordbox, con una
tolerancia de `bass_swap_bar_tolerance_beats` (los giros de EQ son menos
precisos que un corte de fader).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from ..config import DetectionConfig
from ..events import BassSwap
from ..library.anlz import BeatGrid
from .frame import Frame

GridLookup = Callable[[str | None], BeatGrid | None]

_HYSTERESIS = 0.02


@dataclass
class _EqState:
    low: bool | None = None
    last_down: float | None = None
    last_up: float | None = None
    used_down: set = field(default_factory=set)
    used_up: set = field(default_factory=set)


class BassSwapDetector:
    def __init__(self, cfg: DetectionConfig, grids: GridLookup | None = None,
                 bar_tolerance_beats: float = 0.5):
        self.cfg = cfg
        self.grids = grids or (lambda _p: None)
        self.bar_tolerance_beats = bar_tolerance_beats
        self._st: dict[int, _EqState] = {}

    def update(self, frame: Frame) -> list[BassSwap]:
        thr = self.cfg.eq_low_threshold
        events: list[BassSwap] = []
        changed: list[tuple[int, str]] = []
        for d, df in frame.decks.items():
            st = self._st.setdefault(d, _EqState())
            if df.eq_low is None:
                continue
            if st.low is None:
                st.low = df.eq_low < thr
                continue
            if not st.low and df.eq_low < thr - _HYSTERESIS:
                st.low = True
                st.last_down = frame.t
                changed.append((d, "down"))
            elif st.low and df.eq_low > thr + _HYSTERESIS:
                st.low = False
                st.last_up = frame.t
                changed.append((d, "up"))

        for d, kind in changed:
            st = self._st[d]
            own_t = st.last_up if kind == "up" else st.last_down
            own_used = st.used_up if kind == "up" else st.used_down
            if own_t in own_used:
                continue                      # ya emparejado en este mismo sondeo
            partner = self._find_partner(frame, d, "down" if kind == "up" else "up")
            if partner is None:
                continue
            own_used.add(own_t)
            if kind == "up":
                events.append(self._make(frame, from_deck=partner, to_deck=d))
            else:
                events.append(self._make(frame, from_deck=d, to_deck=partner))
        return events

    def _find_partner(self, frame: Frame, deck: int, want: str) -> int | None:
        me = frame.decks[deck]
        best, best_dt = None, None
        for d, st in self._st.items():
            if d == deck or d not in frame.decks:
                continue
            other = frame.decks[d]
            t_evt = st.last_down if want == "down" else st.last_up
            if t_evt is None:
                continue
            used = st.used_down if want == "down" else st.used_up
            if t_evt in used:
                continue
            # Ambos decks deben estar sonando.
            if (me.volume or 0.0) <= self.cfg.fader_zero_threshold:
                continue
            if (other.volume or 0.0) <= self.cfg.fader_zero_threshold:
                continue
            beat = max(me.beat_s(self.cfg.default_bpm), other.beat_s(self.cfg.default_bpm))
            dt = frame.t - t_evt
            if 0 <= dt <= self.cfg.bass_swap_window_beats * beat and (best_dt is None or dt < best_dt):
                best, best_dt = d, dt
        if best is not None:
            st = self._st[best]
            (st.used_down if want == "down" else st.used_up).add(
                st.last_down if want == "down" else st.last_up)
        return best

    def _make(self, frame: Frame, from_deck: int, to_deck: int) -> BassSwap:
        a, b = self._st[from_deck], self._st[to_deck]
        t_down, t_up = a.last_down or frame.t, b.last_up or frame.t
        fin = frame.decks[to_deck]
        beat = fin.beat_s(self.cfg.default_bpm)
        grid = self.grids(fin.track_path)
        on_bar = on_phrase = None
        if grid is not None and fin.position_s is not None:
            # Posición del entrante en el momento en que suben sus graves.
            pos = fin.position_s - (frame.t - t_up)
            tol = self.bar_tolerance_beats * beat
            on_bar = grid.is_bar_start(pos, tol)
            on_phrase = grid.is_phrase_start(pos, tol)
        return BassSwap(
            at=frame.wall_at(max(t_down, t_up)), t=max(t_down, t_up),
            from_deck=from_deck, to_deck=to_deck,
            from_track_id=frame.decks[from_deck].track_id, to_track_id=fin.track_id,
            gap_beats=abs(t_up - t_down) / beat, on_bar=on_bar, on_phrase=on_phrase)
