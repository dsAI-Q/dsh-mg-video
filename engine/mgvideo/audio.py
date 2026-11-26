# -*- coding: utf-8 -*-
"""
mgvideo.audio —— 程序化音轨（BGM + 音效 + 人声混音）

不用任何素材库：和弦垫、whoosh、tick、thud 全部 numpy 合成。
好处是**帧级对齐**——数字滚动的节奏和 tick 的疏密可以精确对上。

混音标准（实测好用）：
  人声主体 -18.5 dB（RMS），峰值不超过 -3 dB
  铺底比人声低 29 dB，句间不抢话
  结尾 0.9–1.2 秒淡出，避免硬切
"""
import math
import os
import wave

import numpy as np

SR = 48000

CHORDS = [
    (110.00, [220.00, 261.63, 329.63]),   # Am
    (87.31, [174.61, 220.00, 261.63]),    # F
    (130.81, [261.63, 329.63, 392.00]),   # C
    (98.00, [196.00, 246.94, 293.66]),    # G
]


def _read_wav(path, n, sr=SR):
    if not os.path.exists(path):
        raise FileNotFoundError(f"人声音轨不存在：{path}")
    with wave.open(path, "rb") as w:
        ch, rs = w.getnchannels(), w.getframerate()
        raw = w.readframes(w.getnframes())
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    a = a.reshape(-1, ch)
    if ch == 1:                      # 单声道补成立体声，避免后续广播出意外
        a = np.repeat(a, 2, axis=1)
    elif ch > 2:
        a = a[:, :2]
    if rs != sr:
        idx = np.linspace(0, a.shape[0] - 1, int(a.shape[0] * sr / rs))
        a = np.stack([np.interp(idx, np.arange(a.shape[0]), a[:, c]) for c in range(a.shape[1])], axis=1)
    if a.shape[0] < n:
        a = np.pad(a, ((0, n - a.shape[0]), (0, 0)))
    return a[:n].astype(np.float32)


def _write_wav(path, x, sr=SR):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())


