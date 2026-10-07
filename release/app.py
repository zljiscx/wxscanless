# -*- coding: utf-8 -*-
"""图形界面（tkinter）。窄长竖条窗口，可拖动、贴屏幕右边缘自动隐藏；账号卡片支持登录与删除。"""
from __future__ import annotations

import math
import os
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox

# 运行根：RELEASE_DIR = release 目录；ROOT = 程序根（data 与 logs 的父目录）
RELEASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("WXPROF_ROOT") or os.path.dirname(RELEASE_DIR)
DATA_DIR = os.path.join(ROOT, "data")
LOG_PATH = os.path.join(ROOT, "logs", "wechat_launcher.log")


CREATE_NO_WINDOW = 0x08000000


def log_line(text: str) -> None:
    """统一日志落盘：logs\wechat_launcher.log。"""
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        n = time.time()
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write("[%s.%03d] %s\n"
                    % (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(n)),
                       int(n * 1000) % 1000, text))
    except Exception:                           # noqa: BLE001
        pass


def _silence_console_windows() -> None:
    import platform as _platform
    import subprocess as _subprocess

    # ① 替换 platform._syscmd_ver 的版本号取值
    try:
        _traced = []

        def _syscmd_ver_no_cmd(system="", release="", version="",
                               supported_platforms=("win32", "win16", "dos")):
            """与标准库同签名，但完全不执行外部命令。"""
            if not _traced:                     # 首次调用留一次调用栈存档
                _traced.append(True)
                try:
                    import traceback
                    log_line("已拦下 platform._syscmd_ver（本会执行 cmd /c ver），"
                                 "调用来源：\n" + "".join(traceback.format_stack()))
                except Exception:               # noqa: BLE001
                    pass
            try:
                w = sys.getwindowsversion()
                version = "%d.%d.%d" % (w.major, w.minor, w.build)
                release = version
            except Exception:                   # noqa: BLE001
                pass
            return (system or "Windows"), release, version

        _platform._syscmd_ver = _syscmd_ver_no_cmd
    except Exception as e:                      # noqa: BLE001
        log_line("接管 platform._syscmd_ver 失败：%r" % (e,))

    # ② subprocess.Popen —— 控制台子进程一律隐藏窗口
    try:
        if not getattr(_subprocess, "_wxprof_no_window", False):
            _orig = _subprocess.Popen

            class _QuietPopen(_orig):
                def __init__(self, *args, **kwargs):
                    kwargs["creationflags"] = (
                        (kwargs.get("creationflags") or 0) | CREATE_NO_WINDOW)
                    super().__init__(*args, **kwargs)

            _subprocess.Popen = _QuietPopen
            _subprocess._wxprof_no_window = True
    except Exception as e:                      # noqa: BLE001
        log_line("包装 subprocess.Popen 失败：%r" % (e,))


_silence_console_windows()

for _p in (os.path.join(RELEASE_DIR, "ext"),      # 第三方依赖目录
           os.path.join(RELEASE_DIR, "src")):     # 业务模块
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from wxprof import login, paths, process, slot, ui, vault, watcher     # noqa: E402
from wxprof import __version__                                          # noqa: E402
from wxprof import avatar as avatar_mod                                 # noqa: E402

_orig_excepthook = sys.excepthook


def _on_uncaught(exc_type, exc, tb) -> None:
    import traceback
    log_line("未捕获异常：\n" + "".join(traceback.format_exception(exc_type, exc, tb)))
    try:
        _orig_excepthook(exc_type, exc, tb)
    except Exception:
        pass
    try:
        messagebox.showerror("微信多账号免扫码登录器 — 出错了",
                             "%s: %s\n\n日志文件：\n%s" % (exc_type.__name__, exc, LOG_PATH))
    except Exception:
        pass


sys.excepthook = _on_uncaught
if hasattr(threading, "excepthook"):
    def _on_thread_uncaught(args):
        _on_uncaught(args.exc_type, args.exc_value, args.exc_traceback)
    threading.excepthook = _on_thread_uncaught


def _tk_report(self, exc, val, tb):
    _on_uncaught(exc, val, tb)


tk.Tk.report_callback_exception = _tk_report

if getattr(sys, "frozen", False):
    class _LogStream:
        def write(self, s):
            if s and s.strip():
                log_line(s)
        def flush(self):
            pass
        def isatty(self):
            return False
    if sys.stdout is None:
        sys.stdout = _LogStream()
    if sys.stderr is None:
        sys.stderr = _LogStream()

BG = "#f5f6f7"
CARD = "#ffffff"
ACCENT = "#07c160"
TEXT = "#1a1a1a"
SUB = "#8a8a8a"
LINE = "#e6e6e6"
GRAY = "#c9c9c9"
GRAY_OFFLINE = "#bdbdbd"                # 离线头像的占位灰
RED = "#e64340"
THUMB = "#c4c4c4"                       # 细滚动条的滑块
THUMB_ACTIVE = "#9e9e9e"                # 按下/拖动时的滑块

AVATAR = avatar_mod.DISPLAY_SIZE        # 头像边长 = 落盘尺寸
# 账号卡片的固定高度
CARD_H = 118
NICK_LINES = 2                          # 昵称最多显示两行
WIN_W = 360                             # 窗口宽（固定，不可调）
WIN_H = 760                             # 窗口高
MIN_H = 480                             # 屏幕太矮时的兜底高度
SCROLLBAR_W = 6                         # 列表滚动条宽度
POLL_MS = 800

# 贴边隐藏只对屏幕右边缘生效
DOCK_EDGE = 18          # 窗口距屏幕**右**边缘多少像素内 → 自动贴边
DOCK_HOT = 8            # 鼠标离屏幕右边缘多少像素内算"靠近"
DOCK_POLL = 120         # 鼠标位置轮询间隔（ms）
DOCK_SLIDE_MS = 12      # 滑动动画帧间隔（ms）

