# FastGPT 数据备份维护

- Mac mini 保留 com.yjl.fastgpt-daily-backup，每天北京时间 01:00；入口 /Users/yjl/.local/share/fastgpt-backup/backup.py 通过 SSH 调用 Ubuntu Dev，不在 Mac mini 创建备份产物。
- Ubuntu Dev 运行目录 /home/archer/.local/share/fastgpt-backup，产物 /home/archer/backups/fastgpt。保留最近 7 个自然日；仅成功导出并校验后清理过期、自有且 complete 的日期目录，失败不删旧备份。
- 数据源仍为 Mac mini 的 mongo、pg、fastgpt-rustfs。数据库通过受限 SSH 流式导出，Ubuntu 临时 mc 容器通过专用 SSH 转发读取 RustFS。不得停止数据库、RustFS 或其他业务容器。
- 凭据只从 RustFS 容器环境读取并经子进程环境传递；禁止输出、写入源码或命令行。SSH 私钥、transport.json、known_hosts、清单缓存、日志及产物不入 Git。
- source_gateway.py 是专用密钥的 forced command，仅允许既定备份读取与 127.0.0.1:9000 转发；禁止放宽为通用远程 shell 或任意 Docker 命令。
- 对象、桶配置、IAM、服务配置、Content-Type/自定义元数据及标签均须保存；版本化桶或复制前后对象变化时失败，不降低备份范围。保留 ETag/大小、压缩流完整性、SHA256、原子发布和任务锁。
- 不伪造历史备份，不覆盖已完成的同日快照。修改后的真实验证使用 Ubuntu 上单独的 FASTGPT_BACKUP_ROOT；验证通过后仅删除该次自有验证目录。
- Mac mini 仅保存脚本、日志和 remote-summary.json 元数据；看板读取缓存，不在每轮采集时 SSH。迁移目录须双端校验文件与清单后才删除来源。
- 排查先看自动化看板，再看 Ubuntu logs/YYYY-MM-DD.log 与 transport.stderr.log、Mac mini launchd.log。修复需局部模拟测试和一次真实备份验证，保留历史错误和修复凭据。
