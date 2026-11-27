"""Temas visuales de la ventana.

- ``material`` (por defecto, pedido del usuario): Material Design 3 oscuro — superficies por
  capas sin bordes, esquinas redondeadas, botones en píldora, pestañas con indicador y color
  primario lila.
- ``rekordbox``: el estilo anterior, inspirado en Rekordbox (negro, gris y azul).
- ``system``: el estilo normal de Windows.

Se cambia en el menú Ver → Tema (``ui.theme``) y se aplica al momento: `apply()` actualiza los
colores de este módulo (los usan los dibujos y paneles) y devuelve la hoja de estilos.
"""

from __future__ import annotations

import colorsys

PALETTES = {
    "rekordbox": {
        "BG": "#141414", "PANEL": "#1e1e1e", "PANEL_2": "#262626", "BORDER": "#333333",
        "TEXT": "#e6e6e6", "TEXT_DIM": "#8c8c8c", "ACCENT": "#2a8cff", "ON_ACCENT": "#ffffff",
        "TONAL": "#262626", "ON_TONAL": "#e6e6e6", "MASTER": "#ff8a00", "REC": "#e53935",
        "OK": "#2e7d32",
    },
    # Material Design 3, esquema oscuro (primario lila de referencia de M3).
    "material": {
        "BG": "#141218", "PANEL": "#1d1b20", "PANEL_2": "#2b2930", "BORDER": "#49454f",
        "TEXT": "#e6e0e9", "TEXT_DIM": "#cac4d0", "ACCENT": "#d0bcff", "ON_ACCENT": "#381e72",
        "TONAL": "#4a4458", "ON_TONAL": "#e8def8", "MASTER": "#ffb871", "REC": "#f2b8b5",
        "OK": "#a8dab5",
    },
}
ROW_STYLES_BY_THEME = {
    # (fondo, texto) por estado de fila. Tonos oscuros para no deslumbrar en cabina.
    "rekordbox": {
        "loaded": ("#173a22", "#7ddc8f"),    # verde: ya cargado (tachado)
        "next": ("#4a4000", "#ffe14d"),      # amarillo: siguiente sugerencia
        "wildcard": ("#4d2c00", "#ffb04d"),  # naranja: comodín
        "skipped": ("#4a1717", "#ff8a80"),   # rojo: sugerencia saltada
    },
    "material": {
        "loaded": ("#1f3a2a", "#a8dab5"),
        "next": ("#433e00", "#f3e27a"),
        "wildcard": ("#4d2e00", "#ffb871"),
        "skipped": ("#5c1d1b", "#f2b8b5"),
    },
}

THEMES = ("material", "rekordbox", "system")
current = "material"

# Colores en uso (los cambia apply()).
BG = PANEL = PANEL_2 = BORDER = TEXT = TEXT_DIM = ACCENT = ON_ACCENT = TONAL = ON_TONAL = ""
MASTER = REC = OK = ""
ROW_STYLES: dict[str, tuple[str, str]] = {}

