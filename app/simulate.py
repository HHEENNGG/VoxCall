"""离线仿真：不依赖声卡与真实模型，用合成音频跑通整条管线。

用途：
- `python main.py --simulate` 自我验证（也可导出 wav 试听）；
- 单元测试的端到端场景（采集/推理/播放三个线程真实运行，只是设备与模型是假的）。

注意：这里注入的 VoxLock 是"行为仿真"，只复现它的锁定规则（攒够约 3 秒语音才锁定，
锁定前原样透传），并不做真正的说话人分离。它用来验证 VoxCall 自己的管线与
"未锁定必须静音"这条安全规则。
"""

from __future__ import annotations

import contextlib
import logging
import sys
import threading
import time
import types
import wave
from dataclasses import dataclass, field

import numpy as np

from . import settings
from .engine import AudioEngine

log = logging.getLogger(__name__)

MODEL_RATE = 16000


# --------------------------------------------------------------------- 信号
def make_scene(rate: int = 48000, seconds: float = 12.0, seed: int = 7):
    """合成一段"房间"音频：本底噪声 + 目标说话人 + 竞争说话人。

    返回 (mix, segments)：segments 里记录各成分的时间段，便于测试断言。
    """
    rng = np.random.default_rng(seed)
    n = int(rate * seconds)
    t = np.arange(n) / rate

    noise = rng.standard_normal(n).astype(np.float32) * 0.006  # ~ -44 dBFS 稳态底噪
    mixed = noise.copy()

    def voiced(f0: float, start: float, stop: float, level: float) -> np.ndarray:
        env = np.zeros(n, dtype=np.float32)
        a, b = int(start * rate), int(stop * rate)
        env[a:b] = 1.0
        # 词/音节包络：约 3.5 Hz 的断续，词间有 -16 dB 的间隙。
        # 真实的 VoxLock 用"比最近 2 秒最低能量高 10 dB"判定语音，纯连续音会判不出来。
        gate = (np.sin(2 * np.pi * 3.5 * t + 0.4) > -0.2).astype(np.float32)
        syllable = 0.15 + 0.85 * gate
        carrier = sum(np.sin(2 * np.pi * f0 * k * t) / k for k in (1, 2, 3, 5))
        return (level * env * syllable * carrier).astype(np.float32)

    target = voiced(120.0, 1.0, max(2.0, seconds - 1.0), 0.16)
    competitor = voiced(210.0, 4.5, min(8.0, seconds - 1.0), 0.22)
    mixed += target + competitor

    # 键盘敲击：短促高频瞬态
    clicks = np.zeros(n, dtype=np.float32)
    for start in np.arange(2.0, min(8.5, seconds - 0.1), 0.7):
        idx = int(start * rate)
        length = min(int(0.02 * rate), n - idx)
        if length <= 1:
            continue
        burst = rng.standard_normal(length).astype(np.float32) * 0.25
        burst *= np.hanning(length)
        clicks[idx : idx + length] += burst
    mixed += clicks

    peak = float(np.max(np.abs(mixed)))
    if peak > 0.9:
        mixed *= 0.9 / peak

    segments = {
        "target": [(1.0, max(2.0, seconds - 1.0))],
        "competitor": [(4.5, min(8.0, seconds - 1.0))],
        "silence": [(0.0, 1.0)],
    }
    return mixed, segments


# ------------------------------------------------------------- 假 VoxLock
class _FakeLiveStream:
    """复现真实 LiveStream 的锁定规则：攒够 sec 秒语音后锁定，锁定前原样透传。"""

    def __init__(self, sec: float = 3.0, isolation: float = 0.25) -> None:
        self.need = int(sec * MODEL_RATE / 160)
        self.isolation = isolation
        self.locked = False
        self._voiced = 0
        self._floor: list[float] = []

    def push(self, chunk):
        x = np.asarray(chunk, dtype=np.float32).reshape(-1)
        nframes = x.size // 160
        for frame in x[: nframes * 160].reshape(nframes, 160):
            energy = 10 * np.log10(float(np.mean(frame * frame)) + 1e-10)
            self._floor.append(energy)
            del self._floor[:-200]
            if len(self._floor) >= 20 and energy >= min(self._floor) + 10:
                self._voiced += 1
        if not self.locked and self._voiced >= self.need:
            self.locked = True
        return x * (self.isolation if self.locked else 1.0)

    def flush(self):
        return np.zeros(0, dtype=np.float32)

    def voiced_frames(self) -> int:
        """已累计的"语音帧"数量，测试用。"""
        return self._voiced


