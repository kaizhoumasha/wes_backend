# WorkLine Generated Plugin 当前架构

> 本页只描述当前生产合同。v3.2 旧 orchestrator/unbound runtime 设计已原文归档到
> [`docs/archive/legacy-workline-plugins/workline_plugin_architecture_design_v3.2.md`](../archive/legacy-workline-plugins/workline_plugin_architecture_design_v3.2.md)。

## 当前唯一执行链

```text
Callback / internal event
  → RuntimeInbox canonical envelope
  → RuntimeInboxProcessorBridge
  → generated Definition route
  → ROUTE_HANDLERS typed handler
  → RuntimeIntent / typed system-capability outcome
  → Runtime-owned state writeback and side effects
```

## 插件合同

- `Definition` 声明插件 identity、contract version、typed config、route 与 input model。
- `ROUTE_HANDLERS` 只绑定纯业务判断；generated index 固定 route identity 与 digest。
- handler registry 在启动/构建期验证 route、handler 与 schema 一致性。
- 插件只返回 Runtime decision/intent，不直接写 Session、设备命令、Outbox 或数据库。
- Runtime 固定完整 Binding pins，负责幂等、事务、状态推进、诊断与副作用。

## 当前必读

- [插件开发指南](../plugin_development_guide.md)
- [Runtime 工作流指南](workline_runtime_workflow_guide.md)
- [Runtime ownership map](../architecture/runtime-ownership-map.md)
- [Runtime orchestration spec](../architecture/runtime-orchestration-spec.md)

历史设计仅用于审计来源，不是当前实现或开发入口。
