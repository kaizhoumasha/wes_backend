# Workline Plugin / System Capability 最小平台与粗分机切片 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 以 rough sorter 已批准的 13-case 业务合同为边界，交付唯一的 Workline Plugin / System Capability 运行合同、真实 QUERY/EFFECT 闭环、确定性静态索引和共享 conformance 门禁，并移除该切片依赖的旧 runtime capability 路由。

**Architecture:** RuntimeInbox 继续拥有 claim、lease、重试和 replay；单次 attempt 在写事务外执行受限 QUERY，再以 processor token、Session version 和 PluginState version 校验结果是否仍可提交。Plugin 只接收 typed input/context/binding/state 并返回 `PluginDecision`；所有副作用转换为 `SYSTEM_CAPABILITY` intent，由 Runtime 在短事务内执行本地领域 Service 或写入 Outbox。运行时只读取生成的两个静态索引，不扫描目录、不动态 import、不 fallback 到旧 catalog。

**Tech Stack:** Python 3.13、FastAPI、Pydantic v2、SQLModel/SQLAlchemy async、PostgreSQL、Alembic、Celery、Pytest、Ruff、Bandit、GitNexus。

---

## 1. 计划边界与交付判定

本计划承接平台设计中的 T3–T7，只交付 rough sorter `SCAN_COMPLETED → PICK_AND_PUT result → WMS admission → next command/Hold/replay evidence` 真实切片。它不是完整 Workline 迁移，也不是生产 cutover：

- 包含：最终两类 Definition、封闭 outcome、typed binding/state/input/decision、attempt-scoped QUERY、typed evidence、recorded replay、`SYSTEM_CAPABILITY` EFFECT、粗分机真实 Plugin、确定性生成索引、共享 conformance、架构门禁和本切片 PostgreSQL/E2E 验证。
- 不包含：完整 rough sorter 后续出料流程、SMT 或 migration matrix 中其他 Workline、跨环境批准与最终排空、发布切换 runbook、通用脚手架、完整诊断产品。
- 不创建：`ExtensionDefinition`、YAML 运行时 loader、N/N-1 loader、兼容 alias、feature flag 双轨、通用 precondition DSL、跨 attempt cache、全仓 UnitOfWork。
- destructive switch 前必须用现有 inventory 证明目标环境没有非 rough-sorter 活动 Session/WorkItem/Inbox/Intent/Outbox 引用；不满足时停在切换前，保留已通过测试的前置提交，但不得启用新 dispatcher 或删除旧入口。

本计划完成的必要条件：13 个 approved case 全部由生产路径实现或以合同规定的 evidence-only/no-op 行为闭环；不得继续保留 `gap` / `partial`；同一 rough sorter 输入不得存在两个可执行 dispatcher。

## 2. 已确认基线与风险

### 2.1 当前事实

- T1 单环境 inventory foundation 已合并；跨环境聚合、WorkItem/Intent version 引用和最终 preflight 仍不属于本计划。
- T2 rough sorter 合同、13-case fixture 和业务批准已合并到 `develop`。
- `src/app/runtime/capability_catalog.py` 同时拥有 Workline identity、YAML manifest、context/parser 和 NG reason；19 个生产/测试入口直接 import 它。
- `src/app/runtime/capability_dispatcher.py` 与 `src/app/runtime/runtime_capability_catalog.py` 只提供手写 catalog、provider admission 和 `SorterInboundRuntimeService` 单例路由，尚无 typed outcome、attempt scope 或 evidence。
- `WorklineSession.context_json` 是共享业务上下文；历史字符串 `plugin_state` 已被迁移删除。新状态必须使用新的 typed JSON snapshot 字段，禁止恢复旧字符串状态或把控制状态塞回 `context_json`。
- RuntimeInbox 已有 `processor_token`、lease、payload hash 与三阶段 processor；WorklineSession/ExecutionSession/ExecutionWorkItem 尚未完整固定 binding/index/plugin state 版本。
- Timeline 已有 `DECISION_MADE`，优先承载 typed QUERY/decision evidence；本切片不新增 evidence 表。

### 2.2 GitNexus 基线

| Symbol | 当前结果 | 执行要求 |
| --- | --- | --- |
| `RuntimeCapabilityDefinition` | LOW；2 个直接 import，三层内 15 个受影响文件 | 修改前重新 impact；覆盖 catalog、inventory、orchestrator 与关联测试 |
| `WorklineCapabilityDefinition` | LOW，但结果标记 `partial` | 不以 LOW 作为安全结论；结合 `rg` 的全部 import 清单逐项迁移 |
| `RuntimeCapabilityDispatcher` | LOW，但结果标记 `partial` | 修改 `dispatch` 前重新 impact，并验证 RuntimeInbox 三阶段路径 |
| `get_workline_capability_definition` | LOW，但结果标记 `partial` | 删除前以文本零引用和 architecture test 双重证明 |
| `RuntimeIntent` | 多个 runtime、service 和测试 import | 新增 `SYSTEM_CAPABILITY` 必须保持既有 intent 行为回归 |
| `RuntimeIntentEffectApplier` | 5 个直接 import；类体约 1300 行 | 只增加通用分支并把新协调逻辑放入独立 service，不继续扩大业务分支 |

任何执行时 impact 为 HIGH/CRITICAL，必须在改文件前向用户报告 direct callers、affected processes/modules 并获得确认。

## 3. 文件结构与职责锁定

### 3.1 Runtime 合同与生成索引

| Path | 责任 |
| --- | --- |
| `src/app/runtime/extension_identity.py` | 只提供 key/version、canonical JSON、SHA-256 和确定性排序工具；不定义第三类 Extension |
| `src/app/runtime/workline_plugins/definition.py` | `WorklinePluginDefinition`、handler route、声明能力、config/state/input 类型校验 |
| `src/app/runtime/workline_plugins/contracts.py` | `PluginContext[TState]`、`PluginDecision[TState]`、decision validation 与 attempt snapshot |
| `src/app/runtime/workline_plugins/generated_index.py` | 生成的 Workline Plugin key/version → Definition 静态映射与 digest |
| `src/app/runtime/workline_plugins/index_builder.py` | 仅构建期发现/校验 Definition 并渲染确定性索引 |
| `src/app/runtime/system_capabilities/definition.py` | `SystemCapabilityDefinition`、QUERY/EFFECT mode、completion mode、required Ports/admission/deadline/audit |
| `src/app/runtime/system_capabilities/outcomes.py` | `Success[T]`、`BusinessReject`、`RetryableFailure`、`ContractViolation` 封闭 outcome |
| `src/app/runtime/system_capabilities/generated_index.py` | 生成的 System Capability key/version → Definition 静态映射与 digest |
| `src/app/runtime/system_capabilities/index_builder.py` | 仅构建期发现/校验 Definition 并渲染确定性索引 |
| `scripts/generate_runtime_extensions.py` | 单一 CLI 协调两个 Builder，支持 write 与 `--check`，输出稳定退出码 |

### 3.2 Binding、attempt 与 evidence

| Path | 责任 |
| --- | --- |
| `src/app/workline/models/plugin_binding.py` | 不可变 `WorklinePluginBinding` 版本行；保存 typed config JSON、hash、provider/Port snapshot、index digest 和激活证据 |
| `src/app/workline/repositories/plugin_binding_repository.py` | binding 版本查询、当前激活版本读取和同一 Workline 串行版本分配 |
| `src/app/workline/services/plugin_binding_service.py` | Definition 驱动的 typed validation、provider/Port admission、hash、激活事务 |
| `src/app/contracts/external_contract_profile_catalog.py` | 静态 provider profile key/version 目录；供 binding activation/admission 使用，不保存 Session-bound instance |
| `src/app/runtime/workline_plugins/attempt_coordinator.py` | claim snapshot、无写事务 QUERY、写回前 token/version 校验和 stale discard |
| `src/app/runtime/system_capabilities/gateway.py` | attempt-scoped QUERY admission、deadline、in-flight coalescing、数量/字节上限、outcome mapping |
| `src/app/runtime/system_capabilities/evidence.py` | typed evidence envelope、脱敏、hash/size validation、timeline payload codec |
| `src/app/runtime/system_capabilities/replay.py` | recorded QUERY/decision evidence 装载；确定性 replay 禁止 provider 调用 |

