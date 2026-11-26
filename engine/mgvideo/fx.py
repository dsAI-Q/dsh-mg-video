# -*- coding: utf-8 -*-
"""
mgvideo.fx —— 背景层 / 转场 / 文字动效 / 冲击效果

三条硬规则（都踩过）：
  1. 背景层只叠在卡片之下；速度线、泛光这类效果只能画在背景上，
     画到卡片上面会像划痕划过文字。
  2. 转场中段两边都可能偏空，甩镜要配一条扫光带把视线带过去。
  3. 数字不要用乱码滚动：观众会看到 16/18 而字幕写着 15。数字一律直接出现。
"""
import math

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from . import core as C

_GOLD, _WHITE, _MUTED = C.GOLD, C.WHITE, C.MUTED


def _diag():
    return math.hypot(C.CW, C.CH)


# ============================ 背景层 ============================
_BOKEH = {}


def _bokeh_sprite(rgb, size, softness=0.62):
    key = (rgb, size, softness)
    if key in _BOKEH:
        return _BOKEH[key]
    n = max(24, size)
    yy, xx = np.mgrid[0:n, 0:n].astype(np.float32)
    c = (n - 1) / 2.0
    d = np.sqrt((xx - c) ** 2 + (yy - c) ** 2) / c
    a = np.clip((1 - d) / softness, 0, 1) ** 2.0
    arr = np.zeros((n, n, 4), np.float32)
    arr[:, :, 0], arr[:, :, 1], arr[:, :, 2] = rgb
    arr[:, :, 3] = a * 255
    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGBA")
    _BOKEH[key] = img
    return img


def bg_bokeh(img, t, n=26, colors=None, alpha=1.0, seed=17):
    """柔和光斑：sin/cos 漂移 + 呼吸缩放（用预渲染精灵，避免逐帧模糊）"""
    colors = colors or [(232, 185, 60), (120, 110, 220), (255, 120, 90), (90, 200, 255)]
    rng = np.random.default_rng(seed)
    xs, ys = rng.uniform(0, 1, n), rng.uniform(0, 1, n)
    bs, sp = rng.uniform(90, 340, n) * C.S, rng.uniform(0.18, 0.55, n)
    ph, al = rng.uniform(0, 2 * math.pi, n), rng.uniform(0.10, 0.34, n)
    ci = rng.integers(0, len(colors), n)
    lay = C.layer()
    for i in range(n):
        tt = t * sp[i] + ph[i]
        x = xs[i] * C.CW + math.sin(tt) * 70 * C.S
        y = ys[i] * C.CH + math.cos(tt * 0.8) * 60 * C.S
        sz = int(bs[i] * (1 + 0.16 * math.sin(tt * 1.2)))
        spr = _bokeh_sprite(colors[ci[i]], sz, 0.72)
        a = int(255 * al[i] * alpha)
        if a <= 2:
            continue
        lay.alpha_composite(C.fade(spr, a / 255.0), dest=(int(x - sz / 2), int(y - sz / 2)))
    return Image.alpha_composite(img, lay)


def bg_grid_wave(img, t, alpha=1.0, step=86, amp=26, sp=1.15):
    """透视点阵波：横向正弦推进，点按深度缩放"""
    lay = C.layer()
    d = ImageDraw.Draw(lay)
    st = int(step * C.S)
    rows, cols = C.CH // st + 2, C.CW // st + 2
    for j in range(rows):
        depth = j / max(rows - 1, 1)
        for i in range(cols):
            x = i * st + st / 2
            wave = math.sin((i * 0.35) + t * sp + j * 0.22) * amp * C.S * (0.35 + depth)
            y = j * st + st / 2 + wave
            r = 2.0 * C.S * (0.5 + depth * 0.9)
            a = int(alpha * (55 + 150 * depth) * (0.55 + 0.45 * math.sin(i * 0.4 + t * 2.1)))
            a = max(0, min(210, a))
            if a <= 2:
                continue
            d.ellipse([x - r, y - r, x + r, y + r], fill=(170, 160, 235, a))
    return Image.alpha_composite(img, lay)


