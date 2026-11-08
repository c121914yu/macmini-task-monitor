# 升级文档远端读取重试

修复预扫描和发布核对阶段单次 GitHub 读取遇到 EOF 就立即失败的问题。REST 默认 GET、git fetch/ls-remote 对瞬时网络错误和 HTTP 429/500/502/503/504 最多重试 3 次，依次等待 10、30、60 秒。认证、权限、404、校验和未知错误直接失败。超时重试也受同一预算限制。

仅重放当前读取请求；不重启扫描、模型或整轮生成。POST/PATCH、push、merge、commit 保留原有单次执行和发布结果核对语义。耗尽后报错，扫描游标不推进，既有固定任务和每小时最多 5 次的生成恢复规则不变。

先核对 targets.json 的原始 SHA256，再在运行目录用 git apply --unidiff-zero 应用两个补丁（在 release-docs-recovery 补丁之后应用），并复制 test_network_retry.py。局部验证：python3 -m unittest test_network_retry test_release_docs test_publication_recovery -q。模拟请求和等待，无真实模型与通知。真实验证仅运行 release_docs.py --check-only。

事件 docs_network_retry 记录分类、重试次数与等待时间，不记录完整命令或响应体。生产调度、版本、会话、worktree 和凭据不变。
