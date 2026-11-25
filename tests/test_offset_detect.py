"""El asistente de offsets contra un "Rekordbox" simulado en memoria falsa."""

import json
import random
import re
import struct

import numpy as np

from rkbx_practice.memory import DeckReader, FakeMemory, OffsetTable
from tools.offset_detect.memscan import FakeProcessMemory, approx, find_strings, first_scan, refine
from tools.offset_detect.pointerscan import PointerMap, find_chains, resolve
from tools.offset_detect.wizard import parse_time_or_float, run

BASE = 0x140000000
MOD_SIZE = 0x10000
HEAP = 0x30000000
STR = 0x38000000


class FakeRekordbox:
    def __init__(self, seed=0):
        self.rng = random.Random(seed)
        m = FakeMemory(module_base=BASE)
        m.map(BASE, MOD_SIZE)
        m.map(HEAP, 0x20000)
        m.map(STR, 0x20000)
        self.m = m
        self.str_next = STR
        # módulo+0x5000 -> gestor (HEAP); gestor+0x10*n -> deck n (HEAP+0x1000*n)
        m.write_ptr(BASE + 0x5000, HEAP)
        for n in (1, 2):
            m.write_ptr(HEAP + 0x10 * n, HEAP + 0x1000 * n)
        # Ruido: floats aleatorios y rutas ANLZ del navegador que no cambian.
        for i in range(400):
            m.write_f32(HEAP + 0x8000 + i * 4, self.rng.uniform(0, 200))
        for i in range(5):
            addr = self._new_string(f"C:/share/PIONEER/USBANLZ/zz{i}/ANLZ0000.DAT")
            m.write_ptr(HEAP + 0x9000 + i * 8, addr)
        self.state = {n: {"bpm": 120.0 + n, "pos": 0.0, "fader": 0.0, "eq": 0.5, "track": 0} for n in (1, 2)}
        self.master = 1
        self.loads = 0
        for n in (1, 2):
            self.load(n)
        self.flush()

    def _new_string(self, text):
        addr = self.str_next
        data = text.encode() + b"\x00"
        self.m.write(addr, data)
        self.str_next += (len(data) + 15) // 16 * 16
        return addr

    def load(self, n):
        self.loads += 1
        path = f"C:/share/PIONEER/USBANLZ/{self.loads:03x}/track{self.loads}/ANLZ0000.DAT"
        self.m.write_ptr(HEAP + 0x1000 * n + 0x40, self._new_string(path))

    def flush(self):
        for n, s in self.state.items():
            d = HEAP + 0x1000 * n
            self.m.write_f32(d + 0x20, s["bpm"])
            self.m.write_f64(d + 0x28, s["pos"])
            self.m.write_f32(d + 0x30, s["fader"])
            self.m.write_f32(d + 0x34, s["eq"])
            self.m.write(d + 0x38, bytes([1 if self.master == n else 0]))
        self.m.write(HEAP + 0x60, bytes([self.master - 1]))
        for i in range(400):   # el ruido cambia en cada acción
            if self.rng.random() < 0.3:
                self.m.write_f32(HEAP + 0x8000 + i * 4, self.rng.uniform(0, 200))


class ScriptedConsole:
    def __init__(self, rb: FakeRekordbox):
        self.rb = rb
        self.log = []

    def say(self, text):
        self.log.append(text)

    def _deck(self, text):
        m = re.search(r"deck (\d)", text)
        return int(m.group(1)) if m else 1

    def action(self, text):
        rb, t = self.rb, text.lower()
        n = self._deck(t)
        s = rb.state[n]
        if "carga otro tema" in t or "carga un tema" in t:
            rb.load(n)
        elif "fader" in t:
            s["fader"] = 0.0 if "baja" in t else 1.0 if "sube" in t else 0.47
        elif "eq de graves" in t:
            s["eq"] = 0.0 if "mínimo" in t else 1.0 if "máximo" in t else 0.5
        elif "master" in t:
            rb.master = n
        rb.flush()

    def ask_float(self, text):
        rb, t = self.rb, text.lower()
        n = self._deck(t)
        s = rb.state[n]
        if "bpm" in t:
            s["bpm"] = round(rb.rng.uniform(118, 135), 2)
            rb.flush()
            return s["bpm"]
        if "tiempo" in t:
            s["pos"] += rb.rng.uniform(3, 9)
            rb.flush()
            return round(s["pos"], 1)
        raise AssertionError(text)


def test_parse_time():
    assert parse_time_or_float("1:05.3") == 65.3
    assert parse_time_or_float("128.5") == 128.5
    assert parse_time_or_float("0:07") == 7


def test_scan_and_refine():
    rb = FakeRekordbox()
    mem = FakeProcessMemory(rb.m, MOD_SIZE)
    c = first_scan(mem, "f32", approx(121.0, 0.01), save_as="a")
    assert HEAP + 0x1020 in c.addresses.tolist()
    rb.state[1]["bpm"] = 127.5
    rb.flush()
    c = refine(mem, c, lambda v, s: np.abs(v - 127.5) < 0.01)
    assert c.addresses.tolist() == [HEAP + 0x1020]


def test_pointer_chain_search():
    rb = FakeRekordbox()
    mem = FakeProcessMemory(rb.m, MOD_SIZE)
    pmap = PointerMap.build(mem)
    chains = find_chains(mem, pmap, HEAP + 0x2030)   # fader deck 2
    assert [0x5000, 0x20, 0x30] in chains
    assert resolve(mem, chains[0]) == HEAP + 0x2030


def test_find_strings():
    rb = FakeRekordbox()
    found = find_strings(FakeProcessMemory(rb.m, MOD_SIZE), "USBANLZ")
    assert len(found) == 7    # 5 de ruido + 2 decks


def test_wizard_end_to_end(tmp_path):
    rb = FakeRekordbox(seed=3)
    mem = FakeProcessMemory(rb.m, MOD_SIZE)
    console = ScriptedConsole(rb)
    out = run(mem, "7.2.20", [1, 2], ["track_path", "bpm", "position", "fader", "eq_low", "is_master"],
              console, tmp_path)
    data = json.loads(out.read_text())
    assert data["version"] == "7.2.20"
    d1 = data["decks"]["1"]
    assert d1["bpm"]["chain"] == ["0x5000", "0x10", "0x20"]
    assert d1["fader"]["chain"] == ["0x5000", "0x10", "0x30"]
    assert d1["track_path"]["type"] == "ptr_utf8"
    assert data["decks"]["2"]["eq_low"]["chain"] == ["0x5000", "0x20", "0x34"]
    assert data["decks"]["2"]["is_master"]["chain"] == ["0x5000", "0x20", "0x38"]
    assert data["global"]["master_deck"]["chain"] == ["0x5000", "0x60"]

    # El archivo generado funciona con el lector de la app.
    table = OffsetTable.from_dict(data)
    rb.state[2].update(fader=0.8, eq=0.25, bpm=126.0)
    rb.master = 2
    rb.flush()
    res = DeckReader(rb.m, table, decks=2).read()
    d2 = res.decks[1]
    assert abs(d2.fader - 0.8) < 1e-6 and abs(d2.eq_low - 0.25) < 1e-6 and abs(d2.bpm - 126.0) < 1e-4
    assert d2.is_master is True and res.decks[0].is_master is False
    assert "USBANLZ" in d2.track_path
