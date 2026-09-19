"""分组管理页（阶段二：接入新增/编辑/删除）。

分组字段：
  id / name / api_key / mode(fixed=固定 | manual=手动) / external_models(list)
"""
from __future__ import annotations

import logging
import secrets
import uuid

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..config_manager import ConfigManager

logger = logging.getLogger("aggregator")


def _gen_api_key() -> str:
    return secrets.token_hex(16)


def _mask_key(value: str) -> str:
    text = str(value or "")
    if len(text) <= 8:
        return text
    return f"{text[:4]}••••{text[-4:]}"


class _GroupDialog(QDialog):
    def __init__(self, parent=None, data: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle("编辑分组" if data else "新增分组")
        self._data = data or {}
        self.setMinimumWidth(420)
        self.resize(460, 280)

        form = QFormLayout(self)
        form.setContentsMargins(18, 16, 18, 16)
        form.setSpacing(10)

        self._name = QLineEdit()
        self._name.setText(str(self._data.get("name", "")))
        form.addRow("分组名称", self._name)

        self._api_key = QLineEdit()
        self._api_key.setText(str(self._data.get("api_key", "")))
        self._api_key.setPlaceholderText("留空则自动生成")
        form.addRow("聚合密钥", self._api_key)

        self._mode = QComboBox()
        self._mode.addItem("固定", "fixed")
        self._mode.addItem("手动", "manual")
        cur_mode = self._data.get("mode", "fixed")
        idx = self._mode.findData(cur_mode)
        self._mode.setCurrentIndex(idx if idx >= 0 else 0)
        form.addRow("模式", self._mode)

        self._ext_models = QLineEdit()
        ext = self._data.get("external_models") or []
        if isinstance(ext, list):
            self._ext_models.setText(", ".join(str(x) for x in ext))
        else:
            self._ext_models.setText(str(ext))
        self._ext_models.setPlaceholderText("固定模式只填一个对外模型名，如 grok4.6")
        form.addRow("对外模型名", self._ext_models)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("确定")
        btns.button(QDialogButtonBox.Cancel).setText("取消")
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        form.addRow(btns)

    def _accept(self) -> None:
        if not self._name.text().strip():
            QMessageBox.warning(self, "提示", "分组名称不能为空")
            return
        self.accept()

    def result_data(self) -> dict:
        name = self._name.text().strip()
        api_key = self._api_key.text().strip() or _gen_api_key()
        mode = self._mode.currentData()
        ext = [x.strip() for x in self._ext_models.text().split(",") if x.strip()]
        data = {
            "id": self._data.get("id") or uuid.uuid4().hex,
            "name": name,
            "api_key": api_key,
            "mode": mode,
            "external_models": ext,
        }
        return data


class GroupsPage(QWidget):
    def __init__(self, config_manager: ConfigManager):
        super().__init__()
        self.cfg = config_manager
        self._build()
        self._copy_timer = QTimer(self)
        self._copy_timer.setSingleShot(True)
        self._copy_timer.timeout.connect(lambda: self._copy_status.setText(""))
        self.reload()

    def _build(self) -> None:
        self.setAttribute(Qt.WA_StyledBackground, True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 22, 28, 22)
        outer.setSpacing(12)

        title = QLabel("分组管理")
        title.setObjectName("pageTitle")
        outer.addWidget(title)

        subtitle = QLabel("管理对外聚合密钥与路由模式")
        subtitle.setObjectName("pageSubtitle")
        outer.addWidget(subtitle)

        toolbar = QHBoxLayout()
        self._btn_add = QPushButton("＋ 新增分组")
        self._btn_add.setObjectName("primaryBtn")
        self._btn_add.clicked.connect(self._on_add)
        toolbar.addWidget(self._btn_add)
        self._copy_status = QLabel("")
        self._copy_status.setObjectName("statusOk")
        self._copy_status.setMinimumWidth(80)
        toolbar.addWidget(self._copy_status)
        toolbar.addStretch(1)
        outer.addLayout(toolbar)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["分组名称", "聚合密钥", "模式", "对外模型名", "操作"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Fixed)
        self.table.setColumnWidth(4, 96)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setVisible(False)
        self.table.cellClicked.connect(self._on_cell_clicked)
        outer.addWidget(self.table, 1)

    # ------------------------------------------------------------------ 读写
    def reload(self) -> None:
        groups = self.cfg.load().get("groups", [])
        self.table.setRowCount(0)
        for group in groups:
            row = self.table.rowCount()
            self.table.insertRow(row)
            mode = "固定" if group.get("mode", "fixed") == "fixed" else "手动"
            ext = group.get("external_models") or []
            ext_text = ", ".join(str(x) for x in ext) if isinstance(ext, list) else str(ext)
            key = str(group.get("api_key", ""))
            cells = [
                str(group.get("name", "")),
                _mask_key(key),
                mode,
                ext_text,
            ]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col == 0:
                    item.setData(Qt.UserRole, group.get("id"))
                if col == 1:
                    item.setData(Qt.UserRole, key)
                    item.setToolTip("点击复制完整密钥")
                self.table.setItem(row, col, item)
            actions = QWidget()
            actions.setObjectName("cellActions")
            action_layout = QHBoxLayout(actions)
            action_layout.setContentsMargins(4, 6, 8, 6)
            action_layout.setSpacing(6)
            edit_btn = QPushButton("✎")
            delete_btn = QPushButton("×")
            edit_btn.setObjectName("darkIconButton")
            delete_btn.setObjectName("darkDeleteButton")
            edit_btn.setToolTip("编辑分组")
            delete_btn.setToolTip("删除分组")
            for button in (edit_btn, delete_btn):
                button.setFixedSize(30, 26)
            edit_btn.clicked.connect(lambda _=False, gid=group.get("id"): self._on_edit(gid))
            delete_btn.clicked.connect(lambda _=False, gid=group.get("id"): self._on_delete(gid))
            action_layout.addWidget(edit_btn)
            action_layout.addWidget(delete_btn)
            self.table.setCellWidget(row, 4, actions)
            self.table.setRowHeight(row, 46)
        if not groups:
            self.table.setRowCount(1)
            self.table.setCellWidget(0, 0, self._make_empty_state())
            self.table.setSpan(0, 0, 1, 5)

    @staticmethod
    def _make_empty_state() -> QWidget:
        """空状态：提示文案 + 「新增分组」引导按钮。"""
        holder = QWidget()
        holder.setObjectName("emptyState")
        box = QVBoxLayout(holder)
        box.setContentsMargins(20, 12, 20, 12)
        box.setSpacing(14)
        box.addStretch(1)
        label = QLabel("还没有分组，先创建一个分组开始使用")
        label.setAlignment(Qt.AlignCenter)
        label.setStyleSheet("border: none; background: transparent; color: #8b96ad; font-size: 13px;")
        box.addWidget(label)
        button = QPushButton("＋ 新增分组")
        button.setObjectName("primaryBtn")
        button.setFixedWidth(140)
        button.clicked.connect(lambda: GroupsPage._trigger_add(holder))
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(button)
        row.addStretch(1)
        box.addLayout(row)
        box.addStretch(1)
        return holder

    @staticmethod
    def _trigger_add(holder: QWidget) -> None:
        page = holder.parent()
        while page is not None and not isinstance(page, GroupsPage):
            page = page.parent()
        if isinstance(page, GroupsPage):
            page._on_add()

    def _on_cell_clicked(self, row: int, col: int) -> None:
        if col != 1:
            return
        item = self.table.item(row, 1)
        key = item.data(Qt.UserRole) if item else ""
        if not key:
            return
        QGuiApplication.clipboard().setText(str(key))
        self._copy_status.setText("✓ 已复制")
        self._copy_timer.start(2000)
        window = self.window()
        if window:
            window.statusBar().showMessage("聚合密钥已复制", 1800)

    def _selected_group_id(self) -> str | None:
        items = self.table.selectedItems()
        if not items:
            return None
        row = items[0].row()
        first = self.table.item(row, 0)
        return first.data(Qt.UserRole) if first else None

    def _get_groups(self) -> list[dict]:
        cfg = self.cfg.load()
        return cfg.setdefault("groups", [])

    def _save_groups(self, groups: list[dict]) -> None:
        cfg = self.cfg.load()
        cfg["groups"] = groups
        self.cfg.save(cfg)
        self.reload()

    # ------------------------------------------------------------------ 操作
    def _on_add(self) -> None:
        dlg = _GroupDialog(self)
        if dlg.exec() == QDialog.Accepted:
            groups = self._get_groups()
            groups.append(dlg.result_data())
            self._save_groups(groups)
            self._notify()

    def _on_edit(self, gid: str | None = None) -> None:
        if not gid:
            return
        data = next((g for g in self._get_groups() if g.get("id") == gid), None)
        if data is None:
            return
        dlg = _GroupDialog(self, data=data)
        if dlg.exec() == QDialog.Accepted:
            groups = self._get_groups()
            for i, g in enumerate(groups):
                if g.get("id") == gid:
                    groups[i] = dlg.result_data()
                    break
            self._save_groups(groups)
            self._notify()

    def _on_delete(self, gid: str | None = None) -> None:
        if not gid:
            return
        cfg = self.cfg.load()
        group = next((g for g in cfg.get("groups", []) if g.get("id") == gid), None)
        if group is None:
            return
        upstream_count = sum(1 for u in cfg.get("upstreams", []) if u.get("group_id") == gid)
        message = f"确定删除分组“{group.get('name', '')}”吗？"
        if upstream_count:
            message += f"\n该操作也会删除其下的 {upstream_count} 个上游。"
        box = QMessageBox(self)
        box.setWindowTitle("确认删除")
        box.setText(message)
        yes = box.addButton("是", QMessageBox.ButtonRole.YesRole)
        box.addButton("否", QMessageBox.ButtonRole.NoRole)
        box.exec()
        if box.clickedButton() is not yes:
            return
        cfg["groups"] = [g for g in cfg.get("groups", []) if g.get("id") != gid]
        cfg["upstreams"] = [u for u in cfg.get("upstreams", []) if u.get("group_id") != gid]
        self.cfg.save(cfg)
        self.reload()
        self._notify()

    def _notify(self) -> None:
        if logger:
            logger.info("分组列表已变更")
