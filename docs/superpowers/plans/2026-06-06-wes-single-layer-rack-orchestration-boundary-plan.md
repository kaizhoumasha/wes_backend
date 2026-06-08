# WES 单层货架执行编排边界 Implementation Plan

> 状态：后端已验收 - 前端承接待独立计划完成
> 进度更新：2026-06-08
> 对齐 SPEC：`docs/superpowers/specs/2026-06-06-wes-single-layer-rack-orchestration-boundary-spec.md`
>
> 2026-06-08 功能性验收结论：本仓库后端合同、服务、OpenAPI、文档守护和目标测试已通过；跨仓前端 generated types、scene adapter 与浏览器视觉 QA 不在本仓库落地范围，按 `wes_frontend` 独立计划继续跟进。
>
> 本计划 Task 0-10 的后端执行步骤均已完成，验收标准、review decisions 和验证记录保留在本文末尾，后续变更按增量 PLAN 或 ADR 处理。
>
> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use Markdown checkbox syntax for tracking.

**Goal:** 将 `2026-06-06-wes-single-layer-rack-orchestration-boundary-spec.md` 落成可验证的 WES v1 执行边界：WES 只权威维护单层货架 active 执行快照和 WorkLine/Station 当前执行上下文，运输与非单层资源由 WMS 负责。

**Architecture:** 本计划采用“边界守护测试优先 + 最小运行合同 + 分拣链路触发修正”的顺序。WES 不新增全局 Location 主账、不新增非单层库存主账、不直连 RCS/AGV/CTU；Station lease 只表达 WES 侧业务绑定，运输需求统一进入 WMS 外部请求合同。

**Tech Stack:** FastAPI / SQLModel / SQLAlchemy AsyncSession / Pydantic v2 / pytest / Ruff / GitNexus / uv。

---

## 插件级单层货架承接矩阵

本计划除通用 Station lease / single-layer orchestration 外，必须验证现有插件的真实业务承接点。未来新增插件只要涉及单层货架，也必须通过同一矩阵准入。

| 插件 | 单层货架承接点 | 必须验证 | 禁止行为 |
| --- | --- | --- | --- |
| 粗分机 `rough_sorter` | 下料/出料工作位承接单层货架；新合同必须显式传入 `work_position_code` / `target_position_role` | active rack 缺失时提交 WMS 补给到粗分机工作位；WMS 到位回调只恢复当前等待 session；callback active_bin_rack 可作为执行证据继续分格 | 不直接调用分拣插件；不让货架 ready 事实选择下一条业务线；不把非单层 evidence 转成 active snapshot；不继续依赖隐藏默认 `SINGLE_LAYER_A` |
| SMT 分拣入库 `smt_sorting_inbound` | source station / target station 上的单层 active snapshot；NG station 只是证据站点 | WorkLine READY 不绑定 station；source/target station lease 可用才允许业务动作；target allocation 只消费 active target snapshot；`COMMAND_NG_PLACE` 由 `TARGET_ARM` 执行 | 不新增 `NG_ARM`；不从 START 直接创建分拣 command；不从 raw context 推断 station/rack 权威 |
| 未来涉及单层货架的插件 | 插件合同必须通过 manifest capability marker 加 `WorklinePluginManifest.single_layer_boundaries` 显式声明 station/rack boundary | 每个 boundary 声明 `station_code` / `position_code`、`rack_kind=SINGLE_LAYER`、承接角色、业务需求类型、WMS operation 类型、snapshot kind、lease scope；manifest 同步声明 `capabilities` / `resource_kinds` / `requires_single_layer_boundary` 或等价轻量 marker；通过统一准入测试 | 不靠默认 `SINGLE_LAYER_A` 隐式绑定；不绕过 Station lease；不判断 WMS 库存授权或物理占用；不靠字符串扫描替代显式合同 |

## 计划约束

本仓库 `AGENTS.md` 对规划文档有更高优先级要求：计划文档必须表达目标、架构决策、任务边界、验收标准和验证方式，不粘贴完整类实现、完整函数实现或大段测试代码。因此本计划提供精确文件、测试名、断言点和命令，代码细节在执行阶段通过 TDD 和 diff 体现。

执行阶段必须遵守：

- 使用中文沟通、文档和 Commit Comment。
- 项目命令统一使用 `uv run ...`。
- 修改函数、类、方法前运行 GitNexus impact analysis；HIGH/CRITICAL 风险先汇报。
- 不回滚当前已有变更：`docs/architecture/SRS.md` 与 SPEC 文件已有变更视为用户上下文。
- API 不直接访问数据库；新增运行逻辑遵守 API -> Service -> Repository -> Database。
- 不新增 WES 对 RCS/AGV/CTU 的直连 Driver Plugin。

## 目标边界

### 必须落地

- `WORKLINE_START_REQUESTED` 只表示 WorkLine 经 START 准入进入 `READY` 待机状态。
- WorkLine 运行状态模型继续使用既有 `READY`，不新增 WorkLine 级空闲状态。
- Station 是单层货架 dock / 业务端点，不是设备。
- Station 空闲只表示 WES 侧没有 active rack binding、active dispatch lease、active session。
- 分拣机只有 `SOURCE_ARM` 和 `TARGET_ARM`；NG 放置由 `TARGET_ARM` 执行。
- WES 只权威维护 `SINGLE_LAYER` active 执行快照。
- 五层货架、生产货架、退料货架、转运货架、库存、逻辑位置真实占用、运输设备调度由 WMS 管理或转发。
- WES v1 所有搬运、交换、旋转需求提交 WMS；WMS 负责转发 RCS/AGV/CTU 并回传结果。

### 明确不做

- 不实现 WES 全局货架调度平台。
- 不让 WES 判断物理位置真实占用或区域拥堵。
- 不让 WES 管理五层货架 A/B 面容量、冷热区、空箱资源授权。
- 不让 WES 管理退料货架库存主账。
- 不新增 direct RCS/AGV/CTU client。
- 不把粗分机与分拣机做成直接互调。
- 不把货架状态变化设计成业务选择器。

## 文件结构

### 文档与守护测试

- Create: `tests/docs/test_wes_resource_boundary_docs.py`
  - 负责扫描 SRS、ADR、SPEC 中的关键边界表达，防止后续文档回退为 WES 直连 RCS、WES 管库存主账或 WorkLine START 即开始作业。
- Modify if guard fails: `docs/architecture/SRS.md`
  - 仅修正原则性冲突，不加入实现细节。
- Modify if guard fails: `docs/architecture/adr/2026-05-13-wes-wms-rcs-resource-boundary.md`
  - 仅补充与本 SPEC 一致的边界措辞。
- Modify if guard fails: `docs/superpowers/specs/2026-06-06-wes-single-layer-rack-orchestration-boundary-spec.md`
  - 仅修正与 SRS/ADR 冲突的边界措辞，不新增实现细节。

### WorkLine / Station 合同

- Create: `src/app/workline/services/station_lease_service.py`
  - 负责判断某 WorkLine 的单层货架 Station 在 WES 侧是否可绑定。
  - 只读取 WES 业务绑定事实：rack position 配置、active rack placement、active SystemOutbox、open session context。
  - 不读取或判断物理位置真实占用。
- Modify: `src/app/workline/services/__init__.py`
  - 导出 `StationLeaseService` 与 `station_lease_service`。
- Modify: `src/app/workline/repositories/session_repository.py`
  - 增加 open session 查询能力，供 Station lease 服务按 WorkLine 查询当前未闭环 session。
- Modify: `src/app/sys/repositories/outbox_repository.py`
  - 增加按 WorkLine、Station/position 和 active status 查询当前外部派发 lease 的能力。
- Test: `tests/workline_runtime/test_station_lease_service.py`
  - 覆盖 Station 空闲、active placement 占用、active outbox 占用、terminal outbox 不占用、open session 占用、非单层 position 拒绝。

### WMS 运输需求合同

- Modify: `src/app/wms_integration/services/transport_contract.py`
  - 在既有 WMS 对接辅助域中封装 WES -> WMS 搬运、交换、旋转需求的最小 payload 约定。
  - 统一稳定业务键、`dispatch_key`、`target_code`、endpoint 逻辑名和 evidence 字段。
  - WES -> WMS outbound 请求不得用 `source_system="WMS"` 表达权威；若需要表达目标网关或资源权威，使用 `target_code`、`gateway_system="WMS"` 或 `authority_system="WMS"` 等不会混淆发送方的字段。
  - 不包含 RCS URL、AGV device id、CTU device id 或物理坐标。
- Modify if needed: `src/app/wms_integration/services/__init__.py`
  - 仅当新增公开 builder 或常量时导出。
- Test: `tests/wms_integration/test_transport_contract.py`
  - 覆盖请求 builder 的字段稳定性、payload 深拷贝、递归拒绝 RCS/AGV/CTU 直连字段、拒绝空 dispatch key。
- Modify: `tests/workline_runtime/test_runtime_intent_contract.py`
  - 新增合同断言：新运输需求使用既有 rack operation intent 落到 WMS 目标，不把 RCS 当成 WES 直接调用对象。
- Modify: `tests/workline_runtime/test_runtime_intent_effects.py`
  - 新增 effect 断言：rack operation request 创建 `EXTERNAL_HTTP` outbox、session 进入 `WAITING_EXTERNAL`、wait token 使用稳定 operation key / dispatch key。

### 单层货架 active 快照边界

- Modify: `src/app/resource/services/active_rack_snapshot_service.py`
  - 只在现有行为需要收紧时调整；保持该服务定位为 SMT 当前单层 active rack 恢复服务。
- Test: `tests/resource/test_smt_active_rack_snapshot_service.py`
  - 增加用例证明服务只根据 WorkLine + 单层 station position 恢复 active rack。
  - 增加用例证明多 active placement 或非单层 position 不产生可执行 active snapshot。
  - 增加用例证明非单层资源只能作为 evidence/projection，不参与单层 active snapshot 权威判断。

### 分拣业务触发链路

- Create: `src/app/workline/services/single_layer_rack_orchestration_service.py`
  - 负责把业务需求转换为“检查 WorkLine READY -> 检查 Station lease -> 读取单层 active snapshot 或提交 WMS 载入需求”的最小服务。
  - 该服务不选择物理位置、不判断 WMS 资源容量、不驱动 RCS/AGV/CTU。
  - 输出 rack operation 语义，接入既有 `RuntimeIntent.rack_operation_request(...)` 路径，不直接产出裸 WMS external request。
- Modify: `src/app/workline/services/__init__.py`
  - 导出 `SingleLayerRackOrchestrationService` 与服务实例。
