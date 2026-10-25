import unittest

import numpy as np

from app.resample import StreamResampler


def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.asarray(x, dtype=np.float64) ** 2)))


def tone(freq: float, rate: int, seconds: float, amp: float = 0.5) -> np.ndarray:
    t = np.arange(int(rate * seconds)) / rate
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


class ResampleTests(unittest.TestCase):
    """长度/相位这类精确断言固定用内置 FIR 后端；另有一条用例覆盖 auto（可能走 soxr）。"""

    def _run(self, resampler: StreamResampler, x: np.ndarray, block: int = 3840) -> np.ndarray:
        out = [resampler.process(x[i : i + block]) for i in range(0, x.size, block)]
        return np.concatenate([np.asarray(c, dtype=np.float32) for c in out])

    def test_downsample_length_and_tone(self):
        r = StreamResampler(48000, 16000, prefer="fir")
        x = tone(1000.0, 48000, 1.0)
        y = self._run(r, x)
        self.assertEqual(y.size, 16000)
        self.assertEqual(y.dtype, np.float32)
        body = y[2000:]
        self.assertAlmostEqual(rms(body) / rms(x[6000:]), 1.0, delta=0.05)
        spectrum = np.abs(np.fft.rfft(body * np.hanning(body.size)))
        freqs = np.fft.rfftfreq(body.size, 1 / 16000)
        self.assertAlmostEqual(float(freqs[int(np.argmax(spectrum))]), 1000.0, delta=8.0)

    def test_upsample_length(self):
        r = StreamResampler(16000, 48000, prefer="fir")
        x = tone(1000.0, 16000, 0.5)
        y = self._run(r, x, block=1280)
        self.assertAlmostEqual(y.size / x.size, 3.0, delta=0.01)
        body = y[2000:]
        self.assertAlmostEqual(rms(body) / rms(x[1000:]), 1.0, delta=0.1)

    def test_antialias_suppression(self):
        """10 kHz 超过 16 kHz 的奈奎斯特频率，必须被抗混叠滤掉。"""
        r = StreamResampler(48000, 16000, prefer="fir")
        x = tone(10000.0, 48000, 1.0)
        y = self._run(r, x)
        body = y[4000:]
        attenuation_db = 20 * np.log10(max(rms(body), 1e-12) / max(rms(x[12000:]), 1e-12))
        self.assertLess(attenuation_db, -20.0, f"抗混叠不足：{attenuation_db:.1f} dB")

    def test_auto_backend_keeps_level_and_pitch(self):
        """auto 后端（有 soxr 用 soxr，没有用 FIR）也要音量/音高正确；流式延迟由 flush 补回。"""
        r = StreamResampler(48000, 16000)
        x = tone(1000.0, 48000, 1.0)
        y = np.concatenate([self._run(r, x), np.asarray(r.flush(), dtype=np.float32)])
        self.assertIn(r.backend, {"soxr", "fir"})
        self.assertLess(abs(y.size - 16000), 400, f"{r.backend} 输出长度异常：{y.size}")
        body = y[3000:13000]
        self.assertAlmostEqual(rms(body) / rms(x[6000:10000]), 1.0, delta=0.1)
        spectrum = np.abs(np.fft.rfft(body * np.hanning(body.size)))
        freqs = np.fft.rfftfreq(body.size, 1 / 16000)
        self.assertAlmostEqual(float(freqs[int(np.argmax(spectrum))]), 1000.0, delta=20.0)

    def test_same_rate_is_passthrough(self):
        r = StreamResampler(16000, 16000)
        x = tone(440.0, 16000, 0.2)
        y = r.process(x)
        np.testing.assert_allclose(y, x, rtol=0, atol=0)

    def test_no_nan_and_continuous_state(self):
        r = StreamResampler(48000, 16000)
        y = np.concatenate([r.process(np.zeros(37, dtype=np.float32)) for _ in range(50)])
        self.assertTrue(np.all(np.isfinite(y)))


if __name__ == "__main__":
    unittest.main()
