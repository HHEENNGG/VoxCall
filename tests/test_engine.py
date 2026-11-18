import threading
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from app import settings, simulate
from app.devices import DeviceInfo, validate_pair
from app.engine import AudioEngine, EngineError
from app.model_adapter import ModelError

SR = 48000


def rms(x) -> float:
    return float(np.sqrt(np.mean(np.asarray(x, dtype=np.float64) ** 2)))


def dev(index: int, name: str, kind: str) -> DeviceInfo:
    return DeviceInfo(
        index=index,
        name=name,
        hostapi=0,
        hostapi_name="test",
        max_input=1 if kind == "input" else 0,
        max_output=0 if kind == "input" else 1,
        default_samplerate=48000.0,
    )


class DeviceValidationTests(unittest.TestCase):
    def test_same_device_rejected(self):
        mic = dev(1, "Microphone", "input")
        errors, _ = validate_pair(mic, mic)
        self.assertTrue(errors)

    def test_non_cable_output_is_only_a_warning(self):
        errors, warnings = validate_pair(dev(1, "Microphone", "input"), dev(2, "Speakers", "output"))
        self.assertFalse(errors)
        self.assertTrue(any("CABLE" in w for w in warnings))

    def test_cable_output_as_speaker_rejected(self):
        errors, _ = validate_pair(dev(1, "Microphone", "input"), dev(3, "CABLE Output", "output"))
        self.assertTrue(errors)

    def test_loopback_mic_warns(self):
        _, warnings = validate_pair(dev(4, "CABLE Output", "input"), dev(5, "CABLE Input", "output"))
        self.assertTrue(any("回环" in w for w in warnings))


class EngineTests(unittest.TestCase):
    def test_start_with_same_device_raises(self):
        engine = AudioEngine(settings.normalize({}))
        mic = dev(1, "Microphone", "input")
        with self.assertRaises(EngineError):
            engine.start(mic, mic)
        engine.stop()

    def test_auto_mode_pipeline(self):
        result = simulate.run(mode="auto", seconds=12.0, speed=6.0)
        metrics = result.metrics
        out = result.output

        self.assertTrue(np.all(np.isfinite(out)))
        self.assertIsNotNone(result.locked_at, "自动模式始终没有锁定")
        # 锁定前绝不能有声音（旧实现会在这里泄漏原始麦克风）
        self.assertLess(result.peak_before_lock, 1e-4)
        self.assertLess(rms(out[: int(4.0 * SR)]), 1e-6)
        # 锁定后必须有声音
        self.assertGreater(rms(out[int(6.0 * SR) : int(8.0 * SR)]), 0.002)
        # 时长与丢样
        self.assertAlmostEqual(out.size / SR, metrics["source_seconds"], delta=0.05)
        self.assertEqual(metrics["dropped_out"], 0)
        self.assertLessEqual(metrics["underruns"], 8)
        self.assertLess(metrics["latency_ms"], 600)

    def test_calibrated_mode_pipeline(self):
        result = simulate.run(mode="calibrated", seconds=12.0, speed=6.0)
        self.assertIsNotNone(result.locked_at)
        self.assertTrue(result.metrics["locked"])
        out = result.output
        self.assertGreater(rms(out[int(4.0 * SR) : int(7.0 * SR)]), 0.002)
        self.assertTrue(np.all(np.isfinite(out)))

    def test_fixed_mode_locks_immediately(self):
        """固定声纹模式：启动即锁定，不需要说话，也不用等 3 秒。"""
        from app import voiceprint

        base = Path(__file__).resolve().parents[2]
        path = base / "_vp_engine_test.npy"
        try:
            voiceprint.save(np.zeros(48000, dtype=np.float32), meta={"device": "test"}, name_or_path=str(path))
            result = simulate.run(
                mode="fixed",
                seconds=8.0,
                speed=6.0,
                config={"model": {"mode": "fixed", "voiceprint": str(path)}},
            )
        finally:
            for p in (path, path.with_suffix(".json")):
                try:
                    p.unlink()
                except OSError:
                    pass
        out = result.output
        self.assertTrue(result.metrics["locked"])
        self.assertIn("固定声纹", str(result.metrics["model"]))
        # 锁定在第一个块就发生：开头 0.5 秒就应该有声音（不用等说话）
        self.assertGreater(rms(out[: int(0.5 * SR)]), 1e-4)

    def test_fixed_mode_without_voiceprint_reports_error(self):
        result = simulate.run(
            mode="fixed",
            seconds=4.0,
            speed=6.0,
            config={"model": {"mode": "fixed", "voiceprint": "D:/nonexistent/vp.npy"}},
        )
        self.assertEqual(result.metrics["state"], "error")
        self.assertIn("声纹", str(result.metrics["status"]))

    def test_restart_after_model_failure_without_manual_stop(self):
        """A failed download must not require an extra manual stop before retry."""
        retried = threading.Event()
        calls = []

        def factory(*_):
            calls.append(True)
            if len(calls) == 1:
                raise ModelError("download failed")
            retried.set()
            return None

        engine = AudioEngine(settings.normalize({}), adapter_factory=factory)
        mic = dev(1, "Microphone", "input")
        cable = dev(2, "CABLE Input", "output")
        try:
            with mock.patch.object(engine, "_open_streams", return_value=48000):
                with self.assertLogs("app.engine", level="ERROR"):
                    engine.start(mic, cable)
                    engine._worker.join(timeout=3)
                self.assertFalse(engine.running)
                self.assertEqual(engine.snapshot()["state"], "error")
                engine.start(mic, cable)
                self.assertTrue(engine.running)
                self.assertTrue(retried.wait(timeout=3))
                self.assertEqual(len(calls), 2)
        finally:
            engine.stop()

    def test_restart_after_timed_out_stop_finishes(self):
        entered = threading.Event()
        release = threading.Event()

        def slow_factory(*_):
            entered.set()
            release.wait(timeout=5)
            return None

        engine = AudioEngine(settings.normalize({}), adapter_factory=slow_factory)
        mic = dev(1, "Microphone", "input")
        cable = dev(2, "CABLE Input", "output")
        try:
            with mock.patch.object(engine, "_open_streams", return_value=48000):
                engine.start(mic, cable)
                self.assertTrue(entered.wait(timeout=3))
                engine.stop(timeout=0)
                with self.assertRaises(EngineError):
                    engine.start(mic, cable)
                release.set()
                engine._worker.join(timeout=3)
                self.assertFalse(engine.running)
                engine.start(mic, cable)
                self.assertTrue(engine.running)
        finally:
            release.set()
            engine.stop()

    def test_stop_is_idempotent(self):
        settings_cfg = settings.normalize({})
        engine = AudioEngine(settings_cfg)
        engine.stop()
        engine.stop()
        self.assertFalse(engine.running)
        self.assertEqual(engine.snapshot()["state"], "idle")


if __name__ == "__main__":
    unittest.main()
