"""设置对话框：启动与托盘、音频延迟、模型。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QSpinBox,
    QVBoxLayout,
)

from . import settings

MODE_CHOICES = [
    ("auto", "自动识别（开始后说 3 秒）"),
    ("calibrated", "校准 3 秒（每次开始都录）"),
    ("fixed", "固定声纹（启动即锁定）"),
]
PRECISION_CHOICES = [
    ("int8-full", "int8-full（最快，推荐）"),
    ("int8", "int8"),
    ("fp16", "fp16（更准，更慢）"),
    ("fp32", "fp32（最准，最慢）"),
]


class SettingsDialog(QDialog):
    def __init__(self, config: dict, parent=None) -> None:
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("设置")
        self.setMinimumWidth(460)

        app_cfg = config.setdefault("app", {})
        audio_cfg = config.setdefault("audio", {})
        model_cfg = config.setdefault("model", {})

        root = QVBoxLayout(self)

        # ---- 启动与托盘
        tray_box = QGroupBox("启动与托盘")
        tray_form = QFormLayout(tray_box)
        self.close_to_tray = QCheckBox("点关闭时最小化到托盘（程序继续在后台运行）")
        self.close_to_tray.setChecked(bool(app_cfg.get("close_to_tray", True)))
        tray_form.addRow(self.close_to_tray)

        self.start_minimized = QCheckBox("启动后直接最小化到托盘")
        self.start_minimized.setChecked(bool(app_cfg.get("start_minimized", False)))
        tray_form.addRow(self.start_minimized)

        self.check_updates = QCheckBox("启动后自动检查更新")
        self.check_updates.setChecked(bool(app_cfg.get("check_updates", True)))
        tray_form.addRow(self.check_updates)

        self.update_hours = QSpinBox()
        self.update_hours.setRange(1, 168)
        self.update_hours.setSuffix(" 小时")
        self.update_hours.setValue(int(app_cfg.get("update_check_hours", 12) or 12))
        self.update_hours.setToolTip("自动检查的缓存时长，避免每次启动都联网")
        tray_form.addRow("更新检查间隔", self.update_hours)
        root.addWidget(tray_box)

        # ---- 音频
        audio_box = QGroupBox("音频（下次点「开始」生效）")
        audio_form = QFormLayout(audio_box)
        self.chunk_ms = QSpinBox()
        self.chunk_ms.setRange(20, 200)
        self.chunk_ms.setSingleStep(10)
        self.chunk_ms.setSuffix(" ms")
        self.chunk_ms.setValue(int(audio_cfg.get("chunk_ms", 60)))
        self.chunk_ms.setToolTip("采集块长度：越小延迟越低，CPU 压力越大")
        audio_form.addRow("采集块", self.chunk_ms)

        self.target_ms = QSpinBox()
        self.target_ms.setRange(40, 500)
        self.target_ms.setSingleStep(10)
        self.target_ms.setSuffix(" ms")
        self.target_ms.setValue(int(audio_cfg.get("target_latency_ms", 100)))
        self.target_ms.setToolTip("播放缓冲目标：越小延迟越低，越容易欠载")
        audio_form.addRow("缓冲目标", self.target_ms)
        hint = QLabel("也可以在主界面用「延迟档位」一键切换")
        hint.setStyleSheet("color:#666;")
        audio_form.addRow("", hint)
        root.addWidget(audio_box)

        # ---- 模型
        model_box = QGroupBox("模型（下次启动生效）")
        model_form = QFormLayout(model_box)
        self.mode_combo = QComboBox()
        for value, label in MODE_CHOICES:
            self.mode_combo.addItem(label, value)
        mode = str(model_cfg.get("mode", "auto"))
        index = self.mode_combo.findData(mode)
        self.mode_combo.setCurrentIndex(index if index >= 0 else 0)
        model_form.addRow("锁定方式", self.mode_combo)

        self.precision_combo = QComboBox()
        for value, label in PRECISION_CHOICES:
            self.precision_combo.addItem(label, value)
        precision = str(model_cfg.get("precision", "int8-full"))
        index = self.precision_combo.findData(precision)
        self.precision_combo.setCurrentIndex(index if index >= 0 else 0)
        model_form.addRow("推理精度", self.precision_combo)
        root.addWidget(model_box)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, Qt.Horizontal, self
        )
        buttons.button(QDialogButtonBox.Ok).setText("保存")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def apply(self) -> list[str]:
        """把界面值写回 config 并保存，返回需要重启才能生效的项。"""
        app_cfg = self.config.setdefault("app", {})
        audio_cfg = self.config.setdefault("audio", {})
        model_cfg = self.config.setdefault("model", {})

        app_cfg["close_to_tray"] = self.close_to_tray.isChecked()
        app_cfg["start_minimized"] = self.start_minimized.isChecked()
        app_cfg["check_updates"] = self.check_updates.isChecked()
        app_cfg["update_check_hours"] = int(self.update_hours.value())

        audio_cfg["chunk_ms"] = int(self.chunk_ms.value())
        audio_cfg["target_latency_ms"] = int(self.target_ms.value())

        model_cfg["mode"] = str(self.mode_combo.currentData() or "auto")
        model_cfg["precision"] = str(self.precision_combo.currentData() or "int8-full")

        settings.save_config(self.config)
        return ["模型设置"]
