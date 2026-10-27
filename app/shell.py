"""调用系统外壳打开文件/文件夹/网址（尽量不依赖 Qt 的 URL 编码）。"""

from __future__ import annotations

import logging
import os
import subprocess
import webbrowser
from pathlib import Path

log = logging.getLogger(__name__)

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def open_path(path: str | os.PathLike) -> bool:
    """用资源管理器/默认程序打开本地文件或文件夹。"""
    target = Path(path)
    # 优先 os.startfile：直接把原生路径交给 ShellExecute，避开 file:// 的编码问题
    try:
        os.startfile(str(target))  # type: ignore[attr-defined]
        return True
    except Exception:
        log.debug("os.startfile 失败，改用 explorer", exc_info=True)
    try:
        subprocess.Popen(["explorer", str(target)], creationflags=_NO_WINDOW)
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
