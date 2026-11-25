"""Análisis de un tema para las luces, a partir del análisis de Rekordbox (archivos ANLZ).

- Rejilla de beats (`PQTZ`, en .DAT).
- Frases con su tipo (`PSSI`, en .EXT): Intro, Up, Down, Chorus, Outro, Verse…
- Energía de graves por beat a partir de la forma de onda por bandas (`PWV7`, 3 bandas,
  en .2EX; si no existe, `PWV5` en .EXT como aproximación de energía total).
- Cambios de energía: subidas fuertes («drop») y bajadas («break»), alineados a compás.

Sin análisis espectral propio (CLAUDE.md §2): todo sale de lo que ya calculó Rekordbox.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from ..library.anlz import BeatGrid, _sibling

log = logging.getLogger(__name__)

# Tipos de frase de PSSI según el «mood» del tema (formato documentado por Deep Symmetry).
_PHRASES_HIGH = {1: "Intro", 2: "Up", 3: "Down", 5: "Chorus", 6: "Outro"}
_PHRASES_MID = {1: "Intro", 2: "Verse 1", 3: "Verse 2", 4: "Verse 3", 5: "Verse 4", 6: "Verse 5",
                7: "Verse 6", 8: "Bridge", 9: "Chorus", 10: "Outro"}
_PHRASES_LOW = {1: "Intro", 2: "Verse 1", 3: "Verse 1", 4: "Verse 1", 5: "Verse 2", 6: "Verse 2",
                7: "Verse 2", 8: "Bridge", 9: "Chorus", 10: "Outro"}
_MOODS = {1: _PHRASES_HIGH, 2: _PHRASES_MID, 3: _PHRASES_LOW}

WAVEFORM_ENTRIES_PER_SECOND = 150  # PWV5 / PWV7: 150 columnas por segundo


@dataclass(frozen=True)
class Phrase:
    beat: int          # beat de inicio (1-based, como en Rekordbox)
    label: str         # Intro, Up, Down, Chorus, Outro, Verse 1…


@dataclass(frozen=True)
class EnergyChange:
    beat: int          # beat de inicio del compás donde cambia (1-based)
    kind: str          # "drop" (sube la energía) o "break" (baja)
    ratio: float       # energía después / energía antes


@dataclass
class TrackAnalysis:
    grid: BeatGrid
    phrases: list[Phrase] = field(default_factory=list)
    beat_energy: list[float] = field(default_factory=list)   # 0..1 por beat (graves)
    changes: list[EnergyChange] = field(default_factory=list)
    # Energía por beat de cada banda («low», «mid», «high»), 0..1 normalizada en el tema.
    bands: dict[str, list[float]] = field(default_factory=dict)
    sections: list = field(default_factory=list)              # structure.Section
    source: str = ""                                          # "PWV7", "PWV5" o ""

    def beat_time(self, beat: int) -> float | None:
        i = beat - 1
        return self.grid.times[i] if 0 <= i < len(self.grid.times) else None

    def bar_beat(self, beat: int) -> str:
        """`compás.beat` como en Rekordbox (1.1, 1.2…). Un compás incompleto al inicio es el 0."""
        i = beat - 1
        if not 0 <= i < len(self.grid.beat_numbers):
            return "?"
        bar = sum(1 for n in self.grid.beat_numbers[: i + 1] if n == 1)
        return f"{bar}.{self.grid.beat_numbers[i]}"


def phrase_label(mood: int, kind: int) -> str:
    return _MOODS.get(mood, _PHRASES_HIGH).get(kind, f"Frase {kind}")


# ---------------------------------------------------------------------------
# Energía y cambios
# ---------------------------------------------------------------------------
def energy_per_beat(samples: list[float], grid: BeatGrid,
                    rate: float = WAVEFORM_ENTRIES_PER_SECOND) -> list[float]:
    """Media de la energía (0..1) entre cada beat y el siguiente."""
    out = []
    times = grid.times
    for i, t in enumerate(times):
        t_end = times[i + 1] if i + 1 < len(times) else t + (t - times[i - 1] if i else 0.5)
        a, b = int(t * rate), max(int(t_end * rate), int(t * rate) + 1)
        chunk = samples[a:b]
        out.append(sum(chunk) / len(chunk) if chunk else 0.0)
    # Normalización robusta: percentil 95 (un pico suelto no aplasta el resto del tema).
    ref = sorted(out)[int(0.95 * (len(out) - 1))] if out else 0.0
    ref = ref or (max(out) if out else 0.0)
    return [min(1.0, v / ref) for v in out] if ref > 0 else out


def detect_changes(grid: BeatGrid, energy: list[float], window_beats: int = 16,
                   rise_ratio: float = 1.8, fall_ratio: float = 0.55,
                   min_gap_beats: int = 32, min_level: float = 0.15) -> list[EnergyChange]:
    """Cambios fuertes de energía en inicio de compás: compara la media de `window_beats`
    antes y después. Separa los cambios al menos `min_gap_beats`."""
    changes: list[EnergyChange] = []
    n = len(energy)
    last = -10 ** 9
    for i in range(window_beats, n - window_beats):
        if grid.beat_numbers[i] != 1:
            continue
        before = sum(energy[i - window_beats:i]) / window_beats
        after = sum(energy[i:i + window_beats]) / window_beats
        if max(before, after) < min_level:
            continue
        ratio = after / before if before > 1e-6 else float("inf")
        kind = "drop" if ratio >= rise_ratio else "break" if ratio <= fall_ratio else None
        if kind is None:
            continue
        if i - last < min_gap_beats:
            # Nos quedamos con el cambio más fuerte de la zona.
            prev = changes[-1]
            if prev.kind == kind and _strength(ratio) > _strength(prev.ratio):
                changes[-1] = EnergyChange(i + 1, kind, ratio)
                last = i
            continue
        changes.append(EnergyChange(i + 1, kind, ratio))
        last = i
    return changes


def _strength(ratio: float) -> float:
    return ratio if ratio >= 1 else 1 / max(ratio, 1e-6)


# ---------------------------------------------------------------------------
# Lectura de ANLZ con pyrekordbox
# ---------------------------------------------------------------------------
def _pwv7_bands(tag) -> dict[str, list[float]]:
    """PWV7 (3 bandas): 3 bytes por columna en el orden documentado medios, agudos, graves."""
    raw = _raw_entries(tag)
    if not raw:
        return {}
    n = len(raw) // 3
    return {
        "mid": [raw[3 * i] / 255.0 for i in range(n)],
        "high": [raw[3 * i + 1] / 255.0 for i in range(n)],
        "low": [raw[3 * i + 2] / 255.0 for i in range(n)],
    }


def _pwv5_height(tag) -> list[float]:
    """PWV5 (color): 2 bytes por columna; altura en los 5 bits bajos (aprox. energía total)."""
    raw = _raw_entries(tag)
    if not raw:
        return []
    return [(((raw[i] << 8) | raw[i + 1]) >> 2 & 0x1F) / 31.0 for i in range(0, len(raw) - 1, 2)]


def _raw_entries(tag) -> bytes:
    content = getattr(tag, "content", None)
    for attr in ("entries", "data"):
        v = getattr(content, attr, None)
        if isinstance(v, (bytes, bytearray)):
            return bytes(v)
        if isinstance(v, list) and v and isinstance(v[0], int):
            return bytes(v)
    return b""


def read_track_analysis(anlz_path: str | Path, grid: BeatGrid | None = None) -> TrackAnalysis | None:
    """Rejilla + frases + energía + cambios de un tema. None si no hay rejilla."""
    try:
        from pyrekordbox.anlz import AnlzFile
    except ImportError:  # pragma: no cover
        log.warning("pyrekordbox no disponible: sin análisis para luces")
        return None
    from ..library.anlz import read_beat_grid
    path = Path(anlz_path)
    grid = grid or read_beat_grid(path)
    if grid is None:
        return None
    phrases: list[Phrase] = []
    samples: dict[str, list[float]] = {}
    source = ""
    ext = _sibling(path, ".EXT")
    if ext is not None:
        try:
            f = AnlzFile.parse_file(ext)
            try:
                pssi = f.get_tag("PSSI")
                mood = int(getattr(pssi.content, "mood", 1))
                for e in pssi.content.entries:
                    phrases.append(Phrase(int(e.beat), phrase_label(mood, int(e.kind))))
            except Exception as exc:  # noqa: BLE001
                log.debug("Sin frases en %s: %s", ext, exc)
            try:
                h = _pwv5_height(f.get_tag("PWV5"))
                if h:
                    samples, source = {"low": h, "mid": h, "high": h}, "PWV5"
            except Exception:  # noqa: BLE001
                pass
        except Exception as exc:  # noqa: BLE001
            log.debug("No se pudo leer %s: %s", ext, exc)
    ex2 = _sibling(path, ".2EX")
    if ex2 is not None:
        try:
            b = _pwv7_bands(AnlzFile.parse_file(ex2).get_tag("PWV7"))
            if b:
                samples, source = b, "PWV7"
        except Exception as exc:  # noqa: BLE001
            log.debug("Sin PWV7 en %s: %s", ex2, exc)
    return build_analysis(grid, sorted(phrases, key=lambda p: p.beat), samples, source)


def build_analysis(grid: BeatGrid, phrases: list[Phrase], samples: dict[str, list[float]],
                   source: str = "") -> TrackAnalysis:
    """Analysis completo a partir de rejilla, frases y columnas de forma de onda por banda."""
    from .structure import classify_sections
    bands = {k: energy_per_beat(v, grid) for k, v in samples.items() if v}
    energy = bands.get("low", [])
    changes = detect_changes(grid, energy) if energy else []
    a = TrackAnalysis(grid=grid, phrases=phrases, beat_energy=energy, changes=changes,
                      bands=bands, source=source)
    a.sections = classify_sections(a)
    return a
