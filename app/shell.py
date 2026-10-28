"""调用系统外壳打开文件/文件夹/网址。

踩过的坑（都实测过）：
1. ``QDesktopServices.openUrl(QUrl.fromLocalFile(path))`` 会把路径编码成
   ``file:///D:/...``，中文/空格路径下 ShellExecute 直接失败（error 5）。
2. 自己起 ``explorer.exe``（或让 ``ShellExecute`` 起）会继承本进程环境：
   打包目录里的 ``_internal`` 带着 ``ucrtbase.dll`` / ``msvcp140.dll``，
   explorer 加载到就被搞崩 → ``explorer.exe - Application Error 0xc0000142``。
3. ``ShellExecuteW("open", 文件夹)`` 还遵守**文件关联**：如果「文件夹」的默认
   打开方式被改成了某个程序，就会真的去启动那个程序（用户看到的「又开了一次软件」）。

结论：打开文件夹只用 ``SHOpenFolderAndSelectItems`` —— 它把请求交给**已经在运行
的资源管理器**，既不起新进程，也不读文件关联。
"""

from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

log = logging.getLogger(__name__)

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _reveal_in_shell(target: Path) -> bool:
    """让正在运行的资源管理器打开该位置（不新起进程，不读文件关联）。

    对文件夹：``SHOpenFolderAndSelectItems(pidlFolder, 0, NULL, 0)`` 打开的就是
    这个文件夹本身（cidl=0 表示不选中任何子项）。
    对文件：打开它所在的目录并选中它。
    """
    if os.name != "nt":
        return False
    try:
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        ole32 = ctypes.WinDLL("ole32", use_last_error=True)

        ole32.CoInitialize(None)

        pidl = ctypes.c_void_p()
        shell32.SHParseDisplayName.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_ulong,
            ctypes.c_void_p,
        ]
        shell32.SHParseDisplayName.restype = ctypes.c_long
        hr = shell32.SHParseDisplayName(str(target), None, ctypes.byref(pidl), 0, None)
        if hr < 0 or not pidl.value:
            log.debug("SHParseDisplayName 失败：hr=0x%08x", hr & 0xFFFFFFFF)
            return False

        shell32.SHOpenFolderAndSelectItems.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_void_p,
            ctypes.c_ulong,
        ]
        shell32.SHOpenFolderAndSelectItems.restype = ctypes.c_long
        hr2 = shell32.SHOpenFolderAndSelectItems(pidl, 0, None, 0)
        ole32.CoTaskMemFree(pidl)
        if hr2 < 0:
            log.debug("SHOpenFolderAndSelectItems 失败：hr=0x%08x", hr2 & 0xFFFFFFFF)
            return False
        return True
    except Exception:
        log.debug("调用 shell 打开位置失败", exc_info=True)
        return False


def open_path(path: str | os.PathLike) -> bool:
    """打开本地文件夹/文件。

    目录和文件都交给已运行的外壳（SHOpenFolderAndSelectItems），**绝不起新进程**：
    这样既不会踩 explorer 的 0xC0000142，也不会被「文件夹关联被劫持」影响。
    失败返回 False，由调用方给出「显示路径 + 复制路径」的兜底。
    """
    target = Path(path)
    if not target.exists():
        log.warning("路径不存在：%s", target)
        return False

    if os.name == "nt":
        opened = _reveal_in_shell(target)
        log.info("打开路径：%s → %s", target, opened)
        return opened

    try:
        os.startfile(str(target))  # type: ignore[attr-defined]
        return True
    except Exception:
        log.debug("os.startfile 失败", exc_info=True)

    try:
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        subprocess.Popen([opener, str(target)], creationflags=_NO_WINDOW)
        return True
    except Exception:
        log.warning("打开路径失败：%s", target, exc_info=True)
    return False


def open_url(url: str) -> bool:
    try:
        if webbrowser.open(url, new=2):
            return True
    except Exception:
        log.debug("webbrowser 打开失败", exc_info=True)
    try:
        os.startfile(url)  # type: ignore[attr-defined]
        return True
    except Exception:
        log.warning("打开网址失败：%s", url, exc_info=True)
        return False
