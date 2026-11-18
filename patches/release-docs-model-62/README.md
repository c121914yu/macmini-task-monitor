# Release docs model: gpt-6.2-sol / high

用户指定只调整升级文档任务模型。release_docs.py 独立定义 MODEL=gpt-6.2-sol、EFFORT=high，不再导入 PR 审查的模型常量。DocsServer 沿用原生登录、网络和工具隔离，在线程建立前将继承的 model_reasoning_effort 覆盖为 high，Fast=false；thread/start/resume 和 turn/start 使用同一目标模型，保留服务端有效配置校验。

运行目录 /Users/yjl/.local/share/fastgpt-release-docs。核对 targets.json，持 task.lock 后应用零上下文增量补丁（git apply --unidiff-zero），同时安装 test_docs_model.py。不修改自动 PR 审查的共享模型、用户全局设置、原会话、已完成轮次、重试和通知凭据。新的模型配置在后续文档轮次生效，不为验证触发真实文档生成。

最小验证：python3 -m unittest test_docs_model test_turn_recovery，共10项通过。测试覆盖继承 effort 的独立覆盖、Fast 保持关闭、PR 常量不变、恢复原会话和新轮次请求一致、旧轮次恢复凭据不重复启动。不调用模型或发送通知。
