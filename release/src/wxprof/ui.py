# -*- coding: utf-8 -*-
"""微信登录窗口的 UI 自动化（**全程后台**，不碰真实鼠标键盘）。

微信 4.x 启动后停在登录窗口（UIA 类名 `mmui::LoginWindow`），上面显示"当前登录
用户 XXX" 和一个「进入微信」按钮 —— 必须点一下才真正登录。

本模块只做三件事，且都要求**后台无感**：

    1. 等到登录窗口出现，并读出登录页状态（一键登录 / 二维码页 / 加载中）
    2. **向按钮自己的窗口句柄投递鼠标消息**点击「进入微信」
       —— 不用屏幕坐标、不移动光标，所以窗口被别的程序挡住也点得中，
          用户的鼠标键盘操作完全不干扰
    3. 判定是否真的进了主界面（`mmui::MainWindow` 出现）

线程注意（重要）：uiautomation 在**非主线程**里使用前必须先 CoInitialize，否则所有
UI 操作会**静默失败**（错误只写进 @AutomationLog.txt）。工作线程里请用：

        with ui.ui_scope():
            win = ui.login_window_of(pid)

依赖 `uiautomation`（已随 `release\\ext\\` 一并分发）；导入失败时 `available()` 返回
False，调用方应显式报错而不是静默降级。
"""
from __future__ import annotations

import contextlib
import ctypes
import os
import time
from ctypes import wintypes

# ★ 延迟导入（`_ensure()`），不在模块导入期做：`import uiautomation` 要 0.56 s
#   （comtypes 首次解析系统类型库），放在导入期会让"双击到窗口出现"平白多等半秒。
#   改成首次真正用到 UI 自动化时才导入 —— 这半秒被藏进用户已经在看界面的时间里。
#   `_OK` 的初值为 None（还没试过），因此所有判据必须走 `_ensure()`，不能直接读
#   `_OK`。外部（app.py）请用 `available()`。
auto = None
_OK = None                      # None=尚未尝试 / True=可用 / False=不可用
_IMPORT_ERR = ""


def _ensure() -> bool:
    """确保 `uiautomation` 已导入（幂等，线程安全由 import 锁保证）。

    返回是否可用；第一次调用承担那 0.56 s，之后都是读标志。
    """
    global auto, _OK, _IMPORT_ERR
    if _OK is not None:
        return _OK
    try:
        import uiautomation as _ua
        auto = _ua
        _OK = True
    except Exception as e:                      # noqa: BLE001
        auto = None
        _OK = False
        _IMPORT_ERR = repr(e)
    return _OK

LOGIN_CLASS = "mmui::LoginWindow"
MAIN_CLASS = "mmui::MainWindow"
PREFIX_USER = "当前登录用户"
BTN_ENTER = "进入微信"
BTN_SWITCH = "切换账号"

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.PostMessageW.restype = wintypes.BOOL
_user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                 wintypes.WPARAM, wintypes.LPARAM]
_user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
_user32.SetFocus.restype = wintypes.HWND
_user32.ShowWindow.restype = wintypes.BOOL
_user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.IsWindow.restype = wintypes.BOOL
_user32.IsWindow.argtypes = [wintypes.HWND]
_user32.IsWindowVisible.restype = wintypes.BOOL
_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_user32.SetForegroundWindow.restype = wintypes.BOOL
_user32.SetForegroundWindow.argtypes = [wintypes.HWND]

WM_CLOSE = 0x0010
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
MK_LBUTTON = 0x0001
VK_RETURN = 0x0D
VK_SPACE = 0x20

SW_HIDE = 0                      # 隐藏窗口（纯 Win32 可见性，不触发微信的关闭逻辑）
SW_SHOW = 5                      # 按当前状态显示
SW_RESTORE = 9                   # 从最小化/隐藏状态恢复并激活

# 点「进入微信」的尝试顺序 = **级联**：一招没反应就换下一招（不是同招反复发）。
#
# 依据：12 种方法逐个实测（每招 3 次样本，全部 100% 有效）。既然有效性分不出高下，
# 级联顺序就按 ①零副作用优先 ②通路不重复优先 ③快者在前 来排（括号内为实测均值）：
#
#   key-win    键盘 VK_RETURN → **窗口**句柄     77 ms  ← 最快，且**不依赖按钮坐标**
#   mouse-btn  鼠标三连      → **按钮**句柄    125 ms  ← 零副作用
#   key-btn    键盘 VK_SPACE  → **按钮**句柄    113 ms
#   mouse-win  鼠标三连      → **窗口**句柄    160 ms
#
# 这 4 招正好铺满「{键盘, 鼠标} × {窗口句柄, 按钮句柄}」的 2×2 —— 要覆盖的是**通路**，
# 不是方法个数（同类方法一个失效、其余的通常一起失效，再加只是重复押注）。
#
# 实测**无效**（不进级联）：UIA `InvokePattern.Invoke()` /
# `LegacyIAccessible.DoDefaultAction()`（本版微信按钮无这些实现）、`BM_CLICK`、
# 鼠标三连用**屏幕坐标**（Qt 只认客户区坐标）。
CLICK_METHODS = ("key-win", "mouse-btn", "key-btn", "mouse-win")

