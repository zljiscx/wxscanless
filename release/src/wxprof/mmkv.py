# -*- coding: utf-8 -*-
"""MMKV 解密与解析（纯 Python，无第三方依赖）。

背景（本机实测确认，2026-10-02）：
    微信 4.x 把"这次该登录哪个账号"记在
        <数据根>\\xwechat_files\\all_users\\config\\global_config  (+ .crc)
    这是一个 MMKV 文件，AES-128-CFB 加密。

    · 密钥 = Weixin.dll 里的明文常量 "xwechat_crypt_key" 的前 16 字节。
      此前 mmkv_decrypt.py（开发期诊断脚本，已删）穷举 6912 种 key/iv/模式组合
      全部失败，唯一原因就是从没试过这个常量。
    · 主文件布局：[4 字节头][密文 payload][0x00 填充]
      .crc 布局：crc32(4) | version(4) | sequence(4) | iv(16) | actualSize(4) | ...
    · payload 是 MMKV 的迷你 protobuf 序列：
          循环 { keyLen(varint) key valueLen(varint) value }
      字符串型 value 内部再套一层长度前缀。
    · CFB 是链式的，但**只有第一个分组受 IV 影响**：
          第 i 个明文块 P_i = C_i XOR E(K, C_{i-1})
      其中 C_{i-1} 是磁盘上的密文，与 IV 无关。所以本模块对所有候选 IV
      只重算第一块，其余复用同一条密文链。

为什么自带 AES 而不依赖 pycryptodome：
    项目运行环境是系统 Python 3.11，其中没有 pycryptodome；而且本工具要求
    兼容 Windows 7，第三方依赖越少越好。这里实现 AES-128 的加密方向即可
    ——CFB 解密用到的就是分组密码的加密方向。

用途：
    1. 读出某个 global_config 到底属于哪个账号（wxid / 昵称 / uin），
       用于**快照自校验**，避免串档（这是历史上一再踩坑的地方）。
    2. 供 watcher 判断"磁盘上这份配置归谁"，从而实现自动保存登录态。

用法：
    python -m wxprof.mmkv [--key] [路径...]
"""
from __future__ import annotations

import hashlib
import os
import struct
import sys

CRYPT_KEY = b"xwechat_crypt_key"[:16]       # 实测有效
_HEAD = 4                                    # 主文件头长度
_PRINTABLE = frozenset(range(32, 127))


# ============================================================ AES-128（加密方向）
def _rotl8(x: int, n: int) -> int:
    return ((x << n) | (x >> (8 - n))) & 0xFF


def _make_sbox() -> list:
    """按 Rijndael 有限域生成 S 盒（避免手抄 256 个常量出错）。"""
    sbox = [0] * 256
    p = q = 1
    for _ in range(256):
        p = (p ^ ((p << 1) & 0xFF) ^ (0x1B if p & 0x80 else 0)) & 0xFF
        q = (q ^ ((q << 1) & 0xFF)) & 0xFF
        q = (q ^ ((q << 2) & 0xFF)) & 0xFF
        q = (q ^ ((q << 4) & 0xFF)) & 0xFF
        if q & 0x80:
            q = (q ^ 0x09) & 0xFF
        sbox[p] = (q ^ _rotl8(q, 1) ^ _rotl8(q, 2) ^ _rotl8(q, 3)
                   ^ _rotl8(q, 4) ^ 0x63) & 0xFF
        if p == 1:
            break
    sbox[0] = 0x63
    return sbox


_SBOX = _make_sbox()
_RCON = (0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36)

# 自检：S 盒生成错误会让整条解密链静默失效，宁可在导入时就炸掉。
if not (_SBOX[0] == 0x63 and _SBOX[1] == 0x7C and _SBOX[0x53] == 0xED):
    raise RuntimeError("AES S-box 生成异常，请检查 mmkv._make_sbox 实现")


def _mul2(a: int) -> int:
    return ((a << 1) ^ 0x1B) & 0xFF if a & 0x80 else (a << 1) & 0xFF


