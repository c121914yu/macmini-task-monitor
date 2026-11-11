# Release docs: preserve existing external content

升级文档任务不再将整条文档分支相对 main 的外部差异误判为模型越界。固定 target 合并后、模型处理前记录工作区和索引指纹到运行 external_baseline，接受已有提交、暂存、未暂存和未跟踪的外部内容；本轮新增外部修改仍拦截。重试仅核对同一基线，不重新采集。package 的 version 仍可更新，其余字段保留处理前实际内容。

文档分支上原有 Pro gitlink 与 incoming main 冲突时保留 ours，记录双方 SHA，不 checkout 或合并 Pro 代码。文档证据仍来自固定 base/target 的真实 gitlink。其他外部内容冲突报告真实合并冲突。

发布只补暂存文档允许路径，保留既有外部暂存内容，不自动加入外部未暂存/未跟踪文件。保留 main 已暂存删除和重命名。历史已有模型产物但无基线的任务保持严格校验，不自动事后豁免。

运行文件位于 `/Users/yjl/.local/share/fastgpt-release-docs`。核对 targets.json 的 before_sha256，持 task.lock 后在该目录用 `git apply --unidiff-zero <patch>` 应用四个增量补丁，安装新测试文件；after_sha256 用于部署验证。不要覆盖配置、凭据、运行状态或通知凭据。

验证：Mac mini 临时目录使用候选代码执行 `python3 -m unittest test_external_content test_release_docs test_publication_recovery test_app_version test_network_retry`，43 项通过。新增测试覆盖原有外部内容保留、二次改动/隐藏暂存拦截、基线不可重采、版本字段边界、仅暂存文档以及 Pro 指针冲突。临时 Git/SQLite fixture，无模型、PR 或 webhook 调用。

当前 v4.18.0 挂起任务可在任务锁下只运行 prepare_worktree/scoped_changes 预检，保留原 base/target 和历史 attempts=5，记录 manual_recovery.attempt_limit=6，下一整点继续原任务；不重置失败预算。部署恢复不直接调用业务模型或推送通知。
