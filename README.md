# Mac mini 任务监控

本机只读网页。Python 标准库提供 HTTP、launchd 采集和 SQLite 记录；静态页面不依赖 CDN 或构建工具。

此仓库仅保存看板源码。运行数据库、日志、缓存和本机凭据不纳入版本控制；任务路径与 launchd 注册信息在 config.py 中按部署机器调整。

## 位置与入口

- 源码：`/Users/yjl/.local/share/macmini-task-monitor/`
- 数据库：`data/monitor.sqlite3`（WAL；仅当前用户可读）
- 服务：`com.yjl.macmini-task-monitor`，默认端口 `8765`
- 服务日志：`data/server.log`、`data/server.stderr.log`
- 原 launchd 定义：`data/launchd-backups/`，保留原始文件，安装凭据在 SQLite metadata 中。
- 当前访问：`http://127.0.0.1:8765`；Tailscale 内网 `http://100.96.142.32:8765`。登录后由 LaunchAgent 自动启动。

安装或变更监听地址：

```sh
cd /Users/yjl/.local/share/macmini-task-monitor
/opt/homebrew/bin/python3 install.py --bind 127.0.0.1
```

可重复添加 `--bind`，例如监听本机和当前 Tailscale 地址。只绑定显式指定的地址；新增域名需 `--allowed-host`。Tailscale 地址启动时不可用会自动重试绑定。

## 记录约定

- 四个自建 launchd 任务经过 `runner.py` 后执行原命令；保留工作目录、环境和标准输出，透传退出码与停止信号。SQLite 不可用时原任务继续执行。
- 新记录有真实开始/结束/退出码，保存脱敏后的末尾 24 KB 日志。运行中每 5 秒落库。
- 执行记录及总览最近活动默认隐藏“跳过”；结果筛选可选择“全部结果”或“已跳过”。统计仍保留全部记录。
- PR 扫描成功且结构化输出 `queued: []` 时记为跳过；有待执行任务记为成功。失败、缺少队列信息的结果不推断为跳过。既有明确空队列记录会同步修正，首次修正前备份 SQLite 到 `data/backups/`。
- 旧备份清单、日报凭据、PR 队列、秘塔 memory 按稳定来源键去重导入。缺失时间、退出码或耗时保留为空。
- 监控接入之前的 PR 队列只保留业务已有状态，不能还原全部历史调度；网页明确标为历史凭据。
- 秘塔仍由原 Codex 定时任务触发。监控只导入已有记录，不请求余额、不发送消息。
- 每 15 秒采集状态。进程不存在或 PID 已复用的运行记录标为中断，实际结束时间保持未知。
- 业务任务自动发现限当前用户自建 launchd 定时任务，另展示 Google 更新和几个已知常驻服务。未获取 root crontab。
- 日历任务显示计划时间；间隔任务根据最近启动时间估计，不能保证休眠、唤醒后的实际时刻。
- SQLite 执行记录按创建时间滚动保留最近 7 天，每轮采集清理过期记录；正在运行的记录完成后再清理。历史导入不会重新写入过期记录，删除后的空页由新记录复用。原业务日志仍由原脚本负责维护。

## FlowerCloud 流量日报

每天北京时间 09:00，由 com.yjl.flowercloud-traffic 运行独立无头 Chrome，读取指定产品的已用流量与总流量并推送飞书。锁屏时可运行，不依赖桌面 Chrome，不调用模型。运行目录为 /Users/yjl/.local/share/flowercloud-traffic；config.json、browser-profile/、checks/ 均为本机私有数据，不入 Git。同日发送成功或响应不确定时不重复发送；异常只记录本地和看板，不发送虚假流量。

## Webhook 限流重试

所有 Mac mini 自动任务共用 /Users/yjl/.local/share/webhook-delivery：明确收到 HTTP 429 或飞书 11232/11233/11247 后，固定等待 60 秒，最多重试 3 次（总计最多 4 次请求）。只重发原消息；网络结果未知不自动重发，耗尽后不被自动补偿重置。覆盖 PR 通知/日报、升级文档、周提醒、秘塔余额和 FlowerCloud；调度时间保持原设置。

共享实现位于 lib/webhook-delivery，部署到上述运行目录；Python 任务调用 send，Node 任务调用 postWebhook。patches/webhook-retry 保存原有运行任务的零上下文补丁与前后 SHA256，使用前核对 targets.json，按目标文件应用（git apply --unidiff-zero），避免覆盖其他业务改动。FlowerCloud 的凭据及 webhook-attempts/ 等运行记录不纳入版本控制。

## 维护

局部验证（使用临时 SQLite，不执行真实业务或发送通知）：

```sh
/opt/homebrew/bin/python3 -m unittest discover -s tests -v
node --check web/app.js
```

交互集成检查：`node tests/frontend.cjs`。使用现有 FastGPT 开发依赖中的 jsdom（可通过 `JSDOM_PATH` 指定），只在 DOM 模拟器中访问本机只读接口，不需要产品运行依赖。它不替代真实浏览器的布局与截图检查。

健康检查：`GET /api/health`。其他接口：`GET /api/overview`、`GET /api/runs`（分页/筛选）、`GET /api/runs/{id}`、`GET /api/tasks/{id}/logs`。所有接口只读，无执行与配置入口。

恢复业务原调度入口（只处理仍由本监控包装的任务，不清空账本）：

```sh
/opt/homebrew/bin/python3 install.py --restore-tasks
```

恢复或安装遇到正在运行的业务时，不要强制停止。安装器会等待；恢复操作需等任务结束后重试。


## Codex 四项任务迁移

个人邮件日报（07:00）、SigNoz 错误日报（07:00）、Plugin 模型更新（01:30）、每周 PR 汇总（周五 10:00）已使用系统 launchd 调度并接入本看板。运行目录 /Users/yjl/.local/share/codex-scheduled-tasks；原 Codex 定时器保持 PAUSED，原记忆、周报会话与文档凭据保留。邮件和 Plugin 使用 gpt-6.1-sol / medium，其他两项 xhigh，均关闭 Fast。所有通知使用统一限流重试；凭据和运行数据不入 Git。实现、验证及恢复说明见 tasks/codex-scheduled-tasks/README.md。

## 任务日历

`/#calendar` 提供北京时间周日历、任务筛选、计划 / 实际执行切换与当天明细。计划按当前 launchd 配置投影，不用于回填历史；实际执行沿用 7 天账本，默认隐藏跳过，未知开始时间明确显示为记录时间。高频间隔和每小时任务合并为持续调度区，不推算固定开始时刻；点击实际记录查看既有日志。`GET /api/calendar?start=YYYY-MM-DD` 为只读七日视图，支持前后一年内起始日期，每次最多读取 10,000 条记录，超出时明确提示。

日历局部验证：`python3 -m unittest discover -s tests -p test_calendar.py -v`、`node tests/calendar.cjs` 及 `node tests/frontend.cjs`。
