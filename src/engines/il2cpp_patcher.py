"""IL2CPP 二进制补丁器 - 直接修改 Unity level/assets 文件中的字符串

原理: Unity 序列化格式中字符串为 [4字节长度][UTF-8内容][4字节对齐填充]。
直接在二进制文件中找到原文并替换为译文。

限制: 译文字节数不能超过原文（中文通常比英文短，但UTF-8编码是3字节/字）。
如果译文更长，会截断并在末尾加 "..." 提示。
"""

from __future__ import annotations

import logging
import shutil
import struct
from pathlib import Path
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class BinaryStringEntry:
    """二进制文件中的一个字符串"""
    file_path: str         # 文件名
    offset: int            # 字符串长度字段的偏移
    original: str          # 原文
    original_bytes: int    # 原文的字节长度
    translated: str = ""   # 译文


class IL2CPPPatcher:
    """IL2CPP 游戏二进制文本提取和补丁"""

    def extract_strings(self, data_dir: Path) -> list[BinaryStringEntry]:
        """从 level 和 assets 文件中提取可翻译字符串"""
        entries = []

        # 扫描 level 文件和 assets 文件
        target_files = []
        for f in sorted(data_dir.iterdir()):
            if f.name.startswith("level") and not f.name.endswith(".resS"):
                target_files.append(f)
            elif f.name.endswith(".assets") and not f.name.endswith(".resS"):
                target_files.append(f)

        for fpath in target_files:
            try:
                file_entries = self._scan_file(fpath)
                entries.extend(file_entries)
                if file_entries:
                    logger.info(f"  {fpath.name}: {len(file_entries)} 条文本")
            except Exception as e:
                logger.warning(f"扫描 {fpath.name} 失败: {e}")

        logger.info(f"IL2CPP: 共提取 {len(entries)} 条可翻译文本")
        return entries

    def _scan_file(self, fpath: Path) -> list[BinaryStringEntry]:
        """扫描单个文件中的 Unity 格式字符串"""
        with open(fpath, "rb") as f:
            data = f.read()

        entries = []
        seen = set()
        i = 0

        while i < len(data) - 8:
            slen = struct.unpack_from("<I", data, i)[0]
            if 5 <= slen <= 500:
                raw = data[i + 4 : i + 4 + slen]
                try:
                    s = raw.decode("utf-8")
                    if s.isprintable() and self._is_game_text(s) and s not in seen:
                        seen.add(s)
                        entries.append(BinaryStringEntry(
                            file_path=fpath.name,
                            offset=i,
                            original=s,
                            original_bytes=slen,
                        ))
                    # 跳过这个字符串
                    i += 4 + slen + (4 - (slen % 4)) % 4
                    continue
                except (UnicodeDecodeError, ValueError):
                    pass
            i += 1

        return entries

    def patch_file(self, fpath: Path, entries: list[BinaryStringEntry], backup_dir: Path) -> int:
        """将翻译写回二进制文件"""
        translated = [e for e in entries if e.translated and e.file_path == fpath.name]
        if not translated:
            return 0

        # 备份
        backup_file = backup_dir / fpath.name
        if not backup_file.exists():
            shutil.copy2(fpath, backup_file)

        with open(fpath, "rb") as f:
            data = bytearray(f.read())

        count = 0
        for entry in translated:
            try:
                translated_bytes = entry.translated.encode("utf-8")
                orig_len = entry.original_bytes

                if len(translated_bytes) <= orig_len:
                    # 译文更短或等长: 写入 + 用 null 填充剩余空间
                    # 先更新长度字段
                    struct.pack_into("<I", data, entry.offset, len(translated_bytes))
                    # 写入译文
                    pos = entry.offset + 4
                    data[pos : pos + len(translated_bytes)] = translated_bytes
                    # 用 null 填充剩余
                    remaining = orig_len - len(translated_bytes)
                    if remaining > 0:
                        data[pos + len(translated_bytes) : pos + orig_len] = b"\x00" * remaining
                    count += 1
                else:
                    # 译文更长: 截断到原长度
                    truncated = self._truncate_utf8(entry.translated, orig_len)
                    truncated_bytes = truncated.encode("utf-8")
                    struct.pack_into("<I", data, entry.offset, len(truncated_bytes))
                    pos = entry.offset + 4
                    data[pos : pos + len(truncated_bytes)] = truncated_bytes
                    remaining = orig_len - len(truncated_bytes)
                    if remaining > 0:
                        data[pos + len(truncated_bytes) : pos + orig_len] = b"\x00" * remaining
                    count += 1
            except Exception as e:
                logger.warning(f"补丁失败 [{entry.original[:30]}]: {e}")

        with open(fpath, "wb") as f:
            f.write(data)

        logger.info(f"IL2CPP: 已补丁 {fpath.name}: {count} 条")
        return count

    @staticmethod
    def _truncate_utf8(text: str, max_bytes: int) -> str:
        """截断字符串使其 UTF-8 编码不超过 max_bytes"""
        encoded = text.encode("utf-8")
        if len(encoded) <= max_bytes:
            return text
        # 留3字节给省略号
        target = max_bytes - 3
        if target < 3:
            target = max_bytes
        # 逐字符截断
        result = ""
        current_bytes = 0
        for char in text:
            char_bytes = len(char.encode("utf-8"))
            if current_bytes + char_bytes > target:
                break
            result += char
            current_bytes += char_bytes
        return result

    @staticmethod
    def _is_game_text(s: str) -> bool:
        """判断字符串是否是游戏可见文本（菜单/对话/描述）"""
        # 长度检查
        if len(s) < 5 or len(s) > 500:
            return False

        # 字母占比
        alpha = sum(1 for c in s if c.isalpha())
        if len(s) > 0 and alpha / len(s) < 0.5:
            return False

        # 包含 HTML 标签的对话文本一定要保留
        if "<br>" in s or "<color" in s:
            return True

        # 没有空格的文本大概率不是游戏可见文本
        if " " not in s:
            return False

        # 排除引擎/代码/资源内容
        skip_keywords = [
            "Material", "Instance", "Shader", "MASK_", "SDF",
            "Camera", "Canvas", "Collider", "Renderer", "Animation",
            "PlayMaker", "CULLABLE", "Prefab", "Transform", "Component",
            "GameObject", "MonoBehaviour", "Texture", "Sprite", "Audio",
            "DUCT LIGHT", "Height Fog", "NOPE SWMI", "CANT SWIM",
            "TCP2_", "GRABPASS", "RenderType", "PixelSnap", "STEREO_",
            "Fill Area", "Handle Slide", "Background", "LIGHTMODE",
            "FORWARDBASE", "SHADOWSUPPORT", "FORWARDADD", "SHADOWCASTER",
            "_DIRT_BLEND", "Ramp Color", "Rim power", "Rim color",
            "Cutout threshold", "White Point", "Hatches Alpha",
            "Bequest_", "Fade end", "Scale influence", "Start fade",
            "Rim Contrast", "Ramp Contrast", "Event", "Object",
            "CTAA", "CURSOR", "ES3IO",
        ]
        if any(kw in s for kw in skip_keywords):
            return False

        # 排除以引擎/代码标识开头的
        skip_starts = [
            "m_", "k_", "_", "IMGUI", "PPtr", "UI.", "ACTIONS.",
            "Hand Script", "Font ", "Glass ", "Stone_", "Outline",
            "Selection ", "FX_MAT_", "Touch ", "Rim ", "Ramp ",
        ]
        if any(s.startswith(sk) for sk in skip_starts):
            return False

        # 排除明显的技术标识符（全大写无空格 + 下划线/数字混合）
        if s.isupper() and ("_" in s or not " " in s):
            return False

        # 至少包含两个有意义的单词
        words = s.split()
        meaningful_words = [w for w in words if len(w) >= 2 and w[0].isalpha()]
        if len(meaningful_words) < 2:
            return False

        # 包含常见游戏文本模式
        # 对话（第一人称/第二人称/情节描述）
        has_pronoun = any(w.lower() in ("i", "you", "he", "she", "we", "they", "my", "your", "his", "her") for w in words)
        has_verb = any(w.lower() in ("is", "are", "was", "were", "have", "has", "had", "can", "will", "do", "don't", "didn't") for w in words)

        # 句子特征: 首字母大写 + 有标点 或 有代词/动词
        is_sentence = (s[0].isupper() and any(c in s for c in ".!?,'\"")) or has_pronoun or has_verb
        # 短语: 2-6个词 + 首字母大写
        is_phrase = 2 <= len(meaningful_words) <= 8 and s[0].isupper()

        return is_sentence or is_phrase