### 3.3 Rough sorter 真实 slice

| Path | 责任 |
| --- | --- |
| `src/app/runtime/workline_plugins/rough_sorter/config.py` | 设备角色、pipeline/NG location、warehouse/owner/provider profile 的 typed config |
| `src/app/runtime/workline_plugins/rough_sorter/state.py` | 仅保存无法从领域事实推导的局部 phase、measurement/WMS evidence 引用和当前 command correlation |
| `src/app/runtime/workline_plugins/rough_sorter/inputs.py` | `SCAN_COMPLETED`、`PICK_AND_PUT` result、business timeout 的 logical typed input |
| `src/app/runtime/workline_plugins/rough_sorter/handlers.py` | 13-case 业务判定；不持久化、不 import Repository/SQLAlchemy/HTTP/Celery/provider DTO |
| `src/app/runtime/workline_plugins/rough_sorter/definition.py` | 唯一作者态 identity、route、config/state/input model、allowed capabilities 与纯解析器 |
| `src/app/runtime/system_capabilities/wms/rough_sorter_inventory_admission/contracts.py` | WMS admission typed input/output；输入含业务键、HHPN/LotCode、有效测量摘要和 binding snapshot |
| `src/app/runtime/system_capabilities/wms/rough_sorter_inventory_admission/handler.py` | 通过 `WmsInventoryQueryPort` 执行一次原子只读查询并返回封闭 outcome |
| `src/app/runtime/system_capabilities/wms/rough_sorter_inventory_admission/definition.py` | QUERY Definition、Port/profile 要求、deadline、audit/redaction policy |
| `src/app/runtime/system_capabilities/material_flow/material_unit_write/` | 创建 MaterialUnit、更新状态/NG 的 LOCAL_TRANSACTIONAL typed capability |
| `src/app/runtime/system_capabilities/device/device_command_write/` | 持久化 DeviceCommand 与 Outbox 的 OUTBOX_ASYNC typed capability |
| `src/app/runtime/system_capabilities/runtime/session_hold/` | 普通 Session Hold 的 LOCAL_TRANSACTIONAL typed capability；不替代 TIMER reconciliation 的 RuntimeHold owner |

每个 capability 目录固定包含 `contracts.py`、`handler.py`、`definition.py`、`__init__.py`，只实现本切片用到的动作，不创建通用 CRUD capability。

### 3.4 Runtime 接入与迁移

| Path | 责任 |
| --- | --- |
| `src/app/runtime/workline_plugins/dispatcher.py` | generated index 路由、typed input/state/context 装配和 `PluginDecision` 校验 |
| `src/app/runtime/orchestration/runtime_intent.py` | 新增唯一通用 `SYSTEM_CAPABILITY` kind 与严格 factory 字段 |
| `src/app/runtime/orchestration/services/intent/system_capability_intent_service.py` | 派生最终幂等 key、payload hash、snapshot/admission 检查和 capability 调用准备 |
| `src/app/runtime/orchestration/services/intent/system_capability_effect_service.py` | Runtime 事务 owner；LOCAL_TRANSACTIONAL 调领域 Service，OUTBOX_ASYNC 只写 Outbox |
| `src/app/runtime/orchestration/services/material_unit_mutation_service.py` | MaterialUnit 条件创建/状态更新的事务参与型 Service；只 flush，不 commit |
| `src/app/runtime/orchestration/services/session_hold_mutation_service.py` | 普通 WorklineSession Hold 的事务参与型 Service；不创建 RuntimeHold，不 commit |
| `src/app/device/services/device_command_service.py` | 增加由外层 Runtime 事务调用的命令/Outbox 原子准备入口；保留 API 自有事务入口 |
| `src/app/runtime/orchestration/runtime_intent_effects.py` | 只分派 `SYSTEM_CAPABILITY` 到独立 service；不得出现 rough sorter key/action/event 分支 |
| `src/app/runtime/orchestration/services/runtime_inbox/runtime_inbox_processor_service.py` | 把 Plugin attempt 的 claim/query/write 三阶段纳入现有 processor 生命周期 |
| `src/app/runtime/orchestration/services/runtime_inbox/runtime_inbox_writeback_service.py` | 原子写 evidence、PluginState、intents 和 inbox terminal；保留 late/duplicate evidence-only 语义 |
| `src/app/runtime/normalization/normalizers/input_normalizer.py` | raw kind → logical typed route；不新增 raw `SYSTEM_CAPABILITY_RESULT` kind |

### 3.5 数据模型与迁移

修改：

- `src/app/workline/models/workline.py`：`config` 只作为编辑草稿；激活后以不可变 binding 为运行时真源，manifest view 由 Definition schema + binding 组合。
- `src/app/runtime/orchestration/models/session.py`：增加 `binding_id`、`binding_version`、`binding_hash`、`extension_index_digest`、`plugin_state_json`、`plugin_state_schema_version`、`plugin_state_version`。
- `src/app/runtime/orchestration/execution_session.py`：固定 `plugin_key`、`plugin_contract_version`、binding identity 与 index digest；停止使用含糊的 `manifest_version` 作为新会话作者态术语。
- `src/app/runtime/orchestration/execution_work_item.py`：固定实际执行的 Plugin key/version、binding hash 和 index digest。
- `src/app/runtime/orchestration/runtime_intent_log.py`：固定 Plugin/Capability key/version、operation identity、policy/binding/provider snapshot、payload hash 和 completion mode。
- 通过 `uv run alembic revision -m "add workline plugin runtime binding"` 生成迁移文件，再编辑生成文件；禁止手写 revision ID。

### 3.6 测试归属

| Path | 覆盖 |
| --- | --- |
| `tests/workline_runtime/extensions/` | Definition、Builder、Gateway、evidence、attempt、effect service 纯逻辑 |
| `tests/workline_plugins/rough_sorter/` | config/state/input/handler 与 13-case 参数化测试 |
| `tests/contracts/system_capabilities/` | outcome、QUERY/EFFECT、idempotency、completion、provider/Port 合同 |
| `tests/contracts/workline/` | binding/version/replay 与 approved fixture 对生产 handler 的映射 |
| `tests/architecture/test_runtime_extension_platform_guardrail.py` | 禁止依赖、runtime scan/import、双轨 catalog、核心业务分支、生成漂移 |
| `tests/integration/workline_capabilities/` | PostgreSQL attempt/transaction/outbox/result/replay |
| `tests/e2e/workline_capabilities/` | 13-case 真实闭环与 provider-call/effect-count evidence |

## 4. 稳定合同命名

后续任务必须使用以下名字，避免计划内部类型漂移：

| 概念 | 名称与稳定字段 |
| --- | --- |
| Plugin identity | `WorklinePluginDefinition(plugin_key, contract_version, config_model, state_model, routes, allowed_capabilities, parsers)` |
| Capability identity | `SystemCapabilityDefinition(capability_key, contract_version, mode, input_model, output_model, handler_factory, required_ports, admission, timeout_seconds, completion_mode, audit_policy)` |
| Capability mode | `SystemCapabilityMode.QUERY` / `SystemCapabilityMode.EFFECT` |
| Effect completion | `EffectCompletionMode.LOCAL_TRANSACTIONAL` / `EffectCompletionMode.OUTBOX_ASYNC` |
| Outcomes | `Success[T]`、`BusinessReject`、`RetryableFailure`、`ContractViolation` |
| Plugin result | `PluginDecision[TState](intents, next_state, outcome_code)` |
| Effect intent | `RuntimeIntentKind.SYSTEM_CAPABILITY`；字段通过 factory 固定为 capability key/version、operation key、typed payload、precondition、timeout |
| Replay | `RecordedDecisionReplay`；只装载 recorded input/query/decision evidence，不调用 Gateway provider path |
| Rough sorter QUERY | `wms.rough_sorter_inventory_admission@v1` |
| Rough sorter EFFECT | `material_flow.material_unit_write@v1`、`device.device_command_write@v1`、`runtime.session_hold@v1` |

## 5. 执行前置条件

