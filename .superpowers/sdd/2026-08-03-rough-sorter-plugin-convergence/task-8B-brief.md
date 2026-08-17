# Task 8B 实施 Brief

- Worktree: `/Users/kaizhou/codeDev/wes_backend-worktrees/codex-phase8-rough-sorter`
- BASE: `12d8cbb4`
- Scope: 只实施下文 `Task 8B`；`Task 8A` 已完成，`Task 8C` 仅作边界上下文。
- Delivery: Task 8B 必须独立提交；不得 amend Task 8A，不得 Push。
- External state: 主工作区已被其它任务占用；禁止使用或修改 `/Users/kaizhou/codeDev/wes_backend`。
- Required method: 读取并遵循 `$wes-implementation`、TDD、receiving/review/verification 边界；首个生产补丁前冻结影响清单并运行 GitNexus upstream impact。
- Main-Agent ownership: 真实 PostgreSQL migration chain、selected HEAVY、完整最终 Review 由主 Agent执行；实施者只跑聚焦 FAST、selector 和 HEAVY collect。
- Direct replacement: 不保留旧 reconciliation operation/class/handler/binding/alias，不迁移开发数据，不提前实现 8C 的 post-commit wake 或 deployment composition。

## Task 8B Execution Lock（2026-08-17）

- Frozen base/head: `12d8cbb4da8c9bb7dbf19a7cb88d6d765c9d463f`；唯一 worktree 为本 brief 顶部路径。
- 初始 dirty：仅 `.superpowers/` untracked；其中文件 SHA-256 已冻结，`task-8B-brief.md` 初始值为
  `a51ef39667abc0fdc74af3fdbbc5d3309275468487d1a8b19fef38da5f5e9cbc`。`AGENTS.md` / `CLAUDE.md` 初始值分别为
  `51b6af001f5abaca2e291d18def468288e57794e76395b6d6cbba14dd78a16eb` / `ac34b7937384873a303a53d3f85dcb79f97fcfbfe6dd1a31ad20d3c76955080d`；
  GitNexus 刷新造成的入口改写已精确恢复到该指纹。
- 变更分类：Transport/recovery/WMS follow-up/Celery 为可观察功能，严格 TDD；migration、HEAVY mapping 和 selector 为机器合同，做聚焦验证；
  brief/report/progress 为人类文档，不制造正文断言。
- 生产符号与调用点：`InboundEvidence` / `InboundEvidenceService.accept()` / `InboundEvidenceRepository`、
  `FactBuilder.build()`、`FactProcessor._prepare_facts_in_session()` 与失败转移、`WmsConfirmationService.complete()` /
  `_dispatch_claimed()`、`WmsInboundAdapter`、`InboundEventEvidenceRecorder.record()`、`InboundEventHandler.handle()`、
  SDK `ReconciliationResultReadyFact`、rough_sorter `ReconciliationDecidedFact` / handler / `build_handlers()`，以及 Celery
  app/config/task export。删除 binding 后必须同步 `src/app/execution/{__init__.py,composition.py}` 的直接消费者。
- 共享 identity / normalization：Transport 唯一身份固定为 `transport:{transport_task_id}:outcome:{outcome_version}`；WMS event 仍为
  `operation:operation_id`；recovery 因果身份固定为公开 `reconciling_evidence_id`；请求和 evidence digest 继续使用既有 canonical JSON，
  不新增通用 ledger、alias 或双路径。
- 冻结生产文件：原 Files 清单，加上直接必需的
  `packages/wes_plugin_sdk/src/wes_plugin_sdk/{facts.py,__init__.py}`、`src/app/execution/{__init__.py,composition.py}`、
  `src/app/execution/services/inbound_evidence_service.py`、`workline_plugins/rough_sorter/src/rough_sorter/__init__.py`；若旧符号残留扫描发现新的
  生产消费者，只允许做 direct-cutover 机械传播，出现新的业务语义或 HIGH/CRITICAL 影响必须暂停。
