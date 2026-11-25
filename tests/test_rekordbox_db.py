"""Adaptador de master.db con un doble de pyrekordbox (no hace falta Rekordbox)."""

from types import SimpleNamespace as NS

import pytest

from rkbx_practice.config import LibraryConfig
from rkbx_practice.library import NodeKind
from rkbx_practice.library import rekordbox_db
from rkbx_practice.library.rekordbox_db import LibraryError, open_rekordbox_library


class FakeSession:
    def __init__(self, log):
        self.log = log

    def rollback(self):
        self.log.append("rollback")

    def commit(self):  # pragma: no cover - no debe llamarse nunca
        raise AssertionError("¡commit en master.db!")


def make_fake_db(log):
    c1 = NS(ID=1, Title="Uno", ArtistName="A", FolderPath="C:/m/1.mp3", BPM=12800, KeyName="Am",
            Length=300, AnalysisDataPath="/PIONEER/USBANLZ/aaa/111/ANLZ0000.DAT")
    c2 = NS(ID=2, Title="Dos", ArtistName=None, FolderPath="C:/m/2.mp3", BPM=0, KeyName=None,
            Length=200, AnalysisDataPath=None)
    folder = NS(ID=10, Name="Carpeta", ParentID="root", Attribute=1, Seq=1, Songs=[])
    pl = NS(ID=11, Name="Lista", ParentID=10, Attribute=0, Seq=1,
            Songs=[NS(ContentID=2, TrackNo=2), NS(ContentID=1, TrackNo=1)])
    smart = NS(ID=12, Name="Inteligente", ParentID="root", Attribute=4, Seq=2, Songs=[])

    class FakeDB:
        def __init__(self, **kwargs):
            log.append(("open", kwargs))
            self.session = FakeSession(log)

        def get_content(self):
            return [c1, c2]

        def get_playlist(self):
            return [smart, pl, folder]

        def get_playlist_contents(self, playlist):
            if playlist.Attribute == 4:
                return [c1]
            raise RuntimeError("usar Songs")

        def close(self):
            log.append("close")

    return FakeDB


def test_reads_library_from_copy(tmp_path, monkeypatch):
    db_file = tmp_path / "master.db"
    db_file.write_bytes(b"original")
    log = []
    monkeypatch.setattr(rekordbox_db, "_import_pyrekordbox", lambda: make_fake_db(log))
    lib = open_rekordbox_library(LibraryConfig(master_db_path=str(db_file)))

    opened = log[0][1]
    assert opened["path"] != str(db_file)            # se abre una COPIA
    assert opened["db_dir"] == str(tmp_path)
    assert "rollback" in log and "close" in log
    assert db_file.read_bytes() == b"original"

    t1 = lib.track("1")
    assert t1.bpm == 128.0 and str(t1.camelot) == "8A" and t1.artist == "A"
    assert lib.track("2").bpm is None
    roots = lib.tree()
    assert [r.name for r in roots] == ["Carpeta", "Inteligente"]
    assert roots[0].kind == NodeKind.FOLDER and roots[1].kind == NodeKind.SMART
    assert [t.id for t in lib.tracks_of("11")] == ["1", "2"]     # orden por TrackNo
    assert [t.id for t in lib.tracks_of("10")] == ["1", "2"]     # carpeta recursiva
    assert [t.id for t in lib.tracks_of("12")] == ["1"]          # lista inteligente
    assert lib.share_dir == tmp_path / "share"


def test_missing_db(tmp_path, monkeypatch):
    monkeypatch.setattr(rekordbox_db, "_import_pyrekordbox", lambda: make_fake_db([]))
    with pytest.raises(LibraryError):
        open_rekordbox_library(LibraryConfig(master_db_path=str(tmp_path / "no.db")))
