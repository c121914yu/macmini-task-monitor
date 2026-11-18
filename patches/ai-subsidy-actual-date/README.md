# AI 补贴实付核验 v7

待办表单按同笔凭证的付款日、实际扣款币种和总额核对。人民币直接取实扣，美元在计算补贴时按凭证付款月换算。季付及年付的覆盖月分摊在后台进行。现有美元等值上限继续用于额度计算。

已有账本记录直接读取。历史已通过但缺少额度记录时，可以用旧表单的月均人民币金额口径补录。新待办使用 v7。

Mac mini 的运行目录为 `/Users/yjl/.local/share/ai-subsidy-review`，维护源码在 `tasks/ai-subsidy-review`。核对 targets.json 中的哈希，在运行锁内将各补丁按 `git apply --unidiff-zero` 应用到对应目录。

在 Mac mini 临时目录运行 test_rules、test_runtime、test_form_changes、test_notify，共 55 项测试通过。测试使用模拟凭证和审批接口。
