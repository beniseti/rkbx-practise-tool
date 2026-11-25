"""Compatibilidad entre dos temas (BPM y Camelot) y coste de transición."""

from __future__ import annotations

from dataclasses import dataclass

from ..config import SessionConfig
from ..library.camelot import ALLOWED_RELATIONS, RELATION_COST, KeyRelation, relation
from ..library.models import Track


@dataclass(frozen=True)
class BpmMatch:
    diff_pct: float      # diferencia relativa (en %) tras aplicar el multiplicador
    multiplier: float    # 1.0, 2.0 (B ≈ doble de A) o 0.5 (B ≈ mitad de A)
    compatible: bool


@dataclass(frozen=True)
class TransitionInfo:
    bpm: BpmMatch
    key: KeyRelation | None   # None si Camelot está desactivado o falta la tonalidad
    compatible: bool
    cost: float


def bpm_match(a: float, b: float, tolerance_pct: float, allow_half_double: bool) -> BpmMatch:
    multipliers = (1.0, 2.0, 0.5) if allow_half_double else (1.0,)
    best = None
    for m in multipliers:
        target = a * m
        diff = abs(b - target) / target * 100.0
        if best is None or diff < best.diff_pct - 1e-9:
            best = BpmMatch(diff_pct=diff, multiplier=m, compatible=diff <= tolerance_pct + 1e-9)
    assert best is not None
    return best


def transition(a: Track, b: Track, cfg: SessionConfig) -> TransitionInfo:
    """Información y coste de pasar de A a B. Menor coste = transición más suave."""
    if a.bpm is None or b.bpm is None:
        bm = BpmMatch(diff_pct=float("inf"), multiplier=1.0, compatible=False)
    else:
        bm = bpm_match(a.bpm, b.bpm, cfg.bpm_tolerance_pct, cfg.allow_half_double_tempo)

    key_rel: KeyRelation | None = None
    key_ok = True
    if cfg.camelot_enabled:
        if a.camelot is None or b.camelot is None:
            key_ok = False
        else:
            key_rel = relation(a.camelot, b.camelot)
            key_ok = key_rel in ALLOWED_RELATIONS[cfg.camelot_strictness]

    compatible = bm.compatible and key_ok
    tol = max(cfg.bpm_tolerance_pct, 0.1)
    bpm_cost = min(bm.diff_pct, 100.0) / tol           # 0..1 dentro de la tolerancia
    if bm.multiplier != 1.0:
        bpm_cost += 0.75                                 # mitad/doble: algo menos natural
    key_cost = RELATION_COST[key_rel] if key_rel is not None else 0.0
    cost = bpm_cost + key_cost
    if not compatible:
        cost += 20.0
    return TransitionInfo(bpm=bm, key=key_rel, compatible=compatible, cost=cost)


def is_compatible(a: Track, b: Track, cfg: SessionConfig) -> bool:
    return transition(a, b, cfg).compatible
