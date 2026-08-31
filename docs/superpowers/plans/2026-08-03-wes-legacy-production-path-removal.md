# Phase 10 旧生产路径最终闭环清理实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

status: Tasks 0–7 completed; onsite target-only cutover verified from candidate 834fe59e
implementation_date: 2026-08-29 to 2026-08-30
merge_status: NOT PUSHED / NO PR / NOT MERGED
deployment_status: DEPLOYED TO INTEGRATION / CUTOVER VERIFIED / NOT SUPPLIER OR BUSINESS ACCEPTED

**Goal:** 在 Phase 9 最小执行基础和当前生产 successor 已真实交付后，通过一次 target-only 原子切换删除旧 Runtime/Intent/Outbox/Hold/Provider 生产路径，并只把零生产消费者的 schema identity 交给 Phase 11。

**Architecture:** Phase 10 不建设新的通用平台。先完成 Task 0 前置冻结，再由独立发布运行静默门禁计划完成 Tasks 1–4 的 RED/DEV/GREEN/Review；随后重跑并最终确认 Task 0 admission，进入 Execution Lock 后才实施本计划 Task 1 起的 target-only 代码。Task 6 只复用并核验既有四表静默门禁基线，不再晚建该能力。首次切换时关闭旧 admission/Beat，由仍运行的旧 worker 排空 legacy owner，取得 legacy stable zero 与四表连续 `READY` 后才停止旧 worker并激活 candidate。数据库表、字段、约束、索引与 revision chain 留给 Phase 11；Phase 10 只证明它们已经没有生产消费者。

**Tech Stack:** Python 3.13、FastAPI、SQLModel/SQLAlchemy、Celery、PostgreSQL、Redis、Docker Compose、Pytest、GitNexus、HEAVY selector。

**Specs:** `docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md`、`docs/superpowers/specs/2026-08-26-phase9-14-guided-development-resequence-design.md`

## Global Constraints

- Task 0 prerequisite freeze、独立发布运行静默门禁 Tasks 1–4、Task 0 final admission 与 Execution Lock 已在
  `codex/phase10-implementation` 闭合；Tasks 1–6 已完成仓内实施与验证。Task 7 已于 2026-08-30 在联调环境完成
  target-only cutover；该结果不代表供应商、物理流程或业务验收。
- Phase 9 必须先真实交付 SRS 已批准的 `BinExecution`、活动管辖期 `PositionProjection` 和本计划需要的最小 successor；不得用 `MaterialExecution`、旧 29-operation registry 或只有 schema、没有领域不变量与测试 owner 的空模型顶替。`BinExecution` 是核心执行对象，不代表人工或自动插件已经交付。
- Task 0 必须证明 `WorkLineRepository.get_unfinished_workload_summary()` 覆盖 `LineRunEpoch`、`MaterialExecution`、`BinExecution`、`DeviceCommand`、`TransportTask`、`InboundEvidence`、`WmsConfirmation` 七类 owner，现有 `ESTOP_PRESSED` final router 与 E03/E07 `WmsConfirmation` typed successor 可 `Verify/Retain`，并冻结 OpenTelemetry 同步 exporter 处置和当前仍需保留的 WMS consumer；任一 `UNRESOLVED` 非零即 `STOP`。
- `manual_bin_processing`、RETURN_BUFFER、人工 Task、自动上架、自动拣货及其 WMS 业务 wire 不属于 Phase 10 入口条件；尚无当前生产消费者的旧 operation 必须裁决为 `DELETE → NONE`，不得为 Phase 12/13 保留旧 Provider 路径。
- 最终目标对象包括 `LineRunEpoch`、`MaterialExecution`、`BinExecution`、`DeviceCommand`、`TransportTask`、`InboundEvidence`、`WmsConfirmation`、`WorklineSafetyIncident`、具体插件 Decision/Fact 和 typed WMS Adapter/Service；这不是排他清单，不得据此删除其它仍有独立业务语义的 owner。
- `DeviceCommand.RECONCILING`、`TransportTask.RECONCILING`、`InboundEvidence.RECONCILING`、`WmsConfirmation.RECONCILING`、`MaterialExecution.HOLD/RECONCILING`、插件 `RecoveryDecidedFact`、具体 claim/lease 与 `TransportResourceBinding` 都是目标能力，不得按词误删。
- `scripts/generate_legacy_matrix.py` 与 `docs/architecture/legacy-cleanup-matrix.csv` 是唯一 disposition registry；Execution Lock 前必须覆盖 runtime、sys、WMS、Celery/boot、deployment 和 schema-deferred identity。复用现有 business absence gate、`tests/architecture/test_legacy_absence_guardrail.py`、`tests/architecture/test_outbound_http_boundary_guardrail.py` 和 `scripts/architecture-guardrails.sh`；不创建第二份 registry、ledger、scanner 或 phase-number absence gate。
- `business-legacy-absence-ledger.csv` 继续只拥有既有 `phase4_carrier=True` 语义。Phase 10 新条目写入现有 cleanup matrix 且 `phase4_carrier=False`，不得把 Phase 5 ledger 扩成另一份 Phase 10 owner 清单。
- `scripts/workline_inbox_retirement_guardrail.py` 和 `tests/architecture/test_workline_inbox_retirement_guardrail.py` 继续作为已退役 `WorklineInbox` predecessor 的唯一缺席 owner；`tests/architecture/test_legacy_absence_guardrail.py` 只承接本阶段待删 owner，不复制 predecessor 规则。两者均由现有 `scripts/architecture-guardrails.sh`/QUALITY 路由，不新增 scanner。
- Phase 10 是大型/高风险变更，代码行为采用内聚 RED → DEV → GREEN；计划、清单说明和当前态文档不走代码式 TDD。旧行为测试只能在 target successor 测试先通过后删除。
- 生产切换不双写、不双读、不保留 feature flag、fallback、alias、v2、shim、tombstone、空 facade 或旧 Provider Profile。生产代码可以按内聚切片实现和评审，但首次激活只允许一个 target-only candidate。
- 一次性 legacy drain 只读，不 resolve、release、cancel、claim、retry、resend、purge 或清理数据；歧义必须在原 `dispatch_key`、`operation_id`、`command_code`、`transport_task_id` 或 execution identity 上人工收敛。
- Phase 10 不删除或重写 table、column、constraint、index、revision 和 migration test。只允许 schema-deferred model 被 `migrations/env.py`、已有 revision 与 schema-only tests 精确引用；应用、API、Celery、Compose、脚本和行为测试引用必须为零。
- `src/app/runtime/orchestration/models/session.py:WorklineSession`、`src/app/runtime/orchestration/models/timeline.py:WorklineTimeline`、`src/app/runtime/orchestration/models/runtime_location_event.py:RuntimeLocationEvent`、`src/app/runtime/orchestration/services/inbox/object_transition_event_service.py:ObjectTransitionEventService`、`src/app/callback/models/callback_log.py`、`src/app/callback/repositories/callback_log_repository.py`、`src/app/callback/services/callback_log_service.py` 和 `src/app/callback/v1/callback_log.py` 明确 `RETAIN`；它们分别不同于 `src/app/runtime/orchestration/execution_session.py:ExecutionSession`、`src/app/runtime/orchestration/runtime_timeline.py:RuntimeTimeline`、generic RuntimeInbox 和已失去 route 的 external callback ingress。只有 Task 0 提供新的直接消费者证据并取得批准，才能改变这些分类。
- `docs/hardware/`、WMS inbound route、`WmsClient`、Phase 2 outbound HTTP、共享 Celery/Redis、`wms-fulfillment` queue、目标 Mock endpoint 和发布 artifact provider 不得因名称命中而删除。
- FULL 发布静默门禁不反向依赖旧表。`docs/superpowers/plans/2026-08-26-release-operational-readiness.md` 的 Tasks 1–4 必须在 Phase 10 Execution Lock 前独立完成 RED/DEV/GREEN/Review；Task 0 随后复核其 candidate runner、插入点和证据，Task 6 只复用并验证该基线进入最终不可变 target-only candidate，不形成 legacy adapter 或双查询。
- Commit、Push、PR、Merge、Deploy 分别授权。本计划默认只实施和验证；没有 Deploy 授权时不得执行 Task 7 的现场切换。

