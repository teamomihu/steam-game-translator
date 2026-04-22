"""OCR引擎抽象层 + 实现"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from PIL import Image


@dataclass
class OCRResult:
    """单个OCR识别结果"""
    text: str
    confidence: float
    bbox: tuple[int, int, int, int]  # (x1, y1, x2, y2)


@dataclass
class OCROutput:
    """完整OCR输出"""
    results: list[OCRResult]
    full_text: str  # 所有文本拼接
    raw_image_size: tuple[int, int]

    @property
    def has_text(self) -> bool:
        return bool(self.full_text.strip())


class OCREngine(ABC):
    """OCR引擎基类"""

    @abstractmethod
    def recognize(self, image: Image.Image) -> OCROutput:
        """识别图片中的文字"""
        pass

    @abstractmethod
    def name(self) -> str:
        pass


class RapidOCREngine(OCREngine):
    """RapidOCR引擎 (基于ONNX Runtime，轻量跨平台)"""

    def __init__(self, confidence_threshold: float = 0.5):
        self._threshold = confidence_threshold
        self._engine = None

    def _ensure_engine(self):
        if self._engine is None:
            try:
                from rapidocr_onnxruntime import RapidOCR
                self._engine = RapidOCR()
            except ImportError:
                raise RuntimeError(
                    "RapidOCR未安装。请运行: pip install 'steam-translator[ocr]'"
                )

    def recognize(self, image: Image.Image) -> OCROutput:
        self._ensure_engine()
        import numpy as np

        img_array = np.array(image)
        result, elapse = self._engine(img_array)

        ocr_results = []
        texts = []

        if result:
            for item in result:
                bbox_points, text, confidence = item
                # RapidOCR 有时返回字符串类型的置信度，统一转为 float
                try:
                    confidence = float(confidence) if confidence is not None else 0.0
                except (TypeError, ValueError):
                    confidence = 0.0
                if confidence < self._threshold:
                    continue
                # bbox_points 是4个点 [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]
                xs = [p[0] for p in bbox_points]
                ys = [p[1] for p in bbox_points]
                bbox = (int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys)))

                ocr_results.append(OCRResult(
                    text=text.strip(),
                    confidence=confidence,
                    bbox=bbox,
                ))
                texts.append(text.strip())

        return OCROutput(
            results=ocr_results,
            full_text="\n".join(texts),
            raw_image_size=(image.width, image.height),
        )

    def name(self) -> str:
        return "RapidOCR"


class PaddleOCREngine(OCREngine):
    """PaddleOCR引擎 (精度更高，需要paddlepaddle)"""

    def __init__(self, lang: str = "japan", confidence_threshold: float = 0.5):
        self._lang = lang
        self._threshold = confidence_threshold
        self._engine = None

    def _ensure_engine(self):
        if self._engine is None:
            try:
                from paddleocr import PaddleOCR
                self._engine = PaddleOCR(
                    use_angle_cls=True,
                    lang=self._lang,
                    show_log=False,
                )
            except ImportError:
                raise RuntimeError(
                    "PaddleOCR未安装。请运行: pip install 'steam-translator[ocr-paddle]'"
                )

    def recognize(self, image: Image.Image) -> OCROutput:
        self._ensure_engine()
        import numpy as np

        img_array = np.array(image)
        result = self._engine.ocr(img_array, cls=True)

        ocr_results = []
        texts = []

        if result and result[0]:
            for line in result[0]:
                bbox_points, (text, confidence) = line
                try:
                    confidence = float(confidence) if confidence is not None else 0.0
                except (TypeError, ValueError):
                    confidence = 0.0
                if confidence < self._threshold:
                    continue
                xs = [p[0] for p in bbox_points]
                ys = [p[1] for p in bbox_points]
                bbox = (int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys)))

                ocr_results.append(OCRResult(
                    text=text.strip(),
                    confidence=confidence,
                    bbox=bbox,
                ))
                texts.append(text.strip())

        return OCROutput(
            results=ocr_results,
            full_text="\n".join(texts),
            raw_image_size=(image.width, image.height),
        )

    def name(self) -> str:
        return "PaddleOCR"


def create_ocr_engine(engine_name: str, **kwargs) -> OCREngine:
    """工厂方法: 根据名称创建OCR引擎"""
    engines = {
        "rapidocr": RapidOCREngine,
        "paddleocr": PaddleOCREngine,
    }
    if engine_name not in engines:
        raise ValueError(f"未知OCR引擎: {engine_name}，可选: {list(engines.keys())}")
    return engines[engine_name](**kwargs)
