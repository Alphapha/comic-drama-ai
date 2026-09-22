"""
AI 配音 (Edge-TTS)
====================
Edge-TTS 是微软 Azure TTS 的免费本地调用版本，
中文语音自然度高，且完全免费。

角色性别 -> TTS 语音自动匹配
"""

import os
import asyncio
import tempfile
from typing import Dict, Optional

from .schema import Episode, Character, Dialogue
from .config import CONFIG, ensure_dirs


def _pick_voice(gender: str) -> str:
    """根据角色性别返回对应 TTS 语音"""
    voices = CONFIG["tts"]
    g = gender.lower()
    if g == "female":
        return voices["voice_female"]
    if g == "narrator":
        return voices["voice_narrator"]
    return voices["voice_male"]


async def _tts_one(text: str, voice: str, rate: str, volume: str, out_path: str):
    """调用 Edge-TTS 生成单条语音 (async)"""
    import edge_tts

    communicate = edge_tts.Communicate(text=text, voice=voice, rate=rate, volume=volume)
    await communicate.save(out_path)


def synthesize_one(
    text: str,
    voice_gender: str = "male",
    out_path: Optional[str] = None,
) -> str:
    """
    同步封装: 生成单条配音

    Args:
        text: 要朗读的文字
        voice_gender: male / female / narrator
        out_path: 输出文件路径 (.mp3)

    Returns:
        生成文件路径
    """
    ensure_dirs()
    if out_path is None:
        out_path = os.path.join(CONFIG["paths"]["audio"], f"tts_{os.getpid()}_{id(text)}.mp3")

    cfg = CONFIG["tts"]
    voice = _pick_voice(voice_gender)

    # Edge-TTS 是 async 的，这里跑事件循环
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # 嵌套事件循环: 开新线程跑
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as ex:
                fut = ex.submit(asyncio.run, _tts_one(text, voice, cfg["rate"], cfg["volume"], out_path))
                fut.result()
        else:
            loop.run_until_complete(_tts_one(text, voice, cfg["rate"], cfg["volume"], out_path))
    except RuntimeError:
        asyncio.run(_tts_one(text, voice, cfg["rate"], cfg["volume"], out_path))

    return out_path


def synthesize_episode_dialogues(
    episode: Episode,
) -> Dict[int, Dict[int, str]]:
    """
    批量生成整集所有对白的配音文件

    Returns:
        dict[shot_id][dlg_index] = 语音文件路径
    """
    ensure_dirs()

    # 预构建角色->性别 映射
    char_gender = {c.name: c.voice_gender for c in episode.characters}

    audio_map: Dict[int, Dict[int, str]] = {}

    for scene in episode.scenes:
        for shot in scene.shots:
            shot_audios = {}
            for idx, dlg in enumerate(shot.dialogue):
                if not dlg.text.strip():
                    continue
                gender = char_gender.get(dlg.character, "male")
                fname = f"shot{shot.shot_id}_dlg{idx}_{dlg.character}.mp3"
                out_path = os.path.join(CONFIG["paths"]["audio"], fname)
                print(f"[TTS] shot{shot.shot_id} 台词{idx} ({dlg.character}): {dlg.text[:20]}...")
                synthesize_one(dlg.text, gender, out_path)
                shot_audios[idx] = out_path
            if shot_audios:
                audio_map[shot.shot_id] = shot_audios

    return audio_map


if __name__ == "__main__":
    # 快速自测
    test_path = "_test_tts.mp3"
    try:
        p = synthesize_one("你好，我是 Edge TTS 测试语音。", "male", test_path)
        print(f"生成: {p}")
        os.remove(p)
    except ImportError:
        print("请先: pip install edge-tts")
