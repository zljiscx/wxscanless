# Third-party notices

本项目把以下第三方组件**原样内置**在 `release/ext/` 中，仅为了让运行期不需要
`pip install` 任何东西。各组件的版权与许可归原作者所有；涉及这些组件时，
以下条款优先于本项目自身的许可。

## uiautomation 2.0.29 — `release/ext/uiautomation/`

- 来源：<https://github.com/yinkaisheng/Python-UIAutomation-for-Windows>
- 许可：**Apache License 2.0**
- 依据：`release/ext/uiautomation/uiautomation.py` 头部声明
- 附带的 `bin/UIAutomationClient_VC140_X64.dll` / `X86.dll` 为 Microsoft 提供的
  UIAutomation COM 服务器实现，随 Windows SDK 分发。

## comtypes 1.4.17 — `release/ext/comtypes/`

- 来源：<https://github.com/enthought/comtypes>
- 许可：**MIT**
- 依据：上游仓库 `LICENSE.txt` 原文
- 版权：© 2006-2013 Thomas Heller，© 2014 Comtypes Developers
- `gen/` 下由 `gencache` 生成的模块、以及 `gen/UIAutomationClient.py` 同样适用上游许可。

## 仅构建期使用（不随本仓库分发）

- PyInstaller 6.22.3 — **GPL-2.0-or-later**，附一项特别例外：
  *允许使用 PyInstaller 构建并分发非自由程序（包括商业程序）*。
  © 2005-2020 Francesco Piccoli / Johann C. Rocholl。
  仅在重新打包 exe 时需要；按其许可，分发打包产物时需一并附上其许可与源码获取方式。

> 因此，用本工具打包出的 exe 不受 GPL 传染 —— 是否开源取决于本项目自身的许可。

## 本项目自身

以 **GPL-3.0** 许可发布，见 [`LICENSE`](LICENSE)。

上述两个运行期组件（Apache-2.0、MIT）均为宽松许可，与 GPL-3.0 单向兼容，
作为独立组件共存于 `release/ext/` 不冲突。
