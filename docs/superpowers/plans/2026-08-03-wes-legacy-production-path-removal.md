# Phase 10 旧生产路径最终闭环清理实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Phase 9 最小执行基础和当前生产 successor 已真实交付后，通过一次 target-only 原子切换删除旧 Runtime/Intent/Outbox/Hold/Provider 生产路径，并只把零生产消费者的 schema identity 交给 Phase 11。

**Architecture:** Phase 10 不建设新的通用平台。先用现有清理矩阵和架构门禁冻结生产 owner、消费者、测试与部署闭包，完成 target-only 代码、发布四表静默门禁、Review 和不可变 candidate；首次切换时再关闭旧 admission/Beat，由仍运行的旧 worker 排空 legacy owner，取得 legacy stable zero 与四表连续 `READY` 后才停止旧 worker并激活 candidate。数据库表、字段、约束、索引与 revision chain 留给 Phase 11；Phase 10 只证明它们已经没有生产消费者。

**Tech Stack:** Python 3.13、FastAPI、SQLModel/SQLAlchemy、Celery、PostgreSQL、Redis、Docker Compose、Pytest、GitNexus、HEAVY selector。

**Specs:** `docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md`、`docs/superpowers/specs/2026-08-26-phase9-14-guided-development-resequence-design.md`

## Global Constraints

- 当前状态是 `GATED — PHASE 9 AND SUCCESSOR CLOSURE REQUIRED`。Task 0、Task 1 通过前不得修改 Phase 10 生产代码、删除旧 owner 或停用旧 consumer。
- Phase 9 必须先真实交付 SRS 已批准的 `BinExecution`、活动管辖期 `PositionProjection` 和本计划需要的最小 successor；不得用 `MaterialExecution`、旧 29-operation registry 或只有 schema、没有领域不变量与测试 owner 的空模型顶替。`BinExecution` 是核心执行对象，不代表人工或自动插件已经交付。
- Task 0 必须证明 WorkLine unfinished-work target aggregate、`ESTOP_PRESSED` final router、E03/E07 `WmsConfirmation` barrier、OpenTelemetry 同步 exporter 处置和当前仍需保留的 WMS consumer 已存在且有唯一 owner；任一 `UNRESOLVED` 非零即 `STOP`。
- `manual_bin_processing`、RETURN_BUFFER、人工 Task、自动上架、自动拣货及其 WMS 业务 wire 不属于 Phase 10 入口条件；尚无当前生产消费者的旧 operation 必须裁决为 `DELETE → NONE`，不得为 Phase 12/13 保留旧 Provider 路径。
- 最终目标对象包括 `LineRunEpoch`、`MaterialExecution`、`BinExecution`、`DeviceCommand`、`TransportTask`、`InboundEvidence`、`WmsConfirmation`、`WorklineSafetyIncident`、具体插件 Decision/Fact 和 typed WMS Adapter/Service；这不是排他清单，不得据此删除其它仍有独立业务语义的 owner。
- `DeviceCommand.RECONCILING`、`TransportTask.RECONCILING`、`InboundEvidence.RECONCILING`、`WmsConfirmation.RECONCILING`、`MaterialExecution.HOLD/RECONCILING`、插件 `RecoveryDecidedFact`、具体 claim/lease 与 `TransportResourceBinding` 都是目标能力，不得按词误删。
- 复用 `scripts/generate_legacy_matrix.py`、`docs/architecture/legacy-cleanup-matrix.csv`、现有 business absence gate、`tests/architecture/test_legacy_absence_guardrail.py`、`tests/architecture/test_outbound_http_boundary_guardrail.py` 和 `scripts/architecture-guardrails.sh`；不创建第二份 registry、ledger、scanner 或 phase-number absence gate。
- `business-legacy-absence-ledger.csv` 继续只拥有既有 `phase4_carrier=True` 语义。Phase 10 新条目写入现有 cleanup matrix 且 `phase4_carrier=False`，不得把 Phase 5 ledger 扩成另一份 Phase 10 owner 清单。
- `scripts/workline_inbox_retirement_guardrail.py` 和 `tests/architecture/test_workline_inbox_retirement_guardrail.py` 继续作为已退役 `WorklineInbox` predecessor 的唯一缺席 owner；`tests/architecture/test_legacy_absence_guardrail.py` 只承接本阶段待删 owner，不复制 predecessor 规则。两者均由现有 `scripts/architecture-guardrails.sh`/QUALITY 路由，不新增 scanner。
- Phase 10 是大型/高风险变更，代码行为采用内聚 RED → DEV → GREEN；计划、清单说明和当前态文档不走代码式 TDD。旧行为测试只能在 target successor 测试先通过后删除。
- 生产切换不双写、不双读、不保留 feature flag、fallback、alias、v2、shim、tombstone、空 facade 或旧 Provider Profile。生产代码可以按内聚切片实现和评审，但首次激活只允许一个 target-only candidate。
- 一次性 legacy drain 只读，不 resolve、release、cancel、claim、retry、resend、purge 或清理数据；歧义必须在原 `dispatch_key`、`operation_id`、`command_code`、`transport_task_id` 或 execution identity 上人工收敛。
- Phase 10 不删除或重写 table、column、constraint、index、revision 和 migration test。只允许 schema-deferred model 被 `migrations/env.py`、已有 revision 与 schema-only tests 精确引用；应用、API、Celery、Compose、脚本和行为测试引用必须为零。
- `src/app/runtime/orchestration/models/session.py:WorklineSession`、`src/app/runtime/orchestration/models/timeline.py:WorklineTimeline`、`src/app/runtime/orchestration/models/runtime_location_event.py:RuntimeLocationEvent`、`src/app/runtime/orchestration/services/inbox/object_transition_event_service.py:ObjectTransitionEventService`、`src/app/callback/models/callback_log.py`、`src/app/callback/repositories/callback_log_repository.py`、`src/app/callback/services/callback_log_service.py` 和 `src/app/callback/v1/callback_log.py` 明确 `RETAIN`；它们分别不同于 `src/app/runtime/orchestration/execution_session.py:ExecutionSession`、`src/app/runtime/orchestration/runtime_timeline.py:RuntimeTimeline`、generic RuntimeInbox 和已失去 route 的 external callback ingress。只有 Task 0 提供新的直接消费者证据并取得批准，才能改变这些分类。
- `docs/hardware/`、WMS inbound route、`WmsClient`、Phase 2 outbound HTTP、共享 Celery/Redis、`wms-fulfillment` queue、目标 Mock endpoint 和发布 artifact provider 不得因名称命中而删除。
- FULL 发布静默门禁不反向依赖旧表。`docs/superpowers/plans/2026-08-26-release-operational-readiness.md` 的 Tasks 2–4 必须作为独立高风险行为切片，在首次 Phase 10 cutover 前实现、验证并进入不可变 target-only candidate；不形成 legacy adapter 或双查询。
- Commit、Push、PR、Merge、Deploy 分别授权。本计划默认只实施和验证；没有 Deploy 授权时不得执行 Task 7 的现场切换。