- [ ] 从最新 `develop` 创建 `codex/feature/workline-capability-minimum-slice` 隔离 worktree，使用 `superpowers:using-git-worktrees`；运行 `./scripts/init-env.sh dev`、`uv sync --dev`、`./scripts/install-git-hooks.sh`。
- [ ] 保存并避开主工作区现有 `AGENTS.md`、`CLAUDE.md` 修改；禁止复制或覆盖用户未提交变更。
- [ ] 运行 `npx gitnexus analyze`，确认 index 与 worktree HEAD 一致。
- [ ] 对本计划每个 Task 的 Symbols 逐一执行 `gitnexus_impact(direction="upstream")`；HIGH/CRITICAL 先报告并确认。
- [ ] 记录基线：

  `uv run pytest tests/architecture/test_test_suite_topology_guardrail.py tests/contracts/workline/test_rough_sorter_scan_decision_spec.py tests/workline_runtime/test_runtime_capability_dispatcher.py tests/workline_runtime/test_runtime_intent_effect_applier.py -q`

  Expected: 全部通过；已有失败先使用 `superpowers:systematic-debugging` 定位，不混入本计划。

- [ ] 运行现有 inventory CLI 生成目标环境报告并保存 digest。destructive switch gate 要求所有非 rough-sorter 活动运行引用为零；不满足时不得执行 Task 10。

### Task 1: 锁定最终 Definition 与 outcome 合同

**Symbols:** `RuntimeCapabilityDefinition`、`WorklineCapabilityDefinition`。

**Files:**

- Create: `tests/contracts/system_capabilities/test_definition_contract.py`
- Create: `tests/contracts/workline/test_plugin_definition_contract.py`
- Create: `tests/workline_runtime/extensions/test_outcomes.py`
- Create: `src/app/runtime/extension_identity.py`
- Create: `src/app/runtime/system_capabilities/definition.py`
- Create: `src/app/runtime/system_capabilities/outcomes.py`
- Create: `src/app/runtime/system_capabilities/__init__.py`
- Create: `src/app/runtime/workline_plugins/definition.py`
- Create: `src/app/runtime/workline_plugins/contracts.py`
- Create: `src/app/runtime/workline_plugins/__init__.py`

- [ ] **Step 1: 重新运行 impact 并记录 blast radius**

对两个 Symbols 分别运行 upstream impact；把 direct callers、affected processes/modules 与风险写入本 Task 执行记录。HIGH/CRITICAL 时停止。

- [ ] **Step 2: 写 Definition 失败测试**

锁定非空 key/version、闭合 mode/completion、Pydantic input/output 类型、handler factory（不得保存实例）、required Ports、route 唯一、Plugin allowed capability key/version、config/state model、不可变声明和稳定 identity。明确断言不存在公共 `ExtensionDefinition`。

- [ ] **Step 3: 写四类 outcome 失败测试**

参数化验证 discriminant、typed payload、稳定 reason/error code、retryable 语义和 JSON round-trip；未知第五种 outcome 必须 `ContractViolation`，不得用异常表达业务拒绝。

- [ ] **Step 4: 运行并确认合同尚不存在而失败**

Run: `uv run pytest tests/contracts/system_capabilities/test_definition_contract.py tests/contracts/workline/test_plugin_definition_contract.py tests/workline_runtime/extensions/test_outcomes.py -q`

Expected: import/contract assertion FAIL；不得 skip/xfail。

- [ ] **Step 5: 实现最小合同类型**

只实现第 4 节命名的类型和校验；`extension_identity.py` 仅包含 canonical JSON、digest、key/version validation、stable sort。`PluginDecision` 验证 intent 数量上限和 next_state 类型，不执行 intent。

- [ ] **Step 6: 运行合同测试并通过**

Run: `uv run pytest tests/contracts/system_capabilities/test_definition_contract.py tests/contracts/workline/test_plugin_definition_contract.py tests/workline_runtime/extensions/test_outcomes.py -q`

Expected: PASS。

- [ ] **Step 7: 提交合同类型**

```bash
git add src/app/runtime/extension_identity.py src/app/runtime/system_capabilities src/app/runtime/workline_plugins tests/contracts/system_capabilities/test_definition_contract.py tests/contracts/workline/test_plugin_definition_contract.py tests/workline_runtime/extensions/test_outcomes.py
git commit -m "feat(runtime): 定义插件与系统能力最终合同"
```

### Task 2: 建立确定性双索引生成器

**Symbols:** 新建 Builder；修改前无需既有 symbol impact，删除旧 catalog 留到 Task 10。

**Files:**

- Create: `src/app/runtime/workline_plugins/index_builder.py`
- Create: `src/app/runtime/workline_plugins/generated_index.py`
- Create: `src/app/runtime/system_capabilities/index_builder.py`
- Create: `src/app/runtime/system_capabilities/generated_index.py`
- Create: `scripts/generate_runtime_extensions.py`
- Create: `tests/workline_runtime/extensions/test_runtime_extension_index_generation.py`

- [ ] **Step 1: 写 Builder/CLI 失败测试**

覆盖稳定排序、重复 identity/route、目录名与 Definition key 不一致、未知 capability reference、QUERY/EFFECT 使用不匹配、handler 签名、未知 Port/profile、原子写、`--check` drift、cold-start import。构建期可发现 Definition 文件；生成文件和生产 runtime 禁止目录扫描及字符串 import。

- [ ] **Step 2: 运行并确认生成器尚不存在而失败**

Run: `uv run pytest tests/workline_runtime/extensions/test_runtime_extension_index_generation.py -q`

Expected: import FAIL。

- [ ] **Step 3: 实现两个独立 Builder 与单一 CLI**

Builder 只共享 identity/digest/atomic-write helper；各自校验自身 Definition。生成文件包含显式 import、只读 mapping、排序后的 identity tuple 和 digest，不包含扫描逻辑或对象实例。

- [ ] **Step 4: 生成初始空索引并验证幂等**

Run: `uv run python scripts/generate_runtime_extensions.py`

Expected: 两个 generated index 写入；第二次运行无 diff。

- [ ] **Step 5: 验证 check 与 cold start**

Run: `uv run python scripts/generate_runtime_extensions.py --check`

Expected: exit 0 且输出两个索引的 identity count/digest；人为临时 drift 的测试必须验证 exit 非 0，测试自行恢复临时文件。

- [ ] **Step 6: 运行测试并通过**

Run: `uv run pytest tests/workline_runtime/extensions/test_runtime_extension_index_generation.py -q`

Expected: PASS。

- [ ] **Step 7: 提交生成器**

```bash
git add src/app/runtime/workline_plugins/index_builder.py src/app/runtime/workline_plugins/generated_index.py src/app/runtime/system_capabilities/index_builder.py src/app/runtime/system_capabilities/generated_index.py scripts/generate_runtime_extensions.py tests/workline_runtime/extensions/test_runtime_extension_index_generation.py
git commit -m "feat(runtime): 生成确定性扩展静态索引"
```

### Task 3: 持久化 immutable binding 与版本 pin

**Symbols:** `WorkLine`、`WorklineSessionBase`、`ExecutionSession`、`ExecutionWorkItem`、`WorkLineService.activate`、`WorklineMigrationInventoryService`。

**Files:**

- Create: `src/app/workline/models/plugin_binding.py`
- Create: `src/app/workline/repositories/plugin_binding_repository.py`
- Create: `src/app/workline/services/plugin_binding_service.py`
- Create: `src/app/contracts/external_contract_profile_catalog.py`
- Modify: `src/app/workline/models/__init__.py`
- Modify: `src/app/workline/repositories/__init__.py`
- Modify: `src/app/workline/services/__init__.py`
- Modify: `src/app/workline/models/workline.py`
- Modify: `src/app/workline/services/workline_service.py`
- Modify: `src/app/workline/services/migration_inventory_service.py`
- Modify: `src/app/workline/models/migration_inventory.py`
- Modify: `src/app/runtime/orchestration/models/session.py`
- Modify: `src/app/runtime/orchestration/execution_session.py`
- Modify: `src/app/runtime/orchestration/execution_work_item.py`
- Create via Alembic generator: `migrations/versions/*_add_workline_plugin_runtime_binding.py`
- Create: `tests/workline_runtime/extensions/test_plugin_binding_service.py`
- Modify: `tests/workline_runtime/test_workline_migration_inventory_models.py`
- Modify: `tests/workline_runtime/test_workline_migration_inventory_service.py`
- Modify: `tests/integration/test_workline_migration_inventory_postgresql.py`

