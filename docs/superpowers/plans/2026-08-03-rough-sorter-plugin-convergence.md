# Phase 8 粗分机插件收敛实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按 Task 1 联合批准后的当前合同重新交付首个可独立安装、构建和测试的粗分机业务插件，并用这一条真实业务闭环冻结最小插件 SDK、可靠执行对象、显式 Composition Root、设备合同附录和供应商一致性验收边界。

**Architecture:** Phase 8 不恢复旧 `rough_sorter`、Runtime/Effect、动态注册表或供应商私有适配代码。核心只提供 `MaterialExecution`、`InboundEvidence`、`WmsConfirmation`、稳定投影读取、类型化 Fact 处理器和封闭 Decision 应用器；插件只依赖独立 `wes_plugin_sdk`，按业务触发拆分 handler，通过构造函数显式注入只读协议，并返回有限 Decision。设备事实仍由 Phase 7 唯一 Device/ECS Adapter 先持久化再 ACK，WMS 交互仍由产品内唯一 `src/app/wms_adapter/` ACL 拥有，运输只可消费 Phase 6 Transport Port。插件不得访问数据库、Repository、HTTP、Celery 或核心内部状态机。

**Tech Stack:** Python 3.13、FastAPI、SQLModel/SQLAlchemy、Alembic、Celery、PostgreSQL、Redis、Pytest、uv workspace、Ruff、GitNexus。

**当前状态:** `IN_PROGRESS — EXTERNAL BLOCKED`。Task 1—8 已分别完成并提交；Task 9 的仓内工程验收、本机 Mock 模拟联调、
分层状态文档和受影响 HEAVY 已完成，供应商一致性、真实 WMS 联调与现场联合验收仍为 `NOT RUN — BLOCKED`。Task 10
已同步当前真源并将被实现取代的过程设计移出项目归档。
Task 8 已将批量 reconciliation 直接替换为单 execution `recovery_decided`，未保留旧 operation、binding 或兼容路径；仓内实现和
测试通过不代表供应商一致性、现场联调或 Phase 8 业务验收完成。

| Task | 状态 | Commit |
| --- | --- | --- |
| 1 | Complete | `0dc3eab9` |
| 2 | Complete | `048c3588` |
| 3 | Complete | `042df417` |
| 4 | Complete | `5d093d1b` |
| 5 | Complete | `ab02f42f` |
| 6 | Complete | `1d5e0209` |
| 7 | Complete | `7bca4a8f` |
| 8 | Complete | 独立原子提交链；最终装配修复至 `714f1c1a` |
| 9 | In progress — external blocked | Commit `d90d0df6` 仓内工程与本机 Mock 验收完成；供应商一致性、真实 WMS 与现场联合验收未运行 |
| 10 | Repository complete | 当前文档真源同步与过期过程设计归档；不改变 Phase 8 外部阻塞状态 |

## Global Constraints

- 本计划是 Phase 8 初始收敛交付、仓内验收和外部阻塞状态的主记录。后续 WorkLine Epoch 激活、多 Endpoint 派发及其前端入口以
  `2026-08-19-rough-sorter-workline-epoch-activation.md` 为增量实施真源；该增量计划不新增 Phase 8A/8B 等正式阶段，也不能改写
  本计划已经完成的 Task、Commit 和分层验收事实。
- 必须遵循 `docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md` 和 `docs/superpowers/plans/2026-08-03-wes-architecture-convergence-master-plan.md`。两者与本计划不一致时先修订并联合批准当前真源，不得由代码自行选择解释。
- 系统尚未发布：直接切换最终模型和表，不迁移旧数据，不保留兼容字段、双写、双读、旧 operation、转发模块、适配 shim 或 downgrade。
- `docs/hardware/` 是供应商原始资料，只读保留。设备字段归一化、错误映射、时限和不可逆点写入获批设备合同附录，不反向改写厂商原文。
- 纯文档任务不得新增或修改测试；只执行格式、引用、状态和 `git diff --check` 等文档验证。代码行为任务严格 TDD：先写最小失败测试，再实现，再运行目标域和质量门禁。
- 修改任何函数、类或方法前必须运行 GitNexus upstream impact analysis；HIGH/CRITICAL 必须先向用户报告并取得确认。Commit 前必须运行 GitNexus detect changes。
- 架构调用保持 API → Service → Repository → Database；插件不能绕过 Service/Port 访问 Repository 或数据库。
- 一个插件键表示一条完整业务执行流。Phase 8 固定 `plugin_key = rough_sorter`；设备、工位和现场差异属于 `LineRunEpoch` 配置与绑定，不拆成多个插件键。
- `LineRunEpoch` 只冻结插件、配置、拓扑和设备合同版本，不拥有单盘生命周期，也不是整线并发锁。
- 业务并发边界是 `MaterialExecution`；设备串行/并发约束归 `DeviceCommand.device_code`；旧架通过获批 release gate 与冻结快照
  围栏。禁止新增 WorkLine 全局锁。
- 核心生命周期只允许 `CREATED | RUNNING | HOLD | CLOSED | RECONCILING`；不得把扫码、准入、PUT、上报等粗分步骤做成核心状态枚举。
- 默认不建立插件私有持久状态，更不得新增通用 `plugin_state` JSON。业务进度由 `InboundEvidence`、`DeviceCommand`、`WmsConfirmation`、位置投影和执行终态推导。Epoch 的不可变 `configuration_snapshot_json` 只保存部署证据，不承载可变业务进度或插件运行状态。
- handler 消费已验证、可关联的类型化 Fact，不接收供应商原始 Payload；装饰器只声明静态元数据，不注册对象、不扫描模块、不产生 import-time 副作用。
- Decision 是封闭集合，不建设通用 Effect 引擎。Phase 8 集合为 `Wait`、`DeferExecution`、`CreateDeviceCommand`、
  `CreateWmsConfirmation`、`CreateTransportTask`、`PauseForReconciliation`、`CompleteExecution`；`DeferExecution` 只表达未满足
  本地可执行条件且等待后需重新求值，`CreateTransportTask` 只用于获批换架计划的两个既有 `RACK_MOVE`。
