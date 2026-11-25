"""Asistente interactivo para generar `data/offsets/<versión>.json`.

Uso (Windows, con Rekordbox abierto):
    python -m tools.offset_detect                 # todo, deck a deck
    python -m tools.offset_detect --fields bpm fader --decks 1
    python -m tools.offset_detect --validate data/offsets/7.2.20.json
    python -m tools.offset_detect --recheck      # tras reiniciar Rekordbox: deja solo cadenas estables

El asistente pide UNA acción cada vez y espera a que pulses Enter. Solo LEE la
memoria de Rekordbox: nunca escribe en ella.

Pasos por deck:
  track_path  cargar temas distintos → punteros a la ruta ANLZ que cambian
  bpm         escribir el BPM que muestra el deck tras mover el pitch
  position    escribir el tiempo transcurrido mostrado tras reproducir/pausar
  fader       fader arriba / abajo / a medias
  eq_low      EQ de graves al mínimo / máximo / centro
  is_master   cambiar el deck MASTER
Globales: crossfader (izquierda / derecha / centro).
Al final se buscan cadenas de punteros estáticas para cada dirección y se
validan con una acción más.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .memscan import Candidates, Memory, approx, find_strings, finite, first_scan, read_values, refine
from .pointerscan import PointerMap, find_chains, resolve, to_hex

ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Entrada/salida con el usuario
# ---------------------------------------------------------------------------
class Console:
    def say(self, text: str) -> None:
        print(text)

    def action(self, text: str) -> None:
        input(f"\n>>> {text}\n    Pulsa Enter cuando esté hecho… ")

    def ask(self, text: str) -> str:
        return input(f"\n>>> {text}\n    ").strip()

    def ask_float(self, text: str) -> float:
        while True:
            raw = self.ask(text).replace(",", ".")
            try:
                return parse_time_or_float(raw)
            except ValueError:
                print("    Valor no válido, prueba otra vez (ej.: 128.00 o 1:05.3)")


def parse_time_or_float(raw: str) -> float:
    """`128.5` → 128.5 ; `1:05.3` → 65.3 ; `0:07` → 7."""
    raw = raw.strip()
    if re.fullmatch(r"\d+:\d{1,2}(\.\d+)?", raw):
        m, s = raw.split(":")
        return int(m) * 60 + float(s)
    return float(raw)


@dataclass
class Found:
    field: str
    deck: int | None
    address: int
    dtype: str
    extra: dict


def _summary(c: Candidates) -> str:
    return f"{len(c)} candidatos ({c.dtype})"


def _narrow(console: Console, mem: Memory, cands: list[Candidates], steps, max_rounds: int = 4) -> list[Candidates]:
    """Repite una secuencia de (acción, filtro) hasta dejar pocos candidatos."""
    for _ in range(max_rounds):
        for text, keep, save in steps:
            console.action(text)
            cands = [refine(mem, c, keep, save_as=save) for c in cands]
            console.say("    " + ", ".join(_summary(c) for c in cands))
        if sum(len(c) for c in cands) <= 5:
            break
    return cands


def _pick(console: Console, mem: Memory, cands: list[Candidates], label: str) -> tuple[int, str] | None:
    flat = [(int(a), c.dtype) for c in cands for a in c.addresses.tolist()]
    if not flat:
        console.say(f"    ✗ No se encontró {label}. Puedes repetir este paso más tarde.")
        return None
    if len(flat) > 1:
        console.say(f"    Quedan {len(flat)} candidatos para {label}; se usa el primero. Si al validar "
                    "no funciona, repite este campo con --fields.")
    return flat[0]


# ---------------------------------------------------------------------------
# Pasos por campo
# ---------------------------------------------------------------------------
def _range_picks(console: Console, cands: list[Candidates], label: str, limit: int = 10):
    """Todos los candidatos (hasta `limit`) de un control continuo, con su propio `map`.

    Se guardan las cadenas de todos: tras reiniciar, --recheck se queda con las que funcionan.
    """
    out = []
    for c in cands:
        for i, a in enumerate(c.addresses.tolist()):
            lo, hi = float(c.saved["lo"][i]), float(c.saved["hi"][i])
            mid = float(c.saved["mid"][i]) if "mid" in c.saved else None
            rel = (mid - lo) / (hi - lo) if mid is not None and hi != lo else None
            console.say(f"    candidato {label} 0x{int(a):X} ({c.dtype}): mín {lo:.4g} · máx {hi:.4g}"
                        + (f" · a la mitad {mid:.4g} ({rel:.0%} del rango)" if rel is not None else ""))
            out.append((int(a), c.dtype, {"map": [lo, hi]}))
    if not out:
        console.say(f"    ✗ No se encontró {label}.")
    elif len(out) > limit:
        console.say(f"    (hay {len(out)} candidatos; se usan los {limit} primeros)")
    return out[:limit]


def describe_object(console: Console, mem: Memory, pmap: PointerMap, address: int) -> None:
    """Diagnóstico: posibles vtables (punteros al módulo) justo antes de la dirección."""
    import struct
    mod_lo, mod_hi = mem.module_base, mem.module_base + mem.module_size
    start = max(0, address - 0x800) & ~7
    data = mem.read(start, address - start + 8)
    if data is None:
        return
    shown = 0
    for off in range(len(data) - 8, -1, -8):
        v = struct.unpack_from("<Q", data, off)[0]
        if mod_lo <= v < mod_hi:
            count = len(pmap.slots_pointing_to(v))
            console.say(f"      posible vtable a -0x{address - (start + off):X}: módulo+0x{v - mod_lo:X} "
                        f"({count} objetos en memoria la usan)")
            shown += 1
            if shown >= 3:
                break


def scan_bpm(console: Console, mem: Memory, deck: int) -> list[Candidates]:
    bpm = console.ask_float(f"Deck {deck}: carga un tema, deja el pitch/tempo en 0 % (tempo original; pulsa en el % para resetearlo) "
                           "y escribe el BPM que muestra el deck (p. ej. 128.00)")
    cands = [first_scan(mem, dt, approx(bpm, 0.06), save_as="a") for dt in ("f32", "f64")]
    console.say("    " + ", ".join(_summary(c) for c in cands))
    for i in range(3):
        bpm = console.ask_float(f"Deck {deck}: mueve el slider de pitch/tempo (p. ej. a +3 %) y escribe el nuevo BPM que muestra ({i + 1}/3)")
        cands = [refine(mem, c, lambda v, s, b=bpm: np.abs(v.astype(np.float64) - b) <= 0.06) for c in cands]
        console.say("    " + ", ".join(_summary(c) for c in cands))
        if sum(len(c) for c in cands) <= 3:
            break
    return cands


POSITION_UNITS = {"s": 1.0, "ms": 1000.0, "samples44k": 44100.0, "samples48k": 48000.0}


def scan_position(console: Console, mem: Memory, deck: int) -> tuple[list[Candidates], dict]:
    t1 = console.ask_float(f"Deck {deck}: reproduce unos segundos, PAUSA y escribe el tiempo "
                           "transcurrido que muestra el deck (ej. 0:07.4)")
    all_cands, units = [], []
    for unit, k in POSITION_UNITS.items():
        for dt in ("f32", "f64"):
            c = first_scan(mem, dt, approx(t1 * k, 0.15 * k), save_as="a")
            all_cands.append(c)
            units.append(unit)
    for i in range(3):
        t = console.ask_float(f"Deck {deck}: reproduce otro poco, PAUSA y escribe el tiempo transcurrido ({i + 1}/3)")
        all_cands = [refine(mem, c, lambda v, s, tt=t * POSITION_UNITS[u]: np.abs(v.astype(np.float64) - tt)
                            <= 0.15 * POSITION_UNITS[u]) for c, u in zip(all_cands, units)]
        console.say("    " + ", ".join(f"{u}:{len(c)}" for c, u in zip(all_cands, units) if len(c)))
        if sum(len(c) for c in all_cands) <= 3:
            break
    best = [(c, u) for c, u in zip(all_cands, units) if len(c)]
    unit = best[0][1] if best else "s"
    return [c for c, _ in best], {"scale": 1.0 / POSITION_UNITS[unit]}


def scan_range_control(console: Console, mem: Memory, what: str, low_text: str, high_text: str,
                       mid_text: str) -> tuple[list[Candidates], dict]:
    """Controles continuos (fader, EQ, crossfader) con escala desconocida."""
    console.action(high_text)
    cands = [first_scan(mem, dt, lambda a: finite(a) & (a > 0.05) & (a < 20000), save_as="hi")
             for dt in ("f32", "f64")]
    console.say("    " + ", ".join(_summary(c) for c in cands))
    steps = [
        (low_text, lambda v, s: v.astype(np.float64) < s["hi"].astype(np.float64) * 0.5 - 1e-6, "lo"),
        (high_text, lambda v, s: np.abs(v.astype(np.float64) - s["hi"]) <= np.abs(s["hi"]) * 0.01 + 1e-6, None),
        (low_text, lambda v, s: np.abs(v.astype(np.float64) - s["lo"]) <= np.abs(s["hi"]) * 0.01 + 1e-6, None),
        # Solo "estrictamente entre mínimo y máximo": con curva logarítmica la ganancia real a
        # media carrera puede estar muy cerca del mínimo (en 7.2.3 el filtro 15–85 % la descartaba).
        (mid_text, lambda v, s: (v > s["lo"] + (s["hi"] - s["lo"]) * 0.002)
         & (v < s["hi"] - (s["hi"] - s["lo"]) * 0.002), "mid"),
    ]
    cands = _narrow(console, mem, cands, steps, max_rounds=3)
    extra = {}
    for c in cands:
        if len(c):
            extra = {"map": [float(c.saved["lo"][0]), float(c.saved["hi"][0])]}
            break
    return cands, extra


def scan_master(console: Console, mem: Memory, decks: int) -> tuple[dict[int, list[Candidates]], list[Candidates]]:
    """Flags por deck (u8 0/1) e índice global del deck master."""
    console.action("Pon el deck 1 como MASTER (botón MASTER del deck 1)")
    per_deck = {d: [first_scan(mem, "u8", lambda a: a <= 1, save_as="m1")] for d in range(1, decks + 1)}
    idx = [first_scan(mem, dt, lambda a: a <= 3, save_as="m1") for dt in ("u8", "i32")]
    for _ in range(3):
        for target in range(1, decks + 1):
            console.action(f"Pon el deck {target} como MASTER")
            for d in per_deck:
                want = 1 if d == target else 0
                per_deck[d] = [refine(mem, c, lambda v, s, w=want: v == w) for c in per_deck[d]]
            idx = [refine(mem, c, lambda v, s, t=target: v == t - 1, save_as=None) for c in idx]
        console.say("    flags: " + ", ".join(f"deck {d}: {len(c[0])}" for d, c in per_deck.items())
                    + f" · índice global: {sum(len(c) for c in idx)}")
        if all(len(c[0]) <= 3 for c in per_deck.values()):
            break
    return per_deck, idx


def scan_track_path(console: Console, mem: Memory, deck: int, decks: int) -> list[int]:
    """Posiciones (slots) que contienen un puntero a la ruta ANLZ del tema del deck."""
    console.action(f"Carga un tema en el deck {deck}")
    pmap = PointerMap.build(mem)
    strings = find_strings(mem, "USBANLZ")
    slots = {}
    for addr, (text, utf16) in strings.items():
        for slot in pmap.slots_pointing_to(addr):
            slots[slot] = (text, utf16)
    console.say(f"    {len(strings)} rutas ANLZ en memoria, {len(slots)} punteros a ellas")
    for i in range(3):
        console.action(f"Carga OTRO tema distinto en el deck {deck} ({i + 1}/3)")
        new = _slot_strings(mem, list(slots))
        slots = {s: new[s] for s in slots if s in new and new[s][0] != slots[s][0]}
        console.say(f"    {len(slots)} punteros cambiaron")
        if decks > 1:
            other = 1 if deck != 1 else 2
            console.action(f"Carga un tema en el deck {other} (el deck {deck} no se toca)")
            new = _slot_strings(mem, list(slots))
            slots = {s: new[s] for s in slots if s in new and new[s][0] == slots[s][0]}
            console.say(f"    {len(slots)} punteros sin cambios")
        if len(slots) <= 5:
            break
    return [(s, utf16) for s, (_t, utf16) in slots.items()]


def _slot_strings(mem: Memory, slots: list[int]) -> dict[int, tuple[str, bool]]:
    import struct
    out = {}
    for s in slots:
        raw = mem.read(s, 8)
        if raw is None:
            continue
        ptr = struct.unpack("<Q", raw)[0]
        for utf16 in (False, True):
            data = mem.read(ptr, 1024)
            if data is None:
                break
            text = data.decode("utf-16-le" if utf16 else "utf-8", errors="ignore").split("\x00")[0]
            if "USBANLZ" in text.upper():
                out[s] = (text, utf16)
                break
    return out


# ---------------------------------------------------------------------------
# Cadenas y archivo final
# ---------------------------------------------------------------------------
def load_anchors(console: Console, mem: Memory, offsets_file: Path, fields: list[str],
                 decks: list[int]) -> list[tuple[int, list[int]]]:
    """Objetos intermedios de las cadenas (estables) de `fields` para los decks indicados."""
    from .pointerscan import anchor_objects
    if not offsets_file.exists():
        console.say(f"No existe {offsets_file}")
        return []
    data = json.loads(offsets_file.read_text(encoding="utf-8"))
    anchors: list[tuple[int, list[int]]] = []
    for deck in decks:
        for field in fields:
            spec = data.get("decks", {}).get(str(deck), {}).get(field) or data.get("global", {}).get(field)
            if not spec:
                console.say(f"    (sin cadena de {field} para el deck {deck}; no se usa como ancla)")
                continue
            objs = anchor_objects(mem, [int(x, 16) for x in spec["chain"]])
            console.say(f"    ancla {field} deck {deck}: {len(objs)} objetos")
            anchors += objs
    if not anchors:
        console.say("No se pudo resolver ninguna ancla (¿hay un tema cargado en el deck?)")
    return anchors


def chain_for(console: Console, mem: Memory, pmap: PointerMap, address: int, label: str) -> list[list[int]]:
    """Todas las cadenas estáticas hacia `address` (la primera es la más corta)."""
    anchors = SEARCH.get("anchors") or []
    chains = find_chains(mem, pmap, address, max_depth=SEARCH["max_depth"], max_offset=SEARCH["max_offset"],
                         max_results=SEARCH["max_results"], max_nodes=SEARCH["max_nodes"],
                         anchors=anchors, anchors_only=bool(anchors))
    if not chains:
        console.say(f"    ✗ {label}: sin cadena de punteros estática (prueba con más profundidad)")
        return []
    console.say(f"    ✓ {label}: {len(chains)} cadenas; mejor: {to_hex(chains[0])}")
    return chains


def build_offsets(version: str, found: list[Found], chains: dict[tuple, list[int]]) -> dict:
    out: dict = {"version": version, "module": "rekordbox.exe", "pointer_size": 8,
                 "decks": {}, "global": {}}
    done = set()
    for f in found:
        chain = chains.get((f.field, f.deck))
        if chain is None or (f.field, f.deck) in done:
            continue
        done.add((f.field, f.deck))
        spec = {"chain": to_hex(chain), "type": f.dtype, **f.extra}
        if f.deck is None:
            out["global"][f.field] = spec
        else:
            out["decks"].setdefault(str(f.deck), {})[f.field] = spec
    return out


def merge_into_file(path: Path, data: dict) -> None:
    if path.exists():
        old = json.loads(path.read_text(encoding="utf-8"))
        for d, fields in data["decks"].items():
            old.setdefault("decks", {}).setdefault(d, {}).update(fields)
        old.setdefault("global", {}).update(data["global"])
        data = old
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


class _Access:
    """Adaptador Memory -> MemoryAccess de la app (solo lectura)."""

    def __init__(self, mem: Memory):
        self.mem = mem
        self.module_base = mem.module_base
        self.pointer_size = mem.pointer_size

    def read_bytes(self, a: int, n: int) -> bytes:
        from rkbx_practice.memory.reader import MemoryReadError
        data = self.mem.read(a, n)
        if data is None:
            raise MemoryReadError(hex(a))
        return data


def validate_file(console: Console, mem: Memory, path: Path) -> None:
    sys.path.insert(0, str(ROOT / "src"))
    from rkbx_practice.memory.offsets import OffsetTable
    from rkbx_practice.memory.reader import read_field
    table = OffsetTable.from_dict(json.loads(path.read_text(encoding="utf-8")))
    acc = _Access(mem)
    for deck in table.supported_decks():
        for name, spec in table.deck_fields(deck).items():
            try:
                console.say(f"  deck {deck} {name:<10} = {read_field(acc, spec)!r}")
            except Exception as exc:  # noqa: BLE001
                console.say(f"  deck {deck} {name:<10} ✗ {exc}")
    for name, spec in table.globals.items():
        try:
            console.say(f"  global {name:<10} = {read_field(acc, spec)!r}")
        except Exception as exc:  # noqa: BLE001
            console.say(f"  global {name:<10} ✗ {exc}")


# Parámetros de la búsqueda de cadenas (ajustables con --depth / --max-offset).
SEARCH = {"max_depth": 6, "max_offset": 0x1000, "max_results": 5000, "max_nodes": 20000}

ALL_FIELDS = ["track_path", "bpm", "position", "fader", "eq_low", "is_master", "crossfader"]


def run(mem: Memory, version: str, decks: list[int], fields: list[str], console: Console,
        out_dir: Path) -> Path:
    found: list[Found] = []
    console.say(f"Rekordbox {version} · módulo en 0x{mem.module_base:X} · decks {decks}")
    for deck in decks:
        console.say(f"\n===== DECK {deck} =====")
        if "track_path" in fields:
            slots = scan_track_path(console, mem, deck, max(decks))
            if slots:
                s, utf16 = slots[0]
                found.append(Found("track_path", deck, s, "ptr_utf16" if utf16 else "ptr_utf8", {"max_len": 512}))
        if "bpm" in fields:
            pick = _pick(console, mem, scan_bpm(console, mem, deck), "BPM")
            if pick:
                found.append(Found("bpm", deck, pick[0], pick[1], {}))
        if "position" in fields:
            cands, extra = scan_position(console, mem, deck)
            pick = _pick(console, mem, cands, "posición")
            if pick:
                found.append(Found("position", deck, pick[0], pick[1], extra if extra.get("scale") != 1.0 else {}))
        if "fader" in fields:
            cands, extra = scan_range_control(
                console, mem, "fader", f"Baja el fader de canal del deck {deck} del todo",
                f"Sube el fader de canal del deck {deck} del todo",
                f"Pon el fader de canal del deck {deck} más o menos a la mitad")
            for addr, dt, ex in _range_picks(console, cands, "fader"):
                found.append(Found("fader", deck, addr, dt, ex))
        if "eq_low" in fields:
            cands, extra = scan_range_control(
                console, mem, "eq_low", f"Gira el EQ de graves (LOW) del deck {deck} al mínimo",
                f"Gira el EQ de graves (LOW) del deck {deck} al máximo",
                f"Pon el EQ de graves (LOW) del deck {deck} en el centro")
            for addr, dt, ex in _range_picks(console, cands, "EQ de graves"):
                found.append(Found("eq_low", deck, addr, dt, ex))
    if "is_master" in fields and len(decks) > 1:
        console.say("\n===== DECK MASTER =====")
        per_deck, idx = scan_master(console, mem, max(decks))
        # Con 2 decks, el flag del deck 2 y un índice global 0/1 son indistinguibles: para cada
        # deck se elige el candidato más cercano a sus otros campos (suelen estar en la misma estructura).
        flags = set()
        for d, cands in per_deck.items():
            near = [f.address for f in found if f.deck == d]
            flat = [(int(a), c.dtype) for c in cands for a in c.addresses.tolist()]
            if near:
                flat.sort(key=lambda x: min(abs(x[0] - a) for a in near))
            pick = _pick(console, mem, [Candidates("u8", np.array([a for a, _ in flat], np.uint64))]
                         if flat else [], f"master deck {d}")
            if pick:
                flags.add(pick[0])
                found.append(Found("is_master", d, pick[0], "bool", {}))
        idx = [Candidates(c.dtype, c.addresses[~np.isin(c.addresses, list(flags))]) for c in idx]
        pick = _pick(console, mem, idx, "índice de deck master")
        if pick:
            found.append(Found("master_deck", None, pick[0], pick[1], {"index_base": 0}))
    if "crossfader" in fields:
        console.say("\n===== CROSSFADER =====")
        cands, extra = scan_range_control(
            console, mem, "crossfader", "Lleva el crossfader del todo a la IZQUIERDA (lado A)",
            "Lleva el crossfader del todo a la DERECHA (lado B)", "Pon el crossfader en el centro")
        for addr, dt, ex in _range_picks(console, cands, "crossfader"):
            found.append(Found("crossfader", None, addr, dt, ex))

    console.say("\n===== CADENAS DE PUNTEROS =====\n(puede tardar un poco)")
    pmap = PointerMap.build(mem)
    chains = {}
    candidates: dict[str, dict] = {}
    for f in found:
        label = f"{f.field}" + (f" deck {f.deck}" if f.deck else "")
        all_chains = chain_for(console, mem, pmap, f.address, label)
        previous = load_candidates(candidates_path(out_dir, version)).get(cand_key(f.field, f.deck))
        if all_chains and previous:
            # Cadenas que también aparecieron en una sesión anterior de Rekordbox = estables.
            old_set = {tuple(c) for c in previous["chains"]}
            common = [c for c in all_chains if tuple(to_hex(c)) in old_set]
            if common and len(common) < len(all_chains):
                console.say(f"      {len(common)} de ellas ya aparecieron en la sesión anterior: "
                            "se guardan solo esas (estables tras reiniciar).")
                all_chains = common
            elif not common:
                console.say("      ninguna coincide con la sesión anterior: se sustituyen las candidatas.")
        if f.field in ("fader", "eq_low", "crossfader"):
            describe_object(console, mem, pmap, f.address)
        if all_chains:
            key = cand_key(f.field, f.deck)
            chains.setdefault((f.field, f.deck), all_chains[0])
            entry = candidates.setdefault(key, {"field": f.field, "deck": f.deck, "type": f.dtype,
                                                "extra": f.extra, "chains": [], "chain_meta": {}})
            for c in all_chains:
                h = to_hex(c)
                entry["chains"].append(h)
                # Cada dirección candidata tiene su propio tipo y rango.
                entry["chain_meta"][" ".join(h)] = {"type": f.dtype, "extra": f.extra}
    data = build_offsets(version, found, chains)
    out = out_dir / f"{version}.json"
    merge_into_file(out, data)
    cand_path = candidates_path(out_dir, version)
    save_candidates(cand_path, candidates)
    console.say(f"\nGuardado en {out} (y todas las cadenas candidatas en {cand_path.name}).\n"
                "IMPORTANTE: una cadena puede no sobrevivir a un reinicio de Rekordbox. Cierra y abre "
                "Rekordbox y ejecuta `python -m tools.offset_detect --recheck` para quedarte solo con "
                "las cadenas estables. Repítelo tras otro reinicio para mayor seguridad.")
    return out


# ---------------------------------------------------------------------------
# Candidatas y re-comprobación tras reiniciar Rekordbox
# ---------------------------------------------------------------------------
def cand_key(field: str, deck: int | None) -> str:
    return f"{field}:{deck if deck is not None else 'global'}"


def candidates_path(out_dir: Path, version: str) -> Path:
    return out_dir / f"{version}.candidates.json"


def load_candidates(path: Path) -> dict[str, dict]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def save_candidates(path: Path, new: dict[str, dict]) -> None:
    data = load_candidates(path)
    data.update(new)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")


def _raw_reader(mem: Memory, ftype: str):
    """Lee el valor crudo de una cadena (sin `map`), o None si no se puede."""
    sys.path.insert(0, str(ROOT / "src"))
    from rkbx_practice.memory.offsets import FieldSpec
    from rkbx_practice.memory.reader import read_field
    acc = _Access(mem)

    def read(chain_hex: list[str]):
        try:
            return read_field(acc, FieldSpec.from_dict("x", {"chain": chain_hex, "type": ftype}))
        except Exception:  # noqa: BLE001
            return None
    return read


def _num(v) -> float | None:
    if v is None or isinstance(v, str):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if np.isfinite(f) else None


def _filter(chains: list[list[str]], read, keep) -> list[list[str]]:
    return [c for c in chains if keep(read(c))]


RANGE_TEXTS = {
    "fader": ("Sube el fader de canal del deck {d} del todo", "Baja el fader de canal del deck {d} del todo"),
    "eq_low": ("Gira el EQ de graves (LOW) del deck {d} al máximo", "Gira el EQ de graves (LOW) del deck {d} al mínimo"),
    "crossfader": ("Lleva el crossfader del todo a la DERECHA (lado B)", "Lleva el crossfader del todo a la IZQUIERDA (lado A)"),
}


def meta_of(cand: dict, chain: list[str]) -> dict:
    """Tipo y extra (map, scale…) de una cadena; cada dirección candidata puede tener los suyos."""
    return cand.get("chain_meta", {}).get(" ".join(chain)) or {"type": cand["type"],
                                                               "extra": cand.get("extra", {})}


def recheck_one(console: Console, mem: Memory, cand: dict) -> list[list[str]]:
    """Guía unas acciones y devuelve las cadenas que siguen leyendo valores correctos."""
    field, deck = cand["field"], cand["deck"]
    chains: list[list[str]] = cand["chains"]
    readers: dict[str, object] = {}

    def read(c: list[str]):
        t = meta_of(cand, c)["type"]
        if t not in readers:
            readers[t] = _raw_reader(mem, t)
        return readers[t](c)  # type: ignore[operator]
    d = deck
    console.say(f"\n--- {field}" + (f" deck {d}" if d else "") + f": {len(chains)} cadenas candidatas")
    if field in RANGE_TEXTS:
        hi_t, lo_t = (t.format(d=d) for t in RANGE_TEXTS[field])
        for _ in range(2):
            console.action(hi_t)
            his = {tuple(c): _num(read(c)) for c in chains}
            console.action(lo_t)
            los = {tuple(c): _num(read(c)) for c in chains}
            readable = [(his[tuple(c)], los[tuple(c)]) for c in chains
                        if his[tuple(c)] is not None and los[tuple(c)] is not None]
            console.say(f"    {len(readable)} de {len(chains)} cadenas se pueden leer"
                        + (f"; ejemplos (arriba→abajo): "
                           + ", ".join(f"{h:.3g}→{l:.3g}" for h, l in readable[:5]) if readable else ""))
            keep = []
            for c in chains:
                hi, lo = his[tuple(c)], los[tuple(c)]
                if hi is None or lo is None or hi - lo <= 1e-6:
                    continue
                mp = meta_of(cand, c)["extra"].get("map")
                tol = abs(mp[1] - mp[0]) * 0.02 + 1e-6 if mp else 0.0
                if mp and (abs(hi - mp[1]) > tol or abs(lo - mp[0]) > tol):
                    continue
                keep.append(c)
            chains = keep
            console.say(f"    quedan {len(chains)}")
    elif field == "bpm":
        for i in range(2):
            b = console.ask_float(f"Deck {d}: mueve el pitch y escribe el BPM que muestra ({i + 1}/2)")
            chains = _filter(chains, read, lambda v, b=b: _num(v) is not None and abs(_num(v) - b) <= 0.06)
            console.say(f"    quedan {len(chains)}")
    elif field == "position":
        scale = cand.get("extra", {}).get("scale", 1.0)
        for i in range(2):
            t = console.ask_float(f"Deck {d}: reproduce, PAUSA y escribe el tiempo transcurrido ({i + 1}/2)")
            chains = _filter(chains, read, lambda v, t=t: _num(v) is not None
                             and abs(_num(v) * scale - t) <= 0.15)
            console.say(f"    quedan {len(chains)}")
    elif field == "track_path":
        console.action(f"Carga un tema en el deck {d}")
        before = {tuple(c): read(c) for c in chains}
        console.action(f"Carga OTRO tema distinto en el deck {d}")
        chains = [c for c in chains if isinstance(read(c), str) and "USBANLZ" in read(c).upper()
                  and read(c) != before[tuple(c)]]
        console.say(f"    quedan {len(chains)}")
    elif field in ("is_master", "master_deck"):
        n_decks = 2
        for target in (1, 2, 1):
            console.action(f"Pon el deck {target} como MASTER")
            if field == "is_master":
                want = 1 if target == d else 0
            else:
                want = target - 1
            chains = _filter(chains, read, lambda v, w=want: _num(v) is not None and int(_num(v)) == w)
        console.say(f"    quedan {len(chains)} (con {n_decks} decks)")
    return chains


def recheck(console: Console, mem: Memory, version: str, out_dir: Path,
            only: list[str] | None, decks: list[int] | None) -> None:
    cpath = candidates_path(out_dir, version)
    cands = load_candidates(cpath)
    if not cands:
        console.say(f"No hay {cpath.name}. Ejecuta antes el asistente (python -m tools.offset_detect).")
        return
    updated: dict[str, dict] = {}
    found: list[Found] = []
    best: dict[tuple, list[int]] = {}
    for key, cand in cands.items():
        if only and cand["field"] not in only:
            continue
        if decks and cand["deck"] is not None and cand["deck"] not in decks:
            continue
        survivors = recheck_one(console, mem, cand)
        if not survivors:
            console.say(f"    ✗ {key}: ninguna cadena sobrevive. Vuelve a ejecutar el asistente para "
                        f"este campo (--fields {cand['field']}).")
            continue
        console.say(f"    ✓ {key}: {len(survivors)} cadenas estables; se usa {survivors[0]}")
        updated[key] = {**cand, "chains": survivors}
        meta = meta_of(cand, survivors[0])
        found.append(Found(cand["field"], cand["deck"], 0, meta["type"], meta["extra"]))
        best[(cand["field"], cand["deck"])] = [int(x, 16) for x in survivors[0]]
    if updated:
        save_candidates(cpath, updated)
        out = out_dir / f"{version}.json"
        merge_into_file(out, build_offsets(version, found, best))
        console.say(f"\nActualizado {out}. Si quieres más seguridad, reinicia Rekordbox y repite --recheck.")


def main(argv: list[str] | None = None) -> int:
    sys.path.insert(0, str(ROOT / "src"))
    from rkbx_practice.memory.offsets import normalize_version
    from .memscan import WinProcessMemory

    p = argparse.ArgumentParser(description="Genera offsets para tu versión de Rekordbox (solo lectura)")
    p.add_argument("--process", default="rekordbox.exe")
    p.add_argument("--decks", type=int, nargs="+", default=[1, 2])
    p.add_argument("--fields", nargs="+", default=ALL_FIELDS, choices=ALL_FIELDS)
    p.add_argument("--out-dir", default=str(ROOT / "data" / "offsets"))
    p.add_argument("--validate", help="Valida un archivo de offsets con la memoria actual")
    p.add_argument("--depth", type=int, default=SEARCH["max_depth"],
                   help="Niveles máximos de la cadena de punteros (por defecto 6)")
    p.add_argument("--max-offset", type=lambda x: int(x, 0), default=SEARCH["max_offset"],
                   help="Desplazamiento máximo por nivel (por defecto 0x1000)")
    p.add_argument("--anchor", nargs="+", metavar="CAMPO",
                   help="Buscar solo caminos que salgan de los objetos de campos ya estables del "
                        "archivo de offsets (p. ej. --anchor bpm). Mucho más fiable tras reiniciar.")
    p.add_argument("--recheck", action="store_true",
                   help="Tras reiniciar Rekordbox: filtra las cadenas candidatas y se queda con las estables")
    args = p.parse_args(argv)
    SEARCH["max_depth"], SEARCH["max_offset"] = args.depth, args.max_offset
    console = Console()
    try:
        mem = WinProcessMemory(args.process)
    except Exception as exc:  # noqa: BLE001
        console.say(f"No se pudo abrir {args.process}: {exc}")
        return 1
    version = normalize_version(mem.version()) or "desconocida"
    if args.anchor:
        SEARCH["anchors"] = load_anchors(console, mem, Path(args.out_dir) / f"{version}.json",
                                         args.anchor, args.decks)
        if not SEARCH["anchors"]:
            return 1
    if args.validate:
        validate_file(console, mem, Path(args.validate))
        return 0
    if args.recheck:
        only = None if args.fields == ALL_FIELDS else args.fields
        recheck(console, mem, version, Path(args.out_dir), only, args.decks)
        return 0
    run(mem, version, args.decks, args.fields, console, Path(args.out_dir))
    return 0
