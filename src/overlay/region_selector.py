"""屏幕区域选择器 - 框选翻译区域"""

from __future__ import annotations

from PySide6.QtCore import Qt, QPoint, QRect, Signal
from PySide6.QtGui import QColor, QCursor, QPainter, QPen, QScreen
from PySide6.QtWidgets import QWidget, QApplication


class RegionSelector(QWidget):
    """全屏半透明覆盖, 鼠标拖拽框选区域"""

    region_selected = Signal(QRect)

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setCursor(QCursor(Qt.CrossCursor))

        self._start = QPoint()
        self._end = QPoint()
        self._drawing = False

        # 获取整个屏幕尺寸
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.geometry()
            self.setGeometry(geo)

    def paintEvent(self, event):
        painter = QPainter(self)
        # 半透明黑色背景
        painter.fillRect(self.rect(), QColor(0, 0, 0, 100))

        if self._drawing and not self._start.isNull() and not self._end.isNull():
            rect = QRect(self._start, self._end).normalized()
            # 选中区域清除遮罩 (显示原始画面)
            painter.setCompositionMode(QPainter.CompositionMode_Clear)
            painter.fillRect(rect, Qt.transparent)
            # 绘制边框
            painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
            pen = QPen(QColor(33, 150, 243), 2)  # 蓝色边框
            painter.setPen(pen)
            painter.drawRect(rect)
            # 显示尺寸
            size_text = f"{rect.width()} x {rect.height()}"
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(rect.topLeft().x(), rect.topLeft().y() - 5, size_text)

        painter.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._start = event.position().toPoint()
            self._end = self._start
            self._drawing = True
            self.update()

    def mouseMoveEvent(self, event):
        if self._drawing:
            self._end = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._drawing:
            self._drawing = False
            rect = QRect(self._start, self._end).normalized()
            if rect.width() > 10 and rect.height() > 10:
                # 转换为屏幕绝对坐标
                global_rect = QRect(
                    self.mapToGlobal(rect.topLeft()),
                    rect.size(),
                )
                self.region_selected.emit(global_rect)
            self.close()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()