# 旧名兼容（"mouse"=现 mouse-btn，"key"=现 key-btn）
CLICK_ALIAS = {"mouse": "mouse-btn", "key": "key-btn"}

# 日志里显示的中文名
CLICK_CN = {"key-win": "键盘回车→窗口", "mouse-btn": "鼠标三连→按钮",
            "key-btn": "键盘空格→按钮", "mouse-win": "鼠标三连→窗口"}


# ----------------------------------------------------------- 线程初始化
def init_thread() -> bool:
    """在当前线程初始化 UI Automation（工作线程里必须调用一次）。"""
    if not _ensure():
        return False
    try:
        import comtypes
        comtypes.CoInitialize()
    except Exception:                           # noqa: BLE001
        pass
    fn = getattr(auto, "InitializeUIAutomationInCurrentThread", None)
    if fn:
        try:
            fn()
        except Exception:                       # noqa: BLE001
            pass
    return True


@contextlib.contextmanager
def ui_scope():
    """UI 自动化作用域；在工作线程里必须用它包住所有 UI 调用。"""
    if not _ensure():
        yield
        return
    maker = getattr(auto, "UIAutomationInitializerInThread", None)
    cm = None
    if maker is not None:
        try:
            cm = maker(debug=False)
            cm.__enter__()
        except Exception:                       # noqa: BLE001
            cm = None
    if cm is None:
        init_thread()
    try:
        yield
    finally:
        if cm is not None:
            try:
                cm.__exit__(None, None, None)
            except Exception:                   # noqa: BLE001
                pass


def available() -> bool:
    return _ensure()


def grab(path: str, ctrl=None) -> str:
    """把控件（默认整个屏幕）截图存成 PNG，返回绝对路径；失败返回空串。

    用来在出了问题时有据可查 —— **判断"微信停在哪一屏"不能只靠 UI 文本**。
    """
    if not _ensure():
        return ""
    try:
        target = ctrl if ctrl is not None else auto.GetRootControl()
        full = os.path.abspath(path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        target.CaptureToImage(full)
        return full
    except Exception:                           # noqa: BLE001
        return ""


# --------------------------------------- 顶层窗口快路径（Win32 枚举 + 句柄缓存）
# ★★ 为什么必须有这条快路径：
#   老做法 `auto.GetRootControl().GetChildren()` 在 250 个上下顶层窗口的桌面上要
#   **456~547 ms/次**（每次都在重建整张桌面元素表），而登录/主界面的每次查找都在做
#   这件事 ⇒ 用户看到的"看到「进入微信」后还要等好几秒才点"，**慢的不是点击，是"看"**。
#
#   快路径 = `EnumWindows`（~3~8 ms / 260 窗口）取句柄 → 按 pid 过滤（只剩几个）
#   → **只对"可见"的那些**做 UIA（实测 4 ms/次读 ClassName，且**按句柄缓存**）。
#   实测同机同刻：老方法 456/544 ms → 快路径 ~5~15 ms（约 30~100 倍）。
#
#   三条必须记住的事实：
#     ① **UIA 的 ClassName ≠ Win32 类名**。微信两类窗口的 Win32 类名**都是**
#        `Qt51514QWindowIcon`，只有 UIA 才报 `mmui::LoginWindow` / `mmui::MainWindow`
#        —— 所以过滤只能按 pid 做，判类型仍必须靠 `ControlFromHandle` 读 ClassName。
#     ② `ControlFromHandle(hwnd)` 读到的就是 UIA 树里那个 `mmui::MainWindow`。
#     ③ **只缓存 `mmui::*` 的结论**。窗口刚建出来时微信的 UIA provider 还没注册，
#        读到的会先是 **Win32 类名**；把那个名字缓存下来，等它变成 `mmui::LoginWindow`
#        时就**永远看不到登录窗口**了。
#
#   为什么加"可见"这一层过滤：一个微信进程有 **14 个**顶层窗口（IME / tooltips /
#   Chrome_SystemMessageWindow / 托盘消息窗口 …），逐个读 ClassName 要 4 ms/个 =
#   54 ms/轮；而真正有用的（登录窗口、主界面）一定是**可见**的，其余全是隐藏的
#   消息窗口。被排除的只有"当前不可见"的窗口 —— 轮询粒度 0.15 s，它一旦显示出来
#   就会被看到。
_FAST_WND = True                # 出问题时置 False 即回到老的整树枚举
WND_VISIBLE_ONLY = True         # 只认可见窗口（登录/主界面需要它都是可见的）

_MMUI_CLS = (LOGIN_CLASS, MAIN_CLASS)   # 微信 UIA provider 自报的类名（可长期缓存）

_ENUM_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_user32.EnumWindows.argtypes = [_ENUM_PROC, wintypes.LPARAM]
_user32.EnumWindows.restype = wintypes.BOOL
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND,
                                             ctypes.POINTER(wintypes.DWORD)]
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD

