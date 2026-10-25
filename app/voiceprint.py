"""固定声纹：录制一次目标说话人的语音样本，之后每次启动直接锁定。

存放位置：优先 项目目录/voiceprints/<name>.npy（16 kHz float32，3 秒）+ 同名 .json 元信息；
程序目录不可写（装在 Program Files、或 Windows「受控文件夹访问」拦了文档目录）时，
自动退到 %LOCALAPPDATA%\\VoxCall\\voiceprints 或 ~/.voxcall/voiceprints。
这是本地文件，不会上传；不想留就点「清除声纹」。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

import numpy as np

from . import devices as devices_mod
from . import paths
from .resample import StreamResampler

log = logging.getLogger(__name__)

from .paths import ROOT  # noqa: E402  (打包后指向 exe 目录)

DIR = ROOT / "voiceprints"
DEFAULT_NAME = "default"
MODEL_RATE = 16000
MIN_SECONDS = 3.0


class VoiceprintError(RuntimeError):
    pass


def candidate_dirs() -> list[Path]:
    """按优先级列出声纹目录：程序目录 → 上一级 → 用户目录 → 临时目录。"""
    override = os.environ.get("VOXCALL_STATE_DIR")
    if override:
        return [Path(override) / "voiceprints"]
    dirs = [DIR]
    for base in paths.candidates():
        dirs.append(base / "voiceprints")
    unique: list[Path] = []
    seen: set[str] = set()
    for directory in dirs:
        key = str(directory).lower()
        if key not in seen:
            seen.add(key)
            unique.append(directory)
    return unique


_STORE_DIR: Path | None = None


def _writable(directory: Path) -> bool:
    return paths.probe(directory)[0]


def store_dir() -> Path:
    """实际用于保存的目录（第一个可写候选）。"""
    global _STORE_DIR
    if _STORE_DIR is not None and _writable(_STORE_DIR):
        return _STORE_DIR
    for directory in candidate_dirs():
        if _writable(directory):
            _STORE_DIR = directory
            return directory
    raise VoiceprintError(_report())


def _report() -> str:
    lines = ["没有可写的目录来保存声纹。逐个尝试结果："]
    for directory in candidate_dirs():
        ok, reason = paths.probe(directory)
        lines.append(f"  {directory} — {'可写' if ok else reason}")
    lines.append(
        "常见原因：程序装在受保护目录；Windows「受控文件夹访问」拦住了写入；或程序被受限运行时启动。\n"
        "解决：把程序移到普通文件夹（如 D:\\VoxCall），或在 Windows 安全中心 → 病毒和威胁防护 → "
        "受控文件夹访问 → 允许应用通过，把 runtime\\python313\\python\\python.exe 加进去。"
    )
    return "\n".join(lines)


def _path(name_or_path: str | None) -> Path:
    """显式路径（配置里指定）或默认保存目录下的文件名。"""
    if not name_or_path:
        return store_dir() / f"{DEFAULT_NAME}.npy"
    p = Path(name_or_path).expanduser()
    if p.suffix.lower() != ".npy":
        p = p.with_suffix(".npy")
    return p if p.is_absolute() else ROOT / p


def _find(name_or_path: str | None = None) -> Path | None:
    """查找已存在的声纹文件（显式路径优先，否则遍历候选目录）。"""
    if name_or_path:
        p = _path(name_or_path)
        return p if p.exists() else None
    for directory in candidate_dirs():
        p = directory / f"{DEFAULT_NAME}.npy"
        if p.exists():
            return p
    return None


def exists(name_or_path: str | None = None) -> bool:
    return _find(name_or_path) is not None


def info(name_or_path: str | None = None) -> dict | None:
    p = _find(name_or_path)
    if p is None:
        return None
    meta: dict = {}
    sidecar = p.with_suffix(".json")
    if sidecar.exists():
        try:
            meta = json.loads(sidecar.read_text(encoding="utf-8"))
        except Exception:  # pragma: no cover - 文件损坏
            log.debug("声纹元信息读取失败", exc_info=True)
    try:
        meta.setdefault("samples", int(np.load(p, mmap_mode="r").size))
    except Exception:
        pass
    meta.setdefault("sample_rate", MODEL_RATE)
    meta["path"] = str(p)
    return meta


def load(name_or_path: str | None = None) -> np.ndarray:
    p = _find(name_or_path)
    if p is None:
        raise VoiceprintError(
            "还没有保存的声纹。请先在界面里点「录制/更新声纹」，对着麦克风连续说 3 秒。"
        )
    try:
        sample = np.load(p)
    except Exception as exc:
        raise VoiceprintError(f"声纹文件读取失败（{p}）：{exc}") from exc
    sample = np.asarray(sample, dtype=np.float32).reshape(-1)
    if sample.size < int(MIN_SECONDS * MODEL_RATE):
        raise VoiceprintError(
            f"声纹太短（{sample.size / MODEL_RATE:.2f} 秒），至少需要 {MIN_SECONDS:.0f} 秒，请重新录制。"
        )
    return sample


def save(sample: np.ndarray, meta: dict | None = None, name_or_path: str | None = None) -> Path:
    arr = np.asarray(sample, dtype=np.float32).reshape(-1)
    if arr.size < int(MIN_SECONDS * MODEL_RATE):
        raise VoiceprintError(
            f"样本太短（{arr.size / MODEL_RATE:.2f} 秒），至少需要 {MIN_SECONDS:.0f} 秒。"
        )
    p = _path(name_or_path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        np.save(p, arr)
        payload = dict(meta or {})
        payload.update(
            samples=int(arr.size),
            sample_rate=MODEL_RATE,
            duration_seconds=round(arr.size / MODEL_RATE, 3),
            created_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        )
        p.with_suffix(".json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        raise VoiceprintError(
            f"写入声纹失败：{p}\n{exc}\n"
            "如果程序放在受保护目录，可换一个可写位置，或在 Windows「受控文件夹访问」里允许本程序。"
        ) from exc
    return p


def clear(name_or_path: str | None = None) -> bool:
    if name_or_path:
        found = _find(name_or_path)
        targets = [found] if found else []
    else:
        # 默认声纹可能在多个候选目录里，全部清掉
        targets = [d / f"{DEFAULT_NAME}.npy" for d in candidate_dirs()]
    removed = False
    for p in targets:
        for target in (p, p.with_suffix(".json")):
            if target.exists():
                try:
                    target.unlink()
                    removed = True
                except OSError:
                    log.warning("删除 %s 失败", target, exc_info=True)
    return removed


def describe(name_or_path: str | None = None) -> str:
    meta = info(name_or_path)
    if not meta:
        return "未录制"
    created = str(meta.get("created_at", "?"))
    device = str(meta.get("device", "") or "")
    return f"已保存（{created}{'，' + device if device else ''}）"


class _Capture:
    """3 秒声纹录制：独立开一个输入流，不经过推理管线。"""

    def __init__(self, device_rate: int, seconds: float) -> None:
        self.device_rate = int(device_rate)
        self.need = int(seconds * device_rate)
        self.chunks: list[np.ndarray] = []
        self.total = 0
        self.done = threading.Event()
        self.lock = threading.Lock()
        self.status: str | None = None

    def callback(self, indata, frames, time_info, status) -> None:
        if status:
            self.status = str(status)
        x = np.asarray(indata[:, 0], dtype=np.float32).copy()
        with self.lock:
            self.chunks.append(x)
            self.total += x.size
            if self.total >= self.need:
                self.done.set()

    def take(self) -> np.ndarray:
        with self.lock:
            audio = np.concatenate(self.chunks)[: self.need] if self.chunks else np.zeros(0, np.float32)
        return audio


def record(
    device: devices_mod.DeviceInfo,
    seconds: float = MIN_SECONDS,
    device_rate: int = 48000,
    target_rate: int = MODEL_RATE,
    hostapi: str = devices_mod.HOSTAPI_AUTO,
    stream_factory=None,
) -> np.ndarray:
    """从指定设备录 `seconds` 秒，重采样到 16 kHz 并做基本质量检查。"""
    if seconds < MIN_SECONDS:
        raise VoiceprintError(f"录制时长至少 {MIN_SECONDS:.0f} 秒。")
    capture = _Capture(device_rate, seconds)
    block = max(1, int(device_rate * 0.08))
    if stream_factory is not None:
        stream = stream_factory(
            kind="input", device=device, rate=device_rate, block=block,
            callback=capture.callback, extra=None,
        )
    else:
        import sounddevice as sd

        try:
            stream = sd.InputStream(
                device=device.index,
                samplerate=device_rate,
                channels=1,
                dtype="float32",
                blocksize=block,
                callback=capture.callback,
                extra_settings=devices_mod.wasapi_settings(device, hostapi),
                latency="low",
            )
        except Exception as exc:
            raise VoiceprintError(f"无法打开录音设备：{exc}") from exc

    try:
        stream.start()
        ok = capture.done.wait(timeout=seconds * 3 + 5)
    finally:
        for action in ("stop", "close"):
            try:
                getattr(stream, action)()
            except Exception:
                pass

    if not ok:
        raise VoiceprintError("录制超时：没有收到音频，检查麦克风是否被占用。")

    audio = capture.take()
    if audio.size == 0:
        raise VoiceprintError("录制失败：没有采到任何音频。")
    rms = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2)))
    if rms < 5e-4:
        raise VoiceprintError(f"录到的几乎都是静音（RMS={rms:.5f}），请靠近麦克风大声说话再试。")
    if float(np.max(np.abs(audio))) > 0.99:
        log.warning("声纹样本接近满幅，可能失真")

    resampler = StreamResampler(device_rate, target_rate)
    out = resampler.process(audio)
    out = resampler.flush() if out.size == 0 else np.concatenate([out, resampler.flush()])
    out = np.asarray(out, dtype=np.float32).reshape(-1)
    need = int(seconds * target_rate)
    if out.size < need:
        # 重采样器有延迟，末尾补零到目标长度
        out = np.concatenate([out, np.zeros(need - out.size, dtype=np.float32)])
    return out[:need]
