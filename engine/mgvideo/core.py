# -*- coding: utf-8 -*-
"""
mgvideo.core —— MG 渲染的图元层

设计约束（都是踩过坑换来的）：
  1. 所有绘制在「超采样画布」CW x CH 上做，最后缩到 W x H。S=1.25 时 1080p 画布是 2400x1350，
     缩小时自带一点抗锯齿，字和细线都干净。
  2. 背景只画一次（BASE），每帧 copy；不要每帧重算渐变和网格。
  3. 不要用遮罩裁剪做文字入场（Pillow 合成链路里会把字横向切成条），一律「淡入 + 上移」。
  4. 动画进度用 seg(f, a, b)，a/b 是段内进度（0–1），归一化必须是 max(b-a, 1e-6)，
     写成 max(b-a, 1) 会把 1 秒以内的区间全部除以 1，动效整体慢 2–3 倍。
  5. 圆角卡片在花哨背景上要用深色玻璃底 + 描边，否则会被背景吃掉。
"""
import math
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

# ---------------------------------------------------------------- 画布配置

W, H = 1920, 1080
S = 1.25
SS = S            # 超采样倍率（画布 = 输出 × SS）；S 会按画布高度等比调整
CW, CH = int(W * S), int(H * S)

BG_TOP = (14, 10, 28)
BG_BOT = (24, 15, 44)
GOLD = (232, 185, 60)
GOLD_SOFT = (255, 214, 120)
GREEN = (53, 208, 127)
RED = (255, 77, 79)
WHITE = (242, 244, 250)
MUTED = (150, 158, 180)
CARD_FILL = (255, 255, 255, 9)
CARD_LINE = (255, 255, 255, 26)
CARD_DARK = (12, 10, 24, 138)
CARD_DARK_LINE = (255, 255, 255, 46)

# 字体：优先用可变字体（可切字重），退化到微软雅黑粗体 / 黑体
FONT_CANDIDATES = [
    (r"C:\Windows\Fonts\NotoSansSC-VF.ttf", True),
    (r"C:\Windows\Fonts\msyhbd.ttc", False),
    (r"C:\Windows\Fonts\msyh.ttc", False),
    (r"C:\Windows\Fonts\simhei.ttf", False),
    (r"/System/Library/Fonts/PingFang.ttc", False),
    (r"/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", False),
    (r"/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc", False),
]

_FONT_CACHE = {}
_BASE = None


def configure(width=1920, height=1080, supersample=1.25):
    """切换画布尺寸（16:9 / 9:16 / 1:1 都能用），并重建背景。

    注意：所有依赖画布尺寸的缓存都要在这里清掉。曾经漏清 _SQUARES，
    换尺寸后方块还在按旧画布坐标画，表现是「小方块全挤到一边」。

    S 是**设计单位缩放**，不是超采样倍率：
      画布 = 输出尺寸 × SS
      S    = SS × (输出高 / 1080)
    这样模板里所有 `× S` 的内边距、圆角、字号、间距在 1080p 上等于 SS（行为不变），
    换成 720p / 1440p 时按高度等比缩放。以前 S 只等于 SS，模板用
    `CH * 比例` 定位、用 `× S` 设间距，两种单位在非 1080p 下会互相打架
    —— 720p 上对比卡的数字就压到了下面那行小字上。
    """
    global W, H, S, SS, CW, CH, _BASE, _SQUARES
    W, H = int(width), int(height)
    SS = float(supersample)
    S = SS * (H / 1080.0)
    CW, CH = int(W * SS), int(H * SS)
    _FONT_CACHE.clear()
    _BASE = None
    _SQUARES = None
    return W, H, CW, CH


def font(px, weight="Bold"):
    """按像素取字体，带缓存。px 是在 CW 画布上的字号。"""
    key = (int(px), weight)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    for path, variable in FONT_CANDIDATES:
        if not os.path.exists(path):
            continue
        try:
            f = ImageFont.truetype(path, int(px))
        except Exception:
            continue
        if variable:
            for name in (weight, "Black", "SemiBold", "Regular"):
                try:
                    f.set_variation_by_name(name)
                    break
                except Exception:
                    continue
        _FONT_CACHE[key] = f
        return f
    f = ImageFont.load_default()
    _FONT_CACHE[key] = f
    return f


def fit_font(text, base_px, max_width, weight="Bold", floor=0.6):
    """文字太长就自动缩字号，避免溢出卡片"""
    px = int(base_px)
    while px > base_px * floor and font(px, weight).getlength(text) > max_width:
        px = int(px * 0.94)
    return font(px, weight)


# ---------------------------------------------------------------- 缓动
def clamp(x, a=0.0, b=1.0):
    return a if x < a else (b if x > b else x)


def eo(t):
    """ease-out cubic：进场用"""
    return 1 - (1 - clamp(t)) ** 3


def eio(t):
    """smoothstep：位移 / 转场用"""
    t = clamp(t)
    return t * t * (3 - 2 * t)


