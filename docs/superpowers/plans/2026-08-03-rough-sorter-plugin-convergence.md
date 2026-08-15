# Phase 8 粗分机插件收敛实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按 Task 1 联合批准后的当前合同重新交付首个可独立安装、构建和测试的粗分机业务插件，并用这一条真实业务闭环冻结最小插件 SDK、可靠执行对象、显式 Composition Root、设备合同附录和供应商一致性验收边界。

**Architecture:** Phase 8 不恢复旧 `rough_sorter`、Runtime/Effect、动态注册表或供应商私有适配代码。核心只提供 `MaterialExecution`、`InboundEvidence`、`WmsConfirmation`、稳定投影读取、类型化 Fact 处理器和封闭 Decision 应用器；插件只依赖独立 `wes_plugin_sdk`，按业务触发拆分 handler，通过构造函数显式注入只读协议，并返回有限 Decision。设备事实仍由 Phase 7 唯一 Device/ECS Adapter 先持久化再 ACK，WMS 交互仍由产品内唯一 `src/app/wms_adapter/` ACL 拥有，运输只可消费 Phase 6 Transport Port。插件不得访问数据库、Repository、HTTP、Celery 或核心内部状态机。

**Tech Stack:** Python 3.13、FastAPI、SQLModel/SQLAlchemy、Alembic、Celery、PostgreSQL、Redis、Pytest、uv workspace、Ruff、GitNexus。

**当前状态:** `GATED`。`docs/contracts/wms-inbound-putaway-integration-requirements.md` 当前仍为 `ReviewRequired`，且粗分机设备合同附录、供应商一致性验收边界和 Phase 6 Transport 消费授权尚未全部冻结。在 Task 1 退出门禁通过前，只允许完成合同、附录和验收责任的纯文档工作；禁止开始生产代码、migration 或行为测试。

## Global Constraints

- 本计划是主计划 Phase 8 的唯一详细实施计划，不新增 Phase 8A/8B 等正式阶段；任务可以作为原子交付批次，但不能改变 Phase 1—12 的阶段语义。
- 必须遵循 `docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md` 和 `docs/superpowers/plans/2026-08-03-wes-architecture-convergence-master-plan.md`。两者与本计划不一致时先修订并联合批准当前真源，不得由代码自行选择解释。
- 系统尚未发布：直接切换最终模型和表，不迁移旧数据，不保留兼容字段、双写、双读、旧 operation、转发模块、适配 shim 或 downgrade。
- `docs/hardware/` 是供应商原始资料，只读保留。设备字段归一化、错误映射、时限和不可逆点写入获批设备合同附录，不反向改写厂商原文。
- 纯文档任务不得新增或修改测试；只执行格式、引用、状态和 `git diff --check` 等文档验证。代码行为任务严格 TDD：先写最小失败测试，再实现，再运行目标域和质量门禁。
- 修改任何函数、类或方法前必须运行 GitNexus upstream impact analysis；HIGH/CRITICAL 必须先向用户报告并取得确认。Commit 前必须运行 GitNexus detect changes。
- 架构调用保持 API → Service → Repository → Database；插件不能绕过 Service/Port 访问 Repository 或数据库。
- 一个插件键表示一条完整业务执行流。Phase 8 固定 `plugin_key = rough_sorter`；设备、工位和现场差异属于 `LineRunEpoch` 配置与绑定，不拆成多个插件键。
- `LineRunEpoch` 只冻结插件、配置、拓扑和设备合同版本，不拥有单盘生命周期，也不是整线并发锁。
- 业务并发边界是 `MaterialExecution`；设备串行/并发约束归 `DeviceCommand.device_code`；共享货架/位置通过现有 generation/fence 约束。禁止新增 WorkLine 全局锁。
- 核心生命周期只允许 `ACTIVE | RECONCILING | COMPLETED | ABORTED`；不得把扫码、准入、PUT、上报等粗分步骤做成核心状态枚举。
- 默认不建立插件私有持久状态，更不得新增通用 `plugin_state` JSON。业务进度由 `InboundEvidence`、`DeviceCommand`、`WmsConfirmation`、位置投影和执行终态推导。
- handler 消费已验证、可关联的类型化 Fact，不接收供应商原始 Payload；装饰器只声明静态元数据，不注册对象、不扫描模块、不产生 import-time 副作用。
- Decision 是封闭集合，不建设通用 Effect 引擎。Phase 8 初始集合为 `Wait`、`CreateDeviceCommand`、`CreateWmsConfirmation`、`PauseForReconciliation`、`CompleteExecution`；只有 Task 1 明确批准 Transport 业务语义后才能加入 `CreateTransportTask`。
- ECS 已 ACK 命令或投递结果未知时，必须假定物理动作可能开始，禁止改目标或创建等价重放。只有 ECS 明确未接纳且没有物理副作用时，才允许用新的业务 Decision 恢复目标。
- 冲突默认冻结当前 `MaterialExecution` 及其已占用/预留资源；货架释放 generation 冲突扩大到该单层货架；只有 Epoch、拓扑或现场事实整体不可信时才暂停整条 WorkLine。
- MVP 不实现动态插件市场、运行时热插拔、多供应商抽象、通用认证平台、负载/HA 平台、仪表盘或 Phase 9 的 `BinExecution`。
- 核心测试、WMS Adapter 测试、插件测试、供应商一致性验收和现场业务验收分别拥有自己的边界，禁止互相代证。

