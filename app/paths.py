"""可写目录解析：程序目录 → 上一级目录 → 用户目录 → 临时目录。

装在只读位置（Program Files）或被 Windows「受控文件夹访问」/受限运行时限制时，
自动退到下一个可写位置，并把每个位置的失败原因带出来，方便排错。
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)


def app_root() -> Path:
    """程序数据目录：打包成 exe 后是 exe 所在目录，源码运行时是项目目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def bundle_dir() -> Path:
    """PyInstaller 解包目录（源码运行时就等于程序目录）。"""
    return Path(getattr(sys, "_MEIPASS", app_root()))


ROOT = app_root()


def assets_dir() -> Path:
    """图标等随包资源：优先 exe 同级 assets，其次打包解包目录，最后源码目录。"""
    for base in (ROOT, bundle_dir(), Path(__file__).resolve().parents[1]):
        candidate = base / "assets"
        if candidate.exists():
            return candidate
    return bundle_dir() / "assets"


def _env_dir(name: str) -> Path | None:
    value = os.environ.get(name)
    return Path(value) if value else None


def candidates() -> list[Path]:
    """候选状态目录（按优先级去重）。"""
    override = _env_dir("VOXCALL_STATE_DIR")
    if override:
        return [override]

    dirs: list[Path] = [ROOT]
    try:
        dirs.append(ROOT.parent)  # 程序放在受限制的子目录里时，上一级通常还可用
    except Exception:  # pragma: no cover
        pass
    local = _env_dir("LOCALAPPDATA")
    if local:
        dirs.append(local / "VoxCall")
    roaming = _env_dir("APPDATA")
    if roaming:
        dirs.append(roaming / "VoxCall")
    try:
        dirs.append(Path.home() / ".voxcall")
    except Exception:  # pragma: no cover
        pass
    try:
        dirs.append(Path(tempfile.gettempdir()) / "VoxCall")
    except Exception:  # pragma: no cover
        pass

    unique: list[Path] = []
    seen: set[str] = set()
    for directory in dirs:
        key = str(directory).lower()
        if key not in seen:
            seen.add(key)
            unique.append(directory)
    return unique


def probe(directory: Path) -> tuple[bool, str]:
    """真正试着建目录并写一个探针文件，返回 (是否可写, 失败原因)。"""
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe_file = directory / ".write-probe"
        probe_file.write_text("x", encoding="utf-8")
        probe_file.unlink()
        return True, ""
    except OSError as exc:
        reason = exc.strerror or str(exc)
        winerror = getattr(exc, "winerror", None)
        return False, f"{reason}（WinError {winerror}）" if winerror else reason


_writable_cache: Path | None = None


def writable_dir(subdir: str = "") -> Path:
    """第一个可写的状态目录（可加子目录）；全都不行时抛 OSError。"""
    global _writable_cache
    target = _writable_cache / subdir if _writable_cache is not None else None
    if target is not None and probe(target)[0]:
        return target
    for base in candidates():
        directory = base / subdir if subdir else base
        ok, _reason = probe(directory)
        if ok:
            _writable_cache = base
            return directory
    raise OSError(_report())


def _report() -> str:
    lines = ["没有可写的位置。逐个尝试结果："]
    for base in candidates():
        ok, reason = probe(base)
        lines.append(f"  {base} — {'可写' if ok else reason}")
    lines.append(
        "常见原因：程序装在受保护目录；Windows「受控文件夹访问」拦住了；或程序被受限运行时启动。\n"
        "解决：把程序移到普通文件夹（如 D:\\VoxCall），或在 Windows 安全中心 → 病毒和威胁防护 → "
        "受控文件夹访问 → 允许应用通过，把 runtime\\python313\\python\\python.exe 加进去。"
    )
    return "\n".join(lines)
