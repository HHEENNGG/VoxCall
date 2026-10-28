"""界面主题：浅色 / 深色 / 跟随系统。

Windows 的深浅色存在注册表 ``HKCU\\...\\Themes\\Personalize\\AppsUseLightTheme``
（1=浅色、0=深色）。Qt 6.5+ 也会通过 ``QStyleHints.colorScheme`` 暴露同一信息，
这里两者都试，注册表优先（更贴近系统设置页的实际状态）。
"""

from __future__ import annotations

import logging
import os
from typing import Literal

log = logging.getLogger(__name__)

Scheme = Literal["light", "dark"]

CHOICES = [
    ("auto", "跟随系统"),
    ("light", "浅色"),
    ("dark", "深色"),
]
PREFS = tuple(value for value, _label in CHOICES)

MUTED = {"light": "#666666", "dark": "#9aa4b2"}
TRACK = {"light": "#f2f4f8", "dark": "#2c2f36"}
BORDER = {"light": "#cfd6e0", "dark": "#40444d"}


def system_is_dark() -> bool:
    """系统当前是否为深色。取不到就当浅色。"""
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            ) as key:
                value, _kind = winreg.QueryValueEx(key, "AppsUseLightTheme")
                return int(value) == 0
        except Exception:
            log.debug("读注册表判断深浅色失败", exc_info=True)
    try:  # 退路：Qt 自己的判断
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QGuiApplication

        hints = QGuiApplication.styleHints()
        return hints.colorScheme() == Qt.ColorScheme.Dark
    except Exception:
        return False


def resolve(pref: str) -> Scheme:
    """把配置里的 auto/light/dark 解析成具体方案。"""
    pref = (pref or "auto").lower()
    if pref == "dark":
        return "dark"
    if pref == "light":
        return "light"
    return "dark" if system_is_dark() else "light"


def muted(pref: str) -> str:
    return MUTED[resolve(pref)]


STYLE_DARK = """
QWidget { background-color: #1f2126; color: #e6e6e6; }
QMainWindow, QDialog { background-color: #1f2126; }
QGroupBox {
    border: 1px solid #3a3d44; border-radius: 7px;
    margin-top: 12px; padding-top: 6px;
}
QGroupBox::title {
    subcontrol-origin: margin; subcontrol-position: top left;
    left: 10px; padding: 0 4px; color: #aab4c2;
}
QLabel { background: transparent; }
QCheckBox, QRadioButton { color: #e6e6e6; background: transparent; spacing: 7px; }
QCheckBox::indicator, QRadioButton::indicator {
    width: 15px; height: 15px;
    border: 1px solid #6b7280; background-color: #2c2f36;
}
QCheckBox::indicator { border-radius: 3px; }
QRadioButton::indicator { border-radius: 8px; }
QCheckBox::indicator:checked, QRadioButton::indicator:checked {
    background-color: #5b9bd5; border-color: #8fc2ee;
}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {
    border-color: #3a3d44; background-color: #23262c;
}
QPushButton {
    background-color: #2c2f36; border: 1px solid #40444d;
    border-radius: 6px; padding: 5px 14px; min-height: 20px;
}
QPushButton:hover { background-color: #373b44; }
QPushButton:pressed { background-color: #23262c; }
QPushButton:disabled { color: #6b7280; border-color: #2f333a; }
QComboBox, QSpinBox, QLineEdit {
    background-color: #2c2f36; border: 1px solid #40444d;
    border-radius: 6px; padding: 4px 8px; min-height: 20px;
}
QComboBox:hover, QSpinBox:hover { border-color: #4d525c; }
QComboBox QAbstractItemView {
    background-color: #2c2f36; border: 1px solid #40444d;
    selection-background-color: #3b6ea5; selection-color: #ffffff;
}
QComboBox::drop-down { border: none; width: 18px; }
QMenuBar { background-color: #23262c; }
QMenuBar::item { background: transparent; padding: 4px 10px; }
QMenuBar::item:selected { background-color: #373b44; border-radius: 4px; }
QMenu { background-color: #23262c; border: 1px solid #3a3d44; padding: 4px; }
QMenu::item { padding: 5px 22px 5px 18px; border-radius: 4px; }
QMenu::item:selected { background-color: #3b6ea5; }
QMenu::separator { height: 1px; background: #3a3d44; margin: 4px 8px; }
QToolTip {
    background-color: #2c2f36; color: #e6e6e6;
    border: 1px solid #40444d; padding: 4px 6px;
}
QProgressBar { border: 1px solid #40444d; border-radius: 6px; background: #2c2f36; }
QScrollBar:vertical { background: #23262c; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: #4a4f59; border-radius: 5px; min-height: 24px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
"""


def stylesheet(pref: str) -> str:
    return STYLE_DARK if resolve(pref) == "dark" else ""


_DEFAULT_PALETTE = None


def remember_default_palette(pal) -> None:
    """在应用启动时记下系统默认调色板，切回浅色时用它（比 standardPalette 更准）。"""
    global _DEFAULT_PALETTE
    _DEFAULT_PALETTE = pal


def palette(pref: str):
    """返回 QPalette；深色时给出完整调色板，浅色时用系统默认调色板。"""
    from PySide6.QtGui import QColor, QPalette
    from PySide6.QtWidgets import QApplication

    scheme = resolve(pref)
    if scheme == "light":
        if _DEFAULT_PALETTE is not None:
            return _DEFAULT_PALETTE
        return QApplication.style().standardPalette()

    pal = QPalette()
    window = QColor("#1f2126")
    base = QColor("#2c2f36")
    text = QColor("#e6e6e6")
    disabled = QColor("#6b7280")
    pal.setColor(QPalette.Window, window)
    pal.setColor(QPalette.WindowText, text)
    pal.setColor(QPalette.Base, base)
    pal.setColor(QPalette.AlternateBase, QColor("#262a31"))
    pal.setColor(QPalette.ToolTipBase, base)
    pal.setColor(QPalette.ToolTipText, text)
    pal.setColor(QPalette.Text, text)
    pal.setColor(QPalette.Button, QColor("#2c2f36"))
    pal.setColor(QPalette.ButtonText, text)
    pal.setColor(QPalette.BrightText, QColor("#ff6b6b"))
    pal.setColor(QPalette.Highlight, QColor("#3b6ea5"))
    pal.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    pal.setColor(QPalette.Link, QColor("#6ea8fe"))
    pal.setColor(QPalette.PlaceholderText, QColor("#8b95a5"))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, disabled)
    return pal
