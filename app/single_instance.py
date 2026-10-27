"""单实例：重复启动时唤起已有窗口，而不是再开一个 VoxCall。"""

from __future__ import annotations

import logging

from PySide6.QtNetwork import QLocalServer, QLocalSocket

log = logging.getLogger(__name__)

MESSAGE_SHOW = b"show\n"


class SingleInstance:
    def __init__(self, key: str = "VoxCall-Beta-0.01") -> None:
        self.key = key
        self._server: QLocalServer | None = None

    def already_running(self) -> bool:
        """已有实例在跑就通知它显示窗口，并返回 True。"""
        socket = QLocalSocket()
        socket.connectToServer(self.key)
        if not socket.waitForConnected(300):
            return False
        try:
            socket.write(MESSAGE_SHOW)
            socket.flush()
            socket.waitForBytesWritten(300)
            socket.disconnectFromServer()
        except Exception:
            log.debug("通知已有实例失败", exc_info=True)
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
        connection.disconnectFromServer()
