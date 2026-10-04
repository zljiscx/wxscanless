# -*- coding: utf-8 -*-
"""图形界面（tkinter，Windows 7/10/11）。

窗口形态：窄长竖条，宽 360 固定不可拉伸，窗口内任意位置可拖动，贴屏幕右边缘自动隐藏。

启动时序：窗口先显示（状态行"正在加载运行环境…（此过程不能登录账号）"、按钮置灰），
UIA 自检 / 环境探测 / 启动监控在后台线程完成，就绪后状态行变"已就绪"并恢复按钮。

账号卡片：登录 / 删除两个操作，另有全局「一键登录」。头像在线彩色 / 离线灰度，
昵称在线绿加粗 / 离线灰常规；卡片高度固定，账号多时为列表加滚动条并支持滚轮。

本文件属于外置代码区 `release\\`，更新功能只需替换本目录，无需重新打包 EXE。
"""
from __future__ import annotations

import math
import os
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox

# --- 运行根 -------------------------------------------------------------
# 本文件位于**外置代码区** release\ 内，因此 __file__ 始终是真实磁盘路径。
#
#   RELEASE_DIR = release\ 目录           业务模块与第三方依赖都在这里
#   ROOT        = 程序根（exe 同级目录）   data\ 与 logs\ 的父目录
#
# ROOT 由启动器通过环境变量注入；直接运行本文件时（开发调试）回退为上一级目录。
# 根目录只留启动器与 exe：运行数据一律进子目录（data\ 账号档案、logs\ 日志）。
RELEASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("WXPROF_ROOT") or os.path.dirname(RELEASE_DIR)
DATA_DIR = os.path.join(ROOT, "data")
LOG_PATH = os.path.join(ROOT, "logs", "wechat_launcher.log")


# --- 启动阶段彻底消除"闪黑窗" -----------------------------------------
# 本程序是 GUI（打包为 --noconsole，自身没有控制台）。只要有任何一处通过
# subprocess 执行**控制台程序**，Windows 就会给它新建一个控制台窗口
# —— 屏幕上一闪而过的"CMD 黑窗"就是这么来的。
#
# 实测定位到的触发点：打包（frozen）环境下标准库 `platform` 会走到 `_syscmd_ver()`，
# 其实现是 `subprocess.check_output('ver', shell=True)`，也就是执行 `cmd.exe /c ver`
# —— 只为取一个系统版本号，却弹出一个控制台窗口。
#
# 两层处理（都必须在本模块其它 import/逻辑之前完成，否则同一条导入链上已经
# 发生的调用就漏掉了）：
#   ① 精准：把 `platform._syscmd_ver` 换成不执行外部命令的实现，版本号改从
#      `sys.getwindowsversion()` 取（同一信息源，格式与 `ver` 输出一致）；
#   ② 兜底：包装 `subprocess.Popen`，凡子进程一律附加 CREATE_NO_WINDOW，
#      今后无论哪个依赖再调外部命令，都不会再闪窗。
CREATE_NO_WINDOW = 0x08000000


def log_line(text: str) -> None:
    """统一日志落盘：logs\\wechat_launcher.log。

    --noconsole 打包后没有控制台，这个文件是唯一的排错入口；目录按需创建
    （logs\\ 可随手整个删掉，下次运行会自动重建）。
    """
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        # ★ 带毫秒：登录过程里"差一轮轮询"就是几百毫秒，秒级时间戳根本看不出
        #   差别（用户反馈"要等 3、4 秒才点"，秒级日志无法定位到到底是哪一段）。
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

    # ① platform._syscmd_ver —— 不再执行 `cmd /c ver`
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

for _p in (os.path.join(RELEASE_DIR, "ext"),      # 第三方依赖（外置，可单独升级）
           os.path.join(RELEASE_DIR, "src")):     # 业务模块
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from wxprof import login, paths, process, slot, ui, vault, watcher     # noqa: E402
from wxprof import __version__                                          # noqa: E402
from wxprof import avatar as avatar_mod                                 # noqa: E402

# --- 运行日志与崩溃兜底 -------------------------------------------------
# 打包成 exe（--noconsole）后没有控制台，print 与未捕获异常都会无声消失，
# 表现就是"点了没反应"。统一落到 logs\wechat_launcher.log（见 log_line），
# 未捕获异常再弹窗提示。
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


