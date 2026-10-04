# -*- coding: utf-8 -*-
"""实例槽位：占用判据、目标槽位、host 读写、monitordata 扫描。"""
from __future__ import annotations

import ctypes
import hashlib
import os
import re
import shutil
from ctypes import wintypes

# 微信的实例槽位，按下标即"第几个实例"；上限 4
INSTANCE_DIRS = ("net", "net_1", "net_2", "net_3")
MAX_INSTANCES = len(INSTANCE_DIRS)

# 本工具在 host 目录里留下的临时后缀，采集时跳过
TEMP_SUFFIX = (".livebak", ".parked", ".hotbak", ".probe_bak", ".tmp")

_MON = re.compile(r"^monitordata_(\d+)_\d+$")
_KEY = re.compile(r"^key_(\d+)_\d+_")
_WXID = re.compile(rb"wxid_[0-9a-zA-Z_-]{5,}")


_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.CreateFileW.restype = ctypes.c_void_p
_k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                             ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                             ctypes.c_void_p]
_k32.CloseHandle.argtypes = [ctypes.c_void_p]
_INVALID = ctypes.c_void_p(-1).value
_GENERIC_READ = 0x80000000
_OPEN_EXISTING = 3


def exclusive_open_ok(path: str) -> bool:
    """能否以 share=0 独占打开该文件（只开句柄再关闭，纯读）。"""
    h = _k32.CreateFileW(path, _GENERIC_READ, 0, None, _OPEN_EXISTING, 0, None)
    if h and h != _INVALID:
        _k32.CloseHandle(h)
        return True
    return False


def slot_root(env) -> str:
    return env.appdata_dir or ""


def slot_dir(env, name: str) -> str:
    return os.path.join(slot_root(env), name)


def lock_file(env, name: str) -> str:
    """占用标记文件：微信对它持有 share=0 独占句柄。"""
    return os.path.join(slot_dir(env, name), "config.ini")


def slot_busy(env, name: str) -> bool:
    """该槽位是否被某个运行中的实例占用。"""
    p = lock_file(env, name)
    if not os.path.isfile(p):
        return False
    return not exclusive_open_ok(p)


def busy_slots(env) -> list:
    """当前被占用的槽位名（顺序同 INSTANCE_DIRS）。"""
    return [n for n in INSTANCE_DIRS if slot_busy(env, n)]


def free_slots(env) -> list:
    """当前空闲的槽位名。"""
    return [n for n in INSTANCE_DIRS if not slot_busy(env, n)]


def free_slot(env) -> str:
    """新实例会落到的槽位 = 下标最小的空闲槽位；没有空闲位返回空串。"""
    for name in INSTANCE_DIRS:
        if not slot_busy(env, name):
            return name
    return ""


def kvcomm_dir(env, name: str) -> str:
    return os.path.join(slot_dir(env, name), "kvcomm")


def monitordata_uins(env, name: str) -> list:
    """该槽位历史出现过的所有非零 uin（累积值，仅用于诊断与兜底）。"""
    d = kvcomm_dir(env, name)
    if not os.path.isdir(d):
        return []
    out = []
    try:
        names = os.listdir(d)
    except OSError:
        return []
    for fn in names:
        m = _MON.match(fn)
        if m and m.group(1) != "0":
            out.append(int(m.group(1)))
    return sorted(set(out))


def _mon_entries(env, name: str) -> tuple:
    """返回 (`monitordata_0` 的 mtime, [(uin, mtime), ...])。"""
    d = kvcomm_dir(env, name)
    if not os.path.isdir(d):
        return 0.0, []
    try:
        names = os.listdir(d)
    except OSError:
        return 0.0, []
    zero = 0.0
    cands = []
    for fn in names:
        m = _MON.match(fn)
        if not m:
            continue
        try:
            mt = os.path.getmtime(os.path.join(d, fn))
        except OSError:
            continue
        if m.group(1) == "0":
            zero = max(zero, mt)
        else:
            cands.append((int(m.group(1)), mt))
    return zero, cands


# monitordata_0 与某 uin 的 mtime 相差在该秒数内 ⇒ 视为同一次写入
_MON_CLUSTER = 3.0


def current_uin(env, name: str) -> int:
    """该槽位当前登录账号的 uin；没有（未登录 / 停在登录页 / 已登出）返回 0。"""
    zero, cands = _mon_entries(env, name)
    if not cands:
        return 0
    best, best_mt = 0, 0.0
    for uin, mt in cands:
        if mt > best_mt:
            best, best_mt = uin, mt
    if not best:
        return 0
    if zero <= 0:
        return best                     # 没有 `_0` 标记 ⇒ 确实有账号
    if abs(best_mt - zero) < _MON_CLUSTER:
        return 0                        # 与 `_0` 同批写入 ⇒ 那是登出，不是在线
    return best


