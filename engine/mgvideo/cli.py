# -*- coding: utf-8 -*-
"""mgvideo.cli —— 命令行入口：render / check / frames / calib / probe"""
import argparse
import json
import os
import subprocess
import sys

from PIL import Image, ImageDraw


def _ff():
    from .render import ffmpeg_bin
    return ffmpeg_bin()


def cmd_render(a):
    from . import render
    spec = render.load_spec(a.spec)
    if a.out:
        spec.setdefault("_out", a.out)
    total = render._prepare(spec)

    def prog(k, t):
        print(f"\r  渲染 {k}/{t}", end="", flush=True)

    info = render.render(spec, a.out, out_dir=a.frames_dir, workers=a.workers,
                         keep_frames=a.keep_frames, progress=prog,
                         shard=tuple(int(x) for x in a.shard.split("/")) if a.shard else None)
    print()
    print(json.dumps(info, ensure_ascii=False, indent=2))


def cmd_check(a):
    from . import render
    spec = render.load_spec(a.spec)
    total = render._prepare(spec)
    tl, total = render.timeline(spec)
    fps = int(spec.get("fps", 30))
    print(f"size      : {spec.get('size', [1920, 1080])} @ {fps}fps")
    print(f"total     : {total} frames = {total / fps:.2f}s")
    print(f"scenes    : {len(tl)}")
    t = 0.0
    for i, (sc, aa, bb) in enumerate(tl):
        print(f"  {i + 1:02d} {sc.get('template', '?'):9s} {aa / fps:6.2f}-{bb / fps:6.2f}s  "
              f"{(sc.get('duration', 0)):5.2f}s  {sc.get('chapter', '')}")
        tpl = sc.get("template")
        if tpl not in __import__("mgvideo.templates", fromlist=["TEMPLATES"]).TEMPLATES:
            print(f"     !! 未知模板: {tpl}")
        if tpl == "shot" and sc.get("frames_dir") and not os.path.isdir(sc["frames_dir"]):
            print(f"     !! frames_dir 不存在: {sc['frames_dir']}")
    if a.json:
        print(json.dumps({"scenes": [{"template": s.get("template"), "start": x / fps, "end": y / fps}
                                     for s, x, y in tl], "total": total}, ensure_ascii=False, indent=2))


def cmd_frames(a):
    """从视频切出逐帧，供 shot 模板使用"""
    os.makedirs(a.out, exist_ok=True)
    cmd = [_ff(), "-hide_banner", "-loglevel", "error", "-y",
           "-ss", str(a.start), "-t", str(a.duration), "-i", a.video,
           "-vf", f"fps={a.fps}", "-q:v", str(a.quality),
           os.path.join(a.out, "%04d.jpg")]
    subprocess.run(cmd, check=True)
    n = len([f for f in os.listdir(a.out) if f.endswith(".jpg")])
    print(json.dumps({"dir": os.path.abspath(a.out), "frames": n, "fps": a.fps}, ensure_ascii=False))


def cmd_calib(a):
    """给视频某一秒出带坐标网格的预览，用来读出截图矩形"""
    out = a.out or os.path.join(os.path.dirname(os.path.abspath(a.video)) or ".", "calib")
    os.makedirs(out, exist_ok=True)
    png = os.path.join(out, f"calib-{int(a.sec)}.png")
    subprocess.run([_ff(), "-hide_banner", "-loglevel", "error", "-y", "-ss", str(a.sec),
                    "-i", a.video, "-frames:v", "1", png], check=True)
    im = Image.open(png).convert("RGB")
    d = ImageDraw.Draw(im)
    W, H = im.size
    for x in range(0, W, 100):
        d.line([(x, 0), (x, H)], fill=(60, 255, 120), width=1)
        d.text((x + 2, 2), str(x), fill=(255, 255, 0))
        d.text((x + 2, H - 14), str(x), fill=(255, 255, 0))
    for y in range(0, H, 100):
        d.line([(0, y), (W, y)], fill=(255, 60, 60), width=1)
        d.text((2, y + 2), str(y), fill=(255, 255, 0))
        d.text((W - 46, y + 2), str(y), fill=(255, 255, 0))
    if a.rect:
        cols = [(255, 60, 60), (60, 255, 120), (80, 180, 255), (255, 220, 0), (255, 120, 220)]
        for i, part in enumerate(a.rect.split(";")):
            try:
                x, y, w, h = [int(v) for v in part.split(",")]
            except ValueError:
                continue
            c = cols[i % len(cols)]
            d.rectangle([x, y, x + w, y + h], outline=c, width=4)
            d.text((x + 6, y + 6), f"{x},{y},{w},{h}", fill=c)
    dest = png.replace(".png", "-grid.png")
    im.save(dest)
    print(json.dumps({"image": dest, "size": [W, H]}, ensure_ascii=False))


