import os
import shutil
import unittest
from pathlib import Path
from unittest import mock

from app import settings


class ConfigTests(unittest.TestCase):
    def test_defaults_are_complete(self):
        cfg = settings.normalize({})
        for section in ("audio", "model", "denoise", "app"):
            self.assertIn(section, cfg)
        self.assertEqual(cfg["audio"]["chunk_ms"], settings.DEFAULTS["audio"]["chunk_ms"])
        self.assertEqual(cfg["audio"]["capture_block_ms"], 10)
        self.assertEqual(cfg["audio"]["model_sample_rate"], 16000)

    def test_invalid_values_are_clamped(self):
        cfg = settings.normalize(
            {
                "audio": {"chunk_ms": 9999, "device_sample_rate": 1, "hostapi": "bogus"},
                "model": {"precision": "fp64", "mode": "psychic", "calibrate_seconds": 0},
                "denoise": {"strength": 9.0},
            }
        )
        self.assertEqual(cfg["audio"]["chunk_ms"], 500)
        self.assertEqual(cfg["audio"]["device_sample_rate"], 8000)
        self.assertEqual(cfg["audio"]["hostapi"], "bogus")  # 允许任意接口名，运行时再匹配
        self.assertEqual(cfg["model"]["precision"], settings.DEFAULTS["model"]["precision"])
        self.assertEqual(cfg["model"]["mode"], "auto")
        self.assertEqual(cfg["model"]["calibrate_seconds"], 3)
        self.assertEqual(cfg["denoise"]["strength"], 1.0)

    def test_hostapi_legacy_values_are_mapped(self):
        self.assertEqual(settings.normalize({"audio": {"hostapi": "wasapi"}})["audio"]["hostapi"], "auto")
        self.assertEqual(settings.normalize({"audio": {"hostapi": "default"}})["audio"]["hostapi"], "all")
        self.assertEqual(
            settings.normalize({"audio": {"hostapi": "Windows WASAPI"}})["audio"]["hostapi"],
            "Windows WASAPI",
        )

    def test_legacy_yaml_keys_are_migrated(self):
        raw = {
            "audio": {"preferred_input": "Mic", "preferred_output": "CABLE Input", "channels": 1},
            "model": {"live_auto_enroll": True, "precision": "int8"},
            "environment_denoise": {"enabled": True, "strength": 0.4, "spectral_gate": True},
        }
        migrated = settings.normalize(settings._migrate_legacy(raw))
        self.assertEqual(migrated["audio"]["input_device"], "Mic")
        self.assertEqual(migrated["audio"]["output_device"], "CABLE Input")
        self.assertTrue(migrated["denoise"]["enabled"])
        self.assertAlmostEqual(migrated["denoise"]["strength"], 0.4)
        self.assertNotIn("channels", migrated["audio"])

    def test_save_and_load_roundtrip(self):
        cfg = settings.normalize({"audio": {"chunk_ms": 120}, "model": {"threads": 2}})
        path = Path(__file__).resolve().parents[2] / "_voxcall_roundtrip.toml"
        try:
            settings.save_config(cfg, path)
            loaded = settings.load_config(path)
        finally:
            path.unlink(missing_ok=True)
        self.assertEqual(loaded, cfg)
        self.assertEqual(loaded["audio"]["chunk_ms"], 120)
        self.assertEqual(loaded["model"]["threads"], 2)

    def test_toml_escapes_strings(self):
        cfg = settings.normalize({"audio": {"input_device": 'Mic "Name" C:\\x'}})
        text = settings.dumps_config(cfg)
        self.assertIn('Mic \\"Name\\"', text)


    def test_save_falls_back_to_user_dir(self):
        """程序目录不可写时，配置改存用户目录，并且下次还能读回来。"""
        base = Path(__file__).resolve().parents[2]
        blocker = base / "_cfg_blocker"
        state = base / "_cfg_state"
        blocker.write_text("not a directory", encoding="utf-8")
        try:
            with mock.patch.object(settings, "CONFIG_PATH", blocker / "config.toml"), mock.patch.dict(
                os.environ, {"VOXCALL_STATE_DIR": str(state)}
            ):
                cfg = settings.normalize({"audio": {"chunk_ms": 100}})
                written = settings.save_config(cfg)
                self.assertEqual(written.parent, state)
                self.assertTrue(written.exists())
                loaded = settings.load_config()
                self.assertEqual(loaded["audio"]["chunk_ms"], 100)
        finally:
            shutil.rmtree(state, ignore_errors=True)
            try:
                blocker.unlink()
            except OSError:
                pass

    def test_global_config_write_still_works(self):
        cfg = settings.normalize({"audio": {"chunk_ms": 60}})
        base = Path(__file__).resolve().parents[2]
        target = base / "_cfg_direct.toml"
        try:
            written = settings.save_config(cfg, target)
            self.assertEqual(Path(written), target)
            self.assertEqual(settings.load_config(target)["audio"]["chunk_ms"], 60)
        finally:
            target.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
