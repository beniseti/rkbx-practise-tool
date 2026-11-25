"""Motor de sesiones: orden, filtros Camelot/BPM, comodines y recálculo."""

from .compatibility import BpmMatch, TransitionInfo, bpm_match, is_compatible, transition
from .engine import GeneratedSession, SessionEntry, generate_session, order_tracks, split_usable
from .practice import PracticeSession, Suggestion

__all__ = [
    "BpmMatch", "TransitionInfo", "bpm_match", "is_compatible", "transition",
    "GeneratedSession", "SessionEntry", "generate_session", "order_tracks", "split_usable",
    "PracticeSession", "Suggestion",
]
