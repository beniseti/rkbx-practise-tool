from rkbx_practice.library import NodeKind, find_node_by_name, normalize_bpm
from rkbx_practice.library.anlz import BeatGrid
from rkbx_practice.library.anlz_path import normalize_anlz_key


def test_normalize_bpm():
    assert normalize_bpm(12800) == 128.0
    assert normalize_bpm(12850) == 128.5
    assert normalize_bpm(128) == 128.0
    assert normalize_bpm(174.5) == 174.5
    assert normalize_bpm(0) is None
    assert normalize_bpm(None) is None
    assert normalize_bpm("abc") is None


def test_tree_and_recursive_folder(library):
    roots = library.tree()
    assert [n.name for n in roots] == ["Techno", "Favoritos"]
    techno = find_node_by_name(roots, "Techno")
    assert techno.kind == NodeKind.FOLDER
    tracks = library.tracks_of(techno.id)
    ids = [t.id for t in tracks]
    assert len(ids) == len(set(ids))  # sin duplicados (Warmup y Peak se solapan)
    assert "2001" in ids and "2002" in ids
    assert len(ids) == 42


def test_find_by_path(library):
    node = find_node_by_name(library.tree(), "techno/peak")
    assert node is not None and node.name == "Peak"
    assert find_node_by_name(library.tree(), "No existe") is None


def test_track_normalization(library):
    t = library.track("1001")
    assert t.bpm is not None and t.bpm < 1000
    assert t.camelot is not None


def test_anlz_key_matching(library):
    mem_path = r"C:\Users\dj\AppData\Roaming\Pioneer\rekordbox\share\PIONEER\USBANLZ\001\00000001-aaaa-bbbb-cccc-000000000001\ANLZ0000.EXT"
    t = library.track_by_analysis_path(mem_path)
    assert t is not None and t.id == "1001"
    assert normalize_anlz_key("/PIONEER/USBANLZ/abc/def/ANLZ0000.DAT") == "pioneer/usbanlz/abc/def"
    assert library.track_by_analysis_path(None) is None
    assert library.track_by_analysis_path("C:/otra/cosa.DAT") is None


def test_json_roundtrip(library, tmp_path):
    p = tmp_path / "lib.json"
    library.save_json(p)
    from rkbx_practice.library import InMemoryLibrary
    lib2 = InMemoryLibrary.load_json(p)
    assert len(lib2.all_tracks()) == len(library.all_tracks())
    assert [n.name for n in lib2.tree()] == [n.name for n in library.tree()]


def test_beat_grid():
    g = BeatGrid.uniform(120.0, first_beat_s=0.1)
    assert g.is_on_beat(0.62, 0.03)        # beat 2 a 0.6 s
    assert not g.is_on_beat(0.85, 0.03)
    assert g.is_bar_start(2.1, 0.03)       # beat 5 = compás 2
    assert not g.is_bar_start(0.6, 0.03)
    assert g.is_phrase_start(0.1, 0.03)
    assert g.is_phrase_start(0.1 + 32 * 0.5, 0.03)
    assert not g.is_phrase_start(2.1, 0.03)