---

## 冻结的目标边界

| 类别 | 处理范围 | 最终原则 |
| --- | --- | --- |
| `DELETE` | RuntimeInbox/ExecutionSession 通用运行时；RuntimeIntent/Effect/SystemCapability/SystemOutbox；generic Hold/Recovery/Reconciliation/Reservation；29-operation WMS Provider/Profile/Manifest/Catalog/query/effect/status lane；旧 task、配置、脚本和仅验证旧行为的测试 | successor 闭合后在 target-only candidate 中删除；candidate 只有在旧部署 producer seal、legacy drain 和四表 readiness 均通过后才激活，不保留兼容层 |
| `SWITCH` | WorkLine START/Safety/unfinished-work/query/trace/resource；ESTOP route；E03/E07 barrier；Transport/WmsConfirmation WMS client；Composition Root、Celery、Compose、Jenkins、当前态文档 | 只切到 Task 0 已证明存在的具体目标 owner，不在 Phase 10 发明 successor |
| `RETAIN` | 目标可靠对象、`WorklineSession`、`WorklineTimeline`、`RuntimeLocationEvent`、`ObjectTransitionEventService`、callback log、typed Adapter/Service、Phase 2 HTTP、bounded response、共享基础设施、明确插件、厂商原始资料 | 继续由原领域测试 owner 验收，不用旧测试替代；与 `ExecutionSession` / `RuntimeTimeline` 按完整路径区分 |
| `schema-deferred` | 旧表对应的 Python metadata identity、`migrations/env.py`、已有 revision 和 schema-only tests | 生产消费者归零后交给 Phase 11；Phase 10 不做 DDL |
| `UNRESOLVED` | 任一无法证明唯一 successor、真实 consumer、目标事务或存量 disposition 的条目 | 数量必须为 `0`；否则在 Task 0 / Execution Lock 前停止。Task 1 只能复核并消费已闭合清单，不得补齐 disposition |

已知 schema-deferred 候选至少包含：`RuntimeInbox`、`ExecutionSession`、`ExecutionCorrelation`、`ExecutionWorkItem`、`RuntimeTimeline`、`RuntimeIntentLog`、`SystemOutbox`、两套 `RuntimeHold`、`NgReturnItem`、`ReconciliationCase` 和 `WorklineBinCellReservation`。Task 0 必须从当前 metadata/FK 重新生成完整身份清单；本段不是删除授权，也不能漏掉直接依赖旧 FK 的模型。

## 2026-08-29 仓内实施证据

- Tasks 0–6 各自一个提交：`eb9b1a54`、`6cbe6f1b`、`17f3ef4c`、`6f311ed5`、`30bda819`、
  `24fed6fd`、`834fe59e`；最终 source tree 为 `58fbe212ba57186668783eb06f85c7cf37a0d7a6`。
- 最终 QUALITY 为 `2378 passed, 5 skipped`；staged selector 选中的 HEAVY 为 `439 passed, 0 skipped`；架构门禁
  0 violations/warnings；同一 Reviewer fresh Review 为 0 findings。
- 由干净 `git archive HEAD` 构建本地候选 `wes-backend-phase10-candidate:834fe59e0c44`，image ID
  `sha256:3cfb0d75e185281d89cd3fbfd6355e04ca8de2fa4d0548a84da041eb3b7d41e6`；隔离 PostgreSQL 从 base 升级到
  `dd35f04b258f`，四表 readiness 返回 `READY`，候选容器健康。
- 上述证据只证明仓内实现、隔离环境和本地不可变候选。Push、PR、Merge、Deploy/Cutover、现场 legacy drain、连续 READY、
  旧 worker 停止、供应商/设备/业务验收均未执行。

## 2026-08-30 Task 7 联调环境退出证据

- 目标：`CANTAISYS@100.94.216.118`；release evidence：
  `/srv/wes/app/releases/phase10-task7-20260830T054232Z-834fe59e/`。
- 不可变候选：source commit `834fe59e0c44c943487eedb6ed41af1c519df7ad`，native amd64 image digest
  `sha256:018c1cd82276b876a64ffbdaa9379ceca15a091fc1b1b265960793d732d8e00d`，expected schema head
  `dd35f04b258f`；API、2 个 general worker、1 个 fulfillment worker、Beat 与 Flower 共 6 个后端容器使用同一 digest。
