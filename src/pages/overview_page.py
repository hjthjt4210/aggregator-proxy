"""概览页：展示运行状态、端口、API 接入地址、配置路径等。"""
from __future__ import annotations

import socket

from PySide6.QtGui import QFont
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..config_manager import ConfigManager


def get_lan_ip() -> str:
    """获取本机局域网 IP。失败时回退回环地址。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(2)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def api_display_host(cfg: dict) -> str:
    """对外展示的接入主机名：通配监听时给局域网 IP，绑定具体地址时如实显示。"""
    host = str(cfg.get("server", {}).get("host", "127.0.0.1")).strip()
    return get_lan_ip() if host in ("0.0.0.0", "::") else (host or "127.0.0.1")


class _StatCard(QFrame):
    def __init__(self, title: str):
        super().__init__()
        self.setObjectName("statCard")
        self.setFrameShape(QFrame.StyledPanel)
        self.setMinimumHeight(112)
        box = QVBoxLayout(self)
        box.setContentsMargins(18, 16, 18, 16)
        box.setSpacing(8)
        self._title = QLabel(title)
        self._title.setObjectName("statTitle")
        self._value = QLabel("--")
        self._value.setObjectName("statValue")
        self._value.setWordWrap(True)
        font = self._value.font()
        font.setPointSize(18)
        font.setBold(True)
        self._value.setFont(font)
        box.addWidget(self._title)
        box.addWidget(self._value, 1)

    def set_value(self, value: str) -> None:
        self._value.setText(value)


class OverviewPage(QWidget):
    def __init__(self, config_manager: ConfigManager):
        super().__init__()
        self.cfg = config_manager
        self._running = False
        self._build()
        self.refresh()

    def _build(self) -> None:
        self.setAttribute(Qt.WA_StyledBackground, True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(18)

        title = QLabel("概览")
        title.setObjectName("pageTitle")
        outer.addWidget(title)
        subtitle = QLabel("本机代理服务与接入状态")
        subtitle.setObjectName("pageSubtitle")
        outer.addWidget(subtitle)

        # 统计卡片
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(14)
        self._card_status = _StatCard("运行状态")
        self._card_port = _StatCard("监听端口")
        self._card_lan = _StatCard("API 接入地址")
        self._card_groups = _StatCard("分组数")

        grid.addWidget(self._card_status, 0, 0)
        grid.addWidget(self._card_port, 0, 1)
        grid.addWidget(self._card_lan, 0, 2)
        grid.addWidget(self._card_groups, 0, 3)
        outer.addLayout(grid)

        # 底部工具
        tools = QHBoxLayout()
        self._refresh_btn = QPushButton("刷新")
        self._refresh_btn.clicked.connect(self.refresh)
        tools.addStretch(1)
        tools.addWidget(self._refresh_btn)
        outer.addLayout(tools)
        outer.addStretch(1)

    def set_running(self, running: bool) -> None:
        self._running = running
        self._card_status.set_value("运行中" if running else "未启动")

    def refresh(self) -> None:
        cfg = self.cfg.load()
        server = cfg.get("server", {})
        port = server.get("port", 5000)
        host = api_display_host(cfg)

        self._card_status.set_value("运行中" if self._running else "未启动")
        self._card_port.set_value(str(port))
        self._card_lan.set_value(f"http://{host}:{port}/v1")
        self._card_groups.set_value(str(len(cfg.get("groups", []))))
