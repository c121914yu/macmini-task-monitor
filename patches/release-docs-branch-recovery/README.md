# Release docs: branch and merged-PR recovery

根因：远端文档分支被改写后与固定 worktree 分叉，原 merge --ff-only 持续失败；旧任务耗尽 5 次后又在检查 PR 生命周期之前返回，导致 PR 已合入仍永久显示错误。

变更：本地干净、尚无外部基线或模型凭据时，远端文档分支允许普通 merge，保留两边历史。先持久化 remote_sync 双方 SHA 和固定 target；允许本版本文档冲突交给同一模型解决，Pro 和时间索引仍沿用宿主处理。恢复记录过的 MERGE_HEAD 时不重新 fetch 远端。若两侧尚未包含固定 main 目标或有外部真实冲突，保留现场并明确报错，不假装同步成功。

达到重试上限前也检查关联 PR。关联分支匹配，合入提交及固定 target 均包含于当前已获取 main、工作区干净且无未核对模型轮次时，旧任务标记 superseded，保留 attempts、错误和所有凭据；清除 active_run，但不推进 checked_sha/last_success，不伪造验证成功。下一次扫描按既有逻辑继续新 main，并在原 PR 已合入时创建下一代分支和 Draft PR。不会强推、rebase、重置或删除旧分支。

部署：运行目录 /Users/yjl/.local/share/fastgpt-release-docs。持 task.lock，先核对 targets.json 的 before_sha256，使用 git apply --unidiff-zero 应用两个增量补丁，安装 test_branch_recovery.py 后检查 after_sha256。不覆盖配置、凭据、数据库或通知账本。与其他 monitor 源码改动相互独立。

验证：Mac mini 的临时 Git 仓库、模拟业务子进程和临时 SQLite 中执行 python3 -m unittest test_branch_recovery test_external_content test_release_docs test_publication_recovery test_app_version test_network_retry，54 项通过。涵盖干净分叉、文档冲突重试固定远端 SHA、未提交/已生成内容不被覆盖、已合入旧任务的幂等归档、旧次数与游标保留、check-only 不写入、未确认模型轮次保留。不为验证调用真实模型、更新 PR 或推送 webhook。

当前旧任务 784b908bf68882b6eb7a47d6 在 PR #8219 合入后可调用 reconcile_merged_pr 恢复；此操作只校正状态，不给旧任务增加尝试、不标记完成、不调用模型，也不发送通知。维护操作可通过既有 runner 记录实际结果，原每小时调度不变。
