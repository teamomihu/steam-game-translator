"""GUI应用 - PySide6 透明覆盖层 + 系统托盘"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
from typing import Optional

from src.core.config import AppConfig
from src.core.pipeline import TranslationPipeline, PipelineResult
from src.core.screenshot import CaptureRegion

logger = logging.getLogger(__name__)


def run_app(config: AppConfig):
    """启动GUI应用"""
    try:
        from PySide6.QtWidgets import QApplication
        from src.overlay.main_window import MainWindow
    except ImportError:
        raise ImportError("PySide6未安装。请运行: pip install PySide6")

    app = QApplication(sys.argv)
    app.setApplicationName("Steam游戏汉化工具")
    app.setQuitOnLastWindowClosed(False)  # 关闭窗口不退出(托盘常驻)

    window = MainWindow(config)
    window.show()

    sys.exit(app.exec())
