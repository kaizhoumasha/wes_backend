# Phase1~Phase4 遗留项推进 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在进入 Phase5 删除旧体系前，关闭 Phase1~Phase4 中会影响删除安全、生产切换和上线验收的代码/门禁遗留项；真实 production evidence 产物作为发布/现场输入，不在本计划中伪造。

**Architecture:** 以 runtime/orchestration 作为运行状态、入站消息、恢复与对账 owner；WorkLine 只保留配置职责，`WorkLine.runtime_status` 只能作为短生命周期兼容投影或被明确迁出。External callback 统一先进入 `RuntimeInbox`，旧 Workline inbox/processor 只能作为过渡消费实现，不再作为入站权威。

**Tech Stack:** FastAPI, SQLModel, SQLAlchemy AsyncSession, Alembic, pytest, Ruff, Bandit, GitNexus, RTK, gstack gates。

---

## 背景与根因

本计划来自 2026-07-06 对 `docs/architecture/workline-and-plugin-restructuring.md` Phase1~Phase4 的验收。

已验证事实：

- Phase1 已通过，7 个 WMS port、capability 注入边界、C1~C5 护栏和 callback admission 均有合同测试覆盖。
- Phase2 主迁移已完成，但 `WorkLine.runtime_status` 仍作为兼容投影存在，并被 safety、hold、reconciliation、start admission、query/trace 等路径读取或写入。
- Phase3 开发/测试 `MOCK` closure 已通过，但 production closure 仍要求真实 P0 E2E artifact 与 production-scale benchmark artifact。
- Phase3 的关键删除阻塞项是 external callback / CB late callback 生产热路径仍先落旧 `WorklineInboxService`。
- Phase4 开发/测试 readiness 与业务语义已通过，但 production evidence profile 仍需要现场或 simulator evidence 文件；业务承载 legacy 删除前必须满足 Phase4 evidence profile。

Root cause hypothesis: Phase1~Phase4 遗留项的根因不是单个测试缺失，而是两个历史过渡层还在承载生产语义：`WorkLine.runtime_status` 兼容投影仍被运行准入/安全隔离读取，callback 入站生产路径仍经 `WorklineInboxService`。因此 Phase5 不能先做全量删除，必须先完成 runtime owner 收敛、RuntimeInbox cutover 和 production evidence gate。

Prior learning applied:

- `ecs_idle_is_dispatch_admission_source`: ECS `/device/status` IDLE 是 dispatch admission 权威，本计划不允许用本地 WorkLine 或 DeviceRuntime 投影替代实时 ECS probe。
- `workline_role_driven_breaking_changes`: 项目未发布，可接受目标态优先的破坏性变更。本计划不保留旧 API / 旧插件形态兼容承诺。

## 范围

本计划关闭三类遗留项：

1. **删除安全遗留项**：Phase2 `WorkLine.runtime_status` owner 未闭合；callback 热路径旧 inbox 依赖；CB late callback 仍与 RuntimeInbox cutover 绑定。
2. **生产验收门禁遗留项**：Phase3 production closure artifact schema/composer、PostgreSQL queue writer benchmark provenance、真实 P0 E2E / benchmark evidence 的校验 gate。
3. **Phase4 业务 evidence 门禁遗留项**：sorter inbound 与 SMT/NG/WMS 对账的 production evidence profile schema/composer 与 gate。

不在本计划内：

- Phase5 实际删除旧 `src/workline_runtime` / `src/workline_plugins` 全量代码。
- 生成或伪造真实现场 Phase3 production artifact、Phase4 production evidence 文件。
- RCS/AGV/CTU 直连 provider adapter。触发条件仍按主设计文档 §10.5 执行。
- 为旧 API、旧表名、旧插件框架新增兼容层。

执行状态（2026-07-06 验收修正）：

- 本计划的开发/测试闭环与 Phase5 technical lane 前置已完成；对应 checkbox 反映执行状态。
- Phase5 business lane 不是本计划内的“已完成”交付物；它必须等待真实 Phase3 production artifact、Phase4 production evidence artifact、Phase4 contract tests 和 legacy matrix 业务项逐项关闭。
- 可选 PostgreSQL 并发验证未作为本地开发阻塞；production closure 前必须提供同等生产规模 evidence。

状态同步（2026-07-07）：

- Phase3 production closure artifacts 与 Phase4 production evidence artifact 已由后续计划补齐并通过 production gates。
- Phase5 technical lane 已随 PR #78 合并；Phase5 business readiness 与 business destructive cleanup 已随 PR #79 合并到 `develop`（`v0.14.0.0`，merge SHA `8c833610c08005005406b3a774c92519f69b7886`）。
- `WorkLine.runtime_status` 物理字段删除仍不属于本计划，继续作为独立 schema/data cleanup 排期。

## 文件结构与职责

### Runtime status owner 收敛

- Modify: `src/app/runtime/orchestration/services/workline_runtime_status_projection_service.py`
  - 现阶段集中维护兼容投影；本计划将其升级为唯一投影写入口，并暴露明确的 read-model 方法名。
- Modify: `src/app/workline/services/safety_service.py`
  - 移除 WorkLine 配置域对运行状态 owner 的直接语义，改为调用 runtime/orchestration 服务处理安全隔离。
- Modify: `src/app/runtime/capabilities/phase4/start_admission_service.py`
  - START 准入只能读取 runtime/orchestration readiness，不把 WorkLine 配置域当状态 owner。
- Modify: `src/app/runtime/orchestration/services/query/runtime_query_service.py`
  - 查询层保留兼容字段输出，但字段说明必须标记为 runtime projection。
- Modify: `src/app/runtime/orchestration/services/trace/trace_query_service.py`
  - trace 输出保留 `workline_runtime_status`，来源改为 runtime projection。
- Create: `tests/architecture/test_phase2_runtime_status_owner_guardrail.py`
  - 静态扫描：禁止 `src/app/workline/**` 和 Phase4 capability 直接赋值 `workline.runtime_status`。
- Modify: `tests/workline_runtime/test_workline_runtime_status_projection_service.py`
  - 覆盖唯一写入口、兼容投影命名、READY/STOPPED/RECONCILING/ESTOPPED 转移。

### RuntimeInbox callback cutover

- Create: `src/app/runtime/orchestration/consumers/callback_runtime_inbox_writer.py`
  - 将 result/event/external callback 的最小入站包络转换为 `RuntimeInboxService.accept_received()` 参数。
  - 只做 normalize 后的持久化与幂等，不推进 session/projection。