---

## 冻结的目标边界

| 类别 | 处理范围 | 最终原则 |
| --- | --- | --- |
| `DELETE` | RuntimeInbox/ExecutionSession 通用运行时；RuntimeIntent/Effect/SystemCapability/SystemOutbox；generic Hold/Recovery/Reconciliation/Reservation；29-operation WMS Provider/Profile/Manifest/Catalog/query/effect/status lane；旧 task、配置、脚本和仅验证旧行为的测试 | successor 闭合后在 target-only candidate 中删除；candidate 只有在旧部署 producer seal、legacy drain 和四表 readiness 均通过后才激活，不保留兼容层 |
| `SWITCH` | WorkLine START/Safety/unfinished-work/query/trace/resource；ESTOP route；E03/E07 barrier；Transport/WmsConfirmation WMS client；Composition Root、Celery、Compose、Jenkins、当前态文档 | 只切到 Task 0 已证明存在的具体目标 owner，不在 Phase 10 发明 successor |
| `RETAIN` | 目标可靠对象、`WorklineSession`、`WorklineTimeline`、`RuntimeLocationEvent`、`ObjectTransitionEventService`、callback log、typed Adapter/Service、Phase 2 HTTP、bounded response、共享基础设施、明确插件、厂商原始资料 | 继续由原领域测试 owner 验收，不用旧测试替代；与 `ExecutionSession` / `RuntimeTimeline` 按完整路径区分 |
| `schema-deferred` | 旧表对应的 Python metadata identity、`migrations/env.py`、已有 revision 和 schema-only tests | 生产消费者归零后交给 Phase 11；Phase 10 不做 DDL |
| `UNRESOLVED` | 任一无法证明唯一 successor、真实 consumer、目标事务或存量 disposition 的条目 | 数量必须为 `0`；否则停在 Task 0/1 |

已知 schema-deferred 候选至少包含：`RuntimeInbox`、`ExecutionSession`、`ExecutionCorrelation`、`ExecutionWorkItem`、`RuntimeTimeline`、`RuntimeIntentLog`、`SystemOutbox`、两套 `RuntimeHold`、`NgReturnItem`、`ReconciliationCase` 和 `WorklineBinCellReservation`。Task 0 必须从当前 metadata/FK 重新生成完整身份清单；本段不是删除授权，也不能漏掉直接依赖旧 FK 的模型。

---

### Task 0: 证明 Phase 10 入口条件并冻结 Execution Lock

**Classification:** 只读实施前审计；失败即停止，不修改生产代码、不运行名义测试。

**Inspect:**

- `docs/superpowers/plans/2026-08-03-wes-architecture-convergence-master-plan.md`
- Phase 9 Minimum Execution Foundation 计划、实际交付包和退出证据
- `docs/architecture/northbound-wms-operation-inventory.csv`（Phase 9 当前机器 handoff；必须在 Phase 10 基线重新验证）
- `src/app/execution/`、`src/app/workline/`、`src/app/device/`、`src/app/transport/`、`src/app/wms_adapter/`
- `src/app/runtime/`、`src/app/sys/`、`src/app/wms_integration/`
- `src/register.py`、`src/celery_app/`、`docker-compose*.yml`、`Jenkinsfile*`
- `docs/architecture/heavy-test-impact.toml`、`tests/README.md`

**Interfaces:**

- Consumes: Phase 9 exit evidence、当前 branch/HEAD/dirty、当前代码/配置/测试/部署装配。
- Produces: `READY FOR PHASE 10 EXECUTION LOCK` 或带精确缺口的 `STOP`；冻结的 DELETE/SWITCH/RETAIN/schema-deferred manifest 和无关 dirty 指纹。

2026-08-29 的 Phase 9 独立 worktree 已生成 `UNRESOLVED=0` 的 operation inventory 与仓内验证证据，但尚未提交或合入
`develop`，也未完成 staged exit gate。因此这些材料只是 Task 0 的候选输入，不构成 `READY FOR PHASE 10 EXECUTION LOCK`。