def bg_starfield(img, t, n=150, speed=1.0, alpha=1.0, seed=5):
    """星流：从中心向外飞"""
    rng = np.random.default_rng(seed)
    ang = rng.uniform(0, 2 * math.pi, n)
    rad0 = rng.uniform(0.06, 1.0, n)
    ln = rng.uniform(0.05, 0.22, n)
    lay = C.layer()
    d = ImageDraw.Draw(lay)
    cx, cy = C.CW * 0.5, C.CH * 0.5
    D = _diag()
    for i in range(n):
        z = (rad0[i] + t * 0.22 * speed) % 1.16
        r1 = z ** 2.1 * D * 0.62
        r2 = max(r1 - ln[i] * D * 0.16 * (0.4 + z), 0)
        x1, y1 = cx + math.cos(ang[i]) * r1, cy + math.sin(ang[i]) * r1 * 0.72
        x2, y2 = cx + math.cos(ang[i]) * r2, cy + math.sin(ang[i]) * r2 * 0.72
        a = int(alpha * 245 * (0.25 + z * 0.75))
        if a <= 3:
            continue
        d.line([(x2, y2), (x1, y1)], fill=(230, 235, 255, min(235, a)),
               width=max(1, int((1.8 + 1.6 * z) * C.S)))
    return Image.alpha_composite(img, lay)


