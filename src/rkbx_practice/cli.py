"""Línea de comandos.

Ejemplos:
    python -m rkbx_practice library list
    python -m rkbx_practice library show "Techno/Warmup"
    python -m rkbx_practice library export biblioteca.json
    python -m rkbx_practice session "Techno/Warmup" --seed 1
    python -m rkbx_practice live --session "Techno/Warmup"
    python -m rkbx_practice ui
    python -m rkbx_practice history list
    python -m rkbx_practice offsets

`--demo` simula dos decks pinchando las sugerencias (sin Rekordbox).
`--library-json archivo.json` usa una biblioteca exportada en lugar de master.db
(útil para probar sin Rekordbox; hay una de ejemplo en data/demo_library.json).
"""

from __future__ import annotations

import argparse
import copy
import logging
import sys
import time
from pathlib import Path

from .config import STRICTNESS_LEVELS, AppConfig, load_config
from .library import find_node_by_name, load_library
from .library.memory_library import InMemoryLibrary
from .library.models import NodeKind, Track
from .library.rekordbox_db import LibraryError


def _out(text: str = "") -> None:
    try:
        print(text)
    except UnicodeEncodeError:  # consolas de Windows sin UTF-8
        print(text.encode("ascii", "replace").decode())


def _fmt_track(i: int | None, t: Track, extra: str = "") -> str:
    num = f"{i:>3}. " if i is not None else ""
    bpm = f"{t.bpm:6.2f}" if t.bpm else "   —  "
    return f"{num}{bpm} BPM  {t.camelot_str:>3}  {t.display}{extra}"


def _load(cfg: AppConfig, args) -> InMemoryLibrary:
    try:
        return load_library(cfg, args.library_json)
    except LibraryError as exc:
        raise SystemExit(f"Error de biblioteca: {exc}") from exc


def _find(lib: InMemoryLibrary, name: str):
    node = lib.node(name) or find_node_by_name(lib.tree(), name)
    if node is None:
        raise SystemExit(f"No se encuentra la carpeta/playlist «{name}». Usa `library list`.")
    return node


def _session_settings(cfg: AppConfig, args):
    s = copy.deepcopy(cfg.session)
    if getattr(args, "tolerance", None) is not None:
        s.bpm_tolerance_pct = args.tolerance
    if getattr(args, "no_camelot", False):
        s.camelot_enabled = False
    if getattr(args, "strictness", None):
        s.camelot_strictness = args.strictness
    if getattr(args, "no_half_double", False):
        s.allow_half_double_tempo = False
    if getattr(args, "variety", None) is not None:
        s.variety = args.variety
    if getattr(args, "wildcard", None) is not None:
        s.wildcard_probability = args.wildcard
    return s


# --- comandos ----------------------------------------------------------
def cmd_library_list(cfg, args) -> int:
    lib = _load(cfg, args)
    icons = {NodeKind.FOLDER: "[+]", NodeKind.PLAYLIST: "   ", NodeKind.SMART: "[*]"}
    for root in lib.tree():
        for depth, node in root.walk():
            count = "" if node.kind == NodeKind.FOLDER else f"  ({len(lib.tracks_of(node.id))})"
            _out(f"{'    ' * depth}{icons[node.kind]} {node.name}{count}   [id {node.id}]")
    return 0


def cmd_library_show(cfg, args) -> int:
    lib = _load(cfg, args)
    node = _find(lib, args.name)
    tracks = lib.tracks_of(node.id)
    _out(f"{node.name}: {len(tracks)} temas")
    for i, t in enumerate(tracks, 1):
        warn = "" if (t.bpm and t.camelot) else "   ⚠ sin analizar" if not t.bpm else "   ⚠ sin tonalidad"
        _out(_fmt_track(i, t, warn))
    return 0


def cmd_library_export(cfg, args) -> int:
    lib = _load(cfg, args)
    lib.save_json(args.output)
    _out(f"Biblioteca exportada a {args.output} ({len(lib.all_tracks())} temas)")
    return 0


