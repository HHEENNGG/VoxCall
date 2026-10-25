import unittest
import zipfile

from app import vbcable


class BundledPackageTests(unittest.TestCase):
    def test_bundled_zip_is_official_and_intact(self):
        path = vbcable.bundled_zip()
        if path is None:
            self.skipTest("未内置安装包（源码环境未带 assets/vbcable）")
        self.assertTrue(path.exists())
        with zipfile.ZipFile(path) as zf:
            names = [n.lower() for n in zf.namelist()]
        self.assertTrue(any("vbcable_setup_x64.exe" in n for n in names), names)
        self.assertTrue(any(n.endswith(".inf") for n in names))

    def test_license_note_mentions_source_and_donation(self):
        note = vbcable.LICENSE_NOTE
        self.assertIn("vb-cable.com", note)
        self.assertIn("donationware", note.lower())
        self.assertTrue(vbcable.DONATE_URL.startswith("https://shop.vb-audio.com/"))

    def test_setup_name_priority(self):
        self.assertEqual(vbcable.SETUP_NAMES[0], "VBCABLE_Setup_x64.exe")
        self.assertIn("VBCABLE_Setup_ARM64.exe", vbcable.SETUP_NAMES)


if __name__ == "__main__":
    unittest.main()