_WND_CACHE: dict = {}           # hwnd(int) -> (UIA 控件, UIA 类名)
_WX_PIDS = [0.0, set()]         # [上次刷新时刻, 微信全部 pid；None = 枚举本身失败]
_WX_PIDS_TTL = 0.3              # pid 集合缓存时长（刷新一次 ~9 ms）


def _top_windows() -> list:
    """[(hwnd, pid, 是否可见)] —— 纯 Win32 `EnumWindows`（实测 3~8 ms / 260 窗口）。"""
    out = []

    def cb(h, _l):
        d = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(h, ctypes.byref(d))
        out.append((h, d.value, bool(_user32.IsWindowVisible(h))))
        return True

    try:
        _user32.EnumWindows(_ENUM_PROC(cb), 0)
    except Exception:                           # noqa: BLE001
        return []
    return out


def _wx_pids():
    """**全部**微信进程 pid（含子进程）：按进程名过滤，**不读命令行**。

    不读命令行是有意的 —— 读 PEB 取 CommandLine 每个进程都要 OpenProcess + 读内存，
    十几个子进程就是十几毫秒；而这里只要回答"这个窗口是不是微信的"，进程名足够
    （实测 9 ms/次）。带上 0.3 s 缓存，热路径上几乎不花时间。

    ★ 返回值三态，别混：
        `set()`  —— 枚举成功，当前**确实没有**微信进程（正常情况，不该兜底）
        `None`   —— **枚举本身失败**（Toolhelp32 快照都拿不到）⇒ 调用方必须退回老做法
    """
    now = time.time()
    if now - _WX_PIDS[0] < _WX_PIDS_TTL:
        return _WX_PIDS[1]
    try:
        from . import process as _proc
        snap = _proc._snapshot()
        s = ({p for p, _pp, nm in snap if nm in _proc.EXE_NAMES}
             if snap else None)
    except Exception:                           # noqa: BLE001
        s = None
    _WX_PIDS[0] = now
    _WX_PIDS[1] = s
    return s


def _control_of(hwnd: int):
    """hwnd → (UIA 控件, UIA 类名)。读不出来返回 (None, "")。

    ★ 缓存规则（关键）：**只有读到 `mmui::*` 才认为结论稳定、长期复用**；读到别的
    （Win32 类名等）时**每次重读** —— 微信的 UIA provider 是"窗口先建出来、provider
    稍后才注册"，先读到的 `Qt51514QWindowIcon` 过一会儿就会变成 `mmui::LoginWindow`。
    把前者缓存住 = 登录窗口永远找不到。重读只花 ~4 ms（控件对象本身复用）。
    """
    ent = _WND_CACHE.get(hwnd)
    if ent is not None and ent[1] in _MMUI_CLS:
        return ent                              # 已是微信自报类名 ⇒ 不会再变
    if not _ensure():
        return None, ""
    if ent is not None:
        c = ent[0]                              # 复用控件对象，只重读类名
    else:
        try:
            c = auto.ControlFromHandle(hwnd)
        except Exception:                       # noqa: BLE001
            # 实测：窗口正在销毁时抛 `-2147220991 EVENT_E_NOCONNECTION`
            return None, ""
        if c is None:
            return None, ""
    try:
        cls = c.ClassName or ""
    except Exception:                           # noqa: BLE001
        _WND_CACHE.pop(hwnd, None)              # 控件可能已失效 ⇒ 丢掉，下次重建
        return None, ""
    _WND_CACHE[hwnd] = (c, cls)
    return c, cls


def _legacy_windows(cls: str, pid: int = 0) -> list:
    """老做法：整树枚举桌面的直接子控件。**慢（456~547 ms）**，只作兜底。"""
    if not _ensure():
        return []
    try:
        ws = list(auto.GetRootControl().GetChildren())
    except Exception:                           # noqa: BLE001
        return []
    out = []
    for w in ws:
        try:
            if w.ClassName != cls:
                continue
            if pid and w.ProcessId != pid:
                continue
        except Exception:                       # noqa: BLE001
            continue
        out.append(w)
    return out


def _windows_of_class(cls: str, pid: int = 0) -> list:
    """全部「此类窗口」的 UIA 控件；给了 pid 就只取该进程的。

    走快路径；只有在"快路径整条都不可用"（枚举不到 / 拿不到微信 pid 集合 /
    候选窗口全都读不出 UIA）时才退回老做法 —— 免得把探测能力弄丢。
    """
    if not _ensure():
        return []
    if not _FAST_WND:
        return _legacy_windows(cls, pid)

    topp = _top_windows()
    if not topp:
        return _legacy_windows(cls, pid)

    got_pids = False
    pids = ()
    cand = 0
    bad = 0
    out = []
    for hwnd, wpid, vis in topp:
        if pid:
            if wpid != pid:
                continue
        else:
            if not got_pids:
                pids = _wx_pids()
                got_pids = True
                if pids is None:                # ★ 枚举失败（≠"没有微信进程"）⇒ 走老路
                    return _legacy_windows(cls, pid)
            if wpid not in pids:
                continue
        if WND_VISIBLE_ONLY and not vis:
            continue                            # 隐藏的消息窗口不值得读 UIA（见文件头③）
        cand += 1
        c, k = _control_of(hwnd)
        if c is None:
            bad += 1
        elif k == cls:
            out.append(c)

    if cand and bad == cand:                    # 候选全读不出来 ⇒ 快路径失效，退回
        return _legacy_windows(cls, pid)

    if _WND_CACHE:                              # 句柄已不在桌面上 ⇒ 清缓存，防无界增长
        live = {h for h, _p, _v in topp}
        for h in [h for h in _WND_CACHE if h not in live]:
            _WND_CACHE.pop(h, None)
    return out