- Test: `tests/workline_runtime/test_single_layer_rack_orchestration_service.py`
  - 覆盖 WorkLine 未 READY 时不生成运输需求。
  - 覆盖 Station 被 WES 业务 lease 占用时不生成重复 dispatch。
  - 覆盖业务需求存在且 Station 可用时生成 WMS 载入请求。
  - 覆盖粗分机释放事实不会直接调用分拣插件，只进入资源事实或 WMS 需求。
- Modify if failing: `src/workline_plugins/smt_sorting_inbound/flow_service.py`
  - 仅在现有 NG 或触发条件测试失败时修正。
- Modify if failing: `src/workline_plugins/smt_sorting_inbound/plugin.py`
  - 仅在 manifest 或命令目标角色测试失败时修正。
- Test: `tests/workline_runtime/test_smt_sorting_inbound_plugin.py`
  - 保持 `COMMAND_NG_PLACE` 目标角色为 `ROLE_SORTING_TARGET_ARM`。
  - 保持 `WORKLINE_START_REQUESTED` 不进入插件 supported events。
- Test: `tests/integration/workline_runtime/test_cross_plan_sandbox_smoke.py`
  - 保持 START 准入、READY、NG 目标角色、RuntimeHold/对账路径的跨计划回归。

### 插件级单层货架准入

- Create: `tests/workline_runtime/test_plugin_single_layer_rack_boundary.py`
  - 负责枚举已注册插件，验证所有涉及单层货架的插件都通过 `WorklinePluginManifest.single_layer_boundaries` 显式声明 station/rack boundary。
  - 粗分机与 SMT 分拣入库是首批强制验证对象。
  - 未来插件若声明 `SINGLE_LAYER`、rack operation、active snapshot 或 station lease，必须通过同一准入门禁。
- Modify if failing: `src/workline_runtime/plugin_manifest.py`
  - 增加轻量 `single_layer_boundaries` manifest 合同，并保持不涉及单层货架的插件可不声明。
- Modify if failing: `tests/workline_runtime/test_plugin_manifest_and_topology.py`
  - 覆盖 boundary 字段归一化、缺失允许、非法声明拒绝等 manifest 合同。
- Modify if failing: `src/workline_plugins/rough_sorter/plugin.py`
  - 仅当现有粗分机 station/rack 承接语义无法通过测试时收紧。
- Modify if failing: `src/workline_plugins/smt_sorting_inbound/plugin.py`
  - 仅当 manifest / command target roles / station evidence 与边界冲突时收紧。
- Modify if failing: `src/workline_plugins/smt_sorting_inbound/flow_service.py`
  - 仅当 target snapshot / station lease 语义与边界冲突时收紧。
- Modify if failing: `src/app/workline/models/workline.py`
  - 在 `WorkLinePluginManifestSummary` 中暴露只读 `single_layer_boundaries`，供配置台和前端运行态视图消费同一显式合同。
- Modify if failing: `src/app/workline/services/workline_service.py`
  - 从插件 manifest 归一化并导出 boundary summary，非法 boundary 返回明确校验错误。
- Modify if failing: `tests/test_workline_service_plugin_validation.py`
  - 覆盖 manifest summary 包含单层货架 boundary、未知插件不返回伪 boundary、非法 boundary 拒绝。
- Modify if failing: `tests/test_workline_routes.py`
  - 覆盖公开 manifest route 响应包含 `single_layer_boundaries`、未知插件不返回伪 boundary、OpenAPI schema 暴露该字段。
- Modify if failing: `src/app/workline/v1/workline.py`
  - 仅当公开 route 或 OpenAPI schema 未随 `WorkLinePluginManifestSummary` 正确暴露 boundary 时修正。

### 运行态结构化展示合同

- Modify if failing: `src/app/workline/models/runtime.py`
  - 在 `RuntimeWorklineDetailResponse` 或其下游结构中暴露前端可直接消费的运行态边界字段。
- 后端 Runtime detail / OpenAPI / generated types 必须使用下表的 snake_case 名称；前端 scene adapter 负责转换为 camelCase `RuntimeSceneModel` 字段，不得用“等价字段”替代。
- Modify if failing: `src/app/workline/services/runtime_query_service.py`
  - 从后端结构化运行事实、manifest boundary 和 runtime wait context 归一化这些字段；不得要求前端解析 raw JSON。
- Modify if failing: `src/app/workline/v1/runtime.py`
  - 仅当 response model / OpenAPI schema 未正确暴露运行态字段时修正。
- Test: `tests/api/test_workline_runtime_api.py`
  - 覆盖 runtime detail route 响应和 OpenAPI schema 包含前端所需结构化字段。
- Test: `tests/workline_runtime/test_runtime_query_service.py`
  - 覆盖 READY、Station lease busy、active snapshot、等待 WMS、WMS 回调证据和 generic evidence fallback 的归一化。

字段合同：

| 后端字段 | 前端 scene 字段 | 状态集合 | 来源与 fallback |
| --- | --- | --- | --- |
| `workline_readiness` | `worklineReadiness` | `READY` / `NOT_READY` / `UNKNOWN` | 来源为 WorkLine runtime status；无法映射时为 `UNKNOWN`。 |
| `station_lease` | 前端 adapter 内部转换为 `stationLease` | `IDLE` / `ACTIVE_RACK_BOUND` / `ACTIVE_DISPATCH_LEASE` / `ACTIVE_SESSION_BOUND` / `UNKNOWN` | 来源为 Station lease 服务；不得由前端扫描 raw session context 推断。 |
| `single_layer_rack_snapshot` | `singleLayerRackSnapshot` | `ACTIVE` / `MISSING` / `INVALID` / `NON_SINGLE_LAYER_EVIDENCE` / `UNKNOWN` | 来源为单层 active snapshot、manifest boundary 和资源证据；非单层资源只能降级为 evidence。 |
| `rack_operation_wait` | `rackOperationWait` | `WAITING_WMS` / `WMS_CALLBACK_RECEIVED` / `TIMEOUT` / `FAILED` / `NONE` / `UNKNOWN` | 来源为 runtime wait context、rack operation task/outbox 和 WMS 回调；无等待时为 `NONE`。 |
| `resource_evidence_kind` | `resourceEvidenceKind` | `WES_ACTIVE_SNAPSHOT` / `WMS_CALLBACK_EVIDENCE` / `TRACE_RESOURCE_EVIDENCE` / `GENERIC_EVIDENCE` / `UNKNOWN` | 来源为结构化 evidence kind；缺少可信分类时降级为 `GENERIC_EVIDENCE` 或 `UNKNOWN`。 |

Nullable / missing 规则：

- 除 `rack_operation_wait` 可明确返回 `NONE` 外，字段不得省略；无法判断时返回 `UNKNOWN`。
- OpenAPI schema 必须暴露上述 snake_case 字段和枚举，前端 generated types 按当前项目风格消费 snake_case；前端 scene adapter 再转换为 camelCase scene model 字段。若执行阶段决定改为 Pydantic alias 输出，必须同时补 response-by-alias、OpenAPI 和 generated type 回归测试。
- 字段缺失属于后端合同缺口，前端只能显示通用 evidence fallback，不能从 `context_json`、`payload_json`、`event_payload` 或 raw badge 文本推断业务含义。

### 出库、退料、转运文档边界

- Modify if guard fails: `docs/architecture/SRS.md`
  - 出库/生产发料：业务需求由工单、波次或产线请求驱动，库存预留与扣减由 WMS 确认。
  - 退料：`Return_Rack_Execution_View` 作为 WES 执行投影或证据视图，不作为库存主账。
  - 转运/补给/空架回流：统一表达为 WMS 搬运需求。
- Test: `tests/docs/test_wes_resource_boundary_docs.py`
  - 覆盖出库、退料、转运章节的关键语义。

## Task 0: 实施前安全检查

**Files:**

- Inspect: `git status --short`
- Inspect: GitNexus impact analysis

- [x] **Step 1: 确认当前工作区**

  Run:

  ```bash
  git status --short
  ```

  Expected: 至少能看到本 SPEC 和 SRS 的既有变更；记录其它用户变更，后续任务不得回滚。

- [x] **Step 2: 检查 GitNexus 索引状态**

  Run:

  ```bash
  npx gitnexus status
  ```

  Expected: repo `wes_backend` 已索引。若提示 stale，先运行 `npx gitnexus analyze`。

- [x] **Step 3: 对计划会修改的符号运行 impact analysis**

  Use GitNexus MCP before editing each existing function/class/method. The list below is the minimum known set; each later task must extend it for any additional existing symbol it touches:

  ```text
  gitnexus_impact({target: "SystemOutboxRepository", direction: "upstream"})
  gitnexus_impact({target: "WorklineSessionRepository", direction: "upstream"})
  gitnexus_impact({target: "SmtActiveRackSnapshotService", direction: "upstream"})
  gitnexus_impact({target: "RuntimeIntent", direction: "upstream"})
  gitnexus_impact({target: "RuntimeIntentEffects", direction: "upstream"})
  gitnexus_impact({target: "WorklinePluginManifest", direction: "upstream"})
  gitnexus_impact({target: "WorkLinePluginManifestSummary", direction: "upstream"})
  gitnexus_impact({target: "WorkLineService", direction: "upstream"})
  gitnexus_impact({target: "get_plugin_manifest_summary", direction: "upstream"})
  gitnexus_impact({target: "RuntimeWorklineDetailResponse", direction: "upstream"})
  gitnexus_impact({target: "RuntimeQueryService", direction: "upstream"})
  gitnexus_impact({target: "get_runtime_workline_detail", direction: "upstream"})
  gitnexus_impact({target: "SmtSortingInboundPlugin", direction: "upstream"})
  gitnexus_impact({target: "SmtSortingInboundFlowService", direction: "upstream"})
  ```

  Expected: 记录 direct callers、affected processes、risk level。若任一目标返回 HIGH 或 CRITICAL，先向用户汇报再继续对应任务。若后续实际修改 route handler、manifest route、model method、service helper 或 repository method，也必须先对对应符号运行 impact analysis，不能只依赖本清单。

- [x] **Step 4: 建立测试基线**

  Run:

  ```bash
  uv run pytest tests/workline_runtime/test_smt_sorting_inbound_plugin.py tests/test_workline_service_plugin_validation.py tests/workline_runtime/test_reserved_runtime_events.py tests/integration/workline_runtime/test_cross_plan_sandbox_smoke.py
  ```

  Expected: 基线通过。若失败，先确认是否与当前计划相关；无关失败记录在执行日志中，不用顺手重构。

