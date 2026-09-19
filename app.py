"""聚合代理 程序入口。

用法：
  python app.py                 正常启动桌面窗口
  python app.py --startup       静默启动：只驻留托盘，不弹窗口（开机自启用）
  python app.py --selftest      打开窗口后 2.5 秒自动关闭并打印 SELFTEST_OK（用于自动验证）

单实例：已有实例运行时再次启动，会唤起已有窗口并立即退出本进程。
"""
from __future__ import annotations

import sys

from PySide6.QtCore import QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

from src.main_window import MainWindow

_SINGLETON_KEY = "aggregator-proxy-singleton"


def _raise_existing_or_listen() -> QLocalServer | None:
    """已有实例在运行：向它发 show 消息并返回 None（本进程应退出）；
    否则返回本实例的监听 server。"""
    probe = QLocalSocket()
    probe.connectToServer(_SINGLETON_KEY)
    if probe.waitForConnected(300):
        probe.write(b"show")
        probe.flush()
        probe.waitForBytesWritten(300)
        probe.disconnectFromServer()
        return None
    # 清理上次异常退出留下的残留监听
    QLocalServer.removeServer(_SINGLETON_KEY)
    server = QLocalServer()
    server.listen(_SINGLETON_KEY)
    return server


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("聚合代理")
    app.setOrganizationName("Aggregator")
    app.setQuitOnLastWindowClosed(False)

    server = None
    if "--selftest" not in sys.argv:
        server = _raise_existing_or_listen()
        if server is None:
            return 0

    window = MainWindow()

    # --startup：开机自启场景，只驻留托盘，不弹主窗口
    if "--startup" not in sys.argv:
        window.show()

    if server is not None:
        def _on_connection() -> None:
            sock = server.nextPendingConnection()
            if sock is not None:
                sock.readyRead.connect(window._show_from_tray)

        server.newConnection.connect(_on_connection)

    if "--selftest" in sys.argv:
        def _close() -> None:
            print("SELFTEST_OK", flush=True)
            window._force_quit = True
            app.quit()

        QTimer.singleShot(2500, _close)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