---

### Task 1: 关闭实施授权、设备附录和 Transport 语义门禁

**Files:**

- Modify after joint approval: `docs/contracts/wms-inbound-putaway-integration-requirements.md`
- Create: `docs/contracts/device-annexes/rough-sorter-device-contract.md`
- Review: `docs/integration/third_party_integration_whitepaper.md`
- Review only: `docs/hardware/**`
- Modify only when approved facts require it: `docs/architecture/SRS.md`
- Modify only when approved facts require it: `docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md`
- Modify only when approved facts require it: `docs/superpowers/plans/2026-08-03-wes-architecture-convergence-master-plan.md`

**Interfaces:**

- Consumes: WMS/WES/RCS/ECS 联合评审、粗分机真实拓扑、供应商原始协议、Phase 6/7 已验收合同。
- Produces: `Approved` 入库 §7—§8、设备 task/event 闭集、不可逆点、endpoint/device/ECS 版本绑定规则、责任清晰的供应商验收包，以及 Transport 消费的明确结论。

- [ ] **Step 1: 逐项关闭入库合同 §20**

  记录每个确认项的责任方、审批证据和冻结版本；只有十项全部闭合后才把文档状态从 `ReviewRequired` 改为 `Approved`。不得用 WES 单方评审代替 WMS、RCS 或 ECS 授权。

- [ ] **Step 2: 冻结粗分机设备合同附录**

  附录只定义本插件真实使用的 `task_type`、`event_type`、严格 Payload、设备错误、ACK/CALLBACK 时限、`Retry-After` 规则、不可逆点和原始供应商字段到统一 wire 的映射；固定 HTTP 路径、公共包络、幂等身份和 callback 语义直接引用 Phase 7 真源，不复制定义。

- [ ] **Step 3: 冻结 endpoint/device/Epoch 绑定证据**

  对每台扫码、测量、机械臂和 NG 交接设备明确 `device_code`、`endpoint_code`、`contract_key`、合同版本、超时和所属 WorkLine。部署时这些值进入现有 `LineRunEpochDeviceBinding` 与 Epoch digest，不新增数据库插件注册表。

- [ ] **Step 4: 裁决 Phase 6 Transport Port 是否属于粗分闭环**

  当前入库 §7—§8 只授权逐盘准入、PUT、NG、货架释放和对账，`REPLACE` 不等于授权搬运当前货架。联合评审必须二选一：

  - 增加明确的 WMS/RCS 授权、目标、幂等身份和完成事实，使 `CreateTransportTask` 成为获批 Decision；或
  - 将 Phase 8 Transport 消费标记为 `NONE`，并同步修订主计划中“消费 Phase 6 Transport”的硬要求。

  未裁决前禁止在插件中猜测运输目标，也禁止创建占位 Transport。

- [ ] **Step 5: 冻结验收所有权矩阵**

  明确 Phase 7 核心只验固定 wire 和 DeviceCommand 可靠性；供应商交付边界验设备附录；WMS Adapter 验 operation/DTO；插件包验 Decision；现场联合验收真实闭环。每个场景只能有一个主要 owner。

- [ ] **Step 6: 执行纯文档验证并取得退出结论**

  Run: `rg -n "^status:|ReviewRequired|Approved|CreateTransportTask|Transport" docs/contracts/wms-inbound-putaway-integration-requirements.md docs/contracts/device-annexes/rough-sorter-device-contract.md docs/superpowers/{specs,plans}`

  Run: `git diff --check -- docs/contracts docs/architecture docs/superpowers`

  Expected: 入库合同和设备附录均为获批当前真源；Transport 只有一个明确结论；项目文档不存在互相冲突的 Phase 8 描述。否则 Phase 8 保持 `GATED`。

### Task 2: 冻结直接切换矩阵与 TDD 验收地图

**Files:**

- Inspect: `src/app/device/models/evidence.py`
- Inspect: `src/app/device/{repositories,services,composition.py}`
- Inspect: `src/app/workline/models/line_run_epoch.py`
- Inspect: `src/app/resource/models/resource.py`
- Inspect: `src/app/wms_adapter/`
- Inspect: `src/celery_app/`
- Inspect: `tests/{runtime,workline,contracts,integration,e2e,deployment,architecture}/`
- Modify: `docs/architecture/heavy-test-impact.toml`

**Interfaces:**

- Consumes: Task 1 冻结的精确合同与当前 Git/Alembic/测试基线。
- Produces: 每个旧 owner 的 successor/`NONE`、每项行为的唯一测试 owner、受影响 HEAVY mapping 和原子提交边界。

- [ ] **Step 1: 记录不可变实施基线**

  Run: `git rev-parse HEAD && git status --short && uv run alembic heads && uv run pytest --collect-only -q -o addopts='' | tail -5`

  Expected: 保存精确 commit、工作区状态、单一 Alembic head 和默认测试收集数；不得覆盖用户已有未提交内容。

- [ ] **Step 2: 建立 owner/successor 清单**

  至少逐项裁决 `DeviceEvidence`、`DeviceEvidenceConflict`、`WmsConfirmationStatus`、旧 RuntimeInbox 消费点、WMS 旧 Effect lane、启动装配和旧部署测试。目标是直接用 `InboundEvidence`/冲突证据、独立 `WmsConfirmation` 和新静态处理器接管；没有当前价值的旧 owner 标记 `NONE` 并删除，不做兼容代理。