- [ ] **Step 1: 固定当前 Git 与索引快照**

  Run: `git branch --show-current && git rev-parse HEAD && git status --short && git rev-list --left-right --count develop...HEAD`

  Run: `npx gitnexus status`

  Expected: 记录 branch、HEAD、dirty、develop ahead/behind 和 GitNexus freshness。索引 stale 时仅运行一次 `npx gitnexus analyze`，前后比较 `AGENTS.md`、`CLAUDE.md`；工具改写超范围且与用户变更重叠时停止。GitNexus 不可用则明确降级为精确 `rg`、调用点、测试 owner 和 HEAVY mapping。

- [ ] **Step 2: 验证 Phase 9 真实退出而不是计划存在**

  Run: `rg -n "class BinExecution|BinExecutionRepository|BinExecutionService" src workline_plugins tests`

  Run: `rg -n "manual_bin_processing|automatic_putaway|automatic_picking|RETURN_BUFFER" src workline_plugins tests --glob '*.py'`

  Expected: `BinExecution` 有 model、Repository、Service、活动管辖期位置投影、领域不变量、测试 owner 和精确 HEAVY mapping；它不依赖 Phase 12/13 插件才能成立。后置业务插件可以不存在；若旧 Provider/operation 仍为它们保留生产路径，则必须进入 `DELETE → NONE`。只有计划文字、migration 残留、fixture 或无领域行为的空包时立即 `STOP`。

- [ ] **Step 3: 逐项关闭五个 successor 阻断**

  对下列对象批量运行 GitNexus upstream impact，并用当前代码/测试复核：

  1. `WorkLineRepository.count_unfinished_workload/first_unfinished_workload` 已只读取 `LineRunEpoch + MaterialExecution + BinExecution + DeviceCommand + TransportTask + WmsConfirmation` 的最终聚合，且不存在持久化空窗；
  2. `ESTOP_PRESSED` 已由最终 device-event router 调用保留的 `WorkLineSafetyService.handle_estop()`，而不是只持久化 `InboundEvidence`；
  3. E03/E07 的 `confirm_inbound` 与 `notify_pkg_binding` 已由 `WmsConfirmation`/typed service 覆盖双义务、互斥、hold release、reconciliation 和锁序；
  4. `RuntimeOpenTelemetryHttpExporter` 已按获批决定移除同步 raw Client、切到唯一生命周期 owner，或明确删除该 exporter backend；
  5. 当前 operation consumer 表已经把 Transport submit、粗分确认和其它真实消费者裁决为具体 typed owner；人工分拣、自动上架、自动拣货等后置业务没有当前消费者时统一裁决为 `DELETE → NONE`。

  Expected: 五项均有唯一生产 owner、直接/间接测试 owner 和必要 HEAVY；不得以设计方向、旧测试绿灯或 29-operation registry 代替。

- [ ] **Step 4: 冻结生产与 schema 闭包**

  精确枚举 model/FK、Repository、Service、API route、permission/OpenAPI、task/include/Beat/route、queue、env key、Compose mount、Jenkins preflight、script、current-doc、直接/间接测试和 HEAVY mapping。对所有旧数据库 identity 记录唯一 schema/name 及允许引用者；对 Redis broker 记录 active/reserved/scheduled legacy task name。

  Expected: 每项都有 `DELETE`、`SWITCH`、`RETAIN` 或 `schema-deferred`，`UNRESOLVED=0`；无关 dirty 只记录路径/stat/hash，不读完整 diff。

- [ ] **Step 5: 冻结原子切换 owner**

  指定一个实施 owner 修改共享执行路径，一个 cutover owner 执行维护窗；Reviewer 只读。冻结 candidate 的 source/image/config digest、旧 producer seal 顺序、database/broker drain predicate、共享 worker 停启顺序和 rollback 边界。

  Expected: 所有清单在同一 base/head 上闭合，Phase 9 exit 证据有效。任何一项失败都输出 `STOP — PHASE 10 REMAINS GATED`，不得进入 Task 1。

### Task 1: 扩展现有清理真源并建立一次完整 RED

**Classification:** 高风险测试治理；复用现有 owner，不创建新 registry/scanner。

**Files:**

- Modify: `scripts/generate_legacy_matrix.py`
- Modify generated truth: `docs/architecture/legacy-cleanup-matrix.csv`
- Modify: `tests/architecture/test_cleanup_matrix_guardrail.py`
- Verify unchanged scope: `docs/architecture/business-legacy-absence-ledger.csv`
- Verify: `scripts/check_business_legacy_absence_gate.py`
- Modify: `tests/architecture/test_legacy_absence_guardrail.py`
- Verify retained predecessor owner: `scripts/workline_inbox_retirement_guardrail.py`
- Verify retained predecessor test: `tests/architecture/test_workline_inbox_retirement_guardrail.py`
- Modify: `tests/architecture/test_outbound_http_boundary_guardrail.py`
- Modify only for routing/remediation text required by the same owners: `scripts/architecture-guardrails.sh`
- Modify: `docs/architecture/heavy-test-impact.toml`

**Interfaces:**

- Consumes: Task 0 冻结 manifest。
- Produces: 现有 matrix 的 Phase 10 entries、target successor 测试和最终旧路径/HTTP 边界 RED；旧 Phase 5 ledger 语义保持不变。

- [ ] **Step 1: 把当前 legacy owner 纳入现有 matrix**

  扩展 `SCAN_DIRS`/精确 migrated-owner 配置，使 Task 0 的 runtime、sys、WMS、boot/deployment 路径进入 `parse_entries()`；复用现有字段记录 owner、semantics、strategy、target、drop phase、risk。Phase 10 条目固定 `phase4_carrier=False`，目标为具体 owner 或 `NONE`，不增加 `phase10-registry.csv`。

