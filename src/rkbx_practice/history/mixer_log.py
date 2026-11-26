"""Registro del mezclador durante la grabación: faders y EQ (HI/MID/LOW) de cada deck en el tiempo.

Pedido del usuario: ver en la revisión «el % de EQs que se están mezclando en cada momento» para
saber cómo mejorar. Se guarda en `<sesión>.mix.json`, aparte del JSON de la sesión (puede ocupar
unos MB en una sesión larga).

Para no guardar 50 muestras por segundo sin necesidad, se guarda una muestra solo si algún valor
cambia (más de `eps`) o han pasado `keepalive_s`, y como mucho una cada `min_interval_s`.
Formato: `columns` = ["t", "d1_fader", "d1_high", "d1_mid", "d1_low", "d1_playing", "d2_fader", …],
`samples` = filas con `t` en segundos desde el inicio de la sesión; null = sin dato.
"""

from __future__ import annotations

from datetime import datetime

FIELDS = ("fader", "eq_high", "eq_mid", "eq_low", "playing")
SHORT = {"fader": "fader", "eq_high": "high", "eq_mid": "mid", "eq_low": "low", "playing": "playing"}


class MixerLog:
    def __init__(self, decks: int, started: datetime, min_interval_s: float = 0.05,
                 keepalive_s: float = 2.0, eps: float = 0.004):
        self.decks = decks
        self.started = started
        self.min_interval_s = min_interval_s
        self.keepalive_s = keepalive_s
        self.eps = eps
        self.samples: list[list] = []
        self._last_row: list | None = None
        self._last_t = -1e9

    @property
    def columns(self) -> list[str]:
        return ["t"] + [f"d{n}_{SHORT[f]}" for n in range(1, self.decks + 1) for f in FIELDS]

    def _row(self, snapshot) -> list:
        row = []
        for n in range(1, self.decks + 1):
            d = snapshot.deck(n)
            for f in FIELDS:
                v = getattr(d, f, None) if d is not None else None
                if f == "playing":
                    row.append(None if v is None else int(bool(v)))
                else:
                    row.append(None if v is None else round(float(v), 3))
        return row

    def _changed(self, row: list) -> bool:
        if self._last_row is None:
            return True
        for a, b in zip(row, self._last_row):
            if (a is None) != (b is None):
                return True
            if a is not None and abs(a - b) > self.eps:
                return True
        return False

    def add(self, snapshot) -> None:
        t = (snapshot.wall - self.started).total_seconds()
        if t < 0 or t - self._last_t < self.min_interval_s:
            return
        row = self._row(snapshot)
        if not self._changed(row) and t - self._last_t < self.keepalive_s:
            return
        self.samples.append([round(t, 3)] + row)
        self._last_row, self._last_t = row, t

    def cut_after(self, t_end: float) -> None:
        self.samples = [s for s in self.samples if s[0] <= t_end]

    def to_dict(self) -> dict:
        return {"format_version": 1, "started_at": self.started.isoformat(timespec="milliseconds"),
                "decks": self.decks, "columns": self.columns, "samples": self.samples}
