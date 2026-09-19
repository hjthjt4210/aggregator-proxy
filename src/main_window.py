"""聚合代理 主窗口。

顶栏：运行状态、端口、API 接入地址 + 启动/停止/重启。
主体：左侧导航 + QStackedWidget（分组管理/上游管理/日志/设置）。
"""
from __future__ import annotations

import logging
import math

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import (
    QAction,
    QColor,
    QFont,
    QGuiApplication,
    QIcon,
    QLinearGradient,
    QPainter,
    QPixmap,
    QPen,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QPushButton,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from .api_server import ApiServerController
from .config_manager import ConfigManager
from .logger import QtLogEmitter, setup_logging
from .pages.groups_page import GroupsPage
from .pages.logs_page import LogsPage
from .pages.overview_page import api_display_host
from .pages.settings_page import SettingsPage
from .pages.upstreams_page import UpstreamsPage

STYLESHEET = """
/* ================= 聚合代理 · Nebula 暗色主题 v3 ================= */
* { outline: none; }

QMainWindow, QWidget#workspace, QStackedWidget, QWidget#upstreamsPage { background: #0a0c12; }
QWidget {
    color: #e7ebf4;
    font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
    font-size: 13px;
    background: transparent;
}
QWidget#toggleSwitch, QWidget#cellActions, QWidget#cellStatus, QWidget#cellToggle { background: transparent; }

/* ---------- 顶栏 ---------- */
QFrame#topBar { background: #0d1019; border: none; border-bottom: 1px solid #1a2033; }
QLabel#brandTitle { color: #f2f5fb; font-size: 16px; font-weight: 700; letter-spacing: 0.5px; }
QLabel#brandSub { color: #5d6880; font-size: 10px; font-weight: 500; letter-spacing: 3px; }
QLabel#statusPill {
    color: #9aa5bd; background: #141a2a; border: 1px solid #232d47;
    border-radius: 13px; padding: 4px 14px; font-weight: 600;
}
QLabel#statusPill[running="true"] {
    color: #4ade80; background: rgba(74, 222, 128, 0.10);
    border-color: rgba(74, 222, 128, 0.30);
}
QLabel#statusPill[error="true"] {
    color: #e8b04b; background: rgba(232, 176, 75, 0.12);
    border-color: rgba(232, 176, 75, 0.38);
}
QLabel#infoChip { color: #7f8ba6; font-size: 12px; }

/* ---------- 按钮 ---------- */
QPushButton {
    min-height: 32px; padding: 0 16px; border-radius: 8px;
    background: #141926; border: 1px solid #262f45; color: #dde3ef;
    font-weight: 600;
}
QPushButton:hover:enabled { background: #1b2234; border-color: #384361; }
QPushButton:pressed:enabled { background: #111726; }
QPushButton:disabled { color: #4f5a70; background: #10141f; border-color: #1c2336; }

QPushButton#primaryBtn {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #8b7cf8, stop:1 #6c5ce7);
    border: 1px solid #7c6cf0; color: #ffffff;
}
QPushButton#primaryBtn:hover:enabled {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #9a8dfa, stop:1 #7a6af0);
    border-color: #8b7cf8;
}
QPushButton#primaryBtn:pressed:enabled { background: #6355d6; border-color: #6c5ce7; color: #eef0ff; }
QPushButton#primaryBtn:disabled { background: #232a44; border-color: #232a44; color: #4f5a70; }

QPushButton#actionSuccess { background: #2fbf83; border-color: #2fbf83; color: #06281c; }
QPushButton#actionSuccess:hover:enabled { background: #3ecf8e; border-color: #3ecf8e; }
QPushButton#actionSuccess:disabled { background: #10141f; border-color: #1c2336; color: #4f5a70; }
QPushButton#actionPrimary { background: transparent; border-color: #262f45; color: #dde3ef; }
QPushButton#actionDanger { background: transparent; border-color: rgba(240, 113, 111, 0.35); color: #f0716f; }
QPushButton#actionDanger:hover:enabled { background: rgba(240, 113, 111, 0.10); border-color: rgba(240, 113, 111, 0.55); }
QPushButton#actionDanger:disabled { color: #4f5a70; border-color: #1c2336; background: transparent; }
QPushButton#darkActionPrimary { background: transparent; border-color: #262f45; color: #dde3ef; }
QPushButton#darkActionPrimary:hover:enabled { background: #1b2234; border-color: #384361; }
QPushButton#darkActionSuccess { background: transparent; border-color: #262f45; color: #dde3ef; }
QPushButton#darkActionSuccess:hover:enabled { background: #1b2234; border-color: #384361; }
QPushButton#darkIconButton, QPushButton#darkDeleteButton {
    min-height: 0px; min-width: 0px; padding: 0px; background: transparent;
    border: 1px solid #262f45; border-radius: 7px; color: #aab4c9;
    font-size: 13px; font-weight: 500;
}
QPushButton#darkIconButton:hover:enabled { background: #1d2537; border-color: #3a4a6e; color: #ffffff; }
QPushButton#darkDeleteButton { color: #f0716f; border-color: rgba(240, 113, 111, 0.30); }
QPushButton#darkDeleteButton:hover:enabled { background: rgba(240, 113, 111, 0.12); border-color: rgba(240, 113, 111, 0.55); color: #ffa3a1; }

/* ---------- 侧边导航 ---------- */
QListWidget#sideNav {
    background: #0c0e15; border: none; border-right: 1px solid #1a2033;
    padding: 14px 10px; font-size: 13px;
}
QListWidget#sideNav::item {
    height: 40px; margin: 2px 4px; padding: 0 12px; border-radius: 9px;
    color: #8b96ad; font-weight: 600;
    border: 1px solid transparent;
}
QListWidget#sideNav::item:selected {
    background: rgba(124, 108, 240, 0.15);
    color: #c3b9ff;
    border: 1px solid rgba(124, 108, 240, 0.32);
}
QListWidget#sideNav::item:hover:!selected { background: #131827; color: #e7ebf4; }

/* ---------- 页面标题 ---------- */
QLabel#pageTitle, QLabel#upstreamPageTitle { color: #f2f5fb; font-size: 20px; font-weight: 700; }
QLabel#pageSubtitle, QLabel#upstreamPageSubtitle { color: #66718c; font-size: 12px; }
QLabel#hint { color: #5d6880; font-size: 12px; }

/* ---------- 卡片 ---------- */
QFrame#statCard, QFrame#formCard {
    background: #10131d; border: 1px solid #1a2033; border-radius: 12px;
}
QFrame#statCard:hover { border-color: #2c3450; background: #12162a; }
QLabel#statTitle { color: #66718c; font-size: 12px; font-weight: 600; }
QLabel#statValue { color: #f2f5fb; font-size: 21px; font-weight: 700; }

/* ---------- 输入控件 ---------- */
QLineEdit, QSpinBox, QComboBox {
    min-height: 34px; background: #0d1018; border: 1px solid #232c42;
    border-radius: 9px; padding: 0 12px; color: #e7ebf4;
    selection-background-color: rgba(124, 108, 240, 0.40);
}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {
    border-color: #7c6cf0; background: #0e1220;
}
QLineEdit:disabled, QSpinBox:disabled { color: #4f5a70; background: #0b0e15; border-color: #1c2336; }
QLineEdit#searchBox { border-radius: 10px; }
QComboBox QAbstractItemView {
    background: #10141f; border: 1px solid #262f45; border-radius: 9px;
    selection-background-color: rgba(124, 108, 240, 0.22);
    selection-color: #ffffff; padding: 4px;
}
QComboBox::drop-down { subcontrol-origin: padding; subcontrol-position: center right; width: 26px; border: none; }
QComboBox::down-arrow {
    width: 0px; height: 0px; border-left: 5px solid transparent;
    border-right: 5px solid transparent; border-top: 6px solid #8b96ad; margin-right: 12px;
}
QComboBox:hover::down-arrow { border-top-color: #c3b9ff; }
QSpinBox::up-button, QSpinBox::down-button { width: 0px; height: 0px; border: none; }
QSpinBox::up-arrow, QSpinBox::down-arrow { width: 0px; height: 0px; }

/* ---------- 表格 ---------- */
QTableWidget {
    background: #0e111a; alternate-background-color: #10131d;
    border: 1px solid #1a2033; border-radius: 12px; gridline-color: transparent;
    selection-background-color: rgba(124, 108, 240, 0.14);
    selection-color: #ffffff; color: #e7ebf4;
}
QTableWidget::item { padding: 6px 10px; border: none; }
QTableWidget::item:selected { color: #ffffff; }
QTableWidget#modelPicker { background: #0d1018; border-color: #1a2033; }
QHeaderView::section {
    background: #0c0f18; color: #6b7690; border: none;
    border-bottom: 1px solid #1a2033; padding: 11px 12px;
    font-weight: 600; font-size: 11px;
}
QTableCornerButton::section { background: #0c0f18; border: none; }

/* ---------- 状态胶囊 / 动态提示 ---------- */
QLabel#statusHealthy, QLabel#statusUnhealthy, QLabel#statusChecking, QLabel#statusUnknown, QLabel#statusDone, QLabel#statusCooldown {
    min-width: 60px; padding: 5px 14px; border-radius: 12px; font-weight: 600; font-size: 12px;
}
QLabel#statusHealthy { color: #3ecf8e; background: rgba(62, 207, 142, 0.10); border: 1px solid rgba(62, 207, 142, 0.28); }
QLabel#statusDone { color: #5eead4; background: rgba(94, 234, 212, 0.10); border: 1px solid rgba(94, 234, 212, 0.30); }
QLabel#statusCooldown { color: #c3b9ff; background: rgba(124, 108, 240, 0.12); border: 1px solid rgba(124, 108, 240, 0.35); }
QLabel#statusUnhealthy { color: #f0716f; background: rgba(240, 113, 111, 0.10); border: 1px solid rgba(240, 113, 111, 0.28); }
QLabel#statusChecking { color: #e8b04b; background: rgba(232, 176, 75, 0.10); border: 1px solid rgba(232, 176, 75, 0.28); }
QLabel#statusUnknown { color: #b6c0d4; background: #141a2a; border: 1px solid #262f45; }
QLabel#statusOk { color: #3ecf8e; font-weight: 600; }
QLabel#statusErr { color: #f0716f; font-weight: 600; }

/* ---------- 弹窗 / 菜单 ---------- */
QDialog, QMessageBox { background: #0f1320; color: #e7ebf4; }
QDialog QLabel, QMessageBox QLabel { color: #dde3ef; }
QDialog QPushButton, QMessageBox QPushButton { min-height: 30px; }
QLabel#dialogIntro { color: #7f8ba6; font-size: 12px; }
QLabel#dialogSelection { color: #3ecf8e; font-weight: 600; }
QLabel#groupBadge {
    color: #c3b9ff; background: rgba(124, 108, 240, 0.14);
    border: 1px solid rgba(124, 108, 240, 0.35); border-radius: 12px;
    padding: 4px 12px; font-weight: 600; font-size: 12px;
}
QMenu {
    background: #10141f; border: 1px solid #262f45; border-radius: 10px;
    padding: 6px; color: #dde3ef;
}
QMenu::item { padding: 7px 24px 7px 14px; border-radius: 7px; background: transparent; }
QMenu::item:selected { background: rgba(124, 108, 240, 0.20); color: #ffffff; }
QMenu::separator { height: 1px; background: #1a2033; margin: 5px 8px; }

/* ---------- 其他 ---------- */
QPlainTextEdit {
    background: #0d1018; color: #c9d3e2; border: 1px solid #232c42;
    border-radius: 10px; padding: 8px;
    selection-background-color: rgba(124, 108, 240, 0.35);
}
QLabel#emptyState, QWidget#emptyState {
    color: #5d6880; background: #0d1018; border: 1px dashed #262f45;
    border-radius: 12px; padding: 24px;
}
QStatusBar { background: #0d1019; color: #5d6880; border-top: 1px solid #1a2033; }
QStatusBar QLabel { color: #5d6880; }
QFormLayout QLabel { color: #9aa5bd; }
QCheckBox { spacing: 8px; color: #dde3ef; }

/* ---------- 滚动条 ---------- */
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #262f45; border-radius: 4px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #384361; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: #262f45; border-radius: 4px; min-width: 30px; }
QScrollBar::handle:horizontal:hover { background: #384361; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0px; height: 0px; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
"""


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("聚合代理")
        self.resize(1120, 720)
        self.setMinimumSize(900, 600)

        self.cfg = ConfigManager()
        self.log_emitter = QtLogEmitter()
        self.logger = setup_logging(self.cfg.logs_dir, emitter=self.log_emitter)
        self.logger.info("聚合代理程序启动……")

        self.api = ApiServerController(self.cfg)
        self.api.state_changed.connect(self._on_api_state_changed)
        self.api.log_message.connect(self._on_api_log)

        self._force_quit = False
        self._api_error: str | None = None
        self.setStyleSheet(STYLESHEET)
        self._build_ui()
        self._setup_tray()
        self._refresh_status()
        self.api.start_failed.connect(self._on_start_failed)
        self.api.start()

    # ------------------------------------------------------------- 界面
    def _build_ui(self) -> None:
        central = QWidget()
        central.setObjectName("workspace")
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_top_bar())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        # 先建立 stack/pages，再建 nav（nav.setCurrentRow(0) 会触发槽函数，需要 pages 已存在）
        self._build_stack()
        self._build_nav()
        self._nav_timer = QTimer(self)
        self._nav_timer.setInterval(1000)
        self._nav_timer.timeout.connect(self._refresh_nav)
        self._nav_timer.start()
        body.addWidget(self.nav)
        body.addWidget(self.stack, 1)
        root.addLayout(body, 1)

        self.setCentralWidget(central)
        self.statusBar().showMessage("就绪")

    def _build_top_bar(self) -> QFrame:
        top = QFrame()
        top.setObjectName("topBar")
        lay = QHBoxLayout(top)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(18)

        logo = QLabel()
        logo.setPixmap(self._make_logo_pixmap(28))
        logo.setFixedSize(28, 28)
        logo.setAlignment(Qt.AlignCenter)
        lay.addWidget(logo)

        brand = QVBoxLayout()
        brand.setSpacing(0)
        brand_title = QLabel("聚合代理")
        brand_title.setObjectName("brandTitle")
        brand_sub = QLabel("本地智能网关")
        brand_sub.setObjectName("brandSub")
        brand.addWidget(brand_title)
        brand.addWidget(brand_sub)
        lay.addLayout(brand)

        self.status_label = QLabel("●  未启动")
        self.status_label.setObjectName("statusPill")
        self.status_label.setCursor(Qt.PointingHandCursor)
        self.status_label.setToolTip("点击前往设置页")
        self.status_label.installEventFilter(self)
        self._api_url = ""
        self._api_info = QLabel()
        self._api_info.setObjectName("infoChip")
        self._api_info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._api_info.setCursor(Qt.PointingHandCursor)
        self._api_info.setToolTip("点击复制 API 调用地址")
        self._api_info.installEventFilter(self)

        lay.addWidget(self.status_label)
        lay.addWidget(self._api_info)
        lay.addStretch(1)

        # 服务控制按钮（接入真实启停逻辑）
        self._btn_start = QPushButton("启动服务")
        self._btn_stop = QPushButton("停止服务")
        self._btn_restart = QPushButton("重启服务")
        self._btn_start.setObjectName("actionSuccess")
        self._btn_stop.setObjectName("actionDanger")
        self._btn_restart.setObjectName("actionPrimary")
        self._btn_start.clicked.connect(self._on_start)
        self._btn_stop.clicked.connect(self._on_stop)
        self._btn_restart.clicked.connect(self._on_restart)
        self._btn_start.setEnabled(True)
        self._btn_stop.setEnabled(False)
        self._btn_restart.setEnabled(False)
        for btn in (self._btn_start, self._btn_stop, self._btn_restart):
            lay.addWidget(btn)
        return top

    def _build_nav(self) -> QListWidget:
        self.nav = QListWidget()
        self.nav.setObjectName("sideNav")
        self.nav.setIconSize(QSize(20, 20))
        self.nav.setFixedWidth(188)
        self._nav_signature = None
        self._refresh_nav(force=True)
        self.nav.currentRowChanged.connect(self._on_nav_changed)
        self.nav.setCurrentRow(1)
        return self.nav

    # ------------------------------------------------------------- 图标绘制
    def _make_logo_pixmap(self, size: int = 28) -> QPixmap:
        """渐变圆角方块 + 「聚」字，用于顶栏 logo 与托盘图标。"""
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        gradient = QLinearGradient(0, 0, size, size)
        gradient.setColorAt(0.0, QColor("#8b7cf8"))
        gradient.setColorAt(1.0, QColor("#5b8af5"))
        painter.setPen(Qt.NoPen)
        painter.setBrush(gradient)
        painter.drawRoundedRect(QRectF(0.5, 0.5, size - 1, size - 1), size * 0.3, size * 0.3)
        painter.setPen(QColor("#ffffff"))
        font = QFont("Microsoft YaHei UI")
        font.setBold(True)
        font.setPixelSize(max(10, int(size * 0.55)))
        painter.setFont(font)
        painter.drawText(pixmap.rect(), Qt.AlignCenter, "聚")
        painter.end()
        return pixmap

    def _nav_icon(self, kind: str) -> QIcon:
        """绘制 20x20 线性导航图标；Selected 模式使用高亮色。"""

        def paint(color: str) -> QPixmap:
            pm = QPixmap(20, 20)
            pm.fill(Qt.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.Antialiasing)
            pen = QPen(QColor(color), 1.6)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            if kind == "groups":
                p.drawRoundedRect(QRectF(3, 3, 9, 9), 3, 3)
                p.drawRoundedRect(QRectF(8, 8, 9, 9), 3, 3)
            elif kind == "upstreams":
                # 双向箭头：请求上行 / 响应下行
                p.drawLine(QPointF(4, 6.5), QPointF(16, 6.5))
                p.drawLine(QPointF(13, 3.8), QPointF(16, 6.5))
                p.drawLine(QPointF(13, 9.2), QPointF(16, 6.5))
                p.drawLine(QPointF(16, 13.5), QPointF(4, 13.5))
                p.drawLine(QPointF(7, 10.8), QPointF(4, 13.5))
                p.drawLine(QPointF(7, 16.2), QPointF(4, 13.5))
            elif kind == "logs":
                p.drawLine(QPointF(3.5, 5), QPointF(16.5, 5))
                p.drawLine(QPointF(3.5, 10), QPointF(16.5, 10))
                p.drawLine(QPointF(3.5, 15), QPointF(11.5, 15))
            elif kind == "settings":
                p.drawEllipse(QRectF(6.2, 6.2, 7.6, 7.6))
                for i in range(8):
                    angle = i * math.pi / 4
                    p.drawLine(
                        QPointF(10 + 6.2 * math.cos(angle), 10 + 6.2 * math.sin(angle)),
                        QPointF(10 + 8.4 * math.cos(angle), 10 + 8.4 * math.sin(angle)),
                    )
            elif kind == "dot":
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(color))
                p.drawEllipse(QPointF(10, 10), 2.6, 2.6)
            p.end()
            return pm

        icon = QIcon()
        icon.addPixmap(paint("#78829c"), QIcon.Normal)
        icon.addPixmap(paint("#c3b9ff"), QIcon.Selected)
        return icon

    def _refresh_nav(self, force: bool = False) -> None:
        groups = self.cfg.load().get("groups", [])
        signature = tuple((str(g.get("id", "")), str(g.get("name", ""))) for g in groups)
        if not force and signature == self._nav_signature:
            return
        current = self.nav.currentItem().data(Qt.UserRole) if self.nav.currentItem() else ("page", 1)
        self._nav_signature = signature
        self.nav.blockSignals(True)
        self.nav.clear()
        entries = [("分组管理", ("page", 0)), ("上游管理", ("page", 1))]
        entries.extend((f"    {g.get('name', '未命名分组')}", ("group", str(g.get("id", "")))) for g in groups)
        entries.extend([("日志", ("page", 2)), ("设置", ("page", 3))])
        icon_map = {0: "groups", 1: "upstreams", 2: "logs", 3: "settings"}
        target_row = 0
        for index, (title, data) in enumerate(entries):
            kind = icon_map.get(data[1], "dot") if data[0] == "page" else "dot"
            item = QListWidgetItem(self._nav_icon(kind), title)
            item.setData(Qt.UserRole, data)
            if data[0] == "group":
                item.setData(Qt.UserRole + 1, "child")
                font = item.font()
                font.setPointSizeF(10)
                font.setWeight(QFont.Weight.Medium)
                item.setFont(font)
                item.setForeground(QColor("#8aa0b3"))
            else:
                font = item.font()
                font.setPointSizeF(10.5)
                font.setWeight(QFont.Weight.DemiBold)
                item.setFont(font)
            self.nav.addItem(item)
            if data == current:
                target_row = index
        self.nav.setCurrentRow(target_row)
        self.nav.blockSignals(False)
        self._on_nav_changed(target_row)

    def _build_stack(self) -> QStackedWidget:
        self.stack = QStackedWidget()
        self.pages = [
            GroupsPage(self.cfg),
            UpstreamsPage(self.cfg),
            LogsPage(self.log_emitter),
            SettingsPage(self.cfg),
        ]
        self._logs_page = self.pages[2]
        for page in self.pages:
            self.stack.addWidget(page)
        return self.stack

    def _on_nav_changed(self, row: int) -> None:
        item = self.nav.item(row)
        if item is None:
            return
        kind, value = item.data(Qt.UserRole) or ("page", 0)
        if kind == "group":
            self.pages[1].set_group_filter(value)
            self.stack.setCurrentIndex(1)
        else:
            if int(value) == 1:
                self.pages[1].set_group_filter(None)
            self.stack.setCurrentIndex(int(value))

    # ------------------------------------------------------------- 状态
    def _refresh_status(self) -> None:
        cfg = self.cfg.load()
        server = cfg.get("server", {})
        port = server.get("port", 5000)
        running = self.api.running
        error = self._api_error
        if running:
            self.status_label.setText("●  运行中")
            self.status_label.setProperty("running", "true")
            self.status_label.setProperty("error", "false")
        elif error:
            self.status_label.setText(f"⚠  {error}")
            self.status_label.setProperty("running", "false")
            self.status_label.setProperty("error", "true")
        else:
            self.status_label.setText("●  未启动")
            self.status_label.setProperty("running", "false")
            self.status_label.setProperty("error", "false")
        style = self.status_label.style()
        style.unpolish(self.status_label)
        style.polish(self.status_label)
        self._api_url = f"http://{api_display_host(cfg)}:{port}/v1"
        self._api_info.setText(f"端口 {port}  •  接入地址 {self._api_url}")

    def _on_start_failed(self, message: str) -> None:
        self._api_error = message
        self._refresh_status()
        self.statusBar().showMessage(f"{message}：点击顶部黄色状态胶囊可前往设置页修改端口", 8000)

    def _goto_page(self, page: int) -> None:
        for row in range(self.nav.count()):
            data = self.nav.item(row).data(Qt.UserRole)
            if data and data[0] == "page" and int(data[1]) == page:
                self.nav.setCurrentRow(row)
                return

    def eventFilter(self, obj, event) -> bool:
        if event.type() != QEvent.MouseButtonPress:
            return super().eventFilter(obj, event)
        if obj is self.status_label:
            self._goto_page(3)
            return True
        if obj is self._api_info and self._api_url:
            QGuiApplication.clipboard().setText(self._api_url)
            self.statusBar().showMessage("API 地址已复制", 1800)
            return True
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------- 服务控制
    def _on_start(self) -> None:
        if self.api.start():
            self.logger.info("用户点击「启动服务」")
        else:
            self.statusBar().showMessage("服务已在运行中", 3000)

    def _on_stop(self) -> None:
        if self.api.running:
            self.logger.info("用户点击「停止服务」")
            self.api.stop()
            self.statusBar().showMessage("正在停止服务……", 3000)
        else:
            self.statusBar().showMessage("服务当前未运行", 3000)

    def _on_restart(self) -> None:
        self.logger.info("用户点击「重启服务」")
        self.api.restart()

    def _on_api_log(self, msg: str) -> None:
        # API 服务的日志回调（用于顶栏提示，详细日志仍走日志页）
        self.statusBar().showMessage(msg, 4000)

    def _on_api_state_changed(self, running: bool) -> None:
        if running:
            self._api_error = None
        self._btn_start.setEnabled(not running)
        self._btn_stop.setEnabled(running)
        self._btn_restart.setEnabled(running)
        self._refresh_status()
        if getattr(self, "_tray", None):
            self._tray.setToolTip("聚合代理 · 运行中" if running else "聚合代理 · 未启动")
            self._tray_start.setEnabled(not running)
            self._tray_stop.setEnabled(running)

    def _make_tray_icon(self) -> QIcon:
        return QIcon(self._make_logo_pixmap(64))

    def _setup_tray(self) -> None:
        self._tray = QSystemTrayIcon(self._make_tray_icon(), self)
        self._tray.setToolTip("聚合代理")
        menu = QMenu()
        show_action = QAction("显示窗口", self)
        show_action.triggered.connect(self._show_from_tray)
        self._tray_start = QAction("启动服务", self)
        self._tray_start.triggered.connect(self._on_start)
        self._tray_stop = QAction("停止服务", self)
        self._tray_stop.triggered.connect(self._on_stop)
        quit_action = QAction("退出", self)
        quit_action.triggered.connect(self._quit_from_tray)
        menu.addAction(show_action)
        menu.addSeparator()
        menu.addAction(self._tray_start)
        menu.addAction(self._tray_stop)
        menu.addSeparator()
        menu.addAction(quit_action)
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activated)
        icon = self._make_tray_icon()
        self.setWindowIcon(icon)
        self._tray.setIcon(icon)
        self._tray.show()

    def _on_tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self._show_from_tray()

    def _show_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _quit_from_tray(self) -> None:
        self._force_quit = True
        self.close()

    def closeEvent(self, event) -> None:
        close_to_tray = bool(self.cfg.load().get("app", {}).get("close_to_tray", False))
        if not self._force_quit and close_to_tray and self._tray.isVisible():
            event.ignore()
            self.hide()
            return
        if getattr(self, "_tray", None):
            self._tray.hide()
        self.api.shutdown()
        super().closeEvent(event)
        QApplication.quit()
