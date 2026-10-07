# -*- coding: utf-8 -*-
"""微信登录窗口的 UI 自动化（全程后台，不碰真实鼠标键盘）。"""
from __future__ import annotations

import contextlib
import ctypes
import os
import time
from ctypes import wintypes
auto = None
_OK = None                      # None=尚未尝试 / True=可用 / False=不可用
_IMPORT_ERR = ""


def _ensure() -> bool:
    """确保 uiautomation 已导入（幂等）。返回是否可用。"""
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

# 点「进入微信」的级联顺序（一招没反应就换下一招，不是同招反复发）：
#   key-win    键盘 VK_RETURN → 窗口句柄
#   mouse-btn  鼠标三连      → 按钮句柄
#   key-btn    键盘 VK_SPACE → 按钮句柄
#   mouse-win  鼠标三连      → 窗口句柄
CLICK_METHODS = ("key-win", "mouse-btn", "key-btn", "mouse-win")

# 旧名兼容
CLICK_ALIAS = {"mouse": "mouse-btn", "key": "key-btn"}

# 日志里显示的中文名
CLICK_CN = {"key-win": "键盘回车→窗口", "mouse-btn": "鼠标三连→按钮",
            "key-btn": "键盘空格→按钮", "mouse-win": "鼠标三连→窗口"}


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
    """把控件（默认整个屏幕）截图存成 PNG，返回绝对路径；失败返回空串。"""
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
_WX_PIDS_TTL = 0.3              # pid 集合缓存时长


def _top_windows() -> list:
    """[(hwnd, pid, 是否可见)] —— 纯 Win32 EnumWindows。"""
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
    """全部微信进程 pid（含子进程）：按进程名过滤，不读命令行。"""
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
    """老做法：整树枚举桌面的直接子控件，只作兜底。"""
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
    """全部此类窗口的 UIA 控件；给了 pid 就只取该进程的。"""
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
                if pids is None:                # 枚举失败 ⇒ 走老路
                    return _legacy_windows(cls, pid)
            if wpid not in pids:
                continue
        if WND_VISIBLE_ONLY and not vis:
            continue                            # 隐藏的消息窗口不读 UIA
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


def login_windows() -> list:
    """全部登录窗口（多开时可能同时有好几个，必须能分辨）。"""
    return _windows_of_class(LOGIN_CLASS)


def login_window_of(pid: int):
    """取某个进程自己的登录窗口。"""
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


# 实测（2026-10-07 抓真实扫码页）：整页只有 9 个控件名 ——
#   微信 / 关闭 / 网络代理设置 / 二维码 / 扫码登录 / 仅传输文件
# ★ 扫码页**没有**「切换账号」（那是"有登录态"时才有的），故它只能当兜底判据。
QR_WORDS = ("扫码登录", "二维码", "扫描二维码", "二维码登录", "扫一扫登录",
            "重新扫码", "扫码验证")

# 「需在手机上完成登录」页的正向文案（首次登录/设备未勾选自动登录时出现）
PHONE_WORDS = ("需在手机上完成登录", "需在手机上确认", "需要通过手机确认",
               "需手机确认", "手机确认", "正在确认安全验证结果",
               "安全验证", "确认登录")


def _iter_controls(root, max_depth: int = 14, max_nodes: int = 0):
    """深度优先遍历控件（返回 (控件, 深度)）；max_nodes > 0 时限制访问节点数。"""
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
    """二维码页的正向证据（空串 = 没有证据）。"""
    if win is None:
        return ""
    try:
        state, _nick, why = login_state(win)
    except Exception:                           # noqa: BLE001
        return ""
    return why if state == ST_QR else ""


def shows_qr(win) -> bool:
    """登录窗口是否**确实**停在二维码页（必须有正向证据）。"""
    try:
        return bool(qr_evidence(win))
    except Exception:                           # noqa: BLE001
        return False


ST_ONE_CLICK = "one_click"   # 有登录态：显示头像+昵称，有「进入微信」
ST_QR = "qr"                 # 无登录态 / 票据被服务端拒：直接是二维码页
ST_PHONE = "phone"           # 票据有效但需在手机上确认：登录本身会成功
ST_FAIL = "fail"             # 服务端明确拒绝：登录失败/需重试
ST_LOADING = "loading"       # 窗口在，但控件树还没建好（启动后一瞬间）
ST_GONE = "gone"             # 登录窗口对象已失效

STATE_CN = {
    ST_ONE_CLICK: "一键登录（有登录态）",
    ST_QR: "扫码登录（无登录态/票据被拒）",
    ST_PHONE: "待手机确认（票据有效，需在手机上点确认）",
    ST_FAIL: "登录被服务端拒绝（需重试）",
    ST_LOADING: "登录页加载中",
    ST_GONE: "登录窗口已消失",
}

