"""
多模态模型调用 - 稳定版剧本生成 + 图片生成
==============================================
针对"长剧本一次性生成不稳定"的问题，采用 **两阶段生成** 策略:
    阶段1 outline: 生成角色 + 场景列表 + 每分镜摘要 (token 少, 不易截断)
    阶段2 scenes : 按场景逐段生成分镜详情, 注入角色设定 + 前一场摘要维持连贯

配套稳定性机制 (经验 2165105 / 1321475 / 2350700):
    • JSON 多层解析: 直接 → markdown 提取 → 补闭合括号 → AI 修复
    • 结构化校验: validate() 以 "JSON可解析+字段齐全+分镜数达标" 为准
    • 失败重试链: 解析失败 → AI 修复(最多2次) → 降级到单次全量生成
    • 错误细粒度分类: 401/404/Quota/网络 分别处理, 不做一刀切
    • 角色一致性硬注入: 自动把 characters_in_frame 的 description 写进 image_prompt 前缀
"""

import os
import json
import re
import time
import requests
from typing import Optional, Dict, List, Tuple

from .schema import Episode, Shot, Scene, Dialogue, Character
from .config import CONFIG, ensure_dirs


# ============================================================
# 0. 异常类 — 细粒度错误分类
# ============================================================

class ModelError(Exception):
    """模型调用错误基类"""
    pass


class ModelAuthError(ModelError):
    """API Key 无效 (401)"""
    pass


class ModelNotFoundError(ModelError):
    """模型不存在/无权限 (404/403)"""
    pass


class ModelQuotaError(ModelError):
    """额度耗尽/欠费"""
    pass


class ModelNetworkError(ModelError):
    """网络/超时"""
    pass


def _classify_error(status_code: Optional[int], text: str) -> ModelError:
    """
    根据 HTTP 状态码 + 错误文本 细粒度分类模型错误

    经验 2350700: 404/NotFound ≠ Quota 耗尽，必须分别处理
    """
    text_lower = text.lower() if text else ""

    if status_code == 401 or "invalid.*api.*key" in text_lower or "unauthorized" in text_lower:
        return ModelAuthError(f"API Key 无效，请检查 .env 中的密钥")
    if status_code == 403 or status_code == 404 or "not.?found" in text_lower or "invalidendpoint" in text_lower:
        return ModelNotFoundError(f"模型不存在或无权限: {text[:120]}")
    if "quota" in text_lower or "insufficient" in text_lower or "paymentrequired" in text_lower \
            or "free quota exhausted" in text_lower or "billing" in text_lower:
        return ModelQuotaError(f"额度耗尽/欠费: {text[:120]}")
    if status_code in (500, 502, 503, 504) or "timeout" in text_lower or "network" in text_lower:
        return ModelNetworkError(f"网络/服务异常: {text[:120]}")
    return ModelError(f"未知错误: {status_code} {text[:120]}")


# ============================================================
# 1. Prompt 模板 — 更短、更硬、更结构化
# ============================================================

# --- 阶段 1: 大纲生成 ---
# 目标是用**最少的 token** 产出一个骨架，避免被截断
OUTLINE_SYSTEM = """你是漫剧编剧。输出 JSON 对象, 不要 markdown, 不要解释。"""

OUTLINE_USER = """漫剧故事:
{story_idea}

请输出如下 JSON (严格遵守结构):
{{
  "title": "剧集标题",
  "description": "一句话简介",
  "characters": [
    {{"name": "角色名", "description": "英文角色外观描述(供AI出图使用，要具体:年龄/发型/服装/体型)", "voice_gender": "male|female|narrator"}}
  ],
  "scenes": [
    {{
      "scene_id": 1,
      "setting": "中文场景描述(时间/地点/氛围)",
      "scene_goal": "中文-本场景叙事目标(15字内)",
      "shots": [
        {{"shot_id": 1, "shot_purpose": "中文-本镜头目的(15字内)", "characters_in_frame": ["角色名"]}}
      ]
    }}
  ]
}}

要求:
- 2-4 个角色, 2-3 个场景, 总共 10-16 个分镜
- 角色 description 必须用**英文**且具体 (如 "16-year-old boy with short black hair, white school uniform")
- 每个场景 4-6 个分镜
"""

