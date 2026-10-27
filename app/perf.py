"""进程自身性能占用（CPU / 内存），只用 ctypes，不引入 psutil。"""

from __future__ import annotations

import ctypes
import logging
import os
import time
from ctypes import wintypes

log = logging.getLogger(__name__)


class _FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]


class _PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def _ft_to_int(value: _FILETIME) -> int:
    return (value.dwHighDateTime << 32) | value.dwLowDateTime


class ProcessMonitor:
    """每秒采样一次，返回 {"cpu_percent": float, "mem_mb": float}。"""

    def __init__(self) -> None:
        self.available = os.name == "nt"
        self.cpu_count = os.cpu_count() or 1
        self._last_wall: float | None = None
        self._last_cpu: int | None = None
        self._last = {"cpu_percent": 0.0, "mem_mb": 0.0}
        if self.available:
            try:
                self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                self._psapi = ctypes.WinDLL("psapi", use_last_error=True)
                # 必须声明 HANDLE 类型：伪句柄 -1 若按 32 位 int 传会被截断成无效句柄
                self._kernel32.GetCurrentProcess.restype = wintypes.HANDLE
                self._handle = self._kernel32.GetCurrentProcess()
                self._kernel32.GetProcessTimes.argtypes = [
                    wintypes.HANDLE,
                    ctypes.POINTER(_FILETIME),
                    ctypes.POINTER(_FILETIME),
                    ctypes.POINTER(_FILETIME),
                    ctypes.POINTER(_FILETIME),
                ]
                self._kernel32.GetProcessTimes.restype = wintypes.BOOL
                self._psapi.GetProcessMemoryInfo.argtypes = [
                    wintypes.HANDLE,
                    ctypes.POINTER(_PROCESS_MEMORY_COUNTERS),
                    wintypes.DWORD,
                ]
                self._psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
            except Exception:
                self.available = False

    def _cpu_time_100ns(self) -> int | None:
        creation, exit_, kernel, user = (
            _FILETIME(),
            _FILETIME(),
            _FILETIME(),
            _FILETIME(),
        )
        ok = self._kernel32.GetProcessTimes(
            self._handle,
            ctypes.byref(creation),
            ctypes.byref(exit_),
            ctypes.byref(kernel),
            ctypes.byref(user),
        )
        if not ok:
            return None
        return _ft_to_int(kernel) + _ft_to_int(user)

    def _memory_mb(self) -> float:
        counters = _PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(_PROCESS_MEMORY_COUNTERS)
        ok = self._psapi.GetProcessMemoryInfo(
            self._handle, ctypes.byref(counters), counters.cb
        )
        if not ok:
            return 0.0
        return counters.WorkingSetSize / 1048576.0

    def sample(self) -> dict:
        if not self.available:
            return self._last
        try:
            now_wall = time.perf_counter()
            now_cpu = self._cpu_time_100ns()
            mem = self._memory_mb()
            cpu_percent = 0.0
            if now_cpu is not None and self._last_wall is not None and self._last_cpu is not None:
                wall = now_wall - self._last_wall
                if wall > 0.05:
                    # 100ns -> 秒；按逻辑核心数换算成「整机百分比」
                    cpu_percent = (
                        (now_cpu - self._last_cpu) / 1e7 / wall / self.cpu_count * 100.0
                    )
            self._last_wall = now_wall
            self._last_cpu = now_cpu
            self._last = {
                "cpu_percent": max(0.0, min(100.0, cpu_percent)),
                "mem_mb": mem,
            }
        except Exception:
            log.debug("采样进程占用失败", exc_info=True)
        return self._last
