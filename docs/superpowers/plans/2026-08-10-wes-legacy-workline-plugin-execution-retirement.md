# WES Phase 5 旧工作线插件执行闭包退役计划

> **状态：Approved**
> **冻结源码基线：** `develop@da8c107385fb64da86c02fddda10429e5d260299`
> **适用条件：** 实施分支相对该基线只能先包含本计划及同批架构文档；若生产代码、测试、迁移或机器可读配置已经变化，
> 本批准自动失效，必须重新生成引用闭包和逐文件矩阵后再评审。
> **执行要求：** 代码实施使用 `superpowers:using-git-worktrees`、`superpowers:test-driven-development` 和
> `superpowers:verification-before-completion`；本文是实施计划，不是可复制执行的删除脚本。

**Goal：** 在不保留兼容路径、不迁移旧插件源码、不提前实现后续阶段能力的前提下，原子退役嵌入核心的
`rough_sorter`、`smt_sorting_inbound` 及其专属 plugin registry、generated index、binding、dispatcher、
Runtime/Intent/Effect/SystemCapability 调用闭包，交付“核心全绿、业务插件安装清单为空”的受控中间态。

**Architecture：** WorkLine 只保留静态身份、物理拓扑、通用运行配置和启停管理；RuntimeInbox 只保留与具体插件无关的
持久化、领取、幂等、重放来源校验和终态机制。具体业务规则、插件 manifest、handler、动态 registry 和插件 attempt
全部删除。Phase 4 Transport 保持暗构建；Phase 6/7 分别拥有 Transport 与 Device/ECS 收敛，Phase 8/9 才重写业务插件。

**Tech stack：** Python 3.13、FastAPI、SQLModel/SQLAlchemy、Celery、Pytest、Ruff、现有质量门禁和 HEAVY selector。

**权威输入：**

- `docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md`
- `docs/superpowers/plans/2026-08-03-wes-architecture-convergence-master-plan.md`
- `docs/superpowers/plans/2026-07-31-wes-test-semantics-and-weight-convergence.md`
- `tests/README.md`
- `docs/architecture/heavy-test-impact.toml`

---

## 1. 核心裁决

Phase 5 删除的是**旧插件活动执行闭包**，不是某一个目录，也不是所有带 `runtime`、`intent`、`effect` 字样的对象。

允许并明确接受以下中间态：

```text
通用核心与已交付基础能力全绿
        ↓
业务插件安装清单为空
        ↓
不接纳插件业务事件，不创建插件执行对象
        ↓
Phase 8/9 按新合同完整重写并显式装配真实插件
```

本阶段采用四种且仅四种处置：

- `DELETE → NONE (PLUGIN_OWNED)`：只证明具体插件业务，未来插件按新代码重建；
- `DELETE → NONE (LEGACY_PLATFORM)`：只为旧插件平台存在，目标架构没有同类 successor；
- `MODIFY → <owner>`：文件同时承载通用核心能力，删除插件分支后由列出的最终或阶段性 owner 继续负责；
- `RETAIN → <owner>`：当前文件不变，仍由明确 owner 负责。

`DEFER`、`MOVE`、`RENAME`、`ALIAS`、`SHIM`、`FALLBACK`、空插件、默认插件和 no-op consumer 均不是合法处置。
需要 Phase 7 新建的能力只记为 `Phase 7 ADD`，不冒充 Phase 5 successor。

## 2. 阶段边界

| 对象 | Phase 5 裁决 | 唯一 owner |
| --- | --- | --- |
| 具体粗分/SMT 业务实现 | 删除，不迁移源码 | Phase 8/9 独立插件包重新实现 |
| generated plugin index、registry、dispatcher、binding、attempt | 删除 | `NONE (LEGACY_PLATFORM)` |
| WorkLine 静态身份、拓扑、通用配置和启停 | 去除插件字段、路由和校验后保留 | WES 核心 WorkLine |
| RuntimeInbox ingress/claim/idempotency/replay/terminal | 去除插件处理分支后保留 | WES 核心 RuntimeInbox |
| RuntimeIntent/Effect/SystemOutbox 的非插件分支 | 本阶段不总删，只解除插件调用 | 后续 Phase 6/7/10 按对象收敛 |
| Phase 4 Transport | 不接旧插件、不删除 | Phase 6 |
| DeviceCommand、设备事件/结果、Epoch fencing | 不提升旧 owner，不新建 | Phase 7 ADD |
| WMS Adapter 公共合同 | 只删除粗分/SMT 专属 operation 和 fixture | `src/app/wms_adapter/`/WMS ACL |
| 既有 Alembic revisions | 保留历史链；Phase 5 新增一条无数据迁移的 schema cleanup revision | Phase 11 压成一次性干净基线 |
| 厂商原始资料 | 不修改、不删除 | `docs/hardware/` |

数据库必须和 ORM 同步收敛。Phase 5 使用 Alembic generator 新建一条 schema cleanup revision，删除
`workline_plugin_bindings`、相关外键/索引，以及 WorkLine、WorklineSession、ExecutionSession、ExecutionWorkItem 上的
plugin identity、manifest、pin/state 列。该 revision 不读取、回填或转换旧数据，也不提供 downgrade。开发和测试数据可以
直接丢弃，但本计划不把清理共享数据库作为自动步骤。既有 revision 只作为生成历史保留到 Phase 11，
临时空库执行 `alembic upgrade head` 后不得再出现旧表、旧列或旧约束。

## 3. 冻结引用图与生产装配

当前唯一插件生产入口是：

```text
callback / RuntimeInbox consumer
  → RuntimeInboxProcessorBridge
  → plugin binding + generated request
  → WorklinePluginDispatcher
  → rough_sorter / smt_sorting_inbound handler
  → RuntimeIntentLog / plugin attempt write set / SystemCapability / SystemOutbox
```

Phase 5 后必须变为：

```text
callback / RuntimeInbox ingress
  → 持久化、领取、幂等、重放来源校验、终态
  → 仅保留已经有独立 owner 的 WMS/control 通用分支

插件业务事件
  → 无安装入口、无 handler、无 binding、无执行对象
```

现状没有独立的插件 Celery queue、beat 或镜像安装清单；Celery 的通用 RuntimeInbox consumer 必须 `RETAIN`，只移除其
插件处理分支。不得为了满足“零插件”删除整个 consumer，也不得增加一个空 handler 吞掉业务事件。

## 4. 逐文件生产代码处置矩阵

### 4.1 嵌入式插件包：全部 `DELETE → NONE`

以下 23 个已跟踪文件逐文件标记为 `DELETE → NONE`；`rough_sorter/` 和 `smt_sorting_inbound/` 为
`PLUGIN_OWNED`，其余为 `LEGACY_PLATFORM`：