# ----------------------------------------------------------- 窗口与控件
def login_windows() -> list:
    """全部登录窗口（多开时可能同时有好几个，必须能分辨）。"""
    return _windows_of_class(LOGIN_CLASS)


def login_window_of(pid: int):
    """取某个进程自己的登录窗口。

    多开时 `find_login_window()` 可能返回**别的实例**的窗口，用它点「进入微信」
    会点错对象 —— 所以按 pid 精确取（顺带更快：不用查微信 pid 集合）。
    """
    ws = _windows_of_class(LOGIN_CLASS, pid)
    return ws[0] if ws else None


def find_login_window(timeout: float = 0.0):
    """等待并返回登录窗口；timeout<=0 表示只查一次。"""
    if not _ensure():
        return None
    end = time.time() + max(timeout, 0.0)
    while True:
        ws = login_windows()
        if ws:
            return ws[0]
        if time.time() >= end:
            return None
        time.sleep(1.0)


def _walk(win):
    if not _ensure():
        return []
    try:
        return list(auto.WalkControl(win, maxDepth=8))
    except Exception:                           # noqa: BLE001
        return []


def login_user(win) -> str:
    """读出登录窗口上的昵称（"当前登录用户"后面的部分）。"""
    if win is None:
        return ""
    for c, _d in _walk(win):
        try:
            if c.Name.startswith(PREFIX_USER):
                return c.Name[len(PREFIX_USER):].strip()
        except Exception:                       # noqa: BLE001
            continue
    return ""


def _find_button(win, name: str):
    if not _ensure():
        return None
    try:
        return auto.FindControl(
            win, lambda c, d: c.ControlTypeName == "ButtonControl" and c.Name == name)
    except Exception:                           # noqa: BLE001
        return None


def has_button(win, name: str) -> bool:
    return _find_button(win, name) is not None


QR_WORDS = ("二维码", "扫码登录", "扫描二维码", "二维码登录", "扫一扫登录")


def _iter_controls(root, max_depth: int = 14, max_nodes: int = 0):
    """深度优先遍历控件（返回 (控件, 深度)）。

    max_nodes > 0 时限制访问节点数 —— 控件树是跨进程 COM 查询，是整个流程里
    最贵的操作；状态判定只关心少数几个控件，没必要走完整棵树。
    """
    stack = [(root, 0)]
    n = 0
    while stack:
        ctrl, d = stack.pop()
        yield ctrl, d
        n += 1
        if max_nodes and n >= max_nodes:
            return
        if d >= max_depth:
            continue
        try:
            kids = ctrl.GetChildren()
        except Exception:                       # noqa: BLE001
            continue
        for c in reversed(kids):
            stack.append((c, d + 1))


def qr_evidence(win) -> str:
    """二维码页的**正向证据**（空串 = 没有证据）。

    为什么不能用"「进入微信」按钮没了"当判据：实测点击成功后按钮也会先消失，
    而登录窗口要再过一会儿才销毁 —— 用"按钮没了"判断会把**正在进主界面**误判成
    **被拒**，然后程序自己打断一次成功的登录。所以这里只认正向证据：
        a) 控件名里出现"二维码/扫码登录"等字样
        b) 有「切换账号」但没有「进入微信」（二维码页的固定布局）
    """
    if win is None:
        return ""
    names = []
    for ctrl, _d in _iter_controls(win, 8, max_nodes=600):
        try:
            nm = (ctrl.Name or "").strip()
        except Exception:                       # noqa: BLE001
            continue
        if not nm:
            continue
        names.append(nm)
        for w in QR_WORDS:
            if w in nm:
                return "文本:%s" % nm[:24]
    if BTN_SWITCH in names and BTN_ENTER not in names:
        return "无进入微信但有切换账号"
    return ""


def shows_qr(win) -> bool:
    """登录窗口是否**确实**停在二维码页（必须有正向证据）。"""
    try:
        return bool(qr_evidence(win))
    except Exception:                           # noqa: BLE001
        return False


# --------------------------------------------------- 登录页状态（一眼可判）
ST_ONE_CLICK = "one_click"   # 有登录态：显示头像+昵称，有「进入微信」
ST_QR = "qr"                 # 无登录态 / 票据被服务端拒：直接是二维码页
ST_LOADING = "loading"       # 窗口在，但控件树还没建好（启动后一瞬间）
ST_GONE = "gone"             # 登录窗口对象已失效

STATE_CN = {
    ST_ONE_CLICK: "一键登录（有登录态）",
    ST_QR: "扫码登录（无登录态/票据被拒）",
    ST_LOADING: "登录页加载中",
    ST_GONE: "登录窗口已消失",
}


