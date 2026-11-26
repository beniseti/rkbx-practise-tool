"""Acceso al audio del PC: captura (soundcard, WASAPI) y escritura de archivos (soundfile).

- **Loopback** (por defecto): se graba lo que suena por una salida de Windows, p. ej. la tarjeta
  de sonido de la controladora. No hace falta ningún cable.
- **Entrada**: una entrada de línea o micro (si la controladora no deja grabar su salida).

Dependencias opcionales (extra ``audio`` de pyproject): ``soundcard`` (captura WASAPI con loopback),
``soundfile`` (FLAC/WAV/OGG con libsndfile) y ``numpy``. Sin ellas la app funciona igual pero las
sesiones se guardan sin audio (se indica el motivo).
"""

from __future__ import annotations

import sys
import warnings
from pathlib import Path

FORMATS = {
    "flac": ("FLAC", "PCM_16", ".flac"),
    "wav": ("WAV", "PCM_16", ".wav"),
    "ogg": ("OGG", "VORBIS", ".ogg"),
}


class AudioUnavailable(RuntimeError):
    """No se puede grabar audio (falta una librería o el dispositivo)."""


def extension(fmt: str) -> str:
    return FORMATS.get(fmt.lower(), FORMATS["flac"])[2]


def _soundcard():
    try:
        import soundcard  # noqa: F401
    except ImportError as exc:   # pragma: no cover - depende del sistema
        raise AudioUnavailable("falta la librería «soundcard» (pip install -e .[audio])") from exc
    except Exception as exc:     # pragma: no cover  (p. ej. sin servicio de audio)
        raise AudioUnavailable(f"no se pudo iniciar el audio de Windows: {exc}") from exc
    warnings.filterwarnings("ignore", message=".*discontinuity.*")
    return soundcard


def com_init() -> None:
    """WASAPI necesita COM iniciado en cada hilo que graba."""
    if sys.platform == "win32":   # pragma: no cover
        try:
            import ctypes
            ctypes.windll.ole32.CoInitializeEx(None, 0)
        except Exception:  # noqa: BLE001
            pass


def list_devices() -> list[dict]:
    """Salidas (para loopback) y entradas disponibles."""
    sc = _soundcard()
    out = []
    try:
        default_out = sc.default_speaker().name
    except Exception:  # noqa: BLE001
        default_out = None
    try:
        default_in = sc.default_microphone().name
    except Exception:  # noqa: BLE001
        default_in = None
    for s in sc.all_speakers():
        out.append({"name": s.name, "kind": "salida", "default": s.name == default_out})
    for m in sc.all_microphones(include_loopback=False):
        out.append({"name": m.name, "kind": "entrada", "default": m.name == default_in})
    return out


def _pick(devices, wanted: str | None, default):
    if not wanted:
        return default()
    w = wanted.lower()
    for d in devices:
        if w in d.name.lower():
            return d
    names = ", ".join(d.name for d in devices) or "ninguno"
    raise AudioUnavailable(f"no hay ningún dispositivo que contenga «{wanted}» (hay: {names})")


class SoundcardSource:
    """Fuente de audio real. Se abre dentro del hilo que graba."""

    def __init__(self, device: str | None, loopback: bool, sample_rate: int, channels: int,
                 block_frames: int):
        sc = _soundcard()
        com_init()
        try:
            if loopback:
                spk = _pick(sc.all_speakers(), device, sc.default_speaker)
                mic = sc.get_microphone(id=str(spk.name), include_loopback=True)
                self.name = f"{spk.name} (lo que suena)"
            else:
                mic = _pick(sc.all_microphones(include_loopback=False), device, sc.default_microphone)
                self.name = f"{mic.name} (entrada)"
            self._rec = mic.recorder(samplerate=sample_rate, channels=channels,
                                     blocksize=max(256, block_frames // 4))
            self._rec.__enter__()
        except AudioUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            raise AudioUnavailable(f"no se pudo abrir el dispositivo de audio: {exc}") from exc

    def read(self, frames: int):
        return self._rec.record(numframes=frames)

    def close(self) -> None:
        try:
            self._rec.__exit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass


def open_writer(path: Path, sample_rate: int, channels: int, fmt: str):
    try:
        import soundfile as sf
    except ImportError as exc:   # pragma: no cover
        raise AudioUnavailable("falta la librería «soundfile» (pip install -e .[audio])") from exc
    major, subtype, _ = FORMATS.get(fmt.lower(), FORMATS["flac"])
    try:
        return sf.SoundFile(str(path), "w", samplerate=sample_rate, channels=channels,
                            format=major, subtype=subtype)
    except Exception as exc:  # noqa: BLE001
        raise AudioUnavailable(f"no se pudo crear {path.name}: {exc}") from exc