def _tk_report(self, exc, val, tb):        # tkinter 回调里的异常不走 excepthook
    _on_uncaught(exc, val, tb)


tk.Tk.report_callback_exception = _tk_report

if getattr(sys, "frozen", False):
    class _LogStream:                       # --noconsole 下 stdout/stderr 是 None
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

# ------------------------------------------------------------------ 外观
BG = "#f5f6f7"
CARD = "#ffffff"
ACCENT = "#07c160"
TEXT = "#1a1a1a"
SUB = "#8a8a8a"
LINE = "#e6e6e6"
GRAY = "#c9c9c9"
GRAY_OFFLINE = "#bdbdbd"                # 离线头像的占位灰（账号还没有头像文件时）
RED = "#e64340"
THUMB = "#c4c4c4"                       # 细滚动条的滑块
THUMB_ACTIVE = "#9e9e9e"                # 按下/拖动时的滑块

AVATAR = avatar_mod.DISPLAY_SIZE        # 头像边长 = 落盘尺寸（1:1 绘制，不裁切）
# 账号卡片的**固定**高度（用户要求：一行昵称的卡片不许比两行昵称的矮）。
# 数值来自实测（_probe\measure_card.py，真实 App 实例量到的像素）：
#   两行昵称的卡片 118px、一行昵称的卡片 114px —— 取大的那个统一。
CARD_H = 118
NICK_LINES = 2                          # 昵称最多显示两行（与 CARD_H 配套，见 _clip_nick）
WIN_W = 360                             # 窗口宽（= 用户实测的最小可用宽度；窗口固定，不可调）
WIN_H = 760                             # 窗口高
MIN_H = 480                             # 仅作"屏幕太矮"时的兜底高度，不影响宽度
SCROLLBAR_W = 6                         # 列表滚动条宽度（用户指定，默认主题约 15px）
POLL_MS = 2000

# ------------------------------------------------------------------ 贴边隐藏
# **只对屏幕右边缘生效**（用户要求：左边与顶边不贴边）。
DOCK_EDGE = 18          # 窗口距屏幕**右**边缘多少像素内 → 自动贴边
DOCK_PEEK_SIDE = 5      # 贴边后留在屏内的细缝宽度
DOCK_HOT = 8            # 细缝向外扩展多少像素算"鼠标靠近"
DOCK_POLL = 120         # 鼠标位置轮询间隔（ms）
DOCK_HIDE_DELAY = 1200  # 鼠标离开窗口后多久收回（ms）
DOCK_SLIDE_MS = 12      # 滑动动画帧间隔（ms）

PALETTE = ("#5b8ff9", "#5ad8a6", "#f6bd16", "#e8684a", "#6dc8ec",
           "#9270ca", "#ff9d4d", "#269a99", "#ff99c3", "#7f8fa6")

# --- 单实例控制 ---------------------------------------------------------
# 登录器**只允许运行一个**（用户要求）。用 Windows 命名互斥体做进程级互斥：
# 第二个实例**不会开窗**，而是通过命名事件通知已有实例"把窗口显示到前台"
# （即使它正贴边收在屏幕边缘），随后自己安静退出。
# 这样用户双击时一定能看到反馈，而不是"双击了却像没反应"。
#
# 用 `Local\` 前缀而非 `Global\`：创建 Global 命名对象需要
# SeCreateGlobalPrivilege（普通进程默认没有），会直接失败；而同一用户会话内
# 互斥用 Local 已经足够。
MUTEX_NAME = "Local\\wxprof_wechat_launcher_mutex"
SHOW_EVENT = "Local\\wxprof_wechat_launcher_show"

_K32 = None
_MUTEX_HANDLE = None        # 故意不关闭：随进程退出由系统释放，下一个实例才能建成功
_SHOW_EVENT_HANDLE = None   # 已有实例用它接收"请显示窗口"信号


def _init_win32():
    """按需加载 kernel32 并**声明函数签名**。

    不声明 argtypes/restype 时 ctypes 按 C int 传参，**64 位下句柄会被截断**，
    API 静默失败（本项目在进程枚举上踩过这个坑）。
    """
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