def cmd_session(cfg, args) -> int:
    from .session import generate_session
    import random
    lib = _load(cfg, args)
    node = _find(lib, args.name)
    settings = _session_settings(cfg, args)
    start = None
    if args.start:
        start = lib.track(args.start)
        if start is None:
            raise SystemExit(f"No existe el tema con ID {args.start}")
    t0 = time.monotonic()
    gen = generate_session(lib.tracks_of(node.id), settings, start=start, rng=random.Random(args.seed))
    _out(f"Sesión desde «{node.name}»: {len(gen.entries)} temas "
         f"(tolerancia {settings.bpm_tolerance_pct}%, Camelot "
         f"{settings.camelot_strictness if settings.camelot_enabled else 'desactivado'}, "
         f"{time.monotonic() - t0:.2f} s)")
    for i, e in enumerate(gen.entries, 1):
        extra = ""
        if e.from_previous is not None:
            tr = e.from_previous
            rel = f"  ← {tr.key.label_es}" if tr.key else ""
            mult = {2.0: " ×2", 0.5: " ×½"}.get(tr.bpm.multiplier, "")
            extra = f"{rel}{mult}  Δ{tr.bpm.diff_pct:.1f}%" + ("" if tr.compatible else "  ⚠ salto incompatible")
        _out(_fmt_track(i, e.track, extra))
    if gen.excluded:
        _out(f"\nExcluidos ({len(gen.excluded)}):")
        for t, reason in gen.excluded:
            _out(f"  - {t.display}: {reason}")
    if gen.incompatible_jumps:
        _out(f"\nAviso: {gen.incompatible_jumps} salto(s) incompatibles; la playlist no permite una cadena perfecta.")
    return 0


def cmd_live(cfg, args) -> int:
    from .app import PracticeController
    lib = _load(cfg, args)
    ctl = PracticeController(cfg, lib)
    if args.demo:
        ctl.use_demo_source()
    if args.session:
        ctl.generate(_find(lib, args.session).id, _session_settings(cfg, args))
    state = {"status": None, "snap": None, "events": []}

    class Listener:
        def on_status(self, s):
            state["status"] = s
            _out(f"\n[{s.mode.label_es}] Rekordbox {s.version or '?'} — {s.message}")

        def on_snapshot(self, s):
            state["snap"] = s

        def on_events(self, ev):
            state["events"] += ev

    ctl.start_live(Listener())
    if args.record:
        ctl.start_recording()
    _out("Leyendo en vivo (Ctrl+C para salir)…")
    try:
        while True:
            time.sleep(0.25)
            evs, state["events"] = state["events"], []
            if evs:
                ctl.apply_events(evs)
                for ev in evs:
                    _out(f"\n  · {_describe_event(ev, lib)}")
                if ctl.session and ctl.session.suggestion:
                    sug = ctl.session.suggestion
                    _out(f"  → Siguiente{' (COMODÍN)' if sug.is_wildcard else ''}: "
                         f"{_fmt_track(None, sug.track)}")
            snap = state["snap"]
            if snap is not None:
                parts = []
                for d in snap.decks:
                    t = lib.track(d.track_id) if d.track_id else None
                    name = (t.title[:22] if t else (Path(d.track_path).parent.name[:12] if d.track_path else "—"))
                    bpm = f"{d.bpm:6.2f}" if d.bpm else "  —   "
                    pos = f"{d.position_s:7.1f}s" if d.position_s is not None else "    —  "
                    fad = f"{d.fader:4.2f}" if d.fader is not None else " — "
                    eq = f"{d.eq_low:4.2f}" if d.eq_low is not None else " — "
                    m = "M" if d.is_master else " "
                    parts.append(f"D{d.deck}{m} {name:<22} {bpm} {pos} F{fad} L{eq}")
                xf = f" X{snap.mixer.crossfader:.2f}" if snap.mixer.crossfader is not None else ""
                sys.stdout.write("\r" + " | ".join(parts) + xf + "   ")
                sys.stdout.flush()
    except KeyboardInterrupt:
        pass
    finally:
        if ctl.recorder.recording:
            path = ctl.stop_recording()
            _out(f"\nSesión guardada en {path}")
        ctl.stop_live()
    _out("")
    return 0