## Task 1: 文档边界守护测试

**Files:**

- Create: `tests/docs/test_wes_resource_boundary_docs.py`
- Modify if failing: `docs/architecture/SRS.md`
- Modify if failing: `docs/architecture/adr/2026-05-13-wes-wms-rcs-resource-boundary.md`
- Modify if failing: `docs/superpowers/specs/2026-06-06-wes-single-layer-rack-orchestration-boundary-spec.md`

- [x] **Step 1: 写文档守护测试**

  Test names:

  - `test_srs_states_wes_does_not_directly_dispatch_rcs_agv_ctu`
  - `test_srs_keeps_single_layer_snapshot_as_wes_authority_only`
  - `test_srs_keeps_non_single_layer_inventory_authority_in_wms`
  - `test_srs_keeps_empty_single_layer_rack_authority_in_wms`
  - `test_srs_defines_workline_start_as_ready_admission_not_job_start`
  - `test_srs_defines_sorter_ng_place_on_target_arm`
  - `test_srs_defines_return_rack_as_execution_view_not_inventory_master`
  - `test_srs_keeps_production_and_return_rack_slots_as_execution_evidence`
  - `test_srs_does_not_assign_five_layer_hot_cold_or_side_balance_to_wes`
  - `test_srs_allows_execution_snapshots_without_inventory_mastership`
  - `test_adr_and_spec_keep_wms_as_transport_and_inventory_authority`
  - `test_spec_keeps_single_layer_snapshot_scope_bounded`

  Assertions:

  - SRS 必须包含“WES 不直连 RCS”或等价表达。
  - SRS 必须包含“AGV/CTU/RCS 任务由 WMS 转发”或等价表达。
  - SRS 必须包含“单层货架 active 执行快照”。
  - SRS 必须包含“空架资源主账”和“物理占用仍由 WMS/RCS 持有”或等价表达。
  - SRS 必须包含“退料执行投影或证据视图”。
  - SRS 中生产货架/退货货架的 PKG、Rack_ID、Side、Slot_ID 只能作为执行投影或证据；不得把真实储位归属、库存可用性或 A/B 面资源授权描述为 WES 主账。
  - SRS 不得以“WES 监控装箱区是否有可用空单层货架”作为独立权威表述；若出现，必须同段声明这是基于 active 执行快照、WMS 授权或 WMS/RCS 回调证据。
  - SRS 不得把 `Return_Rack_Inventory` 作为 WES 退料库存主账。
  - SRS 不得声明分拣机存在 `NG_ARM`。
  - SRS 不得声明 WES 动态平衡五层货架 A/B 面负载、冷热区、空箱授权或 CTU 路径；这些权威必须归属 WMS/RCS。
  - SRS 不得用“WES 不持有数据”覆盖执行事实例外；必须明确 WES 不持有库存主账，但允许持有执行事实、单层 active snapshot、运行投影、回调和对账证据。
  - ADR 与 SPEC 必须保持 WMS 是库存、运输设备转发和非单层资源权威。
  - ADR 与 SPEC 不得声明 WES 直连 RCS/AGV/CTU，或把 WES 扩展成全局 Location/库存主账。
  - SPEC 必须保持单层货架 active snapshot 只作为执行快照，不扩展成全局货架管理。

- [x] **Step 2: 运行文档守护测试并确认失败点**

  Run:

  ```bash
  uv run pytest tests/docs/test_wes_resource_boundary_docs.py -q
  ```

  Expected: 新增测试在未补齐文档时应指出具体冲突；若当前 SRS 已全部满足，则直接 PASS。

- [x] **Step 3: 最小修正文档冲突**

  Edit only the conflicting paragraphs in:

  - `docs/architecture/SRS.md`
  - `docs/architecture/adr/2026-05-13-wes-wms-rcs-resource-boundary.md`
  - `docs/superpowers/specs/2026-06-06-wes-single-layer-rack-orchestration-boundary-spec.md`

  Required wording decisions:

  - WES 生成运输/交换/旋转业务需求并提交 WMS。
  - WMS 管理或转发 RCS/AGV/CTU 任务。
  - 五层货架冷热区、A/B 面负载、空箱授权和 CTU 路径由 WMS/RCS 权威判断；WES 只生成建议/需求并保存授权结果、回调和对账证据。
  - WES 不维护五层货架、生产货架、退料货架、转运货架库存主账。
  - 生产货架/退货货架的料盘、储位和 A/B 面信息在 WES 中只能是执行证据或投影；真实储位归属、库存可用性和资源授权由 WMS 判断。
  - WES 不持有库存主账或库存变动主账，但可以持有执行事实、单层 active snapshot、运行投影、回调和对账证据。
  - `WORKLINE_START_REQUESTED` 不表示货架到位或作业开始。

- [x] **Step 4: 验证文档守护通过**

  Run:

  ```bash
  uv run pytest tests/docs/test_wes_resource_boundary_docs.py -q
  git diff --check -- docs/architecture/SRS.md docs/architecture/adr/2026-05-13-wes-wms-rcs-resource-boundary.md docs/superpowers/specs/2026-06-06-wes-single-layer-rack-orchestration-boundary-spec.md tests/docs/test_wes_resource_boundary_docs.py
  ```

  Expected: pytest PASS；diff check 无 trailing whitespace。

## Task 2: WorkLine START 与分拣机设备角色回归

**Files:**

- Modify: `tests/workline_runtime/test_reserved_runtime_events.py`
- Modify: `tests/workline_runtime/test_smt_sorting_inbound_plugin.py`
- Modify: `tests/test_workline_service_plugin_validation.py`
- Modify if failing: `src/workline_runtime/runtime_events.py`
- Modify if failing: `src/workline_plugins/smt_sorting_inbound/constants.py`
- Modify if failing: `src/workline_plugins/smt_sorting_inbound/plugin.py`

- [x] **Step 1: 补强平台保留事件测试**

  Test names:

  - `test_workline_start_requested_is_platform_control_only`
  - `test_workline_runtime_status_does_not_define_line_idle_state`

  Assertions:

  - `WORKLINE_START_REQUESTED` 在 `PLATFORM_CONTROL_EVENTS` 中。
  - `WORKLINE_START_REQUESTED` 不允许通过 runtime event mapping 映射为插件普通事件。
  - `WorkLineRuntimeStatus` 成员只包含当前运行态集合，不新增 WorkLine 级空闲态。

- [x] **Step 2: 补强分拣机 NG 角色测试**

  Test names:

  - `test_smt_sorter_has_only_source_and_target_arm_roles_for_business_commands`
  - `test_ng_place_uses_target_arm_role`

  Assertions:

  - `COMMAND_NG_PLACE` 的 target role 是 `ROLE_SORTING_TARGET_ARM`。
  - manifest 的 command target roles 不包含 `NG_ARM`。
  - 设备 role requirement 中不存在 `NG_ARM`。

- [x] **Step 3: 运行回归测试**

  Run:

  ```bash
  uv run pytest tests/workline_runtime/test_reserved_runtime_events.py tests/workline_runtime/test_smt_sorting_inbound_plugin.py tests/test_workline_service_plugin_validation.py -q
  ```

  Expected: PASS。若失败，只修正常量、manifest 或 event mapping，不改动业务边界。

## Task 3: Station Lease 最小服务

**Files:**

- Create: `src/app/workline/services/station_lease_service.py`
- Modify: `src/app/workline/services/__init__.py`
- Modify: `src/app/workline/repositories/session_repository.py`
- Modify: `src/app/sys/repositories/outbox_repository.py`
- Test: `tests/workline_runtime/test_station_lease_service.py`
- Read: `src/app/workline/services/rack_position_service.py`
- Read: `src/app/workline/models/rack_position.py`
- Read: `src/app/workline/models/session.py`
- Read: `src/app/sys/models/outbox.py`

- [x] **Step 1: 写 Station lease 失败测试**

  Test names:

  - `test_station_lease_available_when_no_wes_binding_exists`
  - `test_station_lease_busy_when_active_rack_placement_exists`
  - `test_station_lease_busy_when_active_outbox_targets_position`
  - `test_station_lease_ignores_terminal_outbox_for_position`
  - `test_station_lease_busy_when_open_session_binds_position`
  - `test_station_lease_busy_when_open_session_waits_dispatch_to_position`
  - `test_station_lease_rejects_non_single_layer_position`
  - `test_station_lease_does_not_check_external_location_occupancy`

  Required fixtures:

  - fake rack position repo returns enabled `SMT_SORTER_STATION`, `RackKind.SINGLE_LAYER`, `capacity=1`。
  - fake rack placement repo returns either empty list or one active placement。
  - fake outbox repo returns either no active outbox, one active `EXTERNAL_HTTP` outbox, one `SENT` but unfinished outbox, or one finished outbox。
  - fake session repo returns open sessions with `context_json.station.position_code` or `context_json.rack_operation.target_position_code`。

  Assertions:

  - 空闲时 `available is True`，`reason_code is None`。
  - active placement 时 `available is False`，`reason_code == "ACTIVE_RACK_BOUND"`。
  - active outbox 状态为 `NEW`、`DISPATCHING`、`SENT` 或 `BLOCKED_RESOURCE` 且 `finished_at is None` 时 `reason_code == "ACTIVE_DISPATCH_LEASE"`。
  - outbox 只有存在 `finished_at`，或状态为 `FAILED` / `CANCELLED` 且不再等待 WMS 回调时，才不占用 Station。
  - open session 绑定 Station 时 `reason_code == "ACTIVE_SESSION_BOUND"`。
  - open dispatch lease 指向 Station 时 `reason_code == "ACTIVE_DISPATCH_LEASE"`。
  - 非 `SINGLE_LAYER` position 抛出 `ValueError` 或返回明确拒绝状态。
  - `logic_location_code` 与 `external_location_code` 不触发物理占用判断。
  - 两个不同 `business_demand_key` 的并发业务需求命中同一 WorkLine + Station 时，只有一个能成功 claim station dispatch lease，另一个必须返回 busy/block，不得生成第二个 WMS 载入请求。
  - 旧 dispatch 已 terminal 且 `finished_at` 存在后，同一 WorkLine + Station 的新业务需求可以再次 claim；全局唯一 `dispatch_key` 不得把 station scope 永久占住。

- [x] **Step 2: 运行 Station lease 测试确认失败**

  Run:

  ```bash
  uv run pytest tests/workline_runtime/test_station_lease_service.py -q
  ```

  Expected: FAIL because `station_lease_service.py` does not exist or methods are missing.

