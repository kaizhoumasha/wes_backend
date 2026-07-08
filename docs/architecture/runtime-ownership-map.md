# Runtime Ownership Map

> 目标态契约：明确 `src/app/runtime/orchestration/` 域的所有面，固化业务 capability、域服务、入站消费者对 runtime 能力的访问边界。  
> 配套阅读：[`./workline-and-plugin-restructuring.md`](./workline-and-plugin-restructuring.md) 与 [`./adr/0001-phase2-runtime-ownership.md`](./adr/0001-phase2-runtime-ownership.md)。

## 1. 适用范围

本文固化 runtime 域的代码归属。任何新增 runtime capability、service 或 repository 必须落在下表范围内，不允许再向 `src/workline_runtime/` 添加业务代码。

## 2. Runtime 域三层归属

| 层 | 路径前缀 | 职责 | 允许的下行依赖 |
| --- | --- | --- | --- |
| Entity | `src/app/runtime/orchestration/*.py` | 持久化状态、表结构、字段约束 | `src/core/mixins` + `src/core/timezone` 等纯工具 |
| Repository | `src/app/runtime/orchestration/repositories/` | 数据访问封装、唯一约束保护、no_autoflush 协同 | entity + `sqlalchemy` |
| Service | `src/app/runtime/orchestration/services/` | 业务语义编排、幂等闸门、跨实体装配 | entity + repository + 入站 normalizer；不直接引用 provider implementation |

## 3. Entity 归属

| Entity | `__tablename__` | 业务定位 | 唯一 / 索引约束 |
| --- | --- | --- | --- |
| `ExecutionSession` | `execution_sessions` | 会话聚合根，按 `trace_id` / `business_key` 唯一 | `workline_id` + `business_key` 业务唯一 |
| `ExecutionCorrelation` | `execution_correlations` | 跨实体一次性 correlation 锚点；执行关联边界的持久化载体 | `correlation_id` 唯一；跨域 session FK 收敛 |
| `ExecutionWorkItem` | `execution_work_items` | runtime capability 最小推进单位 | 与 `ExecutionSession` 1:N |
| `RuntimeInbox` | `runtime_inbox` | 入站持久化入口契约 | `provider_code` + `source_event_id` 唯一 |
| `RuntimeTimeline` | `runtime_timelines` | 事件溯源 | `correlation_id` 索引 |
| `RuntimeHold` | `runtime_holds` | Manual / Safety E-Stop / Material Conflict hold 状态机 | `correlation_id` + `scope_key` 索引 |
| `RuntimeIntentLog` | `runtime_intent_logs` | plugin 产出 RuntimeIntent 的 ledger | `dispatch_status` |
| `IdempotencyKey` | `idempotency_keys` | `WES-{OPERATION_KIND}-{HASH}` 唯一约束 | `(provider_code, operation_kind, idempotency_key)` 唯一 |
| `ConveyorQueueMembership` | `conveyor_queue_memberships` | 动态队列 active/history 投影 | `membership_status` `CheckConstraint` |

## 4. Repository 归属

| Repository | 文件 | 关键方法 |
| --- | --- | --- |
| `IdempotencyKeyRepository` | `repositories/idempotency_key_repository.py` | `claim_if_absent` + `get_by_identity` |

其余 runtime 实体默认使用 `BaseRepository`，仅在 upsert、并发保护或查询契约复杂时拆出专用 repository。

## 5. Service 归属

| Service | 文件 | 职责 |
| --- | --- | --- |
| `IdempotencyGuard` | `services/idempotency_guard.py` | outbound effect 幂等闸门；`ClaimResult.NEW/MATCH` + `IdempotencyConflict` |
| `RuntimeSnapshotAssembler` | `services/runtime_snapshot_assembler.py` | RuntimeSnapshot 装配：session + timeline + inbox + hold + intent log |
| `RuntimeReconciliationFacade` | `services/runtime_reconciliation_service.py` | device/callback 域对账能力唯一入口；当前委托 runtime reconciliation implementation |

`RuntimeReconciliationFacade` 是反向依赖的受控出口：device/callback 只 import facade，不直接 import workline 域。

## 6. 消费者入口

| 路径 | 角色 |
| --- | --- |
| `src/app/runtime/orchestration/consumers/` | `RuntimeInboxConsumer` 单点入口；唯一允许直接 import inbound normalizer port 的位置 |
| 其他 capability | 通过 `RuntimeCapabilityContext` 获取 query/effect port contract，不直接 import inbound normalizer |

`INBOUND_NORMALIZER_OWNERSHIP` guardrail 扫描 runtime、workline、callback、wms_integration/services 和 device 域，拒绝任何 capability 持有 `WmsEventPort` / `DeviceEventPort` / `InboundEventPort` / `RuntimeInbox` / `RuntimeInboxConsumer` / `InboundNormalizerContext` / `create_inbound_normalizer_context` 类型 hint。

## 7. Legacy Runtime Import Boundary

`src.workline_runtime` 在 production code 中不允许直接 import。仅以下路径类别可保留历史或非生产引用：

| 入口 | 角色 |
| --- | --- |
| `src/workline_runtime/` 自身 | 历史自引用；目录删除后不再存在 |
| `tests/` | 测试 |
| `migrations/` | Alembic 数据迁移 |

其余 `src/` production code 若 import `src.workline_runtime`，由 `LEGACY_RUNTIME_IMPORT` 立即阻塞。

## 8. 验收

- `uv run python -c "from src.app.runtime.orchestration import ExecutionSession, ExecutionCorrelation, ExecutionWorkItem, RuntimeInbox, RuntimeTimeline, RuntimeHold, RuntimeIntentLog, IdempotencyKey, ConveyorQueueMembership"` 全部 import 成功。
- `uv run python -c "from src.app.runtime.orchestration.services import RuntimeReconciliationFacade, IdempotencyGuard, RuntimeSnapshotAssembler"` 全部 import 成功。
- `bash scripts/architecture-guardrails.sh --mode enforced` 退出码 0。
- `uv run pytest tests/architecture/test_legacy_runtime_import_guardrail.py tests/architecture/test_inbound_normalizer_ownership_guardrail.py -q` 全部通过。
