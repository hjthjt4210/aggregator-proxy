"""共享界面控件：统一开关、数字输入、按钮视觉和加载指示器。"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIntValidator, QPainter, QPen
from PySide6.QtWidgets import QLineEdit, QWidget


class Spinner(QWidget):
    """旋转加载指示器：一段圆弧匀速转动，用于状态胶囊内。"""

    def __init__(self, size: int = 13, color: str = "#e8b04b", parent=None):
        super().__init__(parent)
        self._color = QColor(color)
        self._angle = 0
        self.setFixedSize(size, size)
        self._timer = QTimer(self)
        self._timer.setInterval(55)
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _tick(self) -> None:
        self._angle = (self._angle - 40) % 360
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        pen = QPen(self._color, 2)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        side = self.width() - 3
        painter.drawArc(QRectF(1.5, 1.5, side, side), self._angle * 16, 270 * 16)


class ToggleSwitch(QWidget):
    """绿色胶囊开关，开启时滑块位于右侧。"""
    toggled = Signal(bool)

    def __init__(self, checked: bool = False, parent=None):
        super().__init__(parent)
        self.setObjectName("toggleSwitch")
        self.setFixedSize(50, 28)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self.setCursor(Qt.PointingHandCursor)
        self._checked = bool(checked)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.setChecked(not self._checked)
            self.toggled.emit(self._checked)
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        track = QRectF(1, 4, 48, 20)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#7c6cf0" if self._checked else "#2a3148"))
        painter.drawRoundedRect(track, 10, 10)
        knob_x = 29 if self._checked else 5
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(QRectF(knob_x, 6, 16, 16))

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, checked: bool) -> None:
        self._checked = bool(checked)
        self.update()


class NumberEdit(QLineEdit):
    """无上下箭头的数字输入框。"""
    def __init__(self, value: int | str = "", parent=None):
        super().__init__(parent)
        self.setValidator(QIntValidator(0, 999999, self))
        self.setText(str(value) if value not in (None, "") else "")

    def number(self, default: int = 0) -> int:
        try:
            return int(self.text().strip())
        except ValueError:
            return default
