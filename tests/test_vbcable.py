import unittest
from pathlib import Path
from unittest import mock

from app import vbcable


def _device(name: str):
    from app.devices import DeviceInfo

    return DeviceInfo(
        index=0, name=name, hostapi=0, hostapi_name="Windows WASAPI",
        max_input=1, max_output=1, default_samplerate=48000.0,
    )


class DetectTests(unittest.TestCase):
    def test_detects_installed_cable(self):
        with mock.patch.object(
            vbcable.devices_mod,
            "list_devices",
            side_effect=lambda kind, hostapi=None: (
                [_device("CABLE Input (VB-Audio Virtual Cable)")]
                if kind == "output"
                else [_device("Microphone"), _device("CABLE Output (VB-Audio Virtual Cable)")]
            ),
        ):
            self.assertTrue(vbcable.is_installed())

    def test_missing_when_only_one_side(self):
        with mock.patch.object(
            vbcable.devices_mod,
            "list_devices",
            side_effect=lambda kind, hostapi=None: (
                [_device("扬声器 (Realtek)")] if kind == "output" else [_device("麦克风")]
            ),
        ):
            self.assertFalse(vbcable.is_installed())

    def test_failure_is_treated_as_missing(self):
        with mock.patch.object(vbcable.devices_mod, "list_devices", side_effect=RuntimeError("boom")):
            self.assertFalse(vbcable.is_installed())


class InstallerTests(unittest.TestCase):
    def test_find_setup_x64_first(self):
        import tempfile

        base = Path(__file__).resolve().parents[2] / "_vbcable_fake"
        base.mkdir(exist_ok=True)
        try:
            (base / "VBCABLE_Setup.exe").write_bytes(b"x")
            (base / "VBCABLE_Setup_x64.exe").write_bytes(b"x")
            found = vbcable.find_setup(base)
            self.assertEqual(found.name, "VBCABLE_Setup_x64.exe")
        finally:
            import shutil

            shutil.rmtree(base, ignore_errors=True)

    def test_find_setup_none(self):
        base = Path(__file__).resolve().parents[2] / "_vbcable_empty"
        base.mkdir(exist_ok=True)
        try:
            self.assertIsNone(vbcable.find_setup(base))
        finally:
            base.rmdir()

    def test_run_installer_rejects_missing_file(self):
        with self.assertRaises(vbcable.VBCableError):
            vbcable.run_installer(Path("D:/definitely/not/here/VBCABLE_Setup_x64.exe"))

    def test_official_urls(self):
        self.assertTrue(vbcable.DOWNLOAD_URL.startswith("https://download.vb-audio.com/"))
        self.assertIn("vb-audio.com", vbcable.HOMEPAGE)


if __name__ == "__main__":
    unittest.main()
