# 🎬 漫剧 AI 制作工具 (Comic Drama AI Studio)

> 用多模态模型（GPT-4o / Gemini / 通义）一键生成**动态漫剧** — 从故事创意到带运镜 + AI 配音的竖屏短视频。

---

## ✨ 功能概览

```
故事创意 (一句话)
      ↓
【多模态模型】生成完整剧本 + 分镜 + 英文出图提示词
      ↓
【DALL-E 3】批量生成分镜插画 (9:16 竖屏)
      ↓
【PIL 自动排版】叠加中文对白气泡 (圆角 + 尾巴 + 自动换行)
      ↓
【moviepy 运镜】镜头推拉摇移 (zoom / pan / tilt / static)
      ↓
【Edge-TTS 配音】免费自然的中文语音 (按角色性别自动匹配)
      ↓
【最终合成】导出 1080×1920 30fps MP4
```

---

## 📁 项目结构

```
漫剧/
├── main.py                  # CLI 主入口 (一键流水线)
├── requirements.txt         # Python 依赖
├── .env.example             # 环境变量模板 (复制为 .env)
├── .gitignore
├── README.md
├── tools/
│   ├── __init__.py
│   ├── config.py            # .env 配置加载
│   ├── schema.py            # Episode/Shot/Dialogue 数据模型
│   ├── mm_generator.py      # 多模态模型 (剧本 + 图片生成)
│   ├── dialogue.py          # 对白气泡自动排版 (PIL)
│   ├── tts.py               # Edge-TTS 免费配音
│   └── composer.py          # moviepy 运镜 + 视频合成
├── artwork/                 # AI 生成的原图
├── frames/                  # 加对白后的分镜图
├── audio/                   # TTS 配音文件
├── output/                  # 最终 MP4
└── data/                    # 剧本 JSON + 映射表
```

---

## 🚀 快速开始

### 1. 安装依赖

```bash
cd 漫剧
pip install -r requirements.txt
```

### 2. 配置 API Key

```bash
cp .env.example .env
# 编辑 .env，填入你的 OPENAI_API_KEY (必填，其他可选)
# 主模型推荐: openai (GPT-4o + DALL-E 3)
```

### 3. 一键跑 Demo

```bash
python main.py demo
```

等待约 2-5 分钟，你会在 `output/` 目录得到一部完整的动态漫剧 MP4 🎉

### 4. 用自己的故事

```bash
python main.py make "一个高中生捡到玉佩穿越到古代庭院..."
```

---

## 🛠️ 命令详解

| 命令 | 说明 |
|------|------|
| `python main.py demo` | 用内置故事跑完整流水线 |
| `python main.py make "创意"` | 根据创意生成完整漫剧 |
| `python main.py script "创意"` | **仅**生成剧本 JSON (不调用 DALL-E 生图) |
| `python main.py image` | 从已有 `episode.json` 补生成缺失插画 |
| `python main.py tts` | 从已有剧本生成配音 |
| `python main.py compose` | 从已有 frames + audio 合成视频 |

**推荐使用顺序** (逐步调试)：
```bash
python main.py script "校园故事"     # 1. 先看看剧本满不满意
python main.py image                # 2. 满意再生图
python main.py tts                  # 3. 生成配音
python main.py compose              # 4. 合成视频
```

---

## 🛡️ 剧本生成稳定化机制

长剧本一次性生成极易被 `max_tokens` 截断，导致 JSON 不闭合、分镜不完整、角色漂移。我们针对性做了 **6 层稳定化**：

