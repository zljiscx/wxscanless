# -*- coding: utf-8 -*-
"""账号档案库：一个账号一份登录态（aid = wxid）。

    data/<wxid>/
        meta.json              账号元信息（uin / 昵称 / 指纹等）
        avatar.png             头像（tkinter 可直接显示）
        cfg/global_config      登录态：全局配置对（免扫码票据所在）
        cfg/global_config.crc
        host/...               登录态：账号级网络路由文件

★ cfg 一对必须成对保存与还原（.crc 存着 IV 与校验），缺一或配错对会导致
  "有档案却每次都要扫码"。
★ host 是账号级的，须放进实例实际使用的那个槽位（由 slot.free_slot() 启动前算出）才生效。

刻意不采：账号数据目录 <wxid>_<hash>\\config、kvcomm\\*、psk.key、tlsregion.ini、cdncomm\\*
（均与登录态无关）。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time

from . import file_md5, mmkv

CONFIG_FILES = ("global_config", "global_config.crc")


def _stable_read(path: str, tries: int = 4, gap: float = 0.5) -> bytes:
    """稳定读取：连读两次内容一致才认。

    MMKV 是**内存映射**文件，微信就地写、没有原子性；此刻拷走可能拿到"改到一半"
    的内容 —— 解出来看着正常、实际与服务端对不上，点「进入微信」就被拒。
    关键文件一律用这个函数取。
    """
    prev = None
    for _ in range(max(tries, 2)):
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            time.sleep(gap)
            continue
        if prev is not None and data == prev:
            return data
        prev = data
        time.sleep(gap)
    return prev if prev is not None else b""


def _write_bytes(dst: str, data: bytes) -> bool:
    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "wb") as f:
            f.write(data)
        return True
    except OSError:
        return False


class Account:
    __slots__ = ("wxid", "uin", "nickname", "name", "created", "last_capture",
                 "config_fp", "host_fp", "slot")

    def __init__(self, wxid: str, uin: int = 0, nickname: str = "",
                 name: str = "", created: str = "", last_capture: str = "",
                 config_fp: str = "", host_fp: str = "", slot: str = "") -> None:
        self.wxid = wxid
        self.uin = int(uin or 0)
        self.nickname = nickname
        self.name = name
        self.created = created
        self.last_capture = last_capture
        self.config_fp = config_fp        # 上次存下的 config 对内容指纹
        self.host_fp = host_fp            # 上次存下的 host 目录指纹（排序串的 md5）
        self.slot = slot                  # 上次采集时所在槽位（仅记录）

    @property
    def aid(self) -> str:
        return self.wxid

    @property
    def title(self) -> str:
        return self.nickname or self.name or self.wxid

    def to_dict(self) -> dict:
        return {"wxid": self.wxid, "uin": self.uin, "nickname": self.nickname,
                "name": self.name, "created": self.created,
                "last_capture": self.last_capture, "config_fp": self.config_fp,
                "host_fp": self.host_fp, "slot": self.slot}

    @staticmethod
    def from_dict(d: dict) -> "Account":
        return Account(d.get("wxid") or d.get("id", ""), d.get("uin", 0),
                       d.get("nickname", ""), d.get("name", ""),
                       d.get("created", ""), d.get("last_capture", ""),
                       d.get("config_fp", ""), d.get("host_fp", ""),
                       d.get("slot", ""))


class Vault:
    def __init__(self, root: str) -> None:
        self.root = root
        os.makedirs(root, exist_ok=True)

    # ------------------------------------------------------------- 路径
    def account_dir(self, wxid: str) -> str:
        return os.path.join(self.root, wxid)

    def cfg_dir(self, wxid: str) -> str:
        return os.path.join(self.account_dir(wxid), "cfg")

    def host_dir(self, wxid: str) -> str:
        return os.path.join(self.account_dir(wxid), "host")

    def avatar_path(self, wxid: str) -> str:
        return os.path.join(self.account_dir(wxid), "avatar.png")

    def avatar_gray_path(self, wxid: str) -> str:
        """离线头像（`avatar.png` 的灰度副本，按需生成、生成后落盘复用）。"""
        from . import avatar
        return avatar.gray_path_of(self.avatar_path(wxid))

    # ------------------------------------------------------------- 读写
    def list_accounts(self) -> list:
        out = []
        if not os.path.isdir(self.root):
            return out
        for name in sorted(os.listdir(self.root)):
            meta = os.path.join(self.root, name, "meta.json")
            if not os.path.isfile(meta):
                continue
            try:
                with open(meta, "r", encoding="utf-8") as f:
                    acc = Account.from_dict(json.load(f))
            except (OSError, ValueError):
                continue
            if not acc.wxid:
                acc.wxid = name
            out.append(acc)
        return out

    def reload(self, wxid: str):
        for a in self.list_accounts():
            if a.wxid == wxid:
                return a
        return None

    def by_uin(self, uin: int):
        if not uin:
            return None
        for a in self.list_accounts():
            if a.uin == uin:
                return a
        return None

    def save_meta(self, acc: Account) -> None:
        d = self.account_dir(acc.wxid)
        os.makedirs(d, exist_ok=True)
        tmp = os.path.join(d, "meta.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(acc.to_dict(), f, ensure_ascii=False, indent=1)
        os.replace(tmp, os.path.join(d, "meta.json"))

    def delete(self, wxid: str) -> tuple:
        """删除该账号档案。返回 (是否成功, 错误列表)。"""
        d = self.account_dir(wxid)
        errs = []
        if not os.path.isdir(d):
            return True, errs
        for dirpath, _dirs, files in os.walk(d, topdown=False):
            for fn in files:
                try:
                    os.remove(os.path.join(dirpath, fn))
                except OSError as e:
                    errs.append(str(e))
            try:
                os.rmdir(dirpath)
            except OSError:
                pass
        return (not errs), errs

    # ------------------------------------------------------------- 快照查询
    def has_config(self, wxid: str) -> bool:
        return all(os.path.isfile(os.path.join(self.cfg_dir(wxid), fn))
                   for fn in CONFIG_FILES)

    def has_host(self, wxid: str) -> bool:
        d = self.host_dir(wxid)
        return os.path.isdir(d) and bool(os.listdir(d))

    def ready(self, wxid: str) -> bool:
        """是否三件套齐备（config 对 + host）。"""
        return self.has_config(wxid) and self.has_host(wxid)

    def cfg_owner(self, wxid: str) -> str:
        """解密档案里的 config，读出它属于谁（自校验，防串档）。"""
        return mmkv.owner_of(os.path.join(self.cfg_dir(wxid), "global_config"))

    def host_fingerprint(self, wxid: str) -> dict:
        from . import slot
        return slot.host_fingerprint_of(self.host_dir(wxid))

    def host_fp_digest(self, wxid: str) -> str:
        from . import slot
        return slot.fp_digest(self.host_fingerprint(wxid))

    # ------------------------------------------------------------- 保存
    def save_config(self, wxid: str, src_dir: str, expect_wxid: str = "") -> tuple:
        """把活配置对（src_dir 下的 global_config(+.crc)）存进档案。

        返回 (是否保存, 错误)。**先校验归属**：解不出 wxid 或与期望不一致就拒绝
        落盘 —— 直接覆盖会把一份好档案换成废档案/别人的档案，下次登录反而要扫码。
        """
        if not src_dir:
            return False, "无配置目录"
        src = {fn: os.path.join(src_dir, fn) for fn in CONFIG_FILES}
        for fn, p in src.items():
            if not os.path.isfile(p):
                return False, "缺少 %s" % fn
        blob = {fn: _stable_read(p) for fn, p in src.items()}
        if not all(blob.values()):
            return False, "读取配置失败"
        gp = os.path.join(self.cfg_dir(wxid), "global_config")
        tmp_dir = os.path.join(self.account_dir(wxid), "_tmpcfg")
        shutil.rmtree(tmp_dir, ignore_errors=True)
        ok = True
        for fn, data in blob.items():
            ok = _write_bytes(os.path.join(tmp_dir, fn), data) and ok
        owner = mmkv.owner_of(os.path.join(tmp_dir, "global_config")) if ok else ""
        want = expect_wxid or wxid
        if not ok or owner != want:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return False, "归属校验失败（解出 %s，期望 %s）" % (owner or "空", want)
        dst = self.cfg_dir(wxid)
        os.makedirs(dst, exist_ok=True)
        for fn, data in blob.items():
            _write_bytes(os.path.join(dst, fn), data)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return True, ""

    def save_host(self, wxid: str, src_host: str) -> int:
        """把槽位的 host 目录镜像进档案（src_host 即槽位的 `<槽位>\\host\\`）。

        覆盖式镜像（先清空档案里的 host/ 再整目录复制），避免残留旧账号文件。
        """
        from . import slot
        return slot.mirror_host(src_host, self.host_dir(wxid))

    # ------------------------------------------------------------- 指纹
    def cfg_fingerprint(self, wxid: str) -> str:
        """档案里 config 对的内容指纹（两文件 md5 拼接后取 md5）。"""
        vals = [file_md5(os.path.join(self.cfg_dir(wxid), fn))
                for fn in CONFIG_FILES]
        if not all(vals):
            return ""
        return hashlib.md5(("|".join(vals)).encode()).hexdigest()

    @staticmethod
    def live_cfg_fingerprint(cfg_dir: str) -> str:
        vals = [file_md5(os.path.join(cfg_dir, fn)) for fn in CONFIG_FILES]
        if not all(vals):
            return ""
        return hashlib.md5(("|".join(vals)).encode()).hexdigest()
