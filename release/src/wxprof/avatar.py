# -*- coding: utf-8 -*-
"""账号头像：从微信 CDN 下载，并转成 tkinter 能显示的 PNG。

来源：`global_config` 里的 **`mmkv_key_head_img_url`**（该账号自己的头像 URL），
形如 `http://wx.qlogo.cn/mmhead/ver_1/<hash>/132`（实测 HTTP 200、JPEG、132×132；
`/0` 是原图，`/640` 返回 400）。

为什么不用 `all_users\\head_imgs\\<数字>\\<数字>`：那两个数字与账号按时间戳一一
对应，但**无法由 wxid / uin / 头像URL 经 md5/sha1/sha256/crc32/Java-hash/FNV 推出**，
全盘也无明文引用 ⇒ 无法建立"账号 → 目录"的映射，弃用（详见
_docs/项目实现与实测结论.md §3.5）。

为什么要自己转 PNG：tkinter（Tk 8.6）原生只认 PNG/GIF/PPM，**不认 JPEG**；
而本机 Python 3.11 没有 Pillow（且 uiautomation 只装在 3.11）。所以用
**GDI+**（Windows 自带 gdiplus.dll，Win7 起就有）通过 ctypes 转码，
**零第三方依赖**。
"""
from __future__ import annotations

import ctypes
import os
import tempfile
import urllib.request
from ctypes import wintypes

UA = ("Mozilla/5.0 (Windows NT 6.1; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0 Safari/537.36")
_HEADERS = {"User-Agent": UA, "Accept": "image/*,*/*;q=0.8"}
AVATAR_SIZE = 132          # 向 CDN 请求的边长（实测 132 可用；/640 返 400；/0 是原图）
DISPLAY_SIZE = 88          # 落盘边长 = 界面显示边长，**1:1 绘制**，不依赖 tk 缩放


# --------------------------------------------------------------- 下载
def download(url: str, dest: str, timeout: float = 10.0) -> bool:
    """下载 url 到 dest。成功返回 True。"""
    if not url:
        return False
    try:
        req = urllib.request.Request(url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
        if not data or len(data) < 64:
            return False
        os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(data)
        return True
    except Exception:                           # noqa: BLE001
        # 再试一次系统代理设置（部分环境需要）
        try:
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler(urllib.request.getproxies()))
            req = urllib.request.Request(url, headers=_HEADERS)
            with opener.open(req, timeout=timeout) as r:
                data = r.read()
            if data and len(data) >= 64:
                with open(dest, "wb") as f:
                    f.write(data)
                return True
        except Exception:                       # noqa: BLE001
            pass
        return False


# --------------------------------------------------------------- GDI+ 转码
_gdi = ctypes.windll.gdiplus


class _GdiplusStartupInput(ctypes.Structure):
    _fields_ = [("GdiplusVersion", ctypes.c_uint32),
                ("DebugEventCallback", ctypes.c_void_p),
                ("SuppressBackgroundThread", wintypes.BOOL),
                ("SuppressExternalCodecs", wintypes.BOOL)]


class _GUID(ctypes.Structure):
    _fields_ = [("d1", ctypes.c_uint32), ("d2", ctypes.c_uint16),
                ("d3", ctypes.c_uint16), ("d4", ctypes.c_ubyte * 8)]


class _ImageCodecInfo(ctypes.Structure):
    _fields_ = [("Clsid", _GUID), ("FormatID", _GUID),
                ("CodecName", ctypes.c_wchar_p), ("DllName", ctypes.c_wchar_p),
                ("FormatDescription", ctypes.c_wchar_p),
                ("FilenameExtension", ctypes.c_wchar_p),
                ("MimeType", ctypes.c_wchar_p), ("Flags", ctypes.c_uint32),
                ("Version", ctypes.c_uint32), ("SigCount", ctypes.c_uint32),
                ("SigSize", ctypes.c_uint32), ("SigPattern", ctypes.c_void_p),
                ("SigMask", ctypes.c_void_p)]


