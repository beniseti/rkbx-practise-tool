"""Mantenimiento del montaje de luces: canales (tiras), píxeles y segmentos de cada una.

Pedido del usuario: poder cambiar el montaje sin reprogramar (un día 3 tiras, otro 4; un día 3
segmentos por tira, otro 4). Se guarda en config.json (`lights.layout`) y se envía al ESP32, que lo
guarda en su memoria. Las zonas que usan los efectos (`zones`) se numeran tira a tira.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPushButton,
                               QSpinBox, QTableWidget, QVBoxLayout, QWidget)

from ..lights.layout import (CHANNEL_PINS, MAX_LEDS, MAX_SEGMENTS, MAX_STRIPS, MAX_ZONES, StripLayout,
                             normalize_layout, total_zones, zone_table)


class LayoutPreview(QWidget):
    """Dibujo del montaje: una fila por tira, partida en sus segmentos con el nº de zona."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.strips: list[StripLayout] = []
        self.setMinimumHeight(150)

    def set_strips(self, strips: list[StripLayout]) -> None:
        self.strips = strips
        self.update()

    def paintEvent(self, _ev):  # noqa: N802 (Qt)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#0c0e11"))
        if not self.strips:
            return
        w, h = self.width(), self.height()
        pad_l, pad_r, gap = 44, 10, 6
        row_h = h / len(self.strips)
        max_leds = max(s.leds for s in self.strips)
        zones = total_zones(self.strips)
        unit = (w - pad_l - pad_r - gap * (MAX_SEGMENTS - 1)) / max_leds
        font = QFont(self.font())
        font.setPointSizeF(max(7.0, font.pointSizeF() - 1))
        p.setFont(font)
        rows = zone_table(self.strips)
        starts = {}
        for z, si, seg, n in rows:
            a = starts.get(si, 0)
            starts[si] = a + n
            s = self.strips[si - 1]
            y = row_h * (si - 1) + row_h * 0.25
            x = pad_l + a * unit + (seg - 1) * gap
            rect = QRectF(x, y, max(3.0, n * unit), row_h * 0.5)
            color = QColor.fromHsvF(((z - 1) / max(1, zones)) % 1.0, 0.65, 0.85)
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            p.drawRoundedRect(rect, 4, 4)
            p.setPen(QColor("#111"))
            p.drawText(rect, Qt.AlignCenter, f"{z}")
            if seg == 1:
                p.setPen(QColor("#9aa0a6"))
                p.drawText(QRectF(2, y, pad_l - 4, row_h * 0.5), Qt.AlignVCenter | Qt.AlignLeft,
                           f"T{si}")
                p.drawText(QRectF(pad_l, y + row_h * 0.5, w, row_h * 0.25), Qt.AlignLeft | Qt.AlignTop,
                           f"GPIO {CHANNEL_PINS[si - 1]} · {s.leds} píxeles")
        p.end()


