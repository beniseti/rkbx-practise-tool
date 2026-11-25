import json
import socket
import time

import pytest

from rkbx_practice.config import AppConfig, DetectionConfig, LightsConfig, MixerConfig
from rkbx_practice.library.anlz import BeatGrid
from rkbx_practice.lights.analysis import (Phrase, TrackAnalysis, detect_changes, energy_per_beat,
                                           phrase_label)
from rkbx_practice.lights.commands import LightCommand, format_params, parse_params
from rkbx_practice.lights.engine import LightsEngine
from rkbx_practice.lights.show import LightEvent, LightShow, ShowStore, generate_show
from rkbx_practice.lights.udp import UdpSender

from helpers import deck, snap

GRID = BeatGrid.uniform(120.0, n_beats=400)      # 0.5 s por beat, compás cada 2 s


def energy_profile():
    # 64 beats flojos, 64 fuertes (drop en el beat 65), 64 flojos (break en 129), resto fuerte.
    return [0.2] * 64 + [0.9] * 64 + [0.2] * 64 + [0.9] * 208


def test_phrase_labels():
    assert phrase_label(1, 5) == "Chorus"
    assert phrase_label(2, 9) == "Chorus"
    assert phrase_label(3, 3) == "Verse 1"
    assert phrase_label(1, 99) == "Frase 99"


def test_energy_per_beat_and_changes():
    samples = []
    for e in energy_profile():
        samples += [e] * 75                       # 150 columnas/s × 0.5 s por beat
    energy = energy_per_beat(samples, GRID)
    assert len(energy) == 400 and max(energy) == pytest.approx(1.0)
    changes = detect_changes(GRID, energy)
    assert [(c.beat, c.kind) for c in changes] == [(65, "drop"), (129, "break"), (193, "drop")]


def test_bar_beat():
    a = TrackAnalysis(grid=GRID)
    assert a.bar_beat(1) == "1.1" and a.bar_beat(4) == "1.4" and a.bar_beat(5) == "2.1"


def analysis():
    from rkbx_practice.lights.analysis import EnergyChange
    return TrackAnalysis(grid=GRID, phrases=[Phrase(1, "Intro"), Phrase(33, "Verse 1"), Phrase(65, "Chorus"),
                                             Phrase(129, "Down")],
                         changes=[EnergyChange(65, "drop", 4.5)])


def test_generate_show_with_rules(tmp_path):
    rules = LightsConfig().rules
    show = generate_show("1001", "A - B", analysis(), rules)
    by_beat = {e.beat: e for e in show.events}
    assert by_beat[1].fx == "color"
    assert by_beat[33].fx == "color" and by_beat[33].params == {"color": "verde"}   # «Verse 1» → regla «Verse»
    assert by_beat[65].label == "Chorus + drop" and by_beat[65].fx == "flash"      # drop manda en el mismo beat
    assert by_beat[129].fx == "color"
    store = ShowStore(tmp_path)
    store.save(show)
    again = store.load("1001")
    assert [(e.beat, e.fx, e.params) for e in again.events] == [(e.beat, e.fx, e.params) for e in show.events]
    assert json.loads(store.path("1001").read_text())["format_version"] == 1


def test_params_text():
    assert parse_params("color=rojo dur_ms=200 speed=1.5") == {"color": "rojo", "dur_ms": 200, "speed": 1.5}
    assert format_params({"color": "rojo", "dur_ms": 200}) == "color=rojo dur_ms=200"


def make_engine(latency_ms=0):
    show = LightShow("A", events=[LightEvent(9, "Chorus", "phrase", "strobe", {"dur_ms": 1000}),
                                  LightEvent(17, "Down", "phrase", None),
                                  LightEvent(33, "drop", "energy", "flash", {"color": "rojo"})])
    sent = []
    cfg = LightsConfig(enabled=True, latency_compensation_ms=latency_ms)
    eng = LightsEngine(cfg, MixerConfig(), DetectionConfig(),
                       shows=lambda tid: show if tid == "A" else None,
                       grids=lambda p: GRID, send=sent.append, track_bpm=lambda tid: 120.0)
    return eng, sent


def play(eng, t0, t1, pos0, fader2=0.0, dt=0.02, jumped_at=None):
    fired = []
    t = t0
    while t <= t1 + 1e-9:
        pos = pos0 + (t - t0)
        d1 = deck(1, fader=1.0, track="A", pos=pos).with_(jumped=jumped_at is not None and abs(t - jumped_at) < 1e-6)
        fired += eng.update(snap(t, [d1, deck(2, fader=fader2, track="B", pos=0)]))
        t = round(t + dt, 6)
    return fired


def test_engine_fires_events_in_order():
    eng, sent = make_engine()
    fired = play(eng, 0.0, 20.0, pos0=0.0)
    assert [e.beat for e in fired] == [9, 33]             # el 17 no tiene efecto
    assert sent == [LightCommand("strobe", {"dur_ms": 1000}), LightCommand("flash", {"color": "rojo"})]
    assert eng.lead_deck == 1


def test_engine_latency_compensation():
    eng, sent = make_engine(latency_ms=100)
    fired_at = []
    t = 0.0
    while t <= 5.0:
        ev = eng.update(snap(t, [deck(1, fader=1.0, track="A", pos=t), deck(2, fader=0.0, track="B")]))
        if ev:
            fired_at.append(t)
        t = round(t + 0.02, 6)
    assert fired_at and fired_at[0] == pytest.approx(4.0 - 0.1, abs=0.021)   # beat 9 = 4.0 s


