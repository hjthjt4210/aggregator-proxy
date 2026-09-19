"""JSON 配置管理。

- 配置文件位于 <应用目录>/data/config.json
- 保存时：先备份旧文件为 config.json.bak，再用临时文件 + os.replace 原子替换，避免写坏。
- 损坏时：把坏文件复制为 config.json.corrupt-<时间戳>，尝试从 .bak 恢复，否则回退默认配置。
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import sys
import time
from pathlib import Path

# 默认配置（后续阶段会扩展上游/分组等字段）
DEFAULT_CONFIG = {
    "server": {
        # 默认只绑本机：这是个会替你花上游额度的服务，暴露到局域网必须是用户显式选择。
        "host": "127.0.0.1",
        "port": 5000,
        # 转发默认超时（秒）；上游未单独配置 timeout 时使用
        "default_timeout": 20,
        # 某个上游失败后的冷却时长（秒），冷却期内不再被选中
        "cooldown_seconds": 120,
    },
    "groups": [],
    "upstreams": [],
    "app": {
        "log_level": "INFO",
        "close_to_tray": True,
    },
}


def get_app_dir() -> Path:
    """返回应用目录：打包后是 EXE 所在目录，源码运行时是项目根目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


class ConfigManager:
    """负责配置文件 .json 的读写、备份、校验与恢复。"""

    def __init__(self, base_dir: Path | str | None = None):
        self.base_dir = Path(base_dir) if base_dir else get_app_dir()
        self.data_dir = self.base_dir / "data"
        self.logs_dir = self.base_dir / "logs"
        self.config_path = self.data_dir / "config.json"
        self.bak_path = self.data_dir / "config.json.bak"
        self._config: dict | None = None
        self.last_warning: str | None = None

    # ------------------------------------------------------------------ 目录
    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ 读取
    def load(self) -> dict:
        """加载配置。返回校验后的配置字典。"""
        self.ensure_dirs()

        if not self.config_path.exists():
            self._config = copy.deepcopy(DEFAULT_CONFIG)
            self.last_warning = "未找到配置文件，已创建默认配置。"
            self.save(self._config)
            return self._config

        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                self._config = json.load(f)
            if not isinstance(self._config, dict):
                raise ValueError("配置根节点不是对象")
            self._config = self._merge_defaults(self._config)
            return self._config
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            self._recover_from_corruption(exc)
            return self._config

    def get(self, key: str, default=None):
        """读取顶层配置键。"""
        if self._config is None:
            self.load()
        return self._config.get(key, default)

    # ------------------------------------------------------------------ 保存
    def save(self, config: dict) -> None:
        """原子保存配置：备份旧文件 -> 写临时文件 -> os.replace 原子替换。"""
        self.ensure_dirs()

        # 1. 备份现有文件
        if self.config_path.exists():
            try:
                shutil.copy2(self.config_path, self.bak_path)
            except OSError:
                pass

        # 2. 写临时文件，再原子替换
        tmp_path = self.data_dir / ".config.json.tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self.config_path)
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

        self._config = config

    # ------------------------------------------------------------------ 恢复
    def _recover_from_corruption(self, exc: Exception) -> None:
        self.ensure_dirs()
        # 1. 把损坏文件挪到一边（改名为 corrupt-时间戳），避免后续 save 覆盖有效备份
        corrupt_path = self.data_dir / f"config.json.corrupt-{int(time.time())}"
        try:
            self.config_path.rename(corrupt_path)
        except OSError:
            try:
                shutil.copy2(self.config_path, corrupt_path)
                self.config_path.unlink()
            except OSError:
                pass

        # 2. 尝试从备份恢复
        recovered = None
        if self.bak_path.exists():
            try:
                with open(self.bak_path, "r", encoding="utf-8") as f:
                    candidate = json.load(f)
                if isinstance(candidate, dict):
                    recovered = candidate
            except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
                recovered = None

        # 3. 回退默认配置或使用恢复结果
        self._config = self._merge_defaults(recovered) if recovered is not None else copy.deepcopy(DEFAULT_CONFIG)
        self.last_warning = (
            f"配置文件损坏（{exc}），损坏文件已备份为 {corrupt_path.name}，"
            + ("已从备份恢复。" if recovered is not None else "已使用默认配置。")
        )
        # 当前 config_path 已被挪走，save 不会再次备份坏文件
        self.save(self._config)

    # ------------------------------------------------------------------ 校验
    @staticmethod
    def _merge_defaults(config: dict) -> dict:
        """把缺失的顶层结构补成全量默认字段，避免页面积 .get 报 KeyError。"""
        merged = copy.deepcopy(DEFAULT_CONFIG)
        for key, value in config.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key].update(value)
            else:
                merged[key] = value
        return merged

    def export_config(self, target_path: str) -> None:
        """导出当前配置到指定路径"""
        import shutil
        shutil.copy2(self.config_path, target_path)

    def import_config(self, source_path: str) -> None:
        """从指定路径导入配置并覆盖当前配置"""
        import shutil
        shutil.copy2(source_path, self.config_path)
