"""Estado de una sesión de práctica en curso: tachado, sugerencias, comodines y recálculo.

- El tachado ocurre al CARGAR un tema en un deck (`track_loaded`).
- Un tema cargado por error se puede destachar (`unstrike`): vuelve a estar disponible.
- El tachado no persiste entre sesiones: cada práctica empieza de cero.
- Si se carga un tema distinto del sugerido, el resto de la sesión se
  recalcula a partir de él.
- Con probabilidad `wildcard_probability` la sugerencia es un comodín: un tema
  fuera del orden previsto pero compatible en tono y BPM con el último cargado.
"""

from __future__ import annotations

import random
import threading
from dataclasses import dataclass, field, replace
from datetime import datetime

from ..config import SessionConfig
from ..library.models import Track
from .compatibility import TransitionInfo, transition
from .engine import GeneratedSession, order_tracks


@dataclass(frozen=True)
class Suggestion:
    track: Track
    is_wildcard: bool
    from_anchor: TransitionInfo | None


@dataclass(frozen=True)
class StrikeInfo:
    deck: int | None
    at: datetime


@dataclass
class PracticeSession:
    source_name: str
    settings: SessionConfig
    plan: list[Track]
    excluded: list[tuple[Track, str]] = field(default_factory=list)
    rng: random.Random = field(default_factory=random.Random)

    def __post_init__(self) -> None:
        self._lock = threading.RLock()
        self._by_id = {t.id: t for t in self.plan}
        # Orden que ve el usuario: fijo. El recálculo solo cambia `plan` (orden interno de
        # las próximas sugerencias); así la lista no "salta" y el tachado se ve en su sitio.
        self.display_order: list[Track] = list(self.plan)
        self.struck: dict[str, StrikeInfo] = {}
        self.skipped: set[str] = set()      # sugeridos que el usuario se saltó (cargó otro)
        self.load_order: list[str] = []      # temas de la sesión cargados, en orden
        self.anchor: Track | None = None     # último tema cargado (de la sesión o no)
        self._suggestion: Suggestion | None = None
        self._recompute_suggestion()

    @classmethod
    def from_generated(cls, name: str, gen: GeneratedSession, settings: SessionConfig,
                       rng: random.Random | None = None) -> "PracticeSession":
        return cls(source_name=name, settings=settings, plan=gen.tracks,
                   excluded=gen.excluded, rng=rng or random.Random())

    # ------------------------------------------------------------------
    def contains(self, track_id: str) -> bool:
        return track_id in self._by_id

    def is_struck(self, track_id: str) -> bool:
        return track_id in self.struck

    @property
    def suggestion(self) -> Suggestion | None:
        return self._suggestion

    @property
    def remaining(self) -> list[Track]:
        return [t for t in self.plan if t.id not in self.struck]

    def is_skipped(self, track_id: str) -> bool:
        return track_id in self.skipped and track_id not in self.struck

    def entries(self) -> list[tuple[Track, bool]]:
        """Lista para la UI: (tema, tachado) en el orden original de la sesión (no cambia)."""
        with self._lock:
            return [(t, t.id in self.struck) for t in self.display_order]

    # ------------------------------------------------------------------
    def track_loaded(self, track: Track, deck: int | None = None,
                     at: datetime | None = None) -> bool:
        """Se ha cargado `track` en un deck. Devuelve True si era de la sesión."""
        with self._lock:
            at = at or datetime.now()
            in_session = track.id in self._by_id
            sug = self._suggestion
            followed_plan = (sug is not None and not sug.is_wildcard and sug.track.id == track.id)
            if sug is not None and sug.track.id != track.id and sug.track.id not in self.struck:
                self.skipped.add(sug.track.id)
            self.skipped.discard(track.id)
            if in_session and track.id not in self.struck:
                self.struck[track.id] = StrikeInfo(deck, at)
                self.load_order.append(track.id)
            self.anchor = track
            if not followed_plan:
                self._replan_from(track)
            self._recompute_suggestion()
            return in_session

    def unstrike(self, track_id: str) -> bool:
        """Destacha un tema (cargado por error). Vuelve a estar disponible."""
        with self._lock:
            if track_id not in self.struck:
                return False
            del self.struck[track_id]
            if track_id in self.load_order:
                self.load_order.remove(track_id)
            if self.anchor is not None and self.anchor.id == track_id:
                self.anchor = self._by_id[self.load_order[-1]] if self.load_order else None
            self._replan_from(self.anchor)
            self._recompute_suggestion()
            return True

    def reroll(self) -> None:
        """Vuelve a calcular la sugerencia (puede salir otro comodín)."""
        with self._lock:
            self._recompute_suggestion()

    # ------------------------------------------------------------------
    def _replan_from(self, anchor: Track | None) -> None:
        played = [self._by_id[tid] for tid in self.load_order]
        rest = [t for t in self.plan if t.id not in self.struck]
        if anchor is not None and rest:
            # Recálculo en directo: presupuesto de optimización corto para no bloquear la UI.
            quick = replace(self.settings,
                            optimize_time_budget_s=min(self.settings.optimize_time_budget_s, 0.25))
            rest = order_tracks(rest, quick, start=anchor, rng=self.rng)
        self.plan = played + rest

    def _planned_next(self) -> Track | None:
        remaining = self.remaining
        if not remaining:
            return None
        if self.anchor is None or self.anchor.id not in self._by_id:
            return remaining[0]
        idx = next(i for i, t in enumerate(self.plan) if t.id == self.anchor.id)
        for t in self.plan[idx + 1:]:
            if t.id not in self.struck:
                return t
        return remaining[0]

    def _recompute_suggestion(self) -> None:
        planned = self._planned_next()
        if planned is None:
            self._suggestion = None
            return
        anchor = self.anchor
        if anchor is not None and self.rng.random() < self.settings.wildcard_probability:
            candidates = [t for t in self.remaining
                          if t.id != planned.id and t.id != anchor.id
                          and transition(anchor, t, self.settings).compatible]
            if candidates:
                pick = self.rng.choice(candidates)
                self._suggestion = Suggestion(pick, True, transition(anchor, pick, self.settings))
                return
        self._suggestion = Suggestion(planned, False,
                                      transition(anchor, planned, self.settings) if anchor else None)
