# 退役工作线插件活动残留收敛实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 删除 Phase 5 之后仍处于活动源码、API、诊断合同和当前数据库 head 中的旧插件身份与语义，同时保持 Phase 6 Transport、Phase 7 DeviceCommand/ECS 和通用可靠性行为不变。

**Architecture:** 按“诊断与 Trace → RuntimeHold/NG → RuntimeIntentLog → 无生产消费者的 replay/观测字面量”四个垂直切片收敛，每个切片先建立失败测试，再删除生产路径和对应 schema。三个活动 schema 切片共用一条逐步扩展的 Alembic revision 和一个 PostgreSQL 退役验收 owner；历史 revision 不在本计划中压缩，由 Phase 11 基线重置计划统一处理，避免把活动模型修复与历史 revision 重写混成一个高风险变更。

**Tech Stack:** Python 3.13、FastAPI/Pydantic、SQLModel/SQLAlchemy、Alembic、PostgreSQL、Pytest、Ruff、GitNexus、仓库 HEAVY selector。

**当前状态:** `IN_PROGRESS`。Tasks 1–5 已完成原子实施；Task 6 正在执行合入前全局门禁、完整分支独立评审和意见修复循环。Steps 7–8 只能在本分支合入 `develop` 后执行，不得提前清理 deletion tombstone 或归档本计划。

## Global Constraints

- 当前系统未发布，不保留旧字段 alias、兼容 shim、双写、双读、旧错误码映射或 downgrade。
- 只删除旧插件特有身份和语义；不得借机删除 `RuntimeInbox`、TransportTask、DeviceCommand、WMS 当前可靠对象或通用诊断能力。
- `RuntimeIntentLog` 的 GitNexus 上游影响为 70 个节点、风险为 HIGH；执行其任务前必须重新运行影响分析并取得用户确认。
- 本计划只生成一条 Alembic revision：Task 2 用 `uv run alembic revision -m "删除退役插件活动残留"` 生成随机 revision ID，Tasks 3–4 继续编辑同一文件；不得生成三个临时 revision 或手写 revision ID。
- 代码行为遵循 TDD；纯文档同步只做链接、引用和 `git diff --check` 验证。
- 核心测试只验证基础能力和跨模块合同；不得加入具体工作线、供应商私有协议或业务流程。
- HEAVY selector 为新合并 revision，以及尚未被现有规则覆盖且直接承载被删数据库列的 ORM model，增加 `test_workline_plugin_schema_retirement.py` 这一权威 owner。已被现有宽映射覆盖的 repository/service 路径不得再增加不同策略的精确 mapping；现有精确 `NONE` mapping 仅在确认仍无 HEAVY 影响后刷新说明和内容指纹。每次新增 mapping 前先运行 selector 合同证明没有宽窄策略重叠。
- Task 1 必须记录不可变的 `<implementation_base_sha>`。Tasks 2–5 的每个原子提交都必须依次完成：按本任务文件清单精确暂存、核对 `git diff --cached --name-status` 未带入外部变更、运行 staged selector 和其选中的 HEAVY、运行 `gitnexus_detect_changes({scope: "staged"})`、确认范围正确，然后才允许提交。不得把这些提交前门禁推迟到最终任务。
- Tasks 2–4 会逐步扩展同一条未发布 revision；每轮 PostgreSQL 验证必须使用全新临时数据库，已经应用过该 revision 较早形态的开发/测试数据库必须清理重建，不得复用旧 stamp 推断新 schema 已生效。
- 每个任务第一次运行 `--scope unstaged` 前，必须把该任务全部拟改生产路径逐一交给当前 selector 求值。未被既有规则分类的数据库列/enum owner 精确映射到 `test_workline_plugin_schema_retirement.py`；仅由已列 FAST owner 承接且确认无 HEAVY 影响的路径使用精确 `NONE`，并在最终内容稳定后记录内容指纹。先运行 `uv run pytest tests/scripts -q` 证明配置无歧义，再运行红灯测试和 HEAVY；不得让未分类路径掩盖预期红灯。
- 删除的 `replay.py` 和 `timeline_recorded_replay_repository.py` 必须在含删除的分支差异中保留精确 `NONE` tombstone mapping，使 staged 与 CI base diff 可分类；合入 `develop` 后再以独立 cleanup 提交删除 tombstone。仍存活的 `src/app/runtime/system_capabilities/__init__.py` 使用精确、带最终内容指纹的 `NONE` mapping，不随 tombstone 清理。
- 当前阶段的 `src/` 零插件身份是 Phase 5–7 的中间态，不是永久禁止目标插件身份。Phase 8/9 只能在 `LineRunEpoch`、插件 SPI 和 Composition Root 中引入已批准的目标身份，不得恢复本计划删除的 Trace、Diagnostic、RuntimeHold、RuntimeIntentLog 字段、旧错误码或 replay 路径。
- `docs/hardware/` 是供应商原始输入，本计划不得修改、移动或删除其中任何文件。
- 当前工作区存在独立 WES-WMS DTO 文档变更；实施、暂存和提交必须按精确路径隔离，不得带入这些外部变更。