- Modify: `src/app/callback/services/callback_orchestration_service.py`
  - result/event/external 三条路径先写 `RuntimeInbox`，旧 Workline inbox 只作为过渡 worker 输入，不再作为入站 ACK 权威。
- Modify: `src/app/callback/services/callback_ingress_service.py`
  - 保持 HMAC、nonce、allow-list、schema admission 在前；通过后调用 runtime inbox writer。
- Modify: `src/app/runtime/orchestration/consumers/runtime_inbox_consumer.py`
  - 承担 RuntimeInbox 单点消费入口；过渡期可委托旧 processor，但委托必须从 RuntimeInbox record 开始。
- Modify: `src/app/runtime/orchestration/services/runtime_inbox/runtime_inbox_service.py`
  - 修正 `accept_received()` 内重复 `now_ms` 传参问题，并补齐 callback/fulfillment/device_event/reconciliation 的 operation_kind 映射覆盖。
- Test: `tests/runtime/orchestration/test_runtime_inbox_phase3_service.py`
  - 补 result/event/external 的幂等、payload_hash 冲突、source_event_id 缺失路径。
- Test: `tests/api/test_callback_external_api.py`, `tests/api/test_callback_event_api.py`, `tests/api/test_callback_result_api.py`
  - 验证 API ACK 前只做安全校验与 RuntimeInbox 持久化；重复 callback 返回既有 ACK；不同 hash 返回 409。
- Test: `tests/callback/test_callback_runtime_inbox_cutover.py`
  - 新增 cutover 合同，禁止 callback service 直接以旧 Workline inbox 为 ACK source。

### CB late callback 与 WMS fulfillment

- Modify: `src/app/wms_integration/state_machine.py`
  - 保持 `BLOCKED_BY_CB` 只代表出站 effect 阻塞，不覆盖在途 fulfillment 状态。
- Modify: `src/app/wms_integration/services/fulfillment_lifecycle.py`
  - CB open/half-open 恢复前先查 RuntimeInbox/evidence；late callback 由 RuntimeInbox 幂等合并或进入 RECONCILING。
- Modify: `src/app/runtime/orchestration/services/reconciliation/runtime_reconciliation_service_impl.py`
  - late callback 与 active projection 冲突时登记 owner-scoped `ReconciliationRecord`，不直接写 owner 终态。
- Test: `tests/wms_integration/test_fulfillment_state_machine.py`
  - 覆盖 `BLOCKED_BY_CB` 不吞掉 late callback evidence。
- Test: `tests/runtime/orchestration/test_phase3_recovery_policies.py`
  - 覆盖 CB open/half-open、outbound fast-fail、late callback 入 RuntimeInbox、冲突进入 RECONCILING。

### Production evidence 与 benchmark

- Modify: `scripts/check_phase3_closure_gate.py`
  - 保持 mock profile 默认；production profile 必须显式传入 P0 E2E 与 benchmark artifact。
- Modify: `scripts/compose_phase3_p0_e2e_artifact.py`
  - 从 trace-query / simulator recording 组装 production-compatible P0 E2E artifact。
- Modify: `scripts/compose_phase3_runtime_benchmark_artifact.py`
  - 从 RuntimeInbox claim、queue writer、ECS status command、PlaneSnapshot 四类真实 evidence 文件组装 production-scale benchmark artifact。
- Modify: `tests/load/phase3_benchmark_scenarios.py`
  - 固化生产基线规模：RuntimeInbox 1000 pending / 4 worker、queue writer 200 active memberships + identity collision、ECS HTTP status+command、PlaneSnapshot 1 WorkLine / 10 queue / 50 device / 100 session / 200 object。
- Test: `tests/runtime/orchestration/test_phase3_closure_evidence_gate.py`
  - production profile 缺 artifact、缺 evidence 文件、lightweight benchmark 冒充 production 时失败。
- Test: `tests/runtime/orchestration/test_phase3_benchmark_artifact_composer.py`
  - production artifact provenance 与 workload metadata 必填。
- Optional Test: `tests/integration/test_phase3_conveyor_queue_membership_concurrency.py`
  - 仅在 PostgreSQL/Redis 环境变量齐全时运行，验证真实 partial unique index 并发语义。

### Phase4 evidence profile

- Modify: `scripts/check_phase4_runtime_readiness_gate.py`
  - production profile 继续叠加 Phase3 production closure，并校验 Phase4 evidence manifest。
- Modify: `scripts/compose_phase4_runtime_evidence_artifact.py`
  - 从 provider contract、effect dispatch trace、callback worker trace、RuntimeHold/ReconciliationRecord trace 组装 Phase4 artifact。
- Modify: `src/app/runtime/capabilities/phase4/sorter_inbound_runtime_service.py`
  - 保持 production-capable path builder，不根据外部 provider 类型分支。
- Modify: `src/app/runtime/capabilities/phase4/smt_ng_wms_reconciliation_runtime_service.py`
  - 保持 RuntimeInbox evidence、重复 callback 幂等、WMS reject/source_version drift、scope-only release plan。
- Test: `tests/contracts/test_phase4_runtime_readiness_gate.py`
  - production profile 缺 Phase3 artifact 或 Phase4 evidence 时失败。
- Test: `tests/contracts/test_phase4_runtime_evidence_artifact_composer.py`
  - simulator/site/production artifact 均引用真实 evidence 文件。
- Test: `tests/workline_runtime/test_sorter_inbound_runtime_service.py`
  - 验证 RuntimeIntent、WMS fulfillment/inventory effect contract、CellReservation/RuntimeLocationEvent evidence、object-scope reconciliation plan。
- Test: `tests/workline_runtime/test_smt_ng_wms_reconciliation_runtime_service.py`
  - 验证 RuntimeInbox 上游 evidence、重复 callback 幂等合并、WMS reject/source_version drift、RuntimeHold plan。

### Phase5 readiness gate

- Create: `scripts/check_phase5_readiness_gate.py`
  - 检查技术残留 lane 与业务承载 lane 的启动条件。
  - 技术残留 lane：要求 Phase2 runtime owner 收敛、RuntimeInbox cutover、Phase3 mock closure 与行为合同全绿。
  - 业务承载 lane：要求 Phase3 production closure、Phase4 production evidence profile、Phase4 capability / port / contract tests 全绿，以及 legacy matrix 业务项关闭。
- Create: `tests/contracts/test_phase5_readiness_gate.py`
  - 覆盖技术 lane 可启动、业务 lane 被 production evidence 阻塞、旧 WorkLine runtime owner 未闭合时失败。
