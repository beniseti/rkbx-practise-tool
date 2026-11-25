"""Lectura de memoria de rekordbox.exe (solo lectura): versión, offsets y estado de decks."""

from .deck_reader import DeckReader, ReadResult
from .offsets import FieldSpec, OffsetTable, OffsetsError, find_offsets, load_offsets_dir, normalize_version
from .reader import FakeMemory, MemoryAccess, MemoryReadError, read_field, resolve_chain

__all__ = [
    "DeckReader", "ReadResult", "FieldSpec", "OffsetTable", "OffsetsError", "find_offsets",
    "load_offsets_dir", "normalize_version", "FakeMemory", "MemoryAccess", "MemoryReadError",
    "read_field", "resolve_chain",
]