---

## 已冻结的处置决策

| 对象 | 处置 | 理由 |
| --- | --- | --- |
| `src/app/runtime/workline_plugins/**/__pycache__` | 删除本地目录，不提交 | 仅为忽略的旧字节码；无 Git 跟踪源码且 Docker 已排除 |
| Trace、Diagnostic、RuntimeHold、RuntimeIntentLog 中的 `plugin_key` / `plugin_contract_version` / 插件 `contract_version` | 删除 | 当前生产者恒为 `None` 或不存在，属于退役插件身份 |
| `PLUGIN_BINDING_REQUIRED` | 删除且不提供替代码 | 当前无生产发射者；保留会伪造不存在的配置恢复路径 |
| `PLUGIN_EXECUTION_FAILED` / `PLUGIN_TRANSITION_INVALID` | 直接替换为 `WORKFLOW_EXECUTION_FAILED` / `WORKFLOW_TRANSITION_INVALID` | 当前失败来自运行时编排，不再由插件 owner 负责；系统未发布，无旧码兼容 |
| `ErrorDomain.PLUGIN` | 删除；上述新码归 `WORKFLOW` | 当前核心不存在插件执行 owner |
| `NgReasonSource.PLUGIN`、NG DTO 的插件身份 | 删除 | 当前 catalog 没有插件 reason 生产者；新插件业务合同应在 Phase 8/9 独立包重新定义 |
| `RuntimeIntentLog.operation_kind="plugin_intent"` 默认值 | 删除默认值，改为调用方必填 | 默认值会把非插件可靠对象错误标记为插件 intent |
| `RuntimeIntent.binding_snapshot` / `RuntimeIntentLog.binding_snapshot_json` | 本计划暂不删除 | 现有幂等冲突测试还把它用于非插件 domain owner 快照；其整体去留属于 Phase 10 Generic Intent/Effect 收敛 |
| `PLUGIN_DECISION` replay 读取闭包 | 删除 | 只有测试消费者，无生产写入者；目标架构禁止旧插件自动 replay |
| `PLUGIN_EXECUTION` 北向观测阶段 | 删除 | 无生产发射者，且与当前北向 operation 阶段定义不符 |
| 历史插件 migrations | 保留；仅新增一条活动残留收敛 revision | Phase 11 在最终模型稳定后一次重置，不在当前活动模型修复中重复劳动 |
| 插件缺席守卫、Phase 5 schema 退役验收、目标态插件开发指南 | 保留 | 分别承担防回流、当前 head 验收和 Phase 8/9 目标合同职责 |

### Task 1: 清理本地旧插件字节码并冻结实施基线

**Files:**

- Remove locally: `src/app/runtime/workline_plugins/`（仅忽略的 `__pycache__` 和空目录）
- Preserve: 当前 `git status --short` 中已有的 WES-WMS DTO 文档变更

**Interfaces:**

- Consumes: Phase 5 零插件缺席守卫。
- Produces: 无旧字节码干扰、可复核的干净源码扫描基线。

- [ ] **Step 1: 证明目录没有 Git 跟踪文件**

  Run: `git ls-files src/app/runtime/workline_plugins`

  Expected: 无输出。

- [ ] **Step 2: 记录并核对待删除对象**

  Run: `rg --files -uu src/app/runtime/workline_plugins | sort`

  Expected: 仅出现 `__pycache__/*.pyc`，不得出现 `.py`、配置或 fixture。

- [ ] **Step 3: 删除精确目录并验证**

  删除 `src/app/runtime/workline_plugins/` 这一已核对的本地缓存目录，不使用针对仓库根目录的递归清理或 `git clean`。

  Run: `test ! -e src/app/runtime/workline_plugins`

  Expected: 退出码 `0`。

- [ ] **Step 4: 确认没有可提交变化**

  Run: `git rev-parse HEAD && git status --short`

  Expected: 记录输出中的 HEAD 为 `<implementation_base_sha>`；状态与 Step 1 前相比没有新增 tracked 变化，本任务不产生 Commit。