- [ ] **Step 3: 运行 GitNexus 影响分析**

  对将被修改的 `DeviceEvidenceService`、`LineRunEpochService`、WMS 事件入口、Celery 任务、Composition Root 以及所有待修改模型逐个执行 upstream impact。记录直接调用者、执行流程和风险级别；HIGH/CRITICAL 停止并向用户确认。

- [ ] **Step 4: 冻结测试所有权与重量**

  - `tests/runtime/execution/`: 核心执行对象、证据身份、Decision 应用幂等和故障边界。
  - `tests/workline/`: Epoch 插件/配置冻结与绑定。
  - `tests/contracts/wms_adapter/`: 粗分 WMS operation、DTO、HTTP/错误语义。
  - `tests/integration/{execution,wms_adapter}/`: PostgreSQL 约束、事务和真实持久化。
  - `tests/e2e/rough_sorter/`: 部署组合的单条闭环，显式 HEAVY。
  - `workline_plugins/rough_sorter/tests/`: 只验粗分业务 Decision，不进入核心默认 pytest。
  - 供应商一致性验收: 留在供应商 ECS/网关交付边界，不进入本仓库核心质量门禁。

- [ ] **Step 5: 更新精确 HEAVY mapping**

  对新增执行模型、migration、WMS Adapter、Celery 任务、部署装配和 E2E 支撑资产分别映射真实受影响 HEAVY；不得用空 `heavy_tests` 隐藏运行时影响，也不得把插件私有测试加入核心 selector。

### Task 3: 建立最小独立插件 SDK 与边界守卫

**Files:**

- Create: `packages/wes_plugin_sdk/pyproject.toml`
- Create: `packages/wes_plugin_sdk/src/wes_plugin_sdk/__init__.py`
- Create: `packages/wes_plugin_sdk/src/wes_plugin_sdk/facts.py`
- Create: `packages/wes_plugin_sdk/src/wes_plugin_sdk/decisions.py`
- Create: `packages/wes_plugin_sdk/src/wes_plugin_sdk/protocols.py`
- Create: `packages/wes_plugin_sdk/src/wes_plugin_sdk/handler.py`
- Create: `tests/architecture/test_plugin_sdk_boundary_guardrail.py`
- Modify: `tests/architecture/test_core_plugin_test_ownership_guardrail.py`
- Modify: `scripts/architecture-guardrails.sh`
- Modify: `scripts/architecture-guardrails.allowlist`

**Interfaces:**

- Consumes: Task 1 已批准的粗分实际需求，不接受未来插件猜想。
- Produces: 无数据库、网络、Celery、容器或注册表依赖的最小稳定 SDK。

- [ ] **Step 1: 先写失败的 SDK 边界测试**

  测试必须锁定：SDK 不导入 `src.app`、SQLAlchemy/SQLModel、FastAPI、Celery 或 HTTP 库；核心 `src/` 不导入具体 `workline_plugins.*`；插件生产代码只允许导入 SDK 和获批公共合同，不允许导入 `src.app.*` 内部实现。

- [ ] **Step 2: 定义实际使用的类型化 Fact**

  只包含粗分闭环需要的不可变输入，例如物料证据已齐备、WMS 准入已决定、设备 placement 已确认、货架释放已决定、人工对账已决定。Fact 只携带稳定标识、版本和已验证数据，不携带 ORM 对象或原始 wire Payload。

- [ ] **Step 3: 定义封闭 Decision**

  建立 `Wait`、`CreateDeviceCommand`、`CreateWmsConfirmation`、`PauseForReconciliation`、`CompleteExecution`；仅在 Task 1 明确批准后增加 `CreateTransportTask`。Decision 必须包含应用幂等所需的稳定业务身份，不包含 Repository 或执行回调。

- [ ] **Step 4: 定义最窄只读 Protocol**

  只为 handler 当前决策所需的执行快照、位置/资源快照和 Epoch 配置建立读取协议；禁止通用 `Context`、Service Locator、`dict[str, Any]` 或写端口。

- [ ] **Step 5: 实现无副作用 handler 元数据装饰器**

  装饰器只附加 `fact_type`、handler 名称和支持版本等静态元数据。模块导入不能注册 handler、修改全局集合、扫描 entry point 或实例化依赖。

- [ ] **Step 6: 运行 SDK 和架构门禁**

  Run: `uv run pytest tests/architecture/test_plugin_sdk_boundary_guardrail.py tests/architecture/test_core_plugin_test_ownership_guardrail.py -q`

  Expected: PASS；SDK 为独立包，核心和插件依赖方向单向且静态。

- [ ] **Step 7: 提交 SDK 原子变更**

  Commit suggestion: `feat(plugin-sdk): 建立最小静态插件接口`

### Task 4: 建立核心可靠执行对象并直接替换旧证据 owner

**Files:**