- Modify: `docs/architecture/workline-and-plugin-restructuring.md`
  - Phase2/3/4 状态更新只在对应 gate 真实通过后进行。
- Modify: `docs/architecture/legacy-cleanup-matrix.md`
  - 标记哪些 legacy 项只能进入技术残留 lane，哪些必须等待 Phase4 business evidence。

## 实施顺序

执行纪律：Task 1 中的 owner guardrail 是 TDD red step，不单独形成最终提交；实际执行时应与 Task 2 的 runtime owner 收敛合并成一个 green slice，确保每个提交点都能通过对应验证。

### Task 1: 建立 Phase1~4 residual ledger 与 owner guardrail

**Files:**

- Create: `tests/architecture/test_phase2_runtime_status_owner_guardrail.py`
- Modify: `docs/architecture/workline-and-plugin-restructuring.md`
- Modify: `docs/architecture/legacy-cleanup-matrix.md`

- [x] **Step 1: GitNexus 影响分析**

Run:

```bash
rtk node .gitnexus/run.cjs impact WorkLineRuntimeStatusProjectionService --direction upstream
rtk node .gitnexus/run.cjs impact WorkLineSafetyService --direction upstream
```

Expected:

- 记录 direct callers、affected processes、risk level。
- 若任一结果为 HIGH / CRITICAL，先向用户汇报再继续执行代码修改。

- [x] **Step 2: 写失败的 owner guardrail 测试**

在 `tests/architecture/test_phase2_runtime_status_owner_guardrail.py` 中定义静态扫描合同：

- 允许写入口：`src/app/runtime/orchestration/services/workline_runtime_status_projection_service.py`
- 允许只读展示：`runtime_query_service.py`、`trace_query_service.py`
- 禁止直接赋值：`src/app/workline/**`、`src/app/runtime/capabilities/phase4/**`
- 禁止文案把 `WorkLine.runtime_status` 称为状态 owner

Run:

```bash
rtk uv run pytest tests/architecture/test_phase2_runtime_status_owner_guardrail.py -q
```

Expected:

- 当前代码应失败，指出至少 `src/app/workline/services/safety_service.py` 或 Phase4 capability 中仍有直接运行态依赖。

- [x] **Step 3: 更新 residual ledger 文档**

在主设计文档新增或刷新 `Phase1~4 residual ledger` 小节，列出：

- Phase1：callback admission 已关闭，保留 regression gate。
- Phase2：`WorkLine.runtime_status` owner 未闭合。
- Phase3：RuntimeInbox cutover、CB late callback、production artifacts、PostgreSQL benchmark。
- Phase4：production evidence profile 与 business-lane deletion blocker。

Run:

```bash
rtk rg -n "Phase1~4 residual ledger|runtime_status owner|RuntimeInbox cutover|business-lane" docs/architecture/workline-and-plugin-restructuring.md
```

Expected:

- 能定位到 residual ledger。

- [x] **Step 4: 提交**

Run:

```bash
rtk git add tests/architecture/test_phase2_runtime_status_owner_guardrail.py docs/architecture/workline-and-plugin-restructuring.md docs/architecture/legacy-cleanup-matrix.md
rtk git commit -m "test(architecture): add phase residual owner guardrail"
```

Expected:

- commit 成功。

### Task 2: 收敛 `WorkLine.runtime_status` 为 runtime/orchestration 兼容投影

**Files:**

- Modify: `src/app/runtime/orchestration/services/workline_runtime_status_projection_service.py`
- Modify: `src/app/workline/services/safety_service.py`
- Modify: `src/app/runtime/capabilities/phase4/start_admission_service.py`
- Modify: `src/app/runtime/orchestration/services/query/runtime_query_service.py`
- Modify: `src/app/runtime/orchestration/services/trace/trace_query_service.py`
- Modify: `tests/workline_runtime/test_workline_runtime_status_projection_service.py`
- Modify: `tests/architecture/test_phase2_runtime_status_owner_guardrail.py`

- [x] **Step 1: GitNexus 影响分析**

Run:

```bash
rtk node .gitnexus/run.cjs impact WorkLineRuntimeStatusProjectionService --direction upstream
rtk node .gitnexus/run.cjs impact WorkLineSafetyService.handle_estop --direction upstream
rtk node .gitnexus/run.cjs impact StartAdmissionService --direction upstream
```

Expected:

- 明确 `handle_estop`、START admission、hold release、runtime query/trace 的 blast radius。
- HIGH / CRITICAL 时先汇报。

- [x] **Step 2: 扩展失败测试**

在 `tests/workline_runtime/test_workline_runtime_status_projection_service.py` 增加合同场景：

- ESTOP 只能通过 projection service 写入 `ESTOPPED`。
- READY/STOPPED/RECONCILING/ESTOPPED 的变更必须保留 `stopped_reason` / `resumed_at` 语义。
- WorkLine 配置域服务只能委托 runtime/orchestration projection service。

Run:

```bash
rtk uv run pytest tests/workline_runtime/test_workline_runtime_status_projection_service.py tests/architecture/test_phase2_runtime_status_owner_guardrail.py -q
```

Expected:

- 新测试先失败，失败点指向直接读写或缺少 owner 方法。

- [x] **Step 3: 修改 runtime projection service**

调整 `WorkLineRuntimeStatusProjectionService` 的公共方法命名与职责：

- `project_ready_after_start`
- `project_stopped_waiting_start`
- `project_reconciling`
- `project_estopped_active_hold`
- `assert_accepting_runtime_work`
- `runtime_status_snapshot`

约定：

- 该 service 是唯一兼容投影写入口。
- 读取方法返回带 `source="runtime/orchestration"` 的快照对象或 mapping。
- 不引入新的 WorkLine 状态 owner。

- [x] **Step 4: 修改 WorkLine safety 与 START admission**

调整：

- `WorkLineSafetyService.assert_accepting_work()` 调用 runtime projection/readiness 方法。
- `WorkLineSafetyService.handle_estop()` 只通过 projection service 写状态。
- `Phase4StartAdmissionService` 不直接比较 `getattr(workline, "runtime_status")`，改读 runtime readiness。

Run:

```bash
rtk uv run pytest tests/workline_runtime/test_workline_runtime_status_projection_service.py tests/api/test_callback_event_api.py tests/workline_runtime/test_sorter_inbound_runtime_service.py -q
```

Expected:

- 相关测试通过。

- [x] **Step 5: 运行架构护栏**

Run:

```bash
rtk ./scripts/architecture-guardrails.sh --phase phase1
rtk uv run pytest tests/architecture/test_phase2_runtime_status_owner_guardrail.py -q
```

