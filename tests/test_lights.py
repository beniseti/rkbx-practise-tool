import json
import socket
from pathlib import Path
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
    from rkbx_practice.lights.palette import palette_for
    a = techno_analysis()
    pal = palette_for("8A", "1001")
    show = generate_show("1001", "A - B", a, LightsConfig().rules, pal)
    ev = {e.beat: e for e in show.events}
    assert ev[1].fx == "wave" and ev[1].params["color"] == pal["primary"]   # mismos tonos del tema
    assert ev[33].fx in {"pulse", "alternate", "wave", "chase"} and ev[33].params["bpm"] == "live"
    assert ev[97].fx == "twinkle"
    assert ev[129].fx == "build" and ev[129].params["dur_ms"] == 16000      # 32 beats a 120 BPM
    assert ev[157].fx == "strobe" and ev[157].params["dur_ms"] == 1500      # último compás hasta el apagón
    assert ev[160].fx == "off" and ev[160].label == "Apagón"                # apagón 1 beat antes del drop
    drop = next(x for x in a.sections if x.role == "drop")
    assert drop.tier == "hard"                                              # vuelve el bombo a tope tras el break
    assert ev[161].fx == "chase" and ev[161].params["color"] == pal["accent"]   # fuerte: acento en movimiento
    assert 162 not in ev
    assert ev[225].fx == "wave"
    store = ShowStore(tmp_path)
    store.save(show)
    again = store.load("1001")
    assert [(e.beat, e.fx, e.params) for e in again.events] == [(e.beat, e.fx, e.params) for e in show.events]
    assert json.loads(store.path("1001").read_text())["format_version"] == 5


def test_drop_intensity_tiers_and_variants():
    from rkbx_practice.lights.analysis import build_analysis
    from rkbx_practice.lights.show import effective_rules
    from rkbx_practice.lights.structure import tier_for
    assert (tier_for(0.3), tier_for(0.6), tier_for(0.9)) == ("soft", "medium", "hard")
    # Dos drops fuertes en el mismo tema → variantes distintas.
    grid = BeatGrid.uniform(128.0, n_beats=192)
    phrases = [Phrase(1, "Intro"), Phrase(33, "Up"), Phrase(65, "Chorus"), Phrase(97, "Up"), Phrase(129, "Chorus")]
    a = build_analysis(grid, phrases, {}, "")
    drops = [x for x in a.sections if x.role == "drop"]
    assert [x.occurrence for x in drops] == [0, 1]
    ev = {e.beat: e for e in generate_show("1", "", a, {}).events}
    r = effective_rules({})
    key = f"drop_{drops[0].tier}"
    assert ev[65].fx == r[key][0]["fx"] and ev[129].fx == r[key][1]["fx"]


def test_palette_from_camelot():
    from rkbx_practice.lights.palette import apply_palette, palette_for
    p8, p9, p8b = palette_for("8A"), palette_for("9A"), palette_for("8B")
    assert p8 != p9 and p8["primary"].startswith("#") and len(p8["primary"]) == 7
    assert palette_for("8A") == p8                                        # estable
    assert palette_for(None, "123") == palette_for(None, "123")           # sin tonalidad: por ID
    assert apply_palette({"color": "$accent", "x": 1}, p8) == {"color": p8["accent"], "x": 1}


def test_config_rules_override_defaults():
    from rkbx_practice.lights.show import effective_rules
    r = effective_rules({"Drop": {"fx": "rainbow", "params": {"dur_ms": 8000}}, "Chorus": {"fx": "x"},
                         "peak_hard": [{"fx": "chase"}, {"fx": "wave"}]})
    assert all(r[f"drop_{t}"][0]["fx"] == "rainbow" for t in ("soft", "medium", "hard"))
    assert "chorus" not in r and r["groove"][0]["fx"] == "alternate"
    assert [x["fx"] for x in r["peak_hard"]] == ["chase", "wave"]


def test_live_bpm_is_resolved_when_sending():
    from rkbx_practice.lights.commands import resolve_live
    assert resolve_live({"bpm": "live", "color": "rojo"}, 131.234) == {"bpm": 131.23, "color": "rojo"}
    assert resolve_live({"bpm": "live"}, None) == {}


def test_params_text():
    assert parse_params("color=rojo dur_ms=200 speed=1.5") == {"color": "rojo", "dur_ms": 200, "speed": 1.5}
    assert parse_params("color=rojo, zones=1,5,9") == {"color": "rojo", "zones": "1,5,9"}
    assert format_params({"color": "rojo", "dur_ms": 200}) == "color=rojo dur_ms=200"