- Create: `src/app/execution/__init__.py`
- Create: `src/app/execution/models/material_execution.py`
- Create: `src/app/execution/models/inbound_evidence.py`
- Create: `src/app/execution/models/wms_confirmation.py`
- Create: `src/app/execution/models/__init__.py`
- Create: `src/app/execution/repositories/material_execution_repository.py`
- Create: `src/app/execution/repositories/inbound_evidence_repository.py`
- Create: `src/app/execution/repositories/wms_confirmation_repository.py`
- Create: `src/app/execution/repositories/__init__.py`
- Create: `src/app/execution/services/material_execution_service.py`
- Create: `src/app/execution/services/inbound_evidence_service.py`
- Create: `src/app/execution/services/wms_confirmation_service.py`
- Create: `src/app/execution/services/__init__.py`
- Create: `src/app/execution/composition.py`
- Modify: `src/app/device/services/device_evidence_service.py`
- Modify: `src/app/device/{models,repositories}/__init__.py`
- Delete after successor passes: `src/app/device/models/evidence.py` 中被 `InboundEvidence` 接管的旧证据 owner，或删除整个文件中已无 owner 的定义
- Modify: `src/app/workline/models/line_run_epoch.py`
- Modify: `src/app/workline/{models,repositories,services}/__init__.py`
- Modify: `src/app/workline/repositories/line_run_epoch_repository.py`
- Modify: `src/app/workline/services/line_run_epoch_service.py`
- Modify: `src/app/resource/models/resource.py`
- Modify: `migrations/env.py`
- Create with Alembic generator: `migrations/versions/<generated>_converge_phase8_execution_objects.py`
- Create: `tests/runtime/execution/`
- Create: `tests/integration/execution/`
- Modify: `tests/workline/test_line_run_epoch.py`

**Interfaces:**

- Consumes: Phase 7 设备可靠性、现有位置/资源投影、Task 3 SDK 身份类型。
- Produces: 单盘执行、统一入站证据、WMS 可靠义务和 Epoch 插件版本冻结的唯一生产模型。

- [ ] **Step 1: 先写失败的模型与服务测试**

  覆盖：`MaterialExecution` 生命周期和乐观并发；一个活动业务身份只能有一个执行；`InboundEvidence` 同身份同 digest 幂等、异 digest 冲突；`WmsConfirmation` 稳定 `operation + operation_id`、重试不换身份；Epoch 固定 `plugin_key`、`plugin_version`、`flow_mode`；禁止通用插件状态 JSON。

- [ ] **Step 2: 实现 `MaterialExecution`**

  只保存通用身份、`workline_id`、`line_run_epoch_id`、业务相关键、生命周期、版本和诊断时间。粗分进度不做核心枚举，派生自相关证据、命令、确认和投影。

- [ ] **Step 3: 直接收敛 `InboundEvidence`**

  将设备 EVENT/RESULT 和 WMS EVENT/RESULT 表达为有限 evidence kind；保留稳定 source identity、payload digest、规范化 payload、关联对象、应用状态、冲突证据和下游发布时间。Phase 7 入口改为经执行应用端口写入该对象，不允许同时写旧 `DeviceEvidence` 和新表。

- [ ] **Step 4: 建立独立 `WmsConfirmation`**

  保存当前 operation、operation_id、请求 digest、不可变请求、执行关联、派发状态、重试资格和响应 evidence 关联。传输重试沿用原 operation_id；WMS `WAIT` 完成本次决定，后续重试业务决策必须创建新的 operation_id。

- [ ] **Step 5: 扩展 Epoch 冻结字段**

  为 `LineRunEpoch` 增加非空 `plugin_key`、`plugin_version` 和通用 `flow_mode`，沿用现有 topology/configuration digest 与 device binding。不得添加动态插件表或从当前 Epoch 外加载插件版本。

- [ ] **Step 6: 删除被接管的旧 owner**

  successor 测试通过后删除 `DeviceEvidence`/冲突重复定义、`BinMaterialMount` 上只代表旧单点确认的 `WmsConfirmationStatus` 及其无 owner 测试。`DeviceStatusObservation` 若仍由 Phase 7 设备状态拥有则原位保留。

- [ ] **Step 7: 由 Alembic generator 创建直接切换 migration**

  Run: `uv run alembic revision -m "收敛 Phase 8 执行对象"`

  生成后编辑 upgrade，直接删除旧表/字段并建立最终约束、索引和 FK；不复制开发数据，`downgrade()` 明确不支持。

- [ ] **Step 8: 验证核心执行对象**

  Run: `uv run pytest tests/runtime/execution tests/workline/test_line_run_epoch.py -q`

  Run: `./scripts/run_selected_heavy_local.sh --scope unstaged`

  Expected: FAST 与映射到 execution migration 的 PostgreSQL HEAVY 全部 PASS、无跳过；不存在证据双写或旧确认 owner。

- [ ] **Step 9: 提交可靠对象原子变更**

  Commit suggestion: `feat(execution): 建立粗分所需可靠执行对象`

### Task 5: 实现粗分 WMS ACL 与可靠确认派发

**Files:**

- Create: `src/app/wms_adapter/inbound_wire.py`
- Create: `src/app/wms_adapter/inbound_adapter.py`
- Create: `src/app/wms_adapter/inbound_event_handler.py`
- Create: `src/app/wms_adapter/inbound_openapi.py`
- Modify: `src/app/wms_adapter/client.py`
- Modify: `src/app/wms_adapter/v1/events.py`
- Modify: `src/app/wms_adapter/__init__.py`
- Modify: `src/app/execution/services/wms_confirmation_service.py`
- Create: `tests/contracts/wms_adapter/test_inbound_wire_acceptance.py`
- Create: `tests/contracts/wms_adapter/test_inbound_adapter.py`
- Create: `tests/contracts/wms_adapter/test_inbound_event_handler.py`
- Create: `tests/contracts/wms_adapter/test_inbound_openapi.py`
- Create: `tests/integration/wms_adapter/test_inbound_confirmation_postgresql.py`

