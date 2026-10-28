"""把 README 里的版本标记替换成指定 tag 对应的版本。

用法：
    python tools/bump_readme_version.py v0.03-beta      # 指定 tag
    python tools/bump_readme_version.py --latest         # 取 GitHub 最新 release（需要 gh + 网络）

README 里用注释做标记，正文写在两段注释之间：
    <!-- version -->Beta 0.02<!-- /version -->
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

README = Path(__file__).resolve().parents[1] / "README.md"
MARKER = r"<!-- version -->.*?<!-- /version -->"


def version_text(tag: str) -> str:
    """v0.03-beta -> "Beta 0.03"；v0.03 -> "0.03"；v1.2-rc1 -> "RC 1.2"。"""
    match = re.match(r"[vV]?(\d+(?:\.\d+)*)(?:[-_]([A-Za-z]+)\d*)?", (tag or "").strip())
    if not match:
        raise ValueError(f"无法解析版本 tag：{tag!r}")
    number = match.group(1)
    suffix = (match.group(2) or "").lower()
    channel = {"beta": "Beta", "alpha": "Alpha", "rc": "RC"}.get(suffix, "")
    return f"{channel} {number}".strip()


def bump(path: Path, tag: str) -> tuple[bool, str]:
    """返回 (是否改动, 新版本文本)。"""
    text = version_text(tag)
    source = path.read_text(encoding="utf-8")
    replaced, count = re.subn(MARKER, f"<!-- version -->{text}<!-- /version -->", source, flags=re.S)
    if count == 0:
        raise SystemExit(f"{path} 里没有版本标记 {MARKER}")
    changed = replaced != source
    if changed:
        path.write_text(replaced, encoding="utf-8", newline="\n")
    return changed, text


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    tag = argv[1]
    if tag == "--latest":
        import json
        import subprocess

        out = subprocess.run(
            ["gh", "api", "repos/HHEENNGG/VoxCall/releases/latest", "--jq", ".tag_name"],
            capture_output=True,
            text=True,
            check=True,
        )
        tag = out.stdout.strip()
    changed, text = bump(README, tag)
    print(f"{'已更新' if changed else '无需改动'}：版本 = {text}（tag {tag}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