def slot_of_uin(env, uin: int) -> str:
    """哪个槽位当前住着这个 uin；不确定时返回空串。"""
    if not uin:
        return ""
    for name in INSTANCE_DIRS:
        if current_uin(env, name) == uin:
            return name
    hits = [n for n in INSTANCE_DIRS if uin in monitordata_uins(env, n)]
    if len(hits) == 1:
        return hits[0]
    return ""


def online_uins(env) -> dict:
    """当前已登录的账号：{uin: 槽位}。"""
    out = {}
    for name in INSTANCE_DIRS:
        if not slot_busy(env, name):
            continue
        u = current_uin(env, name)
        if u:
            out[u] = name
    return out


def wxid_in_slot(env, name: str, uin: int) -> str:
    """从槽位的统计文件内容里解出 wxid（兜底用）。"""
    d = kvcomm_dir(env, name)
    if not os.path.isdir(d):
        return ""
    try:
        names = os.listdir(d)
    except OSError:
        return ""
    for fn in names:
        m = _KEY.match(fn)
        if not m or int(m.group(1)) != uin:
            continue
        p = os.path.join(d, fn)
        try:
            if os.path.getsize(p) > 4 * 1024 * 1024:
                continue
            with open(p, "rb") as f:
                data = f.read()
        except OSError:
            continue
        hit = _WXID.search(data)
        if hit:
            return hit.group(0).decode("ascii", "replace")
    return ""


def host_dir(env, name: str) -> str:
    """槽位的 host 目录（账号级网络路由文件）。"""
    return os.path.join(slot_dir(env, name), "host")


def fp_digest(fp: dict) -> str:
    """把 {相对路径: md5} 压成一个指纹串（用于"变没变"的比较）。"""
    if not fp:
        return ""
    joined = "|".join("%s=%s" % (k, fp[k]) for k in sorted(fp))
    return hashlib.md5(joined.encode("utf-8")).hexdigest()


def host_fingerprint_of(host_path: str) -> dict:
    """host 目录的内容指纹 {相对路径: md5}；不存在返回 {}。"""
    out = {}
    if not os.path.isdir(host_path):
        return out
    for dirpath, _dirs, files in os.walk(host_path):
        rel_dir = os.path.relpath(dirpath, host_path)
        for fn in files:
            if fn.endswith(TEMP_SUFFIX):
                continue
            p = os.path.join(dirpath, fn)
            try:
                with open(p, "rb") as f:
                    data = f.read()
            except OSError:
                continue
            rel = fn if rel_dir == "." else os.path.join(rel_dir, fn)
            out[rel.replace(os.sep, "/")] = hashlib.md5(data).hexdigest()
    return out


def host_fingerprint(env, name: str) -> dict:
    """槽位 host 目录的内容指纹。"""
    return host_fingerprint_of(host_dir(env, name))


def mirror_host(src_host: str, dst_host: str) -> int:
    """把 src_host 的内容清空后原样镜像到 dst_host。返回文件数。"""
    n = 0
    if not os.path.isdir(src_host):
        return n
    if os.path.isdir(dst_host):
        shutil.rmtree(dst_host, ignore_errors=True)
    os.makedirs(dst_host, exist_ok=True)
    for dirpath, _dirs, files in os.walk(src_host):
        rel_dir = os.path.relpath(dirpath, src_host)
        for fn in files:
            if fn.endswith(TEMP_SUFFIX):
                continue
            s = os.path.join(dirpath, fn)
            rel = fn if rel_dir == "." else os.path.join(rel_dir, fn)
            d = os.path.join(dst_host, rel)
            os.makedirs(os.path.dirname(d), exist_ok=True)
            try:
                shutil.copy2(s, d)
                n += 1
            except OSError:
                try:
                    with open(s, "rb") as f:
                        data = f.read()
                    with open(d, "wb") as f:
                        f.write(data)
                    n += 1
                except OSError:
                    pass
    return n


def install_host(env, name: str, src_host: str) -> int:
    """把档案的 host 目录铺进目标槽位（登录前调用）。返回写入文件数。"""
    return mirror_host(src_host, host_dir(env, name))


def summary(env) -> dict:
    """给界面/日志用的一份槽位概览。"""
    out = {}
    for name in INSTANCE_DIRS:
        out[name] = {
            "exists": os.path.isdir(slot_dir(env, name)),
            "busy": slot_busy(env, name),
            "uins": monitordata_uins(env, name),      # 历史累积（诊断用）
            "current": current_uin(env, name),        # 当前账号（判在线用）
        }
    return out
