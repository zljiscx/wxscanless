# -*- coding: utf-8 -*-
"""登录：把一个已收录的账号免扫码登进主界面。

流程（用户指定）：
    0. 已在线的账号**不允许再次登录**
    1. 按"复用最小空闲槽位"算出本次实例会占用的 `net*` 槽位
    2. 收起已有实例的主窗口（否则新启动会被"转交"，不产生新进程）
    3. **热替换** `global_config` + `.crc`（成对，可回滚）
    4. 把该账号的 `host` 铺进目标槽位（启动前就位）
    5. 启动实例 → **后台**等到登录窗口 → **后台**点「进入微信」
    6. **后台**判定是否真的进了主界面

全程**不碰真实鼠标键盘**：点击是向按钮句柄投递鼠标消息，状态判定走 UI Automation
（跨进程读取控件树）。因此窗口被别的程序挡住、或用户正在用鼠标，都不影响本流程。

为什么 config 必须"热替换"而不是直接覆盖：微信运行期间该文件被内存映射独占，
直接覆盖会报 Errno 22；但**改名可以** —— 把原文件 rename 掉再写新的，已在跑的
实例仍持有旧 inode、内容不受影响，新实例读到新版本。
"""
from __future__ import annotations

import os
import shutil
import time

from . import collect, mmkv, paths, process, slot, ui
from .vault import CONFIG_FILES

LIVE_BAK = ".livebak"          # 热替换时被改名让位的旧配置
POLL = 0.15                    # 统一轮询粒度（UI 读取一次 ~16~50 ms，0.15 s 足够细）
WINDOW_WAIT = 15.0             # 启动后等"本实例登录窗口"；超时 ⇒ 疑似启动被"转交"
RETRY_WAIT = 60.0              # 兜底重试的等待（走老方式 WM_CLOSE，启动较慢）
ENTER_WAIT = 10.0              # 等「进入微信」按钮出现的上限（出现即点，不是固定等）
CLICK_WAIT = 8.0               # 点击 + 确认的总时长（点完没反应会立刻重发）


# --------------------------------------------------------------- 配置装载
def _remove_quiet(path: str) -> bool:
    """尽力删掉一个临时文件；**删不掉也不抛异常**。

    ★ 本机有 "safe-delete" 拦截：`os.remove` 被改写成"送回收站"，在 F: 卷上
    可能直接失败（实测：文件仍被微信占用时必失败，报 `trash-failed`）。
    这类文件只是"让位"用的旧副本，删不掉**绝不该**拖垮整个装载流程 ——
    早先这里让装载返回 0，界面报"config 载入不完整"，完全看不出真因。
    """
    try:
        os.remove(path)
    except OSError:
        pass
    return not os.path.exists(path)


def sweep_backups(env) -> int:
    """清掉 config 目录里遗留的 `*.livebak`（尽力而为，返回清掉的个数）。

    为什么需要：`clean_temp()` 只在"实例全部退出"时被调用；若用户自己从托盘
    退出微信，`clean_temp` 可能永远不执行，`.livebak` 就永久留着。它不影响
    微信，但会让下一次热替换"让位"失败 ⇒ 最好每次装载前顺手扫一遍。
    """
    if not env.config_dir:
        return 0
    try:
        names = os.listdir(env.config_dir)
    except OSError:
        return 0
    n = 0
    for fn in names:
        if LIVE_BAK in fn:
            if _remove_quiet(os.path.join(env.config_dir, fn)):
                n += 1
    return n


