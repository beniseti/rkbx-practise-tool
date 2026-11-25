"""Volumen efectivo de cada deck y deck que "manda" (para luces en el futuro).

`volumen = fader_canal` y, si el crossfader está activado,
`fader_canal × factor_crossfader` según el lado asignado al deck (A, B o THRU).
Curva del crossfader: lineal hasta el centro (el lado propio no se atenúa
hasta pasar el centro). Si hace falta otra curva, se cambia solo aquí.
"""

from __future__ import annotations

from ..config import MixerConfig
from ..state import DeckState, MixerState


def crossfader_factor(side: str, position: float | None) -> float:
    if position is None or side.upper() == "THRU":
        return 1.0
    x = min(1.0, max(0.0, position))
    if side.upper() == "A":
        return 1.0 if x <= 0.5 else (1.0 - x) * 2.0
    if side.upper() == "B":
        return 1.0 if x >= 0.5 else x * 2.0
    return 1.0


def effective_volume(deck: DeckState, mixer: MixerState, cfg: MixerConfig) -> float | None:
    if deck.fader is None:
        return None
    vol = deck.fader
    if cfg.crossfader_enabled:
        side = cfg.crossfader_assign.get(str(deck.deck), "THRU")
        vol *= crossfader_factor(side, mixer.crossfader)
    return vol


class LeadDeckTracker:
    """Deck con más volumen efectivo; en empate, el último que entró (superó el umbral)."""

    def __init__(self, threshold: float, tie_epsilon: float = 1e-3):
        self.threshold = threshold
        self.tie_epsilon = tie_epsilon
        self._entered_at: dict[int, float] = {}

    def update(self, t: float, volumes: dict[int, float | None]) -> int | None:
        for d, v in volumes.items():
            if v is not None and v > self.threshold:
                self._entered_at.setdefault(d, t)
            else:
                self._entered_at.pop(d, None)
        audible = {d: v for d, v in volumes.items() if v is not None and v > self.threshold}
        if not audible:
            return None
        top = max(audible.values())
        tied = [d for d, v in audible.items() if top - v <= self.tie_epsilon]
        return max(tied, key=lambda d: (self._entered_at.get(d, 0.0), d))
