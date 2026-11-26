"""Grabación del audio de una sesión en un hilo propio.

- Se graba por bloques (`block_ms`) y se escribe directamente al archivo (FLAC por defecto), así una
  sesión larga no ocupa memoria y, si la app se cierra de golpe, lo grabado hasta ahí se conserva.
- Se guarda la hora real del primer bloque (`started_at`) para alinear el audio con los eventos
  del histórico (transiciones, fader cuts, bass swaps), que se guardan con hora.
- Si la captura se atrasa respecto al reloj (p. ej. el loopback de Windows no entrega datos
  mientras no suena nada) se rellena con silencio, para que el audio siga cuadrando con la hora.
- Mientras graba calcula la **forma de onda** resumida (`peaks_per_s` picos por segundo, 1 byte
  cada uno) y la guarda en `<sesión>.peaks` para que la página de revisión la dibuje sin
  descodificar todo el audio.
- **Parada automática** (pedido del usuario): una vez ha sonado algo, los bloques en silencio
  (pico < `silence_threshold_db`) no se escriben enseguida, se guardan aparte; si vuelve el sonido
  se escriben (el audio sigue alineado), y si el silencio dura `auto_stop_silence_s` (5 min) la
  grabación se para sola y ese silencio se descarta: el archivo acaba donde dejó de sonar.
  Si no ha sonado nada desde el principio no se para (probablemente el dispositivo no recibe
  audio y lo indica la ventana).
- Nunca para la app: cualquier error queda en `error` y la sesión se guarda sin audio.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from datetime import datetime
from pathlib import Path

from ..config import AudioConfig
from .capture import AudioUnavailable, SoundcardSource, open_writer

log = logging.getLogger(__name__)

MAX_DRIFT_S = 0.5   # se rellena con silencio si el audio va más de esto por detrás del reloj


class AudioRecorder:
    def __init__(self, cfg: AudioConfig, source_factory=None, writer_factory=None, clock=time.time):
        self.cfg = cfg
        self.block_frames = max(64, cfg.sample_rate * cfg.block_ms // 1000)
        self._source_factory = source_factory or (lambda: SoundcardSource(
            cfg.device, cfg.loopback, cfg.sample_rate, cfg.channels, self.block_frames))
        self._writer_factory = writer_factory or open_writer
        self._clock = clock
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self.path: Path | None = None
        self.error: str | None = None
        self.device_name: str | None = None
        self.started_wall: float | None = None
        self.frames = 0
        self.padded_frames = 0
        self.level_db: float | None = None
        self.heard = False             # ya ha sonado algo
        self.auto_stopped = False      # se paró sola tras el silencio
        self.discarded_frames = 0      # silencio final descartado
        self._pending: list = []       # silencio aún sin escribir (int16)
        self._pending_frames = 0
        self._peaks = bytearray()
        self._peak_acc = 0.0
        self._peak_n = 0

    # ------------------------------------------------------------------
    @property
    def recording(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def seconds(self) -> float:
        return self.frames / self.cfg.sample_rate

    @property
    def silence_s(self) -> float:
        """Segundos seguidos de silencio en curso (tras haber sonado algo)."""
        return self._pending_frames / self.cfg.sample_rate

    @property
    def ended_wall(self) -> float | None:
        """Hora real del final de lo grabado (donde dejó de sonar, si se paró sola)."""
        return self.started_wall + self.seconds if self.started_wall is not None else None

    def start(self, path: Path, wait_s: float = 3.0) -> bool:
        """Empieza a grabar en `path`. Espera a que el dispositivo se abra (o falle)."""
        self.path = Path(path)
        self._stop.clear()
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, name="audio-rec", daemon=True)
        self._thread.start()
        self._ready.wait(wait_s)
        return self.error is None

    def stop(self, timeout_s: float = 5.0) -> dict:
        """Para y devuelve la información para el JSON de la sesión."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout_s)
            if self._thread.is_alive():
                self.error = self.error or "la captura no respondió al parar"
        return self.info()

    def info(self) -> dict:
        peaks_file = self.path.with_suffix(".peaks").name if (self.path and self._peaks) else None
        return {
            "file": self.path.name if (self.path is not None and self.frames) else None,
            "started_at": (datetime.fromtimestamp(self.started_wall).isoformat(timespec="milliseconds")
                           if self.started_wall else None),
            "duration_s": round(self.seconds, 3),
            "sample_rate": self.cfg.sample_rate,
            "channels": self.cfg.channels,
            "format": self.cfg.format,
            "device": self.device_name,
            "loopback": self.cfg.loopback,
            "padded_s": round(self.padded_frames / self.cfg.sample_rate, 3),
            "auto_stopped": self.auto_stopped,
            "discarded_silence_s": round(self.discarded_frames / self.cfg.sample_rate, 3),
            "peaks_file": peaks_file,
            "peaks_per_s": self.cfg.peaks_per_s,
            "error": self.error,
        }

    # ------------------------------------------------------------------
    def _run(self) -> None:
        source = writer = None
        try:
            try:
                source = self._source_factory()
                self.device_name = getattr(source, "name", None)
                writer = self._writer_factory(self.path, self.cfg.sample_rate, self.cfg.channels,
                                              self.cfg.format)
            except AudioUnavailable as exc:
                self.error = str(exc)
                log.warning("Sesión sin audio: %s", exc)
                return
            except Exception as exc:  # noqa: BLE001
                self.error = f"no se pudo empezar a grabar audio: {exc}"
                log.exception("Sesión sin audio")
                return
            finally:
                self._ready.set()
            self._loop(source, writer)
            if self.auto_stopped:
                self.discarded_frames = self._pending_frames
            else:
                self._flush_pending(writer)        # parada a mano: se conserva el final
            self._pending, self._pending_frames = [], 0
        except Exception as exc:  # noqa: BLE001
            self.error = f"la grabación de audio se cortó: {exc}"
            log.exception("Grabación de audio cortada")
        finally:
            for obj in (writer, source):
                if obj is not None:
                    try:
                        obj.close()
                    except Exception:  # noqa: BLE001
                        pass
            self._flush_peak()
            self._save_peaks()

    def _loop(self, source, writer) -> None:
        import numpy as np
        sr = self.cfg.sample_rate
        while not self._stop.is_set():
            data = source.read(self.block_frames)
            now = self._clock()
            if data is None or len(data) == 0:
                continue
            data = np.asarray(data, dtype=np.float32)
            if data.ndim == 1:
                data = data[:, None]
            if data.shape[1] != self.cfg.channels:
                data = np.repeat(data[:, :1], self.cfg.channels, axis=1) if data.shape[1] == 1 \
                    else data[:, :self.cfg.channels]
            if self.started_wall is None:
                self.started_wall = now - len(data) / sr
            behind = (now - self.started_wall) * sr - (self.frames + self._pending_frames + len(data))
            if behind > MAX_DRIFT_S * sr:
                gap = np.zeros((int(behind), self.cfg.channels), dtype=np.float32)
                self.padded_frames += len(gap)
                self._feed(writer, gap)
            self._feed(writer, data)

    def _feed(self, writer, block) -> None:
        """Escribe el bloque, o lo guarda aparte si es silencio tras haber sonado algo."""
        import numpy as np
        peak = float(np.abs(block).max()) if len(block) else 0.0
        self.level_db = 20 * math.log10(peak) if peak > 1e-5 else -100.0
        loud = self.level_db >= self.cfg.silence_threshold_db
        if loud:
            self._flush_pending(writer)
            self._write(writer, block)
            self.heard = True
            return
        if not self.heard or self.cfg.auto_stop_silence_s <= 0:
            self._write(writer, block)
            return
        self._pending.append(np.clip(block * 32767, -32768, 32767).astype(np.int16))
        self._pending_frames += len(block)
        if self._pending_frames >= self.cfg.auto_stop_silence_s * self.cfg.sample_rate:
            self.auto_stopped = True
            log.info("Grabación parada: %.0f s sin sonido", self.silence_s)
            self._stop.set()

    def _flush_pending(self, writer) -> None:
        import numpy as np
        for b in self._pending:
            self._write(writer, b.astype(np.float32) / 32767)
        self._pending, self._pending_frames = [], 0

    def _write(self, writer, block) -> None:
        import numpy as np
        writer.write(block)
        self.frames += len(block)
        mono = np.abs(block).max(axis=1)
        per = max(1, self.cfg.sample_rate // max(1, self.cfg.peaks_per_s))
        i = 0
        while i < len(mono):
            take = min(per - self._peak_n, len(mono) - i)
            self._peak_acc = max(self._peak_acc, float(mono[i:i + take].max()))
            self._peak_n += take
            i += take
            if self._peak_n >= per:
                self._flush_peak()

    def _flush_peak(self) -> None:
        if self._peak_n:
            self._peaks.append(min(255, int(round(min(1.0, self._peak_acc) * 255))))
            self._peak_acc, self._peak_n = 0.0, 0

    def _save_peaks(self) -> None:
        if self.path is None or not self._peaks:
            return
        try:
            self.path.with_suffix(".peaks").write_bytes(bytes(self._peaks))
        except OSError as exc:
            log.warning("No se pudo guardar la forma de onda: %s", exc)


def measure(cfg: AudioConfig, seconds: float = 5.0, source_factory=None) -> dict:
    """Graba unos segundos sin guardar nada y devuelve el nivel (para `audio test`)."""
    import numpy as np
    block = max(64, cfg.sample_rate * cfg.block_ms // 1000)
    factory = source_factory or (lambda: SoundcardSource(cfg.device, cfg.loopback, cfg.sample_rate,
                                                         cfg.channels, block))
    src = factory()
    peak, rms_acc, n = 0.0, 0.0, 0
    t_end = time.monotonic() + seconds
    try:
        while time.monotonic() < t_end:
            data = np.asarray(src.read(block), dtype=np.float32)
            if data.size == 0:
                continue
            peak = max(peak, float(np.abs(data).max()))
            rms_acc += float((data.astype(np.float64) ** 2).sum())
            n += data.size
    finally:
        src.close()
    rms = math.sqrt(rms_acc / n) if n else 0.0

    def db(v):
        return round(20 * math.log10(v), 1) if v > 1e-5 else None
    return {"device": getattr(src, "name", None), "peak_db": db(peak), "rms_db": db(rms),
            "silent": peak < 1e-3}