- [x] **Step 3: 实现最小 Station lease 服务**

  Implementation requirements:

  - 新增只读 result 类型，字段包含 `workline_code`、`position_code`、`available`、`reason_code`、`active_rack_code`、`active_session_id`、`active_dispatch_key`。
  - Station lease result 是准入观察结果，不是并发互斥本身；真正创建 WMS dispatch 时必须在同一事务内执行 station lease claim。
  - 服务入口命名为 `get_station_lease_status(...)`。
  - 先通过 `WorklineRackPositionService.require_enabled_position(...)` 校验 position 配置和 `RackKind.SINGLE_LAYER`。
  - 通过 `RackPlacementRepository.list_active_by_workline_position(...)` 判断 WES active rack binding。
  - 通过 `SystemOutboxRepository` 查询 active external outbox lease，查询条件必须限定 `workline_id`、active status、`finished_at is None` 和 station/position 业务字段；`SENT` 未完成仍是 active dispatch lease。
  - 通过 `WorklineSessionRepository.list_open_by_workline_id(...)` 或等价新增方法判断 open session，查询必须限定同一 `workline_id`、open statuses、稳定排序和合理 limit；不得全量扫描历史 session JSON。
  - 只解析 session context 中的 WES 业务字段：`station.position_code`、`position_code`、`active_bin_rack.position_code`、`rack_operation.target_position_code`、`rack_operation.work_position_code`、`waiting_rack_operation_key`。
  - 查询顺序：先查启用 position、active placement、active outbox，再扫描同 WorkLine open sessions；不得扫描历史 session 或全局 outbox。
  - 不查询 location 表，不判断物理占用，不调用 WMS/RCS。
  - 新增 `claim_station_dispatch_lease(...)` 或等价事务入口：锁定 station scope `(workline_id, position_code)`，在同一事务内重查 active placement、active outbox 和 open session 后，再创建业务级 `EXTERNAL_HTTP` outbox。
  - 推荐优先使用现有 `RackPosition` / station 配置行 `SELECT ... FOR UPDATE` 或数据库 advisory lock 锁定 `(workline_id, position_code)`；若执行阶段引入独立 lease 表，必须使用 partial unique constraint 只约束 active lease，不能阻断 terminal 后再次 claim。
  - `SystemOutbox.dispatch_key` 是全局唯一派发幂等键，只能表达具体业务 dispatch，不得单独作为 station scope lock。业务级 dispatch key 必须包含 `business_demand_key` / `operation_key` 等可追踪业务维度；station 互斥由上面的 station scope lock 保证。
  - 并发测试必须覆盖两个不同 `business_demand_key` 同时 claim 同一 WorkLine + Station 只成功一个，以及第一个 dispatch terminal 后新业务可再次 claim。

- [x] **Step 4: 导出服务**

  Modify `src/app/workline/services/__init__.py`:

  - export `StationLeaseService`
  - export `station_lease_service`

- [x] **Step 5: 验证 Station lease 测试通过**

  Run:

  ```bash
  uv run pytest tests/workline_runtime/test_station_lease_service.py tests/rack/test_rack_position_service.py tests/workline_runtime/test_session_repository.py -q
  ```

  Expected: PASS。

## Task 4: WMS 运输需求合同

**Files:**

- Modify: `src/app/wms_integration/services/transport_contract.py`
- Modify if needed: `src/app/wms_integration/services/__init__.py`
- Modify: `tests/workline_runtime/test_runtime_intent_contract.py`
- Modify: `tests/workline_runtime/test_runtime_intent_effects.py`
- Modify: `tests/wms_integration/test_transport_contract.py`
- Read: `src/workline_runtime/runtime_intent.py`
- Read: `src/workline_runtime/runtime_intent_effects.py`

- [x] **Step 1: 写 WMS contract 失败测试**

  Test names:

  - `test_transport_contract_builds_single_layer_rack_operation_with_wms_authority`
  - `test_transport_contract_preserves_stable_dispatch_key`
  - `test_transport_contract_rejects_direct_device_fields_recursively`
  - `test_transport_contract_deep_copies_payload`
  - `test_single_layer_rack_operation_intent_becomes_waiting_external`

  Assertions:

  - contract 输出的 `target_code` 是 WMS 逻辑目标，不是 RCS URL。
  - outbound contract 不使用 `source_system="WMS"` 伪装发送方；如需表达 WMS 权威，使用 `gateway_system` 或 `authority_system`。
  - payload 包含 `business_demand_key`、`workline_code`、`endpoint_code`、`rack_kind`、`rack_code` 或 `rack_snapshot_ref`。
  - payload 任意嵌套层级都不得包含 `rcs_url`、`rcs_path`、`agv_id`、`ctu_id`、`vehicle_id`、`physical_coordinate`。
  - 修改原始 payload 后，contract 内部 payload 不变。

- [x] **Step 2: 运行 contract 测试确认失败**

  Run:

  ```bash
  uv run pytest tests/wms_integration/test_transport_contract.py -q
  ```

  Expected: FAIL because single-layer rack operation builder 或 forbidden-field 递归校验尚未补齐。

- [x] **Step 3: 扩展既有 WMS transport contract**

  Implementation requirements:

  - 不创建 `src/workline_runtime/wms_transport_contract.py`；所有 WMS/RCS payload 规则继续归属 `src/app/wms_integration/services/transport_contract.py`。
  - 提供 single-layer rack operation builder，返回可由 rack operation gateway / `RuntimeIntent.rack_operation_request(...)` 使用的字段集合。
  - `RuntimeIntent.external_request(...)` 是通用外部请求入口；`RuntimeIntent.rack_operation_request(...)` 是 rack operation 领域包装，用于表达 WES 单层货架搬运、交换、旋转或补给需求。
  - rack operation 最终仍复用 `EXTERNAL_HTTP` outbox、Timeline、wait context、`WAITING_EXTERNAL` 和 WMS/RCS 回调恢复语义；wait token 使用 operation key，task dispatch key 用于回调恢复。
  - `dispatch_key` 由调用方显式传入或由稳定业务键组合生成；同一业务需求重复调用必须得到相同 key。
  - 不新增 outbound `source_system="WMS"` 断言；如果合同需要声明网关或资源权威，字段名必须表达 `target/gateway/authority` 语义，避免与发送方来源混淆。
  - `target_code` 使用既有 WMS 逻辑目标，例如 `WMS_RCS_RACK_OPERATION` 或新增的 WMS 逻辑 endpoint；不得使用 URL。
  - builder 递归拒绝直连运输设备字段：`rcs_url`、`rcs_path`、`agv_id`、`ctu_id`、`vehicle_id`、`physical_coordinate`。
  - builder 不判断 WMS 资源可用性，只表达 WES 业务需求。

- [x] **Step 4: 验证 RuntimeIntent effect 合同**

  Add tests to existing files:

  - `tests/workline_runtime/test_runtime_intent_contract.py::test_wms_transport_contract_builds_rack_operation_intent`
  - `tests/workline_runtime/test_runtime_intent_effects.py::test_single_layer_rack_operation_creates_waiting_external_outbox`

  Run:

  ```bash
  uv run pytest tests/wms_integration/test_transport_contract.py tests/workline_runtime/test_runtime_intent_contract.py tests/workline_runtime/test_runtime_intent_effects.py -q
  ```

  Expected: PASS；existing rack operation request and external request tests remain green.

## Task 5: 单层 active 快照权威边界

**Files:**

- Modify: `tests/resource/test_smt_active_rack_snapshot_service.py`
- Modify if failing: `src/app/resource/services/active_rack_snapshot_service.py`

- [x] **Step 1: 写快照边界测试**

  Test names:

  - `test_active_snapshot_uses_single_layer_station_position_from_context`
  - `test_active_snapshot_returns_none_for_multiple_active_station_bindings`
  - `test_active_snapshot_returns_none_when_position_has_no_single_layer_projection`
  - `test_non_single_layer_projection_does_not_become_active_bin_rack_authority`

  Assertions:

  - context 中明确 `position_code="SOURCE_STATION_A"` 时，service 使用该 station 查询 active placement。
  - 同一 WorkLine + position 返回多个 active placement 时返回 `None`。
  - 没有 rack/bin active relation 时返回 `None`。
  - 五层/退料/生产相关 evidence 字段不会被转换成 `active_bin_rack`。

- [x] **Step 2: 运行快照测试确认当前行为**

  Run:

  ```bash
  uv run pytest tests/resource/test_smt_active_rack_snapshot_service.py -q
  ```

  Expected: 新增边界用例若失败，失败原因应指向 position 选择、歧义 placement 或非单层 projection 处理。

- [x] **Step 3: 最小修正快照服务**

  Implementation requirements:

  - 优先使用 runtime context 中显式 station/position 逻辑名。
  - `DEFAULT_SMT_RACK_POSITION_CODE = "SINGLE_LAYER_A"` 只允许作为旧上下文诊断或失败测试输入，不作为新单层货架合同的隐式默认。
  - 对多个 active placement 继续返回 `None`，不得任选一个。
  - 非单层 evidence 只保留在 trace/resource evidence，不作为当前 active snapshot。

- [x] **Step 4: 验证快照和相关运行时回归**

  Run:

  ```bash
  uv run pytest tests/resource/test_smt_active_rack_snapshot_service.py tests/workline_runtime/test_plugin_context_runtime_facts.py tests/workline_runtime/test_trace_resource_view_builder.py -q
  ```

  Expected: PASS。

## Task 6: 业务需求驱动单层货架编排

**Files:**

- Create: `src/app/workline/services/single_layer_rack_orchestration_service.py`
- Modify: `src/app/workline/services/__init__.py`
- Test: `tests/workline_runtime/test_single_layer_rack_orchestration_service.py`
- Read: `src/app/workline/models/workline.py`
- Read: `src/app/workline/models/safety.py`
- Read: `src/app/workline/services/station_lease_service.py`
- Read: `src/app/wms_integration/services/transport_contract.py`
- Read: `src/workline_runtime/runtime_intent.py`

- [x] **Step 1: 写编排服务失败测试**

  Test names:

  - `test_orchestration_waits_when_workline_not_ready`
  - `test_orchestration_waits_when_station_lease_busy`
  - `test_orchestration_dispatches_wms_load_when_business_demand_and_station_available`
  - `test_orchestration_does_not_use_rack_ready_to_select_business`
  - `test_rough_sorter_release_records_fact_without_calling_sorter_plugin`

  Assertions:

  - WorkLine 状态不是 `READY` 时返回 wait decision，reason 为 `WORKLINE_NOT_READY`。
  - Station lease busy 时返回 wait decision，reason 使用 Station lease 的 reason。
  - 业务需求存在、WorkLine READY、Station 可用时，输出 rack operation decision，可转换为 `RuntimeIntent.rack_operation_request(...)`。
  - 输入只有“货架 ready 事实”但没有业务需求时，不生成分拣线 dispatch。
  - 粗分机释放事实不会直接 import 或调用 `smt_sorting_inbound` 插件。