- 测试 / fixture owner：brief 所列 execution/WMS/plugin FAST 与两个 PostgreSQL owner；直接合同消费者还包括
  `tests/architecture/test_plugin_sdk_boundary_guardrail.py`、`tests/runtime/execution/test_inbound_evidence_service.py`、
  `tests/runtime/execution/test_rack_replacement_transport_binding.py`、`tests/api/test_qa_regression_transport_openapi.py`、
  `tests/api/test_wms_transport_events.py`、`workline_plugins/rough_sorter/tests/test_plugin_package.py`。不新增共享 fixture；现有 fake repository/session
  只按新端口机械传播。
- HEAVY / migration：更新 `docs/architecture/heavy-test-impact.toml` 和 selector 合同；SDK reviewed-NONE 内容指纹同步刷新；新 migration 必须由
  `uv run alembic revision -m "闭合粗分持久触发"` 生成，当前 head 为 `72ecc4fd560f`，只做 direct cutover，无开发数据迁移，
  `downgrade()` 明确不支持。实施者只 collect，不运行真实 PG/HEAVY。
- 验证冻结：逐切片 RED/GREEN；最终聚焦 core FAST、WMS contracts、plugin 私有测试、selector 合同、selected HEAVY collect、范围 Ruff/
  basedpyright、`uv run alembic heads`、旧符号零生产命中和 `git diff --check`。完整 QUALITY、真实 PG/migration chain、selected HEAVY 和独立 Review
  归主 Agent。
- GitNexus：已把索引刷新至 `12d8cbb4` 并批量调用上述计划内生产符号 upstream impact；MCP/LadybugDB 因数据库 v42 与 reader v40
  不兼容而全部返回 `UNKNOWN`。按仓库规则降级为精确 `rg` 调用点、测试 owner 和 diff；不得把该结果描述为成功的图谱风险证明。

### 已裁决的 contract gap

- SDK 中 `ReconciliationResultReadyFact` 直接替换为基础 `RecoveryDecidedFact`；不保留 alias/双导出，核心 `FactBuilder` 只产 SDK base Fact，
  绝不 import `rough_sorter`。
- 公开 recovery wire、normalized payload 与 OpenAPI 完全删除旧批量字段和 `resume_action` 语义。
- `authoritative_position` 单独不足以决定后续步骤。rough_sorter 具体 `RecoveryDecidedFact` 在 SDK base 上增加插件内部、非 wire、重命名的
  sealed typed continuation；该 continuation 必须由 Task 8C `PluginFactFactory` 基于 `reconciling_evidence_id` 对应的不可变 causal evidence 和
  snapshots 稳定构建。Task 8B 只定义/验证该 typed continuation 与位置、拓扑、causal identity；`ABORT` 禁止 continuation，`CONTINUE`
  必须携带。新 WMS operation identity 由未来 factory/continuation 稳定提供，不在核心或 handler 临时随机生成。

### Task 8B 实施裁决记录（2026-08-17）

- Transport 基础 evidence 使用 `TRANSPORT_RESULT + transport_task_id + outcome_version`；`FactBuilder` 只验证同 task 的低版本
  `UNKNOWN` causal evidence，不解释 `NEW_IN/OLD_OUT`。
- recovery 已直接替换为 SDK base `RecoveryDecidedFact` 与单 execution wire；rough_sorter 只增加内部
  `RecoveryWmsContinuation | RecoveryDeviceContinuation | RecoveryDeferContinuation`。`CONTINUE` 必须有 continuation，`ABORT`
  禁止；旧 operation、Fact、handler、binding、`resume_action` 无生产残留。
- WMS `WAIT` 的 response evidence、原 confirmation 完成与新 identity follow-up 位于同一事务；planner 使用原 confirmation 的
  `completed_at` 作为 `received_at`，从而稳定满足 `next_attempt_at = received_at + retry_after_ms`。非法 planner 结果或异常保留 evidence
  并把原 confirmation 置为 `RECONCILING`。
- Celery 仅新增固定 100 条扫描的 `dispatch_wms_confirmations_batch`，10 秒 Beat，路由 `wms-fulfillment`，无 ETA/countdown。
- migration 由规定命令生成随机 revision `5695afa99545`，直接增加 Transport 字段/约束/index 并删除批量 binding 表；无数据迁移，
  `downgrade()` 明确不支持。
