"""Grabación de audio de la sesión (con dobles de prueba) y página de revisión."""

import json
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from rkbx_practice.audio import AudioRecorder
from rkbx_practice.audio.capture import AudioUnavailable
from rkbx_practice.config import AudioConfig, SessionConfig
from rkbx_practice.history import SessionRecorder, summarize
from rkbx_practice.review import ReviewServer, session_list


class FakeSource:
    """Seno de 440 Hz a media escala; cada lectura avanza el reloj falso lo que dura el bloque."""

    name = "Altavoces de prueba (lo que suena)"

    def __init__(self, clock, sr=1000, stall_after=None, stall_s=0.0, blocks=None):
        self.clock, self.sr = clock, sr
        self.n = 0
        self.stall_after, self.stall_s = stall_after, stall_s
        self.blocks = blocks
        self.closed = False

    def read(self, frames):
        if self.blocks is not None and self.n >= self.blocks:
            self.clock.stop_event.set()
        self.n += 1
        self.clock.t += frames / self.sr
        if self.stall_after is not None and self.n == self.stall_after:
            self.clock.t += self.stall_s           # el loopback no entregó nada durante un rato
        t = np.arange(frames) / self.sr
        mono = 0.5 * np.sin(2 * np.pi * 5 * t)
        return np.stack([mono, mono], axis=1).astype(np.float32)

    def close(self):
        self.closed = True


class FakeClock:
    def __init__(self):
        self.t = 1_700_000_000.0
        self.stop_event = None

    def __call__(self):
        return self.t


class FakeWriter:
    def __init__(self):
        self.blocks = []
        self.closed = False

    def write(self, data):
        self.blocks.append(np.array(data))

    def close(self):
        self.closed = True

    @property
    def frames(self):
        return sum(len(b) for b in self.blocks)


def _recorder(tmp_path, **src_kw):
    cfg = AudioConfig(sample_rate=1000, channels=2, block_ms=100, peaks_per_s=10)
    clock = FakeClock()
    writer = FakeWriter()
    src = FakeSource(clock, sr=1000, **src_kw)
    rec = AudioRecorder(cfg, source_factory=lambda: src, writer_factory=lambda *a: writer, clock=clock)
    clock.stop_event = rec._stop
    return rec, src, writer


def test_audio_recorder_writes_blocks_peaks_and_info(tmp_path):
    rec, src, writer = _recorder(tmp_path, blocks=20)
    assert rec.start(tmp_path / "session-x.flac")
    rec._thread.join(5)                    # la fuente falsa para sola tras 20 bloques
    info = rec.stop()
    assert writer.closed and src.closed
    assert writer.frames == rec.frames >= 2000                 # ≥ 20 bloques de 100 ms a 1 kHz
    assert info["file"] == "session-x.flac" and info["error"] is None
    assert info["device"] == FakeSource.name
    assert info["duration_s"] == pytest.approx(rec.frames / 1000)
    peaks = (tmp_path / "session-x.peaks").read_bytes()
    assert info["peaks_file"] == "session-x.peaks"
    assert len(peaks) == pytest.approx(info["duration_s"] * 10, abs=1)
    assert all(120 <= p <= 130 for p in peaks)                # media escala ≈ 127
    assert info["started_at"].startswith(datetime.fromtimestamp(1_700_000_000).strftime("%Y-%m-%dT%H:%M"))
    assert rec.level_db == pytest.approx(-6.0, abs=0.2)


def test_audio_recorder_pads_silence_when_capture_lags(tmp_path):
    rec, _, writer = _recorder(tmp_path, blocks=20, stall_after=5, stall_s=2.0)
    rec.start(tmp_path / "s.flac")
    rec._thread.join(5)
    info = rec.stop()
    assert info["padded_s"] == pytest.approx(2.0, abs=0.15)
    # el audio sigue cuadrando con el reloj: duración ≈ tiempo transcurrido
    assert rec.seconds == pytest.approx(rec._clock() - rec.started_wall, abs=0.15)
    assert any(not b.any() for b in writer.blocks)            # hay un bloque de silencio


