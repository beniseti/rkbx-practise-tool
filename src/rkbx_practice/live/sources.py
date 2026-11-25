"""Fuentes de estado en vivo.

- :class:`MemorySource`: lee la memoria de rekordbox.exe (Windows, solo lectura).
- :class:`ScriptedSource`: reproduce una secuencia de estados (tests y demo sin Rekordbox).

Plan B (si la memoria falla tras una actualización): una fuente basada en UI
Automation que lea el texto de los decks. Bastaría con implementar esta misma
interfaz; está pendiente de confirmar (ver CLAUDE.md §13).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Iterable, Protocol

from ..config import AppConfig
from ..memory.deck_reader import DeckReader, ReadResult
from ..memory.offsets import find_offsets, normalize_version
from ..state import ConnectionMode, ConnectionStatus, DeckState, MixerState

log = logging.getLogger(__name__)


class SourceError(RuntimeError):
    """La fuente no está disponible o se ha perdido la conexión."""


class DeckSource(Protocol):
    def connect(self) -> ConnectionStatus: ...
    def read(self) -> ReadResult: ...
    def is_alive(self) -> bool: ...
    def close(self) -> None: ...


class MemorySource:
    def __init__(self, cfg: AppConfig, attach: Callable | None = None):
        self.cfg = cfg
        self._attach = attach
        self._proc = None
        self._reader: DeckReader | None = None
        self.version: str | None = None

    def connect(self) -> ConnectionStatus:
        from ..memory import process as proc_mod
        attach = self._attach or proc_mod.attach
        try:
            self._proc = attach(self.cfg.rekordbox.process_name)
        except proc_mod.ProcessNotFound as exc:
            raise SourceError(str(exc)) from exc
        self.version = normalize_version(self._proc.version)
        offsets_dir = self.cfg.resolve_path(self.cfg.rekordbox.offsets_dir)
        table = find_offsets(offsets_dir, self.version)
        if table is None:
            self._reader = None
            return ConnectionStatus(
                ConnectionMode.LIMITED, self.version,
                f"No hay offsets para Rekordbox {self.version or '(versión desconocida)'} en "
                f"{offsets_dir}. Biblioteca y sesiones disponibles; sin detección en vivo.")
        if table.pointer_size != self._proc.access.pointer_size:
            self._reader = None
            return ConnectionStatus(ConnectionMode.LIMITED, self.version,
                                    "El tamaño de puntero de los offsets no coincide con el proceso.")
        self._reader = DeckReader(self._proc.access, table, self.cfg.rekordbox.decks,
                                  self.cfg.mixer.crossfader_enabled)
        missing = table.missing_fields(self.cfg.rekordbox.decks)
        msg = "Lectura en vivo activa."
        if missing:
            msg += " Faltan offsets: " + ", ".join(missing)
        return ConnectionStatus(ConnectionMode.FULL, self.version, msg)

    def read(self) -> ReadResult:
        if self._proc is None:
            raise SourceError("No conectado")
        if self._reader is None:
            raise SourceError("Modo limitado: sin lectura en vivo")
        return self._reader.read()

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.is_alive()

    def close(self) -> None:
        if self._proc is not None:
            self._proc.close()
        self._proc = None
        self._reader = None


class ScriptedSource:
    """Devuelve una lista de estados en orden; al terminar repite el último."""

    def __init__(self, frames: Iterable[tuple[tuple[DeckState, ...], MixerState]],
                 version: str = "simulado", mode: ConnectionMode = ConnectionMode.FULL):
        self._frames = list(frames)
        self._i = 0
        self.version = version
        self.mode = mode
        self.connected = False

    def connect(self) -> ConnectionStatus:
        self.connected = True
        return ConnectionStatus(self.mode, self.version, "Fuente simulada")

    def read(self) -> ReadResult:
        if not self.connected:
            raise SourceError("No conectado")
        decks, mixer = self._frames[min(self._i, len(self._frames) - 1)]
        self._i += 1
        return ReadResult(decks, mixer, [], 1)

    @property
    def finished(self) -> bool:
        return self._i >= len(self._frames)

    def is_alive(self) -> bool:
        return self.connected

    def close(self) -> None:
        self.connected = False


def default_source(cfg: AppConfig) -> DeckSource:
    return MemorySource(cfg)


def offsets_dir(cfg: AppConfig) -> Path:
    return cfg.resolve_path(cfg.rekordbox.offsets_dir)
