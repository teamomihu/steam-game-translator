"""Unity 引擎适配器

支持:
- Unity Mono 和 IL2CPP 编译的游戏
- 从 TextAsset (JSON/CSV/TXT) 中提取文本
- 从 MonoBehaviour typetree 中提取文本字段
- 从 StreamingAssets 中提取本地化文件 (JSON/CSV)
- macOS (.app/Contents/Resources/Data) 和 Windows 路径

限制:
- IL2CPP 游戏如果文本硬编码在代码中，无法提取(需要运行时HOOK)
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import shutil
from pathlib import Path
from typing import Optional

from src.engines.base import EngineDetectResult, GameEngineAdapter, TextEntry

logger = logging.getLogger(__name__)


class UnityAdapter(GameEngineAdapter):
    """Unity 引擎适配器"""

    def engine_name(self) -> str:
        return "Unity"

    def detect(self, game_path: Path) -> Optional[EngineDetectResult]:
        """检测 Unity 游戏 (Mono 或 IL2CPP)"""
        data_dir = self._find_data_dir(game_path)
        if not data_dir:
            return None

        # 判断 Mono vs IL2CPP
        is_il2cpp = False
        il2cpp_markers = [
            data_dir / "il2cpp_data",
            game_path / "GameAssembly.dll",
            game_path / "GameAssembly.so",
        ]
        # macOS: Frameworks/GameAssembly.dylib
        for fwk in game_path.rglob("GameAssembly.dylib"):
            il2cpp_markers.append(fwk)

        for marker in il2cpp_markers:
            if marker.exists():
                is_il2cpp = True
                break

        variant = "IL2CPP" if is_il2cpp else "Mono"

        # 读取游戏名
        title = ""
        app_info = data_dir / "app.info"
        if app_info.exists():
            try:
                lines = app_info.read_text(encoding="utf-8", errors="ignore").strip().split("\n")
                if len(lines) >= 2:
                    title = lines[1].strip()
            except Exception:
                pass

        return EngineDetectResult(
            engine_name=f"Unity ({variant})",
            confidence=0.95,
            game_title=title,
            details=f"Data: {data_dir}",
        )

    def _find_data_dir(self, game_path: Path) -> Optional[Path]:
        """查找 Unity Data 目录"""
        candidates = [
            # Windows: GameName_Data/
            *[d for d in game_path.iterdir()
              if d.is_dir() and d.name.endswith("_Data")
              and (d / "globalgamemanagers").exists()],
            # macOS: *.app/Contents/Resources/Data
            *list(game_path.rglob("Contents/Resources/Data")),
            # 直接传入了 Data 目录
            game_path if (game_path / "globalgamemanagers").exists() else None,
        ]
        candidates = [c for c in candidates if c and c.exists()]

        # 验证: 必须包含 globalgamemanagers
        for c in candidates:
            if (c / "globalgamemanagers").exists():
                return c
        return None

    def extract_texts(self, game_path: Path) -> list[TextEntry]:
        data_dir = self._find_data_dir(game_path)
        if not data_dir:
            return []

        entries = []

        # 1. 从 StreamingAssets 提取本地化文件 (JSON/CSV/TXT)
        sa_dir = data_dir / "StreamingAssets"
        if sa_dir.is_dir():
            entries.extend(self._extract_streaming_assets(sa_dir))

        # 2. 用 UnityPy 从 assets 文件提取 TextAsset 和 MonoBehaviour
        try:
            entries.extend(self._extract_assets(data_dir))
        except Exception as e:
            logger.warning(f"UnityPy 提取失败: {e}")

        # 3. IL2CPP 二进制扫描
        # 对 IL2CPP 游戏始终执行 (传统方法提取不到游戏对话)
        is_il2cpp = (data_dir / "il2cpp_data").exists()
        if is_il2cpp or len(entries) < 20:
            try:
                from src.engines.il2cpp_patcher import IL2CPPPatcher
                patcher = IL2CPPPatcher()
                bin_entries = patcher.extract_strings(data_dir)
                for be in bin_entries:
                    entries.append(TextEntry(
                        key=f"bin:{be.file_path}:0x{be.offset:08X}",
                        original=be.original,
                        file_path=be.file_path,
                    ))
                # 保存 patcher 引用供写回使用
                self._il2cpp_patcher = patcher
                self._il2cpp_entries = bin_entries
                logger.info(f"IL2CPP二进制扫描: 提取 {len(bin_entries)} 条文本")
            except Exception as e:
                logger.warning(f"IL2CPP 扫描失败: {e}")

        if not entries:
            logger.warning("未能提取到可翻译文本")

        logger.info(f"Unity: 提取了 {len(entries)} 条文本")
        return entries

    def _extract_streaming_assets(self, sa_dir: Path) -> list[TextEntry]:
        """从 StreamingAssets 目录中提取本地化文件"""
        entries = []

        for root, dirs, files in os.walk(sa_dir):
            for fname in files:
                fpath = Path(root) / fname
                rel = str(fpath.relative_to(sa_dir))

                # JSON 本地化文件
                if fname.endswith(".json") and not fname.startswith("catalog"):
                    entries.extend(self._extract_json_file(fpath, rel))

                # CSV 本地化文件
                elif fname.endswith(".csv"):
                    entries.extend(self._extract_csv_file(fpath, rel))

                # TXT 文本文件
                elif fname.endswith(".txt"):
                    entries.extend(self._extract_txt_file(fpath, rel))

        return entries

    def _extract_json_file(self, filepath: Path, rel_path: str) -> list[TextEntry]:
        """提取 JSON 文件中的文本"""
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return []

        entries = []
        self._extract_json_recursive(data, entries, rel_path, "")
        return entries

    def _extract_json_recursive(self, obj, entries: list, file_path: str, path: str):
        if isinstance(obj, dict):
            for k, v in obj.items():
                new_path = f"{path}.{k}" if path else k
                if isinstance(v, str) and self._should_translate(v):
                    entries.append(TextEntry(
                        key=f"sa:{file_path}:{new_path}",
                        original=v,
                        file_path=file_path,
                    ))
                elif isinstance(v, (dict, list)):
                    self._extract_json_recursive(v, entries, file_path, new_path)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                new_path = f"{path}[{i}]"
                if isinstance(item, str) and self._should_translate(item):
                    entries.append(TextEntry(
                        key=f"sa:{file_path}:{new_path}",
                        original=item,
                        file_path=file_path,
                    ))
                elif isinstance(item, (dict, list)):
                    self._extract_json_recursive(item, entries, file_path, new_path)

    def _extract_csv_file(self, filepath: Path, rel_path: str) -> list[TextEntry]:
        """提取 CSV 本地化文件"""
        entries = []
        try:
            with open(filepath, "r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row_idx, row in enumerate(reader):
                    for col, val in row.items():
                        if val and isinstance(val, str) and self._should_translate(val):
                            entries.append(TextEntry(
                                key=f"sa:{rel_path}:{row_idx}:{col}",
                                original=val,
                                file_path=rel_path,
                            ))
        except Exception:
            pass
        return entries

    def _extract_txt_file(self, filepath: Path, rel_path: str) -> list[TextEntry]:
        """提取 TXT 文件的行"""
        entries = []
        try:
            content = filepath.read_text(encoding="utf-8", errors="ignore")
            for line_no, line in enumerate(content.split("\n")):
                line = line.strip()
                if self._should_translate(line) and len(line) > 5:
                    entries.append(TextEntry(
                        key=f"sa:{rel_path}:{line_no}",
                        original=line,
                        file_path=rel_path,
                    ))
        except Exception:
            pass
        return entries

    def _extract_assets(self, data_dir: Path) -> list[TextEntry]:
        """用 UnityPy 从 .assets 文件中提取文本"""
        try:
            import UnityPy
        except ImportError:
            logger.warning("UnityPy 未安装，无法读取 Unity assets。pip install UnityPy")
            return []

        entries = []

        # 扫描所有 .assets 文件
        for fpath in sorted(data_dir.glob("*.assets")):
            if fpath.name.endswith(".resS"):
                continue
            try:
                env = UnityPy.load(str(fpath))
                for obj in env.objects:
                    if obj.type.name == "TextAsset":
                        entries.extend(self._extract_text_asset(obj, fpath.name))
                    elif obj.type.name == "MonoBehaviour":
                        entries.extend(self._extract_mono(obj, fpath.name))
            except Exception as e:
                logger.debug(f"解析 {fpath.name} 出错: {e}")

        # 扫描 Addressable bundles
        bundle_dir = data_dir / "StreamingAssets" / "aa"
        if bundle_dir.is_dir():
            for platform_dir in bundle_dir.iterdir():
                if not platform_dir.is_dir():
                    continue
                for bundle_file in platform_dir.glob("*.bundle"):
                    try:
                        env = UnityPy.load(str(bundle_file))
                        for obj in env.objects:
                            if obj.type.name == "TextAsset":
                                entries.extend(self._extract_text_asset(obj, bundle_file.name))
                            elif obj.type.name == "MonoBehaviour":
                                entries.extend(self._extract_mono(obj, bundle_file.name))
                    except Exception as e:
                        logger.debug(f"解析 bundle {bundle_file.name} 出错: {e}")

        return entries

    def _extract_text_asset(self, obj, source_file: str) -> list[TextEntry]:
        """从 TextAsset 中提取文本"""
        entries = []
        try:
            data = obj.read()
            name = data.m_Name
            script = data.m_Script
            if isinstance(script, bytes):
                script = script.decode("utf-8", errors="ignore")

            # 跳过 shader/代码/二进制
            skip_prefixes = ["shader", "readme", "license", "changelog"]
            if any(name.lower().startswith(p) for p in skip_prefixes):
                return entries
            if not script.strip() or len(script) < 10:
                return entries

            # 尝试作为 JSON 解析
            try:
                json_data = json.loads(script)
                self._extract_json_recursive(json_data, entries, f"asset:{source_file}:{name}", "")
                return entries
            except json.JSONDecodeError:
                pass

            # 尝试作为 CSV
            if "," in script and "\n" in script:
                try:
                    reader = csv.DictReader(io.StringIO(script))
                    for row_idx, row in enumerate(reader):
                        for col, val in row.items():
                            if val and self._should_translate(val):
                                entries.append(TextEntry(
                                    key=f"asset:{source_file}:{name}:{row_idx}:{col}",
                                    original=val,
                                    file_path=f"{source_file}/{name}",
                                ))
                    if entries:
                        return entries
                except Exception:
                    pass

            # 普通文本: 逐行提取
            for line_no, line in enumerate(script.split("\n")):
                line = line.strip()
                if self._should_translate(line) and len(line) > 10:
                    entries.append(TextEntry(
                        key=f"asset:{source_file}:{name}:{line_no}",
                        original=line,
                        file_path=f"{source_file}/{name}",
                    ))

        except Exception as e:
            logger.debug(f"TextAsset 解析出错: {e}")
        return entries

    def _extract_mono(self, obj, source_file: str) -> list[TextEntry]:
        """从 MonoBehaviour typetree 中提取文本字段"""
        entries = []
        try:
            tree = obj.read_typetree()
            if not tree or not isinstance(tree, dict):
                return entries

            name = tree.get("m_Name", "")
            self._extract_tree_texts(tree, entries, source_file, name, depth=0)
        except Exception:
            pass
        return entries

    def _extract_tree_texts(
        self, obj, entries: list, source_file: str,
        path: str, depth: int
    ):
        """递归搜索 typetree 中的字符串"""
        if depth > 5:
            return

        if isinstance(obj, dict):
            for k, v in obj.items():
                # 跳过内部字段
                if k.startswith("m_") and k not in ("m_Name", "m_Text", "m_text"):
                    if k not in ("m_Description", "m_Title", "m_Label",
                                "m_Message", "m_Tooltip", "m_DisplayName"):
                        continue
                new_path = f"{path}.{k}" if path else k
                if isinstance(v, str) and self._should_translate(v):
                    entries.append(TextEntry(
                        key=f"mono:{source_file}:{new_path}",
                        original=v,
                        file_path=source_file,
                    ))
                elif isinstance(v, (dict, list)):
                    self._extract_tree_texts(v, entries, source_file, new_path, depth + 1)
        elif isinstance(obj, list):
            for i, item in enumerate(obj[:50]):  # 限制数组大小
                if isinstance(item, str) and self._should_translate(item):
                    entries.append(TextEntry(
                        key=f"mono:{source_file}:{path}[{i}]",
                        original=item,
                        file_path=source_file,
                    ))
                elif isinstance(item, (dict, list)):
                    self._extract_tree_texts(item, entries, source_file, f"{path}[{i}]", depth + 1)

    def inject_translations(self, game_path: Path, entries: list[TextEntry]) -> int:
        """将翻译写回"""
        data_dir = self._find_data_dir(game_path)
        if not data_dir:
            return 0

        # 备份
        backup = self.backup_originals(game_path)
        count = 0

        # 按来源分类
        sa_entries = [e for e in entries if e.key.startswith("sa:") and e.translated]
        asset_entries = [e for e in entries if e.key.startswith(("asset:", "mono:")) and e.translated]
        bin_entries = [e for e in entries if e.key.startswith("bin:") and e.translated]

        # 1. StreamingAssets 文件: 直接修改 JSON/CSV/TXT
        sa_dir = data_dir / "StreamingAssets"
        count += self._inject_streaming_assets(sa_dir, sa_entries, backup)

        # 2. Unity assets: 用 UnityPy 写回
        count += self._inject_assets(data_dir, asset_entries, backup)

        # 3. IL2CPP 二进制补丁: 直接改 level/assets 文件中的字符串
        if bin_entries and hasattr(self, "_il2cpp_patcher") and hasattr(self, "_il2cpp_entries"):
            # 把翻译映射回 BinaryStringEntry
            translation_map = {e.original: e.translated for e in bin_entries}
            for be in self._il2cpp_entries:
                if be.original in translation_map:
                    be.translated = translation_map[be.original]

            # 按文件分组并补丁
            by_file: dict[str, list] = {}
            for be in self._il2cpp_entries:
                if be.translated:
                    by_file.setdefault(be.file_path, []).append(be)

            for fname, file_entries in by_file.items():
                fpath = data_dir / fname
                if fpath.exists():
                    count += self._il2cpp_patcher.patch_file(fpath, file_entries, backup)

        logger.info(f"Unity: 成功写入 {count} 条翻译")
        return count

    def _inject_streaming_assets(self, sa_dir: Path, entries: list[TextEntry], backup: Path) -> int:
        """修改 StreamingAssets 中的文件"""
        count = 0
        by_file: dict[str, list[TextEntry]] = {}
        for e in entries:
            # key = "sa:rel_path:json_path" 或 "sa:rel_path:row:col"
            parts = e.key.split(":", 2)
            if len(parts) >= 3:
                rel_path = parts[1]
                by_file.setdefault(rel_path, []).append(e)

        for rel_path, file_entries in by_file.items():
            filepath = sa_dir / rel_path
            if not filepath.exists():
                continue

            # 备份
            backup_file = backup / rel_path
            backup_file.parent.mkdir(parents=True, exist_ok=True)
            if not backup_file.exists():
                shutil.copy2(filepath, backup_file)

            if filepath.suffix == ".json":
                count += self._inject_json(filepath, file_entries)
            elif filepath.suffix == ".csv":
                count += self._inject_csv(filepath, file_entries)

        return count

    def _inject_json(self, filepath: Path, entries: list[TextEntry]) -> int:
        """修改 JSON 文件中的文本"""
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)

            count = 0
            for entry in entries:
                # 从 key 中取出 json_path
                parts = entry.key.split(":", 2)
                if len(parts) < 3:
                    continue
                json_path = parts[2]
                if self._set_json_value(data, json_path, entry.translated):
                    count += 1

            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return count
        except Exception as e:
            logger.error(f"JSON 写回失败 {filepath}: {e}")
            return 0

    def _set_json_value(self, obj, path: str, value: str) -> bool:
        """通过路径设置 JSON 值"""
        import re
        parts = re.split(r'\.|\[(\d+)\]', path)
        parts = [p for p in parts if p is not None and p != ""]

        current = obj
        for i, part in enumerate(parts[:-1]):
            if part.isdigit():
                idx = int(part)
                if isinstance(current, list) and idx < len(current):
                    current = current[idx]
                else:
                    return False
            elif isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return False

        last = parts[-1]
        if last.isdigit():
            idx = int(last)
            if isinstance(current, list) and idx < len(current):
                current[idx] = value
                return True
        elif isinstance(current, dict) and last in current:
            current[last] = value
            return True
        return False

    def _inject_csv(self, filepath: Path, entries: list[TextEntry]) -> int:
        """修改 CSV 文件"""
        # 简化处理: 读取所有行，按原文匹配替换
        try:
            content = filepath.read_text(encoding="utf-8-sig")
            count = 0
            for entry in entries:
                if entry.original in content:
                    content = content.replace(entry.original, entry.translated, 1)
                    count += 1
            filepath.write_text(content, encoding="utf-8-sig")
            return count
        except Exception as e:
            logger.error(f"CSV 写回失败: {e}")
            return 0

    def _inject_assets(self, data_dir: Path, entries: list[TextEntry], backup: Path) -> int:
        """用 UnityPy 写回 .assets 文件中的文本"""
        if not entries:
            return 0

        try:
            import UnityPy
        except ImportError:
            return 0

        # 按源 assets 文件分组
        by_source: dict[str, list[TextEntry]] = {}
        for e in entries:
            parts = e.key.split(":", 2)
            if len(parts) >= 3:
                source = parts[1]
                by_source.setdefault(source, []).append(e)

        count = 0
        for source_file, file_entries in by_source.items():
            fpath = data_dir / source_file
            if not fpath.exists():
                continue

            # 备份
            backup_file = backup / source_file
            if not backup_file.exists():
                shutil.copy2(fpath, backup_file)

            try:
                env = UnityPy.load(str(fpath))
                # 构建原文→译文映射
                translation_map = {e.original: e.translated for e in file_entries}

                for obj in env.objects:
                    if obj.type.name == "TextAsset":
                        try:
                            data = obj.read()
                            script = data.m_Script
                            if isinstance(script, bytes):
                                script = script.decode("utf-8", errors="ignore")
                            modified = False
                            for orig, trans in translation_map.items():
                                if orig in script:
                                    script = script.replace(orig, trans)
                                    modified = True
                                    count += 1
                            if modified:
                                data.m_Script = script.encode("utf-8")
                                data.save()
                        except Exception:
                            pass

                # 保存修改后的 assets
                with open(fpath, "wb") as f:
                    f.write(env.file.save())

            except Exception as e:
                logger.error(f"Assets 写回失败 {source_file}: {e}")

        return count

    @staticmethod
    def _should_translate(text: str) -> bool:
        text = text.strip()
        if not text or len(text) < 3:
            return False
        if text.isdigit():
            return False
        # 跳过文件路径/URL/代码/引擎内部
        skip_starts = (
            "http", "Assets/", "Packages/", "//", "#", "{", "Shader",
            "Unity", "UnityEngine", "System.", "com.", "org.",
            "StandaloneOSX", "StandaloneWindows", "Android", "iOS",
            "Version=", "Culture=", "PublicKeyToken",
        )
        if text.startswith(skip_starts):
            return False
        # 路径/类名
        if "/" in text and "." in text and " " not in text:
            return False
        # 驼峰命名 (代码标识符)
        if text[0].isupper() and not " " in text and any(c.isupper() for c in text[1:]):
            lower_count = sum(1 for c in text if c.islower())
            upper_count = sum(1 for c in text if c.isupper())
            if upper_count > 1 and lower_count > 1 and " " not in text:
                return False
        # 已经是中文
        chinese = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
        if chinese > len(text) * 0.5:
            return False
        # 必须包含有意义的字母和空格(自然语言)
        alpha = sum(1 for c in text if c.isalpha())
        if alpha < 3:
            return False
        # 太短且没有空格的大概率是标识符
        if len(text) < 10 and " " not in text:
            return False
        return True
