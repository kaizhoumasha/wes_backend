# Phase 10 旧生产路径最终闭环清理实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Phase 9 全部 successor 已真实交付后，通过一次 target-only 原子切换删除旧 Runtime/Intent/Outbox/Hold/Provider 生产路径，并只把零生产消费者的 schema identity 交给 Phase 11。

**Architecture:** Phase 10 不建设新的通用平台。先用现有清理矩阵和架构门禁冻结生产 owner、消费者、测试与部署闭包；全部 successor 和一次性只读 drain 前置满足后，封住旧 producer、排空旧 worker，再以同一 candidate 删除旧代码、任务、配置和行为测试。数据库表、字段、约束、索引与 revision chain 留给 Phase 11；Phase 10 只证明它们已经没有生产消费者。

**Tech Stack:** Python 3.13、FastAPI、SQLModel/SQLAlchemy、Celery、PostgreSQL、Redis、Docker Compose、Pytest、GitNexus、HEAVY selector。

**Spec:** `docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md`

## Global Constraints

- 当前状态是 `GATED — PHASE 9 AND SUCCESSOR CLOSURE REQUIRED`。Task 0、Task 1 通过前不得修改 Phase 10 生产代码、删除旧 owner 或停用旧 consumer。
- Phase 9 必须先真实交付 `BinExecution`、活动管辖期位置投影、全部获批插件和逐 operation WMS consumer；不得用 `MaterialExecution`、旧 29-operation registry 或空模型顶替。
- Task 0 必须证明 WorkLine unfinished-work target aggregate、`ESTOP_PRESSED` final router、E03/E07 `WmsConfirmation` barrier、OpenTelemetry 同步 exporter 处置和逐 operation WMS consumer 已存在且有唯一 owner；任一 `UNRESOLVED` 非零即 `STOP`。
- 最终只保留 `LineRunEpoch`、`MaterialExecution`、`BinExecution`、`DeviceCommand`、`TransportTask`、`InboundEvidence`、`WmsConfirmation`、`WorklineSafetyIncident`、具体插件 Decision/Fact 和 typed WMS Adapter/Service；不建设第二套通用 Runtime、Manifest、Capability、Intent、Effect、Hold、Recovery、Reconciliation 或 Reservation。
- `DeviceCommand.RECONCILING`、`TransportTask.RECONCILING`、`InboundEvidence.RECONCILING`、`WmsConfirmation.RECONCILING`、`MaterialExecution.HOLD/RECONCILING`、插件 `RecoveryDecidedFact`、具体 claim/lease 与 `TransportResourceBinding` 都是目标能力，不得按词误删。
- 复用 `scripts/generate_legacy_matrix.py`、`docs/architecture/legacy-cleanup-matrix.csv`、现有 business absence gate、`tests/architecture/test_legacy_absence_guardrail.py`、`tests/architecture/test_outbound_http_boundary_guardrail.py` 和 `scripts/architecture-guardrails.sh`；不创建第二份 registry、ledger、scanner 或 phase-number absence gate。
- `business-legacy-absence-ledger.csv` 继续只拥有既有 `phase4_carrier=True` 语义。Phase 10 新条目写入现有 cleanup matrix 且 `phase4_carrier=False`，不得把 Phase 5 ledger 扩成另一份 Phase 10 owner 清单。
- 旧 `WorklineInbox` 缺席保障必须继续有效；最终 legacy absence owner 扩展精确模块、符号、task、route、env key 和 mount，不扫描通用 `replay`、`reconciliation`、`reservation`、`correlation_id` 或 `provider` 词。
- Phase 10 是大型/高风险变更，代码行为采用内聚 RED → DEV → GREEN；计划、清单说明和当前态文档不走代码式 TDD。旧行为测试只能在 target successor 测试先通过后删除。
- 生产切换不双写、不双读、不保留 feature flag、fallback、alias、v2、shim、tombstone、空 facade 或旧 Provider Profile。生产代码可以按内聚切片实现和评审，但首次激活只允许一个 target-only candidate。
- 一次性 legacy drain 只读，不 resolve、release、cancel、claim、retry、resend、purge 或清理数据；歧义必须在原 `dispatch_key`、`operation_id`、`command_code`、`transport_task_id` 或 execution identity 上人工收敛。
- Phase 10 不删除或重写 table、column、constraint、index、revision 和 migration test。只允许 schema-deferred model 被 `migrations/env.py`、已有 revision 与 schema-only tests 精确引用；应用、API、Celery、Compose、脚本和行为测试引用必须为零。
- `docs/hardware/`、callback log、WMS inbound route、`WmsClient`、Phase 2 outbound HTTP、共享 Celery/Redis、`wms-fulfillment` queue、目标 Mock endpoint 和发布 artifact provider 不得因名称命中而删除。
- FULL 发布静默门禁不反向依赖旧表。`docs/superpowers/plans/2026-08-26-release-operational-readiness.md` 的 Tasks 2–4 统一在 Phase 10 exit 后实施，不塞入本次大切换，不形成 legacy adapter 或双查询。
- Commit、Push、PR、Merge、Deploy 分别授权。本计划默认只实施和验证；没有 Deploy 授权时不得执行 Task 7 的现场切换。

