"""音频设备枚举、驱动接口（Host API）过滤与设备组合校验。

一个物理设备会在每个驱动接口下各出现一次（MME / DirectSound / WASAPI / WDM-KS），
所以界面先选接口、再选设备，默认用 WASAPI（共享模式延迟最低）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import sounddevice as sd

log = logging.getLogger(__name__)

CABLE_HINTS = ("cable input", "cable output", "vb-audio", "voicemeeter")
LOOPBACK_HINTS = ("cable output", "stereo mix", "loopback", "what u hear")

HOSTAPI_AUTO = "auto"
HOSTAPI_ALL = "all"


class DeviceError(RuntimeError):
    pass


@dataclass(frozen=True)
class DeviceInfo:
    index: int
    name: str
    hostapi: int
    hostapi_name: str
    max_input: int
    max_output: int
    default_samplerate: float

    @property
    def is_cable(self) -> bool:
        lowered = self.name.lower()
        return any(hint in lowered for hint in CABLE_HINTS)

    @property
    def is_wasapi(self) -> bool:
        return "wasapi" in self.hostapi_name.lower()

    def label(self, with_api: bool = False) -> str:
        return f"{self.name}  [{self.hostapi_name}]" if with_api else self.name


def hostapis() -> list[dict]:
    try:
        return list(sd.query_hostapis())
    except Exception:  # pragma: no cover - 取决于驱动
        log.exception("枚举主机 API 失败")
        return []


def wasapi_index() -> int | None:
    for idx, api in enumerate(hostapis()):
        if "wasapi" in str(api.get("name", "")).lower():
            return idx
    return None


def hostapi_choices() -> list[tuple[str, str]]:
    """界面下拉项：[(value, label)]。value 为 auto / all / 具体接口名。"""
    choices = [
        (HOSTAPI_AUTO, "自动（WASAPI 优先）"),
        (HOSTAPI_ALL, "全部接口（显示所有驱动）"),
    ]
    for api in hostapis():
        name = str(api.get("name", "") or "")
        count = len(api.get("devices", []) or [])
        if name and count:
            choices.append((name, f"{name}（{count} 个）"))
    return choices


def _all_devices(kind: str) -> list[DeviceInfo]:
    if kind not in {"input", "output"}:
        raise ValueError("kind 必须是 input 或 output")
    apis = hostapis()
    result: list[DeviceInfo] = []
    for idx, dev in enumerate(sd.query_devices()):
        max_in = int(dev.get("max_input_channels", 0))
        max_out = int(dev.get("max_output_channels", 0))
        if kind == "input" and max_in <= 0:
            continue
        if kind == "output" and max_out <= 0:
            continue
        api_index = int(dev.get("hostapi", 0))
        api_name = str(apis[api_index]["name"]) if api_index < len(apis) else "?"
        result.append(
            DeviceInfo(
                index=idx,
                name=str(dev["name"]),
                hostapi=api_index,
                hostapi_name=api_name,
                max_input=max_in,
                max_output=max_out,
                default_samplerate=float(dev.get("default_samplerate", 0.0) or 0.0),
            )
        )
    return result


def list_devices(kind: str = "input", hostapi: str = HOSTAPI_AUTO) -> list[DeviceInfo]:
    """按接口过滤并去重后返回设备列表。

    hostapi:
        "auto"  —— 有 WASAPI 就只列 WASAPI（推荐）
        "all"   —— 所有驱动接口下的设备
        其他    —— 按接口名匹配（如 "Windows WASAPI" / "MME"）
    """
    devices = _all_devices(kind)
    value = (hostapi or HOSTAPI_AUTO).strip()
    if value.lower() == HOSTAPI_ALL:
        selected = devices
    elif value.lower() == HOSTAPI_AUTO:
        selected = [d for d in devices if d.is_wasapi] or devices
    else:
        selected = [d for d in devices if value.lower() in d.hostapi_name.lower()] or devices

    deduped: list[DeviceInfo] = []
    seen: set[tuple[int, str]] = set()
    for dev in selected:
        key = (dev.hostapi, dev.name.lower())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(dev)
    return sorted(deduped, key=lambda d: (0 if d.is_wasapi else 1, d.hostapi, d.name.lower()))


def resolve(name: str, kind: str = "input", hostapi: str = HOSTAPI_AUTO) -> DeviceInfo | None:
    """按名称片段挑设备；先在当前接口里找，找不到再跨接口找。"""
    name = (name or "").strip()
    if not name:
        return None
    for pool in (list_devices(kind, hostapi), _all_devices(kind)):
        exact = [d for d in pool if d.name.lower() == name.lower()]
        if exact:
            return exact[0]
        contain = [d for d in pool if name.lower() in d.name.lower()]
        if contain:
            return contain[0]
    return None


def _prefer_wasapi_twin(dev: DeviceInfo, pool: list[DeviceInfo]) -> DeviceInfo:
    """全部接口模式下，同一设备若也有 WASAPI 版本，优先用它（延迟更低）。"""
    if dev.is_wasapi:
        return dev
    for other in pool:
        if other.is_wasapi and other.name.lower() == dev.name.lower():
            return other
    return dev


def default_device(kind: str, hostapi: str = HOSTAPI_AUTO) -> DeviceInfo | None:
    """当前接口下的系统默认设备（取不到就取列表第一个）。"""
    devices = list_devices(kind, hostapi)
    if not devices:
        return None
    apis = hostapis()
    api_index: int | None = None
    value = (hostapi or HOSTAPI_AUTO).strip()
    if value.lower() == HOSTAPI_AUTO:
        api_index = wasapi_index()
    elif value.lower() != HOSTAPI_ALL:
        for idx, api in enumerate(apis):
            if value.lower() in str(api.get("name", "")).lower():
                api_index = idx
                break
    if api_index is not None and api_index < len(apis):
        key = "default_input_device" if kind == "input" else "default_output_device"
        idx = apis[api_index].get(key)
        if idx is not None and int(idx) >= 0:
            for dev in devices:
                if dev.index == int(idx):
                    return _prefer_wasapi_twin(dev, devices)
    try:
        idx = sd.default.device[0 if kind == "input" else 1]
    except Exception:
        idx = None
    for dev in devices:
        if idx is not None and dev.index == int(idx):
            return _prefer_wasapi_twin(dev, devices)
    # 输出端优先 CABLE Input，方便开箱即用
    if kind == "output":
        for dev in devices:
            if dev.is_cable and "cable input" in dev.name.lower():
                return dev
    return devices[0]


def wasapi_settings(device: DeviceInfo, hostapi: str = HOSTAPI_AUTO):
    """为该设备返回 WASAPI 共享模式设置；非 WASAPI 或不可用时返回 None。"""
    if not device.is_wasapi:
        return None
    try:
        return sd.WasapiSettings(exclusive=False)
    except Exception:  # pragma: no cover - 旧版 sounddevice/非 Windows
        log.debug("当前 sounddevice 不支持 WasapiSettings", exc_info=True)
        return None


def validate_pair(in_dev: DeviceInfo | None, out_dev: DeviceInfo | None) -> tuple[list[str], list[str]]:
    """返回 (errors, warnings)。errors 阻止启动，warnings 只提示。"""
    errors: list[str] = []
    warnings: list[str] = []
    if in_dev is None:
        errors.append("没有可用的输入设备（麦克风）。")
    if out_dev is None:
        errors.append("没有可用的输出设备。")
    if in_dev is None or out_dev is None:
        return errors, warnings

    if in_dev.index == out_dev.index:
        errors.append("输入和输出不能是同一个设备，否则会形成回环啸叫。")

    if not out_dev.is_cable:
        warnings.append(
            f"输出设备「{out_dev.name}」看起来不是 VB-CABLE。"
            "若要给通话软件供音，请选择 CABLE Input，否则声音只会从扬声器放出来。"
        )
    lowered = in_dev.name.lower()
    if any(hint in lowered for hint in LOOPBACK_HINTS):
        warnings.append(
            f"输入设备「{in_dev.name}」是回环/虚拟录音端，"
            "用作麦克风会把系统声音或本程序输出采回来，请确认这是你的真实麦克风。"
        )
    if any(hint in out_dev.name.lower() for hint in ("cable output",)):
        errors.append("输出端应选 CABLE Input（播放端），不是 CABLE Output（录音端）。")
    return errors, warnings
