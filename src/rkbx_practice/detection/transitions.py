"""Detección de transiciones como intervalos de tiempo.

Reglas (CLAUDE.md §10):
- Inicio: el volumen del deck entrante sale de cero (supera `fader_zero_threshold`).
- Fin: el volumen del deck saliente llega a cero. Si nunca baja del todo, la
  transición termina cuando el entrante supera al saliente y se mantiene así
  `transition_stable_s` segundos (el fin se fecha en el cruce).
- Toques de pre-escucha: si un deck que no sonaba sube y vuelve a cero en menos
  de `preview_touch_max_ms`, se descarta.
- Cortes secos (cambio en un compás) también son transiciones, muy cortas.

Decisiones de implementación:
- Se usa el volumen "audible": un deck parado cuenta como volumen 0. Así, si el
  saliente se para con el fader arriba, la transición termina ahí.
- Una bajada a cero del saliente se confirma solo si dura más de
  `cut_max_beats` (si vuelve antes, es un fader cut, no el fin).
- Mientras el saliente siga bajando se espera a que llegue a cero (regla
  principal). El fin por estabilidad solo se aplica cuando, además, el
  saliente lleva `transition_stable_s` sin bajar (se ha quedado a medias);
  entonces la transición se cierra fechada en el cruce.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import DetectionConfig
from ..events import TransitionEvent
from .frame import DeckFrame, Frame


@dataclass
class _Active:
    out_deck: int
    in_deck: int
    t_start: float
    out_track: str | None
    in_track: str | None
    out_bpm: float | None
    in_bpm: float | None
    cross_t: float | None = None
    out_min: float | None = None        # volumen mínimo del saliente hasta ahora
    out_last_drop: float | None = None  # última vez que el saliente siguió bajando


@dataclass
class TransitionDetector:
    cfg: DetectionConfig
    on_air: dict[int, float] = field(default_factory=dict)     # deck -> desde cuándo
    rising: dict[int, float] = field(default_factory=dict)     # deck -> subida pendiente de confirmar
    falling: dict[int, float] = field(default_factory=dict)    # deck -> bajada pendiente de confirmar
    active: list[_Active] = field(default_factory=list)
    last_left: tuple[int, float, str | None, float | None] | None = None
    _pending_cut: TransitionEvent | None = None
    _initialized: bool = False

    def _thr(self) -> float:
        return self.cfg.fader_zero_threshold

    def update(self, frame: Frame) -> list[TransitionEvent]:
        out: list[TransitionEvent] = []
        t = frame.t
        decks = frame.decks
        if not self._initialized:
            # Lo que ya suena al empezar no es una transición.
            for d, df in decks.items():
                if df.audible is not None and df.audible > self._thr():
                    self.on_air[d] = t
            self._initialized = True
            return out

        preview_s = self.cfg.preview_touch_max_ms / 1000.0
        for d, df in decks.items():
            if df.audible is None:
                continue
            on = df.audible > self._thr()
            if d in self.on_air:
                if not on:
                    self.falling.setdefault(d, t)
                elif d in self.falling:
                    del self.falling[d]            # era un corte: sigue sonando
                if d in self.falling:
                    window = self.cfg.cut_max_beats * df.beat_s(self.cfg.default_bpm)
                    if t - self.falling[d] > window:
                        t_fall = self.falling.pop(d)
                        del self.on_air[d]
                        out += self._deck_left(d, t_fall, df, frame)
            else:
                if on and d not in self.rising:
                    self.rising[d] = t
                elif not on and d in self.rising:
                    del self.rising[d]             # toque de pre-escucha: descartado
                if d in self.rising and t - self.rising[d] >= preview_s:
                    t_rise = self.rising.pop(d)
                    self.on_air[d] = t_rise
                    self._deck_entered(d, t_rise, df, frame)

        out += self._check_stability(frame)
        return out

    # ------------------------------------------------------------------
    def _deck_entered(self, d: int, t_rise: float, df: DeckFrame, frame: Frame) -> None:
        others = [o for o in self.on_air if o != d and frame.decks.get(o) is not None]
        if others:
            out_deck = max(others, key=lambda o: (frame.decks[o].audible or 0.0, -self.on_air[o]))
            o = frame.decks[out_deck]
            self.active = [a for a in self.active if a.in_deck != d]
            self.active.append(_Active(out_deck, d, t_rise, o.track_id, df.track_id, o.bpm, df.bpm))
            return
        if self.last_left is not None:
            ld, t_fall, ltrack, lbpm = self.last_left
            beat = 60.0 / (lbpm or self.cfg.default_bpm)
            # Corte seco: el saliente se fue menos de un compás antes o después de que
            # entrase este deck (el entrante aún estaba pendiente de confirmar).
            preview_s = self.cfg.preview_touch_max_ms / 1000.0
            if ld != d and abs(t_rise - t_fall) <= max(4 * beat, preview_s):
                t0, t1 = min(t_rise, t_fall), max(t_rise, t_fall)
                self._pending_cut = TransitionEvent(
                    out_deck=ld, in_deck=d, out_track_id=ltrack, in_track_id=df.track_id,
                    out_bpm=lbpm, in_bpm=df.bpm, start=frame.wall_at(t0),
                    end=frame.wall_at(t1), t_start=t0, t_end=t1)
                self.last_left = None

    def _deck_left(self, d: int, t_fall: float, df: DeckFrame, frame: Frame) -> list[TransitionEvent]:
        events = []
        keep = []
        for a in self.active:
            if a.out_deck == d:
                events.append(self._finish(a, t_fall, frame))
            elif a.in_deck == d:
                pass                                # el entrante se retiró: transición abortada
            else:
                keep.append(a)
        self.active = keep
        self.last_left = (d, t_fall, df.track_id, df.bpm)
        return events

    def _check_stability(self, frame: Frame) -> list[TransitionEvent]:
        events = []
        if self._pending_cut is not None:
            events.append(self._pending_cut)
            self._pending_cut = None
        keep = []
        for a in self.active:
            fin, fout = frame.decks.get(a.in_deck), frame.decks.get(a.out_deck)
            if fin is None or fout is None or fin.audible is None or fout.audible is None:
                keep.append(a)
                continue
            if a.out_min is None or fout.audible < a.out_min - 0.005:
                a.out_min = fout.audible
                a.out_last_drop = frame.t
            if fin.audible > fout.audible:
                if a.cross_t is None:
                    a.cross_t = frame.t
                elif (frame.t - a.cross_t >= self.cfg.transition_stable_s
                      and frame.t - (a.out_last_drop or a.t_start) >= self.cfg.transition_stable_s):
                    # El saliente ya no baja: no va a llegar a cero.
                    events.append(self._finish(a, a.cross_t, frame))
                    continue
            else:
                a.cross_t = None
            keep.append(a)
        self.active = keep
        return events

    @staticmethod
    def _finish(a: _Active, t_end: float, frame: Frame) -> TransitionEvent:
        return TransitionEvent(
            out_deck=a.out_deck, in_deck=a.in_deck, out_track_id=a.out_track, in_track_id=a.in_track,
            out_bpm=a.out_bpm, in_bpm=a.in_bpm, start=frame.wall_at(a.t_start),
            end=frame.wall_at(max(t_end, a.t_start)), t_start=a.t_start, t_end=max(t_end, a.t_start))

    def flush(self, frame: Frame | None) -> list[TransitionEvent]:
        """Al parar la grabación: cierra las transiciones cuyo fin por estabilidad estaba en curso."""
        events = []
        if frame is not None:
            for a in self.active:
                if a.cross_t is not None:
                    events.append(self._finish(a, a.cross_t, frame))
        self.active = []
        return events