- [ ] **Step 2: 生成 CSV 并验证 Phase 5 ledger 未被污染**

  Run: `uv run python scripts/generate_legacy_matrix.py`

  Run: `uv run pytest tests/architecture/test_cleanup_matrix_guardrail.py tests/architecture/test_business_legacy_absence_ledger.py tests/architecture/test_business_legacy_absence_guardrail.py -q`

  Expected: CSV 与 generator 完全一致；既有 `phase4_carrier=True` entry set 不变，business final gate 继续通过；`bin_cell_reservation`/`station_lease` 的 Phase 10 当前 owner 以新条目裁决，不篡改旧路径已迁移的历史事实。

- [ ] **Step 3: 扩展 Phase 10 production absence owner**

  在 `test_legacy_absence_guardrail.py` 按 Task 0 manifest 分组加入本阶段旧 module/import/path/task/route/env/mount 精确断言；不复制 `WorklineInbox` predecessor token、allowlist 或 remediation。继续运行 `workline_inbox_retirement_guardrail.py` 及其测试，证明 predecessor owner 未漂移。schema-deferred allowlist 只允许 `migrations/env.py`、已有 revisions、冻结的 schema-only tests 和模型定义本身，不允许 package export 或应用 import。

- [ ] **Step 4: 收紧唯一 HTTP boundary owner**

  扩展现有 AST scanner，最终断言：`src` 的 `AsyncClient` constructor 恰为 `src/core/outbound_http/factory.py`；`src` 同步 `Client` constructor 为空；`scripts` direct Client constructor 为空；业务包 direct `httpx` import 为空；每个 WMS outbound 进程/事件循环最多一个 client；ECS transport 按 canonical endpoint 唯一复用。保留 `bounded_http_response.py` 的 TYPE_CHECKING 类型引用和测试 client 例外。

- [ ] **Step 5: 建立 target successor RED**

  在既有 Safety/START/Resource/Device/Transport/Execution/WmsConfirmation/WMS Adapter/Composition/Deployment 测试 owner 中加入 Task 0 冻结的外部可观察不变量；不得新建 generic Runtime/Hold/Provider 测试包。更新精确 HEAVY mapping，未知路径 fail closed。

- [ ] **Step 6: 运行一次 RED 批次**

  Run: `uv run pytest tests/workline/test_workline_start_service.py tests/api/test_workline_safety_operation_api.py tests/resource/test_resource_c0_projection_contract.py tests/workline_runtime/test_wms_sync_obligation_resolution.py tests/runtime/execution/test_wms_confirmation_service.py tests/contracts/wms_adapter/test_client.py tests/deployment/test_execution_worker_startup.py tests/deployment/test_wms_confirmation_dispatcher.py tests/architecture/test_legacy_absence_guardrail.py tests/architecture/test_outbound_http_boundary_guardrail.py -q`

  Run: `uv run scripts/select_heavy_tests.py --scope unstaged`

  Expected: target successor 缺口或旧生产引用使测试准确失败；matrix/business ledger 合同继续通过。环境未启用导致的 HEAVY skip 不是 RED。Phase 9 基础对象测试由其计划和 exit evidence 拥有，本任务只通过当前 HEAVY selector 补跑受 Phase 10 diff 实际影响的 owner。

### Task 2: 先切换仍在使用旧 owner 的具体消费者

**Classification:** DEV；只实现 Task 1 已冻结的 successor 行为，不删除旧 consumer。

**Files:**

- Modify: `src/app/workline/repositories/workline_repository.py`
- Modify: `src/app/workline/services/workline_start_service.py`
- Modify: `src/app/workline/services/safety_service.py`
- Modify: `src/app/workline/unit_of_work.py`
- Modify: `src/app/resource/services/projection_service.py`
- Modify: `src/app/resource/services/relation_service.py`
- Modify or delete after target response is proven: `src/app/runtime/orchestration/services/query/workline_active_objects_service.py`
- Modify or delete after target response is proven: `src/app/runtime/orchestration/services/query/runtime_query_service.py`
- Modify or delete after target response is proven: `src/app/runtime/orchestration/services/trace/trace_query_service.py`
- Modify: `src/app/workline/v1/operation.py`
- Modify: target device-event router frozen in Task 0
- Modify: target E03/E07 WmsConfirmation/typed service frozen in Task 0

**Interfaces:**

- Consumes: 已存在的 unfinished-work aggregate、ESTOP router、E03/E07 barrier 和 owner-specific recovery APIs。
- Produces: START/Safety/resource/query/trace 不再读写 RuntimeInbox/SystemOutbox/RuntimeHold/ExecutionSession/generic reconciliation；旧 consumer 仍可用于存量 drain，但不接收新业务。

- [ ] **Step 1: 切 WorkLine START 与 unfinished-work admission**

  保留 `LineRunEpoch` 激活、幂等与安全不变量；删除 parked SystemOutbox release/wake、`released_outbox_count`、RuntimeInbox/Hold/old reconciliation ports。stop/deactivate 只调用 Task 0 已证明无空窗的 target aggregate。

- [ ] **Step 2: 切 ESTOP 与 clear-estop transaction**

  最终 device-event router 调用现有 `WorkLineSafetyService.handle_estop()`；Safety 只维护 `WorklineSafetyIncident`、Epoch 与具体可靠对象状态。已发送 DeviceCommand/TransportTask 保持原身份收敛，不盲取消；clear 只清 incident/checklist 并等待新 START。

- [ ] **Step 3: 去除 Resource 的 generic Hold side effect**

  `ResourceProjectionResult.RECONCILING` 保持唯一资源冲突结果；移除 `runtime_hold_creator` 注入和 `runtime_hold` 返回包装。具有 execution identity 的调用方让对应 `MaterialExecution` 进入 `RECONCILING`，无 identity 时不伪造全线 Hold。

