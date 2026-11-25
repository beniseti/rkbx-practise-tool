"""Escaneo de memoria (solo lectura) para localizar valores que cambian con las acciones del usuario.

Inspirado en el enfoque de `rb_offset_detect` (rkbx_wave, MIT): el usuario hace
una acción en Rekordbox, se escanea la memoria y se filtran los candidatos
hasta que quedan pocos. Implementación propia con numpy.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import Callable, Iterable, Protocol

import numpy as np

DTYPES = {"f32": np.dtype("<f4"), "f64": np.dtype("<f8"), "u8": np.dtype("u1"),
          "i32": np.dtype("<i4"), "u64": np.dtype("<u8")}


class Memory(Protocol):
    module_base: int
    module_size: int
    pointer_size: int

    def regions(self) -> list[tuple[int, int]]:
        """Regiones legibles y escribibles por el proceso (base, tamaño)."""

    def read(self, address: int, size: int) -> bytes | None: ...


# ---------------------------------------------------------------------------
# Acceso real al proceso (Windows)
# ---------------------------------------------------------------------------
MEM_COMMIT = 0x1000
PAGE_GUARD = 0x100
PAGE_NOACCESS = 0x01
WRITABLE = {0x04, 0x08, 0x40, 0x80}   # READWRITE, WRITECOPY, EXECUTE_READWRITE, EXECUTE_WRITECOPY


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p),
                ("AllocationProtect", wintypes.DWORD), ("PartitionId", wintypes.WORD),
                ("RegionSize", ctypes.c_size_t), ("State", wintypes.DWORD),
                ("Protect", wintypes.DWORD), ("Type", wintypes.DWORD)]


class WinProcessMemory:
    """Lectura de la memoria de un proceso de Windows. Nunca escribe."""

    def __init__(self, process_name: str = "rekordbox.exe", max_region: int = 256 * 1024 * 1024):
        if sys.platform != "win32":
            raise RuntimeError("Solo funciona en Windows")
        import pymem
        import pymem.process
        self.pm = pymem.Pymem(process_name)
        module = pymem.process.module_from_name(self.pm.process_handle, process_name)
        self.module_base = int(module.lpBaseOfDll)
        self.module_size = int(module.SizeOfImage)
        self.pointer_size = 8
        self.max_region = max_region
        self._k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        self._k32.VirtualQueryEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
                                             ctypes.POINTER(MEMORY_BASIC_INFORMATION), ctypes.c_size_t]
        self._k32.VirtualQueryEx.restype = ctypes.c_size_t
        self._k32.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                                ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]

    def regions(self) -> list[tuple[int, int]]:
        out = []
        addr = 0
        mbi = MEMORY_BASIC_INFORMATION()
        handle = self.pm.process_handle
        while addr < 0x7FFFFFFFFFFF:
            if not self._k32.VirtualQueryEx(handle, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)):
                break
            base = mbi.BaseAddress or 0
            size = mbi.RegionSize
            if (mbi.State == MEM_COMMIT and not (mbi.Protect & PAGE_GUARD)
                    and (mbi.Protect & 0xFF) in WRITABLE and size <= self.max_region):
                out.append((base, size))
            addr = base + size
        return out

    def read(self, address: int, size: int) -> bytes | None:
        buf = ctypes.create_string_buffer(size)
        got = ctypes.c_size_t()
        ok = self._k32.ReadProcessMemory(self.pm.process_handle, ctypes.c_void_p(address), buf, size,
                                         ctypes.byref(got))
        if not ok or got.value != size:
            return None
        return buf.raw

    def version(self) -> str | None:
        from rkbx_practice.memory.process import _process_image_path, get_file_version
        path = _process_image_path(self.pm.process_handle)
        return get_file_version(path) if path else None


class FakeProcessMemory:
    """Adaptador de `rkbx_practice.memory.FakeMemory` para tests."""

    def __init__(self, fake, module_size: int):
        self.fake = fake
        self.module_base = fake.module_base
        self.module_size = module_size
        self.pointer_size = fake.pointer_size

    def regions(self) -> list[tuple[int, int]]:
        return self.fake.regions()

    def read(self, address: int, size: int) -> bytes | None:
        try:
            return self.fake.read_bytes(address, size)
        except Exception:  # noqa: BLE001
            return None


# ---------------------------------------------------------------------------
# Escáner de valores
# ---------------------------------------------------------------------------
@dataclass
class Candidates:
    """Direcciones candidatas para un tipo de dato, con valores guardados por paso."""

    dtype: str
    addresses: np.ndarray                                   # uint64
    saved: dict[str, np.ndarray] = field(default_factory=dict)

    def __len__(self) -> int:
        return int(self.addresses.size)


def first_scan(mem: Memory, dtype: str, predicate: Callable[[np.ndarray], np.ndarray],
               step: int | None = None, regions: Iterable[tuple[int, int]] | None = None,
               save_as: str | None = None) -> Candidates:
    """Recorre toda la memoria escribible y guarda las direcciones cuyo valor cumple `predicate`."""
    dt = DTYPES[dtype]
    step = step or dt.itemsize
    addrs, vals = [], []
    for base, size in (regions if regions is not None else mem.regions()):
        data = mem.read(base, size)
        if data is None:
            continue
        usable = (len(data) // dt.itemsize) * dt.itemsize
        if usable == 0:
            continue
        if step == dt.itemsize:
            arr = np.frombuffer(data[:usable], dtype=dt)
            idx = np.nonzero(predicate(arr))[0]
            addrs.append(base + idx.astype(np.uint64) * dt.itemsize)
            vals.append(arr[idx])
        else:
            for off in range(0, dt.itemsize, step):
                n = (len(data) - off) // dt.itemsize
                if n <= 0:
                    continue
                arr = np.frombuffer(data[off:off + n * dt.itemsize], dtype=dt)
                idx = np.nonzero(predicate(arr))[0]
                addrs.append(base + off + idx.astype(np.uint64) * dt.itemsize)
                vals.append(arr[idx])
    a = np.concatenate(addrs).astype(np.uint64) if addrs else np.zeros(0, np.uint64)
    v = np.concatenate(vals) if vals else np.zeros(0, dt)
    order = np.argsort(a, kind="stable")
    c = Candidates(dtype, a[order])
    if save_as:
        c.saved[save_as] = v[order]
    return c


def read_values(mem: Memory, cands: Candidates) -> tuple[np.ndarray, np.ndarray]:
    """Valores actuales de los candidatos y máscara de los que se pudieron leer."""
    dt = DTYPES[cands.dtype]
    values = np.zeros(len(cands), dtype=dt)
    ok = np.zeros(len(cands), dtype=bool)
    if len(cands) == 0:
        return values, ok
    regions = sorted(mem.regions())
    starts = np.array([r[0] for r in regions], dtype=np.uint64)
    ends = np.array([r[0] + r[1] for r in regions], dtype=np.uint64)
    ridx = np.searchsorted(starts, cands.addresses, side="right") - 1
    for r in np.unique(ridx):
        if r < 0:
            continue
        sel = np.nonzero(ridx == r)[0]
        base, end = int(starts[r]), int(ends[r])
        inside = sel[(cands.addresses[sel] + dt.itemsize) <= end]
        if inside.size == 0:
            continue
        data = mem.read(base, end - base)
        if data is None:
            continue
        offs = (cands.addresses[inside] - base).astype(np.int64)
        raw = np.frombuffer(data, dtype=np.uint8)
        gathered = raw[offs[:, None] + np.arange(dt.itemsize)[None, :]]
        values[inside] = gathered.copy().view(dt).reshape(-1)
        ok[inside] = True
    return values, ok


def refine(mem: Memory, cands: Candidates,
           keep: Callable[[np.ndarray, dict[str, np.ndarray]], np.ndarray],
           save_as: str | None = None) -> Candidates:
    """Relee los candidatos y se queda con los que cumplen `keep(actual, guardados)`."""
    values, ok = read_values(mem, cands)
    with np.errstate(invalid="ignore", over="ignore"):
        mask = ok & keep(values, cands.saved)
    out = Candidates(cands.dtype, cands.addresses[mask],
                     {k: v[mask] for k, v in cands.saved.items()})
    if save_as:
        out.saved[save_as] = values[mask]
    return out


def finite(arr: np.ndarray) -> np.ndarray:
    return np.isfinite(arr) if arr.dtype.kind == "f" else np.ones(arr.shape, bool)


def approx(target: float, tol: float) -> Callable[[np.ndarray], np.ndarray]:
    return lambda a: finite(a) & (np.abs(a.astype(np.float64) - target) <= tol)


# ---------------------------------------------------------------------------
# Cadenas de texto
# ---------------------------------------------------------------------------
def find_strings(mem: Memory, needle: str) -> dict[int, tuple[str, bool]]:
    """Busca `needle` en UTF-8 y UTF-16; devuelve {dirección de inicio: (texto, es_utf16)}."""
    found: dict[int, tuple[str, bool]] = {}
    for base, size in mem.regions():
        data = mem.read(base, size)
        if data is None:
            continue
        for utf16 in (False, True):
            pat = needle.encode("utf-16-le" if utf16 else "utf-8")
            pos = data.find(pat)
            while pos >= 0:
                start = _string_start(data, pos, utf16)
                text = _string_at(data, start, utf16)
                if text:
                    found[base + start] = (text, utf16)
                pos = data.find(pat, pos + len(pat))
    return found


def _string_start(data: bytes, pos: int, utf16: bool) -> int:
    unit = 2 if utf16 else 1
    i = pos
    while i - unit >= 0:
        ch = data[i - unit:i]
        if ch == b"\x00" * unit or (not utf16 and ch[0] < 0x20) or (utf16 and ch[1] == 0 and ch[0] < 0x20):
            break
        i -= unit
    return i


def _string_at(data: bytes, start: int, utf16: bool, max_len: int = 1024) -> str | None:
    unit = 2 if utf16 else 1
    end = start
    while end + unit <= len(data) and data[end:end + unit] != b"\x00" * unit and end - start < max_len * unit:
        end += unit
    try:
        return data[start:end].decode("utf-16-le" if utf16 else "utf-8")
    except UnicodeDecodeError:
        return None