class _FakeStream(_FakeLiveStream):
    def __init__(self, sample) -> None:
        super().__init__()
        self.locked = True


class _FakeVoxLock:
    lock_after = 3.0
    isolation = 0.25
    instances: list = []

    def __init__(self, threads=4, precision="int8", model_dir=None) -> None:
        self.threads = threads
        self.precision = precision
        _FakeVoxLock.instances.append(self)

    def live(self, sec: float = 3.0):
        return _FakeLiveStream(max(sec, self.lock_after), self.isolation)

    def stream(self, sample):
        return _FakeStream(sample)


# ------------------------------------------------------------------- 假设备
class FakeInputStream:
    def __init__(self, device, rate, block, callback, source, speed=4.0, extra=None, **_) -> None:
        self.device = device
        self.rate = rate
        self.block = block
        self.callback = callback
        self.source = source
        self.speed = max(0.25, float(speed))
        self.extra = extra
        self._thread: threading.Thread | None = None
        self._running = False

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="FakeCapture", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        period = self.block / self.rate / self.speed
        deadline = time.perf_counter()
        while self._running:
            block = self.source(self.block)
            if block.size:
                self.callback(block.reshape(-1, 1), block.size, None, None)
            deadline += period
            delay = deadline - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            else:
                deadline = time.perf_counter()

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2)

    def close(self) -> None:
        self._running = False


class FakeOutputStream:
    def __init__(self, device, rate, block, callback, sink, speed=4.0, extra=None, **_) -> None:
        self.device = device
        self.rate = rate
        self.block = block
        self.callback = callback
        self.sink = sink
        self.speed = max(0.25, float(speed))
        self._thread: threading.Thread | None = None
        self._running = False

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="FakePlayback", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        period = self.block / self.rate / self.speed
        deadline = time.perf_counter()
        while self._running:
            out = np.zeros((self.block, 1), dtype=np.float32)
            self.callback(out, self.block, None, None)
            self.sink.append(out[:, 0].copy())
            deadline += period
            delay = deadline - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            else:
                deadline = time.perf_counter()

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2)

    def close(self) -> None:
        self._running = False


class _Source:
    def __init__(self, audio: np.ndarray) -> None:
        self.audio = audio
        self.pos = 0

    @property
    def done(self) -> bool:
        return self.pos >= self.audio.size

    def __call__(self, frames: int) -> np.ndarray:
        if self.pos >= self.audio.size:
            return np.zeros(frames, dtype=np.float32)
        chunk = self.audio[self.pos : self.pos + frames]
        self.pos += chunk.size
        if chunk.size < frames:
            chunk = np.concatenate([chunk, np.zeros(frames - chunk.size, dtype=np.float32)])
        return chunk


# --------------------------------------------------------------------- 运行
@dataclass
class SimResult:
    rate: int
    output: np.ndarray
    mix: np.ndarray
    metrics: dict = field(default_factory=dict)
    locked_at: float | None = None
    peak_before_lock: float = 0.0
    wall_seconds: float = 0.0
    audio_locked_at: float | None = None  # 音频时间轴上的首次出声时刻

    @property
    def rms(self) -> float:
        return float(np.sqrt(np.mean(self.output**2))) if self.output.size else 0.0

    def rms_after_lock(self) -> float:
        if self.audio_locked_at is None or not self.output.size:
            return 0.0
        start = int(self.audio_locked_at * self.rate)
        tail = self.output[start:]
        return float(np.sqrt(np.mean(tail.astype(np.float64) ** 2))) if tail.size else 0.0


def _install_fake_voxlock() -> None:
    module = types.ModuleType("voxlock")
    module.VoxLock = _FakeVoxLock  # type: ignore[attr-defined]
    module.__version__ = "0.0-sim"  # type: ignore[attr-defined]
    sys.modules["voxlock"] = module
    if "onnxruntime" not in sys.modules:
        sys.modules["onnxruntime"] = types.ModuleType("onnxruntime")


@contextlib.contextmanager
def fake_voxlock():
    """临时把假 voxlock 注入 sys.modules，退出时恢复。"""
    saved = {name: sys.modules.get(name) for name in ("voxlock", "onnxruntime")}
    _install_fake_voxlock()
    try:
        yield _FakeVoxLock
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


def _build_source_audio(seconds: float, rate: int) -> tuple[np.ndarray, dict]:
    return make_scene(rate=rate, seconds=seconds)


