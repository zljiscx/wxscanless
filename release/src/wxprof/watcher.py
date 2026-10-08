# -*- coding: utf-8 -*-
"""实时监控：发现微信登录成功即采集 / 更新登录态。"""
from __future__ import annotations

import threading
import time

from . import collect, slot


class Watcher:
    def __init__(self, env, vault, interval: float = 0.6, on_event=None,
                 on_log=None, on_change=None) -> None:
        self.env = env
        self.vault = vault
        self.interval = interval
        self.on_event = on_event            # on_event(kind, payload)
        self.on_log = on_log or (lambda _t: None)
        self.on_change = on_change or (lambda _st: None)  # 在线状态变化时立即回调

        self._stop = threading.Event()
        self._th = None
        self._paused = False
        self._lock = threading.Lock()
        self._last_cfg = {}                 # wxid -> 上次采集 config 时刻
        self._last_host = {}                # wxid -> 上次采集 host 时刻
        self.cfg_gap = 6.0
        self.host_gap = 6.0
        self._last_seen = None              # 上一轮的在线摘要，用于变化通知
        self._prev_online = None            # 上一轮的在线账号，用于离线诊断
        self.status = self._blank()

    @staticmethod
    def _blank() -> dict:
        return {"instances": 0, "online": {}, "busy": [], "live_wxid": "",
                "live_uin": 0, "slots": {}}

    def start(self) -> None:
        if self._th and self._th.is_alive():
            return
        self._stop.clear()
        self._th = threading.Thread(target=self._loop, name="wx-watcher",
                                    daemon=True)
        self._th.start()

    def stop(self) -> None:
        self._stop.set()

    def pause(self) -> None:
        with self._lock:
            self._paused = True

    def resume(self) -> None:
        with self._lock:
            self._paused = False

    def _pausing(self) -> bool:
        with self._lock:
            return self._paused

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception as e:              # noqa: BLE001
                self.on_log("监控异常：%s" % e)
            self._stop.wait(self.interval)

    def poll_once(self) -> dict:
        from . import process
        env = self.env
        live = collect.live_summary(env)
        busy = slot.busy_slots(env)
        online = collect.online_accounts(env, self.vault, live) if busy else {}
        st = {
            "instances": process.count_instances(),
            "online": online,
            "busy": busy,
            "live_wxid": live.get("wxid", "") or "",
            "live_uin": int(live.get("uin_dec") or 0),
            "slots": slot.summary(env),
        }
        self.status = st
        # 账号从在线跌为离线时记录根因，便于排查"突然离线/登录态与账号不匹配"
        prev = self._prev_online
        for wxid, sname in (prev or {}).items():
            if wxid not in online:
                self._diag_offline(wxid, sname)
        self._prev_online = online
        if st["instances"] and online and not self._pausing():
            self._collect(online, live)
        # 在线条目或实例数变了 ⇒ 立刻通知界面，不必等它自己的轮询
        seen = (tuple(sorted(online.items())), st["instances"])
        if seen != self._last_seen:
            self._last_seen = seen
            if self.on_change:
                try:
                    self.on_change(st)
                except Exception:               # noqa: BLE001
                    pass
        return st

    def _collect(self, online: dict, live: dict) -> None:
        now = time.time()
        # ★ 不能只看 live 的 wxid：微信退出时会把 live config 清空（wxid=''），
        #   而登录期间也可能短暂为空 ⇒ 门槛形同虚设，采集永远不触发。
        #   改用 resolve_live_account：live 无 wxid 时回退"槽位 uin 锚点 → 档案"。
        live_wxid, _uin, _src = collect.resolve_live_account(
            self.env, self.vault, live)

        # 1) live 归属账号：完整采集（config 对 + host + 头像）
        if live_wxid and now - self._last_cfg.get(live_wxid, 0.0) >= self.cfg_gap:
            self._last_cfg[live_wxid] = now
            try:
                res = collect.collect_live(self.env, self.vault, log=self.on_log)
            except Exception as e:              # noqa: BLE001
                self.on_log("采集失败：%s" % e)
                return
            if res.get("ok") and (res.get("new") or res.get("changed")):
                self._notify(res)

        # 2) 其它在线账号：只刷新 host（避免用 live config 串档）
        if not online:
            return
        by_id = {a.wxid: a for a in self.vault.list_accounts()}
        for wxid, slot_name in online.items():
            acc = by_id.get(wxid)
            if acc is None or acc.wxid == live_wxid:
                continue
            if now - self._last_host.get(acc.wxid, 0.0) < self.host_gap:
                continue
            self._last_host[acc.wxid] = now
            try:
                if collect.refresh_host(self.env, self.vault, acc, slot_name):
                    self.on_log("已更新「%s」的 host（槽位 %s）"
                                % (acc.title, slot_name))
                    self._notify({"ok": True, "new": False, "changed": ["host"],
                                  "wxid": acc.wxid, "title": acc.title,
                                  "slot": slot_name})
            except Exception as e:              # noqa: BLE001
                self.on_log("更新 host 失败：%s" % e)

    def _notify(self, res: dict) -> None:
        if not self.on_event:
            return
        try:
            self.on_event(res)
        except Exception:                       # noqa: BLE001
            pass

    def _diag_offline(self, wxid: str, sname: str) -> None:
        """账号从在线跌为离线时，记录可定位根因的信息。"""
        env, vault = self.env, self.vault
        if not slot.slot_busy(env, sname):
            reason = "槽位 %s 已不再被占用（微信可能已退出或被收起）" % sname
        else:
            u = slot.current_uin(env, sname)
            pid = slot.slot_owner_pid(env, sname)
            if not u:
                reason = ("槽位 %s 仍被占用(PID=%s)但 monitordata 未锁定"
                          "（账号锚点瞬时丢失）" % (sname, pid or "?"))
            elif pid == 0:
                reason = "槽位 %s 当前账号 uin=%s，但反查不到微信进程 PID" % (sname, u)
            else:
                try:
                    from . import ui
                    uia_ok = getattr(ui, "_OK", True)
                    win = ui.login_window_of(pid)
                    if win is not None:
                        reason = ("槽位 %s 账号 uin=%s、PID=%s，但检测到登录/扫码窗口"
                                  "（未在主界面，判离线）" % (sname, u, pid))
                    elif not uia_ok:
                        reason = ("槽位 %s 账号 uin=%s、PID=%s，UIA 不可用致窗口检测不可靠"
                                  % (sname, u, pid))
                    else:
                        reason = ("槽位 %s 账号 uin=%s、PID=%s，主界面窗口已消失"
                                  "（可能最小化到托盘或进程异常）" % (sname, u, pid))
                except Exception as e:           # noqa: BLE001
                    reason = "槽位 %s 检测主界面窗口异常：%r" % (sname, e)
        self.on_log("账号 %s 从在线跌为离线 —— %s" % (wxid, reason))

    def snapshot(self) -> dict:
        return dict(self.status)
