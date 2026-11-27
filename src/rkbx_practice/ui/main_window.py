"""Ventana principal (PySide6): flotante, siempre visible, redimensionable y compactable.

Pedido del usuario: menú arriba al estilo Windows, multidioma (español/inglés) y separado por
módulos:

- **Práctica**: sesión (probar las canciones: lista, sugerencias, tachado) y grabación.
- **Luces**: activar las luces y sus herramientas (efectos por tema, mis tiras, simulador,
  latencia). Se puede usar junto a la práctica (pestañas) o solo (`ui.module: "lights"` o
  `ui --module lights`).
"""

from __future__ import annotations

import copy
import logging
import time

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QBrush, QColor, QFont, QKeySequence
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog,
                               QDockWidget, QDoubleSpinBox, QFrame, QGridLayout, QHBoxLayout, QHeaderView,
                               QLabel, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
                               QPlainTextEdit, QProgressBar, QPushButton, QSplitter, QTabWidget,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from . import theme
from .i18n import LANGUAGES, language, set_language, tr
from ..app import PracticeController
from ..config import STRICTNESS_LEVELS, AppConfig
from ..events import BassSwap, CutBurst, TrackLoaded, TransitionEvent
from ..library.memory_library import InMemoryLibrary
from ..library.models import NodeKind
from ..state import ConnectionMode, ConnectionStatus, LiveSnapshot

log = logging.getLogger(__name__)

STRICTNESS_LABELS = {"strict": "Estricto", "moderate": "Moderado", "relaxed": "Relajado"}
MODE_COLORS = {ConnectionMode.FULL: "#4caf50", ConnectionMode.LIMITED: "#ff9800",
               ConnectionMode.DISCONNECTED: "#e53935"}
MODULES = ("all", "practice", "lights")


class LiveBridge(QObject):
    """Pasa los avisos del hilo de sondeo al hilo de la interfaz (señales en cola)."""

    status_changed = Signal(object)
    snapshot = Signal(object)
    events = Signal(object)

    def on_status(self, status: ConnectionStatus) -> None:
        self.status_changed.emit(status)

    def on_snapshot(self, snap: LiveSnapshot) -> None:
        self.snapshot.emit(snap)

    def on_events(self, events) -> None:
        self.events.emit(list(events))


def _field_grid(parent_layout: QGridLayout, row0: int, names: list[str]) -> dict[str, QLabel]:
    """Filas «Nombre: valor» alineadas. Devuelve las etiquetas de valor (por nombre en español)."""
    values = {}
    for i, name in enumerate(names):
        k = QLabel(tr(name))
        k.setProperty("role", "dim")
        v = QLabel("—")
        v.setTextInteractionFlags(Qt.TextSelectableByMouse)
        parent_layout.addWidget(k, row0 + i, 0, alignment=Qt.AlignTop)
        parent_layout.addWidget(v, row0 + i, 1)
        values[name] = v
    parent_layout.setColumnStretch(1, 1)
    return values


class SuggestionPanel(QFrame):
    """Siguiente sugerencia con campos estructurados: Tema, BPM, Clave."""

    def __init__(self):
        super().__init__()
        self.setObjectName("suggestionPanel")
        lay = QGridLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        self.header = QLabel(tr("Genera una sesión para empezar"))
        self.header.setObjectName("sugHeader")
        f = QFont()
        f.setPointSize(f.pointSize() + 2)
        f.setBold(True)
        self.header.setFont(f)
        lay.addWidget(self.header, 0, 0, 1, 2)
        self.fields = _field_grid(lay, 1, ["Tema", "BPM", "Clave"])
        big = QFont()
        big.setPointSize(big.pointSize() + 2)
        big.setBold(True)
        for v in self.fields.values():
            v.setFont(big)
            v.setWordWrap(True)
        self.set_color("next")

    def set_color(self, color: str) -> None:
        """`color` es una clave de theme.ROW_STYLES o un color de fondo."""
        _bg, fg = theme.ROW_STYLES.get(color, (color, "#ffffff"))
        border = "none" if theme.current == "material" else f"1px solid {theme.BORDER}"
        radius = 16 if theme.current == "material" else 3
        self.setStyleSheet(f"#suggestionPanel {{ background: {theme.PANEL}; border: {border};"
                           f" border-left: 6px solid {fg}; border-radius: {radius}px; }}"
                           f" QLabel {{ color: {theme.TEXT}; }}"
                           f" QLabel#sugHeader {{ color: {fg}; }}")

    def show_message(self, text: str) -> None:
        self.header.setText(text)
        for v in self.fields.values():
            v.setText("—")

    def show_track(self, header: str, title: str, bpm: str, key: str) -> None:
        self.header.setText(header)
        self.fields["Tema"].setText(title)
        self.fields["BPM"].setText(bpm)
        self.fields["Clave"].setText(key)