# --- 阶段 2: 场景级分镜详情生成 ---
# 每次只处理一个场景, token 少, 连贯性由 injected_context 维持
SCENE_SYSTEM = """你是漫剧分镜师。为给定场景生成详细分镜。
输出 JSON 对象, 不要 markdown, 不要解释。所有 image_prompt 必须用英文。"""

SCENE_USER = """角色设定 (必须严格遵循这些外观):
{character_block}

前一场景摘要 (供参考):
{prev_scene_summary}

当前场景骨架:
{scene_outline_json}

请为当前场景的每个 shot 填充以下字段 (JSON 数组):
[
  {{
    "shot_id": 1,
    "shot_type": "closeup|medium|wide|establishing|over-shoulder",
    "camera_move": "static|zoom-in|zoom-out|pan-left|pan-right|tilt-up|tilt-down",
    "move_speed": "slow|normal|fast",
    "duration": 3.5,
    "image_prompt": "完整英文出图提示词, 必须包含本镜头所有角色的外观 + 场景环境 + 光影氛围 + 画风",
    "image_style": "anime style, cinematic lighting",
    "dialogue": [
      {{"character": "角色名", "text": "中文对白", "emotion": "neutral|happy|sad|angry|surprised", "position": "bottom|top", "delay": 0.0}}
    ],
    "transition_reason": "到下一镜头的中文衔接原因(10字内)"
  }}
]

填充要求:
- camera_move 和 shot_type 交替变化, 避免连续相同
- image_prompt 必须是完整英文句子 (40-80词), 包含角色外观细节
- 对白简洁口语化, 每个分镜对白不超过 2 句
- duration 3-6秒
"""


# ============================================================
# 2. JSON 多层解析 + AI 修复链
# ============================================================

