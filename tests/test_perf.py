import unittest

from app.perf import ProcessMonitor


class ProcessMonitorTests(unittest.TestCase):
    def test_sample_returns_numbers(self):
        monitor = ProcessMonitor()
        first = monitor.sample()
        self.assertIn("cpu_percent", first)
        self.assertIn("mem_mb", first)

    def test_second_sample_has_cpu(self):
        monitor = ProcessMonitor()
        monitor.sample()
        # 做点 CPU 工作，保证第二次采样有可测的增量
        total = 0
        for i in range(300000):
            total += i * i
        second = monitor.sample()
        self.assertGreaterEqual(second["cpu_percent"], 0.0)
        self.assertLessEqual(second["cpu_percent"], 100.0)
        if monitor.available:
            self.assertGreater(second["mem_mb"], 0.0)

    def test_unavailable_is_safe(self):
        monitor = ProcessMonitor()
        monitor.available = False
        out = monitor.sample()
        self.assertEqual(set(out), {"cpu_percent", "mem_mb"})


if __name__ == "__main__":
    unittest.main()
