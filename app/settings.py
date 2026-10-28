"""配置：默认值、类型校验、TOML 读写与旧版 YAML 迁移。

配置使用标准库 tomllib 解析（Python 3.11+），写入用最小 TOML 生成器，
因此运行时不再需要 PyYAML。
"""

from __future__ import annotations

import copy
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from .paths import ROOT

log = logging.getLogger(__name__)

CONFIG_PATH = ROOT / "config.toml"
LEGACY_CONFIG_PATH = ROOT / "config.yaml"

DEFAULTS: dict[str, dict[str, Any]] = {
    "audio": {
        "input_device": "",
        "output_device": "CABLE Input",
        "device_sample_rate": 48000,
        "model_sample_rate": 16000,
        "chunk_ms": 60,
        "capture_block_ms": 10,
        "playback_block_ms": 10,
        "hostapi": "auto",
        "target_latency_ms": 100,
        "max_input_blocks": 10,
        "max_output_ms": 600,
    },
    "model": {
        "mode": "auto",
        "precision": "int8-full",
        "threads": 4,
        "calibrate_seconds": 3,
        "model_dir": "",
        "voiceprint": "",
        "allow_passthrough": True,
    },
    "denoise": {
        "enabled": False,
        "strength": 0.35,
        "floor_gain": 0.2,
    },
    "app": {
        "log_level": "INFO",
        "log_file": "",
        "check_updates": True,
        "update_check_hours": 12,
        "close_to_tray": True,
        "start_minimized": False,
        "theme": "auto",
    },
}

PRECISIONS = ("int8", "int8-full", "fp16", "fp32")
MODES = ("auto", "calibrated", "fixed", "off")


class ConfigError(RuntimeError):
    pass


def deep_merge(base: dict, override: dict) -> dict:
    """递归合并，override 优先；不修改入参。"""
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def normalize(raw: dict) -> dict:
    """合并默认值并做类型/范围校验。非法值回退到默认值并记录警告。"""
    cfg = deep_merge(DEFAULTS, raw)
    audio = cfg["audio"]
    model = cfg["model"]
    denoise = cfg["denoise"]
    app = cfg["app"]

    def int_in(section: dict, section_name: str, key: str, low: int, high: int) -> None:
        default = DEFAULTS[section_name][key]
        try:
            value = int(section[key])
        except (TypeError, ValueError):
            log.warning("配置 %s.%s=%r 不是整数，使用默认值 %r", section_name, key, section[key], default)
            value = default
        section[key] = int(_clamp(value, low, high))

    def float_in(section: dict, section_name: str, key: str, low: float, high: float) -> None:
        default = DEFAULTS[section_name][key]
        try:
            value = float(section[key])
        except (TypeError, ValueError):
            log.warning("配置 %s.%s=%r 不是数字，使用默认值 %r", section_name, key, section[key], default)
            value = default
        section[key] = float(_clamp(value, low, high))

    int_in(audio, "audio", "device_sample_rate", 8000, 192000)
    int_in(audio, "audio", "model_sample_rate", 8000, 48000)
    int_in(audio, "audio", "chunk_ms", 20, 500)
    int_in(audio, "audio", "capture_block_ms", 5, 200)
    int_in(audio, "audio", "playback_block_ms", 5, 200)
    int_in(audio, "audio", "target_latency_ms", 20, 2000)
    int_in(audio, "audio", "max_input_blocks", 2, 100)
    int_in(audio, "audio", "max_output_ms", 100, 3000)
    audio["input_device"] = str(audio["input_device"] or "")
    audio["output_device"] = str(audio["output_device"] or "")
    # hostapi: "auto"（WASAPI 优先）/ "all"（全部接口）/ 具体接口名（如 "Windows WASAPI"）
    legacy = {"wasapi": "auto", "default": "all"}
    value = str(audio.get("hostapi") or "auto").strip() or "auto"
    audio["hostapi"] = legacy.get(value.lower(), value)

    if model["mode"] not in MODES:
        default_mode = DEFAULTS["model"]["mode"]
        log.warning("model.mode=%r 非法，使用 %s", model["mode"], default_mode)
        model["mode"] = default_mode
    if model["precision"] not in PRECISIONS:
        default_precision = DEFAULTS["model"]["precision"]
        log.warning("model.precision=%r 非法，使用 %s", model["precision"], default_precision)
        model["precision"] = default_precision
    int_in(model, "model", "threads", 0, 64)
    int_in(model, "model", "calibrate_seconds", 3, 10)
    model["model_dir"] = str(model["model_dir"] or "")
    model["voiceprint"] = str(model["voiceprint"] or "")
    model["allow_passthrough"] = _as_bool(model["allow_passthrough"])

    denoise["enabled"] = _as_bool(denoise["enabled"])
    float_in(denoise, "denoise", "strength", 0.0, 1.0)
    float_in(denoise, "denoise", "floor_gain", 0.0, 1.0)

    app["log_level"] = str(app["log_level"] or "INFO").upper()
    app["log_file"] = str(app["log_file"] or "")
    theme = str(app.get("theme") or "auto").strip().lower()
    if theme not in ("auto", "light", "dark"):
        log.warning("app.theme=%r 非法，使用 auto", app.get("theme"))
        theme = "auto"
    app["theme"] = theme
    return cfg


