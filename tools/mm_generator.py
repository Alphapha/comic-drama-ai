"""
多模态模型调用 - 剧本生成 + 图片生成
======================================
统一封装 OpenAI / Gemini / 通义 三种后端，
对外暴露两个核心函数:
    generate_episode(story_idea) -> Episode
    generate_image(prompt, style) -> image_path
"""

import os
import json
import time
import requests
from typing import Optional

from .schema import Episode, Shot, Scene, Dialogue, Character
from .config import CONFIG, ensure_dirs


# ============================================================
# 1. 剧本生成 Prompt 模板
# ============================================================

SYSTEM_PROMPT = """你是一位顶级漫剧编剧和分镜师。请根据用户的创意，输出符合指定 JSON 格式的完整漫剧剧本+分镜方案。

关键要求:
1. 所有 image_prompt 必须用**英文** (供 DALL-E / Stable Diffusion 使用)
2. 每个场景至少 3-5 个分镜，整集 10-20 个分镜为佳
3. 分镜之间要有**镜头语言变化** (特写/中景/远景/过肩等交替)
4. 适当加入 camera_move 动态效果 (zoom-in/zoom-out/pan 等)
5. 对白要口语化、简洁，每个分镜对白不超过 2-3 句
6. 人物描述必须与 image_prompt 中角色外观**完全一致** (保证AI出图一致性)
7. duration 设置要合理，对白多的镜头时长略长

请**严格按照 JSON Schema 输出**，不要加任何额外解释或 markdown 代码块标记。"""


USER_STORY_PROMPT = """故事创意:
{story_idea}

请生成完整的漫剧剧本，包含:
- 2-4 个角色
- 2-3 个场景
- 总时长约 60-90 秒
- 竖屏 (9:16) 适配短视频平台
"""


# ============================================================
# 2. 模型调用封装
# ============================================================

def _call_openai_chat(user_prompt: str, system_prompt: str = SYSTEM_PROMPT) -> str:
    """调用 OpenAI Chat (支持 GPT-4o / gpt-4o-mini 等)"""
    from openai import OpenAI

    cfg = CONFIG["openai"]
    client = OpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"])
    model = cfg["chat_model"]

    print(f"[AI Chat] 调用 {model} ...")
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.8,
    )
    return resp.choices[0].message.content


def _call_gemini_chat(user_prompt: str) -> str:
    """调用 Google Gemini (可选)"""
    import google.generativeai as genai

    cfg = CONFIG["gemini"]
    genai.configure(api_key=cfg["api_key"])
    model = genai.GenerativeModel("gemini-1.5-pro")

    print("[AI Chat] 调用 Gemini 1.5 Pro ...")
    resp = model.generate_content(
        SYSTEM_PROMPT + "\n\n" + user_prompt,
        generation_config={"temperature": 0.8},
    )
    # Gemini 可能返回带 ```json ... ``` 的包裹
    text = resp.text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    return text


def _call_qwen_chat(user_prompt: str) -> str:
    """调用阿里通义 (可选)"""
    from openai import OpenAI  # 通义也兼容 OpenAI SDK

    client = OpenAI(
        api_key=CONFIG["qwen"]["api_key"],
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    print("[AI Chat] 调用 通义千问 ...")
    resp = client.chat.completions.create(
        model="qwen-plus",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
    )
    return resp.choices[0].message.content


# ============================================================
# 3. 剧本生成主函数
# ============================================================

def generate_episode(story_idea: str, json_path: Optional[str] = None) -> Episode:
    """
    根据故事创意生成完整漫剧剧本 (JSON + Episode 对象)

    Args:
        story_idea: 故事创意描述，越具体越好
        json_path: 可选，指定剧本 JSON 保存路径

    Returns:
        Episode 对象
    """
    provider = CONFIG["model_provider"]
    user_prompt = USER_STORY_PROMPT.format(story_idea=story_idea)

    if provider == "openai":
        raw = _call_openai_chat(user_prompt)
    elif provider == "gemini":
        raw = _call_gemini_chat(user_prompt)
    elif provider == "qwen":
        raw = _call_qwen_chat(user_prompt)
    else:
        raise ValueError(f"未知的模型提供方: {provider}")

    # 解析 JSON
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"[警告] JSON 解析失败，原始返回:\n{raw[:500]}")
        raise e

    episode = Episode.from_dict(data)
    print(f"[剧本] '{episode.title}' 生成成功: "
          f"{len(episode.characters)}角色 / "
          f"{len(episode.scenes)}场景 / "
          f"{episode.total_shots()}分镜 / "
          f"总时长约 {episode.total_duration():.0f}秒")

    if json_path is None:
        ensure_dirs()
        json_path = os.path.join(CONFIG["paths"]["data"], "episode.json")
    episode.to_json(json_path)

    return episode


