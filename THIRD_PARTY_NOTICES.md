# Third-party notices

本项目把以下第三方组件**原样内置**在 `release/ext/` 中，仅为了让运行期不需要
`pip install` 任何东西。各组件的版权与许可归原作者所有。

## uiautomation 2.0.29 — `release/ext/uiautomation/`

- 来源：<https://github.com/yinkaisheng/Python-UIAutomation-for-Windows>
- 许可：Apache License 2.0（见其源文件头部声明）
- 附带的 `bin/UIAutomationClient_VC140_X64.dll` / `X86.dll` 为 Microsoft 提供的
  UIAutomation COM 服务器实现，随Windows SDK 分发。

## comtypes 1.4.17 — `release/ext/comtypes/`

- 来源：<https://github.com/enthought/comtypes>
- 许可：见上游仓库中的许可声明（MIT 系）
- `gen/` 下由 `gencache` 生成的模块、以及 `gen/UIAutomationClient.py` 同样适用上游许可。

## 仅构建期使用（不随本仓库分发）

- PyInstaller 6.22.3 — GPL-2.0-with-classpath-exception，版权 © Copyright
  2005-2020 Francesco Piccoli / Johann C. Rocholl。声明于 `requirements.txt`，
  仅在重新打包 exe 时需要（按其许可，打包产物需附带其许可与源码获取方式）。

本项目自身的代码以 MIT 许可发布，见 `LICENSE`。