- Task 8C 必须生产装配 `PluginFactFactory`：从 `reconciling_evidence_id` 对应的不可变 causal evidence 与 execution/position/topology
  snapshots 稳定构造插件 continuation，并提供新的 WMS operation identity；同时把 WMS adapter/planner/session 注入 dispatcher runtime。
  Task 8B 不提供临时随机 recovery identity，也不以位置单独推导下一动作。

### Task 8: 闭合持久触发并完成显式部署装配

Task 8 按获批 SPEC 固定为三个顺序提交；每个子任务都有独立 RED、GREEN、Review 和提交，不创建平行计划或 worktree。

#### Task 8A: 建立持久暂缓、真实失败计数与重启围栏

**Files:**

- Modify: `packages/wes_plugin_sdk/src/wes_plugin_sdk/decisions.py`
- Modify: `packages/wes_plugin_sdk/src/wes_plugin_sdk/__init__.py`
- Modify: `src/app/execution/repositories/inbound_evidence_repository.py`
- Modify: `src/app/execution/services/fact_processor.py`
- Modify: `src/app/workline/repositories/line_run_epoch_repository.py`
- Modify: `src/app/workline/services/line_run_epoch_service.py`
- Modify: `src/celery_app/tasks/execution.py`
- Test: `tests/architecture/test_plugin_sdk_boundary_guardrail.py`
- Test: `tests/runtime/execution/test_fact_processor.py`
- Test: `tests/runtime/execution/test_inbound_evidence_repository.py`
- Test: `tests/deployment/test_execution_worker_startup.py`
- Test: `tests/integration/execution/test_decision_processing_postgresql.py`
- Modify: `docs/architecture/heavy-test-impact.toml`
- Test: `tests/scripts/test_select_heavy_tests.py`

**Interfaces:**

- Consumes: `FactProcessor.process_batch(limit=100)`、`InboundEvidence.decision_next_attempt_at`、`LineRunEpochStatus.ACTIVE`。
- Produces: SDK `DeferExecution`；claim 只加 lease、不加失败次数；单 execution defer 原子释放 claim；worker 启动 Epoch 门禁。

- [ ] **Step 1: 写 defer 与领取顺序的失败测试**

  测试先锁定以下可观察结果；不通过新增通用 scheduler 或 mock 业务角色来达成：

  ```python
  decision = DeferExecution(material_execution_id="execution-1", fact_id="fact-1", reason_code="DEVICE_BUSY")
  assert decision.material_execution_id == "execution-1"

  await processor.process_batch()
  assert evidence.published_at is None
  assert evidence.decision_digest is None
  assert evidence.decision_attempt_count == 0
  assert evidence.decision_next_attempt_at == now
  assert execution.status == MaterialExecutionStatus.HOLD
  ```

  另加三个负例：`DeferExecution` 与其它 Decision 混合；一个 WMS_EVENT 关联多个 execution 后 defer；真实 handler 异常。前两项
  fail closed 且不按 defer 处理，第三项才把 `decision_attempt_count` 从 `0` 增到 `1`。

- [ ] **Step 2: 运行精确 RED**

  Run: `uv run pytest tests/architecture/test_plugin_sdk_boundary_guardrail.py tests/runtime/execution/test_fact_processor.py tests/runtime/execution/test_inbound_evidence_repository.py -q`

  Expected: 只因 SDK 尚无 `DeferExecution`、claim 仍递增 attempt、processor 仍写 digest/published、领取顺序未实现而失败。