def _describe_event(ev, lib: InMemoryLibrary) -> str:
    from .events import BassSwap, CutBurst, TrackLoaded, TransitionEvent

    def name(tid):
        t = lib.track(tid) if tid else None
        return t.display if t else (tid or "?")

    if isinstance(ev, TrackLoaded):
        return f"Deck {ev.deck}: cargado {name(ev.track_id) if ev.track_id else ev.track_path}"
    if isinstance(ev, TransitionEvent):
        return f"Transición deck {ev.out_deck}→{ev.in_deck} ({ev.duration_s:.1f} s)"
    if isinstance(ev, CutBurst):
        ok = sum(1 for c in ev.cuts if c.on_beat)
        return f"Fader cuts deck {ev.deck}: {ev.count} ({ok} a tiempo)"
    if isinstance(ev, BassSwap):
        return f"Bass swap {ev.from_deck}→{ev.to_deck} (compás: {ev.on_bar}, frase: {ev.on_phrase})"
    return repr(ev)


def cmd_ui(cfg, args) -> int:
    try:
        from .ui.main_window import run_ui
    except ImportError as exc:
        raise SystemExit(f"Falta PySide6 ({exc}). Instálalo con: pip install PySide6") from exc
    from .app import PracticeController
    lib = _load(cfg, args)
    ctl = PracticeController(cfg, lib)
    if args.demo:
        ctl.use_demo_source()
    return run_ui(cfg, lib, ctl)


def cmd_history_list(cfg, args) -> int:
    from .history import list_sessions, load_session
    sessions = list_sessions(cfg.resolve_path(cfg.history.dir))
    if not sessions:
        _out("No hay sesiones grabadas.")
    for p in sessions:
        try:
            d = load_session(p)
            _out(f"{p.name}  {d.get('source') or '—'}  temas: {len(d.get('tracks', []))}  "
                 f"transiciones: {len(d.get('transitions', []))}")
        except Exception as exc:  # noqa: BLE001
            _out(f"{p.name}  (ilegible: {exc})")
    return 0


def cmd_history_show(cfg, args) -> int:
    from .history import load_session, summarize
    path = Path(args.file)
    if not path.exists():
        path = cfg.resolve_path(cfg.history.dir) / args.file
    _out(summarize(load_session(path)))
    return 0


def cmd_offsets(cfg, args) -> int:
    from .memory.offsets import load_offsets_dir
    from .memory.process import ProcessNotFound, attach
    directory = cfg.resolve_path(cfg.rekordbox.offsets_dir)
    tables = load_offsets_dir(directory)
    _out(f"Offsets disponibles en {directory}: {', '.join(sorted(tables)) or 'ninguno'}")
    try:
        proc = attach(cfg.rekordbox.process_name)
    except ProcessNotFound as exc:
        _out(f"Rekordbox: {exc}")
        return 0
    from .memory.offsets import normalize_version
    v = normalize_version(proc.version)
    _out(f"Rekordbox en ejecución: versión {v} ({proc.exe_path})")
    if v in tables:
        missing = tables[v].missing_fields(cfg.rekordbox.decks)
        _out("Modo completo." + (f" Faltan: {', '.join(missing)}" if missing else ""))
    else:
        _out("Sin offsets para esta versión → modo limitado. Genera los offsets con "
             "`python -m tools.offset_detect`.")
    proc.close()
    return 0


