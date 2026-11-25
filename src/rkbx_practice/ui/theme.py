"""Tema visual inspirado en Rekordbox: fondo casi negro, paneles grises, acentos azules."""

from __future__ import annotations

import colorsys

BG = "#141414"          # fondo general
PANEL = "#1e1e1e"       # paneles / decks
PANEL_2 = "#262626"     # cabeceras, filas alternas
BORDER = "#333333"
TEXT = "#e6e6e6"
TEXT_DIM = "#8c8c8c"
ACCENT = "#2a8cff"      # azul Rekordbox
MASTER = "#ff8a00"      # naranja del botón MASTER
REC = "#e53935"

# (fondo, texto) por estado de fila. Tonos oscuros para no deslumbrar en cabina.
ROW_STYLES = {
    "loaded": ("#173a22", "#7ddc8f"),    # verde: ya cargado (tachado)
    "next": ("#4a4000", "#ffe14d"),      # amarillo: siguiente sugerencia
    "wildcard": ("#4d2c00", "#ffb04d"),  # naranja: comodín
    "skipped": ("#4a1717", "#ff8a80"),   # rojo: sugerencia saltada
}

QSS = f"""
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
"""


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