Expected:

- violations 0 / warnings 0。
- owner guardrail 通过。

- [x] **Step 6: 提交**

Run:

```bash
rtk git add src/app/runtime/orchestration/services/workline_runtime_status_projection_service.py src/app/workline/services/safety_service.py src/app/runtime/capabilities/phase4/start_admission_service.py src/app/runtime/orchestration/services/query/runtime_query_service.py src/app/runtime/orchestration/services/trace/trace_query_service.py tests/workline_runtime/test_workline_runtime_status_projection_service.py tests/architecture/test_phase2_runtime_status_owner_guardrail.py
rtk git commit -m "refactor(runtime): own workline runtime status projection"
```

Expected:

- commit 成功，Phase2 owner 残留关闭或降为明确兼容投影。

### Task 3: RuntimeInbox 成为 callback 入站 ACK 权威

**Files:**

- Create: `src/app/runtime/orchestration/consumers/callback_runtime_inbox_writer.py`
- Modify: `src/app/runtime/orchestration/services/runtime_inbox/runtime_inbox_service.py`
- Modify: `src/app/callback/services/callback_orchestration_service.py`
- Modify: `src/app/callback/services/callback_ingress_service.py`
- Modify: `src/app/runtime/orchestration/consumers/runtime_inbox_consumer.py`
- Create: `tests/callback/test_callback_runtime_inbox_cutover.py`
- Modify: `tests/runtime/orchestration/test_runtime_inbox_phase3_service.py`
- Modify: `tests/api/test_callback_external_api.py`
- Modify: `tests/api/test_callback_event_api.py`
- Modify: `tests/api/test_callback_result_api.py`

- [x] **Step 1: GitNexus 影响分析**

Run:

```bash
rtk node .gitnexus/run.cjs impact CallbackOrchestrationService --direction upstream
rtk node .gitnexus/run.cjs impact RuntimeInboxService --direction upstream
rtk node .gitnexus/run.cjs impact RuntimeInboxConsumer --direction upstream
```

Expected:

- 明确 callback API、tests、worker 的 blast radius。
- HIGH / CRITICAL 时先汇报。

- [x] **Step 2: 写 cutover 失败测试**

新增 `tests/callback/test_callback_runtime_inbox_cutover.py`，覆盖：

- result callback 鉴权/包络通过后调用 `RuntimeInboxService.accept_received()`。
- event callback 鉴权/包络通过后调用 `RuntimeInboxService.accept_received()`。
- external callback 鉴权/包络通过后调用 `RuntimeInboxService.accept_received()`。
- 旧 `WorklineInboxService` 不能作为 ACK source；过渡委托必须发生在 RuntimeInbox record 创建之后。

Run:

```bash
rtk uv run pytest tests/callback/test_callback_runtime_inbox_cutover.py -q
```

Expected:

- 当前实现失败，原因是 callback orchestration 仍直接依赖 `WorklineInboxService`。

- [x] **Step 3: 实现 callback runtime inbox writer**

创建 `callback_runtime_inbox_writer.py`，职责固定为：

- result/event/external 三种 callback 输入到 RuntimeInbox 参数映射。
- `provider_code` 来自 provider profile 或 source_system。
- `event_type` 使用 canonical callback/event/result type。
- `source_event_id` 优先使用 `event_id` / `source_event_id` / `request_id`。
- `payload_hash` 复用已校验 body hash 或 normalized payload hash。
- `correlation_id` 有则传入，无则允许为空。

该文件不得：

- 推进 session。
- 修改 projection。
- 创建 WMS fulfillment。
- 调用旧 Workline inbox processor。

- [x] **Step 4: 修正 RuntimeInboxService 细节**

在 `RuntimeInboxService.accept_received()` 中修正重复 `now_ms` 传参，并确保：

- 同 key 同 hash 返回 existing。
- 同 key 不同 hash 抛 `RuntimeInboxConflict`，API 层映射 409。
- `device_event` canonical/alias 仍同步 claim `IdempotencyKey`。

Run:

```bash
rtk uv run pytest tests/runtime/orchestration/test_runtime_inbox_phase3_service.py -q
```

Expected:

- RuntimeInbox 服务测试通过。

- [x] **Step 5: 改 callback orchestration 热路径**

调整 `CallbackOrchestrationService.process_result/process_event/process_external`：

- 入站先写 RuntimeInbox。
- ACK 语义来自 RuntimeInbox accept result。
- 旧 Workline inbox 仅通过 RuntimeInboxConsumer 或过渡 adapter 消费。
- duplicate 处理读取 RuntimeInbox existing record。

Run:

```bash
rtk uv run pytest tests/callback/test_callback_runtime_inbox_cutover.py tests/api/test_callback_result_api.py tests/api/test_callback_event_api.py tests/api/test_callback_external_api.py -q
```

Expected:

- callback API 与 cutover 合同通过。

- [x] **Step 6: 更新观测合同**

若 callback 入站 metric/span 名称变化，同步：

- `docs/contracts/observability-contract.md`
- `tests/contracts/test_phase3_ops_contract_docs.py`
- `tests/runtime/orchestration/test_workline_inbox_observability.py`

Run:

```bash
rtk uv run pytest tests/contracts/test_phase3_ops_contract_docs.py tests/runtime/orchestration/test_workline_inbox_observability.py -q
```

Expected:

- observability 合同通过。

- [x] **Step 7: 提交**

Run:

```bash
rtk git add src/app/runtime/orchestration/consumers/callback_runtime_inbox_writer.py src/app/runtime/orchestration/services/runtime_inbox/runtime_inbox_service.py src/app/callback/services/callback_orchestration_service.py src/app/callback/services/callback_ingress_service.py src/app/runtime/orchestration/consumers/runtime_inbox_consumer.py tests/callback/test_callback_runtime_inbox_cutover.py tests/runtime/orchestration/test_runtime_inbox_phase3_service.py tests/api/test_callback_external_api.py tests/api/test_callback_event_api.py tests/api/test_callback_result_api.py docs/contracts/observability-contract.md tests/contracts/test_phase3_ops_contract_docs.py tests/runtime/orchestration/test_workline_inbox_observability.py
rtk git commit -m "refactor(callback): route inbound callbacks through RuntimeInbox"
```

Expected:

- commit 成功。

### Task 4: 关闭 CB late callback 与 `BLOCKED_BY_CB` 残留

**Files:**

