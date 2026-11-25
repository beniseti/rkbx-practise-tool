"""Tablas de offsets (cadenas de punteros) por versión de Rekordbox.

Las direcciones NUNCA van en el código: viven en `data/offsets/<versión>.json`.
Formato (inspirado en rkbx_link: base relativa al módulo + desplazamientos):

```json
{
  "version": "7.2.20",
  "module": "rekordbox.exe",
  "pointer_size": 8,
  "decks": {
    "1": {
      "track_path": {"chain": ["0x4A1B2C0", "0x18", "0x2F0"], "type": "ptr_utf8", "max_len": 512},
      "bpm":        {"chain": ["0x4A1B2C0", "0x18", "0x120"], "type": "f32"},
      "fader":      {"chain": ["0x...", "0x..."], "type": "f32", "map": [0.0, 1.0]}
    }
  },
  "global": {
    "crossfader": {"chain": ["0x...", "0x..."], "type": "f32", "map": [-1.0, 1.0]},
    "master_deck": {"chain": ["0x...", "0x..."], "type": "u8", "index_base": 0}
  }
}
```

Semántica de `chain` = [base, o1, ..., on]:
`addr = módulo + base`; para cada `o`: `addr = leer_puntero(addr) + o`; se lee el
valor en `addr`. Si un puntero intermedio es 0, el campo no está disponible
(p. ej. deck vacío).

Tipos: `f32`, `f64`, `i32`, `u32`, `u8`, `bool`, `utf8`, `utf16` (cadena en línea)
y `ptr_utf8`, `ptr_utf16` (la dirección final contiene un puntero a la cadena).
Normalización opcional: `map: [min, max]` → 0..1 (con recorte), `invert`, `scale`.
Archivos cuyo nombre empieza por `_` se ignoran (plantillas/ejemplos).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

NUMERIC_TYPES = {"f32", "f64", "i32", "u32", "u8", "bool"}
STRING_TYPES = {"utf8", "utf16", "ptr_utf8", "ptr_utf16"}
DECK_FIELDS = {"track_path", "track_id", "bpm", "position", "is_master", "fader", "eq_low"}
GLOBAL_FIELDS = {"crossfader", "master_deck"}


class OffsetsError(ValueError):
    pass


def normalize_version(version: str | None) -> str | None:
    """`7.2.20.0` → `7.2.20`; `7.2` → `7.2`. Solo dígitos y puntos."""
    if not version:
        return None
    parts = re.findall(r"\d+", str(version))
    if not parts:
        return None
    while len(parts) > 3 and parts[-1] == "0":
        parts.pop()
    return ".".join(str(int(p)) for p in parts)


def _parse_int(value) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value.strip(), 0)
    raise OffsetsError(f"Offset inválido: {value!r}")


@dataclass(frozen=True)
class FieldSpec:
    chain: tuple[int, ...]
    type: str
    max_len: int = 512
    scale: float = 1.0
    map_range: tuple[float, float] | None = None
    invert: bool = False
    index_base: int = 0

    @classmethod
    def from_dict(cls, name: str, data: dict) -> "FieldSpec":
        if "chain" not in data or not data["chain"]:
            raise OffsetsError(f"{name}: falta 'chain'")
        ftype = data.get("type", "f32")
        if ftype not in NUMERIC_TYPES | STRING_TYPES:
            raise OffsetsError(f"{name}: tipo desconocido {ftype}")
        mp = data.get("map")
        if mp is not None:
            if len(mp) != 2 or float(mp[0]) == float(mp[1]):
                raise OffsetsError(f"{name}: 'map' debe ser [min, max] distintos")
            mp = (float(mp[0]), float(mp[1]))
        return cls(
            chain=tuple(_parse_int(x) for x in data["chain"]),
            type=ftype,
            max_len=int(data.get("max_len", 512)),
            scale=float(data.get("scale", 1.0)),
            map_range=mp,
            invert=bool(data.get("invert", False)),
            index_base=int(data.get("index_base", 0)),
        )

    def normalize(self, raw: float) -> float:
        v = raw * self.scale
        if self.map_range is not None:
            lo, hi = self.map_range
            v = (v - lo) / (hi - lo)
            v = min(1.0, max(0.0, v))
        if self.invert:
            v = 1.0 - v
        return v


@dataclass
class OffsetTable:
    version: str
    module: str = "rekordbox.exe"
    pointer_size: int = 8
    decks: dict[int, dict[str, FieldSpec]] = field(default_factory=dict)
    globals: dict[str, FieldSpec] = field(default_factory=dict)
    source: Path | None = None

    def deck_fields(self, deck: int) -> dict[str, FieldSpec]:
        return self.decks.get(deck, {})

    def supported_decks(self) -> list[int]:
        return sorted(self.decks)

    def missing_fields(self, decks: int) -> list[str]:
        """Campos útiles que faltan (para avisar en la UI/CLI)."""
        missing = []
        for d in range(1, decks + 1):
            f = self.deck_fields(d)
            if not f:
                missing.append(f"deck {d}: todos")
                continue
            if "track_path" not in f and "track_id" not in f:
                missing.append(f"deck {d}: track_path/track_id")
            for name in ("bpm", "position", "fader", "eq_low"):
                if name not in f:
                    missing.append(f"deck {d}: {name}")
            if "is_master" not in f and "master_deck" not in self.globals:
                missing.append(f"deck {d}: is_master")
        return missing

    @classmethod
    def from_dict(cls, data: dict, source: Path | None = None) -> "OffsetTable":
        version = normalize_version(data.get("version"))
        if not version:
            raise OffsetsError("Falta 'version'")
        decks: dict[int, dict[str, FieldSpec]] = {}
        for key, fields_ in (data.get("decks") or {}).items():
            n = int(key)
            if not 1 <= n <= 4:
                raise OffsetsError(f"Deck fuera de rango: {n}")
            decks[n] = {}
            for name, spec in fields_.items():
                if name.startswith("_"):
                    continue
                if name not in DECK_FIELDS:
                    log.warning("%s: campo de deck desconocido '%s' (ignorado)", source, name)
                    continue
                decks[n][name] = FieldSpec.from_dict(f"deck{n}.{name}", spec)
        globals_: dict[str, FieldSpec] = {}
        for name, spec in (data.get("global") or {}).items():
            if name.startswith("_"):
                continue
            if name not in GLOBAL_FIELDS:
                log.warning("%s: campo global desconocido '%s' (ignorado)", source, name)
                continue
            globals_[name] = FieldSpec.from_dict(f"global.{name}", spec)
        ps = int(data.get("pointer_size", 8))
        if ps not in (4, 8):
            raise OffsetsError("pointer_size debe ser 4 u 8")
        return cls(version=version, module=data.get("module", "rekordbox.exe"),
                   pointer_size=ps, decks=decks, globals=globals_, source=source)


def load_offsets_dir(directory: str | Path) -> dict[str, OffsetTable]:
    """Carga todas las tablas válidas del directorio. Las inválidas se avisan y se saltan."""
    directory = Path(directory)
    tables: dict[str, OffsetTable] = {}
    if not directory.is_dir():
        log.warning("No existe el directorio de offsets %s", directory)
        return tables
    for path in sorted(directory.glob("*.json")):
        if path.name.startswith("_"):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            table = OffsetTable.from_dict(data, source=path)
        except (OSError, ValueError) as exc:
            log.error("Archivo de offsets inválido %s: %s", path, exc)
            continue
        if table.version in tables:
            log.warning("Versión %s duplicada (%s); se usa la primera", table.version, path)
            continue
        tables[table.version] = table
    return tables


def find_offsets(directory: str | Path, version: str | None) -> OffsetTable | None:
    """Tabla para la versión EXACTA. Nunca se usa la de otra versión."""
    v = normalize_version(version)
    if v is None:
        return None
    return load_offsets_dir(directory).get(v)