- ECS 已 ACK 命令或投递结果未知时，必须假定物理动作可能开始，禁止改目标或创建等价重放。只有 ECS 明确未接纳且没有物理副作用时，才允许用新的业务 Decision 恢复目标。
- 冲突默认冻结当前 `MaterialExecution` 及其已占用/预留资源；旧架 release gate 或位置冲突扩大到该单层货架；只有 Epoch、
  拓扑或现场事实整体不可信时才暂停整条 WorkLine。
- MVP 不实现动态插件市场、运行时热插拔、多供应商抽象、通用认证平台、负载/HA 平台、仪表盘或 Phase 9 的 `BinExecution`。
- 核心测试、WMS Adapter 测试、插件测试、供应商一致性验收和现场业务验收分别拥有自己的边界，禁止互相代证。

---

### Task 1: 关闭实施授权、设备附录和 Transport 语义门禁

**Files:**

- Create: `docs/contracts/wms-rough-sorter-inbound-integration-requirements.md`
- Modify: `docs/contracts/wms-inbound-putaway-integration-requirements.md`
- Create: `docs/contracts/device-annexes/rough-sorter-device-contract.md`
- Review: `docs/integration/third_party_integration_whitepaper.md`
- Review only: `docs/hardware/**`
- Modify only when approved facts require it: `docs/architecture/SRS.md`
- Modify only when approved facts require it: `docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md`
- Modify only when approved facts require it: `docs/superpowers/plans/2026-08-03-wes-architecture-convergence-master-plan.md`

**Interfaces:**

- Consumes: WMS/WES/RCS/ECS 联合评审、粗分机真实拓扑、供应商原始协议、Phase 6/7 已验收合同。
- Produces: 独立 `Approved` Phase 8 粗分入库合同、设备 task/event 闭集、不可逆点、endpoint/device/ECS 版本绑定规则、
  责任清晰的供应商验收边界，以及两个既有 `RACK_MOVE` 的 Transport 消费结论。

- [x] **Step 1: 拆分并批准 Phase 8 粗分合同**

  `docs/contracts/wms-rough-sorter-inbound-integration-requirements.md` 是 Phase 8 唯一业务合同真源；未来满箱交换、自动上架和
  其它阶段继续保留在 `ReviewRequired` 合同中。

- [x] **Step 2: 冻结粗分机设备合同附录**

  附录只定义本插件真实使用的 `task_type`、`event_type`、严格 Payload、设备错误、ACK/CALLBACK 时限、`Retry-After` 规则、不可逆点和原始供应商字段到统一 wire 的映射；固定 HTTP 路径、公共包络、幂等身份和 callback 语义直接引用 Phase 7 真源，不复制定义。

- [x] **Step 3: 冻结 endpoint/device/Epoch 绑定规则**

  每个 WorkLine 绑定三个一对一角色；每个可派发 Device 各有一个 Endpoint，多个 Device 可共享或分别使用不同 Endpoint。
  `device_code`、Endpoint Base URL、合同身份、状态新鲜度与命令超时进入现有 `LineRunEpochDeviceBinding` 和
  `topology_digest`。ECS/网关/设备/固件版本、时钟、重传、证据策略和位置配置作为规范化业务快照写入
  `LineRunEpoch.configuration_snapshot_json`，并与插件身份、运行模式共同生成 `configuration_digest`。基础层不解释快照字段；
  不新增数据库插件注册表或 `DeviceEndpoint` 实体。

- [x] **Step 4: 裁决 Phase 6 Transport Port 属于粗分换架闭环**

  WMS 返回稳定 `rack_replacement_id` 及旧装载架/新空架各自的 `rack_id + source + target + target_face`。插件创建两个独立
  `RACK_MOVE`，业务幂等键分别为 `(rack_replacement_id, OLD_OUT)` 与 `(rack_replacement_id, NEW_IN)`；应用端将每个键原子
  映射到不同的全局唯一 UUIDv7 `client_request_id`。不新增 `RACK_EXCHANGE`。
  两任务可同时提交，实际顺序由 RCS 控制，是外部未验证前提。

- [x] **Step 5: 冻结验收所有权矩阵**

  明确 Phase 7 核心只验固定 wire 和 DeviceCommand 可靠性；供应商交付边界验设备附录；WMS Adapter 验 operation/DTO；插件包验 Decision；现场联合验收真实闭环。每个场景只能有一个主要 owner。

- [x] **Step 6: 执行纯文档验证并取得退出结论**

  Run: `rg -n "^status:|ReviewRequired|Approved|CreateTransportTask|RACK_MOVE|Transport" docs/contracts/wms-rough-sorter-inbound-integration-requirements.md docs/contracts/wms-inbound-putaway-integration-requirements.md docs/contracts/device-annexes/rough-sorter-device-contract.md docs/superpowers/{specs,plans}`

  Run: `git diff --check -- docs/contracts docs/architecture docs/superpowers`

  Expected: Phase 8 粗分合同和设备附录均为 `Approved`，后续上架合同保持 `ReviewRequired`，Transport 只有两个既有
  `RACK_MOVE` 这一明确结论，项目文档不存在互相冲突的 Phase 8 描述。满足后 Phase 8 进入 `IN_PROGRESS`。