- [ ] **Step 3: 实现最小 SDK 与 processor 语义**

  SDK 只增加以下不可变类型并加入既有 `Decision` union；不增加 context、retry time 或下一动作：

  ```python
  @dataclass(frozen=True, slots=True)
  class DeferExecution:
      material_execution_id: str
      fact_id: str
      reason_code: str
  ```

  `FactProcessor` 在生成 digest 前只接受单组、单项 `DeferExecution`，校验 fact/execution identity 后在同一 session 中执行：

  ```python
  evidence.decision_claim_token = None
  evidence.decision_claim_expires_at = None
  evidence.decision_next_attempt_at = now
  await execution_service.transition(..., target=MaterialExecutionStatus.HOLD, evidence_id=evidence.id)
  ```

  `claim_decision_batch()` 删除 claim 时的 attempt 自增；领取按 `decision_next_attempt_at IS NULL` 优先，再按
  `decision_next_attempt_at, received_at, id`。`_record_failure()` 是唯一 attempt 增量 owner，并继续使用既有有界退避/耗尽语义。

- [ ] **Step 4: 写并实现重启 Epoch 门禁**

  Repository 增加只读查询：

  ```python
  async def has_active_epoch(self, db: AsyncSession) -> bool: ...
  ```

  `LineRunEpochService.assert_execution_worker_startable(db)` 只在存在遗留 `ACTIVE` Epoch 时抛明确领域错误，不修改数据库。execution
  worker child 初始化调用该门禁；`claim_decision_batch()` 通过 join/exists 只领取关联 `ACTIVE` Epoch 的 evidence，`CLOSED` 永久不领。
  新 Epoch 激活仍由 Web/API 事务 owner 完成，不由 worker 自动创建或关闭。

- [ ] **Step 5: 运行 GREEN 与真实 PostgreSQL owner**

  Run: `uv run pytest tests/architecture/test_plugin_sdk_boundary_guardrail.py tests/runtime/execution/test_fact_processor.py tests/runtime/execution/test_inbound_evidence_repository.py tests/deployment/test_execution_worker_startup.py -q`

  Run: `uv run pytest tests/integration/execution/test_decision_processing_postgresql.py -q`

  Expected: 新 evidence 优先、超过 batch limit 的 defer 公平轮转、CLOSED Epoch 不领取、遗留 ACTIVE Epoch 启动失败均 PASS 且无 skip。

- [ ] **Step 6: Review、selector 与独立提交**

  更新精确 HEAVY mapping 后运行 selector 合同、Ruff、basedpyright 和 `git diff --check`；Review 确认核心未解释
  `DEVICE_BUSY`/release gate 等业务 reason。

  Commit: `feat(execution): 建立持久暂缓与重启围栏`

#### Task 8B: 直接替换 recovery 合同并闭合 Transport/WMS 持久生产者

**Files:**

- Modify: `src/app/execution/models/inbound_evidence.py`
- Delete: `src/app/execution/models/inbound_evidence_execution_binding.py`
- Modify: `src/app/execution/models/__init__.py`
- Modify: `src/app/execution/repositories/inbound_evidence_repository.py`
- Delete: `src/app/execution/repositories/inbound_evidence_execution_binding_repository.py`
- Modify: `src/app/execution/repositories/__init__.py`
- Modify: `src/app/execution/services/fact_builder.py`
- Modify: `src/app/execution/services/fact_processor.py`
- Modify: `src/app/execution/services/wms_confirmation_service.py`
- Modify: `src/app/wms_adapter/inbound_wire.py`
- Modify: `src/app/wms_adapter/inbound_adapter.py`
- Modify: `src/app/wms_adapter/inbound_event_handler.py`
- Modify: `src/app/wms_adapter/inbound_openapi.py`
- Modify: `src/app/wms_adapter/v1/events.py`
- Modify: `workline_plugins/rough_sorter/src/rough_sorter/facts.py`
- Delete: `workline_plugins/rough_sorter/src/rough_sorter/handlers/reconciliation_decided.py`
- Create: `workline_plugins/rough_sorter/src/rough_sorter/handlers/recovery_decided.py`
- Modify: `workline_plugins/rough_sorter/src/rough_sorter/handlers/__init__.py`
- Modify: `workline_plugins/rough_sorter/src/rough_sorter/plugin.py`
- Delete: `workline_plugins/rough_sorter/tests/test_transport_and_reconciliation.py`
- Create: `workline_plugins/rough_sorter/tests/test_transport_and_recovery.py`
- Create: `src/celery_app/tasks/wms_confirmation.py`
- Modify: `src/celery_app/tasks/__init__.py`
- Modify: `src/celery_app/app.py`
- Modify: `src/celery_app/config.py`
- Create via `uv run alembic revision -m "闭合粗分持久触发"`: generator 输出的随机 revision migration
- Test: `tests/runtime/execution/test_fact_builder.py`
- Test: `tests/runtime/execution/test_fact_processor.py`
- Test: `tests/runtime/execution/test_wms_confirmation_service.py`
- Test: `tests/contracts/wms_adapter/test_inbound_wire_acceptance.py`
- Test: `tests/contracts/wms_adapter/test_inbound_adapter.py`
- Test: `tests/contracts/wms_adapter/test_inbound_event_handler.py`
- Test: `tests/contracts/wms_adapter/test_inbound_openapi.py`
- Test: `tests/integration/execution/test_decision_processing_postgresql.py`
- Test: `tests/integration/wms_adapter/test_inbound_confirmation_postgresql.py`
- Modify: `docs/architecture/heavy-test-impact.toml`
- Test: `tests/scripts/test_select_heavy_tests.py`

