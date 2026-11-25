import json
from types import SimpleNamespace

import pytest

from rkbx_practice.config import AppConfig, SessionConfig
from rkbx_practice.events import TrackLoaded, TransitionEvent
from rkbx_practice.history import SessionRecorder, list_sessions, load_session, summarize
from rkbx_practice.live import LivePipeline, LivePoller, MemorySource, ScriptedSource, SnapshotEnricher
from rkbx_practice.memory import FakeMemory
from rkbx_practice.memory.deck_reader import ReadResult
from rkbx_practice.memory.process import ProcessNotFound
from rkbx_practice.state import ConnectionMode, DeckState, MixerState

PATH1 = r"C:\Users\dj\AppData\Roaming\Pioneer\rekordbox\share\PIONEER\USBANLZ\001\00000001-aaaa-bbbb-cccc-000000000001\ANLZ0000.DAT"
PATH2 = r"C:\Users\dj\AppData\Roaming\Pioneer\rekordbox\share\PIONEER\USBANLZ\002\00000002-aaaa-bbbb-cccc-000000000002\ANLZ0000.DAT"


def rr(*decks):
    return ReadResult(tuple(decks), MixerState(), [], 1)


def test_enricher_resolves_and_detects_jumps(library):
    lib_bpm = library.track("1001").bpm
    e = SnapshotEnricher(library, jump_tolerance_s=0.25)
    s1 = e.enrich(rr(DeckState(1, track_path=PATH1, bpm=lib_bpm, position_s=10.0)), t=0.0)
    assert s1.decks[0].track_id == "1001"
    s2 = e.enrich(rr(DeckState(1, track_path=PATH1, bpm=lib_bpm, position_s=10.02)), t=0.02)
    assert s2.decks[0].playing and not s2.decks[0].jumped
    s3 = e.enrich(rr(DeckState(1, track_path=PATH1, bpm=lib_bpm, position_s=10.02)), t=0.04)
    assert s3.decks[0].playing  # la posición no se refrescó en un sondeo: sigue sonando
    s4 = e.enrich(rr(DeckState(1, track_path=PATH1, bpm=lib_bpm, position_s=40.0)), t=0.06)
    assert s4.decks[0].jumped   # hot cue / arrastre
    s5 = e.enrich(rr(DeckState(1, track_path=PATH1, bpm=lib_bpm, position_s=40.0)), t=1.0)
    assert s5.decks[0].playing is False
    s6 = e.enrich(rr(DeckState(1, track_path=PATH1, bpm=lib_bpm, position_s=40.02)), t=1.02)
    assert s6.decks[0].playing and not s6.decks[0].jumped   # reanudar tras pausa no es salto


def test_pipeline_track_loaded_ignores_initial_state(library):
    cfg = AppConfig()
    e, p = SnapshotEnricher(library, empty_without_bpm=False), LivePipeline(cfg)
    ev = p.process(e.enrich(rr(DeckState(1, track_path=PATH1), DeckState(2)), t=0))
    assert ev == []   # lo que ya estaba cargado al conectar no se tacha
    ev = p.process(e.enrich(rr(DeckState(1, track_path=PATH1), DeckState(2, track_path=PATH2)), t=0.02))
    assert not [x for x in ev if isinstance(x, TrackLoaded)]     # aún no estable (300 ms)
    ev = p.process(e.enrich(rr(DeckState(1, track_path=PATH1), DeckState(2, track_path=PATH2)), t=0.4))
    loads = [x for x in ev if isinstance(x, TrackLoaded)]
    assert len(loads) == 1 and loads[0].deck == 2 and loads[0].track_id == "1002"
    assert loads[0].t == 0.02                                      # fechada cuando apareció
    ev = p.process(e.enrich(rr(DeckState(1, track_path=PATH1), DeckState(2, track_path=PATH2)), t=0.8))
    assert not [x for x in ev if isinstance(x, TrackLoaded)]


def test_pipeline_ignores_flicker_while_loading(library):
    cfg = AppConfig()
    e, p = SnapshotEnricher(library, empty_without_bpm=False), LivePipeline(cfg)
    p.process(e.enrich(rr(DeckState(1, track_path=PATH1)), t=0))
    events = []
    # Durante la carga la ruta alterna entre el tema nuevo y el anterior; luego se estabiliza.
    seq = [PATH2, PATH1, PATH2, PATH1, PATH2] + [PATH2] * 20
    for i, path in enumerate(seq, start=1):
        events += p.process(e.enrich(rr(DeckState(1, track_path=path)), t=i * 0.05))
    loads = [x for x in events if isinstance(x, TrackLoaded)]
    assert [l.track_id for l in loads] == ["1002"]