- [ ] **Step 1: 逐个运行 impact**

特别记录 `WorkLineService.activate`、Session/WorkItem 模型和 inventory digest 的直接依赖；HIGH/CRITICAL 停止。

- [ ] **Step 2: 写 immutable binding 失败测试**

锁定 `(workline_id, plugin_key, contract_version, binding_version)` 唯一、typed config canonical hash、provider/Port snapshot、generated index digest、activated_at/actor/reason。激活新版本不更新旧行；相同配置可复用同 hash 但仍保持明确版本语义。

- [ ] **Step 3: 写 activation 与 pin 失败测试**

覆盖 config validation、missing device/provider/Port fail closed、WorkLine 乐观锁、Session/ExecutionSession/WorkItem 固定同一 binding/version/hash/digest，以及 binding 被停用后既有 retry 仍按 pinned row 读取且执行时重新 admission。

- [ ] **Step 4: 运行并确认模型/字段缺失而失败**

Run: `uv run pytest tests/workline_runtime/extensions/test_plugin_binding_service.py tests/workline_runtime/test_workline_migration_inventory_models.py tests/workline_runtime/test_workline_migration_inventory_service.py -q`

Expected: FAIL。

- [ ] **Step 5: 实现 Model → Repository → Service 分层**

Binding Service 组合 generated Definition、现有 device/topology query 与 `external_contract_profile_catalog.py`；Repository 只做数据访问。WorkLine `config` 保留编辑草稿，运行时只读 active immutable binding。binding 还保存 `is_enabled`、撤权/停用证据和 provider profile snapshot；每次执行重新检查撤权、有效期、环境 admission 与 kill switch。新增 Service 必须在 `services/__init__.py` 导出。

- [ ] **Step 6: 生成并编辑 Alembic migration**

Run: `uv run alembic revision -m "add workline plugin runtime binding"`

Expected: Alembic 自动生成随机 revision。迁移创建 binding 表和 pin/state 字段；`plugin_state_json` 为 JSON snapshot，不能恢复历史字符串 `plugin_state`。downgrade 精确删除本 revision 新增对象。

- [ ] **Step 7: 扩展 inventory contract**

报告增加 WorkItem/Intent 的 plugin/binding/index 引用和逐 Workline provider/Port requirement；digest 纳入新字段，输出仍确定且只读。foundation readiness 不冒充跨环境批准。

- [ ] **Step 8: 运行 unit 与 PostgreSQL migration/inventory 测试**

Run: `uv run pytest tests/workline_runtime/extensions/test_plugin_binding_service.py tests/workline_runtime/test_workline_migration_inventory_models.py tests/workline_runtime/test_workline_migration_inventory_service.py -q`

Run: `uv run pytest tests/integration/test_workline_migration_inventory_postgresql.py -q -o addopts=''`

Run: `uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head`

Expected: 全部 PASS；downgrade 后再 upgrade 成功。

- [ ] **Step 9: 提交 binding 与 migration**

```bash
git add src/app/workline src/app/contracts/external_contract_profile_catalog.py src/app/runtime/orchestration/models/session.py src/app/runtime/orchestration/execution_session.py src/app/runtime/orchestration/execution_work_item.py migrations/versions tests/workline_runtime/extensions/test_plugin_binding_service.py tests/workline_runtime/test_workline_migration_inventory_models.py tests/workline_runtime/test_workline_migration_inventory_service.py tests/integration/test_workline_migration_inventory_postgresql.py
git commit -m "feat(workline): 固定插件绑定与运行版本"
```

### Task 4: 实现 attempt-scoped QUERY、evidence 与 recorded replay

**Symbols:** `RuntimeInboxProcessorService`、`RuntimeInboxWriteBackService`、`WorklineTimeline`、`RuntimeCapabilityContext`。

**Files:**

- Create: `src/app/runtime/system_capabilities/gateway.py`
- Create: `src/app/runtime/system_capabilities/evidence.py`
- Create: `src/app/runtime/system_capabilities/replay.py`
- Create: `src/app/runtime/workline_plugins/attempt_coordinator.py`
- Modify: `src/app/runtime/capability_port_registry.py`
- Modify: `src/app/runtime/orchestration/services/runtime_inbox/runtime_inbox_processor_service.py`
- Modify: `src/app/runtime/orchestration/services/runtime_inbox/runtime_inbox_writeback_service.py`
- Create: `tests/workline_runtime/extensions/test_system_capability_gateway.py`
- Create: `tests/workline_runtime/extensions/test_plugin_attempt_coordinator.py`
- Create: `tests/contracts/system_capabilities/test_query_evidence_contract.py`
- Modify: `tests/runtime/orchestration/test_runtime_inbox_three_stage_processor.py`

- [ ] **Step 1: 运行四个 Symbols 的 impact**

重点记录 processor/writeback 对 integration/resilience 流程的影响；HIGH/CRITICAL 先确认。

- [ ] **Step 2: 写 QUERY gateway 失败测试**

覆盖 declared capability、typed input、required Port/profile、deadline、四类 outcome、未知异常 → `RetryableFailure(UNKNOWN)`、同 attempt canonical key in-flight coalescing、唯一 QUERY 数、单条/总 evidence 字节限制、redaction failure fail closed。明确断言跨 attempt 不复用 cache。

- [ ] **Step 3: 写三阶段 attempt 失败测试**

锁定短事务 claim/snapshot/token → 无写事务 QUERY → 短事务 token/session version/plugin_state_version revalidate。任一变化丢弃 QUERY 结果并返回安全 retry，不写 evidence/state/intent。

- [ ] **Step 4: 写 recorded replay 失败测试**

同 key/hash replay 只解码 timeline 的 recorded evidence/decision；Gateway handler mock call count 必须为 0。缺少 pinned Definition/binding/index digest 时返回 fail-closed Hold classification，不静默升级。

- [ ] **Step 5: 运行并确认实现缺失而失败**

Run: `uv run pytest tests/workline_runtime/extensions/test_system_capability_gateway.py tests/workline_runtime/extensions/test_plugin_attempt_coordinator.py tests/contracts/system_capabilities/test_query_evidence_contract.py tests/runtime/orchestration/test_runtime_inbox_three_stage_processor.py -q`

Expected: FAIL。

- [ ] **Step 6: 实现 attempt-scoped wiring**

Gateway、Port proxy、handler factory、QUERY evidence cache 每次 Inbox attempt 新建；`CapabilityPortRegistry` 不缓存绑定 AsyncSession 的 service。evidence 使用现有 Timeline `DECISION_MADE` payload，保存 capability/version、input/output hash、authority/source、aware `evidence_at` ISO、source version、admission snapshot 和脱敏摘要。

- [ ] **Step 7: 接入三阶段 processor**

processor 不在 QUERY 阶段持有 DB 写事务、连接或行锁；writeback 在同一事务写 evidence、PluginState、intents 和 inbox terminal。保留现有 lease fencing、late/duplicate archive 和 crash recovery 语义。

- [ ] **Step 8: 运行 pure/runtime tests 并通过**

Run: `uv run pytest tests/workline_runtime/extensions/test_system_capability_gateway.py tests/workline_runtime/extensions/test_plugin_attempt_coordinator.py tests/contracts/system_capabilities/test_query_evidence_contract.py tests/runtime/orchestration/test_runtime_inbox_three_stage_processor.py -q`

Expected: PASS。

- [ ] **Step 9: 提交 QUERY 与 attempt coordinator**

```bash
git add src/app/runtime/system_capabilities src/app/runtime/workline_plugins/attempt_coordinator.py src/app/runtime/capability_port_registry.py src/app/runtime/orchestration/services/runtime_inbox tests/workline_runtime/extensions tests/contracts/system_capabilities/test_query_evidence_contract.py tests/runtime/orchestration/test_runtime_inbox_three_stage_processor.py
git commit -m "feat(runtime): 增加有界查询证据与确定性重放"
```

### Task 5: 实现 rough sorter WMS admission System Capability

**Symbols:** `WmsInventoryQueryPort`、`ExternalContractProfile`。

**Files:**

