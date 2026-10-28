import os
import unittest
from pathlib import Path
from unittest import mock

from app import shell


class OpenPathTests(unittest.TestCase):
    def test_missing_path_returns_false(self):
        self.assertFalse(shell.open_path(Path(r"D:\definitely\missing\dir")))

    def test_dir_uses_shell_delegation_only(self):
        """目录只交给已运行的资源管理器：不起进程、不走文件关联。"""
        target = Path(__file__).resolve().parents[2]
        with mock.patch.object(shell, "_reveal_in_shell", return_value=True) as reveal, mock.patch.object(
            shell.subprocess, "Popen"
        ) as popen:
            self.assertTrue(shell.open_path(target))
        reveal.assert_called_once()
        popen.assert_not_called()

    def test_file_uses_shell_delegation_only(self):
        target = Path(__file__).resolve()
        with mock.patch.object(shell, "_reveal_in_shell", return_value=True) as reveal, mock.patch.object(
            shell.subprocess, "Popen"
        ) as popen:
            self.assertTrue(shell.open_path(target))
        reveal.assert_called_once()
        popen.assert_not_called()

    def test_failed_open_returns_false_without_spawning(self):
        """委派失败就交回界面兜底，绝不再自己起进程。"""
        target = Path(__file__).resolve().parents[2]
        with mock.patch.object(shell, "_reveal_in_shell", return_value=False), mock.patch.object(
            shell.subprocess, "Popen"
        ) as popen:
            self.assertFalse(shell.open_path(target))
        popen.assert_not_called()


class MutexTests(unittest.TestCase):
    def test_second_mutex_reports_exists(self):
        from app.single_instance import ProcessMutex

        if os.name != "nt":
            self.skipTest("仅 Windows 有命名互斥体")
        first = ProcessMutex("Local\\VoxCall-test-mutex")
        second = ProcessMutex("Local\\VoxCall-test-mutex")
        self.assertFalse(first.already_exists)
        self.assertTrue(second.already_exists)


if __name__ == "__main__":
    unittest.main()