**Interfaces:**

- Consumes: Task 1 获批 §7—§8 operation 与公共信封，现有 WMS `OutboundHttpTransport`/client 边界。
- Produces: 类型化 WES→WMS 可靠确认与 WMS→WES 持久化事件入口，不把 WMS 业务语义泄漏到基础 HTTP transport。

- [ ] **Step 1: 先写失败的合同测试**

  精确覆盖获批 operation、严格字段闭集、大小限制、`operation + operation_id` 幂等、同 identity 异 digest 冲突、HTTP 状态、`retry_after_ms`、错误映射和 OpenAPI。禁止把未获批 operation 填进测试作为未来占位。

- [ ] **Step 2: 建立逐盘入库 WES→WMS 类型化 DTO**

  仅实现 `inbound.material.admission_decide@v1`、`target_recovery_decide@v1`、`placement_report@v1` 和 `ng_placement_report@v1`；适配器把 transport 响应规范化为 WMS_RESULT `InboundEvidence`，不直接推进插件或修改库存投影。

- [ ] **Step 3: 建立 WMS→WES 类型化入口**

  仅实现 `inbound.source_rack.release_decided@v1` 和 `inbound.execution.reconciliation_decided@v1`。入口先持久化 WMS_EVENT `InboundEvidence` 再返回 `202 / RECEIVED`；不得在 API 层访问 Repository 或执行 handler。

- [ ] **Step 4: 静态分派现有 WMS 事件端点**

  在现有固定 `/api/v1/wms/events` 入口按有限 operation 显式分派 Transport 与粗分入库 handler；不引入 operation registry、模块扫描或第二个 HTTP Adapter。

- [ ] **Step 5: 实现 `WmsConfirmation` 有界派发**

  每批最多处理 100 条资格记录；派发前重新检查 deadline/重试资格；HTTP 未知结果保留原 operation_id 重试；响应必须先持久化为 evidence 再完成 confirmation。不得复用旧 Runtime Effect lane。

- [ ] **Step 6: 运行 WMS ACL 验收**

  Run: `uv run pytest tests/contracts/wms_adapter -q`

  Run: `./scripts/run_selected_heavy_local.sh --scope unstaged`

  Expected: 新旧 WMS operation 均由显式 ACL 拥有；基础 outbound HTTP 测试不承载粗分业务断言；真实持久化场景 PASS、无跳过。

- [ ] **Step 7: 提交 WMS ACL 原子变更**

  Commit suggestion: `feat(wms-adapter): 接入粗分入库确认合同`

### Task 6: 建立类型化 Fact 处理器与封闭 Decision 应用器

**Files:**

- Create: `src/app/execution/services/fact_builder.py`
- Create: `src/app/execution/services/fact_processor.py`
- Create: `src/app/execution/services/decision_applier.py`
- Create: `src/app/execution/plugin_binding.py`
- Modify: `src/app/execution/composition.py`
- Create: `src/celery_app/tasks/execution.py`
- Modify: `src/celery_app/tasks/__init__.py`
- Modify: `src/celery_app/config.py`
- Modify as required: `src/celery_app/async_runtime.py`
- Create: `tests/runtime/execution/test_fact_builder.py`
- Create: `tests/runtime/execution/test_fact_processor.py`
- Create: `tests/runtime/execution/test_decision_applier.py`
- Create: `tests/deployment/test_execution_worker_startup.py`

**Interfaces:**

- Consumes: 已持久化且可关联的 `InboundEvidence`、Task 3 SDK、核心应用端口。
- Produces: 静态绑定的 handler 调用和类型匹配的原子 Decision 应用；插件没有基础设施写权限。

- [ ] **Step 1: 先写失败的处理与应用测试**

  覆盖 evidence claim、重复处理、同证据不同 Decision 冲突、handler 异常、未知 Fact/Decision fail closed、事务回滚、worker reclaim，以及每种允许 Decision 的精确应用结果。

- [ ] **Step 2: 构建类型化 Fact**

  只有 evidence 已持久化、digest/身份验证完成且关联对象存在时才能生成 SDK Fact。原始 Payload 的供应商映射必须在 Phase 7/设备附录边界完成，Fact builder 不猜测字段。

- [ ] **Step 3: 实现静态插件绑定**

  `plugin_binding.py` 只接收部署时明确传入的 handler 实例映射和只读协议实现；不扫描 filesystem、entry points 或 import 路径，不维护运行时 Catalog。

- [ ] **Step 4: 实现类型匹配的 Decision 应用器**

  应用器只分派封闭类型到 `DeviceCommand`、`WmsConfirmation`、执行状态和可选 `TransportTask` 应用端口。每个结果以 evidence identity + decision type/ordinal 幂等；不建立通用 Effect 表或 handler 回调。

- [ ] **Step 5: 实现有界异步处理**

  入口 ACK 与业务处理解耦；worker 每批最多 100 条，claim 超时可恢复，失败有界退避，冲突进入 `RECONCILING`。不得使用无限轮询或 WorkLine 全局锁。

