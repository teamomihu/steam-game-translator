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
from src.core.hotkey_manager import GlobalHotkeyManager, setup_hotkeys
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
    hotkey_triggered = Signal(str)  # 全局快捷键信号(从pynput线程安全传到Qt主线程)


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
        self._setup_hotkeys()

    def _setup_ui(self):
        self.setWindowTitle("Steam游戏汉化工具 v0.2")
        self.setMinimumSize(440, 720)
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.setAcceptDrops(True)  # 支持拖拽

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setSpacing(10)

        # ─── 一键汉化(拖拽) ───
        drop_group = QGroupBox("一键汉化（推荐）")
        drop_layout = QVBoxLayout(drop_group)

        self._drop_label = QLabel(
            "将游戏文件夹拖到这里\n\n"
            "支持: RPG Maker MV/MZ, Ren'Py\n"
            "自动检测引擎 → 提取文本 → AI翻译 → 写回游戏"
        )
        self._drop_label.setAlignment(Qt.AlignCenter)
        self._drop_label.setMinimumHeight(100)
        self._drop_label.setStyleSheet(
            "QLabel { border: 3px dashed #FF9800; border-radius: 12px; "
            "color: #FF9800; font-size: 15px; font-weight: bold; "
            "padding: 20px; background: rgba(255,152,0,0.05); }"
        )
        drop_layout.addWidget(self._drop_label)

        self._progress_label = QLabel("")
        self._progress_label.setStyleSheet("font-size: 13px; color: #666;")
        self._progress_label.setAlignment(Qt.AlignCenter)
        drop_layout.addWidget(self._progress_label)
        layout.addWidget(drop_group)

        # ─── 实时翻译(OCR模式) ───
        region_group = QGroupBox("实时翻译（OCR模式）")
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
        self._trans_combo.addItems(["gemini", "ollama", "openai", "deepl"])
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

        # ─── 快捷键提示 ───
        hotkey_group = QGroupBox("全局快捷键")
        hotkey_layout = QVBoxLayout(hotkey_group)
        hk = self.config.hotkeys
        hotkey_info = QLabel(
            f"截图翻译: {hk.toggle_translate}\n"
            f"实时翻译: {hk.toggle_realtime}\n"
            f"切换原文: {hk.toggle_display}\n"
            f"重新翻译: {hk.retranslate}"
        )
        hotkey_info.setStyleSheet(
            "font-size: 12px; color: #888; padding: 4px; line-height: 1.6;"
        )
        hotkey_layout.addWidget(hotkey_info)
        layout.addWidget(hotkey_group)

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

    # ─── 全局快捷键 ───

    def _setup_hotkeys(self):
        """初始化全局快捷键监听"""
        self._hotkey_manager: Optional[GlobalHotkeyManager] = None
        self._worker.hotkey_triggered.connect(self._on_hotkey)

        try:
            hk = self.config.hotkeys
            # pynput 回调在后台线程，通过 Qt 信号桥到主线程
            self._hotkey_manager = setup_hotkeys(
                config=hk,
                on_snapshot=lambda: self._worker.hotkey_triggered.emit("snapshot"),
                on_toggle_realtime=lambda: self._worker.hotkey_triggered.emit("realtime"),
                on_toggle_display=lambda: self._worker.hotkey_triggered.emit("display"),
                on_retranslate=lambda: self._worker.hotkey_triggered.emit("retranslate"),
            )
            self._status.showMessage(
                f"就绪 | 快捷键: "
                f"截图翻译 {hk.toggle_translate}  "
                f"实时翻译 {hk.toggle_realtime}  "
                f"切换原文 {hk.toggle_display}"
            )
        except Exception as e:
            logger.error(f"全局快捷键初始化失败: {e}")
            self._status.showMessage("就绪 - 快捷键初始化失败，请手动操作")

    def _on_hotkey(self, action: str):
        """处理全局快捷键触发（已在 Qt 主线程中）"""
        if action == "snapshot":
            self._snapshot_translate()
        elif action == "realtime":
            # 模拟按钮点击切换
            self._btn_realtime.setChecked(not self._btn_realtime.isChecked())
            self._toggle_realtime()
        elif action == "display":
            self._toggle_display()
        elif action == "retranslate":
            self._retranslate()

    def _toggle_display(self):
        """切换原文/译文显示模式"""
        self.config.overlay.show_original = not self.config.overlay.show_original
        mode = "原文+译文" if self.config.overlay.show_original else "仅译文"
        self._status.showMessage(f"显示模式: {mode}")
        logger.info(f"切换显示模式: {mode}")

        # 如果有上次的翻译结果，立即刷新显示
        current_text = self._result_text.toPlainText()
        if current_text and current_text != "未识别到文字":
            # 触发一次截图翻译来刷新
            self._snapshot_translate()

    def _retranslate(self):
        """强制重新翻译（清除上下文历史后重新翻译）"""
        self._status.showMessage("重新翻译...")
        # 清除 pipeline 的上下文历史以获得全新翻译
        self.pipeline._context.clear()
        self._snapshot_translate()

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

    # ─── 拖拽一键汉化 ───

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._drop_label.setStyleSheet(
                "QLabel { border: 3px dashed #4CAF50; border-radius: 12px; "
                "color: #4CAF50; font-size: 15px; font-weight: bold; "
                "padding: 20px; background: rgba(76,175,80,0.1); }"
            )

    def dragLeaveEvent(self, event):
        self._drop_label.setStyleSheet(
            "QLabel { border: 3px dashed #FF9800; border-radius: 12px; "
            "color: #FF9800; font-size: 15px; font-weight: bold; "
            "padding: 20px; background: rgba(255,152,0,0.05); }"
        )

    def dropEvent(self, event):
        self._drop_label.setStyleSheet(
            "QLabel { border: 3px dashed #FF9800; border-radius: 12px; "
            "color: #FF9800; font-size: 15px; font-weight: bold; "
            "padding: 20px; background: rgba(255,152,0,0.05); }"
        )
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if path:
                self._start_one_click(path)

    def _start_one_click(self, game_path_str: str):
        """启动一键汉化"""
        from pathlib import Path
        game_path = Path(game_path_str)

        if not game_path.is_dir():
            self._status.showMessage("请拖入游戏的文件夹，不是单个文件")
            return

        self._drop_label.setText(f"正在分析: {game_path.name}...")
        self._status.showMessage("一键汉化启动中...")
        self._apply_settings()

        def run():
            from src.core.one_click import OneClickTranslator, TranslateProgress
            loop = asyncio.new_event_loop()
            try:
                translator = OneClickTranslator(self.config)
                translator.set_progress_callback(
                    lambda p: self._worker.result_ready.emit(("progress", p))
                )
                result = loop.run_until_complete(translator.translate_game(game_path))
                self._worker.result_ready.emit(("one_click_done", result))
            except Exception as e:
                self._worker.error_occurred.emit(str(e))
            finally:
                loop.close()

        # 替换信号处理（临时）
        self._worker.result_ready.disconnect()
        self._worker.result_ready.connect(self._on_one_click_signal)
        self._worker.error_occurred.connect(self._on_one_click_error)
        threading.Thread(target=run, daemon=True).start()

    def _on_one_click_error(self, error: str):
        """一键汉化出错"""
        self._drop_label.setText(f"汉化出错\n\n{error}\n\n请重试或检查日志")
        self._drop_label.setStyleSheet(
            "QLabel { border: 3px dashed #f44336; border-radius: 12px; "
            "color: #f44336; font-size: 14px; font-weight: bold; padding: 20px; }"
        )
        self._progress_label.setText("")
        self._status.showMessage(f"出错: {error}")
        # 恢复原始信号
        try:
            self._worker.result_ready.disconnect()
            self._worker.result_ready.connect(self._on_translate_result)
        except Exception:
            pass

    def _on_one_click_signal(self, data):
        """一键汉化的信号处理"""
        if isinstance(data, tuple):
            msg_type, payload = data

            if msg_type == "progress":
                progress = payload
                if progress.phase == "detecting":
                    self._drop_label.setText("正在检测游戏引擎...")
                elif progress.phase == "extracting":
                    self._drop_label.setText("正在提取游戏文本...")
                elif progress.phase == "translating":
                    self._drop_label.setText(
                        f"正在翻译 {progress.done}/{progress.total} "
                        f"({progress.percent:.0f}%)"
                    )
                    self._progress_label.setText(progress.current_text)
                elif progress.phase == "injecting":
                    self._drop_label.setText("正在写入翻译...")
                elif progress.phase == "done":
                    self._drop_label.setText("汉化完成!")

            elif msg_type == "one_click_done":
                result = payload
                # 恢复原始信号连接
                self._worker.result_ready.disconnect()
                self._worker.result_ready.connect(self._on_translate_result)

                if result["success"]:
                    self._drop_label.setText(
                        f"汉化完成!\n\n"
                        f"引擎: {result['engine']}\n"
                        f"游戏: {result['game_title']}\n"
                        f"翻译: {result['translated']}条 | "
                        f"缓存: {result['cached']}条 | "
                        f"耗时: {result['time_seconds']}秒"
                    )
                    self._drop_label.setStyleSheet(
                        "QLabel { border: 3px solid #4CAF50; border-radius: 12px; "
                        "color: #4CAF50; font-size: 14px; font-weight: bold; "
                        "padding: 20px; background: rgba(76,175,80,0.05); }"
                    )
                    self._status.showMessage("汉化完成! 可以启动游戏了")
                    self._result_text.setPlainText(
                        f"一键汉化完成!\n"
                        f"引擎: {result['engine']}\n"
                        f"游戏: {result['game_title']}\n"
                        f"翻译: {result['translated']}条新翻译\n"
                        f"缓存: {result['cached']}条命中缓存\n"
                        f"耗时: {result['time_seconds']}秒\n\n"
                        f"原始文件已备份到 _translation_backup 文件夹"
                    )
                else:
                    self._drop_label.setText(
                        f"汉化失败\n\n{result['error']}\n\n"
                        f"可以拖入其他游戏重试"
                    )
                    self._drop_label.setStyleSheet(
                        "QLabel { border: 3px dashed #f44336; border-radius: 12px; "
                        "color: #f44336; font-size: 14px; font-weight: bold; "
                        "padding: 20px; }"
                    )
                    self._status.showMessage(f"汉化失败: {result['error']}")

                self._progress_label.setText("")
        else:
            # 非一键汉化的结果,走原逻辑
            self._on_translate_result(data)

    def closeEvent(self, event):
        event.ignore()
        self.hide()
        self._tray.showMessage(
            "Steam游戏汉化工具",
            "已最小化到托盘，右键图标可退出",
            QSystemTrayIcon.Information, 2000,
        )

    def destroy_hotkeys(self):
        """释放快捷键资源（应用退出时调用）"""
        if self._hotkey_manager:
            self._hotkey_manager.stop()
            self._hotkey_manager = None
