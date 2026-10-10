# -*- coding: utf-8 -*-
"""启动器：唯一被打包进 EXE 的代码。定位程序根目录与外置代码区，做完整性自检后加载业务代码并转交 main()。"""
from __future__ import annotations

import os
import sys
import time
import traceback


# 打包后：根目录 = exe 所在目录；开发期：根目录 = 本文件所在目录。
IS_FROZEN = bool(getattr(sys, "frozen", False))
if IS_FROZEN:
    ROOT = os.path.dirname(os.path.abspath(sys.executable))
else:
    ROOT = os.path.dirname(os.path.abspath(__file__))

RELEASE = os.path.join(ROOT, "release")
APP_PY = os.path.join(RELEASE, "app.py")
LOG_PATH = os.path.join(ROOT, "logs", "wechat_launcher.log")

# release 完整性自检点（只报告，不做修复）
PROBE = (
    "app.py",
    os.path.join("src", "wxprof", "__init__.py"),
    os.path.join("ext", "uiautomation", "__init__.py"),
    os.path.join("ext", "comtypes", "__init__.py"),
)


def log(text: str) -> None:
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write("[%s] [启动器] %s\n"
                    % (time.strftime("%Y-%m-%d %H:%M:%S"), text.rstrip()))
    except OSError:
        pass


if IS_FROZEN:
    class _LogStream:
        def write(self, s):
            if s and s.strip():
                log(s)

        def flush(self):
            pass

        def isatty(self):
            return False

    if sys.stdout is None:
        sys.stdout = _LogStream()
    if sys.stderr is None:
        sys.stderr = _LogStream()


def missing_parts() -> list:
    """`release\\` 里缺哪些部件。**只报告，不修复**（见模块头说明）。"""
    return [p for p in PROBE if not os.path.exists(os.path.join(RELEASE, p))]


def load_app(tag: str):
    """从外置的 app.py 加载模块（按文件路径加载，不污染顶层命名空间）。"""
    import importlib.util

    name = "wxprof_ui_" + tag
    spec = importlib.util.spec_from_file_location(name, APP_PY)
    if spec is None or spec.loader is None:
        raise ImportError("无法为 %s 建立模块规格" % APP_PY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return mod


def on_uncaught(exc_type, exc, tb) -> None:
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    log("未捕获异常：\n" + text)
    miss = missing_parts()
    tail = ""
    if miss:
        tail = "\n\n外置代码区疑似不完整，缺少：\n  " + "\n  ".join(miss)
    log("启动失败：已记录完整异常与缺失信息，请按日志恢复 release 目录。")


def main() -> None:
    log("启动：frozen=%s  根目录=%s" % (IS_FROZEN, ROOT))

    miss = missing_parts()
    if miss:
        log("外置代码区不完整，缺少：%s" % ", ".join(miss))

    if not os.path.isfile(APP_PY):
        raise RuntimeError(
            "找不到业务入口：%s\n"
            "请确认 release\\ 目录与程序放在同一位置且内容完整。" % APP_PY)

    os.environ["WXPROF_ROOT"] = ROOT
    for p in (os.path.join(RELEASE, "ext"), os.path.join(RELEASE, "src")):
        if os.path.isdir(p) and p not in sys.path:
            sys.path.insert(0, p)

    try:
        app = load_app("main")
    except Exception:
        log("加载外置代码失败：\n" + traceback.format_exc())
        raise

    app.main()


def run() -> None:
    sys.excepthook = on_uncaught
    try:
        main()
    except SystemExit:
        raise
    except BaseException:                   # noqa: BLE001
        on_uncaught(*sys.exc_info())


if __name__ == "__main__":
    run()