# ============================================================
# 4. 图片生成
# ============================================================

def _image_prompt_with_style(base_prompt: str, style: str) -> str:
    """把画风合并进 prompt"""
    if style and style not in base_prompt:
        return f"{base_prompt}, {style}"
    return base_prompt


def _generate_openai_image(prompt: str) -> str:
    """调用 DALL-E 3 生成图片，返回保存路径"""
    from openai import OpenAI

    cfg = CONFIG["openai"]
    client = OpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"])

    print(f"[DALL-E] 生成图片: {prompt[:80]}...")
    resp = client.images.generate(
        model=cfg["image_model"],
        prompt=prompt,
        size="1024x1792",   # 9:16 竖屏
        quality="standard",
        n=1,
    )

    img_url = resp.data[0].url
    # 下载保存
    ensure_dirs()
    fname = f"img_{int(time.time()*1000)}.png"
    save_path = os.path.join(CONFIG["paths"]["artwork"], fname)
    r = requests.get(img_url)
    r.raise_for_status()
    with open(save_path, "wb") as f:
        f.write(r.content)
    print(f"[DALL-E] 已保存 -> {save_path}")
    return save_path


def generate_image(prompt: str, style: str = "", save_path: Optional[str] = None) -> str:
    """
    生成单张图片

    Args:
        prompt: 英文提示词
        style: 画风附加词 (如 "anime style, cinematic")
        save_path: 可选指定保存路径

    Returns:
        本地图片路径
    """
    full_prompt = _image_prompt_with_style(prompt, style)
    provider = CONFIG["model_provider"]

    if provider == "openai":
        return _generate_openai_image(full_prompt)
    # TODO: 其他 provider 图片接口
    raise NotImplementedError(f"{provider} 图片生成待实现")


def generate_all_artwork(episode: Episode) -> dict:
    """
    批量为所有分镜生成插画

    Returns:
        dict: key=shot_id, value=图片本地路径
    """
    shot_images = {}
    for scene in episode.scenes:
        for shot in scene.shots:
            if not shot.image_prompt:
                continue
            path = generate_image(shot.image_prompt, shot.image_style)
            shot_images[shot.shot_id] = path
            time.sleep(2)  # 速率限制友好
    return shot_images


# ============================================================
# 5. 便捷入口：一键从故事创意到剧本+图片
# ============================================================

def create_episode_from_idea(story_idea: str, gen_images: bool = False) -> Episode:
    """
    最上层便捷函数: 故事创意 -> Episode 对象

    Args:
        story_idea: 故事创意
        gen_images: True 则同时让 AI 生成插画 (较慢/费 token)

    Returns:
        Episode 对象
    """
    episode = generate_episode(story_idea)

    if gen_images:
        print("\n>>> 开始生成插画...")
        mapping = generate_all_artwork(episode)
        # 把图片路径写回到 artwork 目录，便于后续使用
        with open(os.path.join(CONFIG["paths"]["data"], "shot_images.json"), "w") as f:
            json.dump(mapping, f, indent=2, ensure_ascii=False)

    return episode


if __name__ == "__main__":
    # 测试: 生成一个 Demo 剧本 (不调用 API)
    # 实际调用见 main.py
    print("模块可用。请通过 main.py 或 create_episode_from_idea() 使用。")