- Create: `src/app/runtime/system_capabilities/wms/__init__.py`
- Create: `src/app/runtime/system_capabilities/wms/rough_sorter_inventory_admission/__init__.py`
- Create: `src/app/runtime/system_capabilities/wms/rough_sorter_inventory_admission/contracts.py`
- Create: `src/app/runtime/system_capabilities/wms/rough_sorter_inventory_admission/handler.py`
- Create: `src/app/runtime/system_capabilities/wms/rough_sorter_inventory_admission/definition.py`
- Modify: `src/app/contracts/external_contract_profile.py`
- Modify: `src/app/contracts/external_contract_profile_catalog.py`
- Create: `tests/workline_runtime/system_capabilities/wms/test_rough_sorter_inventory_admission.py`
- Create: `tests/contracts/system_capabilities/test_wms_rough_sorter_admission_contract.py`

- [ ] **Step 1: 运行 Port/profile impact**

确认现有 WMS 7-port contract 与 provider profile consumers；不修改 Port 方法签名，若发现真实 provider 无法满足则停止并回到业务/跨系统合同评审。

- [ ] **Step 2: 写 typed capability 失败测试**

输入固定 business key、HHPN、LotCode、warehouse/owner、正数 diameter/thickness 和 binding snapshot；输出固定 accepted、matched inventory summary、source version。匹配以 `material_code + batch_no` 为准；无匹配为 `BusinessReject(WMS_REJECTED)`，timeout/unavailable 为 `RetryableFailure(WMS_TIMEOUT)`，非法 provider shape 为 `ContractViolation`。

- [ ] **Step 3: 写 Port/admission 失败测试**

handler 只通过 `WmsInventoryQueryPort.query_inventory`，不 import WMS service/model implementation、HTTP client 或 provider exception。profile catalog 必须声明准确 key/version、Port.method、environment、timeout 和 provider code；Definition 只引用 profile identity，不保存 profile instance。

- [ ] **Step 4: 运行并确认 capability 尚不存在而失败**

Run: `uv run pytest tests/workline_runtime/system_capabilities/wms/test_rough_sorter_inventory_admission.py tests/contracts/system_capabilities/test_wms_rough_sorter_admission_contract.py -q`

Expected: FAIL。

- [ ] **Step 5: 实现最小 QUERY capability**

measurement 进入 input/evidence hash，但 WMS Port 查询只使用其公开参数；匹配和输出转换在 handler 内纯计算。handler factory 接收 attempt-scoped Port，不保存全局实例。

- [ ] **Step 6: 生成索引并运行测试**

Run: `uv run python scripts/generate_runtime_extensions.py`

Run: `uv run pytest tests/workline_runtime/system_capabilities/wms/test_rough_sorter_inventory_admission.py tests/contracts/system_capabilities/test_wms_rough_sorter_admission_contract.py tests/workline_runtime/extensions/test_runtime_extension_index_generation.py -q`

Expected: PASS；System Capability index 仅新增 `wms.rough_sorter_inventory_admission@v1`。

- [ ] **Step 7: 提交 WMS QUERY capability**

```bash
git add src/app/runtime/system_capabilities/wms src/app/contracts/external_contract_profile.py src/app/contracts/external_contract_profile_catalog.py src/app/runtime/system_capabilities/generated_index.py tests/workline_runtime/system_capabilities/wms tests/contracts/system_capabilities/test_wms_rough_sorter_admission_contract.py
git commit -m "feat(workline): 增加粗分机 WMS 准入查询"
```

### Task 6: 实现 rough sorter typed Plugin 与 dispatcher

**Symbols:** `_rough_sorter_scan_completed_intents`、`_command_result_intents`、`RoughSorterContext`、`OrchestratorService.process_inbox`。

**Files:**

- Create: `src/app/runtime/workline_plugins/dispatcher.py`
- Create: `src/app/runtime/workline_plugins/rough_sorter/__init__.py`
- Create: `src/app/runtime/workline_plugins/rough_sorter/config.py`
- Create: `src/app/runtime/workline_plugins/rough_sorter/state.py`
- Create: `src/app/runtime/workline_plugins/rough_sorter/inputs.py`
- Create: `src/app/runtime/workline_plugins/rough_sorter/handlers.py`
- Create: `src/app/runtime/workline_plugins/rough_sorter/definition.py`
- Modify: `src/app/runtime/capabilities/material_flow/contracts/rough_sorter.py`
- Create: `tests/workline_plugins/rough_sorter/test_config_and_state.py`
- Create: `tests/workline_plugins/rough_sorter/test_handlers.py`
- Modify: `tests/contracts/workline/test_rough_sorter_scan_decision_spec.py`

- [ ] **Step 1: 对四个 Symbols 运行 impact**

这是本计划首次接触核心业务分支；任何 HIGH/CRITICAL 必须先报告。记录当前 direct callers 和受影响测试。

- [ ] **Step 2: 写 config/state/input 失败测试**

锁定 typed config 的设备角色、pipeline/NG location、warehouse/owner/provider profile；state 只含 phase、measurement/WMS evidence reference、current correlation，不复制 MaterialUnit/Command 权威事实。输入分别覆盖 SCAN、PICK result、business timeout。

- [ ] **Step 3: 将 approved 13 cases 参数化到 handler**

复用现有 fixture，不新增 production fixture loader。每个 case 验证 `PluginDecision` outcome、next_state、System Capability intents、QUERY call count、replay provider call count 和 zero-new-effect。缺 PkgID 必须 Hold，不能沿用当前 NG 行为。

- [ ] **Step 4: 运行并确认新 Plugin 尚不存在而失败**

Run: `uv run pytest tests/workline_plugins/rough_sorter/test_config_and_state.py tests/workline_plugins/rough_sorter/test_handlers.py tests/contracts/workline/test_rough_sorter_scan_decision_spec.py -q`

Expected: FAIL。

- [ ] **Step 5: 实现纯 handler 与 Definition**

handler 只做 input/state/fact/QUERY outcome → decision；稳定原因码完全沿用批准规格。设备失败、测量无效、WMS timeout、idempotency conflict 返回对应 Hold intent；late callback 返回 evidence-only/no intent；business timeout 输出 Runtime reconciliation request 而非 RuntimeIntent。

- [ ] **Step 6: 实现 dispatcher**

从 generated index 精确解析 plugin key/version 和 logical route；装配 pinned binding/state/context/Gateway；未知/歧义 route、state schema 不匹配、未声明 capability 一律 `ContractViolation`。dispatcher 不写 DB。

- [ ] **Step 7: 生成 Workline index 并运行测试**

Run: `uv run python scripts/generate_runtime_extensions.py`

Run: `uv run pytest tests/workline_plugins/rough_sorter tests/contracts/workline/test_rough_sorter_scan_decision_spec.py tests/workline_runtime/extensions/test_runtime_extension_index_generation.py -q`

Expected: PASS；Workline index 仅新增批准的 rough sorter key/version/routes。

- [ ] **Step 8: 提交 typed Plugin**

```bash
git add src/app/runtime/workline_plugins src/app/runtime/capabilities/material_flow/contracts/rough_sorter.py tests/workline_plugins/rough_sorter tests/contracts/workline/test_rough_sorter_scan_decision_spec.py
git commit -m "feat(workline): 实现粗分机类型化决策插件"
```

### Task 7: 实现通用 SYSTEM_CAPABILITY EFFECT pipeline

**Symbols:** `RuntimeIntent`、`RuntimeIntentEffectApplier.apply`、`RuntimeIntentLog`、`OrchestratorWriteBackService.apply_intents`。

**Files:**

- Modify: `src/app/runtime/orchestration/runtime_intent.py`
- Modify: `src/app/runtime/orchestration/runtime_intent_log.py`
- Create: `src/app/runtime/orchestration/services/intent/system_capability_intent_service.py`
- Create: `src/app/runtime/orchestration/services/intent/system_capability_effect_service.py`
- Modify: `src/app/runtime/orchestration/services/intent/__init__.py`
- Create: `src/app/runtime/orchestration/services/material_unit_mutation_service.py`
- Create: `src/app/runtime/orchestration/services/session_hold_mutation_service.py`
- Modify: `src/app/runtime/orchestration/services/__init__.py`
- Modify: `src/app/device/services/device_command_service.py`
- Modify: `src/app/device/services/__init__.py`
- Modify: `src/app/runtime/orchestration/runtime_intent_effects.py`
- Create: `src/app/runtime/system_capabilities/material_flow/material_unit_write/` (`__init__.py`, `contracts.py`, `handler.py`, `definition.py`)
- Create: `src/app/runtime/system_capabilities/device/device_command_write/` (`__init__.py`, `contracts.py`, `handler.py`, `definition.py`)
- Create: `src/app/runtime/system_capabilities/runtime/session_hold/` (`__init__.py`, `contracts.py`, `handler.py`, `definition.py`)
- Create: `tests/contracts/system_capabilities/test_effect_intent_contract.py`
- Create: `tests/workline_runtime/system_capabilities/test_system_capability_effect_service.py`
- Modify: `tests/workline_runtime/test_runtime_intent_effect_applier.py`

