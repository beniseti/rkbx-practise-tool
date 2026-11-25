"""Biblioteca en memoria: base común para master.db y para el formato JSON.

La biblioteca se carga una vez (snapshot) y luego se consulta sin tocar la BD.
"""

from __future__ import annotations

import json
from pathlib import Path

from .anlz_path import normalize_anlz_key
from .models import NodeKind, PlaylistNode, Track


class InMemoryLibrary:
    def __init__(self, tracks: list[Track], nodes: list[PlaylistNode],
                 playlist_tracks: dict[str, list[str]]):
        self._tracks: dict[str, Track] = {t.id: t for t in tracks}
        self._nodes: dict[str, PlaylistNode] = {}
        self._playlist_tracks = playlist_tracks
        # Carpeta `share` de Rekordbox (para resolver rutas ANLZ relativas).
        self.share_dir: Path | None = None
        self._roots: list[PlaylistNode] = []
        for n in nodes:
            n.children = []
            self._nodes[n.id] = n
        for n in nodes:
            parent = self._nodes.get(n.parent_id) if n.parent_id else None
            (parent.children if parent else self._roots).append(n)
        self._by_anlz: dict[str, str] = {}
        for t in tracks:
            key = normalize_anlz_key(t.analysis_path)
            if key:
                self._by_anlz[key] = t.id

    # --- LibrarySource -------------------------------------------------
    def tree(self) -> list[PlaylistNode]:
        return self._roots

    def node(self, node_id: str) -> PlaylistNode | None:
        return self._nodes.get(str(node_id))

    def track(self, track_id: str) -> Track | None:
        return self._tracks.get(str(track_id))

    def all_tracks(self) -> list[Track]:
        return list(self._tracks.values())

    def tracks_of(self, node_id: str) -> list[Track]:
        node = self.node(node_id)
        if node is None:
            raise KeyError(f"No existe la carpeta/playlist {node_id}")
        seen: set[str] = set()
        result: list[Track] = []
        for _, n in node.walk():
            if n.kind == NodeKind.FOLDER:
                continue
            for tid in self._playlist_tracks.get(n.id, []):
                if tid in seen:
                    continue
                t = self._tracks.get(tid)
                if t is not None:
                    seen.add(tid)
                    result.append(t)
        return result

    def track_by_analysis_path(self, path: str | None) -> Track | None:
        key = normalize_anlz_key(path)
        if not key:
            return None
        tid = self._by_anlz.get(key)
        return self._tracks.get(tid) if tid else None

    # --- Serialización (exportar/importar snapshot) --------------------
    def to_dict(self) -> dict:
        nodes = []
        for root in self._roots:
            for _, n in root.walk():
                nodes.append({
                    "id": n.id, "name": n.name, "kind": n.kind.value,
                    "parent_id": n.parent_id,
                    "track_ids": self._playlist_tracks.get(n.id, []),
                })
        return {"tracks": [t.to_dict() for t in self._tracks.values()], "playlists": nodes}

    def save_json(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
                              encoding="utf-8")

    @classmethod
    def from_dict(cls, data: dict) -> "InMemoryLibrary":
        tracks = [
            Track.build(id=t["id"], title=t.get("title", ""), artist=t.get("artist", ""),
                        path=t.get("path", ""), bpm=t.get("bpm"), key=t.get("key"),
                        duration_s=t.get("duration_s"), analysis_path=t.get("analysis_path"))
            for t in data.get("tracks", [])
        ]
        nodes, contents = [], {}
        for p in data.get("playlists", []):
            nodes.append(PlaylistNode(id=str(p["id"]), name=p["name"],
                                      kind=NodeKind(p.get("kind", "playlist")),
                                      parent_id=str(p["parent_id"]) if p.get("parent_id") else None))
            contents[str(p["id"])] = [str(x) for x in p.get("track_ids", [])]
        return cls(tracks, nodes, contents)

    @classmethod
    def load_json(cls, path: str | Path) -> "InMemoryLibrary":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
