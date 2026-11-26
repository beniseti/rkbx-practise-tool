"""Página de revisión de sesiones grabadas (navegador): escuchar las mezclas con sus marcas.

Solo biblioteca estándar. Escucha en 127.0.0.1 (solo este PC):

- ``/``                       página (review.html)
- ``/api/sessions``           lista de sesiones del histórico
- ``/api/session/<id>``       JSON de la sesión
- ``/audio/<id>``             audio, con peticiones por rangos (para saltar a cualquier punto)
- ``/peaks/<id>``             forma de onda resumida (1 byte por pico)

`<id>` es el nombre base de los archivos (``session-20261126-213000``); solo se sirven archivos
del histórico con ese nombre.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

log = logging.getLogger(__name__)

PAGE = Path(__file__).with_name("review.html")
ID_RE = re.compile(r"^session-[0-9A-Za-z_-]+$")
AUDIO_TYPES = {".flac": "audio/flac", ".wav": "audio/wav", ".ogg": "audio/ogg", ".mp3": "audio/mpeg"}
CHUNK = 256 * 1024


def session_list(history_dir: Path) -> list[dict]:
    out = []
    for path in sorted(history_dir.glob("session-*.json"), reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        audio = data.get("audio") or {}
        out.append({
            "id": path.stem,
            "started_at": data.get("started_at"),
            "ended_at": data.get("ended_at"),
            "source": data.get("source"),
            "tracks": len(data.get("tracks", [])),
            "transitions": len(data.get("transitions", [])),
            "has_audio": bool(audio.get("file") and (history_dir / audio["file"]).exists()),
            "duration_s": audio.get("duration_s"),
        })
    return out


class _Handler(BaseHTTPRequestHandler):
    server_version = "rkbx-review"
    history_dir: Path = Path("history")

    def log_message(self, fmt, *args):  # noqa: D401 - silencio en consola
        log.debug("review: " + fmt, *args)

    # -- utilidades --------------------------------------------------
    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _session(self, sid: str) -> tuple[Path, dict] | None:
        if not ID_RE.match(sid):
            return None
        path = self.history_dir / f"{sid}.json"
        if not path.is_file():
            return None
        try:
            return path, json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return None

    def _file_for(self, sid: str, key: str) -> Path | None:
        found = self._session(sid)
        if not found:
            return None
        name = ((found[1].get("audio") or {}).get(key)) or ""
        if not name or "/" in name or "\\" in name or name.startswith("."):
            return None
        p = self.history_dir / name
        return p if p.is_file() else None

    def _serve_file(self, path: Path, ctype: str) -> None:
        size = path.stat().st_size
        start, end = 0, size - 1
        code = 200
        rng = self.headers.get("Range")
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)$", rng.strip())
            if not m or (not m.group(1) and not m.group(2)):
                self._send(416, b"", "text/plain", {"Content-Range": f"bytes */{size}"})
                return
            if m.group(1):
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else size - 1
            else:                                   # bytes=-N: los últimos N
                start = max(0, size - int(m.group(2)))
            end = min(end, size - 1)
            if start > end or start >= size:
                self._send(416, b"", "text/plain", {"Content-Range": f"bytes */{size}"})
                return
            code = 206
        length = end - start + 1
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        try:
            with path.open("rb") as f:
                f.seek(start)
                left = length
                while left > 0:
                    chunk = f.read(min(CHUNK, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (ConnectionError, OSError):
            pass   # el navegador corta la descarga al saltar a otro punto: normal

    # -- rutas -------------------------------------------------------
    def do_HEAD(self):  # noqa: N802
        self.do_GET()

    def do_GET(self):  # noqa: N802
        parts = [unquote(p) for p in urlparse(self.path).path.split("/") if p]
        try:
            if not parts:
                self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            elif parts == ["api", "sessions"]:
                self._json(session_list(self.history_dir))
            elif len(parts) == 3 and parts[:2] == ["api", "session"]:
                found = self._session(parts[2])
                self._json(found[1]) if found else self._json({"error": "no existe"}, 404)
            elif len(parts) == 2 and parts[0] == "audio":
                p = self._file_for(parts[1], "file")
                if p is None:
                    self._send(404, b"sin audio", "text/plain")
                else:
                    ctype = AUDIO_TYPES.get(p.suffix.lower()) or mimetypes.guess_type(p.name)[0] \
                        or "application/octet-stream"
                    self._serve_file(p, ctype)
            elif len(parts) == 2 and parts[0] == "peaks":
                p = self._file_for(parts[1], "peaks_file")
                if p is None:
                    self._send(404, b"", "application/octet-stream")
                else:
                    self._send(200, p.read_bytes(), "application/octet-stream")
            else:
                self._send(404, b"no encontrado", "text/plain")
        except (ConnectionError, OSError):
            pass


class _QuietServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):  # navegador que corta: no es un error
        log.debug("review: conexión cortada por %s", client_address)


class ReviewServer:
    def __init__(self, history_dir: str | Path, host: str = "127.0.0.1", port: int = 8766):
        self.history_dir = Path(history_dir)
        self.host, self.port = host, port
        self._httpd: ThreadingHTTPServer | None = None
        self.external = False     # el puerto ya lo usaba otra copia de la app: se usa esa

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"

    def start(self) -> None:
        handler = type("Handler", (_Handler,), {"history_dir": self.history_dir})
        try:
            self._httpd = _QuietServer((self.host, self.port), handler)
        except OSError:
            self.external = True
            log.info("Puerto %s ocupado: se usa la página de revisión que ya está abierta", self.port)
            return
        self.port = self._httpd.server_address[1]
        threading.Thread(target=self._httpd.serve_forever, name="review-http", daemon=True).start()

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
