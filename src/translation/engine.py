"""翻译引擎抽象层 + 实现"""

from __future__ import annotations

import asyncio
import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

import httpx


@dataclass
class TranslationRequest:
    """翻译请求"""
    text: str
    source_lang: str = "auto"
    target_lang: str = "zh-CN"
    context: list[str] = field(default_factory=list)  # 上下文(前几条对话)
    glossary: dict[str, str] = field(default_factory=dict)  # 术语表


@dataclass
class TranslationResult:
    """翻译结果"""
    original: str
    translated: str
    engine: str
    confidence: float = 1.0


class TranslationEngine(ABC):
    """翻译引擎基类"""

    @abstractmethod
    async def translate(self, request: TranslationRequest) -> TranslationResult:
        pass

    @abstractmethod
    def name(self) -> str:
        pass


class OpenAIEngine(TranslationEngine):
    """OpenAI / 兼容API翻译引擎 (GPT-4o, DeepSeek, 等)"""

    def __init__(
        self,
        api_key: str,
        api_base: str = "https://api.openai.com/v1",
        model: str = "gpt-4o-mini",
        prompt_template: str = "",
    ):
        self._api_key = api_key
        self._api_base = api_base.rstrip("/")
        self._model = model
        self._prompt_template = prompt_template
        self._client = httpx.AsyncClient(timeout=30.0)

    def _build_prompt(self, req: TranslationRequest) -> str:
        glossary_text = ""
        if req.glossary:
            pairs = [f"  {k} → {v}" for k, v in req.glossary.items()]
            glossary_text = "术语表（必须按此翻译）：\n" + "\n".join(pairs) + "\n\n"

        context_text = ""
        if req.context:
            context_text = "前文（供参考上下文）：\n" + "\n".join(
                f"  {c}" for c in req.context[-5:]
            ) + "\n\n"

        lang_map = {
            "ja": "日文", "en": "英文", "ko": "韩文",
            "zh-CN": "简体中文", "zh-TW": "繁体中文",
            "auto": "外文",
        }
        src = lang_map.get(req.source_lang, req.source_lang)
        tgt = lang_map.get(req.target_lang, req.target_lang)

        if self._prompt_template:
            return self._prompt_template.format(
                src_lang=src, tgt_lang=tgt,
                glossary=glossary_text, context=context_text,
                text=req.text,
            )

        return (
            f"你是专业的游戏翻译器。将以下{src}游戏文本翻译为{tgt}。\n"
            f"要求：保留变量占位符(如{{{{name}}}}、%d)不翻译，翻译自然流畅。\n"
            f"只输出译文，不要解释。\n\n"
            f"{glossary_text}{context_text}"
            f"原文：{req.text}\n译文："
        )

    async def translate(self, request: TranslationRequest) -> TranslationResult:
        prompt = self._build_prompt(request)

        response = await self._client.post(
            f"{self._api_base}/chat/completions",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self._model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
                "max_tokens": 2000,
            },
        )
        response.raise_for_status()
        data = response.json()
        translated = data["choices"][0]["message"]["content"].strip()

        # 清理可能的引号包裹
        if translated.startswith('"') and translated.endswith('"'):
            translated = translated[1:-1]
        if translated.startswith("译文："):
            translated = translated[3:]

        return TranslationResult(
            original=request.text,
            translated=translated.strip(),
            engine=f"openai/{self._model}",
        )

    def name(self) -> str:
        return f"OpenAI ({self._model})"

    async def close(self):
        await self._client.aclose()


