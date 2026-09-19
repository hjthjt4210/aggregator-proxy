"""日志设施。

- 写入 <应用目录>/logs/app.log
- 可选地把日志通过 Qt 信号转发到界面（日志页实时显示）。
- 关键约定：**绝不把 API Key 写入日志**（各页面/模块打印时自行规避）。
"""
from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, Signal


class QtLogEmitter(QObject):
    """把日志消息通过 Qt 信号发到 GUI 线程。"""

    message = Signal(str)


class _QtLogHandler(logging.Handler):
    def __init__(self, emitter: QtLogEmitter):
        super().__init__()
        self.emitter = emitter

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.emitter.message.emit(self.format(record))
        except Exception:
            pass


def setup_logging(logs_dir: Path, level: str = "INFO", emitter: QtLogEmitter | None = None) -> logging.Logger:
    """初始化聚合代理的 logger。可重复调用（幂等）。"""
    logs_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("aggregator")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.handlers.clear()
    logger.propagate = False

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")

    file_handler = logging.FileHandler(logs_dir / "app.log", encoding="utf-8")
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    if emitter is not None:
        qt_handler = _QtLogHandler(emitter)
        qt_handler.setFormatter(fmt)
        logger.addHandler(qt_handler)

    return logger
