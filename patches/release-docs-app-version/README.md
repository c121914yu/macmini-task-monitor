# 升级文档应用版本元数据例外

用户确认 `projects/app/package.json` 的 `version` 变化可以随升级文档保留。本轮误拦截仅为 `4.18.0.1` → `4.18.0.2`，该文件相对本轮固定 target 的差异来自已提交内容，工作树本身没有额外产品修改。

在文件白名单中加入此路径，同时按固定 target 分别核对工作区与 Git 索引的 JSON：只允许 version 字段差异，非空字符串必需；依赖、scripts、其他字段与类型变化、无效 JSON、删除或符号链接仍报错。准备阶段按 HEAD 检查未完成改动，提交前再次核对；文档 Prettier 范围不扩展到产品元数据。宿主提示和运行目录约定同步该例外。

核对 targets.json 的 SHA256 后，在运行目录按 `git apply --unidiff-zero` 应用补丁，并复制 test_app_version.py。只运行局部验证：`python3 -m unittest test_app_version test_release_docs test_publication_recovery test_network_retry -q`。测试使用临时 Git 与模拟模型、PR 和通知。

不重置重试次数、固定 target 或原有生成凭据，不主动重发通知；当前轮在下一整点沿用已完成模型继续校验及发布。