### Task 2: 冻结直接切换矩阵与 TDD 验收地图

**Files:**

- Inspect: `src/app/device/models/evidence.py`
- Inspect: `src/app/device/{repositories,services,composition.py}`
- Inspect: `src/app/workline/models/line_run_epoch.py`
- Inspect: `src/app/resource/models/resource.py`
- Inspect: `src/app/wms_adapter/`
- Inspect: `src/celery_app/`
- Inspect: `tests/{runtime,workline,contracts,integration,deployment,architecture}/`
- Inspect: `workline_plugins/rough_sorter/tests/`
- Modify: `scripts/select_heavy_tests.py`
- Modify: `tests/scripts/test_select_heavy_tests.py`
- Modify: `docs/architecture/heavy-test-impact.toml`

**Interfaces:**

- Consumes: Task 1 冻结的精确合同与当前 Git/Alembic/测试基线。
- Produces: 每个旧 owner 的 successor/`NONE`、每项行为的唯一测试 owner、受影响 HEAVY mapping 和原子提交边界。

- [x] **Step 1: 记录不可变实施基线**

  Run: `git rev-parse HEAD && git status --short && uv run alembic heads && uv run pytest --collect-only -q -o addopts='' | tail -5`

  Expected: 保存精确 commit、工作区状态、单一 Alembic head 和默认测试收集数；不得覆盖用户已有未提交内容。

- [x] **Step 2: 建立 owner/successor 清单**

  至少逐项裁决 `DeviceEvidence`、`DeviceEvidenceConflict`、`WmsConfirmationStatus`、本阶段直接替换的旧 RuntimeInbox 消费点、WMS 旧 Effect lane、启动装配和旧部署测试。目标是直接用 `InboundEvidence`/冲突证据、独立 `WmsConfirmation` 和新静态处理器接管；没有当前价值的直接旧 owner 标记 `NONE` 并删除，不做兼容代理。跨阶段 Runtime/System Capability/Intent/Hold 残余只记录精确 owner、消费者和 successor，交给 Phase 10；Phase 8 不以全仓零命中为退出条件。

- [x] **Step 3: 运行 GitNexus 影响分析**

  对将被修改的 `DeviceEvidenceService`、`LineRunEpochService`、WMS 事件入口、Celery 任务、Composition Root 以及所有待修改模型逐个执行 upstream impact。记录直接调用者、执行流程和风险级别；HIGH/CRITICAL 停止并向用户确认。

- [x] **Step 4: 冻结测试所有权与重量**

  - `tests/runtime/execution/`: 核心执行对象、证据身份、Decision 应用幂等和故障边界。
  - `tests/workline/`: Epoch 插件/配置冻结与绑定。
  - `tests/contracts/wms_adapter/`: 粗分 WMS operation、DTO、HTTP/错误语义。
  - `tests/integration/{execution,wms_adapter}/`: PostgreSQL 约束、事务和真实持久化。
  - `workline_plugins/rough_sorter/tests/`: 拥有粗分业务 Decision、插件集成和部署闭环，使用插件包自己的 Pytest/CI，不进入核心默认 pytest、覆盖率或 HEAVY selector。
  - 供应商一致性验收: 留在供应商 ECS/网关交付边界，不进入本仓库核心质量门禁。

- [x] **Step 5: 更新精确 HEAVY mapping**

  先为 selector 写失败合同，证明 `workline_plugins/rough_sorter/` 下的源码、单元测试和 E2E 都应返回空核心 HEAVY 集，而核心 Composition Root、migration 和共享支撑资产仍按 mapping 选择真实 owner；同时证明尚未配置的 `packages/wes_plugin_sdk/**` 继续 fail closed。随后把 `workline_plugins/**` 加入核心 selector 的明确 ignore，并把 `packages/wes_plugin_sdk/**` 纳入核心候选路径。插件包自己的测试配置与 CI 是其唯一选择器，不得加入核心 selector。

  对新增核心执行模型、migration、WMS Adapter、Celery 任务、核心部署装配和后续实际创建的共享 SDK 文件分别映射真实受影响的核心 HEAVY；只有经评审确认仅由 FAST 承接的共享 SDK 文件才可使用带最终内容指纹的精确 `NONE`，不得用宽泛空 mapping 隐藏未来影响。

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

- [x] **Step 1: 先写失败的 SDK 边界测试**

  测试必须锁定：SDK 不导入 `src.app`、SQLAlchemy/SQLModel、FastAPI、Celery 或 HTTP 库；核心 `src/` 不导入具体 `workline_plugins.*`；插件生产代码只允许导入 SDK 和获批公共合同，不允许导入 `src.app.*` 内部实现。

- [x] **Step 2: 定义实际使用的类型化 Fact**

  只包含粗分闭环需要的不可变输入，例如 SCAN 证据已齐备、WMS 准入/目标/换架计划已决定、设备位置结果已确认、
  Transport 结果已发布、人工对账已决定。Fact 只携带稳定标识、版本和已验证数据，不携带 ORM 对象或原始 wire Payload。

- [x] **Step 3: 定义封闭 Decision**

  建立 `Wait`、`CreateDeviceCommand`、`CreateWmsConfirmation`、`CreateTransportTask`、`PauseForReconciliation`、
  `CompleteExecution`。`CreateTransportTask` 只表达两个获批 `RACK_MOVE`，Decision 必须包含应用幂等所需的稳定业务身份，
  不包含 Repository 或执行回调。

- [x] **Step 4: 定义最窄只读 Protocol**

  只为 handler 当前决策所需的执行快照、位置/资源快照和 Epoch 配置建立读取协议；禁止通用 `Context`、Service Locator、`dict[str, Any]` 或写端口。