def test_engine_does_not_fire_after_jump():
    eng, sent = make_engine()
    play(eng, 0.0, 1.0, pos0=0.0)
    # Salto (hot cue) de 1 s a 20 s: los eventos 9 y 33 quedan detrás o delante, ninguno se dispara de golpe.
    fired = eng.update(snap(1.02, [deck(1, fader=1.0, track="A", pos=20.0).with_(jumped=True),
                                   deck(2, fader=0.0, track="B")]))
    assert fired == [] and sent == []


def test_engine_follows_lead_deck():
    eng, sent = make_engine()
    # El deck 2 (tema B, sin archivo) tiene el fader más alto: no se envía nada del tema A.
    fired = play(eng, 0.0, 10.0, pos0=0.0, fader2=1.0)
    assert eng.lead_deck == 2 or fired == []


def test_engine_disabled():
    eng, sent = make_engine()
    eng.enabled = False
    assert play(eng, 0.0, 20.0, pos0=0.0) == [] and sent == []


def test_udp_sender_reaches_receiver():
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(("127.0.0.1", 0))
    rx.settimeout(2)
    port = rx.getsockname()[1]
    tx = UdpSender("127.0.0.1", port)
    assert tx.send(LightCommand("flash", {"color": "rojo", "dur_ms": 200}))
    data, _ = rx.recvfrom(1024)
    assert json.loads(data) == {"fx": "flash", "color": "rojo", "dur_ms": 200}
    tx.close()
    rx.close()


def test_manager_generates_in_background(library, tmp_path):
    from rkbx_practice.library.anlz import BeatGridCache
    from rkbx_practice.lights.manager import LightsManager
    cfg = AppConfig()
    cfg.lights.events_dir = str(tmp_path)
    sent = []

    class Sender:
        def send(self, cmd):
            sent.append(cmd)

    mgr = LightsManager(cfg, library, BeatGridCache(), sender=Sender(), analyzer=lambda p: analysis())
    assert mgr.cached_show("1001") is None          # primera vez: se genera en segundo plano
    for _ in range(100):
        if mgr.cached_show("1001") is not None:
            break
        time.sleep(0.01)
    show = mgr.cached_show("1001")
    assert show is not None and show.track_id == "1001"
    assert ShowStore(tmp_path).load("1001") is not None


def _read_sse(resp, n, timeout=3.0):
    msgs, buf = [], b""
    deadline = time.time() + timeout
    while len(msgs) < n and time.time() < deadline:
        line = resp.fp.readline()
        if line.startswith(b"data: "):
            msgs.append(json.loads(line[6:]))
    return msgs


def test_simulator_udp_to_browser_stream():
    import http.client
    import threading
    from rkbx_practice.lights.simulator import LightSimulator
    sim = LightSimulator(udp_port=0, http_port=0, effects={"flash": {"dur_ms": 200}})
    sim.start()
    try:
        host, port = sim.http_addr
        c = http.client.HTTPConnection(host, port, timeout=5)
        c.request("GET", "/")
        r = c.getresponse()
        assert r.status == 200 and b"Simulador de luces" in r.read()
        c.request("GET", "/config")
        cfg = json.loads(c.getresponse().read())
        assert cfg["effects"] == {"flash": {"dur_ms": 200}}

        sse = http.client.HTTPConnection(host, port, timeout=5)
        sse.request("GET", "/events")
        resp = sse.getresponse()
        assert resp.getheader("Content-Type") == "text/event-stream"
        for _ in range(50):                       # esperar a que el cliente quede suscrito
            if sim._clients:
                break
            time.sleep(0.02)
        tx = UdpSender("127.0.0.1", sim.udp_addr[1])
        tx.send(LightCommand("flash", {"color": "rojo", "dur_ms": 200}))
        c.request("POST", "/send", body=json.dumps({"fx": "strobe", "dur_ms": 500}),
                  headers={"Content-Type": "application/json"})
        assert c.getresponse().status == 200
        msgs = _read_sse(resp, 2)
        assert [m["cmd"]["fx"] for m in msgs] == ["flash", "strobe"]
        assert msgs[0]["origin"] == "udp" and msgs[1]["origin"] == "prueba"
        tx.close()
    finally:
        sim.stop()


def test_sender_multiple_targets():
    rx1, rx2 = (socket.socket(socket.AF_INET, socket.SOCK_DGRAM) for _ in range(2))
    for rx in (rx1, rx2):
        rx.bind(("127.0.0.1", 0))
        rx.settimeout(2)
    tx = UdpSender("127.0.0.1", rx1.getsockname()[1])
    tx.add_target("127.0.0.1", rx2.getsockname()[1])
    tx.send(LightCommand("off"))
    assert json.loads(rx1.recvfrom(100)[0]) == {"fx": "off"}
    assert json.loads(rx2.recvfrom(100)[0]) == {"fx": "off"}
    for s in (rx1, rx2, tx):
        s.close()


def test_engine_status_explains_why_nothing_is_sent():
    eng, sent = make_engine()
    eng.enabled = False
    eng.update(snap(0, [deck(1, fader=1.0, track="A", pos=0)]))
    assert "desactivadas" in eng.status
    eng.enabled = True
    eng.update(snap(0, [deck(1, fader=0.0, track="A", pos=0), deck(2, fader=0.0, track="B")]))
    assert "faders" in eng.status
    eng.update(snap(0.1, [deck(1, fader=0.0, track="A", pos=0), deck(2, fader=1.0, track="B", pos=0)]))
    assert "preparando" in eng.status                      # el tema B no tiene archivo
    play(eng, 1.0, 2.0, pos0=0.0)
    assert "próximo: Chorus → strobe" in eng.status
