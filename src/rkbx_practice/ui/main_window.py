"""Ventana de práctica (PySide6): flotante, siempre visible, redimensionable y compactable."""

from __future__ import annotations

import copy
import logging
import time

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox,
                               QDoubleSpinBox, QFrame, QGridLayout, QHBoxLayout, QHeaderView,
                               QLabel, QMainWindow, QMessageBox, QProgressBar, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..app import PracticeController
from ..config import STRICTNESS_LEVELS, AppConfig
from ..events import BassSwap, CutBurst, TrackLoaded, TransitionEvent
from ..library.memory_library import InMemoryLibrary
from ..library.models import NodeKind
from ..state import ConnectionMode, ConnectionStatus, LiveSnapshot

log = logging.getLogger(__name__)

STRICTNESS_LABELS = {"strict": "Estricto", "moderate": "Moderado", "relaxed": "Relajado"}
MODE_COLORS = {ConnectionMode.FULL: "#2e7d32", ConnectionMode.LIMITED: "#ef6c00",
               ConnectionMode.DISCONNECTED: "#c62828"}


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


class DeckWidget(QFrame):
    def __init__(self, deck: int):
        super().__init__()
        self.deck = deck
        self.setFrameShape(QFrame.StyledPanel)
        lay = QGridLayout(self)
        lay.setContentsMargins(6, 4, 6, 4)
        self.title = QLabel(f"Deck {deck}")
        self.title.setStyleSheet("font-weight: bold;")
        self.track = QLabel("—")
        self.track.setMinimumWidth(120)
        self.bpm = QLabel("— BPM")
        self.fader = QProgressBar()
        self.fader.setRange(0, 100)
        self.fader.setTextVisible(False)
        self.fader.setFixedHeight(8)
        lay.addWidget(self.title, 0, 0)
        lay.addWidget(self.bpm, 0, 1, alignment=Qt.AlignRight)
        lay.addWidget(self.track, 1, 0, 1, 2)
        lay.addWidget(self.fader, 2, 0, 1, 2)

    def update_state(self, title: str, bpm: float | None, fader: float | None, master: bool | None):
        self.track.setText(title)
        self.bpm.setText(f"{bpm:.2f} BPM" if bpm else "— BPM")
        self.fader.setValue(int(round((fader or 0.0) * 100)))
        self.title.setText(f"Deck {self.deck}" + ("  ● MASTER" if master else ""))