def _expand_key(key: bytes) -> list:
    """AES-128 密钥扩展，返回 11 组轮密钥（各 16 字节）。"""
    w = list(key)
    for i in range(4, 44):
        t = w[-4:]
        if i % 4 == 0:
            t = [_SBOX[t[1]], _SBOX[t[2]], _SBOX[t[3]], _SBOX[t[0]]]
            t[0] ^= _RCON[i // 4 - 1]
        b = w[(i - 4) * 4:(i - 4) * 4 + 4]
        w.extend([b[j] ^ t[j] for j in range(4)])
    return [bytes(w[i * 16:(i + 1) * 16]) for i in range(11)]


def _shift_rows(s: list) -> list:
    """状态按列优先排列，行 r 循环左移 r 个位置。"""
    return [s[0], s[5], s[10], s[15],
            s[4], s[9], s[14], s[3],
            s[8], s[13], s[2], s[7],
            s[12], s[1], s[6], s[11]]


def _mix_columns(s: list) -> list:
    for c in range(4):
        i = c * 4
        a0, a1, a2, a3 = s[i], s[i + 1], s[i + 2], s[i + 3]
        t = a0 ^ a1 ^ a2 ^ a3
        s[i] = a0 ^ t ^ _mul2(a0 ^ a1)
        s[i + 1] = a1 ^ t ^ _mul2(a1 ^ a2)
        s[i + 2] = a2 ^ t ^ _mul2(a2 ^ a3)
        s[i + 3] = a3 ^ t ^ _mul2(a3 ^ a0)
    return s


def _encrypt_block(rk: list, block: bytes) -> bytes:
    s = list(block)
    for i in range(16):
        s[i] ^= rk[0][i]
    for r in range(1, 10):
        s = [_SBOX[b] for b in s]
        s = _shift_rows(s)
        s = _mix_columns(s)
        for i in range(16):
            s[i] ^= rk[r][i]
    s = [_SBOX[b] for b in s]
    s = _shift_rows(s)
    for i in range(16):
        s[i] ^= rk[10][i]
    return bytes(s)


class AesCfb128:
    """AES-128-CFB（segment_size=128，与 pycryptodome 默认行为一致）。"""

    def __init__(self, key: bytes = CRYPT_KEY):
        self._rk = _expand_key(key)

    def decrypt(self, iv: bytes, data: bytes) -> bytes:
        if len(iv) != 16:
            iv = (iv + b"\x00" * 16)[:16]
        out = bytearray()
        prev = iv
        for off in range(0, len(data), 16):
            blk = data[off:off + 16]
            ks = _encrypt_block(self._rk, prev)
            out += bytes(a ^ b for a, b in zip(blk, ks))
            prev = blk
        return bytes(out)

    def decrypt_many(self, ivs: list, data: bytes) -> list:
        """一次解密多个候选 IV，返回 [(名称, iv, 明文)]。

        CFB 链上只有第一块受 IV 影响，所以密文链只算一遍，
        每个候选 IV 只额外付出一次分组加密的代价。
        """
        blocks = [data[i:i + 16] for i in range(0, len(data), 16)]
        tail = [None] * len(blocks)
        for i in range(1, len(blocks)):
            tail[i] = _encrypt_block(self._rk, blocks[i - 1])
        out = []
        for name, iv in ivs:
            if len(iv) != 16:
                continue
            head = _encrypt_block(self._rk, iv)
            buf = bytearray()
            if blocks:
                buf += bytes(a ^ b for a, b in zip(blocks[0], head))
                for i in range(1, len(blocks)):
                    buf += bytes(a ^ b for a, b in zip(blocks[i], tail[i]))
            out.append((name, iv, bytes(buf)))
        return out


# ============================================================ MMKV payload 解析
def _varint(buf: bytes, i: int) -> tuple:
    """读一个 varint，返回 (值, 新位置)；越界/超长返回 (-1, i)。"""
    r = 0
    s = 0
    while i < len(buf):
        b = buf[i]
        i += 1
        r |= (b & 0x7F) << s
        if not (b & 0x80):
            return r, i
        s += 7
        if s > 28:
            return -1, i
    return -1, i


def _try_parse(payload: bytes, start: int) -> list:
    """从 start 处顺序解析 MMKV 条目，返回 [(key_bytes, value_bytes)]。"""
    ents = []
    i = start
    while i < len(payload) - 2:
        klen, i2 = _varint(payload, i)
        if not (0 < klen <= 300) or i2 + klen >= len(payload):
            break
        key = payload[i2:i2 + klen]
        if any(c not in _PRINTABLE for c in key):
            break
        j = i2 + klen
        vlen, j2 = _varint(payload, j)
        if vlen < 0 or j2 + vlen > len(payload):
            break
        ents.append((key, payload[j2:j2 + vlen]))
        i = j2 + vlen
    return ents


def _coverage(ents: list) -> int:
    return sum(len(k) + len(v) for k, v in ents)


def _best_alignment(payload: bytes) -> tuple:
    """返回 (起点, 条目列表, 覆盖率)。

    先用"长度前缀 + 全可打印键名"这个廉价特征筛出候选起点，再对候选做完整
    解析并按覆盖率取优。这样既能覆盖"首个条目因 IV 不可靠而不可达"的情况
    （真实起点可能在上百字节之后），又不必对每个偏移都做完整解析。
    """
    total = len(payload)
    best = (0, [], 0)
    i = 0
    while i < len(payload) - 2:
        klen, i2 = _varint(payload, i)
        if 0 < klen <= 300 and i2 + klen < len(payload):
            key = payload[i2:i2 + klen]
            if all(c in _PRINTABLE for c in key):
                ents = _try_parse(payload, i)
                if len(ents) >= 2:
                    cov = _coverage(ents)
                    if cov > best[2]:
                        best = (i, ents, cov)
                        if cov > total * 0.9:
                            break
        i += 1
    return best


def _iv_candidates(crc: bytes) -> list:
    """候选 IV。

    只影响 payload 最前面那一个分组，因此没必要穷举全空间；取 zero、
    .crc 的 iv 字段（MMKV 规范位置 12..28）以及该字段附近几个 4 字节
    对齐窗口即可，代价可控。
    """
    out = [("zero", b"\x00" * 16), ("crc[12:28]", crc[12:28])]
    for off in range(0, min(len(crc) - 16, 40) + 1, 4):
        out.append(("crc@%d" % off, crc[off:off + 16]))
    seen = set()
    uniq = []
    for name, iv in out:
        if len(iv) == 16 and iv not in seen:
            seen.add(iv)
            uniq.append((name, iv))
    return uniq


def decode(path: str) -> dict:
    """解密并解析一个 global_config 文件（只读，不修改任何文件）。"""
    with open(path, "rb") as f:
        blob = f.read()
    crc = b""
    if os.path.isfile(path + ".crc"):
        with open(path + ".crc", "rb") as f:
            crc = f.read()
    body = blob[_HEAD:]

    cipher = AesCfb128()
    best = None
    for name, iv, plain in cipher.decrypt_many(_iv_candidates(crc), body):
        start, ents, cov = _best_alignment(plain)
        # 评分：可解析起点越靠前越好（正确 IV 能让首个条目也解出来）
        score = (0 if ents else 1, start, -cov)
        if best is None or score < best[0]:
            best = (score, name, iv, plain, start, ents)

    _s, iv_name, iv, payload, start, ents = best
    header = struct.unpack_from("<I", blob, 0)[0]
    meta_size = struct.unpack_from("<I", crc, 28)[0] if len(crc) >= 32 else 0
    return {"path": path, "iv_name": iv_name, "iv": iv.hex(),
            "header": header, "meta_size": meta_size,
            "payload": payload, "lost_head": start,
            "entries": ents, "coverage": _coverage(ents)}


# ============================================================ 取值
def val_bytes(v: bytes) -> bytes:
    """剥掉字符串值的内层长度前缀。"""
    if not v:
        return b""
    n, off = _varint(v, 0)
    if n >= 0 and off + n <= len(v):
        return v[off:off + n]
    return v


def val_str(v: bytes) -> str:
    raw = val_bytes(v)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return ""


def val_int(v: bytes) -> int:
    raw = val_bytes(v)
    if not raw:
        return 0
    return int.from_bytes(raw, "little")


def as_dict(info: dict) -> dict:
    """把条目整理成 {key: value}。"""
    return {k.decode("utf-8", "replace"): v for k, v in info["entries"]}


def summary(info: dict) -> dict:
    """抽出与登录态直接相关的关键字段。"""
    d = as_dict(info)
    # ★ uin 必须按 **varint** 解码，不能按小端整数读：同一份 5 字节
    #   `ad ac 88 86 0a` 的 varint 值是 2697074221（=槽位 monitordata 里的数字），
    #   而小端 u64 读出来是 45206777005 —— 那是同一字段的另一种读法，是错的。
    _raw_uin = val_bytes(d.get("ilink_current_uin", b""))
    _uin_dec, _ = _varint(_raw_uin, 0)
    return {
        "wxid": val_str(d.get("mmkv_key_user_name", b"")),
        "nickname": val_str(d.get("mmkv_key_nick_name", b"")),
        "login_username": val_str(d.get("mmkv_key_latest_login_username", b"")),
        "uin": val_int(d.get("mmkv_key_latest_login_uin", b"")),
        "current_uin": val_int(d.get("ilink_current_uin", b"")),
        "uin_dec": _uin_dec if _uin_dec > 0 else 0,
        "auto_login_flag": val_int(d.get("mmkv_key_auto_login_flag", b"")),
        "logout_locked": val_int(d.get("mmkv_key_logout_locked_flag", b"")),
        "push_login_expired": val_int(
            d.get("mmkv_key_push_login_url_expired_time", b"")),
        "has_auth_key": "mmkv_key_auto_auth_key" in d,
        # 登录票据（auto_auth_key）的长度与指纹 —— 它是"能不能免扫码"的唯一
        # 关键字段，且每次登录都会轮换。用它判断票据有没有换新，比整文件 md5
        # 更精准：微信可能只改了个无关字段，票据其实没动。
        "auth_len": len(d.get("mmkv_key_auto_auth_key", b"")),
        "auth_fp": hashlib.md5(
            d.get("mmkv_key_auto_auth_key", b"")).hexdigest()[:16],
        "server_id": val_str(d.get("mmkv_key_server_id", b"")),
        "pc_account_name": val_str(d.get("mmkv_key_pc_account_name", b"")),
        # 该账号自己的头像 URL（显示在登录页/账号卡片上）。实测可下载。
        "head_img_url": val_str(d.get("mmkv_key_head_img_url", b"")),
        "entries": len(info["entries"]),
        "lost_head_bytes": info["lost_head"],
    }


# ============================================================ 面向 watcher 的接口
_probe_cache = {}      # 绝对路径 -> (md5, summary dict)


def fingerprint(path: str) -> str:
    """文件内容 md5。MMKV 是内存映射文件，写完不一定更新 mtime，
    所以判断"变没变"必须看内容，不能看时间。"""
    try:
        with open(path, "rb") as f:
            return hashlib.md5(f.read()).hexdigest()
    except OSError:
        return ""


def probe(path: str) -> dict:
    """解密并摘要一份 global_config，按**内容指纹**缓存。

    返回 summary() 的全部字段，外加 fp（文件内容 md5）。
    文件不存在或解密失败返回 {}。watcher 每 2 秒问一次，靠这层缓存把
    真正解密（纯 Python AES，约毫秒级）压到"内容变化时"才做一次。
    """
    fp = fingerprint(path)
    if not fp:
        return {}
    key = os.path.normcase(os.path.abspath(path))
    hit = _probe_cache.get(key)
    if hit and hit[0] == fp:
        return hit[1]
    try:
        s = summary(decode(path))
    except Exception:                      # noqa: BLE001 解密失败不应打断监控
        s = {}
    s["fp"] = fp
    _probe_cache[key] = (fp, s)
    return s


def owner_of(path: str, fp: str = None) -> str:
    """这份 global_config 属于哪个账号（wxid）；解不出返回空串。"""
    if not path or not os.path.isfile(path):
        return ""
    return probe(path).get("wxid", "")


def ticket_fp(path: str) -> str:
    """登录票据（auto_auth_key）的指纹；没有票据返回空串。"""
    p = probe(path)
    return p.get("auth_fp", "") if p.get("auth_len") else ""


def forget_cache() -> None:
    _probe_cache.clear()


# ============================================================ CLI
def _fmt(name: str, info: dict) -> str:
    s = summary(info)
    tag = "有自动登录标记" if s["auto_login_flag"] else "无自动登录标记"
    return "\n".join([
        "%s" % name,
        "   账号     : %s  (%s)" % (s["wxid"] or "<空>", s["nickname"] or "-"),
        "   uin      : %s / current=%s" % (s["uin"], s["current_uin"]),
        "   自动登录 : auto_login_flag=%d %s, 锁定=%d"
        % (s["auto_login_flag"], tag, s["logout_locked"]),
        "   有效期   : push_login_url_expired_time=%d 秒" % s["push_login_expired"],
        "   auth key : %s" % ("有" if s["has_auth_key"] else "无"),
        "   IV 候选  : %s" % info["iv_name"],
        "   条目     : %d（前 %d 字节因 IV 不可靠而未解析）"
        % (s["entries"], s["lost_head_bytes"]),
    ])


def default_targets() -> list:
    """默认检查对象：磁盘上活动的 global_config + 本项目全部快照。"""
    out = []
    ap = os.environ.get("APPDATA", "")
    p = os.path.join(ap, "Tencent", "xwechat", "config")
    root = ""
    if os.path.isdir(p):
        for n in sorted(os.listdir(p)):
            if not n.lower().endswith(".ini"):
                continue
            try:
                with open(os.path.join(p, n), "rb") as f:
                    raw = f.read()
            except OSError:
                continue
            # 实测（2026-10-02）：该 ini 是 UTF-8；早期误用 gbk 会把
            # "微信聊天记录" 解成 "寰?淇¤亰澶╄?板綍"。
            for enc in ("utf-8-sig", "utf-8", "gbk"):
                try:
                    root = raw.decode(enc).strip().strip("\x00").strip()
                    break
                except UnicodeDecodeError:
                    continue
            break
    if root:
        gp = os.path.join(root, "xwechat_files", "all_users",
                          "config", "global_config")
        if os.path.isfile(gp):
            out.append(gp)
    data = os.path.join(_project_root(), "data")
    if os.path.isdir(data):
        for aid in sorted(os.listdir(data)):
            gp = os.path.join(data, aid, "cfg", "global_config")
            if os.path.isfile(gp):
                out.append(gp)
    return out


def _project_root() -> str:
    """项目根（同时含 release\\ 与 data\\ 的那一级）。

    优先用启动器注入的 WXPROF_ROOT；直接以脚本运行时按目录层级回退 ——
    本文件位于 release\\src\\wxprof\\，向上四级即项目根。
    """
    env = os.environ.get("WXPROF_ROOT")
    if env:
        return env
    return os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))


def main(argv) -> int:
    only_key = "--key" in argv
    targets = [a for a in argv if not a.startswith("--")]
    if not targets:
        targets = default_targets()
    if not targets:
        print("没有找到可检查的 global_config")
        return 1
    base = _project_root()
    for p in targets:
        try:
            name = os.path.relpath(p, base)
        except ValueError:              # 跨盘符（如 F:\ 数据根）
            name = p
        try:
            info = decode(p)
        except Exception as e:          # noqa: BLE001
            print("%s\n   解析失败：%s" % (name, e))
            continue
        if only_key:
            s = summary(info)
            print("%-52s %-24s %s  auto=%d  exp=%d"
                  % (name, s["wxid"] or "<空>", s["nickname"] or "-",
                     s["auto_login_flag"], s["push_login_expired"]))
        else:
            print(_fmt(name, info))
            print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
