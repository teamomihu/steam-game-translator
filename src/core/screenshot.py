"""屏幕截图引擎 - 跨平台 (mss)"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

import mss
import numpy as np
from PIL import Image


@dataclass
class CaptureRegion:
    """截图区域"""
    x: int
    y: int
    width: int
    height: int

    def to_mss_monitor(self) -> dict:
        return {
            "left": self.x,
            "top": self.y,
            "width": self.width,
            "height": self.height,
        }


class ScreenCapture:
    """屏幕截图管理器"""

    def __init__(self):
        self._sct = mss.mss()
        self._last_image: Optional[np.ndarray] = None
        self._last_hash: Optional[int] = None

    def capture_region(self, region: CaptureRegion) -> Image.Image:
        """截取指定屏幕区域，返回 PIL Image"""
        monitor = region.to_mss_monitor()
        screenshot = self._sct.grab(monitor)
        img = Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")
        return img

    def capture_full_screen(self, monitor_index: int = 0) -> Image.Image:
        """截取整个屏幕"""
        monitor = self._sct.monitors[monitor_index + 1]  # 0是全部显示器
        screenshot = self._sct.grab(monitor)
        img = Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")
        return img

    def has_changed(self, region: CaptureRegion, threshold: float = 0.02) -> bool:
        """检测区域画面是否变化 (用于防抖)
        
        通过比较图片hash快速判断，避免不必要的OCR调用。
        threshold: 允许的像素差异比例 (0.02 = 2%)
        """
        img = self.capture_region(region)
        arr = np.array(img)

        # 计算简化hash: 缩小到 16x16 灰度图的均值hash
        small = img.resize((16, 16)).convert("L")
        pixels = np.array(small)
        avg = pixels.mean()
        current_hash = int(np.packbits(pixels > avg).tobytes().hex(), 16)

        if self._last_hash is None:
            self._last_hash = current_hash
            self._last_image = arr
            return True

        changed = current_hash != self._last_hash
        self._last_hash = current_hash
        self._last_image = arr
        return changed

    def upscale(self, img: Image.Image, factor: int = 2) -> Image.Image:
        """放大图片以提升OCR精度"""
        if factor <= 1:
            return img
        new_size = (img.width * factor, img.height * factor)
        return img.resize(new_size, Image.LANCZOS)

    def get_monitors(self) -> list[dict]:
        """获取所有显示器信息"""
        return self._sct.monitors[1:]  # 排除第一个(组合显示器)

    def close(self):
        self._sct.close()

    def __del__(self):
        self.close()
