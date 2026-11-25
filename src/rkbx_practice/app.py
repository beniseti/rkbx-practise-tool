"""Controlador de la práctica: une biblioteca, sesión, bucle en vivo e histórico.

Lo usan la CLI y la ventana. Los eventos del bucle llegan en el hilo de
sondeo: el histórico los registra ahí mismo (tiene su propio cerrojo) y la
sesión se actualiza con :meth:`apply_events`, que la UI llama desde su hilo.
"""

from __future__ import annotations

import logging
import random
from typing import Callable

from .config import AppConfig, SessionConfig
from .events import LiveEvent, TrackLoaded
from .history import SessionRecorder
from .library.anlz import BeatGridCache
from .library.memory_library import InMemoryLibrary
from .library.models import NodeKind, Track
from .live import LivePipeline, LivePoller, SnapshotEnricher
from .live.poller import LiveListener
from .live.sources import DeckSource, MemorySource
from .session import PracticeSession, generate_session

log = logging.getLogger(__name__)


class PracticeController:
    def __init__(self, cfg: AppConfig, library: InMemoryLibrary,
                 source_factory: Callable[[], DeckSource] | None = None):
        self.cfg = cfg
        self.library = library
        self.source_factory = source_factory or (lambda: MemorySource(cfg))
        self.grids = BeatGridCache(library.share_dir)
        self.session: PracticeSession | None = None
        self.source_label: str | None = None
        self.recorder = SessionRecorder(cfg.resolve_path(cfg.history.dir), library,
                                        cfg.session, decks=cfg.rekordbox.decks)
        self.poller: LivePoller | None = None
        self.unknown_loads: list[TrackLoaded] = []
        self._lights = None   # LightsManager, se crea al usarlo (abre un socket UDP)

    @property
    def lights(self):
        if self._lights is None:
            from .lights.manager import LightsManager
            self._lights = LightsManager(self.cfg, self.library, self.grids)
        return self._lights

    def set_lights_enabled(self, on: bool) -> None:
        self.lights.engine.enabled = on

    def use_demo_source(self, track_s: float = 30.0, blend_s: float = 8.0) -> None:
        """Sustituye la memoria de Rekordbox por la simulación (ver live/demo.py)."""
        from .live.demo import DemoSource

        def next_track():
            s = self.session
            if s is not None and s.suggestion is not None:
                return s.suggestion.track
            tracks = [t for t in self.library.all_tracks() if t.bpm and t.camelot]
            return tracks[0] if tracks else None

        self.source_factory = lambda: DemoSource(self.library, next_track, track_s, blend_s)

    # --- sesiones -----------------------------------------------------
    def node_label(self, node_id: str) -> str:
        parts = []
        node = self.library.node(node_id)
        while node is not None:
            parts.append(node.name)
            node = self.library.node(node.parent_id) if node.parent_id else None
        return "/".join(reversed(parts))

    def generate(self, node_id: str, settings: SessionConfig | None = None,
                 seed: int | None = None, loaded: Track | None = None,
                 loaded_deck: int | None = None) -> PracticeSession:
        """Genera la sesión. Si hay un tema `loaded` ya cargado en un deck, la sesión empieza
        desde él (se tacha si está en la lista) y la primera sugerencia es compatible con él."""
        settings = settings or self.cfg.session
        node = self.library.node(node_id)
        if node is None:
            raise KeyError(node_id)
        tracks = self.library.tracks_of(node_id)
        start = None
        if loaded is not None and loaded.bpm and loaded.camelot:
            start = next((t for t in tracks if t.id == loaded.id), None) or loaded
        gen = generate_session(tracks, settings, start=start, rng=random.Random(seed))
        self.session = PracticeSession.from_generated(self.node_label(node_id), gen, settings,
                                                      rng=random.Random(seed))
        if loaded is not None:
            self.session.track_loaded(loaded, deck=loaded_deck)
        self.source_label = self.session.source_name
        log.info("Sesión generada desde %s (%s): %d temas, %d excluidos",
                 self.source_label, "carpeta" if node.kind == NodeKind.FOLDER else "playlist",
                 len(gen.entries), len(gen.excluded))
        return self.session

    def apply_events(self, events: list[LiveEvent]) -> list[TrackLoaded]:
        """Aplica a la sesión los temas cargados. Devuelve los que no están en la biblioteca."""
        unknown = []
        for ev in events:
            if not isinstance(ev, TrackLoaded):
                continue
            track = self.library.track(ev.track_id) if ev.track_id else None
            if track is None:
                unknown.append(ev)
                continue
            if self.session is not None:
                self.session.track_loaded(track, deck=ev.deck, at=ev.at)
        self.unknown_loads += unknown
        return unknown

    # --- grabación ----------------------------------------------------
    def start_recording(self) -> None:
        settings = self.session.settings if self.session else self.cfg.session
        self.recorder.settings = settings
        extra = {"crossfader_enabled": self.cfg.mixer.crossfader_enabled}
        self.recorder.start(self.source_label, extra)

    def stop_recording(self):
        pending = self.poller.flush_pipeline() if self.poller else []
        return self.recorder.stop(pending)

    # --- bucle en vivo ------------------------------------------------
    def start_live(self, listener: LiveListener) -> LivePoller:
        controller = self

        class _Tee:
            def on_status(self, s):
                listener.on_status(s)

            def on_snapshot(self, s):
                lights = controller._lights
                if lights is not None and lights.engine.enabled:
                    try:
                        lights.engine.update(s)
                    except Exception:  # noqa: BLE001  (las luces nunca deben parar el bucle)
                        log.exception("Error en el motor de luces")
                listener.on_snapshot(s)

            def on_events(self, ev):
                controller.recorder.handle(ev)
                listener.on_events(ev)

        enricher = SnapshotEnricher(self.library, self.cfg.live.position_jump_tolerance_s,
                                    empty_without_bpm=self.cfg.live.empty_deck_without_bpm)
        pipeline = LivePipeline(self.cfg, grids=self.grids.get_nowait, prefetch=self.grids.prefetch)
        if self.cfg.lights.enabled:
            self.set_lights_enabled(True)
        self.poller = LivePoller(self.cfg, self.source_factory, enricher, pipeline, _Tee())
        self.poller.start()
        return self.poller

    def stop_live(self) -> None:
        if self.poller is not None:
            self.poller.stop()
            self.poller = None
