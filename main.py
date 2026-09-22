#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
漫剧 AI 制作 - 主入口
======================
一站式 CLI 工具:
    python main.py new          # 生成 Demo 剧本 + 一键制作完整漫剧
    python main.py make IDEA    # 根据创意剧本 + 制作完整漫剧
    python main.py script IDEA  # 仅生成剧本 (JSON)
    python main.py image        # 仅补生成缺失插画
    python main.py tts          # 仅生成配音
    python main.py compose      # 仅合成视频 (从已有 frames + audio)
    python main.py demo         # 用内置 Demo 故事端到端跑通
"""

import os
import sys
import json
import argparse
import traceback

# 确保 tools 包可被导入
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools.config import CONFIG, ensure_dirs, print_config
from tools.schema import Episode
from tools.mm_generator import create_episode_from_idea, generate_episode, generate_image
from tools.dialogue import apply_dialogues_to_all
from tools.tts import synthesize_episode_dialogues
from tools.composer import compose_episode


# ============================================================
# 内置 Demo 故事 (当用户不想想创意时使用)
# ============================================================

DEMO_STORY = """\
一个普通的高中少年"林小宇"，在放学路上意外捡到了一枚发光的古老玉佩。
玉佩突然发出光芒，他被传送到了一个神秘的古代庭院，遇到了一位穿着古装、自称"苏清鸢"的神秘少女。
少女告诉他，小宇是被选中的"时空旅人"，需要帮助她找回破碎的灵力碎片。

第一集内容:
- 开场: 黄昏放学，小宇走在空无一人的小巷，踢到玉佩
- 玉佩发光，画面眩晕转场
- 小宇出现在古风庭院，惊呼不敢相信
- 苏清鸢从廊中走出，自我介绍
- 两人对视，定格/黑屏 结束

