# -*- coding: utf-8 -*-
"""微信多账号免扫码登录器 —— **启动器**（唯一被打包进 EXE 的代码）。

职责只有两件，业务逻辑一概不在这里：

    1. 定位程序根目录（exe 同级）与外置代码区 `release\\`，做一次完整性自检；
    2. 从 `release\\` 加载业务代码，把控制权交给它的 `main()`。

**为什么这样设计**：业务功能全部位于外置的 `release\\` 目录，更新功能只需替换该
目录的内容，**无需重新打包 exe**。EXE 只在两处需要重建：换 Python 版本，或改变
图标/文件名等自身属性。

    release\\
    ├─ app.py              界面与装配
    ├─ src\\wxprof\\        业务模块
    └─ ext\\                第三方依赖（uiautomation / comtypes）

注意：这些模块**不能被 PyInstaller 打进 EXE**。打包器会把导入器注册到
`sys.meta_path`，其优先级高于 `sys.path` —— 一旦打进包，外置的同名模块将永远
不会被加载，且不会报任何错误。故打包命令必须显式排除它们。

★ v1.5.1 起**不再在 EXE 内放 release 的副本**（历史包袱叫"出厂副本"/`_factory`）：
那种"release 损坏就用快照自动重建"的做法与本项目的核心约定直接冲突 ——
既然"改功能只换 release\\、不必重打包"，release\\必然长期领先于快照；一旦触发自愈，
就是把用户已经改过好几版的代码**静默退回旧版**。现在改为**只报告、不修复**：
release\\ 缺文件或加载失败时，日志里写清楚缺什么、堆栈是什么，并弹窗提示 ——
由人来决定怎么恢复，程序绝不擅自替换代码。
"""
from __future__ import annotations

import os
import sys
import time
import traceback

from tkinter import messagebox

# ------------------------------------------------------------------ 路径
# 打包后：根目录 = exe 所在目录；开发期：根目录 = 本文件所在目录。
# 注意不能用 __file__ 定位 exe —— 单文件模式下它指向临时解压目录 _MEIPASS。
IS_FROZEN = bool(getattr(sys, "frozen", False))
if IS_FROZEN:
    ROOT = os.path.dirname(os.path.abspath(sys.executable))
else:
    ROOT = os.path.dirname(os.path.abspath(__file__))

RELEASE = os.path.join(ROOT, "release")
APP_PY = os.path.join(RELEASE, "app.py")
LOG_PATH = os.path.join(ROOT, "logs", "wechat_launcher.log")

# 完整性自检点：**只用于在日志里精确定位"缺了什么"，不做任何修复**。
PROBE = (
    "app.py",
    os.path.join("src", "wxprof", "__init__.py"),
    os.path.join("ext", "uiautomation", "__init__.py"),
    os.path.join("ext", "comtypes", "__init__.py"),
)


# ------------------------------------------------------------------ 日志
def log(text: str) -> None:
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write("[%s] [启动器] %s\n"
                    % (time.strftime("%Y-%m-%d %H:%M:%S"), text.rstrip()))
    except OSError:
        pass


if IS_FROZEN:                       # --noconsole 下 stdout/stderr 为 None
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


# ------------------------------------------------------------ 完整性自检
def missing_parts() -> list:
    """`release\\` 里缺哪些部件。**只报告，不修复**（见模块头说明）。"""
    return [p for p in PROBE if not os.path.exists(os.path.join(RELEASE, p))]


# ---------------------------------------------------------------- 加载业务
def load_app(tag: str):
    """从外置的 app.py 加载模块。

    用 importlib 按**文件路径**加载，而不是 `import app` —— 既不污染顶层
    命名空间，也避免与其它同名模块冲突。`__file__` 此时指向真实的
    release\\app.py，业务侧据此推出的路径全部正确。
    """
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
        sys.modules.pop(name, None)         # 失败时清理半成品，便于重试
        raise
    return mod


# ---------------------------------------------------------------- 异常兜底
def on_uncaught(exc_type, exc, tb) -> None:
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    log("未捕获异常：\n" + text)
    miss = missing_parts()
    tail = ""
    if miss:
        tail = "\n\n外置代码区疑似不完整，缺少：\n  " + "\n  ".join(miss)
    try:
        messagebox.showerror(
            "微信多账号免扫码登录器 — 启动失败",
            "%s: %s\n\n程序目录：\n%s%s\n\n完整日志：\n%s\n\n"
            "本程序不会自动替换或修复 release 目录，请按日志/上文恢复文件。"
            % (exc_type.__name__, exc, ROOT, tail, LOG_PATH))
    except Exception:                       # noqa: BLE001
        pass


# ------------------------------------------------------------------ 主流程
def main() -> None:
    log("启动：frozen=%s  根目录=%s" % (IS_FROZEN, ROOT))

    miss = missing_parts()
    if miss:
        # ★ 只报告。绝不从任何"副本"里铺回去 —— 那会静默回退用户的代码版本。
        log("外置代码区不完整，缺少：%s" % ", ".join(miss))

    if not os.path.isfile(APP_PY):
        raise RuntimeError(
            "找不到业务入口：%s\n"
            "请确认 release\\ 目录与程序放在同一位置且内容完整。" % APP_PY)

    # 让业务代码在任何阶段都能找到 ext / src（app.py 自身也会补一次，幂等）
    os.environ["WXPROF_ROOT"] = ROOT
    for p in (os.path.join(RELEASE, "ext"), os.path.join(RELEASE, "src")):
        if os.path.isdir(p) and p not in sys.path:
            sys.path.insert(0, p)

    try:
        app = load_app("main")
    except Exception:
        log("加载外置代码失败：\n" + traceback.format_exc())
        raise                               # 交给 on_uncaught 弹窗报错，不尝试替换

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
