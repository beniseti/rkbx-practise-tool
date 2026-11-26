"""Fuente de demostración: simula dos decks pinchando las sugerencias de la sesión.

Sirve para ver la ventana y la CLI funcionando sin Rekordbox (o sin offsets) y
para tests de integración. Cada `track_s` segundos carga la sugerencia en el
deck libre y hace una transición de `blend_s` segundos con un bass swap a mitad.
"""

from __future__ import annotations

import time
from typing import Callable

from ..library.memory_library import InMemoryLibrary
from ..library.models import Track
from ..memory.deck_reader import ReadResult
from ..state import ConnectionMode, ConnectionStatus, DeckState, MixerState


class DemoSource:
    def __init__(self, library: InMemoryLibrary, next_track: Callable[[], Track | None],
                 track_s: float = 30.0, blend_s: float = 8.0, load_lead_s: float = 4.0,
                 clock: Callable[[], float] = time.monotonic):
        self.library = library
        self.next_track = next_track
        self.track_s = track_s
        self.blend_s = blend_s
        self.load_lead_s = load_lead_s
        self.clock = clock
        self.version = "demo"
        self._t0: float | None = None
        self._decks: dict[int, dict] = {}
        self._cycle = -1

    def connect(self) -> ConnectionStatus:
        self._t0 = self.clock()
        self._decks = {1: self._empty(), 2: self._empty()}
        self._cycle = -1
        return ConnectionStatus(ConnectionMode.FULL, "demo", "Modo demostración (datos simulados)")

    @staticmethod
    def _empty() -> dict:
        return {"track": None, "pos": 0.0, "started": None}

    def _load(self, deck: int, t: float) -> None:
        track = self.next_track()
        self._decks[deck] = {"track": track, "pos": 0.0, "started": None}

    def read(self) -> ReadResult:
        assert self._t0 is not None
        t = self.clock() - self._t0
        cycle = int(t // self.track_s)
        phase = t - cycle * self.track_s
        # Deck que suena en este ciclo y deck que entra al final.
        cur, nxt = (1, 2) if cycle % 2 == 0 else (2, 1)
        if cycle != self._cycle:
            self._cycle = cycle
            if cycle > 0:
                self._decks[nxt]["started"] = None     # el deck que acaba de salir se para
        if cycle == 0 and self._decks[1]["track"] is None and t >= 0.5:
            # La primera lectura va con los decks vacíos: así la primera carga también se tacha.
            self._load(1, t)
            self._decks[1]["started"] = t
        blend_start = self.track_s - self.blend_s
        if phase >= blend_start - self.load_lead_s and self._decks[nxt]["started"] is None \
                and self._decks[nxt].get("loaded_cycle") != cycle:
            self._load(nxt, t)
            self._decks[nxt]["loaded_cycle"] = cycle
        if phase >= blend_start and self._decks[nxt]["started"] is None and self._decks[nxt]["track"]:
            self._decks[nxt]["started"] = t

        x = min(1.0, max(0.0, (phase - blend_start) / self.blend_s))
        faders = {cur: 1.0 - max(0.0, x - 0.5) * 2.0, nxt: min(1.0, x * 2.0)}
        eq = {cur: 0.75 if x < 0.5 else 0.0, nxt: 0.0 if x < 0.5 else 0.75}
        decks = []
        for n in (1, 2):
            d = self._decks[n]
            track: Track | None = d["track"]
            playing = d["started"] is not None and track is not None
            pos = (t - d["started"]) if playing else 0.0
            decks.append(DeckState(
                deck=n,
                track_path=("C:/rekordbox/share" + track.analysis_path) if track and track.analysis_path else None,
                track_id=track.id if track and not track.analysis_path else None,
                bpm=track.bpm if track else None,
                position_s=pos if track else None,
                is_master=(n == cur),
                fader=faders.get(n, 0.0) if playing else 0.0,
                eq_low=eq.get(n, 0.75),
                eq_mid=0.5,
                eq_high=0.5,
            ))
        return ReadResult(tuple(decks), MixerState(crossfader=0.5), [], 1)

    def is_alive(self) -> bool:
        return True

    def close(self) -> None:
        pass
