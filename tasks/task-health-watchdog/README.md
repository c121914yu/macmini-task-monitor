# Mac mini 定时任务巡检

每小时第 20 分钟运行，错开整点集中执行；LaunchAgent 为 com.yjl.task-health-watchdog，看板名称为“定时任务巡检与修复”。本机纯 Python 检查健康情况，只有持久故障才启动 Codex。

- 读取统一看板的 SQLite，只选择已启用、自建且接入 runner 的任务。无需浏览器、额外 API key 或新的 Codex 原生定时器。
- 执行失败：连续至少 3 次失败/中断，且持续超过 1 小时。跳过中性，成功打断失败链；正在正常执行的重试不重复派单。
- 业务暂停（含 PR 工作树回收阻塞）、执行心跳中断和逾期：至少两次每小时观察且持续 1 小时。新日历任务安装前的计划时间不会误判。
- 一次检查最多启动一项修复，同一任务复用一个 FastGPT 项目可见会话，gpt-6.1-sol/xhigh、Fast 关闭。每个故障最多 3 轮，相隔至少 1 小时。
- 创建/发送结果未知、仍在执行或需要人工处理时不重复派单。模型输出不视为修复证明，重新读取真实结果后才关闭故障。
- 自身及停用任务不参与修复，避免递归。健康检查不启动模型；修复不额外发通知。任务本身的原有通知和授权保持。

运行状态保存在 state.json，故障证据、每轮提示词、结果、Token 和响应保存在 incidents/，仅本机当前用户可读。policy.json 可配置阈值，不存凭据。不要将运行状态及日志加入 Git。

安装只注册此任务、插入一个看板条目并重启只读看板，不重启其他业务：

    /opt/homebrew/bin/python3 install.py

只读检查（不调用模型、不保存派单状态）：

    /opt/homebrew/bin/python3 watchdog.py --check-only

局部测试使用临时 SQLite 和模拟 App Server，不请求模型或发送通知：

    /opt/homebrew/bin/python3 -m unittest test_watchdog -q

修复使用已验证的本机 Codex App Server 登录初始化，不修改全局账户或 provider 配置。未知创建/发送结果需要核对原会话和进程，不能直接删除去重字段重试。