| 文件 | 分类 |
| --- | --- |
| `src/app/runtime/workline_plugins/__init__.py` | `LEGACY_PLATFORM` |
| `src/app/runtime/workline_plugins/attempt_coordinator.py` | `LEGACY_PLATFORM` |
| `src/app/runtime/workline_plugins/contracts.py` | `LEGACY_PLATFORM` |
| `src/app/runtime/workline_plugins/definition.py` | `LEGACY_PLATFORM` |
| `src/app/runtime/workline_plugins/dispatcher.py` | `LEGACY_PLATFORM` |
| `src/app/runtime/workline_plugins/generated_index.py` | `LEGACY_PLATFORM` |
| `src/app/runtime/workline_plugins/handler_registry.py` | `LEGACY_PLATFORM` |
| `src/app/runtime/workline_plugins/index_builder.py` | `LEGACY_PLATFORM` |
| `src/app/runtime/workline_plugins/pre_attempt.py` | `LEGACY_PLATFORM` |
| `src/app/runtime/workline_plugins/registry.py` | `LEGACY_PLATFORM` |
| `src/app/runtime/workline_plugins/schema.py` | `LEGACY_PLATFORM` |
| `src/app/runtime/workline_plugins/rough_sorter/__init__.py` | `PLUGIN_OWNED` |
| `src/app/runtime/workline_plugins/rough_sorter/config.py` | `PLUGIN_OWNED` |
| `src/app/runtime/workline_plugins/rough_sorter/definition.py` | `PLUGIN_OWNED` |
| `src/app/runtime/workline_plugins/rough_sorter/domain_contract.py` | `PLUGIN_OWNED` |
| `src/app/runtime/workline_plugins/rough_sorter/handlers.py` | `PLUGIN_OWNED` |
| `src/app/runtime/workline_plugins/rough_sorter/inputs.py` | `PLUGIN_OWNED` |
| `src/app/runtime/workline_plugins/rough_sorter/pre_attempt.py` | `PLUGIN_OWNED` |
| `src/app/runtime/workline_plugins/rough_sorter/state.py` | `PLUGIN_OWNED` |
| `src/app/runtime/workline_plugins/smt_sorting_inbound/__init__.py` | `PLUGIN_OWNED` |
| `src/app/runtime/workline_plugins/smt_sorting_inbound/contracts.py` | `PLUGIN_OWNED` |
| `src/app/runtime/workline_plugins/smt_sorting_inbound/definition.py` | `PLUGIN_OWNED` |
| `src/app/runtime/workline_plugins/smt_sorting_inbound/handlers.py` | `PLUGIN_OWNED` |

### 4.2 WorkLine 旧平台和过程工具：`DELETE → NONE (LEGACY_PLATFORM)`

| 文件 | 删除理由 |
| --- | --- |
| `src/app/workline/models/plugin_binding.py` | immutable binding 表的活动 ORM owner |
| `src/app/workline/repositories/plugin_binding_repository.py` | binding、session/work item pin 查询 owner |
| `src/app/workline/services/plugin_binding_service.py` | binding activate/admission/snapshot owner |
| `src/app/workline/models/migration_inventory.py` | 旧平台迁移盘点 DTO |
| `src/app/workline/models/migration_matrix.py` | 旧平台迁移矩阵 DTO |
| `src/app/workline/services/migration_inventory_service.py` | generated plugin index 迁移盘点 |
| `src/app/workline/services/migration_matrix_service.py` | 旧平台迁移决策服务 |
| `src/app/workline/services/manifest_validator.py` | 旧插件 manifest 激活校验 |
| `scripts/workline_migration_inventory.py` | 旧平台盘点入口 |
| `scripts/workline_migration_matrix.py` | 旧平台矩阵入口 |
| `scripts/data/repair_runtime_holds.py` | 依赖旧 plugin registry 的未发布数据修复脚本 |

这些文件不得移动到新包；删除记录统一写明 `DELETE → NONE (LEGACY_PLATFORM)`。

### 4.3 具体粗分/SMT 业务闭包：`DELETE → NONE (PLUGIN_OWNED)`

| 文件 | 业务归属 |
| --- | --- |
| `src/app/resource/services/smt_bin_cell_allocation_policy.py` | SMT 槽位分配规则 |
| `src/app/resource/services/smt_rack_bin_scheduling_service.py` | SMT 货架/料箱调度规则 |
| `src/app/workline/domain/services/smt_rack_bin_scheduling_service.py` | SMT 旧转发入口 |
| `src/app/runtime/capabilities/material_flow/contracts/rough_sorter.py` | 粗分业务合同 |
| `src/app/runtime/capabilities/material_flow/contracts/rough_sorter_context.py` | 粗分上下文 |
| `src/app/runtime/capabilities/material_flow/contracts/rough_sorter_inventory_admission.py` | 粗分库存准入 |
| `src/app/runtime/capabilities/material_flow/contracts/smt_inbound_handoff_reason.py` | SMT 交接原因 |
| `src/app/runtime/capabilities/material_flow/contracts/smt_sorting_inbound.py` | SMT 入库合同 |
| `src/app/runtime/capabilities/material_flow/contracts/smt_usage_policy.py` | SMT 使用策略 |
| `src/app/runtime/capabilities/material_flow/contracts/sorting_inbound_context.py` | 分拣入库上下文 |
| `src/app/runtime/capabilities/material_flow/ng_return_item_service.py` | 旧插件 NG 回流规则 |
| `src/app/runtime/capabilities/material_flow/rough_sorter_q19_admission_service.py` | 粗分 Q19 准入 |
| `src/app/runtime/capabilities/material_flow/smt_inbound_handoff_route_service.py` | SMT 交接路由 |
| `src/app/runtime/capabilities/material_flow/smt_ng_wms_reconciliation_policy.py` | SMT NG 对账策略 |
| `src/app/runtime/capabilities/material_flow/smt_ng_wms_reconciliation_preview_service.py` | SMT NG 预览 |
| `src/app/runtime/capabilities/material_flow/smt_ng_wms_reconciliation_runtime_service.py` | SMT NG 运行服务 |
| `src/app/runtime/capabilities/material_flow/sorter_inbound_preview_service.py` | 分拣入库预览 |
| `src/app/runtime/capabilities/material_flow/sorter_inbound_runtime_service.py` | 分拣入库运行服务 |
| `src/app/runtime/orchestration/models/smt_inbound_handoff.py` | SMT 交接模型 |
| `src/app/runtime/orchestration/repositories/plugin_attempt_repository.py` | 旧插件 attempt 持久化 |
| `src/app/runtime/orchestration/repositories/full_box_exchange_repository.py` | SMT 满箱交换持久化 |
| `src/app/runtime/orchestration/repositories/runtime_domain_capability_authority_repository.py` | SMT domain capability 权限事实 |
| `src/app/runtime/orchestration/repositories/rough_sorter_q19_admission_repository.py` | 粗分 Q19 查询 |
| `src/app/runtime/orchestration/repositories/smt_inbound_handoff_repository.py` | SMT 交接持久化 |
| `src/app/runtime/orchestration/sandbox_catalog_bridge.py` | 粗分 SANDBOX payload 与 mock catalog |
| `src/app/runtime/orchestration/repositories/wms_conveyor_batch_repository.py` | SMT 输送批次持久化 |
| `src/app/runtime/orchestration/repositories/wms_conveyor_return_batch_repository.py` | SMT 输送回流持久化 |
| `src/app/runtime/orchestration/services/full_box_exchange_service.py` | SMT 满箱交换业务服务 |
| `src/app/runtime/orchestration/services/intent/smt_inbound_handoff_service.py` | SMT 交接 Intent |
| `src/app/runtime/orchestration/services/intent/runtime_domain_capability_authority_resolver.py` | SMT capability authority 解析 |
| `src/app/runtime/orchestration/services/wms_conveyor_batch_service.py` | SMT 输送线批处理 |
| `src/app/runtime/orchestration/services/wms_conveyor_return_batch_service.py` | SMT 输送回流批处理 |
| `src/app/runtime/orchestration/wms_conveyor_batch_member.py` | SMT 输送批次成员模型 |
| `src/app/runtime/system_capabilities/material_flow/smt_source_pick_command/__init__.py` | SMT 取料命令 capability |
| `src/app/runtime/system_capabilities/material_flow/smt_source_pick_command/contracts.py` | SMT 取料命令合同 |
| `src/app/runtime/system_capabilities/material_flow/smt_source_pick_command/definition.py` | SMT 取料命令定义 |
| `src/app/runtime/system_capabilities/material_flow/smt_source_pick_command/handler.py` | SMT 取料命令 handler |
| `src/app/runtime/system_capabilities/material_flow/smt_source_pick_ledger/__init__.py` | SMT 取料台账 capability |
| `src/app/runtime/system_capabilities/material_flow/smt_source_pick_ledger/contracts.py` | SMT 取料台账合同 |
| `src/app/runtime/system_capabilities/material_flow/smt_source_pick_ledger/definition.py` | SMT 取料台账定义 |
| `src/app/runtime/system_capabilities/material_flow/smt_source_pick_ledger/handler.py` | SMT 取料台账 handler |
| `src/app/runtime/system_capabilities/wms/document/validate_rough_sorter_admission/definition.py` | 粗分 WMS 准入 operation |
| `src/app/runtime/system_capabilities/wms/fulfillment/full_box_exchange/definition.py` | SMT 满箱交换 operation |