- Modify: `src/app/wms_integration/state_machine.py`
- Modify: `src/app/wms_integration/services/fulfillment_lifecycle.py`
- Modify: `src/app/runtime/orchestration/services/reconciliation/runtime_reconciliation_service_impl.py`
- Modify: `tests/wms_integration/test_fulfillment_state_machine.py`
- Modify: `tests/runtime/orchestration/test_phase3_recovery_policies.py`

- [x] **Step 1: GitNexus 影响分析**

Run:

```bash
rtk node .gitnexus/run.cjs impact WmsFulfillmentStateMachine --direction upstream
rtk node .gitnexus/run.cjs impact WmsFulfillmentLifecycleService --direction upstream
rtk node .gitnexus/run.cjs impact RuntimeReconciliationService --direction upstream
```

Expected:

- 明确 fulfillment lifecycle、callback normalizer、runtime reconciliation 的影响。
- HIGH / CRITICAL 时先汇报。

- [x] **Step 2: 写失败测试**

扩展测试场景：

- CB open 时新出站 effect fast-fail 到 `BLOCKED_BY_CB`，不打 HTTP。
- CB half-open trial-in-progress 时第二个出站 effect 不打 HTTP。
- late callback 到达时写 RuntimeInbox/evidence，不把在途 fulfillment 改写为 `BLOCKED_BY_CB`。
- late callback 与 active projection 冲突时登记 RECONCILING。

Run:

```bash
rtk uv run pytest tests/wms_integration/test_fulfillment_state_machine.py tests/runtime/orchestration/test_phase3_recovery_policies.py -q
```

Expected:

- 新 late callback RuntimeInbox 断言先失败。

- [x] **Step 3: 修改 fulfillment lifecycle**

实现约定：

- 出站 CB 只影响新 effect dispatch。
- 入站 callback 不受 CB open/half-open 拦截。
- 恢复重试前必须查询 RuntimeInbox/evidence，避免重复发起已完成物理事实。
- 状态 owner 只根据 evidence 转移，不由 ReconciliationManager 直接写终态。

- [x] **Step 4: 验证**

Run:

```bash
rtk uv run pytest tests/wms_integration/test_fulfillment_state_machine.py tests/runtime/orchestration/test_phase3_recovery_policies.py tests/runtime/orchestration/test_runtime_inbox_phase3_service.py -q
```

Expected:

- 全部通过。

- [x] **Step 5: 提交**

Run:

```bash
rtk git add src/app/wms_integration/state_machine.py src/app/wms_integration/services/fulfillment_lifecycle.py src/app/runtime/orchestration/services/reconciliation/runtime_reconciliation_service_impl.py tests/wms_integration/test_fulfillment_state_machine.py tests/runtime/orchestration/test_phase3_recovery_policies.py
rtk git commit -m "fix(wms): keep late callbacks on RuntimeInbox during breaker recovery"
```

Expected:

- commit 成功。

### Task 5: 补齐 production-scale queue writer / benchmark evidence

**Files:**

- Modify: `tests/load/phase3_benchmark_scenarios.py`
- Modify: `tests/load/test_conveyor_queue_writer_benchmark.py`
- Modify: `tests/load/test_runtime_inbox_claim_benchmark.py`
- Modify: `tests/load/test_ecs_status_command_benchmark.py`
- Modify: `tests/load/test_plane_snapshot_benchmark.py`
- Modify: `scripts/run_phase3_runtime_benchmarks.py`
- Modify: `scripts/compose_phase3_runtime_benchmark_artifact.py`
- Modify: `tests/runtime/orchestration/test_phase3_benchmark_artifact_composer.py`
- Modify: `tests/integration/test_phase3_conveyor_queue_membership_concurrency.py`

- [x] **Step 1: 写 benchmark artifact 失败测试**

在 `tests/runtime/orchestration/test_phase3_benchmark_artifact_composer.py` 增加断言：

- production artifact 必须声明 PostgreSQL source for RuntimeInbox / queue writer。
- ECS scenario 必须声明 ECS HTTP source。
- PlaneSnapshot scenario 必须声明 API HTTP source。
- workload metadata 必须达到 §8.3 基线规模。

Run:

```bash
rtk uv run pytest tests/runtime/orchestration/test_phase3_benchmark_artifact_composer.py -q
```

Expected:

- 当前 lightweight fixture 冒充 production 时失败。

- [x] **Step 2: 固化四类 production scenario metadata**

更新 `tests/load/phase3_benchmark_scenarios.py`：

- RuntimeInbox claim: `pending=1000`, `workers=4`, source=`postgresql`
- Queue writer: `active_memberships=200`, `identity_collision=true`, source=`postgresql`
- ECS status command: source=`ecs-http`, includes status GET + command POST
- PlaneSnapshot: source=`api-http`, includes 1 WorkLine / 10 queue / 50 device / 100 session / 200 object

- [x] **Step 3: 更新 composer 与 CLI**

更新 `scripts/compose_phase3_runtime_benchmark_artifact.py`：

- 只接受四个 evidence JSON 输入。
- 校验 evidence 文件存在且内容 hash 与 artifact 一致。
- 拒绝 `local-lightweight`、`ci-lightweight`、`sandbox` 环境作为 production artifact。

Run:

```bash
rtk uv run pytest tests/load/ tests/runtime/orchestration/test_phase3_benchmark_artifact_composer.py -q
```

Expected:

- load 轻量测试仍可通过。
- production artifact 规则测试通过。

- [x] **Step 4: 记录可选 PostgreSQL 并发验证状态**

仅在本地 PostgreSQL/Redis 准备好时运行：

```bash
RUN_WORKLINE_INTEGRATION=1 ALLOW_SHARED_DEV_DB_INTEGRATION=1 INTEGRATION_DATABASE_URL=<local-postgres> INTEGRATION_REDIS_URL=<local-redis> rtk uv run pytest tests/integration/test_phase3_conveyor_queue_membership_concurrency.py -q
```

Expected:

- 1 passed。
- 若环境不可用，不把该命令作为开发/测试阻塞；production closure 前必须提供同等 evidence。

- [x] **Step 5: 提交**

Run:

```bash
rtk git add tests/load/phase3_benchmark_scenarios.py tests/load/test_conveyor_queue_writer_benchmark.py tests/load/test_runtime_inbox_claim_benchmark.py tests/load/test_ecs_status_command_benchmark.py tests/load/test_plane_snapshot_benchmark.py scripts/run_phase3_runtime_benchmarks.py scripts/compose_phase3_runtime_benchmark_artifact.py tests/runtime/orchestration/test_phase3_benchmark_artifact_composer.py tests/integration/test_phase3_conveyor_queue_membership_concurrency.py
rtk git commit -m "test(runtime): require production provenance for phase3 benchmarks"
```

