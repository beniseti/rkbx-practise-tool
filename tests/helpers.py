"""Utilidades para simular sesiones en vivo en los tests."""

from datetime import datetime, timedelta

from rkbx_practice.config import AppConfig, MixerConfig
from rkbx_practice.detection import build_frame
from rkbx_practice.state import DeckState, LiveSnapshot, MixerState

T0 = datetime(2026, 1, 1, 22, 0, 0)


def snap(t, decks, crossfader=None):
    return LiveSnapshot(t=t, wall=T0 + timedelta(seconds=t), decks=tuple(decks),
                        mixer=MixerState(crossfader))


def deck(n, fader=1.0, track="A", bpm=120.0, pos=None, eq=0.75, playing=True, path=None):
    return DeckState(deck=n, track_id=track, track_path=path or (f"/PIONEER/USBANLZ/x/{track}/ANLZ0000.DAT" if track else None),
                     bpm=bpm, position_s=pos, fader=fader, eq_low=eq, playing=playing)


def timeline(fn, duration, dt=0.02):
    """fn(t) -> lista de DeckState. Devuelve frames."""
    n = int(round(duration / dt))
    return [snap(i * dt, fn(i * dt)) for i in range(n + 1)]


def frames(snaps, mixer=None):
    mixer = mixer or MixerConfig()
    return [build_frame(s, mixer) for s in snaps]