- [ ] **Step 6: 验证不可逆边界**

  对 `CreateDeviceCommand` 明确测试：明确未接纳可以创建新的恢复 Decision；ACK 后或投递未知只能等待 callback/人工对账，不得改目标或重发等价物理动作。

- [ ] **Step 7: 运行核心处理器与启动门禁**

  Run: `uv run pytest tests/runtime/execution tests/deployment/test_execution_worker_startup.py -q`

  Expected: PASS；核心测试使用最小 fake handler，不导入真实 `rough_sorter` 插件。

- [ ] **Step 8: 提交核心处理器原子变更**

  Commit suggestion: `feat(execution): 应用类型化事实与封闭决策`

### Task 7: 实现独立粗分机插件包

**Files:**

- Create: `workline_plugins/rough_sorter/pyproject.toml`
- Create: `workline_plugins/rough_sorter/src/rough_sorter/__init__.py`
- Create: `workline_plugins/rough_sorter/src/rough_sorter/plugin.py`
- Create: `workline_plugins/rough_sorter/src/rough_sorter/handlers/__init__.py`
- Create: `workline_plugins/rough_sorter/src/rough_sorter/handlers/material_evidence_ready.py`
- Create: `workline_plugins/rough_sorter/src/rough_sorter/handlers/admission_decided.py`
- Create: `workline_plugins/rough_sorter/src/rough_sorter/handlers/placement_completed.py`
- Create: `workline_plugins/rough_sorter/src/rough_sorter/handlers/rack_release_decided.py`
- Create: `workline_plugins/rough_sorter/src/rough_sorter/handlers/reconciliation_decided.py`
- Create: `workline_plugins/rough_sorter/tests/`
- Create only for approved payloads: `workline_plugins/rough_sorter/fixtures/`

**Interfaces:**

- Consumes: `wes_plugin_sdk` Fact/Decision/Protocol、Task 1 获批粗分合同、核心只读快照。
- Produces: `rough_sorter` 业务 Decision；无数据库、网络、Celery、供应商 wire 或基础设施实现。

- [ ] **Step 1: 先写插件失败测试**

  最少覆盖：证据不完整 `Wait`、准入 `ACCEPT`、业务 `WAIT`、`REJECT`/NG、PUT 前目标恢复、ECS ACK 后禁止换目标、placement 确认、货架 release fence、generation 冲突、delivery unknown、对账完成和重复 Fact 幂等。

- [ ] **Step 2: 实现最小插件入口**

  `plugin.py` 显式构造 handler 列表并注入只读协议；不提供通用容器、registry 或自动 discovery。目录只保留实际文件，不预建 `domain/`、`application/`、`services/`、`repositories/`、`adapters/`、`infrastructure/`、`utils/`、`config/`。

- [ ] **Step 3: 按业务触发拆分 handler**

  一个文件对应一个稳定业务触发及其决策，不对应原始 EVENT/CALLBACK。handler 保持纯决策：输入 Fact + 只读快照，输出一个或多个封闭 Decision。

- [ ] **Step 4: 实现逐盘准入和 PUT 决策**

  六合一码、直径和厚度齐备后创建 WMS admission confirmation；`ACCEPT` 且目标物理可执行时创建唯一 DeviceCommand；业务 `WAIT` 不伪装成设备忙；`REJECT` 只按 WMS 决定进入获批 NG 路径。

- [ ] **Step 5: 实现目标恢复和不可逆保护**

  只在 PUT 前且旧命令明确未被 ECS 接纳时请求 `target_recovery_decide`；ECS ACK 或结果未知后禁止换 target，进入等待或人工对账。

- [ ] **Step 6: 实现 placement、release 和 reconciliation 决策**

  PUT/NG 可靠完成后创建对应 WMS report；release 只冻结新准入并等待当前货架在途事实闭合；generation/成员/位置冲突进入最小隔离域；获批 reconciliation Fact 才能恢复或终结相关执行。

- [ ] **Step 7: 实现经批准的 Transport 分支或显式 NONE**

  只有 Task 1 选择并冻结 Transport 授权时才添加对应 handler/Decision 测试；若结论为 `NONE`，插件和 SDK 都不得留下空 Transport 抽象。

- [ ] **Step 8: 独立运行插件包测试**

  Run: `uv run --project workline_plugins/rough_sorter pytest -q`

  Expected: 插件测试全部 PASS；核心默认 pytest 不收集该目录；插件 wheel 可独立构建且运行依赖只包含 SDK 和必要公共类型。

- [ ] **Step 9: 提交粗分插件原子变更**

  Commit suggestion: `feat(rough-sorter): 重建粗分机执行决策`

### Task 8: 完成显式部署装配、workspace 与镜像交付

**Files:**

- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `Dockerfile`
- Create: `deployment/__init__.py`
- Create: `deployment/rough_sorter_composition.py`
- Modify: `main.py`
- Modify: `src/celery_app/app.py`
- Modify as required: deployment environment profile files without secrets
- Create: `tests/deployment/test_rough_sorter_plugin_startup.py`
- Modify: `tests/deployment/test_celery_task_runtime_contract.py`

**Interfaces:**

- Consumes: 核心 execution composition、安装后的 `rough_sorter` 包、已冻结 Epoch/plugin/device 配置。
- Produces: 一个部署内唯一、静态、可审计的粗分插件绑定和包含 SDK/plugin wheel 的镜像。