def spring(t, freq=9.0, damp=6.0):
    """带过冲的弹性：数字、柱子、贴片用"""
    t = clamp(t)
    if t <= 0:
        return 0.0
    return 1 - math.exp(-damp * t) * math.cos(freq * t)


def seg(f, a, b):
    """段内进度。归一化必须用 1e-6，不能是 1。"""
    return clamp((f - a) / max(b - a, 1e-6))


# ---------------------------------------------------------------- 背景
def make_bg():
    y = np.linspace(0, 1, CH)[:, None]
    arr = np.zeros((CH, CW, 3), np.float32)
    for i in range(3):
        arr[:, :, i] = (BG_TOP[i] * (1 - y) + BG_BOT[i] * y).reshape(CH, 1)
    yy, xx = np.mgrid[0:CH, 0:CW].astype(np.float32)
    cx, cy = CW * 0.5, CH * 0.44
    r = np.sqrt(((xx - cx) / (CW * 0.66)) ** 2 + ((yy - cy) / (CH * 0.80)) ** 2)
    glow = np.clip(1 - r, 0, 1) ** 2.0
    for i, c in enumerate((46, 26, 70)):
        arr[:, :, i] += glow * c
    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).convert("RGBA")

    grid = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grid)
    step = int(92 * S)
    for x in range(0, CW, step):
        gd.line([(x, 0), (x, CH)], fill=(150, 130, 220, 14), width=1)
    for y2 in range(0, CH, step):
        gd.line([(0, y2), (CW, y2)], fill=(150, 130, 220, 14), width=1)
    img = Image.alpha_composite(img, grid)

    vig = np.clip(1.2 - r, 0, 1) ** 0.6
    vig = (0.30 + 0.70 * vig)[:, :, None]
    a = np.asarray(img.convert("RGB"), np.float32) * vig
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).convert("RGBA")


def base():
    """静态背景（懒加载，只算一次）"""
    global _BASE
    if _BASE is None:
        _BASE = make_bg()
    return _BASE


_SQUARES = None


def squares(frame, n=42, seed=11):
    """缓慢漂移的彩色小方块：给静态背景一点空间感"""
    global _SQUARES
    if _SQUARES is None:
        rng = np.random.default_rng(seed)
        _SQUARES = dict(
            xs=rng.uniform(0, CW, n), ys=rng.uniform(0, CH, n),
            sz=rng.uniform(3, 9, n) * S, sp=rng.uniform(0.15, 0.55, n),
        )
    d0 = _SQUARES
    lay = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    cols = [GOLD, (120, 110, 220), GREEN, RED]
    t = frame / 30.0
    for i in range(n):
        x = (d0["xs"][i] + t * d0["sp"][i] * 26 * S) % CW
        y = (d0["ys"][i] + math.sin(t * d0["sp"][i] + i) * 18 * S) % CH
        s = d0["sz"][i]
        d.rectangle([x, y, x + s, y + s], fill=(*cols[i % len(cols)], 26))
    return lay


# ---------------------------------------------------------------- 图层工具
def layer(w=None, h=None):
    return Image.new("RGBA", (w or CW, h or CH), (0, 0, 0, 0))


def txt(text, f, xy, color, anchor="mm", spacing=0):
    lay = layer()
    ImageDraw.Draw(lay).text(xy, text, font=f, fill=color, anchor=anchor, spacing=spacing)
    return lay


def fade(lay, a):
    a = clamp(a)
    if a >= 0.999:
        return lay
    out = lay.copy()
    out.putalpha(out.split()[3].point(lambda v: int(v * a)))
    return out


def reveal_text(img, text, f, xy, color, p, anchor="mm", dy=22, spacing=0):
    """淡入 + 上滑。替代遮罩裁剪：遮罩会把中文横向切成条。"""
    p = clamp(p)
    if p <= 0.001:
        return img
    lay = txt(text, f, (xy[0], xy[1] + int((1 - eo(p)) * dy * S)), color, anchor=anchor, spacing=spacing)
    return Image.alpha_composite(img, fade(lay, eo(p)))


def card_box(img, box, radius=None, fill=CARD_FILL, line=CARD_LINE, glow=False):
    r = radius or int(26 * S)
    lay = layer()
    ImageDraw.Draw(lay).rounded_rectangle(box, radius=r, fill=fill, outline=line,
                                          width=max(1, int(1.6 * S)))
    if glow:
        outer = layer()
        ImageDraw.Draw(outer).rounded_rectangle([box[0] - 6, box[1] - 6, box[2] + 6, box[3] + 6],
                                                radius=r + 6, fill=(255, 255, 255, 24))
        lay = Image.alpha_composite(outer.filter(ImageFilter.GaussianBlur(16)), lay)
    return Image.alpha_composite(img, lay)


