# -*- coding: utf-8 -*-
"""
mgvideo.templates —— 模板库（9 个版式）

每个模板签名统一：draw(img, sc, p, ctx)
    img  当前帧画布（RGBA，超采样尺寸）
    sc   该段的 spec 字典
    p    段内进度 0–1
    ctx  {"fps", "t", "size", "shot_source": callable}

再加模板就在 TEMPLATES 里注册一个函数，spec 里写 "template": "<名字>"。
"""
import math
import os

import numpy as np
from PIL import Image, ImageDraw

from . import core as C
from . import fx
from . import shot as S

COLORS = {
    "gold": C.GOLD, "gold_soft": C.GOLD_SOFT, "green": C.GREEN, "red": C.RED,
    "white": C.WHITE, "muted": C.MUTED, "cyan": (120, 200, 255),
}


def col(name, default=None):
    if isinstance(name, (list, tuple)):
        return tuple(name)
    return COLORS.get(name, default or C.WHITE)


def _r(img, text, px, xy, color, p, a=0.02, b=0.30, anchor="mm", weight="Bold", dy=22):
    return C.reveal_text(img, text, C.font(px, weight), xy, color, C.seg(p, a, b), anchor=anchor, dy=dy)


# ---------------------------------------------------------------- title
def t_title(img, sc, p, ctx):
    cx, cy = C.CW // 2, C.CH // 2
    title = sc.get("title", "")
    sub = sc.get("subtitle", "")
    kick = sc.get("kick", "")
    f1 = C.fit_font(title, int(122 * C.S), C.CW * 0.86, "Black")
    y = cy - (int(96 * C.S) if sub else 0)
    img = C.reveal_text(img, title, f1, (cx, y), col(sc.get("title_color", "white")),
                        C.seg(p, 0.04, 0.34), dy=26)
    if sub:
        f2 = C.fit_font(sub, int(112 * C.S), C.CW * 0.86, "Black")
        img = C.reveal_text(img, sub, f2, (cx, y + int(150 * C.S)), col(sc.get("sub_color", "gold")),
                            C.seg(p, 0.16, 0.46), dy=26)
    if kick:
        img = _r(img, kick, 40 * C.S, (cx, cy + int(190 * C.S)), C.MUTED, p, 0.42, 0.68, dy=14)
    return img


# ---------------------------------------------------------------- number
def t_number(img, sc, p, ctx):
    cx = C.CW // 2
    label = sc.get("label", "")
    unit = sc.get("unit", "")
    note = sc.get("note", "")
    bars = sc.get("bars")

    # 有柱子时把大数字抬高，否则柱顶标签会和大字撞在一起
    y_label = C.CH * (0.12 if bars else 0.24)
    y_value = C.CH * (0.23 if bars else 0.42)
    big_px = int((110 if bars else 150) * C.S)

    if label:
        img = _r(img, label, 54 * C.S, (cx, y_label), C.MUTED, p, 0.02, 0.16)

    # 数值：直接出现，不做乱码（同屏只允许一个版本）
    frm, to = sc.get("from"), sc.get("to")
    big = sc.get("value", "")
    np_ = C.seg(p, 0.06, 0.30)
    if frm is not None and to is not None:
        cur = frm + (to - frm) * C.eio(C.seg(p, 0.34, 0.62))
        big = f"{cur:.0f}" if abs(cur - round(cur)) < 0.05 else f"{cur:.1f}"
    if big != "":
        f = C.font(big_px, "Black")
        c = col(sc.get("color", "gold"))
        txt = f"{big}{unit}"
        img = C.reveal_text(img, txt, f, (cx, y_value), c, np_, dy=40)
        if sc.get("bar_scale", True) and bars is None:
            # 底下一条比例条，给数字一个参照
            x0, x1 = int(C.CW * 0.30), int(C.CW * 0.70)
            y = int(C.CH * 0.55)
            lay = C.layer()
            d = ImageDraw.Draw(lay)
            d.rounded_rectangle([x0, y, x1, y + int(6 * C.S)], radius=int(4 * C.S), fill=(255, 255, 255, 30))
            w = C.eo(C.seg(p, 0.16, 0.52))
            d.rounded_rectangle([x0, y, x0 + (x1 - x0) * w, y + int(6 * C.S)], radius=int(4 * C.S), fill=(*c, 225))
            img = Image.alpha_composite(img, lay)

    if bars and len(bars) >= 2:
        grow = C.spring(C.seg(p, 0.06, 0.34))
        change = C.eio(C.seg(p, 0.46, 0.72))
        r = sc.get("ratio", 0.4)
        ratio = r * (1 - change) + sc.get("ratio_to", r) * change
        base_y = int(C.CH * 0.76)
        img, gb, rb, hf, hn = C.bar_pair(img, int(C.CW * 0.33), int(C.CW * 0.50),
                                         base_y, int(C.CH * 0.28), ratio, grow, change)
        if grow > 0.4:
            fb = C.font(int(30 * C.S), "Medium")
            img = Image.alpha_composite(img, C.txt(bars[0].get("label", ""), fb,
                                                   (int(C.CW * 0.33), base_y + int(38 * C.S)),
                                                   (140, 148, 172)))
            img = Image.alpha_composite(img, C.txt(bars[1].get("label", ""), fb,
                                                   (int(C.CW * 0.50), base_y + int(38 * C.S)),
                                                   (140, 148, 172)))
            if bars[0].get("top"):
                img = Image.alpha_composite(img, C.txt(bars[0]["top"], fb,
                                                       (int(C.CW * 0.33), base_y - int(hf) - int(30 * C.S)),
                                                       (150, 158, 180)))
            if bars[1].get("top"):
                img = Image.alpha_composite(img, C.txt(bars[1]["top"], fb,
                                                       (int(C.CW * 0.50), base_y - int(hn) - int(30 * C.S)),
                                                       (150, 158, 180)))
        if note and change > 0.7:
            img = Image.alpha_composite(img, C.fade(
                C.txt(note, C.font(int(42 * C.S), "Medium"), (int(C.CW * 0.70), int(C.CH * 0.56)),
                      C.MUTED, anchor="lm"), C.eo(C.seg(p, 0.72, 0.9))))
    elif note:
        img = _r(img, note, 44 * C.S, (cx, C.CH * 0.64), C.MUTED, p, 0.45, 0.75, dy=14)
    return img


