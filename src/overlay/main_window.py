"""主窗口 - 区域选择 + 翻译控制面板"""

from __future__ import annotations

import asyncio
import logging
import threading
from functools import partial
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal, QObject, QRect, QPoint
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPen, QCursor
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QSystemTrayIcon, QMenu,
    QComboBox, QSpinBox, QTextEdit, QGroupBox,
    QApplication, QStatusBar,
)

from src.core.config import AppConfig
from src.core.pipeline import TranslationPipeline, PipelineResult
from src.core.screenshot import CaptureRegion
from src.overlay.overlay_widget import OverlayWindow
from src.overlay.region_selector import RegionSelector

logger = logging.getLogger(__name__)


class AsyncWorker(QObject):
    """异步翻译工作线程信号桥"""
    result_ready = Signal(object)  # PipelineResult
    error_occurred = Signal(str)


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

        self._setup_ui()
        self._setup_tray()

    def _setup_ui(self):
        self.setWindowTitle("Steam游戏汉化工具 v0.1")
        self.setMinimumSize(420, 520)
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setSpacing(10)

        # ─── 区域选择 ───
        region_group = QGroupBox("截图区域")
        region_layout = QVBoxLayout(region_group)

        self._region_label = QLabel("未选择区域")
        self._region_label.setStyleSheet("color: #888; font-size: 13px;")
        region_layout.addWidget(self._region_label)

        btn_layout = QHBoxLayout()
        self._btn_select = QPushButton("框选区域")
        self._btn_select.setMinimumHeight(36)
        self._btn_select.clicked.connect(self._select_region)
        btn_layout.addWidget(self._btn_select)

        self._btn_fullscreen = QPushButton("全屏")
        self._btn_fullscreen.setMinimumHeight(36)
        self._btn_fullscreen.clicked.connect(self._select_fullscreen)
        btn_layout.addWidget(self._btn_fullscreen)
        region_layout.addLayout(btn_layout)
        layout.addWidget(region_group)

        # ─── 翻译控制 ───
        translate_group = QGroupBox("翻译")
        translate_layout = QVBoxLayout(translate_group)

        ctrl_layout = QHBoxLayout()
        self._btn_snapshot = QPushButton("截图翻译")
        self._btn_snapshot.setMinimumHeight(40)
        self._btn_snapshot.setStyleSheet(
            "QPushButton { background-color: #2196F3; color: white; font-weight: bold; "
            "border-radius: 6px; font-size: 14px; }"
            "QPushButton:hover { background-color: #1976D2; }"
        )
        self._btn_snapshot.clicked.connect(self._snapshot_translate)
        ctrl_layout.addWidget(self._btn_snapshot)

        self._btn_realtime = QPushButton("开始实时翻译")
        self._btn_realtime.setMinimumHeight(40)
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

        # OCR引擎
        ocr_layout = QHBoxLayout()
        ocr_layout.addWidget(QLabel("OCR引擎:"))
        self._ocr_combo = QComboBox()
        self._ocr_combo.addItems(["rapidocr", "paddleocr"])
        self._ocr_combo.setCurrentText(self.config.ocr.engine)
        ocr_layout.addWidget(self._ocr_combo)
        settings_layout.addLayout(ocr_layout)

        # 翻译引擎
        trans_layout = QHBoxLayout()
        trans_layout.addWidget(QLabel("翻译引擎:"))
        self._trans_combo = QComboBox()
        self._trans_combo.addItems(["openai", "deepl", "ollama"])
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

        layout.addWidget(settings_group)

        # ─── 结果显示 ───
        result_group = QGroupBox("翻译结果")
        result_layout = QVBoxLayout(result_group)
        self._result_text = QTextEdit()
        self._result_text.setReadOnly(True)
        self._result_text.setMinimumHeight(120)
        self._result_text.setStyleSheet("font-size: 14px; font-family: 'Noto Sans SC', sans-serif;")
        result_layout.addWidget(self._result_text)
        layout.addWidget(result_group)

        # ─── 状态栏 ───
        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._status.showMessage("就绪")

    def _setup_tray(self):
        """系统托盘"""
        self._tray = QSystemTrayIcon(self)
        # 简单使用应用图标 (后续可替换为自定义图标)
        self._tray.setToolTip("Steam游戏汉化工具")

        menu = QMenu()
        action_show = QAction("显示主窗口", self)
        action_show.triggered.connect(self.show)
        menu.addAction(action_show)

        action_snapshot = QAction("截图翻译 (Ctrl+Shift+T)", self)
        action_snapshot.triggered.connect(self._snapshot_translate)
        menu.addAction(action_snapshot)

        menu.addSeparator()
        action_quit = QAction("退出", self)
        action_quit.triggered.connect(QApplication.quit)
        menu.addAction(action_quit)

        self._tray.setContextMenu(menu)
        self._tray.show()

    def _select_region(self):
        """打开区域选择器"""
        self.hide()
        QTimer.singleShot(200, self._do_select_region)

    def _do_select_region(self):
        selector = RegionSelector()
        selector.region_selected.connect(self._on_region_selected)
        selector.showFullScreen()

    def _on_region_selected(self, rect: QRect):
        self.capture_region = CaptureRegion(
            x=rect.x(), y=rect.y(),
            width=rect.width(), height=rect.height(),
        )
        self._region_label.setText(
            f"区域: ({rect.x()}, {rect.y()}) {rect.width()}x{rect.height()}"
        )
        self._region_label.setStyleSheet("color: #4CAF50; font-size: 13px; font-weight: bold;")
        self.show()
        self._status.showMessage("区域已选择，可以开始翻译")

    def _select_fullscreen(self):
        monitors = self.pipeline.capture.get_monitors()
        if monitors:
            m = monitors[0]
            self.capture_region = CaptureRegion(
                x=m["left"], y=m["top"],
                width=m["width"], height=m["height"],
            )
            self._region_label.setText(
                f"全屏: {m['width']}x{m['height']}"
            )
            self._region_label.setStyleSheet("color: #4CAF50; font-size: 13px; font-weight: bold;")
            self._status.showMessage("已选择全屏区域")

    def _apply_settings(self):
        """应用设置变更"""
        self.config.ocr.engine = self._ocr_combo.currentText()
        self.config.translation.engine = self._trans_combo.currentText()
        self.config.ocr.scale_factor = self._scale_spin.value()
        # 重建OCR引擎
        from src.ocr.engine import create_ocr_engine
        self.pipeline.ocr = create_ocr_engine(
            self.config.ocr.engine,
            confidence_threshold=self.config.ocr.confidence_threshold,
        )
        self.pipeline.translator = None  # 下次使用时重建
        self.config.save()

    def _snapshot_translate(self):
        """单次截图翻译"""
        if not self.capture_region:
            self._status.showMessage("请先选择截图区域")
            return

        self._apply_settings()
        self._status.showMessage("正在翻译...")
        self._btn_snapshot.setEnabled(False)

        # 在线程中运行异步翻译
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
        """切换实时翻译"""
        if self._btn_realtime.isChecked():
            if not self.capture_region:
                self._btn_realtime.setChecked(False)
                self._status.showMessage("请先选择截图区域")
                return
            self._apply_settings()
            self._btn_realtime.setText("停止实时翻译")
            self._start_realtime()
        else:
            self._btn_realtime.setText("开始实时翻译")
            self._stop_realtime()

    def _start_realtime(self):
        """启动实时翻译定时器"""
        interval = int(1000 / self.config.capture_fps)
        self._realtime_timer = QTimer(self)
        self._realtime_timer.timeout.connect(self._realtime_tick)
        self._realtime_timer.start(interval)
        self._is_translating = False
        self._status.showMessage(f"实时翻译中... (FPS={self.config.capture_fps})")

    def _stop_realtime(self):
        if self._realtime_timer:
            self._realtime_timer.stop()
            self._realtime_timer = None
        self.pipeline.stop()
        self._status.showMessage("实时翻译已停止")

    def _realtime_tick(self):
        """实时翻译定时回调"""
        if self._is_translating or not self.capture_region:
            return

        # 检测画面变化
        if not self.pipeline.capture.has_changed(self.capture_region):
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
        """翻译结果回调"""
        self._btn_snapshot.setEnabled(True)

        if not result.blocks:
            self._result_text.setPlainText("未识别到文字")
            self._status.showMessage("未识别到文字")
            return

        # 更新结果文本
        lines = []
        for b in result.blocks:
            if self.config.overlay.show_original:
                lines.append(f"[原] {b.original}")
            lines.append(f"{b.translated}")
            lines.append("")
        self._result_text.setPlainText("\n".join(lines))

        # 更新覆盖层
        self._update_overlay(result)

        self._status.showMessage(
            f"翻译完成: {len(result.blocks)}块 | "
            f"OCR={result.ocr_time_ms:.0f}ms | "
            f"翻译={result.translate_time_ms:.0f}ms | "
            f"缓存={result.cache_hits}/{result.cache_hits + result.cache_misses}"
        )

    def _update_overlay(self, result: PipelineResult):
        """更新透明覆盖层"""
        if not self.capture_region:
            return

        if self.overlay is None:
            self.overlay = OverlayWindow(self.config.overlay)

        self.overlay.update_content(result.blocks, self.capture_region)
        self.overlay.show()

    def closeEvent(self, event):
        """关闭时最小化到托盘"""
        event.ignore()
        self.hide()
        self._tray.showMessage(
            "Steam游戏汉化工具",
            "已最小化到系统托盘，右键图标可退出",
            QSystemTrayIcon.Information,
            2000,
        )
