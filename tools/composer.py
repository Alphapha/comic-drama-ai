"""
视频合成 (moviepy)
====================
把静态分镜图片 + 运镜 + 转场 + 配音 合成为最终动态漫剧视频。

核心运镜技巧 (moviepy 2.x):
- zoom-in  : 从原图中心点逐步放大到 1.15x
- zoom-out : 从 1.15x 逐步回到原图
- pan      : 固定视窗，图片在视窗内平移
- static   : 静止画面
"""

import os
from typing import Dict, List, Optional
import numpy as np
from PIL import Image

from .schema import Episode, Shot
from .config import CONFIG, ensure_dirs


# ============================================================
# 工具: PIL 图片 -> moviepy VideoClip
# ============================================================

def _pil_to_numpy(path: str) -> np.ndarray:
    """读图片为 numpy array (RGB)"""
    img = Image.open(path).convert("RGB")
    return np.array(img)


def _pad_image_to_video_size(img_array: np.ndarray, target_w: int, target_h: int) -> np.ndarray:
    """
    把任意长宽的图裁剪/缩放到目标视频尺寸 (等比缩放 + 居中裁剪)
    避免画面变形
    """
    h, w = img_array.shape[:2]
    scale = max(target_w / w, target_h / h)
    new_w, new_h = int(w * scale), int(h * scale)

    # 先缩放
    pil = Image.fromarray(img_array).resize((new_w, new_h), Image.LANCZOS)
    arr = np.array(pil)

    # 居中裁剪到 target
    y0 = (new_h - target_h) // 2
    x0 = (new_w - target_w) // 2
    return arr[y0:y0 + target_h, x0:x0 + target_w]


# ============================================================
# 运镜函数
# ============================================================

def _make_clip_from_image(
    image_path: str,
    duration: float,
    camera_move: str = "static",
    move_speed: str = "normal",
    target_size: tuple = (1080, 1920),
) -> "VideoClip":
    """
    基于一张图片生成一个带运镜的 VideoClip

    Args:
        image_path: 图片路径
        duration: 镜头持续秒数
        camera_move: 运镜类型
        move_speed: slow/normal/fast (影响 zoom 幅度)
        target_size: (w, h) 视频目标尺寸
    """
    from moviepy import ImageClip, VideoClip

    tw, th = target_size
    base_arr = _pad_image_to_video_size(_pil_to_numpy(image_path), tw, th)

    # zoom 幅度
    zoom_amount = {"slow": 0.05, "normal": 0.10, "fast": 0.18}.get(move_speed, 0.10)

    if camera_move == "static" or not camera_move:
        return ImageClip(base_arr).with_duration(duration)

    def make_frame(t):
        """根据时间 t 返回当前帧 (numpy array)"""
        progress = t / duration  # 0 -> 1

        if camera_move == "zoom-in":
            # 从 1.0 放大到 1+zoom_amount
            scale = 1.0 + zoom_amount * progress
            return _crop_center_with_scale(base_arr, scale, tw, th)

        elif camera_move == "zoom-out":
            # 从 1+zoom_amount 缩小到 1.0 (对大图先放大再居中裁剪等效)
            scale = 1.0 + zoom_amount * (1 - progress)
            return _crop_center_with_scale(base_arr, scale, tw, th)

        elif camera_move in ("pan-left", "pan-right"):
            # 平移: 需要比 target 更宽的底图
            wide_arr = _make_pan_base(base_arr, tw, th, direction="horizontal")
            offset_total = wide_arr.shape[1] - tw
            direction = 1 if camera_move == "pan-right" else -1
            offset = int(offset_total * progress * direction * 0.8)
            # clamp
            offset = max(-offset_total, min(0, offset))
            x0 = wide_arr.shape[1] // 2 - tw // 2 + offset
            x0 = max(0, min(x0, wide_arr.shape[1] - tw))
            return wide_arr[:, x0:x0 + tw]

        elif camera_move in ("tilt-up", "tilt-down"):
            tall_arr = _make_pan_base(base_arr, tw, th, direction="vertical")
            offset_total = tall_arr.shape[0] - th
            direction = 1 if camera_move == "tilt-down" else -1
            offset = int(offset_total * progress * direction * 0.8)
            offset = max(-offset_total, min(0, offset))
            y0 = tall_arr.shape[0] // 2 - th // 2 + offset
            y0 = max(0, min(y0, tall_arr.shape[0] - th))
            return tall_arr[y0:y0 + th, :]

        else:
            return base_arr

    clip = VideoClip(make_frame, duration=duration)
    return clip