### 4.4 混合文件：删除插件分支后 `MODIFY →` 明确 owner

| 文件 | Phase 5 精确修改 | 保留 owner |
| --- | --- | --- |
| `src/app/workline/models/workline.py` | 删除 plugin/binding 字段、动态定义属性、插件 DTO；保留静态身份、通用配置、启停 DTO | WorkLine |
| `src/app/workline/models/__init__.py` | 删除旧模型和插件 DTO export | WorkLine |
| `src/app/workline/repositories/__init__.py` | 删除 binding repository export | WorkLine repository |
| `src/app/workline/services/__init__.py` | 删除 binding/migration/manifest service export | WorkLine service |
| `src/app/workline/repositories/workline_repository.py` | 删除 plugin pin 专属锁入口；保留通用按 ID 加锁 | WorkLine repository |
| `src/app/workline/services/workline_service.py` | 删除插件 options/manifest/assignment/binding 激活；配置状态只检查通用 WorkLine 条件 | WorkLine service |
| `src/app/workline/v1/workline.py` | 删除 `/plugins/options`、`/plugins/{plugin_key}/manifest`；保留 CRUD、配置状态、启停、平面视图 | WorkLine API |
| `src/app/workline/services/write_back_service.py` | 删除 plugin contract version 推导 | 通用写回 |
| `src/app/workline/domain/__init__.py` | 删除 SMT 调度 lazy export | WorkLine domain |
| `src/app/workline/domain/services/__init__.py` | 删除 SMT 调度 export | WorkLine domain |
| `src/app/workline/runtime_services.py` | 删除 SMT bin allocator 注入和返回类型 | 通用运行服务装配 |
| `src/app/resource/services/__init__.py` | 删除 SMT policy/service export | Resource 通用服务 |
| `src/app/runtime/capabilities/material_flow/contracts/__init__.py` | 删除粗分/SMT 合同 export | Material-flow 通用合同 |
| `src/app/runtime/capabilities/material_flow/start_admission_service.py` | 删除 registry/manifest 推导；只保留通用设备与拓扑准入 | WorkLine 通用准入 |
| `src/app/runtime/capabilities/material_flow/__init__.py` | 删除已退役业务 service export | Material-flow 基础包 |
| `src/app/device/services/device_context_service.py` | 删除 plugin contract version 推导，不建立 Device successor | Device 当前上下文；Phase 7 收敛 |
| `src/app/runtime/normalization/normalizers/input_normalizer.py` | 删除插件业务 key/result 分类 | 通用 ingress normalization |
| `src/app/runtime/orchestration/topology_bridge.py` | 删除插件 schema 类型依赖，保留通用拓扑读取 | WorkLine topology |
| `src/app/runtime/orchestration/services/session/session_resolver.py` | 删除插件 identity/binding 解析；插件业务 session 不再创建 | 通用 session 查询；Phase 10 收敛 |
| `src/app/runtime/orchestration/services/hold/runtime_hold_query_service.py` | 删除 registry NG reason 解析 | RuntimeHold 查询 |
| `src/app/runtime/orchestration/services/hold/runtime_hold_release_service.py` | 删除插件 definition/reason 依赖 | RuntimeHold 释放 |
| `src/app/runtime/orchestration/services/intent/operation_service.py` | 删除插件 definition 到 WMS operation 的推导 | WMS ACL operation |
| `src/app/runtime/orchestration/services/intent/system_capability_intent_service.py` | 删除 generated plugin index fallback | 非插件 SystemCapability；Phase 10 收敛 |
| `src/app/runtime/orchestration/services/query/runtime_query_service.py` | 删除插件 definition 查询 | 通用运行查询 |
| `src/app/runtime/orchestration/runtime_intent_effects.py` | 删除插件 capability definition 解析 | 非插件 Effect；Phase 6/7/10 收敛 |
| `src/app/runtime/orchestration/repositories/runtime_intent_log_repository.py` | 删除 plugin attempt 类型和提交入口 | 非插件 IntentLog |
| `src/app/runtime/orchestration/repositories/session_execution_anchor_repository.py` | 删除 plugin binding 匹配，保留通用 execution anchor | 通用运行相关性 |
| `src/app/runtime/orchestration/repositories/__init__.py` | 删除 plugin/粗分/SMT repository export | Runtime repository |
| `src/app/runtime/orchestration/models/__init__.py` | 删除 SMT 交接模型 export | Runtime model |
| `src/app/runtime/orchestration/services/intent/__init__.py` | 删除 SMT 交接 service export | Runtime intent service |
| `src/app/runtime/orchestration/repository_wiring.py` | 删除 SMT handoff repository 装配 | Runtime repository wiring |
| `src/app/runtime/orchestration/__init__.py` | 删除 SMT/conveyor 业务 export 与说明 | Runtime orchestration |
| `src/app/runtime/orchestration/services/__init__.py` | 删除 full-box/conveyor service export；保留 rack demand/projector export | Runtime service；Phase 6 收敛 |
| `src/app/runtime/orchestration/effect_bridges.py` | 删除 SMT conveyor/full-box 投影分支；保留 E08/E09 rack demand 投影 | 旧 Transport Effect；Phase 6 收敛 |
| `src/app/runtime/orchestration/operation_observability.py` | 删除 full-box/conveyor 业务观测映射 | 通用 operation observability |
| `src/app/runtime/orchestration/services/wms_effect_status_service.py` | 删除 SMT/full-box/conveyor projector 分支；保留 E08/E09 终态投影 | 旧 Transport effect 状态；Phase 6 收敛 |
| `src/app/runtime/orchestration/services/wms_fulfillment_domain_projector.py` | 删除 SMT、满箱和输送相关 import、构造依赖及投影分支；保留 E08/E09 rack demand preparation、reject、terminal 和 reconciliation 投影 | 旧 Transport 业务投影；Phase 6 删除 |
| `src/app/runtime/orchestration/services/runtime_inbox/runtime_inbox_context_loader.py` | 删除 binding admission 和插件上下文加载 | RuntimeInbox context |
| `src/app/runtime/orchestration/services/runtime_inbox/runtime_inbox_validation_service.py` | 删除插件 registry 路由校验 | RuntimeInbox validation |
| `src/app/runtime/orchestration/services/runtime_inbox/runtime_inbox_orchestrator_bridge.py` | 删除 generated request、attempt、dispatcher 和插件三阶段分支；保留通用 WMS/control 分支 | RuntimeInbox processor |
| `src/app/runtime/orchestration/services/runtime_inbox/runtime_inbox_writeback_service.py` | 删除 `commit_plugin_attempt`、binding 校验和 plugin write set；保留通用终态写回 | RuntimeInbox writeback |
| `src/app/runtime/system_capabilities/generated_index.py` | 重新生成后不含已删除的粗分/SMT capability | 非插件 SystemCapability |
| `src/app/runtime/system_capabilities/material_flow/__init__.py` | 删除 SMT capability export | 非插件 SystemCapability |
| `src/app/runtime/system_capabilities/wms/generated_operation_index.py` | 重新生成后不含粗分准入 operation | WMS Adapter 合同索引 |
| `src/app/wms_integration/ports/document_operations.py` | 删除粗分准入 operation 常量/合同 | WMS ACL |
| `src/app/wms_integration/ports/fulfillment_operations.py` | 删除 full-box/conveyor operation 及仅由二者使用的批次 ACK、冻结范围和终态校验 | WMS ACL |
| `src/app/wms_integration/ports/effect_status.py` | 删除 E12/E13 批次状态请求、`accepted_scope` 恢复和批次终态分支；保留单对象 effect 状态合同 | WMS ACL |
| `src/app/wms_integration/effect_runtime.py` | 删除批次 `accepted_scope` 校验分支；保留非批次异步 ACK 解析 | WMS ACL |
| `src/app/wms_integration/operation_contract.py` | 删除 full-box/conveyor domain projection kind | WMS ACL |
| `src/app/wms_integration/provider_manifest.py` | 删除粗分/SMT 专属 operation 声明 | WMS ACL |
| `src/app/runtime/system_capabilities/wms/contracts.py` | 删除 E13 `max_candidate_count` 透出；保留其他 WMS capability 合同 | WMS Adapter capability |
| `scripts/generate_runtime_extensions.py` | 删除插件 index builder；只继续生成仍保留的 SystemCapability/WMS 索引 | 构建期生成器 |
| `scripts/verify_wms_northbound_feasibility.py` | 删除 E12/E13 请求、批次 ACK 和 `accepted_scope` 验证分支；保留其余 WMS operation 验证 | WMS Adapter 验收工具 |
| `scripts/generate_legacy_matrix.py` | 删除已退役插件/binding 条目并重生成矩阵；保留其他阶段治理 | Phase 10 旧路径治理 |
| `scripts/check_business_legacy_absence_gate.py` | 删除已完成 Phase 5 carrier，继续校验未完成业务清理 | Phase 10 业务缺席门禁 |
| `scripts/workline_inbox_retirement_guardrail.py` | 删除对已退役插件过程工具的允许引用，保留旧 Inbox 缺席门禁 | RuntimeInbox 架构门禁 |
| `scripts/data/init_production_base_data.sql` | 删除粗分/SMT WorkLine、binding 和专属配置 seed | 通用基础数据 |
| `scripts/data/reset_runtime_data.py` | 删除 SMT handoff 旧表清理项；脚本继续清理通用运行表 | 开发/测试数据重置 |
| `src/celery_app/config.py` | 删除 `scan-smt-inbound-handoff-demands-batch` beat | Celery 通用配置 |
| `src/celery_app/tasks/workline.py` | 删除 SMT handoff recovery task/helper/export，保留通用 WorkLine tasks | Celery 通用 worker |
| `scripts/architecture-guardrails.sh` | 把“扫描嵌入插件结构”改为“禁止嵌入插件包和 import” | 架构缺席门禁 |
| `scripts/architecture-guardrails.allowlist` | 删除 Phase 5 旧插件豁免和过期说明 | 架构门禁真源 |
| `docs/architecture/legacy-cleanup-matrix.csv` | 由保留的生成器重生成，删除 Phase 5 已完成 owner 行 | Phase 10 机器可读治理 |
| `docs/architecture/business-legacy-absence-ledger.csv` | 删除已退役插件 carrier 行，保留未完成业务 owner | Phase 10 机器可读治理 |