# 服务端拒绝的失败文案（出现即判失败，别再傻等主界面）
FAIL_WORDS = ("未能登录", "登录失败", "请检查网络设置后再试",
              "账号已登录", "该账号已登录", "正在升级中，请稍后再试")


def _hit(names, words):
    """names 里命中任一关键词即返回该词，否则返回空串。"""
    for nm in names:
        for w in words:
            if w in nm:
                return w
    return ""


def login_state(win) -> tuple:
    """一次遍历判出登录页状态，返回 (状态, 昵称, 证据说明)。"""
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
            names.append(nm)
    except Exception as e:                      # noqa: BLE001
        return ST_GONE, nick, "读取失败:%s" % e

    f = _hit(names, FAIL_WORDS)
    if f:
        return ST_FAIL, nick, "失败文案:%s" % f[:20]

    # 二维码优先于「切换账号」：切号按钮在二维码页也存在
    q = _hit(names, QR_WORDS)
    if q:
        return ST_QR, nick, "二维码证据:%s" % q[:20]

    p = _hit(names, PHONE_WORDS)
    if p:
        return ST_PHONE, nick, "手机确认:%s" % p[:20]

    if BTN_ENTER in names:
        return ST_ONE_CLICK, nick, "有「进入微信」"
    if BTN_SWITCH in names:
        # 兜底：有「切换账号」而无「进入微信」⇒ 二维码页
        return ST_QR, nick, "有「切换账号」但无「进入微信」"
    if not names:
        return ST_LOADING, nick, "控件树为空"
    return ST_LOADING, nick, "暂无明确证据（共 %d 个控件名）" % len(names)


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
    """
    scan = _user32.MapVirtualKeyW(vk, 0) & 0xFF
    down = 1 | (scan << 16)
    up = 1 | (scan << 16) | (1 << 30) | (1 << 31)
    return _post(hwnd, WM_KEYDOWN, vk, down) and _post(hwnd, WM_KEYUP, vk, up)


class _POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


def click(win, name: str, method: str = "mouse-btn") -> bool:
    """用指定方式点击窗口上的按钮。只返回消息是否发出，不代表已生效。"""
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
        try:                                    # 拿不到也无妨
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
        # 屏幕坐标 → 目标窗口的客户区坐标
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


QR_STABLE = 3.0       # 二维码页正向证据需稳定秒数
GONE_GRACE = 12.0     # 登录窗口消失后，再等这么多秒看主界面是否出现
CLICK_RETRY = 0.4     # 一招发出后没反应就换下一招的等待
MAX_CLICKS = len(CLICK_METHODS)  # 级联走满一遍即止
QR_CHECK_EVERY = 0.8  # 二维码证据检查限频（遍历控件树较贵，不必每轮都做）
PHONE_CHECK_EVERY = 1.0  # 等主界面期间查"手机确认页"的限频（1 秒一次足够）


def _wait_main(win, timeout: float, poll: float, pid: int,
               appeared, log, last_state: list) -> str:
    """等主界面出现的全过程中持续查登录页状态，并及时播报。

    手机确认页要等用户去手机上点，所以一识别出来就提示；期间主界面一出现即返回。
    last_state 是 [状态, 证据] 的共享容器，返回后供调用方判最终结论。
    本实例进程退出即返回 "exited"，不再空等。
    """
    from . import process as _proc

    def main_pids_of():
        return _proc.main_pids()

    end = time.time() + timeout
    told_phone = [False]
    told_qr = [False]
    told_fail = [False]
    next_probe = [0.0]
    while time.time() < end:
        if appeared():
            return "main"
        if pid and pid not in main_pids_of():
            # 本实例进程没了（用户关掉了）⇒ 别再空等
            log("  本实例进程已退出，停止等待主界面")
            return "exited"
        now = time.time()
        if now >= next_probe[0]:
            next_probe[0] = now + PHONE_CHECK_EVERY
            try:
                w = login_window_of(pid) if pid else find_login_window()
                if w is not None:
                    st, _nick, why = login_state(w)
                    # 已经确认在等手机确认时，别被后来的"窗口消失"降级覆盖
                    if st != ST_GONE or last_state[0] != ST_PHONE:
                        last_state[0], last_state[1] = st, why
                    if st == ST_PHONE and not told_phone[0]:
                        told_phone[0] = True
                        log("  ⚠ 需要你在手机微信上点确认才能继续（%s）" % why)
                    if st == ST_QR and not told_qr[0]:
                        told_qr[0] = True
                        log("  ✗ 登录页已切为二维码（%s）—— 票据被拒，"
                            "需重新扫码采集登录态" % why)
                    if st == ST_FAIL and not told_fail[0]:
                        told_fail[0] = True
                        log("  ✗ 服务端拒绝了本次登录（%s）" % why)
            except Exception as e:              # noqa: BLE001
                log("  查登录页状态异常：%r" % (e,))
        time.sleep(poll)
    return ""


def click_enter(win, wait: float = 25.0, log=print, base_main: int = 0,
                grace: float = 60.0, poll: float = 0.15,
                pid: int = 0, ready: float = 0.0) -> tuple:
    """点「进入微信」并判定结果。返回 (是否进入主界面, 方式)。
    方式 "qr" = 确实被拒；"" = 没确认，调用方不得据此判定失效。
    """
    def appeared() -> bool:
        """本次实例的主界面是否已出现。
        """
        if pid:
            return main_window_of(pid) is not None
        return len(main_windows()) > base_main      # 老调用方（未传 pid）的兜底

    def qr_stable(since: list, next_at: list) -> bool:
        """二维码正向证据是否已连续稳定 QR_STABLE 秒（限频检查）。"""
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
            st, _nick, why = login_state(win)
            if st == ST_PHONE:
                log("  登录页要求在手机上确认登录（%s）—— 票据有效，"
                    "等手机点确认" % why)
                return False, "phone"
            log("  %.0f 秒内未等到「进入微信」按钮（%s）" % (ready, why))
            return False, ""

    sent_any = False
    attempts = 0
    gone = False
    seen_phone = False                   # 已确认登录页在等手机确认
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
            # 按钮消失有两种可能：正在进入，或页面换了状态。先查异常证据
            st, _nick, why = login_state(win)
            if st == ST_QR:
                log("  「进入微信」已消失，登录页是二维码页（%s）→ 票据被拒"
                    % why)
                return False, "qr"
            if st == ST_FAIL:
                log("  「进入微信」已消失，服务端拒绝（%s）" % why)
                return False, "fail"
            if st == ST_PHONE:
                log("  登录页要求在手机上确认登录（%s）→ 停止点击，等手机确认"
                    % why)
                sent_any = True
                seen_phone = True
                break
            # 无二维码/失败/手机确认证据 → 正在进入主界面
            log("  「进入微信」已消失（无异常证据）→ 判为正在进入，停止重试")
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
        # 确认 CLICK_RETRY 秒：主界面出现或按钮消失就早退，否则换下一招
        t1 = time.time() + CLICK_RETRY
        stopped = ""                          # 非空 = 该退出点击循环了
        while time.time() < t1:
            if appeared():
                log("  点击生效（第 %d 招「%s」，%.2f 秒进入主界面）"
                    % (attempts, CLICK_CN.get(m, m), time.time() - t0))
                return True, CLICK_CN.get(m, m)
            try:
                if not has_button(win, BTN_ENTER):
                    # 按钮消失 ≠ 一定在进主界面：页面可能换成了手机确认/二维码/失败
                    st, _nick, why = login_state(win)
                    if st == ST_PHONE:
                        log("  按钮消失，登录页要求在手机上确认（%s）" % why)
                        stopped = "phone"
                    elif st in (ST_QR, ST_FAIL):
                        log("  按钮消失，登录页已变为%s（%s）"
                            % (STATE_CN[st], why))
                        stopped = st
                    else:
                        log("  「进入微信」已消失（无异常证据）→ 判定为正在进入"
                            "（第 %d 招已生效，转等主界面）" % attempts)
                        gone = True         # 按钮消失 = 正在进主界面
                    break
            except Exception:                   # noqa: BLE001
                pass
            time.sleep(poll)
        if stopped:
            if stopped == ST_PHONE:
                sent_any = True                # 点过了，等手机即可
                break
            return False, stopped

    if sent_any or gone:
        log("  宽限等待（最多 %.0f 秒，只等主界面）" % grace)
        _last_state = [ST_PHONE if seen_phone else ST_LOADING, ""]
        r = _wait_main(win, grace, poll, pid, appeared, log, _last_state)
        if r == "main":
            log("  主界面已出现（宽限等待）")
            return True, "宽限等待"
        if r == "exited":
            return False, "exited"
        st, why = _last_state[0], _last_state[1]
        if st == ST_PHONE:
            log("  仍在等待手机确认（%s）" % why)
            return False, "phone"
        if st in (ST_QR, ST_FAIL):
            return False, st
    return False, ""


def main_windows() -> list:
    """微信主界面窗口列表。"""
    return _windows_of_class(MAIN_CLASS)


def has_main_window() -> bool:
    return bool(main_windows())


def main_window_of(pid: int):
    """取某个进程的主界面窗口（多开时用来定位具体是哪个实例）。"""
    ws = _windows_of_class(MAIN_CLASS, pid)
    return ws[0] if ws else None


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
