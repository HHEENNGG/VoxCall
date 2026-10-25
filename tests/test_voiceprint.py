import shutil
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from app import voiceprint

BASE = Path(__file__).resolve().parents[2]  # 工作区根目录（可写）
NPY = BASE / "_vp_test.npy"


def _clean() -> None:
    for p in (NPY, NPY.with_suffix(".json")):
        try:
            p.unlink()
        except OSError:
            pass


class FakeInputStream:
    """按真实时间 20 倍速推数据，测试不用等 3 秒。"""

    def __init__(self, device=None, rate=48000, block=3840, callback=None, extra=None,
                 kind="input", amplitude=0.1, silent=False):
        self.rate = rate
        self.block = block
        self.callback = callback
        self.amplitude = amplitude
        self.silent = silent
        self._thread = None
        self._running = False

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        rng = np.random.default_rng(0)
        while self._running:
            if self.silent:
                x = np.zeros(self.block, dtype=np.float32)
            else:
                x = (self.amplitude * rng.standard_normal(self.block)).astype(np.float32)
            self.callback(x.reshape(-1, 1), self.block, None, None)
            time.sleep(self.block / self.rate / 20)

    def stop(self):
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=1)

    def close(self):
        self._running = False


class VoiceprintStorageTests(unittest.TestCase):
    def setUp(self):
        _clean()

    def tearDown(self):
        _clean()

    def test_save_load_roundtrip_with_meta(self):
        sample = np.linspace(-0.1, 0.1, 48000, dtype=np.float32)
        voiceprint.save(sample, meta={"device": "Test Mic"}, name_or_path=str(NPY))
        self.assertTrue(voiceprint.exists(str(NPY)))
        loaded = voiceprint.load(str(NPY))
        self.assertEqual(loaded.size, 48000)
        self.assertEqual(loaded.dtype, np.float32)
        np.testing.assert_allclose(loaded, sample, rtol=0, atol=1e-7)
        meta = voiceprint.info(str(NPY))
        self.assertEqual(meta["device"], "Test Mic")
        self.assertIn("created_at", meta)
        self.assertIn("已保存", voiceprint.describe(str(NPY)))

    def test_too_short_rejected(self):
        with self.assertRaises(voiceprint.VoiceprintError):
            voiceprint.save(np.zeros(16000, dtype=np.float32), name_or_path=str(NPY))

    def test_missing_file_raises(self):
        with self.assertRaises(voiceprint.VoiceprintError):
            voiceprint.load(str(NPY))
        self.assertFalse(voiceprint.exists(str(NPY)))
        self.assertEqual(voiceprint.describe(str(NPY)), "未录制")

    def test_clear_removes_files(self):
        voiceprint.save(np.zeros(48000, dtype=np.float32), name_or_path=str(NPY))
        self.assertTrue(voiceprint.clear(str(NPY)))
        self.assertFalse(voiceprint.exists(str(NPY)))
        self.assertFalse(NPY.with_suffix(".json").exists())


class FallbackDirectoryTests(unittest.TestCase):
    """程序目录不可写时（装在 Program Files / 受控文件夹访问拦截）要自动换目录。"""

    def setUp(self):
        self.blocker = BASE / "_vp_blocker"
        self.blocker.write_text("not a directory", encoding="utf-8")
        self.good = BASE / "_vp_fallback"
        self.bad = self.blocker / "voiceprints"  # 父级是文件 → 无法创建
        voiceprint._STORE_DIR = None

    def tearDown(self):
        voiceprint._STORE_DIR = None
        shutil.rmtree(self.good, ignore_errors=True)
        try:
            self.blocker.unlink()
        except OSError:
            pass

    def test_save_uses_first_writable_candidate(self):
        with mock.patch.object(voiceprint, "candidate_dirs", lambda: [self.bad, self.good]):
            path = voiceprint.save(np.zeros(48000, dtype=np.float32))
            self.assertTrue(str(path).startswith(str(self.good)))
            self.assertTrue(voiceprint.exists())
            meta = voiceprint.info()
            self.assertEqual(meta["path"], str(path))
            voiceprint.clear()
            self.assertFalse(voiceprint.exists())

    def test_all_candidates_unwritable_raises_readable_error(self):
        with mock.patch.object(voiceprint, "candidate_dirs", lambda: [self.bad]):
            with self.assertRaises(voiceprint.VoiceprintError) as ctx:
                voiceprint.store_dir()
        self.assertIn("没有可写的目录", str(ctx.exception))


class VoiceprintRecordTests(unittest.TestCase):
    def setUp(self):
        _clean()

    def tearDown(self):
        _clean()

    def _record(self, **kwargs):
        from app.devices import DeviceInfo

        dev = DeviceInfo(
            index=0, name="Test Mic", hostapi=0, hostapi_name="Windows WASAPI",
            max_input=1, max_output=0, default_samplerate=48000.0,
        )
        return voiceprint.record(
            dev, seconds=3.0, device_rate=48000,
            stream_factory=lambda **kw: FakeInputStream(**kw, **kwargs),
        )

    def test_record_produces_three_seconds_at_16k(self):
        sample = self._record()
        self.assertEqual(sample.size, 48000)
        self.assertEqual(sample.dtype, np.float32)
        self.assertGreater(float(np.sqrt(np.mean(sample ** 2))), 1e-3)

    def test_record_silence_rejected(self):
        with self.assertRaises(voiceprint.VoiceprintError):
            self._record(silent=True)


if __name__ == "__main__":
    unittest.main()
