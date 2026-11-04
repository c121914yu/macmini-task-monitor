# FastGPT 数据备份维护

- 仅在 Mac mini 运行，每天北京时间 01:00；运行目录为 /Users/yjl/.local/share/fastgpt-backup，备份位于 /Volumes/code/backups/fastgpt。
- 当前生产开发存储容器为 mongo、pg、fastgpt-rustfs；2026-11-02 对象存储切换到 RustFS，旧 fastgpt-minio 已删除。不得继续硬编码旧容器或仅跳过对象存储宣称完整备份。
- 使用本机已有 MinIO 镜像中的 mc 作为临时客户端，通过 RustFS 容器网络读取。凭据从 RustFS 容器环境读取，只通过子进程环境传递，不打印、写入源代码或命令行。
- 对象、桶配置、IAM、服务配置、Content-Type/自定义元数据及标签均须保存；版本化桶或复制前后对象变化时失败，不静默降低备份范围。保留 ETag/大小、压缩流完整性、SHA256 及原子发布。
- 只有全部导出和校验完成才发布 complete 清单并清理超过 7 个自然日的自有备份；失败不删旧备份。不能伪造失败日期的历史快照，修复后只补跑当前真实时间的备份。
- 独立客户端使用任务专属 UUID 名称和 owner 标签；持任务锁后才回收孤立客户端，不停止数据库、RustFS 或其他容器。
- 排查先看自动化看板，再看 logs/YYYY-MM-DD.log；修复需局部模拟测试和一轮真实备份验证，保留历史失败记录与修复凭据，不修改现有业务数据。