- [ ] **Step 5: 冻结所有拟改路径的 selector 分类**

  逐项核对 Tasks 2–5 的生产文件与 `docs/architecture/heavy-test-impact.toml`。当前未分类清单至少包括 callback/diagnostics 的 `codes.py`、`failure_mapper.py`、`models.py`，`material_flow/contracts/ng_reason.py`，`models/diagnostic.py`、`models/runtime_hold.py`、`runtime_intent_log.py`、`observability.py`、`services/trace/trace_query_service.py`，以及 Task 5 的 replay、repository 和 `system_capabilities/__init__.py`；实施时以当前 selector 实测为准，不得只复制本清单。

  Expected: 每个拟改候选路径在对应任务红灯前都有且只有一个精确或既有宽 mapping 策略；直接数据库 owner 触发 PostgreSQL 退役验收，其余精确 `NONE` 均有现存 FAST owner 和无 HEAVY 影响理由。

### Task 2: 移除 Trace 与诊断合同中的旧插件身份

**Files:**

- Modify: `tests/callback/test_callback_mirror_integration.py`
- Modify: `tests/callback/test_contract_mismatch_diagnostics.py`
- Modify: `tests/runtime/orchestration/test_trace_response_builder.py`
- Modify: `tests/integration/test_workline_plugin_schema_retirement.py`
- Modify: `src/app/callback/contracts/{trace_context.py,models.py,builder.py,codes.py,registry.py,failure_mapper.py}`
- Modify: `src/app/runtime/orchestration/diagnostics/{models.py,builder.py,codes.py,registry.py,failure_mapper.py}`
- Modify: `src/app/runtime/orchestration/models/{diagnostic.py,runtime.py}`
- Modify: `src/app/runtime/orchestration/services/trace/{trace_query_service.py,trace_response_builder.py}`
- Modify: `src/app/runtime/orchestration/services/query/runtime_query_service.py`
- Modify: `src/app/workline/{trace_context.py,services/diagnostic_service.py}`
- Create: `migrations/versions/<generated>_remove_retired_plugin_residuals.py`
- Modify: `docs/architecture/heavy-test-impact.toml`

**Interfaces:**

- Consumes: 现行 request/event/session/device/command/outbox Trace 字段和通用诊断持久化。
- Produces: 不含插件身份的 Trace/Diagnostic DTO、`WORKFLOW_EXECUTION_FAILED`、`WORKFLOW_TRANSITION_INVALID` 和同步后的数据库 schema。

- [ ] **Step 1: 执行影响分析并确认边界**

  对以下 `target / file_path` 组合分别运行 `gitnexus_impact({direction: "upstream"})`，禁止省略 `file_path` 后自行猜测同名对象：

  - `src/app/callback/contracts/trace_context.py:TraceContext`
  - `src/app/workline/trace_context.py:TraceContext`
  - `src/app/callback/contracts/codes.py:ErrorCode`
  - `src/app/runtime/orchestration/diagnostics/codes.py:ErrorCode`
  - `src/app/runtime/orchestration/models/diagnostic.py:WorklineDiagnosticBase`
  - `src/app/runtime/orchestration/models/runtime.py:RuntimeWorklineSummary`

  Expected: 记录 direct callers、风险等级和受影响测试；HIGH/CRITICAL 时先向用户报告并等待确认。

- [ ] **Step 2: 写失败测试**

  在现有测试所有者中增加以下精确断言：

  ```python
  assert {"plugin_key", "contract_version"}.isdisjoint(TraceContext.__dataclass_fields__)
  assert "PLUGIN" not in ErrorDomain.__members__
  assert {"PLUGIN_BINDING_REQUIRED", "PLUGIN_EXECUTION_FAILED", "PLUGIN_TRANSITION_INVALID"}.isdisjoint(ErrorCode.__members__)
  ```

  同时断言：

  - runtime 与 callback mirror 对 `STATE_MISMATCH` 都映射为 `WORKFLOW_TRANSITION_INVALID/WORKFLOW`；
  - runtime 与 callback mirror 对 `SOFTWARE`、`ORCHESTRATION` 都映射为 `WORKFLOW_EXECUTION_FAILED/WORKFLOW`；
  - trace session failure map 识别两个新工作流错误码，旧 `PLUGIN_*` 字符串不在映射中并落入既定 `SESSION_RESOLVE_FAILED` fail-closed 路径；
  - Trace API 与诊断 Pydantic/SQLModel 字段不再暴露 `plugin_key`，两个 mirror 的枚举、registry 和 builder 保持一致。

