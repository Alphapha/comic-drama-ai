"""
对白 / 气泡自动排版 (PIL)
===========================
功能:
- 在插画上自动添加对白框 + 气泡尾巴 + 中文文字
- 支持多角色分镜多对白、不同位置 (左/右/上/下)
- 输出: 带对白的 PNG 图片 -> frames/
"""

import os
from PIL import Image, ImageDraw, ImageFont
from typing import List, Tuple, Optional

from .schema import Episode, Shot, Dialogue
from .config import CONFIG, ensure_dirs


# ============================================================
# 字体加载
# ============================================================

def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """
    加载支持中文的字体
    自动扫描 macOS 常见中文字体路径
    """
    candidates = [
        # macOS
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
        "/System/Library/Fonts/Supplemental/Songti.ttc",
        # Linux
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        # Windows
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    print("[警告] 找不到中文字体，使用 PIL 默认 (可能中文显示为方块)")
    return ImageFont.load_default()


# ============================================================
# 对白框绘制核心
# ============================================================

def _wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> List[str]:
    """按字符宽度自动换行"""
    lines = []
    current = ""
    for ch in text:
        current += ch
        bbox = font.getbbox(current)
        if bbox[2] - bbox[0] > max_width:
            lines.append(current[:-1])
            current = ch
    if current:
        lines.append(current)
    return lines if lines else [text]


def _draw_bubble(
    draw: ImageDraw.ImageDraw,
    box: Tuple[int, int, int, int],       # (x1, y1, x2, y2)
    color_fill: Tuple[int, int, int, int] = (255, 255, 255, 240),
    color_outline: Tuple[int, int, int, int] = (30, 30, 30, 255),
    tail_direction: str = "bottom",        # top / bottom / left / right / none
    tail_size: int = 20,
    radius: int = 25,
):
    """
    绘制圆角矩形对白气泡 + 三角形尾巴

    Args:
        draw: PIL ImageDraw
        box: 气泡四角坐标 (x1, y1, x2, y2)
        tail_direction: 尾巴指向方向
    """
    x1, y1, x2, y2 = box
    # 主体圆角矩形
    draw.rounded_rectangle(box, radius=radius, fill=color_fill, outline=color_outline, width=3)

    # 尾巴
    if tail_direction == "none":
        return
    cx = (x1 + x2) // 2
    cy = (y1 + y2) // 2

    if tail_direction == "bottom":
        tail = [(cx - tail_size, y2), (cx + tail_size, y2), (cx, y2 + tail_size)]
    elif tail_direction == "top":
        tail = [(cx - tail_size, y1), (cx + tail_size, y1), (cx, y1 - tail_size)]
    elif tail_direction == "left":
        tail = [(x1, cy - tail_size), (x1, cy + tail_size), (x1 - tail_size, cy)]
    elif tail_direction == "right":
        tail = [(x2, cy - tail_size), (x2, cy + tail_size), (x2 + tail_size, cy)]
    else:
        return

    # 先描边再填充 (模拟边框)
    draw.polygon(tail, fill=color_fill, outline=color_outline)


def _place_bubble(
    img_w: int, img_h: int,
    position: str,
    text_w: int, text_h: int,
    margin: int = 30,
    padding: int = 20,
) -> Tuple[int, int, int, int, str]:
    """
    根据 position 决定气泡 box 坐标 + 尾巴方向

    Returns:
        (x1, y1, x2, y2, tail_direction)
    """
    bw = text_w + padding * 2
    bh = text_h + padding * 2

    # 默认放在底部中间偏上 (最不遮挡画面)
    if position == "bottom":
        x1 = (img_w - bw) // 2
        y1 = img_h - bh - margin - 40
        tail = "top"      # 尾巴朝上指向画面人物
    elif position == "top":
        x1 = (img_w - bw) // 2
        y1 = margin + 40
        tail = "bottom"
    elif position == "left":
        x1 = margin
        y1 = (img_h - bh) // 2
        tail = "right"
    elif position == "right":
        x1 = img_w - bw - margin
        y1 = (img_h - bh) // 2
        tail = "left"
    else:
        x1 = (img_w - bw) // 2
        y1 = img_h - bh - margin - 40
        tail = "top"

    return (x1, y1, x1 + bw, y1 + bh, tail)


def add_dialogue_to_image(
    image_path: str,
    dialogues: List[Dialogue],
    output_path: Optional[str] = None,
) -> str:
    """
    给一张图片叠加所有对白气泡

    Args:
        image_path: 原图路径
        dialogues: 对白列表 (同一张图上可能有多个)
        output_path: 保存路径

    Returns:
        带对白的图片本地路径
    """
    img = Image.open(image_path).convert("RGBA")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    img_w, img_h = img.size
    font_size = max(28, img_w // 25)       # 根据图宽自适应字号
    font = _load_font(font_size)
    max_text_width = int(img_w * 0.7)       # 气泡文字最大宽度

    # 按 delay 排序，先画的在下面 (视觉层级)
    dialogues_sorted = sorted(dialogues, key=lambda d: d.delay)

    # 追踪已占用的区域，避免重叠
    occupied = []  # list of (x1, y1, x2, y2)

    for dlg in dialogues_sorted:
        if not dlg.text.strip():
            continue

        # 1. 文字自动换行
        lines = _wrap_text(dlg.text, font, max_text_width)
        line_h = font_size + 8
        text_w = max((font.getbbox(l)[2] - font.getbbox(l)[0] for l in lines), default=100)
        text_h = len(lines) * line_h

        # 2. 放置气泡
        x1, y1, x2, y2, tail = _place_bubble(img_w, img_h, dlg.position, text_w, text_h)

        # 检查重叠，如果重叠则下移到已占用区域下方
        bubble_h = y2 - y1
        for ox1, oy1, ox2, oy2 in occupied:
            if not (x2 < ox1 or x1 > ox2 or y2 < oy1 or y1 > oy2):
                y1 = oy2 + 10
                y2 = y1 + bubble_h
                # 边界保护：如果底部越界则反向放到上方
                if y2 > img_h - margin:
                    y2 = oy1 - 10
                    y1 = y2 - bubble_h
                break

        # 3. 画气泡
        _draw_bubble(draw, (x1, y1, x2, y2), tail_direction=tail)
        occupied.append((x1, y1, x2, y2))

        # 4. 画文字
        cur_y = y1 + (y2 - y1 - text_h) // 2
        for line in lines:
            lx = x1 + (x2 - x1 - (font.getbbox(line)[2] - font.getbbox(line)[0])) // 2
            draw.text((lx, cur_y), line, font=font, fill=(20, 20, 20, 255))
            cur_y += line_h

    # 合成
    result = Image.alpha_composite(img, overlay).convert("RGB")

    ensure_dirs()
    if output_path is None:
        base = os.path.splitext(os.path.basename(image_path))[0]
        output_path = os.path.join(CONFIG["paths"]["frames"], f"{base}_with_dlg.png")

    result.save(output_path, "PNG")
    return output_path


def apply_dialogues_to_all(
    episode: Episode,
    artwork_mapping: dict,   # shot_id -> image_path
) -> dict:
    """
    为整集所有分镜添加对白

    Args:
        episode: Episode 对象
        artwork_mapping: 分镜到原图的映射

    Returns:
        dict: shot_id -> 带对白图片路径
    """
    ensure_dirs()
    result = {}
    for scene in episode.scenes:
        for shot in scene.shots:
            if shot.shot_id not in artwork_mapping:
                print(f"[对白] 分镜 {shot.shot_id} 无原图，跳过")
                continue
            img_path = artwork_mapping[shot.shot_id]
            out = add_dialogue_to_image(img_path, shot.dialogue)
            result[shot.shot_id] = out
            print(f"[对白] 分镜 {shot.shot_id} -> {out}")
    return result


if __name__ == "__main__":
    # 快速自测
    import io
    # 生成一张纯色图
    img = Image.new("RGB", (1024, 1792), (120, 180, 220))
    draw = ImageDraw.Draw(img)
    # 画一个"人物"示意
    draw.ellipse([400, 400, 624, 624], fill=(255, 220, 180))
    draw.rectangle([380, 624, 644, 1100], fill=(100, 140, 200))
    draw.rectangle([0, 1400, 1024, 1792], fill=(80, 130, 80))

    os.makedirs("_test_dlg", exist_ok=True)
    test_img = "_test_dlg/test_bg.png"
    img.save(test_img)

    dlg = [
        Dialogue(character="小明", text="今天天气真好呀！我们出去玩吧～", position="bottom"),
    ]
    out = add_dialogue_to_image(test_img, dlg, "_test_dlg/result.png")
    print(f"测试输出: {out}")
