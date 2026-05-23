# Workline Material Flow Runtime

本文是 Workline 物料流 Runtime 的当前权威口径。旧的 2026-05-11 Material Flow Runtime 计划已归档，不再继续执行。

本轮收敛不新增 `RuntimeEvent` 持久化表，不新增 `MaterialRun` 表，不改变 API 合同，不生成数据库迁移。当前生产链路以现有事实源为准：`WorklineSession` 承载流程/物料运行状态，`RuntimeIntentEffectApplier` 将插件输出的 `RuntimeIntent` 落到命令、Outbox、Timeline、Hold 和资源事实中。

## Principle

Plugin owns business judgement. Runtime owns state.

插件只判断业务含义和下一步意图；Runtime 统一拥有 Session lifecycle、持久化状态迁移、事实记录和副作用调度。

## Current Source Of Truth

| 维度 | 当前权威来源 | 说明 |
| --- | --- | --- |
| 插件输出源 | `RuntimeIntent` | 插件只返回运行时意图，不直接写命令、Outbox、Session 或 Timeline。 |
| 流程/物料运行状态源 | `WorklineSession` | 当前生产链路中，一条业务链路的等待状态、完成/失败状态、上下文和追踪字段由 Session 承载。 |
| 命令生命周期 | `DeviceCommand` | 设备命令业务主键、ACK、结果、错误和执行状态的权威记录。 |
| 派发/ACK/重试证据 | `WorklineOutbox` + `WorklineDispatchAttempt` | Outbox 是副作用出口，DispatchAttempt 是派发尝试、ACK 和重试证据。 |
| 异常恢复源 | `RuntimeHold` | 人工介入、对账、恢复和挂起原因的权威记录。 |
| 追踪事实流 | `WorklineTimeline` | Runtime 决策、状态迁移、等待、派发准备、完成/失败等可追溯事实账本。 |
| 资源物理事实 | `ResourceStateEvent` + active projections | 货架、料箱、库位等物理事实先写事件，再由 active projections 表达当前视图。 |
| Rack/Bin 低级操作账本 | `WorklineRackTask` / bin operation task | Rack/Bin 操作由低级任务账本记录执行计划、派发键、顺序和完成证据。 |

## Plugin Developer Model

The plugin answers:

1. What happened on the current device?
2. What does it mean for the material?
3. Which device should do what next?
4. Should the material complete, block, or go to NG?

The plugin never maintains a state machine, writes persistence state, creates commands directly, handles retries, scans timeouts, or writes monitoring records.

插件输出 `RuntimeIntent`，表达业务判断后的目标意图；插件不拥有 Session lifecycle，也不直接决定持久化迁移。

## Runtime Responsibilities

Runtime validates the plugin intent, resolves devices through topology, creates commands, writes Outbox dispatch records, updates WorklineSession, writes WorklineTimeline facts, creates RuntimeHold records, and applies resource facts/reservations.

Runtime 负责校验 `RuntimeIntent`、验证拓扑和设备执行关系，并将合法意图落到当前生产事实源中。

## Effect Layer

`RuntimeIntentEffectApplier` 是 `RuntimeIntent` 的真实落地层。它负责把插件返回的意图转换成：

- `DeviceCommand` + `WorklineOutbox`
- `WorklineTimeline`
- `WorklineSession` 状态/等待/上下文变更
- `RuntimeHold`
- Rack/Bin operation task
- `ResourceStateEvent` 和 active projections

新增 Runtime 能力时应优先扩展 `RuntimeIntent` + `RuntimeIntentEffectApplier`，不要新增第二套 MaterialRun/RuntimeEvent 状态源。
