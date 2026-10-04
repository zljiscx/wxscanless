# -*- coding: utf-8 -*-
"""微信进程管理：识别实例、启动新实例。

微信 4.x 是多进程架构，一个实例会派生出多个同名 Weixin.exe（主进程没有
`--type=`，子进程都有），所以只把主进程算作一个实例。
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import time
from ctypes import wintypes

EXE_NAMES = ("Weixin.exe", "WeChat.exe")
# 子进程携带 --type=<角色>，主进程没有
SUB_TYPE_FLAG = "--type="

# 轮询新主进程出现的粒度与上限
LAUNCH_POLL = 0.1
LAUNCH_TIMEOUT = 20.0

# 启动控制台子程序时隐藏窗口（本模块只用 Win32 原生 API）
CREATE_NO_WINDOW = 0x08000000


_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
_kernel32.OpenProcess.restype = ctypes.c_void_p
_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.CloseHandle.restype = wintypes.BOOL
_kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
_kernel32.ReadProcessMemory.restype = wintypes.BOOL
_kernel32.ReadProcessMemory.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                       ctypes.c_void_p, ctypes.c_size_t,
                                       ctypes.POINTER(ctypes.c_size_t)]
_kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
_kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
_kernel32.Process32FirstW.restype = wintypes.BOOL
_kernel32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_kernel32.Process32NextW.restype = wintypes.BOOL
_kernel32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_kernel32.GetProcessTimes.restype = wintypes.BOOL
_kernel32.GetProcessTimes.argtypes = [ctypes.c_void_p] + [ctypes.c_void_p] * 4
_ntdll.NtQueryInformationProcess.restype = ctypes.c_long
_ntdll.NtQueryInformationProcess.argtypes = [ctypes.c_void_p, ctypes.c_ulong,
                                             ctypes.c_void_p, ctypes.c_ulong,
                                             ctypes.POINTER(ctypes.c_ulong)]

_INVALID_HANDLE = ctypes.c_void_p(-1).value

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010

PTR = ctypes.sizeof(ctypes.c_void_p)          # 4=32位进程视图, 8=64位
# PEB / RTL_USER_PROCESS_PARAMETERS 里 CommandLine 的偏移随位数变化
_OFF_PEB_PARAMS = 0x20 if PTR == 8 else 0x10
_OFF_CMDLINE_LEN = 0x70 if PTR == 8 else 0x40
_OFF_CMDLINE_BUF = 0x78 if PTR == 8 else 0x44


class _PROCESS_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [("Reserved1", ctypes.c_void_p),
                ("PebBaseAddress", ctypes.c_void_p),
                ("Reserved2", ctypes.c_void_p),
                ("Reserved3", ctypes.c_void_p),
                ("UniqueProcessId", ctypes.c_void_p),
                ("Reserved4", ctypes.c_void_p)]


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.POINTER(wintypes.ULONG)),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260)]


def _read_memory(handle, addr, size):
    buf = ctypes.create_string_buffer(size)
    read = ctypes.c_size_t(0)
    ok = _kernel32.ReadProcessMemory(handle, ctypes.c_void_p(addr), buf, size,
                                     ctypes.byref(read))
    if not ok:
        return None
    return buf.raw[:read.value]


def _commandline_of(pid: int) -> str:
    """读指定进程的命令行；读不到返回空串。"""
    handle = _kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ,
                                   False, pid)
    if not handle:
        return ""
    try:
        pbi = _PROCESS_BASIC_INFORMATION()
        ret = ctypes.c_ulong(0)
        status = _ntdll.NtQueryInformationProcess(
            handle, 0, ctypes.byref(pbi), ctypes.sizeof(pbi), ctypes.byref(ret))
        if status != 0 or not pbi.PebBaseAddress:
            return ""
        peb = _read_memory(handle, pbi.PebBaseAddress, 0x100)
        if not peb:
            return ""
        fmt = "<Q" if PTR == 8 else "<I"
        params = ctypes.c_void_p(
            int.from_bytes(peb[_OFF_PEB_PARAMS:_OFF_PEB_PARAMS + PTR], "little"))
        if not params:
            return ""
        pp = _read_memory(handle, params.value, 0x300)
        if not pp:
            return ""
        length = int.from_bytes(pp[_OFF_CMDLINE_LEN:_OFF_CMDLINE_LEN + 2], "little")
        buf_ptr = int.from_bytes(pp[_OFF_CMDLINE_BUF:_OFF_CMDLINE_BUF + PTR], "little")
        if not length or not buf_ptr:
            return ""
        raw = _read_memory(handle, buf_ptr, length)
        if not raw:
            return ""
        return raw.decode("utf-16-le", errors="replace")
    finally:
        _kernel32.CloseHandle(handle)


def _snapshot() -> list:
    """[(pid, ppid, 进程名)]，用 Toolhelp32 快照。"""
    snap = _kernel32.CreateToolhelp32Snapshot(0x00000002, 0)   # TH32CS_SNAPPROCESS
    if not snap or snap == _INVALID_HANDLE:
        return []
    out = []
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        ok = _kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            out.append((entry.th32ProcessID, entry.th32ParentProcessID,
                        entry.szExeFile))
            entry = _PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(entry)
            ok = _kernel32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        _kernel32.CloseHandle(snap)
    return out


def _wx_rows() -> list:
    """[(pid, commandline, ppid)]，只含微信进程。"""
    rows = []
    for pid, ppid, name in _snapshot():
        if name in EXE_NAMES:
            rows.append((pid, _commandline_of(pid), ppid))
    return rows


def _fallback_pids() -> set:
    """最后兜底：按进程名取全部 pid（可能把子进程也算进来）。

    不得再调用 `tasklist` —— 它是控制台程序，GUI 版调用时 Windows 会新建控制台
    窗口，表现为每 2 秒闪一次黑窗。`_snapshot()` 已能列出全部进程，按名字过滤即可。
    """
    return {pid for pid, _ppid, name in _snapshot() if name in EXE_NAMES}


def _main_pids() -> set:
    rows = _wx_rows()
    if rows:
        mains = {pid for pid, cmd, _ppid in rows if cmd and SUB_TYPE_FLAG not in cmd}
        if mains:
            return mains
        # 命令行读不到时按父子关系判断
        wx = {pid for pid, _cmd, _ppid in rows}
        return {pid for pid, _cmd, ppid in rows if ppid not in wx}
    return _fallback_pids()


def _all_pids() -> set:
    rows = _wx_rows()
    if rows:
        return {pid for pid, _cmd, _ppid in rows}
    return _fallback_pids()


def start_time(pid: int) -> float:
    """进程创建时间（Unix 秒）。取不到返回 0。"""
    h = _kernel32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return 0.0
    try:
        creation = wintypes.FILETIME()
        exit_t = wintypes.FILETIME()
        kernel_t = wintypes.FILETIME()
        user_t = wintypes.FILETIME()
        if not _kernel32.GetProcessTimes(h, ctypes.byref(creation),
                                        ctypes.byref(exit_t),
                                        ctypes.byref(kernel_t),
                                        ctypes.byref(user_t)):
            return 0.0
        raw = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
        return raw / 1e7 - 11644473600.0
    finally:
        _kernel32.CloseHandle(h)


def main_pids_ordered() -> list:
    """主进程按启动先后排序。槽位是 net/net_1/net_2... 顺序占用的，
    所以第 i 个主进程就对应第 i 个槽位——退出指定账号时用它定位进程。"""
    return sorted(_main_pids(), key=lambda p: start_time(p) or 0.0)


def count_instances() -> int:
    return len(_main_pids())


def main_pids() -> set:
    return _main_pids()


def pids() -> set:
    return _all_pids()


def _ensure_xweb_dll_path() -> str:
    """把 XWeb 运行时目录补进 PATH，供微信整条进程树解析 xweb_elf.dll。

    WeChatAppEx.exe 把 xweb_elf.dll 作为**静态导入**，而该 DLL 只存在于
    `RadiumWMPF\\<版本>\\extracted\\runtime\\`，存根自己所在的目录里没有。加载器能
    命中它的位置只有两条：当前工作目录（由微信自己设定，并不总是可靠）或 PATH。
    环境变量会被子进程继承，所以在本进程改PATH = 给微信及其全部子孙兜底。

    追加在**末尾**（优先级最低）：runtime\ 里还有 ffmpeg.dll / vulkan-1.dll 等同名
    文件，放在前面反而可能顶掉别处本该加载的版本。幂等；未找到时返回空串。
    """
    from . import paths

    rt = paths.xweb_runtime_dir()
    if not rt:
        return ""
    path = os.environ.get("PATH", "")
    if rt not in path.split(os.pathsep):
        os.environ["PATH"] = (path + os.pathsep + rt) if path else rt
    return rt


def launch(exe_path: str) -> list:
    """启动一个微信实例，返回新出现的主进程 pid 列表（等不到则为空列表）。"""
    if not os.path.isfile(exe_path):
        raise FileNotFoundError(exe_path)
    before = _main_pids()
    _ensure_xweb_dll_path()
    try:
        os.startfile(exe_path)
    except OSError:
        subprocess.Popen(
            [exe_path],
            cwd=os.path.dirname(exe_path),
            creationflags=0x00000008 | 0x00000200 | CREATE_NO_WINDOW,
            # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
    new = []
    end = time.time() + LAUNCH_TIMEOUT
    tick = time.time()
    while time.time() < end:
        tick += LAUNCH_POLL
        gap = tick - time.time()
        if gap > 0:
            time.sleep(gap)
        new = sorted(_main_pids() - before)
        if new:
            break
    return new
