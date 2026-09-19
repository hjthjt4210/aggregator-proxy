"""日志页：实时显示运行日志。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..logger import QtLogEmitter


class LogsPage(QWidget):
    def __init__(self, emitter: QtLogEmitter):
        super().__init__()
        self.emitter = emitter
        self._all_logs: list[str] = []
        self._build()
        self.emitter.message.connect(self._append)

    def _build(self) -> None:
        self.setAttribute(Qt.WA_StyledBackground, True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 22, 28, 22)
        outer.setSpacing(12)

        title = QLabel("日志")
        title.setObjectName("pageTitle")
        outer.addWidget(title)

        subtitle = QLabel("实时查看聚合代理运行日志")
        subtitle.setObjectName("pageSubtitle")
        outer.addWidget(subtitle)

        toolbar = QHBoxLayout()
        self._btn_clear = QPushButton("清空")
        self._btn_clear.clicked.connect(self.clear)
        self._filter_combo = QComboBox()
        self._filter_combo.addItems(["全部", "错误", "警告", "信息", "调试"])
        self._filter_combo.currentTextChanged.connect(self._apply_filter)
        toolbar.addWidget(QLabel("级别："))
        toolbar.addWidget(self._filter_combo)
        toolbar.addStretch(1)
        toolbar.addWidget(self._btn_clear)
        outer.addLayout(toolbar)

        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setLineWrapMode(QPlainTextEdit.NoWrap)
        mono = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        mono.setPointSizeF(9.5)
        self.text.setFont(mono)
        self.text.setPlaceholderText("暂无日志……")
        outer.addWidget(self.text, 1)

    def _append(self, msg: str) -> None:
        self._all_logs.append(msg)
        if self._should_show(msg):
            self.text.appendPlainText(msg)
            self.text.verticalScrollBar().setValue(self.text.verticalScrollBar().maximum())

    def clear(self) -> None:
        self._all_logs.clear()
        self.text.clear()

    def _should_show(self, msg: str) -> bool:
        level = self._filter_combo.currentText()
        if level == "全部":
            return True
        level_map = {"错误": "ERROR", "警告": "WARNING", "信息": "INFO", "调试": "DEBUG"}
        keyword = level_map.get(level, "")
        return keyword in msg.upper()

    def _apply_filter(self) -> None:
        self.text.clear()
        for msg in self._all_logs:
            if self._should_show(msg):
                self.text.appendPlainText(msg)
        self.text.verticalScrollBar().setValue(self.text.verticalScrollBar().maximum())
