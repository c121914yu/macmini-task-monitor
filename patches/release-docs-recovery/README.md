# 升级文档发布恢复

修复已暂存删除路径的重复暂存、维护分支 Release 比较及发布失败后的生成凭据保留。补丁仅适用于 targets.json 中的原始 SHA256，应用前核对，勿覆盖其他现场改动。

在运行目录应用 release_docs.py.patch 和 AGENTS.md.patch；新增测试文件放入同目录，复用既有 test_release_docs.py。局部验证：

    /opt/homebrew/bin/python3 -m unittest test_release_docs test_publication_recovery -q

测试使用临时 Git 仓库及模拟远端，不调用模型或发送通知。任务调度、配置和凭据不变，人工恢复凭据仅保存在运行账本。
