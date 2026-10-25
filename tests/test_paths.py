import os
import unittest
from pathlib import Path
from unittest import mock

from app import paths


class PathCandidateTests(unittest.TestCase):
    def test_program_dir_comes_first(self):
        candidates = paths.candidates()
        self.assertEqual(candidates[0], paths.ROOT)
        self.assertIn(paths.ROOT.parent, candidates)

    def test_override_wins(self):
        with mock.patch.dict(os.environ, {"VOXCALL_STATE_DIR": str(paths.ROOT.parent / "_x")}):
            self.assertEqual(paths.candidates(), [paths.ROOT.parent / "_x"])

    def test_probe_reports_success_and_failure(self):
        base = paths.ROOT.parent
        ok, reason = paths.probe(base / "_paths_probe_dir")
        self.assertTrue(ok, reason)
        try:
            blocker = base / "_paths_blocker"
            blocker.write_text("x", encoding="utf-8")
            ok2, reason2 = paths.probe(blocker / "sub")
            self.assertFalse(ok2)
            self.assertTrue(reason2)
        finally:
            import shutil

            shutil.rmtree(base / "_paths_probe_dir", ignore_errors=True)
            try:
                blocker.unlink()
            except OSError:
                pass

    def test_writable_dir_returns_usable_path(self):
        with mock.patch.dict(os.environ, {"VOXCALL_STATE_DIR": str(paths.ROOT.parent / "_paths_state")}):
            paths._writable_cache = None
            directory = paths.writable_dir("voiceprints")
            self.assertTrue(directory.exists())
            self.assertEqual(directory.parent, paths.ROOT.parent / "_paths_state")
            import shutil

            shutil.rmtree(paths.ROOT.parent / "_paths_state", ignore_errors=True)
            paths._writable_cache = None


if __name__ == "__main__":
    unittest.main()
