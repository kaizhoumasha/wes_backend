# Sandbox Happy Path

Sandbox 用于 WORKLINE 级调试。它会保留真实编排链路，只把真实设备或外部系统副作用切换到沙箱出口。

## 前置条件

- `APP_ENV=dev` 或 `APP_ENV=test`。
- WorkLine 的 `run_mode=SIMULATION`。
- 插件已注册到 `src/workline_plugin_registry.py`，`plugin_key`、`contract_version` 与 WorkLine 配置一致。
- 设备角色、唯一性和能力满足插件 manifest；`Device.upstream_device_id` 仅作为物理路径辅助信息。
- 事件、结果和命令 payload 不增加 `sandbox` 标志字段。

## 流程

1. 发送 `fixtures/event_happy_path.json` 到 callback event 入口。
2. Runtime 解析业务键，创建或复用 Session，并快照 `run_mode=SIMULATION`。
3. 插件 handler 产生命令，Outbox 使用白皮书命令包络，业务字段在 `params`。
4. Dispatcher 把待派发消息送到 sandbox 出口，不访问真实设备。
5. 调试人员查看沙箱待处理消息，按 `fixtures/result_success.json` 构造手工 result callback。
6. result callback 走正常 callback 入口，业务字段在 `data`，推进同一个 Session。
7. 如需验证业务 NG，使用 `fixtures/result_business_ng.json`，插件应产生 business decision，而不是 failure。
8. 如需验证系统异常，使用 `fixtures/result_system_failure.json`，插件应产生 hardware failure。
9. 如需验证 NG return / Runtime Hold，确认插件的 `material_identity_resolver` 能从 source payload 或现场扫码 payload 得到同一个 `idempotency_key`。

插件级 payload 诊断只能解释 handler/context/`RuntimeIntent`，不能替代本流程。
