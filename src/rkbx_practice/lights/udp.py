"""Envío de comandos por UDP al ESP32 (y al simulador). Sin esperar respuesta; nunca bloquea."""

from __future__ import annotations

import logging
import socket

from .commands import LightCommand

log = logging.getLogger(__name__)


class UdpSender:
    def __init__(self, host: str, port: int):
        self.targets: list[tuple[str, int]] = [(host, int(port))]
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setblocking(False)
        self.sent = 0
        self.errors = 0

    @property
    def address(self) -> tuple[str, int]:
        return self.targets[0]

    def add_target(self, host: str, port: int) -> None:
        if (host, int(port)) not in self.targets:
            self.targets.append((host, int(port)))

    def remove_target(self, host: str, port: int) -> None:
        if (host, int(port)) in self.targets[1:]:
            self.targets.remove((host, int(port)))

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
        self._sock.close()
