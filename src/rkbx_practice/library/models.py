"""Modelos de la biblioteca, independientes de pyrekordbox."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Protocol

from .camelot import Camelot, parse_key


def normalize_bpm(raw: float | int | None) -> float | None:
    """master.db guarda el BPM multiplicado por 100 (12800 = 128.00).

    Se acepta también el valor ya normalizado. Un BPM de 0 o vacío significa
    "sin analizar".
    """
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    # Ningún tema real supera los 999 BPM; por encima es la escala x100.
    if value >= 1000:
        value /= 100.0
    return round(value, 2)


@dataclass(frozen=True)
class Track:
    id: str
    title: str
    artist: str = ""
    path: str = ""
    bpm: float | None = None
    key_raw: str | None = None
    duration_s: float | None = None
    analysis_path: str | None = None
    camelot: Camelot | None = field(default=None, compare=False)

    @classmethod
    def build(cls, *, id, title, artist="", path="", bpm=None, key=None,
              duration_s=None, analysis_path=None) -> "Track":
        return cls(
            id=str(id),
            title=title or "",
            artist=artist or "",
            path=path or "",
            bpm=normalize_bpm(bpm),
            key_raw=key,
            duration_s=float(duration_s) if duration_s else None,
            analysis_path=analysis_path or None,
            camelot=parse_key(key),
        )

    @property
    def display(self) -> str:
        return f"{self.artist} - {self.title}" if self.artist else self.title

    @property
    def camelot_str(self) -> str:
        return str(self.camelot) if self.camelot else "—"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "artist": self.artist,
            "path": self.path,
            "bpm": self.bpm,
            "key": self.key_raw,
            "camelot": str(self.camelot) if self.camelot else None,
            "duration_s": self.duration_s,
            "analysis_path": self.analysis_path,
        }


class NodeKind(str, Enum):
    FOLDER = "folder"
    PLAYLIST = "playlist"
    SMART = "smart"


@dataclass
class PlaylistNode:
    id: str
    name: str
    kind: NodeKind
    parent_id: str | None = None
    children: list["PlaylistNode"] = field(default_factory=list)

    def walk(self, depth: int = 0) -> Iterable[tuple[int, "PlaylistNode"]]:
        yield depth, self
        for child in self.children:
            yield from child.walk(depth + 1)


class LibrarySource(Protocol):
    """Lo que el resto de la app necesita de una biblioteca."""

    def tree(self) -> list[PlaylistNode]:
        """Nodos raíz (carpetas y playlists) con sus hijos."""

    def node(self, node_id: str) -> PlaylistNode | None: ...

    def tracks_of(self, node_id: str) -> list[Track]:
        """Temas de una playlist, o de todas las playlists de una carpeta (recursivo, sin duplicados)."""

    def track(self, track_id: str) -> Track | None: ...

    def all_tracks(self) -> list[Track]: ...


def find_node_by_name(roots: list[PlaylistNode], name: str) -> PlaylistNode | None:
    """Busca por nombre o por ruta `Carpeta/Playlist` (sin distinguir mayúsculas)."""
    wanted = [p.strip().lower() for p in name.replace("\\", "/").split("/") if p.strip()]
    if not wanted:
        return None

    def match_path(nodes: list[PlaylistNode], parts: list[str]) -> PlaylistNode | None:
        for n in nodes:
            if n.name.lower() == parts[0]:
                if len(parts) == 1:
                    return n
                found = match_path(n.children, parts[1:])
                if found:
                    return found
        return None

    if len(wanted) > 1:
        return match_path(roots, wanted)
    for root in roots:
        for _, n in root.walk():
            if n.name.lower() == wanted[0] or n.id == name:
                return n
    return None