---

## 冻结的目标边界

| 类别 | 处理范围 | 最终原则 |
| --- | --- | --- |
| `DELETE` | RuntimeInbox/ExecutionSession 通用运行时；RuntimeIntent/Effect/SystemCapability/SystemOutbox；generic Hold/Recovery/Reconciliation/Reservation；29-operation WMS Provider/Profile/Manifest/Catalog/query/effect/status lane；旧 task、配置、脚本和仅验证旧行为的测试 | successor、producer seal 和 drain 全部闭合后，在同一 target-only candidate 删除，不保留兼容层 |
| `SWITCH` | WorkLine START/Safety/unfinished-work/query/trace/resource；ESTOP route；E03/E07 barrier；Transport/WmsConfirmation WMS client；Composition Root、Celery、Compose、Jenkins、当前态文档 | 只切到 Task 0 已证明存在的具体目标 owner，不在 Phase 10 发明 successor |
| `RETAIN` | 目标可靠对象、typed Adapter/Service、Phase 2 HTTP、bounded response、共享基础设施、明确插件、厂商原始资料 | 继续由原领域测试 owner 验收，不用旧测试替代 |
| `schema-deferred` | 旧表对应的 Python metadata identity、`migrations/env.py`、已有 revision 和 schema-only tests | 生产消费者归零后交给 Phase 11；Phase 10 不做 DDL |
| `UNRESOLVED` | 任一无法证明唯一 successor、真实 consumer、目标事务或存量 disposition 的条目 | 数量必须为 `0`；否则停在 Task 0/1 |

已知 schema-deferred 候选至少包含：`RuntimeInbox`、`ExecutionSession`、`ExecutionCorrelation`、`ExecutionWorkItem`、`RuntimeTimeline`、`RuntimeIntentLog`、`SystemOutbox`、两套 `RuntimeHold`、`NgReturnItem`、`ReconciliationCase` 和 `WorklineBinCellReservation`。Task 0 必须从当前 metadata/FK 重新生成完整身份清单；本段不是删除授权，也不能漏掉直接依赖旧 FK 的模型。

---

### Task 0: 证明 Phase 10 入口条件并冻结 Execution Lock

**Classification:** 只读实施前审计；失败即停止，不修改生产代码、不运行名义测试。

**Inspect:**

- `docs/superpowers/plans/2026-08-03-wes-architecture-convergence-master-plan.md`
- Phase 9 的四份已批准插件子计划和实际交付包
- `src/app/execution/`、`src/app/workline/`、`src/app/device/`、`src/app/transport/`、`src/app/wms_adapter/`
- `src/app/runtime/`、`src/app/sys/`、`src/app/wms_integration/`
- `src/register.py`、`src/celery_app/`、`docker-compose*.yml`、`Jenkinsfile*`
- `docs/architecture/heavy-test-impact.toml`、`tests/README.md`

**Interfaces:**

- Consumes: Phase 9 exit evidence、当前 branch/HEAD/dirty、当前代码/配置/测试/部署装配。
- Produces: `READY FOR PHASE 10 EXECUTION LOCK` 或带精确缺口的 `STOP`；冻结的 DELETE/SWITCH/RETAIN/schema-deferred manifest 和无关 dirty 指纹。

