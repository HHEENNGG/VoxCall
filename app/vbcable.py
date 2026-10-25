"""VB-CABLE 检测与「一键引导安装」。

VB-CABLE 是内核驱动，安装必须管理员权限、由官方安装程序完成，装完还要重启；
所以这里的「自动安装」= 自动下载官方安装包 + 弹 UAC 启动安装程序 + 提示重启，
最后一步确认由用户在系统对话框里点。不会、也不可能静默装驱动。
"""

from __future__ import annotations

import ctypes
import logging
import shutil
import ssl
import urllib.request
import zipfile
from pathlib import Path

from . import devices as devices_mod
from . import paths

log = logging.getLogger(__name__)

# 官方页面 https://vb-audio.com/Cable/ 当前提供 Pack45（2024-10，含 ARM64）；
# Pack43（2015）只作为 old package 保留。
HOMEPAGE = "https://vb-audio.com/Cable/"
DOWNLOAD_URL = "https://download.vb-audio.com/Download_CABLE/VBCABLE_Driver_Pack45.zip"
DONATE_URL = "https://shop.vb-audio.com/en/win-apps/11-vb-cable.html"
BUNDLED_ZIP_NAME = "VBCABLE_Driver_Pack45.zip"
SETUP_NAMES = ("VBCABLE_Setup_x64.exe", "VBCABLE_Setup_ARM64.exe", "VBCABLE_Setup.exe")
LICENSE_NOTE = (
    "VB-CABLE © VB-Audio Software（Vincent Burel）。它是 donationware："
    "免费使用、欢迎捐赠，来源 www.vb-cable.com。"
    "本程序只在需要时从官方站点下载安装包，未修改任何 VB-Audio 文件。"
)


class VBCableError(RuntimeError):
    pass


def find_setup(directory: Path) -> Path | None:
    """在解压目录里找官方安装程序。"""
    for name in SETUP_NAMES:
        candidate = directory / name
        if candidate.exists():
            return candidate
    for candidate in directory.rglob("VBCABLE_Setup_x64.exe"):
        return candidate
    for candidate in directory.rglob("VBCABLE_Setup.exe"):
        return candidate
    return None


def is_installed() -> bool:
    """输出列表里有没有 CABLE Input（或输入里有没有 CABLE Output）。"""
    try:
        outputs = devices_mod.list_devices("output", devices_mod.HOSTAPI_ALL)
        inputs = devices_mod.list_devices("input", devices_mod.HOSTAPI_ALL)
    except Exception:
        log.debug("检测 VB-CABLE 失败", exc_info=True)
        return False
    names = [d.name.lower() for d in outputs] + [d.name.lower() for d in inputs]
    return any("cable input" in name for name in names) and any(
        "cable output" in name for name in names
    )


def installer_dir() -> Path:
    return paths.writable_dir("installers")


def bundled_zip() -> Path | None:
    """分发包里内置的官方安装包（原版 zip，未修改）。"""
    from .paths import assets_dir

    base = assets_dir()
    for candidate in (base / "vbcable" / BUNDLED_ZIP_NAME, base / BUNDLED_ZIP_NAME):
        if candidate.exists():
            return candidate
    return None


def _extract(zip_path: Path, directory: Path, progress=None) -> Path:
    if progress:
        progress("正在解压 VB-CABLE 安装包…")
    try:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(directory)
    except Exception as exc:
        raise VBCableError(f"安装包解压失败：{exc}") from exc
    setup = find_setup(directory)
    if setup is None:
        raise VBCableError(f"安装包里没找到 VBCABLE_Setup_x64.exe，请到 {HOMEPAGE} 手动安装。")
    return setup


def download_installer(progress=None) -> Path:
    """准备安装程序：优先用内置的官方包，没有再联网下载。"""
    directory = installer_dir()
    existing = find_setup(directory)
    if existing is not None:
        return existing

    packaged = bundled_zip()
    if packaged is not None:
        if progress:
            progress("正在解压内置的 VB-CABLE 安装包…")
        return _extract(packaged, directory, progress)

    if progress:
        progress("正在从官网下载 VB-CABLE 安装包…")
    ctx = ssl.create_default_context()
    request = urllib.request.Request(DOWNLOAD_URL, headers={"User-Agent": "VoxCall"})
    try:
        with urllib.request.urlopen(request, timeout=120, context=ctx) as response:
            blob = response.read()
    except Exception as exc:
        raise VBCableError(
            f"下载失败：{exc}\n可以手动到 {HOMEPAGE} 下载 VB-CABLE 安装。"
        ) from exc

    archive = directory / "vbcable.zip"
    try:
        archive.write_bytes(blob)
    except OSError as exc:
        raise VBCableError(f"安装包写入失败：{exc}") from exc
    setup = _extract(archive, directory, progress)
    archive.unlink(missing_ok=True)
    return setup


def run_installer(setup: Path) -> None:
    """以管理员身份启动官方安装程序（会弹 UAC）。"""
    if not setup.exists():
        raise VBCableError(f"安装程序不存在：{setup}")
    try:
        # ShellExecuteW + runas：等价于右键「以管理员身份运行」
        result = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", str(setup), None, str(setup.parent), 1
        )
    except Exception as exc:
        raise VBCableError(f"无法启动安装程序：{exc}") from exc
    if result <= 32:  # ShellExecute 返回值 ≤32 表示失败
        raise VBCableError(
            f"启动安装程序被拒绝或取消（代码 {result}）。可以手动运行：{setup}"
        )


def delete_installer() -> None:
    """清理下载的安装包（安装完或取消后）。"""
    try:
        shutil.rmtree(installer_dir(), ignore_errors=True)
    except Exception:
        log.debug("清理安装包失败", exc_info=True)


def installer_running() -> bool:
    """官方安装程序是否还在运行（用 tasklist，避免额外依赖）。"""
    import subprocess

    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    for name in SETUP_NAMES + ("VBCABLE_ControlPanel.exe",):
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {name}", "/NH"],
                capture_output=True,
                text=True,
                timeout=10,
                creationflags=flags,
            )
        except Exception:
            return False
        if name.lower() in (result.stdout or "").lower():
            return True
    return False


def restart_computer(delay_seconds: int = 15) -> None:
    """提示并重启电脑（安装驱动后需要）。"""
    import subprocess

    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.Popen(
            [
                "shutdown",
                "/r",
                "/t",
                str(int(delay_seconds)),
                "/c",
                "VoxCall：安装 VB-CABLE 后重启电脑",
            ],
            creationflags=flags,
        )
    except Exception as exc:
        raise VBCableError(f"无法启动重启命令：{exc}") from exc
