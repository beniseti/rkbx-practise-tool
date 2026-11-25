import pytest

from rkbx_practice.config import DetectionConfig, MixerConfig
from rkbx_practice.detection import (BassSwapDetector, FaderCutDetector, LeadDeckTracker,
                                     TransitionDetector, crossfader_factor, effective_volume)
from rkbx_practice.library.anlz import BeatGrid
from rkbx_practice.state import DeckState, MixerState

from helpers import deck, frames, timeline


def ramp(t, t0, t1, v0, v1):
    if t <= t0:
        return v0
    if t >= t1:
        return v1
    return v0 + (v1 - v0) * (t - t0) / (t1 - t0)


def run(detector, fs):
    out = []
    for f in fs:
        out += detector.update(f)
    return out


CFG = DetectionConfig()


# --- volumen ---------------------------------------------------------
def test_crossfader():
    assert crossfader_factor("A", 0.0) == 1.0
    assert crossfader_factor("A", 0.5) == 1.0
    assert crossfader_factor("A", 1.0) == 0.0
    assert crossfader_factor("B", 0.25) == pytest.approx(0.5)
    assert crossfader_factor("THRU", 1.0) == 1.0
    d = DeckState(deck=1, fader=0.8)
    on = MixerConfig(crossfader_enabled=True)
    assert effective_volume(d, MixerState(1.0), on) == 0.0
    assert effective_volume(d, MixerState(1.0), MixerConfig()) == 0.8   # desactivado por defecto


def test_lead_deck():
    lead = LeadDeckTracker(0.05)
    assert lead.update(0.0, {1: 1.0, 2: 0.0}) == 1
    assert lead.update(1.0, {1: 1.0, 2: 0.5}) == 1
    assert lead.update(2.0, {1: 1.0, 2: 1.0}) == 2   # empate: el último que entró
    assert lead.update(3.0, {1: 0.0, 2: 0.0}) is None


# --- transiciones ----------------------------------------------------
def test_blend_transition_ends_when_outgoing_reaches_zero():
    # A suena; B entra en t=10 y sube hasta 1 en t=14; A baja de 1 a 0 entre t=16 y t=26.
    fs = frames(timeline(lambda t: [
        deck(1, fader=ramp(t, 16, 26, 1, 0), track="A"),
        deck(2, fader=ramp(t, 10, 14, 0, 1), track="B"),
    ], 35))
    out = run(TransitionDetector(CFG), fs)
    assert len(out) == 1
    tr = out[0]
    assert (tr.out_deck, tr.in_deck) == (1, 2)
    assert tr.t_start == pytest.approx(10.2, abs=0.03)   # B supera el umbral 0.05
    assert tr.t_end == pytest.approx(25.5, abs=0.03)     # A cae bajo el umbral
    assert tr.in_track_id == "B" and tr.out_track_id == "A"


def test_stability_end_when_outgoing_never_reaches_zero():
    # A se queda en 0.3; B sube a 1 en t=12 y lo supera de forma estable.
    fs = frames(timeline(lambda t: [
        deck(1, fader=ramp(t, 12, 13, 1, 0.3), track="A"),
        deck(2, fader=ramp(t, 10, 12, 0, 1), track="B"),
    ], 25))
    out = run(TransitionDetector(CFG), fs)
    assert len(out) == 1
    # El cruce: B > A ocurre alrededor de t=12.4; el fin se fecha ahí.
    assert 11.5 < out[0].t_end < 13.0


def test_preview_touch_is_ignored():
    fs = frames(timeline(lambda t: [
        deck(1, fader=1.0, track="A"),
        deck(2, fader=0.6 if 5 <= t < 5.8 else 0.0, track="B"),
    ], 12))
    assert run(TransitionDetector(CFG), fs) == []


def test_hard_cut_is_short_transition():
    # Cambio seco: A cae y B sube casi a la vez en t=8.
    fs = frames(timeline(lambda t: [
        deck(1, fader=1.0 if t < 8.0 else 0.0, track="A"),
        deck(2, fader=1.0 if t >= 8.04 else 0.0, track="B"),
    ], 14))
    out = run(TransitionDetector(CFG), fs)
    assert len(out) == 1
    assert out[0].duration_s < 0.5
    assert (out[0].out_deck, out[0].in_deck) == (1, 2)


