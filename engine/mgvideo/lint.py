# -*- coding: utf-8 -*-
"""
mgvideo.lint —— 渲染前体检，以及 agent 用的分层清单

动机：agent 干完活没人检查。「主轨有一段空白」这类问题，渲染器自己不会报错，
只能靠人眼看。这里把这些判定固化成断言，让机器在交付前挡住它们。

三个入口：
  layers(spec)              结构化分层清单（agent 拿它改内容，不看像素）
  check_spec(spec)          spec 层体检：模板、时长、字幕越界、时间窗、转场吃字幕
  check_video(spec, video)  成片体检：空白段、音频峰值、时长偏差

问题条目统一形状：{"code", "level", "scene", "at", "msg"}
  code 以 S 开头=spec 层，V 开头=成片层，方便脚本按类别过滤
"""
import json
import os
import shutil
import subprocess

import numpy as np

from . import core as C

# 判定阈值（都写在这里，别散落在代码里）
BLANK_INK = 0.012        # 帧内平均梯度低于这个值就算「没有内容」
BLANK_MIN_SECONDS = 0.5  # 连续空白超过这么久才报
PEAK_DBFS_MAX = -1.0     # 音频峰值上限（超过就是接近削波）
DURATION_TOLERANCE = 0.10  # 成片时长与 spec 允许的偏差（秒）
CAPTION_SAFE = 0.84      # 字幕安全区宽度占画布比例
SAMPLE_FPS = 4           # 成片体检的采样率（每秒 4 帧够判断空白）


def _prob(code, msg, scene=None, at=None, level="error"):
    return {"code": code, "level": level, "scene": scene,
            "at": None if at is None else round(float(at), 3), "msg": msg}


def _caption_of(sc):
    """把 caption 统一成 {text, from, to}，没有就返回 None"""
    cap = sc.get("caption")
    if not cap:
        return None
    if isinstance(cap, str):
        return {"text": cap, "from": 0.05, "to": 1.0}
    if isinstance(cap, dict) and cap.get("text"):
        return {"text": cap["text"], "from": float(cap.get("from", 0.05)),
                "to": float(cap.get("to", 1.0))}
    return None


# ---------------------------------------------------------------- 分层清单

def layers(spec):
    """按 spec 推导出「层」清单。

    注意：这是 spec 声明的层，不是像素层。渲染器本身只会把层叠到画布上，
    没有可编辑的图层文件；agent 改内容改的是这份清单背后的 spec 字段。
    """
    fps = int(spec.get("fps", 30))
    chrome = spec.get("chrome") or {}
    out = []
    acc = 0.0
    for i, sc in enumerate(spec.get("scenes") or []):
        dur = float(sc.get("duration", 3.0))
        sid = i + 1
        tpl = sc.get("template")
        rows = []
        rows.append({"z": 0, "kind": "background", "id": f"s{sid}.bg",
                     "value": sc.get("background", spec.get("background", "bokeh")),
                     "from": round(acc, 3), "to": round(acc + dur, 3)})
        fields = {k: v for k, v in sc.items()
                  if k not in ("template", "duration", "caption", "impact", "background",
                               "transition", "transition_duration", "chapter", "shake")}
        rows.append({"z": 10, "kind": "content", "id": f"s{sid}.content", "value": tpl,
                     "from": round(acc, 3), "to": round(acc + dur, 3), "fields": fields})
        imp = sc.get("impact")
        if imp:
            at = float(imp.get("at", 0.4))
            ln = float(imp.get("len", 0.18))
            rows.append({"z": 50, "kind": "impact", "id": f"s{sid}.impact", "value": imp,
                         "from": round(acc + at * dur, 3), "to": round(acc + (at + ln) * dur, 3)})
        cap = _caption_of(sc)
        if cap:
            rows.append({"z": 80, "kind": "caption", "id": f"s{sid}.caption", "text": cap["text"],
                         "from": round(acc + cap["from"] * dur, 3),
                         "to": round(acc + cap["to"] * dur, 3)})
        items = []
        if chrome.get("progress", True):
            items.append("progress")
        if chrome.get("chapter", True):
            items.append("chapter")
        if chrome.get("brand"):
            items.append("brand")
        if items:
            rows.append({"z": 90, "kind": "chrome", "id": f"s{sid}.chrome", "value": items,
                         "from": round(acc, 3), "to": round(acc + dur, 3)})
        out.append({"scene": sid, "template": tpl, "chapter": sc.get("chapter", ""),
                    "start": round(acc, 3), "end": round(acc + dur, 3), "duration": dur,
                    "frames": [int(round(acc * fps)), int(round((acc + dur) * fps))],
                    "layers": rows})
        acc += dur
    return out


