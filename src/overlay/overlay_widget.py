"""透明覆盖层 - 在游戏画面上方显示翻译结果"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QRect, QRectF
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
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
        fm = QFontMetrics(font)

        text_color = QColor(self.config.text_color)
        bg_color = QColor(self.config.bg_color)
        bg_color.setAlphaF(self.config.opacity)

        # 收集所有要渲染的矩形，防止重叠
        rendered_rects: list[QRect] = []

        for block in self._blocks:
            x1, y1, x2, y2 = block.bbox
            text = block.translated
            block_width = x2 - x1
            block_height = y2 - y1

            # 根据文字实际大小计算需要的高度
            text_width = max(block_width, 80)
            text_height = max(block_height, fm.height() + 8)

            text_rect = QRect(x1, y1, text_width, text_height)

            # 防重叠：如果和已有矩形重叠，往下移
            for existing in rendered_rects:
                attempts = 0
                while text_rect.intersects(existing) and attempts < 10:
                    text_rect.moveTop(text_rect.top() + text_height + 4)
                    attempts += 1

            rendered_rects.append(text_rect)

            # 绘制圆角半透明背景
            bg_rect = text_rect.adjusted(-6, -3, 6, 3)
            painter.setPen(Qt.NoPen)
            painter.setBrush(bg_color)
            painter.drawRoundedRect(bg_rect, 4, 4)

            # 绘制文字（白色 + 黑色描边增强可读性）
            # 描边
            painter.setPen(QColor(0, 0, 0, 200))
            for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                painter.drawText(text_rect.adjusted(dx, dy, dx, dy),
                                Qt.AlignCenter | Qt.TextWordWrap, text)
            # 正文
            painter.setPen(text_color)
            painter.drawText(text_rect, Qt.AlignCenter | Qt.TextWordWrap, text)

        painter.end()

    def toggle_passthrough(self, enabled: bool):
        if enabled:
            self.setWindowFlags(self.windowFlags() | Qt.WindowTransparentForInput)
        else:
            self.setWindowFlags(self.windowFlags() & ~Qt.WindowTransparentForInput)
        self.show()

    def hide_overlay(self):
        self.hide()
