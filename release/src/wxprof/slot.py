# -*- coding: utf-8 -*-
"""实例槽位：占用判据、目标槽位、host 读写、monitordata 扫描。

微信 4.x 运行时目录为 %APPDATA%\\Tencent\\xwechat\\net / net_1 / net_2 / net_3（最多 4 个实例），
每个实例占一个。本模块回答四件事：

1. 占用判据：微信对 <槽位>\\config.ini 持有 share=0 独占句柄 —— 我们能独占打开即空闲（只读）。
   对**槽位目录**做同样的事无效（net_1 恒"占用"）。
2. 新实例落在下标最小的空闲槽位。
3. 槽位当前账号：kvcomm\\monitordata_<uin>_<X>，登录成功后才写入，停在登录页时会被清掉。
4. host 目录读写：账号级网络路由文件，需在实例启动前就位。
"""
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
    """能否以 share=0 独占打开该文件（只开句柄再关闭，纯读）。

    成功 ⇒ 没有任何其它进程持有它的句柄 ⇒ 该槽位空闲。
    """
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
    """该槽位**历史出现过**的所有非零 uin（`monitordata_<uin>_<X>`）。

    ⚠️ 这是**累积**的：微信只覆盖不删除，同一槽位先后登录过不同账号就会留下
    多条（实测 net 与 net_1 都各有 2 个）。**不要**用它判断"现在是哪个账号"，
    否则会把历史账号误判成在线 —— 请用 `current_uin()`。
    本函数只用于诊断/兜底。
    """
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
    """该槽位**当前**登录账号的 uin；没有（未登录 / 停在登录页 / 已登出）返回 0。

    为什么不能"有非零 uin 就算在线"：`monitordata_<uin>` 微信**从不清理**，
    同一槽位先后登录不同账号会留下多条 —— 直接取用会把历史账号当成在线，
    这正是"退出一个账号后界面仍显示两个在线"的根因。

    判据（两级）：
      1. 取 mtime 最新的非零 uin —— 它是"最后一次登录的账号"；
      2. 若它和 `monitordata_0`（"当前无账号"标记）的 mtime **相差不到
         `_MON_CLUSTER` 秒**，说明这是**登出时的成对写入** ⇒ 判为无账号。
    运行中时当前账号的 mtime 来自"登录成功那一刻"，而 `_0` 停留在此前某次
    登出时刻，两者相隔通常以分钟计 ⇒ 能稳定区分。
    """
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
    """哪个槽位**当前**住着这个 uin；不确定时返回空串。

    先按"当前账号"精确匹配；再退回"该 uin 只出现在唯一槽位"的无歧义情形。
    若同一 uin 在多个槽位留下痕迹（历史累积）⇒ 无法判定，返回空 ——
    **宁可这一次不采 host，也不能把 A 槽位的 host 存进 B 账号的档案**（串档）。
    """
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
    """当前已登录的账号：{uin: 槽位}。

    两个条件缺一不可：
      · 槽位**被占用**（`config.ini` 独占句柄）—— 说明有实例在跑；
      · `current_uin()` 给出**当前**账号 —— 说明这个实例真的登录了（不是停在登录页），
        且排除掉历史残留的 uin（微信从不清理它们）。
    """
    out = {}
    for name in INSTANCE_DIRS:
        if not slot_busy(env, name):
            continue
        u = current_uin(env, name)
        if u:
            out[u] = name
    return out


def wxid_in_slot(env, name: str, uin: int) -> str:
    """从槽位 `kvcomm\\key_<uin>_*.statistic` 的内容里解出 wxid（兜底）。

    多实例在线时 live `global_config` 只反映"最后一个写入者"，无法枚举全部在线
    账号；而这类统计文件的内容含 wxid 明文，实测映射与账号一致。
    """
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
    """`host_path`（host 目录**本身**）的内容指纹 {相对路径: md5}；不存在返回 {}。

    槽位与档案里的 host 目录结构相同，同一个函数通用。
    """
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
    """把 `src_host` 的内容**清空后**原样镜像到 `dst_host`。返回文件数。

    两个方向共用同一实现：
        · 采集：槽位的 host → 档案的 host
        · 登录：档案的 host → 槽位的 host
    先清空目标再复制 —— 残留上一个账号的文件会导致路由串档。
    源目录不存在时**不做任何改动**（避免把好目标误清空）。
    """
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
