# PR 自动审查并发三槽位

用户确认将最大并发由 2 提高到 3。唯一执行上限仍为 scheduling.MAX_CONCURRENT，扫描器、执行器说明及运行目录约定同步；FIFO、600 秒静置、15 秒 head 监视、5 次异常重试与现有容量保护不变。每 PR 仍复用原会话，不扩大单个任务权限。

先核对 targets.json 的 SHA256，再在原运行目录用 `git apply --unidiff-zero` 应用补丁。局部验证：`python3 -m unittest test_scheduling.ExecutorTests.test_three_concurrent_slots_fifo_and_no_duplicate_claims -v`。四个模拟任务验证 FIFO、最大实际并发恰为 3、无重复领取。

运行中的执行器导入的是旧常量，不能通过普通文件修改立即刷新。维护切换必须持队列锁并记录独立迁移凭据，只替换调度协调进程，不向独立 runner/AppServer/工具进程组发送终止信号；保持原 attempt_id、process_pid、次数和快照。原监控父进程暂保留以读取旧 runner 的输出，新协调器通过现有 monitor runner 启动并接管活动任务；队列清空后退出，既有 launchd 调度继续兜底。严禁使用 kickstart -k 杀掉正在审查的任务树。

2026-11-11 已核对接管前后的两条任务进程与 attempt_id 不变，并确认新心跳 max_concurrent=3、三个活动任务。迁移记录仅保留在运行目录 migration/，不提交任务状态和模型数据。队列 scheduling.max_concurrent 同步为 3，供看板读取。
