import unittest

import numpy as np

from app import model_adapter, simulate
from app.model_adapter import ModelError, VoxLockAdapter

SR = 16000
BLOCK = 1280


def rms(x) -> float:
    return float(np.sqrt(np.mean(np.asarray(x, dtype=np.float64) ** 2)))


class ModelCacheTests(unittest.TestCase):
    """进程内常驻：停止后再开始不该重新初始化 ONNX 会话。"""

    def setUp(self):
        model_adapter.clear_model_cache()
        simulate._FakeVoxLock.instances.clear()

    def tearDown(self):
        model_adapter.clear_model_cache()

    def test_sessions_reused_across_starts(self):
        with simulate.fake_voxlock():
            first = VoxLockAdapter(precision="int8", sample_rate=SR)
            first.start_auto()
            model1 = first.model
            first.close()

            second = VoxLockAdapter(precision="int8", sample_rate=SR)
            second.start_auto()
            model2 = second.model
            second.close()
        self.assertIs(model1, model2)
        self.assertEqual(len(simulate._FakeVoxLock.instances), 1, "模型被重复初始化了")

    def test_different_settings_build_new_session(self):
        with simulate.fake_voxlock():
            a = VoxLockAdapter(precision="int8", sample_rate=SR)
            a.start_auto()
            a.close()
            b = VoxLockAdapter(precision="int8-full", sample_rate=SR)
            b.start_auto()
            b.close()
        self.assertEqual(len(simulate._FakeVoxLock.instances), 2)

    def test_clear_cache_forces_reload(self):
        with simulate.fake_voxlock():
            a = VoxLockAdapter(precision="int8", sample_rate=SR)
            a.start_auto()
            a.close()
            model_adapter.clear_model_cache()
            b = VoxLockAdapter(precision="int8", sample_rate=SR)
            b.start_auto()
            b.close()
        self.assertEqual(len(simulate._FakeVoxLock.instances), 2)

    def test_each_start_gets_fresh_stream(self):
        """会话复用，但 stream（LSTM 状态）必须每次新建。"""
        scene, _ = simulate.make_scene(rate=SR, seconds=6.0)
        sample = scene[SR : SR * 4]
        with simulate.fake_voxlock():
            a = VoxLockAdapter(precision="int8", sample_rate=SR)
            a.start_with_sample(sample)
            stream1 = a.stream
            a.close()
            b = VoxLockAdapter(precision="int8", sample_rate=SR)
            b.start_with_sample(sample)
            stream2 = b.stream
            b.close()
        self.assertIsNot(stream1, stream2)


class AdapterTests(unittest.TestCase):
    def test_muted_until_locked_and_no_leak(self):
        """未锁定时必须输出等长静音，即使底层 LiveStream 正在原样透传。"""
        scene, _ = simulate.make_scene(rate=SR, seconds=8.0)
        with simulate.fake_voxlock():
            adapter = VoxLockAdapter(precision="int8", sample_rate=SR)
            adapter.start_auto()
            early = []
            for i in range(0, int(3.5 * SR), BLOCK):
                early.append(adapter.process(scene[i : i + BLOCK]))
            self.assertFalse(adapter.locked)
            for chunk in early:
                self.assertEqual(chunk.size, BLOCK)
                self.assertLess(float(np.max(np.abs(chunk))), 1e-9)

            later = []
            for i in range(int(3.5 * SR), int(7.0 * SR), BLOCK):
                later.append(adapter.process(scene[i : i + BLOCK]))
            self.assertTrue(adapter.locked, "喂了足够语音后仍未锁定")
            self.assertGreater(rms(np.concatenate(later)), 0.001)

    def test_push_still_happens_while_muted(self):
        """静音期间不能停止 push，否则模型永远学不到目标说话人。"""
        scene, _ = simulate.make_scene(rate=SR, seconds=4.0)
        with simulate.fake_voxlock():
            adapter = VoxLockAdapter(precision="int8", sample_rate=SR)
            adapter.start_auto()
            for i in range(0, int(2.0 * SR), BLOCK):
                adapter.process(scene[i : i + BLOCK])
            self.assertGreater(adapter.stream.voiced_frames(), 0)

    def test_calibrated_mode_locks_immediately(self):
        scene, _ = simulate.make_scene(rate=SR, seconds=6.0)
        sample = scene[int(1.0 * SR) : int(4.0 * SR)]
        with simulate.fake_voxlock():
            adapter = VoxLockAdapter(precision="int8", sample_rate=SR)
            adapter.start_with_sample(sample)
            self.assertTrue(adapter.locked)
            y = adapter.process(scene[int(4.0 * SR) : int(4.0 * SR) + BLOCK])
            self.assertEqual(y.size, BLOCK)
            self.assertGreater(rms(y), 0.001)

    def test_short_sample_rejected(self):
        with simulate.fake_voxlock():
            adapter = VoxLockAdapter(precision="int8", sample_rate=SR)
            with self.assertRaises(ModelError):
                adapter.start_with_sample(np.zeros(SR, dtype=np.float32))


if __name__ == "__main__":
    unittest.main()