PALETTE = ("#5b8ff9", "#5ad8a6", "#f6bd16", "#e8684a", "#6dc8ec",
           "#9270ca", "#ff9d4d", "#269a99", "#ff99c3", "#7f8fa6")

# 单实例互斥体与事件名
MUTEX_NAME = "Local\\wxprof_wechat_launcher_mutex"
SHOW_EVENT = "Local\\wxprof_wechat_launcher_show"

_K32 = None
_MUTEX_HANDLE = None
_SHOW_EVENT_HANDLE = None   # 已有实例用它接收显示窗口信号


def _init_win32():
    """按需加载 kernel32 并声明函数签名。"""
    global _K32
    if _K32 is None:
        import ctypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateMutexW.restype = ctypes.c_void_p
        k.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                   ctypes.c_wchar_p]
        k.CreateEventW.restype = ctypes.c_void_p
        k.CreateEventW.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_wchar_p]
        k.SetEvent.argtypes = [ctypes.c_void_p]
        k.WaitForSingleObject.restype = ctypes.c_ulong
        k.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        _K32 = k
    return _K32


# 扩展窗口样式：切这两个位决定窗口是否出现在任务栏
GWL_EXSTYLE = -20
WS_EX_APPWINDOW = 0x00040000        # 出现在任务栏
WS_EX_TOOLWINDOW = 0x00000080       # 不出现在任务栏
_SWP_NOACTIVATE = 0x0010
_SWP_NOMOVE = 0x0002
_SWP_NOSIZE = 0x0001
_SWP_NOZORDER = 0x0004
_SWP_FRAMECHANGED = 0x0020

_U32 = None


def _init_user32():
    """按需加载 user32 并声明窗口样式相关函数签名（这些不在 kernel32 里）。"""
    global _U32
    if _U32 is None:
        import ctypes
        from ctypes import wintypes
        u = ctypes.WinDLL("user32", use_last_error=True)
        u.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        u.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
        u.SetWindowLongPtrW.restype = ctypes.c_ssize_t
        u.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int,
                                        ctypes.c_ssize_t]
        u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   wintypes.UINT]
        u.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        u.GetAncestor.restype = wintypes.HWND
        _U32 = u
    return _U32


