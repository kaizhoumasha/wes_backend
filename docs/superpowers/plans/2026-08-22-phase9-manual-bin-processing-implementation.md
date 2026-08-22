# Phase 9 Manual Bin Processing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付可独立运行和测试的最小 Bin 执行基础，并让 3、4 号人工 WorkLine 跑通 Task、货架、批量投箱、SCAN1～SCAN4、人工工作位、回库和 NG 的首个真实纵向切片。

**Architecture:** 基础能力只新增 `BinExecution` 和唯一 `PositionProjection`，并复用 `TransportTask`、`DeviceCommand`、`LineRunEpoch`、
`InboundEvidence`、`WmsConfirmation`。人工业务分为两个明确 owner：`src/app/manual_bin_processing/` 是依赖基础端口的业务应用模块，拥有
`ManualTask`、INGRESS 计数和事务编排；`workline_plugins/manual_bin_processing/` 是只依赖 SDK 的纯逻辑插件，只把类型化 Fact 映射为
封闭 Decision。二者均不被基础模块反向导入；不扩展旧 Runtime/Effect 平台，不建立通用工作流、动态 registry、逐 Bin INGRESS 或第二套可靠交互。

**Tech Stack:** Python 3.13、FastAPI、SQLModel/SQLAlchemy、PostgreSQL、Alembic、Celery、pytest、现有 WES Plugin SDK、现有 WMS/Transport/Device 端口。

**Spec:** `docs/contracts/wms-manual-bin-processing-integration-requirements.md`、`docs/contracts/device-annexes/manual-bin-processing-device-contract.md`、`docs/contracts/openapi/wes-wms-manual-bin-processing.openapi.json`

## Global Constraints

- 基础能力可在没有任何具体业务模块或插件时独立安装、启动、迁移和测试；业务应用模块只能依赖公开 Service/Repository 端口，
  纯逻辑插件只能依赖 WES Plugin SDK。基础模块禁止导入 `src.app.manual_bin_processing` 或 `manual_bin_processing`。
- 系统未发布：直接替换旧字段、表和调用点，不保留 alias、shim、wrapper、fallback、双写、旧数据迁移或 downgrade。
- 禁止新增 `WmsExchange`、`ManualBinFlow`、`ManualInboundBatch`、`ManualCtuActionClaim`、逐 Bin INGRESS、通用 Task 基类、工作流 DSL 或动态 capability registry。
- `PositionProjection` 直接替换 `TransportPositionProjection`，任何时刻只允许一个当前位置真源。
- INGRESS 只保存不可变 evidence 和 `occupied_count`；RETURN_BUFFER 使用 PositionProjection 的 `positioned_at` 形成每线 FIFO。
- WMS 入站复用 `InboundEvidence`；WES 出站复用 `WmsConfirmation`；旧 RuntimeInbox/Effect/SystemOutbox 不获得任何 Phase 9 新消费者。
- 代码行为严格 TDD；基础测试、业务应用测试和插件纯逻辑测试分别拥有自己的不变量。插件测试位于
  `workline_plugins/manual_bin_processing/tests/`，不访问数据库，也不进入核心默认 pytest、覆盖率或核心 HEAVY；业务持久化测试位于
  `tests/business/manual_bin_processing/` 和 `tests/integration/manual_bin_processing/`，不得放进插件包。
- 设备 evidence 应用后只通过显式注入的窄通知端口唤醒消费者；`DeviceEvidenceService` 不导入或分支判断人工业务，manual deployment
  显式装配自己的 worker，不接旧 `FactProcessor` 或 Runtime signal。
- 业务应用模块定义窄 `ManualPolicyPort` 并只处理事务、可靠对象和状态；独立插件实现类型化 Handler，deployment adapter 把固定 Handler
  tuple 注入该端口。业务模块不导入插件包，插件也不导入业务模块；不新增通用插件运行时。
- 每个生产符号修改前执行 GitNexus upstream impact；HIGH/CRITICAL 在首个生产补丁前按项目 Execution Lock 一次性获得范围授权。
- Migration 必须用 `uv run alembic revision -m "..."` 生成随机 revision，再编辑；不迁移开发数据，`downgrade()` 明确不支持。
- `docs/hardware/` 只读，不修改、不归档；供应商一致性和现场 E2E 独立于本机 Mock 与后端 RC。
- 每个任务末尾只记录建议提交边界；计划批准不构成 Commit、Push、PR、Merge 或 Deploy 授权。未获得当次明确授权时，只保留工作树和验证证据。

---

## 实施前冻结清单

首个生产补丁前固定以下证据，并把结果写入实施记录：

- base/head、staged/unstaged/untracked manifest 和无关 dirty 文件指纹；
- `TransportPositionProjection`、`TransportService`、`WmsConfirmationService`、`InboundEvidenceService`、
  `DeviceCommandService`、`LineRunEpochService`、WMS events route 和 rough sorter 静态组合的调用者；
- 对应核心、WMS Adapter、插件、integration、E2E 测试 owner，以及 `docs/architecture/heavy-test-impact.toml` 当前映射；
- 本计划的直接删除/交接矩阵：

冻结结果是各 Task `Files` 与验证命令的实施下限。若当前基线出现本计划未列出的生产调用者、Alembic metadata 入口、fixture/helper、
测试 owner 或 HEAVY 消费者，必须先把该传播面回写到对应 Task，再开始首个生产补丁；不得把整目录失败或最终门禁当成调用点清单。

