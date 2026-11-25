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
# Efectos que siguen el beat en el ESP32: se les manda la fase (`phase`, 0..1 dentro del beat en el
# momento en que se aplican) y cada compás un `sync` con el BPM y la fase reales.
BEAT_FX = {"pulse", "alternate", "chase", "wave", "twinkle"}

ShowLookup = Callable[[str | None], LightShow | None]
GridLookup = Callable[[str | None], BeatGrid | None]
Sender = Callable[[LightCommand], object]


class LightsEngine:
    def __init__(self, cfg: LightsConfig, mixer: MixerConfig, detection: DetectionConfig,
                 shows: ShowLookup, grids: GridLookup, send: Sender,
                 track_bpm: Callable[[str | None], float | None] | None = None,
                 track_palette: Callable[[str | None], dict | None] | None = None,
                 schedule: Callable[[float, Callable[[], object]], object] | None = None,
                 poll_s: float = 0.02):
        """`schedule(delay_s, fn)`: si se da, los eventos que caen entre dos sondeos se envían
        en su momento exacto (si no, se envían en el sondeo en que se cruzan)."""
        self.cfg = cfg
        self.track_palette = track_palette or (lambda _tid: None)
        self.schedule = schedule
        self.poll_s = poll_s
        self._grid: BeatGrid | None = None
        self._next = 0                          # siguiente evento por enviar
        self._next_beat = 0                     # siguiente beat de la rejilla (sync / metrónomo)
        self.metronome = False                  # calibración: un destello en cada beat
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
        self._grid = grid
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

    # --- construcción y envío de comandos ----------------------------------
    def _phase_at(self, pos: float) -> float | None:
        g = self._grid
        if g is None or len(g.times) < 2:
            return None
        j = bisect.bisect_right(g.times, pos) - 1
        if j < 0 or j + 1 >= len(g.times):
            return None
        span = g.times[j + 1] - g.times[j]
        return round((pos - g.times[j]) / span, 3) if span > 0 else 0.0

    def _make(self, fx: str, params: dict, bpm: float | None, pos: float | None,
              track_id: str | None = None) -> LightCommand:
        from .palette import apply_palette
        p = resolve_live(apply_palette(dict(params), self.track_palette(track_id)), bpm)
        if fx in BEAT_FX and pos is not None and "phase" not in p:
            ph = self._phase_at(pos)
            if ph is not None:
                p["phase"] = ph
        return LightCommand(fx, p)

    def _dispatch(self, cmd: LightCommand, delay_s: float = 0.0) -> None:
        if self.schedule is not None and delay_s > 0.002:
            self.schedule(delay_s, lambda c=cmd: self.send(c))
        else:
            self.send(cmd)

    # --- efectos básicos -------------------------------------------------
    def _send_rule(self, mode: tuple, rule: dict, label: str, t: float, bpm: float | None,
                   pos: float | None = None, track_id: str | None = None) -> None:
        """Envía `rule` una sola vez por situación `mode` (no en cada sondeo)."""
        if self._mode == mode or not rule or not rule.get("fx"):
            self._mode = mode
            return
        self._mode = mode
        cmd = self._make(rule["fx"], dict(rule.get("params", {})), bpm, pos, track_id)
        self.send(cmd)
        self.last_sent = (LightEvent(0, label, "auto", cmd.fx, cmd.params), t)

    def _catch_up(self, ahead: float, key: tuple, t: float, bpm: float | None,
                  track_id: str | None = None) -> None:
        """Al engancharse a un tema ya empezado: el último efecto de fondo de la sección actual
        (los temporales —flash, strobe, build…— no se repiten a destiempo); si aún no ha llegado
        ninguno, el efecto básico."""
        j = bisect.bisect_right(self._times, ahead)
        for ev in reversed(self._events[:j]):
            if ev.fx in BACKGROUND_FX:
                if self._mode != ("show", key, ev.beat):
                    self._mode = ("show", key, ev.beat)
                    cmd = self._make(ev.fx, dict(ev.params), bpm, ahead, track_id)
                    self.send(cmd)
                    self.last_sent = (ev, t)
                return
        self._send_rule(("fallback", key), self.cfg.fallback, "Efecto básico", t, bpm, ahead, track_id)

    def update(self, snap: LiveSnapshot) -> list[LightEvent]:
        if not self.enabled:
            self._set_status("Luces desactivadas (marca «Luces»).")
            self._mode = None
            return []
        # Solo cuenta lo que suena: un deck parado con el fader arriba no manda (visto en real).
        volumes = {d.deck: (0.0 if d.playing is False else effective_volume(d, snap.mixer, self.mixer))
                   for d in snap.decks}
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
            self._send_rule(("fallback", key), self.cfg.fallback, "Efecto básico", snap.t, d.bpm,
                            None, d.track_id)
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
            self._send_rule(("fallback", key), self.cfg.fallback, "Efecto básico", snap.t, d.bpm,
                            d.position_s, d.track_id)
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
        grid_times = self._grid.times if self._grid is not None else []
        if last is None or d.jumped or ahead < last:
            # Enganche (tema nuevo, cambio de deck, salto): efecto de la sección actual, sin
            # disparar eventos atrasados.
            self._next = j_next
            self._next_beat = bisect.bisect_right(grid_times, ahead)
            self._catch_up(ahead, key, snap.t, d.bpm, d.track_id)
            self._set_status(f"Deck {lead} sincronizado · {nxt_txt}")
            return []
        if d.playing is False:
            self._set_status(f"Deck {lead} parado · {nxt_txt}")
            return []
        # Con planificador, se mira un poco más allá del sondeo actual y cada evento sale en su
        # milisegundo exacto (si no, hasta `poll_s` tarde).
        horizon = self.poll_s * rate * 1.5 if self.schedule is not None else 0.0
        limit = ahead + horizon
        fired = []
        while self._next < len(self._events) and self._times[self._next] <= limit:
            ev, t_ev = self._events[self._next], self._times[self._next]
            self._next += 1
            fired.append(ev)
            if self.metronome or not ev.fx:
                continue
            cmd = self._make(ev.fx, dict(ev.params), d.bpm, t_ev, d.track_id)
            self._dispatch(cmd, max(0.0, (t_ev - ahead) / rate))
            self.last_sent = (ev, snap.t)
            self._mode = ("show", key, ev.beat)
        # Beats de la rejilla: `sync` en cada compás (BPM y fase reales al ESP32) y metrónomo.
        numbers = self._grid.beat_numbers if self._grid is not None else []
        while self._next_beat < len(grid_times) and grid_times[self._next_beat] <= limit:
            b = self._next_beat
            self._next_beat += 1
            delay = max(0.0, (grid_times[b] - ahead) / rate)
            if self.metronome:
                self._dispatch(LightCommand("flash", {"color": "blanco", "dur_ms": 80}), delay)
            elif numbers[b] == 1 and d.bpm:
                self._dispatch(LightCommand("sync", {"bpm": round(float(d.bpm), 2), "phase": 0.0}), delay)
        if self.metronome:
            self._set_status(f"Calibración: destello en cada beat · latencia "
                             f"{self.cfg.latency_compensation_ms} ms")
            return fired
        self._set_status(f"Deck {lead} sonando · {len(self._events)} eventos · {nxt_txt}")
        return fired
