"""
统一配置加载
从 .env 文件读取，提供全局 config 字典
"""

import os
from dotenv import load_dotenv

# 项目根目录 (脚本可从任意目录运行)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 尝试加载项目根目录下的 .env
_env_path = os.path.join(PROJECT_ROOT, ".env")
if os.path.exists(_env_path):
    load_dotenv(_env_path)
else:
    load_dotenv()  # fallback 到工作目录


def _env(key: str, default: str = "") -> str:
    """环境变量读取，去首尾空白"""
    return os.getenv(key, default).strip()


# ========== 模型配置 ==========
CONFIG = {
    # 主模型提供方
    "model_provider": _env("MM_MODEL_PROVIDER", "openai").lower(),

    # OpenAI
    "openai": {
        "api_key": _env("OPENAI_API_KEY"),
        "base_url": _env("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        "chat_model": _env("OPENAI_CHAT_MODEL", "gpt-4o"),
        "image_model": _env("OPENAI_IMAGE_MODEL", "dall-e-3"),
    },

    # Gemini
    "gemini": {
        "api_key": _env("GEMINI_API_KEY"),
    },

    # 通义
    "qwen": {
        "api_key": _env("DASHSCOPE_API_KEY"),
    },

    # ========== TTS 配置 ==========
    "tts": {
        "engine": _env("TTS_ENGINE", "edge-tts"),
        "voice_male": _env("TTS_VOICE_MALE", "zh-CN-YunxiNeural"),
        "voice_female": _env("TTS_VOICE_FEMALE", "zh-CN-XiaoxiaoNeural"),
        "voice_narrator": _env("TTS_VOICE_NARRATOR", "zh-CN-YunxiNeural"),
        "rate": _env("TTS_RATE", "+0%"),
        "volume": _env("TTS_VOLUME", "+0%"),
    },

    # ========== 视频输出 ==========
    "video": {
        "width": int(_env("VIDEO_WIDTH", "1080")),
        "height": int(_env("VIDEO_HEIGHT", "1920")),
        "fps": int(_env("VIDEO_FPS", "30")),
        "format": _env("VIDEO_FORMAT", "mp4"),
    },

    # ========== 路径 ==========
    "paths": {
        "project_root": PROJECT_ROOT,
        "artwork": os.path.join(PROJECT_ROOT, _env("ARTWORK_DIR", "artwork")),
        "frames": os.path.join(PROJECT_ROOT, _env("FRAMES_DIR", "frames")),
        "audio": os.path.join(PROJECT_ROOT, _env("AUDIO_DIR", "audio")),
        "output": os.path.join(PROJECT_ROOT, _env("OUTPUT_DIR", "output")),
        "data": os.path.join(PROJECT_ROOT, _env("DATA_DIR", "data")),
    },
}


def ensure_dirs():
    """确保所有输出目录存在"""
    for p in CONFIG["paths"].values():
        if isinstance(p, str) and not os.path.exists(p):
            os.makedirs(p, exist_ok=True)


def print_config():
    """打印当前生效的配置 (隐藏密钥)"""
    print("=" * 50)
    print("漫剧 AI 制作 - 当前配置")
    print("=" * 50)
    print(f"主模型提供方 : {CONFIG['model_provider']}")
    for k, v in CONFIG.items():
        if isinstance(v, dict):
            print(f"\n[{k}]")
            for kk, vv in v.items():
                if "key" in kk and vv:
                    vv = vv[:8] + "..."
                print(f"  {kk}: {vv}")
    print("=" * 50)


if __name__ == "__main__":
    ensure_dirs()
    print_config()