def no_sync(sent):
    return [c for c in sent if c.fx != "sync"]


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
    # Al engancharse aún no ha llegado ningún efecto de fondo: primero el efecto básico.
    sent = no_sync(sent)
    assert sent[0].fx == "wave"
    assert sent[1:] == [LightCommand("strobe", {"dur_ms": 1000}), LightCommand("flash", {"color": "rojo"})]
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
    assert fired == [] and all(c.fx == "wave" for c in sent)   # solo el efecto básico, ningún evento


def test_engine_basic_effect_until_events_are_loaded():
    sent = []
    shows = {"A": None}
    eng = LightsEngine(LightsConfig(enabled=True), MixerConfig(), DetectionConfig(),
                       shows=lambda tid: shows.get(tid), grids=lambda p: GRID, send=sent.append,
                       track_bpm=lambda tid: 120.0)
    play(eng, 0.0, 1.0, pos0=0.0)
    assert [c.fx for c in sent] == ["wave"] and sent[0].params["bpm"] == 120.0   # una sola vez
    assert "efecto básico" in eng.status
    # Se carga el archivo: se pasa al efecto de la sección actual (el fondo del beat 1).
    shows["A"] = LightShow("A", events=[LightEvent(1, "Intro", "structure", "breathe", {"color": "azul"}),
                                        LightEvent(9, "Groove", "structure", "alternate", {"bpm": "live"})])
    play(eng, 1.02, 5.0, pos0=1.02)
    assert [c.fx for c in no_sync(sent)] == ["wave", "breathe", "alternate"]


def test_engine_idle_when_nothing_sounds():
    eng, sent = make_engine()
    for i in range(5):
        eng.update(snap(i * 0.02, [deck(1, fader=0.0, track="A", pos=0), deck(2, fader=0.0, track="B")]))
    assert [c.fx for c in sent] == ["twinkle"]
    assert "reposo" in eng.status


def test_engine_catch_up_sends_current_background_after_jump():
    sent = []
    show = LightShow("A", events=[LightEvent(1, "Intro", "structure", "wave", {}),
                                  LightEvent(9, "Subida", "structure", "build", {"dur_ms": 4000}),
                                  LightEvent(17, "Drop", "structure", "chase", {"beats": 0.5}),
                                  LightEvent(33, "Breakdown", "structure", "twinkle", {})])
    eng = LightsEngine(LightsConfig(enabled=True), MixerConfig(), DetectionConfig(),
                       shows=lambda tid: show, grids=lambda p: GRID, send=sent.append,
                       track_bpm=lambda tid: 120.0)
    play(eng, 0.0, 0.5, pos0=0.0)
    assert [c.fx for c in sent] == ["wave"]
    # Hot cue a mitad de la subida (beat ~12): se repite el último FONDO (wave), no el build.
    eng.update(snap(0.52, [deck(1, fader=1.0, track="A", pos=5.6).with_(jumped=True), deck(2, fader=0.0, track="B")]))
    assert [c.fx for c in sent] == ["wave"]
    # Salto al drop (beat ~20): se pone su fondo (chase) sin esperar al siguiente evento.
    eng.update(snap(0.54, [deck(1, fader=1.0, track="A", pos=9.6).with_(jumped=True), deck(2, fader=0.0, track="B")]))
    assert [c.fx for c in sent] == ["wave", "chase"]


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
    assert show.format_version == 5 and any(e.fx == "build" for e in show.events)
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


def test_cli_regenerate_keeps_manual(library, tmp_path, monkeypatch):
    from rkbx_practice import cli
    from rkbx_practice.lights import manager as mgr_mod
    monkeypatch.setattr(mgr_mod, "read_track_analysis", lambda p: techno_analysis())
    monkeypatch.setattr(mgr_mod.LightsManager.__init__, "__defaults__", (None, lambda p: techno_analysis()))
    cfg_path = tmp_path / "config" / "config.json"
    cfg_path.parent.mkdir()
    cfg_path.write_text(json.dumps({"lights": {"events_dir": str(tmp_path / "lights")}}))
    store = ShowStore(tmp_path / "lights")
    store.save(LightShow("1001", events=[LightEvent(9, "mío", "manual", "flash", {})]))
    lib_json = str(Path(__file__).parent / "fixtures" / "library.json")
    assert cli.main(["--config", str(cfg_path), "--library-json", lib_json,
                     "lights", "regenerate", "--playlist", "Favoritos"]) == 0
    assert store.load("1001").events[0].source == "manual"          # conservado
    assert store.load("1002").format_version == 5                    # regenerado
    assert cli.main(["--config", str(cfg_path), "--library-json", lib_json,
                     "lights", "regenerate", "--playlist", "Favoritos", "--force"]) == 0
    assert store.load("1001").events[0].source == "structure"