def test_audio_recorder_reports_unavailable_without_crashing(tmp_path):
    def broken():
        raise AudioUnavailable("falta la librería «soundcard»")
    rec = AudioRecorder(AudioConfig(), source_factory=broken)
    assert rec.start(tmp_path / "s.flac") is False
    info = rec.stop()
    assert info["file"] is None and "soundcard" in info["error"]
    assert not (tmp_path / "s.peaks").exists()


def test_session_recorder_reserves_stem_and_stores_audio(tmp_path):
    r = SessionRecorder(tmp_path, None, SessionConfig())
    stem = r.start("Playlist")
    (tmp_path / f"{stem}.flac").write_bytes(b"x")              # el audio ya ocupa el nombre
    path = r.stop(audio={"file": f"{stem}.flac", "duration_s": 75.0, "device": "DDJ", "error": None})
    assert path == tmp_path / f"{stem}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["audio"]["file"] == f"{stem}.flac"
    assert "Audio: " in summarize(data) and "1:15" in summarize(data)
    # una segunda sesión en el mismo segundo no pisa los archivos de la primera
    stem2 = r.start(None)
    assert stem2 != stem and not list(tmp_path.glob(f"{stem2}.*"))
    r.stop()


def _get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status, dict(resp.headers), resp.read()


@pytest.fixture
def review(tmp_path):
    stem = "session-20261126-213000"
    audio = bytes(range(256)) * 40                              # 10240 bytes
    (tmp_path / f"{stem}.flac").write_bytes(audio)
    (tmp_path / f"{stem}.peaks").write_bytes(bytes([10, 200, 30]))
    data = {"started_at": "2026-11-26T21:30:00.000", "ended_at": "2026-11-26T22:30:00.000",
            "source": "Techno", "tracks": [], "transitions": [], "fader_cuts": [], "bass_swaps": [],
            "audio": {"file": f"{stem}.flac", "peaks_file": f"{stem}.peaks", "duration_s": 3600.0,
                      "started_at": "2026-11-26T21:30:00.100"}}
    (tmp_path / f"{stem}.json").write_text(json.dumps(data), encoding="utf-8")
    (tmp_path / "session-20261125-100000.json").write_text(json.dumps(
        {"started_at": "2026-11-25T10:00:00", "tracks": [], "transitions": [], "audio": None}), encoding="utf-8")
    (tmp_path / "secreto.txt").write_text("no", encoding="utf-8")
    srv = ReviewServer(tmp_path, port=0)
    srv.start()
    yield srv, stem, audio
    srv.stop()


def test_review_lists_and_serves_sessions(review, tmp_path):
    srv, stem, audio = review
    lst = session_list(Path(tmp_path))
    assert [s["id"] for s in lst] == [stem, "session-20261125-100000"]
    assert lst[0]["has_audio"] and not lst[1]["has_audio"]
    code, _, body = _get(srv.url + "api/sessions")
    assert code == 200 and json.loads(body)[0]["id"] == stem
    code, _, body = _get(srv.url + f"api/session/{stem}")
    assert json.loads(body)["source"] == "Techno"
    code, headers, body = _get(srv.url)
    assert code == 200 and b"Sesiones grabadas" in body
    code, headers, body = _get(srv.url + f"peaks/{stem}")
    assert body == bytes([10, 200, 30])


def test_review_audio_supports_ranges(review):
    srv, stem, audio = review
    code, headers, body = _get(srv.url + f"audio/{stem}")
    assert code == 200 and body == audio and headers["Content-Type"] == "audio/flac"
    assert headers["Accept-Ranges"] == "bytes"
    code, headers, body = _get(srv.url + f"audio/{stem}", {"Range": "bytes=100-199"})
    assert code == 206 and body == audio[100:200]
    assert headers["Content-Range"] == f"bytes 100-199/{len(audio)}"
    code, headers, body = _get(srv.url + f"audio/{stem}", {"Range": "bytes=10000-"})
    assert code == 206 and body == audio[10000:]
    code, _, body = _get(srv.url + f"audio/{stem}", {"Range": "bytes=-16"})
    assert body == audio[-16:]
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(srv.url + f"audio/{stem}", {"Range": "bytes=99999-"})
    assert e.value.code == 416