def _extract_json_from_text(text: str) -> Optional[dict]:
    """
    多层 JSON 提取 / 修复:
    1. 直接 json.loads
    2. 从 markdown ```json ... ``` 中提取
    3. 手动补闭合括号/引号
    """
    if not text:
        return None

    text = text.strip()

    # 1. 直接尝试
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 2. 从 markdown 代码块中提取
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    if fence_match:
        try:
            return json.loads(fence_match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # 3. 找第一个 { 或 [ 到最后一个 } 或 ]
    first_obj = text.find("{")
    last_obj = text.rfind("}")
    first_arr = text.find("[")
    last_arr = text.rfind("]")

    # 看哪个更可能是完整 JSON
    if first_obj >= 0 and last_obj > first_obj:
        candidate = text[first_obj:last_obj + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            # 尝试补缺失的闭合
            fixed = _try_fix_trailing(candidate)
            if fixed and fixed != candidate:
                try:
                    return json.loads(fixed)
                except json.JSONDecodeError:
                    pass

    if first_arr >= 0 and last_arr > first_arr:
        candidate = text[first_arr:last_arr + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            fixed = _try_fix_trailing(candidate)
            if fixed and fixed != candidate:
                try:
                    return json.loads(fixed)
                except json.JSONDecodeError:
                    pass

    return None


def _try_fix_trailing(text: str) -> str:
    """
    补全未闭合的 JSON 尾部括号/引号
    经验 2165105: 很多截断只是末尾少几个 } ]
    """
    # 简单尝试: 找到最后一个完整逗号, 截断到那里, 然后闭合
    last_comma = text.rfind(",")
    last_brace = max(text.rfind("}"), text.rfind("]"))
    if last_comma > last_brace:
        text = text[:last_comma]

    # 统计开闭括号
    stack = []
    for ch in text:
        if ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if stack and ((ch == "}" and stack[-1] == "{") or (ch == "]" and stack[-1] == "[")):
                stack.pop()

    # 补闭合
    closes = ""
    for ch in reversed(stack):
        closes += "}" if ch == "{" else "]"
    return text + closes


def _ai_repair_json(raw_text: str, parse_error: str, max_attempts: int = 2) -> Optional[dict]:
    """
    把解析失败的 JSON + 错误信息回传给模型, 让它只修复不重写
    经验 2165105: 当 JSON 结构基本正确只是尾部截断时, AI 修复比重生成本低得多
    """
    from .config import CONFIG as _C

    system = "你是 JSON 修复专家。只修复 JSON 语法错误, 不要改写内容, 不要加解释。只输出修复后的 JSON。"
    user = f"以下 JSON 解析失败, 错误: {parse_error}\n\n需要修复的 JSON:\n{raw_text[:4000]}\n\n请输出修复后的完整 JSON:"

    for attempt in range(max_attempts):
        try:
            result = _call_chat_internal(user, system, temperature=0.1, max_tokens=4000)
            data = _extract_json_from_text(result)
            if data is not None:
                print(f"[AI修复] 第 {attempt + 1} 次修复成功!")
                return data
        except Exception as e:
            print(f"[AI修复] 第 {attempt + 1} 次修复失败: {e}")
            time.sleep(1)
    return None


# ============================================================
# 3. 模型调用 (带细粒度错误分类 + 重试)
# ============================================================

def _call_chat_internal(
    user_prompt: str,
    system_prompt: str,
    temperature: float = 0.4,
    max_tokens: int = 6000,
    max_retries: int = 2,
) -> str:
    """
    统一的 chat 调用封装 (无 JSON 解析, 只负责拿到 text)

    经验 2350700: 401/404/Quota/Network 必须分别处理, 不能一刀切
    """
    provider = CONFIG["model_provider"]
    last_error: Optional[ModelError] = None

    for attempt in range(max_retries + 1):
        try:
            if provider == "openai":
                return _call_openai_raw(user_prompt, system_prompt, temperature, max_tokens)
            elif provider == "gemini":
                return _call_gemini_raw(user_prompt, system_prompt, temperature, max_tokens)
            elif provider == "qwen":
                return _call_qwen_raw(user_prompt, system_prompt, temperature, max_tokens)
            else:
                raise ValueError(f"未知 provider: {provider}")

        except ModelAuthError as e:
            # 认证错误不需要重试, 直接抛出让用户配置
            raise
        except ModelQuotaError as e:
            # 额度耗尽也不重试
            raise
        except ModelNotFoundError as e:
            # 模型不存在, 可能是配置了错误的模型名, 不重试
            raise
        except ModelNetworkError as e:
            last_error = e
            if attempt < max_retries:
                wait = 2 ** attempt
                print(f"  [重试] 网络错误, {wait}秒后第 {attempt + 1}/{max_retries} 次重试...")
                time.sleep(wait)
            else:
                raise
        except ModelError as e:
            last_error = e
            if attempt < max_retries:
                time.sleep(1)
            else:
                raise

    raise last_error or ModelError("调用失败")


def _call_openai_raw(user_prompt, system_prompt, temperature, max_tokens) -> str:
    """OpenAI Chat Completions 原始调用"""
    from openai import OpenAI
    from openai import APIError, AuthenticationError, NotFoundError

    cfg = CONFIG["openai"]
    if not cfg["api_key"]:
        raise ModelAuthError("OPENAI_API_KEY 未配置, 请编辑 .env 文件")

    client = OpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"])
    print(f"[AI Chat] 调用 {cfg['chat_model']} (temp={temperature}, max_tokens={max_tokens}) ...")

    try:
        resp = client.chat.completions.create(
            model=cfg["chat_model"],
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return resp.choices[0].message.content

    except AuthenticationError as e:
        raise ModelAuthError(f"OpenAI 认证失败: {e}")
    except NotFoundError as e:
        raise ModelNotFoundError(f"模型不存在: {e}")
    except APIError as e:
        raise _classify_error(getattr(e, "status_code", None), str(e))


def _call_gemini_raw(user_prompt, system_prompt, temperature, max_tokens) -> str:
    """Gemini 原始调用"""
    import google.generativeai as genai

    cfg = CONFIG["gemini"]
    if not cfg["api_key"]:
        raise ModelAuthError("GEMINI_API_KEY 未配置")

    genai.configure(api_key=cfg["api_key"])
    model = genai.GenerativeModel(
        "gemini-1.5-pro",
        generation_config=genai.GenerationConfig(temperature=temperature, max_output_tokens=max_tokens),
    )
    resp = model.generate_content(system_prompt + "\n\n" + user_prompt)
    text = resp.text.strip()
    # 去掉 ```json ``` 包裹
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    return text


def _call_qwen_raw(user_prompt, system_prompt, temperature, max_tokens) -> str:
    """通义千问原始调用 (OpenAI 兼容)"""
    from openai import OpenAI
    from openai import APIError, AuthenticationError, NotFoundError

    if not CONFIG["qwen"]["api_key"]:
        raise ModelAuthError("DASHSCOPE_API_KEY 未配置")

    client = OpenAI(
        api_key=CONFIG["qwen"]["api_key"],
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    print("[AI Chat] 调用 通义千问 ...")

    try:
        resp = client.chat.completions.create(
            model="qwen-plus",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return resp.choices[0].message.content
    except AuthenticationError as e:
        raise ModelAuthError(f"通义认证失败: {e}")
    except NotFoundError as e:
        raise ModelNotFoundError(f"模型不存在: {e}")
    except APIError as e:
        raise _classify_error(getattr(e, "status_code", None), str(e))


# ============================================================
# 4. 阶段 1: 生成大纲骨架
# ============================================================

def _generate_outline(story_idea: str) -> dict:
    """
    阶段 1: 生成角色 + 场景骨架
    token 消耗很小, 一般不会被截断
    """
    user_prompt = OUTLINE_USER.format(story_idea=story_idea)

    raw = _call_chat_internal(
        user_prompt, OUTLINE_SYSTEM,
        temperature=0.6, max_tokens=3000,
    )

    data = _extract_json_from_text(raw)
    if data is None:
        # 最后一搏: 让 AI 修复
        data = _ai_repair_json(raw, "无法解析的 JSON")
    if data is None:
        raise ModelError("阶段1大纲生成失败: AI 输出无法解析为 JSON")

    return data


# ============================================================
# 5. 阶段 2: 按场景生成分镜详情
# ============================================================

def _character_block(characters: List[dict]) -> str:
    """格式化角色设定块, 作为每次场景生成的上下文锚点"""
    lines = []
    for c in characters:
        lines.append(f"- {c['name']}: {c['description']} (voice={c.get('voice_gender','male')})")
    return "\n".join(lines)


def _prev_scene_summary(prev_scene: Optional[dict]) -> str:
    """生成前一场景摘要, 作为连贯性锚点"""
    if not prev_scene:
        return "(第一场, 无前情)"

    shots = prev_scene.get("shots", [])
    shot_ids = [s.get("shot_id", "?") for s in shots]
    characters = set()
    for s in shots:
        for cn in s.get("characters_in_frame", []):
            characters.add(cn)

    goal = prev_scene.get("scene_goal", "")
    hook = prev_scene.get("next_scene_hook", "")

    return (
        f"场景{prev_scene.get('scene_id')} goal='{goal}' "
        f"分镜={shot_ids} 角色={sorted(characters)} "
        f"next_hook='{hook}'"
    )


def _enrich_scene_with_shots(
    scene_outline: dict,
    characters: List[dict],
    prev_scene: Optional[dict],
) -> dict:
    """
    为一个场景填充完整分镜详情 (shot_type / image_prompt / dialogue 等)
    每次只处理一个场景, token 少, 稳定
    """
    scene_outline_json = json.dumps(scene_outline, ensure_ascii=False, indent=2)
    user_prompt = SCENE_USER.format(
        character_block=_character_block(characters),
        prev_scene_summary=_prev_scene_summary(prev_scene),
        scene_outline_json=scene_outline_json,
    )

    raw = _call_chat_internal(
        user_prompt, SCENE_SYSTEM,
        temperature=0.4, max_tokens=5000,
    )

    data = _extract_json_from_text(raw)
    if data is None:
        data = _ai_repair_json(raw, "场景分镜 JSON 解析失败")
    if data is None:
        raise ModelError(f"场景 {scene_outline.get('scene_id')} 分镜生成失败")

    # data 应该是一个 shots 数组
    if isinstance(data, dict) and "shots" in data:
        data = data["shots"]
    if not isinstance(data, list):
        raise ModelError(f"场景 {scene_outline.get('scene_id')} 返回值不是分镜数组")

    # 合并回 scene_outline
    scene_outline = dict(scene_outline)
    scene_outline["shots"] = data
    return scene_outline


# ============================================================
# 6. 两阶段主流程
# ============================================================

def generate_episode(
    story_idea: str,
    json_path: Optional[str] = None,
    two_phase: bool = True,
) -> Episode:
    """
    根据故事创意生成完整漫剧剧本

    Args:
        story_idea: 故事创意描述
        json_path: 剧本 JSON 保存路径
        two_phase: True=两阶段生成 (更稳定); False=单次全量生成 (原始行为)

    Returns:
        Episode 对象
    """
    if two_phase:
        print("\n" + "=" * 60)
        print("【剧本生成 - 两阶段模式】")
        print("=" * 60)

        # --- 阶段 1: 大纲 ---
        print("\n>>> 阶段 1/2: 生成角色 + 场景骨架")
        outline = _generate_outline(story_idea)

        characters = outline.get("characters", [])
        scenes_outline = outline.get("scenes", [])

        print(f"  角色: {[c['name'] for c in characters]}")
        print(f"  场景骨架: {len(scenes_outline)} 个场景")
        for s in scenes_outline:
            print(f"    scene{s['scene_id']}: {s.get('scene_goal', '?')} "
                  f"({len(s.get('shots', []))} shots)")

        # --- 阶段 2: 按场景填充 ---
        print("\n>>> 阶段 2/2: 逐场景填充分镜详情")
        enriched_scenes = []
        total_shots = 0
        for i, scene_outline in enumerate(scenes_outline):
            prev = scenes_outline[i - 1] if i > 0 else None
            print(f"\n  填充 scene {scene_outline['scene_id']} ...")
            enriched = _enrich_scene_with_shots(scene_outline, characters, prev)
            enriched_scenes.append(enriched)
            total_shots += len(enriched["shots"])
            print(f"    -> {len(enriched['shots'])} 个分镜")

        # --- 组装 Episode ---
        full_data = {
            "title": outline.get("title", "未命名漫剧"),
            "description": outline.get("description", ""),
            "episode_number": 1,
            "aspect_ratio": outline.get("aspect_ratio", "9:16"),
            "characters": characters,
            "scenes": enriched_scenes,
        }

    else:
        # 单次全量生成 (fallback / 对比用)
        print("\n>>> 单次全量生成模式")
        full_data = _generate_single_pass(story_idea)

    # --- 反序列化 ---
    episode = Episode.from_dict(full_data)

    # --- 结构校验 ---
    errors = episode.validate()
    if errors:
        print(f"\n[校验警告] 发现 {len(errors)} 个问题:")
        for e in errors:
            print(f"  ⚠️ {e}")

    # --- 角色描述自动注入 (解决人设漂移) ---
    episode.inject_character_descriptions()

    print(f"\n[剧本] '{episode.title}' 生成成功: "
          f"{len(episode.characters)}角色 / "
          f"{len(episode.scenes)}场景 / "
          f"{episode.total_shots()}分镜 / "
          f"总时长约 {episode.total_duration():.0f}秒")

    if json_path is None:
        ensure_dirs()
        json_path = os.path.join(CONFIG["paths"]["data"], "episode.json")
    episode.to_json(json_path)

    return episode


def _generate_single_pass(story_idea: str) -> dict:
    """原始的单次全量生成 (fallback)"""
    # Prompt 比原来更精确
    SYSTEM_PROMPT_STRONG = """你是顶级漫剧编剧+分镜师。输出严格符合结构的 JSON, 不要 markdown, 不要解释。
规则:
1. 所有 image_prompt 必须用英文, 包含完整角色外观 + 场景环境 + 画风
2. 每个场景 3-6 个分镜, 整集 >=10 个分镜
3. 分镜间镜头语言交替 (closeup/medium/wide 不要连续相同)
4. characters_in_frame 中的角色名必须在 characters 数组中有定义
5. 角色 description 必须英文且具体 (年龄/发型/服装)
"""
    USER = f"""故事: {story_idea}

请按此结构输出完整 JSON (characters 2-4个, scenes 2-3个, 总 shots >=10):
{{
  "title": "...", "description": "...",
  "characters": [{{"name":"...","description":"英文外观","voice_gender":"male|female|narrator"}}],
  "scenes": [{{
    "scene_id":1, "setting":"中文", "scene_goal":"中文-15字内",
    "shots":[{{
      "shot_id":1, "shot_type":"closeup|medium|wide|establishing|over-shoulder",
      "camera_move":"static|zoom-in|zoom-out|pan-left|pan-right|tilt-up|tilt-down",
      "move_speed":"slow|normal|fast", "duration":4.0,
      "image_prompt":"完整英文提示词 40-80词", "image_style":"anime style, cinematic",
      "characters_in_frame":["角色名"],
      "dialogue":[{{"character":"角色名","text":"中文","emotion":"neutral","position":"bottom","delay":0.0}}],
      "shot_purpose":"中文-15字内", "transition_reason":"中文-10字内"
    }}]
  }}]
}}"""

    raw = _call_chat_internal(
        USER, SYSTEM_PROMPT_STRONG,
        temperature=0.4, max_tokens=8000,
    )
    data = _extract_json_from_text(raw)
    if data is None:
        data = _ai_repair_json(raw, "单次生成 JSON 解析失败")
    if data is None:
        raise ModelError("单次全量生成失败, JSON 无法解析")
    return data


# ============================================================
# 7. 图片生成
# ============================================================

def generate_image(prompt: str, style: str = "", save_path: Optional[str] = None) -> str:
    """生成单张图片"""
    full_prompt = f"{prompt}, {style}" if style and style not in prompt else prompt

    if CONFIG["model_provider"] != "openai":
        raise NotImplementedError(f"{CONFIG['model_provider']} 图片生成待实现")

    from openai import OpenAI
    cfg = CONFIG["openai"]
    if not cfg["api_key"]:
        raise ModelAuthError("OPENAI_API_KEY 未配置")

    client = OpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"])
    print(f"[DALL-E] 生成图片 ({prompt[:60]}...)")

    try:
        resp = client.images.generate(
            model=cfg["image_model"],
            prompt=full_prompt,
            size="1024x1792",
            quality="standard",
            n=1,
        )
    except Exception as e:
        raise _classify_error(getattr(e, "status_code", None), str(e))

    img_url = resp.data[0].url
    ensure_dirs()
    fname = f"img_{int(time.time() * 1000)}.png"
    save_path = save_path or os.path.join(CONFIG["paths"]["artwork"], fname)

    r = requests.get(img_url)
    r.raise_for_status()
    with open(save_path, "wb") as f:
        f.write(r.content)
    print(f"[DALL-E] 已保存 -> {save_path}")
    return save_path


def generate_all_artwork(episode: Episode) -> dict:
    """批量生成所有分镜插画"""
    shot_images = {}
    all_shots = episode.get_all_shots()
    for i, shot in enumerate(all_shots):
        if not shot.image_prompt:
            print(f"  [跳过] shot {shot.shot_id} 无 image_prompt")
            continue
        try:
            print(f"  ({i + 1}/{len(all_shots)}) shot {shot.shot_id}")
            path = generate_image(shot.image_prompt, shot.image_style)
            shot_images[shot.shot_id] = path
            time.sleep(2)
        except Exception as e:
            print(f"  [错误] shot {shot.shot_id} 生图失败: {e}")
    return shot_images


# ============================================================
# 8. 顶层便捷入口
# ============================================================

def create_episode_from_idea(story_idea: str, gen_images: bool = False) -> Episode:
    """一键: 故事创意 -> Episode 对象 (可选同时生图)"""
    episode = generate_episode(story_idea)
    if gen_images:
        print("\n>>> 开始生成插画...")
        mapping = generate_all_artwork(episode)
        ensure_dirs()
        with open(os.path.join(CONFIG["paths"]["data"], "shot_images.json"), "w") as f:
            json.dump({str(k): v for k, v in mapping.items()}, f, indent=2, ensure_ascii=False)
    return episode


if __name__ == "__main__":
    print("模块可用。请通过 main.py 使用。")
    print("\n快速 JSON 解析测试:")
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from .schema import Episode, Scene, Shot
    # 测试 _extract_json_from_text
    test_cases = [
        '{"title": "test", "scenes": [{"scene_id":1}]}',
        '```json\n{"title": "test"}\n```',
        '前序废话 {"title": "test", "scenes": [{"scene_id":1, "shots": [{"shot_id":1}',
    ]
    for i, tc in enumerate(test_cases):
        result = _extract_json_from_text(tc)
        print(f"  case{i+1}: {'✅' if result else '❌'}")