_REKORDBOX_QSS = """
QWidget {{
    background: {BG};
    color: {TEXT};
    font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif;
    font-size: 9pt;
}}
QToolTip {{ background: {PANEL_2}; color: {TEXT}; border: 1px solid {BORDER}; }}
QFrame#deckPanel, QFrame#suggestionPanel {{
    background: {PANEL};
    border: 1px solid {BORDER};
    border-radius: 3px;
}}
QLabel {{ background: transparent; }}
QLabel[role="dim"] {{ color: {TEXT_DIM}; }}
QLabel[role="deckNum"] {{
    background: {ACCENT}; color: #ffffff; font-weight: bold;
    border-radius: 2px; padding: 0px 6px;
}}
QLabel[role="master"] {{
    color: #000000; background: {MASTER}; font-weight: bold; font-size: 8pt;
    border-radius: 2px; padding: 0px 4px;
}}
QLabel[role="bpm"] {{ font-size: 16pt; font-weight: bold; color: #ffffff; }}
QPushButton {{
    background: {PANEL_2}; color: {TEXT};
    border: 1px solid {BORDER}; border-radius: 3px; padding: 4px 10px;
}}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:pressed {{ background: #333333; }}
QPushButton:checked {{ background: {REC}; color: #ffffff; border-color: {REC}; }}
QComboBox, QDoubleSpinBox {{
    background: {PANEL_2}; color: {TEXT};
    border: 1px solid {BORDER}; border-radius: 3px; padding: 2px 6px;
}}
QComboBox QAbstractItemView {{
    background: {PANEL}; color: {TEXT};
    selection-background-color: {ACCENT}; selection-color: #ffffff;
}}
QCheckBox {{ background: transparent; }}
QTableWidget {{
    background: {PANEL}; alternate-background-color: #191919;
    gridline-color: transparent; border: 1px solid {BORDER};
}}
QHeaderView::section {{
    background: {PANEL_2}; color: {TEXT_DIM};
    border: none; border-right: 1px solid {BORDER}; padding: 3px 6px;
}}
QTableWidget::item {{ padding: 2px 4px; }}
QScrollBar:vertical {{ background: {PANEL}; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: #444444; min-height: 24px; border-radius: 4px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QProgressBar {{ background: #0c0c0c; border: 1px solid {BORDER}; border-radius: 2px; }}
QProgressBar::chunk {{ background: {ACCENT}; }}
QStatusBar {{ background: {PANEL_2}; color: {TEXT_DIM}; }}
QMenuBar {{ background: {PANEL}; border-bottom: 1px solid {BORDER}; }}
QMenuBar::item {{ padding: 4px 10px; background: transparent; }}
QMenuBar::item:selected {{ background: {PANEL_2}; }}
QMenu {{ background: {PANEL}; border: 1px solid {BORDER}; }}
QMenu::item {{ padding: 5px 24px 5px 20px; }}
QMenu::item:selected {{ background: {ACCENT}; color: #ffffff; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 8px; }}
QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 3px; top: -1px; }}
QTabBar::tab {{ background: {PANEL}; border: 1px solid {BORDER}; padding: 5px 16px; margin-right: 2px;
                border-top-left-radius: 3px; border-top-right-radius: 3px; }}
QTabBar::tab:selected {{ background: {PANEL_2}; color: #ffffff; border-bottom-color: {PANEL_2}; }}
QPushButton[role="tile"] {{ text-align: left; padding: 10px 14px; font-size: 10pt; border-radius: 6px; }}
QPushButton[role="bigToggle"] {{ font-size: 12pt; font-weight: bold; padding: 10px; border-radius: 6px; }}
QPushButton[role="bigToggle"]:checked {{ background: {OK}; border-color: {OK}; }}
QPushButton[role="primary"] {{ background: {MASTER}; color: #111111; font-weight: bold; }}
QFrame#card {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 10px; }}
"""