- [ ] **Step 1: 固定当前 Git 与索引快照**

  Run: `git branch --show-current && git rev-parse HEAD && git status --short && git rev-list --left-right --count develop...HEAD`

  Run: `npx gitnexus status`

  Expected: 记录 branch、HEAD、dirty、develop ahead/behind 和 GitNexus freshness。索引 stale 时仅运行一次 `npx gitnexus analyze`，前后比较 `AGENTS.md`、`CLAUDE.md`；工具改写超范围且与用户变更重叠时停止。GitNexus 不可用则明确降级为精确 `rg`、调用点、测试 owner 和 HEAVY mapping。

- [ ] **Step 2: 验证 Phase 9 真实退出而不是计划存在**

  Run: `rg -n "class BinExecution|BinExecutionRepository|BinExecutionService" src workline_plugins tests`

  Run: `rg -n "automatic|manual|full.bin|complex.outbound|putaway|inventory.reservation" workline_plugins src/app/wms_adapter tests --glob '*.py'`

  Expected: `BinExecution` 有 model、Repository、Service、生产 consumer、活动管辖期位置投影、测试 owner 和精确 HEAVY mapping；每个获批插件和 WMS operation 有真实 consumer。只有计划文字、migration 残留、fixture 或空包时立即 `STOP`。

- [ ] **Step 3: 逐项关闭五个 successor 阻断**

  对下列对象批量运行 GitNexus upstream impact，并用当前代码/测试复核：

  1. `WorkLineRepository.count_unfinished_workload/first_unfinished_workload` 已只读取 `LineRunEpoch + MaterialExecution + BinExecution + DeviceCommand + TransportTask + WmsConfirmation` 的最终聚合，且不存在持久化空窗；
  2. `ESTOP_PRESSED` 已由最终 device-event router 调用保留的 `WorkLineSafetyService.handle_estop()`，而不是只持久化 `InboundEvidence`；
  3. E03/E07 的 `confirm_inbound` 与 `notify_pkg_binding` 已由 `WmsConfirmation`/typed service 覆盖双义务、互斥、hold release、reconciliation 和锁序；
  4. `RuntimeOpenTelemetryHttpExporter` 已按获批决定移除同步 raw Client、切到唯一生命周期 owner，或明确删除该 exporter backend；
  5. Phase 9 逐 operation consumer 表已经把 Transport submit、粗分确认、自动上架、人工分拣、满箱交换、复杂出库、库存预约和 reconciliation query 分别裁决为具体 typed owner 或 `DELETE → NONE`。

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

- [ ] **Step 3: 扩展唯一 production absence owner**

  在 `test_legacy_absence_guardrail.py` 按 Task 0 manifest 分组加入旧 module/import/path/task/route/env/mount 精确断言；保留旧插件和 WorklineInbox predecessor 保护。schema-deferred allowlist 只允许 `migrations/env.py`、已有 revisions、冻结的 schema-only tests 和模型定义本身，不允许 package export 或应用 import。

- [ ] **Step 4: 收紧唯一 HTTP boundary owner**

  扩展现有 AST scanner，最终断言：`src` 的 `AsyncClient` constructor 恰为 `src/core/outbound_http/factory.py`；`src` 同步 `Client` constructor 为空；`scripts` direct Client constructor 为空；业务包 direct `httpx` import 为空；每个 WMS outbound 进程/事件循环最多一个 client；ECS transport 按 canonical endpoint 唯一复用。保留 `bounded_http_response.py` 的 TYPE_CHECKING 类型引用和测试 client 例外。

- [ ] **Step 5: 建立 target successor RED**

  在既有 Safety/START/Resource/Device/Transport/Execution/WmsConfirmation/WMS Adapter/Composition/Deployment 测试 owner 中加入 Task 0 冻结的外部可观察不变量；不得新建 generic Runtime/Hold/Provider 测试包。更新精确 HEAVY mapping，未知路径 fail closed。

- [ ] **Step 6: 运行一次 RED 批次**

  Run: `uv run pytest tests/workline/test_workline_start_service.py tests/api/test_workline_safety_operation_api.py tests/resource/test_resource_c0_projection_contract.py tests/workline_runtime/test_wms_sync_obligation_resolution.py tests/runtime/execution/test_wms_confirmation_service.py tests/contracts/wms_adapter/test_client.py tests/deployment/test_execution_worker_startup.py tests/deployment/test_wms_confirmation_dispatcher.py tests/architecture/test_legacy_absence_guardrail.py tests/architecture/test_outbound_http_boundary_guardrail.py -q`

  Run: `uv run scripts/select_heavy_tests.py --scope unstaged`

  Expected: target successor 缺口或旧生产引用使测试准确失败；matrix/business ledger 合同继续通过。环境未启用导致的 HEAVY skip 不是 RED。Phase 9 插件新增测试由其已批准子计划和 exit evidence 拥有，本任务只通过当前 HEAVY selector 补跑受 Phase 10 diff 实际影响的 owner。

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