def _taskbar_visible(win, show: bool) -> bool:
    """把窗口在任务栏里的显示开关切换掉（不重建窗口、不影响可见性）。"""
    try:
        import ctypes
        from ctypes import wintypes
        u = _init_user32()
        root = u.GetAncestor(wintypes.HWND(win.winfo_id()), 2)  # GA_ROOT
        h = wintypes.HWND(int(root or win.winfo_id()))
        ex = u.GetWindowLongPtrW(h, GWL_EXSTYLE)
        if show:
            ex = (ex | WS_EX_APPWINDOW) & ~WS_EX_TOOLWINDOW
        else:
            ex = (ex | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
        u.SetWindowLongPtrW(h, GWL_EXSTYLE, ctypes.c_ssize_t(ex))
        u.SetWindowPos(h, None, 0, 0, 0, 0,
                       _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOZORDER
                       | _SWP_FRAMECHANGED | _SWP_NOACTIVATE)
        return True
    except Exception as e:                      # noqa: BLE001
        log_line("切换任务栏显示失败：%r" % (e,))
        return False


def _claim_single_instance() -> bool:
    """单实例闸门。True = 可以启动；False = 已有实例在跑，本进程应当退出。"""
    global _MUTEX_HANDLE, _SHOW_EVENT_HANDLE
    try:
        import ctypes
        k = _init_win32()
        h = k.CreateMutexW(None, 0, MUTEX_NAME)
        if not h:
            log_line("单实例检查：互斥体创建失败，跳过检查继续启动")
            return True
        if ctypes.get_last_error() == 183:      # ERROR_ALREADY_EXISTS
            ev = k.CreateEventW(None, 0, 0, SHOW_EVENT)
            if ev:
                k.SetEvent(ev)                  # 通知已有实例把窗口显示出来
            log_line("检测到已有实例在运行 —— 已通知它显示窗口，本进程退出")
            return False
        _MUTEX_HANDLE = h
        _SHOW_EVENT_HANDLE = k.CreateEventW(None, 0, 0, SHOW_EVENT)
        return True
    except Exception as e:                      # noqa: BLE001
        log_line("单实例检查异常（忽略并继续启动）：%r" % (e,))
        return True


def _enable_dpi() -> None:
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:                           # noqa: BLE001  Win7 无 shcore
        pass


def short(s: str, n: int = 22) -> str:
    return s if len(s) <= n else s[:n] + "..."


def color_of(wxid: str) -> str:
    return PALETTE[sum(ord(c) for c in wxid) % len(PALETTE)]


class ThinScrollbar(tk.Canvas):
    """宽度可自定义的细滚动条（自绘）。"""

    W = SCROLLBAR_W                 # 宽度（用户指定 6px）
    MIN_THUMB = 24                  # 滑块最小高度，太短不好抓

    def __init__(self, master, command=None) -> None:
        super().__init__(master, width=self.W, highlightthickness=0, bd=0,
                         bg=BG)
        self._command = command
        self._first, self._last = 0.0, 1.0
        self._from_y = None                     # 拖动起点（鼠标 y）
        self._from_first = 0.0                  # 拖动起点的 first 值
        self._thumb = self.create_rectangle(0, 0, 0, 0, fill=THUMB, outline="")
        self.bind("<Configure>", lambda e: self._redraw())
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)

    def set(self, first, last) -> None:
        """由目标控件的 yscrollcommand 调用，刷新滑块位置。"""
        self._first, self._last = float(first), float(last)
        self._redraw()

    def _span(self):
        h = max(1, self.winfo_height())
        top = int(self._first * h)
        bot = int(self._last * h)
        if bot - top < self.MIN_THUMB:
            bot = min(h, top + self.MIN_THUMB)
            top = max(0, bot - self.MIN_THUMB)
        return h, top, bot

    def _redraw(self) -> None:
        if self.winfo_height() <= 1:
            return
        _h, top, bot = self._span()
        self.coords(self._thumb, 0, top, self.W, bot)

    def _on_press(self, event) -> None:
        h, top, bot = self._span()
        if top <= event.y <= bot:               # 按在滑块上 → 开始拖
            self._from_y, self._from_first = event.y, self._first
        else:                                   # 按在轨道上 → 直接跳过去
            self._moveto(event.y / float(h))
        self.itemconfig(self._thumb, fill=THUMB_ACTIVE)

    def _on_drag(self, event) -> None:
        if self._from_y is None:
            return
        h, _top, _bot = self._span()
        self._moveto(self._from_first + (event.y - self._from_y) / float(h))

    def _on_release(self, event) -> None:
        self._from_y = None
        self.itemconfig(self._thumb, fill=THUMB)

    def _moveto(self, first) -> None:
        if self._command:
            self._command("moveto", max(0.0, min(1.0, first)))


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.withdraw()
        self._busy = False
        self._booting = True                    # 启动加载中：登录按钮不可用
        self.rows = {}
        self._avatar_cache = {}                 # (wxid, 灰度?) -> (mtime, PhotoImage)
        self._gray_failed = set()               # 灰度生成失败过的 wxid

        self._cfg_job = None                    # 移动去抖
        self._anim_job = None                   # 滑动动画
        self._dock = None                       # None / "right"
        self._shown_pos = None
        self._hidden_pos = None
        self._docked_shown = False              # 贴边后当前是否处于"滑出"状态
        self._in_taskbar = True                 # 隐藏时连任务栏也不显示

        self._dragging = False                  # 正在拖动（拖时不判贴边）
        self._drag_off = None                   # 按下点相对窗口左上角的偏移

        self.title("微信多账号免扫码登录器 v%s" % __version__)
        self._set_icon()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        h = min(WIN_H, max(MIN_H, sh - 120))    # 屏幕太矮才缩高
        self.geometry("%dx%d+%d+%d" % (WIN_W, h, max(0, sw - WIN_W - 40),
                                       max(40, (sh - h) // 2 - 40)))
        self.resizable(False, False)
        self.configure(bg=BG)

        self.vault = vault.Vault(DATA_DIR)
        self.env = None
        self.watcher = None

        self._build_header()
        self._build_body()
        self._build_footer()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self.bind("<Configure>", self._on_configure)
        self._cfg_job = self.after(420, self._maybe_dock)
        self.after(DOCK_POLL, self._poll_pointer)

        self._bind_drag()                       # 窗口内任意位置可拖（含标题区）
        self.refresh()                          # 初始刷新
        self.deiconify()                        # 布局就绪，正式显示
        self.after(60, self._boot)              # 窗口显示后再做启动加载
        self.after(POLL_MS, self._tick)
        self.after(500, self._poll_show_event)  # 接收第二个实例的唤醒请求

    @property
    def _locked(self) -> bool:
        """界面当前是否"不可操作"：启动加载中，或正在执行登录任务。"""
        return self._busy or self._booting

    def _boot(self) -> None:
        """窗口已经在屏幕上了，才开始加载运行环境（后台线程，不卡界面）。"""
        def job() -> None:
            ok = False
            try:
                ok = _preflight()
            except Exception as e:              # noqa: BLE001
                log_line("启动自检异常：%r" % (e,))
            env = None
            err = None
            try:
                env = paths.detect()
            except Exception as e:              # noqa: BLE001
                err = e
                log_line("环境探测异常：%r" % (e,))
            # 界面更新一律回主线程
            try:
                self.after(0, lambda: self._boot_done(env, err, ok))
            except Exception:                   # noqa: BLE001
                pass                            # 窗口已经关掉了，忽略

        threading.Thread(target=job, daemon=True).start()

    def _boot_done(self, env, err, ok: bool) -> None:
        """加载完成：落日志、启监控、状态行改"已就绪"、恢复登录按钮。"""
        try:
            if not self.winfo_exists():         # 加载期间窗口被关掉了
                return
        except Exception:                       # noqa: BLE001
            return
        self.env = env
        if env is not None:
            log_line("微信环境探测：ok=%s" % env.ok)
            for _line in env.summary().splitlines():
                log_line("    " + _line)
            try:
                self.watcher = watcher.Watcher(
                    env, self.vault, interval=0.6, on_event=self._on_capture,
                    on_log=self._on_watch_log, on_change=self._on_status_change)
                self.watcher.start()
                login.clean_temp(env)           # 上次遗留的 .livebak 清掉
            except Exception as e:              # noqa: BLE001
                log_line("监控启动失败：%r" % (e,))
        else:
            log_line("环境探测失败：没有拿到环境（env=None）")
            if err is not None:
                messagebox.showerror("环境探测失败", str(err), parent=self)
        if not ok:
            _warn_no_uia(self)
        self._booting = False
        self.var_status.set("已就绪")
        self.refresh()
        try:
            self._update_status()
        except Exception as e:                  # noqa: BLE001
            log_line("首轮状态刷新失败：%r" % (e,))

    def _set_icon(self) -> None:
        """设置窗口标题栏与任务栏图标。"""
        ico = os.path.join(RELEASE_DIR, "assets", "icon.ico")
        if not os.path.isfile(ico):
            return
        try:
            self.iconbitmap(ico)
        except Exception as e:                  # noqa: BLE001
            log_line("设置窗口图标失败：%r" % (e,))

    def _build_header(self) -> None:
        head = tk.Frame(self, bg=BG)
        head.pack(fill="x", padx=16, pady=(12, 2))
        tk.Label(head, text="微信多账号免扫码登录器", bg=BG, fg=TEXT,
                 font=("Microsoft YaHei UI", 15, "bold")).pack(side="left")
        tk.Label(head, text="v%s" % __version__, bg=BG, fg=SUB,
                 font=("Microsoft YaHei UI", 9)).pack(side="left", padx=5,
                                                       pady=(3, 0))
        self.var_env = tk.StringVar(value="正在加载运行环境…")
        tk.Label(self, textvariable=self.var_env, bg=BG, fg=SUB, anchor="w",
                 justify="left", wraplength=WIN_W - 40,
                 font=("Microsoft YaHei UI", 9)).pack(fill="x", padx=16)

    def _build_body(self) -> None:
        wrap = tk.Frame(self, bg=BG)
        wrap.pack(fill="both", expand=True, padx=16, pady=(8, 0))

        bar = tk.Frame(wrap, bg=BG)
        bar.pack(fill="x")
        tk.Label(bar, text="账号", bg=BG, fg=TEXT,
                 font=("Microsoft YaHei UI", 11, "bold")).pack(side="left")
        self.var_count = tk.StringVar(value="")
        tk.Label(bar, textvariable=self.var_count, bg=BG, fg=SUB,
                 font=("Microsoft YaHei UI", 9)).pack(side="left", padx=8)

        self.list = tk.Frame(wrap, bg=BG)
        self.list.pack(fill="both", expand=True, pady=(6, 0))
        canvas = tk.Canvas(self.list, bg=BG, highlightthickness=0, width=1)
        sb = ThinScrollbar(self.list, command=canvas.yview)
        self.inner = tk.Frame(canvas, bg=BG)
        self.inner.bind("<Configure>",
                        lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        self._canvas_win = canvas.create_window((0, 0), window=self.inner,
                                                anchor="nw")
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfigure(self._canvas_win,
                                                   width=e.width))
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")         # ← 先分配滚动条
        canvas.pack(side="left", fill="both", expand=True)
        canvas.bind_all("<MouseWheel>",
                        lambda e: canvas.yview_scroll(int(-1 * (e.delta / 120)),
                                                      "units"))

    def _build_footer(self) -> None:
        foot = tk.Frame(self, bg=BG)
        foot.pack(fill="x", padx=16, pady=(8, 12))
        self.var_status = tk.StringVar(
            value="正在加载运行环境…（此过程不能登录账号）")
        tk.Label(foot, textvariable=self.var_status, bg=BG, fg=SUB, anchor="w",
                 justify="left", wraplength=WIN_W - 40,
                 font=("Microsoft YaHei UI", 9)).pack(fill="x")

        box = tk.Frame(foot, bg=BG)
        box.pack(fill="x", pady=(8, 0))
        self.btn_all = tk.Button(box, text="一键登录", command=self.on_login_all,
                                 relief="flat", pady=7, bg=ACCENT, fg="#ffffff",
                                 activebackground="#06ad56",
                                 font=("Microsoft YaHei UI", 10, "bold"),
                                 highlightbackground=ACCENT, highlightthickness=1)
        self.btn_all.pack(side="left", fill="x", expand=True)
        self.btn_all._primary, self.btn_all._danger = True, False
        self._set_btn(self.btn_all, True)
        tk.Button(box, text="刷新", command=self.refresh, relief="flat", padx=14,
                  pady=7, bg="#ffffff", fg=TEXT, activebackground="#f0f0f0",
                  font=("Microsoft YaHei UI", 10), highlightbackground="#dcdcdc",
                  highlightthickness=1).pack(side="left", padx=(6, 0))

    def _btn(self, parent, text, cmd, primary=False, danger=False):
        """卡片上的小按钮（竖排在卡片右侧）。"""
        b = tk.Button(parent, text=text, command=cmd, relief="flat", width=6,
                      pady=2, font=("Microsoft YaHei UI", 9), highlightthickness=1)
        b._primary, b._danger = primary, danger
        b.pack(side="top", pady=2)
        return b

    def _set_btn(self, b, enabled: bool) -> None:
        use = enabled and not self._locked
        b.config(state=tk.NORMAL if use else tk.DISABLED)
        if b._primary:
            b.config(bg=ACCENT if use else GRAY, fg="#ffffff" if use else "#f2f2f2",
                     highlightbackground=ACCENT if use else GRAY)
        else:
            b.config(bg="#ffffff" if use else "#f7f7f7",
                     fg=(RED if b._danger else TEXT) if use else GRAY,
                     highlightbackground="#dcdcdc" if use else "#ebebeb")

    def refresh(self) -> None:
        for w in self.inner.winfo_children():
            w.destroy()
        self.rows = {}
        if self.env is None:
            # 加载中显示"正在加载…"
            tk.Label(self.inner,
                     text=("正在加载运行环境，此过程不能登录账号…"
                           if self._booting else "未检测到微信环境"),
                     bg=BG, fg=SUB).pack(pady=30)
            return
        accs = sorted(self.vault.list_accounts(), key=lambda a: a.wxid)
        self.var_count.set("共 %d 个" % len(accs))
        if not accs:
            empty = tk.Frame(self.inner, bg=CARD, highlightthickness=1,
                             highlightbackground=LINE)
            empty.pack(fill="x", pady=6)
            tk.Label(empty, text="还没有账号", bg=CARD, fg=TEXT,
                     font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w",
                                                                   padx=14, pady=(12, 2))
            tip = tk.Label(empty,
                           text="直接用微信登录一个账号即可 —— 后台监控会自动把它"
                                "收进档案（config + host + 头像），之后就能一键免扫码登录。",
                           bg=CARD, fg=SUB, anchor="w", justify="left",
                           font=("Microsoft YaHei UI", 9), wraplength=200)
            tip.pack(fill="x", padx=14, pady=(0, 12))
            tip.bind("<Configure>",
                     lambda e, lbl=tip: self._fit_wrap(lbl, e))
            self._update_status()
            return
        for acc in accs:
            self._row(acc)
        self._update_status()

    def _avatar_image(self, acc, gray: bool = False):
        """加载头像 PNG；gray=True 用于离线账号（优先读灰度副本）。"""
        p = self.vault.avatar_path(acc.wxid)
        if gray:
            gp = self.vault.avatar_gray_path(acc.wxid)
            if (not os.path.isfile(gp) and os.path.isfile(p)
                    and acc.wxid not in self._gray_failed):
                if not avatar_mod.to_gray_png(p, gp):
                    self._gray_failed.add(acc.wxid)
            if os.path.isfile(gp):
                p = gp
        if not os.path.isfile(p):
            return None
        try:
            mt = os.path.getmtime(p)
        except OSError:
            return None
        key = (acc.wxid, gray)
        hit = self._avatar_cache.get(key)
        if hit and hit[0] == mt:
            return hit[1]
        try:
            img = tk.PhotoImage(file=p)
        except Exception:                       # noqa: BLE001
            return None
        # 尺寸不符时用整数倍缩小
        if img.width() > AVATAR:
            k = max(1, int(math.ceil(img.width() / float(AVATAR))))
            if k > 1:
                try:
                    img = img.subsample(k)
                except Exception:               # noqa: BLE001
                    pass
        self._avatar_cache[key] = (mt, img)
        return img

    def _paint_avatar(self, cv: tk.Canvas, acc, online: bool) -> None:
        """按在线状态把头像画到画布上：在线彩色、离线灰。"""
        cv.delete("all")
        img = self._avatar_image(acc, gray=not online)
        if img is not None:
            # 1:1 居中
            cv.create_image(AVATAR // 2, AVATAR // 2, image=img)
            cv._img = img                       # 防止被回收
            return
        # 还没有头像文件：首字母占位
        cv.create_oval(1, 1, AVATAR - 1, AVATAR - 1,
                       fill=color_of(acc.wxid) if online else GRAY_OFFLINE,
                       outline="")
        ch = (acc.nickname or acc.name or "?")[:1]
        cv.create_text(AVATAR // 2, AVATAR // 2, text=ch, fill="#ffffff",
                       font=("Microsoft YaHei UI", 30, "bold"))

    def _avatar(self, parent, acc, online: bool) -> tk.Canvas:
        cv = tk.Canvas(parent, width=AVATAR, height=AVATAR, bg=CARD,
                       highlightthickness=0)
        self._paint_avatar(cv, acc, online)
        return cv

    def _row(self, acc) -> None:
        row = tk.Frame(self.inner, bg=CARD, highlightthickness=1,
                       highlightbackground=LINE, height=CARD_H)
        row.pack(fill="x", pady=4)
        row.pack_propagate(False)

        # 头像（左）：按当前在线状态画（离线灰/在线彩）
        on_now = acc.wxid in self._status_online()
        cv = self._avatar(row, acc, on_now)
        cv.pack(side="left", padx=(12, 10), pady=12)

        # 按钮（右，竖排）
        right = tk.Frame(row, bg=CARD)
        right.pack(side="right", padx=(6, 12), pady=12)
        b_login = self._btn(right, "登录", lambda a=acc: self.on_login(a),
                            primary=True)
        b_del = self._btn(right, "删除", lambda a=acc: self.on_delete(a),
                          danger=True)

        mid = tk.Frame(row, bg=CARD)
        mid.pack(side="left", fill="x", expand=True, pady=12)
        # 昵称：在线绿加粗 / 离线灰常规，自动折行最多两行
        nick_fg = ACCENT if on_now else SUB
        nick_full = acc.nickname or acc.name or acc.wxid
        lbl_nick = tk.Label(mid, text=nick_full, bg=CARD, fg=nick_fg,
                            anchor="w", justify="left",
                            font=self._nick_font(on_now), wraplength=140)
        lbl_nick._full_text = nick_full       # 原文留底：折行/裁剪都基于它重算
        self._clip_nick(lbl_nick)             # 先按时初始宽度裁一次，避免闪出第三行
        lbl_nick.pack(fill="x")
        mid.bind("<Configure>",
                 lambda e, lbl=lbl_nick: self._fit_wrap(lbl, e))
        tk.Label(mid, text=short(acc.wxid, 24), bg=CARD, fg=SUB, anchor="w",
                 font=("Microsoft YaHei UI", 8)).pack(fill="x")
        status = tk.Label(mid, text="检测中...", bg=CARD, fg=SUB, anchor="w",
                          font=("Microsoft YaHei UI", 9))
        status.pack(fill="x", pady=(3, 0))

        self.rows[acc.wxid] = {"acc": acc, "status": status,
                               "login": b_login, "del": b_del,
                               "avatar": cv, "online": on_now,
                               "nick": lbl_nick, "nick_fg": nick_fg}

    @staticmethod
    def _nick_font(online: bool):
        """昵称字体：在线加粗，离线常规字重。"""
        return ("Microsoft YaHei UI", 11, "bold") if online \
            else ("Microsoft YaHei UI", 11)

    def _fit_wrap(self, lbl, event) -> None:
        """把 Label 的换行宽度对齐容器的实际宽度（长文本自动折行）。"""
        try:
            w = max(60, int(event.width))
            if getattr(lbl, "_full_text", None) is not None:
                self._clip_nick(lbl, w)
            if int(lbl.cget("wraplength")) != w:
                lbl.config(wraplength=w)
        except Exception:                       # noqa: BLE001
            pass

    def _clip_nick(self, lbl, width: int = 0) -> None:
        """昵称最多占两行，超出部分用省略号收尾。"""
        try:
            if not width:
                width = int(lbl.cget("wraplength"))
            text = lbl._full_text
            f = tkfont.Font(root=lbl, font=lbl.cget("font"))
            limit = width * NICK_LINES
            if f.measure(text) <= limit:
                shown = text
            else:
                ell = "…"
                ell_w = f.measure(ell)
                acc = 0
                shown = text
                for i, ch in enumerate(text):
                    acc += f.measure(ch)
                    if acc + ell_w > limit:
                        shown = text[:i].rstrip() + ell
                        break
            if lbl.cget("text") != shown:
                lbl.config(text=shown)
        except Exception:                       # noqa: BLE001
            pass

    def _on_capture(self, res: dict) -> None:
        """监控线程采到新登录态（在工作线程里回调）。"""
        title = res.get("title") or res.get("wxid") or ""
        what = "、".join(res.get("changed") or [])
        verb = "已收录新账号" if res.get("new") else "已更新登录态"
        self.after(0, lambda: self._set("%s「%s」（%s）" % (verb, title, what)))
        self.after(0, self.refresh)

    def _on_watch_log(self, text: str) -> None:
        self.after(0, lambda: self.var_status.set(text))

    def _on_status_change(self, st: dict) -> None:
        """监控线程发现在线状态变了 → 主线程立刻重画（不等轮询）。"""
        self.after(0, self._update_status)

    def _tick(self) -> None:
        try:
            self._update_status()
        except Exception:                       # noqa: BLE001
            pass
        self.after(POLL_MS, self._tick)

    def _status_online(self) -> dict:
        return (self.watcher.status.get("online") or {}) if self.watcher else {}

    def _update_status(self) -> None:
        if self.env is None:
            return
        online = self._status_online()
        n = int(self.watcher.status.get("instances", 0)) if self.watcher else 0
        accs = self.vault.list_accounts()          # 每轮只读一次档案目录
        titles = {a.wxid: (a.nickname or a.wxid) for a in accs}
        names = [titles.get(w, short(w, 8)) for w in online]
        if self.env.ok:
            self.var_env.set("微信 %s · %d 个实例 · %s"
                             % (self.env.version or "?", n,
                                ("在线：" + "、".join(names)) if names
                                else "暂无账号在线"))
        else:
            # 环境没探测全时只显示"未发现微信环境"
            self.var_env.set("未发现微信环境")

        for _w, w in self.rows.items():
            acc = w["acc"]
            on = acc.wxid in online
            ready = self.vault.ready(acc.wxid)
            if on:
                txt, fg = "在线 · 槽位 %s" % online[acc.wxid], ACCENT
            elif ready:
                txt, fg = "离线 · 可一键登录", SUB
            else:
                txt, fg = "离线 · 登录态不完整", SUB
            # 仅在线状态变化时重画头像
            if w.get("online") != on:
                w["online"] = on
                self._paint_avatar(w["avatar"], acc, on)
            nfg = ACCENT if on else SUB        # 昵称：在线绿+粗 / 离线灰+常规
            if w.get("nick_fg") != nfg:
                w["nick_fg"] = nfg
                w["nick"].config(fg=nfg, font=self._nick_font(on))
                # 字重变化会改变字宽，需重新裁剪
                self._clip_nick(w["nick"])
            w["status"].config(text=txt, fg=fg)
            self._set_btn(w["login"], not on and ready)
            self._set_btn(w["del"], not on)
        # 「一键登录」的可用条件
        can_all = any((a.wxid not in online) and self.vault.ready(a.wxid)
                      for a in accs)
        locked = self._locked
        self.btn_all.config(state=tk.NORMAL if (can_all and not locked)
                            else tk.DISABLED,
                            bg=ACCENT if (can_all and not locked) else GRAY,
                            highlightbackground=(ACCENT if (can_all and not locked)
                                                 else GRAY))

    def _bind_drag(self) -> None:
        """让窗口内任意位置都能按住拖动（按钮与滚动条除外）。"""
        if getattr(self, "_wxprof_drag", False):
            return
        self._wxprof_drag = True
        self.bind("<Button-1>", self._drag_start, add="+")
        self.bind("<B1-Motion>", self._drag_move, add="+")
        self.bind("<ButtonRelease-1>", self._drag_end, add="+")

    def _drag_start(self, event) -> None:
        """按下左键：记录"按下点相对窗口左上角"的偏移，开始拖动。"""
        if isinstance(event.widget, (tk.Button, ThinScrollbar)):
            return                              # 按钮/滚动条上不拖窗口
        if self._anim_job:                      # 停下正在跑的贴边滑动动画
            try:
                self.after_cancel(self._anim_job)
            except Exception:                   # noqa: BLE001
                pass
            self._anim_job = None
        # 拖动即视为离开贴边态
        self._dock = None
        self._shown_pos = self._hidden_pos = None
        self._docked_shown = False
        self._set_taskbar(True)
        self._dragging = True
        self._drag_off = (event.x_root - self.winfo_x(),
                          event.y_root - self.winfo_y())

    def _drag_move(self, event) -> None:
        if not self._dragging or self._drag_off is None:
            return
        self.geometry("+%d+%d" % (event.x_root - self._drag_off[0],
                                  event.y_root - self._drag_off[1]))

    def _drag_end(self, event) -> None:
        if not self._dragging:
            return
        self._dragging = False
        self._drag_off = None
        self._on_configure()                    # 松手后重新判一次贴边

    def _on_configure(self, event=None) -> None:
        if event is not None and event.widget is not self:
            return
        if self._cfg_job:
            try:
                self.after_cancel(self._cfg_job)
            except Exception:                   # noqa: BLE001
                pass
        self._cfg_job = self.after(420, self._maybe_dock)

    def _geometry_now(self):
        return (self.winfo_x(), self.winfo_y(),
                self.winfo_width(), self.winfo_height(),
                self.winfo_screenwidth(), self.winfo_screenheight())

    def _maybe_dock(self) -> None:
        """窗口停稳后判断：贴着屏幕右边缘就进入贴边态。"""
        self._cfg_job = None
        if self._anim_job or self._dragging:    # 滑动动画 / 拖动过程中不要干扰
            return
        x, y, w, h, sw, sh = self._geometry_now()
        if w <= 1 or h <= 1:                    # 尚未真正布局
            return
        if x + w >= sw - DOCK_EDGE:
            if self._dock != "right":
                self._enter_dock(y, w, sw)
        elif self._dock:                        # 被拖离右边缘 → 解除贴边
            self._dock = None
            self._shown_pos = self._hidden_pos = None
            self._docked_shown = False
            self._set_taskbar(True)

    def _enter_dock(self, y: int, w: int, sw: int) -> None:
        """进入右侧贴边态：完全滑出屏幕，并把窗口从任务栏摘掉。"""
        self._dock = "right"
        self._shown_pos = (sw - w, y)
        self._hidden_pos = (sw + 2, y)          # 整个窗口挪到屏幕右外侧
        self._docked_shown = False
        self._set_taskbar(False)
        self._slide_to(*self._hidden_pos)

    def _slide_to(self, tx: int, ty: int) -> None:
        if self._anim_job:
            try:
                self.after_cancel(self._anim_job)
            except Exception:                   # noqa: BLE001
                pass
            self._anim_job = None

        def step():
            x, y = self.winfo_x(), self.winfo_y()
            dx, dy = tx - x, ty - y
            if abs(dx) <= 2 and abs(dy) <= 2:
                self.geometry("+%d+%d" % (tx, ty))
                self._anim_job = None
                return
            nx = x + (dx // 2 if abs(dx) > 2 else dx)
            ny = y + (dy // 2 if abs(dy) > 2 else dy)
            self.geometry("+%d+%d" % (nx, ny))
            self._anim_job = self.after(DOCK_SLIDE_MS, step)

        step()

    def _poll_pointer(self) -> None:
        try:
            self._pointer_tick()
        except Exception:                       # noqa: BLE001
            pass
        self.after(DOCK_POLL, self._poll_pointer)

    def _set_taskbar(self, show: bool) -> None:
        """贴边隐藏时窗口在任务栏里也一并消失；滑出时恢复。"""
        if self._in_taskbar == show:
            return
        if _taskbar_visible(self, show):
            self._in_taskbar = show

    def _pointer_tick(self) -> None:
        if self._dock != "right":
            return
        mx, my = self.winfo_pointerxy()
        x, y, w, h, sw, sh = self._geometry_now()
        # 鼠标靠近屏幕右边缘 ⇒ 滑出显示
        hot = mx >= sw - DOCK_HOT
        inside = x <= mx <= x + w and y <= my <= y + h

        if hot and not self._docked_shown:
            self._docked_shown = True
            self._set_taskbar(True)
            self._slide_to(*self._shown_pos)
            return
        if self._docked_shown and not inside:
            # 鼠标一离开窗口立即隐藏，不做等待
            self._docked_shown = False
            self._slide_to(*self._hidden_pos)
            self._set_taskbar(False)

    def _poll_show_event(self) -> None:
        """轮询"请显示窗口"信号（第二个实例被单实例闸门拦下时发的）。"""
        h = _SHOW_EVENT_HANDLE
        if h:
            try:
                import ctypes
                if _init_win32().WaitForSingleObject(
                        ctypes.c_void_p(h), 0) == 0:
                    self._undock_and_focus()
            except Exception:                   # noqa: BLE001
                pass
        self.after(500, self._poll_show_event)

    def _undock_and_focus(self) -> None:
        """把窗口从贴边状态收回屏幕中央并置顶。"""
        log_line("收到第二个实例的唤醒请求，把窗口显示到前台")
        try:
            if self._dock or self._anim_job:
                self._dock = None
                self._shown_pos = self._hidden_pos = None
                self._docked_shown = False
                self._set_taskbar(True)
                sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
                w, h = self.winfo_width(), self.winfo_height()
                self._slide_to(max(0, sw - w - 60),
                               max(40, (sh - h) // 2 - 40))
            self.deiconify()
            self.lift()
            try:
                self.attributes("-topmost", True)
                self.after(600, lambda: self.attributes("-topmost", False))
            except Exception:                   # noqa: BLE001
                pass
            self.focus_force()
        except Exception as e:                  # noqa: BLE001
            log_line("唤醒窗口失败：%r" % (e,))

    def _set(self, text: str) -> None:
        self.var_status.set(text)
        self.update_idletasks()

    def _finish(self, note: str = "") -> None:
        self._busy = False
        self.refresh()
        self._set(note or "就绪")

    def _run(self, work, note: str = "") -> None:
        """后台线程执行 work；期间暂停监控，避免两边同时写档案。"""
        self._busy = True
        if self.watcher:
            self.watcher.pause()
        self._set(note or "处理中...")
        self._update_status()

        def job():
            err = ""
            try:
                work()
            except Exception as e:              # noqa: BLE001
                err = "出错了：%s" % e
                log_line("任务异常：%r" % (e,))
            finally:
                if self.watcher:
                    self.watcher.resume()
            self.after(0, lambda: self._finish(err))
        threading.Thread(target=job, daemon=True).start()

    def _log_login(self, text: str) -> None:
        """登录过程的日志同时落日志文件与界面状态行。"""
        try:
            log_line("[登录] %s" % str(text).strip())
        except Exception:                       # noqa: BLE001
            pass
        try:
            self.after(0, lambda t=str(text): self._set(t))
        except Exception:                       # noqa: BLE001
            pass

    def _do_login(self, acc) -> dict:
        """登录一个账号。"""
        with ui.ui_scope():
            return login.login_account(self.env, self.vault, acc,
                                       log=self._log_login)

    def on_login(self, acc) -> None:
        if self._locked:
            return
        if acc.wxid in self._status_online():
            messagebox.showinfo("已在线", "「%s」已经在线了。" % acc.title)
            return
        if process.count_instances() >= slot.MAX_INSTANCES:
            messagebox.showwarning("实例已满",
                                   "微信同时最多 %d 个实例，请先退出一个。"
                                   % slot.MAX_INSTANCES)
            return
        if not self.vault.ready(acc.wxid):
            messagebox.showwarning(
                "登录态不完整",
                "「%s」档案里缺少 config 对或 host，无法免扫码登录。\n\n"
                "请先用微信登录一次该账号，后台监控会自动补齐。" % acc.title)
            return

        def work():
            res = self._do_login(acc)
            note = res.get("detail") or "完成"
            if res.get("ok"):
                self.after(0, lambda: self._set(note))
            else:
                self.after(0, lambda: messagebox.showwarning(
                    "登录未完成", note or "未知原因"))
        self._run(work, "正在登录「%s」..." % acc.title)

    def on_login_all(self) -> None:
        if self._locked:
            return
        online = self._status_online()
        todo = [a for a in sorted(self.vault.list_accounts(), key=lambda x: x.wxid)
                if a.wxid not in online and self.vault.ready(a.wxid)]
        if not todo:
            skipped = len(self.vault.list_accounts()) - len(todo)
            messagebox.showinfo(
                "无需登录",
                ("没有可登录的账号。\n（已在线 %d 个，登录态不完整 %d 个）"
                 % (len(online), skipped)) if skipped
                else "所有账号都已经在线了。")
            return
        room = slot.MAX_INSTANCES - process.count_instances()
        if room <= 0:
            messagebox.showwarning("实例已满", "微信同时最多 %d 个实例。"
                                   % slot.MAX_INSTANCES)
            return
        if len(todo) > room:
            todo = todo[:room]

        def work():
            done, failed = [], []
            for i, acc in enumerate(todo):
                self.after(0, lambda a=acc, k=i: self._set(
                    "正在登录「%s」（%d/%d）..." % (a.title, k + 1, len(todo))))
                r = self._do_login(acc)
                (done if r.get("ok") else failed).append(acc.title)
                if i < len(todo) - 1:
                    time.sleep(4)
            msg = "完成：成功 %d 个" % len(done)
            if failed:
                msg += "，失败 %d 个（%s）" % (len(failed), "、".join(failed))
            self.after(0, lambda: self._set(msg))
            if failed:
                self.after(0, lambda: messagebox.showwarning(
                    "部分账号未登录成功", "以下账号未能登录：\n%s\n\n"
                    "多为其登录态已失效，请重新用微信登录一次以更新档案。"
                    % "\n".join(failed)))
        self._run(work, "正在一键登录 %d 个账号..." % len(todo))

    def on_delete(self, acc) -> None:
        if self._locked:
            return
        if acc.wxid in self._status_online():
            messagebox.showinfo("账号在线", "「%s」正在使用中，请先退出微信再删除。"
                                % acc.title)
            return
        if not messagebox.askyesno(
                "删除账号",
                "删除「%s」的登录档案？\n（只删本工具的存档，不影响微信聊天记录）"
                % acc.title):
            return
        ok, errs = self.vault.delete(acc.wxid)
        for _k in [k for k in self._avatar_cache if k[0] == acc.wxid]:
            self._avatar_cache.pop(_k, None)
        self._gray_failed.discard(acc.wxid)
        if not ok:
            messagebox.showwarning("部分未删除", "%d 个文件删不掉。\n%s"
                                   % (len(errs), self.vault.account_dir(acc.wxid)))
        self.refresh()

    def on_close(self) -> None:
        for j in (self._cfg_job, self._anim_job):
            if j:
                try:
                    self.after_cancel(j)
                except Exception:               # noqa: BLE001
                    pass
        if self.watcher:
            self.watcher.stop()
        self.destroy()


def _warn_no_uia(parent=None) -> None:
    """UI 自动化不可用时的**大声报错**（不静默降级）。"""
    ext_dir = os.path.join(RELEASE_DIR, "ext")
    msg = ("UI 自动化组件（uiautomation）不可用，无法后台点击「进入微信」，"
           "也无法识别微信窗口。\n\n"
           "当前环境：%s\n"
           "依赖目录：%s\n\n"
           "请确认该目录存在且完整（应含 uiautomation\\ 与 comtypes\\）。\n"
           "★ 本程序**不会**自动重建或替换 release 目录（避免把你的新版代码"
           "静默退回旧版），请手工恢复该目录后再启动。"
           % ("打包版 exe" if getattr(sys, "frozen", False)
              else "源码版 Python %s" % sys.version.split()[0], ext_dir))
    sys.stderr.write("！" + msg.replace("\n", "\n！") + "\n")
    try:
        messagebox.showwarning("缺少 UI 自动化组件", msg, parent=parent)
    except Exception:                           # noqa: BLE001
        pass


def _preflight() -> bool:
    """启动自检：UI 自动化不可用时返回 False（只检测与落日志，不弹窗）。"""
    with ui.ui_scope():     # 在已 CoInitialize 的线程里做延迟导入与自检
        ok = ui.available()
        log_line("启动自检：frozen=%s  解释器=Python %s  uiautomation导入=%s"
                 % (bool(getattr(sys, "frozen", False)),
                    sys.version.split()[0], ok))
        if ok:
            # 首次调用才解析系统类型库，这里实做一次
            try:
                import uiautomation as _ua
                _ua.GetRootControl()
                log_line("UIA 实测：后台自动化可用")
            except Exception as e:              # noqa: BLE001
                log_line("UIA 实测失败：%r" % (e,))
            return True
    return False


def main() -> None:
    # 单实例闸门放在最前
    if not _claim_single_instance():
        return
    _enable_dpi()
    App().mainloop()


if __name__ == "__main__":
    main()