- 旧入口/producer：Nginx、API、Beat 按序停止，旧 worker 保持运行时，现场专用 manifest 对全部 3 个精确 Celery node
  完成两次 legacy database/broker stable zero，结果 `READY`；没有 purge Redis queue。
- 在线四账本预检发现 1 个 `DeviceCommand`、1 个 `TransportTask` 与 4 个 `InboundEvidence` blocker。用户明确确认联调数据可清理并
  授权完整重建精确数据库 `wes_db`；因此本次维护窗采用 empty-site rebuild 处置，而不是伪造 callback、改写原 identity 或盲重发。
- 删除前备份：`/srv/wes/app/backups/wes_db-pre-phase10-task7-20260830T054232Z.dump`，SHA-256
  `4a8bd0dfd5ed7665184d880b6e73e712b6ef72d5a320a90d7a86f007907e3961`，并通过 `pg_restore --list` 校验。只 drop/create
  `wes_db`；PostgreSQL volume、Redis、frontend 与其它数据库均未删除。
- 新库由候选从 base migration 到唯一 head `dd35f04b258f`，运行 `bootstrap_foundation.sh` 后权限独立 `--check` 为
  149 permissions、5 roles、0 delta；候选四账本随后间隔 30 秒取得两次 `READY`，激活后再次 `READY`。
- 运行时 absence：3 个 worker 注册的 legacy task 为 0，Beat 中 legacy schedule task 为 0；7 个遗留 WMS profile/effect env key
  从现场 `.env.prod` 精确删除并重建后端，`/run/wes/wms-provider.yaml` mount 为 0，旧 backend image container 为 0；实时
  OpenAPI 除运行时版本字段外与候选 provider artifact 一致，管理员真实 login/logout 通过。
- 最终入口：HTTP `/health` 与首页从联调主机及远端客户端均返回 200；现有 Nginx 只配置 `listen 80`，虽然 Compose 发布 443，
  但没有 TLS listener/certificate，因此本次不把 HTTPS 记为通过证据。frontend 容器未重建。
- 边界：这证明 Phase 10 target-only 部署与运行准入，不证明 WMS/RCS/ECS、设备物理完成、供应商联调或业务验收。分支仍未
  Push、创建 PR 或合入 `develop`。

---

### Task 0: 前置冻结并最终确认 Phase 10 Execution Lock admission

**Classification:** 两阶段只读实施前审计；先冻结发布门禁所需接口，待其 Tasks 1–4 独立完成后重跑最终 admission。任一阶段失败即停止，不修改 Phase 10 生产代码、不运行名义测试。

**Inspect:**

- `docs/superpowers/plans/2026-08-03-wes-architecture-convergence-master-plan.md`
- Phase 9 Minimum Execution Foundation 计划、实际交付包和退出证据
- `docs/architecture/northbound-wms-operation-inventory.csv`（Phase 9 当前机器 handoff；必须在 Phase 10 基线重新验证）
- `src/app/execution/`、`src/app/workline/`、`src/app/device/`、`src/app/transport/`、`src/app/wms_adapter/`
- `src/app/runtime/`、`src/app/sys/`、`src/app/wms_integration/`
- `src/register.py`、`src/celery_app/`、`docker-compose*.yml`、`Jenkinsfile*`
- `docs/architecture/heavy-test-impact.toml`、`tests/README.md`

**Interfaces:**

- Consumes: `develop@c5a93872` 的 Phase 9 exit evidence、当前 branch/HEAD/dirty、当前代码/配置/测试/部署装配，以及最终 admission 阶段已完成的发布运行静默门禁 Tasks 1–4 证据。
- Produces: 前置阶段的 prerequisite freeze；最终阶段的 `READY FOR PHASE 10 EXECUTION LOCK` 或带精确缺口的 `STOP`；冻结的 DELETE/SWITCH/RETAIN/schema-deferred manifest 和无关 dirty 指纹。

Phase 9 已于 2026-08-29 合入 `develop@c5a93872`，其 `UNRESOLVED=0` operation inventory 和分支验收记录成为 Task 0 当前候选输入；仍须在本 worktree 当前快照重验。Task 0 前置阶段只冻结 minimum cutover order、唯一 candidate runner 和发布运行静默门禁 insertion-point interface，不得提前签发 Execution Lock；发布运行静默门禁 Tasks 1–4 完成 RED/DEV/GREEN/Review 后，必须重跑本 Task 并最终确认 admission。

- [x] **Step 1: 固定当前 Git 与索引快照**

  Run: `git branch --show-current && git rev-parse HEAD && git status --short && git rev-list --left-right --count develop...HEAD`

  Run: `npx gitnexus status`

  Expected: 记录 branch、HEAD、dirty、develop ahead/behind 和 GitNexus freshness。索引 stale 时仅运行一次 `npx gitnexus analyze`，前后比较 `AGENTS.md`、`CLAUDE.md`；工具改写超范围且与用户变更重叠时停止。GitNexus 不可用则明确降级为精确 `rg`、调用点、测试 owner 和 HEAVY mapping。

- [x] **Step 2: 验证 Phase 9 真实退出而不是计划存在**

  Run: `rg -n "class BinExecution|BinExecutionRepository|BinExecutionService" src workline_plugins tests`

  Run: `rg -n "manual_bin_processing|automatic_putaway|automatic_picking|RETURN_BUFFER" src workline_plugins tests --glob '*.py'`

  Expected: `BinExecution` 有 model、Repository、Service、活动管辖期位置投影、领域不变量、测试 owner 和精确 HEAVY mapping；它不依赖 Phase 12/13 插件才能成立。后置业务插件可以不存在；若旧 Provider/operation 仍为它们保留生产路径，则必须进入 `DELETE → NONE`。只有计划文字、migration 残留、fixture 或无领域行为的空包时立即 `STOP`。