**Interfaces:**

- Consumes: `TransportTask.outcome_version`、`RackReplacementTransportBinding`、`WmsConfirmationService.dispatch_batch()`、
  `MaterialExecution.last_transition_evidence_id`。
- Produces: `InboundEvidenceKind.TRANSPORT_RESULT`；单 execution `RecoveryDecidedFact`；typed business WAIT follow-up；独立 WMS dispatcher。

- [x] **Step 1: 写 Transport 与单对象 recovery 的失败合同**

  `InboundEvidence` 最终字段必须满足：

  ```python
  kind = InboundEvidenceKind.TRANSPORT_RESULT
  source_identity = f"transport:{transport_task_id}:outcome:{outcome_version}"
  transport_task_id = transport_task_id
  material_execution_id = execution.id
  ```

  recovery wire 直接替换为 `inbound.execution.recovery_decided@v1`，严格字段仅为
  `recovery_id/material_execution_id/material_trace_id/reconciling_evidence_id/decision/authoritative_position/reason_code`。删除批量数组、
  `ReconciliationData` 和多 execution binding 测试；测试必须先证明旧 operation、旧字段和 stale evidence 均被拒绝。

- [x] **Step 2: 运行 Transport/recovery RED**

  Run: `uv run pytest tests/runtime/execution/test_fact_builder.py tests/runtime/execution/test_fact_processor.py tests/contracts/wms_adapter/test_inbound_wire_acceptance.py tests/contracts/wms_adapter/test_inbound_event_handler.py tests/contracts/wms_adapter/test_inbound_openapi.py workline_plugins/rough_sorter/tests/test_transport_and_recovery.py -q`

  Expected: 因缺 `TRANSPORT_RESULT`、旧批量 recovery 仍存在、插件路由仍指向 reconciliation 而失败；不得通过保留 alias 使其转绿。

- [x] **Step 3: 实现 Transport evidence 与 causal fence**

  `InboundEvidence` 增加 nullable `transport_task_id`，但 `TRANSPORT_RESULT` 由 CheckConstraint 强制非空且 WMS/device identity 为空。
  `FactBuilder.build()` 构建 SDK `TransportResultReadyFact`；基础层不识别 `NEW_IN/OLD_OUT`。同一 task 更高确定版本只有在
  `execution.status == RECONCILING` 且 `last_transition_evidence_id` 指向该 task 较低 UNKNOWN evidence 时才可进入恢复 Fact；其它结果只
  持久化。插件 handler 继续负责 rack/position/face 比较和最终 Decision。

- [x] **Step 4: 实现单对象 recovery direct replacement**

  删除 `InboundEvidenceExecutionBinding` model/repository/export 及 FactProcessor 的多 Fact 展开。WMS ingress 在 ACK 前解析
  `reconciling_evidence_id`，锁定 execution 与 causal evidence，并冻结到单条 WMS_EVENT evidence。Fact 形态固定为：

  ```python
  @dataclass(frozen=True, slots=True)
  class RecoveryDecidedFact(FactReference):
      recovery_id: str
      decision: RecoveryDecision
      authoritative_position: DevicePosition | None
      reason_code: str
  ```

  应用前再次验证 execution 仍为 `RECONCILING` 且 `last_transition_evidence_id` 未变化；`CONTINUE` 用权威位置恢复，`ABORT` 关闭业务推进。
  旧 operation、旧类、旧 handler 和旧 binding 全仓零生产命中。

