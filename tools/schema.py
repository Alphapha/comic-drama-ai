"""
漫剧数据格式定义 (Storyboard Schema)
=====================================
所有多模态模型生成的剧本+分镜必须符合此 JSON Schema，
以便下游 Python 脚本直接消费。
"""

# 漫剧顶层 JSON 结构示例
STORYBOARD_SCHEMA = {
    "$schema": "漫剧.episode.schema.json",
    "title": "string — 剧集标题",
    "description": "string — 一句话简介",
    "episode_number": "int — 第几集",
    "aspect_ratio": "string — 画面比例, 如 '9:16' (竖屏) / '16:9' (横屏)",
    "characters": [
        {
            "name": "string — 角色名",
            "description": "string — 角色外观/人设描述 (供 AI 出图)",
            "voice_gender": "male / female / narrator"
        }
    ],
    "scenes": [
        {
            "scene_id": "int — 场景序号",
            "setting": "string — 场景环境描述 (时间/地点/氛围)",
            "duration": "float — 该场景总时长(秒), 所有 shots duration 之和",
            "shots": [
                {
                    "shot_id": "int — 分镜序号",
                    "shot_type": "closeup / medium / wide / establishing / over-shoulder",
                    "camera_move": "static / zoom-in / zoom-out / pan-left / pan-right / tilt-up / tilt-down",
                    "move_speed": "slow / normal / fast",
                    "duration": "float — 本镜头时长(秒)",
                    "image_prompt": "string — 生成该镜头插画的完整英文提示词 (供 DALL-E / SD 使用)",
                    "image_style": "string — 画风说明 (如 'anime style, watercolor, cinematic')",
                    "characters_in_frame": ["角色名数组"],
                    "dialogue": [
                        {
                            "character": "string — 说话角色名 (Narrator 为旁白)",
                            "text": "string — 对白内容",
                            "emotion": "neutral / happy / sad / angry / surprised / whisper / shout",
                            "position": "left / right / top / bottom — 对白框位置",
                            "delay": "float — 从镜头开始多少秒后出现(秒)"
                        }
                    ],
                    "sound_effect": "string — 音效描述或文件名 (可选)",
                    "transition": "none / fade / dissolve / wipe — 到下一个镜头的转场"
                }
            ]
        }
    ]
}

# --- Python 数据类定义，供程序内使用 ---

from dataclasses import dataclass, field, asdict
from typing import List, Optional
import json


@dataclass
class Character:
    """角色信息"""
    name: str
    description: str
    voice_gender: str = "male"  # male / female / narrator


@dataclass
class Dialogue:
    """单条对白"""
    character: str
    text: str
    emotion: str = "neutral"
    position: str = "bottom"
    delay: float = 0.0


@dataclass
class Shot:
    """单个分镜/镜头"""
    shot_id: int
    shot_type: str = "medium"           # closeup / medium / wide / establishing / over-shoulder
    camera_move: str = "static"         # static / zoom-in / zoom-out / pan-left / pan-right / ...
    move_speed: str = "normal"          # slow / normal / fast
    duration: float = 4.0
    image_prompt: str = ""              # AI 出图提示词
    image_style: str = ""               # 画风
    characters_in_frame: List[str] = field(default_factory=list)
    dialogue: List[Dialogue] = field(default_factory=list)
    sound_effect: str = ""
    transition: str = "none"
    # 连贯性锚点字段 (让模型显式说明分镜意图，便于校验和人工审查)
    shot_purpose: str = ""              # 本镜头叙事目的 (如"建立场景氛围")
    transition_reason: str = ""         # 到下一镜头的衔接原因


@dataclass
class Scene:
    """场景 (多个分镜构成)"""
    scene_id: int
    setting: str
    duration: float = 0.0
    shots: List[Shot] = field(default_factory=list)
    # 连贯性锚点
    scene_goal: str = ""                # 本场景叙事目标
    next_scene_hook: str = ""           # 引出下一场景的钩子