| 当前 owner | Phase 9 处置 |
| --- | --- |
| `TransportPositionProjection` | 直接改名和收敛为 `PositionProjection`；Transport 改为消费者 |
| `WmsConfirmation.material_execution_id` 强制绑定 | 增加必填 Epoch 关联并将 MaterialExecution 关联改为可空；保留同一可靠生命周期 |
| WMS Event 的 MaterialExecution 单一处理分支 | 静态增加 manual operation 分支并写 `InboundEvidence`；不接旧 RuntimeInbox |
| `RuntimeInbox`、WMS Effect、SystemOutbox 热路径 | Phase 9 零新增消费者；按既定 Phase 10 清单删除 |
| rough sorter 静态装配 | 保留；组合根显式并列装配 manual，不复制基础 runtime |

## Task 1: 建立最小 BinExecution 并直接替换位置投影

**Files:**

- Create: `src/app/execution/models/bin_execution.py`
- Create: `src/app/execution/models/position_projection.py`
- Create: `src/app/execution/repositories/bin_execution_repository.py`
- Create: `src/app/execution/repositories/position_projection_repository.py`
- Create: `src/app/execution/services/bin_execution_service.py`
- Create: `src/app/execution/services/position_projection_service.py`
- Modify: `src/app/execution/models/__init__.py`
- Modify: `src/app/execution/repositories/__init__.py`
- Modify: `src/app/execution/services/__init__.py`
- Modify: `src/app/transport/models.py`
- Modify: `src/app/transport/repository.py`
- Modify: `src/app/transport/service.py`
- Modify: `migrations/env.py`
- Create: Alembic revision generated with message `add bin execution and converge position projection`
- Test: `tests/runtime/execution/test_bin_execution_service.py`
- Test: `tests/runtime/execution/test_position_projection_service.py`
- Modify: `tests/runtime/transport/conftest.py`
- Modify: `tests/runtime/transport/test_transport_acceptance_edges.py`
- Modify: `tests/runtime/transport/test_transport_observability.py`
- Modify: `tests/runtime/transport/test_transport_outcome.py`
- Modify: `tests/runtime/transport/test_transport_reconciling_facts.py`
- Modify: `tests/runtime/transport/test_transport_service.py`
- Modify: `tests/runtime/transport/test_transport_submit_fencing.py`
- Modify: `tests/support/transport_projections.py`
- Test: `tests/integration/execution/test_bin_execution_constraints.py`
- Test: `tests/integration/execution/test_position_projection_constraints.py`
- Modify: `tests/integration/transport/test_dark_transport_loop.py`
- Modify: `tests/integration/transport/test_transport_evidence_transaction.py`
- Modify: `tests/integration/transport/test_transport_schema.py`
- Modify: `tests/integration/test_transport_fulfillment_queue.py`
- Modify: `tests/e2e/transport/test_transport_production_wiring.py`
- Modify: `docs/architecture/heavy-test-impact.toml`

**Interfaces:**

- Produces: `BinExecutionService.create_from_scan(...)`、`mark_ng_once(...)`、`close_returned(...)`、`close_ng_removed(...)`。
- Produces: `PositionProjectionService.apply_evidence_position(...)`、`clear_on_departure(...)` 和按 WorkLine/位置/`positioned_at` 的 RETURN 连续前缀查询。
- Constraint: `BinExecution` 只含 execution identity、`bin_id`、Epoch/owner WorkLine、`ACTIVE|CLOSED`、单调 `ng_reason_code`、close reason/time；不得含 `task_id` 或重复位置。

- [ ] **Step 1: 完成 GitNexus 影响分析并冻结所有调用点**

  对 `TransportPositionProjection`、Transport repository/service 的读写符号批量执行 upstream impact；列出生产调用者、测试 owner 和 HIGH/CRITICAL 风险，获得一次范围授权后再改。

- [ ] **Step 2: 写核心 RED 测试**

  锁定：同一 `bin_id` 只能有一个活动执行；NG 原因只能首次设置；关闭原因只允许 RETURNED/NG_REMOVED；位置更新必须引用可靠 evidence；RETURN 查询按本线 `positioned_at,id` 连续排序。

  Run: `uv run pytest tests/runtime/execution/test_bin_execution_service.py tests/runtime/execution/test_position_projection_service.py -q`

  Expected: FAIL，原因是新模型/服务尚不存在。

- [ ] **Step 3: 最小实现基础模型和 Service**

  不增加任务、插件或 WMS 语义；用 Repository 锁和数据库唯一约束保证活动 Bin 与当前位置唯一性。

- [ ] **Step 4: 一次性迁移 Transport 消费者**

  先列全 `TransportPositionProjection` 读写点，再将 Transport、测试 fixture/helper 和现有测试 owner 一次性改为使用
  `PositionProjection`；同步把 `migrations/env.py` 的 metadata 注册切换到 `BinExecution`/`PositionProjection`。删除旧类、旧 repository API、
  旧表和导出，不修改历史 migration。

- [ ] **Step 5: 生成并编辑直接替换 migration**

  新建 BinExecution/PositionProjection 表，删除旧投影表；不复制历史数据，不提供 downgrade。

