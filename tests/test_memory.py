import json

import pytest

from rkbx_practice.memory import (DeckReader, FakeMemory, OffsetTable, OffsetsError, find_offsets,
                                  load_offsets_dir, normalize_version, read_field, resolve_chain)
from rkbx_practice.memory.offsets import FieldSpec

BASE = 0x140000000
HEAP = 0x20000000


def test_normalize_version():
    assert normalize_version("7.2.20.0") == "7.2.20"
    assert normalize_version("7.2.20") == "7.2.20"
    assert normalize_version("7.0.0.0") == "7.0.0"
    assert normalize_version("v7.1.3") == "7.1.3"
    assert normalize_version(None) is None


def build_memory():
    mem = FakeMemory(module_base=BASE)
    mem.map(BASE, 0x2000)          # módulo
    mem.map(HEAP, 0x4000)          # montón
    # base+0x100 -> HEAP ; HEAP+0x10 -> HEAP+0x1000 ; valores en HEAP+0x1000+...
    mem.write_ptr(BASE + 0x100, HEAP)
    mem.write_ptr(HEAP + 0x10, HEAP + 0x1000)
    mem.write_f32(HEAP + 0x1000 + 0x20, 128.5)       # bpm deck 1
    mem.write_f64(HEAP + 0x1000 + 0x28, 42.25)       # posición
    mem.write_f32(HEAP + 0x1000 + 0x30, 100.0)       # fader en escala 0..127
    mem.write(HEAP + 0x1000 + 0x34, b"\x01")         # master
    # puntero a cadena
    path = "C:/x/PIONEER/USBANLZ/001/00000001-aaaa-bbbb-cccc-000000000001/ANLZ0000.DAT"
    mem.write(HEAP + 0x2000, path.encode() + b"\x00")
    mem.write_ptr(HEAP + 0x1000 + 0x40, HEAP + 0x2000)
    # deck 2: puntero nulo (deck vacío)
    mem.write_ptr(BASE + 0x108, 0)
    return mem, path


def table_dict():
    chain = lambda off: ["0x100", "0x10", hex(off)]
    return {
        "version": "7.2.20",
        "decks": {
            "1": {
                "bpm": {"chain": chain(0x20), "type": "f32"},
                "position": {"chain": chain(0x28), "type": "f64"},
                "fader": {"chain": chain(0x30), "type": "f32", "map": [0, 127]},
                "is_master": {"chain": chain(0x34), "type": "bool"},
                "track_path": {"chain": chain(0x40), "type": "ptr_utf8"},
            },
            "2": {"bpm": {"chain": ["0x108", "0x10", "0x20"], "type": "f32"}},
        },
        "global": {"crossfader": {"chain": ["0x5000", "0x0"], "type": "f32"}},
    }


def test_resolve_and_read():
    mem, path = build_memory()
    table = OffsetTable.from_dict(table_dict())
    f = table.deck_fields(1)
    assert resolve_chain(mem, f["bpm"].chain) == HEAP + 0x1020
    assert read_field(mem, f["bpm"]) == pytest.approx(128.5)
    assert read_field(mem, f["position"]) == pytest.approx(42.25)
    assert read_field(mem, f["fader"]) == pytest.approx(100 / 127)
    assert read_field(mem, f["is_master"]) is True
    assert read_field(mem, f["track_path"]) == path
    assert read_field(mem, table.deck_fields(2)["bpm"]) is None  # puntero nulo


def test_deck_reader_handles_errors():
    mem, path = build_memory()
    table = OffsetTable.from_dict(table_dict())
    res = DeckReader(mem, table, decks=2, crossfader_enabled=True).read()
    d1, d2 = res.decks
    assert d1.bpm == pytest.approx(128.5) and d1.track_path == path and d1.is_master
    assert d2.bpm is None
    # el crossfader apunta a memoria sin mapear: error registrado, no excepción
    assert res.mixer.crossfader is None
    assert any("crossfader" in e for e in res.errors)


def test_master_deck_global():
    mem, _ = build_memory()
    mem.write(BASE + 0x300, b"\x01")   # índice 0-based -> deck 2
    d = table_dict()
    del d["decks"]["1"]["is_master"]
    d["global"] = {"master_deck": {"chain": ["0x300"], "type": "u8", "index_base": 0}}
    res = DeckReader(mem, OffsetTable.from_dict(d), decks=2).read()
    assert res.decks[0].is_master is False and res.decks[1].is_master is True


def test_offsets_dir(tmp_path):
    (tmp_path / "7.2.20.json").write_text(json.dumps(table_dict()))
    (tmp_path / "_example.json").write_text(json.dumps({**table_dict(), "version": "1.0.0"}))
    (tmp_path / "broken.json").write_text("{nope")
    tables = load_offsets_dir(tmp_path)
    assert set(tables) == {"7.2.20"}
    assert find_offsets(tmp_path, "7.2.20.0") is not None
    assert find_offsets(tmp_path, "7.2.19") is None   # nunca otra versión
    assert find_offsets(tmp_path, None) is None


def test_invalid_specs():
    with pytest.raises(OffsetsError):
        FieldSpec.from_dict("x", {"type": "f32"})
    with pytest.raises(OffsetsError):
        FieldSpec.from_dict("x", {"chain": ["0x1"], "type": "nope"})
    with pytest.raises(OffsetsError):
        OffsetTable.from_dict({"decks": {}})


def test_missing_fields_report():
    t = OffsetTable.from_dict(table_dict())
    missing = t.missing_fields(2)
    assert "deck 1: eq_low" in missing
    assert any(m.startswith("deck 2") for m in missing)


def test_repo_offsets_dir_loads():
    from rkbx_practice.config import load_config
    cfg = load_config()
    load_offsets_dir(cfg.resolve_path(cfg.rekordbox.offsets_dir))  # no debe fallar