Expected:

- commit 成功。

### Task 6: 补齐 Phase3 production P0 E2E closure artifact

**Files:**

- Modify: `scripts/compose_phase3_p0_e2e_artifact.py`
- Modify: `scripts/check_phase3_p0_e2e_gate.py`
- Modify: `scripts/check_phase3_closure_gate.py`
- Modify: `tests/runtime/orchestration/test_phase3_p0_e2e_artifact_composer.py`
- Modify: `tests/runtime/orchestration/test_phase3_closure_evidence_gate.py`
- Modify: `tests/resilience/test_phase3_integration_lab.py`
- Modify: `tests/resilience/fixtures/phase3_integration_lab_fixture.json`

- [x] **Step 1: 写 production closure 失败测试**

扩展测试：

- production profile 缺 P0 E2E artifact 时失败。
- P0 E2E artifact 缺真实 trace-query evidence 文件时失败。
- exception paths 缺 ECS timeout / WMS reject / callback out-of-order 时失败。
- 端到端 P95 >= 30s 时失败。

Run:

```bash
rtk uv run pytest tests/runtime/orchestration/test_phase3_p0_e2e_artifact_composer.py tests/runtime/orchestration/test_phase3_closure_evidence_gate.py -q
```

Expected:

- 新 production-only 断言先失败。

- [x] **Step 2: 更新 P0 E2E artifact composer**

实现约定：

- artifact 必须包含 WorkLine manifest、ExecutionSession、RuntimeInbox、RuntimeIntentLog、DeviceCommand、WMS fulfillment、PlaneSnapshot、RECONCILING evidence。
- artifact 的 `source.environment` 不能是 sandbox/lightweight。
- exception paths 必须覆盖 ECS timeout、WMS reject、callback out-of-order。
- evidence 文件必须存在并与 artifact 内嵌摘要一致。

- [x] **Step 3: 验证 mock 与 production gate 区分**

Run:

```bash
rtk uv run python scripts/check_phase3_closure_gate.py
rtk uv run pytest tests/runtime/orchestration/test_phase3_closure_evidence_gate.py -q
```

Expected:

- 无 artifact 时 mock closure 通过。
- production profile 测试只接受真实 artifact fixture。

- [x] **Step 4: 提交**

Run:

```bash
rtk git add scripts/compose_phase3_p0_e2e_artifact.py scripts/check_phase3_p0_e2e_gate.py scripts/check_phase3_closure_gate.py tests/runtime/orchestration/test_phase3_p0_e2e_artifact_composer.py tests/runtime/orchestration/test_phase3_closure_evidence_gate.py tests/resilience/test_phase3_integration_lab.py tests/resilience/fixtures/phase3_integration_lab_fixture.json
rtk git commit -m "test(runtime): require production p0 e2e closure evidence"
```

Expected:

- commit 成功。

### Task 7: 补齐 Phase4 production evidence profile

**Files:**

- Modify: `scripts/check_phase4_runtime_readiness_gate.py`
- Modify: `scripts/compose_phase4_runtime_evidence_artifact.py`
- Modify: `tests/contracts/test_phase4_runtime_readiness_gate.py`
- Modify: `tests/contracts/test_phase4_runtime_evidence_artifact_composer.py`
- Modify: `tests/workline_runtime/test_sorter_inbound_runtime_service.py`
- Modify: `tests/workline_runtime/test_smt_ng_wms_reconciliation_runtime_service.py`
- Modify: `docs/superpowers/specs/2026-07-03-phase4-design-with-residuals.md`

- [x] **Step 1: 写 production evidence 失败测试**

扩展测试：

- `--readiness-profile production` 缺 provider contract evidence 时失败。
- 缺 effect dispatch trace 时失败。
- 缺 RuntimeInbox worker trace 时失败。
- 缺 RuntimeHold/ReconciliationRecord trace 时失败。
- 缺 Phase3 production closure artifact 时失败。

Run:

```bash
rtk uv run pytest tests/contracts/test_phase4_runtime_readiness_gate.py tests/contracts/test_phase4_runtime_evidence_artifact_composer.py -q
```

Expected:

- 新 production-only 断言先失败。

- [x] **Step 2: 更新 evidence composer**

`compose_phase4_runtime_evidence_artifact.py` 必须输出：

- `provider_contracts.sorter_inbound`
- `provider_contracts.smt_ng_wms_reconciliation`
- `effect_dispatch_trace`
- `callback_worker_trace`
- `runtime_hold_reconciliation_trace`
- `benchmark`

约定：

- 文件路径可以指向 `reports/`、CI artifact 或部署验收产物。
- 不把 evidence 文件提交到 git。
- composer 只校验引用、hash 和 profile，不伪造现场结果。

- [x] **Step 3: 验证 runtime capability 仍不分支 provider**

Run:

```bash
rtk uv run pytest tests/workline_runtime/test_sorter_inbound_runtime_service.py tests/workline_runtime/test_smt_ng_wms_reconciliation_runtime_service.py -q
```

Expected:

- runtime path builder 只输出 provider contract / RuntimeIntent / RuntimeInbox evidence plan，不根据 MOCK/sandbox/site 写业务分支。

- [x] **Step 4: 更新 Phase4 residual 文档**

更新 `docs/superpowers/specs/2026-07-03-phase4-design-with-residuals.md`：

- 开发/测试 mock readiness 已闭合。
- production evidence profile gate 已具备严格校验。
- 业务承载 legacy 删除仍等待真实 production evidence。

Run:

```bash
rtk rg -n "production evidence profile|业务承载 legacy|Phase3 production closure" docs/superpowers/specs/2026-07-03-phase4-design-with-residuals.md
```

Expected:

- 文档能查到上述口径。

- [x] **Step 5: 提交**

Run:

```bash
rtk git add scripts/check_phase4_runtime_readiness_gate.py scripts/compose_phase4_runtime_evidence_artifact.py tests/contracts/test_phase4_runtime_readiness_gate.py tests/contracts/test_phase4_runtime_evidence_artifact_composer.py tests/workline_runtime/test_sorter_inbound_runtime_service.py tests/workline_runtime/test_smt_ng_wms_reconciliation_runtime_service.py docs/superpowers/specs/2026-07-03-phase4-design-with-residuals.md
rtk git commit -m "test(phase4): require production evidence profile"
```

Expected:

- commit 成功。

### Task 8: 新增 Phase5 readiness gate，防止过早删除

