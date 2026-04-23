"""游戏引擎适配器基类"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class TextEntry:
    """一条可翻译的文本"""
    key: str            # 唯一标识 (文件路径 + 位置)
    original: str       # 原文
    translated: str = ""  # 译文
    file_path: str = ""   # 所在文件
    context: str = ""     # 上下文(用于提升翻译质量)

    @property
    def needs_translation(self) -> bool:
        return bool(self.original.strip()) and not self.translated


@dataclass
class EngineDetectResult:
    """引擎检测结果"""
    engine_name: str
    confidence: float       # 0-1
    game_title: str = ""
    details: str = ""


class GameEngineAdapter(ABC):
    """游戏引擎适配器基类 - 每个引擎实现一个子类"""

    @abstractmethod
    def detect(self, game_path: Path) -> Optional[EngineDetectResult]:
        """检测游戏是否使用该引擎。返回None表示不是。"""
        pass

    @abstractmethod
    def extract_texts(self, game_path: Path) -> list[TextEntry]:
        """从游戏文件中提取所有可翻译文本"""
        pass

    @abstractmethod
    def inject_translations(self, game_path: Path, entries: list[TextEntry]) -> int:
        """将翻译写回游戏文件。返回成功写入的条数。"""
        pass

    @abstractmethod
    def engine_name(self) -> str:
        pass

    def backup_originals(self, game_path: Path) -> Path:
        """备份原始文件(翻译前自动调用)"""
        import shutil
        backup_dir = game_path / "_translation_backup"
        if not backup_dir.exists():
            backup_dir.mkdir()
        return backup_dir