def _png_clsid():
    num = ctypes.c_uint(0)
    size = ctypes.c_uint(0)
    if _gdi.GdipGetImageEncodersSize(ctypes.byref(num), ctypes.byref(size)) != 0:
        return None
    if not size.value:
        return None
    buf = ctypes.create_string_buffer(size.value)
    if _gdi.GdipGetImageEncoders(num, size, buf) != 0:
        return None
    arr = ctypes.cast(buf, ctypes.POINTER(_ImageCodecInfo))
    for i in range(num.value):
        info = arr[i]
        if info.MimeType and "png" in info.MimeType.lower():
            return info.Clsid
    return None


def jpeg_to_png(src: str, dst: str) -> bool:
    """用 GDI+ 把 src（JPEG/其它 GDI+ 支持的格式）转成 PNG 写到 dst。"""
    token = ctypes.c_void_p()
    si = _GdiplusStartupInput(1, None, False, False)
    if _gdi.GdiplusStartup(ctypes.byref(token), ctypes.byref(si), None) != 0:
        return False
    bmp = ctypes.c_void_p()
    try:
        if _gdi.GdipCreateBitmapFromFile(ctypes.c_wchar_p(src),
                                         ctypes.byref(bmp)) != 0 or not bmp:
            return False
        clsid = _png_clsid()
        if clsid is None:
            return False
        os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
        st = _gdi.GdipSaveImageToFile(bmp, ctypes.c_wchar_p(dst),
                                      ctypes.byref(clsid), None)
        return st == 0 and os.path.isfile(dst)
    finally:
        if bmp:
            _gdi.GdipDisposeImage(bmp)
        _gdi.GdiplusShutdown(token)


# 插值/平滑模式的 GDI+ 取值
_INTERP_HIGH_QUALITY_BICUBIC = 7
_SMOOTH_ANTIALIAS = 4
_PIXEL_OFFSET_HIGH_QUALITY = 2
_PIXEL_FORMAT_32BPP_ARGB = 0x26200A


def scale_to_png(src: str, dst: str, size: int) -> bool:
    """用 GDI+ 把 src 高质量缩放到 size×size 的 PNG 写到 dst。

    为什么不在界面里缩：tk 的 `PhotoImage` 只有整数倍 `subsample()`，
    132→88 这类非整数倍根本没法做，硬放就会"只露一角"。所以**落盘即目标尺寸**，
    界面 1:1 绘制 —— 既完整又清晰。
    """
    if size <= 0:
        return jpeg_to_png(src, dst)
    token = ctypes.c_void_p()
    si = _GdiplusStartupInput(1, None, False, False)
    if _gdi.GdiplusStartup(ctypes.byref(token), ctypes.byref(si), None) != 0:
        return False
    bmp = ctypes.c_void_p()
    out = ctypes.c_void_p()
    g = ctypes.c_void_p()
    try:
        if _gdi.GdipCreateBitmapFromFile(ctypes.c_wchar_p(src),
                                         ctypes.byref(bmp)) != 0 or not bmp:
            return False
        if _gdi.GdipCreateBitmapFromScan0(size, size, 0, _PIXEL_FORMAT_32BPP_ARGB,
                                          None, ctypes.byref(out)) != 0 or not out:
            return False
        if _gdi.GdipGetImageGraphicsContext(out, ctypes.byref(g)) != 0 or not g:
            return False
        _gdi.GdipSetInterpolationMode(g, _INTERP_HIGH_QUALITY_BICUBIC)
        _gdi.GdipSetSmoothingMode(g, _SMOOTH_ANTIALIAS)
        _gdi.GdipSetPixelOffsetMode(g, _PIXEL_OFFSET_HIGH_QUALITY)
        if _gdi.GdipDrawImageRectI(g, bmp, 0, 0, size, size) != 0:
            return False
        clsid = _png_clsid()
        if clsid is None:
            return False
        os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
        st = _gdi.GdipSaveImageToFile(out, ctypes.c_wchar_p(dst),
                                      ctypes.byref(clsid), None)
        return st == 0 and os.path.isfile(dst)
    finally:
        if g:
            _gdi.GdipDeleteGraphics(g)
        if out:
            _gdi.GdipDisposeImage(out)
        if bmp:
            _gdi.GdipDisposeImage(bmp)
        _gdi.GdiplusShutdown(token)