class DeepLEngine(TranslationEngine):
    """DeepL翻译引擎"""

    LANG_MAP = {
        "zh-CN": "ZH", "zh-TW": "ZH", "en": "EN",
        "ja": "JA", "ko": "KO", "de": "DE", "fr": "FR",
        "es": "ES", "ru": "RU", "pt": "PT",
    }

    def __init__(self, api_key: str, free: bool = True):
        self._api_key = api_key
        base = "https://api-free.deepl.com" if free else "https://api.deepl.com"
        self._url = f"{base}/v2/translate"
        self._client = httpx.AsyncClient(timeout=15.0)

    async def translate(self, request: TranslationRequest) -> TranslationResult:
        target = self.LANG_MAP.get(request.target_lang, request.target_lang.upper())
        params = {
            "text": request.text,
            "target_lang": target,
        }
        if request.source_lang != "auto":
            params["source_lang"] = self.LANG_MAP.get(
                request.source_lang, request.source_lang.upper()
            )

        response = await self._client.post(
            self._url,
            headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"},
            data=params,
        )
        response.raise_for_status()
        data = response.json()
        translated = data["translations"][0]["text"]

        return TranslationResult(
            original=request.text,
            translated=translated,
            engine="deepl",
        )

    def name(self) -> str:
        return "DeepL"

    async def close(self):
        await self._client.aclose()


class OllamaEngine(TranslationEngine):
    """Ollama本地LLM翻译引擎 (完全免费离线)"""

    def __init__(
        self,
        url: str = "http://localhost:11434",
        model: str = "qwen2.5:7b",
    ):
        self._url = url.rstrip("/")
        self._model = model
        self._client = httpx.AsyncClient(timeout=60.0)

    async def translate(self, request: TranslationRequest) -> TranslationResult:
        glossary_text = ""
        if request.glossary:
            pairs = [f"{k}→{v}" for k, v in request.glossary.items()]
            glossary_text = f"术语表：{', '.join(pairs)}\n"

        prompt = (
            f"将以下游戏文本翻译为中文，只输出译文：\n"
            f"{glossary_text}"
            f"{request.text}"
        )

        response = await self._client.post(
            f"{self._url}/api/generate",
            json={
                "model": self._model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.3},
            },
        )
        response.raise_for_status()
        data = response.json()
        translated = data.get("response", "").strip()

        return TranslationResult(
            original=request.text,
            translated=translated,
            engine=f"ollama/{self._model}",
        )

    def name(self) -> str:
        return f"Ollama ({self._model})"

    async def close(self):
        await self._client.aclose()


# ─── 预处理/后处理管道 ───

# 变量占位符模式
VAR_PATTERNS = [
    re.compile(r'\{[^}]+\}'),           # {name}, {count}
    re.compile(r'%[dsfx%]'),            # %d, %s, %f
    re.compile(r'%\d*\.?\d*[dsfx]'),    # %2d, %.2f
    re.compile(r'\$\{[^}]+\}'),         # ${var}
    re.compile(r'\\[nrt]'),             # \n, \r, \t
    re.compile(r'<[^>]+>'),             # <color=red>, </b>
]


def protect_variables(text: str) -> tuple[str, dict[str, str]]:
    """预处理: 保护变量占位符不被翻译"""
    placeholders = {}
    counter = 0

    for pattern in VAR_PATTERNS:
        for match in pattern.finditer(text):
            token = match.group()
            if token not in placeholders.values():
                key = f"__VAR_{counter}__"
                placeholders[key] = token
                text = text.replace(token, key, 1)
                counter += 1

    return text, placeholders


def restore_variables(text: str, placeholders: dict[str, str]) -> str:
    """后处理: 还原变量占位符"""
    for key, value in placeholders.items():
        text = text.replace(key, value)
    return text


def create_translation_engine(config) -> TranslationEngine:
    """工厂方法: 根据配置创建翻译引擎"""
    if config.engine == "openai":
        return OpenAIEngine(
            api_key=config.api_key,
            api_base=config.api_base,
            model=config.model,
            prompt_template=config.prompt_template,
        )
    elif config.engine == "deepl":
        return DeepLEngine(api_key=config.deepl_key)
    elif config.engine == "ollama":
        return OllamaEngine(url=config.ollama_url, model=config.ollama_model)
    else:
        raise ValueError(f"未知翻译引擎: {config.engine}")