- [ ] **Step 1: 对四个 Symbols 运行 impact**

`RuntimeIntentEffectApplier` 只允许增加一个 generic branch；若 impact 显示新的跨域消费者，先调整测试矩阵再编辑。

- [ ] **Step 2: 写 SYSTEM_CAPABILITY intent 失败测试**

锁定 key/version、operation key、typed payload、payload hash、precondition/fact version、timeout、creator authority/policy/binding/provider snapshot。非法字段组合和同 key 不同 payload 必须 fail closed。

- [ ] **Step 3: 写 transaction/completion 失败测试**

LOCAL_TRANSACTIONAL：领域 Service 参与外层事务，只 flush，领域事实 + intent/evidence 同 commit/rollback。OUTBOX_ASYNC：只在事务内写 DeviceCommand/Outbox，durably accepted 不等于远端完成。handler 禁止 commit/rollback/外部 I/O。

- [ ] **Step 4: 写幂等/precondition 失败测试**

最终 idempotency key 由 capability key/version、Session/WorkItem、operation identity 派生；同 key 同 hash no-op success，同 key 不同 hash `ContractViolation(IDEMPOTENCY_CONFLICT)`；stale fact 返回 `BusinessReject(STALE_PRECONDITION)` 并回流 Plugin，不盲重试。

- [ ] **Step 5: 运行并确认通用分支缺失而失败**

Run: `uv run pytest tests/contracts/system_capabilities/test_effect_intent_contract.py tests/workline_runtime/system_capabilities/test_system_capability_effect_service.py tests/workline_runtime/test_runtime_intent_effect_applier.py -q`

Expected: FAIL。

- [ ] **Step 6: 实现三个最小 EFFECT capability**

每个 handler 只调用所属领域 Service/Port，不直接 Repository。新增 `MaterialUnitMutationService` 和 `SessionHoldMutationService` 承接 EffectApplier 当前内嵌的数据写入；Device capability 调用 `DeviceCommandService` 新增的外层事务参与入口。三个入口都只 flush、不 commit/rollback；Session Hold 不创建 RuntimeHold，TIMER reconciliation 继续由既有 owner 处理。

- [ ] **Step 7: 实现 intent/effect coordinator**

`RuntimeIntentEffectApplier.apply` 只识别 `SYSTEM_CAPABILITY` 并委托独立 service；不加入 capability key、rough sorter action/event/reason 分支。未知 handler exception 由 Runtime 映射 retryable UNKNOWN，耗尽后沿现有 dead-letter/Hold。

- [ ] **Step 8: 生成索引并运行 unit contracts**

Run: `uv run python scripts/generate_runtime_extensions.py`

Run: `uv run pytest tests/contracts/system_capabilities tests/workline_runtime/system_capabilities tests/workline_runtime/test_runtime_intent_effect_applier.py -q`

Expected: PASS。

- [ ] **Step 9: 提交 EFFECT pipeline**

```bash
git add src/app/runtime/orchestration/runtime_intent.py src/app/runtime/orchestration/runtime_intent_log.py src/app/runtime/orchestration/services src/app/runtime/orchestration/runtime_intent_effects.py src/app/device/services src/app/runtime/system_capabilities tests/contracts/system_capabilities tests/workline_runtime/system_capabilities tests/workline_runtime/test_runtime_intent_effect_applier.py
git commit -m "feat(runtime): 统一系统能力副作用执行管线"
```

### Task 8: 把 Plugin attempt 接入 RuntimeInbox/result/timeout

**Symbols:** `RuntimeInboxProcessorService`、`RuntimeInboxOrchestratorBridge` 实际类名（先以文件 context 消歧）、`RuntimeInboxWriteBackService`、`InputNormalizer`、`WorklineRuntimeReconciliationService`。

**Files:**

- Modify: `src/app/runtime/orchestration/services/runtime_inbox/runtime_inbox_processor_service.py`
- Modify: `src/app/runtime/orchestration/services/runtime_inbox/runtime_inbox_orchestrator_bridge.py`
- Modify: `src/app/runtime/orchestration/services/runtime_inbox/runtime_inbox_writeback_service.py`
- Modify: `src/app/runtime/normalization/normalizers/input_normalizer.py`
- Modify: `src/app/runtime/orchestration/services/reconciliation/runtime_reconciliation_service_impl.py`
- Modify: `src/app/workline/services/write_back_service.py`
- Create: `tests/workline_runtime/extensions/test_plugin_runtime_inbox_routing.py`
- Modify: `tests/runtime/orchestration/test_runtime_inbox_three_stage_processor.py`
- Modify: `tests/workline_runtime/test_runtime_capability_dispatcher.py`

- [ ] **Step 1: 用 GitNexus context 消歧并运行 impact**

按文件路径获取真实 class/method UID，再逐个 upstream impact；禁止根据名称猜测类。

- [ ] **Step 2: 写 canonical route 失败测试**

DEVICE_EVENT/COMMAND_RESULT/INTERNAL_EVENT/EXTERNAL_HTTP/TIMER_TIMEOUT 归一化为 Definition 声明的 logical typed input；不新增 raw `SYSTEM_CAPABILITY_RESULT`。correlation 缺失或 callback 不命中当前等待命令只归档 evidence。

- [ ] **Step 3: 写原子 decision writeback 失败测试**

同一短事务验证 token/version，写 QUERY evidence、PluginState snapshot/version、System Capability intents、decision timeline 与 Inbox terminal。任一步异常全部 rollback；retry 可读取新事实，已提交 replay 只能读取 recorded evidence。

- [ ] **Step 4: 写 timeout/result 回流失败测试**

WMS QUERY outcome 和 OUTBOX callback 以 typed result 回到 Plugin；`PICK_AND_PUT` business timeout 保留既有 TIMER reconciliation owner，写 Session Hold + RuntimeHold 且无 RuntimeIntent；稳定原因码为 `ROUGH_SORTER_PICK_RESULT_TIMEOUT`。

- [ ] **Step 5: 运行并确认仍走旧 Orchestrator 分支而失败**

Run: `uv run pytest tests/workline_runtime/extensions/test_plugin_runtime_inbox_routing.py tests/runtime/orchestration/test_runtime_inbox_three_stage_processor.py tests/workline_runtime/test_runtime_capability_dispatcher.py -q`

Expected: 新 routing assertion FAIL；旧回归仍绿。

- [ ] **Step 6: 接入 generated Plugin dispatcher**

rough sorter route 只进入新 dispatcher；Runtime lifecycle events 继续短路。writeback 消费 `PluginDecision`。旧 `OrchestratorResult` 在本 Task 内只服务尚未迁移的非本切片路径，不能作为 rough sorter fallback，并必须在 Task 10 的 inactive legacy branch 清理中退出生产路由。

- [ ] **Step 7: 运行 runtime routing tests 并通过**

Run: `uv run pytest tests/workline_runtime/extensions/test_plugin_runtime_inbox_routing.py tests/runtime/orchestration/test_runtime_inbox_three_stage_processor.py tests/workline_runtime/test_runtime_capability_dispatcher.py tests/contracts/workline/test_rough_sorter_scan_decision_spec.py -q`

Expected: PASS；rough sorter handler/QUERY/EFFECT call count 唯一。

- [ ] **Step 8: 提交 RuntimeInbox 接入**

```bash
git add src/app/runtime/orchestration/services/runtime_inbox src/app/runtime/normalization/normalizers/input_normalizer.py src/app/runtime/orchestration/services/reconciliation/runtime_reconciliation_service_impl.py src/app/workline/services/write_back_service.py tests/workline_runtime/extensions/test_plugin_runtime_inbox_routing.py tests/runtime/orchestration/test_runtime_inbox_three_stage_processor.py tests/workline_runtime/test_runtime_capability_dispatcher.py
git commit -m "feat(runtime): 接入插件决策与结果回流"
```