### Task 4: 封住旧 producer 并取得一次性只读 drain 证据

**Classification:** 受控运维前置；不创建长期 legacy Repository/DTO/CLI，不自动清理数据。

**Inspect/Operate:**

- RuntimeInbox、RuntimeIntentLog、SystemOutbox、两套 RuntimeHold、NgReturnItem、ReconciliationCase、WorklineBinCellReservation 表
- Celery active/reserved/scheduled task 与 Beat schedule
- `src/app/workline/v1/operation.py` 的旧 sandbox/replay/reconciliation routes
- orphan callback RuntimeInbox writer/service

**Interfaces:**

- Consumes: Task 0 冻结的 producer manifest 和原身份人工处置规则。
- Produces: 同一数据库 snapshot 的旧对象计数、broker 快照和连续两次稳定零新增证据；不进入长期 release readiness。

- [ ] **Step 1: 在代码 candidate 中封住所有 generic producer**

  删除旧 sandbox external callback、RuntimeInbox replay、generic reconciliation resolve、SystemCapability/RuntimeIntent create、station lease outbox writer 和 generic TaskQueueGateway wake；保留旧 RuntimeInbox/Outbox worker 与 Beat consumer 供已落账对象排空。精确扫描直接 Redis/Celery producer 和 task name。

- [ ] **Step 2: 进入维护窗并关闭 admission producer**

  只有取得 Deploy/Cutover 授权后才停止 Nginx、优雅停止 API 和 old Beat producer；保留数据库、Redis、旧 consumer worker。不得先停 worker，也不得 purge 共享 `celery` 或 `wms-fulfillment` queue。

- [ ] **Step 3: 取得数据库同快照只读结果**

  必须证明：RuntimeInbox 无 `RECEIVED/PROCESSING`、可重试 lease 和未裁决 dead-letter；RuntimeIntentLog 无 `PROPOSED/ACCEPTED/UNKNOWN/RECONCILING`；SystemOutbox 无 `NEW/DISPATCHING/RETRY_WAIT/UNKNOWN`，且无 unmatched/corrupt dispatch pair 或 `SENT + ACCEPTED`；两套 RuntimeHold 无 active blocker；NgReturnItem 无 `WAITING_REWORK/REWORKING`；ReconciliationCase 无 `OPEN`；BinCellReservation 无 `PLANNED/RECONCILING`。

- [ ] **Step 4: 取得 broker 与 producer freeze 证据**

  旧 task 在 active/reserved/scheduled 中为零；连续两次复核期间没有新增旧 row。任一歧义在原 identity 上人工调查；无法证明未发送时禁止重发。

- [ ] **Step 5: 判定是否允许停止旧 worker**

  Expected: database、broker、producer 三类证据均连续稳定为零。任一非零、查询失败或身份冲突均保持维护态并 `STOP`；不得自动 resolve、cancel、retry、resend 或清空开发/测试数据冒充 drain。

### Task 5: 在同一 target-only candidate 删除旧生产 owner

**Classification:** DEV；与 Tasks 2–4 同一 candidate，按 FK 叶到根移除应用 owner，不做 DDL。

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

- Consumes: target consumer GREEN、旧 producer seal、legacy drain zero。
- Produces: 生产/API/Celery/Compose/script/current-doc 只装配 target owners；旧 schema 仅有精确 schema-deferred 引用。

- [ ] **Step 1: 删除 RuntimeInbox/ExecutionSession 应用闭包**

  删除 repository/service/processor/contracts、orphan callback writer/service、旧 WMS inbox handler、sandbox/replay/query/timeline/snapshot、task/include/Beat/gateway/config/CI acceptance。去除 ExecutionSession/Correlation/WorkItem/Timeline 的应用 export、Repository 和 FK consumer；schema identity 不从 metadata 删除。

- [ ] **Step 2: 删除 Intent/Effect/SystemCapability/SystemOutbox 应用闭包**

  删除 generic intent/effect/reducer/reconciliation、29-operation capability registry/generated index、outbox repository/engine/dispatch attempts/binding/credentials、三个 generic dispatcher、status scanner/callback 和 WorkLine generic projection。目标 Device/Transport/WmsConfirmation 自有状态机不受影响。