def cmd_probe(a):
    """读视频信息。优先用 ffprobe 拿精确时长，退用 ffmpeg 的文本输出。"""
    import shutil
    ff = _ff()
    info = {}
    probe = shutil.which("ffprobe")
    if os.environ.get("MG_FFPROBE"):
        probe = os.environ["MG_FFPROBE"]
    if probe is None:
        cand = os.path.join(os.path.dirname(ff), "ffprobe.exe" if os.name == "nt" else "ffprobe")
        probe = cand if os.path.exists(cand) else None
    if probe:
        r = subprocess.run([probe, "-v", "error", "-show_entries", "format=duration",
                            "-show_entries", "stream=codec_type,width,height,r_frame_rate",
                            "-of", "json", a.video], capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            data = json.loads(r.stdout)
            info["duration_s"] = round(float(data.get("format", {}).get("duration", 0)), 3)
            streams = data.get("streams", [])
            info["video"] = [s for s in streams if s.get("codec_type") == "video"]
            info["audio"] = [s for s in streams if s.get("codec_type") == "audio"]
            print(json.dumps(info, ensure_ascii=False, indent=2))
            return
    out = subprocess.run([ff, "-hide_banner", "-i", a.video], capture_output=True, text=True, errors="ignore")
    for line in out.stderr.splitlines():
        if "Duration:" in line:
            info["duration"] = line.split("Duration:")[1].split(",")[0].strip()
        if "Video:" in line and "Stream #" in line:
            info["video"] = line.strip()
        if "Audio:" in line and "Stream #" in line:
            info["audio"] = line.strip()
    print(json.dumps(info, ensure_ascii=False, indent=2))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="mgvideo", description="MG 动画渲染器：一份 JSON 清单一部片")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("render", help="按 spec 渲染成片")
    r.add_argument("spec")
    r.add_argument("-o", "--out", required=True)
    r.add_argument("--workers", type=int, default=0)
    r.add_argument("--frames-dir", default=None)
    r.add_argument("--keep-frames", action="store_true")
    r.add_argument("--shard", default="", help="只渲染第 i/n 份，例如 0/4；分片跑完再统一 encode")
    r.set_defaults(fn=cmd_render)

    c = sub.add_parser("check", help="校验 spec 并打印时间表")
    c.add_argument("spec")
    c.add_argument("--json", action="store_true")
    c.set_defaults(fn=cmd_check)

    f = sub.add_parser("frames", help="从视频切逐帧（shot 模板用）")
    f.add_argument("video")
    f.add_argument("--start", type=float, default=0.0)
    f.add_argument("--duration", type=float, required=True)
    f.add_argument("--fps", type=int, default=30)
    f.add_argument("--out", required=True)
    f.add_argument("--quality", type=int, default=2)
    f.set_defaults(fn=cmd_frames)

    k = sub.add_parser("calib", help="截图坐标标定图")
    k.add_argument("video")
    k.add_argument("sec", type=float)
    k.add_argument("--rect", default="", help="x,y,w,h 可多组，用 ; 分隔")
    k.add_argument("--out", default=None)
    k.set_defaults(fn=cmd_calib)

    p = sub.add_parser("probe", help="读视频基本信息")
    p.add_argument("video")
    p.set_defaults(fn=cmd_probe)

    a = ap.parse_args(argv)
    return a.fn(a) or 0


if __name__ == "__main__":
    sys.exit(main())