def test_drop_hit_can_be_reenabled():
    a = techno_analysis()
    rules = {"drop_hit": {"fx": "flash", "params": {"color": "blanco", "dur_ms": 400}},
             "blackout": {"fx": "off", "params": {}, "beats": 2}}
    ev = {e.beat: e for e in generate_show("1", "", a, rules).events}
    assert ev[159].fx == "off" and ev[161].fx == "flash" and ev[162].fx == "chase"


def test_default_backgrounds_always_move():
    """Pedido del usuario: las luces nunca se quedan estáticas (salvo el apagón antes del drop)."""
    from rkbx_practice.lights.show import effective_rules
    moving = {"wave", "twinkle", "alternate", "chase", "pulse", "build", "strobe", "flash", "rainbow"}
    r = effective_rules(LightsConfig().rules)
    for key, variants in r.items():
        for rule in variants:
            if key in ("blackout", "drop_hit") or not rule.get("fx"):
                continue
            assert rule["fx"] in moving, key
            assert "zones" not in rule["params"], key      # ninguna zona se queda sin efecto


def test_engine_sends_bar_sync_and_beat_phase():
    sent = []
    show = LightShow("A", events=[LightEvent(1, "Intro", "structure", "wave", {"bpm": "live"}),
                                  LightEvent(9, "Groove", "structure", "alternate", {"bpm": "live"})])
    eng = LightsEngine(LightsConfig(enabled=True), MixerConfig(), DetectionConfig(),
                       shows=lambda tid: show, grids=lambda p: GRID, send=sent.append,
                       track_bpm=lambda tid: 120.0)
    play(eng, 0.0, 6.0, pos0=0.0)
    syncs = [c for c in sent if c.fx == "sync"]
    assert len(syncs) >= 2 and syncs[0].params == {"bpm": 120.0, "phase": 0.0}   # un sync por compás
    alt = next(c for c in sent if c.fx == "alternate")
    assert alt.params["phase"] == 0.0                     # el evento cae justo en el beat


def test_engine_schedules_events_at_exact_time():
    sent, scheduled = [], []
    show = LightShow("A", events=[LightEvent(9, "Chorus", "structure", "strobe", {"dur_ms": 500})])
    eng = LightsEngine(LightsConfig(enabled=True), MixerConfig(), DetectionConfig(),
                       shows=lambda tid: show, grids=lambda p: GRID, send=sent.append,
                       track_bpm=lambda tid: 120.0, schedule=lambda d, fn: scheduled.append((d, fn)),
                       poll_s=0.02)
    # Sondeos cada 20 ms; el beat 9 está en 4.000 s. En el sondeo de 3.99 s ya se programa
    # para dentro de 10 ms en vez de esperar al sondeo de 4.01 s.
    play(eng, 0.0, 3.99, pos0=0.0, dt=0.03)
    assert not any(c.fx == "strobe" for c in sent)          # no se envía antes de tiempo
    for d, fn in scheduled:
        fn()
    assert any(c.fx == "strobe" for c in sent)
    assert any(abs(d - 0.01) < 1e-6 for d, _ in scheduled)  # programado para los 4.000 s exactos


def test_engine_metronome_flashes_every_beat_and_mutes_events():
    eng, sent = make_engine()
    eng.metronome = True
    play(eng, 0.0, 6.0, pos0=0.0)
    flashes = [c for c in sent if c.fx == "flash" and c.params.get("dur_ms") == 80]
    assert 10 <= len(flashes) <= 13                         # ~12 beats en 6 s a 120 BPM
    assert not any(c.fx == "strobe" for c in sent)          # los eventos del tema no suenan
    assert "Calibración" in eng.status


def test_save_value_keeps_rest_of_config(tmp_path):
    from rkbx_practice.config import load_config, save_value
    p = tmp_path / "config" / "config.json"
    p.parent.mkdir()
    p.write_text(json.dumps({"rekordbox": {"decks": 2}, "lights": {"esp32_port": 4210}}))
    save_value(p, ["lights", "latency_compensation_ms"], 85)
    cfg = load_config(p)
    assert cfg.lights.latency_compensation_ms == 85 and cfg.lights.esp32_port == 4210
    assert cfg.rekordbox.decks == 2 and cfg.source_path == p