- [ ] **Step 3: 运行红灯测试**

  Run: `uv run pytest tests/scripts -q`

  Run: `uv run pytest tests/callback/test_callback_mirror_integration.py tests/callback/test_contract_mismatch_diagnostics.py tests/runtime/orchestration/test_trace_response_builder.py -q`

  Run: `./scripts/run_selected_heavy_local.sh --scope unstaged`

  Expected: FAST 与 PostgreSQL 退役验收均 FAIL，失败点必须是旧字段、旧错误码或旧 schema 仍存在；不得因环境跳过。

- [ ] **Step 4: 实现最小替换**

  删除 Trace、诊断持久模型、查询响应和 builder 中的插件字段；删除 `PLUGIN_BINDING_REQUIRED`；将另外两个旧插件错误码直接改名为工作流错误码并归属 `ErrorDomain.WORKFLOW`。同步更新本任务触及文件中已经失效的“legacy mirror”“保留兼容”和插件 owner 注释，不做邻近重构；不得保留旧枚举成员、字符串 alias 或 fallback。

- [ ] **Step 5: 生成并编辑 schema revision**

  Run: `uv run alembic revision -m "删除退役插件活动残留"`

  在生成的唯一 revision 中先删除 `wes_biz.workline_diagnostics.plugin_key` 及其索引；`downgrade()` 明确抛出 `NotImplementedError`。扩展 `test_workline_plugin_schema_retirement.py` 验证当前 head 和 ORM 都不存在该列。为该新 revision 及尚无现有 owner 的直接 ORM model 增加指向此测试的精确 HEAVY mapping；其它生产路径复用现有 owner，或刷新已确认仍成立的精确 `NONE` 指纹，并运行 selector 合同确认不存在重叠策略。

- [ ] **Step 6: 运行绿灯测试与 selector 合同**

  Run: `uv run pytest tests/callback/test_callback_mirror_integration.py tests/callback/test_contract_mismatch_diagnostics.py tests/runtime/orchestration/test_trace_response_builder.py tests/scripts -q`

  Expected: PASS。

  Run: `./scripts/run_selected_heavy_local.sh --scope unstaged`

  Expected: PostgreSQL 退役验收实际执行且无跳过。

- [ ] **Step 7: 提交原子变更**

  按全局原子提交门禁精确暂存、执行 staged HEAVY 和 GitNexus 检测；全部通过后运行：

  Run: `git commit -m "refactor(runtime): 移除退役插件诊断身份"`

### Task 3: 移除 RuntimeHold 与 NG 合同中的插件身份

**Files:**

- Create: `tests/runtime/orchestration/test_runtime_hold_plugin_identity_absence.py`
- Modify: `tests/integration/test_workline_plugin_schema_retirement.py`
- Modify: `src/app/runtime/capabilities/material_flow/contracts/ng_reason.py`
- Modify: `src/app/runtime/orchestration/models/{runtime_hold.py,runtime_hold_api.py}`
- Modify: `src/app/runtime/orchestration/services/hold/{runtime_hold_query_service.py,runtime_hold_release_service.py}`
- Modify: `migrations/versions/<generated>_remove_retired_plugin_residuals.py`
- Modify: `docs/architecture/heavy-test-impact.toml`

**Interfaces:**

- Consumes: `DEVICE_ERROR`、`RUNTIME`、`MANUAL` 三类 NG reason 及 RuntimeHold/NgReturnItem 当前释放流程。
- Produces: 不含插件身份的 RuntimeHold/NG API、ORM 和 PostgreSQL 约束。

- [ ] **Step 1: 执行影响分析**

  对 `NgReasonSource`、`NgReasonDefinition`、`build_ng_reason_catalog` 使用 `file_path="src/app/runtime/capabilities/material_flow/contracts/ng_reason.py"` 运行 upstream impact；对持久化的 `RuntimeHold`、`NgReturnItem` 使用 `file_path="src/app/runtime/orchestration/models/runtime_hold.py"` 运行 upstream impact。不得误选 `src/app/runtime/orchestration/runtime_hold.py` 中待 Phase 10 处置的同名旧模型。

  Expected: 明确 release/query/safety 调用者；HIGH/CRITICAL 时先取得用户确认。

