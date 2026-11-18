"""单实例：重复启动时唤起已有窗口，而不是再开一个 VoxCall。

注意：Windows 上 QLocalServer 用命名管道，进程被强杀后管道名可能残留，
光靠「能连上」判断会误以为已有实例在跑，导致新进程直接退出（表现为双击没反应）。
所以这里加了应答确认：只有真正收到 ack 才算有实例在跑。

PySide6 只在用到 QLocalServer/Socket 时才导入——这样进程互斥（纯 ctypes）
在没有 Qt 的环境（例如只跑单元测试的 CI）里也能用。
"""

from __future__ import annotations

import ctypes
import logging
import os
import time
from ctypes import wintypes
from typing import Any

log = logging.getLogger(__name__)

MESSAGE_SHOW = b"show\n"
MESSAGE_ACK = b"ok\n"

ERROR_ALREADY_EXISTS = 183
WAIT_OBJECT_0 = 0
WAIT_ABANDONED = 0x80
WAIT_TIMEOUT = 0x102


def _qt_network():
    """延迟导入，避免无 Qt 环境 import 本模块就失败。"""
    from PySide6.QtNetwork import QLocalServer, QLocalSocket

    return QLocalServer, QLocalSocket


class ProcessMutex:
    """进程级互斥（Windows 命名互斥体）。

    在 PyInstaller 启动阶段（几百毫秒到几秒）里 QLocalServer 还没起来，
    只靠管道判断会出现「连点两次打开两个窗口」；互斥体是原子的，没有竞态。
    """

    def __init__(self, name: str = "Local\\VoxCall-Beta") -> None:
        self.already_exists = False
        self._handle = None
        self._kernel32 = None
        self._owned = False
        if os.name != "nt":
            return
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, ctypes.c_wchar_p]
            kernel32.CreateMutexW.restype = wintypes.HANDLE
            kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel32.WaitForSingleObject.restype = wintypes.DWORD
            kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
            kernel32.ReleaseMutex.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            handle = kernel32.CreateMutexW(None, True, name)
            if not handle:
                raise ctypes.WinError(ctypes.get_last_error())
            existed = ctypes.get_last_error() == ERROR_ALREADY_EXISTS
            self._handle = handle
            self._kernel32 = kernel32
            self._owned = not existed
            # An existing kernel object may have no live owner: another launch
            # can keep its handle alive even after the original process dies.
            self.already_exists = not self.try_acquire()
        except Exception:
            log.warning("Could not create the process mutex", exc_info=True)
            self.close()

    def try_acquire(self) -> bool:
        """Atomically claim a released or abandoned mutex, never a live owner's."""
        if self._owned:
            return True
        if self._handle is None:
            return not self.already_exists
        result = self._kernel32.WaitForSingleObject(self._handle, 0)
        if result in (WAIT_OBJECT_0, WAIT_ABANDONED):
            self._owned = True
            self.already_exists = False
            return True
        if result != WAIT_TIMEOUT:
            log.warning("Could not wait for the process mutex: %s", result)
        self.already_exists = True
        return False

    def close(self) -> None:
        """Release ownership and the handle, including on GUI startup errors."""
        if self._handle is not None:
            if self._owned:
                self._kernel32.ReleaseMutex(self._handle)
            self._kernel32.CloseHandle(self._handle)
            self._handle = None
            self._owned = False

    def __del__(self) -> None:
        self.close()


class SingleInstance:
    def __init__(self, key: str = "VoxCall-Beta") -> None:
        self.key = key
        self._server: Any = None

    def already_running(self) -> bool:
        """已有实例在跑：通知它显示窗口并返回 True；否则返回 False。

        只认「发消息后收到 ack」这一种情况，避免残留管道导致误判。
        """
        try:
            _QLocalServer, QLocalSocket = _qt_network()
        except Exception:
            log.debug("没有 Qt 环境，跳过单实例检查")
            return False
        socket = QLocalSocket()
        try:
            socket.connectToServer(self.key)
            if not socket.waitForConnected(300):
                return False
            socket.write(MESSAGE_SHOW)
            socket.flush()
            socket.waitForBytesWritten(300)
            deadline = time.monotonic() + 0.6
            reply = bytearray()
            while time.monotonic() < deadline:
                if not socket.bytesAvailable():
                    remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
                    if not socket.waitForReadyRead(remaining_ms):
                        return False
                reply.extend(bytes(socket.readAll()))
                if b"\n" in reply or len(reply) > 64:
                    return bytes(reply) == MESSAGE_ACK
            return False
        except Exception:
            log.debug("Could not notify the existing instance", exc_info=True)
            return False
        finally:
            socket.abort()

    def close(self) -> None:
        if self._server is not None:
            self._server.close()
            self._server = None

    def listen(self, on_show) -> bool:
        QLocalServer, _QLocalSocket = _qt_network()
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