- [x] **Step 2: 运行编排测试确认失败**

  Run:

  ```bash
  uv run pytest tests/workline_runtime/test_single_layer_rack_orchestration_service.py -q
  ```

  Expected: FAIL because orchestration service does not exist.

- [x] **Step 3: 实现最小编排服务**

  Implementation requirements:

  - 服务入口命名为 `plan_single_layer_rack_dispatch(...)`。
  - 输入包含 `business_demand_key`、`demand_type`、`workline`、`station_code`、`rack_snapshot_ref` 或 `rack_code`。
  - 先检查 `workline.runtime_status == WorkLineRuntimeStatus.READY`。
  - 先调用 `StationLeaseService.get_station_lease_status(...)` 作为可观测状态判断；一旦要返回 `DISPATCH_WMS`，必须通过 `claim_station_dispatch_lease(...)` 或等价事务入口完成 Station claim，不能只靠读结果后再创建 outbox。
  - Station claim 成功后才允许返回 rack operation request；claim 失败必须返回 `WAITING` 或 `BLOCKED`，reason 使用当前 active rack binding、active dispatch lease 或 open session 的原因。
  - `claim_station_dispatch_lease(...)` 必须与 WMS dispatch/outbox 创建共享同一事务语义，并锁定 station scope `(workline_id, position_code)`；业务级 dispatch key 只负责外部派发幂等，不能代替 station 互斥锁。
  - 并发两个不同 `business_demand_key` 命中同一 WorkLine + Station 时，只能一个返回 `DISPATCH_WMS`；旧 dispatch 已 terminal 后，新业务必须可以再次 claim。
  - 通过 `wms_integration` transport contract 生成 rack operation payload，并返回可交给 `RuntimeIntent.rack_operation_request(...)` 的 operation key、target code、payload 和 timeout。
  - 不在该服务内判断 WMS 库存、位置容量、RCS 路径。
  - 返回显式 decision：`WAITING`、`DISPATCH_WMS`、`BLOCKED`。
  - `DISPATCH_WMS` decision 必须携带 `rack_operation_request`；payload 包含可追踪的 `business_demand_key`、`dispatch_key`、`operation_key`、`workline_code`、`station_code`。

- [x] **Step 4: 导出服务并运行单元测试**

  Run:

  ```bash
  uv run pytest tests/workline_runtime/test_single_layer_rack_orchestration_service.py tests/workline_runtime/test_station_lease_service.py tests/wms_integration/test_transport_contract.py -q
  ```

  Expected: PASS。

## Task 7: 分拣链路集成回归

**Files:**

- Modify if failing: `src/workline_plugins/smt_sorting_inbound/flow_service.py`
- Modify if failing: `src/workline_plugins/smt_sorting_inbound/plugin.py`
- Modify: `tests/workline_runtime/test_smt_sorting_inbound_plugin.py`
- Modify: `tests/integration/workline_runtime/test_cross_plan_sandbox_smoke.py`
- Modify: `tests/workline_runtime/test_start_admission_service.py`

- [x] **Step 1: 补强 START 不是作业开始的集成断言**

  Test additions:

  - In `test_cross_plan_sandbox_smoke.py`，START admission 后只断言 WorkLine 进入 `READY`，不创建 rack dispatch，不创建分拣 command。
  - In `test_start_admission_service.py`，START 成功后只释放 WorkLine 级 parked outbox，不把 station 绑定为 busy。

- [x] **Step 2: 补强 NG 设备角色回归**

  Assertions:

  - NG scan path 生成的 command intent action 是 `COMMAND_NG_PLACE`。
  - NG scan path 生成的 command intent device role 是 `ROLE_SORTING_TARGET_ARM`。
  - 任何 manifest、device requirement、command target role 中都不存在 `NG_ARM`。

- [x] **Step 3: 运行分拣链路回归**

  Run:

  ```bash
  uv run pytest tests/workline_runtime/test_smt_sorting_inbound_plugin.py tests/test_workline_service_plugin_validation.py tests/workline_runtime/test_start_admission_service.py tests/integration/workline_runtime/test_cross_plan_sandbox_smoke.py -q
  ```

  Expected: PASS。若失败，修正 plugin/flow service 中与本 SPEC 冲突的触发或角色映射。

## Task 8: 插件级单层货架承接矩阵与准入门禁

**Files:**

- Create: `tests/workline_runtime/test_plugin_single_layer_rack_boundary.py`
- Modify if failing: `src/workline_runtime/plugin_manifest.py`
- Modify if failing: `tests/workline_runtime/test_plugin_manifest_and_topology.py`
- Modify if failing: `src/app/workline/models/workline.py`
- Modify if failing: `src/app/workline/services/workline_service.py`
- Modify if failing: `src/app/workline/v1/workline.py`
- Modify if failing: `tests/test_workline_service_plugin_validation.py`
- Modify if failing: `tests/test_workline_routes.py`
- Modify if failing: `src/workline_plugins/rough_sorter/plugin.py`
- Modify if failing: `src/workline_plugins/smt_sorting_inbound/plugin.py`
- Modify if failing: `src/workline_plugins/smt_sorting_inbound/flow_service.py`
- Read: `src/workline_plugin_registry.py`
- Read: `src/workline_plugins/rough_sorter/plugin.py`
- Read: `src/workline_plugins/smt_sorting_inbound/plugin.py`
- Read: `src/workline_plugins/smt_sorting_inbound/flow_service.py`

- [x] **Step 1: 写插件级准入失败测试**

  Test names:

  - `test_manifest_normalizes_single_layer_boundary_contracts`
  - `test_manifest_summary_exports_single_layer_boundaries`
  - `test_registered_single_layer_plugins_have_station_rack_boundary_contract`
  - `test_rough_sorter_single_layer_rack_boundary_targets_classifier_work_position`
  - `test_rough_sorter_rack_arrived_resumes_only_waiting_rough_sorter_session`
  - `test_sorting_inbound_start_ready_does_not_bind_station_or_create_commands`
  - `test_sorting_inbound_source_flow_requires_active_source_snapshot_and_station_lease`
  - `test_sorting_inbound_target_flow_requires_active_target_snapshot_and_station_lease`
  - `test_sorting_inbound_boundaries_support_multiple_source_stations`
  - `test_sorting_inbound_ng_place_uses_target_arm_and_ng_station_as_evidence`
  - `test_plugin_manifest_route_exports_single_layer_boundaries`
  - `test_plugin_manifest_openapi_schema_contains_single_layer_boundaries`

  Assertions:

  - `WorklinePluginManifest.single_layer_boundaries` 缺省为空 tuple；声明后归一化为稳定只读结构。
  - `single_layer_boundaries` 是可重复 boundary 集合；每项必须表达 `station_code` / `position_code`、`rack_kind`、承接角色、业务需求类型、WMS operation 类型、snapshot kind 和 lease scope。
  - 涉及单层货架的插件必须通过 manifest 轻量 marker 暴露识别信号，例如 `capabilities`、`resource_kinds`、`requires_single_layer_boundary` 或等价字段；准入门禁用 marker 或强制插件清单判定“必须声明 boundary”的插件集合。
  - `WorkLinePluginManifestSummary.single_layer_boundaries` 必须导出同一 boundary，供配置台和前端运行态视图消费；未知插件不得生成伪 boundary。
  - 枚举已注册插件，凡通过 manifest marker、强制清单或显式 boundary 表明涉及 `SINGLE_LAYER`、rack operation、active snapshot、station lease 的插件，必须通过 `WorklinePluginManifest.single_layer_boundaries` 声明 station/rack boundary；准入测试不得只靠源码字符串扫描推断业务语义。
  - 粗分机 active rack 缺失时，rack operation 目标必须是粗分机下料/出料工作位，不得直接触发分拣线。
  - 粗分机 WMS 到位回调只能创建当前等待 session 的 storage retry，不得选择其它 WorkLine 或插件。
  - SMT 分拣入库 START 后只允许 WorkLine READY；不得创建 source/target station lease、rack dispatch 或分拣 command。
  - SMT 分拣入库必须支持多个 source station boundary，例如 `SOURCE_STATION_A` / `SOURCE_STATION_B` 不得被压缩成单一 source 字段。
  - SMT 分拣入库 source station / 上料位缺少 active source snapshot 或 Station lease 不可用时必须 block，不得从 raw context 推断可执行源货架。
  - SMT 分拣入库 target allocation 缺少 active target snapshot 或 target station lease 不可用时必须 block，不得从 raw context 推断可用目标格。
  - `COMMAND_NG_PLACE` 的 command target role 必须是 `ROLE_SORTING_TARGET_ARM`；NG station 只能作为 evidence/station role，不是机械臂。
  - 公开 route `GET /plugins/{plugin_key:path}/manifest` 必须返回 `single_layer_boundaries`，未知插件不得返回伪 boundary。
  - OpenAPI schema 中 `WorkLinePluginManifestSummary` 必须包含 `single_layer_boundaries`，确保前端 generated types 可消费。

- [x] **Step 2: 运行插件准入测试确认失败或暴露缺口**

  Run:

  ```bash
  uv run pytest tests/workline_runtime/test_plugin_single_layer_rack_boundary.py -q
  ```

  Expected: 新测试若失败，失败原因必须指向缺失的 station/rack boundary 声明、旧默认 station 口径、或插件 flow 与本 SPEC 冲突。