def login_state(win) -> tuple:
    """**一次遍历**判出登录页状态，返回 (状态, 昵称, 证据说明)。

    分别调用 login_user / has_button / qr_evidence 等于把控件树（跨进程 COM
    查询，最贵）走三遍；而登录页本来就是一眼可判的三态，没必要反复读。
    """
    if win is None:
        return ST_GONE, "", "窗口不存在"
    names = []
    nick = ""
    try:
        for ctrl, _d in _iter_controls(win, 8, max_nodes=600):
            try:
                nm = (ctrl.Name or "").strip()
            except Exception:                   # noqa: BLE001
                continue
            if not nm:
                continue
            if not nick and nm.startswith(PREFIX_USER):
                nick = nm[len(PREFIX_USER):].strip()
            for w in QR_WORDS:                  # 二维码字样优先：出现即定性
                if w in nm:
                    return ST_QR, nick, "二维码证据:%s" % nm[:20]
            names.append(nm)
    except Exception as e:                      # noqa: BLE001
        return ST_GONE, nick, "读取失败:%s" % e

    if BTN_ENTER in names:
        return ST_ONE_CLICK, nick, "有「进入微信」"
    if BTN_SWITCH in names:
        return ST_QR, nick, "有「切换账号」但无「进入微信」"
    if not names:
        return ST_LOADING, nick, "控件树为空"
    return ST_LOADING, nick, "暂无明确证据（共 %d 个控件名）" % len(names)


# ----------------------------------------------------------- 后台点击原语
def _native_handle(ctrl) -> int:
    try:
        return int(ctrl.NativeWindowHandle or 0)
    except Exception:                           # noqa: BLE001
        return 0


def _makelong(lo: int, hi: int) -> int:
    return ((hi & 0xFFFF) << 16) | (lo & 0xFFFF)


def _post(hwnd: int, msg: int, wp: int, lp: int) -> bool:
    try:
        return bool(_user32.PostMessageW(wintypes.HWND(hwnd), msg,
                                         wintypes.WPARAM(wp), wintypes.LPARAM(lp)))
    except Exception:                           # noqa: BLE001
        return False


def _key_strokes(hwnd: int, vk: int) -> bool:
    """向指定句柄投递一次按键（按下+抬起），不动真实键盘状态。

    ★ 实测（2026-10-04）：**按键生效不依赖 Win32 焦点**。`SetFocus` 返回 0
    （没拿到焦点）的情况下，空格/回车照样 62~125 ms 生效 —— 因为 `WM_KEYDOWN`
    是**直接投给窗口句柄**的，Qt 收到后交给它**自己的内部焦点控件**处理；
    Qt 的焦点与 Win32 的焦点是两套独立机制。
    """
    scan = _user32.MapVirtualKeyW(vk, 0) & 0xFF
    down = 1 | (scan << 16)
    up = 1 | (scan << 16) | (1 << 30) | (1 << 31)
    return _post(hwnd, WM_KEYDOWN, vk, down) and _post(hwnd, WM_KEYUP, vk, up)


class _POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