- [x] **Step 5: 实现无副作用 handler 元数据装饰器**

  装饰器只附加 `fact_type`、handler 名称和支持版本等静态元数据。模块导入不能注册 handler、修改全局集合、扫描 entry point 或实例化依赖。

- [x] **Step 6: 运行 SDK 和架构门禁**

  Run: `uv run pytest tests/architecture/test_plugin_sdk_boundary_guardrail.py tests/architecture/test_core_plugin_test_ownership_guardrail.py -q`

  Expected: PASS；SDK 为独立包，核心和插件依赖方向单向且静态。

- [x] **Step 7: 提交 SDK 原子变更**

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

- [x] **Step 1: 先写失败的模型与服务测试**

  覆盖：`MaterialExecution` 只允许 `CREATED | RUNNING | HOLD | CLOSED | RECONCILING` 及合法迁移；一个活动 trace 只能有一个
  执行；`InboundEvidence` 同身份同 digest 幂等、异 digest 冲突；`WmsConfirmation` 稳定 `operation + operation_id`、重试不换
  身份；Epoch 固定 `plugin_key`、`plugin_version`、`flow_mode`；禁止通用插件状态 JSON。

- [x] **Step 2: 实现 `MaterialExecution`**

  只保存通用身份、`workline_id`、`line_run_epoch_id`、业务相关键、生命周期、版本和诊断时间。粗分进度不做核心枚举，派生自相关证据、命令、确认和投影。

- [x] **Step 3: 直接收敛 `InboundEvidence`**

  将设备 EVENT/RESULT 和 WMS EVENT/RESULT 表达为有限 evidence kind；保留稳定 source identity、payload digest、规范化 payload、关联对象、应用状态、冲突证据和下游发布时间。Phase 7 入口改为经执行应用端口写入该对象，不允许同时写旧 `DeviceEvidence` 和新表。

- [x] **Step 4: 建立独立 `WmsConfirmation`**

  保存当前 operation、operation_id、请求 digest、不可变请求、执行关联、派发状态、重试资格和响应 evidence 关联。传输重试沿用原 operation_id；WMS `WAIT` 完成本次决定，后续重试业务决策必须创建新的 operation_id。

- [x] **Step 5: 扩展 Epoch 冻结字段**

  为 `LineRunEpoch` 增加非空 `plugin_key`、`plugin_version` 和通用 `flow_mode`，沿用现有 topology/configuration digest 与 device binding。不得添加动态插件表或从当前 Epoch 外加载插件版本。

- [x] **Step 6: 删除被接管的旧 owner**

  successor 测试通过后删除 `DeviceEvidence`/冲突重复定义、`BinMaterialMount` 上只代表旧单点确认的 `WmsConfirmationStatus` 及其无 owner 测试。`DeviceStatusObservation` 若仍由 Phase 7 设备状态拥有则原位保留。

- [x] **Step 7: 由 Alembic generator 创建直接切换 migration**

  Run: `uv run alembic revision -m "收敛 Phase 8 执行对象"`

  生成后编辑 upgrade，直接删除旧表/字段并建立最终约束、索引和 FK；不复制开发数据，`downgrade()` 明确不支持。

- [x] **Step 8: 验证核心执行对象**

  Run: `uv run pytest tests/runtime/execution tests/workline/test_line_run_epoch.py -q`

  Run: `./scripts/run_selected_heavy_local.sh --scope unstaged`

  Expected: FAST 与映射到 execution migration 的 PostgreSQL HEAVY 全部 PASS、无跳过；不存在证据双写或旧确认 owner。

- [x] **Step 9: 提交可靠对象原子变更**

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

- Consumes: Task 1 独立获批粗分合同的 operation 与公共信封，现有 WMS `OutboundHttpTransport`/client 边界。
- Produces: 类型化 WES→WMS 可靠确认与 WMS→WES 持久化事件入口，不把 WMS 业务语义泄漏到基础 HTTP transport。

- [x] **Step 1: 先写失败的合同测试**

  精确覆盖获批 operation、严格字段闭集、大小限制、`operation + operation_id` 幂等、同 identity 异 digest 冲突、HTTP 状态、`retry_after_ms`、错误映射和 OpenAPI。禁止把未获批 operation 填进测试作为未来占位。

- [x] **Step 2: 建立逐盘入库 WES→WMS 类型化 DTO**

  仅实现 `inbound.material.admission_decide@v1`、`inbound.material.target_decide@v1`、
  `inbound.material.placement_report@v1`、`inbound.material.ng_placement_report@v1` 和
  `inbound.source_rack.replacement_plan_decide@v1`；适配器把 transport 响应规范化为 WMS_RESULT `InboundEvidence`，不直接推进
  插件或修改库存投影。

- [x] **Step 3: 建立 WMS→WES 类型化入口**

  Task 4 已建立先持久化 WMS_EVENT `InboundEvidence` 再返回 `202 / RECEIVED` 的入口基础；当时的批量恢复 wire 和 execution
  binding 由 Task 8 直接替换为单 execution `inbound.execution.recovery_decided@v1`。API 层不得访问 Repository 或执行 handler。

- [x] **Step 4: 静态分派现有 WMS 事件端点**

  在现有固定 `/api/v1/wms/events` 入口按有限 operation 显式分派 Transport 与粗分入库 handler；不引入 operation registry、模块扫描或第二个 HTTP Adapter。

- [x] **Step 5: 实现 `WmsConfirmation` 有界派发**

  每批最多处理 100 条资格记录；派发前重新检查 deadline/重试资格；HTTP 未知结果保留原 operation_id 重试；响应必须先持久化为 evidence 再完成 confirmation。不得复用旧 Runtime Effect lane。