class DeckWidget(QFrame):
    def __init__(self, deck: int):
        super().__init__()
        self.deck = deck
        self.setObjectName("deckPanel")
        lay = QGridLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        head = QHBoxLayout()
        num = QLabel(str(deck))
        num.setProperty("role", "deckNum")
        self.master = QLabel("MASTER")
        self.master.setProperty("role", "master")
        self.master.setVisible(False)
        self.bpm = QLabel("—")
        self.bpm.setProperty("role", "bpm")
        head.addWidget(num)
        head.addWidget(self.master)
        head.addStretch(1)
        head.addWidget(self.bpm)
        lay.addLayout(head, 0, 0, 1, 2)
        self.fields = _field_grid(lay, 1, ["Tema", "Clave"])
        self.fields["Tema"].setWordWrap(True)
        self.fields["Tema"].setMinimumWidth(120)
        self.fader = QProgressBar()
        self.fader.setRange(0, 100)
        self.fader.setTextVisible(False)
        self.fader.setFixedHeight(6)
        self.fader.setToolTip(tr("Fader de canal"))
        lay.addWidget(self.fader, 3, 0, 1, 2)

    def update_state(self, title: str, bpm: float | None, key: str, fader: float | None,
                     master: bool | None):
        self.fields["Tema"].setText(title)
        self.bpm.setText(f"{bpm:.2f}" if bpm else "—")
        self.fields["Clave"].setText(key)
        color = theme.camelot_color(key)
        self.fields["Clave"].setStyleSheet(f"color: {color}; font-weight: bold;" if color else "")
        self.fader.setValue(int(round((fader or 0.0) * 100)))
        self.master.setVisible(bool(master))


class HistoryDialog(QDialog):
    """Sesiones grabadas: lista a la izquierda, resumen a la derecha."""

    def __init__(self, cfg: AppConfig, parent=None, open_review=None):
        super().__init__(parent)
        self._open_review = open_review
        from ..history import list_sessions, load_session, summarize
        self._load, self._summarize = load_session, summarize
        self.setWindowTitle(tr("Histórico de sesiones"))
        self.resize(820, 520)
        lay = QVBoxLayout(self)
        split = QSplitter(Qt.Horizontal)
        self.list = QListWidget()
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.Monospace)
        self.text.setFont(mono)
        split.addWidget(self.list)
        split.addWidget(self.text)
        split.setSizes([260, 560])
        lay.addWidget(split)
        listen = QPushButton(tr("🎧 Escuchar en el navegador"))
        listen.setToolTip(tr("Abre la página de revisión: forma de onda de la sesión con transiciones, "
                             "fader cuts y bass swaps; clic para escuchar desde ese punto"))
        listen.clicked.connect(self._listen)
        lay.addWidget(listen)
        for path in list_sessions(cfg.resolve_path(cfg.history.dir)):
            try:
                data = load_session(path)
                label = (f"{data.get('started_at', '')[:16].replace('T', ' ')}  ·  "
                         f"{data.get('source') or '—'}  ·  {len(data.get('transitions', []))} trans.")
            except Exception:  # noqa: BLE001
                label = tr("{path} (ilegible)", path=path.name)
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, str(path))
            self.list.addItem(item)
        self.list.currentItemChanged.connect(self._select)
        if self.list.count():
            self.list.setCurrentRow(0)
        else:
            self.text.setPlainText(tr("Todavía no hay sesiones grabadas.\n\n"
                                      "Pulsa «Iniciar grabación» para grabar una."))

    def _listen(self) -> None:
        import webbrowser
        if self._open_review is None:
            return
        try:
            url = self._open_review()
        except OSError as exc:
            QMessageBox.warning(self, tr("Revisión"), tr("No se pudo abrir la página de revisión: {error}",
                                                         error=exc))
            return
        item = self.list.currentItem()
        if item is not None:
            from pathlib import Path as _P
            url += f"?s={_P(item.data(Qt.UserRole)).stem}"
        webbrowser.open(url)

    def _select(self, item, _prev=None) -> None:
        if item is None:
            return
        try:
            self.text.setPlainText(self._summarize(self._load(item.data(Qt.UserRole))))
        except Exception as exc:  # noqa: BLE001
            self.text.setPlainText(tr("No se pudo leer la sesión: {error}", error=exc))


