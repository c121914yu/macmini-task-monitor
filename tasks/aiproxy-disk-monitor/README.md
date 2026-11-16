# aiproxy-io 磁盘检查

Mac mini 每天北京时间 10:00 经 SSH 读取远端根分区 `df -Pk /`，可用空间严格小于 10,000,000,000 bytes（10GB）时发送飞书卡片。卡片包括主机、挂载点、剩余/总容量、阈值和检查时间。容量正常时静默；采集失败记为错误，不发送虚假容量告警。无需模型和桌面解锁。

运行目录 `/Users/yjl/.local/share/aiproxy-disk-monitor`；已使用现有 monitor/runner 接入执行记录和任务日历。系统 launchd 标签 `com.yjl.aiproxy-disk-monitor`。连接使用 Mini 上现有服务端密钥及从已信任连接核对的独立 known_hosts，保持 StrictHostKeyChecking=yes；绑定 Mini 实际物理网卡 en1 绕过现有 TUN，不依赖用户电脑中转，不复制私钥。

私有 config.json 字段：host、user、identity_file、interface、threshold_bytes、webhook。配置、known_hosts、检查结果、任务锁及 notifications 均仅保存在运行目录，不入仓库。install.py 只追加本任务注册及新 launchd plist，不覆盖其他任务配置、不修改远端机器、不自动运行业务或发送通知。部署脚本后，准备上述私有文件，再执行 install.py。

通知调用现有 `/Users/yjl/.local/share/webhook-delivery/webhook_delivery.py`。同一自然日告警最多成功一次，明确限流等待 60 秒、最多重试 3 次；中断后保留同一消息及累计预算。未知结果、发送中、明确拒绝和预算耗尽不自动重发。第二天容量仍低时使用新日键告警。状态在发送意图及响应时原子落盘，任务锁避免重叠执行。

验证：在 Mini 临时目录运行 `python3 -m unittest test_check`，使用模拟通知和临时文件；`check.py --check-only` 执行真实只读检查，绝不推送。变更网卡或目标机器时应先确认新的连接和主机指纹，再修改运行配置。
