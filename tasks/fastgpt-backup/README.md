# FastGPT 数据备份

Mac mini 的既有 LaunchAgent 每天北京时间 **01:00** 触发。备份引擎和全部产物在 **Ubuntu Dev**，保留最近 **7 个自然日**。Mac mini 只留调度脚本、日志和看板元数据。

| 主机 | 用途 | 路径 |
| --- | --- | --- |
| Mac mini | 调度、受限数据读取网关 | /Users/yjl/.local/share/fastgpt-backup |
| Ubuntu Dev（archer-unbuntu26-dev） | 备份引擎 | /home/archer/.local/share/fastgpt-backup |
| Ubuntu Dev | 完整备份及校验清单 | /home/archer/backups/fastgpt/YYYY-MM-DD |

## 数据流与校验

MongoDB 全库归档包含 oplog，PostgreSQL 导出全部数据库和角色；两者从 Mac mini 容器经 SSH 直接写入 Ubuntu。RustFS 对象、桶配置、IAM、服务配置、对象元数据和标签由 Ubuntu 临时 mc 容器读取，连接仅在专用 SSH 本地转发存在期间开放。数据不会先落盘到 Mac mini。

原备份引擎保留压缩流验证、PG 完成标记、对象数量/大小与适用的 ETag 校验、元数据 ZIP 校验、全部文件 SHA256。只有完整校验后才原子发布 complete 清单并回收超过 7 个自然日的自有备份。版本化桶或复制前后内容变化时失败，失败不删除旧备份；同日已完成则跳过重复导出。

Mac mini 的 backup.lock 和 Ubuntu 的 transport.lock、backup.lock 防止并发。SSH 隧道随进程关闭；mc 客户端使用专有 UUID 和 owner 标签，绝不停止业务容器。

## 部署约定

1. 将根目录 backup.py、source_gateway.py 放到 Mac mini 运行目录，将 ubuntu/ 内脚本放到 Ubuntu 运行目录。文件及备份目录仅属主可读写；docker_bridge.py 须保留属主可执行权限（chmod 700）。
2. Mac mini 到 archer-unbuntu26-dev 使用已有免密 SSH；Ubuntu 到 Mac mini 使用专用私钥，并以实际主机公钥写入 known_hosts，启用严格主机校验。
3. Ubuntu 的 transport.json 按 transport.example.json 配置，不提交实际配置和 SSH 密钥。Mac mini authorized_keys 为专用公钥设置来源限制、restrict、port-forwarding、permitopen="127.0.0.1:9000" 及 source_gateway.py forced command。
4. Ubuntu archer 需要已有的 sudo -n Docker 权限；mc 镜像预先拉取。不会在 Mac mini 拉取 mc 镜像或创建备份容器。
5. 保留 Mac mini 的 com.yjl.fastgpt-daily-backup 日历配置与 monitor runner 包装。backup.py 调用 Ubuntu 引擎后原子更新 remote-summary.json，只有元数据返回 Mac mini。
6. collector.py 使用原日期清单的稳定来源键，迁移不会重复导入历史记录。远端失败透传非零退出码，不以缓存中的旧成功记录冒充本轮成功。

## 验证与维护

Ubuntu：python3 -m unittest discover -s /home/archer/.local/share/fastgpt-backup -v。

Mac mini：根目录 test_trigger.py 使用临时目录和模拟 SSH；看板局部测试覆盖远端清单及历史幂等。

真实验证：在 Ubuntu 设置 FASTGPT_BACKUP_ROOT 为独立验证目录，执行 run_remote.py，再以 verify_remote.py 对完成目录重新校验。禁止为了验证覆盖正式同日备份；清理时仅删除本次已验证的目录。

迁移既有备份时先校验来源、复制、重新校验目标全部 SHA256 与清单一致性，确认后才删除来源。原引擎的七个自然日保留逻辑不变；不补造缺失日期。
