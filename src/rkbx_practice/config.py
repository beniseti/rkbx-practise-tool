"""Carga de `config/config.json`.

Todo lo ajustable vive en el JSON. Aquí solo hay valores por defecto (los
mismos que el JSON de ejemplo) para que un archivo incompleto siga funcionando.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "config.json"

STRICTNESS_LEVELS = ("strict", "moderate", "relaxed")


@dataclass
class RekordboxConfig:
    process_name: str = "rekordbox.exe"
    decks: int = 2
    offsets_dir: str = "data/offsets"


@dataclass
class LibraryConfig:
    master_db_path: str | None = None  # None = autodetección de pyrekordbox
    db_key: str | None = None
    copy_before_open: bool = True  # abrir una copia temporal: nunca tocar el original


@dataclass
class SessionConfig:
    bpm_tolerance_pct: float = 4.0
    allow_half_double_tempo: bool = True
    camelot_enabled: bool = True
    camelot_strictness: str = "moderate"
    wildcard_probability: float = 0.15
    optimize_time_budget_s: float = 1.0
    follow_list: bool = True  # la sugerencia sigue el orden de la lista generada (sin recálculo)
    variety: float = 0.5   # 0 = siempre la misma sesión; más alto = sesiones más distintas


@dataclass
class LiveConfig:
    poll_interval_ms: int = 20
    reconnect_interval_s: float = 3.0
    position_jump_tolerance_s: float = 0.25
    max_consecutive_read_errors: int = 50
    load_debounce_ms: int = 300
    empty_deck_without_bpm: bool = True


@dataclass
class MixerConfig:
    crossfader_enabled: bool = False
    crossfader_assign: dict[str, str] = field(
        default_factory=lambda: {"1": "A", "2": "B", "3": "A", "4": "B"}
    )


@dataclass
class DetectionConfig:
    fader_zero_threshold: float = 0.05
    preview_touch_max_ms: int = 1500
    transition_stable_s: float = 4.0
    cut_max_beats: float = 1.0
    cut_group_gap_beats: float = 2.0
    bass_swap_window_beats: float = 4.0
    eq_low_threshold: float = 0.3
    on_beat_tolerance_ms: float = 60.0
    bass_swap_bar_tolerance_beats: float = 0.5
    default_bpm: float = 120.0


@dataclass
class HistoryConfig:
    dir: str = "history"


@dataclass
class AudioConfig:
    """Grabación del audio de la sesión (pedido del usuario) junto al histórico."""
    enabled: bool = True
    # Parte del nombre del dispositivo (p. ej. "DDJ-FLX4"); null = salida predeterminada de Windows.
    device: str | None = None
    loopback: bool = True        # True: lo que suena por una SALIDA; False: una ENTRADA (línea/micro)
    sample_rate: int = 44100
    channels: int = 2
    format: str = "flac"         # flac (sin pérdida, ~1/2 de wav), wav u ogg
    block_ms: int = 100
    peaks_per_s: int = 20        # resolución de la forma de onda que se guarda para revisar
    # Parada automática (pedido del usuario): tras este tiempo seguido sin sonido se para la
    # grabación y se corta desde donde dejó de sonar. 0 = desactivado.
    auto_stop_silence_s: float = 300.0
    silence_threshold_db: float = -50.0   # por debajo de este pico se considera silencio
    review_http_port: int = 8766  # página de revisión http://127.0.0.1:8766/


@dataclass
class UiConfig:
    always_on_top: bool = True
    start_compact: bool = False
    theme: str = "rekordbox"   # "rekordbox" (oscuro) o "system"


@dataclass
class LightsConfig:
    enabled: bool = False
    # Direcciones/nombres del ESP32: Wi-Fi de casa (mDNS) y, si no, su Wi-Fi propia.
    esp32_ip: str | list = field(default_factory=lambda: ["rkbx-luces.local", "192.168.4.1"])
    esp32_port: int = 4210
    latency_compensation_ms: int = 0
    events_dir: str = "data/lights"
    simulator_port: int = 4211        # UDP del simulador (127.0.0.1)
    simulator_http_port: int = 8765   # página http://127.0.0.1:8765/
    simulator_leds: int = 60          # (obsoleto: se usa layout)
    # Montaje: 3 tiras con 3 secciones cada una = 9 zonas (tira 1 → zonas 1-3, tira 2 → 4-6…).
    layout: dict = field(default_factory=lambda: {"strips": 3, "sections_per_strip": 3,
                                                  "leds_per_section": 20})
    send_to_simulator: bool = False   # además del ESP32, enviar al simulador local
    # Catálogo de efectos del ESP32 con sus parámetros por defecto (cada uno los suyos).
    # "live" = BPM real del deck al enviar; "until_next" = hasta la siguiente sección.
    effects: dict[str, dict] = field(default_factory=lambda: {
        "color": {"color": "azul"},
        "pulse": {"color": "morado", "bpm": "live"},
        "breathe": {"color": "azul", "period_ms": 4000},
        "build": {"color": "blanco", "dur_ms": 8000},
        "flash": {"color": "blanco", "dur_ms": 200},
        "strobe": {"dur_ms": 1000, "hz": 12},
        "rainbow": {"dur_ms": 4000},
        "alternate": {"color": "morado", "color2": "azul", "bpm": "live"},
        "wave": {"color": "azul", "color2": "morado", "bpm": "live", "beats": 4},
        "twinkle": {"color": "azul", "color2": "morado", "density": 0.15, "bpm": "live"},
        "chase": {"color": "naranja", "bpm": "live", "beats": 1},
        "off": {},
    })
    # Efecto por papel musical (intro, groove, peak, breakdown, buildup, drop, outro) y momentos
    # especiales (drop_hit, pre_drop). Lo que no se ponga aquí usa los valores por defecto de
    # lights/show.py (DEFAULT_RULES).
    rules: dict[str, dict] = field(default_factory=dict)
    # Efecto básico mientras suena un tema sin eventos cargados (hasta que se cargan, o si no tiene).
    fallback: dict = field(default_factory=lambda: {
        "fx": "wave", "params": {"color": "azul", "color2": "morado", "bpm": "live", "beats": 4}})
    # Reposo: ningún deck suena (faders abajo) o el deck que manda está vacío.
    idle: dict = field(default_factory=lambda: {
        "fx": "twinkle", "params": {"color": "blanco", "color2": "azul", "density": 0.06}})


@dataclass
class AppConfig:
    rekordbox: RekordboxConfig = field(default_factory=RekordboxConfig)
    library: LibraryConfig = field(default_factory=LibraryConfig)
    session: SessionConfig = field(default_factory=SessionConfig)
    live: LiveConfig = field(default_factory=LiveConfig)
    mixer: MixerConfig = field(default_factory=MixerConfig)
    detection: DetectionConfig = field(default_factory=DetectionConfig)
    history: HistoryConfig = field(default_factory=HistoryConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    ui: UiConfig = field(default_factory=UiConfig)
    lights: LightsConfig = field(default_factory=LightsConfig)
    base_dir: Path = PROJECT_ROOT
    source_path: Path | None = None   # config.json del que se cargó (para guardar ajustes)

    def resolve_path(self, value: str | Path) -> Path:
        """Rutas relativas del JSON se resuelven contra la raíz del proyecto."""
        p = Path(value)
        return p if p.is_absolute() else (self.base_dir / p)

    def validate(self) -> None:
        if not 2 <= self.rekordbox.decks <= 4:
            raise ValueError("rekordbox.decks debe estar entre 2 y 4")
        if self.session.camelot_strictness not in STRICTNESS_LEVELS:
            raise ValueError(
                f"session.camelot_strictness debe ser uno de {STRICTNESS_LEVELS}"
            )
        if not 0.0 <= self.session.wildcard_probability <= 1.0:
            raise ValueError("session.wildcard_probability debe estar entre 0 y 1")
        if self.session.variety < 0:
            raise ValueError("session.variety no puede ser negativo")
        if self.session.bpm_tolerance_pct < 0:
            raise ValueError("session.bpm_tolerance_pct no puede ser negativo")


def _build(cls: type, data: dict[str, Any], section: str) -> Any:
    known = {f.name: f for f in fields(cls)}
    kwargs: dict[str, Any] = {}
    for key, value in data.items():
        if key not in known:
            log.warning("Clave de configuración desconocida ignorada: %s.%s", section, key)
            continue
        f = known[key]
        default = getattr(cls(), key)
        if is_dataclass(default) and isinstance(value, dict):
            kwargs[key] = _build(type(default), value, f"{section}.{key}")
        elif isinstance(default, float) and isinstance(value, int) and not isinstance(value, bool):
            kwargs[key] = float(value)
        else:
            kwargs[key] = value
    return cls(**kwargs)


def config_from_dict(data: dict[str, Any], base_dir: Path = PROJECT_ROOT) -> AppConfig:
    cfg = AppConfig()
    for f in fields(AppConfig):
        if f.name == "base_dir":
            continue
        section = data.get(f.name)
        if section is None:
            continue
        if not isinstance(section, dict):
            raise ValueError(f"La sección '{f.name}' debe ser un objeto JSON")
        setattr(cfg, f.name, _build(type(getattr(cfg, f.name)), section, f.name))
    for key in data:
        if key not in {f.name for f in fields(AppConfig)}:
            log.warning("Sección de configuración desconocida ignorada: %s", key)
    cfg.base_dir = base_dir
    cfg.validate()
    return cfg


def load_config(path: str | Path | None = None) -> AppConfig:
    path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not path.exists():
        log.warning("No existe %s; se usan valores por defecto", path)
        return AppConfig()
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    # La raíz del proyecto es la carpeta que contiene `config/`.
    base_dir = path.resolve().parent.parent if path.parent.name == "config" else path.resolve().parent
    cfg = config_from_dict(data, base_dir=base_dir)
    cfg.source_path = path
    return cfg


def save_value(path: str | Path, keys: list[str], value) -> None:
    """Cambia un valor en config.json conservando el resto del archivo (p. ej. la latencia
    calibrada desde la ventana)."""
    from collections import OrderedDict
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=OrderedDict) if path.exists() else {}
    node = data
    for k in keys[:-1]:
        node = node.setdefault(k, OrderedDict())
    node[keys[-1]] = value
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)
