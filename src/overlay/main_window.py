"""主窗口 - 区域选择 + 窗口跟踪 + 翻译控制面板"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal, QObject, QRect
from PySide6.QtGui import QAction, QCursor
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QSystemTrayIcon, QMenu,
    QComboBox, QSpinBox, QTextEdit, QGroupBox,
    QApplication, QStatusBar, QListWidget, QListWidgetItem,
    QDialog, QDialogButtonBox,
)

from src.core.config import AppConfig
from src.core.pipeline import TranslationPipeline, PipelineResult
from src.core.screenshot import CaptureRegion
from src.core.window_tracker import WindowTracker, WindowInfo
from src.overlay.overlay_widget import OverlayWindow
from src.overlay.region_selector import RegionSelector

logger = logging.getLogger(__name__)


class AsyncWorker(QObject):
    """异步翻译工作线程信号桥"""
    result_ready = Signal(object)
    error_occurred = Signal(str)


class WindowPickerDialog(QDialog):
    """窗口选择对话框"""

    def __init__(self, windows: list[WindowInfo], parent=None):
        super().__init__(parent)
        self.setWindowTitle("选择要翻译的窗口")
        self.setMinimumSize(400, 300)
        self.selected_window: Optional[WindowInfo] = None

        layout = QVBoxLayout(self)

        hint = QLabel("选择一个游戏窗口，工具会自动跟踪并实时翻译：")
        hint.setStyleSheet("font-size: 13px; margin-bottom: 8px;")
        layout.addWidget(hint)

        self._list = QListWidget()
        self._list.setStyleSheet("font-size: 14px;")
        self._windows = windows
        for w in windows:
            item = QListWidgetItem(f"{w.display_name}  ({w.width}x{w.height})")
            self._list.addItem(item)
        self._list.itemDoubleClicked.connect(self._on_double_click)
        layout.addWidget(self._list)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_accept(self):
        row = self._list.currentRow()
        if row >= 0:
            self.selected_window = self._windows[row]
            self.accept()

    def _on_double_click(self, item):
        row = self._list.row(item)
        self.selected_window = self._windows[row]
        self.accept()


class MainWindow(QMainWindow):
    """主控制面板"""

    def __init__(self, config: AppConfig):
        super().__init__()
        self.config = config
        self.pipeline = TranslationPipeline(config)
        self.overlay: Optional[OverlayWindow] = None
        self.capture_region: Optional[CaptureRegion] = None
        self._realtime_timer: Optional[QTimer] = None
        self._is_translating = False
        self._worker = AsyncWorker()
        self._worker.result_ready.connect(self._on_translate_result)
        self._worker.error_occurred.connect(self._on_translate_error)

        # 窗口跟踪
        self._window_tracker = WindowTracker()
        self._tracked_window_id: Optional[int] = None
        self._track_timer: Optional[QTimer] = None

        self._setup_ui()
        self._setup_tray()

    def _setup_ui(self):
        self.setWindowTitle("Steam游戏汉化工具 v0.1")
        self.setMinimumSize(440, 600)
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setSpacing(10)

        # ─── 区域选择 ───
        region_group = QGroupBox("翻译区域")
        region_layout = QVBoxLayout(region_group)

        self._region_label = QLabel("未选择区域")
        self._region_label.setStyleSheet("color: #888; font-size: 13px;")
        region_layout.addWidget(self._region_label)

        # 第一行按钮：选择窗口（主推）
        self._btn_window = QPushButton("选择游戏窗口（推荐）")
        self._btn_window.setMinimumHeight(42)
        self._btn_window.setStyleSheet(
            "QPushButton { background-color: #FF9800; color: white; font-weight: bold; "
            "border-radius: 6px; font-size: 15px; }"
            "QPushButton:hover { background-color: #F57C00; }"
        )
        self._btn_window.clicked.connect(self._pick_window)
        region_layout.addWidget(self._btn_window)

        # 第二行按钮：框选 / 全屏
        btn_row = QHBoxLayout()
        self._btn_select = QPushButton("手动框选区域")
        self._btn_select.setMinimumHeight(34)
        self._btn_select.clicked.connect(self._select_region)
        btn_row.addWidget(self._btn_select)

        self._btn_fullscreen = QPushButton("全屏")
        self._btn_fullscreen.setMinimumHeight(34)
        self._btn_fullscreen.clicked.connect(self._select_fullscreen)
        btn_row.addWidget(self._btn_fullscreen)
        region_layout.addLayout(btn_row)
        layout.addWidget(region_group)

        # ─── 翻译控制 ───
        translate_group = QGroupBox("翻译")
        translate_layout = QVBoxLayout(translate_group)

        ctrl_layout = QHBoxLayout()
        self._btn_snapshot = QPushButton("截图翻译")
        self._btn_snapshot.setMinimumHeight(42)
        self._btn_snapshot.setStyleSheet(
            "QPushButton { background-color: #2196F3; color: white; font-weight: bold; "
            "border-radius: 6px; font-size: 14px; }"
            "QPushButton:hover { background-color: #1976D2; }"
        )
        self._btn_snapshot.clicked.connect(self._snapshot_translate)
        ctrl_layout.addWidget(self._btn_snapshot)

        self._btn_realtime = QPushButton("开始实时翻译")
        self._btn_realtime.setMinimumHeight(42)
        self._btn_realtime.setCheckable(True)
        self._btn_realtime.setStyleSheet(
            "QPushButton { background-color: #4CAF50; color: white; font-weight: bold; "
            "border-radius: 6px; font-size: 14px; }"
            "QPushButton:hover { background-color: #388E3C; }"
            "QPushButton:checked { background-color: #f44336; }"
        )
        self._btn_realtime.clicked.connect(self._toggle_realtime)
        ctrl_layout.addWidget(self._btn_realtime)
        translate_layout.addLayout(ctrl_layout)
        layout.addWidget(translate_group)

        # ─── 设置 ───
        settings_group = QGroupBox("设置")
        settings_layout = QVBoxLayout(settings_group)

        # 翻译引擎
        trans_layout = QHBoxLayout()
        trans_layout.addWidget(QLabel("翻译引擎:"))
        self._trans_combo = QComboBox()
        self._trans_combo.addItems(["ollama", "openai", "deepl"])
        self._trans_combo.setCurrentText(self.config.translation.engine)
        trans_layout.addWidget(self._trans_combo)
        settings_layout.addLayout(trans_layout)

        # 截图放大
        scale_layout = QHBoxLayout()
        scale_layout.addWidget(QLabel("OCR放大倍数:"))
        self._scale_spin = QSpinBox()
        self._scale_spin.setRange(1, 4)
        self._scale_spin.setValue(self.config.ocr.scale_factor)
        scale_layout.addWidget(self._scale_spin)
        settings_layout.addLayout(scale_layout)

        # 翻译频率
        fps_layout = QHBoxLayout()
        fps_layout.addWidget(QLabel("翻译频率(次/秒):"))
        self._fps_spin = QSpinBox()
        self._fps_spin.setRange(1, 5)
        self._fps_spin.setValue(self.config.capture_fps)
        fps_layout.addWidget(self._fps_spin)
        settings_layout.addLayout(fps_layout)

        layout.addWidget(settings_group)

        # ─── 结果显示 ───
        result_group = QGroupBox("翻译结果")
        result_layout = QVBoxLayout(result_group)
        self._result_text = QTextEdit()
        self._result_text.setReadOnly(True)
        self._result_text.setMinimumHeight(140)
        self._result_text.setStyleSheet("font-size: 14px;")
        result_layout.addWidget(self._result_text)
        layout.addWidget(result_group)

        # ─── 状态栏 ───
        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._status.showMessage("就绪 - 请先选择游戏窗口或框选区域")

    def _setup_tray(self):
        self._tray = QSystemTrayIcon(self)
        self._tray.setToolTip("Steam游戏汉化工具")
        menu = QMenu()
        menu.addAction("显示主窗口", self.show)
        menu.addAction("截图翻译", self._snapshot_translate)
        menu.addSeparator()
        menu.addAction("退出", QApplication.quit)
        self._tray.setContextMenu(menu)
        self._tray.show()

    # ─── 窗口选择 ───

    def _pick_window(self):
        """弹出窗口选择列表"""
        windows = self._window_tracker.list_windows()
        if not windows:
            self._status.showMessage("未检测到可用窗口")
            return

        dialog = WindowPickerDialog(windows, self)
        if dialog.exec() == QDialog.Accepted and dialog.selected_window:
            w = dialog.selected_window
            self._tracked_window_id = w.window_id
            self.capture_region = w.to_capture_region()
            self._region_label.setText(
                f"窗口: {w.display_name}\n"
                f"位置: ({w.x}, {w.y}) {w.width}x{w.height}"
            )
            self._region_label.setStyleSheet(
                "color: #FF9800; font-size: 13px; font-weight: bold;"
            )
            self._status.showMessage(f"已锁定窗口: {w.display_name}")

            # 启动窗口位置跟踪
            self._start_window_tracking()

    def _start_window_tracking(self):
        """定时刷新窗口位置（窗口移动/缩放时自动跟）"""
        if self._track_timer:
            self._track_timer.stop()
        self._track_timer = QTimer(self)
        self._track_timer.timeout.connect(self._update_window_position)
        self._track_timer.start(500)  # 每0.5秒刷新位置

    def _update_window_position(self):
        """更新跟踪窗口的位置"""
        if self._tracked_window_id is None:
            return
        w = self._window_tracker.get_window_by_id(self._tracked_window_id)
        if w:
            self.capture_region = w.to_capture_region()
            # 覆盖层跟随移动
            if self.overlay and self.overlay.isVisible():
                self.overlay.setGeometry(w.x, w.y, w.width, w.height)
        else:
            # 窗口关闭了
            self._status.showMessage("游戏窗口已关闭")
            if self._track_timer:
                self._track_timer.stop()
            self._tracked_window_id = None

    # ─── 区域选择(手动) ───

    def _select_region(self):
        self.hide()
        QTimer.singleShot(200, self._do_select_region)

    def _do_select_region(self):
        selector = RegionSelector()
        selector.region_selected.connect(self._on_region_selected)
        selector.showFullScreen()

    def _on_region_selected(self, rect: QRect):
        self._tracked_window_id = None  # 手动框选时取消窗口跟踪
        if self._track_timer:
            self._track_timer.stop()
        self.capture_region = CaptureRegion(
            x=rect.x(), y=rect.y(),
            width=rect.width(), height=rect.height(),
        )
        self._region_label.setText(
            f"手动区域: ({rect.x()}, {rect.y()}) {rect.width()}x{rect.height()}"
        )
        self._region_label.setStyleSheet("color: #4CAF50; font-size: 13px; font-weight: bold;")
        self.show()
        self._status.showMessage("区域已选择")

    def _select_fullscreen(self):
        self._tracked_window_id = None
        if self._track_timer:
            self._track_timer.stop()
        monitors = self.pipeline.capture.get_monitors()
        if monitors:
            m = monitors[0]
            self.capture_region = CaptureRegion(
                x=m["left"], y=m["top"], width=m["width"], height=m["height"],
            )
            self._region_label.setText(f"全屏: {m['width']}x{m['height']}")
            self._region_label.setStyleSheet("color: #4CAF50; font-size: 13px; font-weight: bold;")
            self._status.showMessage("已选择全屏")

    # ─── 翻译操作 ───

    def _apply_settings(self):
        self.config.translation.engine = self._trans_combo.currentText()
        self.config.ocr.scale_factor = self._scale_spin.value()
        self.config.capture_fps = self._fps_spin.value()
        from src.ocr.engine import create_ocr_engine
        self.pipeline.ocr = create_ocr_engine(
            self.config.ocr.engine,
            confidence_threshold=self.config.ocr.confidence_threshold,
        )
        self.pipeline.translator = None
        self.config.save()

    def _hide_overlay_for_capture(self):
        """截图前隐藏覆盖层，避免截到自己的翻译"""
        if self.overlay and self.overlay.isVisible():
            self.overlay.hide()
            QApplication.processEvents()  # 确保窗口真的隐藏了
            import time; time.sleep(0.05)  # 等待屏幕刷新

    def _snapshot_translate(self):
        if not self.capture_region:
            self._status.showMessage("请先选择游戏窗口或框选区域")
            return
        self._apply_settings()
        self._status.showMessage("正在翻译...")
        self._btn_snapshot.setEnabled(False)
        self._hide_overlay_for_capture()

        def run():
            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(
                    self.pipeline.translate_region(self.capture_region)
                )
                self._worker.result_ready.emit(result)
            except Exception as e:
                self._worker.error_occurred.emit(str(e))
            finally:
                loop.close()

        threading.Thread(target=run, daemon=True).start()

    def _toggle_realtime(self):
        if self._btn_realtime.isChecked():
            if not self.capture_region:
                self._btn_realtime.setChecked(False)
                self._status.showMessage("请先选择游戏窗口或框选区域")
                return
            self._apply_settings()
            self._btn_realtime.setText("停止实时翻译")
            self._start_realtime()
        else:
            self._btn_realtime.setText("开始实时翻译")
            self._stop_realtime()

    def _start_realtime(self):
        interval = int(1000 / self.config.capture_fps)
        self._realtime_timer = QTimer(self)
        self._realtime_timer.timeout.connect(self._realtime_tick)
        self._realtime_timer.start(interval)
        self._is_translating = False
        self._status.showMessage(f"实时翻译中... ({self.config.capture_fps}次/秒)")

    def _stop_realtime(self):
        if self._realtime_timer:
            self._realtime_timer.stop()
            self._realtime_timer = None
        self.pipeline.stop()
        self._status.showMessage("实时翻译已停止")

    def _realtime_tick(self):
        if self._is_translating or not self.capture_region:
            return

        # 先隐藏覆盖层再检测变化和截图
        self._hide_overlay_for_capture()

        if not self.pipeline.capture.has_changed(self.capture_region):
            # 没变化，恢复覆盖层
            if self.overlay:
                self.overlay.show()
            return

        self._is_translating = True

        def run():
            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(
                    self.pipeline.translate_region(self.capture_region)
                )
                self._worker.result_ready.emit(result)
            except Exception as e:
                logger.error(f"实时翻译错误: {e}")
            finally:
                loop.close()
                self._is_translating = False

        threading.Thread(target=run, daemon=True).start()

    def _on_translate_result(self, result: PipelineResult):
        self._btn_snapshot.setEnabled(True)

        if not result.blocks:
            self._result_text.setPlainText("未识别到文字")
            self._status.showMessage("未识别到文字")
            return

        lines = []
        for b in result.blocks:
            if self.config.overlay.show_original:
                lines.append(f"[原] {b.original}")
            lines.append(b.translated)
            lines.append("")
        self._result_text.setPlainText("\n".join(lines))

        self._update_overlay(result)

        self._status.showMessage(
            f"{len(result.blocks)}块 | "
            f"OCR {result.ocr_time_ms:.0f}ms | "
            f"翻译 {result.translate_time_ms:.0f}ms | "
            f"缓存 {result.cache_hits}/{result.cache_hits + result.cache_misses}"
        )

    def _on_translate_error(self, error: str):
        self._btn_snapshot.setEnabled(True)
        self._status.showMessage(f"翻译出错: {error}")
        logger.error(f"翻译错误: {error}")

    def _update_overlay(self, result: PipelineResult):
        if not self.capture_region:
            return
        if self.overlay is None:
            self.overlay = OverlayWindow(self.config.overlay)
        self.overlay.update_content(result.blocks, self.capture_region)
        self.overlay.show()

    def closeEvent(self, event):
        event.ignore()
        self.hide()
        self._tray.showMessage(
            "Steam游戏汉化工具",
            "已最小化到托盘，右键图标可退出",
            QSystemTrayIcon.Information, 2000,
        )