| 问题 | 解决方案 |
|------|----------|
| **长 JSON 被截断** | **两阶段生成**：阶段1 先生成角色+场景骨架 (token < 3k)，阶段2 按场景逐段生成分镜详情 (每次 5k token) |
| **JSON 不闭合** | **四层解析链**：直接 json.loads → markdown 代码块提取 → 手动补闭合括号 → AI 修复 (最多 2 次) |
| **结构性错误** | **validate() 结构化校验**：7 类检查 — 标题/角色数/场景数/分镜数/image_prompt/角色引用/duration |
| **角色外观漂移** | **inject_character_descriptions()**：自动把 characters_in_frame 中角色的英文 description **强制前缀** 注入每段 image_prompt |
| **模型报错混乱** | **细粒度错误分类**：`ModelAuthError`(401)/`ModelNotFoundError`(404)/`ModelQuotaError`(额度)/`ModelNetworkError` — 401/额度/NotFound 直接抛出让用户配置，网络错误指数退避重试 |
| **JSON 随机偏离** | temperature 从 0.8 降到 **0.4** — 结构化 JSON 不需要高随机性 |

### 连贯性锚点字段 (schema 层)

为了让跨场景连贯可**机器校验** + **人工审查**，每个 Scene/Shot 都加了强制锚点：

```json
{
  "scene_goal": "本场景叙事目标 (15字内)",
  "next_scene_hook": "引出下一场景的钩子",
  "shots": [{
    "shot_purpose": "本镜头目的 (15字内)",
    "transition_reason": "到下一镜头的衔接原因"
  }]
}
```

---

## 🧩 模块详解

### 1. `tools/schema.py` — 数据格式

所有多模态模型的输出必须符合此结构，保证下游 Python 脚本能直接消费。

核心数据类：
- `Episode` (顶层) → `Scene[]` → `Shot[]` → `Dialogue[]`
- `Character` 记录角色人设 (供 AI 出图时保持一致性)

```python
from tools.schema import Episode

ep = Episode.from_json("data/episode.json")
print(ep.title, ep.total_shots(), ep.total_duration())
ep.to_json("data/episode_v2.json")
```

### 2. `tools/mm_generator.py` — 多模态模型

支持 3 种后端，通过 `.env` 的 `MM_MODEL_PROVIDER` 切换：

| provider | 环境变量 | 说明 |
|----------|---------|------|
| `openai` | `OPENAI_API_KEY` | GPT-4o 剧本 + DALL-E 3 图片 (推荐) |
| `gemini` | `GEMINI_API_KEY` | Google Gemini (可选) |
| `qwen` | `DASHSCOPE_API_KEY` | 阿里通义千问 (可选，中文好) |

**关键**：`image_prompt` 必须是**英文** (供 DALL-E / SD 使用)。

### 3. `tools/dialogue.py` — 对白排版

- 自动扫描 macOS/Linux/Windows 中文字体 (PingFang / Heiti / Noto Sans CJK...)
- 自动按图宽自适应字号 + 英文自动换行算法同样适用中文
- 支持 `bottom / top / left / right` 四种气泡位置
- 多个气泡自动防重叠 (重叠时下移，底部越界则反向放到上方)

### 4. `tools/tts.py` — AI 配音

使用 **Edge-TTS** (微软 Edge 浏览器的免费 TTS，无需 API Key)：

| 角色性别 | 默认语音 (.env 可改) |
|----------|----------------------|
| male | `zh-CN-YunxiNeural` |
| female | `zh-CN-XiaoxiaoNeural` |
| narrator | `zh-CN-YunxiNeural` |

其他好听的中文语音推荐：
- `zh-CN-YunjianNeural` — 新闻男
- `zh-CN-XiaoyiNeural` — 甜美女
- `zh-TW-HsiaoChenNeural` — 台湾腔

### 5. `tools/composer.py` — 视频合成

**moviepy 2.x** 运镜类型：

| camera_move | 效果 |
|-------------|------|
| `static` | 静止画面 |
| `zoom-in` | 从中心逐步放大 |
| `zoom-out` | 从 1.1x 缩回原图 |
| `pan-left` / `pan-right` | 水平平移 |
| `tilt-up` / `tilt-down` | 垂直平移 |

每个分镜的 `move_speed` (`slow / normal / fast`) 影响运镜幅度。

---

## ⚙️ 环境变量 (.env)

