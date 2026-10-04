# -*- coding: utf-8 -*-
"""微信运行环境探测。

微信 4.x 相对 3.x 有多处改名，这里两套命名都做兼容探测：
    3.x: WeChat.exe / Tencent\\WeChat / WeChat Files / All Users
    4.x: Weixin.exe / Tencent\\xwechat / xwechat_files / all_users
"""
from __future__ import annotations

import os
import winreg
from dataclasses import dataclass, field

# 4.x 与 3.x 的候选命名，按顺序探测
_APPDATA_CANDIDATES = ("xwechat", "WeChat")
_FILES_CANDIDATES = ("xwechat_files", "WeChat Files")
_ALLUSERS_CANDIDATES = ("all_users", "All Users")
_EXE_CANDIDATES = ("Weixin.exe", "WeChat.exe")


@dataclass
class WeChatEnv:
    """微信在本机的完整路径布局。"""
    install_dir: str = ""
    exe_path: str = ""
    version: str = ""
    appdata_dir: str = ""          # %APPDATA%\\Tencent\\xwechat
    data_root: str = ""            # F:\\微信聊天记录
    files_dir: str = ""            # ...\\xwechat_files
    all_users_dir: str = ""        # ...\\xwechat_files\\all_users
    config_dir: str = ""           # ...\\all_users\\config   (全局单份，会被覆盖)
    login_dir: str = ""            # ...\\all_users\\login    (按 wxid 分目录)
    account_dirs: list = field(default_factory=list)   # [(wxid, 目录绝对路径), ...]
    ok: bool = False
    errors: list = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            "安装目录 : %s" % (self.install_dir or "<未找到>"),
            "主程序   : %s" % (self.exe_path or "<未找到>"),
            "版本     : %s" % (self.version or "<未知>"),
            "运行区   : %s" % (self.appdata_dir or "<未找到>"),
            "数据根   : %s" % (self.data_root or "<未找到>"),
            "文件目录 : %s" % (self.files_dir or "<未找到>"),
            "全局配置 : %s" % (self.config_dir or "<未找到>"),
            "登录凭证 : %s" % (self.login_dir or "<未找到>"),
            "账号目录 : %d 个" % len(self.account_dirs),
        ]
        for wxid, d in self.account_dirs:
            lines.append("    %s  ->  %s" % (wxid, d))
        if self.errors:
            lines.append("警告     :")
            lines.extend("    " + e for e in self.errors)
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "install_dir": self.install_dir,
            "exe_path": self.exe_path,
            "version": self.version,
            "appdata_dir": self.appdata_dir,
            "data_root": self.data_root,
            "files_dir": self.files_dir,
            "all_users_dir": self.all_users_dir,
            "config_dir": self.config_dir,
            "login_dir": self.login_dir,
            "account_dirs": self.account_dirs,
        }


def _reg_value(root, path, name):
    try:
        with winreg.OpenKey(root, path) as k:
            return winreg.QueryValueEx(k, name)[0]
    except OSError:
        return ""


def _find_install_dir() -> tuple:
    """返回 (install_dir, version)。优先读注册表，回退卸载表，再回退常见路径。"""
    for key_path in (r"Software\Tencent\Weixin", r"Software\Tencent\WeChat"):
        v = _reg_value(winreg.HKEY_CURRENT_USER, key_path, "InstallPath")
        if v and os.path.isdir(v):
            return v.strip('"'), ""

    uninstall_roots = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    for root, base in uninstall_roots:
        try:
            k = winreg.OpenKey(root, base)
        except OSError:
            continue
        i = 0
        while True:
            try:
                sub = winreg.EnumKey(k, i)
            except OSError:
                break
            i += 1
            try:
                with winreg.OpenKey(k, sub) as kk:
                    name = winreg.QueryValueEx(kk, "DisplayName")[0]
                    loc = winreg.QueryValueEx(kk, "InstallLocation")[0]
                    ver = winreg.QueryValueEx(kk, "DisplayVersion")[0]
            except OSError:
                continue
            if ("微信" in name or "WeChat" in name or "Weixin" in name) and loc:
                loc = loc.strip('"')
                if os.path.isdir(loc):
                    winreg.CloseKey(k)
                    return loc, ver
        winreg.CloseKey(k)

    for p in (r"C:\Program Files\Tencent\Weixin",
              r"C:\Program Files\Tencent\WeChat",
              r"C:\Program Files (x86)\Tencent\WeChat"):
        if os.path.isdir(p):
            return p, ""
    return "", ""