- [x] **Step 3: 收紧插件合同或测试夹具**

  Implementation requirements:

  - 在 `WorklinePluginManifest` 增加轻量 `single_layer_boundaries` 字段，作为本计划唯一插件 station/rack boundary 合同；字段至少表达 `station_code` / `position_code`、`rack_kind`、承接角色、业务需求类型、WMS operation 类型、snapshot kind 和 lease scope。
  - 在 manifest 中增加轻量 capability/resource marker，或维护强制插件清单，用来显式标记“该插件涉及单层货架边界，必须声明 `single_layer_boundaries`”；推荐字段为 `capabilities`、`resource_kinds`、`requires_single_layer_boundary` 或与现有 manifest 风格一致的等价名称。
  - `single_layer_boundaries` 必须支持重复声明；SMT 分拣入库分别声明 `SOURCE_STATION_A` / `SOURCE_STATION_B` 等 source station boundary 与 `TARGET_STATION` target boundary。
  - 必须同步 `WorkLinePluginManifestSummary` 与 `get_plugin_manifest_summary(...)`，避免后端运行时合同和前端/配置台可见合同分叉。
  - 公开 manifest summary route 与 OpenAPI schema 都必须暴露同一结构化字段；前端不得依赖 raw plugin 代码或 raw JSON 推断。
  - 现有不涉及单层货架的插件可保持未声明；涉及单层货架但缺少显式 boundary 的插件必须让准入测试失败。
  - 粗分机不继续依赖隐藏默认 `SINGLE_LAYER_A`；新合同必须显式覆盖 `work_position_code` / `target_position_role`，旧默认只作为迁移前失败检测或诊断输入。
  - SMT 分拣入库必须分别声明 source station / 上料位与 target station 的 boundary、active snapshot 来源和 Station lease 约束；target station lease 不可用时同样必须阻断 target allocation。
  - SMT 分拣入库必须继续保持 `COMMAND_NG_PLACE -> ROLE_SORTING_TARGET_ARM`，并把 `ROLE_SORTING_NG_STATION` 定位为 evidence/station role。
  - 未来插件准入门禁不得要求所有插件声明 station/rack；只约束涉及单层货架的插件。

- [x] **Step 4: 运行插件级回归组合**

  Run:

  ```bash
  uv run pytest tests/workline_runtime/test_plugin_single_layer_rack_boundary.py tests/workline_runtime/test_plugin_manifest_and_topology.py tests/test_workline_service_plugin_validation.py tests/test_workline_routes.py tests/workline_plugins/test_rough_sorter_plugin.py tests/workline_runtime/test_smt_sorting_inbound_plugin.py -q
  ```

  Expected: PASS；若失败，只修正与插件 station/rack boundary 直接相关的合同或测试，不扩大到库存、WMS 授权或前端展示。

## Task 9: 出库、退料、转运边界回归

**Files:**

- Modify: `tests/docs/test_wes_resource_boundary_docs.py`
- Modify if failing: `docs/architecture/SRS.md`
- Read: `docs/superpowers/specs/2026-06-06-wes-single-layer-rack-orchestration-boundary-spec.md`

- [x] **Step 1: 补充业务链路文档断言**

  Test names:

  - `test_outbound_is_driven_by_order_wave_or_line_demand`
  - `test_return_flow_keeps_inventory_confirmation_in_wms`
  - `test_transfer_supply_and_empty_rack_return_are_wms_transport_demands`

  Assertions:

  - 出库/生产发料章节包含工单、波次或产线需求驱动。
  - 出库异常不得由 WES 自动扣减库存。
  - 退料章节包含 LCR、X-Ray、贴标执行证据。
  - 退料库存确认、库存调整和 SAP 同步由 WMS 完成。
  - 转运、补给、空架回流统一提交 WMS。

- [x] **Step 2: 运行文档业务链路测试**

  Run:

  ```bash
  uv run pytest tests/docs/test_wes_resource_boundary_docs.py -q
  ```

  Expected: PASS。若失败，只修正文档权责表述。

## Task 10: 全量验证与变更审计

**Files:**

- Inspect: all changed files
- Inspect: GitNexus detect changes

- [x] **Step 1: 运行计划相关测试集**

  Run:

  ```bash
  uv run pytest tests/docs/test_wes_resource_boundary_docs.py tests/rack/test_rack_position_service.py tests/workline_runtime/test_station_lease_service.py tests/wms_integration/test_transport_contract.py tests/workline_runtime/test_single_layer_rack_orchestration_service.py tests/workline_runtime/test_runtime_intent_contract.py tests/workline_runtime/test_runtime_intent_effects.py tests/resource/test_smt_active_rack_snapshot_service.py tests/workline_runtime/test_plugin_single_layer_rack_boundary.py tests/workline_runtime/test_plugin_manifest_and_topology.py tests/workline_plugins/test_rough_sorter_plugin.py tests/workline_runtime/test_smt_sorting_inbound_plugin.py tests/test_workline_service_plugin_validation.py tests/test_workline_routes.py tests/api/test_workline_runtime_api.py tests/workline_runtime/test_runtime_query_service.py tests/workline_runtime/test_reserved_runtime_events.py tests/workline_runtime/test_start_admission_service.py tests/integration/workline_runtime/test_cross_plan_sandbox_smoke.py
  ```

  Expected: PASS。

- [x] **Step 2: 运行格式与静态检查**

  Run:

  ```bash
  uv run ruff format .
  uv run ruff check .
  ```

  Expected: formatter applied；ruff check PASS。

- [x] **Step 3: 运行架构违规扫描**

  Run:

  ```bash
  grep -r "from sqlalchemy import select" src/app/*/v1/ || true
  grep -r "db.execute(" src/app/*/v1/ || true
  ```

  Expected: 本计划不应新增 API 层直接 DB 访问。

- [x] **Step 4: 运行 GitNexus detect changes**

  Use GitNexus MCP:

  ```text
  gitnexus_detect_changes()
  ```

  Expected: changed symbols only cover Station lease、single-layer orchestration、WMS transport contract、resource snapshot tests/docs guards、plugin single-layer boundary tests、runtime detail structured fields，以及必要的 plugin manifest / plugin manifest summary / manifest route / rough sorter / SMT sorting inbound 合同收紧。若出现无关模块，复核 diff。

- [x] **Step 5: 检查文档和空白**

  Run:

  ```bash
  git diff --check
  git status --short
  ```

  Expected: diff check PASS；status 只包含本计划相关文件和用户既有文档变更。

## 验收标准

- 文档层面可以清楚说明：WES 不直连 RCS/AGV/CTU，运输任务由 WMS 转发。
- 文档层面可以清楚说明：WES 只权威维护单层货架 active 执行快照。
- 代码层面存在 Station lease 服务，且只判断 WES 业务绑定，不判断物理位置真实占用。
- 代码层面在 `wms_integration` 既有 WMS transport contract 中扩展单层货架 operation builder，且递归拒绝直连运输设备字段。
- WMS outbound contract 使用 WMS 逻辑 `target_code` 或显式 gateway/authority 字段表达 WMS 权威，不用 `source_system="WMS"` 混淆请求发送方。
- 代码层面存在单层货架业务需求编排服务，且由业务需求驱动，不由货架 ready 事实主动选择业务；该服务输出 rack operation 语义，不绕过 runtime wait/context 链路。
- `WORKLINE_START_REQUESTED` 只驱动 WorkLine START 准入到 `READY`。
- 分拣机 NG 放置继续由 `TARGET_ARM` 执行，不出现 `NG_ARM`。
- 粗分机、SMT 分拣入库和未来涉及单层货架的插件都有插件级 station/rack boundary 准入测试。
- 未来涉及单层货架的插件必须通过 manifest marker 或强制清单进入准入门禁，再通过 `single_layer_boundaries` 声明具体 station/rack boundary；不得仅靠源码字符串扫描识别插件是否应该声明。
- `single_layer_boundaries` 是可重复 boundary 集合，支持 SMT 分拣入库多个 source station 与 target station 分别声明，不得压缩为单一 source 字段。
- `WorklinePluginManifest.single_layer_boundaries` 必须同步暴露到 `WorkLinePluginManifestSummary`，前端/配置台不得只能通过 raw plugin 代码推断单层货架边界。
- 公开 manifest route 与 OpenAPI schema 必须暴露 `single_layer_boundaries`，前端 generated types 能消费同一结构化合同。
- Runtime detail route 与 OpenAPI schema 必须暴露前端所需 snake_case 结构化字段：`workline_readiness`、`station_lease`、`single_layer_rack_snapshot`、`rack_operation_wait`、`resource_evidence_kind`；generated types 保持后端 snake_case 合同，前端 scene adapter 统一转换为 `worklineReadiness`、`stationLease`、`singleLayerRackSnapshot`、`rackOperationWait`、`resourceEvidenceKind`；前端不得解析 raw JSON 推断这些语义。
- Station lease 中 `ACTIVE_RACK_BOUND`、`ACTIVE_DISPATCH_LEASE`、`ACTIVE_SESSION_BOUND` 都必须被 runtime detail、OpenAPI 枚举、前端 adapter 测试覆盖。
- 创建 WMS rack operation dispatch 必须通过 Station claim 事务入口；并发两个相同 WorkLine + Station 需求时只能一个 claim 成功，失败者返回 busy/block，不得重复派发。
- 旧 dispatch 已 terminal 且不再等待 WMS 回调后，同一 WorkLine + Station 的新业务需求必须可以再次 claim；全局唯一 `dispatch_key` 不得把 station scope 永久占住。
- `SystemOutbox` 状态为 `SENT` 且 `finished_at is None` 时仍是 active dispatch lease，必须继续占用 Station；只有 `finished_at` 存在，或 `FAILED` / `CANCELLED` 且不再等待 WMS 回调时才不占用。
- ADR、SPEC、SRS 三类文档守护必须防止 WES 直连 RCS/AGV/CTU、WES 管库存主账、WES 管空架资源主账或物理占用等口径回退。
- SRS 文档守护必须防止“WES 管空架资源主账或物理占用”口径回退。
- SRS 文档守护必须防止五层货架冷热区、A/B 面负载、空箱授权、CTU 路径回退为 WES 权威，并允许 WES 持有执行事实、单层 active snapshot、运行投影、回调和对账证据。
- 粗分机下料/出料工作位承接单层货架时，只提交 WMS rack operation 或恢复当前粗分机 session，不直接触发分拣插件。
- SMT 分拣入库 source station / 上料位与 target station 都只消费对应 active snapshot 和 Station lease；source 或 target 任一 lease 不可用时均必须阻断对应业务动作，不从 START 或 raw context 推断可执行货架。
- 出库、退料、转运相关文档边界不把 WES 变成库存主账或资源主账。
- 计划相关 pytest、ruff、diff check、GitNexus detect changes 均通过或有明确记录的外部基线失败。

## 执行后建议提交拆分

建议按以下批次提交，Commit Comment 使用中文：

1. `test(docs): 守护 WES 与 WMS 资源边界`
2. `feat(workline): 增加 Station lease 最小合同`
3. `feat(wms-integration): 扩展 WMS 单层货架运输合同`
4. `feat(workline): 增加单层货架业务需求编排`
5. `test(workline): 增加插件级单层货架承接准入`
6. `test(workline): 补强分拣 START 与 NG 角色回归`

