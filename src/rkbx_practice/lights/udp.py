"""Envío de comandos por UDP al ESP32 (y al simulador). Sin esperar respuesta; nunca bloquea.

El ESP32 puede estar en la Wi-Fi de casa o en su propia Wi-Fi (192.168.4.1), así que
`lights.esp32_ip` admite una lista de direcciones y nombres (``rkbx-luces.local``, mDNS). Los
nombres se resuelven en segundo plano (nunca en el bucle en vivo) y se vuelven a resolver cada
`RESOLVE_EVERY_S` por si cambia la red. Se envía a todas las direcciones distintas resueltas.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import threading
import time

from .commands import LightCommand

log = logging.getLogger(__name__)

RESOLVE_EVERY_S = 30.0


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _resolve(host: str, port: int) -> str | None:
    try:
        info = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_DGRAM)
        return info[0][4][0] if info else None
    except OSError:
        return None


class UdpSender:
    def __init__(self, host: str | list[str], port: int, *, resolver=_resolve, background: bool = True):
        hosts = [host] if isinstance(host, str) else [str(h) for h in host]
        self.port = int(port)
        self.hosts = [h for h in hosts if h]
        self._resolver = resolver
        self._resolved: dict[str, str | None] = {h: (h if _is_ip(h) else None) for h in self.hosts}
        self._extra: list[tuple[str, int]] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setblocking(False)
        self.sent = 0
        self.errors = 0
        names = [h for h in self.hosts if not _is_ip(h)]
        if names:
            if background:
                threading.Thread(target=self._resolve_loop, args=(names,), daemon=True,
                                 name="lights-resolve").start()
            else:
                self.resolve_now()

    # -- resolución --------------------------------------------------------
    def resolve_now(self) -> None:
        for h in self.hosts:
            if not _is_ip(h):
                ip = self._resolver(h, self.port)
                with self._lock:
                    if ip != self._resolved.get(h):
                        log.info("Luces: %s → %s", h, ip or "no encontrado")
                    self._resolved[h] = ip

    def _resolve_loop(self, names: list[str]) -> None:
        while not self._stop.is_set():
            self.resolve_now()
            self._stop.wait(RESOLVE_EVERY_S)

    # -- destinos ----------------------------------------------------------
    @property
    def targets(self) -> list[tuple[str, int]]:
        """Direcciones a las que se envía ahora mismo (sin repetidas)."""
        out: list[tuple[str, int]] = []
        with self._lock:
            for h in self.hosts:
                ip = self._resolved.get(h)
                if ip and (ip, self.port) not in out:
                    out.append((ip, self.port))
            for t in self._extra:
                if t not in out:
                    out.append(t)
        return out

    @property
    def address(self) -> tuple[str, int]:
        t = self.targets
        return t[0] if t else (self.hosts[0] if self.hosts else "", self.port)

    def describe(self) -> str:
        """Texto para la ventana: «rkbx-luces.local=192.168.1.50:4210, 192.168.4.1:4210»."""
        parts = []
        with self._lock:
            for h in self.hosts:
                ip = self._resolved.get(h)
                if _is_ip(h):
                    parts.append(f"{h}:{self.port}")
                else:
                    parts.append(f"{h}={ip}:{self.port}" if ip else f"{h} (buscando…)")
            parts += [f"{h}:{p}" for h, p in self._extra]
        return ", ".join(parts)

    def add_target(self, host: str, port: int) -> None:
        with self._lock:
            if (host, int(port)) not in self._extra:
                self._extra.append((host, int(port)))

    def remove_target(self, host: str, port: int) -> None:
        with self._lock:
            if (host, int(port)) in self._extra:
                self._extra.remove((host, int(port)))

    # -- envío -------------------------------------------------------------
    def send(self, cmd: LightCommand) -> bool:
        """True si llegó a salir hacia al menos un destino."""
        data = cmd.to_json()
        ok = False
        for addr in self.targets:
            try:
                self._sock.sendto(data, addr)
                ok = True
            except OSError as exc:   # sin Wi-Fi del ESP32, red caída…: se avisa y se sigue
                self.errors += 1
                if self.errors in (1, 10) or self.errors % 100 == 0:
                    log.warning("No se pudo enviar a %s:%s (%s)", *addr, exc)
        if ok:
            self.sent += 1
        return ok

    def close(self) -> None:
        self._stop.set()
        self._sock.close()
