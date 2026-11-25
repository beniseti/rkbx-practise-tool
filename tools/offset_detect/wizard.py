"""Asistente interactivo para generar `data/offsets/<versión>.json`.

Uso (Windows, con Rekordbox abierto):
    python -m tools.offset_detect                 # todo, deck a deck
    python -m tools.offset_detect --fields bpm fader --decks 1
    python -m tools.offset_detect --validate data/offsets/7.2.20.json

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
        (mid_text, lambda v, s: (v > s["lo"] + (s["hi"] - s["lo"]) * 0.15)
         & (v < s["hi"] - (s["hi"] - s["lo"]) * 0.15), None),
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
def chain_for(console: Console, mem: Memory, pmap: PointerMap, address: int, label: str) -> list[int] | None:
    chains = find_chains(mem, pmap, address)
    if not chains:
        console.say(f"    ✗ {label}: sin cadena de punteros estática (prueba con más profundidad)")
        return None
    console.say(f"    ✓ {label}: {len(chains)} cadenas; mejor: {to_hex(chains[0])}")
    return chains[0]


def build_offsets(version: str, found: list[Found], chains: dict[tuple, list[int]]) -> dict:
    out: dict = {"version": version, "module": "rekordbox.exe", "pointer_size": 8,
                 "decks": {}, "global": {}}
    for f in found:
        chain = chains.get((f.field, f.deck))
        if chain is None:
            continue
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


def validate_file(console: Console, mem: Memory, path: Path) -> None:
    sys.path.insert(0, str(ROOT / "src"))
    from rkbx_practice.memory.offsets import OffsetTable
    from rkbx_practice.memory.reader import read_field

    class Access:
        module_base = mem.module_base
        pointer_size = mem.pointer_size

        def read_bytes(self, a, n):
            from rkbx_practice.memory.reader import MemoryReadError
            data = mem.read(a, n)
            if data is None:
                raise MemoryReadError(hex(a))
            return data

    table = OffsetTable.from_dict(json.loads(path.read_text(encoding="utf-8")))
    acc = Access()
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
            pick = _pick(console, mem, cands, "fader")
            if pick:
                found.append(Found("fader", deck, pick[0], pick[1], extra))
        if "eq_low" in fields:
            cands, extra = scan_range_control(
                console, mem, "eq_low", f"Gira el EQ de graves (LOW) del deck {deck} al mínimo",
                f"Gira el EQ de graves (LOW) del deck {deck} al máximo",
                f"Pon el EQ de graves (LOW) del deck {deck} en el centro")
            pick = _pick(console, mem, cands, "EQ de graves")
            if pick:
                found.append(Found("eq_low", deck, pick[0], pick[1], extra))
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
        pick = _pick(console, mem, cands, "crossfader")
        if pick:
            found.append(Found("crossfader", None, pick[0], pick[1], extra))

    console.say("\n===== CADENAS DE PUNTEROS =====\n(puede tardar un poco)")
    pmap = PointerMap.build(mem)
    chains = {}
    for f in found:
        label = f"{f.field}" + (f" deck {f.deck}" if f.deck else "")
        chain = chain_for(console, mem, pmap, f.address, label)
        if chain is not None:
            chains[(f.field, f.deck)] = chain
            if resolve(mem, chain) != f.address:
                console.say("      (aviso: la cadena no resuelve a la dirección actual)")
    data = build_offsets(version, found, chains)
    out = out_dir / f"{version}.json"
    merge_into_file(out, data)
    console.say(f"\nGuardado en {out}.\nRecomendado: cierra y abre Rekordbox y ejecuta "
                f"`python -m tools.offset_detect --validate {out}` para confirmar que las cadenas "
                "sobreviven a un reinicio.")
    return out


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
    args = p.parse_args(argv)
    console = Console()
    try:
        mem = WinProcessMemory(args.process)
    except Exception as exc:  # noqa: BLE001
        console.say(f"No se pudo abrir {args.process}: {exc}")
        return 1
    version = normalize_version(mem.version()) or "desconocida"
    if args.validate:
        validate_file(console, mem, Path(args.validate))
        return 0
    run(mem, version, args.decks, args.fields, console, Path(args.out_dir))
    return 0