- [x] **Step 3: 逐项关闭五个 successor 阻断**

  对下列对象批量运行 GitNexus upstream impact，并用当前代码/测试复核：

  1. `WorkLineRepository.get_unfinished_workload_summary()` 已以单一聚合覆盖 `LineRunEpoch + MaterialExecution + BinExecution + DeviceCommand + TransportTask + InboundEvidence + WmsConfirmation` 七类 owner，且不存在持久化空窗；不得新增替代别名或拆分方法；
  2. 现有 `ESTOP_PRESSED` final device-event router 已调用保留的 `WorkLineSafetyService.handle_estop()`，默认裁决为 `Verify/Retain`；只有 Task 1 RED 证明精确残余 legacy dependency 时才修改；
  3. 现有 E03/E07 `WmsConfirmation` typed successor 已覆盖 `confirm_inbound` 与 `notify_pkg_binding` 的双义务、互斥、hold release、reconciliation 和锁序，默认裁决为 `Verify/Retain`；只有 Task 1 RED 证明精确残余 legacy dependency 时才修改；
  4. `RuntimeOpenTelemetryHttpExporter` 已按获批决定移除同步 raw Client、切到唯一生命周期 owner，或明确删除该 exporter backend；
  5. 当前 operation consumer 表已经把 Transport submit、粗分确认和其它真实消费者裁决为具体 typed owner；人工分拣、自动上架、自动拣货等后置业务没有当前消费者时统一裁决为 `DELETE → NONE`。

  Expected: 五项均有唯一生产 owner、直接/间接测试 owner 和必要 HEAVY；不得以设计方向、旧测试绿灯或 29-operation registry 代替。

- [x] **Step 4: 冻结唯一 matrix 的生产与 schema 闭包**

  使用且只使用 `scripts/generate_legacy_matrix.py` 与 `docs/architecture/legacy-cleanup-matrix.csv`，精确枚举 runtime、sys、WMS、Celery task/include/Beat/route、boot、deployment、model/FK、Repository、Service、API route、permission/OpenAPI、queue、env key、Compose mount、Jenkins preflight、script、current-doc、直接/间接测试和 HEAVY mapping。对所有旧数据库 identity 记录唯一 schema/name 及允许引用者；对 Redis broker 记录 active/reserved/scheduled legacy task name。

  Expected: 每项都有 `DELETE`、`SWITCH`、`RETAIN` 或 `schema-deferred`，`UNRESOLVED=0`；generator、CSV 与既有 guardrail 在 Execution Lock 前已经一致。任一覆盖缺口都先输出 `STOP`，在锁外按单独授权补齐现有 registry 并重跑 Task 0，不得留给 Task 1；无关 dirty 只记录路径/stat/hash，不读完整 diff。

- [x] **Step 5: 前置冻结发布门禁接口并在完成后重跑 admission**

  指定一个实施 owner 修改共享执行路径，一个 cutover owner 执行维护窗；Reviewer 只读。前置阶段只冻结 minimum cutover order、现有 `business_preflight()` candidate runner 复用边界和 readiness insertion-point interface，供独立发布运行静默门禁计划实施；具体 legacy SQL、broker inspection 命令和演练继续由本计划 Task 4 拥有。发布运行静默门禁 Tasks 1–4 完成后，重跑 Steps 1–4，并冻结 candidate 的 source/image/config digest 输入、旧 producer seal 顺序、database/broker drain predicate、共享 worker 停启顺序和 rollback 边界。

  Expected: 发布运行静默门禁 Tasks 1–4 已独立完成 RED/DEV/GREEN/Review，所有清单在同一 base/head 上闭合，Phase 9 exit 证据有效；此时才输出 `READY FOR PHASE 10 EXECUTION LOCK`。任何一项失败都输出 `STOP — PHASE 10 REMAINS GATED`，不得进入 Task 1。

### Task 1: 复核唯一清理真源并建立一次完整 RED

**Classification:** 高风险测试治理；现有 matrix/generator 的完整覆盖是 Execution Lock 输入，本 Task 只复核并消费，不创建或晚补 registry/scanner。

**Files:**

- Verify unchanged pre-lock baseline: `scripts/generate_legacy_matrix.py`
- Verify unchanged pre-lock generated truth: `docs/architecture/legacy-cleanup-matrix.csv`
- Verify unchanged pre-lock owner: `tests/architecture/test_cleanup_matrix_guardrail.py`
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
- Produces: 基于现有 matrix Phase 10 entries 的 target successor 测试和最终旧路径/HTTP 边界 RED；旧 Phase 5 ledger 与 pre-lock matrix 语义保持不变。

- [x] **Step 1: 复核 pre-lock matrix 完整覆盖**

  复核 Task 0 已冻结的 runtime、sys、WMS、Celery/boot、deployment 和 schema-deferred identity 都已进入现有 `parse_entries()` 与 `legacy-cleanup-matrix.csv`；复用现有字段记录 owner、semantics、strategy、target、drop phase、risk。Phase 10 条目固定 `phase4_carrier=False`，目标为具体 owner 或 `NONE`，不增加 `phase10-registry.csv`。若发现缺口，立即停止并回到 Execution Lock 前补齐、验证和重跑 Task 0，不得在本 Task 静默修改 registry。

- [x] **Step 2: 验证 CSV 与 generator 一致且 Phase 5 ledger 未被污染**

  Run: `uv run pytest tests/architecture/test_cleanup_matrix_guardrail.py tests/architecture/test_business_legacy_absence_ledger.py tests/architecture/test_business_legacy_absence_guardrail.py -q`

  Expected: CSV 与 generator 完全一致；既有 `phase4_carrier=True` entry set 不变，business final gate 继续通过；`bin_cell_reservation`/`station_lease` 的 Phase 10 当前 owner 以新条目裁决，不篡改旧路径已迁移的历史事实。

- [x] **Step 3: 扩展 Phase 10 production absence owner**

  在 `test_legacy_absence_guardrail.py` 按 Task 0 manifest 分组加入本阶段旧 module/import/path/task/route/env/mount 精确断言；不复制 `WorklineInbox` predecessor token、allowlist 或 remediation。继续运行 `workline_inbox_retirement_guardrail.py` 及其测试，证明 predecessor owner 未漂移。schema-deferred allowlist 只允许 `migrations/env.py`、已有 revisions、冻结的 schema-only tests 和模型定义本身，不允许 package export 或应用 import。

- [x] **Step 4: 收紧唯一 HTTP boundary owner**

  扩展现有 AST scanner，最终断言：`src` 的 `AsyncClient` constructor 恰为 `src/core/outbound_http/factory.py`；`src` 同步 `Client` constructor 为空；`scripts` direct Client constructor 为空；业务包 direct `httpx` import 为空；每个 WMS outbound 进程/事件循环最多一个 client；ECS transport 按 canonical endpoint 唯一复用。保留 `bounded_http_response.py` 的 TYPE_CHECKING 类型引用和测试 client 例外。

