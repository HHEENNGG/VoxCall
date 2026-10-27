"""PySide6 主界面。

界面只用 QTimer 每 100 ms 拉一次引擎快照，不跨线程绑定信号，
因此状态文本、电平、延迟都由同一份线程安全快照驱动。
"""

from __future__ import annotations

import logging
import math
import threading
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from . import devices as devices_mod
from . import settings, voiceprint
from .engine import AudioEngine, EngineError

log = logging.getLogger(__name__)

# 延迟档位：(值, 说明, audio 配置覆盖)。数字为本机真设备 + int8-full 实测。
LATENCY_PROFILES = [
    ("low", "低延迟（≈180–210 ms，块更小，可能偶发断续）",
     {"chunk_ms": 40, "capture_block_ms": 10, "playback_block_ms": 10, "target_latency_ms": 60}),
    ("balanced", "均衡（≈200 ms，推荐）",
     {"chunk_ms": 60, "capture_block_ms": 10, "playback_block_ms": 10, "target_latency_ms": 100}),
    ("stable", "稳定（≈240 ms，声音断续或 CPU 吃力时用）",
     {"chunk_ms": 80, "capture_block_ms": 10, "playback_block_ms": 10, "target_latency_ms": 120}),
]


class MainWindow(QMainWindow):
    def __init__(self, config: dict | None = None) -> None:
        super().__init__()
        from . import CHANNEL, VERSION

        self.setWindowTitle(f"VoxCall {CHANNEL} {VERSION} · 实时通话降噪")
        self.resize(760, 580)
        self.config = config if config is not None else settings.load_config()
        self.engine = AudioEngine(self.config)
        self._loading = True  # 初始化期间不触发保存
        self._save_error_shown = False
        self._perf = None
        self._perf_stats = {"cpu_percent": 0.0, "mem_mb": 0.0}
        self._perf_at = 0.0
        self._bar_band = ""
        self._last_state = "idle"
        self._record_job: dict | None = None
        self._install_job: dict | None = None
        self._installer_watch: dict | None = None
        self._update_job: dict | None = None
        self._vbcable_prompted = False

        self._build_menu()

        try:  # 窗口图标（任务栏、Alt-Tab）
            from PySide6.QtGui import QIcon

            from .paths import assets_dir

            for name in ("icon.ico", "icon-256.png", "icon.png"):
                icon_path = assets_dir() / name
                if icon_path.exists():
                    self.setWindowIcon(QIcon(str(icon_path)))
                    break
        except Exception:
            pass

        self._init_tray()

        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setSpacing(10)

        intro = QLabel(
            "VoxLock 目标说话人隔离 + VB-CABLE 虚拟麦克风。\n"
            "模型锁定目标说话人之前不会外放任何声音，避免把旁人的话送进通话。"
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        # ---- 设备 ----
        box = QGroupBox("音频设备")
        box_layout = QVBoxLayout(box)
        box_layout.addWidget(QLabel("驱动接口（Host API）：同一个设备会在每个驱动下各出现一次，选一个即可"))
        self.hostapi_combo = QComboBox()
        for value, label in devices_mod.hostapi_choices():
            self.hostapi_combo.addItem(label, value)
        wanted_api = str(self.config["audio"].get("hostapi", "auto") or "auto")
        api_index = self.hostapi_combo.findData(wanted_api)
        self.hostapi_combo.setCurrentIndex(max(0, api_index))
        box_layout.addWidget(self.hostapi_combo)

        box_layout.addWidget(QLabel("延迟档位（越小延迟越低，也越容易受 CPU 影响）"))
        self.profile_combo = QComboBox()
        for value, label, _overrides in LATENCY_PROFILES:
            self.profile_combo.addItem(label, value)
        self.profile_combo.addItem("自定义（config.toml 里的值）", "custom")
        box_layout.addWidget(self.profile_combo)

        box_layout.addWidget(QLabel("输入设备（你的真实麦克风）"))
        self.input_combo = QComboBox()
        box_layout.addWidget(self.input_combo)
        box_layout.addWidget(QLabel("输出设备（选 CABLE Input）"))
        self.output_combo = QComboBox()
        box_layout.addWidget(self.output_combo)

        row = QHBoxLayout()
        self.refresh_btn = QPushButton("刷新设备")
        self.vbcable_btn = QPushButton("安装 VB-CABLE")
        self.vbcable_btn.setToolTip("没检测到 VB-CABLE 时，自动下载官方安装包并以管理员身份安装")
        self.vbcable_btn.setVisible(False)
        self.start_btn = QPushButton("开始")
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setEnabled(False)
        row.addWidget(self.refresh_btn)
        row.addWidget(self.vbcable_btn)
        row.addStretch(1)
        row.addWidget(self.start_btn)
        row.addWidget(self.stop_btn)
        box_layout.addLayout(row)
        layout.addWidget(box)

        # ---- 模式 ----
        mode_box = QGroupBox("目标说话人")
        mode_layout = QVBoxLayout(mode_box)
        self.auto_radio = QRadioButton("自动识别（开始后说 3 秒左右，模型自己锁定）")
        self.calib_radio = QRadioButton("校准 3 秒（每次启动都要重新说 3 秒）")
        self.fixed_radio = QRadioButton("固定声纹（用已保存的样本，启动即锁定，不用每次重新识别）")
        mode_layout.addWidget(self.auto_radio)
        mode_layout.addWidget(self.calib_radio)
        mode_layout.addWidget(self.fixed_radio)

        vp_row = QHBoxLayout()
        self.record_btn = QPushButton("录制/更新声纹…")
        self.clear_vp_btn = QPushButton("清除声纹")
        self.vp_label = QLabel("声纹：未录制")
        self.vp_label.setWordWrap(True)
        vp_row.addWidget(self.record_btn)
        vp_row.addWidget(self.clear_vp_btn)
        vp_row.addWidget(self.vp_label, 1)
        mode_layout.addLayout(vp_row)

        mode = str(self.config["model"].get("mode", "auto"))
        if mode == "calibrated":
            self.calib_radio.setChecked(True)
        elif mode == "fixed":
            self.fixed_radio.setChecked(True)
        else:
            self.auto_radio.setChecked(True)
        layout.addWidget(mode_box)

        self.gate_check = QCheckBox("启用轻度频谱门（只压风扇/空调类稳态底噪，可能影响音质）")
        self.gate_check.setChecked(bool(self.config["denoise"].get("enabled", False)))
        self.gate_check.toggled.connect(self._toggle_gate)
        layout.addWidget(self.gate_check)

        # ---- 电平 ----
        level_box = QGroupBox("电平")
        level_layout = QVBoxLayout(level_box)
        level_layout.setSpacing(6)

        def make_level_row(title: str, alert_text: str):
            head = QHBoxLayout()
            name = QLabel(title)
            name.setStyleSheet("font-weight:600;")
            peak = QLabel("—")
            peak.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            peak.setMinimumWidth(150)
            peak.setStyleSheet("color:#666; font-family:Consolas,monospace;")
            head.addWidget(name)
            head.addStretch(1)
            head.addWidget(peak)
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(0)
            bar.setTextVisible(False)
            bar.setFixedHeight(22)
            bar.setMinimumWidth(300)
            bar.setStyleSheet(
                "QProgressBar { border:1px solid #cfd6e0; border-radius:6px; "
                "background:#f2f4f8; }"
                "QProgressBar::chunk { border-radius:5px; background:#38c172; }"
            )
            level_layout.addLayout(head)
            level_layout.addWidget(bar)
            return bar, peak, alert_text

        self.in_bar, self.in_peak_label, self._in_alert = make_level_row(
            "输入（麦克风）", "过载"
        )
        self.out_bar, self.out_peak_label, self._out_alert = make_level_row(
            "输出（送往通话软件）", "过载"
        )
        layout.addWidget(level_box)

        self.status_label = QLabel("状态：就绪")
        self.status_label.setWordWrap(True)
        self.status_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.status_label)

        self.info_label = QLabel("")
        self.info_label.setWordWrap(True)
        layout.addWidget(self.info_label)

        help_label = QLabel(
            "VB-CABLE 方向：本程序 → CABLE Input；Discord/QQ/游戏语音的麦克风 → CABLE Output。\n"
            "VoxLock 主要抑制其他说话人，不保证消除键盘敲击等瞬态噪声。"
        )
        help_label.setWordWrap(True)
        help_label.setStyleSheet("color: #666;")
        layout.addWidget(help_label)

        self.setCentralWidget(root)
        self.refresh_btn.clicked.connect(self.refresh_devices)
        self.vbcable_btn.clicked.connect(self.install_vbcable)
        self.hostapi_combo.currentIndexChanged.connect(self._on_hostapi_changed)
        self.profile_combo.setCurrentIndex(self._detect_profile())
        self.profile_combo.currentIndexChanged.connect(self._on_profile_changed)
        self.input_combo.currentIndexChanged.connect(self._on_device_changed)
        self.output_combo.currentIndexChanged.connect(self._on_device_changed)
        for radio in (self.auto_radio, self.calib_radio, self.fixed_radio):
            radio.toggled.connect(self._on_mode_toggled)
        self.start_btn.clicked.connect(self.start_engine)
        self.stop_btn.clicked.connect(self.stop_engine)
        self.record_btn.clicked.connect(self.record_voiceprint)
        self.clear_vp_btn.clicked.connect(self.clear_voiceprint)

        try:
            from .perf import ProcessMonitor

            self._perf = ProcessMonitor() if ProcessMonitor().available else None
        except Exception:
            self._perf = None

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(100)
        self.refresh_devices()
        self.refresh_voiceprint_label()
        self._loading = False
        QTimer.singleShot(400, self._maybe_prompt_vbcable)
        if bool(self.config.get("app", {}).get("check_updates", True)):
            QTimer.singleShot(3000, lambda: self.check_updates(manual=False))

    # ------------------------------------------------------------ 配置保存
    def _save_config(self, reason: str = "") -> None:
        """把当前配置写盘；失败时提示一次并写日志（不再静默丢设置）。"""
        if self._loading:
            return
        try:
            settings.save_config(self.config)
        except Exception as exc:
            log.warning("保存配置失败（%s）", reason, exc_info=True)
            if not self._save_error_shown:
                self._save_error_shown = True
                self.status_label.setText(f"状态：设置保存失败（{exc}）")
                QMessageBox.warning(
                    self,
                    "设置无法保存",
                    f"写入配置文件失败：{exc}\n\n"
                    f"程序会继续运行，但下次启动可能读不到这次改动。\n"
                    f"可尝试把程序放到有写权限的目录（例如非 Program Files）。",
                )

    def _on_device_changed(self, _index: int = 0) -> None:
        if self._loading or self.engine.running:
            return
        device = self._selected(self.input_combo if self.sender() is self.input_combo else self.output_combo)
        if device is None:
            return
        if self.sender() is self.input_combo:
            self.config["audio"]["input_device"] = device.name
        else:
            self.config["audio"]["output_device"] = device.name
        self._save_config("设备选择")

    def _on_mode_toggled(self, checked: bool) -> None:
        if self._loading or not checked:
            return
        self.config["model"]["mode"] = self._mode()
        self._save_config("锁定方式")

    # ------------------------------------------------------------ VB-CABLE
    def _vbcable_present(self) -> bool:
        from . import vbcable

        return vbcable.is_installed()

    def _update_vbcable_state(self) -> None:
        installed = self._vbcable_present()
        self.vbcable_btn.setVisible(not installed)
        if installed:
            return
        self.status_label.setText(
            "状态：未检测到 VB-CABLE（把声音送进通话软件需要它）——点「安装 VB-CABLE」可自动下载安装"
        )

    def _maybe_prompt_vbcable(self) -> None:
        if self._vbcable_present() or getattr(self, "_vbcable_prompted", False):
            return
        self._vbcable_prompted = True
        answer = QMessageBox.question(
            self,
            "缺少 VB-CABLE",
            "没有检测到 VB-CABLE 虚拟声卡。\n\n"
            "它是把处理后的声音送进 Discord / QQ / 游戏语音的必需组件（系统级驱动，"
            "需要管理员权限并重启一次）。\n\n"
            "现在自动下载并安装吗？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if answer == QMessageBox.Yes:
            self.install_vbcable()

    def install_vbcable(self) -> None:
        from . import vbcable

        if self._install_job is not None and not self._install_job.get("done"):
            return
        answer = QMessageBox.question(
            self,
            "安装 VB-CABLE",
            "将从官方站点 vb-audio.com 下载 VB-CABLE 安装包（约 1.3 MB），"
            "然后以管理员身份启动安装程序。\n\n"
            "安装过程需要你在系统提示里确认（UAC），装完需要重启电脑。\n\n"
            + vbcable.LICENSE_NOTE
            + "\n\n继续吗？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if answer != QMessageBox.Yes:
            return

        self.vbcable_btn.setEnabled(False)
        self.status_label.setText("状态：正在下载 VB-CABLE 安装包…")
        job: dict = {"done": False, "error": None, "setup": None, "message": "正在下载 VB-CABLE 安装包…"}
        self._install_job = job

        def work() -> None:
            try:
                job["setup"] = vbcable.download_installer(
                    progress=lambda msg: job.update(message=msg)
                )
            except Exception as exc:  # 线程里不能抛到 Qt
                job["error"] = exc
            finally:
                job["done"] = True

        threading.Thread(target=work, name="VBCableDownload", daemon=True).start()

    def _poll_install_job(self) -> None:
        from . import vbcable

        job = self._install_job
        if job is None:
            return
        if not job.get("done"):
            message = job.get("message")
            if message:
                self.status_label.setText("状态：" + str(message))
            return
        self._install_job = None
        self.vbcable_btn.setEnabled(True)
        if job.get("error") is not None:
            QMessageBox.warning(self, "下载失败", str(job["error"]))
            self.status_label.setText("状态：VB-CABLE 下载失败，可到 vb-audio.com 手动下载")
            return
        try:
            vbcable.run_installer(job["setup"])
        except vbcable.VBCableError as exc:
            QMessageBox.warning(self, "启动安装失败", str(exc))
            return
        QMessageBox.information(
            self,
            "安装程序已启动",
            "已以管理员身份启动 VB-CABLE 安装程序。\n\n"
            "1. 在安装窗口里点 Install\n"
            "2. 安装窗口关闭后，VoxCall 会问你要不要立即重启电脑\n"
            "3. 重启后重新打开 VoxCall，输出设备里就会出现 CABLE Input",
        )
        self.status_label.setText("状态：安装程序已启动，完成后会提示重启")
        self._installer_watch = {"seen": False, "started": time.time()}

    def _poll_installer_exit(self) -> None:
        """等官方安装程序退出，然后询问是否立即重启。"""
        watch = self._installer_watch
        if watch is None:
            return
        from . import vbcable

        if vbcable.installer_running():
            watch["seen"] = True
            self.status_label.setText("状态：VB-CABLE 安装程序运行中…")
            return
        if not watch["seen"]:
            if time.time() - watch["started"] < 90:
                return  # 可能还没弹出来，再等等
            self._installer_watch = None
            self.status_label.setText("状态：没有检测到安装程序运行（可能取消了安装）")
            return
        self._installer_watch = None
        self.status_label.setText("状态：安装程序已结束，建议重启电脑后再使用")
        self._ask_restart()

    def _ask_restart(self) -> None:
        from . import vbcable

        box = QMessageBox(self)
        box.setWindowTitle("安装完成")
        box.setIcon(QMessageBox.Question)
        box.setText(
            "VB-CABLE 安装程序已经结束。\n\n"
            "驱动需要重启电脑后才会生效（重启后 CABLE Input 才会出现在设备列表里）。\n"
            "现在重启吗？"
        )
        restart_btn = box.addButton("立即重启", QMessageBox.AcceptRole)
        box.addButton("稍后", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is not restart_btn:
            self.status_label.setText("状态：已安装，记得稍后重启电脑再使用")
            return
        try:
            vbcable.restart_computer(15)
        except vbcable.VBCableError as exc:
            QMessageBox.warning(self, "无法重启", str(exc))
            return
        QMessageBox.information(
            self,
            "即将重启",
            "电脑将在 15 秒后重启。\n\n如需取消，请运行命令：shutdown /a",
        )

    # ------------------------------------------------------------------ 菜单
    def _build_menu(self) -> None:
        bar = self.menuBar()

        file_menu = bar.addMenu("文件")
        settings_action = file_menu.addAction("设置…")
        settings_action.setShortcut("Ctrl+,")
        settings_action.triggered.connect(self.open_settings)
        file_menu.addSeparator()
        quit_action = file_menu.addAction("退出")
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.quit_app)

        tools_menu = bar.addMenu("工具")
        self.update_action = tools_menu.addAction("检查更新…")
        self.update_action.triggered.connect(lambda: self.check_updates(manual=True))
        self.vbcable_action = tools_menu.addAction("安装 VB-CABLE…")
        self.vbcable_action.triggered.connect(self.install_vbcable)
        tools_menu.addSeparator()
        tools_menu.addAction("打开配置文件夹", self.open_config_folder)
        tools_menu.addAction("打开日志文件夹", self.open_log_folder)

        help_menu = bar.addMenu("帮助")
        help_menu.addAction("关于 VoxCall", self.show_about)

    # ------------------------------------------------------------ 设置
    def open_settings(self) -> None:
        from .settings_dialog import SettingsDialog

        dialog = SettingsDialog(self.config, self)
        if dialog.exec() != dialog.Accepted:
            return
        try:
            restart_needed = dialog.apply()
        except Exception as exc:
            QMessageBox.warning(self, "保存设置失败", str(exc))
            return
        # 立即生效的部分
        self._update_tray_actions()
        if not self.config.get("app", {}).get("check_updates", True):
            self.status_label.setText("状态：已关闭自动更新检查")
        elif restart_needed:
            self.status_label.setText("状态：设置已保存（模型/音频改动在下次开始时生效）")
        else:
            self.status_label.setText("状态：设置已保存")

    # ------------------------------------------------------------ 文件夹
    def open_config_folder(self) -> None:
        from . import shell

        folder = settings.active_config_path().parent
        if not shell.open_path(folder):
            self.status_label.setText(f"状态：无法打开文件夹 {folder}")

    def open_log_folder(self) -> None:
        from . import shell
        from .paths import ROOT

        folder = ROOT / "logs"
        folder.mkdir(parents=True, exist_ok=True)
        if not shell.open_path(folder):
            self.status_label.setText(f"状态：无法打开文件夹 {folder}")

    # ------------------------------------------------------------ 托盘
    def _init_tray(self) -> None:
        self._tray = None
        self._tray_hint_shown = False
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        tray = QSystemTrayIcon(self.windowIcon(), self)
        tray.setToolTip(f"VoxCall {self.windowTitle().split('·')[0].strip()}")

        menu = QMenu(self)
        self._tray_show_action = menu.addAction("显示主窗口", self.show_window)
        menu.addSeparator()
        self._tray_toggle_action = menu.addAction("开始降噪", self.toggle_engine)
        menu.addSeparator()
        menu.addAction("检查更新…", lambda: self.check_updates(manual=True))
        menu.addAction("设置…", self.open_settings)
        menu.addSeparator()
        menu.addAction("退出 VoxCall", self.quit_app)
        tray.setContextMenu(menu)
        tray.activated.connect(self._on_tray_activated)
        tray.messageClicked.connect(self.show_window)
        tray.show()
        self._tray = tray

    def _on_tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.show_window()

    def _update_tray_actions(self) -> None:
        if getattr(self, "_tray", None) is None:
            return
        running = self.engine.running
        self._tray_toggle_action.setText("停止降噪" if running else "开始降噪")
        self._tray.setToolTip(
            "VoxCall Beta 0.01 · " + ("运行中" if running else "就绪")
        )

    def show_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def toggle_engine(self) -> None:
        if self.engine.running:
            self.stop_engine()
        else:
            self.start_engine()

    def quit_app(self) -> None:
        try:
            if self.engine.running:
                self.engine.stop(timeout=3.0)
        finally:
            self.timer.stop()
            if getattr(self, "_tray", None) is not None:
                self._tray.hide()
            QApplication.quit()

    # ------------------------------------------------------------ 检查更新
    def check_updates(self, manual: bool = False) -> None:
        from . import updater

        job = self._update_job
        if job is not None and not job.get("done"):
            if manual:
                self.status_label.setText("状态：正在检查更新…")
            return
        if manual:
            self.update_action.setEnabled(False)
            self.status_label.setText("状态：正在检查更新…")
        job = {"done": False, "result": None, "error": None, "manual": manual}
        self._update_job = job

        def work() -> None:
            try:
                # 手动检查忽略缓存，自动检查走 12 小时缓存
                job["result"] = updater.check(use_cache=not manual)
            except Exception as exc:  # 线程里不能抛到 Qt
                job["error"] = exc
            finally:
                job["done"] = True

        threading.Thread(target=work, name="UpdateCheck", daemon=True).start()

    def _poll_update_job(self) -> None:
        job = self._update_job
        if job is None or not job.get("done"):
            return
        self._update_job = None
        self.update_action.setEnabled(True)
        manual = bool(job.get("manual"))
        result = job.get("result")

        if job.get("error") is not None:
            if manual:
                QMessageBox.warning(self, "检查更新失败", str(job["error"]))
            self.status_label.setText("状态：检查更新失败（网络问题？）")
            return

        if result is None:
            if manual:
                from . import VERSION

                QMessageBox.information(self, "检查更新", f"当前已是最新版本（Beta {VERSION}）。")
            self.status_label.setText("状态：已是最新版本")
            return

        self.status_label.setText(f"状态：发现新版本 {result.tag}，可在「帮助 → 检查更新」查看")
        self._show_update_dialog(result)

    def _show_update_dialog(self, release) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        box = QMessageBox(self)
        box.setWindowTitle("发现新版本")
        box.setIcon(QMessageBox.Information)
        lines = [f"<b>VoxCall {release.tag}</b> 已发布", ""]
        if release.name:
            lines.append(release.name)
        if release.published_at:
            lines.append(f"发布时间：{release.published_at[:10]}")
        if release.prerelease:
            lines.append("（预发布版本）")
        box.setText("<br>".join(lines) + "<br><br>是否打开发布页查看？")
        open_btn = box.addButton("打开发布页", QMessageBox.AcceptRole)
        box.addButton("稍后", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is open_btn:
            QDesktopServices.openUrl(QUrl(release.page_url))

    def show_about(self) -> None:
        from .about import show_about

        show_about(self, self.config)

    # ------------------------------------------------------------------ 设备
    def _hostapi_value(self) -> str:
        value = self.hostapi_combo.currentData()
        return str(value) if value else devices_mod.HOSTAPI_AUTO

    def _on_hostapi_changed(self, _index: int = 0) -> None:
        self.config["audio"]["hostapi"] = self._hostapi_value()
        self.refresh_devices()
        self._save_config("驱动接口")

    # ------------------------------------------------------------ 延迟档位
    def _detect_profile(self) -> int:
        audio = self.config["audio"]
        for index, (value, _label, overrides) in enumerate(LATENCY_PROFILES):
            if all(int(audio.get(key, -1)) == val for key, val in overrides.items()):
                return index
        return self.profile_combo.findData("custom")

    def _on_profile_changed(self, _index: int = 0) -> None:
        value = str(self.profile_combo.currentData() or "")
        for profile_value, _label, overrides in LATENCY_PROFILES:
            if profile_value != value:
                continue
            self.config["audio"].update(overrides)
            try:
                settings.save_config(self.config)
            except Exception:
                log.warning("保存配置失败", exc_info=True)
            if self.engine.running:
                self.status_label.setText("状态：延迟档位将在下次点「开始」时生效")
            return

    def refresh_devices(self) -> None:
        audio_cfg = self.config["audio"]
        api = self._hostapi_value()
        try:
            inputs = devices_mod.list_devices("input", api)
            outputs = devices_mod.list_devices("output", api)
        except Exception as exc:
            QMessageBox.critical(self, "设备枚举失败", str(exc))
            return

        show_api = api == devices_mod.HOSTAPI_ALL
        for combo, devices, kind, want in (
            (self.input_combo, inputs, "input", audio_cfg.get("input_device", "")),
            (self.output_combo, outputs, "output", audio_cfg.get("output_device", "")),
        ):
            combo.blockSignals(True)
            combo.clear()
            for dev in devices:
                combo.addItem(dev.label(with_api=show_api), dev.index)
            target = devices_mod.resolve(str(want or ""), kind, api)
            if target is None:
                target = devices_mod.default_device(kind, api)
            if target is not None:
                pos = combo.findData(target.index)
                if pos >= 0:
                    combo.setCurrentIndex(pos)
            combo.blockSignals(False)
            combo.setEnabled(not self.engine.running)

        if not inputs or not outputs:
            self.status_label.setText("状态：当前接口下没有可用设备，换一个驱动接口试试。")
        elif not any(d.is_cable for d in outputs):
            self.status_label.setText(
                "状态：未找到 CABLE Input。请先安装 VB-CABLE，或换到「全部接口」再看。"
            )
        else:
            self.status_label.setText("状态：就绪")

        self._update_vbcable_state()

    def _selected(self, combo: QComboBox):
        index = combo.currentData()
        if index is None:
            return None
        kind = "input" if combo is self.input_combo else "output"
        # 用「全部接口」找，保证索引不因过滤而失效
        for dev in devices_mod.list_devices(kind, devices_mod.HOSTAPI_ALL):
            if dev.index == index:
                return dev
        return None

    def _mode(self) -> str:
        if self.fixed_radio.isChecked():
            return "fixed"
        if self.calib_radio.isChecked():
            return "calibrated"
        return "auto"

    # ------------------------------------------------------------ 固定声纹
    def _voiceprint_path(self):
        return self.config["model"].get("voiceprint") or None

    def refresh_voiceprint_label(self) -> None:
        if self._record_job is not None and not self._record_job.get("done"):
            return
        path = self._voiceprint_path()
        has_vp = voiceprint.exists(path)
        text = f"声纹：{voiceprint.describe(path)}"
        meta = voiceprint.info(path) if has_vp else None
        if meta:
            actual = str(meta.get("path", ""))
            if not actual.startswith(str(voiceprint.DIR)):
                text += "（已改存其它位置，悬停看路径）"
            self.vp_label.setToolTip(actual)
        else:
            self.vp_label.setToolTip("")
        self.vp_label.setText(text)
        self.vp_label.setStyleSheet("" if has_vp else "color:#c0392b;")
        self.clear_vp_btn.setEnabled(has_vp and not self.engine.running)
        self.record_btn.setEnabled(not self.engine.running)

    def record_voiceprint(self) -> None:
        if self.engine.running:
            QMessageBox.information(self, "请先停止", "录制声纹前请先点「停止」。")
            return
        if self._record_job is not None and not self._record_job.get("done"):
            return
        dev = self._selected(self.input_combo)
        if dev is None:
            QMessageBox.warning(self, "没有输入设备", "请先选择麦克风。")
            return

        # 先确认有地方能存，避免让人白说 3 秒
        try:
            voiceprint.store_dir()
        except voiceprint.VoiceprintError as exc:
            QMessageBox.critical(self, "没有可写的目录", str(exc))
            return

        seconds = float(self.config["model"].get("calibrate_seconds", 3))
        rate = int(self.config["audio"].get("device_sample_rate", 48000))
        hostapi = self._hostapi_value()
        self.vp_label.setText(f"声纹：正在录制 {seconds:.0f} 秒，请对着麦克风正常说话…")
        self.record_btn.setEnabled(False)
        self.clear_vp_btn.setEnabled(False)
        job: dict = {"done": False, "error": None}
        self._record_job = job

        def work() -> None:
            try:
                sample = voiceprint.record(dev, seconds=seconds, device_rate=rate, hostapi=hostapi)
                job["path"] = voiceprint.save(
                    sample,
                    meta={"device": dev.name, "hostapi": dev.hostapi_name, "mode": "fixed"},
                    name_or_path=self._voiceprint_path(),
                )
            except Exception as exc:  # 线程里不能抛到 Qt
                job["error"] = exc
            finally:
                job["done"] = True

        threading.Thread(target=work, name="VoiceprintRecord", daemon=True).start()

    def clear_voiceprint(self) -> None:
        if not voiceprint.exists(self._voiceprint_path()):
            return
        answer = QMessageBox.question(
            self, "清除声纹", "删除已保存的声纹样本？之后需要重新录制。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        voiceprint.clear(self._voiceprint_path())
        self.refresh_voiceprint_label()
        if self.fixed_radio.isChecked():
            self.auto_radio.setChecked(True)
        self.status_label.setText("状态：已清除声纹")

    def _poll_record_job(self) -> None:
        job = self._record_job
        if job is None or not job.get("done"):
            return
        self._record_job = None
        self.record_btn.setEnabled(not self.engine.running)
        if job.get("error") is not None:
            QMessageBox.warning(self, "录制失败", str(job["error"]))
        else:
            self.fixed_radio.setChecked(True)
            self.config["model"]["mode"] = "fixed"
            try:
                settings.save_config(self.config)
            except Exception:
                log.warning("保存配置失败", exc_info=True)
            saved = job.get("path")
            where = f"\n\n保存位置：{saved}" if saved else ""
            QMessageBox.information(
                self, "声纹已保存",
                "已保存固定声纹。以后选「固定声纹」点开始，会直接锁定，不用再说话。" + where,
            )
        self.refresh_voiceprint_label()

    def _toggle_gate(self, checked: bool) -> None:
        self.config["denoise"]["enabled"] = bool(checked)
        self._save_config("频谱门")
        if self.engine.running:
            self.status_label.setText(
                "状态：频谱门已" + ("启用" if checked else "关闭") + "（实时生效，无需重启）"
            )

    # ------------------------------------------------------------------ 控制
    def start_engine(self) -> None:
        if self.engine.running:
            return
        in_dev = self._selected(self.input_combo)
        out_dev = self._selected(self.output_combo)
        errors, warnings = devices_mod.validate_pair(in_dev, out_dev)
        if errors:
            QMessageBox.warning(self, "设备不可用", "\n".join(errors))
            return
        if self._mode() == "fixed" and not voiceprint.exists(self._voiceprint_path()):
            QMessageBox.warning(
                self,
                "还没有声纹",
                "「固定声纹」模式需要先录一次：点「录制/更新声纹…」，"
                "对着麦克风连续说 3 秒即可；也可以先用「校准 3 秒」。",
            )
            return
        if warnings:
            answer = QMessageBox.question(
                self,
                "确认设备",
                "\n".join(warnings) + "\n\n仍然开始吗？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return

        try:
            self.engine.start(in_dev, out_dev, mode=self._mode())
        except EngineError as exc:
            QMessageBox.critical(self, "启动失败", str(exc))
            return
        except Exception as exc:  # pragma: no cover - 兜底
            log.exception("启动失败")
            QMessageBox.critical(self, "启动失败", str(exc))
            return

        self.config["audio"]["input_device"] = in_dev.name
        self.config["audio"]["output_device"] = out_dev.name
        self.config["audio"]["hostapi"] = self._hostapi_value()
        self.config["model"]["mode"] = self._mode()
        try:
            settings.save_config(self.config)
        except Exception:
            log.warning("保存配置失败", exc_info=True)

        self._set_controls_running(True)

    def _set_controls_running(self, running: bool) -> None:
        self.start_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)
        self.refresh_btn.setEnabled(not running)
        self.hostapi_combo.setEnabled(not running)
        self.profile_combo.setEnabled(not running)
        self.input_combo.setEnabled(not running)
        self.output_combo.setEnabled(not running)
        self.auto_radio.setEnabled(not running)
        self.calib_radio.setEnabled(not running)
        self.fixed_radio.setEnabled(not running)
        self.record_btn.setEnabled(not running)
        self.clear_vp_btn.setEnabled(
            not running and voiceprint.exists(self._voiceprint_path())
        )

    def stop_engine(self) -> None:
        self.stop_btn.setEnabled(False)
        self.engine.stop()
        self._set_controls_running(False)

    # ------------------------------------------------------------------ 刷新
    _LEVEL_BANDS = (
        (0.0, "#38c172"),    # 绿：正常
        (0.70, "#f0b429"),   # 黄：偏大
        (0.90, "#e3342f"),   # 红：接近过载
    )

    def _set_level(self, bar, label, peak: float) -> None:
        """电平条 + 峰值 dB + 颜色分段（只在跨段时改样式，避免每 100 ms 刷 QSS）。"""
        peak = max(0.0, min(1.0, float(peak)))
        bar.setValue(int(round(peak * 100)))
        if peak <= 0.0005:
            text = "静音"
        else:
            db = 20.0 * math.log10(peak)
            text = f"{db:6.1f} dB"
            if peak >= 0.98:
                text += "  过载"
        label.setText(text)
        label.setStyleSheet(
            "color:#e3342f; font-family:Consolas,monospace; font-weight:600;"
            if peak >= 0.98
            else "color:#666; font-family:Consolas,monospace;"
        )
        band = ""
        for threshold, color in self._LEVEL_BANDS:
            if peak >= threshold:
                band = color
        if band != self._bar_band:
            self._bar_band = band
            for widget in (self.in_bar, self.out_bar):
                widget.setStyleSheet(
                    "QProgressBar { border:1px solid #cfd6e0; border-radius:6px; background:#f2f4f8; }"
                    f"QProgressBar::chunk {{ border-radius:5px; background:{band}; }}"
                )

    def _tick(self) -> None:
        self._poll_record_job()
        self._poll_install_job()
        self._poll_installer_exit()
        self._poll_update_job()
        snap = self.engine.snapshot()
        state = str(snap.get("state", "idle"))
        stopped = state in {"idle", "error"}
        self.status_label.setText("状态：" + str(snap.get("status", "")))

        if stopped:
            # 停止后不再显示上一轮的"等待语音"，也不保留电平峰值
            self._set_level(self.in_bar, self.in_peak_label, 0.0)
            self._set_level(self.out_bar, self.out_peak_label, 0.0)
        else:
            self._set_level(
                self.in_bar, self.in_peak_label, float(snap.get("in_peak", 0.0))
            )
            self._set_level(
                self.out_bar, self.out_peak_label, float(snap.get("out_peak", 0.0))
            )

        model = str(snap.get("model", "off"))
        if stopped:
            model_text = "模型：已停止"
            latency_text = "延迟估算：—（缓冲 —）"
        else:
            if model and model not in {"off", "直通"}:
                locked = "已锁定" if snap.get("locked") else "等待语音（输出静音）"
                model_text = f"模型：{model} · {locked}"
            else:
                model_text = "模型：未启用（直通）"
            latency_text = (
                f"延迟估算：{float(snap.get('latency_ms', 0)):.0f} ms"
                f"（缓冲 {float(snap.get('buffer_ms', 0)):.0f} ms）"
            )

        perf_text = ""
        if self._perf is not None and time.time() - self._perf_at >= 1.0:
            self._perf_stats = self._perf.sample()
            self._perf_at = time.time()
        if self._perf is not None:
            perf_text = (
                f"CPU：{self._perf_stats.get('cpu_percent', 0):.0f}%    "
                f"内存：{self._perf_stats.get('mem_mb', 0):.0f} MB    "
            )

        self.info_label.setText(
            f"{model_text}    {latency_text}    采样率：{snap.get('device_rate', 0)} Hz\n"
            f"采集块：{self.engine.chunk_ms} ms / 播放块：{self.engine.playback_block_ms} ms    "
            f"驱动：{float(snap.get('driver_ms', 0)):.0f} ms    "
            f"队列：{float(snap.get('queue_ms', 0)):.1f} ms    单次处理：{float(snap.get('infer_ms', 0)):.1f} ms\n"
            f"{perf_text}欠载：{snap.get('underruns', 0)}    "
            f"输入丢块：{snap.get('dropped_in', 0)}    输出丢样：{snap.get('dropped_out', 0)}"
        )

        # 错误或已停止时把按钮状态复位（停止由引擎内部触发的情况，比如模型下载失败）。
        if state in {"idle", "error"} and self._last_state not in {"idle", "error"}:
            self._set_controls_running(False)
        self._last_state = state
        self._update_tray_actions()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        # 有托盘且开启「关闭到托盘」：只隐藏窗口，引擎继续在后台跑
        if (
            getattr(self, "_tray", None) is not None
            and bool(self.config.get("app", {}).get("close_to_tray", True))
        ):
            event.ignore()
            self.hide()
            if not self._tray_hint_shown:
                self._tray_hint_shown = True
                try:
                    self._tray.showMessage(
                        "VoxCall 仍在后台运行",
                        "窗口已收起到托盘。右键托盘图标可以「显示主窗口」或「退出」。",
                        QSystemTrayIcon.Information,
                        4000,
                    )
                except Exception:
                    pass
            return
        try:
            if self.engine.running:
                self.engine.stop(timeout=3.0)
        finally:
            self.timer.stop()
            if getattr(self, "_tray", None) is not None:
                self._tray.hide()
            event.accept()
