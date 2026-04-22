#!/bin/bash
# ============================================
# Steam游戏汉化工具 - 一键启动脚本 (macOS)
# 双击这个文件就能启动，全自动处理一切
# ============================================

# 进入项目目录（不管你从哪里双击）
cd "$(dirname "$0")"

echo ""
echo "╔══════════════════════════════════════╗"
echo "║     Steam 游戏汉化工具 v0.1          ║"
echo "║     正在启动，请稍候...              ║"
echo "╚══════════════════════════════════════╝"
echo ""

# ---------- 第1步：检查 Python ----------
PYTHON=""
if [ -f ".venv/bin/python" ]; then
    PYTHON=".venv/bin/python"
elif command -v python3.13 &>/dev/null; then
    PYTHON="python3.13"
elif command -v python3.12 &>/dev/null; then
    PYTHON="python3.12"
elif command -v python3 &>/dev/null; then
    PYTHON="python3"
else
    echo "❌ 错误：没有找到 Python"
    echo ""
    echo "请先安装 Python："
    echo "  打开终端，输入: brew install python@3.13"
    echo ""
    echo "如果没有 brew，先安装 brew："
    echo "  /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
    echo ""
    read -p "按回车键退出..."
    exit 1
fi

# ---------- 第2步：首次运行创建虚拟环境 ----------
if [ ! -d ".venv" ]; then
    echo "📦 首次运行，正在创建环境（只需要一次，大约2-5分钟）..."
    echo ""
    $PYTHON -m venv .venv
    if [ $? -ne 0 ]; then
        echo "❌ 创建环境失败，请检查 Python 安装"
        read -p "按回车键退出..."
        exit 1
    fi
    PYTHON=".venv/bin/python"

    echo "📥 正在安装依赖包..."
    .venv/bin/pip install --upgrade pip -q
    .venv/bin/pip install mss httpx Pillow numpy pynput cachetools shiboken6 PySide6-Essentials -q
    if [ $? -ne 0 ]; then
        echo "❌ 安装依赖失败，请检查网络连接"
        read -p "按回车键退出..."
        exit 1
    fi

    echo "📥 正在安装 OCR 文字识别引擎..."
    .venv/bin/pip install rapidocr-onnxruntime -q 2>/dev/null
    
    echo ""
    echo "✅ 环境安装完成！"
    echo ""
else
    PYTHON=".venv/bin/python"
fi

# ---------- 第3步：检查OCR引擎 ----------
$PYTHON -c "import rapidocr_onnxruntime" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "📥 正在安装 OCR 文字识别引擎..."
    .venv/bin/pip install rapidocr-onnxruntime -q 2>/dev/null
fi

# ---------- 第4步：自动启动 Ollama ----------
OLLAMA_PID=""
if command -v ollama &>/dev/null; then
    if curl -s http://localhost:11434/api/tags >/dev/null 2>&1; then
        echo "✅ Ollama 翻译引擎已在运行"
    else
        echo "🔄 正在启动 Ollama 翻译引擎..."
        ollama serve &>/dev/null &
        OLLAMA_PID=$!
        sleep 2
        if curl -s http://localhost:11434/api/tags >/dev/null 2>&1; then
            echo "✅ Ollama 翻译引擎启动成功"
        else
            echo "⚠️  Ollama 启动中，稍后可能需要等一下..."
        fi
    fi
else
    echo "💡 未安装 Ollama（免费翻译引擎）"
    echo "   安装方法: brew install ollama && ollama pull qwen2.5:7b"
    echo "   或使用 OpenAI/DeepL 引擎（需要 API Key）"
fi
echo ""

# ---------- 第5步：启动程序 ----------
echo "🚀 启动中..."
echo ""
echo "使用方法："
echo "  1. 点击「框选区域」选择你要翻译的游戏画面区域"
echo "  2. 点击「截图翻译」进行一次翻译"
echo "  3. 或点击「开始实时翻译」持续自动翻译"
echo ""
echo "────────────────────────────────────────"
echo ""

PYTHONPATH="$(pwd)" $PYTHON src/main.py

# 程序退出后，如果是我们启动的 Ollama，也关掉
if [ -n "$OLLAMA_PID" ]; then
    echo "🔄 正在关闭 Ollama..."
    kill "$OLLAMA_PID" 2>/dev/null
fi

echo ""
read -p "程序已退出，按回车键关闭窗口..."
