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
class UiConfig:
    always_on_top: bool = True
    start_compact: bool = False
    theme: str = "rekordbox"   # "rekordbox" (oscuro) o "system"


@dataclass
class LightsConfig:
    enabled: bool = False
    esp32_ip: str = "192.168.4.1"
    esp32_port: int = 4210
    latency_compensation_ms: int = 0


@dataclass
class AppConfig:
    rekordbox: RekordboxConfig = field(default_factory=RekordboxConfig)
    library: LibraryConfig = field(default_factory=LibraryConfig)
    session: SessionConfig = field(default_factory=SessionConfig)
    live: LiveConfig = field(default_factory=LiveConfig)
    mixer: MixerConfig = field(default_factory=MixerConfig)
    detection: DetectionConfig = field(default_factory=DetectionConfig)
    history: HistoryConfig = field(default_factory=HistoryConfig)
    ui: UiConfig = field(default_factory=UiConfig)
    lights: LightsConfig = field(default_factory=LightsConfig)
    base_dir: Path = PROJECT_ROOT

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
    return config_from_dict(data, base_dir=base_dir)
