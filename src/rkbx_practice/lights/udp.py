"""Envío de comandos al ESP32 por UDP (sin esperar respuesta; nunca bloquea el bucle en vivo)."""

from __future__ import annotations

import logging
import socket

from .commands import LightCommand

log = logging.getLogger(__name__)


class UdpSender:
    def __init__(self, host: str, port: int):
        self.address = (host, int(port))
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setblocking(False)
        self.sent = 0
        self.errors = 0

    def send(self, cmd: LightCommand) -> bool:
        try:
            self._sock.sendto(cmd.to_json(), self.address)
            self.sent += 1
            return True
        except OSError as exc:   # sin Wi-Fi del ESP32, red caída…: se avisa y se sigue
            self.errors += 1
            if self.errors in (1, 10) or self.errors % 100 == 0:
                log.warning("No se pudo enviar a %s:%s (%s)", *self.address, exc)
            return False

    def close(self) -> None:
        self._sock.close()