@pytest.mark.parametrize("path", ["audio/..%2Fsecreto.txt", "api/session/..%2Fsecreto", "audio/secreto",
                                  "audio/session-20261125-100000", "peaks/session-nope"])
def test_review_rejects_other_files(review, path):
    srv, _, _ = review
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(srv.url + path)
    assert e.value.code == 404


class ScriptedSource:
    """Bloques de 100 ms: 'S' = sonido, '.' = silencio. Avanza el reloj falso."""

    name = "guion"

    def __init__(self, clock, script, sr=1000):
        self.clock, self.script, self.sr, self.i = clock, script, sr, 0

    def read(self, frames):
        if self.i >= len(self.script):
            self.clock.stop_event.set()
            ch = "."
        else:
            ch = self.script[self.i]
        self.i += 1
        self.clock.t += frames / self.sr
        level = 0.5 if ch == "S" else 0.0
        return np.full((frames, 2), level, dtype=np.float32)

    def close(self):
        pass


def _scripted(script, auto_stop_s=1.0):
    cfg = AudioConfig(sample_rate=1000, channels=2, block_ms=100, peaks_per_s=10,
                      auto_stop_silence_s=auto_stop_s, silence_threshold_db=-50)
    clock = FakeClock()
    writer = FakeWriter()
    rec = AudioRecorder(cfg, source_factory=lambda: ScriptedSource(clock, script),
                        writer_factory=lambda *a: writer, clock=clock)
    clock.stop_event = rec._stop
    return rec, writer


def test_auto_stop_after_silence_cuts_where_sound_stopped(tmp_path):
    # 0,5 s de silencio inicial (no se graba: el audio empieza al sonar), 1 s de sonido, 0,4 s de silencio (vuelve el sonido:
    # se conserva), 0,3 s de sonido y luego silencio largo → para sola y descarta ese silencio.
    rec, writer = _scripted("....." + "S" * 10 + "...." + "SSS" + "." * 40, auto_stop_s=1.0)
    rec.start(tmp_path / "s.flac")
    rec._thread.join(5)
    info = rec.stop()
    assert rec.auto_stopped and info["auto_stopped"]
    assert rec.seconds == pytest.approx(1.0 + 0.4 + 0.3)
    assert info["skipped_leading_s"] == pytest.approx(0.5)
    assert rec.started_wall == pytest.approx(1_700_000_000.0 + 0.5)   # hora del primer sonido
    assert writer.frames == rec.frames
    assert info["discarded_silence_s"] == pytest.approx(1.0)
    assert rec.ended_wall == pytest.approx(rec.started_wall + 1.7)
    last = np.concatenate(writer.blocks)[-300:]
    assert np.abs(last).max() > 0.4                       # el archivo acaba con sonido


def test_no_auto_stop_before_any_sound_and_manual_stop_keeps_tail(tmp_path):
    rec, writer = _scripted("." * 30, auto_stop_s=1.0)      # 3 s sin sonar nunca: no se para
    (tmp_path / "s.flac").write_bytes(b"")                  # el escritor real crea el archivo
    rec.start(tmp_path / "s.flac")
    rec._thread.join(5)
    info = rec.stop()
    assert not rec.auto_stopped and rec.seconds == 0 and writer.frames == 0
    assert info["file"] is None and "no sonó nada" in info["error"]
    assert not (tmp_path / "s.flac").exists()               # no queda un audio vacío
    rec, writer = _scripted("SSSSS" + "." * 5, auto_stop_s=1.0)  # parada a mano con 0,5 s de silencio
    rec.start(tmp_path / "t.flac")
    rec._thread.join(5)
    rec.stop()
    assert not rec.auto_stopped and rec.seconds == pytest.approx(1.0, abs=0.11)


