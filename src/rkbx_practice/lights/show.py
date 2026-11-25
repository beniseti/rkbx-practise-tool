"""Archivo de eventos de luces por tema (editable a mano) y su generación automática.

Formato (`data/lights/<track_id>.json`):

```json
{
  "format_version": 1,
  "track_id": "123",
  "title": "Artista - Título",
  "events": [
    {"beat": 65,  "label": "Chorus", "source": "phrase", "fx": "strobe", "params": {"dur_ms": 1000}},
    {"beat": 129, "label": "drop",   "source": "energy", "fx": "flash",  "params": {"color": "blanco", "dur_ms": 200}},
    {"beat": 193, "label": "Down",   "source": "phrase", "fx": null}
  ]
}
```

- `beat` es el índice de beat de la rejilla de Rekordbox (1-based): sigue cuadrando con cambios
  de pitch.
- `fx: null` = el evento existe (se ve en el editor) pero no envía nada.
- `source`: `phrase` (frase de Rekordbox), `energy` (cambio de energía) o `manual`.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .analysis import TrackAnalysis
from .commands import LightCommand

FORMAT_VERSION = 2   # 2: generado por estructura musical (papeles)


@dataclass
class LightEvent:
    beat: int
    label: str = ""
    source: str = "manual"
    fx: str | None = None
    params: dict = field(default_factory=dict)

    def command(self) -> LightCommand | None:
        return LightCommand(self.fx, dict(self.params)) if self.fx else None


@dataclass
class LightShow:
    track_id: str
    title: str = ""
    events: list[LightEvent] = field(default_factory=list)
    format_version: int = FORMAT_VERSION

    @property
    def outdated(self) -> bool:
        """Generado con una versión anterior y sin eventos añadidos a mano: se puede regenerar."""
        return self.format_version < FORMAT_VERSION and not any(e.source == "manual" for e in self.events)

    def sorted_events(self) -> list[LightEvent]:
        return sorted(self.events, key=lambda e: e.beat)

    def to_dict(self) -> dict:
        return {"format_version": self.format_version, "track_id": self.track_id, "title": self.title,
                "events": [asdict(e) for e in self.sorted_events()]}

    @classmethod
    def from_dict(cls, data: dict) -> "LightShow":
        events = [LightEvent(beat=int(e["beat"]), label=e.get("label", ""),
                             source=e.get("source", "manual"), fx=e.get("fx") or None,
                             params=dict(e.get("params") or {}))
                  for e in data.get("events", [])]
        return cls(track_id=str(data.get("track_id", "")), title=data.get("title", ""), events=events,
                   format_version=int(data.get("format_version", 1)))


# Efecto por papel musical (ver structure.py) y dos momentos especiales:
# «drop_hit» (el golpe justo en el drop) y «pre_drop» (último compás de la subida).
# Valores especiales: "until_next" = duración en ms hasta la siguiente sección;
# "live" = el BPM real del deck en el momento de enviar.
DEFAULT_RULES: dict[str, dict] = {
    "intro": {"fx": "breathe", "params": {"color": "azul", "period_ms": 4000}},
    "groove": {"fx": "pulse", "params": {"color": "morado", "bpm": "live"}},
    "peak": {"fx": "pulse", "params": {"color": "naranja", "bpm": "live"}},
    "breakdown": {"fx": "breathe", "params": {"color": "azul", "period_ms": 6000}},
    "buildup": {"fx": "build", "params": {"color": "blanco", "dur_ms": "until_next"}},
    "pre_drop": {"fx": "strobe", "params": {"dur_ms": "until_next", "hz": 16}},
    "drop_hit": {"fx": "flash", "params": {"color": "blanco", "dur_ms": 400}},
    "drop": {"fx": "pulse", "params": {"color": "rojo", "bpm": "live"}},
    "outro": {"fx": "breathe", "params": {"color": "azul", "period_ms": 6000}},
}


def effective_rules(rules: dict[str, dict] | None) -> dict[str, dict]:
    """Reglas por defecto con las de config.json encima (solo claves de papel conocidas)."""
    out = {k: {"fx": v["fx"], "params": dict(v.get("params", {}))} for k, v in DEFAULT_RULES.items()}
    for k, v in (rules or {}).items():
        key = k.lower()
        if key in out and isinstance(v, dict):
            out[key] = {"fx": v.get("fx"), "params": dict(v.get("params", {}))}
    return out


def _resolve(params: dict, ms_to_next: float | None) -> dict:
    out = {}
    for k, v in params.items():
        if v == "until_next":
            if ms_to_next is None:
                continue
            v = int(round(ms_to_next))
        out[k] = v
    return out


def generate_show(track_id: str, title: str, analysis: TrackAnalysis,
                  rules: dict[str, dict] | None = None) -> LightShow:
    """Eventos según la estructura musical (secciones con papel) y las reglas.

    - Al inicio de cada sección, el efecto de su papel.
    - En un drop: «drop_hit» en el primer beat y el efecto de «drop» en el segundo.
    - En una subida que acaba en drop: «pre_drop» en el último compás.
    """
    from .structure import ROLE_NAMES_ES
    r = effective_rules(rules)
    grid = analysis.grid
    times = grid.times
    n = len(times)

    def t(beat: int) -> float | None:
        return times[beat - 1] if 1 <= beat <= n else None

    def ms_between(a: int, b: int) -> float | None:
        ta, tb = t(a), t(min(b, n))
        return None if ta is None or tb is None else max(0.0, (tb - ta) * 1000.0)

    events: dict[int, LightEvent] = {}

    def add(beat: int, label: str, key: str, until: int | None, source: str = "structure") -> None:
        if not 1 <= beat <= n:
            return
        rule = r.get(key) or {}
        ms = ms_between(beat, until) if until else None
        events[beat] = LightEvent(beat, label, source, rule.get("fx"), _resolve(rule.get("params", {}), ms))

    sections = analysis.sections
    for k, sec in enumerate(sections):
        nxt = sections[k + 1] if k + 1 < len(sections) else None
        until = sec.end
        if sec.role == "drop":
            add(sec.start, f"{sec.name_es} · golpe", "drop_hit", until)
            add(sec.start + 1, sec.name_es, "drop", until)
        else:
            add(sec.start, sec.name_es, sec.role, until)
        if sec.role == "buildup" and nxt is not None and nxt.role == "drop":
            pre = nxt.start - 4                          # último compás antes del drop
            if pre > sec.start:
                add(pre, "Pre-drop", "pre_drop", nxt.start)
    if not sections:
        # Sin estructura (no debería pasar con rejilla): solo cambios de energía.
        for c in analysis.changes:
            key = "drop_hit" if c.kind == "drop" else "breakdown"
            add(c.beat, ROLE_NAMES_ES.get(c.kind, c.kind), key, None, "energy")
    return LightShow(track_id, title, sorted(events.values(), key=lambda e: e.beat))


class ShowStore:
    """Archivos de eventos en `lights.events_dir` (uno por tema)."""

    def __init__(self, directory: str | Path):
        self.dir = Path(directory)

    def path(self, track_id: str) -> Path:
        safe = "".join(ch for ch in str(track_id) if ch.isalnum() or ch in "-_")
        return self.dir / f"{safe}.json"

    def load(self, track_id: str) -> LightShow | None:
        p = self.path(track_id)
        if not p.exists():
            return None
        return LightShow.from_dict(json.loads(p.read_text(encoding="utf-8")))

    def save(self, show: LightShow) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        p = self.path(show.track_id)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(show.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)
        return p