- [ ] **Step 3: 删除 generic Hold/Recovery/Reconciliation/Reservation 应用闭包**

  删除 Hold CRUD/release/query/barrier、NG return API、generic reconciliation manager/case consumers、bin-cell reservation/station lease production owner和旧 DTO/routes。保留 Safety incident、MaterialExecution Wait/Pause、plugin recovery、具体 claim lease 与 TransportResourceBinding。

- [ ] **Step 4: 删除 Provider/Profile/Manifest 和无依据认证**

  删除 compiler/startup/readiness/catalog/attestation、generic query/effect/status/northbound/conformance、profile YAML/mount、HMAC/fallback/credential keys、online IP geolocation external path和 raw client scripts。保留 `WmsClient`、typed adapters、Mock target endpoint、`verify_wms_northbound_feasibility.py` 的 Transport 验证能力和 release artifact provider。

- [ ] **Step 5: 删除旧测试并同步测试治理**

  只有对应 target owner 已通过才删除旧行为测试；共享测试文件只删旧 fixture/assertion。同步 Jenkins、selector、HEAVY mapping、support imports 和 collect topology；旧 revision/FK/schema-only tests留给 Phase 11。

- [ ] **Step 6: 更新当前态文档**

  更新 SRS/authority/current contracts、observability、WMS caller checklist、prod release runbook、file index、master 和 release-readiness prerequisite。完成或被取代的过程文档按项目规则移出项目；`docs/hardware/` 不动。

### Task 6: 运行最终 GREEN、唯一 Review 与候选门禁

**Classification:** GREEN；同一最终 source/config snapshot 只运行一次完整必选门禁。

**Interfaces:**

- Consumes: Tasks 1–5 的完整 target-only diff。
- Produces: `READY FOR ATOMIC CUTOVER` 或精确阻塞报告；没有 Deploy 授权时停在 `IMPLEMENTED — VERIFIED — NOT DEPLOYED`。

- [ ] **Step 1: 闭合测试所有权和残留扫描**

  用 GitNexus tests 或精确 `rg` 枚举生产模块的直接/间接测试、fixture/helper、QA/回归和 HEAVY mapping；扫描旧 module/path/task/route/env/mount/test filename/current-doc 引用。Expected: 仅 Task 0 schema-deferred allowlist 命中，`UNRESOLVED=0`。

- [ ] **Step 2: 运行聚焦 FAST 与架构门禁**

  Run: `uv run pytest tests/workline/test_workline_start_service.py tests/api/test_workline_safety_operation_api.py tests/resource/test_resource_c0_projection_contract.py tests/workline_runtime/test_wms_sync_obligation_resolution.py tests/runtime/execution/test_wms_confirmation_service.py tests/contracts/wms_adapter/test_client.py tests/deployment/test_execution_worker_startup.py tests/deployment/test_wms_confirmation_dispatcher.py tests/architecture/test_cleanup_matrix_guardrail.py tests/architecture/test_business_legacy_absence_ledger.py tests/architecture/test_business_legacy_absence_guardrail.py tests/architecture/test_legacy_absence_guardrail.py tests/architecture/test_outbound_http_boundary_guardrail.py -q`

  Run: `./scripts/architecture-guardrails.sh`

  Expected: 全部 PASS；Phase 9 插件新增 owner 只在 Task 0 impact 证明其受当前 diff 影响时由 selector 追加，不得以整目录失败代替影响分析。

- [ ] **Step 3: 运行测试拓扑、QUALITY 与 staged HEAVY**

  Run: `uv run pytest tests/architecture/test_suite_topology_guardrail.py tests/architecture/test_core_plugin_test_ownership_guardrail.py tests/scripts -q`

  Run: `./scripts/git-quality-gate.sh --profile quality`

  Run: `uv run scripts/select_heavy_tests.py --scope staged`

  Run: `./scripts/run_selected_heavy_local.sh --scope staged`

  Expected: selector manifest 闭合，所有必选 PostgreSQL/worker/deployment HEAVY 实际执行且零 skip；不运行全量 HEAVY 求安心。

