"""流式重采样：优先 soxr，缺失时使用内置多相 FIR（无第三方依赖也能跑）。

soxr 质量更好；内置实现用于环境缺包、离线自检和测试，两者接口一致：
    r = StreamResampler(48000, 16000)
    y = r.process(x)      # float32 一维
"""

from __future__ import annotations

import logging
from math import gcd

import numpy as np

log = logging.getLogger(__name__)

try:  # pragma: no cover - 取决于环境
    import soxr  # type: ignore

    _HAS_SOXR = True
except Exception:  # pragma: no cover
    soxr = None  # type: ignore
    _HAS_SOXR = False


def _design_lowpass(taps: int, cutoff: float) -> np.ndarray:
    """Kaiser 窗 sinc 低通。cutoff 为归一化频率（相对输入采样率，0~0.5）。"""
    n = np.arange(taps) - (taps - 1) / 2.0
    h = 2.0 * cutoff * np.sinc(2.0 * cutoff * n)
    beta = 8.0
    window = np.kaiser(taps, beta)
    h = h * window
    total = h.sum()
    return (h / total).astype(np.float32) if total else h.astype(np.float32)


class _PolyphaseResampler:
    """整数比多相 FIR 重采样（L 倍插值后 M 倍抽取）。"""

    MAX_BLOCK = 8192

    def __init__(self, in_rate: int, out_rate: int, taps_per_phase: int = 32) -> None:
        g = gcd(int(in_rate), int(out_rate))
        self.L = int(out_rate) // g
        self.M = int(in_rate) // g
        self.T = int(taps_per_phase)
        taps = self.T * self.L
        # 滤波器工作在"插值后"的速率上：截止频率要按 L 归一化，否则会留下镜像/混叠。
        cutoff = 0.45 * min(1.0, self.L / self.M) / self.L
        h = _design_lowpass(taps, cutoff)
        # 插值后幅度会掉 L 倍，补偿回来。
        h = (h * self.L).astype(np.float32)
        self.kernels = np.stack([h[p:: self.L][: self.T] for p in range(self.L)]).astype(np.float32)
        self._buf = np.zeros(self.T, dtype=np.float32)   # 左侧补 T 个 0，保证窗口索引始终非负
        self._n_in = 0
        self._m = 0

    def _emit(self, n_total: int) -> np.ndarray:
        m_end = (n_total * self.L) // self.M
        if m_end <= self._m:
            return np.zeros(0, dtype=np.float32)
        m = np.arange(self._m, m_end, dtype=np.int64)
        positions = m * self.M
        q = positions // self.L
        p = positions % self.L
        # _buf 的最后一个样本是全局第 n_total-1 个；把 q 换算成 buf 内的窗口起点。
        start = self._buf.size - self.T - (n_total - 1 - q)
        windows = np.lib.stride_tricks.sliding_window_view(self._buf, self.T)[start]
        out = np.empty(len(m), dtype=np.float32)
        for phase in range(self.L):
            sel = np.nonzero(p == phase)[0]
            if sel.size:
                out[sel] = windows[sel] @ self.kernels[phase]
        self._m = int(m_end)
        return out

    def process(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=np.float32).reshape(-1)
        if x.size == 0:
            return np.zeros(0, dtype=np.float32)
        self._buf = np.concatenate([self._buf, x])
        self._n_in += x.size
        out = self._emit(self._n_in)
        keep = self.T + max(self.MAX_BLOCK, x.size)
        if self._buf.size > keep:
            self._buf = self._buf[-keep:]
        return out

    def flush(self) -> np.ndarray:
        pad = np.zeros(self.T + 16, dtype=np.float32)
        return self.process(pad)


class StreamResampler:
    """带状态的流式重采样器；相同采样率时是零成本直通。"""

    def __init__(self, in_rate: int, out_rate: int, quality: str = "HQ", prefer: str = "auto") -> None:
        self.in_rate = int(in_rate)
        self.out_rate = int(out_rate)
        self.backend = "passthrough"
        self._soxr = None
        self._fir: _PolyphaseResampler | None = None
        if self.in_rate == self.out_rate:
            return
        if _HAS_SOXR and prefer in {"auto", "soxr"}:
            try:
                self._soxr = soxr.ResampleStream(self.in_rate, self.out_rate, 1, dtype="float32", quality=quality)
                self.backend = "soxr"
                return
            except Exception:  # pragma: no cover
                log.warning("soxr 初始化失败，回退到内置 FIR 重采样", exc_info=True)
        if prefer == "soxr":
            log.warning("要求 soxr 但不可用，回退到内置 FIR 重采样")
        self._fir = _PolyphaseResampler(self.in_rate, self.out_rate)
        self.backend = "fir"

    def process(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=np.float32).reshape(-1)
        if self._soxr is not None:
            return np.asarray(self._soxr.resample_chunk(x, last=False), dtype=np.float32).reshape(-1)
        if self._fir is not None:
            return self._fir.process(x)
        return x.astype(np.float32, copy=False)

    def flush(self) -> np.ndarray:
        if self._soxr is not None:
            return np.asarray(self._soxr.resample_chunk(np.zeros(0, np.float32), last=True), dtype=np.float32).reshape(-1)
        if self._fir is not None:
            return self._fir.flush()
        return np.zeros(0, dtype=np.float32)
