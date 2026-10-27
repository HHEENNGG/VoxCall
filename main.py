"""VoxCall 入口。

    python main.py                 启动界面
    python main.py --doctor        环境自检（缺什么、为什么、怎么办）
    python main.py --simulate      离线仿真（不需要声卡和模型）
"""

from __future__ import annotations

import argparse
import logging
import os
import platform
import sys
import time
from pathlib import Path

# 打包成 exe 后，程序数据（config/models/voiceprints/logs）放在 exe 同级目录
if getattr(sys, "frozen", False):
    ROOT = Path(sys.executable).resolve().parent
else:
    ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

VERSION = "0.01"
CHANNEL = "Beta"
VERSION_TEXT = f"{CHANNEL} {VERSION}"
try:  # 版本号以 app/__init__.py 为准
    from app import CHANNEL, VERSION, VERSION_TEXT  # type: ignore[no-redef]
except Exception:  # pragma: no cover - 仅在包不完整时
    pass
PREFERRED_PYTHONS = ((3, 11), (3, 12), (3, 13))


def setup_logging(config: dict) -> None:
    app_cfg = config.get("app", {})
    level_name = str(app_cfg.get("log_level", "INFO")).upper()
    level = getattr(logging, level_name, logging.INFO)
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    log_file = str(app_cfg.get("log_file", "") or "")
    if log_file:
        target = Path(log_file)
        if not target.is_absolute():
            target = ROOT / target
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            handlers.append(logging.FileHandler(target, encoding="utf-8"))
        except OSError:
            pass
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )


def _state_dirs() -> list[Path]:
    """可写目录候选：程序目录 -> 用户目录（程序装在受保护位置时用后者）。"""
    from app import settings

    return [ROOT, settings.state_dir()]


def _write_crash_log(text: str) -> str | None:
    """把启动异常写进 logs/error.log（程序目录不可写就写用户目录），返回路径。"""
    import time

    for base in _state_dirs():
        try:
            log_dir = base / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            path = log_dir / "error.log"
            with path.open("a", encoding="utf-8") as fh:
                fh.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
                fh.write(text)
                fh.write("\n")
            return str(path)
        except OSError:
            continue
    return None


# --------------------------------------------------------------------- doctor
def _module_status(name: str, required: bool) -> tuple[str, bool]:
    try:
        module = __import__(name)
    except Exception as exc:
        flag = "缺失" if required else "可选未装"
        return f"{flag}: {exc.__class__.__name__}: {exc}", False
    version = getattr(module, "__version__", "")
    return f"OK {version}".strip(), True


def cmd_doctor(config: dict) -> int:
    print(f"VoxCall {VERSION_TEXT} · 环境自检")
    print(f"  操作系统 : {platform.platform()}")
    print(f"  Python   : {sys.version.split()[0]}  ({sys.executable})")
    py_ok = sys.version_info[:2] in PREFERRED_PYTHONS
    print(f"  版本适配 : {'OK' if py_ok else '不适配模型（onnxruntime 目前只提供 3.11–3.13 的 wheel）'}")

    print("\n依赖：")
    deps = [
        ("numpy", True),
        ("sounddevice", True),
        ("PySide6", True),
        ("soxr", False),
        ("voxlock", False),
        ("onnxruntime", False),
    ]
    missing_required: list[str] = []
    model_ready = True
    for name, required in deps:
        text, ok = _module_status(name, required)
        print(f"  {name:<12} {text}")
        if not ok and required:
            missing_required.append(name)
        if not ok and name in {"voxlock", "onnxruntime"}:
            model_ready = False

    from app.model_adapter import bundled_model_dir

    bundled = bundled_model_dir()
    if bundled:
        print(f"  {'模型文件':<12} 自带：{bundled}")
    else:
        print(f"  {'模型文件':<12} 未随程序附带（首次启动从 Hugging Face 下载约 75 MB）")

    from app import settings as settings_mod
    from app import voiceprint as voiceprint_mod

    try:
        vp_dir = voiceprint_mod.store_dir()
        print(f"  {'声纹目录':<12} 可写：{vp_dir}")
    except Exception as exc:
        print(f"  {'声纹目录':<12} 不可写（{exc.__class__.__name__}）")
    print(f"  {'配置文件':<12} {settings_mod.CONFIG_PATH}")

    print("\n音频设备：")
    devices_ok = True
    try:
        import sounddevice as sd

        for api in sd.query_hostapis():
            count = len(api.get("devices", []) or [])
            print(f"  主机 API : {api.get('name', '?')}（{count} 个设备）")
        inputs = [d for d in sd.query_devices() if d["max_input_channels"] > 0]
        outputs = [d for d in sd.query_devices() if d["max_output_channels"] > 0]
        print(f"  输入 {len(inputs)} 个 / 输出 {len(outputs)} 个")
        cables = [d["name"] for d in outputs if "cable input" in d["name"].lower()]
        if cables:
            print(f"  VB-CABLE : 已安装（{', '.join(sorted(set(cables)))}）")
        else:
            print("  VB-CABLE : 未检测到 CABLE Input（安装后才能把声音送进通话软件）")
        if not inputs or not outputs:
            devices_ok = False
            print("  警告     : 没有可用的输入或输出设备，界面无法开始。")
    except Exception as exc:
        devices_ok = False
        print(f"  枚举失败 : {exc}")

    print("\n配置：")
    for section, values in config.items():
        print(f"  [{section}]")
        for key, value in values.items():
            print(f"    {key} = {value!r}")

    print("\n结论：")
    exit_code = 0
    if missing_required:
        print(f"  [X] 缺少必需依赖：{', '.join(missing_required)}，请运行 install.ps1 安装。")
        exit_code = 1
    if not devices_ok:
        print("  [X] 音频设备不可用，请检查声音设置 / VB-CABLE 驱动。")
        exit_code = 1
    if not model_ready:
        print("  [!] VoxLock 不可用：程序会以直通模式运行（只做路由与门控）。")
        print("      最快修复： .\\install.ps1 -Portable   （下载便携版 Python 3.13，不改系统）")
        print("      或：      py -3.13 -m venv .venv 后 pip install -r requirements.txt")
        if not py_ok:
            print(f"      当前是 Python {sys.version.split()[0]}，onnxruntime 还没有对应版本。")
    if model_ready and py_ok and not missing_required and devices_ok:
        print("  [OK] 全部就绪，可以直接 python main.py")
    return exit_code