- [ ] **Step 2: 写失败测试**

  测试必须证明 `NgReasonSource` 只含 `DEVICE_ERROR/RUNTIME/MANUAL`，`NgReasonDefinition`、RuntimeHold summary、NgReasonOption 和 ORM 模型均不含插件身份字段；内置三类 reason 仍可构建并按 code 查询。PostgreSQL 验收必须分别证明两张表接受三个合法 source，并拒绝 `PLUGIN`。

- [ ] **Step 3: 运行红灯测试**

  Run: `uv run pytest tests/scripts -q`

  Run: `uv run pytest tests/runtime/orchestration/test_runtime_hold_plugin_identity_absence.py tests/resource/test_resource_relation_service.py tests/workline_runtime/test_wms_sync_obligation_resolution.py tests/workline_runtime/test_workline_runtime_status_projection_service.py -q`

  Run: `./scripts/run_selected_heavy_local.sh --scope unstaged`

  Expected: FAST 与 PostgreSQL 退役验收均 FAIL，原因是 `PLUGIN`、插件字段或旧 CHECK 仍存在；不得因环境跳过。

- [ ] **Step 4: 实现最小删除**

  删除 `PLUGIN` enum member、`plugin_reasons` 扩展参数、`plugin_key` 和插件 `contract_version` 字段及查询映射。保留硬件错误、运行时错误和人工判定，不改变 RuntimeHold 状态机或资源冲突逻辑。

- [ ] **Step 5: 扩展唯一 schema revision**

  继续编辑 Task 2 生成的 revision：删除 `wes_biz.runtime_holds` 的插件字段和索引，并在 `runtime_holds`、`ng_return_items` 上重建只允许 `DEVICE_ERROR/RUNTIME/MANUAL` 的 `ng_reason_source` CHECK。扩展 PostgreSQL 退役验收验证两张表的列与 CHECK；不得转换旧数据，存在 `PLUGIN` 开发数据时要求清库后重试。不得新增第二条 revision 或重复增加 HEAVY owner。

- [ ] **Step 6: 运行绿灯和 PostgreSQL 选择器**

  Run: `uv run pytest tests/runtime/orchestration/test_runtime_hold_plugin_identity_absence.py tests/resource/test_resource_relation_service.py tests/workline_runtime/test_wms_sync_obligation_resolution.py tests/workline_runtime/test_workline_runtime_status_projection_service.py tests/scripts -q`

  Run: `./scripts/run_selected_heavy_local.sh --scope unstaged`

  Expected: 所有选中测试实际执行，JUnit 中 `total > 0` 且 `skipped = 0`。

- [ ] **Step 7: 提交原子变更**

  按全局原子提交门禁精确暂存、执行 staged HEAVY 和 GitNexus 检测；全部通过后运行：

  Run: `git commit -m "refactor(runtime): 移除退役插件暂停身份"`

### Task 4: 移除 RuntimeIntentLog 的显式插件字段和错误默认值

**Files:**

- Modify: `tests/workline_runtime/system_capabilities/test_system_capability_effect_service.py`
- Modify: `tests/workline_runtime/test_effect_state_contract.py`
- Modify: `tests/unit/runtime/orchestration/test_runtime_inbox_state_machine.py`
- Modify: `tests/contracts/workline/test_runtime_snapshot_contract.py`
- Modify: `tests/integration/test_runtime_intent_log_effect_repository.py`
- Modify: `tests/integration/test_runtime_intent_log_idempotency.py`
- Modify: `tests/integration/test_workline_plugin_schema_retirement.py`
- Modify: `src/app/runtime/orchestration/runtime_intent_log.py`
- Modify: `src/app/runtime/orchestration/repositories/runtime_intent_log_repository.py`
- Modify: `src/app/runtime/orchestration/services/intent/system_capability_intent_service.py`
- Modify: `migrations/versions/<generated>_remove_retired_plugin_residuals.py`
- Modify: `docs/architecture/heavy-test-impact.toml`

**Interfaces:**

- Consumes: `(provider_code, operation_kind, idempotency_key)` 唯一身份、显式 `operation_kind`、capability/provider/precondition 快照和 dispatch key。
- Produces: 无插件列、无插件执行 identity 分支、且不可能默认为 `plugin_intent` 的 RuntimeIntentLog。

- [ ] **Step 1: 重新执行 HIGH 风险分析并等待确认**

  对 `src/app/runtime/orchestration/runtime_intent_log.py:RuntimeIntentLog`、`src/app/runtime/orchestration/repositories/runtime_intent_log_repository.py:RuntimeIntentLogRepository.claim_or_match`、`src/app/runtime/orchestration/services/intent/system_capability_intent_service.py:SystemCapabilityIntentService._validate_execution_identity` 分别运行 upstream impact，并把每项冒号前的路径传入 GitNexus `file_path` 参数。

  Expected: `RuntimeIntentLog` 仍为 HIGH 时，向用户报告直接调用者和受影响测试，取得明确继续授权后才能执行后续步骤。

