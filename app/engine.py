"""实时音频引擎：设备 I/O、推理线程、抖动缓冲与状态上报。

线程模型
--------
PortAudio 回调线程：只做拷贝（采集入队 / 从环形缓冲取数播放），绝不跑推理。
推理线程：重采样 → 模型 → 门控 → 重采样 → 写入环形缓冲。
UI 线程：每 100 ms 读取一次 snapshot()，不接收高频信号。

延迟策略
--------
环形缓冲以 `audio.target_latency_ms` 起步预填充，避免启动时欠载；
超过 `audio.max_output_ms` 时丢弃最旧数据（宁可丢一点也不要累积延迟）。
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable

import numpy as np

from . import devices as devices_mod
from .denoise import SpectralGate
from .model_adapter import MODEL_LATENCY_MS, ModelError, create_adapter
from .resample import StreamResampler

log = logging.getLogger(__name__)


class EngineError(RuntimeError):
    pass


class CalibrationError(EngineError):
    pass


class RingBuffer:
    """单生产者/单消费者环形缓冲；写满时丢弃最旧数据并计数。"""

    def __init__(self, capacity: int) -> None:
        self.capacity = max(1, int(capacity))
        self._buf = np.zeros(self.capacity, dtype=np.float32)
        self._read = 0
        self._write = 0
        self._size = 0
        self._lock = threading.Lock()
        self.dropped = 0

    def available(self) -> int:
        with self._lock:
            return self._size

    def clear(self) -> None:
        with self._lock:
            self._read = self._write = self._size = 0

    def write(self, x: np.ndarray) -> int:
        x = np.asarray(x, dtype=np.float32).reshape(-1)
        if x.size == 0:
            return 0
        with self._lock:
            if x.size > self.capacity:
                x = x[-self.capacity:]
            overflow = max(0, self._size + x.size - self.capacity)
            if overflow:
                self._read = (self._read + overflow) % self.capacity
                self._size -= overflow
                self.dropped += overflow
            first = min(x.size, self.capacity - self._write)
            self._buf[self._write : self._write + first] = x[:first]
            if x.size > first:
                self._buf[: x.size - first] = x[first:]
            self._write = (self._write + x.size) % self.capacity
            self._size += x.size
            return overflow

    def read(self, n: int) -> np.ndarray:
        n = int(n)
        out = np.zeros(n, dtype=np.float32)
        with self._lock:
            take = min(n, self._size)
            if take:
                first = min(take, self.capacity - self._read)
                out[:first] = self._buf[self._read : self._read + first]
                if take > first:
                    out[first:take] = self._buf[: take - first]
                self._read = (self._read + take) % self.capacity
                self._size -= take
        return out


@dataclass
class _Metrics:
    state: str = "idle"
    status: str = "就绪"
    in_peak: float = 0.0
    in_rms: float = 0.0
    out_peak: float = 0.0
    out_rms: float = 0.0
    dropped_in: int = 0
    dropped_out: int = 0
    underruns: int = 0
    blocks: int = 0
    buffer_ms: float = 0.0
    latency_ms: float = 0.0
    driver_ms: float = 0.0      # 驱动/系统侧缓冲（采集 + 播放）
    queue_ms: float = 0.0       # 采集到推理线程取走之间的等待
    infer_ms: float = 0.0       # 一次推理（重采样+模型+门控+重采样）耗时
    locked: bool = False
    model: str = "off"
    resampler: str = ""
    device_rate: int = 0
    input_name: str = ""
    output_name: str = ""


class AudioEngine:
    def __init__(
        self,
        config: dict,
        on_status: Callable[[str], None] | None = None,
        stream_factory: Callable[..., object] | None = None,
        adapter_factory: Callable[[dict, Callable[[str], None]], object | None] | None = None,
    ) -> None:
        self.cfg = config
        self.audio_cfg = config["audio"]
        self.model_cfg = config["model"]
        self.denoise_cfg = config["denoise"]
        self.on_status = on_status or (lambda message: None)
        self._stream_factory = stream_factory
        self._adapter_factory = adapter_factory or create_adapter

        self.chunk_ms = int(self.audio_cfg["chunk_ms"])
        self.capture_block_ms = int(self.audio_cfg.get("capture_block_ms", min(10, self.chunk_ms)))
        self.playback_block_ms = int(self.audio_cfg.get("playback_block_ms", self.chunk_ms))
        self.model_rate = int(self.audio_cfg["model_sample_rate"])
        self.device_rate = int(self.audio_cfg["device_sample_rate"])
        self.block = max(1, self.device_rate * self.chunk_ms // 1000)
        self.in_block = max(1, self.device_rate * self.capture_block_ms // 1000)
        self.out_block = max(1, self.device_rate * self.playback_block_ms // 1000)
        self._in_pending = np.zeros(0, dtype=np.float32)

        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self._stream_lock = threading.Lock()
        self._in_stream = None
        self._out_stream = None
        self._priming = True
        self._prefill_samples = 0
        self._input_q: queue.Queue = queue.Queue(maxsize=int(self.audio_cfg["max_input_blocks"]))
        self._ring = RingBuffer(1)
        self._in_resampler: StreamResampler | None = None
        self._out_resampler: StreamResampler | None = None
        self._gate: SpectralGate | None = None
        self._adapter = None
        self._adapter_name = "off"
        self._mode = str(self.model_cfg.get("mode", "auto"))
        self._metrics = _Metrics()
        self._metrics_lock = threading.Lock()

    # ------------------------------------------------------------------ 状态
    def _status(self, message: str) -> None:
        with self._metrics_lock:
            self._metrics.status = message
        try:
            self.on_status(message)
        except Exception:  # pragma: no cover - 界面回调异常不应影响音频
            log.debug("状态回调异常", exc_info=True)

    def _set_state(self, state: str, message: str | None = None) -> None:
        with self._metrics_lock:
            self._metrics.state = state
        if message is not None:
            self._status(message)

    @property
    def running(self) -> bool:
        return self._worker is not None and self._worker.is_alive()

    def snapshot(self) -> dict:
        with self._metrics_lock:
            data = dict(self._metrics.__dict__)
        rate = max(1, self.device_rate)
        buffer_ms = self._ring.available() / rate * 1000.0
        # 驱动缓冲 + 采集块 + 队列等待 + 播放块 + 模型算法延迟 + 门控延迟 + 当前缓冲
        latency = (
            data.get("driver_ms", 0.0)
            + buffer_ms
            + data.get("queue_ms", 0.0)
            + self.chunk_ms
            + self.playback_block_ms
        )
        if self._adapter is not None:
            latency += MODEL_LATENCY_MS
        gate = self._gate
        if gate is not None:
            latency += gate.latency_samples / max(1, self.model_rate) * 1000.0
        data["buffer_ms"] = buffer_ms
        data["latency_ms"] = latency
        data["locked"] = bool(self._adapter is not None and self._adapter.locked)
        data["model"] = self._adapter_name
        data["device_rate"] = self.device_rate
        return data

    # ------------------------------------------------------------ 设备与流
    def _as_device(self, value, kind: str):
        if isinstance(value, devices_mod.DeviceInfo):
            return value
        if isinstance(value, int):
            for dev in devices_mod.list_devices(kind):
                if dev.index == value:
                    return dev
            if self._stream_factory is not None:
                # 离线仿真/测试：没有真实声卡时用占位设备。
                return devices_mod.DeviceInfo(
                    index=value,
                    name="Microphone (sim)" if kind == "input" else "CABLE Input (sim)",
                    hostapi=0,
                    hostapi_name="sim",
                    max_input=1 if kind == "input" else 0,
                    max_output=0 if kind == "input" else 1,
                    default_samplerate=48000.0,
                )
        return None

    def _make_stream(self, kind, device, rate, block, callback, extra):
        if self._stream_factory is not None:
            return self._stream_factory(
                kind=kind, device=device, rate=rate, block=block, callback=callback, extra=extra
            )
        import sounddevice as sd

        kwargs = dict(
            device=device.index,
            samplerate=rate,
            channels=1,
            dtype="float32",
            blocksize=block,
            callback=callback,
            extra_settings=extra,
            latency="low",
        )
        return sd.InputStream(**kwargs) if kind == "input" else sd.OutputStream(**kwargs)

    def _close_streams(self) -> None:
        with self._stream_lock:
            for name in ("_in_stream", "_out_stream"):
                stream = getattr(self, name)
                setattr(self, name, None)
                if stream is None:
                    continue
                for action in ("stop", "close"):
                    try:
                        getattr(stream, action)()
                    except Exception:
                        log.debug("关闭音频流失败", exc_info=True)

    def _open_streams(self, in_dev, out_dev) -> int:
        hostapi = str(self.audio_cfg.get("hostapi", "auto"))
        candidates: list[int] = []
        for rate in (
            self.device_rate,
            int(in_dev.default_samplerate or 0),
            int(out_dev.default_samplerate or 0),
            48000,
            44100,
        ):
            if rate >= 8000 and rate not in candidates:
                candidates.append(rate)

        errors: list[str] = []
        for rate in candidates:
            block = max(1, rate * self.chunk_ms // 1000)
            # 采集流单独用小 blocksize：PortAudio/WASAPI 的驱动缓冲随 blocksize 增长，
            # 用 80 ms 块会白白多出 100+ ms 延迟。音频在回调里再拼成 chunk_ms 的处理块。
            in_block = max(1, rate * min(self.capture_block_ms, self.chunk_ms) // 1000)
            out_block = max(1, rate * self.playback_block_ms // 1000)
            try:
                if self._stream_factory is None:
                    import sounddevice as sd

                    sd.check_input_settings(device=in_dev.index, samplerate=rate, channels=1, dtype="float32")
                    sd.check_output_settings(device=out_dev.index, samplerate=rate, channels=1, dtype="float32")
                self.device_rate = rate
                self.block = block
                self.in_block = in_block
                self.out_block = out_block
                self._ring = RingBuffer(max(1, rate * int(self.audio_cfg["max_output_ms"]) // 1000))
                self._in_resampler = StreamResampler(rate, self.model_rate)
                self._out_resampler = StreamResampler(self.model_rate, rate)
                self._in_stream = self._make_stream(
                    "input", in_dev, rate, in_block, self._capture, devices_mod.wasapi_settings(in_dev, hostapi)
                )
                self._out_stream = self._make_stream(
                    "output", out_dev, rate, out_block, self._playback, devices_mod.wasapi_settings(out_dev, hostapi)
                )
                self._in_stream.start()
                self._out_stream.start()
                self._measure_driver_latency()
                return rate
            except Exception as exc:
                errors.append(f"{rate} Hz — {exc}")
                self._close_streams()

        raise EngineError(
            "无法打开音频设备：\n"
            + "\n".join(errors[:4])
            + "\n请检查：VB-CABLE 是否安装、设备是否被其他程序独占、config.toml 里的 device_sample_rate。"
        )

    # ------------------------------------------------------------- 音频回调
    @staticmethod
    def _stream_latency_ms(stream) -> float:
        try:
            value = getattr(stream, "latency", 0.0)
            if isinstance(value, (tuple, list)):
                value = sum(float(item or 0.0) for item in value)
            return max(0.0, float(value) * 1000.0)
        except Exception:
            return 0.0

    def _measure_driver_latency(self) -> None:
        """PortAudio/驱动侧缓冲（采集 + 播放），这部分改不了，但应当算进估算里。"""
        total = self._stream_latency_ms(self._in_stream) + self._stream_latency_ms(self._out_stream)
        with self._metrics_lock:
            self._metrics.driver_ms = total

    def _enqueue_input(self, block: np.ndarray) -> None:
        item = (time.perf_counter(), block)  # 带时间戳，用于统计队列等待
        try:
            self._input_q.put_nowait(item)
            return
        except queue.Full:
            pass
        try:
            self._input_q.get_nowait()
        except queue.Empty:
            pass
        try:
            self._input_q.put_nowait(item)
        except queue.Full:
            return
        if not self._priming:
            with self._metrics_lock:
                self._metrics.dropped_in += 1

    def _capture(self, indata, frames, time_info, status) -> None:
        if status:
            log.warning("采集状态：%s", status)
        x = np.asarray(indata[:, 0], dtype=np.float32).copy()
        if x.size:
            peak = float(np.max(np.abs(x)))
            rms = float(np.sqrt(np.mean(x * x)))
            with self._metrics_lock:
                self._metrics.in_peak = peak
                self._metrics.in_rms = rms
        # 采集用小块（驱动缓冲小），在这里拼成 chunk_ms 的处理块再交给推理线程。
        pending = self._in_pending
        self._in_pending = x.copy() if pending.size == 0 else np.concatenate([pending, x])
        chunk = self.block
        while self._in_pending.size >= chunk:
            self._enqueue_input(self._in_pending[:chunk].copy())
            self._in_pending = self._in_pending[chunk:]

    def _playback(self, outdata, frames, time_info, status) -> None:
        if status:
            log.warning("播放状态：%s", status)
        available = self._ring.available()
        data = self._ring.read(frames)
        if available < frames and not self._priming:
            with self._metrics_lock:
                self._metrics.underruns += 1
        outdata[:, 0] = data
        if data.size:
            with self._metrics_lock:
                self._metrics.out_peak = float(np.max(np.abs(data)))
                self._metrics.out_rms = float(np.sqrt(np.mean(data * data)))

    # ----------------------------------------------------------------- 门控
    def _active_gate(self) -> SpectralGate | None:
        if not self.denoise_cfg.get("enabled", False):
            return None
        strength = float(self.denoise_cfg.get("strength", 0.35))
        floor_gain = float(self.denoise_cfg.get("floor_gain", 0.2))
        gate = self._gate
        if gate is None or abs(gate.strength - strength) > 1e-6:
            gate = SpectralGate(strength=strength, floor_gain=floor_gain, sample_rate=self.model_rate)
            self._gate = gate
        return gate

    # --------------------------------------------------------------- 工作线程
    def _collect_calibration(self) -> np.ndarray:
        seconds = int(self.model_cfg.get("calibrate_seconds", 3))
        need = seconds * self.model_rate
        self._set_state("calibrating", f"校准中：请连续说话 {seconds} 秒（此期间不外放）…")
        chunks: list[np.ndarray] = []
        total = 0
        deadline = time.monotonic() + seconds * 3.0 + 5.0
        while total < need:
            if self._stop.is_set():
                raise CalibrationError("校准已取消。")
            if time.monotonic() > deadline:
                raise CalibrationError("校准超时：没有收到音频输入，请检查麦克风是否被占用。")
            try:
                item = self._input_q.get(timeout=0.1)
            except queue.Empty:
                continue
            block = item[1] if isinstance(item, tuple) else item
            audio = self._in_resampler.process(block) if self._in_resampler else block
            if audio.size == 0:
                continue
            take = min(audio.size, need - total)
            chunks.append(audio[:take])
            total += take
        sample = np.concatenate(chunks)[:need].astype(np.float32)
        rms = float(np.sqrt(np.mean(sample * sample))) if sample.size else 0.0
        if rms < 5e-4:
            raise CalibrationError(
                f"校准失败：几乎没有声音（RMS={rms:.4f}）。请靠近麦克风连续说话 {seconds} 秒后重试。"
            )
        if float(np.max(np.abs(sample))) > 0.99:
            self._status("提示：校准样本接近满幅，可能会失真；建议离麦克风稍远一点。")
        return sample

    def _drain_input(self) -> None:
        """丢掉模型加载期间堆积的旧音频，避免用过期数据做注册。"""
        dropped = 0
        while True:
            try:
                self._input_q.get_nowait()
                dropped += 1
            except queue.Empty:
                break
        if dropped:
            with self._metrics_lock:
                self._metrics.dropped_in += dropped

    def _reset_ring_to_prefill(self) -> None:
        """把播放缓冲恢复到目标延迟：丢掉模型加载期间堆积的旧音频。"""
        self._ring.clear()
        if self._prefill_samples > 0:
            self._ring.write(np.zeros(self._prefill_samples, dtype=np.float32))

    def _run(self) -> None:
        adapter = None
        try:
            adapter = self._adapter_factory(self.cfg, self._status)
            self._adapter = adapter
            if adapter is not None:
                if self._mode == "calibrated":
                    sample = self._collect_calibration()
                    adapter.start_with_sample(sample)
                elif self._mode == "fixed":
                    from . import voiceprint

                    sample = voiceprint.load(self.model_cfg.get("voiceprint") or None)
                    adapter.start_with_sample(sample, label="固定声纹")
                    self._status("已载入固定声纹，直接锁定（无需重新识别）")
                else:
                    adapter.start_auto()
                self._adapter_name = adapter.description
                self._status(f"{adapter.description} 就绪")
            else:
                self._adapter_name = "直通"
                self._status("直通模式：只做音频路由（未加载 VoxLock 模型）")
            # 模型/校准都结束后再开始正式处理：丢掉过期音频，缓冲回到目标水位。
            self._drain_input()
            self._reset_ring_to_prefill()
            self._priming = False
            self._set_state("running")
            while not self._stop.is_set():
                self._pump()
            self._flush_tail()
        except CalibrationError as exc:
            log.warning("校准失败：%s", exc)
            self._fail(exc, state="idle")
        except ModelError as exc:
            log.error("模型错误：%s", exc)
            self._fail(exc)
        except Exception as exc:  # pragma: no cover - 防御
            log.exception("推理线程异常")
            self._fail(exc)
        finally:
            if adapter is not None:
                try:
                    adapter.close()
                except Exception:
                    log.debug("关闭模型失败", exc_info=True)
            self._adapter = None

    def _pump(self) -> None:
        try:
            item = self._input_q.get(timeout=0.1)
        except queue.Empty:
            return
        if isinstance(item, tuple):
            queued_at, block = item
            age_ms = (time.perf_counter() - queued_at) * 1000.0
            with self._metrics_lock:
                self._metrics.queue_ms = 0.8 * self._metrics.queue_ms + 0.2 * age_ms
        else:
            block = item
        started = time.perf_counter()
        audio = self._in_resampler.process(block) if self._in_resampler else block
        if audio.size == 0:
            return

        adapter = self._adapter
        if adapter is not None:
            processed = adapter.process(audio)
            if processed.size == 0:
                return
        else:
            processed = audio

        gate = self._active_gate()
        if gate is not None:
            processed = gate.process(processed)

        out = self._out_resampler.process(processed) if self._out_resampler else processed
        if out.size:
            dropped = self._ring.write(out)
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            with self._metrics_lock:
                self._metrics.blocks += 1
                self._metrics.infer_ms = 0.8 * self._metrics.infer_ms + 0.2 * elapsed_ms
                if dropped:
                    self._metrics.dropped_out += dropped

    def _flush_tail(self) -> None:
        adapter = self._adapter
        if adapter is None:
            return
        try:
            tail = adapter.flush()
        except Exception:
            log.debug("刷新模型尾部失败", exc_info=True)
            return
        if tail.size:
            out = self._out_resampler.process(tail) if self._out_resampler else tail
            if out.size:
                self._ring.write(out)

    def _fail(self, exc: BaseException, state: str = "error") -> None:
        self._set_state(state, f"错误：{exc}")
        self._close_streams()
        self._stop.set()

    # ------------------------------------------------------------------ 控制
    def start(self, input_device, output_device, mode: str | None = None) -> None:
        if self._worker is not None and self._worker.is_alive():
            raise EngineError("已经在运行，请先停止。")
        if self._worker is not None:
            raise EngineError("上一次停止还在收尾（模型可能在结束任务），请稍等再启动。")

        in_dev = self._as_device(input_device, "input")
        out_dev = self._as_device(output_device, "output")
        errors, warnings = devices_mod.validate_pair(in_dev, out_dev)
        for warning in warnings:
            log.warning("%s", warning)
            self._status(f"提示：{warning}")
        if errors:
            raise EngineError("\n".join(errors))

        self._mode = mode or str(self.model_cfg.get("mode", "auto"))
        self._stop.clear()
        self._priming = True
        self._in_pending = np.zeros(0, dtype=np.float32)
        self._gate = None
        self._adapter = None
        self._adapter_name = "off"
        with self._metrics_lock:
            self._metrics = _Metrics(
                state="starting",
                status="正在启动…",
                input_name=in_dev.name,
                output_name=out_dev.name,
            )

        rate = self._open_streams(in_dev, out_dev)
        self._input_q = queue.Queue(maxsize=int(self.audio_cfg["max_input_blocks"]))
        resampler_name = getattr(self._in_resampler, "backend", "")
        with self._metrics_lock:
            self._metrics.resampler = resampler_name
            self._metrics.device_rate = rate

        # 预填充目标延迟，避免启动阶段欠载导致的断续。
        prefill = int(rate * int(self.audio_cfg["target_latency_ms"]) / 1000)
        self._prefill_samples = max(0, prefill)
        if prefill > 0:
            self._ring.write(np.zeros(prefill, dtype=np.float32))

        self._set_state(
            "starting",
            f"音频设备已打开（{rate} Hz，采集 {self.chunk_ms} ms / 播放 {self.playback_block_ms} ms），正在准备模型…",
        )
        self._worker = threading.Thread(target=self._run, name="VoxCallWorker", daemon=True)
        self._worker.start()

    def stop(self, timeout: float = 8.0) -> None:
        self._stop.set()
        self._priming = True
        self._close_streams()
        worker = self._worker
        if worker is not None and worker.is_alive() and worker is not threading.current_thread():
            worker.join(timeout=timeout)
        if worker is not None and worker.is_alive():
            # 模型仍在下/加载，无法中断；保留引用，start() 会拒绝重复启动。
            self._set_state("stopping", "后台仍在结束模型任务，完成后会自动释放。")
            return
        self._worker = None
        self._adapter = None
        self._adapter_name = "off"
        self._gate = None
        self._ring.clear()
        self._set_state("idle", "已停止")
