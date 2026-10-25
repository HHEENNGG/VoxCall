"""「关于」对话框：版本、作者、许可与运行环境。"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QMessageBox


def _runtime_lines() -> list[str]:
    lines: list[str] = []
    try:
        lines.append(f"Python {sys.version.split()[0]}")
    except Exception:  # pragma: no cover
        pass
    try:
        import PySide6

        lines.append(f"Qt/PySide6 {PySide6.__version__}")
    except Exception:
        pass
    try:
        import onnxruntime

        lines.append(f"ONNX Runtime {onnxruntime.__version__}")
    except Exception:
        pass
    try:
        import voxlock

        version = getattr(voxlock, "__version__", "")
        lines.append(f"VoxLock {version}".strip())
    except Exception:
        lines.append("VoxLock 未安装（直通模式）")
    return lines


def about_text(config: dict | None = None) -> str:
    from . import AI_NOTE, APP_NAME, AUTHOR, CHANNEL, LICENSE, VERSION

    lines = [
        f"<h3>{APP_NAME} {CHANNEL} {VERSION}</h3>",
        "<p>实时通话降噪：用 VoxLock 做目标说话人隔离，再经 VB-CABLE 送入通话软件。</p>",
        f"<p><b>作者：</b>{AUTHOR}<br>"
        f"<span style='color:#666'>{AI_NOTE}</span><br>"
        f"<b>版本：</b>{CHANNEL} {VERSION}<br>"
        f"<b>许可：</b>{LICENSE}（VoxLock 模型为 Apache-2.0；"
        f"VB-CABLE 为 VB-Audio 的 donationware）</p>",
    ]
    runtime = _runtime_lines()
    if runtime:
        lines.append("<p><b>运行环境：</b><br>" + "<br>".join(runtime) + "</p>")

    if config:
        model = config.get("model", {})
        precision = model.get("precision", "?")
        mode = model.get("mode", "?")
        lines.append(f"<p><b>当前模式：</b>{mode}（精度 {precision}）</p>")

    lines.append(
        "<p><b>VB-CABLE：</b>© VB-Audio Software（donationware）。"
        "它是免费软件，觉得有用可以捐赠支持原作者。</p>"
    )
    lines.append(
        "<p style='color:#666'>本项目仅供个人使用；请遵守所在地关于录音与通话的法律法规。</p>"
    )
    return "".join(lines)


def show_about(parent, config: dict | None = None) -> None:
    from PySide6.QtCore import Qt, QUrl
    from PySide6.QtGui import QDesktopServices

    from . import APP_NAME
    from .vbcable import DONATE_URL

    box = QMessageBox(parent)
    box.setWindowTitle(f"关于 {APP_NAME}")
    box.setIcon(QMessageBox.Information)
    box.setTextFormat(Qt.RichText)
    box.setText(about_text(config))
    donate_btn = box.addButton("捐赠 VB-CABLE", QMessageBox.ActionRole)
    box.addButton("确定", QMessageBox.AcceptRole)  # 保持中文界面
    box.exec()
    if box.clickedButton() is donate_btn:
        QDesktopServices.openUrl(QUrl(DONATE_URL))