# ---------------------------------------------------------------- compare
def t_compare(img, sc, p, ctx):
    left, right = sc.get("left", {}), sc.get("right", {})
    w, h = int(C.CW * 0.31), int(C.CH * 0.34)
    y = int(C.CH * 0.30)
    boxes = [(int(C.CW * 0.115), left, "red"), (int(C.CW * 0.575), right, "gold")]

    for i, (x, side, dflt) in enumerate(boxes):
        pp = C.eo(C.seg(p, 0.04 + i * 0.12, 0.34 + i * 0.12))
        if pp <= 0:
            continue
        c = col(side.get("color", dflt))
        img = C.card_box(img, (x, y, x + w, y + h), fill=C.CARD_DARK, line=(*c, 95))
        img = Image.alpha_composite(img, C.fade(C.txt(side.get("title", ""), C.font(int(38 * C.S), "Medium"),
                                                      (x + int(36 * C.S), y + int(62 * C.S)), C.MUTED, anchor="lm"), pp))
        fv = C.fit_font(side.get("value", ""), int(62 * C.S), w - int(72 * C.S), "Black")
        img = Image.alpha_composite(img, C.fade(C.txt(side.get("value", ""), fv,
                                                      (x + int(36 * C.S), y + int(172 * C.S)), c, anchor="lm"), pp))
        if side.get("note"):
            img = Image.alpha_composite(img, C.fade(
                C.txt(side["note"], C.font(int(32 * C.S), "Medium"),
                      (x + int(36 * C.S), y + int(h) - int(52 * C.S)), (185, 180, 205), anchor="lm"), pp))

    # 中间虚线
    if p > 0.40:
        mid = int(C.CW * 0.50)
        lay = C.layer()
        d = ImageDraw.Draw(lay)
        a = int(140 * C.eo(C.seg(p, 0.40, 0.62)))
        for yy in range(y, y + h, int(22 * C.S)):
            d.line([(mid, yy), (mid, yy + int(12 * C.S))], fill=(255, 255, 255, a), width=2)
        img = Image.alpha_composite(img, lay)

    if sc.get("note") and p > 0.55:
        img = _r(img, sc["note"], 48 * C.S, (C.CW // 2, int(C.CH * 0.73)), C.WHITE, p, 0.55, 0.80)
    return img


# ---------------------------------------------------------------- flow
def t_flow(img, sc, p, ctx):
    steps = sc.get("steps", [])
    n = max(len(steps), 2)
    y = int(C.CH * 0.50)
    x0, x1 = int(C.CW * 0.14), int(C.CW * 0.86)
    gap = (x1 - x0) / (n - 1)
    img = C.reveal_text(img, sc.get("label", "流程"), C.font(int(38 * C.S), "Medium"),
                        (int(C.CW * 0.14), int(C.CH * 0.33)), C.MUTED, C.seg(p, 0.02, 0.16), anchor="lm")
    lp = C.eo(C.seg(p, 0.08, 0.55))
    lay = C.layer()
    ImageDraw.Draw(lay).line([(x0, y), (x0 + (x1 - x0) * lp, y)], fill=(*C.GOLD, 120), width=max(2, int(3 * C.S)))
    img = Image.alpha_composite(img, lay)
    for i, s in enumerate(steps):
        name = s if isinstance(s, str) else s.get("name", "")
        pp = C.seg(p, 0.05 + i * 0.13, 0.29 + i * 0.13)
        if pp <= 0:
            continue
        cx = x0 + gap * i
        r = int(44 * C.S) * C.spring(pp)
        c = C.GOLD if i == n - 1 else C.WHITE
        lay2 = C.layer()
        d = ImageDraw.Draw(lay2)
        d.ellipse([cx - r, y - r, cx + r, y + r], outline=(*c, 230), width=max(2, int(3 * C.S)),
                  fill=(14, 10, 28, 230))
        if r > int(18 * C.S):
            d.text((cx, y - int(2 * C.S)), str(i + 1), font=C.font(int(30 * C.S), "Bold"),
                   fill=(*c, 230), anchor="mm")
        img = Image.alpha_composite(img, lay2)
        img = Image.alpha_composite(img, C.fade(
            C.txt(name, C.font(int(48 * C.S), "Bold"), (cx, y + int(104 * C.S)), c), C.eo(pp)))
    return img


# ---------------------------------------------------------------- timeline
def t_timeline(img, sc, p, ctx):
    events = sc.get("events", [])
    y = int(C.CH * 0.52)
    x0, x1 = int(C.CW * 0.16), int(C.CW * 0.84)
    lay = C.layer()
    d = ImageDraw.Draw(lay)
    d.rounded_rectangle([x0, y - int(3 * C.S), x1, y + int(3 * C.S)], radius=int(3 * C.S), fill=(255, 255, 255, 40))
    grow = C.eo(C.seg(p, 0.05, 0.50))
    d.rounded_rectangle([x0, y - int(3 * C.S), x0 + (x1 - x0) * grow, y + int(3 * C.S)],
                        radius=int(3 * C.S), fill=(*C.GOLD, 200))
    img = Image.alpha_composite(img, lay)
    n = max(len(events), 2)
    for i, e in enumerate(events):
        pp = C.seg(p, 0.18 + i * 0.14, 0.44 + i * 0.14)
        if pp <= 0:
            continue
        ex = x0 + (x1 - x0) * (i / max(n - 1, 1))
        r = int(13 * C.S) * C.spring(pp)
        lay2 = C.layer()
        ImageDraw.Draw(lay2).ellipse([ex - r, y - r, ex + r, y + r], fill=(*C.GOLD, 245))
        img = Image.alpha_composite(img, lay2)
        side = -1 if i % 2 == 0 else 1
        ly = int(y + side * int(92 * C.S))
        img = Image.alpha_composite(img, C.fade(
            C.txt(e.get("label", ""), C.font(int(48 * C.S), "Bold"), (ex, ly), C.WHITE), C.eo(pp)))
        if e.get("at"):
            img = Image.alpha_composite(img, C.fade(
                C.txt(e["at"], C.font(int(28 * C.S), "Bold"), (ex, ly + side * int(48 * C.S)), C.GOLD), C.eo(pp)))
    return img


# ---------------------------------------------------------------- quote
def t_quote(img, sc, p, ctx):
    text = sc.get("text", "")
    f = C.fit_font(text, int(92 * C.S), C.CW * 0.72, "Bold")
    total = f.getlength(text)
    x = (C.CW - total) / 2
    y = int(C.CH * 0.46)
    lay = C.layer()
    d = ImageDraw.Draw(lay)
    for i, ch in enumerate(text):
        pp = C.eo(C.seg(p, 0.05 + i * 0.022, 0.23 + i * 0.022))
        if pp <= 0:
            continue
        d.text((x, y), ch, font=f, fill=(*C.WHITE, int(255 * pp)))
        x += f.getlength(ch)
    img = Image.alpha_composite(img, lay)
    q = C.font(int(160 * C.S), "Black")
    img = Image.alpha_composite(img, C.fade(C.txt("「", q, (int(C.CW * 0.15), int(C.CH * 0.40)), (*C.GOLD, 150),
                                                  anchor="mm"), C.eo(C.seg(p, 0.02, 0.18))))
    up = C.eo(C.seg(p, 0.62, 0.86))
    if up > 0:
        lay3 = C.layer()
        half = int(total * 0.5 * up)
        ImageDraw.Draw(lay3).rounded_rectangle([C.CW // 2 - half, y + int(126 * C.S), C.CW // 2 + half,
                                                y + int(132 * C.S)], radius=int(3 * C.S), fill=(*C.GOLD, 220))
        img = Image.alpha_composite(img, lay3)
    if sc.get("by"):
        img = _r(img, f"— {sc['by']}", 34 * C.S, (C.CW // 2, y + int(220 * C.S)), C.MUTED, p, 0.7, 0.9)
    return img


# ---------------------------------------------------------------- bullets
def t_bullets(img, sc, p, ctx):
    items = sc.get("items", [])
    title = sc.get("title", "")
    x = int(C.CW * 0.16)
    y = int(C.CH * 0.26)
    if title:
        img = C.reveal_text(img, title, C.font(int(62 * C.S), "Black"), (x, y), C.GOLD,
                            C.seg(p, 0.02, 0.18), anchor="lm", dy=18)
        y += int(140 * C.S)
    else:
        y += int(40 * C.S)
    for i, it in enumerate(items):
        pp = C.seg(p, 0.10 + i * 0.12, 0.32 + i * 0.12)
        if pp <= 0:
            continue
        e = C.eo(pp)
        dy = int((1 - e) * 26 * C.S)
        # 序号胶囊
        lay = C.layer()
        d = ImageDraw.Draw(lay)
        r = int(26 * C.S)
        d.ellipse([x, y + dy - r, x + 2 * r, y + dy + r], fill=(*C.GOLD, int(235 * e)))
        d.text((x + r, y + dy), str(i + 1), font=C.font(int(26 * C.S), "Black"),
               fill=(20, 16, 30, int(255 * e)), anchor="mm")
        img = Image.alpha_composite(img, lay)
        text = it if isinstance(it, str) else it.get("text", "")
        note = None if isinstance(it, str) else it.get("note")
        f = C.fit_font(text, int(52 * C.S), int(C.CW * 0.66), "Bold")
        img = Image.alpha_composite(img, C.fade(C.txt(text, f, (x + 2 * r + int(28 * C.S), y + dy),
                                                      C.WHITE, anchor="lm"), e))
        if note:
            img = Image.alpha_composite(img, C.fade(
                C.txt(note, C.font(int(32 * C.S), "Medium"),
                      (x + 2 * r + int(28 * C.S), y + dy + int(46 * C.S)), C.MUTED, anchor="lm"), e))
        y += int(130 * C.S)
    return img


# ---------------------------------------------------------------- shot
def _marks(card_img, sc, p, crop_now, size):
    """在卡片上画标注。rect / arrow 坐标都是原片坐标，按当前裁剪框换算。"""
    for mk in sc.get("marks", []):
        a = mk.get("from", 0.0)
        b = mk.get("to", 1.0)
        pp = C.clamp((p - a) / max(b - a, 1e-6))
        if pp <= 0:
            continue
        color = col(mk.get("color", "gold"))
        typ = mk.get("type")
        if typ in ("brackets", "box", "scan", "spot"):
            r = S.to_local(mk["rect"], crop_now, size)
            if typ == "brackets":
                card_img = S.brackets(card_img, r, pp, color=color)
            elif typ == "box":
                card_img = S.box(card_img, r, pp, color=color)
            elif typ == "scan":
                card_img = S.scan(card_img, r, pp, color=(255, 246, 210), strength=0.34, width=0.08)
            else:
                card_img = S.spot(card_img, r, pp, dim=mk.get("dim", 175))
        elif typ == "arrow":
            r = S.to_local(mk["rect"], crop_now, size)
            p1 = (r[0] + r[2] / 2, r[1] + r[3] / 2)
            p0 = (p1[0] + mk.get("dx", 200) * C.S, p1[1] + mk.get("dy", 120) * C.S)
            card_img = C.arrow(card_img, p0, p1, pp, color=color)
        elif typ == "magnifier":
            r = S.to_local(mk["rect"], crop_now, size)
            card_img = S.magnifier(card_img, mk.get("_src"), mk["rect"],
                                   (size[0] * mk.get("cx", 0.72), size[1] * mk.get("cy", 0.66)),
                                   mk.get("r", 110) * C.S, pp, ring=color)
        if mk.get("text") and typ in ("brackets", "box"):
            r = S.to_local(mk["rect"], crop_now, size)
            anchor = mk.get("anchor", "rm")
            xy = (r[0] + r[2] - 6, r[1] + 26) if anchor == "rm" else (r[0], r[1] - 26)
            card_img = S.chip(card_img, xy, mk["text"], C.font(int(34 * C.S), "Bold"),
                              C.clamp((pp - 0.25) / 0.3), color=color, anchor=anchor)
    return card_img


def t_shot(img, sc, p, ctx):
    size = (int(sc.get("card_w", 1200) * C.S), int(sc.get("card_h", 675) * C.S))
    crop = sc.get("crop", [[0, 0, 1920, 1080]])
    if not isinstance(crop[0], (list, tuple)):
        crop = [crop]
    ra = crop[0]
    rb = crop[-1] if len(crop) > 1 else crop[0]
    e = C.eio(C.clamp(p / max(sc.get("move_until", 1.0), 1e-6)))
    crop_now = S.lerp_rect(ra, rb, e)

    src = ctx.get("shot_source")
    src_img = src(sc) if src else None
    if src_img is None:
        card = Image.new("RGBA", size, (20, 16, 34, 255))
    else:
        card = S.crop_anim(src_img, crop_now, crop_now, 0.0, size)
        for mk in sc.get("marks", []):
            if mk.get("type") == "magnifier":
                mk["_src"] = src_img

    card = _marks(card, sc, p, crop_now, size)
    radius = int(28 * C.S)
    card.putalpha(C.round_mask(size, radius))
    edge = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(edge).rounded_rectangle([0, 0, size[0] - 1, size[1] - 1], radius=radius,
                                           outline=(255, 255, 255, 52), width=max(1, int(2 * C.S)))
    card = Image.alpha_composite(card, edge)

    x = int(C.CW * sc.get("x", 0.0833))     # 默认居中偏左
    y = int(C.CH * sc.get("y", 0.2222))
    sh, pad = C.shadow_sprite(size, radius)
    img.alpha_composite(sh, dest=(x - pad, y - pad))
    img.alpha_composite(card, dest=(x, y))

    # 右侧文字面板（可选）
    panel = sc.get("panel")
    if panel:
        px0, px1 = int(C.CW * 0.6125), int(C.CW * 0.9667)
        py0, py1 = int(C.CH * 0.2444), int(C.CH * 0.7037)
        lay = C.layer()
        d = ImageDraw.Draw(lay)
        pa = C.eo(C.seg(p, 0.02, 0.20))
        d.rounded_rectangle([px0, py0, px1, py1], radius=int(30 * C.S),
                            fill=(10, 8, 20, int(158 * pa)), outline=(255, 255, 255, int(30 * pa)),
                            width=max(1, int(2 * C.S)))
        img = Image.alpha_composite(img, lay)
        tx = px0 + int(46 * C.S)
        ty = py0 + int(120 * C.S)
        for i, line in enumerate(panel.get("lines", [])):
            text = line if isinstance(line, str) else line.get("text", "")
            c = col(line.get("color", "white") if isinstance(line, dict) else "white")
            f = C.font(int((line.get("size", 54) if isinstance(line, dict) else 54) * C.S), "Bold")
            img = C.reveal_text(img, text, f, (tx, ty), c, C.seg(p, 0.06 + i * 0.12, 0.24 + i * 0.12),
                                anchor="lm", dy=18)
            ty += int(((line.get("size", 54) if isinstance(line, dict) else 54) * 1.75) * C.S)
    return img


# ---------------------------------------------------------------- end
def t_end(img, sc, p, ctx):
    lines = sc.get("lines", ["谢谢"])
    colors = sc.get("colors", ["gold", "white", "gold"])
    cx, cy = C.CW // 2, C.CH // 2
    step = int(150 * C.S)
    y0 = cy - step * (len(lines) - 1) / 2
    for i, text in enumerate(lines):
        f = C.fit_font(text, int(112 * C.S), C.CW * 0.86, "Black")
        img = C.reveal_text(img, text, f, (cx, y0 + i * step), col(colors[i % len(colors)]),
                            C.seg(p, 0.05 + i * 0.10, 0.35 + i * 0.10), dy=26)
    if sc.get("note"):
        img = _r(img, sc["note"], 38 * C.S, (cx, y0 + len(lines) * step + 10 * C.S), C.MUTED, p, 0.55, 0.8)
    return img


TEMPLATES = {
    "title": t_title, "number": t_number, "compare": t_compare, "flow": t_flow,
    "timeline": t_timeline, "quote": t_quote, "bullets": t_bullets, "shot": t_shot, "end": t_end,
}
