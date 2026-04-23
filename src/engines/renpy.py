"""Ren'Py 引擎适配器

Ren'Py 游戏的文本在 .rpy 脚本文件中，格式为：
  角色名 "对话文本"
  menu:
      "选项文本":

翻译方式: 生成 game/tl/chinese/ 目录下的翻译文件（Ren'Py 原生支持）
"""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path
from typing import Optional

from src.engines.base import EngineDetectResult, GameEngineAdapter, TextEntry

logger = logging.getLogger(__name__)


class RenPyAdapter(GameEngineAdapter):
    """Ren'Py 引擎适配器"""

    def engine_name(self) -> str:
        return "Ren'Py"

    def detect(self, game_path: Path) -> Optional[EngineDetectResult]:
        # 检测 renpy/ 目录 或 .rpa 文件
        renpy_dir = game_path / "renpy"
        game_dir = game_path / "game"

        if renpy_dir.is_dir() or (game_dir.is_dir() and list(game_dir.glob("*.rpa"))):
            title = ""
            # 尝试读取游戏标题
            options_rpy = game_dir / "options.rpy" if game_dir.exists() else None
            if options_rpy and options_rpy.exists():
                try:
                    content = options_rpy.read_text(encoding="utf-8", errors="ignore")
                    match = re.search(r'config\.name\s*=\s*["\'](.+?)["\']', content)
                    if match:
                        title = match.group(1)
                except Exception:
                    pass

            return EngineDetectResult(
                engine_name="Ren'Py",
                confidence=0.95,
                game_title=title,
            )
        return None

    def extract_texts(self, game_path: Path) -> list[TextEntry]:
        game_dir = game_path / "game"
        if not game_dir.is_dir():
            return []

        entries = []
        # 扫描所有 .rpy 文件
        for rpy_file in sorted(game_dir.rglob("*.rpy")):
            # 跳过翻译目录中的文件
            if "/tl/" in str(rpy_file):
                continue
            try:
                entries.extend(self._extract_rpy(rpy_file, game_dir))
            except Exception as e:
                logger.warning(f"解析 {rpy_file.name} 失败: {e}")

        logger.info(f"Ren'Py: 提取了 {len(entries)} 条文本")
        return entries

    def _extract_rpy(self, filepath: Path, game_dir: Path) -> list[TextEntry]:
        """从单个 .rpy 文件提取对话和菜单文本"""
        content = filepath.read_text(encoding="utf-8", errors="ignore")
        rel_path = str(filepath.relative_to(game_dir))
        entries = []

        for line_no, line in enumerate(content.split("\n"), 1):
            stripped = line.strip()

            # 对话: 角色名 "文本" 或 "文本"
            match = re.match(r'^(?:\w+\s+)?"(.+)"', stripped)
            if match:
                text = match.group(1)
                # 跳过变量/表达式
                if text.startswith("[") or text.startswith("{"):
                    continue
                if self._should_translate(text):
                    entries.append(TextEntry(
                        key=f"{rel_path}:{line_no}",
                        original=text,
                        file_path=rel_path,
                    ))
                continue

            # 菜单选项: "选项文本":
            match = re.match(r'^\s*"(.+)"\s*:', stripped)
            if match:
                text = match.group(1)
                if self._should_translate(text):
                    entries.append(TextEntry(
                        key=f"{rel_path}:{line_no}:menu",
                        original=text,
                        file_path=rel_path,
                    ))

        return entries

    def inject_translations(self, game_path: Path, entries: list[TextEntry]) -> int:
        """生成 Ren'Py 翻译文件到 game/tl/chinese/ 目录"""
        game_dir = game_path / "game"
        tl_dir = game_dir / "tl" / "chinese"
        tl_dir.mkdir(parents=True, exist_ok=True)

        # 按源文件分组
        by_file: dict[str, list[TextEntry]] = {}
        for e in entries:
            if e.translated:
                by_file.setdefault(e.file_path, []).append(e)

        count = 0
        for rel_path, file_entries in by_file.items():
            tl_file = tl_dir / rel_path
            tl_file.parent.mkdir(parents=True, exist_ok=True)

            lines = [
                f"# 自动翻译 - {rel_path}",
                f"# 由 Steam游戏汉化工具 生成",
                "",
            ]

            for entry in file_entries:
                # Ren'Py 翻译格式
                lines.append(f'    old "{entry.original}"')
                lines.append(f'    new "{entry.translated}"')
                lines.append("")
                count += 1

            tl_file.write_text("\n".join(lines), encoding="utf-8")

        # 创建语言初始化文件
        init_file = tl_dir / "common.rpy"
        if not init_file.exists():
            init_file.write_text(
                'translate chinese strings:\n'
                '    # 此文件由 Steam游戏汉化工具 自动生成\n'
                '    pass\n',
                encoding="utf-8",
            )

        logger.info(f"Ren'Py: 写入 {count} 条翻译到 {tl_dir}")
        return count

    @staticmethod
    def _should_translate(text: str) -> bool:
        text = text.strip()
        if not text or len(text) < 2:
            return False
        if text.isdigit():
            return False
        chinese_count = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
        if chinese_count > len(text) * 0.5:
            return False
        return True