若某批次只产生测试或文档变更，使用对应 `test(...)` 或 `docs(...)` scope。

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | — | — |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 9 | PLAN_UPDATED | 45 issues incorporated, 0 critical gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | backend-only |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

- **UNRESOLVED:** 0
- **VERDICT:** ENG REVIEW FIXES INCORPORATED — ready for implementation with corrected WMS outbound, manifest-only plugin boundary plus capability marker, station-scope transactional dispatch claim, public API/OpenAPI, exact runtime structured fields, target Station lease coverage, SRS/ADR/SPEC document guard, frontend executable monitor QA contract, and full-repo ruff verification below.

### Eng Review Decisions

- D1: Scope accepted as-is. Continue the larger plan instead of reducing to a smaller first version.
- D2/D5: Do not create `src/workline_runtime/wms_transport_contract.py`; extend `src/app/wms_integration/services/transport_contract.py` and move tests/ruff commands to `tests/wms_integration/test_transport_contract.py`.
- D3/D8: Station lease must check both open session context and active `SystemOutbox`, bounded by `workline_id` and active statuses. Query active placement/outbox first, then scan only open sessions.
- D4: `single_layer_rack_orchestration_service` must output rack-operation semantics compatible with `RuntimeIntent.rack_operation_request`, not a naked WMS external request.
- D6: Station lease tests must distinguish active outbox statuses (`NEW`, `DISPATCHING`, `BLOCKED_RESOURCE`) from terminal/history statuses.
- D7: WMS transport contract tests must recursively reject direct-device fields such as `rcs_url`, `rcs_path`, `agv_id`, `ctu_id`, `vehicle_id`, and `physical_coordinate`.
- D9: Existing and future single-layer rack plugins must pass a plugin-level station/rack boundary matrix; rough sorter and SMT sorting inbound are the first mandatory coverage targets.
- D10: WMS outbound transport requests must not assert `source_system="WMS"`; WMS authority is expressed through logical `target_code` or explicit gateway/authority fields.
- D11: Future single-layer plugin admission must use `WorklinePluginManifest.single_layer_boundaries`, not string scanning or a parallel plugin contract object as the source of truth.
- D12: Rough sorter new contracts must explicitly provide `work_position_code` / `target_position_role`; hidden `SINGLE_LAYER_A` defaults are only migration diagnostics or failing-test fixtures.
- D13: GitNexus detect changes expectations include plugin boundary tests and necessary plugin manifest / rough sorter / SMT sorting inbound contract changes.
- D14: SRS guard wording must distinguish WES execution-view monitoring from WES owning global empty-rack inventory or physical occupancy.
- D15: Plugin boundary work must include `WorklinePluginManifest` impact analysis and manifest/topology tests, because the new boundary field changes the shared plugin contract.
- D16: If boundary lives in manifest, `WorkLinePluginManifestSummary` must expose it so backend runtime, configuration UI, and frontend runtime views do not fork the contract.
- D17: SRS doc guards must lock empty single-layer rack authority to WMS/RCS and prevent wording drift back to WES owning empty-rack inventory or physical occupancy.
- D18: Public plugin manifest route and OpenAPI schema must expose `single_layer_boundaries`; service-only tests are insufficient for the frontend generated-types contract.
- D19: Document guards must cover SRS, ADR, and SPEC consistently; a SRS-only guard does not protect the project-level boundary from later ADR/SPEC drift.
- D20: SMT sorting inbound source station / 上料位 needs the same active snapshot and Station lease admission coverage as target station.
- D21: Existing `tests/test_workline_routes.py` must remain in the regression set when manifest summary or public route contract changes.
- D22: SMT sorting inbound target station lease must be tested and enforced with the same admission semantics as source station lease; active target snapshot alone is insufficient.
- D23: `single_layer_boundaries` is fixed on `WorklinePluginManifest`; this plan no longer keeps a second "dedicated contract object" implementation branch.
- D24: `single_layer_boundaries` must be a repeatable boundary collection so SMT sorting inbound can declare multiple source stations and one target station independently.
- D25: Runtime detail response and OpenAPI must expose exact snake_case structured fields for `workline_readiness`, `station_lease`, `single_layer_rack_snapshot`, `rack_operation_wait`, and `resource_evidence_kind`; frontend scene adapter converts generated snake_case types to camelCase scene fields because the frontend plan forbids raw JSON inference.
- D26: Ruff verification must run against the full repository so "Modify if failing" files are covered even when the final touched-file set differs from the initial plan.
- D27: SRS must not assign five-layer rack hot/cold zones, A/B side balance, empty-bin authorization, or CTU path decisions to WES; those remain WMS/RCS authority.
- D28: Task 0 GitNexus impact analysis is a per-symbol gate for every existing function/class/method touched, and the seed list must include repository, service, route, manifest summary, and runtime detail symbols.
- D29: Runtime detail contract must use exact snake_case fields and exact enum sets in OpenAPI/generated types; "equivalent fields" are not acceptable, and camelCase belongs to the frontend scene adapter unless a separately tested backend alias path is chosen.
- D30: ADR/SPEC must state that `external_request` is the generic external request path and `rack_operation_request` is a rack-domain wrapper that still uses external outbox, timeline, wait context, and callback recovery.
- D31: SRS "WES does not hold data" must be narrowed to inventory mastership; WES may persist execution facts, active snapshots, runtime projections, callbacks, and reconciliation evidence.
- D32: The plan must keep one executable Task checklist. Review-report action items are only finding-to-task mappings, not a second implementation queue.
- D33: The frontend plan must define reproducible visual QA commands, viewport sizes, and overflow/overlap assertions for lease/rack operation/resource evidence states.
- D34: `DISPATCH_WMS` orchestration必须使用事务型 Station dispatch claim，不能只读 lease status 后再创建 outbox。
- D35: `SENT` outbox 且 `finished_at is None` 时仍是 active Station dispatch lease，直到 WMS 回调完成、取消或明确 terminal 处理。
- D36: `station_lease` enum 必须在 PLAN、SPEC、OpenAPI、generated types 和前端 scene adapter 覆盖 `ACTIVE_RACK_BOUND`。
- D37: OpenAPI/generated types 默认保持 snake_case；前端 adapter 转 camelCase scene model，除非后端 alias 路径有 response/OpenAPI/generated type 全链路测试。
- D38: 前端浏览器 QA 必须使用项目可执行命令；如使用 Playwright，必须先把依赖、config 和 spec 纳入计划。
- D39: 前端 raw JSON 搜索范围必须限定到 monitor scene adapter/components 或 allowlist，避免 sandbox/trace evidence 视图误报。
- D40: 未来单层货架插件识别需要显式 manifest capability/resource marker 或强制插件清单，源码字符串扫描不能作为准入信号。
- D41: SRS 任务编排 wording 必须保持 WMS/RCS 是运输拥堵、区域容量、路径规划和避让权威。
- D42: Station claim 必须锁定 station scope `(workline_id, position_code)`；`SystemOutbox.dispatch_key` 是业务 dispatch 幂等键，不能单独作为 station scope lock。
- D43: Station claim 测试必须覆盖两个不同 `business_demand_key` 并发只成功一个，以及 terminal dispatch 后同一 Station 可再次 claim。
- D44: 前端 `pnpm smoke:runtime:agent-browser` 只有覆盖 `/runtime/monitor`、desktop/mobile viewport 和 lease/rack operation/evidence overflow 断言后，才能作为本计划视觉 QA 通过证据。
- D45: SRS 生产货架/退货货架 PKG、Rack_ID、Side、Slot_ID 只能作为 WES 执行投影或证据，真实储位归属、库存可用性和 A/B 面资源授权由 WMS 权威判断。

### Coverage Diagram

2026-06-08 功能性验收已重新核对实现、GitNexus 图谱和目标测试。此前本节仍保留计划阶段缺口标记，是文档状态未随实现同步，不代表当前后端实现缺口。

```text
CODE PATHS                                                USER / SYSTEM FLOWS
[TESTED] docs boundary guard                              [TESTED] Plan reader / future agent
  ├── SRS WES-not-direct-RCS assertions                     ├── detects WES/RCS wording drift
  ├── ADR/SPEC WMS authority assertions                      ├── detects cross-doc authority drift
  ├── return rack inventory wording assertions               ├── detects START-as-job-start drift
  ├── empty rack authority wording assertions                 ├── detects WES-empty-rack-authority drift
  ├── five-layer hot/cold and A/B authority assertions        └── detects SRS old five-layer authority drift
  └── execution snapshot vs inventory master wording assertions
[TESTED] StationLeaseService                              [TESTED] Dispatch admission
  ├── active RackPlacement -> ACTIVE_RACK_BOUND               ├── Station busy prevents duplicate WMS request
  ├── active SystemOutbox -> ACTIVE_DISPATCH_LEASE            ├── station-scope transactional claim prevents duplicate WMS request
  ├── SENT unfinished outbox remains active lease             ├── distinct business demands cannot both claim one station
  ├── terminal dispatch allows later station re-claim         └── historical failed request does not block forever
  ├── terminal outbox ignored
  ├── open session context -> ACTIVE_SESSION_BOUND
  └── non-single-layer position rejected
[TESTED] WMS transport contract in wms_integration
  ├── existing rack/handling envelope behavior
  ├── target_code/gateway/authority WMS boundary
  ├── outbound request does not misuse source_system=WMS
  └── recursive forbidden direct-device fields
[TESTED] SingleLayerRackOrchestrationService
  ├── WorkLine not READY -> WAITING
  ├── Station lease busy -> WAITING/BLOCKED
  ├── business demand -> rack operation decision
  ├── DISPATCH_WMS requires successful station claim
  └── rack-ready fact alone does not dispatch
[TESTED] Plugin single-layer boundary matrix
  ├── manifest capability/resource marker identifies mandatory boundary plugins
  ├── WorklinePluginManifest boundary contract
  ├── WorkLinePluginManifestSummary exports boundary
  ├── manifest route + OpenAPI exports boundary
  ├── rough sorter work position -> WMS rack operation
  ├── rough sorter WMS arrived -> same waiting session only
  ├── sorting inbound START -> READY only, no station bind
  ├── sorting inbound source station requires active source snapshot + lease
  ├── sorting inbound target allocation requires active target snapshot + lease
  ├── sorting inbound multiple source stations remain separate boundaries
  └── future single-layer plugins declare manifest station/rack boundary
[TESTED] Runtime detail backend contract
  ├── runtime detail exports workline_readiness
  ├── runtime detail exports station_lease
  ├── runtime detail exports single_layer_rack_snapshot
  ├── runtime detail exports rack_operation_wait
  ├── runtime detail exports resource_evidence_kind
  └── runtime detail OpenAPI exports exact snake_case enums
[TESTED] Runtime rack operation effects
  ├── rack operation creates tasks + wait context
  ├── new orchestration path uses same effect contract
  └── ADR/SPEC document external_request vs rack_operation_request relation
[CROSS-REPO PENDING] Frontend generated types / scene adapter / visual QA
  ├── generated runtime detail types consume the new snake_case fields
  ├── scene adapter maps fields to camelCase monitor model
  ├── executable browser smoke covers runtime monitor states
  └── desktop/mobile /runtime/monitor checks lease/rack operation/resource evidence overflow
[TESTED] SRS transport authority guard
  └── WES task concurrency wording does not claim AGV congestion authority
[TESTED] Production/return rack authority guard
  └── PKG/Rack/Side/Slot wording remains execution evidence, not WES slot mastership

COVERAGE: backend contract, lease/orchestration/plugin-boundary/API/document-guard paths pass targeted acceptance. Cross-repo frontend generated types, scene adapter and visual QA remain pending in the `wes_frontend` plan.
QUALITY: backend acceptance command `uv run pytest tests/docs/test_wes_resource_boundary_docs.py tests/workline_runtime/test_station_lease_service.py tests/workline_runtime/test_single_layer_rack_orchestration_service.py tests/wms_integration/test_transport_contract.py tests/workline_runtime/test_plugin_single_layer_rack_boundary.py tests/api/test_workline_runtime_api.py tests/workline_runtime/test_runtime_intent_contract.py tests/workline_runtime/test_runtime_intent_effects.py tests/workline_runtime/test_smt_sorting_inbound_plugin.py tests/workline_runtime/test_reserved_runtime_events.py tests/scripts/test_sync_test_workline_devices.py` passed with 281 passed, 15 warnings. GitNexus refreshed with `npx gitnexus analyze` on 2026-06-08.
```