def bg_rays(img, t, n=13, alpha=0.55, color=(255, 214, 120), spin=0.16, cx=0.5, cy=0.30):
    """旋转光束：半分辨率绘制再放大，省掉逐帧全尺寸模糊"""
    hw, hh = C.CW // 2, C.CH // 2
    lay = Image.new("RGBA", (hw, hh), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    ox, oy = cx * C.CW / 2, cy * C.CH / 2
    L = _diag() / 2 * 1.2
    for i in range(n):
        a0 = (i / n) * 2 * math.pi + t * spin
        spread = 0.030 + 0.020 * math.sin(i * 2.7 + t)
        p1 = (ox + math.cos(a0 - spread) * L, oy + math.sin(a0 - spread) * L)
        p2 = (ox + math.cos(a0 + spread) * L, oy + math.sin(a0 + spread) * L)
        a = int(alpha * 46 * (0.55 + 0.45 * math.sin(i * 1.9 + t * 1.6)))
        d.polygon([(ox, oy), p1, p2], fill=(*color, max(0, a)))
    lay = lay.filter(ImageFilter.GaussianBlur(3)).resize((C.CW, C.CH), Image.BILINEAR)
    return Image.alpha_composite(img, lay)


def bg_aurora(img, t, alpha=0.7):
    """极光带：纵向正弦色带叠加"""
    hw, hh = C.CW // 2, C.CH // 2
    arr = np.zeros((hh, hw, 4), np.float32)
    yy, xx = np.mgrid[0:hh, 0:hw].astype(np.float32)
    for k, (freq, spd, col) in enumerate(((0.011, 0.55, (120, 90, 230)),
                                          (0.017, -0.34, (60, 170, 200)),
                                          (0.024, 0.21, (232, 160, 90)))):
        band = np.sin(xx * freq + t * spd + k * 1.7) * (hh * 0.16) + hh * (0.34 + 0.14 * k)
        g = np.exp(-((yy - band) ** 2) / (2 * (hh * 0.11) ** 2))
        arr[:, :, 0] += g * col[0]
        arr[:, :, 1] += g * col[1]
        arr[:, :, 2] += g * col[2]
        arr[:, :, 3] += g * 46
    lay = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGBA")
    lay = lay.filter(ImageFilter.GaussianBlur(9)).resize((C.CW, C.CH), Image.BILINEAR)
    return Image.alpha_composite(img, C.fade(lay, alpha))


# ============================ 转场 ============================
def blur_x(img, r):
    """水平运动模糊（积分图，O(N)）"""
    if r < 1:
        return img
    a = np.asarray(img, dtype=np.float32)
    k = 2 * r + 1
    pad = np.pad(a, ((0, 0), (r, r), (0, 0)), mode="edge")
    cs = np.cumsum(pad, axis=1, dtype=np.float32)
    cs = np.concatenate([np.zeros((a.shape[0], 1, a.shape[2]), np.float32), cs], axis=1)
    out = (cs[:, k:, :] - cs[:, :-k, :]) / k
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGBA")


def xf_whip(a, b, p, direction=1, soft=0.26, bulge=14, flare=0.55):
    """甩镜：软边擦除 + 水平模糊 + 交界扫光带（扫光带是为了避免中段空屏）"""
    e = C.eio(p)
    xs = np.linspace(0.0, 1.0, C.CW, dtype=np.float32)
    edge = e * (1 + soft * 2) - soft
    m = np.clip((edge - xs) / soft + 0.5, 0, 1) if direction >= 0 else np.clip((xs - (1 - edge)) / soft + 0.5, 0, 1)
    mask = np.repeat(m[None, :], C.CH, axis=0)
    out = a.copy()
    out.paste(b, (0, 0), Image.fromarray((mask * 255).astype(np.uint8), "L"))
    out = blur_x(out, int(bulge * C.S * math.sin(math.pi * C.clamp(p))))
    if flare > 0:
        amt = math.sin(math.pi * C.clamp(p)) ** 0.8 * flare
        pos = C.clamp(edge) * C.CW
        band = np.exp(-((xs * C.CW - pos) ** 2) / (2 * (C.CW * 0.055) ** 2))
        arr = np.zeros((C.CH, C.CW, 4), np.float32)
        arr[:, :, 0], arr[:, :, 1], arr[:, :, 2] = 255, 246, 226
        arr[:, :, 3] = band[None, :] * 255 * amt
        out = Image.alpha_composite(out, Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGBA"))
    return out


def xf_circle(a, b, p, cx=0.5, cy=0.5, soft=0.06):
    """圆形擦除"""
    r = C.eio(p) * _diag() * 1.15
    m = Image.new("L", (C.CW, C.CH), 0)
    ox, oy = cx * C.CW, cy * C.CH
    ImageDraw.Draw(m).ellipse([ox - r, oy - r, ox + r, oy + r], fill=255)
    if soft > 0:
        m = m.filter(ImageFilter.GaussianBlur(int(soft * C.CW / 4)))
    out = a.copy()
    out.paste(b, (0, 0), m)
    return out


def xf_mosaic(a, b, p, blocks=(96, 64, 40, 24, 12, 6)):
    """像素块溶解"""
    k = C.clamp(p)
    bs = int(blocks[min(int(k * len(blocks)), len(blocks) - 1)] * C.S)
    q = (k * len(blocks)) % 1.0
    sz = (max(C.CW // bs, 1), max(C.CH // bs, 1))
    sa = a.resize(sz, Image.BILINEAR).resize((C.CW, C.CH), Image.NEAREST)
    sb = b.resize(sz, Image.BILINEAR).resize((C.CW, C.CH), Image.NEAREST)
    return Image.blend(sa, sb, q)


def xf_slide_up(a, b, p, tilt=0.06):
    """上推"""
    e = C.eio(p)
    out = C.layer()
    off = int(C.CH * e)
    out.alpha_composite(C.fade(a, 1 - e * 0.85), dest=(0, -off))
    bs = b.resize((int(C.CW * (1 + tilt * (1 - e))), int(C.CH * (1 + tilt * (1 - e)))), Image.BILINEAR)
    out.alpha_composite(bs, dest=((C.CW - bs.size[0]) // 2, C.CH - off))
    return out


_INK = None


def xf_ink(a, b, p, softness=0.14, seed=23):
    """墨迹扩散：固定噪声场做阈值"""
    global _INK
    if _INK is None:
        rng = np.random.default_rng(seed)
        small = rng.random((C.CH // 26, C.CW // 26)).astype(np.float32)
        n = Image.fromarray((small * 255).astype(np.uint8), "L").resize((C.CW, C.CH), Image.BICUBIC)
        n = n.filter(ImageFilter.GaussianBlur(9))
        _INK = np.asarray(n, dtype=np.float32) / 255.0
    k = C.eio(p) * (1 + softness) - softness * 0.5
    mask = np.clip((k - _INK) / softness + 0.5, 0, 1)
    out = a.copy()
    out.paste(b, (0, 0), Image.fromarray((mask * 255).astype(np.uint8), "L"))
    return out


TRANSITIONS = {
    "whip": xf_whip, "circle": xf_circle, "mosaic": xf_mosaic,
    "slide": xf_slide_up, "ink": xf_ink, "none": None,
}


# ============================ 文字动效 ============================
def t_kinetic(img, words, f, xy, p, color=None, gap=26, stagger=0.12, dur=0.34):
    """词块弹入：每个词独立 spring 放大 + 轻微旋转"""
    color = color or C.WHITE
    sprites = []
    for w in words:
        lay = C.txt(w, f, (C.CW // 2, C.CH // 2), color)
        bb = lay.getbbox()
        sprites.append(lay.crop(bb) if bb else None)
    widths = [s.size[0] if s else 0 for s in sprites]
    x = xy[0] - (sum(widths) + gap * C.S * (len(words) - 1)) / 2
    for i, spr in enumerate(sprites):
        pp = C.spring(C.seg(p, i * stagger, i * stagger + dur))
        if spr is not None and pp > 0.001:
            sc = 0.55 + 0.45 * min(pp, 1.25)
            s2 = spr.resize((max(int(spr.size[0] * sc), 1), max(int(spr.size[1] * sc), 1)), Image.BICUBIC)
            s2 = C.fade(s2, min(1.0, pp * 1.6))
            s2 = s2.rotate((1 - min(pp, 1)) * 6 * (1 if i % 2 == 0 else -1), resample=Image.BICUBIC, expand=True)
            img.alpha_composite(s2, dest=(int(x + widths[i] / 2 - s2.size[0] / 2),
                                          int(xy[1] - s2.size[1] / 2)))
        x += widths[i] + gap * C.S
    return img


_GLYPHS = "ABCDEFGHJKLMNPQRSTUVWXYZ0123456789%+-"
_DIGITS = "0123456789"


def t_scramble(img, text, f, xy, p, color=None, anchor="mm", settle=0.72):
    """乱码锁定。**不要用于数字**：同一屏会出现两个数值。"""
    color = color or C.WHITE
    body = [c for c in text if not c.isspace()]
    numeric = bool(body) and all(c.isdigit() or c in "%+-.,:" for c in body)
    pool = _DIGITS if numeric else _GLYPHS
    n, out = len(text), []
    for i, ch in enumerate(text):
        th = i / max(n, 1) * settle
        if p >= th + (1 - settle) * 0.6:
            out.append(ch)
        elif ch.isspace():
            out.append(" ")
        elif p > th * 0.55:
            out.append(pool[int((p * 97 + i * 13)) % len(pool)])
        else:
            out.append(ch)
    return Image.alpha_composite(img, C.txt("".join(out), f, xy, color, anchor=anchor))


def t_gradient(img, text, f, xy, p, c1=None, c2=(255, 245, 210), anchor="mm", shift=0.0):
    """渐变扫光文字"""
    c1 = c1 or C.GOLD
    m = Image.new("L", (C.CW, C.CH), 0)
    ImageDraw.Draw(m).text(xy, text, font=f, fill=255, anchor=anchor)
    bb = m.getbbox()
    if not bb:
        return img
    w = bb[2] - bb[0]
    xs = np.linspace(0, 1, C.CW, dtype=np.float32)[None, :]
    k = np.clip((xs * C.CW - bb[0]) / max(w, 1) + shift - p * 1.1, 0, 1)
    row = np.zeros((1, C.CW, 4), np.float32)
    for i in range(3):
        row[0, :, i] = c1[i] * (1 - k[0]) + c2[i] * k[0]
    row[0, :, 3] = 255
    grad = Image.fromarray(row.astype(np.uint8), "RGBA").resize((C.CW, C.CH), Image.BILINEAR)
    grad.putalpha(m)
    return Image.alpha_composite(img, grad)


def t_wave(img, text, f, xy, p, color=None, amp=24, freq=0.55):
    """波浪字：逐字 sin 位移"""
    color = color or C.WHITE
    total = f.getlength(text)
    x = xy[0] - total / 2
    a = C.clamp(p * 3)
    for i, ch in enumerate(text):
        dy = math.sin(i * freq + p * 7.0) * amp * C.S * a
        img = Image.alpha_composite(img, C.fade(C.txt(ch, f, (x, xy[1] + dy), color, anchor="lm"), a))
        x += f.getlength(ch)
    return img


def t_typewriter(img, text, f, xy, p, color=None, cursor=True, anchor="lm"):
    """打字机：逐字出现 + 光标（p 到 1 时光标消失）"""
    color = color or C.WHITE
    cut = int(C.clamp(p) * len(text) + 0.0001)
    s = text[:cut]
    img = Image.alpha_composite(img, C.txt(s, f, xy, color, anchor=anchor))
    if cursor and 0.02 < p < 0.995 and (int(p * 20) % 2 == 0 or cut < len(text)):
        w = f.getlength(s)
        lay = C.layer()
        ImageDraw.Draw(lay).rectangle([xy[0] + w + 6 * C.S, xy[1] - 40 * C.S,
                                       xy[0] + w + 12 * C.S, xy[1] + 26 * C.S], fill=(*C.GOLD, 220))
        img = Image.alpha_composite(img, lay)
    return img


def t_stroke(img, text, f, xy, p, color=None, fill_progress=0.65, anchor="mm"):
    """描边生长：先画轮廓，再填色"""
    color = color or C.GOLD
    stroke_p = C.clamp(p / max(fill_progress, 0.01))
    fill_p = C.clamp((p - fill_progress) / max(1 - fill_progress, 0.01))
    out = img
    if stroke_p > 0:
        lay = C.layer()
        ImageDraw.Draw(lay).text(xy, text, font=f, fill=None, stroke_width=max(1, int(2.4 * C.S)),
                                 stroke_fill=(*color, int(220 * stroke_p)), anchor=anchor)
        bb = lay.getbbox()
        if bb:
            h = bb[3] - bb[1]
            m = Image.new("L", (C.CW, C.CH), 0)
            ImageDraw.Draw(m).rectangle([0, bb[3] - int(h * stroke_p), C.CW, bb[3]], fill=255)
            lay.putalpha(Image.composite(lay.split()[3], Image.new("L", (C.CW, C.CH), 0), m))
        out = Image.alpha_composite(out, lay)
    if fill_p > 0:
        out = Image.alpha_composite(out, C.fade(C.txt(text, f, xy, color, anchor=anchor), fill_p))
    return out


TEXT_STYLES = {
    "reveal": None, "kinetic": t_kinetic, "gradient": t_gradient,
    "wave": t_wave, "typewriter": t_typewriter, "stroke": t_stroke,
}


# ============================ 画面级 ============================
def shake(img, frame, amp=10, decay=1.0, seed=3):
    """镜头震动：每帧取确定性偏移（seed 要用帧号，否则等于没动）"""
    rng = np.random.default_rng(seed)
    dx = (rng.random() - 0.5) * 2 * amp * C.S * decay
    dy = (rng.random() - 0.5) * 2 * amp * C.S * decay
    out = C.layer()
    out.alpha_composite(img, dest=(int(dx), int(dy)))
    return out


def speedlines(img, p, n=54, alpha=0.85, seed=9):
    """速度线：从四周向中心收束"""
    rng = np.random.default_rng(seed)
    lay = C.layer()
    d = ImageDraw.Draw(lay)
    cx, cy = C.CW * 0.5, C.CH * 0.5
    k = C.clamp(p)
    D = _diag()
    for i in range(n):
        a0 = rng.uniform(0, 2 * math.pi)
        r0 = D * (0.36 - 0.20 * k) * rng.uniform(0.45, 1.0)
        ln = rng.uniform(0.06, 0.20) * D * (1 - 0.45 * k)
        x1, y1 = cx + math.cos(a0) * r0, cy + math.sin(a0) * r0 * 0.75
        x2, y2 = cx + math.cos(a0) * (r0 + ln), cy + math.sin(a0) * (r0 + ln) * 0.75
        al = int(255 * alpha * (1 - k) * rng.uniform(0.5, 1.0))
        if al <= 4:
            continue
        d.line([(x1, y1), (x2, y2)], fill=(255, 255, 255, al), width=max(1, int(2 * C.S)))
    return Image.alpha_composite(img, lay)


def glow_pulse(img, p, color=None, strength=0.5):
    """高光泛光：提亮部模糊叠加，只在关键帧用（贵）"""
    color = color or C.GOLD
    if p <= 0:
        return img
    small = img.resize((C.CW // 4, C.CH // 4), Image.BILINEAR)
    a = np.asarray(small, dtype=np.float32)
    lum = a[:, :, 0] * 0.3 + a[:, :, 1] * 0.5 + a[:, :, 2] * 0.2
    m = np.clip((lum - 150) / 105, 0, 1) ** 1.4
    arr = np.zeros_like(a)
    arr[:, :, 0], arr[:, :, 1], arr[:, :, 2] = color
    arr[:, :, 3] = m * 255 * strength * C.clamp(p)
    lay = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGBA")
    lay = lay.filter(ImageFilter.GaussianBlur(6)).resize((C.CW, C.CH), Image.BILINEAR)
    return Image.alpha_composite(img, lay)
