"""Grabación de sesiones y guardado en `history/` (un JSON por sesión).

La grabación empieza y termina con el botón de la app (no el REC de Rekordbox).
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from ..config import SessionConfig
from ..events import BassSwap, CutBurst, LiveEvent, TrackLoaded, TransitionEvent
from ..library.memory_library import InMemoryLibrary
from ..session.compatibility import transition

log = logging.getLogger(__name__)

FORMAT_VERSION = 1


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="milliseconds") if dt else None


class SessionRecorder:
    def __init__(self, history_dir: str | Path, library: InMemoryLibrary | None,
                 settings: SessionConfig, decks: int = 2):
        self.dir = Path(history_dir)
        self.library = library
        self.settings = settings
        self.decks = decks
        self._lock = threading.Lock()
        self._data: dict | None = None

    @property
    def recording(self) -> bool:
        return self._data is not None

    def start(self, source_name: str | None, extra_settings: dict | None = None) -> None:
        with self._lock:
            self._data = {
                "format_version": FORMAT_VERSION,
                "started_at": _iso(datetime.now()),
                "ended_at": None,
                "source": source_name,
                "settings": {**asdict(self.settings), "decks": self.decks, **(extra_settings or {})},
                "tracks": [],
                "transitions": [],
                "fader_cuts": [],
                "bass_swaps": [],
            }

    def stop(self, pending_events: list[LiveEvent] | None = None) -> Path | None:
        if pending_events:
            self.handle(pending_events)
        with self._lock:
            if self._data is None:
                return None
            data, self._data = self._data, None
        data["ended_at"] = _iso(datetime.now())
        return self._save(data)

    def counts(self) -> dict[str, int]:
        with self._lock:
            if self._data is None:
                return {}
            return {k: len(self._data[k]) for k in ("tracks", "transitions", "fader_cuts", "bass_swaps")}

    # ------------------------------------------------------------------
    def _track_info(self, track_id: str | None) -> dict:
        t = self.library.track(track_id) if (self.library and track_id) else None
        if t is None:
            return {"id": track_id, "title": None, "artist": None, "bpm": None, "camelot": None}
        return {"id": t.id, "title": t.title, "artist": t.artist, "bpm": t.bpm,
                "camelot": str(t.camelot) if t.camelot else None}

    def handle(self, events: list[LiveEvent]) -> None:
        with self._lock:
            if self._data is None:
                return
            for ev in events:
                try:
                    self._handle_one(ev)
                except Exception:  # noqa: BLE001
                    log.exception("No se pudo registrar el evento %r", ev)

    def _handle_one(self, ev: LiveEvent) -> None:
        d = self._data
        assert d is not None
        if isinstance(ev, TrackLoaded):
            info = self._track_info(ev.track_id)
            d["tracks"].append({"at": _iso(ev.at), "deck": ev.deck, **info,
                                "anlz_path": ev.track_path})
        elif isinstance(ev, TransitionEvent):
            out_t = self.library.track(ev.out_track_id) if (self.library and ev.out_track_id) else None
            in_t = self.library.track(ev.in_track_id) if (self.library and ev.in_track_id) else None
            compat = None
            if out_t is not None and in_t is not None:
                tr_info = transition(out_t, in_t, self.settings)
                compat = {
                    "compatible": tr_info.compatible,
                    "bpm_compatible": tr_info.bpm.compatible,
                    "bpm_diff_pct": round(tr_info.bpm.diff_pct, 2),
                    "tempo_multiplier": tr_info.bpm.multiplier,
                    "key_relation": tr_info.key.value if tr_info.key else None,
                }
            d["transitions"].append({
                "start": _iso(ev.start), "end": _iso(ev.end),
                "duration_s": round(ev.duration_s, 3),
                "out": {"deck": ev.out_deck, **self._track_info(ev.out_track_id),
                        "bpm_live": ev.out_bpm},
                "in": {"deck": ev.in_deck, **self._track_info(ev.in_track_id),
                       "bpm_live": ev.in_bpm},
                "compatibility": compat,
            })
        elif isinstance(ev, CutBurst):
            d["fader_cuts"].append({
                "at": _iso(ev.start), "deck": ev.deck, "track_id": ev.track_id, "count": ev.count,
                "cuts": [{"at": _iso(c.at), "duration_ms": round(c.duration_s * 1000, 1),
                          "on_beat": c.on_beat,
                          "beat_offset_ms": None if c.beat_offset_ms is None else round(c.beat_offset_ms, 1)}
                         for c in ev.cuts],
            })
        elif isinstance(ev, BassSwap):
            d["bass_swaps"].append({
                "at": _iso(ev.at), "from_deck": ev.from_deck, "to_deck": ev.to_deck,
                "from_track_id": ev.from_track_id, "to_track_id": ev.to_track_id,
                "gap_beats": round(ev.gap_beats, 2), "on_bar": ev.on_bar, "on_phrase": ev.on_phrase,
            })

    def _save(self, data: dict) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.fromisoformat(data["started_at"]).strftime("%Y%m%d-%H%M%S")
        path = self.dir / f"session-{stamp}.json"
        n = 1
        while path.exists():
            n += 1
            path = self.dir / f"session-{stamp}-{n}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        log.info("Sesión guardada en %s", path)
        return path


def list_sessions(history_dir: str | Path) -> list[Path]:
    return sorted(Path(history_dir).glob("session-*.json"), reverse=True)


def load_session(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def summarize(data: dict) -> str:
    """Resumen legible (en español) de una sesión grabada."""
    lines = [
        f"Sesión: {data.get('started_at')} → {data.get('ended_at')}",
        f"Origen: {data.get('source') or '—'}",
        f"Temas cargados: {len(data.get('tracks', []))}",
    ]
    for t in data.get("tracks", []):
        name = f"{t.get('artist') or ''} - {t.get('title') or t.get('id') or t.get('anlz_path')}"
        lines.append(f"  {t['at'][11:19]}  deck {t['deck']}  {name}  "
                     f"({t.get('bpm') or '?'} BPM, {t.get('camelot') or '?'})")
    trs = data.get("transitions", [])
    lines.append(f"Transiciones: {len(trs)}")
    for tr in trs:
        c = tr.get("compatibility") or {}
        ok = {True: "compatible", False: "NO compatible", None: "?"}[c.get("compatible")]
        lines.append(f"  {tr['start'][11:19]}  {tr['out']['deck']}→{tr['in']['deck']}  "
                     f"{tr['duration_s']:.1f} s  {tr['out'].get('camelot') or '?'}→"
                     f"{tr['in'].get('camelot') or '?'}  {ok}")
    cuts = data.get("fader_cuts", [])
    total = sum(c["count"] for c in cuts)
    on_beat = sum(1 for b in cuts for c in b["cuts"] if c.get("on_beat"))
    rated = sum(1 for b in cuts for c in b["cuts"] if c.get("on_beat") is not None)
    lines.append(f"Fader cuts: {len(cuts)} ráfagas, {total} cortes"
                 + (f", {on_beat}/{rated} a tiempo" if rated else ""))
    for b in cuts:
        marks = "".join({True: "✓", False: "✗", None: "·"}[c.get("on_beat")] for c in b["cuts"])
        offs = [c["beat_offset_ms"] for c in b["cuts"] if c.get("beat_offset_ms") is not None]
        avg = f"  desfase medio {sum(abs(o) for o in offs) / len(offs):.0f} ms" if offs else ""
        lines.append(f"  {b['at'][11:19]}  deck {b['deck']}  {b['count']} cortes  {marks}{avg}")
    swaps = data.get("bass_swaps", [])
    on_bar = sum(1 for s in swaps if s.get("on_bar"))
    on_phrase = sum(1 for s in swaps if s.get("on_phrase"))
    lines.append(f"Bass swaps: {len(swaps)} ({on_bar} en compás, {on_phrase} en frase)")
    yn = {True: "sí", False: "no", None: "?"}
    for sw in swaps:
        lines.append(f"  {sw['at'][11:19]}  {sw['from_deck']}→{sw['to_deck']}  "
                     f"separación {sw['gap_beats']:.1f} beats  compás: {yn[sw.get('on_bar')]}  "
                     f"frase: {yn[sw.get('on_phrase')]}")
    return "\n".join(lines)