_MATERIAL_QSS = """
QWidget {{
    background: {BG}; color: {TEXT};
    font-family: "Roboto", "Segoe UI Variable Text", "Segoe UI", Arial, sans-serif;
    font-size: 10pt;
}}
QToolTip {{ background: {PANEL_2}; color: {TEXT}; border: none; border-radius: 6px; padding: 6px 8px; }}
QLabel {{ background: transparent; }}
QLabel[role="dim"] {{ color: {TEXT_DIM}; }}
QFrame#deckPanel, QFrame#suggestionPanel {{
    background: {PANEL}; border: none; border-radius: 16px;
}}
QLabel[role="deckNum"] {{
    background: {ACCENT}; color: {ON_ACCENT}; font-weight: bold;
    border-radius: 10px; padding: 1px 9px;
}}
QLabel[role="master"] {{
    color: #2b1700; background: {MASTER}; font-weight: bold; font-size: 8pt;
    border-radius: 9px; padding: 1px 8px;
}}
QLabel[role="bpm"] {{ font-size: 18pt; font-weight: 500; color: {TEXT}; }}
QPushButton {{
    background: {TONAL}; color: {ON_TONAL};
    border: none; border-radius: 18px; padding: 8px 18px; font-weight: 500;
}}
QPushButton:hover {{ background: #5a536a; }}
QPushButton:pressed {{ background: #635b74; }}
QPushButton:disabled {{ background: #2b2930; color: #6f6a76; }}
QPushButton:checked {{ background: {REC}; color: #601410; }}
QToolButton {{ background: {TONAL}; color: {ON_TONAL}; border: none; border-radius: 15px; }}
QToolButton:hover {{ background: #5a536a; }}
QToolButton:disabled {{ background: #2b2930; color: #6f6a76; }}
QComboBox, QDoubleSpinBox, QSpinBox, QLineEdit {{
    background: transparent; color: {TEXT};
    border: 1px solid {BORDER}; border-radius: 8px; padding: 5px 10px;
    selection-background-color: {ACCENT}; selection-color: {ON_ACCENT};
}}
QComboBox:hover, QDoubleSpinBox:hover, QSpinBox:hover, QLineEdit:hover {{ border-color: {TEXT_DIM}; }}
QComboBox:focus, QDoubleSpinBox:focus, QSpinBox:focus, QLineEdit:focus {{ border: 2px solid {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {PANEL_2}; color: {TEXT}; border: none; border-radius: 8px; padding: 4px;
    selection-background-color: {TONAL}; selection-color: {ON_TONAL}; outline: none;
}}
QCheckBox {{ background: transparent; spacing: 8px; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 4px; border: 2px solid {TEXT_DIM}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QTableWidget, QListWidget, QPlainTextEdit {{
    background: {PANEL}; alternate-background-color: #211f26;
    gridline-color: transparent; border: none; border-radius: 12px;
    selection-background-color: {TONAL}; selection-color: {ON_TONAL};
}}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{
    background: {PANEL}; color: {TEXT_DIM}; border: none;
    border-bottom: 1px solid {BORDER}; padding: 6px 8px; font-weight: 500;
}}
QTableWidget::item {{ padding: 3px 6px; }}
QListWidget::item {{ padding: 6px 8px; border-radius: 8px; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 4px 0; }}
QScrollBar::handle:vertical {{ background: {BORDER}; min-height: 28px; border-radius: 4px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 8px; }}
QScrollBar::handle:horizontal {{ background: {BORDER}; border-radius: 4px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QProgressBar {{ background: {PANEL_2}; border: none; border-radius: 3px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}
QStatusBar {{ background: {PANEL}; color: {TEXT_DIM}; }}
QMenuBar {{ background: {BG}; padding: 2px 4px; }}
QMenuBar::item {{ padding: 6px 12px; background: transparent; border-radius: 14px; }}
QMenuBar::item:selected {{ background: {PANEL_2}; }}
QMenu {{ background: {PANEL_2}; border: none; border-radius: 10px; padding: 6px 0; }}
QMenu::item {{ padding: 8px 28px 8px 16px; border-radius: 0; }}
QMenu::item:selected {{ background: #36343b; }}
QMenu::item:checked {{ color: {ACCENT}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 6px 0; }}
QTabWidget::pane {{ border: none; top: 0; }}
QTabBar {{ qproperty-drawBase: 0; }}
QTabBar::tab {{
    background: transparent; color: {TEXT_DIM}; border: none;
    border-bottom: 3px solid transparent; padding: 10px 20px; margin: 0 2px; font-weight: 500;
}}
QTabBar::tab:hover {{ color: {TEXT}; background: {PANEL}; border-top-left-radius: 8px; border-top-right-radius: 8px; }}
QTabBar::tab:selected {{ color: {ACCENT}; border-bottom: 3px solid {ACCENT}; }}
QDockWidget {{ color: {TEXT_DIM}; }}
QDockWidget::title {{ background: {PANEL}; padding: 8px 12px; }}
QDialog {{ background: {BG}; }}
QMessageBox {{ background: {PANEL_2}; }}
QPushButton[role="tile"] {{
    text-align: left; padding: 14px 18px; font-size: 10pt; border-radius: 16px;
    background: {PANEL}; color: {TEXT};
}}
QPushButton[role="tile"]:hover {{ background: {PANEL_2}; }}
QPushButton[role="bigToggle"] {{
    font-size: 13pt; font-weight: 500; padding: 14px; border-radius: 28px;
    background: {TONAL}; color: {ON_TONAL};
}}
QPushButton[role="bigToggle"]:checked {{ background: {ACCENT}; color: {ON_ACCENT}; }}
QPushButton[role="primary"] {{ background: {ACCENT}; color: {ON_ACCENT}; }}
QPushButton[role="primary"]:hover {{ background: #e0d2ff; }}
QFrame#card {{ background: {PANEL}; border: none; border-radius: 16px; }}
"""


def apply(name: str | None) -> str:
    """Activa un tema: actualiza los colores del módulo y devuelve la hoja de estilos ("" = sistema)."""
    global current, ROW_STYLES
    name = name if name in THEMES else "material"
    current = name
    pal = PALETTES["rekordbox" if name == "system" else name]
    globals().update(pal)
    ROW_STYLES = ROW_STYLES_BY_THEME["rekordbox" if name == "system" else name]
    if name == "system":
        return ""
    return (_MATERIAL_QSS if name == "material" else _REKORDBOX_QSS).format(**pal)


QSS = apply("material")    # compatibilidad: hoja de estilos por defecto



def camelot_color(code: str) -> str | None:
    """Color de la rueda Camelot (1 = rojizo … 12, como un círculo de tonos). None si no aplica."""
    try:
        n = int(code[:-1])
        minor = code[-1].upper() == "A"
    except (ValueError, IndexError):
        return None
    hue = ((n - 1) / 12.0 + 0.95) % 1.0
    r, g, b = colorsys.hsv_to_rgb(hue, 0.55 if minor else 0.40, 0.95)
    return "#{:02x}{:02x}{:02x}".format(int(r * 255), int(g * 255), int(b * 255))