- [x] **Step 5: 建立 target successor RED**

  在既有 Safety/START/Resource/Device/Transport/Execution/WmsConfirmation/WMS Adapter/Composition/Deployment 测试 owner 中加入 Task 0 冻结的外部可观察不变量；不得新建 generic Runtime/Hold/Provider 测试包。更新精确 HEAVY mapping，未知路径 fail closed。

- [x] **Step 6: 运行一次 RED 批次**

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
- Verify/Retain by default; modify only if Task 1 RED proves an exact residual legacy dependency: target device-event router frozen in Task 0
- Verify/Retain by default; modify only if Task 1 RED proves an exact residual legacy dependency: target E03/E07 WmsConfirmation/typed service frozen in Task 0

**Interfaces:**

- Consumes: 已存在的 unfinished-work aggregate、ESTOP router、E03/E07 barrier 和 owner-specific recovery APIs。
- Produces: START/Safety/resource/query/trace 不再读写 RuntimeInbox/SystemOutbox/RuntimeHold/ExecutionSession/generic reconciliation；旧 consumer 仍可用于存量 drain，但不接收新业务。

- [x] **Step 1: 切 WorkLine START 与 unfinished-work admission**

  保留 `LineRunEpoch` 激活、幂等与安全不变量；删除 parked SystemOutbox release/wake、`released_outbox_count`、RuntimeInbox/Hold/old reconciliation ports。stop/deactivate 只调用 Task 0 已证明无空窗的 target aggregate。

- [x] **Step 2: 切 ESTOP 与 clear-estop transaction**

  先验证现有 final device-event router 调用 `WorkLineSafetyService.handle_estop()` 并标记为 `Verify/Retain`。只有 Task 1 RED 证明该 router 仍有精确 residual legacy dependency 时，才删除该依赖；不得借 Phase 10 重写已闭合行为。Safety 只维护 `WorklineSafetyIncident`、Epoch 与具体可靠对象状态。已发送 DeviceCommand/TransportTask 保持原身份收敛，不盲取消；clear 只清 incident/checklist 并等待新 START。

- [x] **Step 3: 去除 Resource 的 generic Hold side effect**

  `ResourceProjectionResult.RECONCILING` 保持唯一资源冲突结果；移除 `runtime_hold_creator` 注入和 `runtime_hold` 返回包装。具有 execution identity 的调用方让对应 `MaterialExecution` 进入 `RECONCILING`，无 identity 时不伪造全线 Hold。

- [x] **Step 4: 切 active-object/query/trace**

  保留的 API 只投影 `LineRunEpoch`、Material/BinExecution、DeviceCommand、TransportTask、WmsConfirmation、Safety incident 和具体 resource evidence；删除 `active_runtime_hold_ids`、generic `open_issue_count`、old session/outbox/station-lease wrapper。没有当前 API caller 的旧 query/trace owner直接列入 Task 5 DELETE。

- [x] **Step 5: 切 E03/E07 双义务**

  先验证 Task 0 已证明的 `WmsConfirmation` identity/transaction/lock owner 并标记为 `Verify/Retain`。只有 Task 1 RED 证明它仍精确依赖 `ExecutionWorkItem` mutex、RuntimeIntent/Hold/ReconciliationCase barrier 时，才删除该 residual dependency；不得重建 typed successor。两个 confirmation 的创建、互斥、完成、拒绝、歧义和 execution 推进必须继续在原 execution identity 上闭合。

- [x] **Step 6: 运行聚焦 GREEN**

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

- [x] **Step 1: 建立最小 WMS composition**

  只用 `WMS_BASE_URL + TRANSPORT_SUBMIT_PATH` 构造 target `WmsClient`；同一实际 outbound 进程/事件循环由 Transport 与 WmsConfirmation 共享并由 composition root 关闭。没有 outbound caller 的进程不建立空闲 pool。

- [x] **Step 2: 固定入站认证与 operation owner**

  `WmsInboundAuthPolicy` 只允许 isolated LAN + `NONE` 和 typed event admission；移除 `CompiledWmsProviderProfile`、HMAC enum/credential fallback。逐 operation 表中有真实 consumer 的能力留在对应 typed Adapter/plugin，没有 consumer 的能力保持 `DELETE → NONE`。

- [x] **Step 3: 切 API/Celery lifecycle**

  `src/register.py`、Celery child、Beat 只装配 DeviceCommand、TransportTask、Execution、WmsConfirmation、明确插件和 WMS inbound handler；保留 `wms-fulfillment` worker/queue，移除 Provider catalog、query/effect/status runtime 和旧 process role。

- [x] **Step 4: 切 Compose/Jenkins/config provenance**

  删除 `WMS_PROVIDER_PROFILE_FILE`、host file、profile mount、`WMS_EFFECT_*`、HMAC secrets 和 credential registry keys；Jenkins 以最小 target config digest、task/queue/client readiness 替代 provider profile digest/attestation。保留 target Mock endpoint 和发布兼容 provenance。

- [x] **Step 5: 执行 OpenTelemetry 已批准裁决**

  若 Task 0 决定保留 exporter，则使用批准的唯一生命周期 owner且不再直接构造同步 Client；若决定删除 backend，则同时删除其配置、注册、测试和 current observability reference。不得把异步 transport 静默塞给同步线程模型。

- [x] **Step 6: 运行聚焦 GREEN**

  运行 target WMS client/adapter、Transport/WmsConfirmation、worker startup、Compose/Jenkins contract 和 outbound HTTP boundary 测试。Expected: target candidate 不依赖 Provider Profile；旧 generic runtime 尚待 Task 5 删除，最终 absence 仍未宣称通过。

### Task 4: 冻结只读 drain 谓词与 cutover manifest

**Classification:** 只读演练与运行准备；本 Task 独占具体 legacy aggregate SQL、broker inspection 命令和 rehearsal，不进入真实维护窗，不修改现场数据或进程。

**Inspect/Rehearse:**

- RuntimeInbox、RuntimeIntentLog、SystemOutbox、两套 RuntimeHold、NgReturnItem、ReconciliationCase、WorklineBinCellReservation 表
- Celery active/reserved/scheduled task 与 Beat schedule
- `src/app/workline/v1/operation.py` 的旧 sandbox/replay/reconciliation routes
- orphan callback RuntimeInbox writer/service

