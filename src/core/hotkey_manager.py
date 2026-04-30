"""全局快捷键管理器 - 基于 pynput 实现跨平台全局热键"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

from pynput import keyboard

from src.core.config import HotkeyConfig

logger = logging.getLogger(__name__)


def _parse_hotkey(hotkey_str: str) -> frozenset[keyboard.Key | keyboard.KeyCode]:
    """将 'ctrl+shift+t' 格式的字符串解析为 pynput 按键集合"""
    MODIFIER_MAP = {
        "ctrl": keyboard.Key.cmd,       # macOS: Cmd, 其他平台映射到 ctrl
        "control": keyboard.Key.ctrl_l,
        "cmd": keyboard.Key.cmd,
        "command": keyboard.Key.cmd,
        "shift": keyboard.Key.shift,
        "alt": keyboard.Key.alt,
        "option": keyboard.Key.alt,
    }

    import platform
    if platform.system() != "Darwin":
        MODIFIER_MAP["ctrl"] = keyboard.Key.ctrl_l

    keys = set()
    for part in hotkey_str.lower().strip().split("+"):
        part = part.strip()
        if part in MODIFIER_MAP:
            keys.add(MODIFIER_MAP[part])
        elif len(part) == 1:
            keys.add(keyboard.KeyCode.from_char(part))
        else:
            logger.warning(f"无法识别的按键: '{part}'，忽略")

    return frozenset(keys)


class GlobalHotkeyManager:
    """
    全局快捷键管理器。
    
    在后台线程中监听键盘，当检测到已注册的组合键时，
    通过回调通知主线程（Qt 信号安全方式）。
    """

    def __init__(self):
        self._hotkeys: dict[frozenset, Callable] = {}
        self._pressed: set = set()
        self._listener: Optional[keyboard.Listener] = None
        self._lock = threading.Lock()
        self._running = False

    def register(self, hotkey_str: str, callback: Callable) -> None:
        """
        注册一个全局热键。
        
        Args:
            hotkey_str: 快捷键描述，如 'ctrl+shift+t'
            callback: 触发时的回调函数（会在 pynput 线程中调用，
                      需通过 Qt 信号桥转到主线程）
        """
        keys = _parse_hotkey(hotkey_str)
        if not keys:
            logger.warning(f"快捷键 '{hotkey_str}' 解析为空，跳过注册")
            return
        with self._lock:
            self._hotkeys[keys] = callback
        logger.info(f"注册全局快捷键: {hotkey_str}")

    def unregister(self, hotkey_str: str) -> None:
        """取消注册一个热键"""
        keys = _parse_hotkey(hotkey_str)
        with self._lock:
            self._hotkeys.pop(keys, None)
        logger.info(f"取消注册快捷键: {hotkey_str}")

    def unregister_all(self) -> None:
        """取消所有注册的热键"""
        with self._lock:
            self._hotkeys.clear()
        logger.info("已清除所有快捷键")

    def start(self) -> None:
        """启动监听（在后台线程中运行）"""
        if self._running:
            return
        self._running = True
        self._listener = keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
        )
        self._listener.daemon = True
        self._listener.start()
        logger.info("全局快捷键监听已启动")

    def stop(self) -> None:
        """停止监听"""
        self._running = False
        if self._listener:
            self._listener.stop()
            self._listener = None
        with self._lock:
            self._pressed.clear()
        logger.info("全局快捷键监听已停止")

    def _normalize_key(self, key) -> keyboard.Key | keyboard.KeyCode:
        """统一按键表示，将左右修饰键合并"""
        # 将 ctrl_l/ctrl_r 统一为 ctrl_l
        MERGE = {
            keyboard.Key.ctrl_r: keyboard.Key.ctrl_l,
            keyboard.Key.shift_r: keyboard.Key.shift,
            keyboard.Key.alt_r: keyboard.Key.alt,
            keyboard.Key.alt_gr: keyboard.Key.alt,
            keyboard.Key.cmd_r: keyboard.Key.cmd,
        }
        if key in MERGE:
            return MERGE[key]

        # 大写字母 → 小写 KeyCode
        if isinstance(key, keyboard.KeyCode) and key.char and key.char.isalpha():
            return keyboard.KeyCode.from_char(key.char.lower())

        return key

    def _on_press(self, key):
        """按键按下事件"""
        normalized = self._normalize_key(key)
        with self._lock:
            self._pressed.add(normalized)
            pressed_frozen = frozenset(self._pressed)

            for combo, callback in self._hotkeys.items():
                if combo == pressed_frozen:
                    # 匹配成功，触发回调
                    logger.debug(f"快捷键触发: {combo}")
                    try:
                        callback()
                    except Exception as e:
                        logger.error(f"快捷键回调出错: {e}")
                    break

    def _on_release(self, key):
        """按键释放事件"""
        normalized = self._normalize_key(key)
        with self._lock:
            self._pressed.discard(normalized)


def setup_hotkeys(
    config: HotkeyConfig,
    on_snapshot: Callable,
    on_toggle_realtime: Callable,
    on_toggle_display: Callable,
    on_retranslate: Callable,
) -> GlobalHotkeyManager:
    """
    便捷函数：根据配置一次性注册所有快捷键并启动监听。
    
    Args:
        config: 快捷键配置
        on_snapshot: 截图翻译回调
        on_toggle_realtime: 开始/停止实时翻译回调
        on_toggle_display: 切换原文/译文回调
        on_retranslate: 重新翻译回调
    
    Returns:
        已启动的 GlobalHotkeyManager 实例
    """
    manager = GlobalHotkeyManager()
    manager.register(config.toggle_translate, on_snapshot)
    manager.register(config.toggle_realtime, on_toggle_realtime)
    manager.register(config.toggle_display, on_toggle_display)
    manager.register(config.retranslate, on_retranslate)
    manager.start()
    return manager
