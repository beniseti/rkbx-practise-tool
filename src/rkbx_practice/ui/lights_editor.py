"""Editor de efectos de luces por tema: ver los cambios del tema y asignar efectos."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox, QTableWidget,
                               QTableWidgetItem, QVBoxLayout)

from ..library.models import Track
from ..lights.commands import LightCommand, format_params, parse_params
from ..lights.show import LightEvent, LightShow

NO_FX = "(ninguno)"


class LightsEditor(QDialog):
    COLS = ["Compás", "Tiempo", "Sección", "Efecto", "Parámetros", ""]

    def __init__(self, controller, tracks: list[tuple[str, Track]], parent=None):
        """`tracks`: [(texto para el desplegable, tema)], el primero es el que suena."""
        super().__init__(parent)
        self.ctl = controller
        self.mgr = controller.lights
        self.effects = self.mgr.cfg.lights.effects
        self.show: LightShow | None = None
        self.analysis = None
        self.track: Track | None = None
        self.setWindowTitle("Efectos de luces")
        self.resize(760, 560)
        lay = QVBoxLayout(self)

        top = QHBoxLayout()
        self.track_combo = QComboBox()
        for label, t in tracks:
            self.track_combo.addItem(label, t.id)
        self.track_combo.currentIndexChanged.connect(self._load_selected)
        top.addWidget(QLabel("Tema:"))
        top.addWidget(self.track_combo, 1)
        lay.addLayout(top)

        self.info = QLabel("")
        self.info.setProperty("role", "dim")
        lay.addWidget(self.info)

        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        hh = self.table.horizontalHeader()
        for c in (0, 1, 3, 5):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        hh.setSectionResizeMode(4, QHeaderView.Stretch)
        lay.addWidget(self.table, 1)

        bottom = QHBoxLayout()
        self.bar_spin = QSpinBox()
        self.bar_spin.setRange(1, 9999)
        self.bar_spin.setPrefix("compás ")
        add = QPushButton("Añadir")
        add.clicked.connect(self._add_event)
        remove = QPushButton("Quitar")
        remove.clicked.connect(self._remove_selected)
        regen = QPushButton("Regenerar desde el análisis")
        regen.clicked.connect(self._regenerate)
        save = QPushButton("Guardar")
        save.setDefault(True)
        save.clicked.connect(self._save)
        for w in (self.bar_spin, add, remove):
            bottom.addWidget(w)
        bottom.addStretch(1)
        bottom.addWidget(regen)
        bottom.addWidget(save)
        lay.addLayout(bottom)

        if self.track_combo.count():
            self._load_selected()

    # --- carga ----------------------------------------------------------
    def _load_selected(self) -> None:
        tid = self.track_combo.currentData()
        track = self.ctl.library.track(tid) if tid else None
        if track is None:
            return
        self.track = track
        self.analysis = self.mgr.analyze(track)
        self.show = self.mgr.load_or_generate(track)
        if self.show is None:
            self.info.setText("Sin análisis de Rekordbox para este tema (¿está analizado?).")
            self.table.setRowCount(0)
            return
        n_phr = len(self.analysis.phrases) if self.analysis else 0
        n_chg = len(self.analysis.changes) if self.analysis else 0
        self.info.setText(f"{n_phr} frases y {n_chg} cambios de energía detectados · "
                          f"archivo: {self.mgr.store.path(track.id)}")
        self._fill()

    def _fill(self) -> None:
        assert self.show is not None
        events = self.show.sorted_events()
        self.table.setRowCount(len(events))
        for row, ev in enumerate(events):
            bar, when = "?", "—"
            if self.analysis is not None:
                bar = self.analysis.bar_beat(ev.beat)
                t = self.analysis.beat_time(ev.beat)
                if t is not None:
                    when = f"{int(t // 60)}:{t % 60:04.1f}"
            for col, text in ((0, bar), (1, when), (2, ev.label)):
                item = QTableWidgetItem(text)
                item.setData(Qt.UserRole, ev.beat)
                if col != 2:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(row, col, item)
            combo = QComboBox()
            combo.addItem(NO_FX)
            for name in self.effects:
                combo.addItem(name)
            combo.setCurrentText(ev.fx or NO_FX)
            params = QLineEdit(format_params(ev.params))
            params.setPlaceholderText("color=rojo dur_ms=200")
            combo.currentTextChanged.connect(lambda name, p=params: self._fx_changed(name, p))
            self.table.setCellWidget(row, 3, combo)
            self.table.setCellWidget(row, 4, params)
            test = QPushButton("Probar")
            test.clicked.connect(lambda _=False, r=row: self._test_row(r))
            self.table.setCellWidget(row, 5, test)

    def _fx_changed(self, name: str, params: QLineEdit) -> None:
        if name != NO_FX:
            params.setText(format_params(self.effects.get(name, {})))
        else:
            params.setText("")

    # --- edición --------------------------------------------------------
    def _collect(self) -> list[LightEvent]:
        out = []
        for row in range(self.table.rowCount()):
            beat = int(self.table.item(row, 0).data(Qt.UserRole))
            label = self.table.item(row, 2).text()
            fx = self.table.cellWidget(row, 3).currentText()
            params = parse_params(self.table.cellWidget(row, 4).text())
            source = next((e.source for e in self.show.events if e.beat == beat), "manual") if self.show else "manual"
            out.append(LightEvent(beat, label, source, None if fx == NO_FX else fx, params))
        return out

    def _add_event(self) -> None:
        if self.show is None or self.analysis is None:
            return
        numbers = self.analysis.grid.beat_numbers
        bar = self.bar_spin.value()
        count, beat = 0, None
        for i, n in enumerate(numbers):
            if n == 1:
                count += 1
                if count == bar:
                    beat = i + 1
                    break
        if beat is None:
            QMessageBox.information(self, "Luces", "Ese compás no existe en el tema.")
            return
        self.show.events = self._collect() + [LightEvent(beat, "manual", "manual", "flash",
                                                         dict(self.effects.get("flash", {})))]
        self._fill()

    def _remove_selected(self) -> None:
        if self.show is None:
            return
        rows = {i.row() for i in self.table.selectedIndexes()}
        events = self._collect()
        self.show.events = [e for i, e in enumerate(sorted(events, key=lambda e: e.beat)) if i not in rows]
        self._fill()

    def _regenerate(self) -> None:
        if self.track is None:
            return
        if QMessageBox.question(self, "Luces", "Se perderán los cambios hechos a mano en este tema. "
                                "¿Regenerar desde el análisis?") != QMessageBox.Yes:
            return
        show = self.mgr.generate(self.track, self.analysis)
        if show is not None:
            self.show = show
            self._fill()

    def _test_row(self, row: int) -> None:
        fx = self.table.cellWidget(row, 3).currentText()
        if fx == NO_FX:
            return
        cmd = LightCommand(fx, parse_params(self.table.cellWidget(row, 4).text()))
        ok = self.mgr.sender.send(cmd)
        self.info.setText(("Enviado: " if ok else "NO enviado (¿conectado a la Wi-Fi del ESP32?): ")
                          + cmd.to_json().decode())

    def _save(self) -> None:
        if self.show is None:
            return
        self.show.events = self._collect()
        path = self.mgr.save(self.show)
        self.info.setText(f"Guardado en {path}")
