"""Editor de efectos de luces por tema: ver los cambios del tema y asignar efectos."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox, QTableWidget,
                               QTableWidgetItem, QToolTip, QVBoxLayout, QWidget)

from . import theme

from ..library.models import Track
from ..lights.commands import LightCommand, format_params, parse_params
from ..lights.show import LightEvent, LightShow

NO_FX = "(ninguno)"

# Colores de las frases (inspirados en los de Rekordbox).
PHRASE_COLORS = {
    "intro": "#2f6fd6", "up": "#e0a020", "down": "#7b52d9", "chorus": "#e04545",
    "outro": "#2fa88a", "verse": "#3a9fb0", "bridge": "#c062c0",
}
ENERGY_CHANGE_COLORS = {"drop": "#ffffff", "break": "#7fb0ff"}


def phrase_color(label: str) -> QColor:
    return QColor(PHRASE_COLORS.get(label.lower().split(" ")[0], "#666666"))


class TimelineWidget(QWidget):
    """Línea de tiempo del tema: frases, energía de graves, eventos y cursor de reproducción."""

    beatClicked = Signal(int)

    PHRASE_H = 18
    MARGIN = 6

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(130)
        self.setMouseTracking(True)
        self.analysis = None
        self.events: list[LightEvent] = []
        self.selected_beat: int | None = None
        self.playhead_beat: float | None = None

    # --- datos ---------------------------------------------------------
    def set_data(self, analysis, events: list[LightEvent]) -> None:
        self.analysis = analysis
        self.events = sorted(events, key=lambda e: e.beat)
        self.update()

    def set_selected(self, beat: int | None) -> None:
        self.selected_beat = beat
        self.update()

    def set_playhead(self, beat: float | None) -> None:
        if beat != self.playhead_beat:
            self.playhead_beat = beat
            self.update()

    # --- geometría -------------------------------------------------------
    def _n(self) -> int:
        return len(self.analysis.grid.times) if self.analysis else 0

    def _x(self, beat: float) -> float:
        n = max(self._n(), 1)
        w = self.width() - 2 * self.MARGIN
        return self.MARGIN + (beat - 1) / n * w

    def _beat_at(self, x: float) -> int:
        n = max(self._n(), 1)
        w = max(self.width() - 2 * self.MARGIN, 1)
        return int(min(max(1, round((x - self.MARGIN) / w * n + 1)), n))

    # --- dibujo --------------------------------------------------------
    def paintEvent(self, _ev) -> None:  # noqa: N802 (Qt)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(theme.PANEL))
        if not self.analysis or not self._n():
            p.setPen(QColor(theme.TEXT_DIM))
            p.drawText(self.rect(), Qt.AlignCenter, "Sin análisis")
            return
        n = self._n()
        top, bottom = self.MARGIN, self.height() - self.MARGIN
        phrase_top = bottom - self.PHRASE_H

        # Rejilla de compases (cada 16 compases, más marcada).
        numbers = self.analysis.grid.beat_numbers
        bar = 0
        for i, num in enumerate(numbers):
            if num != 1:
                continue
            bar += 1
            if bar % 4 != 1:
                continue
            x = self._x(i + 1)
            p.setPen(QPen(QColor("#3a3a3a" if bar % 16 == 1 else "#262626"), 1))
            p.drawLine(QPointF(x, top), QPointF(x, phrase_top))

        # Frases: bandas de color con su nombre.
        phrases = self.analysis.phrases
        f = QFont(self.font())
        f.setPointSizeF(max(7.0, f.pointSizeF() - 1))
        p.setFont(f)
        for k, ph in enumerate(phrases):
            end = phrases[k + 1].beat if k + 1 < len(phrases) else n + 1
            r = QRectF(self._x(ph.beat), phrase_top, self._x(end) - self._x(ph.beat), self.PHRASE_H)
            p.fillRect(r.adjusted(0, 0, -1, 0), phrase_color(ph.label))
            if r.width() > 34:
                p.setPen(QColor("#ffffff"))
                p.drawText(r.adjusted(3, 0, -2, 0), Qt.AlignVCenter | Qt.AlignLeft, ph.label)

        # Energía de graves.
        energy = self.analysis.beat_energy
        h = phrase_top - top - 14
        if energy:
            path = QPainterPath(QPointF(self._x(1), phrase_top - 2))
            for i, e in enumerate(energy):
                path.lineTo(QPointF(self._x(i + 1), phrase_top - 2 - e * h))
            path.lineTo(QPointF(self._x(len(energy)), phrase_top - 2))
            path.closeSubpath()
            p.fillPath(path, QBrush(QColor(42, 140, 255, 110)))
            p.setPen(QPen(QColor(theme.ACCENT), 1))
            p.drawPath(path)

        # Cambios de energía (drop/break): línea fina.
        for c in self.analysis.changes:
            x = self._x(c.beat)
            p.setPen(QPen(QColor(ENERGY_CHANGE_COLORS.get(c.kind, "#ffffff")), 1, Qt.DashLine))
            p.drawLine(QPointF(x, top + 12), QPointF(x, phrase_top))

        # Eventos con efecto: triángulo arriba (amarillo; seleccionado en blanco).
        for ev in self.events:
            x = self._x(ev.beat)
            sel = ev.beat == self.selected_beat
            color = QColor("#ffffff" if sel else (theme.ROW_STYLES["next"][1] if ev.fx else "#666666"))
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            size = 6 if sel else 4.5
            p.drawPolygon(QPolygonF([QPointF(x - size, top), QPointF(x + size, top), QPointF(x, top + size * 1.6)]))
            if sel:
                p.setPen(QPen(color, 1))
                p.drawLine(QPointF(x, top + 8), QPointF(x, bottom))

        # Cursor de reproducción.
        if self.playhead_beat is not None:
            x = self._x(self.playhead_beat)
            p.setPen(QPen(QColor(theme.MASTER), 2))
            p.drawLine(QPointF(x, top), QPointF(x, bottom))

    # --- ratón ---------------------------------------------------------
    def _near_event(self, x: float) -> LightEvent | None:
        best = min(self.events, key=lambda e: abs(self._x(e.beat) - x), default=None)
        return best if best is not None and abs(self._x(best.beat) - x) <= 6 else None

    def mousePressEvent(self, ev) -> None:  # noqa: N802 (Qt)
        if not self.analysis:
            return
        x = ev.position().x()
        near = self._near_event(x)
        self.beatClicked.emit(near.beat if near else self._beat_at(x))

    def mouseMoveEvent(self, ev) -> None:  # noqa: N802 (Qt)
        if not self.analysis:
            return
        x = ev.position().x()
        beat = self._beat_at(x)
        near = self._near_event(x)
        phrase = next((ph.label for ph in reversed(self.analysis.phrases) if ph.beat <= beat), "—")
        t = self.analysis.beat_time(beat)
        text = f"Compás {self.analysis.bar_beat(beat)} · {int(t // 60)}:{t % 60:04.1f} · {phrase}" if t is not None else ""
        if near is not None and near.fx:
            text += f"\nEvento: {near.label} → {near.fx} {format_params(near.params)}"
        QToolTip.showText(ev.globalPosition().toPoint(), text, self)


class LightsEditor(QDialog):
    COLS = ["Compás", "Tiempo", "Sección", "Efecto", "Parámetros", ""]

    def __init__(self, controller, tracks: list[tuple[str, Track]], parent=None, position_provider=None):
        """`tracks`: [(texto para el desplegable, tema)], el primero es el que suena.
        `position_provider()` → {track_id: posición en segundos} de los temas que suenan."""
        super().__init__(parent)
        self.ctl = controller
        self.mgr = controller.lights
        self.effects = self.mgr.cfg.lights.effects
        self.show: LightShow | None = None
        self.analysis = None
        self.track: Track | None = None
        self.position_provider = position_provider
        self.setWindowTitle("Efectos de luces")
        self.resize(900, 640)
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

        self.timeline = TimelineWidget()
        self.timeline.beatClicked.connect(self._timeline_clicked)
        lay.addWidget(self.timeline)
        legend = QLabel("  ".join(f"<span style='color:{c}'>■</span> {name.capitalize()}"
                                  for name, c in PHRASE_COLORS.items())
                        + "   <span style='color:#ffe14d'>▼</span> evento"
                        + "   <span style='color:#ff8a00'>|</span> reproducción")
        legend.setProperty("role", "dim")
        lay.addWidget(legend)

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
        self.table.itemSelectionChanged.connect(self._selection_changed)

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

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._update_playhead)
        self._timer.start(100)

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
            self.timeline.set_data(None, [])
            return
        n_phr = len(self.analysis.phrases) if self.analysis else 0
        n_chg = len(self.analysis.changes) if self.analysis else 0
        self.info.setText(f"{n_phr} frases y {n_chg} cambios de energía detectados · "
                          f"archivo: {self.mgr.store.path(track.id)}")
        self._fill()

    # --- línea de tiempo ------------------------------------------------
    def _refresh_timeline(self) -> None:
        events = self._collect() if self.table.rowCount() else (self.show.events if self.show else [])
        self.timeline.set_data(self.analysis, events)

    def _timeline_clicked(self, beat: int) -> None:
        for row in range(self.table.rowCount()):
            if int(self.table.item(row, 0).data(Qt.UserRole)) == beat:
                self.table.selectRow(row)
                self.table.scrollToItem(self.table.item(row, 0))
                return
        # Sin evento ahí: se prepara el compás para «Añadir».
        if self.analysis is not None:
            bar = self.analysis.bar_beat(beat).split(".")[0]
            if bar.isdigit():
                self.bar_spin.setValue(max(1, int(bar)))
            self.timeline.set_selected(None)

    def _selection_changed(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        beat = int(self.table.item(rows[0], 0).data(Qt.UserRole)) if rows else None
        self.timeline.set_selected(beat)

    def _update_playhead(self) -> None:
        if self.track is None or self.analysis is None or self.position_provider is None:
            return
        pos = (self.position_provider() or {}).get(self.track.id)
        if pos is None:
            self.timeline.set_playhead(None)
            return
        nb = self.analysis.grid.nearest_beat(pos)
        if nb is None:
            return
        j, off = nb
        times = self.analysis.grid.times
        period = (times[j + 1] - times[j]) if j + 1 < len(times) else (times[j] - times[j - 1] if j else 0.5)
        self.timeline.set_playhead(j + 1 + (off / period if period else 0))

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
            combo.currentTextChanged.connect(lambda _t: self._refresh_timeline())
        self._refresh_timeline()

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