- [x] **Step 6: 运行 WMS ACL 验收**

  Run: `uv run pytest tests/contracts/wms_adapter -q`

  Run: `./scripts/run_selected_heavy_local.sh --scope unstaged`

  Expected: 新旧 WMS operation 均由显式 ACL 拥有；基础 outbound HTTP 测试不承载粗分业务断言；真实持久化场景 PASS、无跳过。

- [x] **Step 7: 提交 WMS ACL 原子变更**

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

- [x] **Step 1: 先写失败的处理与应用测试**

  覆盖 evidence claim、重复处理、同证据不同 Decision 冲突、handler 异常、未知 Fact/Decision fail closed、事务回滚、worker reclaim，以及每种允许 Decision 的精确应用结果。

- [x] **Step 2: 构建类型化 Fact**

  只有 evidence 已持久化、digest/身份验证完成且关联对象存在时才能生成 SDK Fact。原始 Payload 的供应商映射必须在 Phase 7/设备附录边界完成，Fact builder 不猜测字段。

- [x] **Step 3: 实现静态插件绑定**

  `plugin_binding.py` 只接收部署时明确传入的 handler 实例映射和只读协议实现；不扫描 filesystem、entry points 或 import 路径，不维护运行时 Catalog。

- [x] **Step 4: 实现类型匹配的 Decision 应用器**

  应用器只分派封闭类型到 `DeviceCommand`、`WmsConfirmation`、执行状态和可选 `TransportTask` 应用端口。每个结果以 evidence identity + decision type/ordinal 幂等；不建立通用 Effect 表或 handler 回调。

- [x] **Step 5: 实现有界异步处理**

  入口 ACK 与业务处理解耦；worker 每批最多 100 条，claim 超时可恢复，失败有界退避，冲突进入 `RECONCILING`。不得使用无限轮询或 WorkLine 全局锁。

- [x] **Step 6: 验证不可逆边界**

  对 `CreateDeviceCommand` 明确测试：明确未接纳可以创建新的恢复 Decision；ACK 后或投递未知只能等待 callback/人工对账，不得改目标或重发等价物理动作。

- [x] **Step 7: 运行核心处理器与启动门禁**

  Run: `uv run pytest tests/runtime/execution tests/deployment/test_execution_worker_startup.py -q`

  Expected: PASS；核心测试使用最小 fake handler，不导入真实 `rough_sorter` 插件。

- [x] **Step 8: 提交核心处理器原子变更**

  Commit suggestion: `feat(execution): 应用类型化事实与封闭决策`

### Task 7: 实现独立粗分机插件包

**Files:**

- Create: `workline_plugins/rough_sorter/pyproject.toml`
- Create: `workline_plugins/rough_sorter/src/rough_sorter/__init__.py`
- Create: `workline_plugins/rough_sorter/src/rough_sorter/plugin.py`
- Create: `workline_plugins/rough_sorter/src/rough_sorter/handlers/__init__.py`
- Create: `workline_plugins/rough_sorter/src/rough_sorter/handlers/material_evidence_ready.py`
- Create: `workline_plugins/rough_sorter/src/rough_sorter/handlers/admission_decided.py`
- Create: `workline_plugins/rough_sorter/src/rough_sorter/handlers/device_position_confirmed.py`
- Create: `workline_plugins/rough_sorter/src/rough_sorter/handlers/target_decided.py`
- Create: `workline_plugins/rough_sorter/src/rough_sorter/handlers/placement_completed.py`
- Create: `workline_plugins/rough_sorter/src/rough_sorter/handlers/replacement_plan_decided.py`
- Create: `workline_plugins/rough_sorter/src/rough_sorter/handlers/transport_outcome_published.py`
- Historical Create, Task 8 Delete/Replace: `workline_plugins/rough_sorter/src/rough_sorter/handlers/reconciliation_decided.py`
- Create: `workline_plugins/rough_sorter/tests/`
- Create only for approved payloads: `workline_plugins/rough_sorter/fixtures/`

**Interfaces:**

- Consumes: `wes_plugin_sdk` Fact/Decision/Protocol、Task 1 获批粗分合同、核心只读快照。
- Produces: `rough_sorter` 业务 Decision；无数据库、网络、Celery、供应商 wire 或基础设施实现。

- [x] **Step 1: 先写插件失败测试**

  最少覆盖：证据不完整 fail closed、准入 `ACCEPT` 不含目标、入料/输送顺序、出口后目标 Cell 晚绑定、业务 `WAIT`、
  `REJECT`/NG、ECS ACK 后禁止等价重放、placement 确认、旧架 release gate、两个 `RACK_MOVE` 稳定身份、新架先成功恢复目标请求、
  两任务失败不级联、delivery unknown、对账完成和重复 Fact 幂等。

- [x] **Step 2: 实现最小插件入口**

  `plugin.py` 显式构造 handler 列表并注入只读协议；不提供通用容器、registry 或自动 discovery。目录只保留实际文件，不预建 `domain/`、`application/`、`services/`、`repositories/`、`adapters/`、`infrastructure/`、`utils/`、`config/`。

- [x] **Step 3: 按业务触发拆分 handler**

  一个文件对应一个稳定业务触发及其决策，不对应原始 EVENT/CALLBACK。handler 保持纯决策：输入 Fact + 只读快照，输出一个或多个封闭 Decision。