### 4.5 Schema cutover 与显式保留项

| 文件/范围 | 处置 | 理由 |
| --- | --- | --- |
| `migrations/versions/20260325_1540_4d2d6f0d9d8a_add_workline_plugin_fields.py` | `RETAIN → Phase 11` | 保留历史链，当前 schema 由 Phase 5 cleanup revision 覆盖 |
| `migrations/versions/20260622_1052_84c693e1bac9_sync_workline_plugin_contract_versions.py` | `RETAIN → Phase 11` | 同上 |
| `migrations/versions/20260717_0739_fa15ba0aef65_add_workline_plugin_runtime_binding.py` | `RETAIN → Phase 11` | 同上 |
| `migrations/versions/20260723_1445_5d251fdbb1e8_drop_legacy_port_method_snapshots.py` | `RETAIN → Phase 11` | 直接操作 `workline_plugin_bindings`，属于插件 schema 历史链 |
| `migrations/versions/20260727_1742_be496b91f3e3_enforce_runtime_plugin_binding.py` | `RETAIN → Phase 11` | 同上 |
| `migrations/versions/<generated_revision>_remove_workline_plugin_execution_schema.py` | `ADD → Phase 5 schema cutover` | generator 生成 ID；只删除旧表、列、FK、索引和约束，不迁移数据、不提供 downgrade |
| `src/app/runtime/orchestration/execution_session.py` | `MODIFY → Phase 10` | 删除 plugin key、manifest、binding pin/state 字段；保留仍被通用相关性查询使用的会话身份和状态 |
| `src/app/runtime/orchestration/execution_work_item.py` | `MODIFY → Phase 10` | 删除 plugin key、manifest、binding pin/state 字段；保留对象身份、步骤、相关性和 lease |
| `src/app/runtime/orchestration/models/session.py` | `MODIFY → Phase 10` | 从 WorklineSession 删除 plugin key、contract、binding pin/state 字段；保留通用会话和运行状态 |
| `src/app/runtime/orchestration/runtime_intent.py` | `RETAIN → Phase 10` | 非插件 Intent 仍有阶段性 owner，不以插件测试证明 |
| `src/app/runtime/orchestration/runtime_intent_log.py` | `RETAIN → Phase 10` | 可空 plugin identity 只作冻结审计；Phase 5 删除其插件写入 owner |
| `src/app/runtime/orchestration/models/runtime_hold.py` | `RETAIN → Phase 10` | 可空 plugin identity 不构成执行入口；后续随旧 runtime 总清理 |
| `src/app/runtime/orchestration/models/diagnostic.py` | `RETAIN → Phase 10` | 同上 |
| `src/app/runtime/orchestration/models/runtime.py` | `RETAIN → Phase 10` | 同上；不得由零插件路径产生新值 |
| `src/app/runtime/orchestration/services/runtime_inbox/` 其他文件 | `RETAIN → RuntimeInbox` | claim、lease、timeout、replay、terminal 属于核心 |
| `src/app/wms_integration/effect_preparation_runtime.py` | `RETAIN → Phase 6` | 继续显式注入缩减后的 E08/E09 projector；不得改成空绑定或跳过投影 |
| `src/app/runtime/system_capabilities/wms/effect_runtime.py` | `RETAIN → Phase 6` | 保留 `domain_projector` Port 和事务内调用；Phase 5 不改变旧 Transport effect 语义 |
| `src/core/outbound_http/`、`src/app/wms_adapter/` | `RETAIN → Phase 6` | 不接旧插件，不用其测试证明 Phase 5 |
| `src/app/device/` 除矩阵列出的单一修改 | `RETAIN → Phase 7` | 本阶段不把旧 Device owner 升格或重写 |
| `docs/hardware/` | `RETAIN → 厂商原始资料` | 永不进入 Phase 5 清理范围 |

revision 必须通过 `rtk uv run alembic revision -m "remove workline plugin execution schema"` 生成，再编辑生成文件；禁止手写
revision ID。

## 5. 测试 successor/`NONE` 矩阵

### 5.1 先建立或确认的核心 successor

以下测试必须在删除旧 owner 前通过；若覆盖不足，先在这些路径补最小断言，不创建按 Phase 命名的临时测试：

| 通用不变量 | 唯一 successor 测试路径 |
| --- | --- |
| WorkLine 无插件字段的创建、更新、启停和通用配置状态 | `tests/workline/test_workline_service_projection.py`、`tests/api/test_workline_routes.py` |
| RuntimeInbox 五态领取、lease 和终态 | `tests/runtime/orchestration/test_runtime_inbox_service_5state_claim.py` |
| 重放来源和 envelope 防篡改 | `tests/runtime/orchestration/test_runtime_inbox_replay_source_validation.py` |
| PostgreSQL claim/并发领取 | `tests/integration/test_runtime_inbox_claim_repository.py` |
| 内部事件持久化与终态 | `tests/integration/test_runtime_inbox_service_internal_events.py` |
| WMS ingress 幂等冲突 | `tests/integration/test_wms_event_runtime_inbox_idempotency.py` |
| PostgreSQL 重放链、并发与防篡改 | `tests/integration/test_runtime_inbox_processing_postgresql.py`（移除插件场景后原路径保留） |
| 空库升级后的 plugin schema 缺席及 ORM/数据库一致性 | `tests/integration/test_workline_plugin_schema_retirement.py`（新增） |
| 崩溃恢复、fencing 和失败状态机 | `tests/resilience/test_runtime_inbox_failure_state_machine.py` |
| 核心不得拥有或导入嵌入插件 | `tests/architecture/test_core_plugin_test_ownership_guardrail.py` |
| E08/E09 projector 仍在 Intent/Outbox/reducer 事务边界内执行 | `tests/workline_runtime/system_capabilities/test_wms_fulfillment_domain_projection_hooks.py`（旧 Transport 业务回归，不作为 Phase 5 核心能力证明） |

