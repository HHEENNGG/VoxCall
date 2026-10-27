"""检查更新：查 GitHub Releases，比较版本号。

设计原则：
- 绝不阻塞界面（在后台线程里跑，界面用轮询拿结果）
- 没网、限流、解析失败都当作「没有更新」，不弹错误
- 结果缓存（默认 12 小时），避免每次启动都打 API
"""

from __future__ import annotations

import json
import logging
import re
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import VERSION, settings

log = logging.getLogger(__name__)

REPO = "HHEENNGG/VoxCall"
RELEASES_URL = f"https://api.github.com/repos/{REPO}/releases?per_page=10"
RELEASE_PAGE = f"https://github.com/{REPO}/releases"
TIMEOUT_SECONDS = 8
CACHE_FILE = "update_check.json"


@dataclass
class Release:
    version: str          # 去掉前缀的版本号，如 "0.02"
    tag: str              # 原始 tag，如 "v0.02-beta"
    name: str
    page_url: str
    prerelease: bool
    published_at: str
    notes: str = ""


def parse_version(text: str) -> tuple[int, ...]:
    """把 "v0.02-beta" / "0.02" / "Beta 0.01" 变成 (0, 2)。取前两段数字足够。"""
    numbers = re.findall(r"\d+", text or "")
    parts = [int(n) for n in numbers[:2]] or [0]
    while len(parts) < 2:
        parts.append(0)
    return tuple(parts)


def is_newer(candidate: str, current: str) -> bool:
    return parse_version(candidate) > parse_version(current)


def _pick_latest(releases: list[dict]) -> dict | None:
    """在所有发布里挑版本号最大的（含 prerelease）。"""
    best: dict | None = None
    best_key: tuple[int, ...] = (0,)
    for item in releases:
        tag = str(item.get("tag_name") or "")
        if not tag or item.get("draft"):
            continue
        key = parse_version(tag)
        # 同版本时优先正式版
        if key > best_key or (key == best_key and best is not None and item.get("prerelease") is False):
            best, best_key = item, key
    return best


def _cache_path() -> Path:
    return settings.state_dir() / CACHE_FILE


def _read_cache(max_age_hours: float) -> dict | None:
    try:
        path = _cache_path()
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if time.time() - float(data.get("checked_at", 0)) > max_age_hours * 3600:
            return None
        return data
    except Exception:
        return None


def _write_cache(payload: dict) -> None:
    try:
        path = _cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception:
        log.debug("写更新缓存失败", exc_info=True)


def _fetch() -> list[dict]:
    request = urllib.request.Request(
        RELEASES_URL,
        headers={
            "User-Agent": "VoxCall-update-checker",
            "Accept": "application/vnd.github+json",
        },
    )
    context = ssl.create_default_context()
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS, context=context) as response:
        return json.loads(response.read().decode("utf-8"))


def check(use_cache: bool = True, max_age_hours: float = 12.0) -> Release | None:
    """返回「比当前版本新」的发布；没有更新（或检查失败）返回 None。"""
    cached = _read_cache(max_age_hours) if use_cache else None
    if cached is not None:
        latest_version = str(cached.get("latest_version") or "")
        if not latest_version or not is_newer(latest_version, VERSION):
            return None
        return Release(
            version=latest_version,
            tag=str(cached.get("tag") or ""),
            name=str(cached.get("name") or ""),
            page_url=str(cached.get("page_url") or RELEASE_PAGE),
            prerelease=bool(cached.get("prerelease")),
            published_at=str(cached.get("published_at") or ""),
        )

    try:
        releases = _fetch()
    except urllib.error.HTTPError as exc:
        log.info("检查更新失败（HTTP %s），稍后再试", exc.code)
        return None
    except Exception as exc:
        log.info("检查更新失败：%s", type(exc).__name__)
        return None

    item = _pick_latest(releases)
    if item is None:
        return None

    tag = str(item.get("tag_name") or "")
    payload = {
        "checked_at": time.time(),
        "latest_version": tag.lstrip("vV"),
        "tag": tag,
        "name": str(item.get("name") or tag),
        "page_url": str(item.get("html_url") or RELEASE_PAGE),
        "prerelease": bool(item.get("prerelease")),
        "published_at": str(item.get("published_at") or ""),
    }
    _write_cache(payload)

    if not is_newer(payload["latest_version"], VERSION):
        return None
    return Release(
        version=payload["latest_version"],
        tag=payload["tag"],
        name=payload["name"],
        page_url=payload["page_url"],
        prerelease=payload["prerelease"],
        published_at=payload["published_at"],
        notes=str(item.get("body") or ""),
    )
