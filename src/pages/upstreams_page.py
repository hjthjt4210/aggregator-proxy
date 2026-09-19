"""按分组查看上游；一个 URL/Key 上游可以配置多个模型。"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import datetime

import httpx
from PySide6.QtCore import QObject, QThread, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QFrame, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
    QSizePolicy, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..api_server import (
    check_group_now, check_upstream_now, get_cooldown_snapshot, get_health,
    reorder_group_models, _model_entries,
)
from ..config_manager import ConfigManager
from ..ui_widgets import NumberEdit, Spinner, ToggleSwitch
from .overview_page import api_display_host

logger = logging.getLogger("aggregator")


def _format_checked_at(value) -> str:
    try:
        stamp = float(value)
    except (TypeError, ValueError):
        return ""
    return datetime.fromtimestamp(stamp).strftime("%H:%M:%S")


class _CheckWorker(QObject):
    finished = Signal(list)

    def __init__(self, task):
        super().__init__()
        self._task = task

    def run(self) -> None:
        try:
            self.finished.emit(self._task() or [])
        except Exception as exc:  # noqa: BLE001
            self.finished.emit([{"status": "unhealthy", "error": str(exc)}])


def _mask_key(value: str) -> str:
    text = str(value or "")
    if len(text) <= 8:
        return text
    return f"{text[:4]}••••{text[-4:]}"


def _status_label(health: dict) -> QWidget:
    state = health.get("status", "unknown")
    names = {"healthy": ("可用", "statusHealthy"), "unhealthy": ("不可用", "statusUnhealthy"), "checking": ("检查中", "statusChecking")}
    text, name = names.get(state, ("未检查", "statusUnknown"))
    label = QLabel(text)
    label.setObjectName(name)
    label.setAlignment(Qt.AlignCenter)
    label.setAttribute(Qt.WA_StyledBackground, True)
    when = _format_checked_at(health.get("checked_at"))
    extra = []
    if health.get("latency_ms") is not None and state == "healthy":
        extra.append(f"{health.get('latency_ms')}ms")
    if when and state != "checking":
        extra.append(when)
    error = str(health.get("error") or "")
    tip = " · ".join(extra + ([error] if error and state == "unhealthy" else []))
    label.setToolTip(tip or text)
    wrap = QWidget()
    wrap.setObjectName("cellStatus")
    box = QHBoxLayout(wrap)
    box.setContentsMargins(4, 8, 4, 8)
    box.setAlignment(Qt.AlignCenter)
    box.addWidget(label)
    return wrap


def _make_status_widget(text: str, object_name: str, tooltip: str = "", spinner: bool = False) -> QWidget:
    """构造状态胶囊单元格；spinner=True 时胶囊内带旋转指示器。"""
    wrap = QWidget()
    wrap.setObjectName("cellStatus")
    box = QHBoxLayout(wrap)
    box.setContentsMargins(4, 8, 4, 8)
    box.setSpacing(6)
    box.setAlignment(Qt.AlignCenter)
    if spinner:
        spin = Spinner(13, "#e8b04b")
        spin.start()
        box.addWidget(spin)
    label = QLabel(text)
    label.setObjectName(object_name)
    label.setAlignment(Qt.AlignCenter)
    label.setAttribute(Qt.WA_StyledBackground, True)
    if tooltip:
        label.setToolTip(tooltip)
    box.addWidget(label)
    return wrap


def _extract_model_names(payload) -> list[str]:
    """从常见 /models 返回里抽出模型名，不要求必须是 OpenAI 的 data[].id。"""
    items = []
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        for key in ("data", "models", "result", "list", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                items = value
                break
        if not items:
            nested = payload.get("data")
            if isinstance(nested, dict):
                for key in ("data", "models", "list", "items"):
                    value = nested.get(key)
                    if isinstance(value, list):
                        items = value
                        break
    names: list[str] = []
    seen: set[str] = set()
    for item in items:
        name = ""
        if isinstance(item, str):
            name = item.strip()
        elif isinstance(item, dict):
            for key in ("id", "name", "model", "model_name", "slug"):
                value = item.get(key)
                if value:
                    name = str(value).strip()
                    break
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def _payload_preview(payload) -> str:
    if isinstance(payload, dict):
        keys = ", ".join(list(payload.keys())[:12])
        sample = payload.get("data", payload.get("models", payload.get("result")))
        extra = ""
        if isinstance(sample, list) and sample:
            first = sample[0]
            extra = f"；列表第一项类型={type(first).__name__}"
            if isinstance(first, dict):
                extra += f"，字段={', '.join(list(first.keys())[:12])}"
            elif isinstance(first, str):
                extra += f"，示例={first[:80]}"
        return f"顶层字段：{keys}{extra}"
    if isinstance(payload, list):
        first = payload[0] if payload else None
        extra = f"，第一项类型={type(first).__name__}" if first is not None else "，空列表"
        return f"顶层是数组，共 {len(payload)} 项{extra}"
    return f"顶层类型：{type(payload).__name__}"


def _complete_v1(value: str) -> str:
    """规范化接口地址，并补全 OpenAI 兼容接口常用的 /v1。"""
    value = value.strip().rstrip("/")
    if value and not value.lower().endswith("/v1"):
        value += "/v1"
    return value


class _ModelsDialog(QDialog):
    """模型多选：只有勾选后才开放对外名和优先级。"""

    def __init__(self, models: list[str], selected: list[dict] | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("选择模型")
        self.setMinimumSize(720, 480)
        self.resize(760, 520)
        selected_map = {str(x.get("name")): x for x in (selected or [])}
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 12)
        root.setSpacing(10)
        intro = QLabel("打开需要启用的模型开关，并设置优先级。")
        intro.setObjectName("dialogIntro")
        root.addWidget(intro)

        self.table = QTableWidget(0, 3)
        self.table.setObjectName("modelPicker")
        self.table.setHorizontalHeaderLabels(["启用", "模型名", "优先级"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.NoSelection)
        self.table.setFocusPolicy(Qt.NoFocus)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Fixed)
        self.table.setColumnWidth(0, 76)
        self.table.setColumnWidth(2, 108)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.table.itemClicked.connect(self._on_check_clicked)

        self._rows: list[tuple[ToggleSwitch, NumberEdit]] = []
        self._updating = False
        for row, name in enumerate(models):
            self.table.insertRow(row)
            old = selected_map.get(name, {})
            checked = name in selected_map
            toggle = ToggleSwitch(checked)
            toggle.toggled.connect(lambda state, r=row: self._apply_row_state(r, state))
            cell = QWidget()
            cell.setObjectName("cellToggle")
            cell_box = QHBoxLayout(cell)
            cell_box.setContentsMargins(8, 6, 8, 6)
            cell_box.addWidget(toggle, 0, Qt.AlignCenter)
            self.table.setCellWidget(row, 0, cell)

            model_item = QTableWidgetItem(name)
            model_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.table.setItem(row, 1, model_item)

            priority = NumberEdit(int(old.get("priority", row + 1)) if checked else "")
            priority.setPlaceholderText("优先级")
            priority.setEnabled(checked)
            priority.setFocusPolicy(Qt.StrongFocus if checked else Qt.NoFocus)
            priority.setMaximumWidth(96)
            self.table.setCellWidget(row, 2, priority)
            self.table.setRowHeight(row, 48)
            self._rows.append((toggle, priority))

        root.addWidget(self.table, 1)
        self._selected_label = QLabel()
        self._selected_label.setObjectName("dialogSelection")
        root.addWidget(self._selected_label)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("确定")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._update_selected_label()
        self.table.setFocus()

    def _on_check_clicked(self, item: QTableWidgetItem) -> None:
        return

    def _apply_row_state(self, row: int, checked: bool) -> None:
        if self._updating or not 0 <= row < len(self._rows):
            return
        _, priority = self._rows[row]
        self._updating = True
        priority.setEnabled(checked)
        priority.setFocusPolicy(Qt.StrongFocus if checked else Qt.NoFocus)
        if checked and not priority.text().strip():
            priority.setText(str(row + 1))
        elif not checked:
            priority.clear()
        self._updating = False
        self._update_selected_label()

    def _update_selected_label(self) -> None:
        count = sum(1 for toggle, _ in self._rows if toggle.isChecked())
        self._selected_label.setText(f"已选择 {count} 个模型")

    def result_models(self) -> list[dict]:
        result = []
        for row, (toggle, priority) in enumerate(self._rows):
            if not toggle.isChecked():
                continue
            name = self.table.item(row, 1).text()
            result.append({
                "name": name,
                "priority": priority.number(row + 1),
                "enabled": True,
            })
        return result


class _UpstreamDialog(QDialog):
    def __init__(self, group_id: str, parent=None, data: dict | None = None):
        super().__init__(parent); self._group_id = group_id; self._data = data or {}
        self.setWindowTitle("编辑上游" if data else "新增上游"); self.setMinimumWidth(560); self.resize(620, 420)
        form = QFormLayout(self); form.setContentsMargins(18, 16, 18, 16); form.setSpacing(10)
        self._name = QLineEdit(str(self._data.get("name", ""))); form.addRow("名称", self._name)
        url_row = QHBoxLayout(); url_row.setSpacing(10)
        self._base = QLineEdit(str(self._data.get("base_url", "")))
        self._base.setPlaceholderText("例如：https://api.example.com")
        has_v1 = self._base.text().strip().rstrip("/").lower().endswith("/v1")
        self._v1 = ToggleSwitch(True if not data else has_v1)
        self._v1.toggled.connect(self._on_v1_toggled)
        v1_label = QLabel("补全 /v1")
        url_row.addWidget(self._base, 1)
        url_row.addWidget(self._v1)
        url_row.addWidget(v1_label)
        form.addRow("接口地址", url_row)
        self._key = QLineEdit(str(self._data.get("api_key", ""))); form.addRow("上游密钥", self._key)
        model_row = QHBoxLayout(); self._model_hint = QLabel(self._models_text()); self._model_hint.setWordWrap(True)
        fetch = QPushButton("获取模型"); fetch.clicked.connect(self._fetch_models)
        manual = QPushButton("手动填写"); manual.clicked.connect(self._manual_models)
        model_row.addWidget(self._model_hint, 1); model_row.addWidget(fetch); model_row.addWidget(manual); form.addRow("模型列表", model_row)
        self._timeout = NumberEdit(self._data.get("timeout", "")); self._timeout.setPlaceholderText("留空使用默认值"); form.addRow("超时（秒）", self._timeout)
        self._enabled = ToggleSwitch(bool(self._data.get("enabled", True))); form.addRow("状态", self._enabled)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel); buttons.button(QDialogButtonBox.Ok).setText("确定"); buttons.button(QDialogButtonBox.Cancel).setText("取消"); buttons.accepted.connect(self._accept); buttons.rejected.connect(self.reject); form.addRow(buttons)

    def _on_v1_toggled(self, on: bool) -> None:
        value = self._base.text().strip().rstrip("/")
        if on:
            self._base.setText(_complete_v1(value))
            return
        if value.lower().endswith("/v1"):
            self._base.setText(value[:-3].rstrip("/"))

    def _models(self) -> list[dict]:
        models = self._data.get("models")
        if isinstance(models, list) and models: return models
        return _model_entries(self._data)

    def _models_text(self) -> str:
        models = self._models(); return "、".join(str(x.get("name")) for x in models) if models else "尚未选择模型"

    def _fetch_models(self) -> None:
        base = self._base.text().strip().rstrip("/")
        if self._v1.isChecked():
            base = _complete_v1(base)
            self._base.setText(base)
        key = self._key.text().strip()
        if not base or not key: QMessageBox.warning(self, "提示", "请先填写接口地址和上游密钥"); return
        try:
            response = httpx.get(f"{base}/models", headers={"Authorization": f"Bearer {key}", "Accept": "application/json"}, timeout=20)
            response.raise_for_status()
            try:
                payload = response.json()
            except ValueError:
                raise ValueError("返回的不是 JSON：" + response.text.strip()[:220])
            names = _extract_model_names(payload)
            if not names:
                box = QMessageBox(self)
                box.setWindowTitle("没有模型列表")
                box.setText("上游已连通，但 /models 返回空列表。这类网关通常不公开模型目录，需要按「模型名」或「模型名:分组」手动填写，例如 gpt-5.6-sol:plus。")
                fill = box.addButton("手动填写", QMessageBox.AcceptRole)
                box.addButton("取消", QMessageBox.RejectRole)
                box.exec()
                if box.clickedButton() is fill:
                    self._manual_models()
                return
        except ValueError as exc:
            QMessageBox.warning(self, "获取模型失败", f"上游返回的数据不是有效模型列表：{exc}"); return
        except httpx.HTTPStatusError as exc:
            QMessageBox.warning(self, "获取模型失败", f"上游接口返回 HTTP {exc.response.status_code}，请检查接口地址和上游密钥"); return
        except httpx.HTTPError as exc:
            QMessageBox.warning(self, "获取模型失败", f"连接上游失败：{exc}"); return
        except Exception as exc:
            QMessageBox.warning(self, "获取模型失败", f"读取模型列表失败：{exc}"); return
        dialog = _ModelsDialog(names, self._models(), self)
        if dialog.exec() == QDialog.Accepted:
            self._data["models"] = dialog.result_models(); self._model_hint.setText(self._models_text())

    def _manual_models(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("手动填写模型")
        dialog.setMinimumWidth(460)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(16, 14, 16, 12)
        hint = QLabel("每行一个模型名。绿 API 这类网关常用「模型名:分组」，例如 gpt-5.6-sol:plus。")
        hint.setObjectName("dialogIntro")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        editor = QPlainTextEdit()
        editor.setPlaceholderText("gpt-5.6-sol:plus")
        editor.setPlainText("\n".join(str(x.get("name")) for x in self._models()))
        editor.setFixedHeight(140)
        layout.addWidget(editor)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("确定")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.Accepted:
            return
        raw = editor.toPlainText().replace("、", "\n").replace("，", "\n").replace(",", "\n")
        names = [x.strip() for x in raw.splitlines() if x.strip()]
        if not names:
            QMessageBox.warning(self, "提示", "请至少填写一个模型名")
            return
        self._data["models"] = [{"name": name, "priority": index, "enabled": True} for index, name in enumerate(names, 1)]
        self._model_hint.setText(self._models_text())

    def _accept(self) -> None:
        if not self._name.text().strip() or not self._base.text().strip(): QMessageBox.warning(self, "提示", "名称和接口地址不能为空"); return
        if not self._models(): QMessageBox.warning(self, "提示", "请先获取模型，或点击“手动填写”至少填一个模型名"); return
        self.accept()

    def result_data(self) -> dict:
        base = self._base.text().strip().rstrip("/")
        if self._v1.isChecked():
            base = _complete_v1(base)
        return {"id": self._data.get("id") or uuid.uuid4().hex, "group_id": self._group_id, "name": self._name.text().strip(), "base_url": base, "api_key": self._key.text().strip(), "models": self._models(), "timeout": self._timeout.number(0), "enabled": self._enabled.isChecked()}


class UpstreamsPage(QWidget):
    check_done = Signal(list)  # 后台检查线程完成时发射（queued 回主线程）

    def __init__(self, config_manager: ConfigManager):
        super().__init__()
        self.cfg = config_manager
        self._filter_group_id = None
        self._busy = False
        self._pending = None
        # 冷却倒计时：row -> (胶囊 QLabel, 冷却结束时间戳)
        self._cooldown_cells: dict[int, tuple[QLabel, float]] = {}
        self._cooldown_timer = QTimer(self)
        self._cooldown_timer.setInterval(1000)
        self._cooldown_timer.timeout.connect(self._tick_cooldowns)
        self._search = None
        self._status_filter = None
        self._group_filter = None
        self._table = None
        self.check_done.connect(self._on_check_done)
        self._build()
        self.reload()

    def set_group_filter(self, group_id: str | None) -> None:
        self._filter_group_id = group_id
        self.reload()

    def _build(self) -> None:
        self.setObjectName("upstreamsPage")
        self.setAttribute(Qt.WA_StyledBackground, True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 22, 28, 22)
        outer.setSpacing(12)
        
        title = QLabel("上游管理")
        title.setObjectName("upstreamPageTitle")
        outer.addWidget(title)

        subtitle = QLabel("配置上游接口、模型与优先级")
        subtitle.setObjectName("upstreamPageSubtitle")
        outer.addWidget(subtitle)

        # 分组视图顶部：显示该分组的对外调用信息（聚合地址 + 聚合密钥），可一键复制
        self._full_url = ""
        self._full_key = ""
        self._group_bar = QFrame()
        self._group_bar.setObjectName("formCard")
        self._group_bar.setAttribute(Qt.WA_StyledBackground, True)
        bar_lay = QHBoxLayout(self._group_bar)
        bar_lay.setContentsMargins(16, 10, 16, 10)
        bar_lay.setSpacing(10)
        bar_lay.setAlignment(Qt.AlignVCenter)

        self._gbar_name = QLabel("")
        self._gbar_name.setObjectName("groupBadge")
        self._gbar_name.setAttribute(Qt.WA_StyledBackground, True)
        self._gbar_name.setFixedHeight(32)
        self._gbar_name.setAlignment(Qt.AlignCenter)
        bar_lay.addWidget(self._gbar_name)
        bar_lay.addSpacing(12)

        url_label = QLabel("聚合地址")
        url_label.setObjectName("hint")
        url_label.setFixedHeight(32)
        url_label.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        bar_lay.addWidget(url_label)
        self._gbar_url = QLineEdit()
        self._gbar_url.setReadOnly(True)
        self._gbar_url.setFixedHeight(32)
        self._gbar_url.setMinimumWidth(280)
        self._gbar_url.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        bar_lay.addWidget(self._gbar_url, 1)
        self._btn_copy_url = QPushButton("复制")
        self._btn_copy_url.setFixedSize(68, 32)
        bar_lay.addWidget(self._btn_copy_url)

        key_label = QLabel("聚合密钥")
        key_label.setObjectName("hint")
        key_label.setFixedHeight(32)
        key_label.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        bar_lay.addWidget(key_label)
        self._gbar_key = QLineEdit()
        self._gbar_key.setReadOnly(True)
        self._gbar_key.setFixedHeight(32)
        self._gbar_key.setFixedWidth(240)
        self._gbar_key.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        bar_lay.addWidget(self._gbar_key)
        self._btn_copy_key = QPushButton("复制")
        self._btn_copy_key.setFixedSize(68, 32)
        bar_lay.addWidget(self._btn_copy_key)

        self._btn_copy_url.clicked.connect(lambda: self._copy_group_value(self._full_url, self._btn_copy_url, "聚合地址已复制"))
        self._btn_copy_key.clicked.connect(lambda: self._copy_group_value(self._full_key, self._btn_copy_key, "聚合密钥已复制"))
        self._group_bar.setVisible(False)
        outer.addWidget(self._group_bar)

        tools = QHBoxLayout()
        tools.setSpacing(8)
        
        self._search = QLineEdit()
        self._search.setObjectName("searchBox")
        self._search.setPlaceholderText("搜索上游、分组或模型")
        self._search.setFixedHeight(34)
        self._search.textChanged.connect(self._apply_filters)
        
        self._group_filter = QComboBox()
        self._group_filter.setFixedWidth(140)
        self._group_filter.currentIndexChanged.connect(self._on_group_filter_changed)
        
        self._status_filter = QComboBox()
        self._status_filter.addItems(["全部状态", "可用", "不可用", "检查中", "未检查"])
        self._status_filter.setFixedWidth(120)
        self._status_filter.currentIndexChanged.connect(self._apply_filters)
        
        refresh = QPushButton("刷新")
        refresh.setObjectName("darkActionPrimary")
        refresh.setFixedSize(88, 32)
        refresh.clicked.connect(self.reload)

        check_all = QPushButton("检查全部")
        check_all.setObjectName("darkActionPrimary")
        check_all.setFixedSize(96, 32)
        check_all.clicked.connect(self._check_all)

        add = QPushButton("＋ 新增上游")
        add.setObjectName("primaryBtn")
        add.setFixedSize(104, 34)
        add.clicked.connect(self._show_add_menu)

        tools.addWidget(self._search, 1)
        tools.addWidget(self._group_filter)
        tools.addWidget(self._status_filter)
        tools.addWidget(refresh)
        tools.addWidget(check_all)
        tools.addWidget(add)
        outer.addLayout(tools)
        
        self._table = QTableWidget(0, 6)
        self._table.setObjectName("darkUpstreamTable")
        self._table.setHorizontalHeaderLabels(["上游名称", "所属分组", "模型名", "优先级", "上游状态", "操作"])
        self._table.setSelectionMode(QTableWidget.NoSelection)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setWordWrap(False)
        self._table.verticalHeader().setVisible(False)
        self._table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Stretch)
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Fixed)
        header.setSectionResizeMode(2, QHeaderView.Fixed)
        header.setSectionResizeMode(3, QHeaderView.Fixed)
        # 上游状态列吃掉剩余宽度，保证表格左右铺满
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        header.setSectionResizeMode(5, QHeaderView.Fixed)

        self._table.setColumnWidth(1, 120)
        self._table.setColumnWidth(2, 180)
        self._table.setColumnWidth(3, 72)
        self._table.setColumnWidth(5, 160)
        self._table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        
        outer.addWidget(self._table, 1)

    def _clear(self) -> None:
        self._table.setRowCount(0)

    def _tick_cooldowns(self) -> None:
        """每秒刷新冷却倒计时；归零的行恢复为真实健康状态。"""
        if not self._cooldown_cells:
            self._cooldown_timer.stop()
            return
        now = time.time()
        finished: list[int] = []
        for row, (label, expire) in self._cooldown_cells.items():
            remain = expire - now
            if remain <= 0:
                finished.append(row)
                continue
            try:
                label.setText(f"冷却 {int(remain) + 1}s")
            except RuntimeError:
                finished.append(row)  # 胶囊控件已被重建
        for row in finished:
            self._cooldown_cells.pop(row, None)
            item = self._table.item(row, 0)
            if item is None:
                continue
            uid = str(item.data(Qt.UserRole + 2) or "")
            model_item = self._table.item(row, 2)
            model_name = model_item.text() if model_item else None
            self._table.setCellWidget(row, 4, _status_label(get_health(uid, model_name)))
        if not self._cooldown_cells:
            self._cooldown_timer.stop()
            self.reload()

    def _refresh_group_bar(self, cfg: dict, groups: list[dict]) -> None:
        """点进具体分组时显示该分组的对外调用信息（聚合地址 + 聚合密钥）。"""
        if not self._filter_group_id:
            self._group_bar.setVisible(False)
            return
        self._group_bar.setVisible(True)
        group = next(
            (g for g in groups if str(g.get("id", "")) == str(self._filter_group_id)), None
        )
        name = str((group or {}).get("name", "未命名分组"))
        port = int(cfg.get("server", {}).get("port", 5000))
        self._full_url = f"http://{api_display_host(cfg)}:{port}/v1"
        self._full_key = str((group or {}).get("api_key", ""))
        self._gbar_name.setText(f"{name}")
        self._gbar_url.setText(self._full_url)
        self._gbar_key.setText(_mask_key(self._full_key) if self._full_key else "（未设置）")
        self._gbar_key.setToolTip("点击「复制」获取完整密钥" if self._full_key else "")
        self._btn_copy_key.setEnabled(bool(self._full_key))

    def _copy_group_value(self, value: str, button: QPushButton, message: str) -> None:
        if not value:
            return
        QGuiApplication.clipboard().setText(value)
        old = button.text()
        button.setText("✓")
        button.setToolTip(message + "（已复制）")
        QTimer.singleShot(1500, lambda: (button.setText(old), button.setToolTip("")))
        window = self.window()
        if window is not None:
            window.statusBar().showMessage(message, 1800)

    @staticmethod
    def _make_empty_state(text: str, button_text: str, callback) -> QWidget:
        """空状态：提示文案 + 引导按钮。"""
        holder = QWidget()
        holder.setObjectName("emptyState")
        box = QVBoxLayout(holder)
        box.setContentsMargins(20, 12, 20, 12)
        box.setSpacing(14)
        box.addStretch(1)
        label = QLabel(text)
        label.setAlignment(Qt.AlignCenter)
        label.setStyleSheet("border: none; background: transparent; color: #8b96ad; font-size: 13px;")
        box.addWidget(label)
        button = QPushButton(button_text)
        button.setObjectName("primaryBtn")
        button.setFixedWidth(140)
        button.clicked.connect(callback)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(button)
        row.addStretch(1)
        box.addLayout(row)
        box.addStretch(1)
        return holder

    def reload(self) -> None:
        cfg = self.cfg.load()
        groups = cfg.get("groups", [])
        ups = cfg.get("upstreams", [])
        self._refresh_group_bar(cfg, groups)
        
        # 更新分组筛选下拉
        self._group_filter.blockSignals(True)
        self._group_filter.clear()
        self._group_filter.addItem("全部分组", None)
        for group in groups:
            self._group_filter.addItem(group.get("name", "未命名"), group.get("id"))
        if self._filter_group_id:
            for i in range(self._group_filter.count()):
                if self._group_filter.itemData(i) == self._filter_group_id:
                    self._group_filter.setCurrentIndex(i)
                    break
        self._group_filter.blockSignals(False)
        
        self._clear()
        # 点进具体分组时隐藏"所属分组"列（只有上游管理总页显示）
        self._table.setColumnHidden(1, self._filter_group_id is not None)
        
        # 筛选上游
        filtered_ups = [u for u in ups if self._filter_group_id is None or u.get("group_id") == self._filter_group_id]
        
        if not filtered_ups:
            self._table.setRowCount(1)
            self._table.setCellWidget(0, 0, self._make_empty_state("还没有上游", "＋ 新增上游", self._show_add_menu))
            self._table.setSpan(0, 0, 1, 6)
            return
        
        # 构建分组映射
        group_map = {g.get("id"): g.get("name", "未命名") for g in groups}

        # 冷却倒计时快照：key = "上游id::model" -> 结束时间戳
        cooldown_snapshot = get_cooldown_snapshot()
        self._cooldown_cells = {}

        # 展开所有上游的模型行
        model_rows = []
        for upstream in filtered_ups:
            for model in _model_entries(upstream):
                model_rows.append((upstream, model))
        
        # 按优先级排序
        model_rows.sort(key=lambda pair: (int(pair[1].get("priority", 999999)), str(pair[0].get("name", "")), str(pair[1].get("name", ""))))
        
        # 填充表格
        for row, (upstream, model) in enumerate(model_rows):
            self._table.insertRow(row)
            
            # 上游名称
            name_item = QTableWidgetItem(str(upstream.get("name", "")))
            search_text = f"{upstream.get('name', '')} {group_map.get(upstream.get('group_id'), '')} {model.get('name', '')}".lower()
            name_item.setData(Qt.UserRole, search_text)
            name_item.setData(Qt.UserRole + 2, str(upstream.get("id", "")))
            self._table.setItem(row, 0, name_item)
            
            # 所属分组
            group_item = QTableWidgetItem(group_map.get(upstream.get("group_id"), ""))
            self._table.setItem(row, 1, group_item)
            
            # 模型名
            model_item = QTableWidgetItem(str(model.get("name", "")))
            self._table.setItem(row, 2, model_item)
            
            # 优先级
            priority_item = QTableWidgetItem(str(model.get("priority", 1)))
            self._table.setItem(row, 3, priority_item)
            
            # 上游状态
            health = get_health(str(upstream.get("id", "")), str(model.get("name", "")))
            state = health.get("status", "unknown")
            state_name = {"healthy": "可用", "unhealthy": "不可用", "checking": "检查中"}.get(state, "未检查")
            name_item.setData(Qt.UserRole + 1, state_name)

            # 转发失败冷却中的上游：显示「冷却 Ns」倒计时而不是模糊的可用性
            # key 必须与转发端一致：上游id::模型名（转发时 candidate["model"]=模型名）
            cooldown_key = f"{upstream.get('id')}::{model.get('name', '')}"
            remain = cooldown_snapshot.get(cooldown_key, 0.0) - time.time()
            if remain > 0:
                wrap = _make_status_widget(f"冷却 {int(remain) + 1}s", "statusCooldown")
                label = wrap.findChild(QLabel)
                tip = f"转发失败冷却中，约 {int(remain) + 1} 秒后恢复参与调度"
                if health.get("error"):
                    tip += f" · {health['error']}"
                label.setToolTip(tip)
                self._cooldown_cells[row] = (label, cooldown_snapshot[cooldown_key])
            else:
                wrap = _status_label(health)
            self._table.setCellWidget(row, 4, wrap)
            
            # 操作按钮
            actions = QWidget()
            actions.setObjectName("cellActions")
            box = QHBoxLayout(actions)
            box.setContentsMargins(4, 6, 8, 6)
            box.setSpacing(8)
            
            edit = QPushButton("✎")
            check = QPushButton("↻")
            remove = QPushButton("×")
            uid = upstream.get("id")
            
            edit.setObjectName("darkIconButton")
            check.setObjectName("darkIconButton")
            remove.setObjectName("darkDeleteButton")
            edit.setToolTip("编辑上游")
            check.setToolTip("检查上游")
            remove.setToolTip("删除上游")
            
            for button in (edit, check, remove):
                button.setFixedSize(30, 26)
            
            edit.clicked.connect(lambda _, x=uid: self._edit(x))
            check.clicked.connect(lambda _, x=uid: self._check(x))
            remove.clicked.connect(lambda _, x=uid: self._delete(x))
            
            box.addWidget(edit)
            box.addWidget(check)
            box.addWidget(remove)
            self._table.setCellWidget(row, 5, actions)
            self._table.setRowHeight(row, 56)

        self._apply_filters()
        # 有冷却中的行才开启秒级刷新
        if self._cooldown_cells:
            self._cooldown_timer.start()
        else:
            self._cooldown_timer.stop()
    
    def _on_group_filter_changed(self) -> None:
        self._filter_group_id = self._group_filter.currentData()
        self.reload()
    
    def _show_add_menu(self) -> None:
        cfg = self.cfg.load()
        groups = cfg.get("groups", [])
        if not groups:
            QMessageBox.warning(self, "提示", '请先到"分组管理"创建分组')
            return
        if len(groups) == 1:
            self._add(groups[0].get("id"))
            return
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        for group in groups:
            action = menu.addAction(group.get("name", "未命名"))
            action.triggered.connect(lambda checked=False, gid=group.get("id"): self._add(gid))
        menu.exec(self.sender().mapToGlobal(self.sender().rect().bottomLeft()))

    def _apply_filters(self) -> None:
        query = self._search.text().strip().lower()
        status = self._status_filter.currentText()
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 0)
            if not item:
                continue
            search_text = item.data(Qt.UserRole) or ""
            state_name = item.data(Qt.UserRole + 1) or ""
            match_search = not query or query in search_text
            match_status = status == "全部状态" or status == state_name
            self._table.setRowHidden(row, not (match_search and match_status))

    def _run_check(self, task, callback, reload_after: bool = True):
        """在 Python 后台线程执行检查任务，经 check_done 信号排队回主线程。"""
        if self._busy:
            return
        self._busy = True
        self._pending = (callback, reload_after)

        def runner():
            try:
                result = task() or []
            except Exception as exc:  # noqa: BLE001
                result = [{"status": "unhealthy", "error": str(exc)}]
            self.check_done.emit(result)

        threading.Thread(target=runner, daemon=True).start()

    def _on_check_done(self, results) -> None:
        callback, reload_after = self._pending or (None, True)
        self._pending = None
        self._busy = False
        if reload_after:
            self.reload()
        if callback:
            callback(results)

    def _find(self, uid):
        cfg = self.cfg.load()
        for up in cfg.get("upstreams", []):
            if up.get("id") == uid:
                return cfg, up
        return cfg, None

    def _save_upstream(self, gid, data, replace_id=None):
        cfg = self.cfg.load()
        ups = cfg.setdefault("upstreams", [])
        if replace_id:
            ups[:] = [data if x.get("id") == replace_id else x for x in ups]
        else:
            ups.append(data)
        reorder_group_models(ups, gid, data.get("id"), data.get("models") or [])
        self.cfg.save(cfg)
        self.reload()

    def _add(self, gid):
        d = _UpstreamDialog(gid, self)
        if d.exec() == QDialog.Accepted:
            self._save_upstream(gid, d.result_data())

    def _edit(self, uid):
        cfg, up = self._find(uid)
        if up:
            d = _UpstreamDialog(up.get("group_id", ""), self, up)
            if d.exec() == QDialog.Accepted:
                self._save_upstream(up.get("group_id", ""), d.result_data(), uid)

    def _delete(self, uid):
        cfg, up = self._find(uid)
        if not up:
            return
        box = QMessageBox(self)
        box.setWindowTitle("确认删除")
        box.setText(f'确定删除上游"{up.get("name", "")}"吗？')
        yes = box.addButton("是", QMessageBox.ButtonRole.YesRole)
        box.addButton("否", QMessageBox.ButtonRole.NoRole)
        box.exec()
        if box.clickedButton() is yes:
            cfg["upstreams"] = [x for x in cfg["upstreams"] if x.get("id") != uid]
            self.cfg.save(cfg)
            self.reload()

    def _delete_model(self, uid, name):
        cfg, up = self._find(uid)
        if not up:
            return
        models = [m for m in _model_entries(up) if m.get("name") != name]
        if not models:
            self._delete(uid)
            return
        up["models"] = models
        up.pop("model", None)
        self.cfg.save(cfg)
        self.reload()

    def _check(self, uid):
        cfg, up = self._find(uid)
        if not up or self._busy:
            return
        # 立刻把该上游的所有行切到「检查中 + 转圈」
        self._set_rows_status(uid, "检查中", "statusChecking", spinner=True)
        self._run_check(
            lambda: [check_upstream_now(up, cfg, str(model.get("name"))) for model in _model_entries(up) if model.get("enabled", True)],
            lambda results: self._finish_check(uid, results),
            reload_after=False,
        )

    def _finish_check(self, uid, results) -> None:
        """检查完成：胶囊短暂显示「检查完成」，随后恢复真实状态。"""
        bad = [r for r in results if r.get("status") != "healthy"]
        self._set_rows_status(uid, "检查完成", "statusDone", tooltip="稍后自动显示检查结果")
        window = self.window()
        if window is not None:
            if bad:
                window.statusBar().showMessage(
                    f"检查完成：{len(results)} 个模型，{len(bad)} 个不可用（悬停状态胶囊可查看原因）", 6000
                )
            else:
                window.statusBar().showMessage(f"检查完成：{len(results)} 个模型全部可用", 6000)
        QTimer.singleShot(2000, self.reload)

    def _set_rows_status(self, uid, text: str, object_name: str, spinner: bool = False, tooltip: str = "") -> None:
        for row in self._rows_of_upstream(uid):
            self._table.setCellWidget(
                row, 4, _make_status_widget(text, object_name, tooltip, spinner)
            )

    def _rows_of_upstream(self, uid) -> list[int]:
        rows = []
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 0)
            if item and str(item.data(Qt.UserRole + 2) or "") == str(uid):
                rows.append(row)
        return rows

    def _check_all(self) -> None:
        """一键检查当前视图（分组过滤生效时只检查该分组）内的全部上游。"""
        if self._busy:
            return
        cfg = self.cfg.load()
        ups = [
            up for up in cfg.get("upstreams", [])
            if up.get("enabled", True)
            and (self._filter_group_id is None or up.get("group_id") == self._filter_group_id)
        ]
        if not ups:
            QMessageBox.information(self, "提示", "当前没有可检查的上游")
            return
        for up in ups:
            self._set_rows_status(str(up.get("id", "")), "检查中", "statusChecking", spinner=True)

        def task():
            results = []
            for up in ups:
                for model in _model_entries(up):
                    if model.get("enabled", True):
                        results.append(check_upstream_now(up, cfg, str(model.get("name"))))
            return results

        def callback(results):
            bad = [r for r in results if r.get("status") != "healthy"]
            for up in ups:
                self._set_rows_status(str(up.get("id", "")), "检查完成", "statusDone")
            window = self.window()
            if window is not None:
                if bad:
                    window.statusBar().showMessage(
                        f"检查完成：共 {len(results)} 个模型，{len(bad)} 个不可用（悬停状态胶囊可查看原因）", 8000
                    )
                else:
                    window.statusBar().showMessage(f"检查完成：{len(results)} 个模型全部可用", 8000)
            QTimer.singleShot(2000, self.reload)

        self._run_check(task, callback, reload_after=False)