- [ ] **Step 4: 切 active-object/query/trace**

  保留的 API 只投影 `LineRunEpoch`、Material/BinExecution、DeviceCommand、TransportTask、WmsConfirmation、Safety incident 和具体 resource evidence；删除 `active_runtime_hold_ids`、generic `open_issue_count`、old session/outbox/station-lease wrapper。没有当前 API caller 的旧 query/trace owner直接列入 Task 5 DELETE。

- [ ] **Step 5: 切 E03/E07 双义务**

  用 Task 0 已证明的 `WmsConfirmation` identity/transaction/lock owner替换 `ExecutionWorkItem` mutex、RuntimeIntent/Hold/ReconciliationCase barrier；两个 confirmation 的创建、互斥、完成、拒绝、歧义和 execution 推进必须在原 execution identity 上闭合。

- [ ] **Step 6: 运行聚焦 GREEN**

  运行 Task 1 冻结的 Safety/START/Resource/query/trace/E03/E07 FAST 与 selector 选出的 PostgreSQL owner。Expected: target tests PASS；旧 producer/worker 尚未删除，Task 1 absence RED 仍允许失败。本任务不得提交半切换生产 candidate。

### Task 3: 将目标 WMS 与 HTTP 装配切出旧 Provider 平台

**Classification:** DEV；目标配置直接替换，不保留双 profile。

**Files:**

- Modify: `src/app/transport/composition.py`
- Modify: `src/app/wms_adapter/client.py` 及其现有 factory/composition owner
- Modify: `src/app/wms_adapter/inbound_auth.py`
- Modify: `src/register.py`
- Modify: `src/celery_app/async_runtime.py`
- Modify: `src/celery_app/app.py`
- Modify: `src/celery_app/config.py`
- Modify: `src/core/conf.py`
- Modify: `.env.dev`、`.env.test` 和生产环境模板
- Modify: `docker-compose.yml`、`docker-compose.deploy.yml`、`docker-compose.test-deploy.yml`、`docker-compose.frontend.yml`
- Modify: `Jenkinsfile.backend-ci`、`Jenkinsfile.test-deploy`
- Modify or delete according to Task 0 decision: Runtime OpenTelemetry HTTP exporter owner

**Interfaces:**

- Consumes: 批准的 `WMS_BASE_URL`、`TRANSPORT_SUBMIT_PATH`、auth=`NONE`、Task 0 逐 operation consumer 表和 OpenTelemetry decision。
- Produces: Transport/WmsConfirmation/获批插件共享的唯一 `WmsClient`；target-only API/worker/Beat/Compose candidate；生产 HTTP constructor 边界闭合。

- [ ] **Step 1: 建立最小 WMS composition**

  只用 `WMS_BASE_URL + TRANSPORT_SUBMIT_PATH` 构造 target `WmsClient`；同一实际 outbound 进程/事件循环由 Transport 与 WmsConfirmation 共享并由 composition root 关闭。没有 outbound caller 的进程不建立空闲 pool。

- [ ] **Step 2: 固定入站认证与 operation owner**

  `WmsInboundAuthPolicy` 只允许 isolated LAN + `NONE` 和 typed event admission；移除 `CompiledWmsProviderProfile`、HMAC enum/credential fallback。逐 operation 表中有真实 consumer 的能力留在对应 typed Adapter/plugin，没有 consumer 的能力保持 `DELETE → NONE`。

- [ ] **Step 3: 切 API/Celery lifecycle**

  `src/register.py`、Celery child、Beat 只装配 DeviceCommand、TransportTask、Execution、WmsConfirmation、明确插件和 WMS inbound handler；保留 `wms-fulfillment` worker/queue，移除 Provider catalog、query/effect/status runtime 和旧 process role。

- [ ] **Step 4: 切 Compose/Jenkins/config provenance**

  删除 `WMS_PROVIDER_PROFILE_FILE`、host file、profile mount、`WMS_EFFECT_*`、HMAC secrets 和 credential registry keys；Jenkins 以最小 target config digest、task/queue/client readiness 替代 provider profile digest/attestation。保留 target Mock endpoint 和发布兼容 provenance。

- [ ] **Step 5: 执行 OpenTelemetry 已批准裁决**

  若 Task 0 决定保留 exporter，则使用批准的唯一生命周期 owner且不再直接构造同步 Client；若决定删除 backend，则同时删除其配置、注册、测试和 current observability reference。不得把异步 transport 静默塞给同步线程模型。

- [ ] **Step 6: 运行聚焦 GREEN**

  运行 target WMS client/adapter、Transport/WmsConfirmation、worker startup、Compose/Jenkins contract 和 outbound HTTP boundary 测试。Expected: target candidate 不依赖 Provider Profile；旧 generic runtime 尚待 Task 5 删除，最终 absence 仍未宣称通过。

### Task 4: 冻结只读 drain 谓词与 cutover manifest

**Classification:** 只读演练与运行准备；不进入真实维护窗，不修改现场数据或进程。

**Inspect/Rehearse:**

- RuntimeInbox、RuntimeIntentLog、SystemOutbox、两套 RuntimeHold、NgReturnItem、ReconciliationCase、WorklineBinCellReservation 表
- Celery active/reserved/scheduled task 与 Beat schedule
- `src/app/workline/v1/operation.py` 的旧 sandbox/replay/reconciliation routes
- orphan callback RuntimeInbox writer/service

**Interfaces:**

- Consumes: Task 0 冻结的 producer manifest 和原身份人工处置规则。
- Produces: 经演练的同一 snapshot 查询、broker inspection 命令、零值判定、人工 disposition 和 cutover manifest；不声称现场已 drain，也不进入长期 release readiness。