`test_runtime_inbox_processing_postgresql.py` 不新建同义 successor：直接删除其中的粗分/插件 happy path，并把现有共享 fixture
改为无插件的 RuntimeInbox 证据 fixture。这样保留原有通用 owner，避免重复测试路径。

### 5.2 `DELETE → NONE`

| 测试/fixture | 分类 | 审计记录 |
| --- | --- | --- |
| `tests/unit/workline/test_workline_manifest_summary_service.py` | `LEGACY_PLATFORM` | `DELETE → NONE` |
| `tests/workline/test_manifest_activation_validator.py` | `LEGACY_PLATFORM` | `DELETE → NONE` |
| `tests/support/runtime_binding.py` | `LEGACY_PLATFORM` | `DELETE → NONE` |
| `tests/runtime/orchestration/test_runtime_inbox_processor_parity.py` 中插件 characterization 用例 | `LEGACY_PLATFORM` | 删除；通用 fencing 先由 resilience successor 承接 |
| `tests/runtime/orchestration/test_runtime_inbox_three_stage_processor.py` 中 attempt/dispatcher/generated write-set 用例 | `LEGACY_PLATFORM` | 删除用例；文件保留通用 claim/replay/timeout owner |
| `tests/support/runtime_inbox_processing_postgresql.py` 中粗分生产 fixture | `PLUGIN_OWNED` | 删除具体 fixture；文件改为无插件通用证据 fixture |
| `tests/integration/test_runtime_inbox_processing_postgresql.py` 中粗分/插件执行 happy path | `PLUGIN_OWNED` | `DELETE → NONE` |
| `tests/contracts/wms_integration/test_wms_batch_ack_contract.py` | `PLUGIN_OWNED` | `DELETE → NONE`；整文件只证明已删除的 E12/E13 批次合同 |

### 5.3 混合测试文件与新增 successor 的精确处置

| 文件 | 处置 |
| --- | --- |
| `tests/runtime/orchestration/test_runtime_inbox_processor_parity.py` | 通用 ESTOP/fencing 先补入 resilience successor，再删除整文件 |
| `tests/runtime/orchestration/test_runtime_inbox_three_stage_processor.py` | 删除插件 attempt/dispatcher 用例；原路径保留通用 claim/replay/timeout 用例 |
| `tests/architecture/test_runtime_inbox_repository_consumer_guardrail.py` | 删除 SMT repository import、文件项和 wiring 断言；保留 WorkLine/RuntimeInbox repository owner 门禁 |
| `tests/callback/test_external_runtime_inbox_persistence_flow.py` | 删除 WorkLine/WorklineSession plugin 字段和 binding fixture；保留外部 RuntimeInbox 持久化流程 |
| `tests/contracts/workline/test_runtime_session_advance_contract.py` | 删除 ExecutionSession/WorkItem plugin pin 与 manifest 断言；保留生命周期、对象级推进和父子相关性合同 |
| `tests/contracts/workline/test_runtime_snapshot_contract.py` | 删除 plugin manifest 快照断言；保留状态、timeline、inbox、hold、intent 和 correlation 快照合同 |
| `tests/integration/test_command_result_correlation_authority.py` | 改用无插件 ExecutionSession fixture；保留命令结果相关性权威 |
| `tests/integration/test_runtime_inbox_processing_postgresql.py` | 删除粗分/插件执行用例；原路径保留通用重放、并发、领取和防篡改 |
| `tests/integration/test_runtime_inbox_service_internal_events.py` | 删除 SMT source-pick producer 用例；其余 ExecutionSession fixture 移除 plugin pin，保留通用内部事件 |
| `tests/integration/test_runtime_intent_log_idempotency.py` | 改用无插件 ExecutionSession fixture；保留 IntentLog 幂等与相关性 |
| `tests/integration/test_runtime_remaining_entities.py` | 删除 binding fixture 和 plugin 字段断言，新增 ORM 中旧列/FK 缺席断言；保留通用 runtime metadata 合同 |
| `tests/integration/test_system_outbox_repository.py` | 删除 WorklineSession plugin pin fixture；保留非插件 SystemOutbox repository 行为到后续 owner 收敛 |
| `tests/integration/test_workline_plugin_schema_retirement.py` | `ADD`；从临时空 PostgreSQL 执行 `alembic upgrade head`，断言旧表、列、FK、索引缺席且当前 ORM 列集合一致 |
| `tests/support/runtime_inbox_processing_postgresql.py` | 删除粗分 domain import/fixture；原路径改为无插件 RuntimeInbox 证据 fixture |
| `tests/reconciliation/test_reconciliation_manager_contract.py` | 改用无插件 ExecutionSession fixture；保留通用对账 owner 合同 |
| `tests/runtime/orchestration/test_runtime_inbox_timer_reconciliation_flow.py` | 删除 WorklineSession/ExecutionSession plugin pin；保留 timer 与对账相关性 |
| `tests/unit/runtime/orchestration/test_execution_correlation_key.py` | 删除 plugin/manifest 必填断言；保留 ExecutionSession 身份、状态和 correlation FK 合同 |
| `tests/workline_runtime/test_workline_session_repository_versioning.py` | 删除 WorklineSession plugin pin；保留通用乐观锁版本行为 |
| `tests/mock/wms_operation_fixtures.py` | 删除粗分/SMT fixture，保留 WMS Adapter 公共 operation fixture |
| `tests/mock/wms_northbound_contract.py` | 删除 E12/E13 终态结果和 `accepted_scope` 构造分支；保留其他 WMS mock 合同 helper |
| `tests/mock/wms_mock_server.py` | 删除批次 `accepted_scope` 状态存取；保留其余 WMS operation mock 路由 |
| `tests/mock/test_wms_mock_server.py` | 删除 E12/E13 可见状态恢复测试；保留其余 mock server 测试 |
| `tests/mock/test_wms_northbound_contract.py` | `RETAIN`；作为修改后 WMS mock 合同 helper 的回归 owner |
| `tests/support/wms_conformance_runner.py` | 删除 E12/E13 批次状态请求选择；保留其余供应商一致性 runner |
| `tests/contracts/wms_integration/test_provider_conformance_runner_cli.py` | 删除已移除的 `accepted_scope` 响应字段；保留单对象状态 runner 合同 |
| `tests/sys/test_wms_async_effect_dispatch.py` | 删除 E12/E13 常量、批次 payload helper、批次 ACK 用例及对已删测试文件的私有函数导入；保留其他异步 effect 分派 |
| `tests/workline_runtime/system_capabilities/test_wms_effect_status_service.py` | 删除 E12 批次冻结范围用例；保留其他 WMS effect 状态测试 |
| `tests/contracts/wms_integration/test_wms_operation_catalog.py` | 删除粗分/SMT operation 断言，保留 WMS Adapter 合同 |
| `tests/contracts/wms_integration/test_effect_status_contract.py` | 删除 full-box/conveyor operation 组合，保留 Phase 6 WMS effect 状态合同 |
| `tests/workline/test_workline_service_projection.py` | 删除 binding stub/断言，改为无插件 WorkLine successor |
| `tests/api/test_workline_routes.py` | 删除插件 options/manifest route，保留 WorkLine API facade |
| `tests/device/test_device_context_service.py` | 删除 plugin contract version 推导用例，保留设备上下文边界 |
| `tests/workline_runtime/test_effect_apply_state.py` | 删除 `commit_plugin_attempt` 用例，保留非插件 effect 状态 |
| `tests/workline_runtime/test_effect_reducer.py` | 删除 binding fixture/断言，保留非插件 reducer 状态机 |
| `tests/workline_runtime/test_runtime_intent_effect_applier.py` | 删除插件 schema/resource-boundary 用例，保留非插件 effect |
| `tests/workline_runtime/test_runtime_type_boundary_regressions.py` | 删除 plugin attempt 持久化用例，保留其他类型边界 |
| `tests/workline_runtime/system_capabilities/test_system_capability_effect_service.py` | 删除 SMT capability 用例，DeviceCommand 用例留给 Phase 7 |
| `tests/integration/workline_capabilities/test_effect_reducer_postgresql.py` | 删除插件 identity fixture，保留通用 reducer 数据一致性 |
| `tests/integration/workline_capabilities/test_wms_effect_status_postgresql.py` | 删除粗分/SMT fixture，保留 WMS effect 状态直到 Phase 6 |
| `tests/workline_runtime/system_capabilities/test_wms_fulfillment_domain_projection_hooks.py` | `RETAIN`；只验证保留到 Phase 6 的 E08/E09 preparation、reject、terminal 和 reconciliation 投影，不证明 Phase 5 核心能力 |
| `tests/integration/test_external_http_transport_attempt_postgresql.py` | 仅移除 plugin binding fixture；Transport 断言原样保留，不作为 Phase 5 证明 |
| `tests/resilience/test_external_http_effect_crash_matrix_postgresql.py` | 仅移除 plugin binding fixture；Transport 断言原样保留 |
| `tests/architecture/test_process_naming_guardrail.py` | 删除 SMT 文件特例，保留通用命名门禁 |
| `tests/architecture/test_workline_service_shim_contract.py` | 删除 binding/migration service export 断言，保留其他边界 |
| `tests/architecture/test_core_plugin_test_ownership_guardrail.py` | 增加核心生产包和 `src.app.runtime.workline_plugins` import 缺席门禁 |
| `tests/architecture/test_business_legacy_absence_guardrail.py` | 保留旧包缺席门禁，并增加嵌入式插件包路径 |
| `tests/architecture/test_legacy_absence_guardrail.py` | 保留旧符号缺席门禁，并增加当前被删平台符号 |
| `tests/architecture/test_confirm_inbound_legacy_cutover.py` | 保留；被删 sorter 文件继续作为“路径缺席即通过”的旧来源 |
| `tests/architecture/test_notify_pkg_binding_legacy_cutover.py` | 删除被删 sorter 文件的强制读取，保留其他 WMS 旧入口缺席断言 |
| `tests/architecture/test_outbound_http_boundary_guardrail.py` | 删除 SMT route service 特例，保留 Transport 边界 |
| `tests/architecture/test_transport_boundaries.py` | `RETAIN`；`workline_plugins` 仅作为禁止依赖字面量 |
| `tests/architecture/test_transport_test_boundaries.py` | `RETAIN`；`workline_plugins` 仅作为禁止依赖字面量 |
| `tests/architecture/test_workline_inbox_retirement_guardrail.py` | 删除 SMT handoff 模型 import/断言，保留旧 WorklineInbox 缺席门禁 |
| `tests/deployment/test_celery_task_runtime_contract.py` | 删除 SMT handoff task/beat 期望，保留通用 Celery 合同 |
| `tests/integration/test_runtime_inbox_consumer_service.py` | 删除 binding fixture/字段，改为无插件 RuntimeInbox consumer |
| `tests/integration/test_runtime_inbox_migration_postgresql.py` | 删除 SMT handoff 表数据断言，保留 WorklineInbox 迁移边界到 Phase 11 |
| `tests/integration/test_runtime_inbox_repository_consumers.py` | 删除 SMT repository/service consumer，保留 RuntimeInbox/WorkLine consumer |
| `tests/workline_runtime/system_capabilities/test_runtime_domain_capability_authority_resolver.py` | `DELETE → NONE (PLUGIN_OWNED)` |
| `tests/workline_runtime/test_runtime_diagnostics_contract.py` | 删除 plugin binding 诊断用例，保留通用诊断合同 |
| `tests/resource/test_resource_domain_pruning_contract.py` | `RETAIN`；`FullBoxExchangeTask` 仅作为已删除模型缺席断言 |
| `tests/scripts/test_select_heavy_tests.py` | 同步新/删 HEAVY 路径和 selector 预期 |
| `tests/scripts/test_select_heavy_tests_regression_5.py` | 保留 generator 为受影响候选，不删除当前治理入口 |
| `tests/architecture/test_cleanup_matrix_guardrail.py` | 更新 Phase 5 已完成条目，保留矩阵 schema/闭包门禁 |
| `tests/architecture/test_business_legacy_absence_ledger.py` | 更新 carrier 集合，保留 ledger/matrix 一致性 |
| `tests/contracts/test_business_legacy_matrix_closure.py` | 删除 Phase 5 插件 carrier 断言，保留后续业务清理闭包 |
| `tests/architecture/test_business_contract_no_cycle_guardrail.py` | `RETAIN`；`ng_return_item_service` 仅为违规示例字面量 |
| `tests/deployment/test_docker_compose_mock_urls.py` | `RETAIN`；sandbox bridge 仅为镜像缺席断言 |
| `tests/mock/test_mock_dockerfile.py` | `RETAIN`；sandbox bridge 仅为镜像缺席断言 |

