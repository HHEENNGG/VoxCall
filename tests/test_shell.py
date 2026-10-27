import os
import unittest
from pathlib import Path
from unittest import mock

from app import shell


class CleanEnvTests(unittest.TestCase):
    def test_strips_internal_and_runtime(self):
        polluted = os.pathsep.join(
            [
                r"D:\app\VoxCall\_internal",
                r"D:\app\VoxCall\runtime\python313\python\Lib\site-packages",
                r"C:\Windows\System32",
            ]
        )
        with mock.patch.dict(os.environ, {"PATH": polluted, "PYTHONHOME": "x", "PYTHONPATH": "y"}):
            env = shell._clean_env()
        lowered = env["PATH"].lower()
        self.assertNotIn("_internal", lowered)
        self.assertNotIn("site-packages", lowered)
        self.assertNotIn("PYTHONHOME", env)
        self.assertNotIn("PYTHONPATH", env)
        self.assertIn(r"c:\windows\system32", lowered)

    def test_keeps_clean_entries(self):
        with mock.patch.dict(os.environ, {"PATH": r"C:\Program Files\Git\cmd"}, clear=False):
            env = shell._clean_env()
        self.assertIn("git", env["PATH"].lower())


class OpenPathTests(unittest.TestCase):
    def test_missing_path_returns_false(self):
        self.assertFalse(shell.open_path(Path(r"D:\definitely\missing\dir")))

    def test_existing_dir_goes_through_shell(self):
        target = Path(__file__).resolve().parents[2]
        with mock.patch.object(shell, "_reveal_in_shell", return_value=True) as reveal:
            self.assertTrue(shell.open_path(target))
        reveal.assert_called_once()

    def test_failed_reveal_returns_false_without_spawning_explorer(self):
        """打不开就交回给界面兜底，绝不再自己起 explorer（会崩 0xC0000142）。"""
        target = Path(__file__).resolve().parents[2]
        with mock.patch.object(shell, "_reveal_in_shell", return_value=False), mock.patch.object(
            shell.subprocess, "Popen"
        ) as popen:
            self.assertFalse(shell.open_path(target))
        popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
