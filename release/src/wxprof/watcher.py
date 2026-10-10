# -*- coding: utf-8 -*-
"""实时监控：把微信实例按 PID 绑定槽位与账号，并跟踪实例界面状态。"""
from __future__ import annotations

import threading
import time

from . import collect, slot, process

ST_MAIN = "main"                 # 主界面（正常显示）
ST_MAIN_MIN = "main_min"         # 主界面（已最小化）
ST_MAIN_CLOSED = "main_closed"   # 进程仍在，主界面窗口已关闭
ST_UNKNOWN = "unknown"           # 状态读不到（UIA 不可用或窗口读取失败）

STATE_CN = {
    ST_MAIN: "主界面",
    ST_MAIN_MIN: "主界面（最小化）",
    ST_MAIN_CLOSED: "主界面已关闭（进程仍在）",
    ST_UNKNOWN: "状态未知",
    "login_one_click": "登录页（带登录态，可一键进入）",
    "login_qr": "登录页（等待扫码）",
    "login_phone": "登录页（等待手机确认）",
    "login_loading": "登录页（加载中）",
    "login_fail": "登录页（服务端拒绝）",
}


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
        self._bind = {}                     # pid -> {slot, uin, state, ts}
        self.status = self._blank()

    @staticmethod
    def _blank() -> dict:
        return {"instances": 0, "online": {}, "busy": [], "live_wxid": "",
                "live_uin": 0, "slots": {}, "states": {}}

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
        env = self.env
        live = collect.live_summary(env)
        self._sync_bind(env)
        online = self._online_from_bind()
        st = {
            "instances": process.count_instances(),
            "online": online,
            "busy": slot.busy_slots(env),
            "live_wxid": live.get("wxid", "") or "",
            "live_uin": int(live.get("uin_dec") or 0),
            "slots": slot.summary(env),
            "states": {p: b.get("state", "") for p, b in self._bind.items()},
        }
        self.status = st
        if online and not self._pausing():
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

    def _sync_bind(self, env) -> None:
        """维护 PID→槽位→账号 绑定：进程退出即解绑，新进程按槽位占用建绑。"""
        pids = set(process.main_pids())
        for pid in list(self._bind):
            if pid in pids:
                continue
            b = self._bind.pop(pid)
            self.on_log("实例 PID=%s 已退出 → uin=%s 离线（槽位 %s）"
                        % (pid, b.get("uin") or "?", b.get("slot") or "?"))

        owner = {}
        for name in slot.instance_names(env):
            if not slot.slot_busy(env, name):
                continue
            op = slot.slot_owner_pid(env, name)
            if op:
                owner[name] = op

        for pid in pids:
            if pid in self._bind:
                continue
            name = ""
            for n, o in owner.items():
                if o == pid:
                    name = n
                    break
            if not name:
                continue                        # 还没占到槽位，下轮再看
            u = slot.current_uin(env, name)
            if not u:
                continue                        # 账号还没定（登录中），下轮再试
            self._bind[pid] = {"slot": name, "uin": u, "state": "", "ts": 0.0}
            self._on_bound(pid, name, u)

        for pid in list(self._bind):
            self._refresh_state(pid)

    def _refresh_state(self, pid) -> None:
        """按频率刷新实例界面状态；主界面后降频。主界面退回登录页即解绑重来。"""
        b = self._bind.get(pid)
        if b is None:
            return
        now = time.time()
        gap = 3.0 if b.get("state") == ST_MAIN else 1.0
        if now - b.get("ts", 0.0) < gap:
            return
        st = self._probe_state(pid)
        prev = b.get("state", "")
        b["ts"] = now
        if prev == ST_MAIN and st.startswith("login_"):
            self._bind.pop(pid, None)
            self.on_log("实例 PID=%s 由主界面退回登录页（%s）→ 重新绑定账号"
                        % (pid, STATE_CN.get(st, st)))
            return
        b["state"] = st

    def _probe_state(self, pid) -> str:
        """识别该实例当前的界面状态。"""
        from . import ui
        try:
            win = ui.login_window_of(pid)
        except Exception:                       # noqa: BLE001
            return ST_UNKNOWN
        if win is not None:
            try:
                state, _nick, _why = ui.login_state(win)
            except Exception:                   # noqa: BLE001
                return ST_UNKNOWN
            return "login_" + state
        try:
            mw = ui.main_window_of(pid)
        except Exception:                       # noqa: BLE001
            return ST_UNKNOWN
        if mw is None:
            return ST_MAIN_CLOSED
        try:
            return ui.window_visual_state(mw)
        except Exception:                       # noqa: BLE001
            return ST_MAIN

    def _online_from_bind(self) -> dict:
        """由绑定表构造在线名单 {wxid: 槽位}；已绑定的 PID 即视为在线。"""
        out = {}
        for b in self._bind.values():
            u = b.get("uin")
            if not u:
                continue
            acc = self.vault.by_uin(u)
            if acc is not None and acc.wxid not in out:
                out[acc.wxid] = b.get("slot", "")
        return out

    def _on_bound(self, pid, slot_name: str, uin: int) -> None:
        """实例刚绑定成功（登录成功）→ 立刻为该账号采集一次 host。"""
        acc = self.vault.by_uin(uin)
        if acc is None:
            return
        try:
            if collect.refresh_host(self.env, self.vault, acc, slot_name):
                self.on_log("已更新「%s」的 host（槽位 %s）"
                            % (acc.title, slot_name))
                self._notify({"ok": True, "new": False, "changed": ["host"],
                              "wxid": acc.wxid, "title": acc.title,
                              "slot": slot_name})
        except Exception as e:                  # noqa: BLE001
            self.on_log("更新 host 失败：%s" % e)

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

    def snapshot(self) -> dict:
        return dict(self.status)