def run(
    mode: str = "auto",
    seconds: float = 12.0,
    speed: float = 4.0,
    config: dict | None = None,
    denoise: bool = False,
    real_model: bool = False,
) -> SimResult:
    """用假设备跑一次完整管线，返回输出音频与指标。

    real_model=True 时不注入假 voxlock，直接调真实模型（需要已安装 voxlock 与模型文件）。
    """
    if real_model:
        # 真实推理有 RTF 限制，倍速过高会追不上实时；模型加载也会吃掉开头几秒，
        # 所以给足音频长度。
        return _run_impl(mode=mode, seconds=max(seconds, 20.0), speed=1.0,
                         config=config, denoise=denoise)
    with fake_voxlock():
        return _run_impl(mode=mode, seconds=seconds, speed=speed, config=config, denoise=denoise)


def _run_impl(*, mode: str, seconds: float, speed: float, config: dict | None, denoise: bool) -> SimResult:
    cfg = settings.normalize(config or {})
    cfg["audio"]["device_sample_rate"] = 48000
    cfg["audio"]["chunk_ms"] = 80
    cfg["audio"]["target_latency_ms"] = 160
    cfg["model"]["mode"] = mode
    cfg["denoise"]["enabled"] = bool(denoise)

    mix, _segments = _build_source_audio(seconds, 48000)
    source = _Source(mix)
    sink: list[np.ndarray] = []

    def factory(*, kind, device, rate, block, callback, extra):
        if kind == "input":
            return FakeInputStream(device, rate, block, callback, source, speed=speed, extra=extra)
        return FakeOutputStream(device, rate, block, callback, sink, speed=speed, extra=extra)

    statuses: list[str] = []
    engine = AudioEngine(
        cfg,
        on_status=statuses.append,
        stream_factory=factory,
    )
    engine.start(0, 1, mode=mode)

    expected = seconds / speed
    started = time.perf_counter()
    locked_at: float | None = None
    peak_before_lock = 0.0
    done_at: float | None = None
    while True:
        now = time.perf_counter()
        if now - started > expected + 3.0:
            break
        if source.done:
            if done_at is None:
                done_at = now
            elif now - done_at > 0.25:
                break
        snap = engine.snapshot()
        if snap["locked"] and locked_at is None:
            locked_at = now - started
        if not snap["locked"]:
            peak_before_lock = max(peak_before_lock, snap["out_peak"])
        if snap["state"] == "error":
            break
        time.sleep(0.02)
    metrics = engine.snapshot()
    engine.stop()
    engine.stop()  # 幂等，确保线程收尾

    output = np.concatenate(sink) if sink else np.zeros(0, dtype=np.float32)
    output = output[: mix.size]
    nonzero = np.nonzero(np.abs(output) > 1e-6)[0]
    audio_locked = float(nonzero[0]) / 48000 if nonzero.size else None
    metrics["statuses"] = statuses
    metrics["source_seconds"] = mix.size / 48000
    metrics["output_seconds"] = output.size / 48000
    return SimResult(
        rate=48000,
        output=output,
        mix=mix,
        metrics=metrics,
        locked_at=locked_at,
        peak_before_lock=peak_before_lock,
        wall_seconds=time.perf_counter() - started,
        audio_locked_at=audio_locked,
    )


def write_wav(path: str, audio: np.ndarray, rate: int = 48000) -> None:
    data = np.clip(np.asarray(audio, dtype=np.float32), -1.0, 1.0)
    pcm = (data * 32767.0).astype("<i2")
    with wave.open(path, "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(rate)
        fh.writeframes(pcm.tobytes())


def report(result: SimResult) -> str:
    m = result.metrics
    lines = [
        "离线仿真结果（假设备 + 假模型，仅验证 VoxCall 自身管线）",
        f"  时长：源 {m.get('source_seconds', 0):.1f}s / 输出 {m.get('output_seconds', 0):.1f}s",
        f"  设备：{m.get('device_rate')} Hz，块 {m.get('chunk_ms', 80)} ms，重采样 {m.get('resampler') or '-'}",
        f"  模型：{m.get('model')}（锁定 {result.locked_at:.2f}s）" if result.locked_at else f"  模型：{m.get('model')}（未锁定）",
        f"  锁定前输出峰值：{result.peak_before_lock:.5f}",
        f"  锁定后输出 RMS：{result.rms_after_lock():.4f}",
        f"  延迟估算：{m.get('latency_ms', 0):.0f} ms（缓冲 {m.get('buffer_ms', 0):.0f} ms）",
        f"  欠载 {m.get('underruns', 0)} 次 / 输入丢块 {m.get('dropped_in', 0)} / 输出丢样 {m.get('dropped_out', 0)}",
        f"  输出 RMS：{result.rms:.4f}",
    ]
    return "\n".join(lines)