def install_config_pair(env, src_dir: str, log=print) -> int:
    """把 src_dir 里的 `global_config` **一对**装到 live —— 成对、可回滚。

    两条硬规则（踩过坑）：
      · 源缺任一文件 → 整体放弃，绝不做半套（半套会让微信读不出登录态，
        表现就是"明明有档案却每次都要扫码"）；
      · 逐个写入，任一失败 → 回滚已写入的那个。
    """
    src = {}
    for fn in CONFIG_FILES:
        p = os.path.join(src_dir, fn)
        if not os.path.isfile(p):
            log("  源缺少 %s —— 放弃本次装载（不做半套）" % fn)
            return 0
        src[fn] = p

    sweep_backups(env)                      # 顺手清残留，别让历史包袱拖垮本次装载

    done = []
    for fn, s in src.items():
        d = os.path.join(env.config_dir, fn)
        try:
            shutil.copy2(s, d)                  # 未被占用时直接覆盖，最干净
            done.append((fn, None))
            continue
        except OSError:
            pass
        bak = d + LIVE_BAK
        if os.path.exists(bak):                 # 旧备份让不开名 ⇒ 换个唯一名字
            if not _remove_quiet(bak):
                bak = "%s.%d%s" % (d, int(time.time() * 1000) % 100000, LIVE_BAK)
        try:
            if os.path.exists(d):
                os.rename(d, bak)               # 被内存映射挡住时先改名让位
            shutil.copy2(s, d)
            done.append((fn, bak))
        except OSError as e:
            log("  写入 %s 失败：%s —— 回滚本次装载" % (fn, e))
            for f2, b2 in done:
                if b2 and os.path.exists(b2):
                    try:
                        shutil.copy2(b2, os.path.join(env.config_dir, f2))
                    except OSError:
                        pass
            return 0
    return len(done)


def clean_temp(env) -> None:
    """实例全退出后，清掉热替换留下的 .livebak。"""
    if not env.config_dir or process.count_instances():
        return
    try:
        names = os.listdir(env.config_dir)
    except OSError:
        return
    for fn in names:
        if LIVE_BAK in fn:                  # 用 in 而不是 endswith：兜住唯一名
            _remove_quiet(os.path.join(env.config_dir, fn))


# --------------------------------------------------------------- 进入判定
def _entered(env, pid: int, acc, target_slot: str) -> bool:
    """是否真的进了主界面（两条独立证据，任一成立）。

    ★ 判据一律围绕**本实例的 pid**，**不能**用「主窗口总数 > base_main」：
    登录过程中会把旧实例的窗口显示回来（`restore_windows`），总数判据会把
    那些"复活"的窗口算成本次登录的成果 —— 2026-10-04 实测到的假成功就是
    这么来的（第二个账号停在登录页，却报"已登录"）。
    """
    try:
        if ui.main_window_of(pid) is not None:
            return True                         # 本实例的主界面窗口出现
    except Exception:                           # noqa: BLE001
        pass
    if acc.uin and acc.uin in slot.monitordata_uins(env, target_slot):
        return True                             # 槽位写下本账号 uin = 已登录
    return False


def wait_entered(env, pid: int, acc, target_slot: str,
                 timeout: float = 120.0, log=print) -> bool:
    t0 = time.time()
    last = 0.0
    while time.time() - t0 < timeout:
        if _entered(env, pid, acc, target_slot):
            return True
        if process.main_pids() and pid not in process.main_pids():
            # 实例已经没了：可能进程被回收/崩溃
            log("  实例已退出，停止等待")
            return False
        if time.time() - last > 15:
            last = time.time()
            log("  等待进入主界面…（已 %.0f 秒）" % (time.time() - t0))
        time.sleep(POLL)
    return False


# --------------------------------------------------------------- 主流程
def _hide_existing(log) -> list:
    """隐藏所有**可见**的已登录实例主窗口，返回被隐藏的窗口句柄列表。

    为什么非收不可：微信的"单实例转交"——已有实例的主窗口**可见**时，再启动
    微信会被转交给它而不产生新进程，多开就无从谈起。

    ★ v1.4.3：收起方式由 `WM_CLOSE`（微信会置内部隐藏标记 → 窗口恢复后"点不动"）
    改为 `SW_HIDE`（纯 Win32 可见性变化，微信无感，恢复后功能完好）。
    见 `ui.hide_main_window` 的说明。
    """
    out = []
    for p in process.main_pids_ordered():
        try:
            mw = ui.main_window_of(p)
        except Exception:                       # noqa: BLE001
            mw = None
        if mw is None:
            continue
        h = ui.hide_main_window(mw, log=log)
        if h:
            out.append(h)
    if out:
        time.sleep(1.2)                         # 等窗口真的收起，再启动新实例
        log("  已收起 %d 个已有实例的主窗口（进程保留，随后自动显示回来）"
            % len(out))
    return out