### Task 9: 固化共享 conformance 与架构门禁

**Symbols:** `scripts/architecture-guardrails.sh` 内 capability rules；脚本修改前用文本/测试影响清单，不修改业务 symbol。

**Files:**

- Create: `tests/workline_plugins/conformance.py`
- Create: `tests/workline_plugins/rough_sorter/test_conformance.py`
- Create: `tests/architecture/test_runtime_extension_platform_guardrail.py`
- Modify: `tests/architecture/test_capability_dependency_guardrail.py`
- Modify: `scripts/architecture-guardrails.sh`
- Modify: `scripts/architecture-guardrails.allowlist`
- Modify: `tests/architecture/test_test_suite_topology_guardrail.py`

- [ ] **Step 1: 写 conformance 失败测试**

共享 suite 参数化 Definition fixture，验证 identity/routes/config/state、declared capability、decision closure、QUERY/EFFECT boundary、replay provider call 0、intent/state/evidence limits。rough sorter 只提供 fixture 和业务特有 assertions，不复制公共测试。

- [ ] **Step 2: 写 architecture 失败测试**

门禁扫描：Plugin 禁止 Repository/SQLAlchemy/HTTP/Celery/provider DTO/service locator；System Capability 禁止 Repository 与事务控制；Runtime generated index 禁止 scan/dynamic import；Orchestrator/EffectApplier 禁止任何 Workline key/event/action/business-timeout 分支；旧三 catalog/dispatcher 禁止被 production path import。

- [ ] **Step 3: 运行并确认门禁能发现当前残留**

Run: `uv run pytest tests/workline_plugins/rough_sorter/test_conformance.py tests/architecture/test_runtime_extension_platform_guardrail.py tests/architecture/test_capability_dependency_guardrail.py -q`

Expected: 对尚未清理的旧路径或缺失 guardrail FAIL，违规信息包含 rule/file/line。

- [ ] **Step 4: 实现共享 suite 与稳定 guardrail IDs**

新增 rule 使用稳定业务名称，不使用 phase/wave 缩写。allowlist 只能逐文件、带原因/到期日/legacy entry；新平台目录不允许进入长期 allowlist。

- [ ] **Step 5: 运行 conformance/architecture/topology tests**

Run: `uv run pytest tests/workline_plugins/rough_sorter/test_conformance.py tests/architecture/test_runtime_extension_platform_guardrail.py tests/architecture/test_capability_dependency_guardrail.py tests/architecture/test_test_suite_topology_guardrail.py -q`

Expected: PASS；所有新测试文件少于 1000 行，默认 collection 边界不变。

- [ ] **Step 6: 提交 conformance 与门禁**

```bash
git add tests/workline_plugins tests/architecture scripts/architecture-guardrails.sh scripts/architecture-guardrails.allowlist
git commit -m "test(runtime): 固化扩展平台一致性门禁"
```

### Task 10: 执行 rough sorter destructive switch 并删除旧 runtime catalog

**Symbols:** `get_workline_capability_definition`、`list_workline_capability_definitions`、`RuntimeCapabilityDispatcher`、`OrchestratorService.process_inbox`、所有 `src/app/runtime/capability_catalog.py` importers。

**Files:**

- Delete: `src/app/runtime/capability_dispatcher.py`
- Delete: `src/app/runtime/runtime_capability_catalog.py`
- Delete: `src/app/runtime/capability_catalog.py`
- Delete: `src/app/workline/domain/plugin_manifest.py`
- Delete: `src/app/workline/domain/contracts/manifests/rough_sorter.yaml`
- Delete: `src/app/workline/domain/contracts/manifests/smt_sorting_inbound.yaml`
- Modify: all production importers returned by `rg -l "src.app.runtime.capability_catalog|src.app.runtime.runtime_capability_catalog|src.app.runtime.capability_dispatcher" src scripts`
- Modify or delete: all active importers returned by `rg -l "WorklinePluginManifest|plugin_manifest|contracts/manifests" src tests scripts`
- Modify: `src/app/workline/services/workline_service.py`
- Modify: `src/app/workline/services/migration_inventory_service.py`
- Modify: `src/app/runtime/orchestration/orchestrator_bridge.py`
- Modify: affected unit/contract tests identified by `rg`
- Modify: `tests/characterization/workline_legacy/test_business_semantics_characterization.py`

- [ ] **Step 1: 重新生成 inventory 并执行 switch gate**

Target report 必须证明非 rough-sorter 活动 Session/WorkItem/Inbox/Intent/Outbox 引用为零，rough sorter binding/Definition/provider/Port/index digest 完整。任一非零或 digest 不一致立即停止本 Task，不删除文件、不启用 fallback。

- [ ] **Step 2: 对删除 Symbols 运行 impact 与完整 import inventory**

GitNexus `partial` 结果必须辅以：

`rg -l "src.app.runtime.capability_catalog|src.app.runtime.runtime_capability_catalog|src.app.runtime.capability_dispatcher|WorklinePluginManifest|plugin_manifest|contracts/manifests" src tests scripts`

逐项分类为迁移、删除或历史测试更新，禁止遗漏。

- [ ] **Step 3: 写零引用/单 dispatcher 失败测试**

architecture test 要求 active production 不存在旧 module import、任何 Workline key/event/action/business-timeout 不出现在 Orchestrator/EffectApplier、runtime 只从两个 generated index 路由。characterization 更新为新生产真源，archive 仍仅是历史证据。

- [ ] **Step 4: 迁移所有通用读取方**

WorkLine options/manifest view、Session resolver、normalizer、hold query/release、operation/query service、device context、repair script 和 inventory 改读 generated Plugin Definition/binding；provider admission 改读 `external_contract_profile_catalog.py`。rough sorter parser/NG reason/topology 进入自身 Definition/config；需要解释历史 SMT Hold 的稳定 reason 进入 `src/app/runtime/capabilities/material_flow/contracts/smt_sorting_inbound.py`，但 SMT 不进入当前 generated Plugin index。只保留 Definition schema + binding 组合出的 API view，不保留 YAML/Manifest identity 真源。

- [ ] **Step 5: 删除旧 runtime capability dispatcher/catalog**

删除 `RuntimeCapabilityDefinition/Catalog/Dispatcher` 和手写 rough sorter runtime catalog；同时删除 inventory gate 已证明无活动引用的 SMT/其他 Workline-specific Orchestrator 分支。`SorterInboundRuntimeService` 保留为 material-flow 领域 service，但不得再作为 runtime capability handler、Plugin dispatcher 或 Workline decision owner。

- [ ] **Step 6: 处理旧 Workline catalog**

删除 `capability_catalog.py`、`plugin_manifest.py` 和两个 YAML manifest。目标环境中未迁移 Workline 已由 switch gate 证明无活动运行引用；其配置继续由 inventory 报告为未支持且禁止激活，不创建临时 Definition、adapter 或 fallback。

- [ ] **Step 7: 运行零引用和受影响回归**

Run: `uv run pytest tests/architecture/test_runtime_extension_platform_guardrail.py tests/workline_runtime tests/workline_plugins/rough_sorter tests/contracts/workline tests/contracts/system_capabilities -q`

Run: `uv run python scripts/generate_runtime_extensions.py --check`

Expected: PASS；旧 module import 为零；Orchestrator/EffectApplier 无 Workline-specific 分支；rough sorter 单 dispatcher。

- [ ] **Step 8: 提交 destructive switch**

```bash
git add -A src/app/runtime src/app/workline src/app/device scripts/data tests
git commit -m "refactor(runtime): 切换粗分机扩展平台唯一入口"
```

### Task 11: 验证 PostgreSQL transaction、Celery/Outbox 与 replay

**Symbols:** 在新增 integration 测试触及的 processor/effect/outbox 方法上重新 impact；本 Task 不在未分析时修改生产 symbol。

**Files:**

