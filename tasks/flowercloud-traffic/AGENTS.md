# FlowerCloud 流量日报

- 仅在 Mac mini 运行，独立无头 Chrome，不操作桌面 Chrome 或解锁系统，不调用模型。
- 仅查询指定 FlowerCloud 产品流量，并推送到用户指定飞书 webhook。不得修改套餐、订阅开关或付款。
- config.json、browser-profile、checks/ 中的认证信息和登录态仅限当前用户读取，不入 Git，不打印凭据、Cookie、Webhook 或整页 HTML。
- 每日北京时间 09:00，真实查询与成功发送均留存本地凭据；同日发送不重复，发送结果不确定时不盲目重发。
- 测试使用临时目录、模拟认证与推送边界；真实试运行单独留档，不伪造流量。
