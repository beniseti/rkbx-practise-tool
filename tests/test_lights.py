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


# Tema sintético de techno: (frase, desde, hasta, graves, medios, agudos) por beat, a 120 BPM.
TECHNO = [("Intro", 1, 33, .55, .30, .20), ("Chorus", 33, 97, .90, .50, .60),
          ("Down", 97, 129, .05, .40, .30), ("Up", 129, 161, .05, None, None),
          ("Chorus", 161, 225, 1.0, .60, .70), ("Outro", 225, 257, .50, .30, .20)]


def techno_samples(n_beats=256):
    cols = {"low": [], "mid": [], "high": []}
    for _label, a, b, lo, mi, hi in TECHNO:
        for beat in range(a, b):
            if mi is None:                     # subida: medios y agudos crecen
                k = (beat - a) / (b - a)
                m, h = .3 + .5 * k, .2 + .7 * k
            else:
                m, h = mi, hi
            for _ in range(75):               # 150 columnas/s × 0.5 s
                cols["low"].append(lo)
                cols["mid"].append(m)
                cols["high"].append(h)
    return cols


def techno_analysis(with_phrases=True):
    from rkbx_practice.lights.analysis import build_analysis
    grid = BeatGrid.uniform(120.0, n_beats=256)
    phrases = [Phrase(a, label) for label, a, *_ in TECHNO] if with_phrases else []
    return build_analysis(grid, phrases, techno_samples(), "PWV7")


def test_structure_roles_with_phrases():
    a = techno_analysis()
    assert [(s.start, s.role) for s in a.sections] == [
        (1, "intro"), (33, "peak"), (97, "breakdown"), (129, "buildup"), (161, "drop"), (225, "outro")]


def test_structure_roles_without_phrases():
    a = techno_analysis(with_phrases=False)
    roles = [(s.start, s.role) for s in a.sections]
    assert (97, "buildup") in roles or (97, "breakdown") in roles
    assert (161, "drop") in roles
    assert roles[-1][1] == "outro"


def test_up_then_chorus_is_drop_even_without_bands():
    from rkbx_practice.lights.analysis import build_analysis
    grid = BeatGrid.uniform(128.0, n_beats=128)
    a = build_analysis(grid, [Phrase(1, "Intro"), Phrase(33, "Up"), Phrase(65, "Chorus")], {}, "")
    assert [s.role for s in a.sections] == ["intro", "buildup", "drop"]


def test_generate_show_from_structure(tmp_path):
    a = techno_analysis()
    show = generate_show("1001", "A - B", a, LightsConfig().rules)
    ev = {e.beat: e for e in show.events}
    assert ev[1].fx == "breathe"
    assert ev[33].fx == "chase" and ev[33].params["bpm"] == "live"          # pico
    assert ev[97].fx == "breathe"
    assert ev[129].fx == "build" and ev[129].params["dur_ms"] == 16000      # 32 beats a 120 BPM
    assert ev[157].fx == "strobe" and ev[157].params["dur_ms"] == 2000      # último compás
    assert ev[161].fx == "flash" and "golpe" in ev[161].label               # golpe del drop
    assert ev[162].fx == "pulse" and ev[162].params["color"] == "rojo"
    assert ev[225].fx == "breathe"
    store = ShowStore(tmp_path)
    store.save(show)
    again = store.load("1001")
    assert [(e.beat, e.fx, e.params) for e in again.events] == [(e.beat, e.fx, e.params) for e in show.events]
    assert json.loads(store.path("1001").read_text())["format_version"] == 2


def test_config_rules_override_defaults():
    from rkbx_practice.lights.show import effective_rules
    r = effective_rules({"Drop": {"fx": "rainbow", "params": {"dur_ms": 8000}}, "Chorus": {"fx": "x"}})
    assert r["drop"]["fx"] == "rainbow" and "chorus" not in r and r["groove"]["fx"] == "alternate"


def test_live_bpm_is_resolved_when_sending():
    from rkbx_practice.lights.commands import resolve_live
    assert resolve_live({"bpm": "live", "color": "rojo"}, 131.234) == {"bpm": 131.23, "color": "rojo"}
    assert resolve_live({"bpm": "live"}, None) == {}


def test_params_text():
    assert parse_params("color=rojo dur_ms=200 speed=1.5") == {"color": "rojo", "dur_ms": 200, "speed": 1.5}
    assert parse_params("color=rojo, zones=1,5,9") == {"color": "rojo", "zones": "1,5,9"}
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


def test_old_show_files_are_regenerated_unless_manual(library, tmp_path):
    from rkbx_practice.library.anlz import BeatGridCache
    from rkbx_practice.lights.manager import LightsManager
    cfg = AppConfig()
    cfg.lights.events_dir = str(tmp_path)

    class Sender:
        def send(self, cmd):
            pass

    old = {"format_version": 1, "track_id": "1001", "events": [{"beat": 5, "label": "Chorus", "source": "phrase",
                                                                 "fx": "strobe", "params": {}}]}
    (tmp_path / "1001.json").write_text(json.dumps(old))
    mgr = LightsManager(cfg, library, BeatGridCache(), sender=Sender(), analyzer=lambda p: techno_analysis())
    show = mgr.load_or_generate(library.track("1001"))
    assert show.format_version == 2 and any(e.fx == "build" for e in show.events)
    old["events"].append({"beat": 9, "label": "manual", "source": "manual", "fx": "flash", "params": {}})
    (tmp_path / "1001.json").write_text(json.dumps(old))
    show = mgr.load_or_generate(library.track("1001"))
    assert show.format_version == 1                       # con eventos a mano no se toca


def test_expand_zones_3x3():
    from rkbx_practice.lights.commands import expand_zones
    assert expand_zones("all") == list(range(1, 10))
    assert expand_zones("impares") == [1, 3, 5, 7, 9]
    assert expand_zones("even") == [2, 4, 6, 8]
    assert expand_zones("strip:2") == [4, 5, 6]
    assert expand_zones("seccion:3") == [3, 6, 9]
    assert expand_zones("1,5,9,12") == [1, 5, 9]
    assert expand_zones([2, 8]) == [2, 8]
