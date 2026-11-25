"""Lectura de `master.db` de Rekordbox con pyrekordbox, en SOLO LECTURA.

Reglas (CLAUDE.md §3):
- Nunca se escribe en master.db ni en ningún archivo de Rekordbox.
- Por defecto se abre una COPIA temporal de master.db (y de sus archivos
  -wal/-shm si existen). Así ni siquiera un checkpoint de SQLite al cerrar
  puede tocar el archivo original mientras Rekordbox está abierto.
- Nunca se llama a commit/flush ni se modifica ningún objeto ORM.
- La biblioteca se lee una vez y se guarda en memoria (InMemoryLibrary).
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path

from ..config import LibraryConfig
from .memory_library import InMemoryLibrary
from .models import NodeKind, PlaylistNode, Track

log = logging.getLogger(__name__)

# DjmdPlaylist.Attribute
_ATTR_PLAYLIST = 0
_ATTR_FOLDER = 1
_ATTR_SMART = 4


class LibraryError(RuntimeError):
    pass


def _import_pyrekordbox():
    try:
        import pyrekordbox  # noqa: F401
        from pyrekordbox import Rekordbox6Database
    except ImportError as exc:  # pragma: no cover - depende del entorno
        raise LibraryError(
            "Falta pyrekordbox. Instálalo con: pip install pyrekordbox"
        ) from exc
    return Rekordbox6Database


def _default_db_dir() -> Path | None:
    """Carpeta de master.db según la configuración de pyrekordbox."""
    try:
        from pyrekordbox.config import get_config
        for app in ("rekordbox7", "rekordbox6"):
            try:
                conf = get_config(app)
            except Exception:  # noqa: BLE001
                continue
            if conf and conf.get("db_dir"):
                return Path(conf["db_dir"])
    except Exception:  # noqa: BLE001  pragma: no cover
        pass
    return None


def _copy_database(db_path: Path) -> Path:
    tmp_dir = Path(tempfile.mkdtemp(prefix="rkbx_practice_db_"))
    for suffix in ("", "-wal", "-shm"):
        src = db_path.with_name(db_path.name + suffix)
        if src.exists():
            shutil.copy2(src, tmp_dir / src.name)
    return tmp_dir / db_path.name


def _safe(obj, attr, default=None):
    try:
        value = getattr(obj, attr)
    except Exception:  # noqa: BLE001  (relaciones rotas, campos ausentes...)
        return default
    return default if value is None else value


def _content_to_track(content) -> Track:
    return Track.build(
        id=_safe(content, "ID"),
        title=_safe(content, "Title", ""),
        artist=_safe(content, "ArtistName", ""),
        path=_safe(content, "FolderPath", ""),
        bpm=_safe(content, "BPM"),
        key=_safe(content, "KeyName"),
        duration_s=_safe(content, "Length"),
        analysis_path=_safe(content, "AnalysisDataPath"),
    )


def _playlist_content_ids(db, playlist) -> list[str]:
    """IDs de los temas de una playlist en su orden (incluidas listas inteligentes)."""
    getter = getattr(db, "get_playlist_contents", None)
    if getter is not None:
        try:
            return [str(c.ID) for c in getter(playlist)]
        except Exception as exc:  # noqa: BLE001
            log.debug("get_playlist_contents falló para %s: %s", playlist.ID, exc)
    songs = list(_safe(playlist, "Songs", []))
    songs.sort(key=lambda s: _safe(s, "TrackNo", 0))
    return [str(s.ContentID) for s in songs if _safe(s, "ContentID") is not None]


def open_rekordbox_library(cfg: LibraryConfig) -> InMemoryLibrary:
    """Lee master.db (solo lectura) y devuelve un snapshot en memoria."""
    Rekordbox6Database = _import_pyrekordbox()

    original = Path(cfg.master_db_path) if cfg.master_db_path else None
    if original is None:
        db_dir = _default_db_dir()
        if db_dir is not None:
            original = db_dir / "master.db"
    if original is not None and not original.exists():
        raise LibraryError(f"No se encuentra master.db en {original}")

    kwargs = {}
    if cfg.db_key:
        kwargs["key"] = cfg.db_key
    open_path = original
    tmp_copy: Path | None = None
    if original is not None and cfg.copy_before_open:
        tmp_copy = _copy_database(original)
        open_path = tmp_copy
        log.info("Abriendo copia temporal de master.db: %s", tmp_copy)
    if open_path is not None:
        kwargs["path"] = str(open_path)
        kwargs["db_dir"] = str(original.parent)

    try:
        db = Rekordbox6Database(**kwargs)
    except Exception as exc:  # noqa: BLE001
        raise LibraryError(f"No se pudo abrir master.db: {exc}") from exc

    try:
        tracks = [_content_to_track(c) for c in db.get_content()]
        nodes: list[PlaylistNode] = []
        contents: dict[str, list[str]] = {}
        for pl in sorted(db.get_playlist(), key=lambda p: (_safe(p, "Seq", 0), _safe(p, "Name", ""))):
            attr = _safe(pl, "Attribute", _ATTR_PLAYLIST)
            kind = {_ATTR_FOLDER: NodeKind.FOLDER, _ATTR_SMART: NodeKind.SMART}.get(attr, NodeKind.PLAYLIST)
            parent = _safe(pl, "ParentID")
            parent = None if parent in (None, "", "root", 0, "0") else str(parent)
            nodes.append(PlaylistNode(id=str(pl.ID), name=_safe(pl, "Name", ""), kind=kind,
                                      parent_id=parent))
            if kind != NodeKind.FOLDER:
                contents[str(pl.ID)] = _playlist_content_ids(db, pl)
    finally:
        try:
            # Solo cerramos la sesión; nunca commit.
            db.session.rollback()
            db.close()
        except Exception:  # noqa: BLE001
            pass
        if tmp_copy is not None:
            shutil.rmtree(tmp_copy.parent, ignore_errors=True)

    lib = InMemoryLibrary(tracks, nodes, contents)
    lib.share_dir = original.parent / "share" if original is not None else None
    return lib
