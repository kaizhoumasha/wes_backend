# Runtime Ownership Map (Phase 2 launch PR)

> **目标态契约**:明确 Phase 2 launch PR 后 `src/app/runtime/orchestration/` 域的所有面,
> 固化业务 capability / 域服务 / 入站消费者对 runtime 能力的访问边界。
> 与 [`./workline-and-plugin-restructuring.md`](./workline-and-plugin-restructuring.md) §9.2 + §10.3 + [`./adr/0001-phase2-runtime-ownership.md`](./adr/0001-phase2-runtime-ownership.md) 配套阅读。

## 1. 适用范围

本文固化 Phase 2 launch PR 后 runtime 域的代码归属,作为 Phase 2 burn-down (814 rows, 756 rebuild + 58 move) 的目标态参考。任何新增 runtime capability / service / repository 必须落在下表范围内,不允许再向 `src/workline_runtime/` 添加业务代码。

## 2. Runtime 域三层归属

| 层 | 路径前缀 | 职责 | 允许的下行依赖 |
|---|---|---|---|
| **Entity (实体)** | `src/app/runtime/orchestration/*.py` (9 文件) | 持久化状态、表结构、字段约束 (`__tablename__`)、`CheckConstraint` | `src/core/mixins` + `src/core/timezone` 等纯工具 |
| **Repository** | `src/app/runtime/orchestration/repositories/` | 数据访问封装、唯一约束保护、no_autoflush 协同 | entity + `sqlalchemy` |
| **Service** | `src/app/runtime/orchestration/services/` | 业务语义编排、幂等闸门、跨实体装配 (snapshot) | entity + repository + 入站 normalizer **不直接引用** |

## 3. Entity 归属 (9 实体)

| Entity | `__tablename__` | 业务定位 | 唯一 / 索引约束 |
|---|---|---|---|
| `ExecutionSession` | `execution_sessions` | 会话聚合根,按 `trace_id` / `business_key` 唯一 | `workline_id` + `business_key` 业务唯一 |
| `ExecutionCorrelation` | `execution_correlations` | 跨实体一次性 correlation 锚点 (主计划 §C2) | `correlation_id` 唯一;跨域 session FK 收敛 |
| `ExecutionWorkItem` | `execution_work_items` | runtime capability 最小推进单位 | 与 `ExecutionSession` 1:N |
| `RuntimeInbox` | `runtime_inbox` | 入站持久化入口契约 (H4 反注入边界守门) | `provider_code` + `source_event_id` 唯一 |
| `RuntimeTimeline` | `runtime_timelines` | 事件溯源 | `correlation_id` 索引 |
| `RuntimeHold` | `runtime_holds` | Manual / Safety E-Stop / Material Conflict hold 状态机 | `correlation_id` + `scope_key` 索引 |
| `RuntimeIntentLog` | `runtime_intent_logs` | plugin 产出 RuntimeIntent 的 ledger | `dispatch_status` (PENDING/DISPATCHING/DISPATCHED/ACKED/FAILED) |
| `IdempotencyKey` | `idempotency_keys` | H5 `WES-{OPERATION_KIND}-{HASH}` 唯一约束 | `(provider_code, operation_kind, idempotency_key)` 唯一 |
| `ConveyorQueueMembership` | `conveyor_queue_memberships` | 动态队列 active/history 投影 | `membership_status` `CheckConstraint` (Phase 1 §4.6) |

## 4. Repository 归属

| Repository | 文件 | 关键方法 |
|---|---|---|
| `IdempotencyKeyRepository` | `repositories/idempotency_key_repository.py` | `claim_if_absent` (Phase 1 §5.4 幂等闸门) + `get_by_identity` |

> **当前范围**:仅 `IdempotencyKey` 需要独立 Repository (upsert 语义复杂);其余 8 实体直接走 `BaseRepository` (Phase 2 burn-down 按需再拆)。

## 5. Service 归属

| Service | 文件 | 职责 |
|---|---|---|
| `IdempotencyGuard` | `services/idempotency_guard.py` | outbound effect 幂等闸门 (主计划 §5.4 H5);`ClaimResult.NEW/MATCH` + `IdempotencyConflict` |
| `RuntimeSnapshotAssembler` | `services/runtime_snapshot_assembler.py` | BC-02 RuntimeSnapshot 装配 (session + timeline + inbox + hold + intent log) |
| `RuntimeReconciliationFacade` | `services/runtime_reconciliation_service.py` | **Phase 2 launch PR 新增** — device/callback 域对账能力唯一入口;当前委托 workline 单例,Phase 2 burn-down 阶段替换为本地实现 |

### 5.1 `RuntimeReconciliationFacade` 的角色

