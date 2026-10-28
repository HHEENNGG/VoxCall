"""调用系统外壳打开文件/文件夹/网址。

三个坑，都是实测踩出来的：

1. Qt 的 ``QDesktopServices.openUrl(QUrl.fromLocalFile(path))`` 会把路径编码成
   ``file:///D:/...``，中文/空格路径下 ShellExecute 直接失败（error 5）。
2. 从打包后的进程里启动 ``explorer.exe``，它可能加载到 ``_internal`` 自带的
   ``ucrtbase.dll`` / ``msvcp140.dll`` → ``explorer.exe - Application Error 0xc0000142``。
3. 所以优先用 ``SHOpenFolderAndSelectItems``：它把请求交给**已经在运行的资源管理器**
   （不新起进程），完全不受本进程环境/DLL 目录影响。

调用顺序：SHOpenFolderAndSelectItems → 净化环境启动 explorer → os.startfile。
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


def _system_root() -> str:
    return os.environ.get("SystemRoot") or r"C:\Windows"


def _clean_env() -> dict[str, str]:
    """给 explorer 用的干净环境：只留系统 PATH，剔掉打包器/运行时的目录。"""
    env = {
        key: value
        for key, value in os.environ.items()
        if key.upper() not in {"PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "QT_PLUGIN_PATH"}
    }
    root = _system_root()
    system_path = os.pathsep.join(
        [
            os.path.join(root, "system32"),
            root,
            os.path.join(root, "System32", "Wbem"),
            os.path.join(root, "System32", "WindowsPowerShell", "v1.0"),
        ]
    )

    marked = ("_internal", "site-packages", "voxcall", "runtime")
    keep: list[str] = []
    for part in (os.environ.get("PATH") or "").split(os.pathsep):
        part = part.strip()
        if not part:
            continue
        lowered = part.lower().replace("/", "\\")
        if any(token in lowered for token in marked):
            continue
        keep.append(part)
    env["PATH"] = system_path + (os.pathsep + os.pathsep.join(keep) if keep else "")
    return env


def _open_folder_in_shell(target: Path) -> bool:
    """打开文件夹本身（显示其内容），交给 shell 处理，不自己拼命令行。

    注意：``SHOpenFolderAndSelectItems`` 的语义是「打开所在目录并选中该项」，
    对文件夹用它会打开**上一级**，所以这里必须用 ShellExecute「open」。
    """
    if os.name != "nt":
        return False
    try:
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        shell32.ShellExecuteW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_int,
        ]
        shell32.ShellExecuteW.restype = ctypes.c_void_p
        result = shell32.ShellExecuteW(None, "open", str(target), None, None, 1)
        code = int(result or 0)
        if code <= 32:
            log.debug("ShellExecuteW 打开文件夹失败：%d", code)
            return False
        return True
    except Exception:
        log.debug("打开文件夹失败", exc_info=True)
        return False


def _reveal_in_shell(target: Path) -> bool:
    """让正在运行的资源管理器打开该位置（不新起 explorer 进程）。"""
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


def _launch_explorer(target: Path) -> bool:
    """兜底：用净化后的环境 + 系统目录 cwd 启动 explorer.exe。

    启动后短暂等一会：正常打开文件夹的 explorer 会一直活着；
    如果它立刻以非 0 退出（典型是 0xC0000142 启动失败），就当作失败，
    让调用方走「显示路径 + 复制」的兜底，而不是把系统错误框甩给用户。
    """
    explorer = Path(_system_root()) / "explorer.exe"
    exe = str(explorer) if explorer.exists() else "explorer.exe"
    try:
        proc = subprocess.Popen(
            [exe, str(target)],
            env=_clean_env(),
            cwd=_system_root(),
            creationflags=_NO_WINDOW,
            close_fds=True,
        )
    except Exception:
        log.debug("explorer 打开失败：%s", target, exc_info=True)
        return False

    for _ in range(12):
        time.sleep(0.1)
        code = proc.poll()
        if code is None:
            return True
        if code != 0:
            log.warning("explorer 启动失败（退出码 0x%08X）", code & 0xFFFFFFFF)
            return False
    return True


def open_path(path: str | os.PathLike) -> bool:
    """打开本地文件夹/文件。

    - 文件夹：``ShellExecute`` 的 open 动作 → 打开文件夹**本身**（不是它的上一级），
      由 shell 处理，不自己拼 explorer 命令行。
    - 文件：用 ``SHOpenFolderAndSelectItems`` 打开所在目录并选中该文件。
    任一失败返回 False，由调用方给出「显示路径 + 复制」的兜底。
    """
    target = Path(path)
    if not target.exists():
        log.warning("路径不存在：%s", target)
        return False

    if os.name == "nt":
        return _open_folder_in_shell(target) if target.is_dir() else _reveal_in_shell(target)

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
