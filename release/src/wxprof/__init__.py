# -*- coding: utf-8 -*-
"""wxprof —— 微信 4.x 多账号登录态管理库（Windows 7/10/11）。

    paths     安装 / 运行 / 数据目录探测
    process   进程识别与启动（多进程架构下只认主进程）
    slot      实例槽位：占用判据、目标槽位、host 读写、monitordata 扫描
    mmkv      global_config 解密与解析（纯 Python AES-128-CFB）
    avatar    头像下载 + GDI+ 转 PNG
    vault     账号档案库
    collect   采集链路
    watcher   实时监控
    login     免扫码登录主流程
    ui        登录窗口 UI 自动化（后台操作，不动真实鼠标）
"""
from __future__ import annotations

import hashlib

__version__ = "1.5.1"


def file_md5(path: str) -> str:
    """文件内容 md5；读不到返回空串。"""
    try:
        with open(path, "rb") as f:
            return hashlib.md5(f.read()).hexdigest()
    except OSError:
        return ""