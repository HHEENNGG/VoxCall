"""界面主题：浅色 / 深色 / 跟随系统。

Windows 的深浅色存在注册表 ``HKCU\\...\\Themes\\Personalize\\AppsUseLightTheme``
（1=浅色、0=深色）。Qt 6.5+ 也会通过 ``QStyleHints.colorScheme`` 暴露同一信息，
这里两者都试，注册表优先（更贴近系统设置页的实际状态）。

浅色与深色**共用同一份样式表**，只是配色不同——这样两套主题的控件尺寸完全一致，
不会出现菜单栏/按钮高矮不一的情况。
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

PALETTES: dict[str, dict[str, str]] = {
    "light": {
        "window": "#f4f5f7",
        "panel": "#ffffff",
        "field": "#ffffff",
        "field_alt": "#eef0f4",
        "text": "#1f2328",
        "muted": "#5c6675",
        "border": "#c8d0db",
        "indicator": "#9aa4b2",
        "button": "#ffffff",
        "button_hover": "#eef1f6",
        "button_pressed": "#e2e7ee",
        "button_disabled": "#b6bdc7",
        "highlight": "#2f6fd0",
        "highlight_border": "#2f6fd0",
        "menu": "#ffffff",
        "menu_hover": "#e6ecf6",
        "separator": "#dde2ea",
        "track": "#e9ecf1",
        "scroll": "#c3cad4",
        "tooltip_bg": "#ffffe1",
        "tooltip_text": "#1f2328",
    },
    "dark": {
        "window": "#1f2126",
        "panel": "#1f2126",
        "field": "#2c2f36",
        "field_alt": "#262a31",
        "text": "#e6e6e6",
        "muted": "#9aa4b2",
        "border": "#40444d",
        "indicator": "#6b7280",
        "button": "#2c2f36",
        "button_hover": "#373b44",
        "button_pressed": "#23262c",
        "button_disabled": "#6b7280",
        "highlight": "#3b6ea5",
        "highlight_border": "#8fc2ee",
        "menu": "#23262c",
        "menu_hover": "#373b44",
        "separator": "#3a3d44",
        "track": "#2c2f36",
        "scroll": "#4a4f59",
        "tooltip_bg": "#2c2f36",
        "tooltip_text": "#e6e6e6",
    },
}

# 兼容旧代码里的取色常量
MUTED = {name: colors["muted"] for name, colors in PALETTES.items()}
TRACK = {name: colors["track"] for name, colors in PALETTES.items()}
BORDER = {name: colors["border"] for name, colors in PALETTES.items()}

_STYLE = """
QWidget {{ background-color: {window}; color: {text}; }}
QMainWindow, QDialog {{ background-color: {window}; }}
QGroupBox {{
    border: 1px solid {separator}; border-radius: 7px;
    margin-top: 12px; padding-top: 6px;
    background-color: {panel};
}}
QGroupBox::title {{
    subcontrol-origin: margin; subcontrol-position: top left;
    left: 10px; padding: 0 4px; color: {muted};
}}
QLabel {{ background: transparent; }}
QCheckBox, QRadioButton {{ color: {text}; background: transparent; spacing: 7px; }}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 15px; height: 15px;
    border: 1px solid {indicator}; background-color: {field};
}}
QCheckBox::indicator {{ border-radius: 3px; }}
QRadioButton::indicator {{ border-radius: 8px; }}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background-color: {highlight}; border-color: {highlight_border};
}}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{
    border-color: {button_disabled}; background-color: {field_alt};
}}
QPushButton {{
    background-color: {button}; border: 1px solid {border};
    border-radius: 6px; padding: 5px 14px; min-height: 20px;
}}
QPushButton:hover {{ background-color: {button_hover}; }}
QPushButton:pressed {{ background-color: {button_pressed}; }}
QPushButton:disabled {{ color: {button_disabled}; border-color: {separator}; }}
QComboBox, QSpinBox, QLineEdit {{
    background-color: {field}; border: 1px solid {border};
    border-radius: 6px; padding: 4px 8px; min-height: 20px;
}}
QComboBox:hover, QSpinBox:hover {{ border-color: {indicator}; }}
QComboBox QAbstractItemView {{
    background-color: {field}; border: 1px solid {border};
    selection-background-color: {highlight}; selection-color: #ffffff;
}}
QComboBox::drop-down {{ border: none; width: 18px; }}
QMenuBar {{ background-color: {menu}; }}
/* 8px 垂直内边距：与原生菜单栏度量（33 px）一致 */
QMenuBar::item {{ background: transparent; padding: 8px 10px; }}
QMenuBar::item:selected {{ background-color: {menu_hover}; border-radius: 4px; }}
QMenu {{ background-color: {menu}; border: 1px solid {separator}; padding: 3px; }}
QMenu::item {{ padding: 3px 22px 3px 18px; border-radius: 4px; }}
QMenu::item:selected {{ background-color: {highlight}; color: #ffffff; }}
QMenu::separator {{ height: 1px; background: {separator}; margin: 4px 8px; }}
QToolTip {{
    background-color: {tooltip_bg}; color: {tooltip_text};
    border: 1px solid {border}; padding: 4px 6px;
}}
QProgressBar {{ border: 1px solid {border}; border-radius: 6px; background: {track}; }}
QScrollBar:vertical {{ background: {field_alt}; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {scroll}; border-radius: 5px; min-height: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
"""


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
    return PALETTES[resolve(pref)]["muted"]


def colors(pref: str) -> dict[str, str]:
    return PALETTES[resolve(pref)]


def stylesheet(pref: str) -> str:
    """两套主题共用同一份 QSS，保证控件尺寸完全一致。"""
    return _STYLE.format(**colors(pref))


_DEFAULT_PALETTE = None


def remember_default_palette(pal) -> None:
    """在应用启动时记下系统默认调色板（浅色的兜底）。"""
    global _DEFAULT_PALETTE
    _DEFAULT_PALETTE = pal


def _build_dark_palette():
    from PySide6.QtGui import QColor, QPalette

    c = PALETTES["dark"]
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(c["window"]))
    pal.setColor(QPalette.WindowText, QColor(c["text"]))
    pal.setColor(QPalette.Base, QColor(c["field"]))
    pal.setColor(QPalette.AlternateBase, QColor(c["field_alt"]))
    pal.setColor(QPalette.ToolTipBase, QColor(c["field"]))
    pal.setColor(QPalette.ToolTipText, QColor(c["text"]))
    pal.setColor(QPalette.Text, QColor(c["text"]))
    pal.setColor(QPalette.Button, QColor(c["field"]))
    pal.setColor(QPalette.ButtonText, QColor(c["text"]))
    pal.setColor(QPalette.BrightText, QColor("#ff6b6b"))
    pal.setColor(QPalette.Highlight, QColor(c["highlight"]))
    pal.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    pal.setColor(QPalette.Link, QColor("#6ea8fe"))
    pal.setColor(QPalette.PlaceholderText, QColor("#8b95a5"))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, QColor(c["button_disabled"]))
    return pal


def _build_light_palette():
    from PySide6.QtGui import QColor, QPalette

    c = PALETTES["light"]
    pal = QPalette(_DEFAULT_PALETTE) if _DEFAULT_PALETTE is not None else QPalette()
    pal.setColor(QPalette.Window, QColor(c["window"]))
    pal.setColor(QPalette.WindowText, QColor(c["text"]))
    pal.setColor(QPalette.Base, QColor(c["field"]))
    pal.setColor(QPalette.AlternateBase, QColor(c["field_alt"]))
    pal.setColor(QPalette.Text, QColor(c["text"]))
    pal.setColor(QPalette.Button, QColor(c["button"]))
    pal.setColor(QPalette.ButtonText, QColor(c["text"]))
    pal.setColor(QPalette.Highlight, QColor(c["highlight"]))
    pal.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    pal.setColor(QPalette.ToolTipBase, QColor(c["tooltip_bg"]))
    pal.setColor(QPalette.ToolTipText, QColor(c["tooltip_text"]))
    return pal


def palette(pref: str):
    """返回与样式表配套的 QPalette。"""
    return _build_dark_palette() if resolve(pref) == "dark" else _build_light_palette()