- [ ] **Step 6: GREEN 与 PostgreSQL 约束验证**

  Run:
  `uv run pytest tests/runtime/execution/test_bin_execution_service.py tests/runtime/execution/test_position_projection_service.py tests/runtime/transport -q`

  Run:
  `uv run pytest tests/integration/execution/test_bin_execution_constraints.py tests/integration/execution/test_position_projection_constraints.py tests/integration/transport/test_transport_schema.py tests/integration/transport/test_transport_evidence_transaction.py tests/integration/transport/test_dark_transport_loop.py tests/integration/test_transport_fulfillment_queue.py -q`

  Expected: PASS，且 `rg "TransportPositionProjection|transport_position_projection" src migrations/env.py` 零命中，
  同一表达式在 `tests` 只允许新 migration/schema owner 的旧表缺席断言命中；
  `migrations/versions/**` 历史 migration 保持不变且不纳入缺席范围；
  `tests/e2e/transport/test_transport_production_wiring.py` 由更新后的 HEAVY mapping 选中，不由基础单元测试代证。

- [ ] **Step 7: 更新 HEAVY mapping 并记录提交检查点**

  获得单独 Commit 授权后建议使用：`feat(execution): 建立 Bin 执行与唯一位置投影`

## Task 2: 复用 WmsConfirmation 与 InboundEvidence 承载人工 wire

**Files:**

- Modify: `src/app/execution/models/wms_confirmation.py`
- Modify: `src/app/execution/services/wms_confirmation_service.py`
- Modify: `src/app/execution/services/decision_applier.py`
- Modify: `src/app/execution/repositories/wms_confirmation_repository.py`
- Modify: `src/app/execution/models/inbound_evidence.py`
- Modify: `src/app/execution/services/inbound_evidence_service.py`
- Create: `src/app/wms_adapter/manual_bin_wire.py`
- Create: `src/app/wms_adapter/manual_bin_openapi.py`
- Modify: `src/app/wms_adapter/inbound_adapter.py`
- Modify: `src/app/wms_adapter/inbound_event_handler.py`
- Modify: `src/app/wms_adapter/v1/events.py`
- Modify: `src/register.py`
- Create: Alembic revision generated with message `extend reliable wms evidence for bin execution`
- Test: `tests/runtime/execution/test_wms_confirmation_service.py`
- Test: `tests/runtime/execution/test_inbound_evidence_service.py`
- Modify: `tests/runtime/execution/test_decision_applier.py`
- Modify: `tests/runtime/execution/test_fact_processor.py`
- Modify: `tests/contracts/wms_adapter/test_inbound_adapter.py`
- Test: `tests/contracts/wms_adapter/test_manual_bin_wire.py`
- Test: `tests/contracts/wms_adapter/test_manual_bin_openapi.py`
- Test: `tests/api/test_wms_manual_bin_events.py`
- Modify: `tests/contracts/wms_adapter/test_inbound_event_handler.py`
- Modify: `tests/api/test_wms_transport_events.py`
- Modify: `tests/api/test_qa_regression_transport_openapi.py`
- Modify: `tests/deployment/test_rough_sorter_plugin_startup.py`
- Modify: `tests/integration/execution/test_decision_processing_postgresql.py`
- Modify: `tests/integration/execution/test_execution_constraints.py`
- Modify: `tests/integration/wms_adapter/test_inbound_confirmation_postgresql.py`
- Test: `tests/integration/wms_adapter/test_manual_bin_evidence_postgresql.py`
- Modify: `docs/architecture/heavy-test-impact.toml`

**Interfaces:**

- Changes: `WmsConfirmationService.create_or_get(..., line_run_epoch_id: int, material_execution_id: Optional[int], ...)`。
- Changes: WMS `InboundEvidence` 可选关联 `bin_execution_id`，但 WMS Event 在创建执行前允许两个 execution FK 都为空。
- Produces: 当前合同 13 个 operation 的严格 parser/schema；已有 rough sorter operation 行为不变。

- [ ] **Step 1: 写可靠性 RED 测试**

  覆盖不绑定 MaterialExecution 的 confirmation、必填 Epoch、冻结消息重放、response evidence、冲突进入 RECONCILING，以及 manual WMS Event 先 evidence 后 ACK。

- [ ] **Step 2: 最小放宽 WmsConfirmation**

  增加必填 `line_run_epoch_id`，将 `material_execution_id` 改为可空；派发不再为取得 Epoch 强制加载 MaterialExecution。禁止新建 `WmsExchange`、新状态或新 worker。
  当前 `DecisionApplier` 路径必须显式传入 `execution.line_run_epoch_id + execution.id`，manual 路径传入当前 Epoch 与空
  `material_execution_id`；business-wait follow-up 沿用原 confirmation 的两个关联，不能重新推导或丢失 Epoch。

- [ ] **Step 3: 扩展 InboundEvidence 当前关联**

  增加可选 `bin_execution_id` 和互斥/一致性约束；保留现有 source identity、digest、conflict 和 apply status 语义。

- [ ] **Step 4: 写并接入 manual 严格 wire**

  按同级 OpenAPI 实现 operation 与 data 联合；在现有 WMS events route 中使用显式 operation 分支，不建立动态 registry，不接 RuntimeInbox。

- [ ] **Step 5: 生成直接 schema migration**

  清理开发/测试数据后改变 FK/非空约束；不添加兼容列、回填或双写。