- Create: `tests/integration/workline_capabilities/test_rough_sorter_plugin_attempt_postgresql.py`
- Create: `tests/integration/workline_capabilities/test_system_capability_effect_postgresql.py`
- Create: `tests/integration/workline_capabilities/test_rough_sorter_replay_postgresql.py`
- Create: `tests/integration/workline_capabilities/test_rough_sorter_outbox_result_flow.py`
- Modify: `tests/resilience/test_runtime_inbox_crash_recovery_postgresql.py`

- [ ] **Step 1: 写 PostgreSQL concurrency 失败测试**

覆盖 QUERY 期间 session/state/token 改变 → discard/retry；两个 worker 同时处理同 key → 单 decision/effect；写回崩溃 → evidence/state/intent 全 rollback；lease owner 丢失不能提交。

- [ ] **Step 2: 写 EFFECT transaction 失败测试**

覆盖本地 capability 成功原子 commit、领域 Service 异常全 rollback、stale precondition 业务拒绝、同 operation/payload 幂等、同 operation/不同 payload 冲突。检查 handler 未 commit/rollback。

- [ ] **Step 3: 写 Outbox/result 失败测试**

DeviceCommand/Outbox 入队只表示 accepted；callback 通过 RuntimeInbox logical result 回流 Plugin。callback 丢失由 timeout/retry/Hold 可见，不能把 queued/dispatched 当业务完成。

- [ ] **Step 4: 写 deterministic replay/crash 失败测试**

首次成功 QUERY 后 replay provider call 0；首次 timeout replay 不产生成功 evidence；同 digest zero-new-effect；不同 digest 首次一次 Hold，后续 replay zero-new-hold。crash recovery 保持 exactly-once effect evidence。

- [ ] **Step 5: 运行显式 integration/resilience tests**

Run: `uv run pytest tests/integration/workline_capabilities -q -o addopts=''`

Run: `uv run pytest tests/resilience/test_runtime_inbox_crash_recovery_postgresql.py -q -o addopts=''`

Expected: PASS。

- [ ] **Step 6: 提交 integration evidence**

```bash
git add tests/integration/workline_capabilities tests/resilience/test_runtime_inbox_crash_recovery_postgresql.py
git commit -m "test(runtime): 验证扩展事务与重放一致性"
```

### Task 12: 完成 13-case E2E、性能预算与计划收口

**Files:**

- Create: `tests/e2e/workline_capabilities/test_rough_sorter_scan_decision_slice.py`
- Create: `tests/workline_runtime/extensions/test_runtime_extension_performance_budget.py`
- Modify: `docs/superpowers/specs/2026-07-15-workline-plugin-system-capability-platform-design.md`
- Modify: `docs/business/rough_sorter_scan_decision_contract.md`
- Modify: `tests/fixtures/workline_contract/rough_sorter/scan_decision_cases.json`

- [ ] **Step 1: 写 13-case E2E 参数化测试**

从 fixture 驱动真实 RuntimeInbox → Plugin → QUERY/evidence → intent → EFFECT/result → Plugin；断言最终 Session/Material/Command/RuntimeHold/Timeline、provider call count、effect count、reason code 和 replay evidence。fixture 不进入 production loader。

- [ ] **Step 2: 把 implementation_status 收敛为 covered**

只有对应 E2E 生产行为通过后，才把 13 个 case 的 `gap`/`partial` 改为 `covered`；合同测试要求集合最终仅含 `covered`。业务语义未改变，不重写批准时间；若实现迫使语义变化，停止并重新走业务批准。

- [ ] **Step 3: 建立最小性能预算**

测量 cold-start generated index import、单 Inbox 无 QUERY 决策、一次 WMS QUERY 决策、Outbox enqueue 和 recorded replay。预算写入测试常量与失败输出；不引入缓存框架。基准异常先定位热点，再决定是否调整预算。

- [ ] **Step 4: 运行 E2E 与性能测试**

Run: `uv run pytest tests/e2e/workline_capabilities/test_rough_sorter_scan_decision_slice.py -q -o addopts=''`

Run: `uv run pytest tests/workline_runtime/extensions/test_runtime_extension_performance_budget.py -q`

Expected: 13 cases PASS；性能预算 PASS。

- [ ] **Step 5: 更新平台设计进度**

将 T3–T7 标为本切片已完成，链接 generated index digest、integration/E2E、migration 和 quality evidence；明确 T1 remaining、其他 Workline、T8、T9 未完成，不把 slice readiness 写成 production cutover readiness。

- [ ] **Step 6: 运行完整质量验证**

Run:

```bash
uv run python scripts/generate_runtime_extensions.py --check
uv run pytest tests/architecture/test_test_suite_topology_guardrail.py -q
uv run pytest tests/workline_plugins tests/workline_runtime tests/contracts/system_capabilities tests/contracts/workline -q
uv run pytest tests/integration/workline_capabilities tests/integration/test_workline_migration_inventory_postgresql.py -q -o addopts=''
uv run pytest tests/e2e/workline_capabilities -q -o addopts=''
uv run pytest tests/resilience/test_runtime_inbox_crash_recovery_postgresql.py -q -o addopts=''
uv run pytest --collect-only -q -o addopts='' | tail -5
uv run ruff format --check src tests scripts
uv run ruff check src tests scripts
uv run bandit -r src/
./scripts/git-quality-gate.sh --profile quality
uv run python -c "from main import app; assert app"
uv run celery -A src.celery_app.app inspect ping
git diff --check
```

Expected: 全部通过；Celery ping 需要本地 worker，无法启动外部依赖时必须明确记录未执行原因，不能把它写成通过。

- [ ] **Step 7: 运行 GitNexus detect changes**

调用 `gitnexus_detect_changes(scope="all")`。

Expected: 受影响流程只包含预期的 Workline config/session、RuntimeInbox decision、System Capability effect、Outbox/result/replay；出现无关 API/领域流程时先审查再提交。

- [ ] **Step 8: 提交收口证据**

```bash
git add docs/superpowers/specs/2026-07-15-workline-plugin-system-capability-platform-design.md docs/business/rough_sorter_scan_decision_contract.md tests/fixtures/workline_contract/rough_sorter/scan_decision_cases.json tests/e2e/workline_capabilities tests/workline_runtime/extensions/test_runtime_extension_performance_budget.py
git commit -m "test(workline): 闭合粗分机平台真实切片"
```

## 6. 最终验收清单

- [ ] Runtime 只存在 `WorklinePluginDefinition` 与 `SystemCapabilityDefinition` 两类扩展定义，无公共 Extension DSL。
- [ ] rough sorter 每个 logical input 只命中 generated Plugin index 的一个 route，无旧 dispatcher/fallback。
- [ ] QUERY 不占写事务、连接或行锁；attempt-scoped coalescing/limits/deadline/evidence 全部可测。
- [ ] replay 只读取 recorded input/QUERY/decision evidence，provider call count 为 0。
- [ ] `SYSTEM_CAPABILITY` 是 EffectApplier 唯一新增通用分支；核心文件无 rough sorter key/event/action/timeout 分支。
- [ ] LOCAL_TRANSACTIONAL 与 OUTBOX_ASYNC 完成语义、幂等冲突、stale precondition 和 rollback 均由 PostgreSQL 测试证明。
- [ ] Binding 不可变、Session/WorkItem/Intent 固定 key/version/hash/index digest，缺失版本 fail closed。
- [ ] Plugin 不 import Repository/SQLAlchemy/HTTP/Celery/provider DTO；Capability 不 import Repository、不控制事务。
- [ ] 13-case fixture 全部 `covered`，业务批准语义未被工程实现改写。
- [ ] generator `--check`、cold-start、architecture/topology、默认域、integration、E2E、resilience、Ruff、Bandit、quality gate 通过。
- [ ] 平台设计明确保留 T1 remaining、其他 Workline、T8 cutover、T9 DX 的未完成状态。

## 7. 后续独立计划

本计划通过后再分别编写：

1. 完整 rough sorter 后续出料/格位/满箱业务规格与迁移计划。
2. SMT 及 inventory matrix 中其他 active Workline 的逐一规格与 Plugin 迁移计划。
3. T8 inventory-backed 跨环境 preflight、freeze/drain、历史 trace replay、原子 cutover 与 roll-forward 计划。
4. T9 脚手架、无副作用诊断和 `docs/plugin_development_guide.md` 更新计划。

这些计划不得反向要求本切片预建通用 DSL、兼容层或跨 attempt cache。
