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

    def test_existing_dir_opens_the_folder_itself(self):
        """文件夹要打开它自己，不能走「打开上级并选中」（那会打开上一级）。"""
        target = Path(__file__).resolve().parents[2]
        with mock.patch.object(shell, "_open_folder_in_shell", return_value=True) as opener, mock.patch.object(
            shell, "_reveal_in_shell"
        ) as reveal:
            self.assertTrue(shell.open_path(target))
        opener.assert_called_once()
        reveal.assert_not_called()

    def test_file_is_revealed_in_folder(self):
        target = Path(__file__).resolve()
        with mock.patch.object(shell, "_open_folder_in_shell") as opener, mock.patch.object(
            shell, "_reveal_in_shell", return_value=True
        ) as reveal:
            self.assertTrue(shell.open_path(target))
        reveal.assert_called_once()
        opener.assert_not_called()

    def test_failed_open_returns_false_without_spawning_explorer(self):
        """打不开就交回给界面兜底，绝不再自己起 explorer（会崩 0xC0000142）。"""
        target = Path(__file__).resolve().parents[2]
        with mock.patch.object(shell, "_open_folder_in_shell", return_value=False), mock.patch.object(
            shell.subprocess, "Popen"
        ) as popen:
            self.assertFalse(shell.open_path(target))
        popen.assert_not_called()


class MutexTests(unittest.TestCase):
    def test_second_mutex_reports_exists(self):
        from app.single_instance import ProcessMutex

        first = ProcessMutex("Local\\VoxCall-test-mutex")
        second = ProcessMutex("Local\\VoxCall-test-mutex")
        if os.name != "nt":
            self.skipTest("仅 Windows 有命名互斥体")
        self.assertFalse(first.already_exists)
        self.assertTrue(second.already_exists)


if __name__ == "__main__":
    unittest.main()
