"""Montaje de las luces, en visual (pedido del usuario: «que sea visual, que no sea muy técnico»).

Cada tira se dibuja como una tira de luz real, partida en sus partes (cada parte = una zona de
los efectos, con su número). Se cambia con botones − / +: cuántas tiras, cuántos puntos de luz y
en cuántas partes se divide cada una. Clic en una parte = se enciende esa parte en las luces.
Se guarda en config.json (`lights.layout`) y se envía al ESP32, que lo recuerda.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (QAbstractSpinBox, QComboBox, QDialog, QFrame, QHBoxLayout, QLabel,
                               QMessageBox, QPushButton, QScrollArea, QSpinBox, QToolButton, QVBoxLayout,
                               QWidget)

from .i18n import tr
from ..lights.layout import (MAX_LEDS, MAX_SEGMENTS, MAX_STRIPS, MAX_ZONES, SAFE_PINS, StripLayout,
                             needs_restart, normalize_layout)

PART_COLORS = ["#ff5c5c", "#ffa94d", "#ffd43b", "#69db7c", "#38d9a9", "#4dabf7", "#748ffc", "#da77f2"]


def part_color(zone: int) -> QColor:
    return QColor(PART_COLORS[(zone - 1) % len(PART_COLORS)])


class Stepper(QWidget):
    """«Título  [−] 14 [+]» con botones grandes."""

    changed = Signal(int)

    def __init__(self, title: str, value: int, lo: int, hi: int, tip: str = "", parent=None):
        super().__init__(parent)
        self.lo, self.hi, self._value = lo, hi, value
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        t = QLabel(title)
        t.setStyleSheet("color:#9aa0a6;")
        self.minus, self.plus = QToolButton(), QToolButton()
        for b, txt in ((self.minus, "−"), (self.plus, "+")):
            b.setText(txt)
            b.setFixedSize(30, 30)
            b.setAutoRepeat(True)
            b.setStyleSheet("font-size:16px; font-weight:bold;")
        self.label = QSpinBox()                 # se puede escribir el número directamente
        self.label.setRange(lo, hi)
        self.label.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setMinimumWidth(46)
        self.label.setStyleSheet("font-size:16px; font-weight:bold;")
        self.label.valueChanged.connect(self.set_value)
        self.minus.clicked.connect(lambda: self.set_value(self._value - 1))
        self.plus.clicked.connect(lambda: self.set_value(self._value + 1))
        lay.addWidget(t)
        lay.addWidget(self.minus)
        lay.addWidget(self.label)
        lay.addWidget(self.plus)
        if tip:
            for w in (self, t, self.label):
                w.setToolTip(tip)
        self._sync()

    def value(self) -> int:
        return self._value

    def set_value(self, v: int) -> None:
        v = max(self.lo, min(self.hi, v))
        if v != self._value:
            self._value = v
            self._sync()
            self.changed.emit(v)

    def set_max(self, hi: int) -> None:
        self.hi = max(self.lo, hi)
        if self._value > self.hi:
            self.set_value(self.hi)
        self._sync()

    def _sync(self) -> None:
        self.label.blockSignals(True)
        self.label.setMaximum(self.hi)
        self.label.setValue(self._value)
        self.label.blockSignals(False)
        self.minus.setEnabled(self._value > self.lo)
        self.plus.setEnabled(self._value < self.hi)


class StripView(QWidget):
    """Dibujo de una tira: sus partes de colores con el número de zona y los puntos de luz."""

    part_clicked = Signal(int)          # número de zona

    def __init__(self, parent=None):
        super().__init__(parent)
        self.leds, self.segments, self.first_zone, self.scale_leds = 14, 3, 1, 14
        self.setMinimumHeight(58)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(tr("Clic en una parte para encenderla en las luces"))
        self._rects: list[tuple[QRectF, int]] = []

    def set_data(self, leds: int, segments: int, first_zone: int, scale_leds: int) -> None:
        self.leds, self.segments, self.first_zone, self.scale_leds = leds, segments, first_zone, scale_leds
        self.update()

    def paintEvent(self, _ev):  # noqa: N802 (Qt)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        gap = 8
        unit = (w - gap * (self.segments - 1) - 4) / max(self.scale_leds, 1)
        dot = max(2.5, min(7.0, unit * 0.32))
        self._rects = []
        x = 2.0
        font = QFont(self.font())
        font.setBold(True)
        if font.pointSizeF() > 0:
            font.setPointSizeF(font.pointSizeF() + 1)
        p.setFont(font)
        for c in range(self.segments):
            a, b = c * self.leds // self.segments, (c + 1) * self.leds // self.segments
            n = b - a
            zone = self.first_zone + c
            col = part_color(zone)
            rect = QRectF(x, 6, max(n * unit, 14), h - 12)
            self._rects.append((rect, zone))
            bg = QColor(col)
            bg.setAlpha(45)
            p.setPen(QPen(col, 2))
            p.setBrush(bg)
            p.drawRoundedRect(rect, 10, 10)
            for i in range(n):                                     # los puntos de luz
                cx = rect.left() + (i + 0.5) * (rect.width() / n)
                p.setPen(Qt.NoPen)
                glow = QColor(col)
                glow.setAlpha(70)
                p.setBrush(glow)
                p.drawEllipse(QRectF(cx - dot * 1.8, rect.center().y() - dot * 1.8, dot * 3.6, dot * 3.6))
                p.setBrush(col)
                p.drawEllipse(QRectF(cx - dot, rect.center().y() - dot, dot * 2, dot * 2))
            badge = QRectF(rect.left() + 4, rect.top() + 3, 22, 18)          # número de zona
            p.setBrush(QColor(0, 0, 0, 170))
            p.drawRoundedRect(badge, 6, 6)
            p.setPen(QColor("#ffffff"))
            p.drawText(badge, Qt.AlignCenter, str(zone))
            x = rect.right() + gap
        p.end()

    def mousePressEvent(self, ev):  # noqa: N802 (Qt)
        pos = ev.position() if hasattr(ev, "position") else ev.pos()
        for rect, zone in self._rects:
            if rect.contains(pos):
                self.part_clicked.emit(zone)
                return


class StripCard(QFrame):
    """Una tira: nombre, dibujo y botones − / + para puntos de luz y partes."""

    changed = Signal()
    removed = Signal(object)
    part_clicked = Signal(int)

    def __init__(self, strip: StripLayout, parent=None):
        super().__init__(parent)
        self._pin = strip.pin
        self.setObjectName("card")
        self.setStyleSheet("#card { background:#1b1e24; border:1px solid #2f343d; border-radius:10px; }")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 10)
        top = QHBoxLayout()
        self.title = QLabel()
        self.title.setStyleSheet("font-size:15px; font-weight:bold;")
        top.addWidget(self.title)
        top.addStretch(1)
        self.leds = Stepper(tr("Puntos de luz"), strip.leds, 1, MAX_LEDS,
                            tr("Cuántas luces se pueden encender por separado.\n"
                               "En tiras de 12 V (WS2811), cuenta cada grupo de 3 LEDs como 1."))
        self.parts = Stepper(tr("Partes"), strip.segments, 1, MAX_SEGMENTS,
                             tr("En cuántos trozos iguales se divide la tira.\n"
                                "Cada trozo puede tener su propio efecto."))
        top.addWidget(self.leds)
        top.addSpacing(14)
        top.addWidget(self.parts)
        self.delete = QToolButton()
        self.delete.setText("✕")
        self.delete.setToolTip(tr("Quitar esta tira"))
        self.delete.setFixedSize(30, 30)
        top.addSpacing(8)
        top.addWidget(self.delete)
        lay.addLayout(top)
        self.view = StripView()
        lay.addWidget(self.view)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        pin_label = QLabel(tr("Cable de datos en el pin"))
        pin_label.setStyleSheet("color:#9aa0a6;")
        self.pin = QComboBox()
        for p in SAFE_PINS:
            self.pin.addItem(f"{p}", p)
        self.pin.setToolTip(tr("Pin del ESP32 al que va el cable de datos de esta tira.\n"
                               "Se elige una vez al montarla; si lo cambias, las luces se reinician un momento."))
        if strip.pin in SAFE_PINS:
            self.pin.setCurrentIndex(SAFE_PINS.index(strip.pin))
        self.pin.currentIndexChanged.connect(lambda _i: self.changed.emit())
        bottom.addWidget(pin_label)
        bottom.addWidget(self.pin)
        lay.addLayout(bottom)
        self.leds.changed.connect(lambda _v: self.changed.emit())
        self.parts.changed.connect(lambda _v: self.changed.emit())
        self.delete.clicked.connect(lambda: self.removed.emit(self))
        self.view.part_clicked.connect(self.part_clicked.emit)

    def strip(self) -> StripLayout:
        return StripLayout(self.leds.value(), self.parts.value(), self.pin.currentData())

    def set_taken_pins(self, taken: set[int]) -> None:
        """Pines que usan otras tiras: se ven en gris y no se pueden elegir."""
        model = self.pin.model()
        for i, p in enumerate(SAFE_PINS):
            item = model.item(i)
            item.setEnabled(p not in taken)
            self.pin.setItemText(i, f"{p}" + (tr("  (otra tira)") if p in taken else ""))


class LayoutDialog(QDialog):
    def __init__(self, ctl, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self.setWindowTitle(tr("Mis luces"))
        self.resize(760, 560)
        root = QVBoxLayout(self)
        head = QLabel(tr("Tus tiras de luz"))
        head.setStyleSheet("font-size:18px; font-weight:bold;")
        root.addWidget(head)
        sub = QLabel(tr("Añade tus tiras (hasta 8) y dile cuántas luces tiene cada una y en cuántas partes la "
                        "quieres dividir. Cada parte de color puede tener su propio efecto. "
                        "Toca una parte para verla encenderse."))
        sub.setWordWrap(True)
        sub.setStyleSheet("color:#9aa0a6;")
        root.addWidget(sub)

        self.cards_box = QVBoxLayout()
        self.cards_box.setSpacing(10)
        holder = QWidget()
        inner = QVBoxLayout(holder)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.addLayout(self.cards_box)
        self.add_btn = QPushButton(tr("＋  Añadir tira"))
        self.add_btn.setMinimumHeight(46)
        self.add_btn.setStyleSheet("QPushButton { border:2px dashed #495057; border-radius:10px; "
                                   "font-size:15px; color:#ced4da; background:transparent; } "
                                   "QPushButton:hover { border-color:#ff8a1f; color:#ff8a1f; } "
                                   "QPushButton:disabled { color:#495057; }")
        self.add_btn.clicked.connect(self._add)
        inner.addWidget(self.add_btn)
        inner.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(holder)
        root.addWidget(scroll, 1)

        self.summary = QLabel()
        self.summary.setStyleSheet("color:#9aa0a6;")
        root.addWidget(self.summary)

        btns = QHBoxLayout()
        test = QPushButton(tr("✨  Probar en las luces"))
        test.setToolTip(tr("Cada parte se enciende de su color en las luces de verdad (y en el simulador)"))
        test.clicked.connect(self._identify)
        save = QPushButton(tr("Guardar"))
        save.setDefault(True)
        save.setStyleSheet("QPushButton { background:#ff8a1f; color:#111; font-weight:bold; "
                           "padding:6px 22px; border-radius:6px; }")
        save.clicked.connect(self._save)
        cancel = QPushButton(tr("Cancelar"))
        cancel.clicked.connect(self.reject)
        btns.addWidget(test)
        btns.addStretch(1)
        btns.addWidget(cancel)
        btns.addWidget(save)
        root.addLayout(btns)

        self.cards: list[StripCard] = []
        self._original = list(ctl.lights.layout)     # para deshacer las pruebas si se cancela
        for s in ctl.lights.layout:
            self._add_card(s)
        self._refresh()

    # --- tiras ----------------------------------------------------------------
    def _add_card(self, s: StripLayout) -> None:
        card = StripCard(s)
        card.changed.connect(self._refresh)
        card.removed.connect(self._remove)
        card.part_clicked.connect(self._flash_part)
        self.cards.append(card)
        self.cards_box.addWidget(card)

    def _add(self) -> None:
        if len(self.cards) < MAX_STRIPS:
            last = self.cards[-1].strip() if self.cards else StripLayout(14, 3)
            # pin: el de fábrica de esa posición (o el primero libre)
            new = normalize_layout({"strips": [{"leds": s.leds, "segments": s.segments, "pin": s.pin}
                                               for s in self.strips()]
                                    + [{"leds": last.leds, "segments": last.segments}]})[-1]
            self._add_card(new)
            self._refresh()

    def _remove(self, card: StripCard) -> None:
        if len(self.cards) <= 1:
            return
        self.cards.remove(card)
        card.setParent(None)
        card.deleteLater()
        self._refresh()

    def strips(self) -> list[StripLayout]:
        return [c.strip() for c in self.cards]

    def _refresh(self) -> None:
        scale = max(c.leds.value() for c in self.cards)
        zone = 1
        used = sum(c.parts.value() for c in self.cards)
        pins = [c.pin.currentData() for c in self.cards]
        for i, c in enumerate(self.cards, start=1):
            c.title.setText(tr("Tira {n}", n=i))
            c.set_taken_pins({p for k, p in enumerate(pins) if k != i - 1})
            # no más partes que luces ni más de 32 partes en total
            room = MAX_ZONES - (used - c.parts.value())
            c.parts.set_max(min(MAX_SEGMENTS, c.leds.value(), room))
            c.delete.setVisible(len(self.cards) > 1)
            c.view.set_data(c.leds.value(), c.parts.value(), zone, scale)
            zone += c.parts.value()
        n = len(self.cards)
        self.add_btn.setEnabled(n < MAX_STRIPS)
        self.add_btn.setText(tr("＋  Añadir tira") if n < MAX_STRIPS else tr("Máximo {n} tiras", n=MAX_STRIPS))
        parts = zone - 1
        self.summary.setText(tr("{n} tira · {parts} partes con efecto propio" if n == 1
                                else "{n} tiras · {parts} partes con efecto propio", n=n, parts=parts))

    def reject(self) -> None:  # Cancelar: las luces vuelven al montaje guardado
        if self.ctl.lights.layout != self._original:
            self.ctl.lights.set_layout(self._original, save=False)
        super().reject()

    # --- en las luces -----------------------------------------------------------
    def _send_layout(self) -> None:
        # Probar no reinicia las luces: si cambian pines o nº de tiras, se prueba con lo que hay
        # hasta pulsar «Guardar».
        new = normalize_layout({"strips": [{"leds": s.leds, "segments": s.segments, "pin": s.pin}
                                           for s in self.strips()]})
        if not needs_restart(self.ctl.lights.layout, new):
            self.ctl.lights.set_layout(self.strips(), save=False)

    def _flash_part(self, zone: int) -> None:
        from ..lights.commands import LightCommand
        self._send_layout()
        color = part_color(zone).name()
        self.ctl.lights.sender.send(LightCommand("flash", {"color": color, "dur_ms": 1200, "zones": str(zone)}))

    def _identify(self) -> None:
        from ..lights.commands import LightCommand
        self._send_layout()
        self.ctl.lights.sender.send(LightCommand("identify", {"dur_ms": 8000}))

    def _save(self) -> None:
        restart = needs_restart(self.ctl.lights.layout, normalize_layout(
            {"strips": [{"leds": s.leds, "segments": s.segments, "pin": s.pin} for s in self.strips()]}))
        ok = self.ctl.lights.set_layout(self.strips(), save=True)
        if not ok:
            QMessageBox.information(self, tr("Mis luces"),
                                    tr("Guardado. Las luces no han respondido (¿están encendidas y en la "
                                       "misma Wi-Fi?); se les enviará al activar «Luces»."))
        elif restart:
            QMessageBox.information(self, tr("Mis luces"),
                                    tr("Guardado. Como has cambiado pines o el número de tiras, las luces "
                                       "se reinician un momento para usarlos."))
        self.accept()
