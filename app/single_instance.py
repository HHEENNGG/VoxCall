"""单实例：重复启动时唤起已有窗口，而不是再开一个 VoxCall。

注意：Windows 上 QLocalServer 用命名管道，进程被强杀后管道名可能残留，
光靠「能连上」判断会误以为已有实例在跑，导致新进程直接退出（表现为双击没反应）。
所以这里加了应答确认：只有真正收到 ack 才算有实例在跑。
"""

from __future__ import annotations

import ctypes
import logging
import os
from ctypes import wintypes

from PySide6.QtNetwork import QLocalServer, QLocalSocket

log = logging.getLogger(__name__)

MESSAGE_SHOW = b"show\n"
MESSAGE_ACK = b"ok\n"

ERROR_ALREADY_EXISTS = 183


class ProcessMutex:
    """进程级互斥（Windows 命名互斥体）。

    在 PyInstaller 启动阶段（几百毫秒到几秒）里 QLocalServer 还没起来，
    只靠管道判断会出现「连点两次打开两个窗口」；互斥体是原子的，没有竞态。
    """

    def __init__(self, name: str = "Local\\VoxCall-Beta") -> None:
        self.already_exists = False
        self._handle = None
        if os.name != "nt":
            return
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, ctypes.c_wchar_p]
            kernel32.CreateMutexW.restype = wintypes.HANDLE
            handle = kernel32.CreateMutexW(None, True, name)
            if not handle:
                return
            self._handle = handle
            self.already_exists = ctypes.get_last_error() == ERROR_ALREADY_EXISTS
        except Exception:
            log.debug("创建互斥体失败", exc_info=True)


class SingleInstance:
    def __init__(self, key: str = "VoxCall-Beta") -> None:
        self.key = key
        self._server: QLocalServer | None = None

    def already_running(self) -> bool:
        """已有实例在跑：通知它显示窗口并返回 True；否则返回 False。

        只认「发消息后收到 ack」这一种情况，避免残留管道导致误判。
        """
        socket = QLocalSocket()
        socket.connectToServer(self.key)
        if not socket.waitForConnected(300):
            return False
        try:
            socket.write(MESSAGE_SHOW)
            socket.flush()
            socket.waitForBytesWritten(300)
            # 真正的实例会回 ack；残留管道/僵尸服务不会
            if not socket.waitForReadyRead(600):
                log.info("单实例管道无应答，按未运行处理（清理残留）")
                socket.disconnectFromServer()
                return False
            socket.disconnectFromServer()
        except Exception:
            log.debug("通知已有实例失败", exc_info=True)
            return False
        return True

    def listen(self, on_show) -> bool:
        QLocalServer.removeServer(self.key)  # 清理上次异常退出留下的名字
        server = QLocalServer()
        server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        if not server.listen(self.key):
            log.warning("单实例监听失败：%s", server.errorString())
            return False
        self._server = server

        def handle() -> None:
            while server.hasPendingConnections():
                conn = server.nextPendingConnection()
                if conn is None:
                    break
                conn.readyRead.connect(lambda c=conn: self._on_message(c, on_show))
                conn.disconnected.connect(conn.deleteLater)
                if conn.bytesAvailable():
                    self._on_message(conn, on_show)

        server.newConnection.connect(handle)
        return True

    @staticmethod
    def _on_message(connection, on_show) -> None:
        data = bytes(connection.readAll())
        if MESSAGE_SHOW in data:
            on_show()
        try:
            connection.write(MESSAGE_ACK)
            connection.flush()
            connection.waitForBytesWritten(200)
        except Exception:
            log.debug("回 ack 失败", exc_info=True)
        connection.disconnectFromServer()