def _find_exe_and_version(install_dir: str, version: str) -> tuple:
    exe = ""
    for name in _EXE_CANDIDATES:
        p = os.path.join(install_dir, name)
        if os.path.isfile(p):
            exe = p
            break
    if not version:
        # 安装目录下的版本号子目录，如 4.1.15.13
        try:
            for name in sorted(os.listdir(install_dir)):
                parts = name.split(".")
                if os.path.isdir(os.path.join(install_dir, name)) and len(parts) == 4 \
                        and all(x.isdigit() for x in parts):
                    version = name
                    break
        except OSError:
            pass
    return exe, version


def _find_appdata_dir() -> str:
    appdata = os.environ.get("APPDATA", "")
    for name in _APPDATA_CANDIDATES:
        p = os.path.join(appdata, "Tencent", name)
        if os.path.isdir(p):
            return p
    return ""


def _read_data_root(appdata_dir: str) -> str:
    """数据根目录写在 appdata_dir\\config\\<md5>.ini 里，文件内容就是路径本身。"""
    cfg = os.path.join(appdata_dir, "config")
    try:
        for name in sorted(os.listdir(cfg)):
            if not name.lower().endswith(".ini"):
                continue
            with open(os.path.join(cfg, name), "rb") as f:
                raw = f.read(512)
            for enc in ("utf-8", "gbk"):
                try:
                    txt = raw.decode(enc).strip().strip("\x00").strip()
                except UnicodeDecodeError:
                    continue
                if txt and os.path.isdir(txt):
                    return txt
    except OSError:
        pass
    return ""


def _find_files_dir(data_root: str) -> str:
    for name in _FILES_CANDIDATES:
        p = os.path.join(data_root, name)
        if os.path.isdir(p):
            return p
    return ""


def _find_all_users(files_dir: str) -> str:
    for name in _ALLUSERS_CANDIDATES:
        p = os.path.join(files_dir, name)
        if os.path.isdir(p):
            return p
    return ""


def _find_account_dirs(files_dir: str) -> list:
    """账号数据目录形如 wxid_xxxx_hash；wxid 取 '_hash' 之前的部分。"""
    out = []
    if not files_dir or not os.path.isdir(files_dir):
        return out
    try:
        for name in sorted(os.listdir(files_dir)):
            if not name.startswith("wxid_"):
                continue
            full = os.path.join(files_dir, name)
            if not os.path.isdir(full):
                continue
            wxid = name.rsplit("_", 1)[0]
            out.append((wxid, full))
    except OSError:
        pass
    return out


