# -*- coding: utf-8 -*-
"""采集：把当前登录的账号收进档案。

    ① 读 live global_config → wxid / uin（varint）/ 头像 URL / 昵称
    ② 用 uin 定位槽位（含 monitordata_<uin>_<X> 的那个）
    ③ 采该槽位的 host\\ 整目录
    ④ 采 config 一对（global_config + .crc）
"""
from __future__ import annotations

import os
import time

from . import avatar, file_md5, mmkv, slot
from .vault import Account, Vault


def live_config_path(env) -> str:
    return os.path.join(env.config_dir, "global_config") if env.config_dir else ""


def live_summary(env) -> dict:
    p = live_config_path(env)
    return mmkv.probe(p) if p else {}


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def locate_slot(env, vault, uin: int) -> str:
    """定位某 uin 所在槽位；不确定返回空串。

    两重判据：
      ① `slot.slot_of_uin()` —— 基于"当前账号"（时间簇），对新账号同样成立；
      ② **host 指纹兜底** —— `host\\host-redirect.xml` 是账号级且内容稳定
         （实测两账号互不相同、槽位与档案能一一对上），比 mtime 与 kvcomm
         累积都可靠。但它只对"已收录且 host 齐备"的账号有效，
         所以只作第二重保险，新账号首次采集仍靠 ①。
    """
    s = slot.slot_of_uin(env, uin)
    if s:
        return s
    acc = vault.by_uin(uin) if vault else None
    if acc is None or not vault.has_host(acc.wxid):
        return ""
    want = file_md5(os.path.join(vault.host_dir(acc.wxid), "host-redirect.xml"))
    if not want:
        return ""
    for name in slot.INSTANCE_DIRS:
        if not slot.slot_busy(env, name):
            continue
        p = os.path.join(slot.slot_dir(env, name), "host", "host-redirect.xml")
        if file_md5(p) == want:
            return name
    return ""


def resolve_wxid(env, slot_name: str, uin: int, live: dict, vault) -> str:
    """给定槽位+uin，尽力解出 wxid：live config → 档案 → 槽位统计文件。"""
    if live.get("uin_dec") == uin and live.get("wxid"):
        return live["wxid"]
    acc = vault.by_uin(uin) if vault else None
    if acc is not None:
        return acc.wxid
    return slot.wxid_in_slot(env, slot_name, uin)


def online_accounts(env, vault, live: dict = None) -> dict:
    """当前**已登录**的账号：{wxid: 槽位}。每个槽位最多产出一个账号。

    判据 = 槽位被占用（config.ini 独占句柄）**且** `current_uin()` 能给出当前账号
    （= 登录成功且非历史残留）。停在登录页的实例只有前者，不算在线。

    ⚠️ 不能遍历 `monitordata_uins()`：微信从不清理旧的 `monitordata_<uin>`，一个
    槽位会累积多个历史账号，那样会把已退出的账号一直报成在线。
    """
    if live is None:
        live = live_summary(env)
    out = {}
    for name in slot.INSTANCE_DIRS:
        if not slot.slot_busy(env, name):
            continue
        u = slot.current_uin(env, name)
        if not u:
            continue
        w = resolve_wxid(env, name, u, live, vault)
        if w:
            out[w] = name
    return out


def refresh_host(env, vault: Vault, acc: Account, slot_name: str) -> bool:
    """只刷新某个在线账号的 host（host 分槽位存放，随时可安全更新）。

    返回是否真的更新了。**不采 config**：多实例在线时 live config 只属于
    "最后一个写入者"，拿它覆盖别的账号就是串档。
    """
    if not slot_name or acc is None:
        return False
    fp = slot.host_fingerprint(env, slot_name)
    digest = slot.fp_digest(fp)
    if not digest or digest == acc.host_fp:
        return False
    n = vault.save_host(acc.wxid, slot.host_dir(env, slot_name))
    if not n:
        return False
    acc.host_fp = digest
    acc.slot = slot_name
    vault.save_meta(acc)
    return True


def collect_live(env, vault: Vault, log=print, want_avatar: bool = True) -> dict:
    """采集当前 live config 所属的账号。返回结果摘要 dict。"""
    res = {"ok": False, "new": False, "changed": [], "wxid": "", "uin": 0,
           "slot": "", "detail": ""}
    if not env.config_dir:
        res["detail"] = "未探测到配置目录"
        return res

    s = live_summary(env)
    wxid = s.get("wxid") or ""
    if not wxid:
        res["detail"] = "live config 里没有账号信息（未登录）"
        return res

    uin = int(s.get("uin_dec") or 0)
    nick = s.get("nickname") or ""
    url = s.get("head_img_url") or ""
    has_ticket = bool(s.get("auth_len"))

    acc = vault.reload(wxid)
    is_new = acc is None
    if is_new:
        acc = Account(wxid, uin=uin, nickname=nick, name=nick or wxid,
                      created=_now())
    changed = []

    live_fp = Vault.live_cfg_fingerprint(env.config_dir)
    if live_fp and live_fp != acc.config_fp:
        if not has_ticket and not is_new:
            log("  跳过 config 更新：此刻配置里没有登录票据（微信短暂移出）")
        else:
            ok, err = vault.save_config(wxid, env.config_dir, wxid)
            if ok:
                acc.config_fp = vault.cfg_fingerprint(wxid)
                changed.append("config")
            else:
                log("  config 未保存：%s" % err)

    slot_name = locate_slot(env, vault, uin) if uin else ""
    if slot_name:
        digest = slot.fp_digest(slot.host_fingerprint(env, slot_name))
        if digest and digest != acc.host_fp:
            n = vault.save_host(wxid, slot.host_dir(env, slot_name))
            if n:
                acc.host_fp = digest
                acc.slot = slot_name
                changed.append("host")
    elif uin:
        res["detail"] = "当前没有槽位记录该账号（未登录成功），暂不采 host"

    if want_avatar and url:
        png = vault.avatar_path(wxid)
        if is_new or not os.path.isfile(png):
            if avatar.save_avatar(avatar.normalize_url(url), png):
                changed.append("avatar")

    if uin:
        acc.uin = uin
    if nick:
        acc.nickname = nick
    if not acc.name:
        acc.name = nick or wxid
    if is_new or changed:
        acc.last_capture = _now()
        vault.save_meta(acc)

    res.update(ok=True, new=is_new, changed=changed, wxid=wxid, uin=uin,
               slot=slot_name, title=acc.title)
    return res