画风: 日系动漫 + 电影感光影，9:16竖屏
"""


# ============================================================
# 各阶段实现
# ============================================================

def step_generate_script(story_idea: str, save_path: str = None, two_phase: bool = True) -> Episode:
    """阶段1: 生成剧本 (JSON)"""
    print("\n" + "=" * 60)
    print("【阶段 1】生成漫剧剧本 + 分镜")
    print("=" * 60)
    return generate_episode(story_idea, json_path=save_path, two_phase=two_phase)


def step_generate_images(episode: Episode) -> dict:
    """阶段2: 为每个分镜生成插画"""
    print("\n" + "=" * 60)
    print("【阶段 2】AI 生成分镜插画 (DALL-E 3)")
    print("=" * 60)

    artwork = {}
    for scene in episode.scenes:
        for shot in scene.shots:
            if not shot.image_prompt:
                print(f"  [跳过] shot {shot.shot_id} 无 image_prompt")
                continue
            try:
                path = generate_image(shot.image_prompt, shot.image_style)
                artwork[shot.shot_id] = path
            except Exception as e:
                print(f"  [错误] shot {shot.shot_id} 生图失败: {e}")
                traceback.print_exc()

    # 保存映射供后续步骤使用
    ensure_dirs()
    map_path = os.path.join(CONFIG["paths"]["data"], "shot_images.json")
    with open(map_path, "w", encoding="utf-8") as f:
        json.dump({str(k): v for k, v in artwork.items()}, f, indent=2, ensure_ascii=False)
    print(f"[映射] 已保存 -> {map_path}")

    return artwork


def step_add_dialogues(episode: Episode, artwork: dict) -> dict:
    """阶段3: 给插画叠加对白气泡"""
    print("\n" + "=" * 60)
    print("【阶段 3】对白气泡自动排版 (PIL)")
    print("=" * 60)
    return apply_dialogues_to_all(episode, artwork)


def step_generate_tts(episode: Episode) -> dict:
    """阶段4: 生成 AI 配音"""
    print("\n" + "=" * 60)
    print("【阶段 4】AI 配音 (Edge-TTS)")
    print("=" * 60)
    return synthesize_episode_dialogues(episode)


def step_compose_video(episode: Episode, frame_images: dict, audio_mapping: dict) -> str:
    """阶段5: 合成最终视频"""
    print("\n" + "=" * 60)
    print("【阶段 5】动态漫剧视频合成 (moviepy)")
    print("=" * 60)
    return compose_episode(episode, frame_images, audio_mapping)


# ============================================================
# 一键全流程
# ============================================================

def run_full_pipeline(story_idea: str, two_phase: bool = True):
    """一键跑完整流程: 剧本 -> 插画 -> 对白 -> 配音 -> 视频"""
    ensure_dirs()
    print_config()

    # 1. 剧本
    episode = step_generate_script(story_idea, two_phase=two_phase)

    # 2. 插画
    artwork = step_generate_images(episode)
    if not artwork:
        print("\n[致命错误] 没有成功生成任何插画，无法继续。")
        print("请检查 OPENAI_API_KEY 或网络连接。")
        return None

    # 3. 对白气泡
    frame_images = step_add_dialogues(episode, artwork)

    # 4. TTS 配音
    try:
        audio_mapping = step_generate_tts(episode)
    except Exception as e:
        print(f"[警告] TTS 失败，将输出无对白视频: {e}")
        audio_mapping = {}

    # 5. 合成视频
    output_path = step_compose_video(episode, frame_images, audio_mapping)

    # 总结
    print("\n" + "=" * 60)
    print("🎉 漫剧制作完成！")
    print("=" * 60)
    print(f"  标题   : {episode.title}")
    print(f"  集数   : 第 {episode.episode_number} 集")
    print(f"  分镜数 : {episode.total_shots()}")
    print(f"  总时长 : {episode.total_duration():.0f} 秒")
    print(f"  输出   : {output_path}")
    print(f"  剧本   : {os.path.join(CONFIG['paths']['data'], 'episode.json')}")
    print("=" * 60)

    return output_path


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="漫剧 AI 制作工具 - 多模态模型一键生成动态漫剧",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  python main.py demo                  用内置故事跑完整流程
  python main.py make "一个刺客的故事"  根据创意生成完整漫剧
  python main.py script "校园日常"      仅生成剧本 JSON
  python main.py image                 补生成缺失插画
  python main.py tts                   生成配音
  python main.py compose               合成视频 (从已有数据)
        """,
    )
    sub = parser.add_subparsers(dest="cmd", help="子命令")

    # demo / make
    p_demo = sub.add_parser("demo", help="用内置 Demo 故事端到端跑通")

    p_make = sub.add_parser("make", help="根据创意生成完整漫剧")
    p_make.add_argument("idea", help="故事创意描述")
    mode_group = p_make.add_mutually_exclusive_group()
    mode_group.add_argument("--two-phase", action="store_true", default=True, help="两阶段生成 (默认, 更稳定)")
    mode_group.add_argument("--single-pass", action="store_true", help="单次全量生成 (快但可能被截断)")

    p_script = sub.add_parser("script", help="仅生成剧本 JSON")
    p_script.add_argument("idea", help="故事创意描述")
    s_mode = p_script.add_mutually_exclusive_group()
    s_mode.add_argument("--two-phase", action="store_true", default=True)
    s_mode.add_argument("--single-pass", action="store_true")

    p_new = sub.add_parser("new", help="等同 demo")

    # 部分步骤
    sub.add_parser("image", help="从已有 episode.json 生成插画")
    sub.add_parser("tts", help="从已有 episode.json 生成配音")
    sub.add_parser("compose", help="从已有数据合成视频")

    args = parser.parse_args()
    ensure_dirs()

    if args.cmd in ("demo", "new"):
        run_full_pipeline(DEMO_STORY, two_phase=True)

    elif args.cmd == "make":
        two_phase = not args.single_pass  # 默认 True
        run_full_pipeline(args.idea, two_phase=two_phase)

    elif args.cmd == "script":
        two_phase = not args.single_pass
        ep = step_generate_script(args.idea, two_phase=two_phase)
        print(f"\n✅ 剧本已生成: {ep.title} -> data/episode.json")

    elif args.cmd == "image":
        ep = Episode.from_json(os.path.join(CONFIG["paths"]["data"], "episode.json"))
        step_generate_images(ep)

    elif args.cmd == "tts":
        ep = Episode.from_json(os.path.join(CONFIG["paths"]["data"], "episode.json"))
        step_generate_tts(ep)

    elif args.cmd == "compose":
        ep = Episode.from_json(os.path.join(CONFIG["paths"]["data"], "episode.json"))
        # 加载 artwork 映射
        map_path = os.path.join(CONFIG["paths"]["data"], "shot_images.json")
        with open(map_path) as f:
            artwork = {int(k): v for k, v in json.load(f).items()}
        frames = step_add_dialogues(ep, artwork)
        audio = step_generate_tts(ep)  # 重新生成也可以接受
        step_compose_video(ep, frames, audio)

    else:
        parser.print_help()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n[中断] 用户取消")
    except Exception as e:
        print(f"\n[致命错误] {e}")
        traceback.print_exc()
        print("\n💡 请检查:")
        print("  1. 是否已复制 .env.example 为 .env 并填入 OPENAI_API_KEY")
        print("  2. 是否已安装依赖: pip install -r requirements.txt")
        print("  3. 网络是否畅通")
        sys.exit(1)
