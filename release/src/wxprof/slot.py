# -*- coding: utf-8 -*-
"""实例槽位：占用判据、目标槽位、host 读写、monitordata 扫描。"""
from __future__ import annotations

import ctypes
import hashlib
import os
import re
import shutil
import time
from ctypes import wintypes

# 微信的实例槽位，按下标即"第几个实例"；数量不固定，运行时动态枚举
_SLOT_RE = re.compile(r"^net(?:_(\d+))?$")


def instance_names(env) -> list:
    """枚举微信实例槽位名（net / net_1 / ...），支持任意数量。"""
    root = slot_root(env)
    max_idx = 0
    if os.path.isdir(root):
        for fn in os.listdir(root):
            m = _SLOT_RE.match(fn)
            if m and m.group(1):
                max_idx = max(max_idx, int(m.group(1)))
    # 覆盖到"已存在最大下标 + 3"，保证微信能继续开新实例
    names = ["net"]
    for i in range(1, max_idx + 4):
        names.append("net_%d" % i)
    return names

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


# Restart Manager：反查占用某文件的进程 PID（供槽位与进程精准对应）
_CCH_RM_SESSION_KEY = 32


class _RM_UNIQUE_PROCESS(ctypes.Structure):
    _fields_ = [("dwProcessId", ctypes.c_uint32),
                ("ProcessStartTime", ctypes.c_uint64)]


class _RM_PROCESS_INFO(ctypes.Structure):
    _fields_ = [("Process", _RM_UNIQUE_PROCESS),
                ("strAppName", ctypes.c_wchar * 256),
                ("strServiceShortName", ctypes.c_wchar * 64),
                ("ApplicationType", ctypes.c_int),
                ("AppStatus", ctypes.c_uint32),
                ("TSSessionId", ctypes.c_uint32),
                ("bRestartable", ctypes.c_int)]


def file_owner_pids(path: str) -> list:
    """占用该文件的进程 PID（Restart Manager 反查，失败返回 []）。"""
    try:
        rm = ctypes.windll.LoadLibrary("rstrtmgr.dll")
    except Exception:
        return []
    try:
        rm.RmStartSession.restype = ctypes.c_uint32
        rm.RmStartSession.argtypes = [ctypes.POINTER(ctypes.c_uint32),
                                      ctypes.c_uint32, ctypes.c_wchar_p]
        rm.RmRegisterResources.restype = ctypes.c_uint32
        rm.RmRegisterResources.argtypes = [ctypes.c_uint32, ctypes.c_uint32,
                                           ctypes.POINTER(ctypes.c_wchar_p),
                                           ctypes.c_uint32, ctypes.c_void_p,
                                           ctypes.c_uint32, ctypes.c_void_p]
        rm.RmGetList.restype = ctypes.c_uint32
        rm.RmGetList.argtypes = [ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32),
                                 ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p,
                                 ctypes.POINTER(ctypes.c_uint32)]
        rm.RmEndSession.restype = ctypes.c_uint32
        rm.RmEndSession.argtypes = [ctypes.c_uint32]
    except Exception:
        return []
    sess = ctypes.c_uint32(0)
    key = ctypes.create_unicode_buffer(_CCH_RM_SESSION_KEY + 1)
    if rm.RmStartSession(ctypes.byref(sess), 0, key) != 0:
        return []
    try:
        files = (ctypes.c_wchar_p * 1)(path)
        if rm.RmRegisterResources(sess, 1, files, 0, None, 0, None) != 0:
            return []
        needed = ctypes.c_uint32(0)
        filled = ctypes.c_uint32(0)
        reason = ctypes.c_uint32(0)
        rm.RmGetList(sess, ctypes.byref(needed), ctypes.byref(filled), None,
                     ctypes.byref(reason))
        n = needed.value
        if n == 0:
            return []
        buf = (ctypes.c_byte * (n * ctypes.sizeof(_RM_PROCESS_INFO)))()
        filled.value = n
        if rm.RmGetList(sess, ctypes.byref(needed), ctypes.byref(filled), buf,
                        ctypes.byref(reason)) != 0:
            return []
        arr = (_RM_PROCESS_INFO * filled.value).from_buffer_copy(buf)
        return [p.Process.dwProcessId for p in arr if p.Process.dwProcessId]
    except Exception:
        return []
    finally:
        try:
            rm.RmEndSession(sess)
        except Exception:
            pass


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
    """当前被占用的槽位名（按枚举顺序）。"""
    return [n for n in instance_names(env) if slot_busy(env, n)]


def free_slots(env) -> list:
    """当前空闲的槽位名。"""
    return [n for n in instance_names(env) if not slot_busy(env, n)]


def free_slot(env, want_host: str = "") -> str:
    """新实例会落到的槽位 = 下标最小的空闲槽位；没有返回空串。

    ★ 必须与微信的分配规则**完全一致**（下标最小），不能自作聪明地按线路
    "挑一个同源的" —— 铺进非最小空闲槽位的 host，微信根本不会去读那个槽位，
    结果就是 host 白铺、登录页切二维码（实测小号被铺到 net_2 后跳扫码）。
    """
    for name in instance_names(env):
        if not slot_busy(env, name):
            return name
    return ""


def pick_slot(env, want_host: str = "") -> tuple:
    """选目标槽位，返回 (槽位名, 是否强制)。强制=True =槽位已满以外的情况。"""
    name = free_slot(env)
    return (name, False) if name else ("", False)


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


