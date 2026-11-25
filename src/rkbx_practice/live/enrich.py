"""Completa las lecturas crudas: ID del tema, reproducción y saltos de posición."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..library.memory_library import InMemoryLibrary
from ..memory.deck_reader import ReadResult
from ..state import DeckState, LiveSnapshot


@dataclass
class _Prev:
    t: float
    position: float | None
    track_key: str | None
    last_advance: float | None = None


class SnapshotEnricher:
    """Convierte un ReadResult en LiveSnapshot.

    - `track_id`: si la memoria no da el ID, se cruza la ruta ANLZ con master.db.
    - `playing`: la posición avanza entre lecturas.
    - `jumped`: la posición cambia de forma distinta a lo esperado por el tempo
      (loops, hot cues, arrastre). Los detectores lo usan para no disparar
      eventos dos veces o a destiempo.
    """

    def __init__(self, library: InMemoryLibrary | None, jump_tolerance_s: float = 0.25,
                 stop_after_s: float = 0.3):
        self.library = library
        self.jump_tolerance_s = jump_tolerance_s
        # La posición en memoria puede no refrescarse en cada sondeo: un deck se
        # considera parado solo si no avanza durante `stop_after_s`.
        self.stop_after_s = stop_after_s
        self._prev: dict[int, _Prev] = {}
        self._resolve_cache: dict[str, str | None] = {}

    def reset(self) -> None:
        self._prev.clear()

    def _resolve(self, path: str | None) -> str | None:
        if not path or self.library is None:
            return None
        if path not in self._resolve_cache:
            t = self.library.track_by_analysis_path(path)
            self._resolve_cache[path] = t.id if t else None
        return self._resolve_cache[path]

    def enrich(self, res: ReadResult, t: float, wall: datetime | None = None) -> LiveSnapshot:
        decks = []
        for d in res.decks:
            track_id = d.track_id or self._resolve(d.track_path)
            key = track_id or d.track_path
            prev = self._prev.get(d.deck)
            playing, jumped = None, False
            last_advance = None
            if d.position_s is not None and prev is not None and prev.position is not None \
                    and prev.track_key == key:
                dt = t - prev.t
                delta = d.position_s - prev.position
                advanced = delta > 1e-4
                last_advance = t if advanced else prev.last_advance
                playing = last_advance is not None and t - last_advance <= self.stop_after_s
                rate = 1.0
                if d.bpm and self.library is not None and track_id:
                    tr = self.library.track(track_id)
                    if tr is not None and tr.bpm:
                        rate = d.bpm / tr.bpm
                # Si la posición no se refrescó en los sondeos anteriores, el avance
                # esperado cubre todo el tiempo desde el último avance.
                was_playing = (prev.last_advance is not None
                               and prev.t - prev.last_advance <= self.stop_after_s)
                since = (t - prev.last_advance) if was_playing else dt
                expected = since * rate if advanced else 0.0
                if advanced and abs(delta - expected) > self.jump_tolerance_s + dt * rate:
                    jumped = True
                if delta < -1e-4:
                    jumped = True
            self._prev[d.deck] = _Prev(t, d.position_s, key, last_advance)
            decks.append(d.with_(track_id=track_id, playing=playing, jumped=jumped))
        return LiveSnapshot(t=t, wall=wall or datetime.now(), decks=tuple(decks), mixer=res.mixer)