- [ ] **Step 6: 执行 GREEN**

  Run:
  `uv run pytest tests/runtime/execution/test_wms_confirmation_service.py tests/runtime/execution/test_inbound_evidence_service.py tests/runtime/execution/test_decision_applier.py tests/runtime/execution/test_fact_processor.py tests/contracts/wms_adapter/test_inbound_adapter.py tests/contracts/wms_adapter/test_inbound_event_handler.py tests/contracts/wms_adapter/test_manual_bin_wire.py tests/contracts/wms_adapter/test_manual_bin_openapi.py tests/api/test_wms_transport_events.py tests/api/test_qa_regression_transport_openapi.py tests/api/test_wms_manual_bin_events.py tests/deployment/test_rough_sorter_plugin_startup.py -q`

  Run:
  `uv run pytest tests/integration/execution/test_decision_processing_postgresql.py tests/integration/execution/test_execution_constraints.py tests/integration/wms_adapter/test_inbound_confirmation_postgresql.py tests/integration/wms_adapter/test_manual_bin_evidence_postgresql.py -q`

  Expected: PASS；现有 rough sorter 的 confirmation 创建、派发和 business-wait 行为不变；
  `rg "WmsExchange|RuntimeInbox" src/app/wms_adapter src/app/execution` 无 Phase 9 新生产引用。

- [ ] **Step 7: 更新 HEAVY mapping 并记录提交检查点**

  获得单独 Commit 授权后建议使用：`feat(wms): 复用可靠确认支持人工工作线合同`

## Task 3: 建立人工业务应用模块、独立插件和最小业务状态

**Files:**

- Create: `workline_plugins/manual_bin_processing/pyproject.toml`
- Create: `workline_plugins/manual_bin_processing/src/manual_bin_processing/__init__.py`
- Create: `workline_plugins/manual_bin_processing/src/manual_bin_processing/activation.py`
- Create: `workline_plugins/manual_bin_processing/src/manual_bin_processing/facts.py`
- Create: `workline_plugins/manual_bin_processing/src/manual_bin_processing/plugin.py`
- Create: `workline_plugins/manual_bin_processing/src/manual_bin_processing/handlers/__init__.py`
- Create: `workline_plugins/manual_bin_processing/src/manual_bin_processing/handlers/task_changed.py`
- Create: `src/app/manual_bin_processing/models.py`
- Create: `src/app/manual_bin_processing/repository.py`
- Create: `src/app/manual_bin_processing/ports.py`
- Create: `src/app/manual_bin_processing/task_service.py`
- Create: `src/app/manual_bin_processing/__init__.py`
- Create: Alembic revision generated with message `add manual bin processing state`
- Test: `workline_plugins/manual_bin_processing/tests/test_package_boundary.py`
- Test: `workline_plugins/manual_bin_processing/tests/test_activation.py`
- Test: `workline_plugins/manual_bin_processing/tests/test_task_policy.py`
- Test: `tests/business/manual_bin_processing/test_task_service.py`
- Test: `tests/integration/manual_bin_processing/test_manual_state_postgresql.py`
- Modify: `tests/support/test_suite_topology.py`
- Modify: `tests/README.md`
- Modify: `docs/architecture/heavy-test-impact.toml`

**Interfaces:**

- Produces: 插件纯业务 policy，只接收不可变 snapshot 并返回有限动作；不导入 `src`、数据库、HTTP、Celery 或 Repository。
- Produces: 业务应用模块的 `ManualTask` 单表，字段严格为合同 §5；`source_rack_faces` 使用规范化 JSON 数组和当前索引，不拆第二张计划表。
- Produces: `ManualIngressCounter(line_run_epoch_id, occupied_count)`；capacity 只读 Epoch snapshot，不重复存储。

- [ ] **Step 1: 写包边界和激活 RED 测试**

  固定插件键 `manual_bin_processing`、版本、3/4 线角色、逻辑动作映射、INGRESS capacity 与位置 binding；禁止包导入核心实现。

- [ ] **Step 2: 最小实现 activation/facts/plugin/handler**

  复用 rough sorter 已验证的显式 `plugin.py`、类型化 Fact 和 Handler 结构，但不复制其业务判断；插件只依赖 SDK，并由 deployment adapter
  注入业务应用的 `ManualPolicyPort`。

- [ ] **Step 3: 写 ManualTask/Counter RED 测试**

  在 `tests/business/manual_bin_processing/` 覆盖 revision 连续、准备后队列不可改、一线一个未完成 Task、计划增量只追加五层货架面、
  occupied_count 不可负；不得用插件 Handler 测试替代业务应用 Service 测试。

- [ ] **Step 4: 实现单表 Task 与单行 Counter**

  不增加 `ManualBinFlow`、入站成员表、通用 Task 基类、状态历史表或 JSON 通用插件状态。

- [ ] **Step 5: 生成 migration 并运行 GREEN**

  Run:
  `uv run pytest -c workline_plugins/manual_bin_processing/pyproject.toml workline_plugins/manual_bin_processing/tests/test_package_boundary.py workline_plugins/manual_bin_processing/tests/test_activation.py workline_plugins/manual_bin_processing/tests/test_task_policy.py -q`

  Run:
  `uv run pytest tests/business/manual_bin_processing/test_task_service.py -q`

  Run:
  `uv run pytest tests/integration/manual_bin_processing/test_manual_state_postgresql.py -q`

  Expected: PASS；插件测试未出现在核心默认 collect 中。

- [ ] **Step 6: 更新测试拓扑与 HEAVY mapping，记录提交检查点**

  在 `tests/support/test_suite_topology.py` 登记 `tests/business`，并在 `tests/README.md` 将其定义为业务应用 FAST Service 测试 owner；
  不把具体 WorkLine 插件流程迁入核心测试树。

  Run:
  `uv run pytest tests/architecture/test_suite_topology_guardrail.py tests/architecture/test_core_plugin_test_ownership_guardrail.py -q`

  获得单独 Commit 授权后建议使用：`feat(manual-bin): 建立业务应用、独立插件与最小任务状态`

## Task 4: 打通 Task、计划增量与货架串行搬运

**Files:**