第 4.3 节删除文件的直接测试引用已经逐文件列在本节，不存在“其他直接测试”或按关键词批量删除的兜底项。
实施时发现新引用即视为基线漂移，不能套用 `PLUGIN_OWNED` 自动删除。

任何未列入本节、但实施时因 import 失败必须修改的测试，视为基线漂移或矩阵遗漏：停止删除、把计划状态改回
`ReviewRequired`，不得现场扩大范围。

## 6. HEAVY selector 精确调整

`docs/architecture/heavy-test-impact.toml` 必须完成以下映射，不允许以空 `heavy_tests` 掩盖风险：

- 保留 `tests/support/runtime_inbox_processing_postgresql.py` →
  `tests/integration/test_runtime_inbox_processing_postgresql.py` 映射，并把两者改为无插件通用证据 owner；
- RuntimeInbox bridge、context、validation、writeback 和 session 模型映射到：
  `tests/integration/test_runtime_inbox_claim_repository.py`、
  `tests/integration/test_runtime_inbox_service_internal_events.py`、
  `tests/integration/test_wms_event_runtime_inbox_idempotency.py`、
  `tests/integration/test_runtime_inbox_processing_postgresql.py`、
  `tests/resilience/test_runtime_inbox_failure_state_machine.py`；
- `execution_session.py`、`execution_work_item.py`、`models/session.py` 继续运行
  `tests/integration/test_runtime_remaining_entities.py`；
- 新生成的 schema cleanup revision、上述三个模型和 `workline.py` 共同映射到
  `tests/integration/test_workline_plugin_schema_retirement.py`；
- `models/session.py` 继续额外运行
  `tests/integration/test_external_http_transport_attempt_postgresql.py`，但该测试只作为共享模型回归，不证明 Phase 5；
- `src/app/runtime/orchestration/effect_bridges.py` 保持现有三项映射：
  `tests/integration/test_effect_contract_fresh_import.py`、
  `tests/integration/test_runtime_intent_log_effect_repository.py`、
  `tests/integration/workline_capabilities/test_effect_reducer_postgresql.py`；
- `src/app/runtime/orchestration/execution_work_item.py` 保持
  `tests/resilience/test_runtime_inbox_crash_recovery_postgresql.py` 映射；
- `src/app/wms_integration/ports/document_operations.py` 与
  `scripts/verify_wms_northbound_feasibility.py` 保持
  `tests/integration/test_wms_northbound_feasibility_probe.py` 映射；
- `src/app/runtime/orchestration/services/wms_fulfillment_domain_projector.py` 新增到
  `tests/integration/workline_capabilities/test_effect_reducer_postgresql.py`、
  `tests/integration/workline_capabilities/test_wms_effect_status_postgresql.py` 的映射；直接 FAST owner 为
  `tests/workline_runtime/system_capabilities/test_wms_fulfillment_domain_projection_hooks.py`；
