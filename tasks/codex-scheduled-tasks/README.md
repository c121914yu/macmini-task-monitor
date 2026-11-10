# Codex 定时任务迁移

运行于 Mac mini 的 /Users/yjl/.local/share/codex-scheduled-tasks，由四个独立 launchd 日历调度。看板只读，调度与执行记录统一通过既有 monitor runner。

| 任务 | Asia/Shanghai | 模型 | 原定时器 |
| --- | --- | --- | --- |
| 个人邮件每日总结 | 每天 07:00 | gpt-6.1-sol / medium | automation |
| FastGPT Signoz 错误日报 | 每天 07:00 | gpt-6.1-sol / xhigh | fastgpt-signoz |
| Plugin 新增模型 | 每天 01:30 | gpt-6.1-sol / medium | plugin |
| FastGPT 每周 PR 汇总 | 周五 10:00 | gpt-6.1-sol / xhigh | fastgpt-pr |

所有模型使用原生登录和配置，Fast 关闭。不创建新项目。普通任务按周期创建有原项目归属的会话；周报保持原 heartbeat 会话。所有任务继续读写原 memory.md，周报继续复用原 reports/ 的文档状态。

## 数据与副作用

- IMAP 在宿主脚本用 EXAMINE、BODY.PEEK、INTERNALDATE 精确筛选；三个邮箱分别失败重试 10 秒、30 秒。Gmail 保留已连接工具优先，不新增未授权邮箱或文件夹。报告按邮箱分组、验证码内容不输出。
- 完整发送所有邮件汇总段后，才标记纳入摘要的精确 Gmail message_id 和 IMAP UID；脚本核验 UIDVALIDITY。只移除 UNREAD / 添加 Seen，不归档、移动或删除。
- SigNoz 通过固定只读 SELECT 投影按 5 分钟小窗串行采集当日和前一日三类日志，独立 count 对照提取、校验唯一日志 ID 和边界。仅结果截断、明确的服务端资源限制或数量不匹配才拆窗至一分钟；网络错误不拆窗。兼容 SigNoz 将内存限制包装成 HTTP 500、仅提供 ClickHouse code: 241 和普通文字的响应；最小窗口仍失败明确记录资源或结果限制。EOF、超时、连接中断及 HTTP 429/500/502/503/504 在同一查询内等待 10、30、60 秒，最多重试 3 次；证书校验、认证及其他永久错误直接报告。保留脱敏错误分类、底层类型、窗口和尝试次数，不保存 API key、SQL 或响应体。失败不调用模型、不冒充零。trace 关联同样按 5 分钟串行查询，每批最多 40 个 trace ID，最后不足 5 分钟的窗口仍完整覆盖；对资源限制继续拆窗，无法恢复时记录失败窗口与分类并明确未知。部分完成的 result.json 必须写 log_analysis_complete:true，前提是主日志分析和数量闭合已核验；宿主会验证完整采集及已发送凭据。缺字段不会盲目重跑模型或重发通知，先核对现有产物。模型启动前由 signoz_digest.py 校验原始数量、唯一 ID 和时间边界，再生成 signoz-digest.json：按受控错误签名合并，保留原始次数、去重估算请求数、模型/接口/小时分布、前日对比和每组最多 2 条脱敏代表信息。每类每天最多展示 20 组，模型输入最大 128 KiB，超出部分合并成准确的长尾计数；不同状态码/错误码保持区分，未知错误保留独立指纹且不转发原文。完整日志和全量分组仅留宿主核对，模型仅读取摘要，不再遍历原始日志。聚合失败不调用模型或发送通知，旧周期已发送结果仍按原凭据恢复，避免重复分析。
- PR 周报采集覆盖三个仓库全部作者和目标分支，分页超限拆窗，再按真实 mergedAt 筛选。保留周五 10–12 点原空档，原文档 ID 与通知进度继续复用。
- Plugin 仅检查 skill `references/provider_allowlist.json` 的静态预设厂商；名单外目录不扫描、不计入待核验，也不因其标记部分完成。白名单内证据缺失仍如实标记。保留固定分支 codex/plugin-model-sync、同步 upstream/main、复用未合并 PR、无变化不建空 PR 的规则。原任务无 webhook，不新增通知。
- 已准备通知只通过 notify.py 发送；模型拿不到 webhook。所有分段先校验，再调用既有 webhook-delivery（明确限流等 60 秒，最多 3 次重试）。同一内容和目标冻结；成功、结果未知及预算耗尽不得自动重置。网络结果未知保留现场，禁止补发。
- 周期锁、持久化创建意图、turn ID 和 clientUserMessageId 防止重复模型。旧 Codex 定时器恢复 ACTIVE 时，脚本拒绝再运行，避免双调度。中断后先核对既有会话和凭据，不盲目发新轮次。

## 私有文件

private.json（仅当前用户 0600）包含 IMAP 密码、SigNoz API key 和通知目标；不提交 Git。prompts/ 是脱敏后的原提示模板；tasks.json 保存实际启用状态。runs/、logs/、verification/、migration/ 均仅存本机，禁止入 Git。历史聊天和原 memory 不复制到仓库。

迁移按先安装 RunAtLoad=false 的日历、无模型预检和测试、暂停原定时器、启用新脚本的顺序。activate 在本机原子更新 native TOML，并保留私有回滚凭据；不会恢复笔记本上的旧定时器。四项原任务的业务权限与通知目标延续原授权。

## 验证与运维

安装前把本目录脚本复制到运行目录并配置 tasks.json/private.json、脱敏 prompts/。使用 Python 3.14：

- python3 -m unittest -v test_signoz_digest test_signoz_network test_migration：临时目录和模拟边界，不执行真实业务。
- python3 run.py --check-only：只校验本地配置。
- python3 run.py --probe：只检查原生登录、模型目录、周报原会话；不创建会话或启动 turn。
- python3 install_tasks.py install：加载系统日历，不立即执行。
- python3 install_tasks.py activate：暂停 Mac mini 原定时器，再启用脚本。
- python3 install_tasks.py verify：核验原任务 PAUSED、新任务启用及真实 launchd 定义。

正式执行 run.py --task <id>；仅用于授权执行/恢复，不为观察看板运行。检查 /api/overview 的四项 loaded、schedule、next_at 及执行日志。失败看 runs/<id>/<period>/state.json，保留现有会话、采集和通知凭据；不要删除或重置状态来“重试”。

本地状态明确区分数据采集、会话创建、模型执行、模型完成、通知和最终完成。对模型执行中断的轮次必须先核对 App Server 原会话结果；未知发送状态不自动恢复。到下一周期才发起新一轮业务分析。
