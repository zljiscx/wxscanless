# -*- coding: utf-8 -*-
"""实时监控：发现微信登录成功即采集 / 更新登录态。"""
from __future__ import annotations

import threading
import time

from . import collect, slot


class Watcher:
    def __init__(self, env, vault, interval: float = 2.0, on_event=None,
                 on_log=None) -> None:
        self.env = env
        self.vault = vault
        self.interval = interval
        self.on_event = on_event            # on_event(kind, payload)
        self.on_log = on_log or (lambda _t: None)

        self._stop = threading.Event()
        self._th = None
        self._paused = False
        self._lock = threading.Lock()
        self._last_cfg = {}                 # wxid -> 上次采集 config 时刻
        self._last_host = {}                # wxid -> 上次采集 host 时刻
        self.cfg_gap = 6.0
        self.host_gap = 6.0
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
        if st["instances"] and online and not self._pausing():
            self._collect(online, live)
        return st

    def _collect(self, online: dict, live: dict) -> None:
        now = time.time()
        live_wxid = live.get("wxid") or ""

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