# --------------------------------------------------------------- 离线灰度
# 界面里"离线账号头像置灰"需要一张灰度图。tkinter 自己没有灰度能力
# （`PhotoImage` 只支持整倍缩放），所以还是走 GDI+：新建一张同尺寸的 32bpp
# 位图把原图画上去，逐像素按亮度重算后存成 PNG。标准库 + 系统 DLL，零依赖。
#
# 为什么不用 `GdipSetImageAttributesColorMatrix`：那要构造 5×5 矩阵并改用
# `GdipDrawImageRectRectI`，参数多、失败只回一个状态码，不好排查；而头像只有
# 88×88 ≈ 7.7k 像素，逐像素读写几十毫秒，且**生成一次即落盘**长期复用。
GRAY_SUFFIX = "_gray"
_LUMA_R, _LUMA_G, _LUMA_B = 299, 587, 114      # ITU-R BT.601 亮度权重（千分比）

_gdi.GdipBitmapGetPixel.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                    ctypes.POINTER(ctypes.c_uint32)]
_gdi.GdipBitmapGetPixel.restype = ctypes.c_int
_gdi.GdipBitmapSetPixel.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_uint32]
_gdi.GdipBitmapSetPixel.restype = ctypes.c_int
_gdi.GdipGetImageWidth.argtypes = [ctypes.c_void_p,
                                   ctypes.POINTER(ctypes.c_uint32)]
_gdi.GdipGetImageWidth.restype = ctypes.c_int
_gdi.GdipGetImageHeight.argtypes = [ctypes.c_void_p,
                                    ctypes.POINTER(ctypes.c_uint32)]
_gdi.GdipGetImageHeight.restype = ctypes.c_int
_gdi.GdipCreateBitmapFromScan0.argtypes = [ctypes.c_int, ctypes.c_int,
                                           ctypes.c_int, ctypes.c_int,
                                           ctypes.c_void_p,
                                           ctypes.POINTER(ctypes.c_void_p)]
_gdi.GdipCreateBitmapFromScan0.restype = ctypes.c_int
_gdi.GdipDrawImageRectI.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                    ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int]
_gdi.GdipDrawImageRectI.restype = ctypes.c_int
_gdi.GdipGetImageGraphicsContext.argtypes = [ctypes.c_void_p,
                                             ctypes.POINTER(ctypes.c_void_p)]
_gdi.GdipGetImageGraphicsContext.restype = ctypes.c_int
_gdi.GdipDeleteGraphics.argtypes = [ctypes.c_void_p]
_gdi.GdipDeleteGraphics.restype = ctypes.c_int
_gdi.GdipDisposeImage.argtypes = [ctypes.c_void_p]
_gdi.GdipDisposeImage.restype = ctypes.c_int
_gdi.GdipCreateBitmapFromFile.argtypes = [ctypes.c_wchar_p,
                                          ctypes.POINTER(ctypes.c_void_p)]
_gdi.GdipCreateBitmapFromFile.restype = ctypes.c_int
_gdi.GdipSaveImageToFile.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p,
                                     ctypes.c_void_p, ctypes.c_void_p]
_gdi.GdipSaveImageToFile.restype = ctypes.c_int


def gray_path_of(png_path: str) -> str:
    """灰度副本的路径约定：与彩色图同目录，文件名加 `_gray`。"""
    root, ext = os.path.splitext(png_path)
    return root + GRAY_SUFFIX + ext


