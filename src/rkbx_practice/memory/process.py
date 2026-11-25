"""Conexión al proceso `rekordbox.exe` (Windows) en SOLO LECTURA.

- Se usa pymem, pero únicamente sus funciones de lectura (ReadProcessMemory).
- La versión se lee del recurso de versión del ejecutable (VS_FIXEDFILEINFO),
  usando la API de Windows vía ctypes (sin dependencias extra).
"""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

from .reader import MemoryReadError

log = logging.getLogger(__name__)


class ProcessNotFound(RuntimeError):
    pass


def is_windows() -> bool:
    return sys.platform == "win32"


def get_file_version(exe_path: str | Path) -> str | None:
    """Versión de archivo (`7.2.20.0`) de un ejecutable de Windows."""
    if not is_windows():
        return None
    path = str(exe_path)
    version = ctypes.windll.version  # type: ignore[attr-defined]
    size = version.GetFileVersionInfoSizeW(path, None)
    if not size:
        return None
    buf = ctypes.create_string_buffer(size)
    if not version.GetFileVersionInfoW(path, 0, size, buf):
        return None
    ptr = ctypes.c_void_p()
    length = wintypes.UINT()
    if not version.VerQueryValueW(buf, "\\", ctypes.byref(ptr), ctypes.byref(length)):
        return None

    class VS_FIXEDFILEINFO(ctypes.Structure):
        _fields_ = [(n, wintypes.DWORD) for n in (
            "dwSignature", "dwStrucVersion", "dwFileVersionMS", "dwFileVersionLS",
            "dwProductVersionMS", "dwProductVersionLS", "dwFileFlagsMask", "dwFileFlags",
            "dwFileOS", "dwFileType", "dwFileSubtype", "dwFileDateMS", "dwFileDateLS")]

    info = ctypes.cast(ptr, ctypes.POINTER(VS_FIXEDFILEINFO)).contents
    ms, ls = info.dwFileVersionMS, info.dwFileVersionLS
    return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"


def _process_image_path(handle) -> str | None:
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    buf = ctypes.create_unicode_buffer(32768)
    size = wintypes.DWORD(len(buf))
    if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
        return buf.value
    return None


@dataclass
class PymemAccess:
    """Implementación de MemoryAccess sobre pymem (solo lectura)."""

    pm: object
    module_base: int
    pointer_size: int = 8

    def read_bytes(self, address: int, size: int) -> bytes:
        try:
            return self.pm.read_bytes(address, size)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001  (pymem lanza varios tipos)
            raise MemoryReadError(f"Lectura fallida en 0x{address:X}: {exc}") from exc


@dataclass
class RekordboxProcess:
    access: PymemAccess
    exe_path: str | None
    version: str | None
    pid: int

    def is_alive(self) -> bool:
        if not is_windows():
            return False
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        code = wintypes.DWORD()
        handle = getattr(self.access.pm, "process_handle", None)
        if handle is None or not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == 259  # STILL_ACTIVE

    def close(self) -> None:
        try:
            self.access.pm.close_process()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass


def attach(process_name: str = "rekordbox.exe", module_name: str | None = None) -> RekordboxProcess:
    """Se engancha al proceso para LEER su memoria."""
    if not is_windows():
        raise ProcessNotFound("La lectura de memoria solo funciona en Windows")
    try:
        import pymem
        import pymem.process
    except ImportError as exc:  # pragma: no cover
        raise ProcessNotFound("Falta pymem. Instálalo con: pip install pymem") from exc
    try:
        pm = pymem.Pymem(process_name)
    except Exception as exc:  # noqa: BLE001
        raise ProcessNotFound(f"No se encuentra {process_name} en ejecución") from exc
    module = pymem.process.module_from_name(pm.process_handle, module_name or process_name)
    if module is None:
        pm.close_process()
        raise ProcessNotFound(f"No se encuentra el módulo {module_name or process_name}")
    exe_path = _process_image_path(pm.process_handle)
    version = get_file_version(exe_path) if exe_path else None
    pointer_size = 8 if getattr(pm, "is_64_bit", True) else 4
    access = PymemAccess(pm=pm, module_base=int(module.lpBaseOfDll), pointer_size=pointer_size)
    log.info("Conectado a %s (pid %s, versión %s)", process_name, pm.process_id, version)
    return RekordboxProcess(access=access, exe_path=exe_path, version=version, pid=int(pm.process_id))
