# -*- coding: utf-8 -*-
"""
mgvideo.shot —— 截图动图：把录屏里的关键局部截出来，做成「会动的重点」

用于 ComfyUI / 工作流这类画面密集的录屏：节点名、连线、参数按钮都很小，
全屏看不清楚，必须截局部放大 + 指出来。

这些函数都作用在卡片尺寸的画布上（不是整屏），坐标用卡片内坐标；
原片坐标 → 卡片坐标用 to_local() 换算。

重要：录屏会自己滚动。行级标注框只能在对得上的那几秒显示，
否则会框到别的行上去（这是踩过的坑）。
"""
import math

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from . import core as C


def lerp_rect(ra, rb, t):
    return tuple(ra[i] + (rb[i] - ra[i]) * t for i in range(4))


def _clip_rect(rect, w, h):
    x, y, rw, rh = rect
    x = max(0, min(x, w - 1))
    y = max(0, min(y, h - 1))
    rw = max(2, min(rw, w - x))
    rh = max(2, min(rh, h - y))
    return x, y, rw, rh


def crop_anim(src, ra, rb, p, size, sharpen=True):
    """在两块原片矩形之间缓动弹（推近 / 平移），输出 size 大小。
    录屏放大后会发虚，默认补一次 unsharp。"""
    w, h = src.size
    e = C.eio(p)
    a, b = _clip_rect(ra, w, h), _clip_rect(rb, w, h)
    x, y, rw, rh = lerp_rect(a, b, e)
    box = (int(round(x)), int(round(y)), int(round(x + rw)), int(round(y + rh)))
    box = (box[0], box[1], max(box[0] + 2, box[2]), max(box[1] + 2, box[3]))
    out = src.crop(box).resize(size, Image.LANCZOS)
    if sharpen and size[0] > box[2] - box[0]:
        out = out.filter(ImageFilter.UnsharpMask(radius=1.4, percent=70, threshold=2))
    return out.convert("RGBA")


def to_local(src_rect, crop_rect, size):
    """原片坐标 → 卡片内坐标"""
    sx, sy, sw, sh = crop_rect
    cx, cy, cw, ch = src_rect
    return ((cx - sx) * size[0] / max(sw, 1e-6), (cy - sy) * size[1] / max(sh, 1e-6),
            cw * size[0] / max(sw, 1e-6), ch * size[1] / max(sh, 1e-6))


def card(src_img, crop_rect, size, radius=None, border=(255, 255, 255, 52), border_w=2):
    """把原片一块做成圆角卡片（不带阴影；阴影用 C.shadow_sprite）"""
    spr = crop_anim(src_img, crop_rect, crop_rect, 0.0, size)
    r = radius if radius is not None else int(min(size) * 0.045)
    spr.putalpha(C.round_mask(size, r))
    lay = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(lay).rounded_rectangle([0, 0, size[0] - 1, size[1] - 1], radius=r,
                                          outline=border, width=max(1, int(border_w * C.S)))
    return Image.alpha_composite(spr, lay)


def spot(canvas, rect, p=1.0, dim=175, feather=34):
    """聚光灯：矩形外压暗"""
    w, h = canvas.size
    r = int(min(rect[2], rect[3]) * 0.10)
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).rounded_rectangle([rect[0], rect[1], rect[0] + rect[2], rect[1] + rect[3]],
                                        radius=r, fill=255)
    m = m.filter(ImageFilter.GaussianBlur(max(2, feather / 2)))
    dark = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    dark.putalpha(m.point(lambda v: int((255 - v) * dim / 255 * C.clamp(p))))
    return Image.alpha_composite(canvas, dark)


def brackets(canvas, rect, p, color=None, length=0.20, width=3):
    """四角取景框：先长横臂，再长竖臂"""
    color = color or C.GOLD
    if p <= 0:
        return canvas
    w, h = canvas.size
    x, y, rw, rh = rect
    lay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    a = int(255 * C.clamp(p))
    if a <= 3:
        return canvas
    lw = max(6, min(rw, rh) * length)
    arm, arm2 = C.eo(C.clamp(p * 1.6)), C.eo(C.clamp(p * 1.6 - 0.35))
    cw = max(2, int(width * C.S))
    for cx, cy, sx, sy in ((x, y, 1, 1), (x + rw, y, -1, 1), (x, y + rh, 1, -1), (x + rw, y + rh, -1, -1)):
        d.line([(cx, cy), (cx + sx * lw * arm, cy)], fill=(*color, a), width=cw)
        d.line([(cx, cy), (cx, cy + sy * lw * arm2)], fill=(*color, a), width=cw)
    return Image.alpha_composite(canvas, lay)


def box(canvas, rect, p, color=None, width=3):
    """细边框：上右下左依次生长"""
    color = color or C.GOLD
    if p <= 0:
        return canvas
    w, h = canvas.size
    x, y, rw, rh = rect
    lay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    a = int(235 * C.clamp(min(1.0, p * 3)))
    cw = max(2, int(width * C.S))
    k, per = C.clamp(p), 0.25
    d.line([(x, y), (x + rw * C.eo(C.clamp(k / per)), y)], fill=(*color, a), width=cw)
    if k > per:
        d.line([(x + rw, y), (x + rw, y + rh * C.eo(C.clamp((k - per) / per)))], fill=(*color, a), width=cw)
    if k > per * 2:
        d.line([(x + rw, y + rh), (x + rw - rw * C.eo(C.clamp((k - per * 2) / per)), y + rh)],
               fill=(*color, a), width=cw)
    if k > per * 3:
        d.line([(x, y + rh), (x, y + rh - rh * C.eo(C.clamp((k - per * 3) / per)))], fill=(*color, a), width=cw)
    return Image.alpha_composite(canvas, lay)


