"""Búsqueda de cadenas de punteros estáticas (módulo + desplazamientos) hacia una dirección.

Las direcciones de los datos cambian en cada arranque de Rekordbox; lo estable es
un camino desde una dirección dentro de `rekordbox.exe` (estática) siguiendo
punteros. Formato de salida = formato de `data/offsets`: `[base, o1, ..., on]`
donde `base` es relativo al módulo y la lectura es
`addr = módulo + base; addr = *addr + o1; ...`.

Búsqueda hacia atrás: desde la dirección objetivo T se buscan las posiciones de
memoria que contienen un valor V con `T - V` en `[0, max_offset]`; cada una es
un nivel más. Si la posición está dentro del módulo, la cadena es estática.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .memscan import Memory


@dataclass
class PointerMap:
    values: np.ndarray     # uint64, ordenado
    slots: np.ndarray      # uint64, dirección donde está guardado cada valor

    @classmethod
    def build(cls, mem: Memory) -> "PointerMap":
        regions = sorted(mem.regions())
        mod_lo, mod_hi = mem.module_base, mem.module_base + mem.module_size
        all_ranges = regions + [(mod_lo, mem.module_size)]
        starts = np.array(sorted(r[0] for r in all_ranges), dtype=np.uint64)
        ends_by_start = {r[0]: r[0] + r[1] for r in all_ranges}
        ends = np.array([ends_by_start[int(s)] for s in starts], dtype=np.uint64)
        vals, slots = [], []
        ps = mem.pointer_size
        dt = np.dtype("<u8") if ps == 8 else np.dtype("<u4")
        for base, size in regions:
            data = mem.read(base, size)
            if data is None:
                continue
            usable = (len(data) // ps) * ps
            arr = np.frombuffer(data[:usable], dtype=dt).astype(np.uint64)
            # Solo valores que apuntan a memoria conocida.
            idx = np.searchsorted(starts, arr, side="right") - 1
            valid = idx >= 0
            valid[valid] &= arr[valid] < ends[idx[valid]]
            sel = np.nonzero(valid)[0]
            vals.append(arr[sel])
            slots.append(base + sel.astype(np.uint64) * ps)
        if not vals:
            return cls(np.zeros(0, np.uint64), np.zeros(0, np.uint64))
        v = np.concatenate(vals)
        s = np.concatenate(slots)
        order = np.argsort(v, kind="stable")
        return cls(v[order], s[order])

    def pointing_into(self, lo: int, hi: int) -> tuple[np.ndarray, np.ndarray]:
        """(valores, posiciones) con lo <= valor <= hi."""
        a = np.searchsorted(self.values, np.uint64(lo), side="left")
        b = np.searchsorted(self.values, np.uint64(hi), side="right")
        return self.values[a:b], self.slots[a:b]

    def slots_pointing_to(self, address: int) -> list[int]:
        _, s = self.pointing_into(address, address)
        return [int(x) for x in s]


def anchor_objects(mem: Memory, chain: list[int]) -> list[tuple[int, list[int]]]:
    """Objetos intermedios de una cadena estable: [(dirección del objeto, prefijo de cadena)].

    Para `[b, o1, ..., on]`: P1 = *(módulo+b) con prefijo [b]; P2 = *(P1+o1) con prefijo
    [b, o1]; ... Una cadena que llegue a `Pk + x` se escribe `prefijo_k + [x]`.
    """
    import struct
    out = []
    addr = mem.module_base + chain[0]
    prefix = [chain[0]]
    for off in chain[1:]:
        data = mem.read(addr, mem.pointer_size)
        if data is None:
            break
        ptr = struct.unpack("<Q" if mem.pointer_size == 8 else "<I", data)[0]
        if ptr == 0:
            break
        out.append((ptr, list(prefix)))
        addr = ptr + off
        prefix.append(off)
    return out


def find_chains(mem: Memory, pmap: PointerMap, target: int, max_depth: int = 4,
                max_offset: int = 0x800, max_nodes: int = 3000, max_results: int = 50,
                module_slots: list[tuple[int, int]] | None = None,
                anchors: list[tuple[int, list[int]]] | None = None,
                anchors_only: bool = False) -> list[list[int]]:
    """Cadenas `[base_relativa, o1, ..., on]` que llegan a `target`.

    Con `anchors` (objetos de cadenas ya comprobadas como estables, ver `anchor_objects`)
    también se aceptan caminos que salen de esos objetos; esas cadenas van primero.
    """
    mod_lo, mod_hi = mem.module_base, mem.module_base + mem.module_size
    results: list[list[int]] = []
    anchored: list[list[int]] = []
    anchors = anchors or []

    def check_anchor(addr: int, offsets: list[int]) -> None:
        for obj, prefix in anchors:
            if obj <= addr <= obj + max_offset:
                anchored.append(prefix + [addr - obj] + offsets)

    if mod_lo <= target < mod_hi and not anchors_only:
        results.append([target - mod_lo])
    check_anchor(target, [])
    frontier: list[tuple[int, list[int]]] = [(target, [])]
    seen = {target}
    for _depth in range(max_depth):
        nxt: list[tuple[int, list[int]]] = []
        for addr, offsets in frontier:
            lo = max(0, addr - max_offset)
            vals, slots = pmap.pointing_into(lo, addr)
            # Preferimos desplazamientos pequeños: recorremos de más cercano a más lejano.
            for v, slot in sorted(zip(vals.tolist(), slots.tolist()), key=lambda x: addr - x[0]):
                new_offsets = [addr - v] + offsets
                if mod_lo <= slot < mod_hi:
                    if not anchors_only and len(results) < max_results:
                        results.append([slot - mod_lo] + new_offsets)
                elif slot not in seen and len(nxt) < max_nodes:
                    seen.add(slot)
                    check_anchor(slot, new_offsets)
                    nxt.append((slot, new_offsets))
        frontier = nxt
        if not frontier or (len(results) >= max_results and not anchors):
            break
    return _rank(anchored)[:max_results] + _rank(results)


def _rank(chains: list[list[int]]) -> list[list[int]]:
    return sorted(chains, key=lambda c: (len(c), sum(c[1:]), c[0]))


def resolve(mem: Memory, chain: list[int]) -> int | None:
    """Dirección final de una cadena en la memoria actual (None si se rompe)."""
    import struct
    addr = mem.module_base + chain[0]
    for off in chain[1:]:
        data = mem.read(addr, mem.pointer_size)
        if data is None:
            return None
        ptr = struct.unpack("<Q" if mem.pointer_size == 8 else "<I", data)[0]
        if ptr == 0:
            return None
        addr = ptr + off
    return addr


def to_hex(chain: list[int]) -> list[str]:
    return [hex(x) for x in chain]