- [ ] **Step 2: 写失败测试**

  增加断言证明 RuntimeIntentLog 表不含 `plugin_key/plugin_contract_version`，并用符合 SQLModel table model 实际语义的三层验收约束 `operation_kind`：

  - `RuntimeIntentLog.model_fields["operation_kind"].is_required()` 为真，ORM 列 `nullable` 为假，PostgreSQL `column_default` 为 `NULL`；
  - `RuntimeIntentLogRepository.claim_or_match()` 缺失或空白 `operation_kind` 时，在访问数据库前明确抛出 `ValueError`；
  - PostgreSQL 直接写入缺失 `operation_kind` 时触发 `NOT NULL`。

  claim 不再要求插件 identity key；保留 request hash、correlation、owner snapshot 和 dispatch key 冲突检测。不得把 SQLModel 构造器是否立即报错作为验收条件。

- [ ] **Step 3: 运行红灯测试**

  Run: `uv run pytest tests/scripts -q`

  Run: `uv run pytest tests/workline_runtime/system_capabilities/test_system_capability_effect_service.py tests/workline_runtime/test_effect_state_contract.py tests/unit/runtime/orchestration/test_runtime_inbox_state_machine.py tests/contracts/workline/test_runtime_snapshot_contract.py -q`

  Run: `./scripts/run_selected_heavy_local.sh --scope unstaged`

  Expected: FAST 与 PostgreSQL 退役验收均 FAIL，原因是旧字段、旧 schema、SQLModel 默认值或 PostgreSQL `server_default='plugin_intent'` 仍存在；不得因环境跳过。

- [ ] **Step 4: 删除显式插件身份**

  删除模型、claim、repository insert 和 execution identity 中的两个插件字段；删除未被调用且仍拼接 binding 的 `_business_owner_key`；将 `operation_kind` 改为无默认值的必填字段，并在 repository 入口对缺失或空白值抛出清晰的 `ValueError`。保留当前非插件 domain owner 使用的 `binding_snapshot_json`，不扩大到 Phase 10 Generic Intent/Effect 删除。

- [ ] **Step 5: 完成唯一 schema revision**

  继续编辑 Task 2 生成的 revision：删除 `wes_runtime.runtime_intent_logs.plugin_key` 的索引以及 `plugin_key/plugin_contract_version` 两列，并对 `operation_kind` 执行 `alter_column(..., server_default=None)`，同时保持 `NOT NULL`。扩展 PostgreSQL 退役验收，证明当前 head 与 ORM 都不存在插件字段、`column_default IS NULL`，且直接写入缺失 `operation_kind` 会触发 `NOT NULL`；不转换数据、不提供 downgrade，也不得新增第三条 revision。

- [ ] **Step 6: 运行 FAST 与 PostgreSQL 验收**

  Run: `uv run pytest tests/workline_runtime/system_capabilities/test_system_capability_effect_service.py tests/workline_runtime/test_effect_state_contract.py tests/unit/runtime/orchestration/test_runtime_inbox_state_machine.py tests/contracts/workline/test_runtime_snapshot_contract.py -q`

  Run: `./scripts/run_selected_heavy_local.sh --scope unstaged`

  Expected: 所有选中测试实际执行且无跳过；空库 head 与 ORM 字段一致。

- [ ] **Step 7: 提交原子变更**

  按全局原子提交门禁精确暂存、执行 staged HEAVY 和 GitNexus 检测；全部通过后运行：

  Run: `git commit -m "refactor(runtime): 移除退役插件意图身份"`

### Task 5: 删除无生产消费者的旧 replay 与观测语义

**Files:**

- Modify: `tests/contracts/system_capabilities/test_query_evidence_contract.py`
- Modify: `tests/runtime/orchestration/test_northbound_operation_observability.py`
- Delete: `src/app/runtime/system_capabilities/replay.py`
- Delete: `src/app/runtime/orchestration/repositories/timeline_recorded_replay_repository.py`
- Modify: `src/app/runtime/system_capabilities/__init__.py`
- Modify: `src/app/runtime/orchestration/repositories/__init__.py`
- Modify: `src/app/runtime/orchestration/{observability.py,operation_observability.py}`
- Modify: `docs/contracts/observability-contract.md`
- Modify: `docs/architecture/heavy-test-impact.toml`

