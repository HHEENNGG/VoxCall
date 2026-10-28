import shutil
import unittest
from pathlib import Path

from tools.bump_readme_version import bump, version_text

TMP = Path(__file__).resolve().parents[2] / "_bump_tmp"


class VersionTextTests(unittest.TestCase):
    def test_common_tags(self):
        self.assertEqual(version_text("v0.02-beta"), "Beta 0.02")
        self.assertEqual(version_text("v0.03-BETA"), "Beta 0.03")
        self.assertEqual(version_text("v0.03"), "0.03")
        self.assertEqual(version_text("0.04"), "0.04")
        self.assertEqual(version_text("v1.2-rc1"), "RC 1.2")
        self.assertEqual(version_text("v1.0-alpha2"), "Alpha 1.0")

    def test_invalid_tag(self):
        with self.assertRaises(ValueError):
            version_text("nightly")


class BumpTests(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(TMP, ignore_errors=True)
        TMP.mkdir(parents=True)
        self.readme = TMP / "README.md"
        self.readme.write_text(
            "# VoxCall\n\n版本 <!-- version -->Beta 0.01<!-- /version --> · 作者\n"
            "页脚 <!-- version -->Beta 0.01<!-- /version -->\n",
            encoding="utf-8",
        )

    def tearDown(self):
        shutil.rmtree(TMP, ignore_errors=True)

    def test_replaces_all_markers(self):
        changed, text = bump(self.readme, "v0.02-beta")
        self.assertTrue(changed)
        self.assertEqual(text, "Beta 0.02")
        content = self.readme.read_text(encoding="utf-8")
        self.assertNotIn("Beta 0.01", content)
        self.assertEqual(content.count("Beta 0.02"), 2)

    def test_same_version_is_noop(self):
        changed, text = bump(self.readme, "v0.01-beta")
        self.assertFalse(changed)
        self.assertEqual(text, "Beta 0.01")

    def test_missing_marker_raises(self):
        self.readme.write_text("# 没有标记\n", encoding="utf-8")
        with self.assertRaises(SystemExit):
            bump(self.readme, "v0.02-beta")


if __name__ == "__main__":
    unittest.main()
