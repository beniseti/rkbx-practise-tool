"""Estructura musical de un tema a partir del análisis de Rekordbox (frases + 3 bandas por beat).

Cada sección (una frase de Rekordbox, partida si dentro hay un cambio fuerte de energía) recibe
un **papel** según su contenido y lo que viene antes:

- ``intro``: al principio, sin el bombo a pleno.
- ``groove``: bombo sonando (graves al nivel normal del tema).
- ``peak``: groove con mucha energía (frase Chorus o graves y agudos altos) que no viene de un break.
- ``breakdown``: sin graves (o muy bajos) y sin subida clara.
- ``buildup``: sin graves pero con medios/agudos subiendo, o frase **Up**: tensión hacia un drop.
- ``drop``: vuelven los graves fuertes justo después de un breakdown/buildup. Regla del usuario:
  **Up → Chorus es casi siempre un drop**.
- ``outro``: al final, bajando.

Todo sale del análisis de Rekordbox (CLAUDE.md §2): no se analiza el audio.
"""

from __future__ import annotations

from dataclasses import dataclass

ROLES = ("intro", "groove", "peak", "breakdown", "buildup", "drop", "outro")
ROLE_NAMES_ES = {"intro": "Intro", "groove": "Groove", "peak": "Pico", "breakdown": "Breakdown",
                 "buildup": "Subida", "drop": "Drop", "outro": "Outro"}

# Umbrales (relativos al nivel de graves «normal» del tema = percentil 80).
NO_KICK = 0.45        # por debajo: sin bombo
INTRO_OUTRO = 0.70    # intro/outro sin etiqueta: graves por debajo de esto
PEAK = 0.92           # pico: graves y agudos por encima de esto
RISING = 0.08         # subida: medios+agudos crecen al menos esto dentro de la sección
DROP_JUMP = 1.5       # drop sin etiqueta: los graves se multiplican al menos por esto


@dataclass
class Section:
    start: int            # beat de inicio (1-based)
    end: int              # beat de fin (exclusivo)
    role: str
    label: str            # etiqueta de Rekordbox («Chorus», «Up»…) o ""
    low: float            # graves medios relativos al nivel normal del tema
    mid: float
    high: float
    trend: float          # crecimiento de medios+agudos dentro de la sección

    @property
    def name_es(self) -> str:
        base = ROLE_NAMES_ES.get(self.role, self.role)
        return f"{base} ({self.label})" if self.label and self.label.lower() != base.lower() else base


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return s[min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))]


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _segments(analysis) -> list[tuple[int, int, str]]:
    """(inicio, fin, etiqueta): frases de Rekordbox, partidas en cambios fuertes de energía."""
    n = len(analysis.grid.times)
    starts: dict[int, str] = {}
    for p in analysis.phrases:
        if 1 <= p.beat <= n:
            starts[p.beat] = p.label
    # Un drop/break que no coincide con una frase (±4 beats) abre una sección nueva.
    for c in analysis.changes:
        if all(abs(c.beat - b) > 4 for b in starts):
            prev = max((b for b in starts if b < c.beat), default=None)
            starts[c.beat] = starts.get(prev, "") if prev else ""
    if not starts or min(starts) > 1:
        starts[1] = starts.get(1, "")
    beats = sorted(starts)
    return [(b, beats[i + 1] if i + 1 < len(beats) else n + 1, starts[b]) for i, b in enumerate(beats)]


def classify_sections(analysis) -> list[Section]:
    n = len(analysis.grid.times)
    if n == 0:
        return []
    bands = analysis.bands or {}
    low = bands.get("low") or [0.0] * n
    mid = bands.get("mid") or low
    high = bands.get("high") or low
    ref_low = _percentile(low, 0.8) or 1.0
    ref_mh = _percentile([m + h for m, h in zip(mid, high)], 0.8) or 1.0
    has_energy = any(low)

    raw = []
    for start, end, label in _segments(analysis):
        i0, i1 = start - 1, max(start, end - 1)
        seg_low, seg_mid, seg_high = low[i0:i1], mid[i0:i1], high[i0:i1]
        mh = [m + h for m, h in zip(seg_mid, seg_high)]
        q = max(1, len(mh) // 4)
        trend = (_mean(mh[-q:]) - _mean(mh[:q])) / ref_mh if mh else 0.0
        raw.append((start, end, label, _mean(seg_low) / ref_low, _mean(seg_mid), _mean(seg_high), trend))

    sections: list[Section] = []
    for k, (start, end, label, lo, mi, hi, trend) in enumerate(raw):
        lab = label.lower()
        prev = sections[-1] if sections else None
        prev_low = raw[k - 1][3] if k else None
        last = k == len(raw) - 1
        if lab.startswith("intro") or (k == 0 and has_energy and lo < INTRO_OUTRO and not lab):
            role = "intro"
        elif lab.startswith("outro") or (last and has_energy and lo < INTRO_OUTRO and k > 0):
            role = "outro"
        elif lab.startswith("up") or (has_energy and lo < NO_KICK and trend > RISING):
            role = "buildup"
        elif lab.startswith("down") or (has_energy and lo < NO_KICK):
            role = "breakdown"
        elif prev is not None and prev.role in ("buildup", "breakdown") and (
                lab.startswith("chorus") or not has_energy
                or (prev_low is not None and lo >= max(NO_KICK, prev_low * DROP_JUMP))):
            # Up → Chorus = drop (regla del usuario); y cualquier vuelta fuerte del bombo tras un break.
            role = "drop"
        elif lab.startswith("chorus") or (has_energy and lo >= PEAK and hi >= _percentile(high, 0.8)):
            role = "peak"
        else:
            role = "groove"
        sections.append(Section(start, end, role, label, round(lo, 3), round(mi, 3), round(hi, 3),
                                round(trend, 3)))
    return sections