**Interfaces:**

- Consumes: 当前 QueryEvidence 合同和仍在使用的北向观测阶段。
- Produces: 无 `PLUGIN_DECISION` reader、无 `PLUGIN_EXECUTION` stage、无仅由测试支撑的 replay service。

- [ ] **Step 1: 证明没有生产消费者**

  对 `src/app/runtime/system_capabilities/replay.py:TimelineRecordedReplayService`、`src/app/runtime/orchestration/repositories/timeline_recorded_replay_repository.py:TimelineRecordedReplayRepository` 和 `src/app/runtime/orchestration/operation_observability.py:NorthboundTraceStage` 分别使用对应 `file_path` 运行 GitNexus context/impact。

  分层执行以下只读证明，禁止把定义、导出、测试和计划正文混在一个人工解释的命中列表中：

  Run: `rg -n "TimelineRecordedReplay(Service|Repository)" src --glob '*.py' --glob '!**/replay.py' --glob '!**/timeline_recorded_replay_repository.py' --glob '!**/__init__.py'`

  Expected: 无输出，证明没有定义与导出之外的生产消费者。

  Run: `rg -n 'record_type[^\n]*PLUGIN_DECISION|PLUGIN_DECISION[^\n]*record_type' src --glob '*.py' --glob '!**/replay.py'`

  Expected: 无输出，证明没有生产写入者。

  Run: `rg -n "stage\\s*=\\s*['\\\"]PLUGIN_EXECUTION['\\\"]" src --glob '*.py'`

  Expected: 无输出，证明没有生产发射点；类型定义和 closed-set 常量另行列示，不得算作发射。

  Run: `rg -n "TimelineRecordedReplay|PLUGIN_DECISION|PLUGIN_EXECUTION" tests docs --glob '*.py' --glob '*.md'`

  Expected: 仅列出待修改测试、当前合同和本计划，用于建立删除清单，不作为生产消费者证据。

- [ ] **Step 2: 先保住 QueryEvidence 与现行观测合同**

  修改既有测试，使其继续验证 QueryEvidence hash/identity 和当前北向阶段，但不再构造 recorded plugin decision 或期待 `PLUGIN_EXECUTION`。

- [ ] **Step 3: 删除无 owner 闭包并运行测试**

  删除 replay service/repository 及导出，删除旧观测阶段，同步观测合同中的阶段顺序。为两个删除路径保留全局约束指定的精确 `NONE` tombstone，为仍存活的 `system_capabilities/__init__.py` 增加精确、带最终内容指纹的 `NONE` mapping。

  Run: `uv run pytest tests/scripts -q`

  Run: `uv run pytest tests/contracts/system_capabilities/test_query_evidence_contract.py tests/runtime/orchestration/test_northbound_operation_observability.py -q`

  Expected: PASS；QueryEvidence 仍有唯一测试 owner。

- [ ] **Step 4: 运行缺席扫描并提交**

  Run: `rg -n "PLUGIN_BINDING_REQUIRED|PLUGIN_EXECUTION_FAILED|PLUGIN_TRANSITION_INVALID|PLUGIN_DECISION|PLUGIN_EXECUTION|plugin_intent|plugin_key|plugin_contract_version|ErrorDomain\\.PLUGIN" src --glob '*.py'`

  Expected: 无输出。

  按全局原子提交门禁精确暂存、执行 staged HEAVY 和 GitNexus 检测；全部通过后运行：

  Run: `git commit -m "refactor(runtime): 删除退役插件重放语义" -m "测试承接: NONE；该 replay 闭包无生产调用者"`

### Task 6: 总体验证、独立评审和 Phase 11 交接

**Files:**

- Modify only if evidence requires: `docs/architecture/file_index.md`
- Modify: `docs/superpowers/plans/2026-08-03-wes-architecture-convergence-master-plan.md`
- Modify: `docs/superpowers/README.md`
- Preserve: `docs/plugin_development_guide.md`
- Preserve: `tests/integration/test_workline_plugin_schema_retirement.py`
- Archive externally when completed: `docs/superpowers/plans/2026-08-15-wes-retired-plugin-residual-convergence.md` → `../archive_docs/wes_backend/docs/superpowers/plans/2026-08-15-wes-retired-plugin-residual-convergence.md`

**Interfaces:**

- Consumes: Tasks 1–5 的原子提交。
- Produces: 活动源码/schema 零旧插件身份证据，以及对 Phase 11 历史 revision 清理的明确交接。