- [ ] **Step 1: 先写失败的部署装配测试**

  测试锁定：部署只显式绑定 `rough_sorter`；插件键/版本必须与活动 `LineRunEpoch` 一致；未知键、版本漂移、缺少设备 binding 或 digest 不一致时启动/激活 fail closed；只有仓库顶层 deployment Composition Root 可以导入具体插件，核心 `src/app/**` 不导入任意插件。

- [ ] **Step 2: 配置 uv workspace**

  只加入 `packages/wes_plugin_sdk` 和 `workline_plugins/rough_sorter` 两个实际成员并更新 lock；不得创建空的未来插件 workspace。

- [ ] **Step 3: 建立部署 Composition Root**

  部署模块显式 import `rough_sorter.plugin`，构造只读协议、handler map、Fact processor 和 Decision applier。插件实例只在应用/worker 启动装配处出现，不进入核心领域模块。

- [ ] **Step 4: 调整 Docker 构建顺序**

  在 `uv sync` 前复制 workspace 成员的 `pyproject.toml`/必要包结构，确保 lock 可重现；最终镜像必须包含 SDK 和粗分插件，不能依赖宿主机可编辑安装。

- [ ] **Step 5: 绑定 Web 与 Celery 同一插件版本**

  API ingress 和 worker 使用同一个部署配置及 Epoch 版本验证，不允许 Web/Celery 各自扫描或选择插件。启动日志输出非敏感的 plugin key/version/config digest 供验收。

- [ ] **Step 6: 验证安装、镜像和启动**

  Run: `uv sync --dev && uv lock --check`

  Run: `uv run pytest tests/deployment/test_rough_sorter_plugin_startup.py tests/deployment/test_celery_task_runtime_contract.py -q`

  Run: `docker build -t wes-backend:phase8-rough-sorter .`

  Expected: workspace lock 无漂移；Web/Celery 静态绑定一致；镜像内可导入 SDK 和插件，未知/漂移配置 fail closed。

- [ ] **Step 7: 提交部署装配原子变更**

  Commit suggestion: `feat(deployment): 显式装配粗分机插件`

### Task 9: 完成供应商一致性、真实闭环和业务验收

**Files:**

- Create: `tests/e2e/rough_sorter/test_rough_sorter_business_loop.py`
- Create or update: named non-secret E2E fixtures under `tests/e2e/rough_sorter/fixtures/`
- Create: `docs/integration/rough-sorter-supplier-conformance.md`
- Create: `docs/integration/rough-sorter-joint-acceptance.md`
- Modify: `docs/architecture/heavy-test-impact.toml`

**Interfaces:**

- Consumes: 供应商 ECS/网关、WMS mock/联调端、真实 PostgreSQL/Redis/Celery/HTTP、获批附录与插件镜像。
- Produces: 供应商一致性结果、单条真实 event→evidence→decision→command→callback→confirmation→complete 闭环和现场联合验收证据。

- [ ] **Step 1: 在供应商交付边界运行一致性验收**

  覆盖附录 task/event、字段闭集、身份、ACK、CALLBACK、错误、时限、投递未知和不可逆点。结果只记录版本、环境、通过/失败和证据位置；供应商私有 DTO/认证/原始映射不复制进 WES 核心或插件。

- [ ] **Step 2: 先建立失败的部署 E2E**

  单场景必须经过真实 HTTP ingress、持久化 evidence、Celery handler、DeviceCommand 派发、callback、WmsConfirmation 和 execution 完成；不得直接调用 Service 跳过装配。失败原因应是闭环尚未接通，不是依赖缺失或测试跳过。

- [ ] **Step 3: 跑通主成功路径**

  标准完整料盘证据 → WMS `ACCEPT` → 粗分 PUT → ECS 确定成功 → placement report → WMS `RECORDED` → `MaterialExecution.COMPLETED`。验证每个可靠对象身份稳定且无双写。

- [ ] **Step 4: 跑通安全失败路径**

  至少现场/集成验证：重复与冲突 evidence、业务 `WAIT`、PUT 前恢复、ACK 后禁止改目标、callback 未知、货架 release generation 冲突、人工 reconciliation。非功能性负载、HA 和多供应商矩阵不作为 MVP 退出条件。

- [ ] **Step 5: 执行受影响 HEAVY**

  Run: `uv run scripts/select_heavy_tests.py --scope unstaged`

  Run: `./scripts/run_selected_heavy_local.sh --scope unstaged`

  Expected: selector 包含粗分 E2E 及实际受影响 integration 目录；JUnit `total > 0`、`failed = 0`、`skipped = 0`。

- [ ] **Step 6: 记录分层验收结论**

  分别记录核心基线、WMS Adapter、供应商一致性、插件业务和现场联合验收状态。任一层未通过时整体不得标记完成，也不得用其他层测试替代。

- [ ] **Step 7: 提交验收资产原子变更**

  Commit suggestion: `test(rough-sorter): 验收粗分业务闭环`

### Task 10: 清除旧路径、完成质量门禁并关闭 Phase 8

**Files:**

