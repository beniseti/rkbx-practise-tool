"""Prueba de punta a punta con la fuente de demostración (sin Rekordbox)."""

from rkbx_practice.app import PracticeController
from rkbx_practice.config import AppConfig
from rkbx_practice.events import BassSwap, TrackLoaded, TransitionEvent
from rkbx_practice.history import load_session
from rkbx_practice.live import LivePipeline, LivePoller, SnapshotEnricher
from rkbx_practice.live.demo import DemoSource


class Clock:
    t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def test_demo_session_end_to_end(library, tmp_path):
    cfg = AppConfig()
    cfg.history.dir = str(tmp_path)
    cfg.session.wildcard_probability = 0.0
    cfg.session.optimize_time_budget_s = 0.1
    clock = Clock()
    ctl = PracticeController(cfg, library)
    session = ctl.generate("20", seed=1)            # Favoritos (6 temas)
    planned = [t.id for t in session.plan]

    def next_track():
        s = ctl.session
        return s.suggestion.track if s and s.suggestion else None

    source = DemoSource(library, next_track, track_s=30, blend_s=8, clock=clock)
    received = []

    class L:
        def on_status(self, s): pass
        def on_snapshot(self, s): pass
        def on_events(self, ev):
            ctl.recorder.handle(ev)
            ctl.apply_events(ev)      # en la UI esto ocurre en su hilo
            received.extend(ev)

    poller = LivePoller(cfg, lambda: source, SnapshotEnricher(library), LivePipeline(cfg), L(),
                        clock=clock, sleep=clock.sleep)
    ctl.poller = poller
    ctl.start_recording()
    for _ in range(int(95 / 0.02)):
        clock.t += 0.02
        poller.step()
    path = ctl.stop_recording()

    loads = [e for e in received if isinstance(e, TrackLoaded)]
    assert [e.track_id for e in loads] == planned[:4]
    assert all(session.is_struck(tid) for tid in planned[:4])
    assert session.suggestion.track.id == planned[4]

    transitions = [e for e in received if isinstance(e, TransitionEvent)]
    assert len(transitions) == 3
    assert all(6.0 < tr.duration_s < 9.0 for tr in transitions)
    assert [(tr.out_deck, tr.in_deck) for tr in transitions] == [(1, 2), (2, 1), (1, 2)]

    swaps = [e for e in received if isinstance(e, BassSwap)]
    assert len(swaps) == 3

    data = load_session(path)
    assert len(data["tracks"]) == 4 and len(data["transitions"]) == 3 and len(data["bass_swaps"]) == 3
    assert data["source"] == "Favoritos"
