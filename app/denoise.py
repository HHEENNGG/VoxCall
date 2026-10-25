"""轻量频谱门（WOLA 重叠相加）。

与旧版的关键区别：
1. 分析窗/合成窗均为 sqrt-Hann，50% 重叠时严格满足 COLA：增益为 1 时逐样本还原；
2. 内部有 N-hop 的算法延迟并用 FIFO 对齐，输出长度始终等于输入长度；
3. 噪声底只在"当前帧不比参考能量高很多"时缓慢抬升，避免语音把底噪抬高后自锁。

它不是神经网络降噪器：只能压稳态底噪（风扇/空调/电流声），
对键盘、关门等瞬态噪声作用有限，过强会产生"水声"伪影。
"""

from __future__ import annotations

import numpy as np


class SpectralGate:
    def __init__(
        self,
        strength: float = 0.35,
        floor_gain: float = 0.2,
        sample_rate: int = 16000,
        n_fft: int = 512,
        hop: int | None = None,
    ) -> None:
        self.sample_rate = int(sample_rate)
        self.n_fft = int(n_fft)
        self.hop = int(hop or n_fft // 2)
        self.strength = float(np.clip(strength, 0.0, 1.0))
        self.floor_gain = float(np.clip(floor_gain, 0.0, 1.0))
        self.hist_frames = max(8, int(round(1.0 * self.sample_rate / self.hop)))

        window = 0.5 - 0.5 * np.cos(2.0 * np.pi * np.arange(self.n_fft) / self.n_fft)
        self.window = np.sqrt(window).astype(np.float32)  # sqrt-Hann 满足 COLA

        self._floor: np.ndarray | None = None
        self._hist: np.ndarray | None = None   # 最近约 1 秒的幅度谱，用于稳健估计噪声底
        self._hist_pos = 0
        self._hist_count = 0
        self._gain = np.ones(self.n_fft // 2 + 1, dtype=np.float32)
        self._ola = np.zeros(self.n_fft, dtype=np.float32)
        self._pending = np.zeros(0, dtype=np.float32)
        self._ready: list[np.ndarray] = []

    @property
    def latency_samples(self) -> int:
        return self.n_fft - self.hop

    def reset(self) -> None:
        self._floor = None
        self._hist = None
        self._hist_pos = 0
        self._hist_count = 0
        self._gain = np.ones(self.n_fft // 2 + 1, dtype=np.float32)
        self._ola = np.zeros(self.n_fft, dtype=np.float32)
        self._pending = np.zeros(0, dtype=np.float32)
        self._ready = []

    def _ready_len(self) -> int:
        return sum(chunk.size for chunk in self._ready)

    def _noise_floor(self, mag: np.ndarray) -> np.ndarray:
        """用最近约 1 秒幅度谱的中位数估计噪声底。

        比逐帧取最小值稳健得多：最小统计会被 Rayleigh 分布的深衰落拖到接近 0，
        导致门几乎不衰减；中位数对短时语音尖峰不敏感，又能跟上噪声电平变化。
        """
        bins = mag.size
        if self._hist is None:
            self._hist = np.zeros((self.hist_frames, bins), dtype=np.float32)
        self._hist[self._hist_pos] = mag
        self._hist_pos = (self._hist_pos + 1) % self.hist_frames
        self._hist_count = min(self.hist_frames, self._hist_count + 1)
        count = self._hist_count
        view = self._hist[:count] if count < self.hist_frames else self._hist
        return np.median(view, axis=0).astype(np.float32)

    def _process_frame(self, frame: np.ndarray) -> None:
        spec = np.fft.rfft(frame * self.window, n=self.n_fft)
        mag = np.abs(spec).astype(np.float32)
        floor = self._noise_floor(mag)

        # 过减阈值：strength 越大，判定为噪声的门槛越高、衰减越深。
        oversub = 1.0 + 2.0 * self.strength
        snr = (mag / np.maximum(floor * oversub, 1e-9)) ** 2
        gain = snr / (1.0 + snr)                        # 维纳式软掩蔽
        gain = np.maximum(gain, self.floor_gain)        # 残留噪声下限
        gain = gain ** (1.0 + self.strength)            # strength 控制衰减深度

        # 时域平滑避免"水声"，再做 3 点频域平滑抑制音乐噪声。
        self._gain = 0.5 * self._gain + 0.5 * gain.astype(np.float32)
        smoothed = self._gain.copy()
        smoothed[1:-1] = 0.25 * self._gain[:-2] + 0.5 * self._gain[1:-1] + 0.25 * self._gain[2:]

        out = np.fft.irfft(spec * smoothed, n=self.n_fft).astype(np.float32) * self.window
        self._ola += out
        self._ready.append(self._ola[: self.hop].copy())
        self._ola = np.concatenate([self._ola[self.hop :], np.zeros(self.hop, dtype=np.float32)])

    def process(self, audio: np.ndarray) -> np.ndarray:
        x = np.asarray(audio, dtype=np.float32).reshape(-1)
        n = x.size
        if n == 0:
            return x
        if self.strength <= 0.0:
            return x  # 旁路：不改变任何内部状态

        self._pending = np.concatenate([self._pending, x])
        while self._pending.size >= self.n_fft:
            self._process_frame(self._pending[: self.n_fft])
            self._pending = self._pending[self.hop :]

        missing = n - self._ready_len()
        if missing > 0:
            # 启动阶段算法延迟尚未填满：补零，保证输出长度与输入严格一致。
            self._ready.insert(0, np.zeros(missing, dtype=np.float32))

        parts: list[np.ndarray] = []
        need = n
        while need > 0 and self._ready:
            head = self._ready[0]
            take = min(need, head.size)
            parts.append(head[:take])
            if take == head.size:
                self._ready.pop(0)
            else:
                self._ready[0] = head[take:]
            need -= take
        if need > 0:
            parts.append(np.zeros(need, dtype=np.float32))
        return np.concatenate(parts).astype(np.float32, copy=False)