- Create: `src/app/manual_bin_processing/rack_service.py`
- Create: `deployment/manual_bin_processing_composition.py`
- Modify: `src/app/manual_bin_processing/task_service.py`
- Modify: `src/app/wms_adapter/inbound_event_handler.py`
- Modify: `workline_plugins/manual_bin_processing/src/manual_bin_processing/facts.py`
- Modify: `workline_plugins/manual_bin_processing/src/manual_bin_processing/plugin.py`
- Modify: `workline_plugins/manual_bin_processing/src/manual_bin_processing/handlers/task_changed.py`
- Test: `workline_plugins/manual_bin_processing/tests/test_task_policy.py`
- Test: `tests/business/manual_bin_processing/test_task_lifecycle.py`
- Test: `tests/business/manual_bin_processing/test_rack_service.py`
- Test: `tests/integration/manual_bin_processing/test_task_and_rack_postgresql.py`

**Interfaces:**

- Consumes: `TransportService.move_rack/rotate_rack` 当前公开端口和 Transport 回调的实际 `rack_id + rack_face`。
- Produces: issued → queue_changed → prepare → plan_delta → ACTIVE 的唯一事务 owner。
- Produces: 固定选择“当前未完成面、同架另一面、revision/数组顺序”，不同架严格旧出新入。

- [ ] **Step 1: 写 Task/货架 RED 场景**

  覆盖未知 task_kind、排队修订、WES 选 3/4 线、prepare 冻结 WorkLine/Epoch、plan revision 缺口、同架 RACK_ROTATE、不同架串行 RACK_MOVE、位置未知等待。

- [ ] **Step 2: 实现 Task 入站与 prepare**

  使用 Task repository 锁和现有 WmsConfirmation；不把 Task 状态写入 BinExecution。

- [ ] **Step 3: 实现最小 RackService**

  只组织现有 Transport；不实现 RCS Client、搬运重试、资源锁或位置查询。

- [ ] **Step 4: 执行 GREEN**

  Run:
  `uv run pytest -c workline_plugins/manual_bin_processing/pyproject.toml workline_plugins/manual_bin_processing/tests/test_task_policy.py -q`

  Run:
  `uv run pytest tests/business/manual_bin_processing/test_task_lifecycle.py tests/business/manual_bin_processing/test_rack_service.py -q`

  Run:
  `uv run pytest tests/integration/manual_bin_processing/test_task_and_rack_postgresql.py -q`

- [ ] **Step 5: 记录提交检查点**

  获得单独 Commit 授权后建议使用：`feat(manual-bin): 接入任务计划与货架搬运`

## Task 5: 实现两步入站和 INGRESS 计数

**Files:**

- Create: `src/app/manual_bin_processing/ingress_service.py`
- Modify: `deployment/manual_bin_processing_composition.py`
- Modify: `src/app/wms_adapter/inbound_adapter.py`
- Modify: `workline_plugins/manual_bin_processing/src/manual_bin_processing/facts.py`
- Modify: `workline_plugins/manual_bin_processing/src/manual_bin_processing/plugin.py`
- Create: `workline_plugins/manual_bin_processing/src/manual_bin_processing/handlers/inbound_batch_decided.py`
- Test: `workline_plugins/manual_bin_processing/tests/test_ingress_policy.py`
- Test: `tests/business/manual_bin_processing/test_ingress_service.py`
- Test: `tests/integration/manual_bin_processing/test_ingress_counter_postgresql.py`

**Interfaces:**

- Consumes: `workline.bin.inbound_batch_decide@v1`、现有 `TransportService.move_bins`、`transport.task.resulted@v1`。
- Produces: `max_bin_count=min(4, capacity-occupied_count)`、一次 1..4 Bin 的批量 Transport、按 SUCCEEDED 数量一次入账。

- [ ] **Step 1: 写两步入站 RED 测试**

  覆盖货架未到不请求、容量为零不请求、READY 同面/数量校验、NO_BATCH、RACK_FACE_DONE、一次批量 BIN_MOVE、整批结果重复不重复计数。

- [ ] **Step 2: 实现入站决定与 Transport 创建**

  READY 成员只作为冻结 Transport 输入；不创建 BinExecution、INGRESS entry 或 PositionProjection。

- [ ] **Step 3: 实现最终结果计数**

  在应用唯一 Transport evidence 的事务内按 SUCCEEDED 数增加 Counter；不消费 `member_position_changed@v1`，不增加 batch completion operation。

- [ ] **Step 4: 执行 GREEN**

  Run:
  `uv run pytest -c workline_plugins/manual_bin_processing/pyproject.toml workline_plugins/manual_bin_processing/tests/test_ingress_policy.py -q`

  Run:
  `uv run pytest tests/business/manual_bin_processing/test_ingress_service.py -q`

  Run:
  `uv run pytest tests/integration/manual_bin_processing/test_ingress_counter_postgresql.py -q`

- [ ] **Step 5: 记录提交检查点**

  获得单独 Commit 授权后建议使用：`feat(manual-bin): 实现批量投箱与入口计数`

## Task 6: 实现 SCAN1 路由、不可读跨线链和 NG

**Files:**

