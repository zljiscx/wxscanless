# -*- coding: utf-8 -*-
"""登录：把已收录的账号免扫码登进主界面。"""
from __future__ import annotations

import os
import shutil
import time

from . import collect, mmkv, paths, process, slot, ui
from .vault import CONFIG_FILES

LIVE_BAK = ".livebak"          # 热替换时被改名让位的旧配置
POLL = 0.15                    # 统一轮询粒度
WINDOW_WAIT = 15.0             # 等本实例登录窗口出现的上限
ENTER_WAIT = 10.0              # 等「进入微信」按钮出现的上限
CLICK_WAIT = 8.0               # 点击 + 确认的总时长


def _remove_quiet(path: str) -> bool:
    """尽力删掉一个临时文件；**删不掉也不抛异常**。
    """
    try:
        os.remove(path)
    except OSError:
        pass
    return not os.path.exists(path)


def sweep_backups(env) -> int:
    """清掉 config 目录里遗留的 *.livebak（尽力而为，返回清掉的个数）。"""
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
    """把 src_dir 里的 global_config 一对装到 live，成对且可回滚。"""
    src = {}
    for fn in CONFIG_FILES:
        p = os.path.join(src_dir, fn)
        if not os.path.isfile(p):
            log("  源缺少 %s —— 放弃本次装载（不做半套）" % fn)
            return 0
        src[fn] = p

    sweep_backups(env)

    done = []
    for fn, s in src.items():
        d = os.path.join(env.config_dir, fn)
        try:
            shutil.copy2(s, d)
            done.append((fn, None))
            continue
        except OSError:
            pass
        bak = d + LIVE_BAK
        if os.path.exists(bak):
            if not _remove_quiet(bak):
                bak = "%s.%d%s" % (d, int(time.time() * 1000) % 100000, LIVE_BAK)
        try:
            if os.path.exists(d):
                os.rename(d, bak)
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
        if LIVE_BAK in fn:
            _remove_quiet(os.path.join(env.config_dir, fn))


def _entered(env, pid: int, acc, target_slot: str) -> bool:
    """是否真的进了主界面（判据围绕本实例的 pid）。"""
    try:
        if ui.main_window_of(pid) is not None:
            return True                         # 本实例的主界面窗口出现
    except Exception:                           # noqa: BLE001
        pass
    if acc.uin and slot.current_uin(env, target_slot) == acc.uin:
        return True                             # 槽位当前账号就是本账号 = 已登录
    return False


def wait_entered(env, pid: int, acc, target_slot: str,
                 timeout: float = 120.0, log=print) -> dict:
    """等进入主界面。返回 {ok, state, why}（state 为最后的登录页状态）。"""
    t0 = time.time()
    last = 0.0
    st, why = ui.ST_LOADING, ""
    told_phone = False
    next_probe = 0.0
    while time.time() - t0 < timeout:
        if _entered(env, pid, acc, target_slot):
            return {"ok": True, "state": ui.ST_ONE_CLICK, "why": "主界面已出现"}
        alive = process.main_pids()               # 一次枚举即可
        if pid not in alive:                 # 含"全部退出"（alive 为空集）
            log("  实例已退出，停止等待")
            return {"ok": False, "state": ui.ST_GONE, "why": "实例已退出"}
        now = time.time()
        # 登录页状态：限频查（控件树遍历较贵），手机确认页一出现就立即播报
        if now >= next_probe:
            next_probe = now + 1.0
            try:
                w = ui.login_window_of(pid)
                if w is not None:
                    st, _nick, why = ui.login_state(w)
                    if st == ui.ST_PHONE and not told_phone:
                        told_phone = True
                        log("  ⚠ 请在手机微信上点确认登录（%s）—— 等你确认"
                            % why)
            except Exception as e:              # noqa: BLE001
                log("  查登录页状态异常：%r" % (e,))
        if now - last > 15:
            last = now
            log("  等待进入主界面…（已 %.0f 秒）%s"
                % (now - t0, ("｜当前登录页：%s（%s）"
                              % (ui.STATE_CN.get(st, st), why))
                   if why else ""))
        time.sleep(POLL)
    return {"ok": False, "state": st, "why": why}