- [ ] **Step 1: 冻结 producer seal manifest**

  精确列出旧 sandbox external callback、RuntimeInbox replay、generic reconciliation resolve、SystemCapability/RuntimeIntent create、station lease outbox writer、generic TaskQueueGateway wake、直接 Redis/Celery producer 和 task name。这里只冻结 Task 5 candidate 要删除的 producer，不部署、不关闭 route、不停止 Beat。

- [ ] **Step 2: 冻结数据库同快照谓词**

  只读查询必须同时返回：RuntimeInbox 的可处理、lease、dead-letter；RuntimeIntentLog 的 active/ambiguous；SystemOutbox 的 active/ambiguous、unmatched pair、identity/digest conflict 和 `SENT + ACCEPTED`；两套 RuntimeHold active blocker；NgReturnItem active；ReconciliationCase open；BinCellReservation active。查询不 lock、不 claim、不写审计、不 commit。

- [ ] **Step 3: 冻结 broker 与连续稳定规则**

  记录 Celery active/reserved/scheduled legacy task inspection、producer freeze 时间点、两次复核间隔和“期间无新增旧 row”规则。不得设计 queue purge；共享 `celery`、`device-command`、`wms-fulfillment` 消息按 task identity 观察。

- [ ] **Step 4: 演练查询和失败路径**

  在隔离测试数据库或批准的只读副本演练查询输出、查询失败、非零、identity conflict 和 broker inspection 失败。任一歧义只生成原 identity 人工调查项；不自动 resolve、cancel、retry、resend 或清理。

- [ ] **Step 5: 冻结 Task 7 cutover manifest**

  manifest 必须写明旧 deployment 的 Nginx/API/Beat/worker 精确 service、candidate-container 的四表 readiness 命令、legacy 查询、broker inspection、连续 READY/zero 次数、不可变 candidate digest 输入和每个失败点的维护态处理。Expected: 只形成可执行清单；真实 admission stop、drain 和 worker stop 全部留给 Task 7。

### Task 5: 在同一 target-only candidate 删除旧生产 owner

**Classification:** DEV；与 Tasks 2/3 同一 target-only candidate，按 FK 叶到根移除应用 owner，不做 DDL。

**Files:**

- Delete/adapt: `src/app/runtime/orchestration/services/runtime_inbox/`、旧 inbox/callback/query/trace/snapshot owners
- Delete/adapt: RuntimeIntent/Effect/SystemCapability、RuntimeHold/Recovery/Reconciliation/Reservation services/repositories/contracts/exports
- Delete/adapt: `src/app/sys/` 的 SystemOutbox model 之外生产 owner、dispatcher、binding、credentials 与 evidence runtime
- Delete/adapt: `src/app/wms_integration/` 的 Provider/Profile/Manifest/Catalog/query/effect/status/conformance owners
- Delete/adapt: `src/celery_app/tasks/runtime_inbox.py`、旧 `tasks/sys.py`/`tasks/workline.py` 分支、include/Beat/routes/gateway constants
- Delete/adapt: old API/DTO/permission/OpenAPI、settings、Compose/Jenkins wiring、scripts、current docs
- Delete/adapt: 旧行为 tests/support/fixtures 和 `docs/architecture/heavy-test-impact.toml`
- Retain schema-only: Task 0 冻结的 model identities、`migrations/env.py`、已有 revisions 与 schema-only tests

**Interfaces:**

- Consumes: target consumer GREEN、Task 4 producer seal/drain/cutover manifest；不消费尚未发生的现场 drain 结果。
- Produces: 生产/API/Celery/Compose/script/current-doc 只装配 target owners；旧 schema 仅有精确 schema-deferred 引用。

- [ ] **Step 1: 删除 RuntimeInbox/ExecutionSession 应用闭包**

  删除 repository/service/processor/contracts、orphan external callback RuntimeInbox writer/service、旧 WMS inbox handler、sandbox/replay/query/snapshot、task/include/Beat/gateway/config/CI acceptance。去除 `src/app/runtime/orchestration/execution_session.py:ExecutionSession`、`execution_correlation.py:ExecutionCorrelation`、`execution_work_item.py:ExecutionWorkItem` 和 `runtime_timeline.py:RuntimeTimeline` 的应用 export、Repository 与 FK consumer；schema identity 不从 metadata 删除。

  明确保留 `src/app/runtime/orchestration/models/session.py:WorklineSession`、`src/app/runtime/orchestration/models/timeline.py:WorklineTimeline`、`src/app/runtime/orchestration/models/runtime_location_event.py:RuntimeLocationEvent`、其 repository/service/query owner，以及 `src/app/runtime/orchestration/services/inbox/object_transition_event_service.py:ObjectTransitionEventService`；它们不属于 `execution_session.py:ExecutionSession` 或 `runtime_timeline.py:RuntimeTimeline` 删除集。

- [ ] **Step 2: 删除 Intent/Effect/SystemCapability/SystemOutbox 应用闭包**

  删除 generic intent/effect/reducer/reconciliation、29-operation capability registry/generated index、outbox repository/engine/dispatch attempts/binding/credentials、三个 generic dispatcher、status scanner/callback 和 WorkLine generic projection。目标 Device/Transport/WmsConfirmation 自有状态机不受影响。

  保留 `src/app/callback/models/callback_log.py`、`src/app/callback/repositories/callback_log_repository.py`、`src/app/callback/services/callback_log_service.py` 和 `src/app/callback/v1/callback_log.py`；删除的是已失去 route 的 external callback ingress/RuntimeInbox writer，不是 callback log 查询能力。