- Create: `src/app/manual_bin_processing/conveyor_service.py`
- Modify: `deployment/manual_bin_processing_composition.py`
- Modify: `src/app/device/services/device_evidence_service.py`
- Modify: `src/core/task_queue_gateway.py`
- Create: `src/celery_app/tasks/manual_bin_processing.py`
- Modify: `workline_plugins/manual_bin_processing/src/manual_bin_processing/facts.py`
- Modify: `workline_plugins/manual_bin_processing/src/manual_bin_processing/plugin.py`
- Create: `workline_plugins/manual_bin_processing/src/manual_bin_processing/handlers/scan_completed.py`
- Test: `workline_plugins/manual_bin_processing/tests/test_scan1_routing.py`
- Test: `workline_plugins/manual_bin_processing/tests/test_unreadable_and_ng.py`
- Test: `tests/business/manual_bin_processing/test_conveyor_service.py`
- Test: `tests/integration/manual_bin_processing/test_scan1_transaction_postgresql.py`
- Test: `tests/contracts/device/test_manual_bin_processing_device_contract.py`
- Modify: `tests/runtime/device_command/test_evidence_service.py`

**Interfaces:**

- Consumes: SCAN_COMPLETED evidence、`workline.bin.route_decide@v1`、现有 DeviceCommand 应用端口。
- Produces: 可靠可读 SCAN1 创建 BinExecution；不可读命令引用 `SCAN_EVIDENCE`；命令成功原子减少来源线计数。
- Produces: `DeviceEvidenceService` 只调用注入的 `EvidenceAppliedNotifier`；默认 foundation notifier 保留现有 execution wake，manual
  composition 追加显式 manual queue wake。基础模块不知道 plugin key、Task、INGRESS 或人工状态。

- [ ] **Step 1: 写 Device DTO 与路由 RED 测试**

  覆盖 READABLE/UNREADABLE 严格联合、MOVE_TOP/MOVE_RIGHT、无 Task 正常旁路、已有 NG 不问 WMS、WMS 未确定则停当前 Bin。

- [ ] **Step 2: 实现 SCAN1 事务**

  先保存 evidence，再创建/锁 BinExecution；Transport 计数未入账时只保存扫码，不发命令；同 Bin 活动冲突只围栏该 Bin。

- [ ] **Step 3: 写不可读/NG RED 测试**

  覆盖 SCAN1→各线 SCAN3 向右、下游首次读出创建 NG execution、全程不可读零 execution、下游不动 INGRESS、第四线进入 NGZone。

- [ ] **Step 4: 实现固定跨线规则**

  使用 Epoch 冻结的左右拓扑和四个逻辑动作映射；不建立跨线执行引擎或下一线状态机。

- [ ] **Step 5: 实现 NG 人工移除关闭**

  `workline.bin.ng_removed@v1` 只接受 WMS/PDA 在操作员实际取走后形成的
  `bin_id + ng_zone_code + scan_evidence_id + removed_at` 冻结事实；关闭唯一活动 NG execution 并清除位置投影。匿名或冲突只记录
  evidence，单纯到达 NGZone 不关闭执行。

- [ ] **Step 6: 执行 GREEN**

  Run:
  `uv run pytest tests/contracts/device/test_manual_bin_processing_device_contract.py -q`

  Run:
  `uv run pytest -c workline_plugins/manual_bin_processing/pyproject.toml workline_plugins/manual_bin_processing/tests/test_scan1_routing.py workline_plugins/manual_bin_processing/tests/test_unreadable_and_ng.py -q`

  Run:
  `uv run pytest tests/runtime/device_command/test_evidence_service.py tests/business/manual_bin_processing/test_conveyor_service.py -q`

  Run:
  `uv run pytest tests/integration/manual_bin_processing/test_scan1_transaction_postgresql.py -q`

- [ ] **Step 7: 记录提交检查点**

  获得单独 Commit 授权后建议使用：`feat(manual-bin): 实现扫码路由与 NG 链路`

## Task 7: 实现 SCAN2 人工工作位和业务释放

**Files:**

- Modify: `src/app/manual_bin_processing/conveyor_service.py`
- Modify: `src/app/wms_adapter/inbound_event_handler.py`
- Modify: `workline_plugins/manual_bin_processing/src/manual_bin_processing/facts.py`
- Modify: `workline_plugins/manual_bin_processing/src/manual_bin_processing/plugin.py`
- Create: `workline_plugins/manual_bin_processing/src/manual_bin_processing/handlers/workstation_changed.py`
- Test: `workline_plugins/manual_bin_processing/tests/test_workstation_policy.py`
- Test: `tests/business/manual_bin_processing/test_workstation_flow.py`
- Test: `tests/integration/manual_bin_processing/test_workstation_flow_postgresql.py`

**Interfaces:**

- Consumes: `manual.bin.workstation_arrived@v1`、`manual.bin.release_decided@v1`、DeviceCommand。
- Produces: RETURN 释放到普通 SCAN3；NG 单调标记后释放到 SCAN3；WMS/PDA 不触碰 ECS。

- [ ] **Step 1: 写工作位 RED 测试**

  覆盖只有 MOVE_TOP 到达才报 Fact、重复到达幂等、RETURN/NG 严格决定、NG 不可清除、Device 失败不推进。

- [ ] **Step 2: 实现到位 Fact 与释放 Event**

  复用 WmsConfirmation/InboundEvidence；不新增工作位任务表或物料状态。

- [ ] **Step 3: 实现 SCAN2 RELEASE DeviceCommand**

  使用 Epoch 映射和 BinExecution reference；CALLBACK 成功结束 Task 对该 Bin 的本地动作，但不关闭执行。

