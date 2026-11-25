"""Detecciones: transiciones, fader cuts y bass swaps."""

from .bass_swap import BassSwapDetector
from .cuts import FaderCutDetector
from .frame import DeckFrame, Frame, build_frame
from .transitions import TransitionDetector
from .volume import LeadDeckTracker, crossfader_factor, effective_volume

__all__ = [
    "BassSwapDetector", "FaderCutDetector", "DeckFrame", "Frame", "build_frame",
    "TransitionDetector", "LeadDeckTracker", "crossfader_factor", "effective_volume",
]
