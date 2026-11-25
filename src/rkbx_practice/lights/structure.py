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
    intensity: float = 0.0  # 0..1 (drops y picos): graves, agudos y contraste con lo anterior
    tier: str = ""          # "soft", "medium" o "hard" según la intensidad (drops y picos)
    occurrence: int = 0     # n.º de drop/pico dentro del tema (0, 1, 2…) para variar efectos

    @property
    def name_es(self) -> str:
        base = ROLE_NAMES_ES.get(self.role, self.role)
        if self.tier:
            base += {"soft": " suave", "medium": " medio", "hard": " fuerte"}[self.tier]
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
    _rate_intensity(sections, has_energy)
    return sections


# Intensidad de drops y picos (pedido del usuario: el Chorus no debe tener siempre el mismo
# efecto; varía según lo fuerte que sea el drop).
SOFT_BELOW = 0.50
HARD_FROM = 0.72


def tier_for(intensity: float) -> str:
    return "soft" if intensity < SOFT_BELOW else "hard" if intensity >= HARD_FROM else "medium"


def _rate_intensity(sections: list[Section], has_energy: bool) -> None:
    counts: dict[str, int] = {}
    for k, sec in enumerate(sections):
        if sec.role not in ("drop", "peak"):
            continue
        if has_energy:
            prev_low = sections[k - 1].low if k else 0.0
            lo_n = min(1.0, sec.low / 1.2)
            contrast = min(1.0, max(0.0, sec.low - prev_low))
            intensity = 0.45 * lo_n + 0.30 * min(1.0, sec.high) + 0.25 * contrast
        else:
            intensity = 0.70 if sec.role == "drop" else 0.50   # sin bandas: solo por etiqueta
        sec.intensity = round(intensity, 3)
        sec.tier = tier_for(intensity)
        sec.occurrence = counts.get(sec.role, 0)
        counts[sec.role] = sec.occurrence + 1


# Minidrops (pedido del usuario, ver captura de Rekordbox): dentro de un Chorus/groove el bombo
# desaparece 1-8 beats y vuelve en inicio de compás. Se hace un apagón y se cambia de efecto.
MINI_LOW = 0.35        # graves del hueco por debajo de esto (relativo al nivel normal del tema)
MINI_CONTEXT = 0.60    # graves antes y después del hueco por encima de esto
MINI_MAX_BEATS = 8


@dataclass(frozen=True)
class MiniDrop:
    dip_start: int     # primer beat sin bombo (1-based)
    returns: int       # beat en que vuelve el bombo (inicio de compás)


def detect_minidrops(analysis) -> list[MiniDrop]:
    low = (analysis.bands or {}).get("low") or []
    n = len(low)
    if n < 16:
        return []
    ref = _percentile(low, 0.8) or 1.0
    rel = [v / ref for v in low]
    numbers = analysis.grid.beat_numbers
    kick_roles = {"groove", "peak", "drop"}

    def role_at(beat: int) -> str | None:
        for sec in analysis.sections:
            if sec.start <= beat < sec.end:
                return sec.role
        return None

    drops_after_build = {sec.start for k, sec in enumerate(analysis.sections)
                         if sec.role == "drop" and k and analysis.sections[k - 1].role == "buildup"}
    found: list[MiniDrop] = []
    i = 4
    while i < n - 4:
        if rel[i] >= MINI_LOW:
            i += 1
            continue
        j = i
        while j < n and rel[j] < MINI_LOW:
            j += 1
        length = j - i                        # beats sin bombo: i .. j-1 (0-based)
        before = sum(rel[max(0, i - 4):i]) / max(1, min(4, i))
        after = sum(rel[j:j + 4]) / max(1, len(rel[j:j + 4]))
        # La vuelta del bombo se ajusta al inicio de compás más cercano (±1 beat de margen).
        bar = next((k for k in (j, j - 1, j + 1) if 0 <= k < n and numbers[k] == 1), None)
        returns = (bar + 1) if bar is not None else j + 1   # 1-based
        if (1 <= length <= MINI_MAX_BEATS and before >= MINI_CONTEXT and after >= MINI_CONTEXT
                and bar is not None and returns not in drops_after_build
                and role_at(returns) in kick_roles and role_at(i + 1) in kick_roles | {None}):
            found.append(MiniDrop(i + 1, returns))
        i = j
    return found