# ---------------------------------------------------------------------------
# Módulo Práctica: probar las canciones (sesión) y grabación
# ---------------------------------------------------------------------------
class PracticePage(QWidget):
    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        cfg, library = win.cfg, win.library
        self.ctl, self.cfg, self.library = win.ctl, cfg, library
        self._record_started: float | None = None
        self._auto_stop_msg: str | None = None
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)

        # --- selector de carpeta/playlist, ajustes y grabación ----------
        self.settings_box = QWidget()
        sl = QGridLayout(self.settings_box)
        sl.setContentsMargins(0, 0, 0, 0)
        self.source_combo = QComboBox()
        for r in library.tree():
            for depth, node in r.walk():
                prefix = "    " * depth + ("📁 " if node.kind == NodeKind.FOLDER else "")
                self.source_combo.addItem(prefix + node.name, node.id)
        self.generate_button = QPushButton(tr("Generar sesión"))
        self.generate_button.setProperty("role", "primary")
        self.generate_button.clicked.connect(self.generate)
        self.tolerance = QDoubleSpinBox()
        self.tolerance.setRange(0.5, 20.0)
        self.tolerance.setSingleStep(0.5)
        self.tolerance.setSuffix(tr(" % BPM"))
        self.tolerance.setValue(cfg.session.bpm_tolerance_pct)
        self.camelot_check = QCheckBox(tr("Camelot"))
        self.camelot_check.setChecked(cfg.session.camelot_enabled)
        self.strictness = QComboBox()
        for s in STRICTNESS_LEVELS:
            self.strictness.addItem(tr(STRICTNESS_LABELS[s]), s)
        self.strictness.setCurrentIndex(STRICTNESS_LEVELS.index(cfg.session.camelot_strictness))
        self.camelot_check.toggled.connect(self.strictness.setEnabled)
        self.strictness.setEnabled(cfg.session.camelot_enabled)
        sl.addWidget(self.source_combo, 0, 0, 1, 3)
        sl.addWidget(self.generate_button, 0, 3)
        sl.addWidget(self.tolerance, 1, 0)
        sl.addWidget(self.camelot_check, 1, 1)
        sl.addWidget(self.strictness, 1, 2)
        root.addWidget(self.settings_box)

        rec_row = QHBoxLayout()
        self.rec_button = QPushButton(tr("● Iniciar grabación"))
        self.rec_button.setCheckable(True)
        self.rec_button.clicked.connect(win.set_recording)
        rec_row.addWidget(self.rec_button, 1)
        root.addLayout(rec_row)

        # --- sugerencia y lista ------------------------------------------
        self.suggestion = SuggestionPanel()
        root.addWidget(self.suggestion)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["#", tr("Tema"), tr("BPM"), tr("Camelot")])
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setDefaultSectionSize(22)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.cellClicked.connect(self._row_clicked)
        self.table.setToolTip(tr("Verde: cargado (clic para destachar) · Amarillo: siguiente · "
                                 "Naranja: comodín · Rojo: te lo saltaste"))
        root.addWidget(self.table, 1)
        self.info_label = QLabel("")
        self.info_label.setWordWrap(True)
        self.info_label.setStyleSheet("color: #ff9800;")
        root.addWidget(self.info_label)

        # --- decks -------------------------------------------------------
        decks_box = QWidget()
        dl = QGridLayout(decks_box)
        dl.setContentsMargins(0, 0, 0, 0)
        self.deck_widgets = {}
        for n in range(1, cfg.rekordbox.decks + 1):
            w = DeckWidget(n)
            self.deck_widgets[n] = w
            dl.addWidget(w, (n - 1) // 2, (n - 1) % 2)
        root.addWidget(decks_box)

    def status(self, msg: str, ms: int = 4000) -> None:
        self.win.statusBar().showMessage(msg, ms)

    # --- sesión ----------------------------------------------------------
    def _current_settings(self):
        s = copy.deepcopy(self.cfg.session)
        s.bpm_tolerance_pct = float(self.tolerance.value())
        s.camelot_enabled = self.camelot_check.isChecked()
        s.camelot_strictness = self.strictness.currentData()
        return s

    def generate(self) -> None:
        node_id = self.source_combo.currentData()
        if node_id is None:
            return
        if self.ctl.session is not None and self.ctl.session.struck:
            ans = QMessageBox.question(self, tr("Nueva sesión"),
                                       tr("Se perderá el tachado de la sesión actual. ¿Continuar?"))
            if ans != QMessageBox.Yes:
                return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            loaded, loaded_deck = self.win.currently_loaded()
            session = self.ctl.generate(node_id, self._current_settings(),
                                        loaded=loaded, loaded_deck=loaded_deck)
        finally:
            QApplication.restoreOverrideCursor()
        if session.excluded:
            names = ", ".join(t.display for t, _ in session.excluded[:5])
            more = "…" if len(session.excluded) > 5 else ""
            self.info_label.setText(tr("{n} tema(s) sin analizar (sin BPM o tonalidad) excluidos: {names}",
                                       n=len(session.excluded), names=names + more))
        else:
            self.info_label.setText("")
        self.refresh_session()

    def refresh_session(self) -> None:
        s = self.ctl.session
        if s is None:
            self.table.setRowCount(0)
            return
        sug = s.suggestion
        entries = s.entries()
        ids = [t.id for t, _ in entries]
        current_ids = [self.table.item(r, 1).data(Qt.UserRole) if self.table.item(r, 1) else None
                       for r in range(self.table.rowCount())]
        rebuild = ids != current_ids      # solo al generar una sesión nueva
        if rebuild:
            self.table.setRowCount(0)
            self.table.setRowCount(len(entries))
        for row, (track, struck) in enumerate(entries):
            cells = [str(row + 1), track.display, f"{track.bpm:.1f}" if track.bpm else "—", track.camelot_str]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text) if rebuild else self.table.item(row, col)
                item.setData(Qt.UserRole, track.id)
                font = item.font()
                font.setStrikeOut(False)
                item.setFont(font)
                item.setBackground(QBrush())
                item.setForeground(QBrush())
                # Verde = ya cargado (tachado) · amarillo = siguiente sugerencia (naranja si es
                # comodín) · rojo = sugerencia que te saltaste (sigue disponible).
                state = None
                if struck:
                    font = item.font()
                    font.setStrikeOut(True)
                    item.setFont(font)
                    state = "loaded"
                elif sug is not None and track.id == sug.track.id:
                    state = "wildcard" if sug.is_wildcard else "next"
                elif s.is_skipped(track.id):
                    state = "skipped"
                if state:
                    bg, fg = theme.ROW_STYLES[state]
                    item.setBackground(QBrush(QColor(bg)))
                    item.setForeground(QBrush(QColor(fg)))
                elif col == 3 and theme.camelot_color(text):
                    item.setForeground(QBrush(QColor(theme.camelot_color(text))))
                if col in (0, 2, 3):
                    item.setTextAlignment(Qt.AlignCenter)
                if rebuild:
                    self.table.setItem(row, col, item)
        if sug is not None and rebuild:
            row = next((i for i, (t, _) in enumerate(entries) if t.id == sug.track.id), None)
            if row is not None:
                self.table.scrollToItem(self.table.item(row, 1), QAbstractItemView.EnsureVisible)
        self._refresh_suggestion()

    def _refresh_suggestion(self) -> None:
        s = self.ctl.session
        if s is None:
            self.suggestion.set_color("next")
            self.suggestion.show_message(tr("Genera una sesión para empezar"))
            return
        sug = s.suggestion
        if sug is None:
            self.suggestion.set_color("loaded")
            self.suggestion.show_message(tr("¡Sesión completada!"))
            return
        t = sug.track
        header = tr("🃏 COMODÍN (fuera de orden, compatible)") if sug.is_wildcard else tr("Siguiente")
        key = t.camelot_str
        if sug.from_anchor is not None and sug.from_anchor.key is not None:
            key += f"  ({sug.from_anchor.key.label_es})"
        self.suggestion.set_color("wildcard" if sug.is_wildcard else "next")
        self.suggestion.show_track(header, t.display, f"{t.bpm:.1f}" if t.bpm else "—", key)

    def _row_clicked(self, row: int, _col: int) -> None:
        s = self.ctl.session
        item = self.table.item(row, 1)
        if s is None or item is None:
            return
        tid = item.data(Qt.UserRole)
        if s.is_struck(tid):
            s.unstrike(tid)
            self.status(tr("Destachado: {title}", title=item.text()))
            self.refresh_session()

    # --- en vivo -----------------------------------------------------------
    def on_events(self, events) -> None:
        unknown = self.ctl.apply_events(events)
        loaded = [e for e in events if isinstance(e, TrackLoaded)]
        if loaded:
            self.refresh_session()
            s = self.ctl.session
            for ev in loaded:
                t = self.library.track(ev.track_id) if ev.track_id else None
                if t is None:
                    continue
                if s is not None and s.contains(t.id):
                    msg = tr("Deck {deck}: tachado «{title}»", deck=ev.deck, title=t.display)
                else:
                    msg = tr("Deck {deck}: «{title}» no está en la sesión (no se tacha)",
                             deck=ev.deck, title=t.display)
                self.status(msg, 6000)
        for ev in unknown:
            self.status(tr("Deck {deck}: tema no encontrado en master.db ({path})",
                           deck=ev.deck, path=ev.track_path), 8000)
        for ev in events:
            if isinstance(ev, TransitionEvent):
                self.status(tr("Transición {a}→{b} ({s:.1f} s)", a=ev.out_deck, b=ev.in_deck,
                               s=ev.duration_s), 5000)
            elif isinstance(ev, CutBurst):
                self.status(tr("Fader cuts en deck {deck}: {n}", deck=ev.deck, n=ev.count))
            elif isinstance(ev, BassSwap):
                self.status(tr("Bass swap {a}→{b}", a=ev.from_deck, b=ev.to_deck))

    def refresh(self, snap: LiveSnapshot | None) -> None:
        if snap is not None:
            for d in snap.decks:
                w = self.deck_widgets.get(d.deck)
                if w is None:
                    continue
                t = self.library.track(d.track_id) if d.track_id else None
                title = t.display if t else (tr("(no está en la biblioteca)") if d.track_path else "—")
                w.update_state(title, d.bpm, t.camelot_str if t else "—", d.fader, d.is_master)
        self._refresh_recording()

    # --- grabación -----------------------------------------------------------
    def _refresh_recording(self) -> None:
        if self.ctl.recorder.recording and self.ctl.audio_auto_stopped:
            self._auto_stop_msg = tr("Grabación parada sola: {mins:g} min sin sonido "
                                     "(cortada donde dejó de sonar)",
                                     mins=self.cfg.audio.auto_stop_silence_s / 60)
            self.win.set_recording(False)               # guarda la sesión (cortada)
            return
        if not (self.ctl.recorder.recording and self._record_started is not None):
            return
        c = self.ctl.recorder.counts()
        secs = int(time.monotonic() - self._record_started)
        clock = f"{secs // 60:02d}:{secs % 60:02d}"
        audio = self.ctl.audio
        if audio is None:
            a_txt = tr("audio desactivado (audio.enabled)")
        elif audio.error:
            a_txt = tr("SIN AUDIO: {error}", error=audio.error)
        elif audio.level_db is None or audio.started_wall is None:
            a_txt = tr("audio: esperando a que suene algo (aún no se graba)")
        elif audio.silence_s >= 10:
            left = max(0, self.cfg.audio.auto_stop_silence_s - audio.silence_s)
            a_txt = tr("audio: {s:.0f} s sin sonido (se para sola en {left:.0f} s)", s=audio.silence_s,
                       left=left)
        else:
            a_txt = tr("audio {db:.0f} dB", db=audio.level_db) + (tr(" (silencio)") if audio.level_db < -60 else "")
        no_audio = audio is not None and audio.error
        self.rec_button.setText(tr("■ Parar grabación {t} (sin audio)" if no_audio else "■ Parar grabación {t}",
                                   t=clock))
        self.rec_button.setToolTip(tr("Temas {tracks} · transiciones {tr} · cuts {cuts} · bass swaps {swaps} · {audio}",
                                      tracks=c.get("tracks", 0), tr=c.get("transitions", 0),
                                      cuts=c.get("fader_cuts", 0), swaps=c.get("bass_swaps", 0), audio=a_txt))

    def recording_changed(self, on: bool) -> None:
        """Lo llama la ventana al empezar o parar la grabación."""
        self.rec_button.blockSignals(True)
        self.rec_button.setChecked(on)
        self.rec_button.blockSignals(False)
        if on:
            self._record_started = time.monotonic()
            audio = self.ctl.audio
            if audio is not None and audio.error:
                self.status(tr("Grabando sesión SIN AUDIO: {error}", error=audio.error), 15000)
            elif audio is not None:
                self.status(tr("Grabando sesión y audio ({device})…", device=audio.device_name), 5000)
            else:
                self.status(tr("Grabando sesión…"), 3000)
        else:
            self._record_started = None
            self.rec_button.setText(tr("● Iniciar grabación"))
            self.rec_button.setToolTip("")

    def saved(self, path) -> None:
        msg, self._auto_stop_msg = self._auto_stop_msg, None
        if path:
            self.status((msg + " · " if msg else "") + tr("Sesión guardada en {path}", path=path),
                        20000 if msg else 10000)

    def set_compact(self, compact: bool) -> None:
        for w in (self.settings_box, self.table, self.info_label):
            w.setVisible(not compact)