- [x] **Step 4: 实现逐盘准入和 PUT 决策**

  `SCAN_COMPLETED` 的 trace、六合一码、直径、厚度、外形结果和位置齐备后创建 WMS admission confirmation；`ACCEPT` 后只创建
  入料 `PICK_AND_PUT`，成功后创建 `MOVE_FORWARD`。料盘可靠到达出口后才请求目标 Cell；业务 `WAIT` 不伪装成设备忙，
  设备忙或 release gate 未释放在 Task 8 按获批设计改为 `DeferExecution`；`REJECT` 只按 WMS 决定进入获批 NG 路径。

- [x] **Step 5: 实现目标晚绑定和不可逆保护**

  WMS 返回精确 Cell 且本地 source/target/设备门禁通过时才创建出料 `PICK_AND_PUT`。没有可用 Cell 时不下发出料命令；
  ECS ACK、FAILED 或结果未知后禁止换 target 或重放等价动作，进入等待或人工对账。

- [x] **Step 6: 实现 placement、换架和 reconciliation 决策**

  PUT/NG 可靠完成后创建对应 WMS report；旧架 release gate 闭合后冻结快照；位置/成员冲突进入最小隔离域；获批
  reconciliation Fact 才能恢复或终结相关执行。

- [x] **Step 7: 实现两个获批 `RACK_MOVE`**

  按同一 `rack_replacement_id` 创建 `OLD_OUT` 和 `NEW_IN` 两个独立 `CreateTransportTask`；以
  `(rack_replacement_id, leg)` 为业务幂等键，由应用端在首次调用前原子持久化到全局唯一 UUIDv7 `client_request_id`，同键重试
  复用原 UUIDv7 和原 Handle。两任务可以同时提交，顺序不由插件控制；新架匹配 T3 成功后可重新请求 Cell，旧架失败只隔离
  旧 rack，新架失败阻止出料。

- [x] **Step 8: 独立运行插件包测试**

  Run: `uv run --project workline_plugins/rough_sorter pytest -q`

  Expected: 插件测试全部 PASS；核心默认 pytest 不收集该目录；插件 wheel 可独立构建且运行依赖只包含 SDK 和必要公共类型。

- [x] **Step 9: 提交粗分插件原子变更**

  Commit suggestion: `feat(rough-sorter): 重建粗分机执行决策`

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

- [x] **Step 1: 写 defer 与领取顺序的失败测试**

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

- [x] **Step 2: 运行精确 RED**

  Run: `uv run pytest tests/architecture/test_plugin_sdk_boundary_guardrail.py tests/runtime/execution/test_fact_processor.py tests/runtime/execution/test_inbound_evidence_repository.py -q`

  Expected: 只因 SDK 尚无 `DeferExecution`、claim 仍递增 attempt、processor 仍写 digest/published、领取顺序未实现而失败。

- [x] **Step 3: 实现最小 SDK 与 processor 语义**

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

- [x] **Step 4: 写并实现重启 Epoch 门禁**

  Repository 增加只读查询：

  ```python
  async def has_active_epoch(self, db: AsyncSession) -> bool: ...
  ```

  `LineRunEpochService.assert_execution_worker_startable(db)` 只在存在遗留 `ACTIVE` Epoch 时抛明确领域错误，不修改数据库。execution
  worker child 初始化调用该门禁；`claim_decision_batch()` 通过 join/exists 只领取关联 `ACTIVE` Epoch 的 evidence，`CLOSED` 永久不领。
  新 Epoch 激活仍由 Web/API 事务 owner 完成，不由 worker 自动创建或关闭。

- [x] **Step 5: 运行 GREEN 与真实 PostgreSQL owner**

  Run: `uv run pytest tests/architecture/test_plugin_sdk_boundary_guardrail.py tests/runtime/execution/test_fact_processor.py tests/runtime/execution/test_inbound_evidence_repository.py tests/deployment/test_execution_worker_startup.py -q`

  Run: `uv run pytest tests/integration/execution/test_decision_processing_postgresql.py -q`

  Expected: 新 evidence 优先、超过 batch limit 的 defer 公平轮转、CLOSED Epoch 不领取、遗留 ACTIVE Epoch 启动失败均 PASS 且无 skip。

- [x] **Step 6: Review、selector 与独立提交**

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

- [x] **Step 8: Review、selector 与独立提交**

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

- [x] **Step 1: 写 post-commit 唤醒 RED**

  `TaskQueueGateway` 只增加两个无载荷方法：

  ```python
  def enqueue_execution_facts(self) -> None: ...
  def enqueue_wms_confirmations(self) -> None: ...
  ```

  测试分别证明 Device/WMS_RESULT/Transport material evidence 提交后只发送 execution scan，立即可派发的普通 confirmation 提交后只发送
  WMS scan，未来到期的 business WAIT follow-up 不即时发送；事务回滚不发送，enqueue 异常只记录结构化日志且不改变已提交事实。

- [x] **Step 2: 运行唤醒 RED 并实现事务 owner 调用**

  Run: `uv run pytest tests/deployment/test_celery_task_runtime_contract.py tests/runtime/execution/test_wms_confirmation_service.py -q`

  Expected: 因 gateway 和 commit 后调用点不存在而失败。GREEN 时只能由真正执行 `commit`/session context 退出的应用服务调用 gateway，
  Repository、model、插件和 handler 不得导入 Celery。

- [x] **Step 3: 写静态 Composition Root 与版本门禁 RED**

  `tests/deployment/test_rough_sorter_plugin_startup.py` 锁定：仅 `deployment/rough_sorter_composition.py` 可 import `rough_sorter`；未知
  `plugin_key`、版本漂移、Epoch digest 漂移或角色缺绑定时 fail closed；Web 和 Celery 获得同一不可变配置。核心
  `src/app/**` 对 `workline_plugins.*`/`rough_sorter` 零命中。