class MainWindow(QMainWindow):
    def __init__(self, cfg: AppConfig, library: InMemoryLibrary, controller: PracticeController):
        super().__init__()
        self.cfg = cfg
        self.library = library
        self.ctl = controller
        self._last_snapshot: LiveSnapshot | None = None
        self._compact = False
        self._record_started: float | None = None

        self.setWindowTitle("Práctica Rekordbox")
        if cfg.ui.always_on_top:
            self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.resize(460, 720)

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        self.setCentralWidget(central)

        # --- cabecera: conexión, grabación, compactar -----------------
        head = QHBoxLayout()
        self.conn_label = QLabel("● Buscando Rekordbox…")
        self.conn_label.setWordWrap(True)
        self.rec_button = QPushButton("● Iniciar grabación")
        self.rec_button.setCheckable(True)
        self.rec_button.clicked.connect(self._toggle_recording)
        self.compact_button = QPushButton("Compactar")
        self.compact_button.clicked.connect(self._toggle_compact)
        head.addWidget(self.conn_label, 1)
        head.addWidget(self.rec_button)
        head.addWidget(self.compact_button)
        root.addLayout(head)

        # --- selector de carpeta/playlist y ajustes -------------------
        self.settings_box = QWidget()
        sl = QGridLayout(self.settings_box)
        sl.setContentsMargins(0, 0, 0, 0)
        self.source_combo = QComboBox()
        for r in library.tree():
            for depth, node in r.walk():
                prefix = "    " * depth + ("📁 " if node.kind == NodeKind.FOLDER else "")
                self.source_combo.addItem(prefix + node.name, node.id)
        self.generate_button = QPushButton("Generar sesión")
        self.generate_button.clicked.connect(self._generate)
        self.tolerance = QDoubleSpinBox()
        self.tolerance.setRange(0.5, 20.0)
        self.tolerance.setSingleStep(0.5)
        self.tolerance.setSuffix(" % BPM")
        self.tolerance.setValue(cfg.session.bpm_tolerance_pct)
        self.camelot_check = QCheckBox("Camelot")
        self.camelot_check.setChecked(cfg.session.camelot_enabled)
        self.strictness = QComboBox()
        for s in STRICTNESS_LEVELS:
            self.strictness.addItem(STRICTNESS_LABELS[s], s)
        self.strictness.setCurrentIndex(STRICTNESS_LEVELS.index(cfg.session.camelot_strictness))
        self.camelot_check.toggled.connect(self.strictness.setEnabled)
        self.strictness.setEnabled(cfg.session.camelot_enabled)
        sl.addWidget(self.source_combo, 0, 0, 1, 3)
        sl.addWidget(self.generate_button, 0, 3)
        sl.addWidget(self.tolerance, 1, 0)
        sl.addWidget(self.camelot_check, 1, 1)
        sl.addWidget(self.strictness, 1, 2)
        root.addWidget(self.settings_box)

        # --- sugerencia -----------------------------------------------
        self.suggestion = QLabel("Genera una sesión para empezar")
        self.suggestion.setWordWrap(True)
        f = QFont()
        f.setPointSize(f.pointSize() + 3)
        f.setBold(True)
        self.suggestion.setFont(f)
        self.suggestion.setStyleSheet("padding: 6px; border-radius: 4px; background: #263238; color: #fff;")
        root.addWidget(self.suggestion)

        # --- lista de la sesión ---------------------------------------
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["#", "Tema", "BPM", "Camelot"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.cellClicked.connect(self._row_clicked)
        self.table.setToolTip("Clic en un tema tachado para destacharlo")
        root.addWidget(self.table, 1)
        self.info_label = QLabel("")
        self.info_label.setWordWrap(True)
        self.info_label.setStyleSheet("color: #ef6c00;")
        root.addWidget(self.info_label)

        # --- decks ----------------------------------------------------
        decks_box = QWidget()
        dl = QGridLayout(decks_box)
        dl.setContentsMargins(0, 0, 0, 0)
        self.deck_widgets = {}
        for n in range(1, cfg.rekordbox.decks + 1):
            w = DeckWidget(n)
            self.deck_widgets[n] = w
            dl.addWidget(w, (n - 1) // 2, (n - 1) % 2)
        root.addWidget(decks_box)

        self.statusBar().showMessage("Listo")

        # Refresco de decks a ~10 Hz (el sondeo va a 50 Hz).
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_decks)
        self._timer.start(100)

        if cfg.ui.start_compact:
            self._toggle_compact()

    # --- sesión -------------------------------------------------------
    def _current_settings(self):
        s = copy.deepcopy(self.cfg.session)
        s.bpm_tolerance_pct = float(self.tolerance.value())
        s.camelot_enabled = self.camelot_check.isChecked()
        s.camelot_strictness = self.strictness.currentData()
        return s

    def _generate(self) -> None:
        node_id = self.source_combo.currentData()
        if node_id is None:
            return
        if self.ctl.session is not None and self.ctl.session.struck:
            ans = QMessageBox.question(self, "Nueva sesión",
                                       "Se perderá el tachado de la sesión actual. ¿Continuar?")
            if ans != QMessageBox.Yes:
                return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            session = self.ctl.generate(node_id, self._current_settings())
        finally:
            QApplication.restoreOverrideCursor()
        if session.excluded:
            names = ", ".join(t.display for t, _ in session.excluded[:5])
            more = "…" if len(session.excluded) > 5 else ""
            self.info_label.setText(f"{len(session.excluded)} tema(s) sin analizar (sin BPM o tonalidad) "
                                    f"excluidos: {names}{more}")
        else:
            self.info_label.setText("")
        self._refresh_session()

    def _refresh_session(self) -> None:
        s = self.ctl.session
        self.table.setRowCount(0)
        if s is None:
            return
        sug = s.suggestion
        entries = s.entries()
        self.table.setRowCount(len(entries))
        for row, (track, struck) in enumerate(entries):
            cells = [str(row + 1), track.display, f"{track.bpm:.1f}" if track.bpm else "—", track.camelot_str]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setData(Qt.UserRole, track.id)
                if struck:
                    font = item.font()
                    font.setStrikeOut(True)
                    item.setFont(font)
                    item.setForeground(QBrush(QColor("#9e9e9e")))
                elif sug is not None and track.id == sug.track.id:
                    item.setBackground(QBrush(QColor("#ffe082" if sug.is_wildcard else "#c8e6c9")))
                    item.setForeground(QBrush(QColor("#000000")))
                if col in (0, 2, 3):
                    item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row, col, item)
        if sug is not None:
            row = next((i for i, (t, _) in enumerate(entries) if t.id == sug.track.id), None)
            if row is not None:
                self.table.scrollToItem(self.table.item(row, 1), QAbstractItemView.EnsureVisible)
        self._refresh_suggestion()

    def _refresh_suggestion(self) -> None:
        s = self.ctl.session
        if s is None:
            self.suggestion.setText("Genera una sesión para empezar")
            return
        sug = s.suggestion
        if sug is None:
            self.suggestion.setText("¡Sesión completada!")
            return
        t = sug.track
        tag = "🃏 COMODÍN · " if sug.is_wildcard else "Siguiente · "
        rel = ""
        if sug.from_anchor is not None and sug.from_anchor.key is not None:
            rel = f" · {sug.from_anchor.key.label_es}"
        self.suggestion.setText(f"{tag}{t.display}\n{t.bpm:.1f} BPM · {t.camelot_str}{rel}")
        color = "#f9a825" if sug.is_wildcard else "#263238"
        fg = "#000" if sug.is_wildcard else "#fff"
        self.suggestion.setStyleSheet(f"padding: 6px; border-radius: 4px; background: {color}; color: {fg};")

    def _row_clicked(self, row: int, _col: int) -> None:
        s = self.ctl.session
        item = self.table.item(row, 1)
        if s is None or item is None:
            return
        tid = item.data(Qt.UserRole)
        if s.is_struck(tid):
            s.unstrike(tid)
            self.statusBar().showMessage(f"Destachado: {item.text()}", 4000)
            self._refresh_session()

    # --- en vivo ------------------------------------------------------
    def on_status(self, status: ConnectionStatus) -> None:
        color = MODE_COLORS[status.mode]
        version = f" {status.version}" if status.version else ""
        self.conn_label.setText(f"<span style='color:{color}'>●</span> Rekordbox{version} — "
                                f"{status.mode.label_es}")
        self.conn_label.setToolTip(status.message)
        if status.message:
            self.statusBar().showMessage(status.message, 8000)

    def on_snapshot(self, snap: LiveSnapshot) -> None:
        self._last_snapshot = snap

    def on_events(self, events) -> None:
        unknown = self.ctl.apply_events(events)
        loaded = [e for e in events if isinstance(e, TrackLoaded)]
        if loaded:
            self._refresh_session()
            s = self.ctl.session
            for ev in loaded:
                t = self.library.track(ev.track_id) if ev.track_id else None
                if t is None:
                    continue
                if s is not None and s.contains(t.id):
                    msg = f"Deck {ev.deck}: tachado «{t.display}»"
                else:
                    msg = f"Deck {ev.deck}: «{t.display}» no está en la sesión (no se tacha)"
                self.statusBar().showMessage(msg, 6000)
        for ev in unknown:
            self.statusBar().showMessage(
                f"Deck {ev.deck}: tema no encontrado en master.db ({ev.track_path})", 8000)
        for ev in events:
            if isinstance(ev, TransitionEvent):
                self.statusBar().showMessage(
                    f"Transición {ev.out_deck}→{ev.in_deck} ({ev.duration_s:.1f} s)", 5000)
            elif isinstance(ev, CutBurst):
                self.statusBar().showMessage(f"Fader cuts en deck {ev.deck}: {ev.count}", 4000)
            elif isinstance(ev, BassSwap):
                self.statusBar().showMessage(f"Bass swap {ev.from_deck}→{ev.to_deck}", 4000)

    def _refresh_decks(self) -> None:
        snap = self._last_snapshot
        if snap is not None:
            for d in snap.decks:
                w = self.deck_widgets.get(d.deck)
                if w is None:
                    continue
                t = self.library.track(d.track_id) if d.track_id else None
                title = t.display if t else ("(no está en la biblioteca)" if d.track_path else "—")
                w.update_state(title, d.bpm, d.fader, d.is_master)
        if self.ctl.recorder.recording and self._record_started is not None:
            c = self.ctl.recorder.counts()
            secs = int(time.monotonic() - self._record_started)
            self.rec_button.setText(f"■ Parar grabación {secs // 60:02d}:{secs % 60:02d}")
            self.rec_button.setToolTip(f"Temas {c.get('tracks', 0)} · transiciones "
                                       f"{c.get('transitions', 0)} · cuts {c.get('fader_cuts', 0)} · "
                                       f"bass swaps {c.get('bass_swaps', 0)}")

    # --- grabación y compactar ---------------------------------------
    def _toggle_recording(self, checked: bool) -> None:
        if checked:
            self.ctl.start_recording()
            self._record_started = time.monotonic()
            self.rec_button.setStyleSheet("background: #c62828; color: white;")
            self.statusBar().showMessage("Grabando sesión…", 3000)
        else:
            path = self.ctl.stop_recording()
            self._record_started = None
            self.rec_button.setText("● Iniciar grabación")
            self.rec_button.setStyleSheet("")
            if path:
                self.statusBar().showMessage(f"Sesión guardada en {path}", 10000)

    def _toggle_compact(self) -> None:
        self._compact = not self._compact
        for w in (self.settings_box, self.table, self.info_label):
            w.setVisible(not self._compact)
        self.compact_button.setText("Ampliar" if self._compact else "Compactar")
        if self._compact:
            self.resize(self.width(), self.minimumSizeHint().height())

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt)
        if self.ctl.recorder.recording:
            path = self.ctl.stop_recording()
            log.info("Grabación guardada al cerrar: %s", path)
        self.ctl.stop_live()
        super().closeEvent(event)


def run_ui(cfg: AppConfig, library: InMemoryLibrary, controller: PracticeController | None = None) -> int:
    app = QApplication.instance() or QApplication([])
    ctl = controller or PracticeController(cfg, library)
    win = MainWindow(cfg, library, ctl)
    bridge = LiveBridge()
    bridge.status_changed.connect(win.on_status)
    bridge.snapshot.connect(win.on_snapshot)
    bridge.events.connect(win.on_events)
    win._bridge = bridge  # mantener viva la referencia
    ctl.start_live(bridge)
    win.show()
    return app.exec()