# ---------------------------------------------------------------------------
# Módulo Luces (puede usarse solo, sin la práctica)
# ---------------------------------------------------------------------------
class LightsPage(QWidget):
    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        self.ctl = win.ctl
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        self.toggle = QPushButton()
        self.toggle.setProperty("role", "bigToggle")
        self.toggle.setCheckable(True)
        self.toggle.clicked.connect(win.set_lights)
        root.addWidget(self.toggle)
        hint = QLabel(tr("Las luces siguen la música que suena en Rekordbox."))
        hint.setProperty("role", "dim")
        hint.setWordWrap(True)
        root.addWidget(hint)

        box = QFrame()
        box.setObjectName("deckPanel")
        grid = QGridLayout(box)
        self.fields = _field_grid(grid, 0, ["Ahora", "Último efecto", "Destino"])
        for v in self.fields.values():
            v.setWordWrap(True)
        root.addWidget(box)

        tiles = QGridLayout()
        tiles.setSpacing(8)
        items = [
            ("🎚  " + tr("Efectos por tema"), tr("Mira y cambia qué efecto sale en cada parte de una canción."),
             win.show_lights_editor),
            ("💡  " + tr("Mis tiras"), tr("Cuántas tiras tienes, sus luces y en cuántas partes se dividen."),
             win.show_layout),
            ("🖥  " + tr("Simulador"), tr("Ver las luces en el navegador, sin el ESP32."), win.open_simulator),
            ("⏱  " + tr("Latencia"), tr("Ajustar el retraso para que las luces vayan con el bombo."),
             win.show_latency),
            ("👁  " + tr("Ver luces aquí"), tr("Las luces en un lateral de esta ventana, sin abrir el navegador."),
             lambda: win.set_light_view(True)),
        ]
        for i, (title, desc, fn) in enumerate(items):
            b = QPushButton(f"{title}\n{desc}")
            b.setProperty("role", "tile")
            b.setMinimumHeight(58)
            b.clicked.connect(fn)
            tiles.addWidget(b, i // 2, i % 2)
        root.addLayout(tiles)
        root.addStretch(1)
        self.set_enabled_state(win.cfg.lights.enabled)

    def set_enabled_state(self, on: bool) -> None:
        self.toggle.blockSignals(True)
        self.toggle.setChecked(on)
        self.toggle.blockSignals(False)
        self.toggle.setText(("💡  " + tr("Luces encendidas") + "  ·  " + tr("Apagar luces")) if on
                            else ("○  " + tr("Luces apagadas") + "  ·  " + tr("Encender luces")))

    def refresh(self, snap: LiveSnapshot | None) -> None:
        lights = self.ctl._lights
        if lights is None:
            return
        eng = lights.engine
        status = eng.status
        lead = eng.lead_deck
        if snap is not None and lead is not None:
            d = snap.deck(lead)
            if d is not None:
                t = self.win.library.track(d.track_id) if d.track_id else None
                if d.track_id in lights.problems:
                    status = tr("Deck {deck}: sin luces: {why}", deck=lead, why=lights.problems[d.track_id])
                elif t is not None:
                    status = tr("Deck {deck}", deck=lead) + f": {t.display}\n{status}"
        self.fields["Ahora"].setText(status if eng.enabled else "—")
        if eng.last_sent is not None:
            ev, _t = eng.last_sent
            self.fields["Último efecto"].setText(f"{ev.label} → {ev.fx}")
        describe = getattr(lights.sender, "describe", None)
        dest = tr("enviados {n} a {dest}", n=lights.sender.sent, dest=describe() if describe else "")
        if lights.sender.errors:
            dest += " · " + tr("{n} errores de envío", n=lights.sender.errors)
        self.fields["Destino"].setText(dest)


# ---------------------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self, cfg: AppConfig, library: InMemoryLibrary, controller: PracticeController,
                 module: str | None = None):
        super().__init__()
        self.cfg = cfg
        self.library = library
        self.ctl = controller
        self.module = module if module in MODULES else (cfg.ui.module if cfg.ui.module in MODULES else "all")
        self._last_snapshot: LiveSnapshot | None = None
        self._compact = False

        self.setWindowTitle(tr("Práctica Rekordbox") if self.module != "lights" else "Rekordbox — " + tr("Luces"))
        if cfg.ui.always_on_top:
            self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.resize(480, 760)

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 6, 8, 8)
        self.setCentralWidget(central)
        self.conn_label = QLabel(tr("● Buscando Rekordbox…"))
        self.conn_label.setWordWrap(True)
        root.addWidget(self.conn_label)

        self.practice = PracticePage(self) if self.module in ("all", "practice") else None
        self.lights_page = LightsPage(self) if self.module in ("all", "lights") else None
        self.tabs = QTabWidget()
        if self.practice is not None:
            self.tabs.addTab(self.practice, "🎧  " + tr("Práctica"))
        if self.lights_page is not None:
            self.tabs.addTab(self.lights_page, "💡  " + tr("Luces"))
        self.tabs.tabBar().setVisible(self.tabs.count() > 1)
        root.addWidget(self.tabs, 1)

        self._build_menu()
        self.statusBar().showMessage(tr("Listo"))
        if cfg.lights.enabled or self.module == "lights":
            self.set_lights(True)

        # Refresco a ~10 Hz (el sondeo va a 50 Hz).
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(100)
        if cfg.ui.start_compact:
            self.toggle_compact()
        self._light_dock: QDockWidget | None = None
        if cfg.ui.light_view and self.lights_page is not None:
            QTimer.singleShot(300, lambda: self.set_light_view(True))

    # --- menú ------------------------------------------------------------------
    def _action(self, menu, text: str, slot=None, shortcut: str | None = None, checkable: bool = False,
                tip: str | None = None) -> QAction:
        act = QAction(tr(text), self)
        if shortcut:
            act.setShortcut(QKeySequence(shortcut))
        act.setCheckable(checkable)
        if tip:
            act.setStatusTip(tr(tip))
        if slot is not None:
            act.triggered.connect(slot)
        menu.addAction(act)
        return act

    def _build_menu(self) -> None:
        mb = self.menuBar()
        m = mb.addMenu(tr("&Archivo"))
        self._action(m, "&Salir", self.close, "Ctrl+Q")

        if self.practice is not None:
            m = mb.addMenu(tr("&Práctica"))
            self._action(m, "&Generar sesión", self.practice.generate, "Ctrl+G")
            self.act_record = self._action(m, "Iniciar / parar &grabación", self.set_recording, "Ctrl+R",
                                           checkable=True)
            m.addSeparator()
            self._action(m, "&Histórico…", self.show_history, "Ctrl+H")
            self._action(m, "&Escuchar grabaciones (navegador)", self.open_review)
        else:
            self.act_record = None

        if self.lights_page is not None:
            m = mb.addMenu(tr("&Luces"))
            self.act_lights = self._action(m, "&Activar luces", self.set_lights, "Ctrl+L", checkable=True)
            m.addSeparator()
            self._action(m, "&Efectos por tema…", self.show_lights_editor, "Ctrl+E")
            self._action(m, "&Mis tiras…", self.show_layout)
            self._action(m, "&Simulador (navegador)", self.open_simulator)
            self._action(m, "&Latencia…", self.show_latency)
            m.addSeparator()
            self.act_view = self._action(m, "&Ver luces en la ventana (lateral)", self.set_light_view,
                                         "Ctrl+Shift+L", checkable=True)
        else:
            self.act_lights = None
            self.act_view = None

        m = mb.addMenu(tr("&Ver"))
        if self.tabs.count() > 1:
            self._action(m, "Módulo &Práctica", lambda: self.tabs.setCurrentWidget(self.practice), "Ctrl+1")
            self._action(m, "Módulo &Luces", lambda: self.tabs.setCurrentWidget(self.lights_page), "Ctrl+2")
            m.addSeparator()
        self.act_compact = self._action(m, "&Compactar", self.toggle_compact, "Ctrl+M", checkable=True)
        top = self._action(m, "Siempre &visible", self.set_always_on_top, checkable=True)
        top.setChecked(self.cfg.ui.always_on_top)
        m.addSeparator()
        tm = m.addMenu(tr("&Tema"))
        tgroup = QActionGroup(self)
        for code, label in (("material", "Material Design"), ("rekordbox", "Rekordbox"),
                            ("system", "Sistema (Windows)")):
            act = QAction(tr(label), self, checkable=True)
            act.setChecked(code == theme.current)
            act.triggered.connect(lambda _c=False, code=code: self.change_theme(code))
            tgroup.addAction(act)
            tm.addAction(act)

        m = mb.addMenu(tr("&Idioma"))
        group = QActionGroup(self)
        for code, name in LANGUAGES.items():
            act = QAction(name, self, checkable=True)
            act.setChecked(code == language())
            act.triggered.connect(lambda _c=False, code=code: self.change_language(code))
            group.addAction(act)
            m.addAction(act)

        m = mb.addMenu(tr("Ay&uda"))
        self._action(m, "&Acerca de…", self.about)

    # --- acciones ---------------------------------------------------------------
    def set_recording(self, on: bool) -> None:
        if self.practice is None or on == self.ctl.recorder.recording:
            self._sync_record(self.ctl.recorder.recording)
            return
        if on:
            self.ctl.start_recording()
            self._sync_record(True)
        else:
            path = self.ctl.stop_recording()
            self._sync_record(False)
            self.practice.saved(path)

    def _sync_record(self, on: bool) -> None:
        if self.act_record is not None:
            self.act_record.blockSignals(True)
            self.act_record.setChecked(on)
            self.act_record.blockSignals(False)
        if self.practice is not None:
            self.practice.recording_changed(on)

    def set_lights(self, on: bool) -> None:
        self.ctl.set_lights_enabled(on)
        if self.act_lights is not None:
            self.act_lights.blockSignals(True)
            self.act_lights.setChecked(on)
            self.act_lights.blockSignals(False)
        if self.lights_page is not None:
            self.lights_page.set_enabled_state(on)

    def show_history(self) -> None:
        HistoryDialog(self.cfg, self, open_review=self.ctl.open_review).exec()

    def open_review(self) -> None:
        import webbrowser
        try:
            webbrowser.open(self.ctl.open_review())
        except OSError as exc:
            QMessageBox.warning(self, tr("Revisión"), tr("No se pudo abrir la página de revisión: {error}",
                                                         error=exc))

    def show_layout(self) -> None:
        from .layout_dialog import LayoutDialog
        LayoutDialog(self.ctl, self).exec()

    def show_latency(self) -> None:
        from .lights_editor import LatencyDialog
        LatencyDialog(self.ctl, self).exec()

    def open_simulator(self) -> None:
        import webbrowser
        try:
            url = self.ctl.lights.start_simulator()
        except OSError as exc:
            QMessageBox.warning(self, tr("Simulador"), tr("No se pudo abrir el simulador: {error}", error=exc))
            return
        webbrowser.open(url)
        self.set_lights(True)                       # abrir el simulador sin luces no sirve de nada
        extra = tr(" (ya estaba abierto: se usa ese)") if self.ctl.lights.external_simulator else ""
        self.statusBar().showMessage(tr("Simulador en {url}{extra} · luces activadas", url=url, extra=extra), 8000)

    def set_light_view(self, on: bool) -> None:
        """Panel lateral con las luces (el simulador dentro de la ventana, solo los efectos)."""
        if on and self._light_dock is None:
            try:
                from PySide6.QtWebEngineWidgets import QWebEngineView
            except ImportError:
                QMessageBox.information(self, tr("Luces"), tr(
                    "Esta instalación de PySide6 no incluye el navegador integrado (QtWebEngine). "
                    "Se abre el simulador en el navegador."))
                self._sync_view(False)
                self.open_simulator()
                return
            try:
                url = self.ctl.lights.start_simulator()
            except OSError as exc:
                QMessageBox.warning(self, tr("Simulador"), tr("No se pudo abrir el simulador: {error}", error=exc))
                self._sync_view(False)
                return
            from PySide6.QtCore import QUrl
            view = QWebEngineView()
            view.setUrl(QUrl(url + "?embed=1"))
            dock = QDockWidget(tr("Luces en directo"), self)
            dock.setObjectName("lightView")
            dock.setWidget(view)
            dock.setMinimumWidth(220)
            # Al cerrar el panel con su ✕ se recuerda; al minimizar o cerrar la app, no.
            dock.visibilityChanged.connect(lambda visible: self._sync_view(
                visible, save=self.isVisible() and not self.isMinimized() and not getattr(self, "_closing", False)))
            self.addDockWidget(Qt.RightDockWidgetArea, dock)
            self._light_dock = dock
            # Hacer sitio al panel solo si no lo hay ya (antes se sumaban 320 px cada vez que se
            # rehacía la ventana, p. ej. al cambiar de idioma, y la ventana crecía sin parar).
            if self.width() < 800:
                self.resize(800, self.height())
        if self._light_dock is not None:
            self._light_dock.setVisible(on)
        if on:
            self.set_lights(True)
        self._sync_view(on, save=True)

    def _sync_view(self, on: bool, save: bool = False) -> None:
        if self.act_view is not None:
            self.act_view.blockSignals(True)
            self.act_view.setChecked(on)
            self.act_view.blockSignals(False)
        if save and self.cfg.ui.light_view != on:
            self.cfg.ui.light_view = on
            if self.cfg.source_path is not None:
                from ..config import save_value
                save_value(self.cfg.source_path, ["ui", "light_view"], on)

    def show_lights_editor(self) -> None:
        from .lights_editor import LightsEditor
        tracks, seen = [], set()
        snap = self._last_snapshot
        if snap is not None:
            for d in snap.decks:
                t = self.library.track(d.track_id) if d.track_id else None
                if t is not None and t.id not in seen:
                    seen.add(t.id)
                    tracks.append((tr("Deck {deck}", deck=d.deck) + f": {t.display}", t))
        if self.ctl.session is not None:
            for t, _ in self.ctl.session.entries():
                if t.id not in seen:
                    seen.add(t.id)
                    tracks.append((t.display, t))
        if not tracks:
            QMessageBox.information(self, tr("Luces"), tr("Carga un tema en un deck o genera una sesión primero."))
            return

        def positions():
            snap = self._last_snapshot
            if snap is None:
                return {}
            return {d.track_id: d.position_s for d in snap.decks
                    if d.track_id and d.position_s is not None}

        LightsEditor(self.ctl, tracks, self, position_provider=positions).exec()

    def toggle_compact(self) -> None:
        self._compact = not self._compact
        self.act_compact.blockSignals(True)
        self.act_compact.setChecked(self._compact)
        self.act_compact.blockSignals(False)
        if self.practice is not None:
            self.practice.set_compact(self._compact)
        self.tabs.tabBar().setVisible(self.tabs.count() > 1 and not self._compact)
        if self._compact:
            self.resize(self.width(), self.minimumSizeHint().height())

    def set_always_on_top(self, on: bool) -> None:
        self.setWindowFlag(Qt.WindowStaysOnTopHint, on)
        self.show()

    def change_language(self, code: str) -> None:
        if code == language():
            return
        from ..config import save_value
        self.cfg.ui.language = code
        if self.cfg.source_path is not None:
            save_value(self.cfg.source_path, ["ui", "language"], code)
        set_language(code)
        # Se rehace la ventana con el idioma nuevo dentro de la misma app (antes se relanzaba el
        # programa y fallaba con «python -m rkbx_practice»). La lectura en vivo, la sesión, la
        # grabación y las luces siguen sin cortes.
        QTimer.singleShot(0, self.rebuild)

    def rebuild(self) -> "MainWindow":
        """Crea una ventana nueva (p. ej. con otro idioma) que toma el relevo de esta."""
        new = MainWindow(self.cfg, self.library, self.ctl, module=self.module)
        bridge = getattr(self, "_bridge", None)
        if bridge is not None:
            for sig, name in ((bridge.status_changed, "on_status"), (bridge.snapshot, "on_snapshot"),
                              (bridge.events, "on_events")):
                try:
                    sig.disconnect(getattr(self, name))
                except (RuntimeError, TypeError):
                    pass
                sig.connect(getattr(new, name))
            new._bridge = bridge
        new._last_snapshot = self._last_snapshot
        if self.ctl._lights is not None and new.lights_page is not None:
            new.set_lights(self.ctl._lights.engine.enabled)
        new.conn_label.setText(self.conn_label.text())
        new.conn_label.setToolTip(self.conn_label.toolTip())
        if new.practice is not None:
            if self.practice is not None:
                new.practice.source_combo.setCurrentIndex(self.practice.source_combo.currentIndex())
                new.practice.info_label.setText(self.practice.info_label.text())
                new.practice._record_started = self.practice._record_started
            new.practice.refresh_session()
            if self.ctl.recorder.recording:
                started = new.practice._record_started
                new._sync_record(True)
                new.practice._record_started = started or new.practice._record_started
        if self.tabs.currentIndex() < new.tabs.count():
            new.tabs.setCurrentIndex(self.tabs.currentIndex())
        new.setGeometry(self.geometry())
        QApplication.instance()._main_window = new     # mantener viva la ventana nueva
        new.show()
        self._handover = True
        self.close()
        return new

    def change_theme(self, name: str) -> None:
        """Cambia el tema al momento y lo guarda (ui.theme)."""
        app = QApplication.instance()
        app.setStyleSheet(theme.apply(name))
        if self.practice is not None:              # colores que se pintan desde el código
            self.practice.suggestion.setStyleSheet("")
            self.practice.refresh_session()
            self.practice._refresh_suggestion()
        self.cfg.ui.theme = name
        if self.cfg.source_path is not None:
            from ..config import save_value
            save_value(self.cfg.source_path, ["ui", "theme"], name)

    def about(self) -> None:
        QMessageBox.about(self, tr("Acerca de"), "rkbx-practise-tool\n\n" +
                          tr("Asistente de práctica para Rekordbox: sesiones, grabación de mezclas y luces."))

    # --- en vivo -----------------------------------------------------------------
    def currently_loaded(self):
        """Tema que está sonando (deck master; si no, el de fader más alto)."""
        snap = self._last_snapshot
        if snap is None:
            return None, None
        decks = [d for d in snap.decks if d.track_id and self.library.track(d.track_id)]
        if not decks:
            return None, None
        playing = [x for x in decks if x.playing is not False] or decks
        d = next((x for x in playing if x.is_master), None) or max(
            playing, key=lambda x: (x.playing is True, x.fader or 0.0))
        return self.library.track(d.track_id), d.deck

    def on_status(self, status: ConnectionStatus) -> None:
        color = MODE_COLORS[status.mode]
        version = f" {status.version}" if status.version else ""
        self.conn_label.setText(f"<span style='color:{color}'>●</span> " +
                                tr("Rekordbox{version} — {mode}", version=version, mode=tr(status.mode.label_es)))
        self.conn_label.setToolTip(status.message)
        if status.message:
            self.statusBar().showMessage(status.message, 8000)

    def on_snapshot(self, snap: LiveSnapshot) -> None:
        self._last_snapshot = snap

    def on_events(self, events) -> None:
        if self.practice is not None:
            self.practice.on_events(events)
        else:
            self.ctl.apply_events(events)

    def _refresh(self) -> None:
        snap = self._last_snapshot
        if self.practice is not None:
            self.practice.refresh(snap)
        if self.lights_page is not None:
            self.lights_page.refresh(snap)
        lights = self.ctl._lights
        if lights is not None and lights.engine.enabled and lights.engine.last_sent is not None:
            ev, t_sent = lights.engine.last_sent
            if t_sent != getattr(self, "_last_light_shown", None):
                self._last_light_shown = t_sent
                self.statusBar().showMessage(tr("Luces: {label} → {fx}", label=ev.label, fx=ev.fx), 3000)

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt)
        self._closing = True
        if getattr(self, "_handover", False):      # relevo por otra ventana: no parar nada
            self._timer.stop()
            super().closeEvent(event)
            return
        if self.ctl.recorder.recording:
            path = self.ctl.stop_recording()
            log.info("Grabación guardada al cerrar: %s", path)
        self.ctl.stop_live()
        if self.ctl._lights is not None:
            self.ctl._lights.stop_simulator()
        super().closeEvent(event)


def run_ui(cfg: AppConfig, library: InMemoryLibrary, controller: PracticeController | None = None,
           module: str | None = None) -> int:
    set_language(cfg.ui.language)
    if QApplication.instance() is None:
        # El navegador integrado (panel lateral de luces) debe cargarse antes de crear la aplicación.
        QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
        try:
            import PySide6.QtWebEngineWidgets  # noqa: F401
        except ImportError:
            pass
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setStyleSheet(theme.apply(cfg.ui.theme))
    ctl = controller or PracticeController(cfg, library)
    win = MainWindow(cfg, library, ctl, module=module)
    bridge = LiveBridge()
    bridge.status_changed.connect(win.on_status)
    bridge.snapshot.connect(win.on_snapshot)
    bridge.events.connect(win.on_events)
    win._bridge = bridge  # mantener viva la referencia
    app._main_window = win
    ctl.start_live(bridge)
    win.show()
    return app.exec()