- Modify as required: `tests/architecture/test_business_legacy_absence_guardrail.py`
- Modify as required: `tests/architecture/test_runtime_status_owner_guardrail.py`
- Modify as required: `tests/architecture/test_workline_inbox_retirement_guardrail.py`
- Modify: `docs/plugin_development_guide.md`
- Modify: `docs/superpowers/README.md`
- Modify: `docs/superpowers/plans/2026-08-03-wes-architecture-convergence-master-plan.md`
- Archive only when superseded: obsolete process documents to `../archive_docs/wes_backend/`

**Interfaces:**

- Consumes: Task 3—9 全部实现、测试和联合验收证据。
- Produces: 无旧 Runtime/粗分残留、当前开发指南与主计划一致、可独立交付的 Phase 8 最终状态。

- [ ] **Step 1: 执行旧路径 absence 扫描**

  Run: `rg -n "RuntimeInbox|ExecutionSession|RuntimeIntent|RuntimeHold|SystemCapability|plugin_state|src\.app\.runtime\.workline_plugins|old_rough_sorter|confirm_inbound|notify_pkg_binding" src packages workline_plugins tests --glob '*.py'`

  Expected: 生产路径零命中；测试只允许明确的 absence 字面量。不得保留 dual path、shim、registry 或被新对象取代的旧状态 owner。

- [ ] **Step 2: 验证依赖方向和测试拓扑**

  Run: `uv run pytest tests/architecture/test_suite_topology_guardrail.py tests/architecture/test_core_plugin_test_ownership_guardrail.py tests/architecture/test_plugin_sdk_boundary_guardrail.py -q`

  Run: `uv run pytest --collect-only -q -o addopts='' | tail -5`

  Expected: 核心默认收集不包含插件和供应商测试；核心、SDK、插件依赖方向符合计划。

- [ ] **Step 3: 运行插件和核心 FAST**

  Run: `uv run --project workline_plugins/rough_sorter pytest -q`

  Run: `uv run pytest tests/ -q`

  Expected: 全部 PASS；插件结果单独报告，不合并成核心覆盖率数字。

- [ ] **Step 4: 运行完整质量与受影响 HEAVY**

  Run: `uv run ruff format --check . && uv run ruff check .`

  Run: `./scripts/git-quality-gate.sh --profile quality`

  Run: `./scripts/run_selected_heavy_local.sh --scope unstaged`

  Expected: 质量门禁和受影响 HEAVY 全部 PASS，真实 HEAVY 无跳过；不使用 `--no-verify` 或降低断言换取通过。

- [ ] **Step 5: GitNexus 最终范围检测与独立评审**

  运行 GitNexus detect changes，确认受影响符号/流程只属于 Phase 8。独立评审重点检查：WMS/WES/ECS/RCS 权威边界、物理不可逆点、并发隔离域、插件基础设施泄漏、测试代证和超出 MVP 的抽象。

- [ ] **Step 6: 更新当前开发真源**

  用已验证实现更新 `docs/plugin_development_guide.md`：保持“一插件一子目录”，handler 按业务触发拆分，不写成“一物理 EVENT/CALLBACK 一文件”；明确装饰器元数据、显式 DI 和无动态注册表。主计划只在所有分层验收通过后把 Phase 8 标为完成。

- [ ] **Step 7: 归档被当前结果取代的过程文档**

  逐个确认 obsolete 文档及当前引用，移动到 `../archive_docs/wes_backend/` 对应原相对路径；项目内不保留副本、占位、软链接或转发页。`docs/hardware/**` 永不进入此清理。

- [ ] **Step 8: 提交 Phase 8 收尾**

  Commit suggestion: `docs(phase8): 关闭粗分机插件收敛阶段`

## Phase 8 Exit Gate

只有以下条件同时成立，Phase 8 才能从 `GATED/IN_PROGRESS` 改为 `COMPLETE`：

1. 入库合同 §7—§8 和粗分机设备合同附录已联合批准，Transport 消费已有唯一明确结论。
2. `MaterialExecution`、`InboundEvidence`、`WmsConfirmation`、Epoch 插件冻结和封闭 Decision 应用器成为唯一生产路径，无旧 owner/双写/兼容路径。
3. `wes_plugin_sdk` 与 `rough_sorter` 均可独立安装、构建和测试；SDK 只含真实使用的稳定接口。
4. Web 与 Celery 通过一个显式 Composition Root 绑定同一插件版本、配置 digest 和设备 binding；核心无具体插件 import 或供应商分支。
5. Phase 7 Device/ECS、WMS Adapter、供应商一致性、插件业务和现场联合验收分别通过，且证据不互相代替。
6. 成功闭环及重复、冲突、WAIT、投递未知、不可逆、释放围栏和对账路径全部通过；受影响 HEAVY `failed = 0` 且 `skipped = 0`。
7. 旧 Runtime/Effect/粗分路径 absence 扫描通过，当前文档真源已更新，过期过程文档已移出项目目录，`docs/hardware/` 保持原样。

## 明确延期到后续阶段

- `BinExecution`、满箱交换、自动/人工分拣和复杂出库：Phase 9。
- 动态插件发现、热插拔、插件市场、数据库插件注册表：无已批准需求，YAGNI。
- 第二个设备 HTTP Adapter、供应商私有 client/auth/DTO：供应商 ECS/网关边界，不进入 WES。
- 多供应商并行矩阵、容量压测、HA/容灾平台、通用运维仪表盘：MVP 后按真实非功能需求单独立项，不阻塞 Phase 8 正确性与安全退出门禁。
