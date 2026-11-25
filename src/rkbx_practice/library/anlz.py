"""Rejilla de beats y frases desde los archivos ANLZ de Rekordbox.

Se usa el análisis de Rekordbox (sin análisis espectral propio):
- `PQTZ` (en .DAT): rejilla de beats (número de beat dentro del compás y tiempo).
- `PSSI` (en .EXT): estructura de frases (beat de inicio de cada frase).

La lectura binaria la hace pyrekordbox; este módulo solo expone un modelo
sencillo (:class:`BeatGrid`) que usan las detecciones y, en el futuro, las luces.
"""

from __future__ import annotations

import bisect
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class BeatGrid:
    times: list[float]            # segundos de cada beat
    beat_numbers: list[int]       # 1..4 dentro del compás
    phrase_start_beats: list[int] = field(default_factory=list)  # índice (1-based) de beat

    def __post_init__(self) -> None:
        if len(self.times) != len(self.beat_numbers):
            raise ValueError("times y beat_numbers deben tener la misma longitud")

    @classmethod
    def uniform(cls, bpm: float, first_beat_s: float = 0.0, n_beats: int = 2000,
                beats_per_bar: int = 4, phrase_bars: int = 8) -> "BeatGrid":
        period = 60.0 / bpm
        times = [first_beat_s + i * period for i in range(n_beats)]
        numbers = [(i % beats_per_bar) + 1 for i in range(n_beats)]
        phrases = list(range(1, n_beats + 1, beats_per_bar * phrase_bars))
        return cls(times, numbers, phrases)

    def nearest_beat(self, position_s: float) -> tuple[int, float] | None:
        """(índice 0-based del beat más cercano, desfase en segundos: posición - beat)."""
        if not self.times:
            return None
        i = bisect.bisect_left(self.times, position_s)
        candidates = [j for j in (i - 1, i) if 0 <= j < len(self.times)]
        j = min(candidates, key=lambda k: abs(self.times[k] - position_s))
        return j, position_s - self.times[j]

    def beat_offset_s(self, position_s: float) -> float | None:
        nb = self.nearest_beat(position_s)
        return nb[1] if nb else None

    def is_on_beat(self, position_s: float, tolerance_s: float) -> bool | None:
        off = self.beat_offset_s(position_s)
        return None if off is None else abs(off) <= tolerance_s

    def is_bar_start(self, position_s: float, tolerance_s: float) -> bool | None:
        nb = self.nearest_beat(position_s)
        if nb is None:
            return None
        j, off = nb
        return abs(off) <= tolerance_s and self.beat_numbers[j] == 1

    def is_phrase_start(self, position_s: float, tolerance_s: float) -> bool | None:
        if not self.phrase_start_beats:
            return None
        nb = self.nearest_beat(position_s)
        if nb is None:
            return None
        j, off = nb
        return abs(off) <= tolerance_s and (j + 1) in set(self.phrase_start_beats)


def _sibling(path: Path, ext: str) -> Path | None:
    for candidate in (path.with_suffix(ext), path.with_suffix(ext.lower())):
        if candidate.exists():
            return candidate
    return None


def read_beat_grid(anlz_path: str | Path) -> BeatGrid | None:
    """Lee la rejilla (y las frases si existen) de un tema. None si no se puede."""
    try:
        from pyrekordbox.anlz import AnlzFile
    except ImportError:  # pragma: no cover
        log.warning("pyrekordbox no disponible: sin rejilla de beats")
        return None
    path = Path(anlz_path)
    dat = _sibling(path, ".DAT") or (path if path.exists() else None)
    if dat is None:
        return None
    try:
        anlz = AnlzFile.parse_file(dat)
        tag = anlz.get_tag("PQTZ")
        beats = [int(b) for b in tag.get_beats()]
        times = [float(t) for t in tag.get_times()]
    except Exception as exc:  # noqa: BLE001
        log.warning("No se pudo leer la rejilla de %s: %s", dat, exc)
        return None
    if times and max(times) > 36000:  # milisegundos -> segundos
        times = [t / 1000.0 for t in times]
    phrases: list[int] = []
    ext = _sibling(path, ".EXT")
    if ext is not None:
        try:
            pssi = AnlzFile.parse_file(ext).get_tag("PSSI")
            phrases = sorted(int(e.beat) for e in pssi.content.entries)
        except Exception as exc:  # noqa: BLE001
            log.debug("Sin frases para %s: %s", ext, exc)
    return BeatGrid(times, beats, phrases)


class BeatGridCache:
    """Caché de rejillas por ruta ANLZ (las lecturas de disco son lentas)."""

    def __init__(self, share_dir: Path | None = None, reader=read_beat_grid):
        self._share_dir = share_dir
        self._reader = reader
        self._cache: dict[str, BeatGrid | None] = {}
        self._loading: set[str] = set()

    def _resolve(self, anlz_path: str) -> Path:
        p = Path(anlz_path.replace("\\", "/"))
        if not p.is_absolute() and self._share_dir is not None:
            p = self._share_dir / str(p).lstrip("/")
        return p

    def get(self, anlz_path: str | None) -> BeatGrid | None:
        """Lectura bloqueante (lee del disco si no está en caché)."""
        if not anlz_path:
            return None
        if anlz_path not in self._cache:
            self._cache[anlz_path] = self._reader(self._resolve(anlz_path))
        return self._cache[anlz_path]

    def get_nowait(self, anlz_path: str | None) -> BeatGrid | None:
        """Sin bloquear: si aún no está leída, lanza la lectura en segundo plano y devuelve None.
        Es la que usa el bucle en vivo (no puede pararse a leer el disco)."""
        if not anlz_path:
            return None
        if anlz_path in self._cache:
            return self._cache[anlz_path]
        self.prefetch(anlz_path)
        return None

    def prefetch(self, anlz_path: str | None) -> None:
        """Lee la rejilla en un hilo aparte (se llama al cargar un tema)."""
        if not anlz_path or anlz_path in self._cache or anlz_path in self._loading:
            return
        self._loading.add(anlz_path)

        def work() -> None:
            try:
                self._cache[anlz_path] = self._reader(self._resolve(anlz_path))
            except Exception as exc:  # noqa: BLE001
                log.warning("No se pudo leer la rejilla de %s: %s", anlz_path, exc)
                self._cache[anlz_path] = None
            finally:
                self._loading.discard(anlz_path)

        threading.Thread(target=work, name="beatgrid-prefetch", daemon=True).start()