def _find_track(lib: InMemoryLibrary, text: str) -> Track:
    t = lib.track(text)
    if t is not None:
        return t
    low = text.lower()
    matches = [t for t in lib.all_tracks() if low in t.display.lower()]
    if not matches:
        raise SystemExit(f"No se encuentra ningún tema con «{text}»")
    if len(matches) > 1:
        _out(f"Varios temas coinciden; uso el primero: {matches[0].display} [id {matches[0].id}]")
    return matches[0]


def cmd_lights_show(cfg, args) -> int:
    from .app import PracticeController
    lib = _load(cfg, args)
    ctl = PracticeController(cfg, lib)
    track = _find_track(lib, args.track)
    mgr = ctl.lights
    if args.regenerate:
        show = mgr.generate(track)
        if show is not None:
            mgr.save(show)
    else:
        show = mgr.load_or_generate(track)
    if show is None:
        raise SystemExit("No hay análisis de Rekordbox para este tema (¿está analizado?)")
    analysis = mgr.analyze(track)
    _out(f"{track.display}  [{mgr.store.path(track.id)}]")
    if analysis is not None:
        _out(f"Análisis: {len(analysis.phrases)} frases de Rekordbox · forma de onda: "
             f"{analysis.source or 'no encontrada'}")
        _out("Estructura:")
        for sec in analysis.sections:
            t0 = analysis.beat_time(sec.start) or 0.0
            _out(f"  {analysis.bar_beat(sec.start):>7}  {int(t0 // 60)}:{t0 % 60:05.2f}  "
                 f"{sec.name_es:<22} graves {sec.low:4.2f}  medios {sec.mid:4.2f}  agudos {sec.high:4.2f}  "
                 f"subida {sec.trend:+.2f}")
        _out("Eventos:")
    for e in show.sorted_events():
        when = ""
        if analysis is not None:
            t = analysis.beat_time(e.beat)
            when = f"{analysis.bar_beat(e.beat):>7}  {int(t // 60)}:{t % 60:05.2f}" if t is not None else ""
        from .lights.commands import format_params
        fx = f"{e.fx} {format_params(e.params)}" if e.fx else "(sin efecto)"
        _out(f"  beat {e.beat:>5}  {when}  {e.label:<16} {fx}")
    return 0


def cmd_lights_regenerate(cfg, args) -> int:
    """Rehace los archivos de efectos con el análisis y las reglas actuales."""
    from .app import PracticeController
    lib = _load(cfg, args)
    ctl = PracticeController(cfg, lib)
    mgr = ctl.lights
    if args.playlist:
        tracks = lib.tracks_of(_find(lib, args.playlist).id)
    elif args.only_existing:
        ids = {p.stem for p in mgr.store.dir.glob("*.json")}
        tracks = [t for t in lib.all_tracks() if t.id in ids]
    else:
        tracks = [t for t in lib.all_tracks() if t.analysis_path]
    done = kept = failed = 0
    for i, t in enumerate(tracks, 1):
        old = mgr.store.load(t.id)
        if old is not None and not args.force and any(e.source == "manual" for e in old.events):
            kept += 1
            continue
        show = mgr.generate(t)
        if show is None:
            failed += 1
            continue
        mgr.save(show)
        done += 1
        if i % 25 == 0 or i == len(tracks):
            _out(f"  {i}/{len(tracks)}…")
    _out(f"Regenerados: {done} · conservados (con eventos a mano; usa --force para rehacerlos): {kept} · "
         f"sin análisis de Rekordbox: {failed}")
    return 0


def cmd_lights_send(cfg, args) -> int:
    from .lights.commands import LightCommand, parse_params
    from .lights.udp import UdpSender
    cmd = LightCommand(args.fx, parse_params(" ".join(args.params)))
    host, port = (("127.0.0.1", cfg.lights.simulator_port) if args.simulator
                  else (cfg.lights.esp32_ip, cfg.lights.esp32_port))
    tx = UdpSender(host, port)
    ok = tx.send(cmd)
    _out(f"{'Enviado' if ok else 'NO enviado'} a {host}:{port}: {cmd.to_json().decode()}")
    return 0 if ok else 1


