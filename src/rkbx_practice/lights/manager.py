"""Gestor de luces: caché de archivos de eventos, generación automática y motor en directo.

El bucle en vivo solo consulta la caché (nunca el disco). Si un tema no tiene archivo de eventos,
se genera en segundo plano a partir del análisis de Rekordbox y las reglas de `lights.rules`.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from ..config import AppConfig
from ..library.anlz import BeatGridCache
from ..library.memory_library import InMemoryLibrary
from ..library.models import Track
from .analysis import TrackAnalysis, read_track_analysis
from .engine import LightsEngine
from .show import LightShow, ShowStore, generate_show
from .udp import UdpSender

log = logging.getLogger(__name__)


class LightsManager:
    def __init__(self, cfg: AppConfig, library: InMemoryLibrary, grids: BeatGridCache,
                 sender=None, analyzer=read_track_analysis):
        self.cfg = cfg
        self.library = library
        self.grids = grids
        self.store = ShowStore(cfg.resolve_path(cfg.lights.events_dir))
        self.analyzer = analyzer
        self._shows: dict[str, LightShow | None] = {}
        self._pending: set[str] = set()
        self.problems: dict[str, str] = {}   # track_id -> por qué no hay archivo de luces
        self._lock = threading.Lock()
        self.sender = sender or UdpSender(cfg.lights.esp32_ip, cfg.lights.esp32_port)
        if cfg.lights.send_to_simulator and hasattr(self.sender, "add_target"):
            self.sender.add_target("127.0.0.1", cfg.lights.simulator_port)
        self.simulator = None
        self.external_simulator = False
        from .palette import palette_for

        def track_palette(tid):
            t = library.track(tid) if tid else None
            return palette_for(t.camelot_str if t and t.camelot else None, tid) if tid else None

        def schedule(delay_s: float, fn) -> None:
            timer = threading.Timer(delay_s, fn)   # envío en el milisegundo exacto del evento
            timer.daemon = True
            timer.start()

        self.engine = LightsEngine(
            cfg.lights, cfg.mixer, cfg.detection, shows=self.cached_show,
            grids=grids.get_nowait, send=self.sender.send,
            track_bpm=lambda tid: (library.track(tid).bpm if tid and library.track(tid) else None),
            track_palette=track_palette, schedule=schedule,
            poll_s=cfg.live.poll_interval_ms / 1000.0)

    # --- análisis y archivos ----------------------------------------
    def anlz_path(self, track: Track) -> str | None:
        if not track.analysis_path:
            return None
        share = self.library.share_dir
        return str(share / track.analysis_path.lstrip("/\\")) if share else track.analysis_path

    def analyze(self, track: Track) -> TrackAnalysis | None:
        path = self.anlz_path(track)
        return self.analyzer(path) if path else None

    def generate(self, track: Track, analysis: TrackAnalysis | None = None) -> LightShow | None:
        analysis = analysis or self.analyze(track)
        if analysis is None:
            return None
        from .palette import palette_for
        return generate_show(track.id, track.display, analysis, self.cfg.lights.rules,
                             palette_for(track.camelot_str if track.camelot else None, track.id))

    def load_or_generate(self, track: Track, save: bool = True) -> LightShow | None:
        show = self.store.load(track.id)
        if show is not None and show.outdated:
            log.info("Luces: regenerando %s (archivo de una versión anterior)", track.display)
            show = None
        if show is None:
            show = self.generate(track)
            if show is not None and save:
                self.store.save(show)
            if show is None:
                path = self.anlz_path(track)
                self.problems[track.id] = (
                    "el tema no tiene ruta de análisis en master.db" if not path else
                    f"no se pudo leer el análisis de Rekordbox ({path})")
                log.warning("Luces: %s: %s", track.display, self.problems[track.id])
        with self._lock:
            self._shows[track.id] = show
        return show

    def save(self, show: LightShow) -> Path:
        path = self.store.save(show)
        with self._lock:
            self._shows[show.track_id] = show
        return path

    # --- montaje (tiras, píxeles, segmentos) ---------------------------
    @property
    def layout(self):
        from .layout import normalize_layout
        return normalize_layout(self.cfg.lights.layout)

    def send_layout(self) -> bool:
        """Envía el montaje al ESP32 (y al simulador), que lo guarda y lo aplica sin reiniciar."""
        from .layout import layout_command
        try:
            return bool(self.sender.send(layout_command(self.layout)))
        except Exception:  # noqa: BLE001
            log.exception("No se pudo enviar el montaje")
            return False

    def set_layout(self, strips, save: bool = True) -> bool:
        """Cambia el montaje: config.json (si `save`), simulador y ESP32."""
        from ..config import save_value
        from .layout import layout_to_config, normalize_layout
        strips = normalize_layout(layout_to_config(strips))
        self.cfg.lights.layout = layout_to_config(strips)
        if save and self.cfg.source_path is not None:
            save_value(self.cfg.source_path, ["lights", "layout"], self.cfg.lights.layout)
        if self.simulator is not None:
            self.simulator.layout = self.cfg.lights.layout
        return self.send_layout()

    # --- simulador en el navegador -----------------------------------
    def start_simulator(self) -> str:
        """Arranca el simulador dentro de la app y envía también a él. Devuelve la URL."""
        from .simulator import LightSimulator
        lc = self.cfg.lights
        if self.simulator is None:
            sim = LightSimulator(udp_port=lc.simulator_port, http_port=lc.simulator_http_port,
                                 leds=lc.simulator_leds, effects=lc.effects, layout=lc.layout)
            try:
                sim.start()
            except OSError:
                # Puerto ocupado: ya hay un simulador abierto (p. ej. `lights simulate`). Antes esto
                # daba error y no se le enviaba nada; ahora se usa ese simulador.
                sim.stop()
                log.info("Simulador ya en marcha en el puerto %s: se usa ese", lc.simulator_port)
                if hasattr(self.sender, "add_target"):
                    self.sender.add_target("127.0.0.1", lc.simulator_port)
                self.external_simulator = True
                return f"http://127.0.0.1:{lc.simulator_http_port}/"
            self.simulator = sim
            if hasattr(self.sender, "add_target"):
                self.sender.add_target("127.0.0.1", sim.udp_addr[1])
        return self.simulator.url

    def stop_simulator(self) -> None:
        if self.simulator is not None:
            if hasattr(self.sender, "remove_target"):
                self.sender.remove_target("127.0.0.1", self.simulator.udp_addr[1])
            self.simulator.stop()
            self.simulator = None

    # --- para el bucle en vivo (sin bloquear) -------------------------
    def cached_show(self, track_id: str | None) -> LightShow | None:
        if not track_id:
            return None
        with self._lock:
            if track_id in self._shows:
                return self._shows[track_id]
            if track_id in self._pending:
                return None
            self._pending.add(track_id)
        track = self.library.track(track_id)

        def work() -> None:
            try:
                if track is not None:
                    self.load_or_generate(track)
            except Exception:  # noqa: BLE001
                log.exception("No se pudo preparar el archivo de luces de %s", track_id)
                with self._lock:
                    self._shows[track_id] = None
            finally:
                with self._lock:
                    self._pending.discard(track_id)

        threading.Thread(target=work, name="lights-show", daemon=True).start()
        return None