### NOT In Scope

- No direct RCS/AGV/CTU client or driver plugin. This remains a WMS-forwarded transport boundary.
- No global location occupancy engine or inventory master in WES.
- No migration for Station lease indexed fields in this PR; use bounded existing queries first.
- No frontend/UI changes in this backend plan. Backend must still provide the runtime structured fields and manifest/OpenAPI contracts consumed by `wes_frontend/docs/superpowers/specs/2026-06-06-single-layer-rack-boundary-frontend-design.md` and `wes_frontend/docs/superpowers/plans/2026-06-06-single-layer-rack-boundary-frontend-plan.md`.

### What Already Exists

- `src/app/wms_integration/services/transport_contract.py` already centralizes WMS/RCS transport payload construction and must be reused.
- `RuntimeIntent.rack_operation_request` and `RuntimeIntentEffectApplier` already create rack operation tasks, wait context, timeline, and external outbox.
- `RackPlacementRepository.list_active_by_workline_position` already provides active rack binding lookup.
- `WorklineSessionRepository.get_open_session_by_waiting_rack_operation_key` already demonstrates open-session JSON wait-key lookup.

### Failure Modes

- Duplicate Station dispatch: covered only if Station lease checks active outbox and open sessions; add tests from D6.
- Concurrent duplicate WMS dispatch: covered only if orchestration uses a transactional station-scope claim locking `(workline_id, position_code)` before outbox creation; a read-only lease status check or business `dispatch_key` is insufficient.
- Station-scope lock drift: mitigated by separating station scope locking from globally unique business `dispatch_key`; station lock covers `(workline_id, position_code)` only while active, and terminal dispatch does not permanently reserve the Station.
- `SENT` unfinished outbox releases Station too early: mitigated by treating `SENT` with `finished_at is None` as active dispatch lease until callback completion or explicit terminal handling.
- Stale terminal outbox blocks Station forever: mitigated by Task 3 / Task 6 terminal-status tests; terminal dispatch must not block later station re-claim after WMS wait is closed.
- WMS callback cannot recover session: mitigated by D4 using rack operation wait token, not naked external request.
- Direct-device fields hidden in nested payload: uncovered in current plan; add recursive forbidden-field tests.
- Plugin station/rack semantics drift: mitigated by `WorklinePluginManifest.single_layer_boundaries` plus future plugin准入门禁；summary API drift is mitigated by exporting the same boundary through `WorkLinePluginManifestSummary`、public manifest route and OpenAPI schema.
- Empty-rack authority wording drift: mitigated by SRS/ADR/SPEC guard tests that pin empty-rack resource master, transport authority, and physical occupancy to WMS/RCS.
- Sorting source station drift: mitigated by source station / 上料位 active snapshot and Station lease tests matching target station coverage.
- Sorting target station drift: mitigated by requiring target station lease coverage in the same test lane as active target snapshot coverage.
- Runtime frontend contract drift: mitigated by runtime detail response and OpenAPI tests for structured lease/snapshot/wait/evidence fields.
- SRS old authority drift: mitigated by SRS guard tests for five-layer hot/cold zones, A/B side balance, empty-bin authorization, CTU path authority, and execution snapshot exceptions.
- Runtime enum drift: mitigated by exact field, enum, OpenAPI, generated-type, and frontend adapter checks, including `ACTIVE_RACK_BOUND`.
- Backend/frontend alias drift: mitigated by keeping OpenAPI/generated types snake_case by default and testing any optional backend alias path end to end before adoption.
- Dual-checklist drift: mitigated by keeping only Task 0-10 as the executable checklist and using review-report items as finding mappings.
- Frontend visual QA unavailable: mitigated by extending the project executable `pnpm smoke:runtime:agent-browser` or adding a monitor smoke command that actually opens `/runtime/monitor`, fixes desktop/mobile viewport, and asserts lease/rack operation/resource evidence states; Playwright may be used only after dependency/config/spec setup is part of the plan.
- Frontend raw JSON scan false positives or zsh glob failure: mitigated by using `rg --files | rg ... | xargs rg` against monitor adapter/components instead of unmatched shell globs, while maintaining an allowlist for sandbox/trace raw evidence views.
- Future single-layer plugin false negative: mitigated by manifest capability/resource marker or mandatory plugin list rather than source-code string scanning.
- SRS congestion authority drift: mitigated by wording that WES can submit business throttling or pacing suggestions, while WMS/RCS owns region capacity, AGV congestion, path planning, and avoidance.
- Production/return rack slot mastership drift: mitigated by SRS wording and doc guards that treat PKG/Rack/Side/Slot as execution evidence only, not WES truth for storage ownership or inventory availability.
- Format-check drift: mitigated by full-repository `uv run ruff format .` and `uv run ruff check .` instead of a stale explicit file list.

### Parallelization

Limited parallelization is possible after Task 0 and the WMS/plugin boundary decisions are locked. Keep shared runtime contract edits serialized, but docs guards, WMS transport contract tests, Station lease tests, and plugin boundary tests can be prepared as separate lanes before implementation is merged.

### Review Finding Map

以下内容只表示评审发现到正文 Task 的映射，不作为第二套执行清单。实际实施只能按上方 `## Task 0` 到 `## Task 10` 的 checkbox 推进。

| Review finding | Priority | Main task | Scope |
| --- | --- | --- | --- |
| T1 | P1 | Task 4 | 把 WMS transport contract 收回 `wms_integration`。 |
| T2 | P1 | Task 3 | Station lease 同时检查 active outbox 与 open session。 |
| T3 | P1 | Task 6 | 单层编排输出 rack operation 语义，不绕过 wait/context。 |
| T4 | P2 | Task 3 / Task 4 | 补齐 lease/outbox 状态过滤和 forbidden payload 递归测试。 |
| T5 | P1 | Task 8 | 增加插件级单层货架承接矩阵与公开 manifest 合同。 |
| T6 | P1 | 运行态结构化展示合同 / Task 10 | 暴露 exact runtime detail snake_case 字段、枚举和 OpenAPI 合同，前端 adapter 转 camelCase。 |
| T7 | P2 | Task 1 | 让 SRS/ADR/SPEC 文档守护范围与计划表述一致。 |
| T8 | P2 | Task 1 / frontend plan | 补强 SRS 旧五层权威口径、执行事实例外和前端视觉 QA 可复现性。 |
| T9 | P1 | Task 3 / Task 6 | Station dispatch 必须通过事务 claim，避免并发重复派发。 |
| T10 | P1 | Task 3 / Task 6 | `SENT` 且未完成的 outbox 仍占用 Station。 |
| T11 | P1 | 运行态结构化展示合同 / frontend plan | `station_lease` enum 同步包含 `ACTIVE_RACK_BOUND`。 |
| T12 | P1 | 运行态结构化展示合同 / frontend plan | OpenAPI/generated types 保持 snake_case，前端 adapter 转 camelCase。 |
| T13 | P2 | frontend plan | 使用项目已有可执行 browser QA 命令，Playwright 需先纳入依赖/config/spec。 |
| T14 | P2 | frontend plan | raw JSON 搜索范围收窄到 monitor adapter/components 或 allowlist。 |
| T15 | P2 | Task 8 | 未来单层插件识别使用 manifest marker 或强制清单。 |
| T16 | P2 | Task 1 | SRS 任务并发 wording 不得把 AGV 拥堵/区域容量权威交给 WES。 |
| T17 | P1 | Task 3 / Task 6 | Station claim 锁定 `(workline_id, position_code)`，业务 `dispatch_key` 不代替 station scope lock。 |
| T18 | P1 | Task 3 / Task 6 | 并发不同业务需求只允许一个 claim，同一 Station terminal 后允许新业务再次 claim。 |
| T19 | P1 | frontend plan | `pnpm smoke:runtime:agent-browser` 必须实际覆盖 `/runtime/monitor` 双 viewport 和 overflow/overlap 断言。 |
| T20 | P2 | Task 1 | SRS 生产/退货货架槽位口径保持执行证据，不表达 WES 储位主账。 |

### Completion Summary

- Step 0: Scope Challenge — scope accepted as-is by user.
- Architecture Review: 9 issues found, all decisions resolved.
- Code Quality Review: 2 issues found, resolved.
- Test Review: diagram produced, 46 gaps identified.
- Performance Review: 1 issue found, resolved.
- NOT in scope: written.
- What already exists: written.
- TODOS.md updates: 0 items proposed; all findings are same-branch plan fixes, not deferred TODOs.
- Failure modes: 0 critical silent gaps after accepted fixes are added to plan.
- Outside voice: skipped.
- Parallelization: limited parallel lanes available after shared contract decisions.
- Lake Score: 7/7 recommendations chose complete option.