def monitordata_locked_uin(env, name: str) -> int:
    """被微信占用（无法独占打开）的 monitordata 文件对应的 uin；无则 0。"""
    d = kvcomm_dir(env, name)
    if not os.path.isdir(d):
        return 0
    try:
        names = os.listdir(d)
    except OSError:
        return 0
    for fn in names:
        m = _MON.match(fn)
        if not m or m.group(1) == "0":
            continue
        p = os.path.join(d, fn)
        if exclusive_open_ok(p):        # 能独占打开 = 未被占用
            continue
        return int(m.group(1))          # 被占用 → 就是当前账号
    return 0


_OWNER_TTL = 2.0
_OWNER_CACHE = {}


def slot_owner_pid(env, name: str) -> int:
    """该槽位的微信进程 PID（monitordata 锁优先，退化时由 config.ini 锁反查）；无法确定返回 0。"""
    d = kvcomm_dir(env, name)
    names = []
    if os.path.isdir(d):
        try:
            names = os.listdir(d)
        except OSError:
            names = []
    now = time.time()
    cached = _OWNER_CACHE.get(name)
    if cached and cached[1] > now:
        return cached[0]
    owner = 0
    # 主路：被锁的 monitordata 文件（账号级，登录后才出现）
    for fn in names:
        m = _MON.match(fn)
        if not m or m.group(1) == "0":
            continue
        p = os.path.join(d, fn)
        if exclusive_open_ok(p):
            continue
        pids = file_owner_pids(p)
        owner = pids[0] if pids else 0
        if owner:
            break
    # 退化路：槽位一直被占用的 config.ini（停在登录页、未登账号也能反查）
    if not owner and slot_busy(env, name):
        lf = lock_file(env, name)
        if os.path.isfile(lf):
            pids = file_owner_pids(lf)
            owner = pids[0] if pids else 0
    if owner:
        _OWNER_CACHE[name] = (owner, now + _OWNER_TTL)
    else:
        _OWNER_CACHE.pop(name, None)
    return owner


def _uin_in_name(fn: str) -> int:
    """从含 uin 的文件名里取出 uin，取不到返回 0。"""
    if fn.startswith("monitordata_"):
        m = _MON.match(fn)
        return int(m.group(1)) if m else 0
    if fn.startswith("key_reportnow_"):
        idx = 2
    elif fn.startswith("key_") or fn.startswith("reportnow_"):
        idx = 1
    else:
        idx = 0
    parts = fn.split("_")
    if len(parts) <= idx:
        return 0
    try:
        return int(parts[idx])
    except ValueError:
        return 0


def current_uin(env, name: str) -> int:
    """该槽位当前被哪个账号占用（uin）；定不了返回 0。

    单 uin 占用直接判定；多个 uin 同时占用时改用含 uin 的其它文件消歧。
    """
    if not slot_busy(env, name):
        return 0
    d = kvcomm_dir(env, name)
    if not os.path.isdir(d):
        return 0
    try:
        names = os.listdir(d)
    except OSError:
        return 0
    locked = set()
    for fn in names:
        m = _MON.match(fn)
        if not m or m.group(1) == "0":
            continue
        if not exclusive_open_ok(os.path.join(d, fn)):
            locked.add(int(m.group(1)))
    if not locked:
        return 0
    if len(locked) == 1:
        return next(iter(locked))
    for fn in names:
        if fn.startswith("monitordata_"):
            continue
        u = _uin_in_name(fn)
        if u and u in locked and not exclusive_open_ok(os.path.join(d, fn)):
            return u
    return 0


def slot_of_uin(env, uin: int) -> str:
    """哪个槽位当前住着这个 uin；不确定时返回空串。"""
    if not uin:
        return ""
    for name in instance_names(env):
        if current_uin(env, name) == uin:
            return name
    hits = [n for n in instance_names(env) if uin in monitordata_uins(env, n)]
    if len(hits) == 1:
        return hits[0]
    return ""


def online_uins(env) -> dict:
    """当前已登录的账号：{uin: 槽位}。"""
    out = {}
    for name in instance_names(env):
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


def slot_logged_in(env, name: str) -> bool:
    """该槽位是否**已登录进主界面**（判在线用，靠登录窗口是否消失）。"""
    from . import process, ui
    pids = process.main_pids_ordered()
    pid = slot_owner_pid(env, name)
    if pid and pid in pids:
        try:
            return ui.login_window_of(pid) is None
        except Exception:                       # noqa: BLE001
            return False
    order = [n for n in instance_names(env) if slot_busy(env, n)]
    for i, nm in enumerate(order):
        if nm != name or i >= len(pids):
            continue
        try:
            return ui.login_window_of(pids[i]) is None
        except Exception:                       # noqa: BLE001
            return False
    return False


def summary(env) -> dict:
    """给界面/日志用的一份槽位概览。"""
    out = {}
    for name in instance_names(env):
        out[name] = {
            "exists": os.path.isdir(slot_dir(env, name)),
            "busy": slot_busy(env, name),
            "uins": monitordata_uins(env, name),      # 历史累积（诊断用）
            "current": current_uin(env, name),        # 当前账号（判在线用）
            "locked_uin": monitordata_locked_uin(env, name),  # 被占用的那份
        }
    return out