**Interfaces:**

- Consumes: Task 0 冻结的 producer manifest 和原身份人工处置规则。
- Produces: 经演练的具体同一 snapshot aggregate SQL、broker inspection 命令、零值判定、人工 disposition 和 cutover manifest；不声称现场已 drain，也不进入长期 release readiness。

- [x] **Step 1: 冻结 producer seal manifest**

  精确列出旧 sandbox external callback、RuntimeInbox replay、generic reconciliation resolve、SystemCapability/RuntimeIntent create、station lease outbox writer、generic TaskQueueGateway wake、直接 Redis/Celery producer 和 task name。这里只冻结 Task 5 candidate 要删除的 producer，不部署、不关闭 route、不停止 Beat。

- [x] **Step 2: 冻结数据库同快照谓词**

  只读查询必须同时返回：RuntimeInbox 的可处理、lease、dead-letter；RuntimeIntentLog 的 active/ambiguous；SystemOutbox 的 active/ambiguous、unmatched pair、identity/digest conflict 和 `SENT + ACCEPTED`；两套 RuntimeHold active blocker；NgReturnItem active；ReconciliationCase open；BinCellReservation active。查询不 lock、不 claim、不写审计、不 commit。

- [x] **Step 3: 冻结 broker 与连续稳定规则**

  记录 Celery active/reserved/scheduled legacy task inspection、producer freeze 时间点、两次复核间隔和“期间无新增旧 row”规则。不得设计 queue purge；共享 `celery`、`device-command`、`wms-fulfillment` 消息按 task identity 观察。

- [x] **Step 4: 演练查询和失败路径**

  在隔离测试数据库或批准的只读副本演练查询输出、查询失败、非零、identity conflict 和 broker inspection 失败。任一歧义只生成原 identity 人工调查项；不自动 resolve、cancel、retry、resend 或清理。

- [x] **Step 5: 冻结 Task 7 cutover manifest**

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

- [x] **Step 1: 删除 RuntimeInbox/ExecutionSession 应用闭包**

  删除 repository/service/processor/contracts、orphan external callback RuntimeInbox writer/service、旧 WMS inbox handler、sandbox/replay/query/snapshot、task/include/Beat/gateway/config/CI acceptance。去除 `src/app/runtime/orchestration/execution_session.py:ExecutionSession`、`execution_correlation.py:ExecutionCorrelation`、`execution_work_item.py:ExecutionWorkItem` 和 `runtime_timeline.py:RuntimeTimeline` 的应用 export、Repository 与 FK consumer；schema identity 不从 metadata 删除。

  明确保留 `src/app/runtime/orchestration/models/session.py:WorklineSession`、`src/app/runtime/orchestration/models/timeline.py:WorklineTimeline`、`src/app/runtime/orchestration/models/runtime_location_event.py:RuntimeLocationEvent`、其 repository/service/query owner，以及 `src/app/runtime/orchestration/services/inbox/object_transition_event_service.py:ObjectTransitionEventService`；它们不属于 `execution_session.py:ExecutionSession` 或 `runtime_timeline.py:RuntimeTimeline` 删除集。

- [x] **Step 2: 删除 Intent/Effect/SystemCapability/SystemOutbox 应用闭包**

  删除 generic intent/effect/reducer/reconciliation、29-operation capability registry/generated index、outbox repository/engine/dispatch attempts/binding/credentials、三个 generic dispatcher、status scanner/callback 和 WorkLine generic projection。目标 Device/Transport/WmsConfirmation 自有状态机不受影响。

  保留 `src/app/callback/models/callback_log.py`、`src/app/callback/repositories/callback_log_repository.py`、`src/app/callback/services/callback_log_service.py` 和 `src/app/callback/v1/callback_log.py`；删除的是已失去 route 的 external callback ingress/RuntimeInbox writer，不是 callback log 查询能力。

- [x] **Step 3: 删除 generic Hold/Recovery/Reconciliation/Reservation 应用闭包**

  删除 Hold CRUD/release/query/barrier、NG return API、generic reconciliation manager/case consumers、bin-cell reservation/station lease production owner和旧 DTO/routes。保留 Safety incident、MaterialExecution Wait/Pause、plugin recovery、具体 claim lease 与 TransportResourceBinding。

- [x] **Step 4: 删除 Provider/Profile/Manifest 和无依据认证**

  删除 compiler/startup/readiness/catalog/attestation、generic query/effect/status/northbound/conformance、profile YAML/mount、HMAC/fallback/credential keys、online IP geolocation external path和 raw client scripts。保留 `WmsClient`、typed adapters、Mock target endpoint、`verify_wms_northbound_feasibility.py` 的 Transport 验证能力和 release artifact provider。

- [x] **Step 5: 删除旧测试并同步测试治理**

  只有对应 target owner 已通过才删除旧行为测试；共享测试文件只删旧 fixture/assertion。同步 Jenkins、selector、HEAVY mapping、support imports 和 collect topology；旧 revision/FK/schema-only tests留给 Phase 11。

- [x] **Step 6: 更新当前态文档**

  更新 SRS/authority/current contracts、observability、WMS caller checklist、prod release runbook、file index、master 和 release-readiness prerequisite。完成或被取代的过程文档按项目规则移出项目；`docs/hardware/` 不动。

### Task 6: 完成最终同快照证据、复核发布静默基线与不可变候选

**Classification:** Phase 10 target-only 最终验证。发布运行静默门禁 Tasks 1–4 已在 Execution Lock 前独立完成；本 Task 只复用并核验其基线，最终只构建一次包含二者的不可变 candidate，不晚建或重复 Review 该能力。

**Interfaces:**

- Consumes: Tasks 1–5 的完整 target-only diff，以及 `docs/superpowers/plans/2026-08-26-release-operational-readiness.md` Tasks 1–4 已完成的 RED/DEV/GREEN/Review 基线和 Task 0 最终 admission 证据。
- Produces: 含四表只读 readiness CLI 的不可变 target-only candidate digest，或精确阻塞报告；没有 Deploy 授权时停在 `IMPLEMENTED — VERIFIED — NOT DEPLOYED`。

