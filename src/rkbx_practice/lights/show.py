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

FORMAT_VERSION = 7   # 7: subidas encadenadas (prebuild) y apagón ≥ 4 beats · 6: minidrops · 2: estructura · 3: apagón + drop en movimiento · 4: siempre en movimiento · 5: paleta por tema e intensidad de drops


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


# Efecto por papel musical (ver structure.py) y momentos especiales.
# - Colores: `$primary`, `$secondary`, `$accent` = paleta del tema (palette.py), así una misma
#   canción mantiene sus tonos.
# - Drops y picos (Chorus) por intensidad: `drop_soft|medium|hard`, `peak_soft|medium|hard`
#   (pedido del usuario: el Chorus no siempre igual). Un valor puede ser una lista de variantes:
#   el 1.er drop del tema usa la primera, el 2.º la segunda…
# - Valores especiales: "until_next" = ms hasta la siguiente sección; "live" = BPM real del deck.
# - Nunca estático (pedido del usuario): todos los fondos se mueven; solo el apagón es quieto.
DEFAULT_RULES: dict[str, object] = {
    "intro": {"fx": "wave", "params": {"color": "$primary", "color2": "$secondary", "bpm": "live", "beats": 4}},
    "groove": {"fx": "alternate", "params": {"color": "$primary", "color2": "$secondary", "bpm": "live"}},
    "peak_soft": {"fx": "pulse", "params": {"color": "$secondary", "bpm": "live"}},
    "peak_medium": [
        {"fx": "alternate", "params": {"color": "$secondary", "color2": "$accent", "bpm": "live"}},
        {"fx": "wave", "params": {"color": "$secondary", "color2": "$primary", "bpm": "live", "beats": 2}},
    ],
    "peak_hard": [
        {"fx": "chase", "params": {"color": "$secondary", "bpm": "live", "beats": 1}},
        {"fx": "alternate", "params": {"color": "$accent", "color2": "$secondary", "bpm": "live"}},
    ],
    "breakdown": {"fx": "twinkle", "params": {"color": "$secondary", "color2": "$primary", "density": 0.15,
                                              "bpm": "live"}},
    "buildup": {"fx": "build", "params": {"color": "blanco", "dur_ms": "until_next"}},
    # Varias subidas seguidas (pedido del usuario: varios «build» seguidos no lucen): las primeras
    # son un solo «prebuild» que va cargando los LEDs a lo largo de las tiras hasta la última,
    # que hace el «build» final.
    "prebuild": {"fx": "prebuild", "params": {"color": "$secondary", "dur_ms": "until_next"}},
    "pre_drop": {"fx": "strobe", "params": {"dur_ms": "until_next", "hz": 16}},
    # Subida → drop (Up → Chorus), pedido por el usuario: apagón (`beats` beats antes del drop,
    # como mínimo MIN_BLACKOUT_BEATS) y en el drop se encienden con un efecto en movimiento.
    "blackout": {"fx": "off", "params": {}, "beats": 4},
    "drop_hit": {"fx": None, "params": {}},
    "drop_soft": [
        {"fx": "pulse", "params": {"color": "$primary", "bpm": "live"}},
        {"fx": "wave", "params": {"color": "$primary", "color2": "$accent", "bpm": "live", "beats": 2}},
    ],
    "drop_medium": [
        {"fx": "alternate", "params": {"color": "$primary", "color2": "$accent", "bpm": "live"}},
        {"fx": "chase", "params": {"color": "$primary", "bpm": "live", "beats": 1}},
    ],
    "drop_hard": [
        {"fx": "chase", "params": {"color": "$accent", "bpm": "live", "beats": 0.5}},
        {"fx": "alternate", "params": {"color": "$accent", "color2": "$primary", "bpm": "live"}},
        {"fx": "wave", "params": {"color": "$accent", "color2": "$primary", "bpm": "live", "beats": 1}},
    ],
    # Minidrops (pedido del usuario): apagón durante el hueco sin bombo (como mucho `beats` beats)
    # y, al volver el bombo, un efecto distinto cada vez (se van alternando las variantes).
    "mini_blackout": {"fx": "off", "params": {}, "beats": 4},
    "mini_drop": [
        {"fx": "chase", "params": {"color": "$accent", "bpm": "live", "beats": 0.5}},
        {"fx": "alternate", "params": {"color": "$accent", "color2": "$primary", "bpm": "live"}},
        {"fx": "wave", "params": {"color": "$accent", "color2": "$secondary", "bpm": "live", "beats": 1}},
    ],
    "outro": {"fx": "wave", "params": {"color": "$primary", "color2": "$secondary", "bpm": "live", "beats": 8}},
}


MIN_BLACKOUT_BEATS = 4   # pedido del usuario: apagar la luz como mínimo 4 beats antes del drop


def _copy_rule(v: dict) -> dict:
    d = {"fx": v.get("fx"), "params": dict(v.get("params", {}))}
    if "beats" in v:
        d["beats"] = v["beats"]
    return d