- [ ] **Step 3: 删除 generic Hold/Recovery/Reconciliation/Reservation 应用闭包**

  删除 Hold CRUD/release/query/barrier、NG return API、generic reconciliation manager/case consumers、bin-cell reservation/station lease production owner和旧 DTO/routes。保留 Safety incident、MaterialExecution Wait/Pause、plugin recovery、具体 claim lease 与 TransportResourceBinding。

- [ ] **Step 4: 删除 Provider/Profile/Manifest 和无依据认证**

  删除 compiler/startup/readiness/catalog/attestation、generic query/effect/status/northbound/conformance、profile YAML/mount、HMAC/fallback/credential keys、online IP geolocation external path和 raw client scripts。保留 `WmsClient`、typed adapters、Mock target endpoint、`verify_wms_northbound_feasibility.py` 的 Transport 验证能力和 release artifact provider。

- [ ] **Step 5: 删除旧测试并同步测试治理**

  只有对应 target owner 已通过才删除旧行为测试；共享测试文件只删旧 fixture/assertion。同步 Jenkins、selector、HEAVY mapping、support imports 和 collect topology；旧 revision/FK/schema-only tests留给 Phase 11。

- [ ] **Step 6: 更新当前态文档**

  更新 SRS/authority/current contracts、observability、WMS caller checklist、prod release runbook、file index、master 和 release-readiness prerequisite。完成或被取代的过程文档按项目规则移出项目；`docs/hardware/` 不动。

### Task 6: 完成 GREEN、Review、发布静默切片与不可变候选

**Classification:** 两个独立高风险行为切片。先闭合 Phase 10 target-only 代码，再独立实施发布静默门禁 Tasks 2–4；最终只构建一次包含二者的不可变 candidate。

**Interfaces:**

- Consumes: Tasks 1–5 的完整 target-only diff，以及 `docs/superpowers/plans/2026-08-26-release-operational-readiness.md` Task 1 的当前审计结论。
- Produces: 含四表只读 readiness CLI 的不可变 target-only candidate digest，或精确阻塞报告；没有 Deploy 授权时停在 `IMPLEMENTED — VERIFIED — NOT DEPLOYED`。

- [ ] **Step 1: 闭合测试所有权和残留扫描**

  用 GitNexus tests 或精确 `rg` 枚举生产模块的直接/间接测试、fixture/helper、QA/回归和 HEAVY mapping；扫描旧 module/path/task/route/env/mount/test filename/current-doc 引用。Expected: 仅 Task 0 schema-deferred allowlist 命中，`UNRESOLVED=0`。

- [ ] **Step 2: 运行聚焦 FAST 与架构门禁**

  Run: `uv run pytest tests/workline/test_workline_start_service.py tests/api/test_workline_safety_operation_api.py tests/resource/test_resource_c0_projection_contract.py tests/workline_runtime/test_wms_sync_obligation_resolution.py tests/runtime/execution/test_wms_confirmation_service.py tests/contracts/wms_adapter/test_client.py tests/deployment/test_execution_worker_startup.py tests/deployment/test_wms_confirmation_dispatcher.py tests/architecture/test_cleanup_matrix_guardrail.py tests/architecture/test_business_legacy_absence_ledger.py tests/architecture/test_business_legacy_absence_guardrail.py tests/architecture/test_legacy_absence_guardrail.py tests/architecture/test_outbound_http_boundary_guardrail.py -q`

  Run: `./scripts/architecture-guardrails.sh`

  Expected: 全部 PASS；Phase 9 基础对象新增 owner 只在 Task 0 impact 证明其受当前 diff 影响时由 selector 追加，不得以整目录失败代替影响分析。

- [ ] **Step 3: 运行测试拓扑、QUALITY 与 staged HEAVY**

  Run: `uv run pytest tests/architecture/test_suite_topology_guardrail.py tests/architecture/test_core_plugin_test_ownership_guardrail.py tests/scripts -q`

  Run: `./scripts/git-quality-gate.sh --profile quality`

  Run: `uv run scripts/select_heavy_tests.py --scope staged`

  Run: `./scripts/run_selected_heavy_local.sh --scope staged`

  Expected: selector manifest 闭合，所有必选 PostgreSQL/worker/deployment HEAVY 实际执行且零 skip；不运行全量 HEAVY 求安心。

- [ ] **Step 4: 完成 Phase 10 主 Review**

  固定 base/head/staged manifest 和证据，使用一个只读 Reviewer做完整 diff review；修复运行时问题后由同一 Reviewer 一轮完成旧意见闭环和 fresh full review。Reviewer 不重复 QUALITY、HEAVY、迁移或部署。

- [ ] **Step 5: 精确提交 Phase 10 代码切片（仅已授权时）**

  核对 `git diff --cached --name-status`、cached diff、`git diff --cached --check` 和 `npx gitnexus detect-changes --scope staged --repo "$PWD"`；只暂存 Execution Lock 清单，提交可回退的 target-only 代码切片。不得使用 `git add .` 或 `--no-verify`。

- [ ] **Step 6: 独立实施发布静默门禁 Tasks 2–4**

  严格按 `2026-08-26-release-operational-readiness.md` 一次执行 Task 2 RED、Task 3 DEV 和 Task 4 GREEN，形成独立 Review/Commit。该切片只读四个目标表，不读取 legacy owner，不复制 Task 4 drain predicate，不用 feature flag、legacy adapter 或双查询。任一 RED/GREEN、单 statement、超时、部署顺序测试或 Review 未闭合，Task 7 禁止开始。