def to_gray_png(src: str, dst: str) -> bool:
    """把 src 转成灰度 PNG 写到 dst（保留尺寸与 alpha）。失败返回 False，不抛异常。"""
    if not os.path.isfile(src):
        return False
    token = ctypes.c_void_p()
    si = _GdiplusStartupInput(1, None, False, False)
    if _gdi.GdiplusStartup(ctypes.byref(token), ctypes.byref(si), None) != 0:
        return False
    src_bmp = ctypes.c_void_p()
    out = ctypes.c_void_p()
    g = ctypes.c_void_p()
    g_alive = False
    try:
        if _gdi.GdipCreateBitmapFromFile(ctypes.c_wchar_p(src),
                                         ctypes.byref(src_bmp)) != 0 or not src_bmp:
            return False
        w = ctypes.c_uint32(0)
        h = ctypes.c_uint32(0)
        if _gdi.GdipGetImageWidth(src_bmp, ctypes.byref(w)) != 0 or not w.value:
            return False
        if _gdi.GdipGetImageHeight(src_bmp, ctypes.byref(h)) != 0 or not h.value:
            return False
        # 先落到 32bppARGB 再逐像素处理：源若是索引色等格式，SetPixel 会失败。
        if _gdi.GdipCreateBitmapFromScan0(w.value, h.value, 0,
                                          _PIXEL_FORMAT_32BPP_ARGB, None,
                                          ctypes.byref(out)) != 0 or not out:
            return False
        if _gdi.GdipGetImageGraphicsContext(out, ctypes.byref(g)) != 0 or not g:
            return False
        g_alive = True
        if _gdi.GdipDrawImageRectI(g, src_bmp, 0, 0, w.value, h.value) != 0:
            return False
        _gdi.GdipDeleteGraphics(g)              # 画完立刻释放，否则位图仍被锁
        g_alive = False
        px = ctypes.c_uint32(0)
        for y in range(h.value):
            for x in range(w.value):
                if _gdi.GdipBitmapGetPixel(out, x, y, ctypes.byref(px)) != 0:
                    continue
                v = px.value
                lum = (((v >> 16) & 0xFF) * _LUMA_R
                       + ((v >> 8) & 0xFF) * _LUMA_G
                       + (v & 0xFF) * _LUMA_B) // 1000
                _gdi.GdipBitmapSetPixel(out, x, y,
                                        (((v >> 24) & 0xFF) << 24)
                                        | (lum << 16) | (lum << 8) | lum)
        clsid = _png_clsid()
        if clsid is None:
            return False
        os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
        st = _gdi.GdipSaveImageToFile(out, ctypes.c_wchar_p(dst),
                                      ctypes.byref(clsid), None)
        return st == 0 and os.path.isfile(dst)
    finally:
        if g_alive:
            _gdi.GdipDeleteGraphics(g)
        if out:
            _gdi.GdipDisposeImage(out)
        if src_bmp:
            _gdi.GdipDisposeImage(src_bmp)
        _gdi.GdiplusShutdown(token)


def save_avatar(url: str, dst_png: str, size: int = DISPLAY_SIZE) -> bool:
    """下载头像 → 缩放到 size×size → 存成 PNG。全程失败返回 False（不抛异常）。"""
    if not url:
        return False
    tmp = os.path.join(tempfile.gettempdir(),
                       "wxprof_avatar_%d.jpg" % os.getpid())
    try:
        if not download(url, tmp):
            return False
        if scale_to_png(tmp, dst_png, size):
            return True
        # 转码失败但下载成功：至少把原图留下，界面会回退到首字母占位
        try:
            os.replace(tmp, os.path.splitext(dst_png)[0] + ".jpg")
        except OSError:
            pass
        return False
    finally:
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except OSError:
            pass


def normalize_url(url: str, size: int = AVATAR_SIZE) -> str:
    """把 URL 末尾的尺寸段换成 size；不是 qlogo 形式则原样返回。"""
    if not url:
        return ""
    if "/mmhead/" in url or "/mmopen/" in url:
        parts = url.rstrip("/").split("/")
        if parts and parts[-1].isdigit():
            parts[-1] = str(size)
            return "/".join(parts)
    return url
