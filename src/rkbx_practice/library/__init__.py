"""Biblioteca de Rekordbox: master.db (solo lectura), ANLZ y normalización Camelot."""

from __future__ import annotations

from pathlib import Path

from ..config import AppConfig
from .camelot import Camelot, KeyRelation, parse_key, relation
from .memory_library import InMemoryLibrary
from .models import LibrarySource, NodeKind, PlaylistNode, Track, find_node_by_name, normalize_bpm

__all__ = [
    "Camelot", "KeyRelation", "parse_key", "relation", "InMemoryLibrary", "LibrarySource",
    "NodeKind", "PlaylistNode", "Track", "find_node_by_name", "normalize_bpm", "load_library",
]


def load_library(cfg: AppConfig, json_path: str | Path | None = None) -> InMemoryLibrary:
    """Carga la biblioteca desde un JSON exportado o desde master.db."""
    if json_path:
        return InMemoryLibrary.load_json(json_path)
    from .rekordbox_db import open_rekordbox_library
    return open_rekordbox_library(cfg.library)
