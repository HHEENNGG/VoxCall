import os
import subprocess
import sys
import types
import unittest
import uuid
from unittest import mock

from app.single_instance import MESSAGE_ACK, ProcessMutex, SingleInstance


@unittest.skipUnless(os.name == "nt", "Windows named mutex tests")
class MutexTests(unittest.TestCase):
    def setUp(self):
        self.name = "Local\\VoxCall-test-" + uuid.uuid4().hex
        code = (
            "import sys; from app.single_instance import ProcessMutex; "
            "m=ProcessMutex(sys.argv[1]); print(m.already_exists, flush=True); "
            "sys.stdin.read(); m.close()"
        )
        self.owner = subprocess.Popen(
            [sys.executable, "-u", "-c", code, self.name],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
        )
        self.addCleanup(self.cleanup_owner)
        self.assertEqual(self.owner.stdout.readline().strip(), "False")

    def cleanup_owner(self):
        if self.owner.poll() is None:
            self.owner.kill()
            self.owner.wait(timeout=5)
        self.owner.stdin.close()
        self.owner.stdout.close()

    def test_live_owner_prevents_duplicate(self):
        waiter = ProcessMutex(self.name)
        self.addCleanup(waiter.close)
        self.assertTrue(waiter.already_exists)
        self.assertFalse(waiter.try_acquire())

    def test_abandoned_mutex_is_not_a_running_instance(self):
        # Keep a second handle alive to reproduce the old ERROR_ALREADY_EXISTS bug.
        waiter = ProcessMutex(self.name)
        self.addCleanup(waiter.close)
        self.owner.kill()
        self.owner.wait(timeout=5)
        restarted = ProcessMutex(self.name)
        self.addCleanup(restarted.close)
        self.assertFalse(restarted.already_exists)

    def test_waiting_launch_can_take_over_after_owner_dies(self):
        waiter = ProcessMutex(self.name)
        self.addCleanup(waiter.close)
        self.assertTrue(waiter.already_exists)
        self.owner.kill()
        self.owner.wait(timeout=5)
        self.assertTrue(waiter.try_acquire())
        self.assertFalse(waiter.already_exists)

    def test_normal_close_releases_existing_object(self):
        waiter = ProcessMutex(self.name)
        self.addCleanup(waiter.close)
        self.owner.stdin.close()
        self.owner.wait(timeout=5)
        self.assertTrue(waiter.try_acquire())
        waiter.close()
        waiter.close()


class SingleInstanceTests(unittest.TestCase):
    def check_reply(self, parts):
        socket = mock.Mock()
        socket.waitForConnected.return_value = True
        socket.waitForReadyRead.return_value = True
        socket.bytesAvailable.return_value = 1
        socket.readAll.side_effect = parts
        with mock.patch("app.single_instance._qt_network", return_value=(mock.Mock(), lambda: socket)):
            result = SingleInstance("test-pipe").already_running()
        socket.abort.assert_called_once()
        return result

    def test_exact_ack_required(self):
        self.assertTrue(self.check_reply([MESSAGE_ACK]))
        self.assertFalse(self.check_reply([b"not-an-ack\n"]))

    def test_fragmented_ack(self):
        self.assertTrue(self.check_reply([b"o", b"k\n"]))

    def test_silent_pipe_not_running(self):
        socket = mock.Mock()
        socket.waitForConnected.return_value = True
        socket.bytesAvailable.return_value = 0
        socket.waitForReadyRead.return_value = False
        with mock.patch("app.single_instance._qt_network", return_value=(mock.Mock(), lambda: socket)):
            self.assertFalse(SingleInstance().already_running())
        socket.abort.assert_called_once()

    def test_listening_server_closed(self):
        guard = SingleInstance()
        server = mock.Mock()
        guard._server = server
        guard.close()
        guard.close()
        server.close.assert_called_once()


class GuiInstanceCleanupTests(unittest.TestCase):
    def run_gui(self, *, existing=False, acquire=None, startup_error=None):
        import main

        app = mock.Mock()
        app.exec.return_value = 0
        mutex = mock.Mock()
        mutex.already_exists = existing
        if acquire is not None:
            mutex.try_acquire.side_effect = acquire
        else:
            mutex.try_acquire.return_value = False
        guard = mock.Mock()
        guard.already_running.return_value = False
        window_type = mock.Mock()
        window_type.side_effect = startup_error
        widgets = types.ModuleType("PySide6.QtWidgets")
        widgets.QApplication = mock.Mock(return_value=app)
        gui = types.ModuleType("PySide6.QtGui")
        gui.QIcon = mock.Mock()
        window_module = types.ModuleType("app.main_window")
        window_module.MainWindow = window_type
        with mock.patch.dict(sys.modules, {"PySide6.QtWidgets": widgets, "PySide6.QtGui": gui, "app.main_window": window_module}), \
                mock.patch("app.single_instance.ProcessMutex", return_value=mutex), \
                mock.patch("app.single_instance.SingleInstance", return_value=guard), \
                mock.patch("main.time.sleep"), \
                mock.patch("app.theme.remember_default_palette"), \
                mock.patch("main._show_error_dialog") as dialog:
            try:
                result = main.run_gui({"app": {"start_minimized": False}})
            finally:
                mutex.close.assert_called_once()
                guard.close.assert_called_once()
        return result, window_type, dialog

    def test_startup_exception_releases_mutex_and_server(self):
        with self.assertRaisesRegex(RuntimeError, "startup failed"):
            self.run_gui(startup_error=RuntimeError("startup failed"))

    def test_normal_exit_releases_mutex_and_server(self):
        result, window_type, _ = self.run_gui()
        self.assertEqual(result, 0)
        window_type.assert_called_once()

    def test_owner_dying_during_retry_allows_startup(self):
        result, window_type, _ = self.run_gui(existing=True, acquire=[False, True])
        self.assertEqual(result, 0)
        window_type.assert_called_once()

    def test_unresponsive_live_owner_reports_error_not_silent_success(self):
        with self.assertLogs("main", level="ERROR"):
            result, window_type, dialog = self.run_gui(existing=True)
        self.assertEqual(result, 1)
        window_type.assert_not_called()
        dialog.assert_called_once()


if __name__ == "__main__":
    unittest.main()
