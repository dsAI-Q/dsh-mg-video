# dsh-mg-video

用**一份 JSON 清单**渲染 MG 动画视频。全程本地：Pillow 逐帧画，ffmpeg 编码，音轨用 numpy 合成。不调用任何在线服务、不需要 API key、不做 AI 生成画面。

> 为什么不用 AI 视频模型：MG 的信息层是**文字和数字**。扩散模型会把数字画成鬼画符、把中文字形画歪。信息层必须用图元精确绘制——这是这个项目的前提假设，也是它唯一不能妥协的地方。

```
清单 JSON ──▶ 逐帧渲染（Pillow）──▶ 音轨合成（numpy）──▶ 编码（ffmpeg）──▶ mp4
```

## 能力一览

| 类别 | 内容 |
|---|---|
| 模板 9 个 | 标题卡 `title`、数字卡 `number`、对比卡 `compare`、流程卡 `flow`、时间轴卡 `timeline`、要点卡 `bullets`、引用卡 `quote`、截图卡 `shot`、落版 `end` |
| 背景层 5 个 | 光斑 `bokeh`、点阵波 `grid`、星流 `star`、旋转光束 `rays`、极光 `aurora` |
| 转场 5 个 | 甩镜 `whip`（带扫光带）、圆形擦除 `circle`、像素块 `mosaic`、上推 `slide`、墨迹 `ink` |
| 文字动效 | 词块弹入、渐变扫光、波浪、打字机、描边生长（乱码锁定只用于非数字） |
| 截图动图 | 两矩形间缓动弹（推近/平移）、聚光灯压暗、四角取景框、细边框、箭头、贴片标签、扫描光带、圆形放大镜、沿路径流动光点 |
| 音频 | 程序化和弦垫 + whoosh / tick / thud，可与原声混音（人声 −18.5 dB 标准） |
| 画布 | 1920×1080 / 1080×1920 / 任意，1.25 倍超采样后缩回 |

## 安装

需要三样东西：

```bash
# 1. Python 3.9+，装 Pillow 和 numpy
python -m pip install pillow numpy

# 2. ffmpeg 在 PATH 里（或设 MG_FFMPEG 指向可执行文件）
ffmpeg -version

# 3. 这个包
npm i -g @dsai-q/dsh-mg-video     # 或者直接 npx
```

自检：

```bash
mgvideo doctor      # Python / Pillow / numpy / ffmpeg 是否齐
mgvideo selftest    # 11 条回归断言（seg 归一化、缓存、漏边、音轨…）
```

`selftest` 不是摆设：每一条断言都对应一个真实修过的缺陷，改动后先跑它。

## 快速开始

```bash
mgvideo check examples/spec-demo.json          # 先看时间表，不渲染
mgvideo render examples/spec-demo.json -o demo.mp4 --keep-frames
```

清单长这样（节选）：

```json
{
  "size": [1920, 1080],
  "fps": 30,
  "background": "bokeh",
  "transition": "whip",
  "chrome": { "brand": "我的频道", "chapter": true },
  "audio": { "bgm": true },
  "scenes": [
    { "template": "title", "duration": 3.0, "background": "rays", "transition": "none",
      "title": "这不是剪出来的", "subtitle": "是排出来的", "caption": "一份清单，就是一部片。" },

    { "template": "number", "duration": 6.0, "label": "市值",
      "from": -40, "to": -38, "unit": "%",
      "ratio": 0.40, "ratio_to": 0.38,
      "bars": [{ "label": "原值", "top": "100" }, { "label": "现值", "top": "62" }],
      "note": "十块钱，只剩六块" }
  ]
}
```

改文案 = 改 JSON，不碰代码。渲染一版 1080p 大约 **0.25 秒/帧**（单核），30 秒的片子约 3–4 分钟。

## 9 个模板怎么填

| 模板 | 关键字段 | 说明 |
|---|---|---|
| `title` | `title` `subtitle` `kick` | 大字淡入上滑，副标后到 |
| `number` | `value` 或 `from`/`to`，`unit` `label` `note`，可选 `bars`+`ratio` | 有 `bars` 时大数字自动上移，避免和柱顶标签撞 |
| `compare` | `left`/`right` 各含 `title` `value` `note` `color`，`note` | 左右两卡 + 中间虚线 + 底部结论 |
| `flow` | `steps`（3–5 个字符串）`label` | 节点逐个亮起，连线生长 |
| `timeline` | `events[]` 每项 `at` `label` | 横轴推进，事件点上下交替弹出 |
| `bullets` | `title` `items[]`（字符串或 `{text,note}`） | 序号胶囊 + 逐条进场 |
| `quote` | `text` `by` | 逐字浮现 + 下划线生长 |
| `shot` | `frames_dir` `crop` `marks[]` | 截图卡，见下节 |
| `end` | `lines[]` `note` | 落版三行 |

每段还能加：`background`、`transition`、`transition_duration`、`chapter`（右上角章名）、`caption`（底部字幕）、`impact`（冲击效果）、`shake`。

## 截图卡：把录屏做成「会动的重点」

分析 ComfyUI 工作流这类画面密集的录屏时，全屏看不清节点名，必须截局部放大再指出来。