```ini
# ---- 必填 ----
MM_MODEL_PROVIDER=openai
OPENAI_API_KEY=sk-xxxxxxxxxxxxxx

# ---- 可选 ----
OPENAI_CHAT_MODEL=gpt-4o        # 或 gpt-4o-mini (省钱)
OPENAI_IMAGE_MODEL=dall-e-3

# TTS
TTS_ENGINE=edge-tts
TTS_VOICE_MALE=zh-CN-YunxiNeural
TTS_VOICE_FEMALE=zh-CN-XiaoxiaoNeural

# 视频
VIDEO_WIDTH=1080
VIDEO_HEIGHT=1920
VIDEO_FPS=30
```

---

## ❓ 常见问题 FAQ

### Q1: 运行报错 "OPENAI_API_KEY is required"
检查 `.env` 文件是否存在，`MM_MODEL_PROVIDER` 是否正确设为 `openai`，以及 key 是否有前缀 `sk-`。

### Q2: DALL-E 3 生成的图角色不一致怎么办？
在多轮生图时，同一角色在 `characters[].description` 里的描述必须**逐字复用**（如 "a boy with black short hair, wearing white school uniform"）。可以用 `python main.py script` 先人工检查所有 `image_prompt`，确认一致后再生图。

### Q3: moviepy 运行报 ffmpeg 找不到
moviepy 2.x 会自动下载 `imageio-ffmpeg`，如果还是报错：
```bash
pip install imageio-ffmpeg
python -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())"
```

### Q4: Edge-TTS 报错 "No module named 'edge_tts'"
```bash
pip install edge-tts
# 测试:
python -c "import asyncio, edge_tts; asyncio.run(edge_tts.Communicate('测试','zh-CN-YunxiNeural').save('t.mp3'))"
```

### Q5: 想改成横屏 (16:9) 怎么办？
编辑生成的 `data/episode.json`：
1. `"aspect_ratio": "16:9"`
2. 在 `.env` 中改 `VIDEO_WIDTH=1920` `VIDEO_HEIGHT=1080`
3. 重新 `python main.py image` (因为 DALL-E 3 的 size 会随视频比例变)
4. 再 `python main.py compose`

### Q6: 生成的视频体积太大？
在 `composer.py` 的 `write_videofile` 里把 `preset="medium"` 改成 `preset="slow"` + 加 `bitrate="5000k"`。或用 HandBrake 二次压缩。

### Q7: 中文对白显示为方块？
说明找不到中文字体。系统自带字体扫描路径已覆盖 macOS (PingFang)、Windows (msyh)、Linux (Noto CJK / wqy)。如果都没有：
```bash
# macOS 检查:
ls /System/Library/Fonts/PingFang.ttc
# Linux 安装:
sudo apt install fonts-noto-cjk
```

---

## 📦 依赖清单

| 包 | 版本 | 用途 |
|----|------|------|
| openai | ≥1.0 | GPT-4o / DALL-E 3 |
| google-generativeai | ≥0.8 | Gemini (可选) |
| Pillow | ≥10.0 | 对白排版 / 图片处理 |
| moviepy | ≥2.0 | 视频合成 / 运镜 |
| imageio-ffmpeg | ≥0.4 | ffmpeg 后端 |
| edge-tts | ≥6.1 | 免费 AI 配音 |
| python-dotenv | ≥1.0 | .env 加载 |
| requests | ≥2.31 | 下载 DALL-E 图片 |

---

## 📝 路线图

- [ ] Stable Diffusion 本地出图支持 (避免 API 费用)
- [ ] 跨分镜转场效果 (crossfade / wipe)
- [ ] 字幕自动 timing (按 TTS 时长精确对齐)
- [ ] BGM 自动生成 / 叠加
- [ ] 批量生成长剧集 (第 2、3 ... 集保持角色一致性)
- [ ] Web UI (Gradio)

---

## 📄 License

MIT — 随便用，但别把你漫剧里的内容拿去当商用素材哦 😄
