import unittest

import numpy as np

from app.denoise import SpectralGate

SR = 16000
BLOCK = 1280


def rms(x) -> float:
    return float(np.sqrt(np.mean(np.asarray(x, dtype=np.float64) ** 2)))


def run(gate: SpectralGate, x: np.ndarray) -> np.ndarray:
    parts = [gate.process(x[i : i + BLOCK]) for i in range(0, x.size, BLOCK)]
    return np.concatenate(parts)


def speech_like(seconds: float, rng: np.random.Generator) -> np.ndarray:
    """30% 占空比的断续"语音"，用来验证门不会把正常说话压掉。"""
    t = np.arange(int(SR * seconds)) / SR
    gate = (np.sin(2 * np.pi * 3.0 * t) > 0.6).astype(np.float32)
    carrier = sum(np.sin(2 * np.pi * 180 * k * t) / k for k in (1, 2, 3))
    return (0.3 * gate * carrier).astype(np.float32)


class DenoiseTests(unittest.TestCase):
    def test_bypass_when_strength_is_zero(self):
        gate = SpectralGate(strength=0.0)
        x = speech_like(0.5, np.random.default_rng(0))
        np.testing.assert_allclose(run(gate, x), x, rtol=0, atol=0)

    def test_length_and_dtype_preserved(self):
        gate = SpectralGate(strength=0.4)
        rng = np.random.default_rng(1)
        for size in (1, 17, 256, 1280, 5000):
            x = (0.1 * rng.standard_normal(size)).astype(np.float32)
            y = gate.process(x)
            self.assertEqual(y.size, size)
            self.assertEqual(y.dtype, np.float32)

    def test_steady_noise_is_attenuated(self):
        gate = SpectralGate(strength=0.5)
        rng = np.random.default_rng(2)
        noise = (0.01 * rng.standard_normal(SR * 3)).astype(np.float32)
        out = run(gate, noise)
        # 跳过 1 秒收敛期
        reduction_db = 20 * np.log10(max(rms(out[SR:]), 1e-12) / max(rms(noise[SR:]), 1e-12))
        self.assertLess(reduction_db, -6.0, f"稳态噪声只降了 {reduction_db:.1f} dB")

    def test_speech_is_preserved(self):
        gate = SpectralGate(strength=0.5)
        rng = np.random.default_rng(3)
        speech = speech_like(2.0, rng)
        out = run(gate, speech)
        ratio_db = 20 * np.log10(max(rms(out[SR:]), 1e-12) / max(rms(speech[SR:]), 1e-12))
        self.assertGreater(ratio_db, -3.0, f"语音被压掉太多：{ratio_db:.1f} dB")
        self.assertLess(ratio_db, 1.5, f"语音被异常放大：{ratio_db:.1f} dB")

    def test_no_periodic_dropouts(self):
        """旧实现每个块边界幅度归零；WOLA 之后逐跳 RMS 应基本平稳。"""
        gate = SpectralGate(strength=0.5)
        t = np.arange(SR * 2) / SR
        tone = (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
        out = run(gate, tone)
        body = out[SR:]
        hop = gate.hop
        blocks = [rms(body[i : i + hop]) for i in range(0, body.size - hop, hop)]
        mean = float(np.mean(blocks))
        spread = float(np.std(blocks) / mean)
        self.assertGreater(mean, 0.005)
        self.assertLess(spread, 0.2, f"输出存在周期性起伏：{spread:.2f}")


if __name__ == "__main__":
    unittest.main()