- [x] **Step 5: 写 WMS business WAIT follow-up RED**

  在 `tests/runtime/execution/test_wms_confirmation_service.py` 锁定：确定 `WAIT` 先保存 WMS_RESULT evidence，原 confirmation
  `COMPLETED`，同一事务创建新 `PENDING` confirmation；新 `operation_id` 不等于原值，`next_attempt_at = received_at + retry_after_ms`。
  未到期 `dispatch_batch()` 返回 `0`，到期只领取一次。技术投递未知仍复用原 operation identity。

- [x] **Step 6: 实现 typed follow-up 与独立 dispatcher**

  execution service 只依赖窄端口：

  ```python
  @dataclass(frozen=True, slots=True)
  class WmsBusinessWaitFollowUp:
      operation: str
      operation_id: str
      request_payload: dict[str, object]
      next_attempt_at: datetime

  class WmsBusinessWaitPlanner(Protocol):
      def plan(self, confirmation: WmsConfirmation, response_payload: dict[str, object]) -> WmsBusinessWaitFollowUp | None: ...
  ```

  planner 的粗分 operation/DTO 解释只位于 `src/app/wms_adapter/`。新增无业务载荷 Celery task
  `dispatch_wms_confirmations_batch(limit=100)`，固定路由 `wms-fulfillment`、Beat 10 秒；禁止 ETA/countdown，execution scanner 不调用 WMS。

- [x] **Step 7: 生成 direct-cutover migration 并验证 GREEN**

  migration 直接增加 transport identity/约束/index，删除批量 binding 表，不迁移开发数据，`downgrade()` 明确不支持。先运行聚焦 FAST，
  再在独占干净 PostgreSQL 上验证当前 base→head、metadata 一致、stale recovery CAS、Transport version fence、WAIT 原子事务。

  Run: `uv run pytest tests/contracts/wms_adapter tests/runtime/execution workline_plugins/rough_sorter/tests -q`

  Run: `uv run alembic heads`

  Run: `uv run pytest tests/integration/execution/test_decision_processing_postgresql.py tests/integration/wms_adapter/test_inbound_confirmation_postgresql.py -q`

- [x] **Step 8: 实施 owner 自审、selector 与独立提交**

  Review 必须确认不存在批量 recovery、compatibility alias、基础层 `NEW_IN/OLD_OUT` 分支或 WES 人工工单。selector 只选择实际 schema、WMS、
  Transport/execution PostgreSQL owner。

  Commit: `feat(execution): 闭合粗分持久触发`

#### Task 8C: 完成 post-commit 唤醒与静态部署装配

**Files:**