# ---------------------------------------------------------------- spec 体检

def check_spec(spec):
    from . import templates, render

    probs = []
    fps = int(spec.get("fps", 30))
    size = spec.get("size", [1920, 1080])
    C.configure(int(size[0]), int(size[1]), spec.get("supersample", 1.25))
    scenes = spec.get("scenes") or []
    if not scenes:
        return [_prob("S001", "scenes 为空：至少要有一段")]

    try:
        tl, total = render.timeline(spec)
    except Exception as e:  # 时间表算不出来，后面的检查没意义
        return [_prob("S002", f"时间表算不出来：{type(e).__name__}: {e}")]

    safe_w = C.CW * CAPTION_SAFE
    cap_font = C.font(int(46 * C.S), "Bold")
    dur_all = total / fps

    for i, (sc, a, b) in enumerate(tl):
        sid = i + 1
        dur = float(sc.get("duration", 3.0))
        tpl = sc.get("template")
        if not tpl:
            probs.append(_prob("S010", "缺 template 字段", sid, a / fps))
        elif tpl not in templates.TEMPLATES:
            probs.append(_prob("S011", f"模板不存在：{tpl}", sid, a / fps))
        if dur <= 0:
            probs.append(_prob("S012", f"时长必须 > 0，实际 {dur}", sid, a / fps))
            continue
        if tpl == "shot":
            d = sc.get("frames_dir")
            if not d:
                probs.append(_prob("S013", "shot 段缺 frames_dir", sid, a / fps))
            elif not os.path.isdir(d):
                probs.append(_prob("S014", f"frames_dir 不存在：{d}", sid, a / fps))

        cap = _caption_of(sc)
        if cap:
            if cap["from"] < 0 or cap["to"] > 1.0001:
                probs.append(_prob("S020",
                                   f"字幕时间窗越界：[{cap['from']}, {cap['to']}] 必须落在 [0, 1]",
                                   sid, a / fps))
            elif cap["from"] >= cap["to"]:
                probs.append(_prob("S021",
                                   f"字幕时间窗为空：from={cap['from']} >= to={cap['to']}",
                                   sid, a / fps))
            # 不能用 C.txt 量宽度：它是画在整张画布上，超出部分被裁掉，
            # getbbox 量到的是「被裁后的宽度」，越界反而测不出来。用字体本身量。
            try:
                tw = float(cap_font.getlength(cap["text"]))
            except AttributeError:  # Pillow < 9
                from PIL import ImageDraw as _ID
                tw = float(_ID.Draw(C.layer()).textlength(cap["text"], font=cap_font))
            if tw > safe_w:
                probs.append(_prob("S022",
                                   f"字幕超出安全区：{tw:.0f}px > {safe_w:.0f}px（{tw / C.CW:.0%} 画面宽），"
                                   f"会被裁或压到边缘：{cap['text'][:18]}",
                                   sid, a / fps))

        imp = sc.get("impact")
        if imp:
            at = float(imp.get("at", 0.4))
            ln = float(imp.get("len", 0.18))
            if at < 0 or at + ln > 1.0001:
                probs.append(_prob("S030",
                                   f"冲击效果时间窗越界：[{at}, {at + ln}] 必须落在 [0, 1]",
                                   sid, a / fps))

        tdn = float(sc.get("transition_duration", spec.get("transition_duration", 0.34)))
        if tdn >= dur:
            probs.append(_prob("S031",
                               f"转场 {tdn:.2f}s 不短于本段时长 {dur:.2f}s，整段会被转场吃掉",
                               sid, a / fps))

        # 帧对齐：段落帧数是两次 round 相减出来的，时长不是整帧的倍数时
        # 会多出或少掉 1 帧 —— 表现就是画面一顿、或者两段之间插了一帧
        span = b - a
        want = int(round(dur * fps))
        if span != want:
            probs.append(_prob("S040",
                               f"段落时长与帧数不对齐：{dur:.3f}s × {fps}fps 应为 {want} 帧，"
                               f"实际 {span} 帧（差 {span - want:+d} 帧，画面会跳一下）；"
                               f"把 duration 调成 {want / fps:.3f}s 附近",
                               sid, a / fps, level="warn"))

    return probs


# ---------------------------------------------------------------- 成片体检

def _run(cmd):
    return subprocess.run(cmd, capture_output=True)