- [ ] **Step 4: 执行 GREEN 并记录提交检查点**

  Run:
  `uv run pytest -c workline_plugins/manual_bin_processing/pyproject.toml workline_plugins/manual_bin_processing/tests/test_workstation_policy.py -q`

  Run:
  `uv run pytest tests/business/manual_bin_processing/test_workstation_flow.py -q`

  Run:
  `uv run pytest tests/integration/manual_bin_processing/test_workstation_flow_postgresql.py -q`

  获得单独 Commit 授权后建议使用：`feat(manual-bin): 接入人工工作位释放`

## Task 8: 实现 RETURN FIFO、回库与执行闭合

**Files:**

- Create: `src/app/manual_bin_processing/return_service.py`
- Modify: `src/app/manual_bin_processing/conveyor_service.py`
- Modify: `deployment/manual_bin_processing_composition.py`
- Modify: `workline_plugins/manual_bin_processing/src/manual_bin_processing/facts.py`
- Modify: `workline_plugins/manual_bin_processing/src/manual_bin_processing/plugin.py`
- Create: `workline_plugins/manual_bin_processing/src/manual_bin_processing/handlers/return_changed.py`
- Test: `workline_plugins/manual_bin_processing/tests/test_return_policy.py`
- Test: `tests/business/manual_bin_processing/test_return_service.py`
- Test: `tests/integration/manual_bin_processing/test_return_flow_postgresql.py`

**Interfaces:**

- Consumes: SCAN4 readable evidence、PositionProjection FIFO、`workline.bin.return_batch_decide@v1`、BIN_MOVE、RETURN_OUTPUT BIN_DEPARTED、`workline.bin.returned@v1`。
- Produces: 每线跨 Task FIFO、连续前缀最多 4 个、离口立即释放缓存、WMS 记录后关闭 execution。

- [ ] **Step 1: 写 FIFO RED 测试**

  覆盖 SCAN4 入列、缺失 execution 时按现场身份创建普通 execution、跨 Task 顺序、回库优先、连续前缀、NO_BATCH 不变、不跨线组批。

- [ ] **Step 2: 实现 PositionProjection-backed FIFO**

  直接查询 RETURN_BUFFER 位置，按 `positioned_at,id`；不增加 ReturnEntry 表、CTU capacity query 或队列 sequence。

- [ ] **Step 3: 写离口/Transport RED 测试**

  覆盖离开前失败保留位置、可靠 BIN_DEPARTED 立即清位置、离开后失败/未知保持 execution、最终成功加 returned Fact 才关闭。

- [ ] **Step 4: 实现回库搬运和闭合**

  READY 只接受候选连续前缀及精确 RackBinSlot；复用 Transport 高 revision 和 RECONCILING，不新增 WMS 对账 operation。

- [ ] **Step 5: 执行 GREEN 并记录提交检查点**

  Run:
  `uv run pytest -c workline_plugins/manual_bin_processing/pyproject.toml workline_plugins/manual_bin_processing/tests/test_return_policy.py -q`

  Run:
  `uv run pytest tests/business/manual_bin_processing/test_return_service.py -q`

  Run:
  `uv run pytest tests/integration/manual_bin_processing/test_return_flow_postgresql.py -q`

  获得单独 Commit 授权后建议使用：`feat(manual-bin): 实现退箱 FIFO 与回库闭合`

## Task 9: 完成任务闭合、STOP 和静态生产装配

**Files:**

- Modify: `src/app/manual_bin_processing/task_service.py`
- Modify: `deployment/manual_bin_processing_composition.py`
- Modify: `src/register.py`
- Modify: `src/celery_app/async_runtime.py`
- Modify: `workline_plugins/manual_bin_processing/src/manual_bin_processing/facts.py`
- Modify: `workline_plugins/manual_bin_processing/src/manual_bin_processing/plugin.py`
- Create: `workline_plugins/manual_bin_processing/src/manual_bin_processing/handlers/task_completion.py`
- Test: `workline_plugins/manual_bin_processing/tests/test_completion_policy.py`
- Test: `tests/business/manual_bin_processing/test_completion_and_stop.py`
- Test: `tests/deployment/test_manual_bin_processing_startup.py`
- Test: `workline_plugins/manual_bin_processing/tests/e2e/test_manual_business_loop.py`
- Create: `workline_plugins/manual_bin_processing/fixtures/business-loop-provider.yaml`
- Create: `workline_plugins/manual_bin_processing/fixtures/business-loop-seed.sql`

**Interfaces:**

- Consumes: `workline.task.completion_confirm@v1`、`workline.rack.departure_decide@v1`、现有静态插件 binding。
- Produces: COMPLETED/STALE/IN_PROGRESS 闭环；STOP 仅阻止新准备/入站；rough sorter 和 manual 明确并列装配。

- [ ] **Step 1: 写完成/STOP RED 测试**

  覆盖 stale delta 重放、business retry、新 operation identity、任务完成不等待物理尾巴、下一任务可准备、RETURN 仍优先、STOP 无 drain 新协议。

- [ ] **Step 2: 实现完成确认与货架离场**

  不把 return_batch/rack departure 复用为排空；无合格 RETURN 目标时保持 Epoch ACTIVE。

- [ ] **Step 3: 实现唯一静态组合根**

  Web/Celery 共用一个 manual runtime；显式装配已知 rough sorter/manual bindings，不扫描包、不复制 WmsClient/Transport/Device runtime。

- [ ] **Step 4: 写部署与 E2E RED**

  E2E 必须走真实 HTTP ingress、持久 evidence、WmsConfirmation worker、Transport callback、Device callback 和最终执行关闭；不得直接调 Service 跳过装配。

