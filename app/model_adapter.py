"""VoxLock 适配层。

关键行为（与旧版的区别）：
- 是否出声由 VoxLock 自己的锁定状态 `LiveStream.locked` 决定，而不是"启动计时 5 秒"。
  模型没锁定时（还没攒够目标语音）返回等长静音，杜绝对外来声音的直通泄漏。
- 支持两种模式：
    auto        —— `live()` 自动识别目标说话人（锁定前静音）；
    calibrated  —— 用界面上录到的 3 秒目标语音 `stream(sample)`，立即锁定，无等待。
- voxlock 未安装/加载失败时不把异常抛到界面线程之外：返回 None 让引擎降级为直通模式，
  同时给出可读的原因（Python 版本、onnxruntime、网络等）。
"""

from __future__ import annotations

import logging
import threading
import time

import numpy as np

log = logging.getLogger(__name__)

MODEL_LATENCY_MS = 24.0
MIN_ENROLL_SECONDS = 3.0


class ModelError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# 进程内模型会话缓存：ONNX 初始化要 3.5–4.5 s，停止后再开始不该重来一遍。
# 只缓存"会话"（enroll/chunk 两个 InferenceSession），每次开始仍会新建 stream，
# 因为 LSTM 状态属于 stream，不能复用。
# ---------------------------------------------------------------------------
_cache_lock = threading.Lock()
_cache: tuple[tuple, object] | None = None  # (key, VoxLock)


def _cache_key(model_dir: str, precision: str, threads: int) -> tuple:
    return (str(model_dir or ""), str(precision), int(threads))


def clear_model_cache() -> None:
    global _cache
    with _cache_lock:
        _cache = None


def cached_model_info() -> dict | None:
    with _cache_lock:
        if _cache is None:
            return None
        key = _cache[0]
        return {"model_dir": key[0], "precision": key[1], "threads": key[2]}


def drop_model_from_cache(model: object) -> None:
    global _cache
    with _cache_lock:
        if _cache is not None and _cache[1] is model:
            _cache = None


def _get_or_build(key: tuple, factory, on_note=None) -> tuple[object, bool]:
    """返回 (模型, 是否命中缓存)。factory 在缓存未命中时被调用一次。"""
    global _cache
    with _cache_lock:
        if _cache is not None and _cache[0] == key:
            log.info("复用已加载的模型会话：%s", key)
            return _cache[1], True
        if _cache is not None:
            log.info("模型参数变化，丢弃旧会话：%s -> %s", _cache[0], key)
            _cache = None
        started = time.perf_counter()
        model = factory()
        log.info("模型会话加载完成，用时 %.1fs", time.perf_counter() - started)
        _cache = (key, model)
        if on_note:
            on_note(f"模型已加载（{time.perf_counter() - started:.1f}s）")
        return model, False


def voxlock_status() -> tuple[bool, str]:
    """检查 voxlock 是否可用，返回 (可用, 说明)。"""
    try:
        import voxlock  # noqa: F401
    except ImportError as exc:
        return False, (
            f"未安装 VoxLock（{exc}）。需要 Python 3.11–3.13 + onnxruntime；"
            "请运行 install.ps1 或 pip install voxlock。当前程序将以直通模式运行。"
        )
    except Exception as exc:  # pragma: no cover - 依赖二进制异常
        return False, f"VoxLock 导入失败：{exc}。当前程序将以直通模式运行。"
    try:
        import onnxruntime  # noqa: F401
    except ImportError as exc:  # pragma: no cover
        return False, f"缺少 onnxruntime（{exc}）。请 pip install onnxruntime。当前程序将以直通模式运行。"
    return True, "VoxLock 可用"