**Files:**

- Create: `scripts/check_phase5_readiness_gate.py`
- Create: `tests/contracts/test_phase5_readiness_gate.py`
- Modify: `./scripts/git-quality-gate.sh`
- Modify: `docs/architecture/workline-and-plugin-restructuring.md`
- Modify: `docs/architecture/legacy-cleanup-matrix.md`

- [x] **Step 1: 写失败测试**

新增 `tests/contracts/test_phase5_readiness_gate.py`：

- technical lane 需要 Phase2 owner guardrail、RuntimeInbox cutover、Phase3 mock closure、行为合同全绿。
- business lane 需要 Phase3 production closure、Phase4 production evidence profile。
- `WorkLine.runtime_status` 仍被 WorkLine 域直接写入时两个 lane 均失败。
- callback 热路径未切 RuntimeInbox 时两个 lane 均失败。

Run:

```bash
rtk uv run pytest tests/contracts/test_phase5_readiness_gate.py -q
```

Expected:

- 当前无脚本时失败。

- [x] **Step 2: 实现 readiness gate**

`scripts/check_phase5_readiness_gate.py` 支持：

- `--lane technical`
- `--lane business`
- `--phase3-p0-e2e-artifact`
- `--phase3-benchmark-artifact`
- `--phase4-evidence-artifact`

输出约定：

- 成功：`Phase 5 readiness passed: lane=<lane>`
- 失败：`Phase 5 readiness failed: <REASON>`

失败 reason 必须覆盖：

- `PHASE2_RUNTIME_STATUS_OWNER_OPEN`
- `RUNTIME_INBOX_CUTOVER_OPEN`
- `MISSING_PHASE3_PRODUCTION_CLOSURE`
- `MISSING_PHASE4_PRODUCTION_EVIDENCE`
- `PHASE5_BUSINESS_CONTRACTS_OPEN`
- `LEGACY_MATRIX_BUSINESS_ITEMS_OPEN`

- [x] **Step 3: 接入 quality profile**

更新 `scripts/git-quality-gate.sh`：

- `quality` profile 默认跑 technical lane。
- business lane 不默认跑，供发布/删除业务承载 legacy 前显式运行。

Run:

```bash
rtk ./scripts/git-quality-gate.sh --check phase5-readiness
rtk uv run pytest tests/contracts/test_phase5_readiness_gate.py -q
```

Expected:

- technical lane 在前序任务完成后通过。
- business lane 不默认执行；显式运行时必须在 Phase3/Phase4 production evidence 之后继续验证 Phase4 capability / port / contract tests。

- [x] **Step 4: 更新主设计文档**

更新 `docs/architecture/workline-and-plugin-restructuring.md`：

- Phase2 owner 收敛状态。
- Phase3 mock / production closure 的边界。
- Phase4 production evidence gate 状态。
- Phase5 启动条件改为引用 readiness gate。

- [x] **Step 5: 提交**

Run:

```bash
rtk git add scripts/check_phase5_readiness_gate.py tests/contracts/test_phase5_readiness_gate.py scripts/git-quality-gate.sh docs/architecture/workline-and-plugin-restructuring.md docs/architecture/legacy-cleanup-matrix.md
rtk git commit -m "chore(phase5): add readiness gate for legacy cleanup"
```

Expected:

- commit 成功。

### Task 9: 全量验证与验收报告

**Files:**

- Modify: `docs/architecture/workline-and-plugin-restructuring.md`
- Modify: `docs/architecture/legacy-cleanup-matrix.md`

- [x] **Step 1: 运行 Phase1/2 定向验收**

Run:

```bash
rtk uv run pytest tests/architecture/test_wms_7_ports_contract.py tests/architecture/test_runtime_capability_context_routing.py tests/architecture/test_ri3c_inbound_normalizer_port_guardrail.py tests/architecture/test_ri3_capability_injection_guardrail.py tests/architecture/test_c1_wms_import_guardrail.py tests/architecture/test_c2_cross_domain_fk_guardrail.py tests/architecture/test_c3_authority_metadata_guardrail.py tests/architecture/test_c4_device_command_fields_guardrail.py tests/architecture/test_c5_runtime_inbox_state_machine.py tests/architecture/test_phase2_runtime_status_owner_guardrail.py tests/contracts/workline tests/characterization/workline_legacy -q
```

Expected:

- 全部通过；允许既有 strict xfail 继续存在，但不能新增未解释 xfail。

- [x] **Step 2: 运行 Phase3 定向验收**

Run:

```bash
rtk uv run pytest tests/runtime/orchestration/test_phase3_p0_closure_contract.py tests/runtime/orchestration/test_phase3_p0_e2e_artifact_composer.py tests/runtime/orchestration/test_phase3_closure_evidence_gate.py tests/runtime/orchestration/test_phase3_benchmark_artifact_composer.py tests/runtime/orchestration/test_phase3_operational_contracts.py tests/runtime/orchestration/test_phase3_recovery_policies.py tests/runtime/orchestration/test_idempotency_phase3_audit.py tests/runtime/orchestration/test_runtime_inbox_phase3_service.py tests/reconciliation/test_reconciliation_manager_contract.py tests/active_objects/test_active_object_registry.py tests/workline/test_manifest_activation_validator_phase3.py tests/workline/test_plane_read_model_phase3.py tests/wms_integration/test_fulfillment_state_machine.py tests/wms_integration/test_callback_normalizer.py tests/api/test_callback_external_api.py tests/api/test_callback_result_api.py tests/api/test_callback_event_api.py tests/resilience/test_phase3_scenario_replay.py tests/resilience/test_phase3_integration_lab.py tests/load/ tests/contracts/test_phase3_ops_contract_docs.py -q
```

Expected:

- 全部通过。
- `scripts/check_phase3_closure_gate.py` 无 artifact 时仍显示 mock closure passed。

- [x] **Step 3: 运行 Phase4 定向验收**

Run:

```bash
rtk uv run pytest tests/contracts/test_phase4_design_docs.py tests/contracts/test_phase4_runtime_readiness_gate.py tests/contracts/test_phase4_runtime_evidence_artifact_composer.py tests/mock/material_flow/test_sorter_inbound_mock_contracts.py tests/mock/material_flow/test_wave2_wave3_mock_acceptance.py tests/workline_runtime/test_bin_cell_reservation_target_lifecycle.py tests/workline_runtime/test_runtime_location_event_service.py tests/workline_runtime/test_material_location_query_service.py tests/workline_runtime/test_workline_active_objects_service.py tests/workline_runtime/test_sorter_inbound_preview_service.py tests/workline_runtime/test_sorter_inbound_runtime_service.py tests/workline_runtime/test_smt_ng_wms_reconciliation_preview_service.py tests/workline_runtime/test_smt_ng_wms_reconciliation_runtime_service.py tests/api/test_phase4_read_model_routes.py tests/migrations/test_phase4_runtime_location_reservation_migration.py -q
```