def _migrate_legacy(raw: dict) -> dict:
    """把旧版 config.yaml 的键映射到新 schema。"""
    audio = dict(raw.get("audio") or {})
    if "preferred_input" in audio and "input_device" not in audio:
        audio["input_device"] = audio.pop("preferred_input")
    if "preferred_output" in audio and "output_device" not in audio:
        audio["output_device"] = audio.pop("preferred_output")
    for stale in ("preferred_input", "preferred_output", "channels",
                  "input_queue_blocks", "output_queue_blocks"):
        audio.pop(stale, None)

    model = dict(raw.get("model") or {})
    model.pop("live_auto_enroll", None)

    denoise = dict(raw.get("denoise") or raw.get("environment_denoise") or {})
    denoise.pop("spectral_gate", None)

    return {"audio": audio, "model": model, "denoise": denoise, "app": dict(raw.get("app") or {})}


def _read_file(path: Path) -> dict:
    suffix = path.suffix.lower()
    if suffix == ".toml":
        import tomllib

        with path.open("rb") as fh:
            return tomllib.load(fh)
    if suffix in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore
        except ImportError as exc:  # pragma: no cover - 依赖可选
            raise ConfigError(
                f"读取 {path.name} 需要 PyYAML；请改用 config.toml（推荐）或 pip install pyyaml"
            ) from exc
        with path.open("r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    if suffix == ".json":
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    raise ConfigError(f"不支持的配置文件格式：{path.suffix}")


def state_dir() -> Path:
    """程序目录不可写时的状态目录（配置/日志的兜底位置）。"""
    from . import paths

    override = os.environ.get("VOXCALL_STATE_DIR")
    if override:
        return Path(override)
    try:
        return paths.writable_dir()
    except OSError:
        return paths.candidates()[0]


def fallback_config_path() -> Path:
    return state_dir() / "config.toml"


def active_config_path() -> Path:
    """当前真正生效的配置文件：用户目录有就用它，否则用程序目录。"""
    fallback = fallback_config_path()
    if fallback.exists():
        return fallback
    return CONFIG_PATH


def load_config(path: str | os.PathLike | None = None) -> dict:
    """加载配置；显式路径优先，否则 用户目录 -> 程序目录 -> 旧版 yaml -> 默认值。

    用户目录优先很重要：程序目录装到 Program Files 之类只读位置时，保存会落到
    用户目录；如果这里仍优先读程序目录，界面上的改动就永远"存不住"。
    """
    if path is not None:
        p = Path(path)
        if not p.exists():
            raise ConfigError(f"配置文件不存在：{p}")
        raw = _read_file(p)
        if p.suffix.lower() in {".yaml", ".yml"}:
            raw = _migrate_legacy(raw)
        return normalize(raw)

    for candidate in (fallback_config_path(), CONFIG_PATH, LEGACY_CONFIG_PATH):
        if not candidate.exists():
            continue
        try:
            raw = _read_file(candidate)
        except ConfigError as exc:
            log.warning("%s（已忽略，使用默认配置）", exc)
            continue
        if candidate.suffix.lower() in {".yaml", ".yml"}:
            raw = _migrate_legacy(raw)
        return normalize(raw)
    return normalize({})


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def dumps_config(cfg: dict) -> str:
    lines = [
        "# VoxCall 配置。音频设备选择可在界面里改，开始时会自动写回这里。",
        "",
    ]
    for section in ("audio", "model", "denoise", "app"):
        values = cfg.get(section)
        if not isinstance(values, dict):
            continue
        lines.append(f"[{section}]")
        for key, value in values.items():
            if isinstance(value, dict):
                continue
            lines.append(f"{key} = {_toml_value(value)}")
        lines.append("")
    return "\n".join(lines)


def _write_config(target: Path, cfg: dict) -> None:
    if not target.parent.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
    text = dumps_config(cfg)
    tmp_path = target.with_name(target.name + ".tmp")
    try:
        with tmp_path.open("w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp_path, target)
    except BaseException:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


def save_config(cfg: dict, path: str | os.PathLike | None = None) -> Path:
    """写入配置：优先程序目录，不可写时自动改存用户目录。返回实际写入路径。"""
    targets = [Path(path)] if path is not None else [CONFIG_PATH, fallback_config_path()]
    last_error: BaseException | None = None
    for target in targets:
        try:
            _write_config(target, cfg)
            return target
        except OSError as exc:
            last_error = exc
            log.warning("写入配置失败：%s（%s），尝试下一个位置", target, exc)
    raise ConfigError(f"配置写入失败：{last_error}")