def detect() -> WeChatEnv:
    """自动探测微信环境。任何一环缺失都会记录到 errors，不抛异常。"""
    env = WeChatEnv()

    env.install_dir, ver = _find_install_dir()
    if env.install_dir:
        env.exe_path, env.version = _find_exe_and_version(env.install_dir, ver)
    if not env.exe_path:
        env.errors.append("未找到 Weixin.exe / WeChat.exe")

    env.appdata_dir = _find_appdata_dir()
    if not env.appdata_dir:
        env.errors.append("未找到 %APPDATA%\\Tencent\\xwechat 运行目录")

    if env.appdata_dir:
        env.data_root = _read_data_root(env.appdata_dir)
    if not env.data_root:
        env.errors.append("未能从 config\\*.ini 读出数据目录，将回退到默认文档目录")
        docs = os.path.join(os.path.expanduser("~"), "Documents")
        env.data_root = docs

    env.files_dir = _find_files_dir(env.data_root)
    if not env.files_dir:
        env.errors.append("未找到 xwechat_files / WeChat Files")

    if env.files_dir:
        env.all_users_dir = _find_all_users(env.files_dir)
        env.account_dirs = _find_account_dirs(env.files_dir)
    if env.all_users_dir:
        env.config_dir = os.path.join(env.all_users_dir, "config")
        env.login_dir = os.path.join(env.all_users_dir, "login")
    if not env.account_dirs:
        env.errors.append("未在文件目录下发现 wxid_* 账号目录（可能尚未登录过任何账号）")

    env.ok = bool(env.exe_path and env.files_dir and env.all_users_dir)
    return env


# --------------------------------------------------------------- XWeb 运行时
# 微信 4.x 的小程序 / 内置浏览器宿主 WeChatAppEx.exe 采用「存根 + 版本化负载」部署：
#
#     ...\xplugin\plugins\RadiumWMPF\
#     ├─ WeChatAppEx.exe                 存根（入口）
#     └─ <版本号>\extracted\runtime\
#         ├─ xweb_elf.dll                ← 只存在于这里
#         ├─ WeChatAppEx.exe             与存根同内容（真正的宿主）
#         └─ flue.dll / XNet.dll / ...
#
# ★ 关键事实（2026-10-03 实测确立）：xweb_elf.dll 是存根的**第一个静态导入**，
#   加载器必须在进程执行任何一行代码之前解析它。而该 DLL **只存在于 runtime\
#   里**，存根自己所在的目录没有。Windows 的 DLL 搜索顺序中能命中 runtime\ 的
#   只有两条：
#
#     1) 当前工作目录恰好是 runtime\  —— 微信自己会这么设，但并不总是可靠。
#        一旦不是，进程直接以 0xC0000135(STATUS_DLL_NOT_FOUND) 死掉，由 csrss
#        弹出「系统错误：由于找不到 xweb_elf.dll」。一个实例会连带拉起 11 个
#        该进程，于是表现为"要点 11 下确定"。
#     2) runtime\ 出现在 PATH 里       —— 这一条可以从外部兜住，不必改动微信。
#
# 修复就落在第 2 条：启动微信前把 runtime\ 补进本进程的 PATH。实测：错误工作
# 目录 + PATH 前置 runtime 的启动结果，与工作目录正确时**完全一致**。
_XWEB_PLUGIN_ROOT = ("xplugin", "plugins", "RadiumWMPF")
_XWEB_DLL = "xweb_elf.dll"


def xweb_runtime_dir(appdata_dir: str = "") -> str:
    """微信 XWeb 宿主的运行时目录（含 xweb_elf.dll 的那一个）；找不到返回空串。

    约定位置为 `...\\xplugin\\plugins\\RadiumWMPF\\<版本号>\\extracted\\runtime`。
    可能有多个版本并存，取**版本号最大**且确实含 xweb_elf.dll 的那个。
    """
    root = appdata_dir or _find_appdata_dir()
    if not root:
        return ""
    host = os.path.join(root, *_XWEB_PLUGIN_ROOT)
    if not os.path.isdir(host):
        return ""
    try:
        names = os.listdir(host)
    except OSError:
        return ""
    found = []
    for name in names:
        rt = os.path.join(host, name, "extracted", "runtime")
        if os.path.isfile(os.path.join(rt, _XWEB_DLL)):
            found.append((name, rt))
    if not found:
        return ""
    numbered = [(int(n), rt) for n, rt in found if n.isdigit()]
    if numbered:
        return max(numbered, key=lambda it: it[0])[1]
    return found[0][1]


if __name__ == "__main__":
    e = detect()
    print(e.summary())
