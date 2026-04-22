"""核心翻译Pipeline - 串联截图→OCR→翻译→显示"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from PIL import Image

from src.cache.translation_cache import TranslationCache
from src.core.config import AppConfig
from src.core.screenshot import CaptureRegion, ScreenCapture
from src.ocr.engine import OCREngine, OCROutput, create_ocr_engine
from src.translation.engine import (
    TranslationEngine,
    TranslationRequest,
    TranslationResult,
    create_translation_engine,
    protect_variables,
    restore_variables,
)

logger = logging.getLogger(__name__)


@dataclass
class TranslatedBlock:
    """翻译后的文本块 (带位置信息)"""
    original: str
    translated: str
    bbox: tuple[int, int, int, int]  # 在截图中的位置
    confidence: float


@dataclass
class PipelineResult:
    """Pipeline单次执行结果"""
    blocks: list[TranslatedBlock]
    ocr_time_ms: float
    translate_time_ms: float
    cache_hits: int
    cache_misses: int
    timestamp: float = field(default_factory=time.time)

    @property
    def total_time_ms(self) -> float:
        return self.ocr_time_ms + self.translate_time_ms

    @property
    def full_text(self) -> str:
        return "\n".join(b.translated for b in self.blocks)


class TranslationPipeline:
    """核心Pipeline: 截图 → OCR → 预处理 → 翻译 → 后处理"""

    def __init__(self, config: AppConfig):
        self.config = config
        self.capture = ScreenCapture()
        self.ocr: OCREngine = create_ocr_engine(
            config.ocr.engine,
            confidence_threshold=config.ocr.confidence_threshold,
        )
        self.translator: Optional[TranslationEngine] = None
        self.cache = TranslationCache()
        self.glossary: dict[str, str] = {}
        self._context: list[str] = []  # 上下文历史
        self._running = False
        self._on_result: Optional[Callable[[PipelineResult], None]] = None

    def set_result_callback(self, callback: Callable[[PipelineResult], None]):
        """设置结果回调 (用于更新UI)"""
        self._on_result = callback

    def load_glossary(self, glossary: dict[str, str]):
        """加载术语表"""
        self.glossary = glossary

    def _ensure_translator(self):
        if self.translator is None:
            self.translator = create_translation_engine(self.config.translation)

    async def translate_image(self, image: Image.Image) -> PipelineResult:
        """翻译单张图片 (核心方法)"""
        self._ensure_translator()
        cache_hits = 0
        cache_misses = 0

        # 1. 图像预处理 + OCR
        t0 = time.perf_counter()
        processed = self.capture.preprocess_for_ocr(image)
        upscaled = self.capture.upscale(processed, self.config.ocr.scale_factor)
        ocr_output = self.ocr.recognize(upscaled)
        ocr_time = (time.perf_counter() - t0) * 1000

        if not ocr_output.has_text:
            return PipelineResult(
                blocks=[], ocr_time_ms=ocr_time, translate_time_ms=0,
                cache_hits=0, cache_misses=0,
            )

        # 2. 逐块翻译
        t1 = time.perf_counter()
        blocks = []
        scale = self.config.ocr.scale_factor

        for item in ocr_output.results:
            text = item.text.strip()
            if not text or len(text) < 2:
                continue

            # 过滤明显的OCR垃圾（纯数字、纯符号、混合乱码）
            if self._is_garbage(text):
                continue

            # 检测是否已经是中文 (跳过不需翻译的)
            if self._is_chinese(text):
                blocks.append(TranslatedBlock(
                    original=text, translated=text,
                    bbox=self._scale_bbox(item.bbox, 1 / scale),
                    confidence=item.confidence,
                ))
                continue

            # 预处理: 保护变量占位符
            protected_text, placeholders = protect_variables(text)

            # 查缓存
            cached = self.cache.get(
                protected_text,
                self.config.translation.source_language,
                self.config.translation.target_language,
            )

            if cached:
                translated = restore_variables(cached, placeholders)
                cache_hits += 1
            else:
                # 调用翻译API
                try:
                    result = await self.translator.translate(TranslationRequest(
                        text=protected_text,
                        source_lang=self.config.translation.source_language,
                        target_lang=self.config.translation.target_language,
                        context=self._context[-self.config.translation.context_window:],
                        glossary=self.glossary,
                    ))
                    translated = restore_variables(result.translated, placeholders)

                    # 写入缓存
                    self.cache.put(
                        protected_text, result.translated,
                        self.config.translation.source_language,
                        self.config.translation.target_language,
                        engine=result.engine,
                    )
                    cache_misses += 1
                except Exception as e:
                    logger.error(f"翻译失败: {e}")
                    translated = f"[翻译失败] {text}"

            # 更新上下文
            if len(text) > 10:  # 只有较长的文本才加入上下文
                self._context.append(translated)
                if len(self._context) > 20:
                    self._context = self._context[-20:]

            blocks.append(TranslatedBlock(
                original=text, translated=translated,
                bbox=self._scale_bbox(item.bbox, 1 / scale),
                confidence=item.confidence,
            ))

        translate_time = (time.perf_counter() - t1) * 1000

        result = PipelineResult(
            blocks=blocks,
            ocr_time_ms=ocr_time,
            translate_time_ms=translate_time,
            cache_hits=cache_hits,
            cache_misses=cache_misses,
        )

        if self._on_result:
            self._on_result(result)

        return result

    async def translate_region(self, region: CaptureRegion) -> PipelineResult:
        """截图+翻译指定区域"""
        image = self.capture.capture_region(region)
        return await self.translate_image(image)

    async def run_realtime(
        self,
        region: CaptureRegion,
        on_result: Optional[Callable[[PipelineResult], None]] = None,
    ):
        """实时翻译循环"""
        if on_result:
            self._on_result = on_result

        self._running = True
        interval = 1.0 / self.config.capture_fps
        debounce_sec = self.config.debounce_ms / 1000.0
        last_change_time = 0.0

        logger.info(f"实时翻译启动: FPS={self.config.capture_fps}, 防抖={self.config.debounce_ms}ms")

        while self._running:
            try:
                # 检测画面变化
                if self.capture.has_changed(region):
                    last_change_time = time.time()

                # 防抖: 画面稳定后才OCR
                if last_change_time > 0 and (time.time() - last_change_time) >= debounce_sec:
                    result = await self.translate_region(region)
                    if result.blocks:
                        logger.info(
                            f"翻译完成: {len(result.blocks)}块, "
                            f"OCR={result.ocr_time_ms:.0f}ms, "
                            f"翻译={result.translate_time_ms:.0f}ms, "
                            f"缓存命中={result.cache_hits}"
                        )
                    last_change_time = 0.0  # 重置

                await asyncio.sleep(interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"实时翻译错误: {e}")
                await asyncio.sleep(1)

        logger.info("实时翻译已停止")

    def stop(self):
        """停止实时翻译"""
        self._running = False

    @staticmethod
    def _is_chinese(text: str) -> bool:
        """检测文本是否主要是中文"""
        chinese_count = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
        return chinese_count > len(text) * 0.5

    @staticmethod
    def _is_garbage(text: str) -> bool:
        """检测OCR结果是否为垃圾/乱码"""
        import re
        # 纯数字或纯符号
        if re.fullmatch(r'[\d\s\.\,\-\+\=\*\/\#\@\!\?\;\:]+', text):
            return True
        # 有效字母太少（字母+汉字+假名 占比不到30%）
        valid = sum(1 for c in text if c.isalpha() or '\u4e00' <= c <= '\u9fff'
                    or '\u3040' <= c <= '\u30ff')
        if len(text) > 3 and valid / len(text) < 0.3:
            return True
        # 中英日混杂在一起的乱码（比如"房层历中ORY"）
        has_chinese = any('\u4e00' <= c <= '\u9fff' for c in text)
        has_latin = any(c.isascii() and c.isalpha() for c in text)
        if has_chinese and has_latin and len(text) < 15:
            # 短文本中中英混杂，大概率是OCR把覆盖层和原文混在一起了
            chinese_count = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
            latin_count = sum(1 for c in text if c.isascii() and c.isalpha())
            if 0.2 < chinese_count / len(text) < 0.8:
                return True
        return False

    @staticmethod
    def _scale_bbox(bbox: tuple, scale: float) -> tuple[int, int, int, int]:
        return (
            int(bbox[0] * scale),
            int(bbox[1] * scale),
            int(bbox[2] * scale),
            int(bbox[3] * scale),
        )