- Modify: `src/core/task_queue_gateway.py`
- Modify: `src/app/device/services/device_evidence_service.py`
- Modify: `src/app/execution/services/wms_confirmation_service.py`
- Modify: `src/app/wms_adapter/inbound_event_handler.py`
- Modify: `src/app/transport/service.py`
- Create: `deployment/__init__.py`
- Create: `deployment/rough_sorter_composition.py`
- Modify: `src/celery_app/async_runtime.py`
- Modify: `src/celery_app/app.py`
- Modify: `src/celery_app/config.py`
- Modify: `main.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `Dockerfile`
- Test: `tests/deployment/test_rough_sorter_plugin_startup.py`
- Test: `tests/deployment/test_celery_task_runtime_contract.py`
- Test: `tests/deployment/test_execution_worker_startup.py`
- Test: `tests/runtime/execution/test_wms_confirmation_service.py`
- Test: `tests/integration/execution/test_decision_processing_postgresql.py`
- Modify: `docs/architecture/heavy-test-impact.toml`
- Test: `tests/scripts/test_select_heavy_tests.py`

**Interfaces:**

- Consumes: Task 8A/8B 的两个无载荷扫描任务、Task 7 rough_sorter 静态 handlers、既有 `TaskQueueGateway`。
- Produces: Web/Celery 共用的单一 `RoughSorterComposition`；commit 后低延迟提示；10 秒 Beat 最终恢复；包含 SDK/plugin 的镜像。

- [ ] **Step 1: 写 post-commit 唤醒 RED**

  `TaskQueueGateway` 只增加两个无载荷方法：

  ```python
  def enqueue_execution_facts(self) -> None: ...
  def enqueue_wms_confirmations(self) -> None: ...
  ```

  测试分别证明 Device/WMS_RESULT/Transport material evidence 提交后只发送 execution scan，立即可派发的普通 confirmation 提交后只发送
  WMS scan，未来到期的 business WAIT follow-up 不即时发送；事务回滚不发送，enqueue 异常只记录结构化日志且不改变已提交事实。

- [ ] **Step 2: 运行唤醒 RED 并实现事务 owner 调用**

  Run: `uv run pytest tests/deployment/test_celery_task_runtime_contract.py tests/runtime/execution/test_wms_confirmation_service.py -q`

  Expected: 因 gateway 和 commit 后调用点不存在而失败。GREEN 时只能由真正执行 `commit`/session context 退出的应用服务调用 gateway，
  Repository、model、插件和 handler 不得导入 Celery。

- [ ] **Step 3: 写静态 Composition Root 与版本门禁 RED**

  `tests/deployment/test_rough_sorter_plugin_startup.py` 锁定：仅 `deployment/rough_sorter_composition.py` 可 import `rough_sorter`；未知
  `plugin_key`、版本漂移、Epoch digest 漂移或角色缺绑定时 fail closed；Web 和 Celery 获得同一不可变配置。核心
  `src/app/**` 对 `workline_plugins.*`/`rough_sorter` 零命中。

- [ ] **Step 4: 实现显式装配、workspace 与镜像**

  新 Composition Root 只暴露一个明确工厂，不扫描 entry point 或 filesystem：

  ```python
  def build_rough_sorter_runtime(*, session_factory: async_sessionmaker[AsyncSession]) -> ExecutionRuntime:
      return ExecutionRuntime(
          fact_processor=FactProcessor(...),
          wms_confirmation_service=WmsConfirmationService(...),
      )
  ```

  `pyproject.toml` workspace 只加入 `packages/wes_plugin_sdk` 与 `workline_plugins/rough_sorter`；Web/Celery 调同一工厂。Docker 在 `uv sync`
  前复制两个成员的 `pyproject.toml` 与包目录，镜像不依赖宿主 editable install。配置只使用现有非 secret profile，不新增动态插件目录。

- [ ] **Step 5: 运行部署 GREEN、镜像与最终门禁**

  Run: `uv sync --dev && uv lock --check`

  Run: `uv run pytest tests/deployment/test_rough_sorter_plugin_startup.py tests/deployment/test_celery_task_runtime_contract.py tests/deployment/test_execution_worker_startup.py -q`

  Run: `docker build -t wes-backend:phase8-rough-sorter .`

  Run: `./scripts/git-quality-gate.sh --profile quality`

  Run: `uv run scripts/select_heavy_tests.py --scope unstaged`

  Run: `./scripts/run_selected_heavy_local.sh --scope unstaged`

  Expected: Web/Celery 静态绑定一致；两个 Beat 分别恢复遗漏；插件与 SDK 在镜像内可导入；QUALITY、选中 HEAVY、干净 migration chain
  全绿且无跳过。插件 FAST、核心 FAST、PostgreSQL HEAVY 仍分别报告，不能互相代证。

- [ ] **Step 6: Fresh Review 与独立提交**

  Reviewer 核对当前完整 Task 8 diff：基础/业务边界、重启安全、WMS/RCS/ECS 权威、post-commit 时序、旧 recovery absence、测试所有权和
  HEAVY mapping。修复后只刷新失效证据。

  Commit: `feat(deployment): 显式装配粗分机插件`