def test_stopped_deck_with_fader_up_ends_transition():
    fs = frames(timeline(lambda t: [
        deck(1, fader=1.0, track="A", playing=t < 20),
        deck(2, fader=ramp(t, 10, 11, 0, 0.9), track="B"),
    ], 25))
    out = run(TransitionDetector(CFG.__class__(transition_stable_s=60)), fs)
    assert len(out) == 1
    assert out[0].t_end == pytest.approx(20.0, abs=0.1)


def test_fader_cut_during_play_does_not_end_transition():
    # Durante la mezcla, A hace un corte rápido (0.2 s) y vuelve: no termina la transición.
    fs = frames(timeline(lambda t: [
        deck(1, fader=0.0 if 15 <= t < 15.2 or t >= 24 else 1.0, track="A"),
        deck(2, fader=ramp(t, 10, 11, 0, 0.8), track="B"),
    ], 30))
    out = run(TransitionDetector(CFG.__class__(transition_stable_s=60)), fs)
    assert len(out) == 1
    assert out[0].t_end == pytest.approx(24.0, abs=0.1)


# --- fader cuts ------------------------------------------------------
def test_fader_cut_burst_and_on_beat():
    grid = BeatGrid.uniform(120.0)   # un beat cada 0.5 s
    cut_times = [10.0, 10.5, 11.0, 11.27]   # el último fuera de tiempo

    def fader(t):
        return 0.0 if any(c <= t < c + 0.2 for c in cut_times) else 1.0

    fs = frames(timeline(lambda t: [deck(1, fader=fader(t), track="A", pos=t)], 16))
    out = run(FaderCutDetector(CFG, grids=lambda p: grid), fs)
    assert len(out) == 1
    burst = out[0]
    assert burst.count == 4 and burst.deck == 1
    assert [c.on_beat for c in burst.cuts] == [True, True, True, False]


def test_separate_bursts_and_long_drop_is_not_cut():
    def fader(t):
        if 5 <= t < 5.2 or 12 <= t < 12.2:
            return 0.0
        if 18 <= t < 20:     # bajada larga: no es corte
            return 0.0
        return 1.0

    fs = frames(timeline(lambda t: [deck(1, fader=fader(t), pos=t)], 25))
    out = run(FaderCutDetector(CFG), fs)
    assert [b.count for b in out] == [1, 1]
    assert out[0].cuts[0].on_beat is None    # sin rejilla


def test_deck_not_playing_before_is_not_cut():
    # Un deck que entra (sube) y baja rápido no es un fader cut.
    fs = frames(timeline(lambda t: [deck(1, fader=1.0 if 5 <= t < 5.3 else 0.0, pos=t)], 10))
    assert run(FaderCutDetector(CFG), fs) == []


# --- bass swaps ------------------------------------------------------
def test_bass_swap_on_bar():
    grid = BeatGrid.uniform(120.0)   # compás cada 2 s, frase cada 16 s
    # A corta graves en t=16.0; B los sube en t=16.0 (posición de B = t)
    fs = frames(timeline(lambda t: [
        deck(1, fader=1.0, track="A", eq=0.75 if t < 15.9 else 0.0, pos=t + 100),
        deck(2, fader=1.0, track="B", eq=0.0 if t < 16.0 else 0.75, pos=t),
    ], 20))
    out = run(BassSwapDetector(CFG, grids=lambda p: grid), fs)
    assert len(out) == 1
    s = out[0]
    assert (s.from_deck, s.to_deck) == (1, 2)
    assert s.on_bar is True and s.on_phrase is True


def test_bass_swap_outside_window_ignored():
    fs = frames(timeline(lambda t: [
        deck(1, fader=1.0, track="A", eq=0.75 if t < 5 else 0.0),
        deck(2, fader=1.0, track="B", eq=0.0 if t < 12 else 0.75),
    ], 15))
    assert run(BassSwapDetector(CFG), fs) == []


def test_bass_swap_requires_both_audible():
    fs = frames(timeline(lambda t: [
        deck(1, fader=1.0, track="A", eq=0.75 if t < 5 else 0.0),
        deck(2, fader=0.0, track="B", eq=0.0 if t < 5.5 else 0.75),
    ], 8))
    assert run(BassSwapDetector(CFG), fs) == []
