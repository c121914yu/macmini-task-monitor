# 人工冲突工作副本回收

人工任务显式登记后，目录可以使用 PR号-conflict-resolution-key前12位。回收仍需核对 job.json 的 purpose、完整 key、PR、工作目录及生命周期租约；锁、进程、cwd、Git 登记和主仓/Pro 恢复点保护保持。未知目录或不一致证据继续保护，不放宽全局容量规则。

应用前核对 targets.json 的原始 SHA256，按对应运行文件应用补丁，勿覆盖其他改动。将新增测试放入运行目录，复用既有 test_workspace_lifecycle.py，执行：

    /opt/homebrew/bin/python3 -m unittest test_manual_workspace_gc test_workspace_lifecycle test_worktree_storage -q

测试使用临时 Git 仓库，不请求 GitHub、模型或飞书。自动扫描可继续安全回收，无需清零队列、重建会话或重启正在执行的任务。
