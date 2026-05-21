# Workline Material Flow Runtime

本文描述 target architecture / 目标架构，用于指导迁移和删除 legacy per-plugin state machines；不表示当前代码已经全部落地。

## Principle

Plugin owns business judgement. Runtime owns state.

插件只判断业务含义和下一步意图，Material Flow Runtime 统一拥有物料流转状态、持久化状态、事件流和副作用调度。

## Plugin Developer Model

The plugin answers:

1. What happened on the current device?
2. What does it mean for the material?
3. Which device should do what next?
4. Should the material complete, block, or go to NG?

The plugin never maintains a state machine, writes persistence state, creates commands directly, handles retries, scans timeouts, or writes monitoring records.

插件输出 `RuntimeIntent`，表达业务判断后的目标意图；插件不拥有 MaterialRun 生命周期，也不直接决定持久化迁移。

## Runtime Responsibilities

Runtime validates the plugin intent, resolves devices through topology, creates commands, updates MaterialRun, writes RuntimeEvent, creates blockers, updates projections, calculates metrics, and emits alerts.

Runtime 负责校验 `RuntimeIntent`、验证拓扑和设备执行关系，并将合法意图落到统一的 MaterialRun 生命周期和事实流中。

## Fact Stream

RuntimeEvent is the single source for replay, trace, current views, metrics, and alerts.

RuntimeEvent 是物料流转回放、追踪、当前视图、指标和告警的唯一事实来源。