def test_session_cut_removes_events_after_end(tmp_path):
    from rkbx_practice.events import TrackLoaded
    r = SessionRecorder(tmp_path, None, SessionConfig())
    r.start(None)
    t1, t2 = datetime(2026, 11, 26, 21, 0, 0), datetime(2026, 11, 26, 21, 10, 0)
    r.handle([TrackLoaded(at=t1, t=0.0, deck=1, track_id="a", track_path=None),
              TrackLoaded(at=t2, t=600.0, deck=2, track_id="b", track_path=None)])
    path = r.stop(audio={"file": None, "auto_stopped": True}, ended_at=datetime(2026, 11, 26, 21, 5))
    data = json.loads(path.read_text(encoding="utf-8"))
    assert [t["id"] for t in data["tracks"]] == ["a"]
    assert data["ended_at"].startswith("2026-11-26T21:05:00")


def _snap(sec, f1, low1, f2=0.0, low2=0.0, base=datetime(2026, 11, 26, 21, 0, 0)):
    from datetime import timedelta
    from rkbx_practice.state import DeckState, LiveSnapshot
    return LiveSnapshot(t=sec, wall=base + timedelta(seconds=sec), decks=(
        DeckState(deck=1, fader=f1, eq_low=low1, eq_mid=0.5, eq_high=0.5, playing=True),
        DeckState(deck=2, fader=f2, eq_low=low2, eq_mid=0.5, eq_high=None, playing=False)))


def test_mixer_log_keeps_changes_and_keepalive():
    from rkbx_practice.history.mixer_log import MixerLog
    log = MixerLog(2, datetime(2026, 11, 26, 21, 0, 0), min_interval_s=0.05, keepalive_s=2.0)
    for i in range(100):                                  # 2 s sin cambios a 20 ms
        log.add(_snap(i * 0.02, 1.0, 0.5))
    assert len(log.samples) == 1                          # nada cambia: solo la primera
    log.add(_snap(2.1, 1.0, 0.5))                         # keepalive
    log.add(_snap(2.12, 0.6, 0.5))                        # cambio, pero < min_interval
    log.add(_snap(2.2, 0.6, 0.2))
    assert [s[0] for s in log.samples] == [0.0, 2.1, 2.2]
    assert log.columns[:6] == ["t", "d1_fader", "d1_high", "d1_mid", "d1_low", "d1_playing"]
    assert log.samples[-1][1:6] == [0.6, 0.5, 0.5, 0.2, 1]
    assert log.samples[-1][7] is None and log.samples[-1][10] == 0   # sin dato / parado
    log.cut_after(2.15)
    assert [s[0] for s in log.samples] == [0.0, 2.1]


def test_session_saves_mixer_file_and_lists_only_sessions(tmp_path):
    r = SessionRecorder(tmp_path, None, SessionConfig())
    stem = r.start(None)
    r.mixer.started = datetime(2026, 11, 26, 21, 0, 0)
    r.sample(_snap(0.0, 1.0, 0.5))
    r.sample(_snap(1.0, 0.5, 0.0, 0.8, 0.5))
    path = r.stop()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["mixer_file"] == f"{stem}.mix.json"
    mix = json.loads((tmp_path / data["mixer_file"]).read_text(encoding="utf-8"))
    assert len(mix["samples"]) == 2 and mix["decks"] == 2
    from rkbx_practice.history import list_sessions
    assert list_sessions(tmp_path) == [path]              # el .mix.json no cuenta como sesión
    assert [s["id"] for s in session_list(tmp_path)] == [stem]
    srv = ReviewServer(tmp_path, port=0)
    srv.start()
    try:
        code, _, body = _get(srv.url + f"mixer/{stem}")
        assert code == 200 and json.loads(body)["columns"][1] == "d1_fader"
    finally:
        srv.stop()