# ------------------------------------------------------------------- simulate
def cmd_simulate(args, config: dict) -> int:
    from app import simulate

    print("正在跑离线仿真（假设备 + 假模型，验证管线与「未锁定必须静音」规则）…")
    result = simulate.run(
        mode=args.mode,
        seconds=args.seconds,
        speed=args.speed,
        config=config,
        denoise=args.denoise,
        real_model=args.real_model,
    )
    print(simulate.report(result))
    if args.wav:
        simulate.write_wav(args.wav, result.output, result.rate)
        print(f"已写出 {args.wav}")
    finite = bool(result.output.size) and bool(np_all_finite(result.output))
    ok = (
        result.peak_before_lock < 1e-3
        and result.locked_at is not None
        and result.rms_after_lock() > 1e-4
        and finite
    )
    print(f"\n结果：{'通过' if ok else '未通过'}")
    return 0 if ok else 2


def np_all_finite(array) -> bool:
    import numpy as np

    return bool(np.isfinite(array).all())


# ------------------------------------------------------------------------ GUI
def run_gui(config: dict) -> int:
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError as exc:
        message = (
            f"无法启动界面：缺少 PySide6（{exc}）。\n"
            "请先运行 install.ps1，或 pip install PySide6；也可以用 --doctor 查看环境。"
        )
        print(message, file=sys.stderr)
        _show_error_dialog("VoxCall 缺少依赖", message)
        return 1

    from app.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("VoxCall")
    app.setApplicationVersion(VERSION)

    try:  # 窗口/任务栏图标
        from PySide6.QtGui import QIcon

        from app.paths import assets_dir

        for name in ("icon.ico", "icon-256.png", "icon.png"):
            icon_path = assets_dir() / name
            if icon_path.exists():
                app.setWindowIcon(QIcon(str(icon_path)))
                break
    except Exception:
        pass

    window = MainWindow(config)
    window.show()

    # 启动即最小化到托盘（设置里可开）
    if bool(config.get("app", {}).get("start_minimized", False)) and window._tray is not None:
        window.hide()
        window._tray.showMessage(
            "VoxCall 已在后台运行",
            "右键托盘图标可以显示主窗口或退出。",
            window._tray.Information,
            3000,
        )

    return int(app.exec())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="VoxCall", description="Windows 实时通话降噪（VoxLock + VB-CABLE）")
    parser.add_argument("--config", default=None, help="配置文件路径（默认 config.toml）")
    parser.add_argument("--doctor", action="store_true", help="检查环境并给出修复建议")
    parser.add_argument("--simulate", action="store_true", help="离线仿真，不需要声卡与模型")
    parser.add_argument("--mode", default="auto", choices=["auto", "calibrated"], help="仿真用的锁定模式")
    parser.add_argument("--seconds", type=float, default=12.0, help="仿真音频时长")
    parser.add_argument("--speed", type=float, default=6.0, help="仿真倍速")
    parser.add_argument("--denoise", action="store_true", help="仿真时启用频谱门")
    parser.add_argument("--real-model", action="store_true", help="仿真时使用真实 VoxLock 模型（慢，需已装模型）")
    parser.add_argument("--wav", default="", help="仿真的输出 wav 路径")
    parser.add_argument("--version", action="version", version=f"VoxCall {VERSION_TEXT}")
    return parser


def _ensure_streams() -> None:
    """用 pythonw（无控制台）启动时 stdout/stderr 是 None，补成空设备，避免 print 崩溃。"""
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))


def _show_error_dialog(title: str, text: str) -> None:
    """没有控制台时，把致命错误显示成 Windows 弹窗，而不是静默退出。"""
    if sys.stderr is not None and sys.stderr.name != os.devnull and getattr(sys.stderr, "isatty", lambda: False)():
        return  # 有控制台就只打日志，不弹窗
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, text, title, 0x10)
    except Exception:
        pass


def main(argv: list[str] | None = None) -> int:
    _ensure_streams()
    args = build_parser().parse_args(argv)
    try:  # 统一输出编码：run.bat 已 chcp 65001，避免 GBK 控制台乱码
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    from app import settings

    try:
        config = settings.load_config(args.config)
    except settings.ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 1
    setup_logging(config)

    if args.doctor:
        return cmd_doctor(config)
    if args.simulate:
        return cmd_simulate(args, config)

    import traceback

    try:
        return run_gui(config)
    except Exception:
        detail = traceback.format_exc()
        path = _write_crash_log(detail)
        print("启动崩溃，详细信息：", file=sys.stderr)
        print(detail, file=sys.stderr)
        if path:
            print(f"已写入：{path}", file=sys.stderr)
        _show_error_dialog(
            "VoxCall 启动失败",
            detail.strip().splitlines()[-1] + (f"\n\n完整日志：{path}" if path else ""),
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