def _claim_single_instance() -> bool:
    """单实例闸门。True = 可以启动；False = 已有实例在跑，本进程应当退出。

    **任何异常都放行**（返回 True）—— 单实例是体验优化，不该在异常环境下
    把程序彻底锁死到打不开。
    """
    global _MUTEX_HANDLE, _SHOW_EVENT_HANDLE
    try:
        import ctypes
        k = _init_win32()
        h = k.CreateMutexW(None, 0, MUTEX_NAME)
        if not h:
            log_line("单实例检查：互斥体创建失败，跳过检查继续启动")
            return True
        # 必须紧跟 CreateMutexW 读取，中间不能插入其它 API 调用
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
    """宽度可自定义的细滚动条（自绘）。

    为什么不用现成的：
      · `ttk.Scrollbar` 的宽度只能从 style 设，而 Windows 默认主题（vista）
        **完全忽略** `width` —— 实测无论设 6/8/12，请求宽度恒为 17px；
      · 经典 `tk.Scrollbar` 带上下箭头，宽度做不小。
    本窗口要一条 6px 的窄条，于是用一个 Canvas 自绘：
      · 轨道与列表背景同色 ⇒ 视觉上只剩一截灰色滑块，干净；
      · 支持拖动滑块、点击轨道跳转、鼠标悬停变深；
      · 接口与 tk.Scrollbar 一致：`command=目标.yview`，
        目标控件配 `yscrollcommand=本.set`。
    """

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

    # -------------------------------------------------- 供目标控件调用
    def set(self, first, last) -> None:
        """由目标控件的 yscrollcommand 调用，刷新滑块位置。"""
        self._first, self._last = float(first), float(last)
        self._redraw()

    # -------------------------------------------------------------- 内部
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
        # ★ 立刻隐藏窗口：Tk 的 root 窗口**一被创建就以默认尺寸显示**（实测
        # 216x239，位置在屏幕中部），直到下面 self.geometry(...) 生效才变成目标
        # 尺寸并移到屏幕右侧 —— 中间那一帧就是用户看到的"一闪就没的小窗口"。
        self.withdraw()
        self._busy = False
        self._booting = True                    # ★ 启动加载中：登录按钮此时不可用
        self.rows = {}
        self._avatar_cache = {}                 # (wxid, 灰度?) -> (mtime, PhotoImage)
        self._gray_failed = set()               # 灰度生成失败过的 wxid（不反复重试）

        # 贴边隐藏状态（**只对屏幕右边缘生效**）
        self._cfg_job = None                    # 移动去抖
        self._anim_job = None                   # 滑动动画
        self._dock = None                       # None / "right"
        self._shown_pos = None
        self._hidden_pos = None
        self._docked_shown = False              # 贴边后当前是否处于"滑出"状态
        self._leave_at = 0.0

        # 任意位置拖动状态
        self._dragging = False                  # 正在拖动（拖时不判贴边）
        self._drag_off = None                   # 按下点相对窗口左上角的偏移

        self.title("微信多账号免扫码登录器 v%s" % __version__)
        self._set_icon()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        h = min(WIN_H, max(MIN_H, sh - 120))    # 屏幕太矮才缩高，宽度恒为 WIN_W
        self.geometry("%dx%d+%d+%d" % (WIN_W, h, max(0, sw - WIN_W - 40),
                                       max(40, (sh - h) // 2 - 40)))
        # ★ 窗口大小固定（用户要求：不允许调整大小）。
        # 用 resizable(False, False) 而不是 minsize+maxsize：它同时禁用边框拖拽，
        # 并让标题栏的「最大化」按钮变灰。拖动位置不受影响（拖的是窗口不是边框）。
        self.resizable(False, False)
        self.configure(bg=BG)

        self.vault = vault.Vault(DATA_DIR)
        # ★ 环境探测 / UIA 自检 / 监控启动一律**不在这里**做（三者合计约 1.3 秒）：
        #   放在这里，窗口就得等它们全部做完才出现（用户实测"启动要 9 秒"里，
        #   有相当一部分是这段）。改为——界面先显示（状态行提示"正在加载运行
        #   环境"，登录按钮禁用），再由 `_boot()` 在后台线程把它们补齐。
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
        self.refresh()                          # env 尚为 None ⇒ 列表区显示"正在加载…"
        self.deiconify()                        # 布局就绪，正式显示（配合开头的 withdraw）
        self.after(60, self._boot)              # ★ 窗口已在屏幕上，才开始做重活
        self.after(POLL_MS, self._tick)
        self.after(500, self._poll_show_event)  # 接收"第二个实例"的唤醒请求

    # ------------------------------------------------- 启动加载（窗口显示后）
    # 启动期最贵的三件事（UIA 自检 / 环境探测 / 启监控）合计约 1.3 秒，若放在窗口
    # 出现之前，窗口就得等它们全部做完才显示。现在窗口先出来（状态行写明"正在加载
    # 运行环境"、登录按钮置灰），重活挪到后台线程，完成后自动改成"已就绪"并恢复按钮。
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
            # 回主线程：界面更新一律走 after（后台线程直接动 tkinter 不安全）
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
            # ★ 探测结果整体落日志。换电脑、微信换盘、数据目录被挪走时，
            # 看一眼日志就知道**卡在哪一环** —— summary() 末尾会逐条列出 errors。
            # 没有这几行，界面上只会看到"未发现微信环境"，无从下手。
            log_line("微信环境探测：ok=%s" % env.ok)
            for _line in env.summary().splitlines():
                log_line("    " + _line)
            try:
                self.watcher = watcher.Watcher(
                    env, self.vault, interval=2.0, on_event=self._on_capture,
                    on_log=self._on_watch_log)
                self.watcher.start()
                login.clean_temp(env)           # 上次遗留的 .livebak 清掉
            except Exception as e:              # noqa: BLE001
                log_line("监控启动失败：%r" % (e,))
        else:
            log_line("环境探测失败：没有拿到环境（env=None）")
            if err is not None:
                messagebox.showerror("环境探测失败", str(err), parent=self)
        if not ok:
            _warn_no_uia(self)                  # 只在主线程弹（后台线程弹不安全）
        self._booting = False
        self.var_status.set("已就绪")
        self.refresh()
        try:
            # 立刻把按钮/状态刷到位，不等下一轮 _tick（否则"已就绪"之后按钮
            # 还会灰着最多 POLL_MS 那么久）
            self._update_status()
        except Exception as e:                  # noqa: BLE001
            log_line("首轮状态刷新失败：%r" % (e,))

    # -------------------------------------------------------------- 界面
    def _set_icon(self) -> None:
        """设置窗口标题栏与任务栏图标。

        tkinter 窗口**不会**自动继承 exe 的资源图标，必须显式指定。
        图标与 EXE 的那张同源（`release\\assets\\icon.ico`，随外置代码一起走，
        所以"只替换 release\\"的迭代方式不会把图标弄丢）；缺失时静默跳过。
        """
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

        # ★ 滚动条**必须先 pack**（pack 是"先到先分配"）：canvas 的默认请求宽度比
        # 本窗口可用宽度还宽，先 pack 它会把全部宽度吃掉，滚动条只分到 0px。
        # 同时给 canvas 一个小的显式宽度，不依赖它的默认请求宽度。
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
        # ★ 立即按"忙/加载中"置一次灰：`_tick` 要等 POLL_MS 才跑第一轮，启动加载期间
        #   `_update_status` 又会因 env 还是 None 直接返回 —— 不显式设一次，
        #   「一键登录」在加载阶段会显示成可点的绿色。
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

    # -------------------------------------------------------------- 列表
    def refresh(self) -> None:
        for w in self.inner.winfo_children():
            w.destroy()
        self.rows = {}
        if self.env is None:
            # 启动加载中 ⇒ 说"正在加载…"；加载完成后仍为 None ⇒ 才是真的没检测到
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
            # ★ 这行提示**不做手工换行**（按固定像素 `\n` 断行会随窗口宽度被截断），
            # 改由提示 Label 自身的 <Configure> 回填实际宽度自动折行。
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
        """加载头像 PNG（文件已按 DISPLAY_SIZE 落盘，这里 1:1 使用）。

        `gray=True` 用于**离线账号**：优先读灰度副本，没有就现场生成一份
        （生成一次即落盘、长期复用；生成失败也只试一次，不会每轮重试）。
        """
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
        # 兜底：尺寸不等于显示尺寸时，用整数倍缩小（取上取整，保证不超框、不裁切）
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
        """按在线状态把头像画到画布上：**在线彩色、离线灰**。

        之前只靠状态文字区分在线/离线，字小、扫一眼看不出来；头像置灰后
        整列卡片一眼就能分出哪些账号没在跑。
        """
        cv.delete("all")
        img = self._avatar_image(acc, gray=not online)
        if img is not None:
            # 1:1 居中：图片完整落在画布内，不会被裁切
            cv.create_image(AVATAR // 2, AVATAR // 2, image=img)
            cv._img = img                       # 防止被回收
            return
        # 还没有头像文件：首字母占位，同样遵循"离线灰、在线彩"
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
        # ★ 卡片高度**固定**：用 pack_propagate(False) 关掉"按内容自适应高度" ——
        #   否则子控件会把卡片各自撑到不同高度（一行昵称 114 vs 两行 118）。
        row = tk.Frame(self.inner, bg=CARD, highlightthickness=1,
                       highlightbackground=LINE, height=CARD_H)
        row.pack(fill="x", pady=4)
        row.pack_propagate(False)

        # 头像（左）：按**当前**在线状态直接画对（离线灰/在线彩），免得先画彩色
        # 再被下个刷新周期改成灰，白白闪一下。
        on_now = acc.wxid in self._status_online()
        cv = self._avatar(row, acc, on_now)
        cv.pack(side="left", padx=(12, 10), pady=12)

        # 按钮（右，竖排；先 pack 保证不被中间文字挤压）
        right = tk.Frame(row, bg=CARD)
        right.pack(side="right", padx=(6, 12), pady=12)
        b_login = self._btn(right, "登录", lambda a=acc: self.on_login(a),
                            primary=True)
        b_del = self._btn(right, "删除", lambda a=acc: self.on_delete(a),
                          danger=True)

        # 文字（中间，占剩余宽度）。用 fill="x" 而**不** fill="y"：高度由内容决定，
        # pack 会把文字块在卡片里垂直居中 —— 一行昵称时不会顶在上边。
        mid = tk.Frame(row, bg=CARD)
        mid.pack(side="left", fill="x", expand=True, pady=12)
        # 昵称：**在线绿+加粗 / 离线灰+常规**（与头像同一套在线判据，建卡时直接画对）。
        # 自动折行、最多两行（卡片高度固定，第三行会被裁）；换行宽度不写死像素，
        # 由 mid 的 <Configure> 回填实际宽度。
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
        """昵称字体：**在线加粗**（绿色字笔画细，不加粗不够醒目 —— 用户要求），
        离线常规字重，两者拉开对比。"""
        return ("Microsoft YaHei UI", 11, "bold") if online \
            else ("Microsoft YaHei UI", 11)

    def _fit_wrap(self, lbl, event) -> None:
        """把 Label 的换行宽度对齐**容器的实际宽度**（长文本自动折行）。

        `wraplength` 只认固定像素值，所以必须由容器的 `<Configure>` 回填；
        加"值没变就不设"的判断，避免 `Configure → config → Configure` 反复触发。
        昵称（绑 `mid`）与空态提示（绑提示 Label 自身）共用这一个方法。
        """
        try:
            w = max(60, int(event.width))
            if getattr(lbl, "_full_text", None) is not None:
                self._clip_nick(lbl, w)
            if int(lbl.cget("wraplength")) != w:
                lbl.config(wraplength=w)
        except Exception:                       # noqa: BLE001
            pass

    def _clip_nick(self, lbl, width: int = 0) -> None:
        """昵称**最多占两行**，超出的部分用省略号收尾（卡片高度是固定的）。

        为什么必须限制行数：卡片高度统一成 `CARD_H`（两行昵称的高度）之后，
        第三行没有地方放 —— 不裁的话会被卡片边缘切掉半个字，比省略号更难看。
        宽度按像素算（`tkfont` 量文本宽度），中英文混排也能算准。
        """
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

    # -------------------------------------------------------------- 状态
    def _on_capture(self, res: dict) -> None:
        """监控线程采到新登录态（在工作线程里回调）。"""
        title = res.get("title") or res.get("wxid") or ""
        what = "、".join(res.get("changed") or [])
        verb = "已收录新账号" if res.get("new") else "已更新登录态"
        self.after(0, lambda: self._set("%s「%s」（%s）" % (verb, title, what)))
        self.after(0, self.refresh)

    def _on_watch_log(self, text: str) -> None:
        self.after(0, lambda: self.var_status.set(text))

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
            # 环境没探测全（换电脑 / 微信没装 / 数据目录被挪走）。
            # 这里只说"未发现"，**不要**退化成"微信 4.x · 0 个实例" ——
            # 那会让人以为环境正常、只是没账号在线。具体卡在哪一环看日志。
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
            # 在线状态翻转时重画头像（彩色 ↔ 灰色）。只在真的变化时画，
            # 否则每 2 秒的轮询都会重建一次图形对象。
            if w.get("online") != on:
                w["online"] = on
                self._paint_avatar(w["avatar"], acc, on)
            nfg = ACCENT if on else SUB        # 昵称：在线绿+粗 / 离线灰+常规
            if w.get("nick_fg") != nfg:
                w["nick_fg"] = nfg
                w["nick"].config(fg=nfg, font=self._nick_font(on))
                # 字重变了（粗 ↔ 常规）字宽跟着变，重新裁一次：
                # 这种情况 `<Configure>` 不会触发（容器宽度没变），必须手动算。
                self._clip_nick(w["nick"])
            w["status"].config(text=txt, fg=fg)
            self._set_btn(w["login"], not on and ready)
            self._set_btn(w["del"], not on)
        # 「一键登录」只要还有"未在线且登录态齐备"的账号就可点。
        # 判据必须与 on_login_all 里的 todo 一致，否则按钮状态与点击结果会打架。
        can_all = any((a.wxid not in online) and self.vault.ready(a.wxid)
                      for a in accs)
        locked = self._locked
        self.btn_all.config(state=tk.NORMAL if (can_all and not locked)
                            else tk.DISABLED,
                            bg=ACCENT if (can_all and not locked) else GRAY,
                            highlightbackground=(ACCENT if (can_all and not locked)
                                                 else GRAY))

    # -------------------------------------------------------- 任意位置拖动
    def _bind_drag(self) -> None:
        """让**窗口内任意位置**都能按住拖动（用户要求：不限于标题栏）。

        只需绑**顶层窗口**一次：tkinter 的事件会沿 bindtags 从目标控件逐级
        冒泡到 toplevel（`.`），所以点在任何 Label / Frame / 头像画布上，
        最终都会走到 _drag_start —— 不必递归给每个控件挂绑定。

        按钮与滚动条在 _drag_start 里按 **事件最初的目标控件** 排除：它们要保持
        "点一下就触发" / "拖滑块滚列表" 的原生行为，被拖动抢走会很难用。
        """
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
        # 拖动即视为"离开贴边态"；再贴到右边缘时会由 _maybe_dock 重新判定
        self._dock = None
        self._shown_pos = self._hidden_pos = None
        self._docked_shown = False
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
        self._on_configure()                    # 松手后重新去抖判一次贴边

    # -------------------------------------------------------------- 贴边隐藏
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
        """窗口停稳后判断：贴着**屏幕右边缘**就进入贴边态。

        （用户要求：贴边隐藏**只对右边生效**，左边与顶边都不贴。）
        """
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

    def _enter_dock(self, y: int, w: int, sw: int) -> None:
        """进入右侧贴边态：完全贴住右边，只在屏内留一条细缝。"""
        self._dock = "right"
        self._shown_pos = (sw - w, y)
        self._hidden_pos = (sw - DOCK_PEEK_SIDE, y)
        self._docked_shown = False
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

    def _pointer_tick(self) -> None:
        if self._dock != "right":
            return
        mx, my = self.winfo_pointerxy()
        x, y, w, h, sw, sh = self._geometry_now()
        # 只有"鼠标靠近右边缘细缝"才算要滑出
        hot = mx >= sw - DOCK_PEEK_SIDE - DOCK_HOT and y <= my <= y + h
        inside = x <= mx <= x + w and y <= my <= y + h

        if hot and not self._docked_shown:
            self._slide_to(*self._shown_pos)
            self._docked_shown = True
            self._leave_at = time.time()
            return
        if self._docked_shown:
            if inside:
                self._leave_at = time.time()
            elif time.time() - self._leave_at > DOCK_HIDE_DELAY / 1000.0:
                self._slide_to(*self._hidden_pos)
                self._docked_shown = False

    # ------------------------------------------------------- 被第二个实例唤醒
    def _poll_show_event(self) -> None:
        """轮询"请显示窗口"信号 —— 第二个实例被单实例闸门拦下时发的。

        用 auto-reset 事件：WaitForSingleObject 返回 0 即消费掉信号，
        不会重复触发。500 ms 一轮，开销可忽略。
        """
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
        """把窗口从贴边状态收回屏幕中央并置顶（被唤醒时调用）。

        贴在屏幕边缘时窗口是"正常显示但位置在屏幕外"，**光靠 ShowWindow
        唤不回来** —— 必须由窗口自己重置几何位置，所以走这条信号通路。
        """
        log_line("收到第二个实例的唤醒请求，把窗口显示到前台")
        try:
            if self._dock or self._anim_job:
                self._dock = None
                self._shown_pos = self._hidden_pos = None
                self._docked_shown = False
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

    # -------------------------------------------------------------- 工具
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

    # -------------------------------------------------------------- 登录
    def _log_login(self, text: str) -> None:
        """登录过程的日志**同时**落日志文件与界面状态行。

        login 模块每一步都有 log，落盘后用户反馈"登录出问题"时能直接看出卡在哪
        一步。本方法在**后台线程**被调用 ⇒ 界面更新一律 `after` 回主线程。
        """
        try:
            log_line("[登录] %s" % str(text).strip())
        except Exception:                       # noqa: BLE001
            pass
        try:
            self.after(0, lambda t=str(text): self._set(t))
        except Exception:                       # noqa: BLE001
            pass

    def _do_login(self, acc) -> dict:
        """登录一个账号。

        `hidden` 兜底：流程一旦早退（实例已满、启动失败、config 载入不完整…），
        也要把第 2 步收进托盘的窗口显示回来 —— 否则用户的微信窗口会莫名留在
        托盘里，看起来像是被本程序退出了。
        """
        hidden = []
        try:
            with ui.ui_scope():
                return login.login_account(self.env, self.vault, acc,
                                           log=self._log_login, hidden=hidden)
        finally:
            # 纯 Win32 ShowWindow，不涉及 UIA/COM，无需再进 ui_scope
            login.restore_windows(hidden, log=self._log_login)

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

    # -------------------------------------------------------------- 删除
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

    # -------------------------------------------------------------- 关闭
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
    """启动自检：UI 自动化不可用时返回 False（**只检测 + 落日志，不弹窗**）。

    `ui.available()` 为假（import uiautomation 失败）时，登录窗口/主界面的探测会
    **静默返回空**，表现为"看不到微信、点了没反应"。弹窗不在这里做：本函数跑在
    后台线程（tkinter 对话框只能在主线程弹），由主线程拿到返回值后调
    `_warn_no_uia()`。
    """
    with ui.ui_scope():     # ① 让"延迟导入 uiautomation"发生在**已 CoInitialize** 的线程里
                           # ② uiautomation 在非主线程必须先初始化，否则 UIA 调用静默失败
        ok = ui.available()
        log_line("启动自检：frozen=%s  解释器=Python %s  uiautomation导入=%s"
                 % (bool(getattr(sys, "frozen", False)),
                    sys.version.split()[0], ok))
        if ok:
            # 导入成功 ≠ 真的能用：uiautomation 在**首次调用**时才执行
            # comtypes.client.GetModule("UIAutomationCore.dll") 去解析系统类型库，
            # 这一步恰恰是打包后最容易失效的环节 —— 这里实打实做一次并记入日志。
            # ★ 只要拿到根控件就够，不调 GetChildren()（那要 0.65 s 重建整张桌面
            #   元素表，纯属白花）。
            try:
                import uiautomation as _ua
                _ua.GetRootControl()
                log_line("UIA 实测：后台自动化可用")
            except Exception as e:              # noqa: BLE001
                log_line("UIA 实测失败：%r" % (e,))
            return True
    return False


def main() -> None:
    # 单实例闸门放在最前：已有实例时，本进程不做任何多余初始化就退出
    if not _claim_single_instance():
        return
    _enable_dpi()
    # ★ 启动自检（UIA）不在这里做 —— 它要 1 s 左右，会让窗口迟迟不出现；改由窗口
    #   显示之后 `App._boot()` 在后台线程里完成。
    App().mainloop()


if __name__ == "__main__":
    main()
