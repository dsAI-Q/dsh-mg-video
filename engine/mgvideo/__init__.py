# -*- coding: utf-8 -*-
"""mgvideo —— 用一份 JSON 清单渲染 MG 动画视频（Pillow 逐帧 + ffmpeg）"""
__version__ = "0.1.0"

from . import core, fx, templates, shot, audio, render  # noqa: F401

__all__ = ["core", "fx", "templates", "shot", "audio", "render", "__version__"]
