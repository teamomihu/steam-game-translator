"""RPG Maker MV/MZ 引擎适配器
   
RPG Maker MV/MZ 的文本全部存储在 data/*.json 中，格式统一：
- Map*.json: 地图事件对话
- CommonEvents.json: 公共事件
- System.json: 系统文本（菜单、术语）
- Actors/Items/Skills/Weapons/Armors/Enemies/States.json: 数据库文本
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Optional

from src.engines.base import EngineDetectResult, GameEngineAdapter, TextEntry

logger = logging.getLogger(__name__)

# 需要翻译的JSON文件和提取路径
TRANSLATABLE_FILES = {
    # 文件名模式: 提取文本的JSON路径描述
    "Actors.json": "角色名/描述",
    "Armors.json": "防具",
    "Classes.json": "职业",
    "CommonEvents.json": "公共事件对话",
    "Enemies.json": "敌人",
    "Items.json": "道具",
    "Skills.json": "技能",
    "States.json": "状态",
    "System.json": "系统术语/菜单",
    "Weapons.json": "武器",
}


class RPGMakerAdapter(GameEngineAdapter):
    """RPG Maker MV/MZ 适配器"""

    def engine_name(self) -> str:
        return "RPG Maker MV/MZ"

    def detect(self, game_path: Path) -> Optional[EngineDetectResult]:
        # MV: www/data/ 或 data/ 目录下有 System.json
        # MZ: data/ 目录下有 System.json + js/rmmz_core.js
        for data_dir in [game_path / "www" / "data", game_path / "data"]:
            system_file = data_dir / "System.json"
            if system_file.exists():
                # 区分 MV 和 MZ
                mz_core = game_path / "js" / "rmmz_core.js"
                mv_core = game_path / "www" / "js" / "rpg_core.js"
                if not mv_core.exists():
                    mv_core = game_path / "js" / "rpg_core.js"

                if mz_core.exists():
                    version = "MZ"
                elif mv_core.exists():
                    version = "MV"
                else:
                    version = "MV/MZ"

                # 尝试读取游戏标题
                title = ""
                try:
                    with open(system_file, "r", encoding="utf-8") as f:
                        sys_data = json.load(f)
                    title = sys_data.get("gameTitle", "")
                except Exception:
                    pass

                return EngineDetectResult(
                    engine_name=f"RPG Maker {version}",
                    confidence=0.95,
                    game_title=title,
                    details=f"数据目录: {data_dir}",
                )
        return None

    def _find_data_dir(self, game_path: Path) -> Optional[Path]:
        for d in [game_path / "www" / "data", game_path / "data"]:
            if d.exists() and (d / "System.json").exists():
                return d
        return None

    def extract_texts(self, game_path: Path) -> list[TextEntry]:
        data_dir = self._find_data_dir(game_path)
        if not data_dir:
            return []

        entries = []

        # 1. 提取 Map 文件中的对话
        for map_file in sorted(data_dir.glob("Map*.json")):
            try:
                entries.extend(self._extract_map(map_file))
            except Exception as e:
                logger.warning(f"解析 {map_file.name} 失败: {e}")

        # 2. 提取 CommonEvents
        ce_file = data_dir / "CommonEvents.json"
        if ce_file.exists():
            try:
                entries.extend(self._extract_common_events(ce_file))
            except Exception as e:
                logger.warning(f"解析 CommonEvents 失败: {e}")

        # 3. 提取数据库文本 (道具/技能/角色等)
        for filename in TRANSLATABLE_FILES:
            filepath = data_dir / filename
            if filepath.exists():
                try:
                    entries.extend(self._extract_database(filepath))
                except Exception as e:
                    logger.warning(f"解析 {filename} 失败: {e}")

        logger.info(f"RPG Maker: 提取了 {len(entries)} 条文本")
        return entries

    def _extract_map(self, filepath: Path) -> list[TextEntry]:
        """提取地图事件中的对话文本"""
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        entries = []
        events = data.get("events", [])
        if not events:
            return entries

        for event in events:
            if not event or not isinstance(event, dict):
                continue
            pages = event.get("pages", [])
            for page_idx, page in enumerate(pages):
                if not page:
                    continue
                event_list = page.get("list", [])
                self._extract_event_commands(
                    event_list, entries,
                    file_path=filepath.name,
                    prefix=f"event{event.get('id', 0)}_p{page_idx}",
                )
        return entries

    def _extract_common_events(self, filepath: Path) -> list[TextEntry]:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        entries = []
        for ce in data:
            if not ce or not isinstance(ce, dict):
                continue
            event_list = ce.get("list", [])
            self._extract_event_commands(
                event_list, entries,
                file_path=filepath.name,
                prefix=f"ce{ce.get('id', 0)}",
            )
        return entries

    def _extract_event_commands(
        self, commands: list, entries: list[TextEntry],
        file_path: str, prefix: str
    ):
        """从事件命令列表中提取文本(对话/选项/滚动文本)"""
        i = 0
        text_buffer = []

        while i < len(commands):
            cmd = commands[i]
            if not isinstance(cmd, dict):
                i += 1
                continue

            code = cmd.get("code", 0)
            params = cmd.get("parameters", [])

            # 401: 对话文本(续行), 101: 对话头
            if code == 401 and params:
                text = str(params[0]).strip()
                if text:
                    text_buffer.append(text)

            # 遇到非401时，把之前收集的对话合并
            elif text_buffer:
                full_text = "\n".join(text_buffer)
                if self._should_translate(full_text):
                    entries.append(TextEntry(
                        key=f"{file_path}:{prefix}:msg{len(entries)}",
                        original=full_text,
                        file_path=file_path,
                    ))
                text_buffer = []

                # 102: 选择项
                if code == 102 and params:
                    choices = params[0] if isinstance(params[0], list) else []
                    for ci, choice in enumerate(choices):
                        choice_text = str(choice).strip()
                        if self._should_translate(choice_text):
                            entries.append(TextEntry(
                                key=f"{file_path}:{prefix}:choice{ci}",
                                original=choice_text,
                                file_path=file_path,
                            ))

                # 405: 滚动文本续行, 105: 滚动文本头
                if code == 405 and params:
                    text = str(params[0]).strip()
                    if self._should_translate(text):
                        entries.append(TextEntry(
                            key=f"{file_path}:{prefix}:scroll{len(entries)}",
                            original=text,
                            file_path=file_path,
                        ))

            else:
                # 102: 选择项（没有前导对话的情况）
                if code == 102 and params:
                    choices = params[0] if isinstance(params[0], list) else []
                    for ci, choice in enumerate(choices):
                        choice_text = str(choice).strip()
                        if self._should_translate(choice_text):
                            entries.append(TextEntry(
                                key=f"{file_path}:{prefix}:choice{ci}",
                                original=choice_text,
                                file_path=file_path,
                            ))

            i += 1

        # 收尾
        if text_buffer:
            full_text = "\n".join(text_buffer)
            if self._should_translate(full_text):
                entries.append(TextEntry(
                    key=f"{file_path}:{prefix}:msg{len(entries)}",
                    original=full_text,
                    file_path=file_path,
                ))

    def _extract_database(self, filepath: Path) -> list[TextEntry]:
        """提取数据库JSON中的 name / description / message 等字段"""
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        entries = []
        if not isinstance(data, list):
            # System.json 是 dict
            if filepath.name == "System.json":
                return self._extract_system(data, filepath.name)
            return entries

        for item in data:
            if not item or not isinstance(item, dict):
                continue
            item_id = item.get("id", 0)
            for field in ["name", "description", "message1", "message2",
                         "message3", "message4", "nickname", "profile", "note"]:
                text = item.get(field, "")
                if isinstance(text, str) and self._should_translate(text):
                    entries.append(TextEntry(
                        key=f"{filepath.name}:{item_id}:{field}",
                        original=text,
                        file_path=filepath.name,
                    ))
        return entries

    def _extract_system(self, data: dict, file_path: str) -> list[TextEntry]:
        """提取 System.json 中的术语和菜单文本"""
        entries = []

        # gameTitle
        title = data.get("gameTitle", "")
        if title:
            entries.append(TextEntry(
                key=f"{file_path}:gameTitle",
                original=title,
                file_path=file_path,
            ))

        # terms.messages (战斗/菜单/系统消息)
        terms = data.get("terms", {})
        messages = terms.get("messages", {})
        if isinstance(messages, dict):
            for k, v in messages.items():
                if isinstance(v, str) and self._should_translate(v):
                    entries.append(TextEntry(
                        key=f"{file_path}:terms.messages.{k}",
                        original=v,
                        file_path=file_path,
                    ))

        # terms.commands (菜单命令名称)
        commands = terms.get("commands", [])
        if isinstance(commands, list):
            for i, cmd in enumerate(commands):
                if cmd and isinstance(cmd, str) and self._should_translate(cmd):
                    entries.append(TextEntry(
                        key=f"{file_path}:terms.commands.{i}",
                        original=cmd,
                        file_path=file_path,
                    ))

        return entries

    def inject_translations(self, game_path: Path, entries: list[TextEntry]) -> int:
        """将翻译写回JSON文件"""
        data_dir = self._find_data_dir(game_path)
        if not data_dir:
            return 0

        # 按文件分组
        by_file: dict[str, list[TextEntry]] = {}
        for e in entries:
            if e.translated:
                by_file.setdefault(e.file_path, []).append(e)

        count = 0
        for filename, file_entries in by_file.items():
            filepath = data_dir / filename
            if not filepath.exists():
                continue

            # 备份
            backup = self.backup_originals(game_path)
            backup_file = backup / filename
            if not backup_file.exists():
                shutil.copy2(filepath, backup_file)

            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)

                for entry in file_entries:
                    if self._apply_translation(data, entry):
                        count += 1

                with open(filepath, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)

            except Exception as e:
                logger.error(f"写回 {filename} 失败: {e}")

        logger.info(f"RPG Maker: 成功写入 {count} 条翻译")
        return count

    def _apply_translation(self, data, entry: TextEntry) -> bool:
        """将单条翻译应用到JSON数据中"""
        parts = entry.key.split(":")
        if len(parts) < 2:
            return False

        filename = parts[0]

        # System.json 的特殊处理
        if filename == "System.json" and len(parts) >= 2:
            path = parts[1]
            if path == "gameTitle":
                data["gameTitle"] = entry.translated
                return True
            elif path.startswith("terms.messages."):
                msg_key = path.replace("terms.messages.", "")
                data.setdefault("terms", {}).setdefault("messages", {})[msg_key] = entry.translated
                return True
            elif path.startswith("terms.commands."):
                idx = int(path.replace("terms.commands.", ""))
                cmds = data.setdefault("terms", {}).setdefault("commands", [])
                if idx < len(cmds):
                    cmds[idx] = entry.translated
                    return True

        # 数据库文件 (Actors/Items/etc): key = "file:id:field"
        if len(parts) == 3 and isinstance(data, list):
            try:
                item_id = int(parts[1])
                field = parts[2]
                for item in data:
                    if item and isinstance(item, dict) and item.get("id") == item_id:
                        if field in item:
                            item[field] = entry.translated
                            return True
            except (ValueError, IndexError):
                pass

        # Map/CommonEvents 的对话文本 - 按顺序匹配原文并替换
        # 这里使用原文匹配法(更可靠)
        if isinstance(data, (dict, list)):
            return self._replace_text_recursive(data, entry.original, entry.translated)

        return False

    def _replace_text_recursive(self, obj, original: str, translated: str) -> bool:
        """递归搜索JSON，找到原文并替换"""
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(v, str) and v.strip() == original.strip():
                    obj[k] = translated
                    return True
                elif isinstance(v, (dict, list)):
                    if self._replace_text_recursive(v, original, translated):
                        return True
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                if isinstance(item, str) and item.strip() == original.strip():
                    obj[i] = translated
                    return True
                elif isinstance(item, (dict, list)):
                    if self._replace_text_recursive(item, original, translated):
                        return True
        return False

    @staticmethod
    def _should_translate(text: str) -> bool:
        """判断文本是否需要翻译"""
        text = text.strip()
        if not text or len(text) < 1:
            return False
        # 纯数字/纯符号跳过
        if text.replace(" ", "").replace("\n", "").isdigit():
            return False
        # 已经是中文的跳过
        chinese_count = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
        if chinese_count > len(text) * 0.5:
            return False
        # 变量/代码跳过
        if text.startswith("\\") or text.startswith("$") or text.startswith("//"):
            return False
        return True