class LayoutDialog(QDialog):
    COLS = ["Canal", "Pin (GPIO)", "Píxeles", "Segmentos", "Zonas"]

    def __init__(self, ctl, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self.setWindowTitle("Montaje de luces")
        self.resize(640, 520)
        lay = QVBoxLayout(self)
        help_ = QLabel(
            "Cada canal es una tira conectada a su pin del ESP32. <b>Píxeles</b> = puntos que se "
            "controlan por separado (en WS2811 de 12 V, cada grupo de 3 LEDs es 1 píxel). Cada tira se "
            "divide en <b>segmentos</b> iguales; cada segmento es una <b>zona</b> de los efectos "
            "(<i>zones</i>: all, odd, even, strip:N, section:N o 1,5,9), numeradas tira a tira.")
        help_.setWordWrap(True)
        lay.addWidget(help_)

        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        lay.addWidget(self.table)

        row = QHBoxLayout()
        self.add_btn = QPushButton("＋ Añadir tira")
        self.add_btn.clicked.connect(self._add)
        self.del_btn = QPushButton("－ Quitar la última")
        self.del_btn.clicked.connect(self._remove)
        row.addWidget(self.add_btn)
        row.addWidget(self.del_btn)
        row.addStretch(1)
        self.summary = QLabel()
        row.addWidget(self.summary)
        lay.addLayout(row)

        self.preview = LayoutPreview()
        lay.addWidget(self.preview, 1)

        btns = QHBoxLayout()
        self.identify_btn = QPushButton("Identificar zonas")
        self.identify_btn.setToolTip("Envía el montaje y enciende cada zona de un color, con su primer "
                                     "píxel en blanco (también en el simulador)")
        self.identify_btn.clicked.connect(self._identify)
        save = QPushButton("Guardar y enviar al ESP32")
        save.setDefault(True)
        save.clicked.connect(self._save)
        close = QPushButton("Cerrar")
        close.clicked.connect(self.reject)
        btns.addWidget(self.identify_btn)
        btns.addStretch(1)
        btns.addWidget(save)
        btns.addWidget(close)
        lay.addLayout(btns)

        for s in ctl.lights.layout:
            self._append(s)
        self._refresh()

    # --- filas --------------------------------------------------------------
    def _append(self, s: StripLayout) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setCellWidget(r, 0, QLabel(f"  Tira {r + 1}"))
        self.table.setCellWidget(r, 1, QLabel(f"  {CHANNEL_PINS[r]}"))
        leds = QSpinBox()
        leds.setRange(1, MAX_LEDS)
        leds.setValue(s.leds)
        seg = QSpinBox()
        seg.setRange(1, MAX_SEGMENTS)
        seg.setValue(s.segments)
        for w in (leds, seg):
            w.valueChanged.connect(self._refresh)
        self.table.setCellWidget(r, 2, leds)
        self.table.setCellWidget(r, 3, seg)
        self.table.setCellWidget(r, 4, QLabel())

    def strips(self) -> list[StripLayout]:
        return [StripLayout(self.table.cellWidget(r, 2).value(), self.table.cellWidget(r, 3).value())
                for r in range(self.table.rowCount())]

    def _add(self) -> None:
        if self.table.rowCount() >= MAX_STRIPS:
            return
        last = self.strips()[-1] if self.table.rowCount() else StripLayout(14, 3)
        self._append(last)
        self._refresh()

    def _remove(self) -> None:
        if self.table.rowCount() > 1:
            self.table.removeRow(self.table.rowCount() - 1)
            self._refresh()

    def _refresh(self) -> None:
        raw = self.strips()
        strips = normalize_layout({"strips": [{"leds": s.leds, "segments": s.segments} for s in raw]})
        z = 1
        for r, s in enumerate(strips):
            label = self.table.cellWidget(r, 4)
            label.setText(f"  {z}–{z + s.segments - 1}" if s.segments > 1 else f"  {z}")
            z += s.segments
        n = total_zones(raw)
        warn = f" · ⚠ máximo {MAX_ZONES} zonas" if n > MAX_ZONES else ""
        cut = any(s.segments > s.leds for s in raw)
        self.summary.setText(f"{len(raw)} tiras · {total_zones(strips)} zonas{warn}"
                             + (" · ⚠ más segmentos que píxeles" if cut else ""))
        self.add_btn.setEnabled(self.table.rowCount() < MAX_STRIPS)
        self.del_btn.setEnabled(self.table.rowCount() > 1)
        self.preview.set_strips(strips)

    # --- acciones -----------------------------------------------------------
    def _identify(self) -> None:
        from ..lights.commands import LightCommand
        mgr = self.ctl.lights
        mgr.set_layout(self.strips(), save=False)
        mgr.sender.send(LightCommand("identify", {"dur_ms": 8000}))

    def _save(self) -> None:
        ok = self.ctl.lights.set_layout(self.strips(), save=True)
        msg = ("Montaje guardado en config.json y enviado al ESP32 (lo guarda en su memoria)."
               if ok else "Montaje guardado en config.json, pero no se pudo enviar al ESP32 "
               "(¿está encendido y en la misma Wi-Fi?). Se enviará al activar «Luces».")
        QMessageBox.information(self, "Montaje de luces", msg)
        self.accept()
