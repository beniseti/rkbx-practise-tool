"""Simulador del ESP32 en el navegador.

- Escucha UDP (como el ESP32) en `lights.simulator_port` y reenvía cada comando a la página
  `simulator.html` mediante Server-Sent Events (`/events`).
- Sirve la página en `http://127.0.0.1:<http_port>/`.
- `POST /send` (JSON) inyecta un comando como si llegara por UDP (botones de prueba de la página).

Solo usa la biblioteca estándar (sin dependencias nuevas).
"""

from __future__ import annotations

import json
import logging
import queue
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

log = logging.getLogger(__name__)

PAGE = Path(__file__).with_name("simulator.html")


class LightSimulator:
    def __init__(self, udp_host: str = "127.0.0.1", udp_port: int = 4211,
                 http_host: str = "127.0.0.1", http_port: int = 8765, leds: int = 60,
                 effects: dict | None = None):
        self.udp_addr = (udp_host, udp_port)
        self.http_addr = (http_host, http_port)
        self.leds = leds
        self.effects = effects or {}
        self._clients: list[queue.Queue] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.received: list[dict] = []
        self._udp: socket.socket | None = None
        self._http: ThreadingHTTPServer | None = None

    # --- difusión a las páginas abiertas -------------------------------------
    def publish(self, cmd: dict, origin: str = "udp") -> None:
        msg = {"t": time.time(), "origin": origin, "cmd": cmd}
        self.received.append(msg)
        del self.received[:-200]
        with self._lock:
            for q in list(self._clients):
                q.put(msg)

    def _subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self._lock:
            self._clients.append(q)
        return q

    def _unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._clients:
                self._clients.remove(q)

    # --- UDP -------------------------------------------------------------------
    def _udp_loop(self) -> None:
        assert self._udp is not None
        while not self._stop.is_set():
            try:
                data, _ = self._udp.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                cmd = json.loads(data.decode("utf-8"))
                if not isinstance(cmd, dict):
                    raise ValueError("no es un objeto JSON")
            except ValueError as exc:
                cmd = {"fx": "?", "error": f"comando no válido: {exc}", "raw": data[:200].decode("utf-8", "replace")}
            self.publish(cmd)

    # --- HTTP ------------------------------------------------------------------
    def _handler(self):
        sim = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):  # silencio en consola
                log.debug("http: " + fmt, *args)

            def _send(self, code: int, body: bytes, ctype: str) -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):  # noqa: N802
                if self.path in ("/", "/index.html"):
                    self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
                elif self.path == "/config":
                    body = json.dumps({"leds": sim.leds, "effects": sim.effects,
                                       "udp": f"{sim.udp_addr[0]}:{sim.udp_addr[1]}"}).encode()
                    self._send(200, body, "application/json")
                elif self.path == "/events":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Connection", "keep-alive")
                    self.end_headers()
                    q = sim._subscribe()
                    try:
                        self.wfile.write(b": conectado\n\n")
                        self.wfile.flush()
                        while not sim._stop.is_set():
                            try:
                                msg = q.get(timeout=10)
                                payload = json.dumps(msg, ensure_ascii=False)
                                self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                            except queue.Empty:
                                self.wfile.write(b": ping\n\n")
                            self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                        pass
                    finally:
                        sim._unsubscribe(q)
                else:
                    self._send(404, b"no encontrado", "text/plain")

            def do_POST(self):  # noqa: N802
                if self.path != "/send":
                    self._send(404, b"no encontrado", "text/plain")
                    return
                length = int(self.headers.get("Content-Length", "0") or 0)
                try:
                    cmd = json.loads(self.rfile.read(length) or b"{}")
                except ValueError:
                    self._send(400, b"JSON no valido", "text/plain")
                    return
                sim.publish(cmd, origin="prueba")
                self._send(200, b"ok", "text/plain")

        return Handler

    # --- ciclo de vida ---------------------------------------------------------
    def start(self) -> None:
        self._udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._udp.bind(self.udp_addr)
        self._udp.settimeout(0.5)
        self.udp_addr = self._udp.getsockname()
        self._http = ThreadingHTTPServer(self.http_addr, self._handler())
        self._http.daemon_threads = True
        self.http_addr = self._http.server_address[:2]
        threading.Thread(target=self._udp_loop, name="sim-udp", daemon=True).start()
        threading.Thread(target=self._http.serve_forever, name="sim-http", daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://{self.http_addr[0]}:{self.http_addr[1]}/"

    def stop(self) -> None:
        self._stop.set()
        if self._http is not None:
            self._http.shutdown()
            self._http.server_close()
        if self._udp is not None:
            self._udp.close()