def cmd_lights_debug(cfg, args) -> int:
    """Lectura en vivo + motor de luces, imprimiendo por qué se envía o no cada efecto."""
    from .app import PracticeController
    lib = _load(cfg, args)
    ctl = PracticeController(cfg, lib)
    if args.demo:
        ctl.use_demo_source()
    mgr = ctl.lights
    if args.simulator:
        mgr.sender.add_target("127.0.0.1", cfg.lights.simulator_port)
    ctl.set_lights_enabled(True)

    class Listener:
        def on_status(self, s):
            _out(f"[{s.mode.label_es}] Rekordbox {s.version or '?'} — {s.message}")

        def on_snapshot(self, s):
            pass

        def on_events(self, ev):
            pass

    ctl.start_live(Listener())
    targets = ", ".join(f"{h}:{p}" for h, p in mgr.sender.targets)
    _out(f"Enviando a: {targets}. Ctrl+C para salir.")
    last_status, last_sent = None, None
    try:
        while True:
            time.sleep(0.2)
            eng = mgr.engine
            status = eng.status
            for tid, why in mgr.problems.items():
                status += f" | {lib.track(tid).display if lib.track(tid) else tid}: {why}"
            if status != last_status:
                _out(f"  {status}")
                last_status = status
            if eng.last_sent is not None and eng.last_sent != last_sent:
                last_sent = eng.last_sent
                ev = last_sent[0]
                _out(f"  → ENVIADO: {ev.label} → {ev.fx} {ev.params}")
    except KeyboardInterrupt:
        pass
    finally:
        ctl.stop_live()
    return 0


