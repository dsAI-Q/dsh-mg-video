# -*- coding: utf-8 -*-
"""
mgvideo.selftest —— 回归自测

把审计中发现过的每个真实缺陷都固化成一条断言，防止以后再犯。
跑法：  python -m mgvideo selftest
退出码 0 = 全过；1 = 有失败。

注意：这一组断言刻意都很小、很快（不到 2 秒），目的是「每次改完都能跑」，
而不是覆盖渲染质量。
"""
import copy
import json
import os
import shutil
import tempfile
import wave

import numpy as np
from PIL import Image

from . import audio, core as C, fx, render


def _tmpdir():
    """不用 tempfile.mkdtemp：受限环境下它建的目录子进程写不进去
    （PermissionError），所以按 MG_TMP 手动建一个普通目录。"""
    base = os.environ.get("MG_TMP") or tempfile.gettempdir()
    for i in range(100):
        d = os.path.join(base, f"mgvideo-selftest-{os.getpid()}-{i}")
        if not os.path.exists(d):
            os.makedirs(d, exist_ok=True)
            return d
    raise RuntimeError("建不出临时目录")


def _wav(path, seconds=0.5, channels=1, sr=48000):
    n = int(sr * seconds)
    t = np.linspace(0, 1, n)
    sig = (np.sin(2 * np.pi * 220 * t) * 8000).astype(np.int16)
    data = np.stack([sig] * channels, axis=1).tobytes()
    with wave.open(path, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(data)


def run(verbose=True):
    tmp = _tmpdir()
    out = []

    def case(name, fn):
        try:
            msg = fn()
            out.append((name, True, msg or ""))
        except AssertionError as e:
            out.append((name, False, str(e) or "断言失败"))
        except Exception as e:
            out.append((name, False, f"{type(e).__name__}: {e}"))

    # 1. seg() 归一化：段内进度必须按区间长度归一
    def c_seg():
        # 旧 bug：max(b-a, 1) 会让 seg(0.5,0.4,0.6) 变成 0.1，动效整体慢 5 倍
        assert abs(C.seg(0.5, 0.4, 0.6) - 0.5) < 1e-9, f"seg 中点应为 0.5，实际 {C.seg(0.5, 0.4, 0.6)}"
        assert C.seg(0.4, 0.4, 0.6) == 0.0
        assert C.seg(0.7, 0.4, 0.6) == 1.0
        # 区间在 0.6 就走满，不能拖到段末
        assert C.seg(0.6, 0.4, 0.6) == 1.0
        assert C.seg(1.0, 1.0, 1.0) in (0.0, 1.0), "零长度区间不该抛错"
        return f"seg(0.5,0.4,0.6)={C.seg(0.5, 0.4, 0.6):.2f}"

    # 2. 缓动函数值域
    def c_ease():
        for f in (C.eo, C.eio, C.clamp):
            for x in (-0.5, 0, 0.5, 1, 1.5):
                v = f(x)
                assert -0.001 <= v <= 1.001, f"{f.__name__}({x})={v} 越界"
        assert abs(C.eo(1) - 1) < 1e-9 and abs(C.eio(0)) < 1e-9
        return "eo/eio/clamp 值域都在 [0,1]"

    # 3. configure 之后缓存必须跟着换（曾经漏清 _SQUARES）
    def c_size_cache():
        C.configure(1920, 1080)
        s1 = C.squares(5).size
        assert s1 == (2400, 1350), f"16:9 方块层尺寸不对：{s1}"
        C.configure(1080, 1920)
        s2 = C.squares(5).size
        assert s2 == (1350, 2400), f"9:16 的方块层尺寸应随画布变，实际 {s2}"
        C.configure(1920, 1080)
        return f"16:9 {s1} → 9:16 {s2}"

    # 4. 墨迹转场跨画布尺寸（曾经 paste 尺寸不匹配直接崩）
    def c_ink_resize():
        C.configure(1280, 720)
        a = Image.new("RGBA", (C.CW, C.CH), (10, 10, 20, 255))
        b = Image.new("RGBA", (C.CW, C.CH), (200, 200, 200, 255))
        fx.xf_ink(a, b, 0.5)
        C.configure(720, 1280)
        a = Image.new("RGBA", (C.CW, C.CH), (10, 10, 20, 255))
        b = Image.new("RGBA", (C.CW, C.CH), (200, 200, 200, 255))
        out = fx.xf_ink(a, b, 0.5)
        assert out.size == (C.CW, C.CH)
        C.configure(1920, 1080)
        return "换尺寸后墨迹转场正常"

    # 5. 镜头震动不能漏透明边（漏了合成后就是黑边）
    def c_shake_edges():
        C.configure(640, 360, 1.0)
        base = Image.new("RGBA", (C.CW, C.CH), (40, 30, 60, 255))
        out = fx.shake(base, 7, amp=14, seed=7)
        for xy in ((0, 0), (C.CW - 1, 0), (0, C.CH - 1), (C.CW - 1, C.CH - 1)):
            assert out.getpixel(xy)[3] == 255, f"{xy} 的 alpha={out.getpixel(xy)[3]}，漏边"
        C.configure(1920, 1080)
        return "四角 alpha=255"

    # 6. 极短音轨不能因为淡入长度越界而崩
    def c_short_audio():
        p = os.path.join(tmp, "short.wav")
        audio.build(0.1, p)
        assert os.path.getsize(p) > 1000
        return "0.1s 音轨可生成"

    # 7. 单声道人声也能混（曾经广播形状不对）
    def c_mono_voice():
        v = os.path.join(tmp, "mono.wav")
        _wav(v, 0.4, channels=1)
        p = os.path.join(tmp, "mix.wav")
        r = audio.build(0.4, p, voice=v)
        assert -25 < r["rms_db"] < -12, f"响度异常 {r['rms_db']}"
        return f"mono 混音 rms={r['rms_db']}dB"

    # 8. 人声文件不存在要给清楚的报错
    def c_missing_voice():
        try:
            audio.build(0.3, os.path.join(tmp, "x.wav"), voice=os.path.join(tmp, "nope.wav"))
        except FileNotFoundError as e:
            assert "不存在" in str(e), f"报错信息不够明确：{e}"
            return "报错点名了人声音轨"
        raise AssertionError("缺失人声没有抛错")

    # 9. shot 模板不能就地改 spec（曾经往 marks 里塞 _src）
    def c_shot_no_mutation():
        d = os.path.join(tmp, "src")
        os.makedirs(d, exist_ok=True)
        Image.new("RGB", (320, 180), (90, 90, 120)).save(os.path.join(d, "0001.jpg"))
        spec = {
            "size": [640, 360], "fps": 10, "background": "none", "transition": "none",
            "scenes": [{
                "template": "shot", "duration": 1.0, "frames_dir": d,
                "crop": [0, 0, 320, 180],
                "marks": [{"type": "magnifier", "rect": [40, 40, 60, 30], "from": 0, "to": 1}],
            }],
        }
        before = json.dumps(spec, sort_keys=True, default=str)
        render._prepare(spec)
        render.frame_image(0, spec)
        after = json.dumps(spec, sort_keys=True, default=str)
        assert before == after, "渲染过程修改了 spec"
        return "spec 未被修改"

    # 10. 空 scenes 要给明确错误，不是 IndexError
    def c_empty():
        try:
            render._prepare({"scenes": []})
        except ValueError as e:
            assert "scenes" in str(e)
            return "空 scenes 报 ValueError"
        raise AssertionError("空 scenes 没报错")

    # 11. 一帧都不能漏：worker 在全新进程状态下也要能出帧
    def c_worker_cold():
        C.configure(1920, 1080)
        render._TIMELINE = None
        spec = {"size": [640, 360], "fps": 10, "background": "none", "transition": "none",
                "scenes": [{"template": "title", "duration": 0.3, "title": "冷启动"}]}
        p = os.path.join(tmp, "0000.png")
        render._render_one((0, p, spec))
        assert os.path.exists(p) and os.path.getsize(p) > 1000
        render._TIMELINE = None
        return "无 _prepare 直接出帧成功"

    for nm, fn in [
        ("seg 归一化", c_seg),
        ("缓动值域", c_ease),
        ("换画布后缓存跟随", c_size_cache),
        ("墨迹转场跨尺寸", c_ink_resize),
        ("镜头震动不漏边", c_shake_edges),
        ("极短音轨", c_short_audio),
        ("单声道人声混音", c_mono_voice),
        ("缺失人声报错", c_missing_voice),
        ("shot 不改 spec", c_shot_no_mutation),
        ("空 scenes 报错", c_empty),
        ("worker 冷启动出帧", c_worker_cold),
    ]:
        case(nm, fn)

    if verbose:
        w = max(len(n) for n, _, _ in out) + 2
        for name, ok, msg in out:
            print(f"  {'ok  ' if ok else 'FAIL'} {name:<{w}} {msg}")
    shutil.rmtree(tmp, ignore_errors=True)
    failed = [n for n, ok, _ in out if not ok]
    if verbose:
        print(f"\n{len(out) - len(failed)} passed, {len(failed)} failed")
    return len(failed) == 0, out