def _crop_center_with_scale(base: np.ndarray, scale: float, tw: int, th: int) -> np.ndarray:
    """以中心点为基准，按 scale 缩放后再裁剪到 (tw, th)"""
    h, w = base.shape[:2]
    new_w, new_h = int(w * scale), int(h * scale)
    pil = Image.fromarray(base).resize((new_w, new_h), Image.LANCZOS)
    arr = np.array(pil)
    y0 = (new_h - th) // 2
    x0 = (new_w - tw) // 2
    return arr[y0:y0 + th, x0:x0 + tw]


def _make_pan_base(base: np.ndarray, tw: int, th: int, direction: str) -> np.ndarray:
    """生成用于平移的更大底图"""
    from PIL import Image
    h, w = base.shape[:2]
    if direction == "horizontal":
        new_w = int(w * 1.5)
        return np.array(Image.fromarray(base).resize((new_w, h), Image.LANCZOS))
    else:
        new_h = int(h * 1.5)
        return np.array(Image.fromarray(base).resize((w, new_h), Image.LANCZOS))


# ============================================================
# 单个分镜 -> 带音频的 VideoClip
# ============================================================

def build_shot_clip(
    shot: Shot,
    frame_image_path: str,
    audio_paths: Dict[int, str],   # dlg_index -> mp3 path
    target_size: tuple,
) -> "VideoClip":
    """
    构建单个分镜的完整 clip (画面运镜 + 对白叠加)
    """
    from moviepy import AudioFileClip, CompositeAudioClip

    # 1. 画面
    clip = _make_clip_from_image(
        frame_image_path, shot.duration,
        shot.camera_move, shot.move_speed, target_size,
    )

    # 2. 叠加对白音轨 (按 delay 偏移)
    audio_clips = []
    for idx, mp3 in audio_paths.items():
        try:
            ac = AudioFileClip(mp3)
            dlg = shot.dialogue[idx]
            ac = ac.with_start(dlg.delay)
            audio_clips.append(ac)
        except Exception as e:
            print(f"[警告] 音频加载失败 {mp3}: {e}")

    if audio_clips:
        combined = CompositeAudioClip(audio_clips)
        clip = clip.with_audio(combined)

    return clip


# ============================================================
# 整集合成主函数
# ============================================================

def compose_episode(
    episode: Episode,
    frame_images: Dict[int, str],       # shot_id -> 带对白图片路径
    audio_mapping: Dict[int, Dict[int, str]],  # shot_id -> {dlg_idx: mp3}
    output_path: Optional[str] = None,
) -> str:
    """
    把所有分镜 + 运镜 + 转场 + 对白 合成最终视频

    Args:
        episode: Episode 对象
        frame_images: shot_id -> 带对白图片路径
        audio_mapping: shot_id -> {dlg_idx: mp3}
        output_path: 输出 mp4 路径

    Returns:
        输出文件路径
    """
    from moviepy import concatenate_videoclips

    ensure_dirs()
    vcfg = CONFIG["video"]
    target_size = (vcfg["width"], vcfg["height"])

    if output_path is None:
        safe_title = episode.title.replace(" ", "_").replace("/", "-")
        output_path = os.path.join(CONFIG["paths"]["output"], f"{safe_title}_ep{episode.episode_number}.mp4")

    all_clips: List["VideoClip"] = []

    shots = episode.get_all_shots()
    total = len(shots)
    for i, shot in enumerate(shots):
        print(f"[合成] ({i+1}/{total}) shot {shot.shot_id} "
              f"{shot.camera_move} duration={shot.duration}s ...")
        img = frame_images.get(shot.shot_id)
        if img is None or not os.path.exists(img):
            print(f"  [跳过] 无图片")
            continue

        audios = audio_mapping.get(shot.shot_id, {})
        clip = build_shot_clip(shot, img, audios, target_size)
        all_clips.append(clip)

    if not all_clips:
        raise RuntimeError("没有可合成的分镜！请先确保 artwork + frames 都已生成")

    # 拼接 (暂不做跨 clip 转场以简化)
    final = concatenate_videoclips(all_clips, method="compose")

    # 导出
    print(f"\n>>> 开始导出视频 -> {output_path}")
    print(f"    总时长: {final.duration:.1f}s, 分辨率: {target_size}, FPS: {vcfg['fps']}")

    final.write_videofile(
        output_path,
        fps=vcfg["fps"],
        codec="libx264",
        audio_codec="aac",
        preset="medium",
        threads=4,
    )

    # 释放资源
    final.close()
    for c in all_clips:
        c.close()

    print(f"\n[完成] 视频已生成 -> {output_path}")
    return output_path


if __name__ == "__main__":
    print("模块可用。请通过 main.py 使用。")
