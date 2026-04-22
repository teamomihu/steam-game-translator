"""翻译缓存 - SQLite持久化 + 内存LRU"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
from pathlib import Path
from typing import Optional

from cachetools import LRUCache

from src.core.config import CACHE_DIR


class TranslationCache:
    """双层翻译缓存: 内存LRU + SQLite磁盘"""

    def __init__(self, db_path: Optional[Path] = None, memory_size: int = 5000):
        self._db_path = db_path or (CACHE_DIR / "translations.db")
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._memory = LRUCache(maxsize=memory_size)
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(str(self._db_path)) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS translations (
                    hash TEXT PRIMARY KEY,
                    source_text TEXT NOT NULL,
                    translated_text TEXT NOT NULL,
                    source_lang TEXT,
                    target_lang TEXT,
                    engine TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    hit_count INTEGER DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_source_text 
                ON translations(source_text)
            """)
            conn.commit()

    @staticmethod
    def _make_key(text: str, source_lang: str, target_lang: str) -> str:
        raw = f"{text}|{source_lang}|{target_lang}"
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    def get(self, text: str, source_lang: str = "auto", target_lang: str = "zh-CN") -> Optional[str]:
        """查找缓存，先查内存再查磁盘"""
        key = self._make_key(text, source_lang, target_lang)

        # 内存缓存
        with self._lock:
            if key in self._memory:
                return self._memory[key]

        # 磁盘缓存
        try:
            with sqlite3.connect(str(self._db_path)) as conn:
                row = conn.execute(
                    "SELECT translated_text FROM translations WHERE hash = ?",
                    (key,)
                ).fetchone()
                if row:
                    translated = row[0]
                    # 回填内存
                    with self._lock:
                        self._memory[key] = translated
                    # 更新命中计数
                    conn.execute(
                        "UPDATE translations SET hit_count = hit_count + 1 WHERE hash = ?",
                        (key,)
                    )
                    conn.commit()
                    return translated
        except sqlite3.Error:
            pass

        return None

    def put(
        self,
        text: str,
        translated: str,
        source_lang: str = "auto",
        target_lang: str = "zh-CN",
        engine: str = "",
    ):
        """写入缓存 (内存 + 磁盘)"""
        key = self._make_key(text, source_lang, target_lang)

        # 内存
        with self._lock:
            self._memory[key] = translated

        # 磁盘
        try:
            with sqlite3.connect(str(self._db_path)) as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO translations 
                       (hash, source_text, translated_text, source_lang, target_lang, engine)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (key, text, translated, source_lang, target_lang, engine),
                )
                conn.commit()
        except sqlite3.Error:
            pass

    def stats(self) -> dict:
        """缓存统计"""
        try:
            with sqlite3.connect(str(self._db_path)) as conn:
                total = conn.execute("SELECT COUNT(*) FROM translations").fetchone()[0]
                hits = conn.execute("SELECT SUM(hit_count) FROM translations").fetchone()[0] or 0
            return {
                "disk_entries": total,
                "memory_entries": len(self._memory),
                "total_hits": hits,
            }
        except sqlite3.Error:
            return {"disk_entries": 0, "memory_entries": len(self._memory), "total_hits": 0}

    def clear(self):
        """清空缓存"""
        with self._lock:
            self._memory.clear()
        try:
            with sqlite3.connect(str(self._db_path)) as conn:
                conn.execute("DELETE FROM translations")
                conn.commit()
        except sqlite3.Error:
            pass
