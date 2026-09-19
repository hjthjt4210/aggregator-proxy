"""设置页：编辑服务监听地址、端口、日志级别等（写入 config.json）。"""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import autostart
from ..config_manager import ConfigManager
from ..ui_widgets import NumberEdit, ToggleSwitch

logger = logging.getLogger("aggregator")


class SettingsPage(QWidget):
    def __init__(self, config_manager: ConfigManager):
        super().__init__()
        self.cfg = config_manager
        self._build()
        self.load_values()

    def _build(self) -> None:
        self.setAttribute(Qt.WA_StyledBackground, True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 22, 28, 22)
        outer.setSpacing(12)

        title = QLabel("设置")
        title.setObjectName("pageTitle")
        outer.addWidget(title)

        subtitle = QLabel("服务监听、日志与托盘行为")
        subtitle.setObjectName("pageSubtitle")
        outer.addWidget(subtitle)

        form_holder = QHBoxLayout()
        form = QFormLayout()
        form.setSpacing(12)
        form.setContentsMargins(0, 4, 0, 4)

        self.host_edit = QLineEdit()
        self.host_edit.setPlaceholderText("127.0.0.1")
        self.host_edit.setFixedWidth(340)
        form.addRow("服务监听地址", self.host_edit)

        self._host_hint = QLabel(
            "默认 127.0.0.1，只有本机能调用。填 0.0.0.0 会开放给整个局域网——"
            "拿到聚合密钥的人可直接消耗你的上游额度，请只在可信网络下这样设置。"
        )
        self._host_hint.setObjectName("hint")
        self._host_hint.setWordWrap(True)
        self._host_hint.setMaximumWidth(340)
        form.addRow("", self._host_hint)

        self.port_spin = NumberEdit(5000)
        self.port_spin.setPlaceholderText("1 至 65535")
        self.port_spin.setFixedWidth(340)
        form.addRow("端口", self.port_spin)

        self.level_combo = QComboBox()
        self.level_combo.addItems(["调试", "普通", "警告", "错误"])
        self.level_combo.setFixedWidth(340)
        form.addRow("日志级别", self.level_combo)

        self.tray_check = ToggleSwitch(False)
        self.tray_hint = QLabel("关闭窗口时最小化到托盘")
        tray_row = QHBoxLayout(); tray_row.addWidget(self.tray_check); tray_row.addWidget(self.tray_hint); tray_row.addStretch(1)
        form.addRow("托盘行为", tray_row)

        self.auto_check = ToggleSwitch(False)
        self.auto_check.toggled.connect(self._on_autostart_toggled)
        self.auto_hint = QLabel("开机后自动在托盘后台运行")
        auto_row = QHBoxLayout(); auto_row.addWidget(self.auto_check); auto_row.addWidget(self.auto_hint); auto_row.addStretch(1)
        form.addRow("开机自启", auto_row)
        form_holder.addLayout(form)
        form_holder.addStretch(1)
        outer.addLayout(form_holder)

        self._config_path = QLabel("")
        self._config_path.setObjectName("hint")
        outer.addWidget(self._config_path)

        self._version = QLabel("版本：v1.2.0")
        self._version.setObjectName("hint")
        outer.addWidget(self._version)

        import_export = QHBoxLayout()
        self._export_btn = QPushButton("导出配置")
        self._export_btn.clicked.connect(self.export_config)
        self._import_btn = QPushButton("导入配置")
        self._import_btn.clicked.connect(self.import_config)
        import_export.addWidget(self._export_btn)
        import_export.addWidget(self._import_btn)
        import_export.addStretch(1)
        outer.addLayout(import_export)

        tools = QHBoxLayout()
        self._save_btn = QPushButton("保存配置")
        self._save_btn.setObjectName("primaryBtn")
        self._save_btn.clicked.connect(self.save)
        self._status = QLabel("")
        tools.addWidget(self._save_btn)
        tools.addWidget(self._status)
        tools.addStretch(1)
        outer.addLayout(tools)
        outer.addStretch(1)

    def load_values(self) -> None:
        cfg = self.cfg.load()
        server = cfg.get("server", {})
        self.host_edit.setText(str(server.get("host", "127.0.0.1")))
        self.port_spin.setText(str(int(server.get("port", 5000))))
        app = cfg.get("app", {})
        level = str(app.get("log_level", "INFO"))
        level_map = {"DEBUG": "调试", "INFO": "普通", "WARNING": "警告", "ERROR": "错误"}
        idx = self.level_combo.findText(level_map.get(level, "普通"))
        if idx >= 0:
            self.level_combo.setCurrentIndex(idx)
        self.tray_check.setChecked(bool(app.get("close_to_tray", False)))
        self._config_path.setText(f"配置文件：{self.cfg.config_path}")

        self.auto_check.setChecked(autostart.is_enabled())
        if not autostart.is_supported():
            self.auto_check.setEnabled(False)
            self.auto_hint.setText("开机自启仅发布版（EXE）支持")

    def _on_autostart_toggled(self, enabled: bool) -> None:
        if not autostart.is_supported():
            self.auto_check.setChecked(False)
            self._flash_status("✗ 仅发布版支持开机自启", False)
            return
        if autostart.set_enabled(enabled):
            self._flash_status("✓ 已开启开机自启" if enabled else "已关闭开机自启", True)
            logger.info("开机自启已设为 %s", enabled)
        else:
            self.auto_check.setChecked(not enabled)
            self._flash_status("✗ 设置开机自启失败", False)

    def _flash_status(self, text: str, ok: bool) -> None:
        self._status.setText(text)
        self._status.setObjectName("statusOk" if ok else "statusErr")
        self._status.style().unpolish(self._status)
        self._status.style().polish(self._status)

    def save(self) -> None:
        cfg = self.cfg.load()
        cfg.setdefault("server", {})
        old_host = str(cfg["server"].get("host", "127.0.0.1"))
        old_port = int(cfg["server"].get("port", 5000))
        new_host = self.host_edit.text().strip() or "127.0.0.1"
        cfg["server"]["host"] = new_host
        new_port = max(1, min(65535, self.port_spin.number(5000)))
        cfg["server"]["port"] = new_port
        cfg.setdefault("app", {})
        level_map = {"调试": "DEBUG", "普通": "INFO", "警告": "WARNING", "错误": "ERROR"}
        cfg["app"]["log_level"] = level_map.get(self.level_combo.currentText(), "INFO")
        cfg["app"]["close_to_tray"] = self.tray_check.isChecked()
        self.cfg.save(cfg)
        logger.info("配置已保存到 %s", self.cfg.config_path)
        if new_port != old_port or new_host != old_host:
            self._flash_status("已保存 ✓  监听地址/端口已改，点顶栏「重启服务」生效", True)
        else:
            self._flash_status("已保存 ✓", True)

    def export_config(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(
            self, "导出配置", "config_backup.json", "JSON 文件 (*.json)"
        )
        if path:
            try:
                self.cfg.export_config(path)
                self._flash_status("✓ 配置已导出", True)
                logger.info("配置已导出到 %s", path)
            except Exception as exc:
                self._flash_status(f"✗ 导出失败：{exc}", False)

    def import_config(self) -> None:
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        path, _ = QFileDialog.getOpenFileName(
            self, "导入配置", "", "JSON 文件 (*.json)"
        )
        if path:
            reply = QMessageBox.question(
                self, "确认导入", "导入配置将覆盖当前设置，是否继续？",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply == QMessageBox.Yes:
                try:
                    self.cfg.import_config(path)
                    self.load_values()
                    self._flash_status("✓ 配置已导入", True)
                    logger.info("配置已从 %s 导入", path)
                except Exception as exc:
                    self._flash_status(f"✗ 导入失败：{exc}", False)
