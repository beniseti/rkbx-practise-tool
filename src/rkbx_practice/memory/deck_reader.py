"""Lectura del estado de los decks y del mezclador a partir de una tabla de offsets."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..state import DeckState, MixerState
from .offsets import OffsetTable
from .reader import MemoryAccess, MemoryReadError, read_field

log = logging.getLogger(__name__)

_DECK_ATTR = {
    "track_path": "track_path", "track_id": "track_id", "bpm": "bpm",
    "position": "position_s", "is_master": "is_master", "fader": "fader", "eq_low": "eq_low",
    "eq_mid": "eq_mid", "eq_high": "eq_high",
}


@dataclass
class ReadResult:
    decks: tuple[DeckState, ...]
    mixer: MixerState
    errors: list[str] = field(default_factory=list)
    fields_read: int = 0


class DeckReader:
    def __init__(self, mem: MemoryAccess, table: OffsetTable, decks: int,
                 crossfader_enabled: bool = False):
        self.mem = mem
        self.table = table
        self.decks = decks
        self.crossfader_enabled = crossfader_enabled

    def read(self) -> ReadResult:
        errors: list[str] = []
        ok = 0
        master_deck = None
        mspec = self.table.globals.get("master_deck")
        if mspec is not None:
            try:
                raw = read_field(self.mem, mspec)
                if raw is not None:
                    master_deck = int(raw) - mspec.index_base + 1
                ok += 1
            except (MemoryReadError, ValueError, TypeError) as exc:
                errors.append(f"master_deck: {exc}")

        decks = []
        for n in range(1, self.decks + 1):
            values: dict = {"deck": n}
            for name, spec in self.table.deck_fields(n).items():
                try:
                    v = read_field(self.mem, spec)
                    ok += 1
                except (MemoryReadError, ValueError) as exc:
                    errors.append(f"deck{n}.{name}: {exc}")
                    continue
                if name == "track_id" and v is not None:
                    v = str(v) if v != 0 else None
                values[_DECK_ATTR[name]] = v
            if "is_master" not in values and master_deck is not None:
                values["is_master"] = master_deck == n
            if values.get("bpm") is not None and values["bpm"] <= 0:
                values["bpm"] = None
            decks.append(DeckState(**values))

        crossfader = None
        cspec = self.table.globals.get("crossfader")
        if self.crossfader_enabled and cspec is not None:
            try:
                crossfader = read_field(self.mem, cspec)
                ok += 1
            except MemoryReadError as exc:
                errors.append(f"crossfader: {exc}")
        return ReadResult(tuple(decks), MixerState(crossfader=crossfader), errors, ok)