```bash
# ① 先把要用的那 20 秒切成逐帧
mgvideo frames 录屏.mp4 --start 302 --duration 20 --out frames/src

# ② 出坐标标定图（100 px 网格），一眼读出矩形，不用猜
mgvideo calib 录屏.mp4 312 --rect "470,285,515,168"

# ③ 写进清单
```

```json
{
  "template": "shot", "duration": 8.0,
  "frames_dir": "frames/src",
  "card_w": 1200, "card_h": 675,
  "crop": [[0, 0, 1920, 1080], [330, 265, 780, 439]],
  "move_until": 1.0,
  "marks": [
    { "type": "brackets", "rect": [409, 306, 580, 289], "from": 0.05, "to": 0.35,
      "color": "gold", "text": "① 高噪声" },
    { "type": "scan", "rect": [449, 419, 502, 37], "from": 0.30, "to": 0.55 },
    { "type": "arrow", "rect": [449, 629, 502, 36], "from": 0.55, "to": 0.75,
      "color": "cyan", "dx": 180, "dy": 120 }
  ],
  "panel": { "lines": [{ "text": "两个都要加载", "color": "gold", "size": 54 }] }
}
```

- `crop` 给两个矩形时，镜头会在它们之间缓动弹（推近 / 平移）
- `marks[].rect` 用**原片坐标**，会自动按当前裁剪框换算到卡片里
- `panel` 是右侧文字面板，和截图卡片各自一块底，永远不压字

## 音轨

`audio` 字段三种用法：

```json
{ "bgm": true }                                  // 只有程序化和弦垫 + 音效
{ "bgm": true, "voice": "我的录音.wav" }          // 人声 + 垫底（人声 −18.5 dB 标准）
{ "bgm": false }                                  // 静音
```

有 `voice` 时按 **人声主体 −18.5 dB、峰值 ≤ −3 dB、铺底 −29 dB** 混。
这套数字是实测出来的：用峰值归一会让不同素材之间响度差 2 dB 以上，听起来一会儿厚一会儿薄。

## 并行与提速

单核 0.25 秒/帧。两种并行：

```bash
# 多进程（受限环境自动退回单进程，不会中断）
mgvideo render spec.json -o out.mp4 --workers 8

# 分片：开 4 个终端各跑一份（只出帧不编码），最后统一编码
mgvideo render spec.json -o out.mp4 --shard 0/4 --keep-frames --frames-dir frames
mgvideo render spec.json -o out.mp4 --shard 1/4 --keep-frames --frames-dir frames
mgvideo render spec.json -o out.mp4 --shard 2/4 --keep-frames --frames-dir frames
mgvideo render spec.json -o out.mp4 --shard 3/4 --keep-frames --frames-dir frames
mgvideo encode spec.json --frames frames -o out.mp4
```

## 在 DSH 里当插件用

这个包同时是一个 DSH 插件 bundle（`package.json` 里声明了 `dsh.bundle`）。装上后 agent 直接有四个工具：

| 工具 | 作用 |
|---|---|
| `mg_render` | 清单 → mp4 |
| `mg_check` | 校验清单、打印时间表 |
| `mg_frames` | 从视频切逐帧 |
| `mg_calib` | 出坐标标定图 |

配套的 `SKILL.md` 是给 agent 看的工作流说明（怎么选题、怎么拆屏、怎么验收）。把它放进 skills 目录，agent 就知道什么时候该用这套东西、哪些坑不能踩。

## 设计取舍

**为什么是 Pillow 逐帧，不是 After Effects / Remotion / Fotor**

- 时间精度到 1/30 秒，所有动效都是显式函数，可复现、可 diff
- 改一个像素级细节只改一行，不用开图形界面、不用等导出
- 无 GPU 依赖，一台普通机器就能跑；渲染速度和画质完全成正比且可预测
- 代价是**没有现成模板市场**。所有版式都得自己写——这也是本仓库攒了 9 个模板 + 5 背景 + 5 转场的原因

**为什么模板化，而不是「一句话生成」**

模板不聪明，但它不犯蠢：数字不会错、字不会乱码、版式不会崩、改一个字段就重渲。教学类视频 80% 的画面就是「一句一卡」，模板化恰好对得上这种结构。

## 已知边界

- 中文排版依赖系统字体：Windows 用 Noto Sans SC / 微软雅黑，macOS 用苹方，Linux 需要装 Noto CJK。找不到字体会退化到默认字体，中文会缺字
- 极限分辨率靠裁剪放大：从 1080p 裁 800 px 宽放到 1200 px 会发虚，代码里补了一次 unsharp，但**素材分辨率仍是上限**
- 复杂的三维、粒子、流体效果不在这套范围内——那是 AI 视频模型或专门渲染器的活
- 一条片子的版式需要有人设计。模板解决「不要每次重画」，不解决「好不好看」

## 许可

MIT。模板、动效、音频合成的写法都随你改、随你用，商用也行。

---

更多：
- [SKILL.md](SKILL.md) —— 给 agent 的工作流（选模板、拆段、验收）
- [PITFALLS.md](PITFALLS.md) —— 20 条踩过的坑，每条都是花时间换来的