@dataclass
class Episode:
    """整部漫剧 (顶层结构)"""
    title: str
    description: str = ""
    episode_number: int = 1
    aspect_ratio: str = "9:16"
    characters: List[Character] = field(default_factory=list)
    scenes: List[Scene] = field(default_factory=list)

    def to_dict(self) -> dict:
        """序列化为可导出 JSON 的字典"""
        data = asdict(self)
        return data

    def to_json(self, filepath: str):
        """导出为 JSON 文件"""
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
        print(f"[数据] 剧本已保存 -> {filepath}")

    @classmethod
    def from_dict(cls, data: dict) -> "Episode":
        """从字典反序列化"""
        ep = cls(
            title=data["title"],
            description=data.get("description", ""),
            episode_number=data.get("episode_number", 1),
            aspect_ratio=data.get("aspect_ratio", "9:16"),
        )
        for c in data.get("characters", []):
            ep.characters.append(Character(**c))
        for s in data.get("scenes", []):
            scene = Scene(
                scene_id=s["scene_id"], setting=s.get("setting", ""),
                scene_goal=s.get("scene_goal", ""),
                next_scene_hook=s.get("next_scene_hook", ""),
            )
            for sh in s.get("shots", []):
                shot = Shot(
                    shot_id=sh["shot_id"],
                    shot_type=sh.get("shot_type", "medium"),
                    camera_move=sh.get("camera_move", "static"),
                    move_speed=sh.get("move_speed", "normal"),
                    duration=sh.get("duration", 4.0),
                    image_prompt=sh.get("image_prompt", ""),
                    image_style=sh.get("image_style", ""),
                    characters_in_frame=sh.get("characters_in_frame", []),
                    sound_effect=sh.get("sound_effect", ""),
                    transition=sh.get("transition", "none"),
                    shot_purpose=sh.get("shot_purpose", ""),
                    transition_reason=sh.get("transition_reason", ""),
                )
                for d in sh.get("dialogue", []):
                    shot.dialogue.append(Dialogue(**d))
                scene.shots.append(shot)
            scene.duration = sum(sh.duration for sh in scene.shots)
            ep.scenes.append(scene)
        return ep

    @classmethod
    def from_json(cls, filepath: str) -> "Episode":
        """从 JSON 文件加载"""
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        print(f"[数据] 剧本已加载 <- {filepath}")
        return cls.from_dict(data)

    def total_duration(self) -> float:
        """全片总时长"""
        return sum(s.duration for s in self.scenes)

    def total_shots(self) -> int:
        """总分镜数"""
        return sum(len(s.shots) for s in self.scenes)

    def get_all_shots(self) -> List[Shot]:
        """扁平化获取所有分镜"""
        shots = []
        for scene in self.scenes:
            shots.extend(scene.shots)
        return shots

    # ============================================================
    # 结构化校验 (经验 2165105 / 1321475: 完成判定必须以"结构可解析 + 关键字段齐全"为准)
    # ============================================================

    def validate(self, min_shots: int = 6) -> List[str]:
        """
        校验剧本结构是否满足下游消费要求

        Args:
            min_shots: 最低分镜数阈值

        Returns:
            错误信息列表 (空列表=校验通过)
        """
        errors = []

        # 1. 顶层必填
        if not self.title or not self.title.strip():
            errors.append("title 不能为空")

        # 2. 角色数量
        if len(self.characters) == 0:
            errors.append("至少需要 1 个角色")

        # 3. 场景/分镜
        total = self.total_shots()
        if len(self.scenes) == 0:
            errors.append("至少需要 1 个场景")
        if total < min_shots:
            errors.append(f"分镜数 {total} < 最低要求 {min_shots}，可能被截断或生成不完整")

        # 4. image_prompt 完整性
        empty_prompt_shots = []
        char_prompt_mismatch = []
        char_names = {c.name for c in self.characters}

        for scene in self.scenes:
            for shot in scene.shots:
                if not shot.image_prompt.strip():
                    empty_prompt_shots.append(shot.shot_id)
                # 5. characters_in_frame 必须引用已定义角色
                for cn in shot.characters_in_frame:
                    if cn not in char_names and cn != "Narrator":
                        char_prompt_mismatch.append(f"shot{shot.shot_id}: characters_in_frame={cn} 未在角色表定义")

        if empty_prompt_shots:
            errors.append(f"以下分镜缺少 image_prompt: {empty_prompt_shots}")
        if char_prompt_mismatch:
            errors.extend(char_prompt_mismatch)

        # 6. duration 合理性
        for scene in self.scenes:
            for shot in scene.shots:
                if shot.duration <= 0 or shot.duration > 30:
                    errors.append(f"scene{scene.scene_id}/shot{shot.shot_id}: duration={shot.duration}s 异常")

        # 7. 对白角色必须存在
        for scene in self.scenes:
            for shot in scene.shots:
                for d in shot.dialogue:
                    if d.character not in char_names and d.character != "Narrator":
                        errors.append(f"shot{shot.shot_id}: 对白角色 '{d.character}' 未在角色表定义")

        return errors

    def is_valid(self, min_shots: int = 6) -> bool:
        """快速判断是否校验通过"""
        return len(self.validate(min_shots)) == 0

    # ============================================================
    # 角色描述强注入 (经验 2350700: 人设一致性必须在最终 prompt 中硬锚点)
    # ============================================================

    def inject_character_descriptions(self, force_replace: bool = False) -> int:
        """
        把 characters_in_frame 中角色的 description **强制前缀** 注入到每段 image_prompt 开头。
        解决 AI 生成时角色外观漂移的问题。

        Args:
            force_replace: True=覆盖已有前缀；False=仅当 prompt 开头不包含角色关键词时注入

        Returns:
            被修改的分镜数量
        """
        char_desc_map = {c.name: c.description for c in self.characters}
        modified = 0

        for scene in self.scenes:
            for shot in scene.shots:
                if not shot.characters_in_frame:
                    continue

                # 收集本镜头需要的角色描述
                needed_parts = []
                for cn in shot.characters_in_frame:
                    desc = char_desc_map.get(cn, "")
                    if desc:
                        needed_parts.append(f"[{cn}: {desc}]")

                if not needed_parts:
                    continue

                prefix = "CHARACTERS: " + "; ".join(needed_parts) + " | "
                original = shot.image_prompt

                if force_replace or not original.startswith("CHARACTERS:"):
                    shot.image_prompt = prefix + original
                    modified += 1

        print(f"[注入] 角色描述已注入 {modified} 个分镜的 image_prompt")
        return modified


if __name__ == "__main__":
    # 快速自测: 创建一个最小 Episode
    ep = Episode(
        title="测试漫剧",
        description="数据格式自测",
        episode_number=1,
    )
    ep.characters.append(Character(name="小明", description="黑发少年", voice_gender="male"))
    ep.scenes.append(Scene(scene_id=1, setting="放学后的教室"))
    ep.scenes[0].shots.append(Shot(
        shot_id=1, duration=3.0,
        image_prompt="a boy sitting in classroom after school, anime style",
    ))
    ep.scenes[0].shots[0].dialogue.append(Dialogue(character="小明", text="今天好无聊啊"))

    import os
    os.makedirs("data", exist_ok=True)
    ep.to_json("data/_schema_test.json")
    loaded = Episode.from_json("data/_schema_test.json")
    print(f"总时长: {loaded.total_duration()}s, 分镜数: {loaded.total_shots()}")
    os.remove("data/_schema_test.json")