- 所有删除文件对应 mapping 一并删除，不保留指向不存在测试的条目。

selector 若返回本节之外的 HEAVY 路径，必须先解释真实影响并更新本计划/矩阵；不得静默跳过。

## 7. 文档、配置、数据与归档清单

### 7.1 当前态文档

代码退出门禁通过后更新以下当前真源的实施状态、测试处置和链接：

- `docs/superpowers/plans/2026-08-03-wes-architecture-convergence-master-plan.md`
- `docs/superpowers/plans/2026-07-31-wes-test-semantics-and-weight-convergence.md`
- 本计划的执行证据段落
- `README.md`、`docs/superpowers/README.md`：删除已归档迁移盘点文档入口

不得把执行日志、失败过程或临时决策另写成新的当前态设计文档。

### 7.2 归档

**冻结归档清单：恰好 1 项。**

| 项目内源路径 | 项目外目标路径 | 基线检查 |
| --- | --- | --- |
| `docs/operations/workline-plugin-migration-inventory.md` | `../archive_docs/wes_backend/docs/operations/workline-plugin-migration-inventory.md` | 目标不存在；保留原文件名和完整内容 |

归档时先创建目标父目录、再次确认目标不存在，再移动原文件；项目内只留下 Git 删除记录，不保留转发页、副本或软链接。
`README.md` 和 `docs/superpowers/README.md` 的当前态入口必须同步删除。

若实施基线出现新的过期文档候选，停止实施并回到评审；不得在本批准范围内临时扩充归档清单。

### 7.3 明确无变更项

- 不修改 `docs/hardware/`；
- 只新增第 4.5 节冻结的 schema cleanup revision；不新增数据迁移、回填、兼容读取或 downgrade；
- 不新增插件 workspace、SDK、模板、镜像安装项或 Celery queue；
- 不把 Phase 4 Transport 接入任何旧/空 consumer；
- 不创建根目录 `workline_plugins/` 空包或只有测试的插件包。

## 8. TDD 实施顺序与提交边界

为避免产生一个可提交但架构无效的“空 registry/空 binding”中间态，旧平台切除必须是单一原子代码提交。允许的提交边界如下，
不得把“先删生产代码、后修测试”拆成两个提交。

### Commit 1：承接通用可靠性测试

**Commit：** `test(runtime): 承接零插件核心可靠性不变量`

**Files：**

- 修改第 5.1 节列出的现有 successor 测试；
- 先让 `tests/integration/test_runtime_inbox_processing_postgresql.py` 的通用用例不依赖具体插件 fixture；
- `tests/integration/test_workline_plugin_schema_retirement.py` 属于 Commit 2 的 schema TDD，不进入 Commit 1；
- 不删除旧测试，不修改生产代码。

**通过标准：** 新旧测试可同时通过；新增用例不包含具体插件 key、fixture、handler 或业务流程。

### Commit 2：原子退役旧插件执行闭包

**Commit：** `refactor(runtime): 原子退役旧工作线插件执行闭包`

**Files：** 第 4 节全部生产代码、脚本、数据、export、schema cleanup revision 和机器可读配置；第 5.2/5.3 节全部测试处置；
`docs/architecture/heavy-test-impact.toml`。

**顺序：** 先在工作区新增 schema 缺席测试并确认红灯 → 最小修改通用 owner → 删除具体业务闭包 →
删除 binding/dispatcher/package → 生成 schema cleanup revision → 清理 exports/生成索引/fixtures →
使用临时空 PostgreSQL 验证完整升级链 → 运行分层验证 → 一次提交。

**通过标准：** 提交自身可启动、可收集、FAST/QUALITY/受影响 HEAVY 全绿；不存在空 registry 或临时 shim。

### Commit 3：记录 Phase 5 退出证据

**Commit：** `docs(architecture): 记录 Phase 5 零插件基线`

**Files：** 第 7.1 节当前态文档；归档
`docs/operations/workline-plugin-migration-inventory.md` 到第 7.2 节唯一项目外目标，并删除两个当前态入口。

**通过标准：** 只记录已经获得的命令、结果和提交 SHA；纯文档提交只运行文档相称验证，不新增或修改 pytest。

每个删除测试的 Commit/PR 描述必须逐类写明：

```text
DELETE → NONE (PLUGIN_OWNED): <paths>
DELETE → NONE (LEGACY_PLATFORM): <paths>
DELETE → <successor test path>: <paths>
```

## 9. 精确验证命令

所有命令在独立实施 worktree 根目录执行，并使用 `rtk` 与 `uv run`。

### 9.1 基线与差异冻结

```bash
rtk git rev-parse HEAD
rtk git status --short
rtk git diff --name-status da8c107385fb64da86c02fddda10429e5d260299...HEAD
```

开始 Commit 1 前，除本计划及同批架构文档外不得有代码、测试、迁移或配置差异。

### 9.2 FAST：受影响域

```bash
rtk uv run pytest \
  tests/architecture/test_core_plugin_test_ownership_guardrail.py \
  tests/architecture/test_runtime_inbox_processor_ownership.py \
  tests/architecture/test_runtime_inbox_repository_consumer_guardrail.py \
  tests/architecture/test_runtime_inbox_service_ownership_guardrail.py \
  tests/architecture/test_runtime_inbox_state_machine_guardrail.py \
  tests/api/test_workline_routes.py \
  tests/callback/test_external_runtime_inbox_persistence_flow.py \
  tests/contracts/test_business_legacy_matrix_closure.py \
  tests/contracts/wms_integration/test_effect_status_contract.py \
  tests/contracts/wms_integration/test_provider_conformance_runner_cli.py \
  tests/contracts/wms_integration/test_wms_operation_catalog.py \
  tests/contracts/workline/test_runtime_session_advance_contract.py \
  tests/contracts/workline/test_runtime_snapshot_contract.py \
  tests/deployment/test_celery_task_runtime_contract.py \
  tests/device/test_device_context_service.py \
  tests/reconciliation/test_reconciliation_manager_contract.py \
  tests/runtime/orchestration/test_runtime_inbox_three_stage_processor.py \
  tests/runtime/orchestration/test_runtime_inbox_timer_reconciliation_flow.py \
  tests/runtime/orchestration/test_runtime_inbox_service_5state_claim.py \
  tests/runtime/orchestration/test_runtime_inbox_replay_source_validation.py \
  tests/sys/test_wms_async_effect_dispatch.py \
  tests/unit/runtime/orchestration/test_execution_correlation_key.py \
  tests/workline/test_workline_service_projection.py \
  tests/workline_runtime/system_capabilities/test_system_capability_effect_service.py \
  tests/workline_runtime/system_capabilities/test_wms_effect_status_service.py \
  tests/workline_runtime/system_capabilities/test_wms_fulfillment_domain_projection_hooks.py \
  tests/workline_runtime/test_effect_apply_state.py \
  tests/workline_runtime/test_effect_reducer.py \
  tests/workline_runtime/test_runtime_diagnostics_contract.py \
  tests/workline_runtime/test_runtime_intent_effect_applier.py \
  tests/workline_runtime/test_runtime_type_boundary_regressions.py \
  tests/workline_runtime/test_workline_session_repository_versioning.py -q

rtk uv run pytest tests/architecture -q
rtk uv run pytest tests/scripts -q
```

### 9.3 核心 FAST 与 QUALITY

```bash
rtk uv run pytest --collect-only -q -o addopts=''
rtk uv run pytest -q --junitxml=reports/fast-tests.xml
rtk uv run python scripts/check_fast_test_budget.py reports/fast-tests.xml
rtk ./scripts/git-quality-gate.sh --profile quality
rtk uv run ruff format --check src tests scripts
rtk uv run ruff check src tests scripts
rtk uv run bandit -r src/
```

### 9.4 HEAVY：精确运行集

