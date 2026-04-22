"""全局配置管理"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path


CONFIG_DIR = Path.home() / ".steam-translator"
CONFIG_FILE = CONFIG_DIR / "config.json"
CACHE_DIR = CONFIG_DIR / "cache"
GLOSSARY_DIR = CONFIG_DIR / "glossary"


@dataclass
class OCRConfig:
    engine: str = "rapidocr"  # rapidocr | paddleocr | system
    language: str = "ja+en"   # 源语言
    confidence_threshold: float = 0.6
    scale_factor: int = 2     # 截图放大倍数提升OCR精度


@dataclass
class TranslationConfig:
    engine: str = "ollama"    # ollama | openai | deepl
    target_language: str = "zh-CN"
    source_language: str = "auto"
    # OpenAI / Gemini
    api_key: str = ""
    api_base: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    # DeepL
    deepl_key: str = ""
    # Ollama (本地)
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:1.5b"
    # 翻译质量
    context_window: int = 5   # 上下文窗口大小(条数)
    max_concurrent: int = 3   # 最大并发翻译数
    prompt_template: str = (
        "你是一个专业的游戏翻译器。请将以下{src_lang}游戏文本翻译为{tgt_lang}。\n"
        "要求：保持游戏术语一致，保留变量占位符(如{{name}}、%d等)不翻译，"
        "翻译要自然流畅，符合游戏语境。\n"
        "{glossary}"
        "{context}"
        "原文：{text}\n"
        "译文："
    )


@dataclass
class OverlayConfig:
    opacity: float = 0.85
    font_size: int = 16
    font_family: str = "Noto Sans SC"
    text_color: str = "#FFFFFF"
    bg_color: str = "#000000"
    show_original: bool = False  # 是否同时显示原文
    passthrough: bool = True     # 点击穿透


@dataclass
class HotkeyConfig:
    toggle_translate: str = "ctrl+shift+t"   # 截图翻译
    toggle_realtime: str = "ctrl+shift+s"    # 开始/停止实时翻译
    toggle_display: str = "ctrl+shift+d"     # 切换原文/译文
    retranslate: str = "ctrl+shift+r"        # 重新翻译


@dataclass
class AppConfig:
    ocr: OCRConfig = field(default_factory=OCRConfig)
    translation: TranslationConfig = field(default_factory=TranslationConfig)
    overlay: OverlayConfig = field(default_factory=OverlayConfig)
    hotkeys: HotkeyConfig = field(default_factory=HotkeyConfig)
    # 防抖
    debounce_ms: int = 1000   # 文字稳定等待时间(ms)
    capture_fps: int = 2      # OCR捕获频率

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls) -> AppConfig:
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return cls(
                    ocr=OCRConfig(**data.get("ocr", {})),
                    translation=TranslationConfig(**data.get("translation", {})),
                    overlay=OverlayConfig(**data.get("overlay", {})),
                    hotkeys=HotkeyConfig(**data.get("hotkeys", {})),
                    debounce_ms=data.get("debounce_ms", 1000),
                    capture_fps=data.get("capture_fps", 2),
                )
            except (json.JSONDecodeError, TypeError):
                pass
        cfg = cls()
        cfg.save()
        return cfg


# 环境变量覆盖 API key (安全)
def apply_env_overrides(cfg: AppConfig) -> AppConfig:
    if key := os.environ.get("OPENAI_API_KEY"):
        cfg.translation.api_key = key
    if key := os.environ.get("DEEPL_API_KEY"):
        cfg.translation.deepl_key = key
    return cfg