def chip(canvas, xy, text, f, p, color=None, fg=(18, 14, 28), anchor="lm"):
    """贴片标签：圆角实底 + 文字，从左滑入"""
    color = color or C.GOLD
    if p <= 0:
        return canvas
    e = C.eo(C.clamp(p))
    w, h = canvas.size
    tw = f.getlength(text)
    th = f.size * 1.05
    padx, pady = 16 * C.S, 10 * C.S
    bw, bh = tw + padx * 2, th + pady * 2
    x = xy[0] - bw if anchor == "rm" else (xy[0] - bw / 2 if anchor == "mm" else xy[0])
    y = xy[1] - bh / 2
    x += (1 - e) * -34 * C.S
    lay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    a = int(248 * e)
    d.rounded_rectangle([x, y, x + bw, y + bh], radius=int(bh * 0.34), fill=(*color, a))
    d.text((x + bw / 2, y + bh / 2), text, font=f, fill=(*fg, a), anchor="mm")
    return Image.alpha_composite(canvas, lay)


def scan(canvas, rect, p, color=(255, 245, 210), strength=0.5, width=0.10):
    """扫描光带：一条亮带在矩形内横扫一次"""
    if not (0 < p < 1):
        return canvas
    w, h = canvas.size
    x, y, rw, rh = rect
    band = np.zeros((h, w, 4), np.float32)
    pos = x + rw * C.eio(p)
    xs = np.arange(w)[None, :].astype(np.float32)
    g = np.exp(-((xs - pos) ** 2) / (2 * (rw * width) ** 2))
    band[:, :, 0], band[:, :, 1], band[:, :, 2] = color
    band[:, :, 3] = g * 255 * strength * math.sin(math.pi * p)
    inrect = np.zeros((h, w), np.float32)
    inrect[int(y):int(y + rh), int(x):int(x + rw)] = 1
    band[:, :, 3] *= inrect
    return Image.alpha_composite(canvas, Image.fromarray(np.clip(band, 0, 255).astype(np.uint8), "RGBA"))


def magnifier(canvas, src_img, src_rect, center, r, p, ring=None, zoom=2.4):
    """圆形放大镜：从原片再裁一块放大，放进气泡"""
    ring = ring or C.GOLD
    if p <= 0:
        return canvas
    rr = int(r * C.spring(C.clamp(p)))
    if rr < 4:
        return canvas
    x, y, rw, rh = src_rect
    d = 2 * r / zoom
    box_ = (max(0, int(x + rw / 2 - d / 2)), max(0, int(y + rh / 2 - d / 2)),
            int(x + rw / 2 + d / 2), int(y + rh / 2 + d / 2))
    crop = src_img.crop(box_).resize((2 * rr, 2 * rr), Image.LANCZOS).convert("RGBA")
    m = Image.new("L", (2 * rr, 2 * rr), 0)
    ImageDraw.Draw(m).ellipse([0, 0, 2 * rr - 1, 2 * rr - 1], fill=255)
    crop.putalpha(m)
    w, h = canvas.size
    lay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    lay.alpha_composite(crop, dest=(int(center[0] - rr), int(center[1] - rr)))
    out = Image.alpha_composite(canvas, lay)
    ring_lay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(ring_lay).ellipse([center[0] - rr, center[1] - rr, center[0] + rr, center[1] + rr],
                                     outline=(*ring, 240), width=max(2, int(4 * C.S)))
    return Image.alpha_composite(out, ring_lay)


def flow(canvas, points, p, color=None, n=3, r=6, width=3):
    """沿折线流动的光点：提示连线走向"""
    color = color or C.GOLD
    if p <= 0 or len(points) < 2:
        return canvas
    w, h = canvas.size
    segs = []
    total = 0.0
    for i in range(len(points) - 1):
        ln = math.hypot(points[i + 1][0] - points[i][0], points[i + 1][1] - points[i][1])
        segs.append((points[i], points[i + 1], ln))
        total += ln
    lay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    a0 = int(90 * C.clamp(p * 2))
    for (a, b, _) in segs:
        d.line([a, b], fill=(*color, a0), width=max(1, int(width * C.S * 0.6)))
    for k in range(n):
        t = ((p * 1.6 + k / n) % 1.0) * total
        acc = 0.0
        for (a, b, ln) in segs:
            if acc + ln >= t:
                u = (t - acc) / max(ln, 1e-6)
                px, py = a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u
                rr = r * C.S
                d.ellipse([px - rr, py - rr, px + rr, py + rr], fill=(*color, 235))
                d.ellipse([px - rr * 2.4, py - rr * 2.4, px + rr * 2.4, py + rr * 2.4], fill=(*color, 40))
                break
            acc += ln
    return Image.alpha_composite(canvas, lay)