- [ ] **Step 5: 完成 Mock fixture 和 GREEN**

  Run:
  `uv run pytest tests/business/manual_bin_processing/test_completion_and_stop.py tests/deployment/test_manual_bin_processing_startup.py -q`

  Run:
  `uv run pytest -c workline_plugins/manual_bin_processing/pyproject.toml workline_plugins/manual_bin_processing/tests/test_completion_policy.py -q`

  Run:
  `uv run pytest -c workline_plugins/manual_bin_processing/pyproject.toml workline_plugins/manual_bin_processing/tests/e2e/test_manual_business_loop.py -q`

  Expected: PASS；仅证明本机 Mock 纵向切片，不标记供应商/现场验收。

- [ ] **Step 6: 记录提交检查点**

  获得单独 Commit 授权后建议使用：`feat(manual-bin): 完成人工纵向切片装配`

## Task 10: 闭合所有权、旧路径清单和最终门禁

**Files:**

- Modify: `docs/architecture/heavy-test-impact.toml`
- Modify: `docs/architecture/file_index.md`
- Modify: `docs/superpowers/README.md`
- Modify: `docs/superpowers/plans/2026-08-03-wes-architecture-convergence-master-plan.md`
- Archive outside repository: 仅本实施完成后被当前真源完整替代的过程计划，目标 `../archive_docs/wes_backend/`

**Interfaces:**

- Produces: Phase 9 直接旧 owner 零残留、Phase 10 精确 `DELETE → successor / NONE / RETAIN` 清单、当前测试与验证证据。

- [ ] **Step 1: 闭合生产符号与测试所有权**

  使用 GitNexus tests 和精确 `rg` 枚举 BinExecution、PositionProjection、WmsConfirmation、ManualTask、INGRESS、Device/Transport/WMS operation 的直接与间接测试；补齐 HEAVY mapping，禁止用插件测试证明基础能力。

- [ ] **Step 2: 执行旧概念缺席扫描**

  必须确认生产代码零命中：`WmsExchange`、`ManualBinFlow`、`ManualInboundBatch`、`ManualCtuActionClaim`、逐 Bin ingress entry、manual `member_position_changed` consumer、旧 manual operation 和 `TransportPositionProjection`。
  位置投影直接替换还须确认 `rg "TransportPositionProjection|transport_position_projection" src migrations/env.py` 零命中，
  同一表达式在 `tests` 只允许新 migration/schema owner 的旧表缺席断言命中；
  `migrations/versions/**` 是不可改写的历史 migration，不属于缺席范围。

- [ ] **Step 3: 运行聚焦与拓扑回归**

  Run:
  `uv run pytest tests/runtime/execution tests/runtime/transport tests/runtime/device_command tests/contracts/wms_adapter tests/contracts/device tests/architecture/test_suite_topology_guardrail.py tests/architecture/test_core_plugin_test_ownership_guardrail.py -q`

  Run:
  `uv run pytest -c workline_plugins/manual_bin_processing/pyproject.toml workline_plugins/manual_bin_processing/tests -q`

  Expected: 全部 PASS，integration/E2E 只在依赖就绪时显式运行且不得以 skipped 充当通过。

- [ ] **Step 4: 验证 migration 链**

  在独占临时 PostgreSQL 逻辑库从仓库 base 升到 head，并运行 Phase 9 migration/constraint integration owner；不得使用共享 dev 数据库。

- [ ] **Step 5: 运行最终 QUALITY 与 selector 选中的 HEAVY**

  开发阶段使用 `unstaged` scope。只有已获得 Commit 授权并显式暂存最终源码快照后，才运行下列 `staged` selector 与 HEAVY；
  计划本身不授权暂存或提交。

  Run: `./scripts/git-quality-gate.sh --profile quality`

  Run: `uv run scripts/select_heavy_tests.py --scope staged`

  Run: `./scripts/run_selected_heavy_local.sh --scope staged`

  Expected: QUALITY PASS；HEAVY 只执行 manifest，`NONE` 为有效结果。

- [ ] **Step 6: 完成唯一主 Review 与修复闭环**

  固定 base/head/staged manifest；Reviewer 只读核对基础/业务边界、可靠性、测试 owner、Phase 10 交接和当前 full diff。生产修复后同一 Reviewer 一轮闭合旧意见并 fresh full review，直到零未解决意见。

- [ ] **Step 7: 更新当前态文档并归档过期过程文档**

  只归档已被当前真源完整替代且不再承担执行职责的过程文档；项目内不留副本、转发页或软链接，`docs/hardware/` 保持不变。

- [ ] **Step 8: 获得授权后执行最终 staged 范围检查**

  Run: `npx gitnexus detect-changes --scope staged --repo "$PWD"`

  Run: `git diff --cached --check`

  检查通过后等待独立 Commit 授权；获得授权后建议使用：`feat(manual-bin): 交付 Phase 9 人工业务闭环`

## 完成判定

只有以下条件同时满足，Phase 9 第一阶段后端才可称为 Functional RC candidate：

- 基础能力在不安装 manual 插件时独立启动、迁移和测试；
- manual 插件独立构建，3/4 线本机 Mock 纵向切片完整闭合；
- WMS 严格合同、Transport 整批结果、DeviceCommand、BinExecution/PositionProjection 和任务/FIFO 所有权无重复；
- QUALITY、必选 HEAVY、干净 migration、旧路径缺席和零意见 Review 绑定同一最终源码快照；
- Phase 10 获得精确旧 owner 清单；未提前删除仍有合法消费者的旧路径；
- 未把本机 Mock、镜像发布、TEST 部署、供应商一致性或现场业务验收互相代替。
