"""Bucle de sondeo en un hilo: conecta, lee, enriquece, detecta y notifica.

- Reintenta la conexión cada `reconnect_interval_s` si Rekordbox no está abierto.
- En modo limitado (sin offsets para la versión) no lee memoria, pero sigue
  comprobando si el proceso vive y si aparece un archivo de offsets.
- Ningún error de lectura cierra la app: se cuentan y, si se acumulan
  (`max_consecutive_read_errors`), se reconecta.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Protocol

from ..config import AppConfig
from ..events import LiveEvent
from ..state import ConnectionMode, ConnectionStatus, LiveSnapshot
from .enrich import SnapshotEnricher
from .pipeline import LivePipeline
from .sources import DeckSource, SourceError

log = logging.getLogger(__name__)


class LiveListener(Protocol):
    def on_status(self, status: ConnectionStatus) -> None: ...
    def on_snapshot(self, snap: LiveSnapshot) -> None: ...
    def on_events(self, events: list[LiveEvent]) -> None: ...


class LivePoller:
    def __init__(self, cfg: AppConfig, source_factory: Callable[[], DeckSource],
                 enricher: SnapshotEnricher, pipeline: LivePipeline,
                 listener: LiveListener, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] | None = None):
        self.cfg = cfg
        self.source_factory = source_factory
        self.enricher = enricher
        self.pipeline = pipeline
        self.listener = listener
        self.clock = clock
        self._stop = threading.Event()
        self._sleep = sleep or (lambda s: self._stop.wait(s))
        self._thread: threading.Thread | None = None
        self._source: DeckSource | None = None
        self.status = ConnectionStatus(ConnectionMode.DISCONNECTED, None, "Buscando Rekordbox…")
        self._lock = threading.Lock()
        self._errors = 0

    # --- control ------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self.run, name="rkbx-live", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)
        self._disconnect("Detenido")

    def flush_pipeline(self) -> list[LiveEvent]:
        with self._lock:
            return self.pipeline.flush()

    # --- bucle --------------------------------------------------------
    def _set_status(self, status: ConnectionStatus) -> None:
        if status != self.status:
            self.status = status
            try:
                self.listener.on_status(status)
            except Exception:  # noqa: BLE001
                log.exception("Error en on_status")

    def _disconnect(self, message: str) -> None:
        if self._source is not None:
            try:
                self._source.close()
            except Exception:  # noqa: BLE001
                pass
        self._source = None
        self._set_status(ConnectionStatus(ConnectionMode.DISCONNECTED, None, message))

    def _try_connect(self) -> bool:
        try:
            source = self.source_factory()
            status = source.connect()
        except SourceError as exc:
            self._set_status(ConnectionStatus(ConnectionMode.DISCONNECTED, None,
                                              f"{exc}. Reintentando…"))
            return False
        except Exception as exc:  # noqa: BLE001
            log.exception("Error inesperado conectando")
            self._set_status(ConnectionStatus(ConnectionMode.DISCONNECTED, None, f"Error: {exc}"))
            return False
        self._source = source
        with self._lock:
            self.enricher.reset()
            self.pipeline.reset()
        self._set_status(status)
        return True

    def step(self) -> None:
        """Una iteración del bucle (público para tests)."""
        if self._source is None:
            if not self._try_connect():
                self._sleep(self.cfg.live.reconnect_interval_s)
                return
        assert self._source is not None
        if self.status.mode == ConnectionMode.LIMITED:
            # Comprobamos si sigue vivo o si han aparecido offsets.
            self._sleep(self.cfg.live.reconnect_interval_s)
            if not self._source.is_alive():
                self._disconnect("Rekordbox se ha cerrado. Reintentando…")
            else:
                self._source.close()
                self._source = None  # el siguiente step reconecta y recarga offsets
            return
        try:
            res = self._source.read()
        except SourceError as exc:
            self._disconnect(f"{exc}. Reintentando…")
            return
        except Exception as exc:  # noqa: BLE001
            log.exception("Error leyendo memoria")
            res = None
            self._errors = self._errors + 1
        if res is not None:
            if res.errors and res.fields_read == 0:
                self._errors = self._errors + 1
            else:
                self._errors = 0
            if res.errors:
                log.debug("Errores de lectura: %s", res.errors[:3])
        if self._errors >= self.cfg.live.max_consecutive_read_errors:
            self._errors = 0
            alive = self._source.is_alive()
            self._disconnect("Rekordbox se ha cerrado. Reintentando…" if not alive
                             else "Demasiados errores de lectura. Reconectando…")
            return
        if res is None:
            return
        with self._lock:
            snap = self.enricher.enrich(res, self.clock())
            events = self.pipeline.process(snap)
        try:
            self.listener.on_snapshot(snap)
            if events:
                self.listener.on_events(events)
        except Exception:  # noqa: BLE001
            log.exception("Error en un oyente del bucle en vivo")

    def run(self) -> None:
        interval = self.cfg.live.poll_interval_ms / 1000.0
        while not self._stop.is_set():
            t0 = self.clock()
            try:
                self.step()
            except Exception:  # noqa: BLE001  (el bucle no debe morir nunca)
                log.exception("Error en el bucle en vivo")
                self._disconnect("Error interno. Reintentando…")
                self._sleep(self.cfg.live.reconnect_interval_s)
            elapsed = self.clock() - t0
            if self._source is not None and self.status.mode == ConnectionMode.FULL:
                self._sleep(max(0.0, interval - elapsed))
