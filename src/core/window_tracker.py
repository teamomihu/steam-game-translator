"""窗口跟踪器 - 自动检测并跟踪游戏窗口位置"""

from __future__ import annotations

import platform
import logging
from dataclasses import dataclass
from typing import Optional

from src.core.screenshot import CaptureRegion

logger = logging.getLogger(__name__)


@dataclass
class WindowInfo:
    """窗口信息"""
    window_id: int
    title: str
    owner: str          # 应用名称
    x: int
    y: int
    width: int
    height: int

    @property
    def display_name(self) -> str:
        if self.title:
            return f"{self.owner} - {self.title}"
        return self.owner

    def to_capture_region(self) -> CaptureRegion:
        return CaptureRegion(
            x=self.x, y=self.y,
            width=self.width, height=self.height,
        )


class WindowTracker:
    """窗口跟踪器 - 列出窗口、跟踪位置变化"""

    def list_windows(self) -> list[WindowInfo]:
        """列出所有可见窗口"""
        if platform.system() == "Darwin":
            return self._list_windows_macos()
        else:
            return self._list_windows_fallback()

    def get_window_by_id(self, window_id: int) -> Optional[WindowInfo]:
        """通过ID获取窗口最新位置(用于跟踪)"""
        for w in self.list_windows():
            if w.window_id == window_id:
                return w
        return None

    def _list_windows_macos(self) -> list[WindowInfo]:
        """macOS: 通过 Quartz 获取窗口列表"""
        try:
            import Quartz
        except ImportError:
            logger.warning("pyobjc-framework-Quartz 未安装")
            return []

        windows = []
        window_list = Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
            Quartz.kCGNullWindowID,
        )

        if not window_list:
            return []

        for w in window_list:
            # 跳过太小的窗口和系统窗口
            bounds = w.get(Quartz.kCGWindowBounds, {})
            width = int(bounds.get("Width", 0))
            height = int(bounds.get("Height", 0))
            if width < 100 or height < 100:
                continue

            owner = w.get(Quartz.kCGWindowOwnerName, "") or ""
            title = w.get(Quartz.kCGWindowName, "") or ""

            # 跳过自己和系统进程
            skip_owners = {"Window Server", "Dock", "SystemUIServer", "Control Center",
                          "Notification Center", "Steam游戏汉化工具"}
            if owner in skip_owners:
                continue

            window_id = w.get(Quartz.kCGWindowNumber, 0)
            x = int(bounds.get("X", 0))
            y = int(bounds.get("Y", 0))

            windows.append(WindowInfo(
                window_id=window_id,
                title=title,
                owner=owner,
                x=x, y=y,
                width=width, height=height,
            ))

        return windows

    def _list_windows_fallback(self) -> list[WindowInfo]:
        """其他平台: 返回空列表(后续扩展)"""
        logger.warning("当前平台暂不支持窗口检测，请使用框选模式")
        return []