- [x] **Step 4: 实现显式装配、workspace 与镜像**

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

- [x] **Step 5: 运行部署 GREEN、镜像与最终门禁**

  Run: `uv sync --dev && uv lock --check`

  Run: `uv run pytest tests/deployment/test_rough_sorter_plugin_startup.py tests/deployment/test_celery_task_runtime_contract.py tests/deployment/test_execution_worker_startup.py -q`

  Run: `docker build -t wes-backend:phase8-rough-sorter .`

  Run: `./scripts/git-quality-gate.sh --profile quality`

  Run: `uv run scripts/select_heavy_tests.py --scope unstaged`

  Run: `./scripts/run_selected_heavy_local.sh --scope unstaged`

  Expected: Web/Celery 静态绑定一致；两个 Beat 分别恢复遗漏；插件与 SDK 在镜像内可导入；QUALITY、选中 HEAVY、干净 migration chain
  全绿且无跳过。插件 FAST、核心 FAST、PostgreSQL HEAVY 仍分别报告，不能互相代证。

- [x] **Step 6: Fresh Review 与独立提交**

  Reviewer 核对当前完整 Task 8 diff：基础/业务边界、重启安全、WMS/RCS/ECS 权威、post-commit 时序、旧 recovery absence、测试所有权和
  HEAVY mapping。修复后只刷新失效证据。

  Commit: `feat(deployment): 显式装配粗分机插件`

### Task 9: 完成供应商一致性、插件闭环和业务验收

**Files:**

- Create: `workline_plugins/rough_sorter/tests/e2e/test_business_loop.py`
- Create or update: named non-secret E2E fixtures under `workline_plugins/rough_sorter/fixtures/`
- Create: `docs/integration/rough-sorter-supplier-conformance.md`
- Create: `docs/integration/rough-sorter-joint-acceptance.md`

**Interfaces:**

- Consumes: 供应商 ECS/网关、WMS mock/联调端、真实 PostgreSQL/Redis/Celery/HTTP、获批附录与插件镜像。
- Produces: 供应商一致性结果、单条真实 event→evidence→decision→command→callback→confirmation→complete 闭环和现场联合验收证据。

- [ ] **Step 1: 在供应商交付边界运行一致性验收**

  覆盖附录 task/event、字段闭集、身份、ACK、CALLBACK、错误、时限、投递未知和不可逆点。结果只记录版本、环境、通过/失败和证据位置；供应商私有 DTO/认证/原始映射不复制进 WES 核心或插件。

- [x] **Step 2: 先在插件包建立失败的部署 E2E**

  单场景必须通过插件包独立测试配置，经过真实 HTTP ingress、持久化 evidence、Celery handler、DeviceCommand 派发、callback、WmsConfirmation 和 execution 完成；不得直接调用 Service 跳过装配。失败原因应是闭环尚未接通，不是依赖缺失或测试跳过。该测试不进入核心 `tests/`、核心覆盖率或核心 HEAVY selector。

- [x] **Step 3: 跑通主成功路径**

  `SCAN_COMPLETED` → WMS `ACCEPT` → 入料 `PICK_AND_PUT` → `MOVE_FORWARD` → WMS 目标 Cell 晚绑定 → 出料
  `PICK_AND_PUT` → placement report → WMS `RECORDED` → `MaterialExecution.CLOSED`。验证每个可靠对象身份稳定且无双写。

- [ ] **Step 4: 跑通安全失败路径**

  至少现场/集成验证：重复与冲突 evidence、业务 `WAIT`、无 Cell 不下发出料、ACK 后禁止等价重放、callback 未知、旧架 release
  gate、两个 `RACK_MOVE` 独立失败、新架先成功恢复目标请求和单对象人工核验恢复。非功能性负载、HA 和多供应商矩阵不作为
  MVP 退出条件。

  2026-08-19 仓内子集已通过：WMS `WAIT` 完成本次确认、创建新 operation 且 `ACCEPT` 前无设备命令；ECS `ACK` 后跨真实
  下一次 Beat 保持 `ACKNOWLEDGED:1` 且不重放，匹配 callback 后才关闭 execution。其余需要真实外部系统或物理事实的场景
  继续保留在本 Step，不以 Mock 代证。

- [x] **Step 5: 分别执行插件闭环与受影响核心 HEAVY**

  Run: `uv run --project workline_plugins/rough_sorter pytest workline_plugins/rough_sorter/tests/e2e -q`

  Run: `uv run scripts/select_heavy_tests.py --base 'ab02f42f^'`

  Run: `./scripts/run_selected_heavy_local.sh --base 'ab02f42f^'`

  当前 E2E Result（2026-08-19，`develop@bda2079d`，source manifest `e60415d18a05ef02d1961e30e4572d59c5a544ab`）:
  插件 E2E `11 passed, 0 skipped`。先前 Phase 8 快照 `aab69fd7` 的证据为：计划锁定核心 owner `21 passed`；
  selector 选中 31 个 HEAVY 资产，实际执行 `362 passed, 0 skipped`。该历史绿灯不作为 `bda2079d` 的当前 HEAVY 证据；
  核心 selector 不包含粗分插件私有测试。

- [x] **Step 6: 记录分层验收结论**

  分别记录核心基线、WMS Adapter、供应商一致性、插件业务和现场联合验收状态。任一层未通过时整体不得标记完成，也不得用其他层测试替代。

- [x] **Step 7: 提交验收资产原子变更**

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
- Produces: 无本阶段直接替换的旧粗分/WMS Effect 双路径、Phase 10 残余 owner 清单、当前开发指南与主计划一致、可独立交付的 Phase 8 最终状态。

