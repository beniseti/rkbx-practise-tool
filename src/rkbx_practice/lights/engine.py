"""Motor de luces en directo.

En cada sondeo:
1. Deck que manda: el de más volumen efectivo; en empate, el último que entró
   (`LeadDeckTracker`).
2. Su posición en el tema (segundos) se adelanta `latency_compensation_ms` (convertido a tiempo
   del tema según el pitch) y se compara con los eventos del archivo del tema (en beats → segundos
   con la rejilla de Rekordbox).
3. Se envían los eventos cruzados desde el sondeo anterior.

No se disparan eventos atrasados: al cambiar de deck que manda, al cambiar de tema, tras un salto
(loop, hot cue, arrastre) o con el deck parado, solo se resincroniza la posición.
"""

from __future__ import annotations

import bisect
import logging
from typing import Callable

from ..config import LightsConfig, MixerConfig, DetectionConfig
from ..detection.volume import LeadDeckTracker, effective_volume
from ..library.anlz import BeatGrid
from ..state import LiveSnapshot
from .commands import LightCommand
from .show import LightEvent, LightShow

log = logging.getLogger(__name__)

ShowLookup = Callable[[str | None], LightShow | None]
GridLookup = Callable[[str | None], BeatGrid | None]
Sender = Callable[[LightCommand], object]


class LightsEngine:
    def __init__(self, cfg: LightsConfig, mixer: MixerConfig, detection: DetectionConfig,
                 shows: ShowLookup, grids: GridLookup, send: Sender,
                 track_bpm: Callable[[str | None], float | None] | None = None):
        self.cfg = cfg
        self.mixer = mixer
        self.shows = shows
        self.grids = grids
        self.send = send
        self.track_bpm = track_bpm or (lambda _tid: None)
        self.lead = LeadDeckTracker(detection.fader_zero_threshold)
        self._key: tuple | None = None          # (deck, track_id) que estamos siguiendo
        self._last_pos: float | None = None
        self._times: list[float] = []
        self._events: list[LightEvent] = []
        self.lead_deck: int | None = None
        self.last_sent: tuple[LightEvent, float] | None = None
        self.enabled = cfg.enabled

    # ------------------------------------------------------------------
    def _load(self, track_id: str | None, track_path: str | None) -> None:
        show = self.shows(track_id)
        grid = self.grids(track_path)
        self._events, self._times = [], []
        if show is None or grid is None:
            return
        evs = [e for e in show.sorted_events() if e.fx and 1 <= e.beat <= len(grid.times)]
        self._events = evs
        self._times = [grid.times[e.beat - 1] for e in evs]

    def update(self, snap: LiveSnapshot) -> list[LightEvent]:
        if not self.enabled:
            return []
        volumes = {d.deck: effective_volume(d, snap.mixer, self.mixer) for d in snap.decks}
        lead = self.lead.update(snap.t, volumes)
        self.lead_deck = lead
        if lead is None:
            self._key, self._last_pos = None, None
            return []
        d = snap.deck(lead)
        if d is None or d.position_s is None:
            return []
        key = (lead, d.track_id or d.track_path)
        if key != self._key:
            self._key = key
            self._load(d.track_id, d.track_path)
            self._last_pos = None
        if not self._events:
            # El archivo o la rejilla pueden llegar más tarde (lectura en segundo plano).
            self._load(d.track_id, d.track_path)
            if not self._events:
                self._last_pos = d.position_s
                return []
        # Adelanto por latencia, en tiempo del tema (el pitch acelera o frena el tema).
        rate = 1.0
        orig = self.track_bpm(d.track_id)
        if d.bpm and orig:
            rate = d.bpm / orig
        ahead = d.position_s + self.cfg.latency_compensation_ms / 1000.0 * rate
        last = self._last_pos
        self._last_pos = ahead
        if last is None or d.jumped or d.playing is False or ahead < last:
            return []                       # resincronizar sin disparar nada atrasado
        i = bisect.bisect_right(self._times, last)
        j = bisect.bisect_right(self._times, ahead)
        fired = self._events[i:j]
        for ev in fired:
            cmd = ev.command()
            if cmd is not None:
                self.send(cmd)
                self.last_sent = (ev, snap.t)
        return fired