def effective_rules(rules: dict | None) -> dict[str, list[dict]]:
    """Reglas por defecto con las de config.json encima. Cada clave → lista de variantes.
    `drop` o `peak` sueltos en config.json valen para todas sus intensidades."""
    def as_list(v) -> list[dict]:
        return [_copy_rule(x) for x in (v if isinstance(v, list) else [v]) if isinstance(x, dict)]

    out = {k: as_list(v) for k, v in DEFAULT_RULES.items()}
    for k, v in (rules or {}).items():
        key = k.lower()
        variants = as_list(v)
        if not variants:
            continue
        if key in ("drop", "peak"):
            for tier in ("soft", "medium", "hard"):
                out[f"{key}_{tier}"] = variants
        elif key in out:
            if "beats" in out[key][0]:
                for x in variants:
                    x.setdefault("beats", out[key][0]["beats"])
            out[key] = variants
    return out


def pick_rule(rules: dict[str, list[dict]], key: str, occurrence: int = 0) -> dict:
    variants = rules.get(key) or []
    return variants[occurrence % len(variants)] if variants else {}


def _resolve(params: dict, ms_to_next: float | None, palette: dict | None = None) -> dict:
    from .palette import apply_palette
    params = apply_palette(params, palette)
    out = {}
    for k, v in params.items():
        if v == "until_next":
            if ms_to_next is None:
                continue
            v = int(round(ms_to_next))
        out[k] = v
    return out


def generate_show(track_id: str, title: str, analysis: TrackAnalysis,
                  rules: dict | None = None, palette: dict | None = None) -> LightShow:
    """Eventos según la estructura musical (secciones con papel) y las reglas.

    - Al inicio de cada sección, el efecto de su papel.
    - En un drop: «drop_hit» en el primer beat y el efecto de «drop» en el segundo.
    - Varias subidas seguidas: un «prebuild» (carga los LEDs) hasta la última, que hace el «build».
    - Subida que acaba en drop: «build» → «pre_drop» (un compás) → apagón (≥ 4 beats) → drop.
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

    def add(beat: int, label: str, key: str, until: int | None, source: str = "structure",
            occurrence: int = 0) -> None:
        if not 1 <= beat <= n:
            return
        rule = pick_rule(r, key, occurrence)
        ms = ms_between(beat, until) if until else None
        events[beat] = LightEvent(beat, label, source, rule.get("fx"),
                                  _resolve(rule.get("params", {}), ms, palette))

    sections = analysis.sections
    # Subidas seguidas: la cadena empieza en la primera; las anteriores a la última son un prebuild.
    chain_first: dict[int, int] = {}            # índice de sección → índice de la primera de su cadena
    for k, sec in enumerate(sections):
        if sec.role == "buildup":
            prev = k - 1
            chain_first[k] = chain_first[prev] if prev >= 0 and sections[prev].role == "buildup" else k
    for k, sec in enumerate(sections):
        nxt = sections[k + 1] if k + 1 < len(sections) else None
        until = sec.end
        if sec.role in ("drop", "peak"):
            key = f"{sec.role}_{sec.tier or 'medium'}"
            if sec.role == "drop" and pick_rule(r, "drop_hit").get("fx"):
                add(sec.start, f"{sec.name_es} · golpe", "drop_hit", until)
                add(sec.start + 1, sec.name_es, key, until, occurrence=sec.occurrence)
            else:
                add(sec.start, sec.name_es, key, until, occurrence=sec.occurrence)
        elif sec.role == "buildup":
            last = nxt is None or nxt.role != "buildup"
            first = chain_first[k]
            if not last:
                if first == k:                   # un solo prebuild para todas menos la última
                    end_k = k
                    while end_k + 1 < len(sections) and sections[end_k + 1].role == "buildup":
                        end_k += 1
                    add(sec.start, f"{sec.name_es} · carga", "prebuild", sections[end_k].start)
                continue
            # Última subida de la cadena: build final → pre-drop → apagón (≥ 4 beats) → drop.
            if nxt is not None and nxt.role == "drop":
                black = pick_rule(r, "blackout")
                gap = max(MIN_BLACKOUT_BEATS, int(black.get("beats", MIN_BLACKOUT_BEATS))) \
                    if black.get("fx") else 0
                dark = nxt.start - gap
                pre = dark - 4                   # el compás antes del apagón
                chain_start = sections[first].start
                build_end = pre if pre > sec.start else (dark if dark > sec.start else nxt.start)
                add(sec.start, sec.name_es, "buildup", build_end)
                if sec.start < pre < dark:
                    add(pre, "Pre-drop", "pre_drop", dark)
                if gap and dark > chain_start:
                    if dark <= sec.start:        # subida corta: el apagón manda
                        events.pop(sec.start, None)
                    add(max(dark, chain_start), "Apagón", "blackout", nxt.start)
            else:
                add(sec.start, sec.name_es, "buildup", until)
        else:
            add(sec.start, sec.name_es, sec.role, until)
    # Minidrops: apagón en el hueco y cambio de efecto al volver el bombo.
    for k, md in enumerate(getattr(analysis, "minidrops", []) or []):
        black = pick_rule(r, "mini_blackout")
        if black.get("fx"):
            dark = max(md.dip_start, md.returns - int(black.get("beats", 4)))
            if dark < md.returns:
                add(dark, "Minidrop · apagón", "mini_blackout", md.returns)
        sec = next((x for x in sections if x.start <= md.returns < x.end), None)
        add(md.returns, f"Minidrop ({sec.name_es})" if sec else "Minidrop", "mini_drop",
            sec.end if sec else None, occurrence=k)
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