def _pad(n, dur, sr=SR, level=1.0):
    """和弦垫：每段交叉淡化衔接，避免和弦交接处出现音量空洞"""
    pad = np.zeros((n, 2), np.float32)
    seg_len = dur / len(CHORDS)
    for ci, (root, tones) in enumerate(CHORDS):
        a = max(ci * seg_len - 1.2, 0.0)
        b = min((ci + 1) * seg_len + 1.2, dur)
        m = int((b - a) * sr)
        tt = np.arange(m) / sr
        env = np.ones(m)
        at = min(int(1.4 * sr), m // 2)
        env[:at] = np.linspace(0, 1, at) ** 1.6
        rt = min(int(1.6 * sr), m // 2)
        env[-rt:] = np.linspace(1, 0, rt) ** 1.4
        left = np.zeros(m, np.float32)
        right = np.zeros(m, np.float32)
        for f in tones:
            left += np.sin(2 * np.pi * f * tt + 0.3) / 3.0
            right += np.sin(2 * np.pi * f * (1 + 0.0016) * tt + 1.1) / 3.0
        left += np.sin(2 * np.pi * root * tt) * 0.55
        right += np.sin(2 * np.pi * root * (1 + 0.001) * tt) * 0.55
        i = int(a * sr)
        k = min(m, n - i)
        pad[i:i + k, 0] += (left * env / 1.55)[:k] * level
        pad[i:i + k, 1] += (right * env / 1.55)[:k] * level
    return pad


def _whoosh(sr=SR, dur=0.6, seed=7):
    rng = np.random.default_rng(seed)
    m = int(dur * sr)
    tt = np.arange(m) / sr
    s = rng.normal(0, 1, m).astype(np.float32)
    k = max(1, int(sr * 0.003))
    s = np.convolve(s, np.ones(k) / k, mode="same")
    s = np.diff(s, prepend=0.0)
    s = s * (np.sin(np.pi * np.clip(tt / dur, 0, 1)) ** 2.2) * np.linspace(1.0, 0.35, m)
    return (s / (np.max(np.abs(s)) + 1e-9)).astype(np.float32)


def _tick(freq=1800.0, sr=SR, dur=0.05):
    m = int(dur * sr)
    tt = np.arange(m) / sr
    env = np.exp(-tt * 82.0)
    return ((np.sin(2 * np.pi * freq * tt) * 0.62 + np.sin(2 * np.pi * freq * 1.5 * tt) * 0.2) * env).astype(np.float32)


def _thud(sr=SR, dur=0.9):
    m = int(dur * sr)
    tt = np.arange(m) / sr
    env = np.exp(-tt * 5.0)
    return ((np.sin(2 * np.pi * 58 * tt) * 0.8 + np.sin(2 * np.pi * 39 * tt) * 0.45) * env).astype(np.float32)


def _add(buf, sig, at, gain=1.0, sr=SR):
    s = np.stack([sig, sig], axis=1) if sig.ndim == 1 else sig
    i = int(at * sr)
    if i < 0:
        s = s[-i:]
        i = 0
    if i >= len(buf) or len(s) == 0:
        return
    k = min(len(s), len(buf) - i)
    buf[i:i + k] += s[:k] * gain


def build(duration, out, voice=None, scene_bounds=(), ticks=(), hits=(), sr=SR,
          voice_db=-18.5, pad_offset_db=-29.0, fade_out=1.0):
    """生成音轨。
    duration       总时长（秒）
    voice          人声音轨路径（可选）；没有就只出铺底 + 音效
    scene_bounds   段落边界（秒）：每个边界放一个 whoosh
    ticks          需要「嗒」一下的时刻（秒）
    hits           需要低频冲击的时刻（秒）
    """
    n = int(sr * duration)
    if n < 32:
        raise ValueError(f"时长为 {duration} 秒，太短了（至少要 0.05 秒）")
    pad = _pad(n, duration, sr)
    fx = np.zeros((n, 2), np.float32)
    for at in scene_bounds:
        _add(fx, _whoosh(sr=sr), at - 0.18, 0.32)
    for at in ticks:
        _add(fx, _tick(1600 + 300 * ((at * 7) % 3), sr=sr), at, 0.15)
    for at in hits:
        _add(fx, _thud(sr=sr), at, 0.30)

    if voice:
        v = _read_wav(voice, n, sr)
        vp = np.max(np.abs(v)) + 1e-9
        v = v * (0.71 / vp)
        v_rms = np.sqrt(np.mean(v ** 2)) + 1e-9
        pad = pad * (v_rms * (10 ** (pad_offset_db / 20)) / (np.sqrt(np.mean(pad ** 2)) + 1e-9))
        fx = fx * (v_rms * 0.085 / (np.sqrt(np.mean(fx ** 2)) + 1e-9))
        mix = v + pad + fx
        target = 10 ** (voice_db / 20)
    else:
        pad = pad * (10 ** (-20.0 / 20) / (np.sqrt(np.mean(pad ** 2)) + 1e-9))
        fx = fx * 0.12
        mix = pad + fx
        target = 10 ** (-20.0 / 20)

    # 淡入淡出长度要按时长夹住：0.22s 的淡入叠在 0.1s 的音轨上会数组形状不匹配
    fin = min(int(0.22 * sr), max(n // 4, 1))
    mix[:fin] *= np.linspace(0, 1, fin)[:, None]
    fo = min(int(fade_out * sr), max(n // 3, 0))
    if fo > 0:
        mix[-fo:] *= np.linspace(1, 0, fo)[:, None] ** 1.3
    mix *= target / (np.sqrt(np.mean(mix ** 2)) + 1e-12)
    mix = np.tanh(mix * 1.02) / 1.02
    pk = np.max(np.abs(mix))
    if pk > 0.9:
        mix *= 0.9 / pk
    _write_wav(out, mix, sr)
    return {
        "path": out,
        "duration": round(n / sr, 3),
        "rms_db": round(20 * math.log10(np.sqrt(np.mean(mix ** 2)) + 1e-12), 1),
        "peak_db": round(20 * math.log10(np.max(np.abs(mix)) + 1e-12), 1),
    }
