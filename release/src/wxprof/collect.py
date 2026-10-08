# -*- coding: utf-8 -*-
"""采集：把当前登录的账号收进档案。"""
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
    """定位某 uin 所在槽位；不确定返回空串。"""
    s = slot.slot_of_uin(env, uin)
    if s:
        return s
    acc = vault.by_uin(uin) if vault else None
    if acc is None or not vault.has_host(acc.wxid):
        return ""
    want = file_md5(os.path.join(vault.host_dir(acc.wxid), "host-redirect.xml"))
    if not want:
        return ""
    for name in slot.instance_names(env):
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


def slot_account_of(env, vault, slot_name: str) -> str:
    """该槽位当前住的是哪个账号（wxid）；认不出返回空串。

    用 uin 锚点 + 档案反查，这是槽位归属唯一可靠的实时依据。
    """
    u = slot.current_uin(env, slot_name)
    if not u:
        return ""
    acc = vault.by_uin(u) if vault else None
    return acc.wxid if acc is not None else ""


def online_accounts(env, vault, live: dict = None,
                     require_login: bool = True) -> dict:
    """当前已登录的账号：{wxid: 槽位}。

    require_login=True 时额外要求该槽位**登录窗口已消失**（=已进主界面）。
    只靠 kvcomm 锚点会误报：微信停在登录界面时不写新 uin、也不清旧 uin。
    ⚠ 不能用"主界面窗口是否可见"当判据 —— 主窗口隐藏/最小化会被误判成离线。
    """
    if live is None:
        live = live_summary(env)
    out = {}
    for name in slot.instance_names(env):
        if not slot.slot_busy(env, name):
            continue
        u = slot.current_uin(env, name)
        if not u:
            continue
        if require_login and not slot.slot_logged_in(env, name):
            continue
        w = resolve_wxid(env, name, u, live, vault)
        if w:
            out[w] = name
    return out


def refresh_host(env, vault: Vault, acc: Account, slot_name: str) -> bool:
    """只刷新某个在线账号的 host，返回是否真的更新了。

    防串档靠**槽位归属**（该槽位的 uin 锚点是不是本账号），而不是"线路与档案
    是否一致" —— 档案本身可能是旧的/被污染的，拿它当基准会拒绝正确的更新。
    """
    if not slot_name or acc is None:
        return False
    owner = slot_account_of(env, vault, slot_name)
    if owner and owner != acc.wxid:
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


def resolve_live_account(env, vault, live: dict = None):
    """判定"当前线上的是哪个账号"，返回 (wxid, uin, 来源说明)。

    live config 的 wxid 会被微信**清空**（退出后实测 auth_len=0、wxid=''），
    所以不能只认它；回退到"槽位 uin 锚点 → 档案 by_uin"这条独立链路。
    """
    if live is None:
        live = live_summary(env)
    wxid = live.get("wxid") or ""
    if wxid:
        return wxid, int(live.get("uin_dec") or 0), "live config"
    # 回退：任一槽位的当前 uin 若能对应到档案账号，就认为是它
    for name in slot.instance_names(env):
        if not slot.slot_busy(env, name):
            continue
        u = slot.current_uin(env, name)
        if not u:
            continue
        acc = vault.by_uin(u) if vault else None
        if acc is not None:
            return acc.wxid, u, "槽位 %s 的 uin 锚点" % name
    return "", int(live.get("uin_dec") or 0), ""


def collect_live(env, vault: Vault, log=print, want_avatar: bool = True) -> dict:
    """采集当前 live config 所属的账号。返回结果摘要 dict。"""
    res = {"ok": False, "new": False, "changed": [], "wxid": "", "uin": 0,
           "slot": "", "detail": ""}
    if not env.config_dir:
        res["detail"] = "未探测到配置目录"
        return res

    s = live_summary(env)
    wxid, uin, src = resolve_live_account(env, vault, s)
    if not wxid:
        res["detail"] = "无法判定当前线上账号（live config 为空且槽位无账号锚点）"
        return res
    if src != "live config":
        log("  live config 里没有账号信息（微信退出时会清空），改用 %s 判定"
            % src)

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
    # 槽位必须是**本账号自己铺的**才能采host。
    # ⚠ 不能用"线路与档案是否一致"当判据：档案本身可能是旧的/被污染的
    #   （实测档案是 sz 线、槽位却是登录器铺的正确默认线），那样会拒绝**正确**更新，
    #   把采集彻底堵死。真正要防的是"槽位属于**别的账号**"。
    if slot_name:
        owner = slot_account_of(env, vault, slot_name)
        if owner and owner != wxid:
            log("  跳过 host 采集：槽位 %s 属于账号 %s，不是本账号"
                % (slot_name, owner[:12]))
            slot_name = ""
    if slot_name:
        digest = slot.fp_digest(slot.host_fingerprint(env, slot_name))
        if digest and digest != acc.host_fp:
            n = vault.save_host(wxid, slot.host_dir(env, slot_name))
            if n:
                acc.host_fp = digest
                acc.slot = slot_name
                changed.append("host")
    elif uin:
        res["detail"] = "当前没有可用槽位记录该账号（未登录成功），暂不采 host"

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