- [ ] **Step 1: 运行架构与测试拓扑门禁**

  Run: `uv run pytest tests/architecture/test_business_legacy_absence_guardrail.py tests/architecture/test_core_plugin_test_ownership_guardrail.py tests/contracts/test_business_legacy_matrix_closure.py tests/architecture/test_suite_topology_guardrail.py -q`

  Expected: PASS。

- [ ] **Step 2: 验证默认收集与质量门禁**

  Run: `uv run pytest --collect-only -q -o addopts='' | tail -5`

  Run: `./scripts/git-quality-gate.sh --profile quality`

  Expected: 收集成功；质量门禁退出码 `0`。

- [ ] **Step 3: 验证完整实施差异的 HEAVY 选择与实际执行**

  Run: `uv run scripts/select_heavy_tests.py --base <implementation_base_sha>`

  Run: `./scripts/run_selected_heavy_local.sh --base <implementation_base_sha>`

  Expected: 新合并 schema revision 新增的 mapping 只指向 PostgreSQL 退役验收；完整分支差异还必须选择并实际运行所有被既有 repository/service/model 映射命中的 HEAVY，全部无跳过，且 selector 合同未报告宽窄 mapping 重叠。

- [ ] **Step 4: 对完整实施差异运行 GitNexus 变更检测**

  Run `gitnexus_detect_changes({scope: "compare", base_ref: "<implementation_base_sha>"})`。

  Expected: 变更只影响诊断、RuntimeHold/NG、RuntimeIntentLog、旧 replay/观测和对应测试/schema；不得包含 WMS-WES DTO 文档。

- [ ] **Step 5: 发起独立代码评审**

  使用 `superpowers:requesting-code-review` 对 `<implementation_base_sha>` 以来的完整 diff 评审；存在意见则通过 `superpowers:receiving-code-review` 核实并按 TDD 修复。任何修复必须回到所属 Task 2–5，重新执行全局精确暂存、staged selector/HEAVY、GitNexus 检测和原子提交，再完整重跑本 Task Steps 1–4 并重新评审；循环至无可操作意见后才允许合入。

- [ ] **Step 6: 确认 Phase 11 剩余边界**

  Run: `rg -n "plugin_key|plugin_contract_version|smt_classifier|rough_sorter|smt_sorting_inbound" migrations/versions --glob '*.py'`

  Expected: 只允许历史 revision 命中；活动 `src/` 已零命中。历史 revision 的唯一后续 owner 为 `docs/superpowers/plans/2026-08-15-wes-schema-and-migration-baseline-reset.md`。

  该零命中结论只覆盖 Phase 5–7 退役闭包。Phase 8/9 若引入目标插件身份，必须以设计 SPEC 为依据并限制在 `LineRunEpoch`、插件 SPI、Composition Root 及插件独立包中；任何旧 Trace/Diagnostic/Hold/Intent 字段或旧错误码回流都视为门禁失败。

- [ ] **Step 7: 合入后清理 deletion tombstone mappings**

  含源文件删除的实施分支合入 `develop` 后，以独立 cleanup 提交只删除 `replay.py` 和 `timeline_recorded_replay_repository.py` 的两个精确 tombstone mappings；不得删除仍存活 `system_capabilities/__init__.py` 的 reviewed `NONE` mapping。运行 `uv run pytest tests/scripts -q`，并以 cleanup 提交的 staged diff 运行 selector 和 `gitnexus_detect_changes({scope: "staged"})`；全部通过后提交 `chore(test): 清理退役路径 HEAVY tombstone`。

  Expected: cleanup 差异不再包含已删除生产路径，selector 合同 PASS，`develop` 上不保留无后续分类用途的 deletion tombstone。

- [ ] **Step 8: 更新当前态引用并归档完成计划**

  先把 master plan 和 `docs/superpowers/README.md` 更新为完成状态，并确认项目内不再把本计划当作待执行入口。计算源文件 SHA-256，确认目标文件不存在；若目标重名则先确定新的唯一文件名，不得覆盖。随后把本计划移动到上述项目外精确路径，验证项目内原路径缺席、归档文件存在且 SHA-256 与源文件一致；项目内不得保留副本、占位、软链接或转发文档。

  只精确暂存 master plan、README、确有证据需修改的 file index 和本计划源路径删除；核对 `git diff --cached --name-status` 后运行 `git diff --check`、项目内引用扫描和 `gitnexus_detect_changes({scope: "staged"})`，全部通过后提交 `docs(architecture): 归档退役插件残余收敛计划`。