def _ffmpeg():
    from .render import ffmpeg_bin
    return ffmpeg_bin()


def _ffprobe():
    if os.environ.get("MG_FFPROBE"):
        return os.environ["MG_FFPROBE"]
    p = os.path.join(os.path.dirname(_ffmpeg()), "ffprobe.exe" if os.name == "nt" else "ffprobe")
    return p if os.path.exists(p) else (shutil.which("ffprobe") or "ffprobe")


def _probe(video):
    r = _run([_ffprobe(), "-v", "error", "-show_entries", "format=duration",
              "-show_entries", "stream=codec_type", "-of", "json", video])
    if r.returncode != 0:
        return None
    import json as _json
    data = _json.loads(r.stdout.decode("utf-8", "ignore") or "{}")
    kinds = [s.get("codec_type") for s in data.get("streams", [])]
    dur = float((data.get("format") or {}).get("duration", 0) or 0)
    return {"duration": dur, "has_video": "video" in kinds, "has_audio": "audio" in kinds}


def _sample_gray(video, w=320, h=180, fps=SAMPLE_FPS):
    r = _run([_ffmpeg(), "-v", "error", "-i", video,
              "-vf", f"fps={fps},scale={w}:{h},format=gray",
              "-f", "rawvideo", "-"])
    if r.returncode != 0 or not r.stdout:
        return None
    buf = np.frombuffer(r.stdout, dtype=np.uint8)
    n = len(buf) // (w * h)
    if n == 0:
        return None
    return buf[:n * w * h].reshape(n, h, w).astype(np.float32)


def _ink(frame):
    """帧内平均梯度：纯背景渐变很小，文字/卡片边缘很大"""
    gy, gx = np.gradient(frame)
    return float(np.mean(np.sqrt(gx * gx + gy * gy)))


def _peak_dbfs(video):
    r = _run([_ffmpeg(), "-hide_banner", "-i", video, "-af", "volumedetect",
              "-f", "null", os.devnull])
    txt = (r.stderr or b"").decode("utf-8", "ignore")
    for line in txt.splitlines():
        if "max_volume:" in line:
            try:
                return float(line.split("max_volume:")[1].split("dB")[0].strip())
            except ValueError:
                return None
    return None


def check_video(spec, video):
    probs = []
    if not os.path.exists(video):
        return [_prob("V000", f"成片不存在：{video}")]
    info = _probe(video)
    if not info:
        return [_prob("V001", "读不出成片信息（ffprobe 失败）")]
    fps = int(spec.get("fps", 30))
    want = len(spec.get("scenes") or []) and sum(
        float(s.get("duration", 3.0)) for s in spec["scenes"]) or 0
    if want and abs(info["duration"] - want) > DURATION_TOLERANCE:
        probs.append(_prob("V010",
                           f"成片时长 {info['duration']:.2f}s 与 spec 的 {want:.2f}s 差 "
                           f"{info['duration'] - want:+.2f}s"))

    audio_cfg = spec.get("audio") or {}
    if audio_cfg.get("voice") or audio_cfg.get("bgm", True):
        if not info["has_audio"]:
            probs.append(_prob("V011", "spec 要求音轨，成片里没有音轨"))
        else:
            pk = _peak_dbfs(video)
            if pk is not None and pk > PEAK_DBFS_MAX:
                probs.append(_prob("V012", f"音频峰值 {pk:.2f} dBFS 超过 {PEAK_DBFS_MAX} dBFS，有削波风险"))

    frames = _sample_gray(video)
    if frames is None:
        probs.append(_prob("V020", "取不到画面（ffmpeg 采样失败）"))
        return probs

    inks = [_ink(f) for f in frames]
    blank = [v < BLANK_INK for v in inks]
    run_start = None
    for k, is_blank in enumerate(blank + [False]):
        if is_blank and run_start is None:
            run_start = k
        elif not is_blank and run_start is not None:
            span = (k - run_start) / SAMPLE_FPS
            if span >= BLANK_MIN_SECONDS:
                probs.append(_prob("V030",
                                   f"画面空了一段：{run_start / SAMPLE_FPS:.2f}s 起 {span:.2f}s "
                                   f"内没有可看的元素（平均梯度 {min(inks[run_start:k]):.4f} < {BLANK_INK}）",
                                   at=run_start / SAMPLE_FPS, level="warn"))
            run_start = None
    return probs


# ---------------------------------------------------------------- 入口

def lint(spec, video=None):
    probs = check_spec(spec)
    if video:
        probs += check_video(spec, video)
    return probs