def login_account(env, vault, acc, log=print) -> dict:
    """免扫码登录一个已收录账号。返回 {ok, stage, detail, pid, slot, state}。"""
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

    # 1) 目标槽位 = 线路与本账号同源的槽位（否则宁可无槽可用）
    src_host = vault.host_dir(acc.wxid)
    target, forced = slot.pick_slot(env, src_host)
    if not target:
        res.update(stage="full",
                   detail="没有可用的空闲槽位（微信实例可能已开满），请先退出一个")
        return res
    res["slot"] = target
    if forced:
        log("  ⚠ 没有与「%s」线路同源的空闲槽位，只能用最小空闲槽位 %s"
            "—— 该槽位线路属于别的账号，可能被服务端拒绝（跳扫码页）"
            % (acc.title, target))

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

    # 3) 热替换 config 对
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

    # 6) 启动实例，等本实例的登录窗口（按 pid 认）
    log("  XWeb 运行时搜索路径：%s"
        % (paths.xweb_runtime_dir(env.appdata_dir) or "<未找到，按原样启动>"))

    def _launch_and_wait(timeout: float):
        """启动微信 → 等本实例的登录窗口（或它已直接进主界面）。返回 (pid, win)。"""
        pids = process.launch(env.exe_path)
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

    if not pid:
        res.update(stage="launch-failed", detail="微信未能启动")
        return res
    res["pid"] = pid
    log("  本次实例 pid=%s" % pid)

    # 7) 「进入微信」一出现就点
    if win is not None:
        ok, method = ui.click_enter(win, wait=CLICK_WAIT, ready=ENTER_WAIT,
                                    poll=POLL, log=log, pid=pid)
        if method == "qr":
            res.update(stage="ticket-rejected",
                       detail="票据被服务端拒绝（登录页切为二维码），需重新采集")
            return res
        if method == "phone":
            # 票据有效，只差手机上点一下确认 —— 继续等，不算失败
            log("  ⚠ 请在手机微信上点确认登录 —— 本机在等你的确认")
        elif method == "fail":
            res.update(stage="login-failed",
                       detail="服务端拒绝了本次登录（登录页显示登录失败）")
            return res
        elif method == "exited":
            res.update(stage="closed",
                       detail="登录过程中实例被关闭（可能是你手动关掉了登录界面）")
            return res
        elif not ok and not method:
            res.update(stage="no-enter-button",
                       detail="登录页始终未出现「进入微信」，未能开始登录")
            return res
        res["state"] = ui.ST_ONE_CLICK if (ok or method) else ui.ST_LOADING
    else:
        log("  未取到登录窗口，可能已直接进入主界面")

    # 9) 确认进入主界面
    r = wait_entered(env, pid, acc, target, timeout=120.0, log=log)
    if r.get("ok"):
        res.update(ok=True, stage="done",
                   detail="「%s」已登录（槽位 %s，全程未扫码）" % (acc.title, target))
        log("  " + res["detail"])
        return res

    st = r.get("state") or ""
    if st == ui.ST_PHONE:
        res.update(stage="need-phone",
                   detail="票据有效，但微信要求在手机上确认登录 —— "
                          "请在手机微信上点一下确认（%s）" % (r.get("why") or ""))
        return res
    if st == ui.ST_QR:
        res.update(stage="ticket-rejected",
                   detail="登录页已切为二维码，票据被服务端拒绝，需重新采集")
        return res
    if st == ui.ST_FAIL:
        res.update(stage="login-failed",
                   detail="服务端拒绝了本次登录：%s" % (r.get("why") or ""))
        return res
    res.update(stage="not-entered",
               detail="点了「进入微信」仍未确认进入主界面（登录态可能已失效）")
    return res
