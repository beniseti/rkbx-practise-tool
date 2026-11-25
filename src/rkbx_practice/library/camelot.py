"""Normalización de tonalidades a la rueda Camelot y relaciones armónicas.

Acepta notación clásica (`Am`, `G#m`, `Abm`, `Db`, `F# minor`, `B♭`) y
alfanumérica (`8A`, `08a`). Siempre devuelve un :class:`Camelot`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

# Semitono de cada nota natural.
_NATURAL = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}

# Clase de altura (0=C) -> número Camelot.
_MINOR_TO_NUMBER = {8: 1, 3: 2, 10: 3, 5: 4, 0: 5, 7: 6, 2: 7, 9: 8, 4: 9, 11: 10, 6: 11, 1: 12}
_MAJOR_TO_NUMBER = {11: 1, 6: 2, 1: 3, 8: 4, 3: 5, 10: 6, 5: 7, 0: 8, 7: 9, 2: 10, 9: 11, 4: 12}

# Nombres preferidos (los de la tabla del proyecto).
_MINOR_NAMES = {1: "Abm", 2: "Ebm", 3: "Bbm", 4: "Fm", 5: "Cm", 6: "Gm", 7: "Dm",
                8: "Am", 9: "Em", 10: "Bm", 11: "F#m", 12: "Dbm"}
_MAJOR_NAMES = {1: "B", 2: "F#", 3: "Db", 4: "Ab", 5: "Eb", 6: "Bb", 7: "F",
                8: "C", 9: "G", 10: "D", 11: "A", 12: "E"}

_CAMELOT_RE = re.compile(r"^\s*0?(1[0-2]|[1-9])\s*([ABab])\s*$")
_CLASSIC_RE = re.compile(
    r"^\s*([A-Ga-g])\s*([#♯b♭]*)\s*(m|min|minor|moll|maj|major|dur|M)?\s*$"
)


@dataclass(frozen=True, order=True)
class Camelot:
    number: int  # 1..12
    letter: str  # "A" (menor) o "B" (mayor)

    def __post_init__(self) -> None:
        if not 1 <= self.number <= 12 or self.letter not in ("A", "B"):
            raise ValueError(f"Código Camelot inválido: {self.number}{self.letter}")

    def __str__(self) -> str:
        return f"{self.number}{self.letter}"

    @property
    def is_minor(self) -> bool:
        return self.letter == "A"

    @property
    def classic_name(self) -> str:
        return (_MINOR_NAMES if self.is_minor else _MAJOR_NAMES)[self.number]

    def shifted(self, steps: int) -> "Camelot":
        return Camelot((self.number - 1 + steps) % 12 + 1, self.letter)

    def relative(self) -> "Camelot":
        return Camelot(self.number, "B" if self.is_minor else "A")


def parse_key(value: str | None) -> Camelot | None:
    """Convierte cualquier notación soportada a Camelot. Devuelve None si no se reconoce."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    m = _CAMELOT_RE.match(text)
    if m:
        return Camelot(int(m.group(1)), m.group(2).upper())
    m = _CLASSIC_RE.match(text)
    if not m:
        return None
    note, accidentals, quality = m.groups()
    pitch = _NATURAL[note.upper()]
    for acc in accidentals:
        pitch += 1 if acc in "#♯" else -1
    pitch %= 12
    minor = quality is not None and quality.lower() in ("m", "min", "minor", "moll") and quality != "M"
    table = _MINOR_TO_NUMBER if minor else _MAJOR_TO_NUMBER
    return Camelot(table[pitch], "A" if minor else "B")


class KeyRelation(str, Enum):
    """Relación armónica de A hacia B (es direccional)."""

    SAME = "same"                  # misma clave
    ADJACENT = "adjacent"          # ±1, misma letra
    RELATIVE = "relative"          # mismo número, cambio de letra
    ENERGY_BOOST = "energy_boost"  # +2, misma letra
    SEMITONE_UP = "semitone_up"    # +7, misma letra
    NONE = "none"

    @property
    def label_es(self) -> str:
        return _LABELS_ES[self]


_LABELS_ES = {
    KeyRelation.SAME: "misma clave",
    KeyRelation.ADJACENT: "vecina (±1)",
    KeyRelation.RELATIVE: "relativa",
    KeyRelation.ENERGY_BOOST: "energy boost (+2)",
    KeyRelation.SEMITONE_UP: "subida de semitono (+7)",
    KeyRelation.NONE: "incompatible",
}

ALLOWED_RELATIONS: dict[str, frozenset[KeyRelation]] = {
    "strict": frozenset({KeyRelation.SAME, KeyRelation.ADJACENT, KeyRelation.RELATIVE}),
    "moderate": frozenset({KeyRelation.SAME, KeyRelation.ADJACENT, KeyRelation.RELATIVE,
                           KeyRelation.ENERGY_BOOST}),
    "relaxed": frozenset({KeyRelation.SAME, KeyRelation.ADJACENT, KeyRelation.RELATIVE,
                          KeyRelation.ENERGY_BOOST, KeyRelation.SEMITONE_UP}),
}

# Coste de cada relación para el orden de la sesión (menor = más suave).
RELATION_COST: dict[KeyRelation, float] = {
    KeyRelation.SAME: 0.0,
    KeyRelation.ADJACENT: 0.5,
    KeyRelation.RELATIVE: 0.8,
    KeyRelation.ENERGY_BOOST: 1.5,
    KeyRelation.SEMITONE_UP: 2.5,
    KeyRelation.NONE: 10.0,
}


def relation(a: Camelot, b: Camelot) -> KeyRelation:
    if a == b:
        return KeyRelation.SAME
    if a.letter == b.letter:
        diff = (b.number - a.number) % 12
        if diff in (1, 11):
            return KeyRelation.ADJACENT
        if diff == 2:
            return KeyRelation.ENERGY_BOOST
        if diff == 7:
            return KeyRelation.SEMITONE_UP
        return KeyRelation.NONE
    if a.number == b.number:
        return KeyRelation.RELATIVE
    return KeyRelation.NONE


def is_compatible(a: Camelot, b: Camelot, strictness: str = "moderate") -> bool:
    return relation(a, b) in ALLOWED_RELATIONS[strictness]