- [x] **Step 1: 闭合测试所有权和残留扫描**

  用 GitNexus tests 或精确 `rg` 枚举生产模块的直接/间接测试、fixture/helper、QA/回归和 HEAVY mapping；扫描旧 module/path/task/route/env/mount/test filename/current-doc 引用。Expected: 仅 Task 0 schema-deferred allowlist 命中，`UNRESOLVED=0`。

- [x] **Step 2: 冻结、精确暂存并记录最终 cached fingerprint**

  先冻结 exact code/test snapshot；只按 Execution Lock manifest 精确 `git add <paths>`，不得使用 `git add .`。随后核对 `git diff --cached --name-status`、完整 cached diff、`git diff --cached --check`，记录 cached tree/diff fingerprint，并确认可执行树没有使该快照失真的 unstaged/untracked 变化。空 index、stale index 或 fingerprint 与待验证代码/测试不一致均为阻断错误，必须停止并修正快照；Commit 仍需单独授权。

- [x] **Step 3: 对同一 cached snapshot 运行最终聚焦检查与 staged HEAVY**

  Run: `uv run pytest tests/workline/test_workline_start_service.py tests/api/test_workline_safety_operation_api.py tests/resource/test_resource_c0_projection_contract.py tests/workline_runtime/test_wms_sync_obligation_resolution.py tests/runtime/execution/test_wms_confirmation_service.py tests/contracts/wms_adapter/test_client.py tests/deployment/test_execution_worker_startup.py tests/deployment/test_wms_confirmation_dispatcher.py tests/architecture/test_cleanup_matrix_guardrail.py tests/architecture/test_business_legacy_absence_ledger.py tests/architecture/test_business_legacy_absence_guardrail.py tests/architecture/test_legacy_absence_guardrail.py tests/architecture/test_outbound_http_boundary_guardrail.py -q`

  Run: `./scripts/architecture-guardrails.sh`

  Run: `uv run pytest tests/architecture/test_suite_topology_guardrail.py tests/architecture/test_core_plugin_test_ownership_guardrail.py tests/scripts -q`

  Run: `./scripts/git-quality-gate.sh --profile quality`

  Run: `uv run scripts/select_heavy_tests.py --scope staged`

  Run: `./scripts/run_selected_heavy_local.sh --scope staged`

  Expected: 全部命令绑定 Step 2 记录的同一 cached fingerprint；selector manifest 闭合，所有必选 PostgreSQL/worker/deployment HEAVY 实际执行且零 skip。Phase 9 基础对象新增 owner 只在 Task 0 impact 证明其受当前 diff 影响时由 selector 追加，不得以整目录失败代替影响分析，也不运行全量 HEAVY 求安心。

- [x] **Step 4: 完成 Phase 10 主 Review**

  使用 Step 2 的相同 base/head、cached manifest 和 fingerprint，由一个只读 Reviewer 对同一 cached diff 做完整 Review；Reviewer 不重复 QUALITY、HEAVY、迁移或部署。任何生产、测试、脚本、配置或 staged snapshot 变化都会使 Step 2–4 证据失效，必须从冻结与精确暂存重新开始；修复运行时问题后由同一 Reviewer 一轮完成旧意见闭环和 fresh full review。

- [x] **Step 5: 精确提交 Phase 10 代码切片（仅已授权时）**

  再次核对 cached fingerprint、`git diff --cached --name-status`、cached diff、`git diff --cached --check` 和 `npx gitnexus detect-changes --scope staged --repo "$PWD"`，确认与 Steps 2–4 完全相同后才可提交可回退的 target-only 代码切片。暂存不等于 Commit 授权；不得使用 `--no-verify`。

- [x] **Step 6: 复用并核验发布运行静默门禁 Tasks 1–4 基线**

  核验 Execution Lock 前已独立完成的 Tasks 1–4 证据仍绑定当前基线：四表单 statement、硬超时、PostgreSQL integration owner、部署 cutover 测试 owner、精确 HEAVY mapping、性能证据和 Review 均有效。只在 Phase 10 diff 使其证据失效时刷新受影响验证，不重新实施或重复完整 Review。该切片只读四个目标表，不读取 legacy owner，不复制本计划 Task 4 drain predicate，不用 feature flag、legacy adapter 或双查询。任一证据失效且未刷新，Task 7 禁止开始。

- [x] **Step 7: 构建并验证最终不可变 candidate**

  在两个切片各自绿灯和 Review 后，构建唯一 candidate image，记录 source/image/config digest。渲染全部 Compose；以 candidate-container 连接当前数据库运行四表 readiness CLI，并验证 registered task、Beat schedule、queue、OpenAPI、env/mount、Client uniqueness、SQLModel production export 和旧 owner absence。此时只读预检，不停止现场 admission、Beat 或 worker。

  Expected: candidate 不注册旧 task、不加载旧 profile、不暴露旧 route，四表 CLI 可从 candidate-container 读取当前现场数据库；`wms-fulfillment`、共享 worker和 Redis 未误删。candidate digest 冻结后不得再修改生产、测试、脚本、配置或部署输入；任何变化都必须重新构建并刷新受影响证据。

### Task 7: 执行首次原子 cutover 与 Phase 11 handoff

**Classification:** 部署/运行切换；必须单独取得 Deploy/Cutover 授权。

**Interfaces:**

- Consumes: Task 6 不可变 candidate digest、已验证的四表 readiness CLI、Task 4 drain/cutover manifest 和 Deploy/Cutover 授权。
- Produces: Phase 10 exit evidence；只包含零消费者 schema identities 的 Phase 11 handoff。

- [x] **Step 1: 复核 candidate ready 后关闭旧 admission 与 Beat**

  先用 candidate-container 对当前现场数据库执行在线四表预检，并核对 candidate digest/config。只有结果为 `READY` 才停止旧 deployment 的 Nginx、优雅停止 API、验证 listener 关闭，再停止旧 Beat；旧 worker、数据库和 Redis 继续运行。在线 `BLOCK`、`WAIT_DRAIN` 或查询失败在不进入维护态时终止；listener 或 Beat 停止失败则保持维护态。两类失败都不得激活 candidate。

  现场预检为 `BLOCK`，首次尝试按门禁中止。用户随后明确授权清理联调数据与完整重建 `wes_db`，形成新的 empty-site
  maintenance 授权；候选未在 blocker 数据库上激活，入口关闭后才继续执行。

