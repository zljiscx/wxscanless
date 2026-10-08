# -*- coding: utf-8 -*-
"""wxprof —— 微信 4.x 多账号登录态管理库。"""
from __future__ import annotations

import hashlib

__version__ = "1.6.4"


def file_md5(path: str) -> str:
    """文件内容 md5；读不到返回空串。"""
    try:
        with open(path, "rb") as f:
            return hashlib.md5(f.read()).hexdigest()
    except OSError:
        return ""