Expected:

- 全部通过。

- [x] **Step 4: 运行 quality profile**

Run:

```bash
rtk ./scripts/git-quality-gate.sh --profile quality
```

Expected:

- ruff format/check 通过。
- bandit 无 issue。
- runtime toggle gate 通过。
- Phase4 readiness development-mock 通过。
- import-linter contract kept。
- architecture guardrails 0 violations / 0 warnings。
- test suite topology 通过。
- Phase5 technical readiness 通过。

- [x] **Step 5: GitNexus detect changes**

Run:

```bash
rtk node .gitnexus/run.cjs detect-changes --scope all
```

Expected:

- risk level 不高于 medium。
- affected processes 与 runtime/callback/wms/phase gate 范围一致。
- 如果 HIGH / CRITICAL，补充风险说明并暂停提交或发布。

- [x] **Step 6: 更新验收报告**

在主设计文档中记录：

- Phase2: `WorkLine.runtime_status` 已迁出或已明确为 runtime-owned compatibility projection。
- Phase3: development/mock closure 通过；production closure 仍需真实 artifact，或若已提供则标记通过。
- Phase4: development/mock readiness 通过；production evidence profile 仍需真实 artifact，或若已提供则标记通过。
- Phase5: technical lane 是否可启动；business lane 是否仍被 production evidence 阻塞。

- [x] **Step 7: 提交**

Run:

```bash
rtk git add docs/architecture/workline-and-plugin-restructuring.md docs/architecture/legacy-cleanup-matrix.md
rtk git commit -m "docs(workline): update residual closure status"
```

Expected:

- commit 成功。

## 验收标准

### 开发/测试验收

- `tests/architecture/test_phase2_runtime_status_owner_guardrail.py` 通过。
- callback result/event/external 热路径先写 RuntimeInbox，cutover 合同测试通过。
- CB late callback 在 RuntimeInbox/evidence/reconciliation 路径闭环，不再被 `BLOCKED_BY_CB` 覆盖。
- Phase3 mock closure 通过。
- Phase4 development-mock readiness 通过。
- `./scripts/git-quality-gate.sh --profile quality` 通过。
- GitNexus detect-changes 风险符合预期。

### Phase5 technical lane 启动验收

- Phase2 owner guardrail 通过。
- RuntimeInbox callback cutover gate 通过。
- Phase3 mock closure 与行为合同全绿。
- Legacy cleanup matrix 中无业务语义的技术残留可以按 Phase5 技术 lane 删除。

### Phase5 business lane 启动验收

- Phase3 production closure 通过，包含真实 P0 E2E artifact 与 production-scale benchmark artifact。
- Phase4 production evidence profile 通过。
- 对应 Phase4 capability / port / contract tests 全绿。
- legacy cleanup matrix 中业务承载项逐项有 evidence 和删除前置条件。

当前状态（2026-07-07 同步）：business lane 不再停留在 `blocked-until-production-evidence`；Phase3/Phase4 production evidence 与 Phase5 business cleanup 已在后续计划中闭合。仍不得仅凭 mock closure、lightweight benchmark 或本地 contract tests 删除新的业务承载 legacy；任何后续 schema/data destructive cleanup 仍需独立计划和 production evidence。

## 风险与应对

| 风险 | 影响 | 应对 |
| --- | --- | --- |
| Runtime status 收敛触碰 safety / dispatch ACK HIGH 风险路径 | 可能影响急停、恢复、准入和 outbox 释放 | 每个任务前跑 GitNexus impact；先加 owner guardrail，再改实现；HIGH/CRITICAL 必须先汇报 |
| RuntimeInbox cutover 一次性替换旧 processor | callback ACK 成功但 worker 未消费，或重复处理 | 采用 RuntimeInbox primary + 过渡 consumer 委托；先保证 ACK/幂等/审计，再迁 worker |
| Production artifact 被 lightweight 数据冒充 | Phase3/4 生产门禁失真 | gate 明确拒绝 lightweight/sandbox environment，强制 evidence 文件存在且内容一致 |
| Phase4 runtime capability 混入 provider-specific 分支 | 后续现场接入变成多套业务路径 | runtime service 只输出 provider contract / RuntimeIntent / RuntimeInbox evidence plan；provider 差异留在 adapter/profile |
| Phase5 提前删除业务承载 legacy | 丢失尚未 production 验证的业务语义 | 新增 Phase5 readiness gate，区分 technical lane 与 business lane |

## Self-Review

Spec coverage:

- Phase1 residual：保留 regression gate，不新增工作项。
- Phase2 residual：Task 1、Task 2、Task 8 覆盖 `WorkLine.runtime_status` owner 与 Phase5 readiness。
- Phase3 residual：Task 3、Task 4、Task 5、Task 6 覆盖 RuntimeInbox cutover、CB late callback、queue writer PostgreSQL 语义、P0 E2E / benchmark production artifact。
- Phase4 residual：Task 7、Task 8 覆盖 production evidence profile 与业务承载 legacy 删除前置。

Placeholder scan:

- 本计划不使用未定义占位符。
- 因仓库 `AGENTS.md` 明确禁止规划文档粘贴完整类/函数/大段测试代码，本计划使用接口名、测试场景、文件职责、验证命令和通过标准，不粘贴完整实现。

Type/name consistency:

- 统一使用 `RuntimeInboxService.accept_received()`、`WorkLineRuntimeStatusProjectionService`、`CallbackOrchestrationService`、`RuntimePhase3ClosureGate`、`Phase4 runtime readiness gate`。
- `BLOCKED_BY_CB` 只用于 WMS fulfillment 出站阻塞状态，不作为 callback 入站证据状态。

## 执行建议

建议执行顺序：

1. Task 1-2 先关闭 Phase2 owner 残留。
2. Task 3-4 再关闭 callback / RuntimeInbox / CB late callback cutover。
3. Task 5-7 补齐 production evidence 与 Phase4 profile。
4. Task 8-9 增加 Phase5 readiness gate 并更新验收状态。

执行方式推荐使用 subagent-driven-development：每个 Task 一个新 subagent，主线程在每个 Task 后 review diff、跑对应验证命令，再进入下一 Task。
