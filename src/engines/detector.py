"""游戏引擎自动检测器"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from src.engines.base import EngineDetectResult, GameEngineAdapter

logger = logging.getLogger(__name__)


class EngineDetector:
    """扫描游戏目录，自动识别使用的引擎"""

    def __init__(self):
        self._adapters: list[GameEngineAdapter] = []
        self._load_adapters()

    def _load_adapters(self):
        """加载所有引擎适配器"""
        try:
            from src.engines.rpgmaker import RPGMakerAdapter
            self._adapters.append(RPGMakerAdapter())
        except Exception as e:
            logger.warning(f"RPGMaker适配器加载失败: {e}")

        try:
            from src.engines.renpy import RenPyAdapter
            self._adapters.append(RenPyAdapter())
        except Exception as e:
            logger.warning(f"RenPy适配器加载失败: {e}")

        try:
            from src.engines.unity import UnityAdapter
            self._adapters.append(UnityAdapter())
        except Exception as e:
            logger.warning(f"Unity适配器加载失败: {e}")

    def detect(self, game_path: Path) -> Optional[tuple[EngineDetectResult, GameEngineAdapter]]:
        """检测游戏引擎，返回 (检测结果, 适配器) 或 None"""
        if not game_path.is_dir():
            return None

        best_result: Optional[EngineDetectResult] = None
        best_adapter: Optional[GameEngineAdapter] = None
        best_confidence = 0.0

        for adapter in self._adapters:
            try:
                result = adapter.detect(game_path)
                if result and result.confidence > best_confidence:
                    best_result = result
                    best_adapter = adapter
                    best_confidence = result.confidence
            except Exception as e:
                logger.warning(f"{adapter.engine_name()} 检测出错: {e}")

        if best_result and best_confidence >= 0.5:
            logger.info(
                f"检测到引擎: {best_result.engine_name} "
                f"(置信度: {best_result.confidence:.0%})"
            )
            return best_result, best_adapter

        return None
