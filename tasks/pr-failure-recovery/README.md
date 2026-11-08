# PR 失败诊断恢复

运行于 Mac mini PR 流程目录。Git/子模块准备失败时立即分析；首次执行加五次重试耗尽后，再通过原有两个 FIFO 槽位做一次诊断。原计数不清零，同一 head 至多自动诊断一次。原会话、Token、临时状态评论、快照取消、资源租约和通知规则继续沿用。

- PR 代码导致：固定 GitHub head、分页 diff、文件、评论、Pro gitlink 远程审查，提交 Codex COMMENT Review，并按现有 skill 清理旧报告。关键代码/Pro/比较基准不完整只能 blocked；不把检出失败当作通过。
- 本机原因：只尝试修复本轮独占受管副本和临时环境，不改变 PR 源码、主工作区、全局认证、队列账本或其他任务。验证成功后由宿主重新执行严格检出和普通审查；依赖通道仍进行原有 install/lock 更新流程。
- 原因不明、修复不确定或诊断中断：保留证据，不循环调用模型。策略拦截不自动进入诊断，不通过远程或新会话绕过。
- 发布前仍校验 OPEN、非 draft、相同 head/base；新提交由原 15 秒监视终止进程树并重新静置十分钟。复审读取旧报告及新评论。无待处理任务时不调用模型。
- recovery.json 和 recovery-context.json、recovery-decision.json 位于原快照 runs 目录；传输中断先核对凭据，不删除它们来重试。常规和诊断 Token 均由原账本采集。
- 补丁采用零上下文，应用前核对 targets.json 的原文件 SHA256，再在运行目录使用 git apply --unidiff-zero；复制新模块。避免覆盖其他维护改动。

验证在运行目录执行 python3 -m unittest -v test_pr_recovery test_scheduling test_queue_v2 test_review_failures test_pr_eligibility test_app_server_config。使用临时状态和模拟模型，不真实执行审查或发布评论。
