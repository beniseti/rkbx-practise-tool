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

FORMAT_VERSION = 1


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

    def sorted_events(self) -> list[LightEvent]:
        return sorted(self.events, key=lambda e: e.beat)

    def to_dict(self) -> dict:
        return {"format_version": FORMAT_VERSION, "track_id": self.track_id, "title": self.title,
                "events": [asdict(e) for e in self.sorted_events()]}

    @classmethod
    def from_dict(cls, data: dict) -> "LightShow":
        events = [LightEvent(beat=int(e["beat"]), label=e.get("label", ""),
                             source=e.get("source", "manual"), fx=e.get("fx") or None,
                             params=dict(e.get("params") or {}))
                  for e in data.get("events", [])]
        return cls(track_id=str(data.get("track_id", "")), title=data.get("title", ""), events=events)


def _rule_for(label: str, rules: dict[str, dict]) -> dict | None:
    """Regla por etiqueta exacta (sin distinguir mayúsculas) o por su primera palabra
    («Verse 2» → «verse»)."""
    low = {k.lower(): v for k, v in rules.items()}
    key = label.lower()
    if key in low:
        return low[key]
    first = key.split(" ")[0]
    return low.get(first)


def generate_show(track_id: str, title: str, analysis: TrackAnalysis,
                  rules: dict[str, dict]) -> LightShow:
    """Eventos a partir de las frases y los cambios de energía, con el efecto según `rules`.

    `rules`: {"Chorus": {"fx": "strobe", "params": {...}}, "drop": {...}, ...}. Si dos eventos caen
    en el mismo beat (p. ej. frase Chorus y drop), se queda uno solo: el cambio de energía.
    """
    by_beat: dict[int, LightEvent] = {}
    for p in analysis.phrases:
        rule = _rule_for(p.label, rules) or {}
        by_beat[p.beat] = LightEvent(p.beat, p.label, "phrase", rule.get("fx"), dict(rule.get("params", {})))
    for c in analysis.changes:
        rule = _rule_for(c.kind, rules) or {}
        prev = by_beat.get(c.beat)
        label = f"{prev.label} + {c.kind}" if prev else c.kind
        by_beat[c.beat] = LightEvent(c.beat, label, "energy", rule.get("fx") or (prev.fx if prev else None),
                                     dict(rule.get("params", {}) or (prev.params if prev else {})))
    return LightShow(track_id, title, sorted(by_beat.values(), key=lambda e: e.beat))


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