```bash
rtk uv run scripts/select_heavy_tests.py --scope staged
rtk uv run pytest \
  tests/integration/test_command_result_correlation_authority.py \
  tests/integration/test_effect_contract_fresh_import.py \
  tests/integration/test_external_http_transport_attempt_postgresql.py \
  tests/integration/test_runtime_inbox_claim_repository.py \
  tests/integration/test_runtime_inbox_consumer_service.py \
  tests/integration/test_runtime_inbox_migration_postgresql.py \
  tests/integration/test_runtime_inbox_processing_postgresql.py \
  tests/integration/test_runtime_inbox_repository_consumers.py \
  tests/integration/test_runtime_inbox_service_internal_events.py \
  tests/integration/test_runtime_intent_log_effect_repository.py \
  tests/integration/test_runtime_intent_log_idempotency.py \
  tests/integration/test_runtime_remaining_entities.py \
  tests/integration/test_system_outbox_repository.py \
  tests/integration/test_wms_event_runtime_inbox_idempotency.py \
  tests/integration/test_wms_northbound_feasibility_probe.py \
  tests/integration/test_workline_plugin_schema_retirement.py \
  tests/integration/workline_capabilities/test_effect_reducer_postgresql.py \
  tests/integration/workline_capabilities/test_wms_effect_status_postgresql.py \
  tests/mock/test_wms_mock_server.py \
  tests/mock/test_wms_northbound_contract.py \
  tests/resilience/test_external_http_effect_crash_matrix_postgresql.py \
  tests/resilience/test_runtime_inbox_crash_recovery_postgresql.py \
  tests/resilience/test_runtime_inbox_failure_state_machine.py -q
```

`test_external_http_transport_attempt_postgresql.py` 只证明共享 session 模型未被破坏，不能写入 Phase 5 核心能力验收结论。

### 9.5 运行态 smoke

```bash
rtk docker-compose up -d db redis
rtk uv run python -c 'from main import app; assert app is not None'
rtk uv run python -c 'from src.celery_app.app import celery_app; assert celery_app is not None'
rtk uv run pytest tests/integration/test_runtime_remaining_entities.py -q
```

随后在终端 A 启动真实 worker：

```bash
CELERY_WORKER_QUEUES=default,celery,device rtk uv run celery \
  -A src.celery_app.app:celery_app worker --loglevel=info --pool=solo --concurrency=1 \
  --queues=default,celery,device
```

在终端 B 验证 worker 已响应且旧 SMT task 未注册，完成后在终端 A 使用 `Ctrl-C` 正常退出：

```bash
rtk uv run celery -A src.celery_app.app:celery_app inspect ping --timeout=10
rtk uv run celery -A src.celery_app.app:celery_app inspect registered --timeout=10 > /tmp/phase5-registered-tasks.txt
! rtk rg -n 'smt_inbound_handoff|rough_sorter|workline_plugins' /tmp/phase5-registered-tasks.txt
```

运行态只要求零插件核心与通用 worker 可装载；不得向旧插件事件发送请求来“证明返回错误”，因为旧入口必须根本不存在。

### 9.6 缺席验证

```bash
! rtk git ls-files 'src/app/runtime/workline_plugins/**' | rtk rg .
! rtk rg -n 'from src\.app\.runtime\.workline_plugins|import src\.app\.runtime\.workline_plugins' \
  src tests scripts --glob '*.py'
! rtk rg -n 'rough_sorter|smt_sorting_inbound' src/app --glob '*.py'
! rtk rg -n 'WorklinePluginBinding|workline_plugin_bindings|active_plugin_binding' src/app --glob '*.py'
! rtk rg -n 'workline_plugins|rough_sorter|smt_sorting_inbound' \
  src/celery_app pyproject.toml Jenkinsfile.backend-ci Jenkinsfile.test-deploy \
  docker-compose.yml docker-compose.ci-heavy.yml docker-compose.deploy.yml docker-compose.frontend.yml \
  docker-compose.test-deploy.yml docker-compose.wms-acceptance.yml
rtk git diff --check
```

迁移 revision、当前真源中的历史说明和缺席门禁可以包含被删除名称；它们不属于活动生产依赖。缺席验证不得扫描
`docs/hardware/`，也不得把用于禁止回归的测试字面量误报为生产 owner。

### 9.7 纯文档 Commit 3

```bash
rtk git diff --check
rtk rg -n '状态：Approved|详细计划已批准|零插件' \
  docs/superpowers/plans/2026-08-03-wes-architecture-convergence-master-plan.md \
  docs/superpowers/plans/2026-07-31-wes-test-semantics-and-weight-convergence.md \
  docs/superpowers/plans/2026-08-10-wes-legacy-workline-plugin-execution-retirement.md
test -z "$(rtk git status --porcelain -- docs/hardware)"
test -f ../archive_docs/wes_backend/docs/operations/workline-plugin-migration-inventory.md
test ! -e docs/operations/workline-plugin-migration-inventory.md
! rtk rg -n 'docs/operations/workline-plugin-migration-inventory.md' README.md docs/superpowers/README.md
```

纯文档提交不运行 pytest，也不新增文档正文测试。

## 10. 退出门禁

Phase 5 只有同时满足以下条件才完成：

1. 第 4 节每个文件均按唯一处置落地，没有未登记的现场扩项；
2. `src/app/runtime/workline_plugins/`、binding、generated plugin index、dispatcher、attempt 和具体业务闭包缺席；
3. WorkLine API/Service/Schema 不再暴露插件 options、manifest、key、contract 或 binding；空库升级后的 ORM 与 PostgreSQL
   同时缺少旧 binding 表、plugin identity/manifest/pin/state 列及其 FK、索引和约束；
4. RuntimeInbox 通用 ingress/claim/replay/terminal 可用，但没有插件业务处理分支；
5. 通用可靠性 successor 先通过，旧测试按 `DELETE → successor` 或 `DELETE → NONE` 可审计处置；
6. FAST、QUALITY、精确 HEAVY、应用/worker/metadata smoke 和缺席验证全部通过；
7. Phase 4 Transport 未接旧插件，Phase 7 Device/ECS 未提前实现；
8. 没有 shim、alias、fallback、双路径、空插件、默认插件、no-op consumer 或旧数据迁移；
9. 唯一过程文档已经移到冻结归档目标，项目内原路径和入口缺席，`docs/hardware/` 无变更；
10. 当前态文档记录真实提交 SHA 和验证结果，总控将 Phase 5 标记为完成并把下一阶段指向 Phase 6。

任何一项不满足，都不得把本计划、总控或 PR 标记为 Phase 5 完成。

## 11. 风险与失败处理

- **通用不变量没有 successor：** 停止删除，先补第 5.1 节的最终测试 owner；不得用旧插件 happy path 顶替。
- **发现新活动调用方：** 视为矩阵失效，把状态改回 `ReviewRequired`；不得现场用 shim 解围。
- **ORM 与数据库不一致：** 停止实施，修正 schema cleanup revision 和空库升级测试；不得用“零插件暂时不写入”掩盖漂移。
- **HEAVY selector 返回额外路径：** 运行并解释真实影响，必要时更新矩阵后重新评审。
- **Celery worker 仍要求插件 registry：** 删除该装配依赖，保留通用 worker；不得注册空 handler。
- **未来插件需要旧逻辑：** 以获批业务合同和当前基础能力重新实现，禁止复制 Phase 5 删除源码。

## 12. 批准结论

复审重新核对了 `DELETE` 文件的生产反向 import，比较了 `MODIFY` 文件与 HEAVY selector 映射，并交叉检查精确验证命令和
删除测试清单。`wms_fulfillment_domain_projector.py` 是混合修改项，只删除 SMT、满箱和输送分支；此前遗漏的两个调用方保留到
Phase 6。FAST 不执行待删除文件，HEAVY 运行集包含当前映射真源要求的路径。

本计划已经冻结逐文件生产处置、测试 successor/`NONE`、删除与归档清单、FAST/QUALITY/HEAVY/运行态/缺席命令，以及三个
可独立审查的提交边界。基于冻结源码基线，Phase 5 没有已知的实施前规划阻断项，可以进入独立 worktree 按 TDD 执行。