def cmd_lights_simulate(cfg, args) -> int:
    import webbrowser
    from .lights.simulator import LightSimulator
    lc = cfg.lights
    sim = LightSimulator(udp_host=args.host, udp_port=args.udp_port or lc.simulator_port,
                         http_port=args.http_port or lc.simulator_http_port,
                         leds=args.leds or lc.simulator_leds, effects=lc.effects, layout=lc.layout)
    try:
        sim.start()
    except OSError as exc:
        raise SystemExit(f"No se pudo abrir el simulador ({exc}). ¿Ya hay otro en marcha?") from exc
    _out(f"Simulador en {sim.url}  ·  escuchando UDP en {sim.udp_addr[0]}:{sim.udp_addr[1]}")
    _out("Para que la app le envíe los efectos: en config.json, lights.send_to_simulator = true "
         "(o el botón «Simulador» de la ventana). Ctrl+C para salir.")
    if not args.no_browser:
        webbrowser.open(sim.url)
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        sim.stop()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="rkbx-practice", description="Asistente de práctica para Rekordbox")
    p.add_argument("--config", help="Ruta a config.json")
    p.add_argument("--library-json", help="Usar una biblioteca exportada (JSON) en lugar de master.db")
    p.add_argument("--demo", action="store_true",
                   help="Simular los decks (sin Rekordbox) para probar la ventana y las detecciones")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    lib = sub.add_parser("library", help="Biblioteca de Rekordbox").add_subparsers(dest="sub", required=True)
    lib.add_parser("list", help="Lista carpetas y playlists").set_defaults(func=cmd_library_list)
    sp = lib.add_parser("show", help="Muestra los temas de una carpeta/playlist")
    sp.add_argument("name", help="Nombre, ruta Carpeta/Playlist o ID")
    sp.set_defaults(func=cmd_library_show)
    sp = lib.add_parser("export", help="Exporta la biblioteca a JSON")
    sp.add_argument("output")
    sp.set_defaults(func=cmd_library_export)

    def session_opts(sp):
        sp.add_argument("--tolerance", type=float, help="Tolerancia de BPM en %%")
        sp.add_argument("--no-camelot", action="store_true", help="Ignorar la tonalidad")
        sp.add_argument("--strictness", choices=STRICTNESS_LEVELS)
        sp.add_argument("--no-half-double", action="store_true", help="No comparar a mitad/doble tempo")
        sp.add_argument("--wildcard", type=float, help="Probabilidad de comodín (0..1)")
        sp.add_argument("--variety", type=float, help="Variedad entre sesiones (0 = siempre igual)")

    sp = sub.add_parser("session", help="Genera una sesión de práctica")
    sp.add_argument("name", help="Carpeta o playlist")
    sp.add_argument("--start", help="ID del tema inicial")
    sp.add_argument("--seed", type=int, default=None)
    session_opts(sp)
    sp.set_defaults(func=cmd_session)

    sp = sub.add_parser("live", help="Muestra en vivo el estado de cada deck")
    sp.add_argument("--session", help="Carpeta/playlist para tachar temas en vivo")
    sp.add_argument("--record", action="store_true", help="Grabar la sesión al histórico")
    session_opts(sp)
    sp.set_defaults(func=cmd_live)

    sub.add_parser("ui", help="Abre la ventana de práctica").set_defaults(func=cmd_ui)

    h = sub.add_parser("history", help="Sesiones grabadas").add_subparsers(dest="sub", required=True)
    h.add_parser("list").set_defaults(func=cmd_history_list)
    sp = h.add_parser("show")
    sp.add_argument("file")
    sp.set_defaults(func=cmd_history_show)

    sub.add_parser("offsets", help="Versión de Rekordbox y offsets disponibles").set_defaults(func=cmd_offsets)

    li = sub.add_parser("lights", help="Luces (ESP32)").add_subparsers(dest="sub", required=True)
    sp = li.add_parser("show", help="Cambios del tema y efectos asignados (genera el archivo si falta)")
    sp.add_argument("track", help="ID del tema o parte del título")
    sp.add_argument("--regenerate", action="store_true", help="Volver a generar desde el análisis")
    sp.set_defaults(func=cmd_lights_show)
    sp = li.add_parser("regenerate", help="Rehace los archivos de efectos con el análisis y reglas actuales")
    sp.add_argument("--playlist", help="Solo los temas de esta carpeta/playlist")
    sp.add_argument("--only-existing", action="store_true",
                    help="Solo los temas que ya tienen archivo de efectos")
    sp.add_argument("--force", action="store_true", help="Rehacer también los que tienen eventos a mano")
    sp.set_defaults(func=cmd_lights_regenerate)
    sp = li.add_parser("send", help="Envía un efecto al ESP32 (prueba)")
    sp.add_argument("fx", help="Efecto, p. ej. flash")
    sp.add_argument("params", nargs="*", help="Parámetros, p. ej. color=rojo dur_ms=200")
    sp.add_argument("--simulator", action="store_true", help="Enviar al simulador local en vez de al ESP32")
    sp.set_defaults(func=cmd_lights_send)
    sp = li.add_parser("debug", help="En vivo: muestra por qué se envían o no los efectos")
    sp.add_argument("--simulator", action="store_true", help="Enviar también al simulador local")
    sp.set_defaults(func=cmd_lights_debug)
    sp = li.add_parser("simulate", help="Simulador de luces en el navegador (hace de ESP32)")
    sp.add_argument("--host", default="127.0.0.1", help="IP en la que escucha UDP (0.0.0.0 = toda la red)")
    sp.add_argument("--udp-port", type=int, help="Puerto UDP (por defecto lights.simulator_port)")
    sp.add_argument("--http-port", type=int, help="Puerto de la página (por defecto lights.simulator_http_port)")
    sp.add_argument("--leds", type=int, help="Número de LEDs a dibujar")
    sp.add_argument("--no-browser", action="store_true", help="No abrir el navegador")
    sp.set_defaults(func=cmd_lights_simulate)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config(args.config)
    return args.func(cfg, args)


if __name__ == "__main__":
    sys.exit(main())
