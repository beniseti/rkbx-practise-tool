"""Generación del orden de una sesión de práctica.

Algoritmo (ideas de keyid —cadena— y djkr8 —puntuación y optimización—):

1. Se calcula una matriz de costes A→B (BPM + relación Camelot; ver
   :func:`compatibility.transition`). Las transiciones incompatibles se
   penalizan mucho pero no se prohíben: si la playlist no permite una cadena
   perfecta, la sesión sigue completa y el salto queda marcado.
2. Cadena voraz (vecino más cercano) desde varios inicios; se queda la mejor.
3. Mejora local 2-opt (inversión de tramos, coste direccional) y Or-opt
   (mover tramos de 1-3 temas), limitada por `optimize_time_budget_s`.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field

from ..config import SessionConfig
from ..library.models import Track
from .compatibility import TransitionInfo, transition


@dataclass(frozen=True)
class SessionEntry:
    track: Track
    from_previous: TransitionInfo | None  # None en el primer tema


@dataclass
class GeneratedSession:
    entries: list[SessionEntry]
    excluded: list[tuple[Track, str]] = field(default_factory=list)  # (tema, motivo)

    @property
    def tracks(self) -> list[Track]:
        return [e.track for e in self.entries]

    @property
    def incompatible_jumps(self) -> int:
        return sum(1 for e in self.entries if e.from_previous and not e.from_previous.compatible)


def split_usable(tracks: list[Track]) -> tuple[list[Track], list[tuple[Track, str]]]:
    """Separa los temas sin BPM o sin tonalidad (no analizados)."""
    usable, excluded = [], []
    for t in tracks:
        if t.bpm is None and t.camelot is None:
            excluded.append((t, "sin BPM ni tonalidad (¿no analizado?)"))
        elif t.bpm is None:
            excluded.append((t, "sin BPM"))
        elif t.camelot is None:
            excluded.append((t, "sin tonalidad" if not t.key_raw else f"tonalidad no reconocida: {t.key_raw}"))
        else:
            usable.append(t)
    return usable, excluded


class CostMatrix:
    def __init__(self, tracks: list[Track], cfg: SessionConfig, rng: random.Random | None = None):
        self.tracks = tracks
        self.cfg = cfg
        n = len(tracks)
        self.c = [[0.0] * n for _ in range(n)]
        # Variedad: ruido aleatorio SOLO en las transiciones compatibles, para que cada
        # sesión generada sea distinta sin empeorar la compatibilidad.
        noise = max(0.0, cfg.variety) * 1.5 if rng is not None else 0.0
        for i in range(n):
            for j in range(n):
                if i != j:
                    info = transition(tracks[i], tracks[j], cfg)
                    self.c[i][j] = info.cost + (rng.uniform(0.0, noise) if noise and info.compatible else 0.0)

    def path_cost(self, path: list[int]) -> float:
        return sum(self.c[path[k]][path[k + 1]] for k in range(len(path) - 1))


def _greedy(cm: CostMatrix, start: int, candidates: set[int]) -> list[int]:
    path = [start]
    remaining = set(candidates)
    remaining.discard(start)
    cur = start
    row = cm.c
    while remaining:
        # Desempate determinista por índice.
        nxt = min(remaining, key=lambda j: (row[cur][j], j))
        path.append(nxt)
        remaining.discard(nxt)
        cur = nxt
    return path


def _two_opt(cm: CostMatrix, path: list[int], fixed_start: bool, deadline: float) -> list[int]:
    """2-opt con coste direccional: invertir path[i..j] invierte el sentido de sus aristas."""
    c = cm.c
    n = len(path)
    if n < 3:
        return path
    improved = True
    lo = 1 if fixed_start else 0
    while improved and time.monotonic() < deadline:
        improved = False
        # Prefijos de coste hacia delante (F) y hacia atrás (R).
        F = [0.0] * n
        R = [0.0] * n
        for k in range(1, n):
            F[k] = F[k - 1] + c[path[k - 1]][path[k]]
            R[k] = R[k - 1] + c[path[k]][path[k - 1]]
        for i in range(lo, n - 1):
            if time.monotonic() >= deadline:
                break
            for j in range(i + 1, n):
                old = F[j] - F[i]
                new = R[j] - R[i]
                if i > 0:
                    old += c[path[i - 1]][path[i]]
                    new += c[path[i - 1]][path[j]]
                if j < n - 1:
                    old += c[path[j]][path[j + 1]]
                    new += c[path[i]][path[j + 1]]
                if new < old - 1e-9:
                    path[i:j + 1] = reversed(path[i:j + 1])
                    improved = True
                    break
            if improved:
                break
    return path


def _or_opt(cm: CostMatrix, path: list[int], fixed_start: bool, deadline: float) -> bool:
    """Mueve un tramo de 1-3 temas a otra posición (sin invertirlo). True si mejora."""
    c = cm.c
    n = len(path)

    def edge(a: int | None, b: int | None) -> float:
        return 0.0 if a is None or b is None else c[a][b]

    lo = 1 if fixed_start else 0
    for k in (1, 2, 3):
        for i in range(lo, n - k + 1):
            if time.monotonic() >= deadline:
                return False
            seg_first, seg_last = path[i], path[i + k - 1]
            prev = path[i - 1] if i > 0 else None
            nxt = path[i + k] if i + k < n else None
            removal_gain = edge(prev, seg_first) + edge(seg_last, nxt) - edge(prev, nxt)
            rest = path[:i] + path[i + k:]
            # Insertar entre rest[p-1] y rest[p]; p=0 es al principio.
            for p in range(0 if not fixed_start else 1, len(rest) + 1):
                if p == i:
                    continue
                a = rest[p - 1] if p > 0 else None
                b = rest[p] if p < len(rest) else None
                insert_cost = edge(a, seg_first) + edge(seg_last, b) - edge(a, b)
                if insert_cost < removal_gain - 1e-9:
                    path[:] = rest[:p] + path[i:i + k] + rest[p:]
                    return True
    return False


def _improve(cm: CostMatrix, path: list[int], fixed_start: bool, deadline: float) -> list[int]:
    while time.monotonic() < deadline:
        before = cm.path_cost(path)
        path = _two_opt(cm, path, fixed_start, deadline)
        while time.monotonic() < deadline and _or_opt(cm, path, fixed_start, deadline):
            pass
        if cm.path_cost(path) >= before - 1e-9:
            break
    return path


def order_tracks(tracks: list[Track], cfg: SessionConfig, start: Track | None = None,
                 rng: random.Random | None = None) -> list[Track]:
    """Ordena `tracks` en una cadena suave. Si `start` se da, la cadena empieza por él
    (puede no pertenecer a `tracks`: en ese caso se usa solo como ancla y no se incluye)."""
    rng = rng or random.Random(0)
    if not tracks:
        return []
    pool = list(tracks)
    anchor_external = start is not None and all(t.id != start.id for t in pool)
    if anchor_external:
        pool = [start] + pool
    cm = CostMatrix(pool, cfg, rng)
    n = len(pool)
    all_idx = set(range(n))

    if start is not None:
        start_idx = next(i for i, t in enumerate(pool) if t.id == start.id)
        starts = [start_idx]
    else:
        by_bpm = sorted(range(n), key=lambda i: (pool[i].bpm or 0.0, i))
        if n <= 40:
            starts = list(range(n))
        else:
            sample = set(by_bpm[:10])
            sample.update(rng.sample(range(n), min(20, n)))
            starts = sorted(sample)

    paths = sorted(((cm.path_cost(p), p) for p in (_greedy(cm, s, all_idx) for s in starts)),
                   key=lambda x: x[0])
    best_path = paths[0][1]
    if cfg.variety > 0 and start is None and len(paths) > 1:
        # Con variedad, el inicio se elige al azar entre las mejores cadenas.
        top = [p for c, p in paths if c <= paths[0][0] * (1 + cfg.variety) + 1e-9][:5]
        best_path = rng.choice(top)
    deadline = time.monotonic() + max(cfg.optimize_time_budget_s, 0.0)
    best_path = _improve(cm, best_path, fixed_start=start is not None, deadline=deadline)
    ordered = [pool[i] for i in best_path]
    if anchor_external:
        ordered = ordered[1:]
    return ordered


def build_entries(ordered: list[Track], cfg: SessionConfig, previous: Track | None = None) -> list[SessionEntry]:
    entries = []
    prev = previous
    for t in ordered:
        entries.append(SessionEntry(t, transition(prev, t, cfg) if prev else None))
        prev = t
    return entries


def generate_session(tracks: list[Track], cfg: SessionConfig, start: Track | None = None,
                     rng: random.Random | None = None) -> GeneratedSession:
    usable, excluded = split_usable(tracks)
    ordered = order_tracks(usable, cfg, start=start, rng=rng)
    return GeneratedSession(build_entries(ordered, cfg), excluded)