- [x] **Step 1: 执行本阶段直接旧路径 absence 扫描并冻结 Phase 10 交接**

  Run: `rg -n "plugin_state|src\.app\.runtime\.workline_plugins|old_rough_sorter" src packages workline_plugins tests --glob '*.py'`

  Run: `rg -n "confirm_inbound|notify_pkg_binding" src packages workline_plugins tests --glob '*.py'`

  Run: `rg -n "RuntimeInbox|ExecutionSession|RuntimeIntent|RuntimeHold|SystemCapability" src packages workline_plugins tests --glob '*.py'`

  Expected: 第一组本阶段直接旧路径在生产代码零命中，测试只允许明确的 absence 字面量；不得保留 dual path、shim、registry 或被新对象取代的旧状态 owner。`confirm_inbound`/`notify_pkg_binding` 是后续入库/上架合同仍使用的通用 WMS operation，记录其当前 owner 和 Phase 9/10 guardrail，不由粗分 Phase 8 删除。跨阶段 Runtime 命中必须逐项记录当前 owner、消费者和 successor，作为 Phase 10 输入，不要求 Phase 8 越权删除或全仓零命中。

- [x] **Step 2: 验证依赖方向和测试拓扑**

  Run: `uv run pytest tests/architecture/test_suite_topology_guardrail.py tests/architecture/test_core_plugin_test_ownership_guardrail.py tests/architecture/test_plugin_sdk_boundary_guardrail.py -q`

  Run: `uv run pytest --collect-only -q -o addopts='' | tail -5`

  Expected: 核心默认收集不包含插件和供应商测试；核心、SDK、插件依赖方向符合计划。

- [x] **Step 3: 运行插件和核心 FAST**

  Run: `uv run --project workline_plugins/rough_sorter pytest workline_plugins/rough_sorter/tests --ignore=workline_plugins/rough_sorter/tests/e2e -q`

  Run: `uv run pytest tests/ -q`

  Expected: 全部 PASS；插件结果单独报告，不合并成核心覆盖率数字。

- [x] **Step 4: 运行完整质量与受影响 HEAVY**

  Run: `uv run ruff format --check . && uv run ruff check .`

  Run: `./scripts/git-quality-gate.sh --profile quality`

  Run: `./scripts/run_selected_heavy_local.sh --scope unstaged`

  Expected: 质量门禁和受影响 HEAVY 全部 PASS，真实 HEAVY 无跳过；不使用 `--no-verify` 或降低断言换取通过。

- [x] **Step 5: GitNexus 最终范围检测与独立评审**

  运行 GitNexus detect changes，确认受影响符号/流程只属于 Phase 8。独立评审重点检查：WMS/WES/ECS/RCS 权威边界、物理不可逆点、并发隔离域、插件基础设施泄漏、测试代证和超出 MVP 的抽象。

- [x] **Step 6: 更新当前开发真源**

  用已验证实现更新 `docs/plugin_development_guide.md`：保持“一插件一子目录”，handler 按业务触发拆分，不写成“一物理 EVENT/CALLBACK 一文件”；明确装饰器元数据、显式 DI 和无动态注册表。主计划只在所有分层验收通过后把 Phase 8 标为完成。

- [x] **Step 7: 归档被当前结果取代的过程文档**

  逐个确认 obsolete 文档及当前引用，移动到 `../archive_docs/wes_backend/` 对应原相对路径；项目内不保留副本、占位、软链接或转发页。`docs/hardware/**` 永不进入此清理。

- [x] **Step 8: 提交 Phase 8 仓内收尾**

  Commit suggestion: `docs(phase8): 收敛仓内交付与外部阻塞`

## Phase 8 Exit Gate

只有以下条件同时成立，Phase 8 才能从 `GATED/IN_PROGRESS` 改为 `COMPLETE`：

1. 独立 Phase 8 粗分入库合同和粗分机设备合同附录已联合批准；Transport 消费唯一结论为两个既有 `RACK_MOVE`。
2. `MaterialExecution`、`InboundEvidence`、`WmsConfirmation`、Epoch 插件冻结和封闭 Decision 应用器成为唯一生产路径，无旧 owner/双写/兼容路径。
3. `wes_plugin_sdk` 与 `rough_sorter` 均可独立安装、构建和测试；SDK 只含真实使用的稳定接口。
4. Web 与 Celery 通过一个显式 Composition Root 绑定同一插件版本、配置 digest 和设备 binding；核心无具体插件 import 或供应商分支。
5. Phase 7 Device/ECS、WMS Adapter、供应商一致性、插件业务和现场联合验收分别通过，且证据不互相代替。
6. 成功闭环及重复、冲突、WAIT、投递未知、不可逆、释放围栏和人工核验恢复路径全部通过；受影响 HEAVY `failed = 0` 且
   `skipped = 0`。
7. 本阶段直接替换的旧粗分/WMS Effect 双路径 absence 扫描通过，跨阶段 Runtime 残余已形成 Phase 10 精确交接清单；当前文档真源已更新，过期过程文档已移出项目目录，`docs/hardware/` 保持原样。

## 明确延期到后续阶段

- `BinExecution`、满箱交换、自动/人工分拣和复杂出库：Phase 9。
- 动态插件发现、热插拔、插件市场、数据库插件注册表：无已批准需求，YAGNI。
- 第二个设备 HTTP Adapter、供应商私有 client/auth/DTO：供应商 ECS/网关边界，不进入 WES。
- 多供应商并行矩阵、容量压测、HA/容灾平台、通用运维仪表盘：MVP 后按真实非功能需求单独立项，不阻塞 Phase 8 正确性与安全退出门禁。
