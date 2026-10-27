"""单实例：重复启动时唤起已有窗口，而不是再开一个 VoxCall。

注意：Windows 上 QLocalServer 用命名管道，进程被强杀后管道名可能残留，
光靠「能连上」判断会误以为已有实例在跑，导致新进程直接退出（表现为双击没反应）。
所以这里加了应答确认：只有真正收到 ack 才算有实例在跑。
"""

from __future__ import annotations

import logging

from PySide6.QtNetwork import QLocalServer, QLocalSocket

log = logging.getLogger(__name__)

MESSAGE_SHOW = b"show\n"
MESSAGE_ACK = b"ok\n"


class SingleInstance:
    def __init__(self, key: str = "VoxCall-Beta-0.01") -> None:
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
