"""一键汉化 - 拖入游戏文件夹即可完成翻译"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from src.core.config import AppConfig
from src.engines.base import GameEngineAdapter, TextEntry
from src.engines.detector import EngineDetector
from src.translation.engine import (
    TranslationEngine, TranslationRequest, create_translation_engine,
    protect_variables, restore_variables,
)
from src.cache.translation_cache import TranslationCache

logger = logging.getLogger(__name__)


@dataclass
class TranslateProgress:
    """翻译进度"""
    total: int
    done: int
    current_text: str = ""
    phase: str = ""  # detecting / extracting / translating / injecting / done

    @property
    def percent(self) -> float:
        return (self.done / self.total * 100) if self.total > 0 else 0


class OneClickTranslator:
    """一键汉化器"""

    def __init__(self, config: AppConfig):
        self.config = config
        self.detector = EngineDetector()
        self.cache = TranslationCache()
        self._translator: Optional[TranslationEngine] = None
        self._on_progress: Optional[Callable[[TranslateProgress], None]] = None

    def set_progress_callback(self, cb: Callable[[TranslateProgress], None]):
        self._on_progress = cb

    def _report(self, progress: TranslateProgress):
        if self._on_progress:
            self._on_progress(progress)

    def _ensure_translator(self):
        if self._translator is None:
            self._translator = create_translation_engine(self.config.translation)

    async def translate_game(self, game_path: Path) -> dict:
        """一键汉化主流程"""
        result = {
            "success": False,
            "engine": "",
            "game_title": "",
            "total_texts": 0,
            "translated": 0,
            "cached": 0,
            "time_seconds": 0,
            "error": "",
        }
        t0 = time.time()

        # 1. 检测引擎
        self._report(TranslateProgress(0, 0, phase="detecting"))
        detection = self.detector.detect(game_path)
        if not detection:
            result["error"] = (
                "未能识别游戏引擎。\n"
                "当前一键汉化支持: RPG Maker MV/MZ, Ren'Py, Unity\n\n"
                "你可以用下方的「实时翻译」模式:\n"
                "选择游戏窗口 → 开始实时翻译"
            )
            return result

        detect_result, adapter = detection
        result["engine"] = detect_result.engine_name
        result["game_title"] = detect_result.game_title
        logger.info(f"检测到: {detect_result.engine_name} - {detect_result.game_title}")

        # 2. 提取文本
        self._report(TranslateProgress(0, 0, phase="extracting"))
        entries = adapter.extract_texts(game_path)
        if not entries:
            engine = detect_result.engine_name
            if "IL2CPP" in engine:
                result["error"] = (
                    f"检测到 {engine}，但游戏文本编译在代码中，无法直接提取。\n\n"
                    f"这是最难汉化的游戏类型。请用下方的「实时翻译」模式:\n"
                    f"选择游戏窗口 → 开始实时翻译"
                )
            else:
                result["error"] = (
                    f"检测到 {engine}，但未找到可翻译的文本文件。\n\n"
                    f"请用下方的「实时翻译」模式"
                )
            return result

        # 过滤：已翻译的 + 清理无效字符
        to_translate = []
        for e in entries:
            if not e.needs_translation:
                continue
            # 清理无效 Unicode 字符（surrogate 等）
            try:
                clean = e.original.encode("utf-8", errors="ignore").decode("utf-8")
                if len(clean.strip()) < 3:
                    continue
                e.original = clean
                to_translate.append(e)
            except Exception:
                continue

        result["total_texts"] = len(to_translate)
        logger.info(f"提取到 {len(entries)} 条文本，需翻译 {len(to_translate)} 条")

        # 3. 翻译
        self._report(TranslateProgress(len(to_translate), 0, phase="translating"))
        self._ensure_translator()

        cached = 0
        translated = 0

        for i, entry in enumerate(to_translate):
            # 查缓存
            cached_text = self.cache.get(entry.original, "auto", "zh-CN")
            if cached_text:
                entry.translated = cached_text
                cached += 1
            else:
                # 调翻译API
                try:
                    protected, placeholders = protect_variables(entry.original)
                    tr_result = await self._translator.translate(TranslationRequest(
                        text=protected,
                        target_lang="zh-CN",
                    ))
                    entry.translated = restore_variables(tr_result.translated, placeholders)
                    # 写入缓存
                    self.cache.put(entry.original, entry.translated, engine=tr_result.engine)
                    translated += 1
                except Exception as e:
                    logger.warning(f"翻译失败: {e} | 原文: {entry.original[:50]}")

            self._report(TranslateProgress(
                total=len(to_translate),
                done=i + 1,
                current_text=entry.original[:40],
                phase="translating",
            ))

        result["translated"] = translated
        result["cached"] = cached

        # 4. 安全检查：翻译成功率过低则不写入，防止损坏游戏
        translated_entries = [e for e in to_translate if e.translated]
        success_count = len(translated_entries)
        total_count = len(to_translate)
        success_rate = success_count / total_count if total_count > 0 else 0

        if total_count > 20 and success_rate < 0.3:
            result["error"] = (
                f"翻译成功率过低 ({success_count}/{total_count} = {success_rate:.0%})，"
                f"已中止写入以保护游戏文件。\n\n"
                f"可能原因: API 限流(429)或 Key 额度用完。\n"
                f"建议: 等几分钟后重试，或更换翻译引擎。"
            )
            result["time_seconds"] = round(time.time() - t0, 1)
            logger.warning(f"翻译成功率 {success_rate:.0%}，中止写入")
            return result

        # 5. 写回游戏文件
        self._report(TranslateProgress(len(to_translate), len(to_translate), phase="injecting"))
        inject_count = adapter.inject_translations(game_path, translated_entries)

        result["success"] = inject_count > 0
        result["time_seconds"] = round(time.time() - t0, 1)

        self._report(TranslateProgress(len(to_translate), len(to_translate), phase="done"))
        logger.info(
            f"汉化完成: {inject_count}条写入 ({success_rate:.0%}成功率), "
            f"{cached}条缓存命中, 耗时{result['time_seconds']}秒"
        )
        return result
