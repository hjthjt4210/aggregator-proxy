"""开机自启（Windows 注册表 Run 项）。

打包后写入 HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run，
值为 EXE 路径 + --startup 参数（开机后静默驻留托盘，不弹窗口）。
"""
from __future__ import annotations

import sys

APP_NAME = "聚合代理"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def is_supported() -> bool:
    """只有打包成 EXE 后才能可靠自启；源码运行时不提供该开关。"""
    return bool(getattr(sys, "frozen", False))


def _winreg():
    try:
        import winreg  # type: ignore

        return winreg
    except ImportError:
        return None


def command() -> str:
    """开机自启执行的命令：EXE 路径 + --startup。"""
    return f'"{sys.executable}" --startup'


def is_enabled() -> bool:
    winreg = _winreg()
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, APP_NAME)
            return bool(value)
    except OSError:
        return False


def set_enabled(enabled: bool) -> bool:
    winreg = _winreg()
    if winreg is None:
        return False
    try:
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            if enabled:
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, command())
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except OSError:
                    pass
        return True
    except OSError:
        return False
