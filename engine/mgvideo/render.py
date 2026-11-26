# -*- coding: utf-8 -*-
"""
mgvideo.render —— 按 spec 渲染成片

流程：spec → 段落时间表 → 逐帧画（可多进程）→ ffmpeg 编码 → mp4
"""
import json
import os
import shutil
import subprocess

from PIL import Image, ImageDraw

from . import core as C
from . import fx
from . import templates

BG_FN = {
    "none": None,
    "bokeh": lambda img, t: fx.bg_bokeh(img, t),
    "grid": lambda img, t: fx.bg_grid_wave(img, t, alpha=0.55),
    "star": lambda img, t: fx.bg_starfield(img, t),
    "rays": lambda img, t: fx.bg_rays(img, t, alpha=0.40),
    "aurora": lambda img, t: fx.bg_aurora(img, t, alpha=0.34),
}

_SPEC = None
_TIMELINE = None


def load_spec(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def timeline(spec):
    """累计每段的起止帧"""
    fps = int(spec.get("fps", 30))
    out = []
    acc = 0.0
    for sc in spec["scenes"]:
        dur = float(sc.get("duration", 3.0))
        a, b = int(round(acc * fps)), int(round((acc + dur) * fps))
        out.append((sc, a, b))
        acc += dur
    return out, int(round(acc * fps))


def _prepare(spec):
    global _SPEC, _TIMELINE
    _SPEC = spec
    w, h = spec.get("size", [1920, 1080])
    C.configure(w, h, spec.get("supersample", 1.25))
    _TIMELINE, total = timeline(spec)
    return total


_FRAME_CACHE = {}


def _shot_source(sc):
    d = sc.get("frames_dir")
    if not d:
        return None
    frame = _ctx_frame[0] - sc.get("_start", 0)
    pat = sc.get("frame_pattern", "{:04d}.jpg")
    idx = max(0, frame)
    key = (d, idx)
    if key in _FRAME_CACHE:
        return _FRAME_CACHE[key]
    for pat2 in (pat, "{:04d}.png", "{:05d}.png", "{:04d}.jpg", "{:05d}.jpg"):
        p = os.path.join(d, pat2.format(idx + int(sc.get("frame_offset", 1))))
        if os.path.exists(p):
            im = Image.open(p).convert("RGB")
            if len(_FRAME_CACHE) > 8:
                _FRAME_CACHE.clear()
            _FRAME_CACHE[key] = im
            return im
    return None


_ctx_frame = [0]


def scene_body(idx, frame, p, spec=None):
    """段落本体：背景 + 模板。转场时只有这部分参与混合。"""
    spec = spec or _SPEC
    sc, a, b = _TIMELINE[idx]
    _ctx_frame[0] = frame
    sc = dict(sc)
    sc["_start"] = a
    img = C.base().copy()
    img = Image.alpha_composite(img, C.squares(frame))

    bg = sc.get("background", spec.get("background", "bokeh"))
    fn = BG_FN.get(bg, BG_FN["bokeh"])
    if fn:
        img = fn(img, frame / float(spec.get("fps", 30)))

    # 冲击效果只作用在背景层，绝不能盖到卡片文字上
    imp = sc.get("impact")
    if imp:
        at = float(imp.get("at", 0.4))
        ln = float(imp.get("len", 0.18))
        if at <= p < at + ln:
            q = (p - at) / ln
            if imp.get("speedlines"):
                sl = imp["speedlines"]
                img = fx.speedlines(img, q, alpha=sl if isinstance(sl, float) else 0.8)
            if imp.get("glow"):
                img = fx.glow_pulse(img, 1 - q, strength=float(imp["glow"]))

    tpl = templates.TEMPLATES.get(sc.get("template"))
    if tpl is None:
        raise ValueError(f"unknown template: {sc.get('template')}")
    ctx = {"fps": spec.get("fps", 30), "t": frame / float(spec.get("fps", 30)),
           "size": (C.W, C.H), "shot_source": _shot_source}
    img = tpl(img, sc, p, ctx)

    if imp and sc.get("shake"):
        at = float(imp.get("at", 0.4))
        ln = float(imp.get("len", 0.18))
        if at <= p < at + ln:
            img = fx.shake(img, frame, amp=float(sc["shake"]) * (1 - (p - at) / ln), seed=frame % 211)
    return img


def scene_overlay(img, idx, frame, p, spec=None):
    """顶部进度 / 章名 / 底部字幕。转场之后再叠，避免重影。"""
    spec = spec or _SPEC
    sc = _TIMELINE[idx][0]
    ch = spec.get("chrome", {})
    lay = C.layer()
    d = ImageDraw.Draw(lay)
    if ch.get("progress", True):
        total = _TIMELINE[-1][2] if _TIMELINE else 1
        y = int(46 * C.S)
        x0 = int(70 * C.S)
        w = int(C.CW * 0.86)
        d.rounded_rectangle([x0, y, x0 + w, y + int(4 * C.S)], radius=int(2 * C.S),
                            outline=(255, 255, 255, 18), width=1)
        d.rounded_rectangle([x0, y, x0 + w * frame / max(total - 1, 1), y + int(4 * C.S)],
                            radius=int(2 * C.S), fill=(*C.GOLD, 205))
    img = Image.alpha_composite(img, lay)
    if ch.get("brand"):
        img = Image.alpha_composite(img, C.txt(ch["brand"], C.font(int(28 * C.S), "Medium"),
                                               (int(70 * C.S), int(88 * C.S)), (150, 140, 190), anchor="lm"))
    if ch.get("chapter", True):
        label = sc.get("chapter") or f"{idx + 1:02d} / {len(_TIMELINE):02d}"
        img = Image.alpha_composite(img, C.txt(label, C.font(int(28 * C.S), "Bold"),
                                               (C.CW - int(70 * C.S), int(88 * C.S)), (*C.GOLD, 220), anchor="rm"))

    cap = sc.get("caption")
    if cap:
        if isinstance(cap, str):
            cap = {"text": cap}
        f = C.seg(p, cap.get("from", 0.0), cap.get("to", 1.0))
        if f > 0:
            lay = C.txt(cap["text"], C.font(int(46 * C.S), "Bold"), (C.CW // 2, int(C.CH * 0.925)), C.WHITE)
            bb = lay.getbbox()
            if bb:
                bg2 = C.layer()
                pad = int(34 * C.S)
                ImageDraw.Draw(bg2).rounded_rectangle(
                    [bb[0] - pad, bb[1] - int(16 * C.S), bb[2] + pad, bb[3] + int(16 * C.S)],
                    radius=int(26 * C.S), fill=(8, 6, 16, 165))
                lay = Image.alpha_composite(bg2, lay)
            a = min(C.eo(C.clamp(f * 4)), C.eo(C.clamp((1 - f) * 4)))
            img = Image.alpha_composite(img, C.fade(lay, a))
    return img


def frame_image(frame, spec=None):
    spec = spec or _SPEC
    fps = int(spec.get("fps", 30))
    for i, (sc, a, b) in enumerate(_TIMELINE):
        if a <= frame < b:
            p = (frame - a) / max(b - a, 1)
            body = scene_body(i, frame, p, spec)
            tr = sc.get("transition", spec.get("transition", "whip"))
            td = float(sc.get("transition_duration", spec.get("transition_duration", 0.34)))
            fn = fx.TRANSITIONS.get(tr)
            if fn and i > 0 and (frame - a) / fps < td:
                prev = scene_body(i - 1, max(a - 1, 0), 1.0, spec)
                body = fn(prev, body, C.clamp(((frame - a) / fps) / td))
            return scene_overlay(body, i, frame, p, spec)
    sc, a, b = _TIMELINE[-1]
    last = len(_TIMELINE) - 1
    return scene_overlay(scene_body(last, b - 1, 1.0, spec), last, b - 1, 1.0, spec)


def _render_one(args):
    frame, out_path, spec = args
    img = frame_image(frame, spec)
    img.putalpha(Image.new("L", (C.CW, C.CH), 255))
    out = img.convert("RGB").resize((C.W, C.H), Image.LANCZOS)
    out.save(out_path)
    return frame


def ffmpeg_bin():
    for p in (os.environ.get("MG_FFMPEG"),
              shutil.which("ffmpeg"),
              r"D:\ProgramData\ffmpeg-9.0.1-full_build\bin\ffmpeg.exe"):
        if p and os.path.exists(p):
            return p
    return "ffmpeg"


def render(spec, out_mp4, out_dir=None, workers=None, keep_frames=False, progress=None, shard=None):
    """渲染整片。返回统计信息。

    并行有两种方式：
      1) workers>1：多进程 Pool。某些受限环境（沙箱禁止命名管道）会起不来，
         这时自动退回单进程，不会报错终止。
      2) shard=(i, n)：只渲染帧号 % n == i 的部分。适合在 shell 里开 n 个进程，
         任何环境都能用，也方便放到多台机器上分。
    """
    total = _prepare(spec)
    fps = int(spec.get("fps", 30))
    out_dir = out_dir or os.path.join(os.path.dirname(os.path.abspath(out_mp4)), "frames")
    os.makedirs(out_dir, exist_ok=True)

    idxs = list(range(total))
    if shard:
        i, n = shard
        idxs = [f for f in idxs if f % n == i]
    tasks = [(i, os.path.join(out_dir, f"{i:04d}.png"), spec) for i in idxs]

    workers = workers or min(os.cpu_count() or 4, 8)
    done = 0
    if workers > 1:
        try:
            import multiprocessing as mp
            with mp.Pool(workers) as pool:
                for k, _ in enumerate(pool.imap_unordered(_render_one, tasks, chunksize=4)):
                    done = k
                    if progress and k % 30 == 0:
                        progress(k, len(tasks))
        except (PermissionError, OSError) as e:
            print(f"[mgvideo] 多进程不可用（{type(e).__name__}: {e}），退回单进程。"
                  f"需要提速可以用 --shard 分片并行。", flush=True)
            workers = 1
    if workers <= 1:
        for k, t in enumerate(tasks):
            _render_one(t)
            if progress and k % 30 == 0:
                progress(k, len(tasks))

    # 分片模式下不编码（由调用方在所有分片完成后统一编码）
    if shard and shard[1] > 1:
        return {"frames": len(tasks), "shard": list(shard), "encoded": False}

    audio = spec.get("audio") or {}
    wav = None
    if audio.get("voice") or audio.get("bgm", True):
        from . import audio as A
        bounds = [a / fps for _, a, b in _TIMELINE][1:]
        ticks = [float(x) for x in audio.get("ticks", [])]
        hits = [float(x) for x in audio.get("hits", [])]
        wav = os.path.join(out_dir, "mix.wav")
        A.build(total / fps, wav, voice=audio.get("voice"), scene_bounds=bounds, ticks=ticks, hits=hits)

    encode(spec, out_dir, out_mp4, wav)
    if not keep_frames:
        shutil.rmtree(out_dir, ignore_errors=True)
    return {"output": out_mp4, "frames": total, "seconds": round(total / fps, 2),
            "size_mb": round(os.path.getsize(out_mp4) / 1048576, 2)}


def encode(spec, frames_dir, out_mp4, wav=None):
    """把帧序列（可选音轨）编码成 mp4"""
    fps = int(spec.get("fps", 30))
    cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y",
           "-framerate", str(fps), "-i", os.path.join(frames_dir, "%04d.png")]
    if wav:
        cmd += ["-i", wav]
    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", str(spec.get("crf", 18)),
            "-pix_fmt", "yuv420p"]
    if wav:
        cmd += ["-c:a", "aac", "-b:a", "192k", "-shortest"]
    cmd += ["-movflags", "+faststart", out_mp4]
    subprocess.run(cmd, check=True)
    return out_mp4