**问题**:Step 5 跨域 import 修复发现 device/callback 反向依赖 `src.app.workline.services.runtime_reconciliation_service`,违反分层架构 (主计划 §7.5 + §9.2)。

**解决**:
1. 新建 `runtime_reconciliation_facade` 作为对账能力的 runtime 域官方入口
2. device/callback 改 import facade,不再 import workline 域
3. facade 内部当前仍委托 workline 单例 (Phase 2 launch PR 阶段的合规桥接)
4. Phase 2 burn-down 阶段把 workline_runtime_reconciliation_service 整体迁入 `services/runtime_reconciliation_service_impl.py`,facade 直接 import 实现

**边界合规**:facade 是反向依赖的反向出口,允许向下委托一次;不破坏分层架构。

## 6. 消费者入口 (single entry point)

| 路径 | 角色 |
|---|---|
| `src/app/runtime/orchestration/consumers/` | `RuntimeInboxConsumer` 单点入口,Phase 1 Packet D 已定义;**唯一允许**直接 import inbound normalizer (`WmsEventPort` / `DeviceEventPort`) 的位置 |
| 其他 capability | 通过 `RuntimeCapabilityContext.get_inbound_normalizer` 获取,不允许直接 import |

**R-I3c guardrail** (Step 4) 已扫描 5 个域,拒绝任何 capability 持有 `WmsEventPort` / `DeviceEventPort` / `InboundEventPort` / `RuntimeInbox` / `RuntimeInboxConsumer` / `InboundNormalizerContext` / `create_inbound_normalizer_context` 类型 hint。

## 7. wlr allowlist 严格型 (Phase 2 Step 3)

`src.workline_runtime` 在生产代码中**仅允许**以下入口直接 import:

| 入口 | 角色 | 数量 |
|---|---|---|
| `src/workline_runtime/` 自身 | wlr 内部模块互引 | (全部) |
| `src/app/runtime/orchestration/consumers/` | RuntimeInboxConsumer 单点入口 | 1 |
| `tests/` | 测试 | (全部) |
| `migrations/` | Alembic 数据迁移 | (全部) |

其余 `src/` 任何 production code 都**不允许** import `src.workline_runtime`。当前 28 处跨域 wlr import 已全部纳入 `scripts/architecture-guardrails.allowlist` 严格型条目,legacy_entry_id 格式 `legacy:<path>:<file>#R-WLR`,drop_phase = `phase2`,expires_at = `2026-09-30`。

## 8. 不在本 launch PR 范围 (Phase 2 burn-down 待办)

| 任务 | 当前状态 | 后续 PR |
|---|---|---|
| 814 rows cleanup matrix 实际迁移 (756 rebuild + 58 move) | 未开始 | Phase 2 burn-down 6 阶段 |
| `src/workline_runtime/` 整目录删除 | 未开始 | Phase 2 T3 |
| Runtime API facade 迁移 (Phase 2 burn-down 阶段 5) | 未开始 | Phase 2 burn-down |
| Phase 3 ENG-009 / ENG-011 / ENG-020 (idempotency 完整 + inbox backpressure + scenario replay) | 未开始 | Phase 3 |
| Phase 5 tech-debt cleanup (debug endpoints 等) | 未开始 | Phase 5 |

## 9. 验收

- `uv run python -c "from src.app.runtime.orchestration import ExecutionSession, ExecutionCorrelation, ExecutionWorkItem, RuntimeInbox, RuntimeTimeline, RuntimeHold, RuntimeIntentLog, IdempotencyKey, ConveyorQueueMembership"` 全部 import 成功
- `uv run python -c "from src.app.runtime.orchestration.services import RuntimeReconciliationFacade, IdempotencyGuard, RuntimeSnapshotAssembler"` 全部 import 成功
- `ARCHITECTURE_PHASE=phase1 ./scripts/architecture-guardrails.sh --phase phase1` 退出码 0,R-WLR + R-I3a/b/c 全绿
- `uv run pytest tests/architecture/test_wlr_import_guardrail.py tests/architecture/test_ri3c_inbound_normalizer_port_guardrail.py -v` 全部通过

## 10. 引用

- 主计划 §9.2 (runtime 域 7 实体)+ §10.3 (Phase 2 launch PR 启动条件) + §7.5 (架构不变量)
- ADR-0001: Phase 2 runtime ownership (本文配套)
- 主计划 §5.4 H5 幂等键命名:`WES-{OPERATION_KIND}-{HASH}`
- 主计划 §C2 跨域 session FK 收敛:ExecutionCorrelation
- 主计划 §3.5.1 + H2 inbound normalizer 边界
- 主计划 §10.8 L2269 legacy-runtime-migration-spec (Step 8 待补)
