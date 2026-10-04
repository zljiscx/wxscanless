# -*- coding: utf-8 -*-
"""wxprof —— 微信多账号登录态管理工具库（微信 4.x，Windows 7/10/11）。

模块一览（v1.0.0 架构）：
    paths     微信安装/运行/数据目录探测
    process   微信进程识别与启动（多进程架构下只认主进程）
    slot      实例槽位（net/net_1/…）：占用判据、目标槽位、host 读写、
              monitordata 扫描
    mmkv      global_config 解密与解析（纯 Python AES-128-CFB，无第三方依赖）
    avatar    账号头像：下载 + GDI+ 转 PNG（零第三方依赖）
    vault     账号档案库（一账号一份：config 对 + host + 头像 + 元信息）
    collect   采集链路：读 config → uin → 定位槽位 → 采该槽位 host
    watcher   实时监控：发现登录成功即采集/更新
    login     登录：热替换 config 对 + 放置 host 到目标槽位 + 后台点击进主界面
    ui        登录窗口 UI 自动化（后台点击/后台判定，不动真实鼠标）

关键实测结论见 _docs/项目实现与实测结论.md（项目唯一文档）。
"""
__version__ = "1.5.1"
