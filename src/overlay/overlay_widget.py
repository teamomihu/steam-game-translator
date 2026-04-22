"""透明覆盖层 - 在游戏画面上方显示翻译结果"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QRect, QPoint
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from src.core.config import OverlayConfig
from src.core.screenshot import CaptureRegion


class OverlayWindow(QWidget):
    """透明覆盖层窗口"""

    def __init__(self, config: OverlayConfig):
        super().__init__()
        self.config = config
        self._blocks: list = []
        self._region: Optional[CaptureRegion] = None

        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowTransparentForInput  # 点击穿透
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)

    def update_content(self, blocks: list, region: CaptureRegion):
        """更新显示内容"""
        self._blocks = blocks
        self._region = region
        # 定位到截图区域
        self.setGeometry(region.x, region.y, region.width, region.height)
        self.update()

    def paintEvent(self, event):
        if not self._blocks or not self._region:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        font = QFont(self.config.font_family, self.config.font_size)
        font.setBold(True)
        painter.setFont(font)

        text_color = QColor(self.config.text_color)
        bg_color = QColor(self.config.bg_color)
        bg_color.setAlphaF(self.config.opacity)

        for block in self._blocks:
            x1, y1, x2, y2 = block.bbox
            text = block.translated

            # 计算文本区域
            text_rect = QRect(x1, y1, x2 - x1, y2 - y1)

            # 绘制半透明背景
            painter.fillRect(text_rect.adjusted(-2, -2, 4, 4), bg_color)

            # 绘制文字阴影 (增强可读性)
            painter.setPen(QColor(0, 0, 0, 180))
            painter.drawText(text_rect.adjusted(1, 1, 1, 1), Qt.AlignLeft | Qt.TextWordWrap, text)

            # 绘制文字
            painter.setPen(text_color)
            painter.drawText(text_rect, Qt.AlignLeft | Qt.TextWordWrap, text)

        painter.end()

    def toggle_passthrough(self, enabled: bool):
        """切换点击穿透"""
        if enabled:
            self.setWindowFlags(self.windowFlags() | Qt.WindowTransparentForInput)
        else:
            self.setWindowFlags(self.windowFlags() & ~Qt.WindowTransparentForInput)
        self.show()  # 需要重新show以应用flag变更

    def hide_overlay(self):
        self.hide()