- [ ] **Step 7: 构建并验证最终不可变 candidate**

  在两个切片各自绿灯和 Review 后，构建唯一 candidate image，记录 source/image/config digest。渲染全部 Compose；以 candidate-container 连接当前数据库运行四表 readiness CLI，并验证 registered task、Beat schedule、queue、OpenAPI、env/mount、Client uniqueness、SQLModel production export 和旧 owner absence。此时只读预检，不停止现场 admission、Beat 或 worker。

  Expected: candidate 不注册旧 task、不加载旧 profile、不暴露旧 route，四表 CLI 可从 candidate-container 读取当前现场数据库；`wms-fulfillment`、共享 worker和 Redis 未误删。candidate digest 冻结后不得再修改生产、测试、脚本、配置或部署输入；任何变化都必须重新构建并刷新受影响证据。

### Task 7: 执行首次原子 cutover 与 Phase 11 handoff

**Classification:** 部署/运行切换；必须单独取得 Deploy/Cutover 授权。

**Interfaces:**

- Consumes: Task 6 不可变 candidate digest、已验证的四表 readiness CLI、Task 4 drain/cutover manifest 和 Deploy/Cutover 授权。
- Produces: Phase 10 exit evidence；只包含零消费者 schema identities 的 Phase 11 handoff。

- [ ] **Step 1: 复核 candidate ready 后关闭旧 admission 与 Beat**

  先用 candidate-container 对当前现场数据库执行在线四表预检，并核对 candidate digest/config。只有结果为 `READY` 才停止旧 deployment 的 Nginx、优雅停止 API、验证 listener 关闭，再停止旧 Beat；旧 worker、数据库和 Redis 继续运行。在线 `BLOCK`、`WAIT_DRAIN` 或查询失败在不进入维护态时终止；listener 或 Beat 停止失败则保持维护态。两类失败都不得激活 candidate。

- [ ] **Step 2: 由旧 worker 排空 legacy owner**

  旧部署的 consumer worker 继续处理已经落账的 RuntimeInbox/Intent/Outbox 等工作；执行 Task 4 的数据库同快照查询、broker inspection 和 producer freeze 复核。任一 active、ambiguous、identity conflict、查询失败或期间新增旧 row 都保持维护态，按原 identity 人工调查，不自动重发或清理。

- [ ] **Step 3: 证明 legacy 连续稳定为零**

  按 cutover manifest 的间隔取得连续两次 database/broker/producer stable zero。只有两次都满足且无新旧 identity 冲突，才允许继续；旧 worker 此时仍运行。

- [ ] **Step 4: 在旧 worker 仍运行时取得四表连续 READY**

  由同一不可变 candidate-container 读取现场 `DeviceCommand`、`TransportTask`、`InboundEvidence`、`WmsConfirmation`，按发布静默门禁取得连续两次 `READY`。`WAIT_DRAIN` 继续等待至批准的超时，`BLOCK`、查询失败或超时保持维护态；不得先停旧 worker 逼出静默。

- [ ] **Step 5: 停止旧 worker 并激活 target-only candidate**

  只有 legacy stable zero 与四表连续 READY 同时成立后，才有序停止旧共享 worker并确认无 task 正在执行；随后激活不可变 candidate。禁止 old/new worker、Beat、API 或 profile 并行，不 purge 共享 Redis queue。

- [ ] **Step 6: 重开 admission 前复核 absence 与 readiness**

  在 candidate 运行态复核旧 import/task/Beat/route/env/mount/production schema owner absence、registered tasks/queues 和四表 readiness。任何失败保持 admission 关闭，不自动 fallback 到旧路径；成功后才启动 target Beat/API/Nginx 并验证 listener。

- [ ] **Step 7: 形成 Phase 10 exit 证据**

  记录 source/image/config digest、legacy drain snapshots、四表连续 READY、task/queue/route/config absence、QUALITY/HEAVY 和未验证现场边界。健康检查、Mock、部署成功不等于设备、供应商或业务验收。

- [ ] **Step 8: 只交接零消费者 schema identities**

  对 Task 0 的每个 schema-deferred identity证明只有 model definition、`migrations/env.py`、已有 revisions 和 schema-only tests可引用，再将 schema/name/FK/index identity交给 `2026-08-15-wes-schema-and-migration-baseline-reset.md` Task 1 重新冻结。Phase 10 不传递 DDL、revision ID 或基线生成方案。

---

## Phase 10 完成定义

只有以下全部成立才能把 master 的 Phase 10 标记为完成：

1. Phase 9 exit 已证明，Task 0 所有 successor 唯一且 `UNRESOLVED=0`；
2. 旧 producer 封闭，数据库与 broker 一次性 drain 连续稳定为零，歧义按原 identity 收敛且无盲重发；
3. production/package export/API/OpenAPI/permission/Celery/Beat/Compose/env/mount/script/current-doc 对旧 owner 零引用；
4. target Safety/START/Resource、DeviceCommand、TransportTask、Material/BinExecution、InboundEvidence、WmsConfirmation 和插件 owner 测试闭合；
5. 生产 `AsyncClient` constructor 只有 Phase 2 factory，无同步 raw Client、scripts raw Client、业务 direct httpx import 或重复 WMS pool；ECS endpoint pool 仍按 canonical endpoint 复用；
6. 现有 cleanup matrix、business absence gate、legacy absence test、HTTP boundary test、architecture gate 与最终 snapshot 一致，没有第二套同义门禁；
7. QUALITY、staged selector、必选 HEAVY、candidate composition 和唯一 Review 全部有效；
8. Phase 11 handoff 只含已证明零生产消费者的 schema/model identity，revision chain 未修改；
9. 首次 cutover 只运行 target candidate，未使用双写、兼容、fallback、feature flag 或自动清理；
10. 发布静默门禁 Tasks 2–4 已在首次 cutover 前作为独立行为切片完成并进入同一不可变 candidate，普通 FULL 发布继续长期复用。
