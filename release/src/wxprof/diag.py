# -*- coding: utf-8 -*-
"""诊断日志：模块内的异常与关键判断直接落盘（不依赖界面日志窗口）。"""
from __future__ import annotations

import os
import threading
import time

_lock = threading.Lock()
_path = ""
_recent = {}                        # (tag, text) -> 上次落盘时刻（去重）


def project_root() -> str:
    """程序根（data 与 logs 的父目录）。"""
    return os.environ.get("WXPROF_ROOT") or os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def set_path(p: str) -> None:
    """由界面层指定日志文件路径（与界面日志同一个文件）。"""
    global _path
    _path = p or ""


def warn(tag: str, text: str, dedupe: float = 30.0) -> None:
    """把一条诊断信息落盘；相同内容在 dedupe 秒内只记一次；写失败静默。"""
    key = (tag, text)
    now = time.time()
    with _lock:
        if dedupe and now - _recent.get(key, 0.0) < dedupe:
            return
        _recent[key] = now
        if len(_recent) > 2000:
            _recent.clear()
    p = _path or os.path.join(project_root(), "logs", "wechat_launcher.log")
    try:
        with _lock:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "a", encoding="utf-8") as f:
                f.write("[%s.%03d] [%s] %s\n"
                        % (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
                           int(now * 1000) % 1000, tag, text))
    except Exception:                           # noqa: BLE001
        pass