def restore_windows(hidden: list, log=print) -> int:
    """把 `_hide_existing` 收起的窗口显示回来。幂等：恢复过的会从列表里移除。"""
    n = 0
    while hidden:
        if ui.show_window(hidden.pop(), log=log):
            n += 1
    return n


def login_account(env, vault, acc, log=print, hide_existing: bool = True,
                  hidden: list = None) -> dict:
    """免扫码登录一个已收录账号。返回 {ok, stage, detail, pid, slot, state}。

    `hidden`：可选列表，用来**接收**本次被收进托盘的窗口句柄。流程正常时本函数
    会自己把它们显示回来；若中途早退（如启动失败），调用方应对该列表再调一次
    `restore_windows` 兜底 —— 别把用户已登录的窗口留在托盘里。
    """
    hidden = hidden if hidden is not None else []
    res = {"ok": False, "stage": "", "detail": "", "pid": 0,
           "slot": "", "state": ""}

    if not env.exe_path or not os.path.isfile(env.exe_path):
        res.update(stage="no-exe", detail="未找到微信主程序")
        return res

    # 0) 已在线 → 不允许再次登录
    online = collect.online_accounts(env, vault)
    if acc.wxid in online:
        res.update(ok=True, stage="already-online", slot=online[acc.wxid],
                   detail="「%s」已经在线（槽位 %s）" % (acc.title, online[acc.wxid]))
        log("  " + res["detail"])
        return res

    # 1) 目标槽位 = 最小空闲槽位
    target = slot.free_slot(env)
    if not target:
        res.update(stage="full",
                   detail="微信最多 %d 个实例，当前已满，请先退出一个"
                          % slot.MAX_INSTANCES)
        return res
    res["slot"] = target
    log("  目标槽位：%s（按最小空闲槽位规则）" % target)

    # 2) 登录态必须齐备
    if not vault.has_config(acc.wxid):
        res.update(stage="no-config", detail="档案里没有 config 对，无法免扫码登录")
        return res
    if not vault.has_host(acc.wxid):
        res.update(stage="no-host", detail="档案里没有 host，无法免扫码登录")
        return res
    owner = vault.cfg_owner(acc.wxid)
    if owner and owner != acc.wxid:
        res.update(stage="bad-config",
                   detail="档案里的 config 属于 %s，与账号不符，拒绝使用" % owner)
        return res

    # 3) 隐藏已有实例主窗口（主窗口可见时，新启动会被转交）
    if hide_existing:
        hidden.extend(_hide_existing(log))

    # 4) 热替换 config 对
    n = install_config_pair(env, vault.cfg_dir(acc.wxid), log=log)
    if n != len(CONFIG_FILES):
        res.update(stage="load-failed",
                   detail="config 载入不完整（%d/%d）" % (n, len(CONFIG_FILES)))
        return res
    who = mmkv.owner_of(collect.live_config_path(env))
    if who != acc.wxid:
        res.update(stage="load-failed",
                   detail="装载后配置归属为 %s，与 %s 不符" % (who or "空", acc.wxid))
        return res
    log("  已热替换 config 对（归属校验通过：%s）" % who)

    # 5) host 铺进目标槽位
    hn = slot.install_host(env, target, vault.host_dir(acc.wxid))
    log("  已把 host 铺进槽位 %s（%d 个文件）" % (target, hn))

    # 6) 启动实例，并等**本实例**的登录窗口（按 pid 认；多开时别人的窗口不算数）
    #    不再采"主窗口基准数"：判据一律按 **pid** 认（见下方 7.5 的说明），
    #    固定数字的基准会被 "7.5 恢复旧窗口" 直接污染。
    log("  XWeb 运行时搜索路径：%s"
        % (paths.xweb_runtime_dir(env.appdata_dir) or "<未找到，按原样启动>"))

    def _launch_and_wait(timeout: float):
        """启动微信 → 等本实例的登录窗口（或它已直接进主界面）。返回 (pid, win)。"""
        pids, _ = process.launch(env.exe_path)
        if not pids:
            return 0, None
        p = pids[0]
        log("  实例已启动 pid=%s" % p)
        w = None
        t = time.time()
        while time.time() - t < timeout:
            try:
                w = ui.login_window_of(p)
            except Exception:                   # noqa: BLE001
                w = None
            if w is not None or ui.main_window_of(p) is not None:
                break
            time.sleep(POLL)
        return p, w

    pid, win = _launch_and_wait(WINDOW_WAIT)

    # 6.5) ★ 兜底：本实例迟迟不出现登录窗口 ⇒ 大概率是启动被"转交"给了旧实例，
    #      说明"直接隐藏（SW_HIDE）"不足以让微信新建实例。此时**退回老做法**：
    #      先把旧窗口显示出来，再走微信自己的"关闭到托盘"（WM_CLOSE），最后重启。
    #      代价是那些旧窗口可能"点不动"（WM_CLOSE 的固有副作用，需用户手动点一下
    #      标题栏最小化/最大化），但**保证多开不会失败**。
    #      绝大多数情况下第一条路（SW_HIDE）就走通了，根本走不到这里。
    if hidden and (not pid or (win is None and ui.main_window_of(pid) is None)):
        log("  本实例 %.0f 秒内未出现登录窗口 —— 疑似被\"转交\"，"
            "改用老方式（WM_CLOSE）重试" % WINDOW_WAIT)
        for h in list(hidden):
            ui.show_window(h, log=log)          # 微信对"已隐藏"的窗口常忽略 WM_CLOSE
        time.sleep(0.5)
        for h in list(hidden):
            ui.close_window(h, log=log)
        time.sleep(1.5)
        pid, win = _launch_and_wait(RETRY_WAIT)

    if not pid:
        res.update(stage="launch-failed", detail="微信未能启动")
        return res
    res["pid"] = pid
    log("  本次实例 pid=%s" % pid)

    # 7.5) 新实例已经出现在登录页 ⇒ 微信的"单实例判定"早已完成，此刻把之前
    #      收起的窗口显示回来是安全的（再早有可能被判为"已有实例"而被转交）。
    #      这样用户只会看到旧窗口消失几秒，而不是一直躺在托盘里。
    #
    #      ★★ 代价（2026-10-04 踩到，务必记住）：这里显示回来的**旧实例主窗口**
    #      会重新出现在 UIA 树里。所以下面每一处"是否进入主界面"的判定都必须
    #      按 **pid** 认（见 _entered 与 ui.click_enter 的 appeared()），
    #      **绝不能**用"主窗口总数变多了"。否则旧窗口一复活就会被当成本次登录
    #      的成果：明明一次都没点「进入微信」，也会被判成功（账号其实没登进去）。
    restore_windows(hidden, log=log)

    # 8) ★★ 用户要求：**「进入微信」一出现就点**，不额外等"稳定"。
    #    整段交给 `ui.click_enter` 的 **ready 阶段**，最短路径如下：
    #      · 轮询粒度 `POLL`=0.15 s（原来是 0.25 s sleep + 一次"限 600 节点整树
    #        遍历"读状态，一轮下来 ≈0.3 s，且按钮只有遍历完才看得到）；
    #      · 判"按钮在不在"用 `FindControl`，**命中即返回**（实测 ~16 ms）；
    #      · 命中后**立刻发点击**，不再压着等 stable 稳定确认；
    #      · 点完 0.5 s 没动静就**重发**（登录页刚画出来时坐标可能还没稳），
    #        不再"点一次 → 等满 7 秒 → 才换下一招"。
    #    二维码页（登录态失效）仍按"正向证据连续稳定 3 秒"判，不会误伤正常登录。
    if win is not None:
        ok, method = ui.click_enter(win, wait=CLICK_WAIT, ready=ENTER_WAIT,
                                    poll=POLL, log=log, pid=pid)
        res["state"] = ui.ST_ONE_CLICK if (ok or method) else ui.ST_LOADING
        if not ok and method == "qr":
            res.update(stage="ticket-rejected",
                       detail="票据被服务端拒绝（登录页切为二维码），需重新采集")
            return res
    else:
        log("  未取到登录窗口，可能已直接进入主界面")

    # 9) 确认进入主界面
    if wait_entered(env, pid, acc, target, timeout=120.0, log=log):
        res.update(ok=True, stage="done",
                   detail="「%s」已登录（槽位 %s，全程未扫码）" % (acc.title, target))
        log("  " + res["detail"])
        return res

    res.update(stage="not-entered",
               detail="点了「进入微信」仍未确认进入主界面（登录态可能已失效）")
    return res
