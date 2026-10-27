import json
import shutil
import time
import unittest
from pathlib import Path
from unittest import mock

from app import updater

TMP = Path(__file__).resolve().parents[2] / "_update_tmp"


class VersionTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(updater.parse_version("v0.02-beta"), (0, 2))
        self.assertEqual(updater.parse_version("0.01"), (0, 1))
        self.assertEqual(updater.parse_version("Beta 0.01"), (0, 1))
        self.assertEqual(updater.parse_version("v1.2.3"), (1, 2))
        self.assertEqual(updater.parse_version(""), (0, 0))

    def test_newer(self):
        self.assertTrue(updater.is_newer("0.02", "0.01"))
        self.assertTrue(updater.is_newer("v0.10", "0.9"))
        self.assertFalse(updater.is_newer("0.01", "0.01"))
        self.assertFalse(updater.is_newer("0.01-beta", "0.01"))
        self.assertFalse(updater.is_newer("0.00", "0.01"))


class PickTests(unittest.TestCase):
    def test_pick_highest_including_prerelease(self):
        releases = [
            {"tag_name": "v0.01-beta", "prerelease": True},
            {"tag_name": "v0.03-beta", "prerelease": True},
            {"tag_name": "v0.02", "prerelease": False},
        ]
        self.assertEqual(updater._pick_latest(releases)["tag_name"], "v0.03-beta")

    def test_skip_draft(self):
        releases = [
            {"tag_name": "v9.9", "draft": True},
            {"tag_name": "v0.02"},
        ]
        self.assertEqual(updater._pick_latest(releases)["tag_name"], "v0.02")


class CheckTests(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(TMP, ignore_errors=True)
        TMP.mkdir(parents=True, exist_ok=True)
        patcher = mock.patch.object(updater.settings, "state_dir", return_value=TMP)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        shutil.rmtree(TMP, ignore_errors=True)

    def test_reports_newer_release(self):
        payload = [
            {
                "tag_name": "v0.02-beta",
                "name": "VoxCall Beta 0.02",
                "html_url": "https://github.com/HHEENNGG/VoxCall/releases/tag/v0.02-beta",
                "prerelease": True,
                "published_at": "2026-11-01T00:00:00Z",
                "body": "- 新东西",
            }
        ]
        with mock.patch.object(updater, "_fetch", return_value=payload):
            release = updater.check(use_cache=False)
        self.assertIsNotNone(release)
        self.assertEqual(release.tag, "v0.02-beta")
        self.assertTrue(release.page_url.endswith("v0.02-beta"))

    def test_same_version_means_no_update(self):
        payload = [
            {
                "tag_name": f"v{updater.VERSION}",
                "name": "same",
                "html_url": "https://example.com",
                "prerelease": True,
            }
        ]
        with mock.patch.object(updater, "_fetch", return_value=payload):
            self.assertIsNone(updater.check(use_cache=False))

    def test_network_failure_is_silent(self):
        with mock.patch.object(updater, "_fetch", side_effect=OSError("no network")):
            self.assertIsNone(updater.check(use_cache=False))

    def test_cache_used_within_window(self):
        cached = {
            "checked_at": time.time(),
            "latest_version": "0.05",
            "tag": "v0.05",
            "name": "cached",
            "page_url": "https://example.com/v0.05",
            "prerelease": False,
            "published_at": "2026-11-01T00:00:00Z",
        }
        (TMP / updater.CACHE_FILE).write_text(json.dumps(cached), encoding="utf-8")
        with mock.patch.object(updater, "_fetch", side_effect=AssertionError("no network")):
            release = updater.check(use_cache=True)
        self.assertIsNotNone(release)
        self.assertEqual(release.version, "0.05")

    def test_expired_cache_refetches(self):
        cached = {
            "checked_at": time.time() - 48 * 3600,
            "latest_version": "0.09",
            "tag": "v0.09",
            "name": "old",
            "page_url": "https://example.com",
            "prerelease": False,
            "published_at": "",
        }
        (TMP / updater.CACHE_FILE).write_text(json.dumps(cached), encoding="utf-8")
        payload = [{"tag_name": f"v{updater.VERSION}", "html_url": "https://example.com"}]
        with mock.patch.object(updater, "_fetch", return_value=payload) as fetch:
            self.assertIsNone(updater.check(use_cache=True))
        self.assertTrue(fetch.called)


if __name__ == "__main__":
    unittest.main()
