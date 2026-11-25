"""Resolución de cadenas de punteros y lectura tipada (solo lectura).

El acceso real al proceso lo da un objeto que cumpla :class:`MemoryAccess`
(`process.PymemAccess` en Windows, `FakeMemory` en tests). Este módulo NO
escribe nunca en memoria: la interfaz ni siquiera tiene método de escritura.
"""

from __future__ import annotations

import struct
from typing import Protocol

from .offsets import FieldSpec


class MemoryReadError(RuntimeError):
    pass


class MemoryAccess(Protocol):
    module_base: int
    pointer_size: int

    def read_bytes(self, address: int, size: int) -> bytes:
        """Lee `size` bytes o lanza MemoryReadError."""


_NUMERIC = {
    "f32": ("<f", 4), "f64": ("<d", 8), "i32": ("<i", 4), "u32": ("<I", 4),
    "u8": ("<B", 1), "bool": ("<B", 1),
}


def read_pointer(mem: MemoryAccess, address: int) -> int:
    size = mem.pointer_size
    data = mem.read_bytes(address, size)
    return struct.unpack("<Q" if size == 8 else "<I", data)[0]


def resolve_chain(mem: MemoryAccess, chain: tuple[int, ...]) -> int | None:
    """Dirección final de la cadena, o None si algún puntero intermedio es nulo."""
    addr = mem.module_base + chain[0]
    for off in chain[1:]:
        ptr = read_pointer(mem, addr)
        if ptr == 0:
            return None
        addr = ptr + off
    return addr


def _read_string(mem: MemoryAccess, address: int, max_len: int, utf16: bool) -> str | None:
    # Lectura por bloques para no salirnos de una página válida.
    unit = 2 if utf16 else 1
    out = bytearray()
    chunk = 64
    while len(out) < max_len * unit:
        try:
            data = mem.read_bytes(address + len(out), chunk)
        except MemoryReadError:
            if chunk == unit:
                break
            chunk = unit
            continue
        for i in range(0, len(data) - unit + 1, unit):
            piece = data[i:i + unit]
            if piece == b"\x00" * unit:
                return out.decode("utf-16-le" if utf16 else "utf-8", errors="replace") or None
            out += piece
    return out.decode("utf-16-le" if utf16 else "utf-8", errors="replace") or None


def read_field(mem: MemoryAccess, spec: FieldSpec):
    """Lee y normaliza un campo. Devuelve None si la cadena está vacía (puntero nulo)."""
    addr = resolve_chain(mem, spec.chain)
    if addr is None:
        return None
    t = spec.type
    if t in _NUMERIC:
        fmt, size = _NUMERIC[t]
        raw = struct.unpack(fmt, mem.read_bytes(addr, size))[0]
        if t == "bool":
            return bool(raw)
        if raw != raw:  # NaN
            return None
        if spec.map_range is not None or spec.invert or spec.scale != 1.0:
            return spec.normalize(float(raw))
        return raw
    if t in ("ptr_utf8", "ptr_utf16"):
        ptr = read_pointer(mem, addr)
        if ptr == 0:
            return None
        return _read_string(mem, ptr, spec.max_len, t == "ptr_utf16")
    if t in ("utf8", "utf16"):
        return _read_string(mem, addr, spec.max_len, t == "utf16")
    raise MemoryReadError(f"Tipo no soportado: {t}")


class FakeMemory:
    """Memoria simulada para tests y para la herramienta de offsets."""

    def __init__(self, module_base: int = 0x140000000, pointer_size: int = 8):
        self.module_base = module_base
        self.pointer_size = pointer_size
        self._regions: dict[int, bytearray] = {}

    def map(self, address: int, size: int) -> None:
        self._regions[address] = bytearray(size)

    def _locate(self, address: int, size: int) -> tuple[bytearray, int]:
        for base, buf in self._regions.items():
            if base <= address and address + size <= base + len(buf):
                return buf, address - base
        raise MemoryReadError(f"Dirección no mapeada 0x{address:X}")

    def write(self, address: int, data: bytes) -> None:  # solo para preparar tests
        buf, off = self._locate(address, len(data))
        buf[off:off + len(data)] = data

    def write_ptr(self, address: int, value: int) -> None:
        self.write(address, struct.pack("<Q" if self.pointer_size == 8 else "<I", value))

    def write_f32(self, address: int, value: float) -> None:
        self.write(address, struct.pack("<f", value))

    def write_f64(self, address: int, value: float) -> None:
        self.write(address, struct.pack("<d", value))

    def read_bytes(self, address: int, size: int) -> bytes:
        buf, off = self._locate(address, size)
        return bytes(buf[off:off + size])

    def regions(self) -> list[tuple[int, int]]:
        return [(b, len(buf)) for b, buf in self._regions.items()]