def click(win, name: str, method: str = "mouse-btn") -> bool:
    """用指定方式点击窗口上的按钮。只返回"消息是否发出"，**不代表已生效**。

    method 取值见 `CLICK_METHODS`（旧名 `"mouse"` / `"key"` 仍兼容）：

        key-win    焦点给**窗口** → 投回车   （实测最快 62~92 ms，且不依赖按钮坐标）
        mouse-btn  鼠标三连 → **按钮**句柄   （客户区坐标；实测 110~147 ms）
        key-btn    焦点给**按钮** → 投空格   （实测 99~125 ms）
        mouse-win  鼠标三连 → **窗口**句柄   （实测 149~173 ms）

    四种方式都**只把消息投给目标句柄**，窗口在后台、被遮挡、无焦点都不影响
    （已实测：置顶窗口盖住按钮仍有效）。全程**不动真实鼠标键盘**。
    """
    method = CLICK_ALIAS.get(method, method)
    btn = _find_button(win, name)
    if btn is None:
        return False
    h_btn = _native_handle(btn) or _native_handle(win)
    h_win = _native_handle(win) or h_btn
    if not h_btn:
        return False

    if method in ("key-win", "key-btn"):
        hwnd = h_win if method == "key-win" else h_btn
        if not hwnd:
            return False
        try:                                    # 拿不到也无妨（见 _key_strokes 说明）
            _user32.SetFocus(wintypes.HWND(hwnd))
        except Exception:                       # noqa: BLE001
            pass
        if method == "key-win":
            return _key_strokes(hwnd, VK_RETURN)
        return _key_strokes(hwnd, VK_SPACE) or _key_strokes(hwnd, VK_RETURN)

    if method in ("mouse-btn", "mouse-win"):
        hwnd = h_btn if method == "mouse-btn" else h_win
        if not hwnd:
            return False
        # 屏幕坐标 → 目标窗口的**客户区**坐标（Qt 只认客户区坐标）
        try:
            r = btn.BoundingRectangle
            pt = _POINT((r.left + r.right) // 2, (r.top + r.bottom) // 2)
            _user32.MapWindowPoints(None, wintypes.HWND(hwnd),
                                    ctypes.byref(pt), 1)
            lp = _makelong(pt.x, pt.y)
        except Exception:                       # noqa: BLE001
            return False
        return (_post(hwnd, WM_MOUSEMOVE, 0, lp)
                and _post(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lp)
                and _post(hwnd, WM_LBUTTONUP, 0, lp))

    return False


QR_STABLE = 3.0       # 二维码页的**正向证据**需稳定这么多秒才敢下结论
GONE_GRACE = 12.0     # 登录窗口消失后，再等这么多秒看主界面是否出现
CLICK_RETRY = 0.4     # 一招发出去后等多久没反应就**换下一招**（实测最慢一招 173 ms 见效，
                      # 0.4 s 留足余量；再短有把有效招误判成无效的风险）
MAX_CLICKS = len(CLICK_METHODS)  # 级联走满一遍就够（每招都是不同通路，重复发无意义）
QR_CHECK_EVERY = 0.8  # 二维码证据检查限频（遍历控件树较贵，不必每轮都做）


def click_enter(win, wait: float = 25.0, log=print, base_main: int = 0,
                grace: float = 60.0, poll: float = 0.15,
                pid: int = 0, ready: float = 0.0) -> tuple:
    """点「进入微信」并判定结果。返回 (是否进入主界面, 方式)。

    设计目标：**「进入微信」一出现就点**，不额外等"稳定"。两个阶段：

      `ready > 0`  阶段一：按 `poll` 粒度轮询「进入微信」按钮，**出现即进入阶段二**
                   （最多等 ready 秒；期间若先出现二维码正向证据 ⇒ 判被拒）。
      `wait`       阶段二：**级联点击** + 确认的总时长。按 `CLICK_METHODS` 一招一招
                   来；某一招发出去后 `CLICK_RETRY` 秒内没动静 ⇒ **换下一招**（不同
                   通路，比同招重发有信息量）；走满一遍仍无反应 ⇒ 只等不点。

    判据只有两条，且都必须有**正向证据**：

        成功：**本 `pid` 实例**的主界面窗口（mmui::MainWindow）出现
        被拒：登录窗口出现二维码页的正向证据（qr_evidence），且稳定 QR_STABLE 秒

    `pid` 是本次新启动实例的进程号。**传了它就必须按它判**（见 appeared()）。
    **绝不能**用"「进入微信」按钮消失了"当失败判据（见 qr_evidence 说明）。

    第二个返回值的含义：
        "qr" —— 有正向证据，确实被服务端拒了（调用方据此判定登录态失效）
        ""   —— **没确认**（超时或窗口消失），含义是"不知道"，调用方不得据此
                判定失效、更不得结束实例
    """
    def appeared() -> bool:
        """本次实例的主界面是否已出现。

        ★ **必须按 `pid` 判，不能用「主窗口总数 > base_main」**：登录过程中会把旧
        实例的窗口显示回来（`login.restore_windows` 提前恢复），那些"复活"的窗口
        会让总数凭空虚高、把**别人的窗口**算成本次登录的成果（实测过一次假成功：
        账号停在登录页、「进入微信」一次都没点，却被判成功）。
        """
        if pid:
            return main_window_of(pid) is not None
        return len(main_windows()) > base_main      # 老调用方（未传 pid）的兜底

    def qr_stable(since: list, next_at: list) -> bool:
        """二维码正向证据是否已**连续稳定** QR_STABLE 秒（限频检查）。

        为什么限频：`qr_evidence` 要遍历控件树（实测 ~50 ms），而"按钮在不在"
        只要 `FindControl` 命中即返回（~16 ms）—— 热路径上不能每轮都付这个钱。
        """
        now = time.time()
        if now < next_at[0]:
            return False
        next_at[0] = now + QR_CHECK_EVERY
        if qr_evidence(win):
            if not since[0]:
                since[0] = now
            return now - since[0] >= QR_STABLE
        since[0] = 0.0
        return False

    def poll_until(timeout: float, allow_qr: bool = True,
                   watch_button: bool = False) -> str:
        end = time.time() + timeout
        qr_since = 0.0
        gone_at = 0.0
        while time.time() < end:
            if appeared():
                return "main"
            # ★ 有 pid 就按 pid 判登录窗口在不在：比 `find_login_window()` 更准
            #   （多开时别人的登录窗口不算数），也更快（不必查微信 pid 集合）。
            lw = login_window_of(pid) if pid else find_login_window()
            if lw is None:
                # ★ 登录窗口消失 ≠ 失败：进入主界面时登录窗口**先销毁**、主界面
                #   **稍后**才建出来（实测 16.4s 窗口没了、17.9s 主界面才可检测）。
                if not gone_at:
                    gone_at = time.time()
                elif time.time() - gone_at >= GONE_GRACE:
                    return "gone"
            else:
                gone_at = 0.0
                if allow_qr and qr_evidence(win):
                    if not qr_since:
                        qr_since = time.time()
                    elif time.time() - qr_since >= QR_STABLE:
                        return "qr"
                else:
                    qr_since = 0.0
                    # 无二维码证据 + 按钮已消失 → 正在进主界面，立即转入等待
                    if watch_button and not has_button(win, BTN_ENTER):
                        return "entering"
            time.sleep(poll)
        return ""

    # ---- 阶段一：等「进入微信」出现（**出现即点**，不等"稳定"）
    #      判"按钮在不在"直接调 has_button（`FindControl` 命中就返回，实测 ~16 ms），
    #      不用 login_state 那种"限 600 节点整树遍历"（~50 ms 起，且必须遍历完
    #      才知道有没有按钮）；轮询粒度 = poll（默认 0.15 s）。
    if ready > 0:
        t0 = time.time()
        end = t0 + ready
        qr_since, qr_next = [0.0], [0.0]
        hit = False
        while time.time() < end:
            if appeared():
                return True, "主界面已出现"
            try:
                hit = has_button(win, BTN_ENTER)
            except Exception:                   # noqa: BLE001
                hit = False
            if hit:
                log("  「进入微信」已出现（启动后 %.1f 秒）→ 立即点击"
                    % (time.time() - t0))
                break
            if qr_stable(qr_since, qr_next):
                log("  登录页已是二维码页（正向证据）—— 登录态已失效")
                return False, "qr"
            time.sleep(poll)
        if not hit:
            log("  %.0f 秒内未等到「进入微信」按钮" % ready)
            return False, ""

    # ---- 阶段二：**级联点击** + 细粒度确认。
    #      按 CLICK_METHODS 顺序一招一招来：某一招发出去后 CLICK_RETRY 秒内
    #      主界面出现 / 按钮消失 ⇒ 收工；没动静（这一招没落到按钮上）⇒ **换下一招**
    #      （不同通路，比"同招重发"有信息量）。走满一遍仍无反应 ⇒ 只等不点。
    #      依据：两轮实测 8 招 100% 有效、最慢 173 ms ⇒ 0.4 s 的判定窗足够。
    sent_any = False
    attempts = 0
    gone = False
    end = time.time() + wait
    qr_since, qr_next = [0.0], [0.0]
    while time.time() < end and not gone:
        if appeared():
            return True, ("已点击 %d 次" % attempts) if attempts else "主界面已出现"
        try:
            still = has_button(win, BTN_ENTER)
        except Exception:                       # noqa: BLE001
            still = True
        if not still:
            # 按钮没了、又没有二维码证据 —— 大概率正在进主界面，别再点
            log("  「进入微信」已消失（无二维码证据）→ 判为正在进入，停止重试")
            sent_any = True
            break
        if qr_stable(qr_since, qr_next):
            log("  点击后登录页切为二维码页（正向证据，稳定 %.0f 秒）→ 止损"
                % QR_STABLE)
            return False, "qr"
        if attempts >= MAX_CLICKS:
            time.sleep(poll)                    # 级联走满还没反应 —— 只等不点
            continue
        m = CLICK_METHODS[min(attempts, len(CLICK_METHODS) - 1)]
        t0 = time.time()
        try:
            sent = click(win, BTN_ENTER, m)
        except Exception as e:                  # noqa: BLE001
            log("  点击方式 %s 异常：%s" % (CLICK_CN.get(m, m), e))
            sent = False
        attempts += 1
        if not sent:
            log("  第 %d 招「%s」：消息未能发出（换下一招）" % (attempts, CLICK_CN.get(m, m)))
            continue
        sent_any = True
        log("  第 %d 招「%s」已发出" % (attempts, CLICK_CN.get(m, m)))
        # 细粒度确认 CLICK_RETRY 秒：主界面出现 / 按钮消失就早退；
        # 都没发生 ⇒ 这一招没落到按钮上，循环回去**换下一招**。
        t1 = time.time() + CLICK_RETRY
        while time.time() < t1:
            if appeared():
                log("  点击生效（第 %d 招「%s」，%.2f 秒进入主界面）"
                    % (attempts, CLICK_CN.get(m, m), time.time() - t0))
                return True, CLICK_CN.get(m, m)
            try:
                if not has_button(win, BTN_ENTER):
                    log("  「进入微信」已消失 → 判定为正在进入"
                        "（第 %d 招已生效，转等主界面）" % attempts)
                    gone = True                 # 按钮消失 = 正在进主界面
                    break
            except Exception:                   # noqa: BLE001
                pass
            time.sleep(poll)

    if sent_any or gone:
        log("  宽限等待（最多 %.0f 秒，只等主界面）" % grace)
        if poll_until(grace, allow_qr=False) == "main":
            log("  主界面已出现（宽限等待）")
            return True, "宽限等待"
        if qr_evidence(win):
            return False, "qr"
    return False, ""


def main_windows() -> list:
    """微信主界面窗口（mmui::MainWindow）列表。

    这是"真的登录进去了"的可靠判据 —— 登录窗口消失也可能是被关掉了。
    """
    return _windows_of_class(MAIN_CLASS)


def has_main_window() -> bool:
    return bool(main_windows())


def main_window_of(pid: int):
    """取某个进程的主界面窗口（多开时用来定位具体是哪个实例）。"""
    ws = _windows_of_class(MAIN_CLASS, pid)
    return ws[0] if ws else None


def hide_main_window(win, log=print) -> int:
    """把主界面窗口**隐藏**起来（进程、登录态、窗口内容全部保留）。

    这是多开的必要前置动作：已有实例的主窗口**可见**时，再启动微信会被"转交"给
    那个实例（不产生独立进程）；主窗口**不可见**时才会真正新建实例。

    ★★ 必须用 `ShowWindow(SW_HIDE)`，不能用 `WM_CLOSE`：`WM_CLOSE` 会被微信当作
    "关闭主窗口 → 隐藏到托盘"，**微信内部（Qt 层）会置一个隐藏标记**，而这个标记只有
    Qt 自己的 `show()`（即用户点标题栏按钮）能清。从外部用 `ShowWindow` 怎么组合都
    清不掉（`SW_RESTORE`、`SW_SHOW`、`SW_MINIMIZE`+`SW_RESTORE`、
    `AttachThreadInput` 强制激活、`WM_SYSCOMMAND` 的 `SC_MINIMIZE`/`SC_MAXIMIZE`
    实测全部无效）：窗口看得见、画得动，但**鼠标键盘全被丢弃**。
    `SW_HIDE` 只是 Win32 层的可见性变化，不经过微信的关闭逻辑，恢复后功能完好。

    **返回被隐藏的窗口句柄**（0 表示没成功），调用方拿到它就能用 `show_window`
    原样显示回来 —— 隐藏只是"让它暂时不可见"，不是让用户丢掉窗口。
    """
    if win is None:
        return 0
    hwnd = _native_handle(win)
    if not hwnd:
        log("  主窗口没有原生句柄，无法隐藏")
        return 0
    h = int(hwnd)
    log("  隐藏主窗口（SW_HIDE，不触发微信的关闭逻辑）")
    try:
        _user32.ShowWindow(wintypes.HWND(h), SW_HIDE)
    except Exception as e:                      # noqa: BLE001
        log("  隐藏窗口失败：%r" % (e,))         # 别再静默吞掉真实错误
        return 0
    return h


def close_window(hwnd: int, log=print) -> bool:
    """（**兜底专用**）对窗口补发 `WM_CLOSE` —— 走微信自己的"关闭到托盘"逻辑。

    只在"直接隐藏不足以让微信新建实例（新实例被转交）"时才用。注意它会让微信
    置上那个内部隐藏标记，窗口恢复后可能需要用户手动点一下标题栏最小化/最大化
    （见 `hide_main_window` 说明）—— 所以它是**退路**，不是常用路径。

    调用前应先把窗口 `show_window` 出来：微信对**已隐藏**的窗口常常直接忽略
    `WM_CLOSE`（实测：对一个已处于异常隐藏态的窗口发 WM_CLOSE，窗口纹丝不动）。
    """
    if not hwnd:
        return False
    h = int(hwnd)
    try:
        if not _user32.IsWindow(wintypes.HWND(h)):
            return False
        log("  对窗口 %s 补发 WM_CLOSE（兜底：让它进入微信的托盘状态）" % h)
        return _post(h, WM_CLOSE, 0, 0)
    except Exception as e:                      # noqa: BLE001
        log("  补发 WM_CLOSE 失败：%r" % (e,))
        return False


def show_window(hwnd: int, log=print) -> bool:
    """把 `hide_main_window` 隐藏的主窗口**原样显示回来**。

    窗口只是被隐藏，**句柄依然有效**，位置/大小/内容全部保留，显示回来即可，
    **不影响账号在线状态**。若用户自己已从托盘点开了窗口、或已退出该账号，
    这里会安全地什么都不做。收起方式既然是 `SW_HIDE`，恢复就只需 `SW_SHOW`。
    """
    if not hwnd:
        return False
    # ★ 句柄必须**全程保持 int**：Python 3.11 上 `int(wintypes.HWND(x))` 会抛
    #   ValueError（它拿裸内存去当字符串解析，报 "invalid literal for int()"）。
    #   早先版本在这里用它格式化日志，异常被 except 吞掉 —— 窗口其实已经显示
    #   回来了，函数却返回 False，白折腾一轮才查出。
    h = int(hwnd)
    try:
        if not _user32.IsWindow(wintypes.HWND(h)):
            return False                        # 账号已被用户退出，句柄已失效
        _user32.ShowWindow(wintypes.HWND(h), SW_SHOW)
        if not _user32.IsWindowVisible(wintypes.HWND(h)):
            _user32.ShowWindow(wintypes.HWND(h), SW_RESTORE)    # 兜底：极少见
        log("  已把窗口 %s 显示回来" % h)
        return True
    except Exception as e:                      # noqa: BLE001
        log("  显示窗口失败：%r" % (e,))         # 别再静默吞掉真实错误
        return False


def wait_login_window_gone(timeout=60) -> bool:
    """等到登录窗口消失，说明已进入主界面。0.4 秒粒度，消失即返回。"""
    if not _ensure():
        return False
    end = time.time() + timeout
    while time.time() < end:
        if find_login_window() is None:
            return True
        time.sleep(0.4)
    return False


def is_login_page() -> bool:
    return find_login_window() is not None