def pill(img, xy, text, f, fg, bg, pad=None, anchor="lm"):
    """胶囊标签"""
    pad = pad or int(20 * S)
    bb = f.getbbox(text)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    w, h = tw + pad * 2, th + pad * 1.1
    if anchor == "lm":
        x0, y0 = xy[0], xy[1] - h / 2
    elif anchor == "mm":
        x0, y0 = xy[0] - w / 2, xy[1] - h / 2
    else:
        x0, y0 = xy[0] - w, xy[1] - h / 2
    lay = layer()
    d = ImageDraw.Draw(lay)
    d.rounded_rectangle([x0, y0, x0 + w, y0 + h], radius=h / 2, fill=(*bg, 235))
    d.text((x0 + w / 2, y0 + h / 2), text, font=f, fill=fg, anchor="mm")
    return Image.alpha_composite(img, lay)


def round_mask(size, r):
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size[0] - 1, size[1] - 1], radius=r, fill=255)
    return m


def shadow_sprite(size, radius, pad=46, offset=10, alpha=170, blur=22):
    sh = Image.new("RGBA", (size[0] + pad * 2, size[1] + pad * 2), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle([pad, pad + offset, pad + size[0], pad + size[1] + offset],
                                         radius=radius, fill=(0, 0, 0, alpha))
    return sh.filter(ImageFilter.GaussianBlur(blur)), pad


# ---------------------------------------------------------------- 数据图元
def bar_pair(img, cx_left, cx_right, base_y, full_h, ratio, p_grow, p_change, w=None):
    """两根柱子：左原值（上半段红斜纹表示跌掉的部分）、右现值。
    返回 (img, ghost_box, now_box, h_full, h_now)，方便调用方在柱子上方标数字。
    """
    w = w or int(190 * S)
    h_full = full_h * clamp(p_grow)
    h_now = h_full * (1 - clamp(ratio))

    gx = cx_left - w // 2
    ghost = layer()
    gd = ImageDraw.Draw(ghost)
    gd.rounded_rectangle([gx, base_y - h_full, gx + w, base_y], radius=int(14 * S),
                         fill=(255, 255, 255, 16), outline=(255, 255, 255, 40),
                         width=max(1, int(1.5 * S)))
    cut_h = h_full - h_now
    if cut_h > 2:
        band = Image.new("RGBA", (w, int(cut_h)), (255, 77, 79, 86))
        bd = ImageDraw.Draw(band)
        for x in range(-int(cut_h), w + int(cut_h), int(16 * S)):
            bd.line([(x, cut_h), (x + cut_h, 0)], fill=(255, 120, 120, 175), width=max(1, int(1.7 * S)))
        m = Image.new("L", (w, int(cut_h)), 0)
        ImageDraw.Draw(m).rounded_rectangle([0, 0, w - 1, int(cut_h) - 1], radius=int(14 * S), fill=255)
        band.putalpha(Image.composite(band.split()[3], Image.new("L", (w, int(cut_h)), 0), m))
        ghost.alpha_composite(band, (gx, int(base_y - h_full)))
    img = Image.alpha_composite(img, ghost)

    col = GOLD if p_change > 0.5 else RED
    rx = cx_right - w // 2
    ry = base_y - int(h_now)
    if h_now > 4:
        arr = np.zeros((int(h_now), w, 4), np.uint8)
        for yy in range(int(h_now)):
            t = yy / max(int(h_now) - 1, 1)
            for i in range(3):
                arr[yy, :, i] = int(col[i] * (0.55 + 0.45 * (1 - t)))
            arr[yy, :, 3] = 255
        bar = Image.fromarray(arr, "RGBA")
        m = Image.new("L", (w, int(h_now)), 0)
        ImageDraw.Draw(m).rounded_rectangle([0, 0, w - 1, int(h_now) - 1], radius=int(14 * S), fill=255)
        bar.putalpha(m)
        img.alpha_composite(bar, (rx, ry))
        top = layer()
        ImageDraw.Draw(top).rounded_rectangle([rx, ry, rx + w, ry + int(20 * S)],
                                              radius=int(10 * S), fill=(*GOLD_SOFT, 210))
        img = Image.alpha_composite(img, top.filter(ImageFilter.GaussianBlur(14)))
    return img, (gx, base_y - h_full, gx + w, base_y), (rx, ry, rx + w, base_y), h_full, h_now


def arrow(img, p0, p1, p, color=GOLD, width=4, head=18):
    """指示箭头：线从 p0 长到 p1，末端带三角"""
    if p <= 0:
        return img
    lay = layer()
    d = ImageDraw.Draw(lay)
    a = int(245 * clamp(min(1.0, p * 2.2)))
    cw = max(2, int(width * S))
    k = eo(clamp(p))
    x1 = p0[0] + (p1[0] - p0[0]) * k
    y1 = p0[1] + (p1[1] - p0[1]) * k
    d.line([(p0[0], p0[1]), (x1, y1)], fill=(*color, a), width=cw)
    if k > 0.82:
        ang = math.atan2(p1[1] - p0[1], p1[0] - p0[0])
        hl = head * S
        tip = (x1, y1)
        d.polygon([tip,
                   (tip[0] - hl * math.cos(ang - 0.42), tip[1] - hl * math.sin(ang - 0.42)),
                   (tip[0] - hl * math.cos(ang + 0.42), tip[1] - hl * math.sin(ang + 0.42))],
                  fill=(*color, a))
    return Image.alpha_composite(img, lay)
