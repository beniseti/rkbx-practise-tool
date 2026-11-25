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
from .commands import LightCommand, resolve_live
from .show import LightEvent, LightShow

log = logging.getLogger(__name__)

# Efectos de fondo (se pueden reenviar al engancharse a mitad de una sección); el resto son
# temporales (flash, strobe, build, rainbow…) y no se repiten a destiempo.
BACKGROUND_FX = {"color", "off", "pulse", "breathe", "alternate", "chase", "wave", "twinkle"}

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
        self.status = "Luces sin datos todavía."
        self._mode: tuple | None = None         # situación del último efecto automático enviado

    # ------------------------------------------------------------------
    def _load(self, track_id: str | None, track_path: str | None) -> str | None:
        """Carga eventos y tiempos. Devuelve el motivo si no hay nada que disparar."""
        show = self.shows(track_id)
        grid = self.grids(track_path)
        self._events, self._times = [], []
        if show is None:
            return "no_show"
        if grid is None:
            return "no_grid"
        evs = [e for e in show.sorted_events() if e.fx and 1 <= e.beat <= len(grid.times)]
        if not evs:
            return "no_fx"
        self._events = evs
        self._times = [grid.times[e.beat - 1] for e in evs]
        return None

    def _set_status(self, text: str) -> None:
        self.status = text

    # --- efectos básicos -------------------------------------------------
    def _send_rule(self, mode: tuple, rule: dict, label: str, t: float, bpm: float | None) -> None:
        """Envía `rule` una sola vez por situación `mode` (no en cada sondeo)."""
        if self._mode == mode or not rule or not rule.get("fx"):
            self._mode = mode
            return
        self._mode = mode
        cmd = LightCommand(rule["fx"], resolve_live(dict(rule.get("params", {})), bpm))
        self.send(cmd)
        self.last_sent = (LightEvent(0, label, "auto", cmd.fx, cmd.params), t)

    def _catch_up(self, ahead: float, key: tuple, t: float, bpm: float | None) -> None:
        """Al engancharse a un tema ya empezado: el último efecto de fondo de la sección actual
        (los temporales —flash, strobe, build…— no se repiten a destiempo); si aún no ha llegado
        ninguno, el efecto básico."""
        j = bisect.bisect_right(self._times, ahead)
        for ev in reversed(self._events[:j]):
            if ev.fx in BACKGROUND_FX:
                if self._mode != ("show", key, ev.beat):
                    self._mode = ("show", key, ev.beat)
                    cmd = LightCommand(ev.fx, resolve_live(dict(ev.params), bpm))
                    self.send(cmd)
                    self.last_sent = (ev, t)
                return
        self._send_rule(("fallback", key), self.cfg.fallback, "Efecto básico", t, bpm)

    def update(self, snap: LiveSnapshot) -> list[LightEvent]:
        if not self.enabled:
            self._set_status("Luces desactivadas (marca «Luces»).")
            self._mode = None
            return []
        volumes = {d.deck: effective_volume(d, snap.mixer, self.mixer) for d in snap.decks}
        lead = self.lead.update(snap.t, volumes)
        self.lead_deck = lead
        if lead is None:
            self._key, self._last_pos = None, None
            if all(v is None for v in volumes.values()):
                self._set_status("No se leen los faders (¿falta el offset del fader?).")
            else:
                self._set_status("Ningún deck suena: efecto de reposo "
                                 "(los faders se leen de la controladora).")
            self._send_rule(("idle",), self.cfg.idle, "Reposo", snap.t, None)
            return []
        d = snap.deck(lead)
        if d is None or not (d.track_id or d.track_path):
            self._set_status(f"Manda el deck {lead}, pero no tiene tema cargado: efecto de reposo.")
            self._send_rule(("idle",), self.cfg.idle, "Reposo", snap.t, None)
            return []
        key = (lead, d.track_id or d.track_path)
        if d.position_s is None:
            self._set_status(f"Manda el deck {lead}, pero no se lee su posición: efecto básico.")
            self._send_rule(("fallback", key), self.cfg.fallback, "Efecto básico", snap.t, d.bpm)
            return []
        reason = None
        if key != self._key or not self._events:
            if key != self._key:
                self._key = key
                self._last_pos = None
            # El archivo o la rejilla pueden llegar más tarde (lectura en segundo plano).
            reason = self._load(d.track_id, d.track_path)
        if not self._events:
            # Hasta que se carguen los eventos del tema (o si no tiene): efecto básico.
            self._last_pos = None
            self._set_status({
                "no_show": f"Deck {lead}: preparando el archivo de luces (o no hay análisis) · efecto básico",
                "no_grid": f"Deck {lead}: leyendo la rejilla de beats · efecto básico",
                "no_fx": f"Deck {lead}: el tema no tiene eventos con efecto · efecto básico",
            }.get(reason or "no_show", f"Deck {lead}: sin eventos · efecto básico"))
            self._send_rule(("fallback", key), self.cfg.fallback, "Efecto básico", snap.t, d.bpm)
            return []
        # Adelanto por latencia, en tiempo del tema (el pitch acelera o frena el tema).
        rate = 1.0
        orig = self.track_bpm(d.track_id)
        if d.bpm and orig:
            rate = d.bpm / orig
        ahead = d.position_s + self.cfg.latency_compensation_ms / 1000.0 * rate
        last = self._last_pos
        self._last_pos = ahead
        j_next = bisect.bisect_right(self._times, ahead)
        nxt = self._events[j_next] if j_next < len(self._events) else None
        nxt_txt = (f"próximo: {nxt.label} → {nxt.fx} en {self._times[j_next] - ahead:.1f} s"
                   if nxt else "no quedan eventos en el tema")
        if last is None or d.jumped or ahead < last:
            # Enganche (tema nuevo, cambio de deck, salto): efecto de la sección actual, sin
            # disparar eventos atrasados.
            self._catch_up(ahead, key, snap.t, d.bpm)
            self._set_status(f"Deck {lead} sincronizado · {nxt_txt}")
            return []
        if d.playing is False:
            self._set_status(f"Deck {lead} parado · {nxt_txt}")
            return []
        i = bisect.bisect_right(self._times, last)
        fired = self._events[i:j_next]
        for ev in fired:
            cmd = ev.command()
            if cmd is not None:
                cmd = LightCommand(cmd.fx, resolve_live(cmd.params, d.bpm))
                self.send(cmd)
                self.last_sent = (ev, snap.t)
                self._mode = ("show", key, ev.beat)
        self._set_status(f"Deck {lead} sonando · {len(self._events)} eventos · {nxt_txt}")
        return fired