- [ ] **Step 4: 验证 candidate 装配**

  渲染全部 Compose；启动 candidate 后验证 registered task、Beat schedule、queue、OpenAPI、env/mount、Client uniqueness、SQLModel production export 和 target readiness。旧 task/profile/API/schema owner 不可达；`wms-fulfillment` 和共享 worker/Redis 未误删。

- [ ] **Step 5: 完成唯一主 Review**

  固定 base/head/staged manifest 和证据，使用一个只读 Reviewer做完整 diff review；修复运行时问题后由同一 Reviewer 一轮完成旧意见闭环和 fresh full review。Reviewer 不重复 QUALITY、HEAVY、迁移或部署。

- [ ] **Step 6: 精确暂存并提交候选（仅已授权时）**

  核对 `git diff --cached --name-status`、cached diff、`git diff --cached --check` 和 `npx gitnexus detect-changes --scope staged --repo "$PWD"`；只暂存 Execution Lock 清单，提交一个可回退的原子 candidate。不得使用 `git add .` 或 `--no-verify`。

### Task 7: 执行首次原子 cutover 与 Phase 11 handoff

**Classification:** 部署/运行切换；必须单独取得 Deploy/Cutover 授权。

**Interfaces:**

- Consumes: 不可变 candidate digest、Task 4 稳定 drain、Task 6 绿灯与 Review。
- Produces: Phase 10 exit evidence；只包含零消费者 schema identities 的 Phase 11 handoff。

- [ ] **Step 1: 停止旧执行装配**

  在 admission 已关闭且 drain 稳定后，有序停止旧共享 worker，确认无旧 task 正在执行。不得无差别 purge Redis queue。

- [ ] **Step 2: 启动 target-only candidate**

  candidate 不注册旧 task、不加载旧 profile、不读取旧表、不暴露旧 route；API/worker/Beat 只装配 target objects。禁止 old/new worker、Beat、API 或 profile 并行。

- [ ] **Step 3: 重开 admission 前验收**

  复核 legacy database/broker 零活动、旧 import/task/Compose/production schema owner absence，以及 DeviceCommand、TransportTask、InboundEvidence、WmsConfirmation 四目标表 readiness。任何失败保持维护态，不 fallback 到旧路径。

- [ ] **Step 4: 形成 Phase 10 exit 证据**

  记录 source/image/config digest、drain snapshot、task/queue/route/config absence、target readiness、QUALITY/HEAVY 和未验证现场边界。健康检查、Mock、部署成功不等于设备、供应商或业务验收。

- [ ] **Step 5: 只交接零消费者 schema identities**

  对 Task 0 的每个 schema-deferred identity证明只有 model definition、`migrations/env.py`、已有 revisions 和 schema-only tests可引用，再将 schema/name/FK/index identity交给 `2026-08-15-wes-schema-and-migration-baseline-reset.md` Task 1 重新冻结。Phase 10 不传递 DDL、revision ID 或基线生成方案。

### Task 8: Phase 10 exit 后实施长期发布静默门禁

**Classification:** 独立高风险行为切片；不与 Phase 10 candidate 合并。

**Files/Owner:**

- Execute: `docs/superpowers/plans/2026-08-26-release-operational-readiness.md` Tasks 2–4

**Interfaces:**

- Consumes: Phase 10 exit evidence和四个目标可靠对象。
- Produces: FULL 发布长期在线预检、admission closure 后稳定静默复核和对应 GREEN；不读取任何 legacy table。

- [ ] **Step 1: 重新验证 release-readiness Task 1 前置**

  以 Phase 10 target-only source 和部署拓扑重新冻结 admission closure、四表状态与 candidate runner 复用点。Phase 10 exit 未证明时不得继续。

- [ ] **Step 2: 按原计划一次执行 Tasks 2–4**

  在一个行为切片中完成 RED、最小只读四表 Repository/Service/CLI、Jenkins 接入和 GREEN；不复制本计划的一次性 drain，不增加 legacy adapter、feature flag 或双查询。

- [ ] **Step 3: 报告独立完成边界**

  Phase 10 完成不等于长期发布门禁已完成；Tasks 2–4 绿灯前状态为 `PHASE 10 EXITED — RELEASE READINESS NOT IMPLEMENTED`。TEST 验收仍需 release-readiness Task 5 的单独 Deploy 授权。

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
10. 长期发布静默门禁按 Task 8 独立追踪，未把 Phase 10 exit 夸大为其完成证据。