class Listener:
    def __init__(self):
        self.statuses, self.snaps, self.events = [], [], []

    def on_status(self, s):
        self.statuses.append(s)

    def on_snapshot(self, s):
        self.snaps.append(s)

    def on_events(self, ev):
        self.events += ev


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def test_poller_with_scripted_source_and_reconnect(library):
    cfg = AppConfig()
    clock = FakeClock()
    frames = [((DeckState(1, track_path=PATH1, fader=1.0), DeckState(2, fader=0.0)), MixerState())] * 3
    frames += [((DeckState(1, track_path=PATH1, fader=1.0), DeckState(2, track_path=PATH2, fader=0.0)), MixerState())]
    attempts = {"n": 0}

    def factory():
        attempts["n"] += 1
        if attempts["n"] == 1:
            from rkbx_practice.live.sources import SourceError
            raise SourceError("Rekordbox no está abierto")
        return ScriptedSource(frames)

    lst = Listener()
    poller = LivePoller(cfg, factory, SnapshotEnricher(library, empty_without_bpm=False), LivePipeline(cfg), lst,
                        clock=clock, sleep=clock.sleep)
    poller.step()   # falla y espera
    assert lst.statuses[-1].mode == ConnectionMode.DISCONNECTED
    assert clock.t == cfg.live.reconnect_interval_s
    for _ in range(30):
        clock.t += 0.02
        poller.step()
    assert lst.statuses[-1].mode == ConnectionMode.FULL
    loads = [e for e in lst.events if isinstance(e, TrackLoaded)]
    assert [(l.deck, l.track_id) for l in loads] == [(2, "1002")]


def test_memory_source_limited_mode_without_offsets(tmp_path):
    cfg = AppConfig()
    cfg.rekordbox.offsets_dir = str(tmp_path)
    proc = SimpleNamespace(version="7.9.99.0", access=SimpleNamespace(pointer_size=8),
                           is_alive=lambda: True, close=lambda: None)
    src = MemorySource(cfg, attach=lambda name: proc)
    status = src.connect()
    assert status.mode == ConnectionMode.LIMITED and status.version == "7.9.99"
    assert "7.9.99" in status.message


def test_memory_source_full_mode(tmp_path):
    from test_memory import BASE, build_memory, table_dict
    cfg = AppConfig()
    cfg.rekordbox.offsets_dir = str(tmp_path)
    (tmp_path / "7.2.20.json").write_text(json.dumps(table_dict()))
    mem, path = build_memory()
    proc = SimpleNamespace(version="7.2.20.0", access=mem, is_alive=lambda: True, close=lambda: None)
    src = MemorySource(cfg, attach=lambda name: proc)
    assert src.connect().mode == ConnectionMode.FULL
    res = src.read()
    assert res.decks[0].track_path == path


def test_memory_source_process_missing():
    cfg = AppConfig()

    def attach(name):
        raise ProcessNotFound("no está")

    from rkbx_practice.live.sources import SourceError
    with pytest.raises(SourceError):
        MemorySource(cfg, attach=attach).connect()


def test_recorder_end_to_end(library, tmp_path):
    from datetime import datetime
    rec = SessionRecorder(tmp_path, library, SessionConfig(), decks=2)
    rec.handle([TrackLoaded(1, "1001", PATH1, datetime.now(), 0.0)])  # sin grabar: se ignora
    rec.start("Techno/Warmup")
    now = datetime.now()
    rec.handle([
        TrackLoaded(1, "1001", PATH1, now, 0.0),
        TrackLoaded(2, "1002", PATH2, now, 1.0),
        TransitionEvent(1, 2, "1001", "1002", 124.0, 124.0, now, now, 10.0, 25.5),
    ])
    assert rec.counts()["tracks"] == 2
    path = rec.stop()
    assert path is not None and path in list_sessions(tmp_path)
    data = load_session(path)
    assert data["source"] == "Techno/Warmup"
    assert len(data["transitions"]) == 1
    tr = data["transitions"][0]
    assert tr["duration_s"] == 15.5
    assert tr["out"]["camelot"] == str(library.track("1001").camelot)
    assert tr["compatibility"] is not None and "compatible" in tr["compatibility"]
    assert "Transiciones: 1" in summarize(data)
    assert rec.stop() is None


def test_deck_without_bpm_is_empty(library):
    e = SnapshotEnricher(library)
    snap = e.enrich(rr(DeckState(1, track_path=PATH1, bpm=126.0), DeckState(2, track_path=PATH1, bpm=None)), t=0)
    assert snap.decks[0].track_id == "1001"
    assert snap.decks[1].track_id is None and snap.decks[1].track_path is None
