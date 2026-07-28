# Runtime Ownership Map

> 当前契约：明确 `src/app/runtime/orchestration/` 域的所有面，固化业务 capability、域服务、入站消费者对 runtime 能力的访问边界。
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
| `ExecutionSession` | `execution_sessions` | 会话聚合根，固定 WorkLine、plugin 与 Binding pins | `id` 主键；`workline_id` / `plugin_key` 索引；`plugin_binding_id` FK |
| `ExecutionCorrelation` | `execution_correlations` | 跨实体一次性 correlation 锚点；执行关联边界的持久化载体 | `correlation_id` 唯一；跨域 session FK 收敛 |
| `ExecutionWorkItem` | `execution_work_items` | runtime capability 最小推进单位 | 与 `ExecutionSession` 1:N |
| `RuntimeInbox` | `runtime_inbox` | 入站持久化入口契约 | `provider_code` + `source_event_id` 唯一 |
| `RuntimeTimeline` | `runtime_timelines` | 事件溯源 | `correlation_id` 索引 |
| `RuntimeHold` | `runtime_holds` | Manual / Safety E-Stop / Material Conflict hold 状态机 | `correlation_id` + `scope_key` 索引 |
| `RuntimeIntentLog` | `runtime_intent_logs` | plugin 产出 RuntimeIntent 的 ledger | effect identity 与 `dispatch_key` 唯一；`effect_status` 索引 |
| `IdempotencyKey` | `idempotency_keys` | `WES-{OPERATION_KIND}-{HASH}` 唯一约束 | `(provider_code, operation_kind, idempotency_key)` 唯一 |
| `ConveyorQueueMembership` | `conveyor_queue_memberships` | 动态队列 active/history 投影 | `membership_status` `CheckConstraint` |

## 4. Repository 归属

| Repository | 文件 | 关键方法 |
| --- | --- | --- |
| `IdempotencyKeyRepository` | `repositories/idempotency_key_repository.py` | `claim_if_absent` + `get_by_identity` |
| `RuntimeInboxRepository` | `repositories/runtime_inbox_repository.py` | canonical 持久化、FIFO `SKIP LOCKED` claim、lease reclaim、fenced terminal、只读 SLI snapshot |

其余 runtime 实体默认使用 `BaseRepository`，仅在 upsert、并发保护或查询契约复杂时拆出专用 repository。

## 5. Service 归属

| Service | 文件 | 职责 |
| --- | --- | --- |
| `IdempotencyGuard` | `services/idempotency_guard.py` | outbound effect 幂等闸门；`ClaimResult.NEW/MATCH` + `IdempotencyConflict` |
| `RuntimeSnapshotAssembler` | `services/runtime_snapshot_assembler.py` | RuntimeSnapshot 装配：session + timeline + inbox + hold + intent log |
| `RuntimeInboxService` | `services/runtime_inbox/runtime_inbox_service.py` | 六类 ingress 幂等接收、五态 claim/fencing、audit-only 排除与 `REPLAY_REQUEST` 审计 |
| `RuntimeInboxProcessorBridge` | `services/runtime_inbox/runtime_inbox_orchestrator_bridge.py` | binding/context validation → generated dispatch → typed effect + fenced write-back |
| `RuntimeReconciliationServiceImpl` | `services/reconciliation/runtime_reconciliation_service_impl.py` | runtime 域对账实现；调用方通过正式 service contract 使用 |

Device/callback 对账调用遵循 runtime service/port 边界，不 import WorkLine 实现。

## 6. 消费者入口

| 路径 | 角色 |
| --- | --- |
| `src/app/runtime/orchestration/services/runtime_inbox/runtime_inbox_service.py` | RuntimeInbox ACK-before-processing、幂等接收、五态状态机；INBOUND_NORMALIZER_OWNERSHIP 对 RuntimeInbox 持有的显式例外 |
| `src/app/runtime/orchestration/consumers/callback_runtime_inbox_writer.py` | callback ingress 写入 RuntimeInbox 的薄适配器，不消费、不编排 |
| 其他 capability | 通过 `RuntimeCapabilityContext` 获取 query/effect port contract，不直接 import inbound normalizer |

`INBOUND_NORMALIZER_OWNERSHIP` guardrail 扫描 runtime、workline、callback、wms_integration/services 和 device 域，拒绝 capability 持有 `WmsEventPort` / `DeviceEventPort` / `InboundEventPort` / `RuntimeInbox`；仅 registry、RuntimeInbox entity/repository/service 等逐文件例外可持有。

RuntimeInbox 的六种 `kind` 为 `COMMAND_RESULT / DEVICE_EVENT / EXTERNAL_HTTP / INTERNAL_EVENT /
TIMER_TIMEOUT / REPLAY_REQUEST`，数据库状态固定为 `RECEIVED / PROCESSING / PROCESSED / FAILED /
DEAD_LETTER`。标记为 `PRE_CUTOVER_AUDIT_ONLY` 的历史行只属于审计证据，不可 claim、retry 或 replay。
Replay 的 `request_id`、认证 `actor`、`reason`、直接/根 source inbox 和原业务 kind 由
`RuntimeInboxService` 统一构造与审计，API/operation service 不复制该领域规则。

## 7. Production import boundary

生产代码不允许 import `src.workline_runtime`。仅以下非生产路径类别允许引用：

| 入口 | 角色 |
| --- | --- |
| `tests/` | 测试 |
| `migrations/` | Alembic 数据迁移 |

其余 `src/` production code 若 import `src.workline_runtime`，由 `LEGACY_RUNTIME_IMPORT` 立即阻塞。

## 8. 验收

- `uv run python -c "from src.app.runtime.orchestration import ExecutionSession, ExecutionCorrelation, ExecutionWorkItem, RuntimeInbox, RuntimeTimeline, RuntimeHold, RuntimeIntentLog, IdempotencyKey, ConveyorQueueMembership"` 全部 import 成功。
- `uv run python -c "from src.app.runtime.orchestration.services import IdempotencyGuard, RuntimeSnapshotAssembler"` 全部 import 成功。
- `bash scripts/architecture-guardrails.sh --mode enforced` 退出码 0。
- `uv run pytest tests/architecture/test_legacy_runtime_import_guardrail.py tests/architecture/test_inbound_normalizer_ownership_guardrail.py -q` 全部通过。
