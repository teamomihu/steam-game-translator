"""Steam游戏汉化工具 - 主入口"""

from __future__ import annotations

import asyncio
import logging
import sys

from src.core.config import AppConfig, apply_env_overrides

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("steam-translator")


def check_api_key(config: AppConfig):
    """检查翻译引擎的 API Key 是否配置"""
    engine = config.translation.engine
    if engine == "openai" and not config.translation.api_key:
        logger.warning("未设置 OpenAI API Key")
        logger.warning("设置方法（任选一种）：")
        logger.warning("  方法1: 启动前在终端运行 export OPENAI_API_KEY=\"你的key\"")
        logger.warning("  方法2: 编辑 ~/.steam-translator/config.json 填入 api_key")
        logger.warning("  方法3: 在程序设置中切换为 ollama（免费本地翻译）")
        logger.warning("")
        logger.warning("没有 API Key？推荐使用免费方案：")
        logger.warning("  1. 安装 Ollama: https://ollama.com/download")
        logger.warning("  2. 终端运行: ollama pull qwen2.5:7b")
        logger.warning("  3. 程序中翻译引擎选择 ollama")
    elif engine == "deepl" and not config.translation.deepl_key:
        logger.warning("未设置 DeepL API Key")
        logger.warning("设置方法: export DEEPL_API_KEY=\"你的key\"")


def main():
    """主入口"""
    logger.info("Steam游戏汉化工具 v0.1.0 启动中...")

    # 加载配置
    config = AppConfig.load()
    config = apply_env_overrides(config)
    logger.info(f"OCR引擎: {config.ocr.engine}")
    logger.info(f"翻译引擎: {config.translation.engine}")

    # 检查 API Key
    check_api_key(config)

    # 检查是否有GUI环境
    try:
        from src.overlay.app import run_app
        run_app(config)
    except ImportError as e:
        logger.warning(f"GUI模块不可用 ({e})，进入CLI模式")
        asyncio.run(cli_mode(config))


async def cli_mode(config: AppConfig):
    """CLI模式: 截图翻译"""
    from src.core.pipeline import TranslationPipeline, PipelineResult
    from src.core.screenshot import CaptureRegion

    pipeline = TranslationPipeline(config)

    print("\n=== Steam游戏汉化工具 (CLI模式) ===")
    print("用法: 输入截图区域坐标 (x,y,width,height) 或 'q' 退出")
    print("示例: 100,200,800,600\n")

    while True:
        try:
            user_input = input("区域坐标> ").strip()
            if user_input.lower() in ("q", "quit", "exit"):
                break

            parts = [int(p.strip()) for p in user_input.split(",")]
            if len(parts) != 4:
                print("格式错误，需要4个数字: x,y,width,height")
                continue

            region = CaptureRegion(*parts)
            print(f"正在截图并翻译区域 ({region.x},{region.y}) {region.width}x{region.height}...")

            result = await pipeline.translate_region(region)

            if not result.blocks:
                print("  未识别到文字")
                continue

            print(f"  识别到 {len(result.blocks)} 个文本块:")
            for i, block in enumerate(result.blocks, 1):
                print(f"  [{i}] {block.original}")
                print(f"      → {block.translated}")
            print(f"  耗时: OCR={result.ocr_time_ms:.0f}ms, 翻译={result.translate_time_ms:.0f}ms")
            print(f"  缓存: {result.cache_hits}命中, {result.cache_misses}未命中")

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"  错误: {e}")

    print("\n再见!")


if __name__ == "__main__":
    main()