class VoxLockAdapter:
    name = "VoxLock"

    def __init__(
        self,
        threads: int = 4,
        precision: str = "int8",
        sample_rate: int = 16000,
        model_dir: str = "",
        on_note=None,
    ) -> None:
        self.threads = int(threads)
        self.precision = str(precision)
        self.sample_rate = int(sample_rate)
        self.model_dir = str(model_dir or "")
        self.on_note = on_note or (lambda message: None)
        self.model = None
        self.stream = None
        self.mode = "off"
        self._locked = False
        self._label = ""

    # ---- 状态 -------------------------------------------------------
    @property
    def locked(self) -> bool:
        if self.mode == "calibrated":
            return self._locked
        stream = self.stream
        if stream is None:
            return False
        try:
            return bool(getattr(stream, "locked", False))
        except Exception:  # pragma: no cover
            return self._locked

    @property
    def latency_ms(self) -> float:
        return MODEL_LATENCY_MS

    @property
    def description(self) -> str:
        if self._label:
            return f"VoxLock {self.precision} · {self._label}"
        mode = "校准样本" if self.mode == "calibrated" else "自动识别"
        return f"VoxLock {self.precision} · {mode}"

    # ---- 生命周期 ---------------------------------------------------
    def _load(self) -> None:
        import voxlock

        def build(precision: str):
            kwargs = {"threads": self.threads, "precision": precision}
            if self.model_dir:
                kwargs["model_dir"] = self.model_dir
            try:
                return voxlock.VoxLock(**kwargs)
            except TypeError:
                # 兼容 1.0.x：没有 model_dir 参数
                return voxlock.VoxLock(threads=self.threads, precision=precision)

        def factory():
            try:
                return build(self.precision)
            except Exception as exc:
                if self.precision != "int8":
                    log.warning("%s 模型加载失败，回退到 int8：%s", self.precision, exc)
                    self.on_note(f"{self.precision} 模型不可用，已回退到 int8")
                    self.precision = "int8"
                    try:
                        return build("int8")
                    except Exception as exc2:
                        raise ModelError(self._explain(exc2)) from exc2
                raise ModelError(self._explain(exc)) from exc

        key = _cache_key(self.model_dir, self.precision, self.threads)
        try:
            self.model, hit = _get_or_build(key, factory, self.on_note)
        except ModelError:
            raise
        except Exception as exc:  # pragma: no cover - 防御
            raise ModelError(self._explain(exc)) from exc
        if hit:
            self.on_note("模型会话已复用（无需重新加载）")

    @staticmethod
    def _explain(exc: BaseException) -> str:
        text = str(exc) or exc.__class__.__name__
        lowered = text.lower()
        if any(key in lowered for key in ("connection", "timed out", "ssl", "resolve", "http")):
            return (
                f"模型文件下载失败：{text}\n"
                "首次运行需要从 Hugging Face 下载约 75 MB 模型。国内网络可先设置环境变量 "
                "HF_ENDPOINT=https://hf-mirror.com 再启动。"
            )
        if "model_dir" in lowered or "onnx" in lowered:
            return f"模型加载失败：{text}\n可在 config.toml 里用 model.model_dir 指向本地 onnx 目录。"
        return f"模型初始化失败：{text}"

    def start_auto(self, sec: float = MIN_ENROLL_SECONDS) -> None:
        if self.model is None:
            self._load()
        self.mode = "auto"
        try:
            self.stream = self.model.live(sec=max(MIN_ENROLL_SECONDS, float(sec)))
        except Exception as exc:
            raise ModelError(self._explain(exc)) from exc
        self._locked = False
        self.on_note("模型已加载，正在等待目标语音（此期间输出静音）")

    def start_with_sample(self, sample: np.ndarray, label: str = "校准样本") -> None:
        if self.model is None:
            self._load()
        sample = np.asarray(sample, dtype=np.float32).reshape(-1)
        need = int(MIN_ENROLL_SECONDS * self.sample_rate)
        if sample.size < need:
            raise ModelError(f"校准样本太短：{sample.size / self.sample_rate:.2f} 秒，至少需要 3 秒。")
        self.mode = "calibrated"
        self._label = label
        try:
            # 传完整样本：VoxLock 会自己取其中最响的 3 秒，比截断前 3 秒更稳。
            self.stream = self.model.stream(sample)
        except Exception as exc:
            raise ModelError(self._explain(exc)) from exc
        self._locked = True
        self.on_note(f"{label}已就绪，直接锁定目标说话人")

    # ---- 推理 -------------------------------------------------------
    def process(self, audio_16k: np.ndarray) -> np.ndarray:
        x = np.asarray(audio_16k, dtype=np.float32).reshape(-1)
        if self.stream is None:
            return np.zeros_like(x)
        # 必须始终 push：模型要靠这些音频完成"目标说话人"注册。
        try:
            y = np.asarray(self.stream.push(x), dtype=np.float32).reshape(-1)
        except Exception as exc:
            # 会话坏了就别再复用缓存，下次开始重新加载
            drop_model_from_cache(self.model)
            raise ModelError(self._explain(exc)) from exc
        if not self.locked:
            # 未锁定期间模型输出是原样透传，必须丢弃，否则别人的声音会直接进通话。
            return np.zeros_like(x)
        return y

    def flush(self) -> np.ndarray:
        stream = self.stream
        self.stream = None
        if stream is None:
            return np.zeros(0, dtype=np.float32)
        try:
            return np.asarray(stream.flush(), dtype=np.float32).reshape(-1)
        except Exception:  # pragma: no cover
            log.exception("VoxLock flush 失败")
            return np.zeros(0, dtype=np.float32)

    def close(self) -> None:
        self.flush()
        self.model = None
        self._locked = False
        self.mode = "off"


def bundled_model_dir() -> str:
    """程序目录下自带的模型（models/onnx），有就直接用，免下载。

    打包成 exe 后：先在 exe 同级目录找，再找 PyInstaller 解包目录。
    """
    from .paths import ROOT, bundle_dir

    chunks = ("chunk_int8_full.onnx", "chunk_int8.onnx", "chunk_fp16.onnx", "chunk.onnx")
    for base in (ROOT / "models" / "onnx", bundle_dir() / "models" / "onnx"):
        if not (base / "enroll.onnx").exists() and not (base / "enroll_fp16.onnx").exists():
            continue
        if any((base / name).exists() for name in chunks):
            return str(base)
    return ""


def create_adapter(config: dict, on_note=None) -> VoxLockAdapter | None:
    """按配置创建适配器；不可用时返回 None（调用方降级为直通）。"""
    model_cfg = config["model"]
    mode = str(model_cfg.get("mode", "auto"))
    if mode == "off":
        return None
    available, reason = voxlock_status()
    if not available:
        if model_cfg.get("allow_passthrough", True):
            if on_note:
                on_note(reason)
            return None
        raise ModelError(reason)
    model_dir = str(model_cfg.get("model_dir", "") or "") or bundled_model_dir()
    return VoxLockAdapter(
        threads=int(model_cfg.get("threads", 4)),
        precision=str(model_cfg.get("precision", "int8")),
        sample_rate=int(config["audio"].get("model_sample_rate", 16000)),
        model_dir=model_dir,
        on_note=on_note,
    )