- [x] **Step 2: 由旧 worker 排空 legacy owner**

  旧部署的 consumer worker 继续处理已经落账的 RuntimeInbox/Intent/Outbox 等工作；执行 Task 4 的数据库同快照查询、broker inspection 和 producer freeze 复核。任一 active、ambiguous、identity conflict、查询失败或期间新增旧 row 都保持维护态，按原 identity 人工调查，不自动重发或清理。

- [x] **Step 3: 证明 legacy 连续稳定为零**

  按 cutover manifest 的间隔取得连续两次 database/broker/producer stable zero。只有两次都满足且无新旧 identity 冲突，才允许继续；旧 worker 此时仍运行。

- [x] **Step 4: 在旧 worker 仍运行时取得四表连续 READY**

  由同一不可变 candidate-container 读取现场 `DeviceCommand`、`TransportTask`、`InboundEvidence`、`WmsConfirmation`，按发布静默门禁取得连续两次 `READY`。`WAIT_DRAIN` 继续等待至批准的超时，`BLOCK`、查询失败或超时保持维护态；不得先停旧 worker 逼出静默。

  原保留数据路径因 blocker 未满足；在旧 worker 仍运行时先完成 legacy stable zero，随后按用户单独授权停止全部 writer、备份并
  完整重建精确数据库。新库初始化后由同一候选间隔 30 秒取得两次 `READY`。这是联调 empty-site rebuild 的显式例外，不能
  复用为日常 FULL 发布，也不能作为生产数据清理授权。

- [x] **Step 5: 停止旧 worker 并激活 target-only candidate**

  只有 legacy stable zero 与四表连续 READY 同时成立后，才有序停止旧共享 worker并确认无 task 正在执行；随后激活不可变 candidate。禁止 old/new worker、Beat、API 或 profile 并行，不 purge 共享 Redis queue。

  本次按 Step 4 记录的 empty-site rebuild 例外，在 legacy stable zero 后先停止全部 writer，完成备份/重建和两次候选 `READY`，
  再激活 target-only candidate；全程没有 old/new worker、Beat 或 API 并行，也未 purge Redis。

- [x] **Step 6: 重开 admission 前复核 absence 与 readiness**

  在 candidate 运行态复核旧 import/task/Beat/route/env/mount/production schema owner absence、registered tasks/queues 和四表 readiness。任何失败保持 admission 关闭，不自动 fallback 到旧路径；成功后才启动 target Beat/API/Nginx 并验证 listener。

- [x] **Step 7: 形成 Phase 10 exit 证据**

  记录 source/image/config digest、legacy drain snapshots、四表连续 READY、task/queue/route/config absence、QUALITY/HEAVY 和未验证现场边界。健康检查、Mock、部署成功不等于设备、供应商或业务验收。

- [x] **Step 8: 只交接零消费者 schema identities**

  对 Task 0 的每个 schema-deferred identity证明只有 model definition、`migrations/env.py`、已有 revisions 和 schema-only tests可引用，再将 schema/name/FK/index identity交给项目外归档 `../archive_docs/wes_backend/docs/superpowers/plans/2026-08-15-wes-schema-and-migration-baseline-reset-completed-2026-08-31.md` 记录的 Task 1 重新冻结。Phase 10 不传递 DDL、revision ID 或基线生成方案。

  已交接 22 个 cleanup matrix `schema-deferred` table/model identity；Phase 11 只把它们作为 Task 1 重新枚举 FK、index 与
  PostgreSQL 专有对象的输入，不把本次 handoff 当作删除授权。

---

## Phase 10 完成定义

Tasks 0–7 已满足仓内实施、准入与联调环境 target-only cutover；Phase 10 当前状态是
`COMPLETED — DEPLOYED TO INTEGRATION — NOT SUPPLIER OR BUSINESS ACCEPTED`。其中 empty-site rebuild 只绑定本次用户授权，
不改变普通发布的保留数据门禁：

1. Phase 9 exit 已证明，Task 0 所有 successor 唯一且 `UNRESOLVED=0`；
2. 旧 producer 封闭，数据库与 broker 一次性 drain 连续稳定为零；本次联调四账本 blocker 未冒充物理闭合，而是在可验证备份后
   按用户明确授权完整重建 empty-site 数据库，全程无盲重发；
3. production/package export/API/OpenAPI/permission/Celery/Beat/Compose/env/mount/script/current-doc 对旧 owner 零引用；
4. target Safety/START/Resource、DeviceCommand、TransportTask、Material/BinExecution、InboundEvidence、WmsConfirmation 和插件 owner 测试闭合；
5. 生产 `AsyncClient` constructor 只有 Phase 2 factory，无同步 raw Client、scripts raw Client、业务 direct httpx import 或重复 WMS pool；ECS endpoint pool 仍按 canonical endpoint 复用；
6. 现有 cleanup matrix、business absence gate、legacy absence test、HTTP boundary test、architecture gate 与最终 snapshot 一致，没有第二套同义门禁；
7. QUALITY、staged selector、必选 HEAVY、candidate composition 和唯一 Review 全部有效；
8. Phase 11 handoff 只含已证明零生产消费者的 schema/model identity，revision chain 未修改；
9. 首次 cutover 只运行 target candidate，未使用双写、兼容、fallback、feature flag 或自动清理；
10. 发布静默门禁 Tasks 1–4 已在 Execution Lock 前作为独立行为切片完成 RED/DEV/GREEN/Review，Task 6 只复核并将其纳入同一不可变 candidate，普通 FULL 发布继续长期复用。

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | 本轮未运行 |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | — | 本轮未运行嵌套 Codex |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR | 2 个计划矛盾已闭环；fresh findings 0，critical gaps 0 |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | N/A | 后端清理与发布门禁，无 UI 范围 |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | N/A | 本轮不改变开发者接口 |

**HISTORICAL VERDICT:** ENG CLEARED — ready to implement within the frozen gate order

**CURRENT VERDICT:** Tasks 0–7 COMPLETED；#187 已合入 `develop@97e6887a`；此前 target-only candidate 已部署到联调环境，
但 merge commit 未证明重新部署；旧 backend runtime consumer 为零，Phase 11 schema handoff 已形成；未完成供应商、设备物理或业务验收。

NO UNRESOLVED DECISIONS
