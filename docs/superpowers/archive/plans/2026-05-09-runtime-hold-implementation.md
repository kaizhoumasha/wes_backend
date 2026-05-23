# Runtime Hold Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 runtime reconciliation、ACK 耗尽、执行未知和安全冻结收敛到 `RuntimeHold` 单一事实源；拓扑/Sandbox 只展示摘要与入口，完整处置在 Runtime Hold 页面完成，并支持 `CONTINUE` / `RETURN_TO_NG` 物料处置。

**Architecture:** 后端新增 `RuntimeHold`、`NgReturnItem`、`RuntimeHoldReleaseService` 和插件物料身份/NG 原因契约；异常发生事务内创建 Hold；释放 WorkLine 与 blocked outbox 只能通过 ReleaseService；前端新增 `/runtime/holds/:holdId` 页面，运行态视图改为读取 Hold 投影，不再按 session reconciliation 自行推断。

**Tech Stack:** FastAPI + SQLModel + SQLAlchemy Async + Alembic + pytest；Vue 3 + Vite + Pinia + Element Plus + OpenAPI generated contract + pnpm。

---

## 规格来源

本计划执行并固化以下设计文档：

- `docs/superpowers/specs/2026-05-09-runtime-reconciliation-case-design.md`

关键不变量：

- `RuntimeHold` 是运行时异常恢复的权威事实源；Reconciliation 只是 `hold_type` 或 UI 标签。
- GET 不创建 Hold；历史缺失 Hold 只能由显式 repair job 补齐。
- WorkLine release 只能由 `RuntimeHoldReleaseService` 执行。
- `RETURN_TO_NG` release 必须有服务端可校验的物理交接证据和插件解析的 `MaterialIdentity`。
- 拓扑数字只表示设备未完成命令；`BLOCKED_RESOURCE` 和异常 Hold 分开显示。

## 当前代码地图

后端：

- `src/app/workline/models/session.py`：现有 session reconciliation 字段，后续仅作为来源事实/过渡投影。
- `src/app/workline/models/safety.py`：`WorkLineRuntimeStatus` 与 `WorklineSafetyIncident`。
- `src/app/workline/models/outbox.py`：现有 blocked 字段，需要新增 `blocked_by_runtime_hold_id` 并迁移写路径。
- `src/app/workline/models/operation.py`：现有 `ResolveRuntimeReconciliationRequest`，会新增 Runtime Hold schema。
- `src/app/workline/services/runtime_reconciliation_service.py`：ACK 耗尽/Callback 超时进入异常的事务边界。
- `src/app/workline/services/safety_service.py`：ESTOP 进入/解除边界，解除线体路径必须改走 ReleaseService。
- `src/app/workline/services/runtime_query_service.py`：拓扑和运行态投影计数。
- `src/app/workline/services/operation_service.py` 与 `src/app/workline/v1/operation.py`：现有 sandbox/reconciliation API。
- `src/app/workline/repositories/outbox_repository.py`：sandbox pending/completed 与 blocked outbox 查询。
- `src/workline_runtime/plugin_manifest.py`：插件 manifest 契约入口。
- `src/workline_runtime/types.py` 与 `src/workline_runtime/plugin_base.py`：业务判定和插件返回意图。
- `src/workline_plugins/smt_classifier/plugin.py` / `contract.py`：SMT 插件 NG reason 与 material identity 第一批实现。
- `migrations/versions/`：Alembic migration 目录；新迁移必须用 `uv run alembic revision -m "<message>"` 生成后再编辑。

前端：

- `frontend/src/router/routes/runtime.ts`：新增 `/runtime/holds/:holdId` 路由。
- `frontend/src/api/modules/workline.ts`、`frontend/src/api/generated/*`：OpenAPI contract 生成结果。
- `frontend/src/types/runtime.ts`：运行态手写类型聚合。
- `frontend/src/stores/workline-runtime.ts`：工作线运行态 store。
- `frontend/src/views/runtime/worklines/WorklineRuntimePage.vue`：工作线主视图。
- `frontend/src/views/runtime/sandbox/RuntimeSandboxPage.vue`：Sandbox 入口。
- `frontend/src/components/common/runtime/WorklineRouteMap.vue`：拓扑节点计数与异常标记。
- `frontend/src/components/common/runtime/WorklineReconciliationPanel.vue`：旧 session 对账面板，需降级为 Hold 入口或删除。
- `frontend/src/components/common/runtime/SandboxActionList.vue` / `SandboxPendingQueue.vue`：命令列表与历史展示。

## 任务 1：建立 Runtime Hold 与 NG Return 持久模型

- [ ] 后端测试先行：新增 `tests/workline_runtime/test_runtime_hold_models.py`。
  - 覆盖 `RuntimeHold.active blocking` 定义。
  - 覆盖 `source_idempotency_key` 唯一。
  - 覆盖 `NgReturnItem` 同一 Hold + material identity 幂等约束。
  - 覆盖 `RETURN_TO_NG` 的服务端确认字段不能由客户端写入。

- [ ] 新增 `src/app/workline/models/runtime_hold.py`。
  - 枚举：
    - `RuntimeHoldType`: `RUNTIME_RECONCILIATION`, `SAFETY_ESTOP`, `MANUAL_HOLD`
    - `RuntimeHoldStatus`: `OPEN`, `IN_PROGRESS`, `RESOLVED`, `VOIDED`, `REOPENED`
    - `MaterialDisposition`: `CONTINUE`, `RETURN_TO_NG`
    - `NgReasonSource`: `PLUGIN`, `DEVICE_ERROR`, `RUNTIME`, `MANUAL`
    - `NgReturnItemStatus`: `WAITING_REWORK`, `REWORKING`, `REWORKED`, `CANCELLED`
  - 模型：
    - `RuntimeHold(EnterpriseMixin, DataTableMixin, table=True)`
    - `NgReturnItem(EnterpriseMixin, DataTableMixin, table=True)`
  - `RuntimeHold` 必须包含 SPEC 中的 `source_*`、`evidence_snapshot_json`、`release_evidence_json`、`material_disposition`、`ng_reason_*`、`resolved_*`、`reopened_from_hold_id` 字段。
  - `NgReturnItem` 必须包含 source workline/session/command/event、material identity JSON、handoff evidence JSON、`created_from_runtime_hold_id`、`status`。

- [ ] 更新模型导出。
  - `src/app/workline/models/__init__.py`
  - 如已有集中 metadata 注册逻辑，加入 `RuntimeHold` / `NgReturnItem`。

- [ ] 使用 Alembic generator 创建迁移。

```bash
cd backend
uv run alembic revision -m "add runtime hold"
```

- [ ] 编辑生成的 migration。
  - 新建 `wes_biz.runtime_holds`。
  - 新建 `wes_biz.ng_return_items`。
  - `wes_biz.workline_outboxes` 新增 nullable `blocked_by_runtime_hold_id`。
  - 添加必要索引：
    - `runtime_holds.source_idempotency_key` unique
    - active 查询索引：`workline_id,status,blocking`
    - `source_session_id/source_outbox_id/source_command_id/source_device_id`
    - `ng_return_items.created_from_runtime_hold_id`
    - `ng_return_items.material_identity_key`
  - 不在本迁移删除 `blocked_by_reconciliation_session_id`；本版本先迁移写路径，后续清理。

- [ ] 运行后端模型测试。

```bash
cd backend
uv run pytest tests/workline_runtime/test_runtime_hold_models.py
```

- [ ] Commit 1。

```bash
cd backend
git status --short
git add src/app/workline/models/runtime_hold.py src/app/workline/models/__init__.py migrations/versions/*_add_runtime_hold.py tests/workline_runtime/test_runtime_hold_models.py
git commit -m "feat(workline): add runtime hold models"
```

## 任务 2：新增 Runtime Hold Repository 与异常创建服务

- [ ] 后端测试先行：新增 `tests/workline_runtime/test_runtime_hold_repository.py`。
  - `create_open_hold` 对同一 `source_idempotency_key` 幂等。
  - `get_active_blocking_by_workline` 只返回 `OPEN/IN_PROGRESS/REOPENED + blocking=true`。
  - `get_for_update` 支持 release 并发控制。
  - `count_open_issues_by_device` 基于 active Hold 的 `source_device_id` 聚合。

- [ ] 新增 `src/app/workline/repositories/runtime_hold_repository.py`。
  - 方法：
    - `get_by_id`
    - `get_for_update`
    - `get_by_source_idempotency_key`
    - `create_open_hold`
    - `get_active_blocking_by_workline`
    - `count_active_by_workline`
    - `count_open_issues_by_device`
    - `list_ng_return_items`

- [ ] 新增 `src/app/workline/services/runtime_hold_creation_service.py`。
  - 只负责异常发生时创建/复用 Hold，不负责 resolve。
  - 提供：
    - `create_for_callback_deadline_expired`
    - `create_for_dispatch_ack_exhausted`
    - `create_for_safety_estop`
  - `source_idempotency_key` 规则必须稳定：
    - `callback-timeout:{session_id}:{inbox_id}`
    - `dispatch-ack-exhausted:{outbox_id}:{command_id or "no-command"}`
    - `safety-estop:{incident_id}`
  - `evidence_snapshot_json` 只存可审计摘要，不复制整条 trace。

- [ ] 更新仓库导出。
  - `src/app/workline/repositories/__init__.py`
  - `src/app/workline/services/__init__.py`

- [ ] 运行测试。

```bash
cd backend
uv run pytest tests/workline_runtime/test_runtime_hold_repository.py
```

- [ ] Commit 2。

```bash
cd backend
git add src/app/workline/repositories/runtime_hold_repository.py src/app/workline/repositories/__init__.py src/app/workline/services/runtime_hold_creation_service.py src/app/workline/services/__init__.py tests/workline_runtime/test_runtime_hold_repository.py
git commit -m "feat(workline): create runtime holds idempotently"
```

## 任务 3：插件 MaterialIdentityResolver 与 NG reason taxonomy

- [ ] 后端测试先行。
  - 新增 `tests/workline_runtime/test_material_identity.py`。
  - 新增 `tests/workline_runtime/test_ng_reason_catalog.py`。
  - 扩展 `tests/workline_plugins/test_smt_classifier_contract.py`。
  - 覆盖：
    - `PkgID/HHPN/LotCode` 只能是 display，不是平台自造 identity。
    - resolver 返回 `RESOLVED/AMBIGUOUS/MISSING`。
    - `RETURN_TO_NG` 所需 material identity 必须 `RESOLVED`。
    - SMT 插件将 `SCAN_NG`、`INSPECTION_SIZE_NG` 等映射到 canonical taxonomy。
    - 未映射 reason 报告为可修复错误，不静默落到 `UNKNOWN`。

- [ ] 新增 `src/workline_runtime/material_identity.py`。
  - `MaterialIdentityResolutionStatus`
  - `MaterialIdentity`
  - `MaterialIdentityInput`
  - `MaterialIdentityResolver = Callable[[MaterialIdentityInput], MaterialIdentity]`
  - helper `hash_material_evidence`，使用 canonical JSON + sha256。

- [ ] 新增 `src/workline_runtime/ng_reason.py`。
  - `NgReasonSource`
  - `NgReasonDefinition`
  - `NgReasonCatalog`
  - 系统内置兜底：
    - `UNKNOWN_PHYSICAL_STATE`
    - `OPERATOR_JUDGED_NG`
    - `RUNTIME_RECOVERY_NG`

- [ ] 扩展 `src/workline_runtime/plugin_manifest.py`。
  - 新增字段：
    - `material_identity_resolver: MaterialIdentityResolver | None`
    - `ng_reason_catalog: Sequence[NgReasonDefinition]`
  - 新增方法：
    - `resolve_material_identity(input: MaterialIdentityInput) -> MaterialIdentity`
    - `list_ng_reasons() -> Sequence[NgReasonDefinition]`
  - 没有 resolver 时返回 `MISSING`，不能用 display 字段兜底生成 identity。

- [ ] 实现 SMT 插件契约。
  - `src/workline_plugins/smt_classifier/contract.py`
  - `src/workline_plugins/smt_classifier/plugin.py`
  - material identity 第一版使用插件业务规则生成 `idempotency_key`，例如 `smt:{PkgID}`，缺失或多候选时返回 `MISSING/AMBIGUOUS`。
  - NG reason catalog 映射已有 `business_decision(reason_code)`：
    - `SCAN_NG`
    - `SCAN_NG_BY_RULE`
    - `INSPECTION_SIZE_NG`
    - `INSPECTION_THICKNESS_NG`
    - `BARCODE_INVALID`
    - `BARCODE_INCOMPLETE`

- [ ] 运行测试。

```bash
cd backend
uv run pytest tests/workline_runtime/test_material_identity.py tests/workline_runtime/test_ng_reason_catalog.py tests/workline_plugins/test_smt_classifier_contract.py
```

- [ ] Commit 3。

```bash
cd backend
git add src/workline_runtime/material_identity.py src/workline_runtime/ng_reason.py src/workline_runtime/plugin_manifest.py src/workline_plugins/smt_classifier/contract.py src/workline_plugins/smt_classifier/plugin.py tests/workline_runtime/test_material_identity.py tests/workline_runtime/test_ng_reason_catalog.py tests/workline_plugins/test_smt_classifier_contract.py
git commit -m "feat(workline): add material identity and ng reasons"
```

## 任务 4：异常发生事务内创建 RuntimeHold

- [ ] 后端测试先行：扩展 `tests/workline_runtime/test_runtime_reconciliation_service.py`、`tests/workline_runtime/test_timeout_scanner.py`、`tests/workline_runtime/test_workline_safety_service.py`。
  - Callback deadline timeout 创建 `RuntimeHold`，且同一 inbox 重放不重复创建。
  - Dispatch ACK exhausted 创建 `RuntimeHold`，`source_reason` 区分 `COMMAND_ACK_EXHAUSTED` / `OUTBOX_DISPATCH_FAILED`。
  - GET/read resolver 不创建 Hold。
  - ESTOP 创建 safety hold。
  - 同一 WorkLine 多个 active blocking Hold 并存时，WorkLine 仍保持隔离。

- [ ] 修改 `src/app/workline/services/runtime_reconciliation_service.py`。
  - 在 `handle_timer_timeout` 写 session reconciliation 的同一事务内调用 `RuntimeHoldCreationService.create_for_callback_deadline_expired`。
  - 在 `handle_dispatch_ack_exhausted` 同一事务内调用 `create_for_dispatch_ack_exhausted`。
  - 返回值仍可保持 session 以减少调用方改动，但需要在 evidence/timeline 中记录 hold id。

- [ ] 修改 `src/app/workline/services/safety_service.py`。
  - `simulate_estop` / ESTOP 入口创建 `WorklineSafetyIncident` 后同步创建 `SAFETY_ESTOP` Hold。
  - 暂时保留 incident 作为安全来源事实，但 release 语义迁走。

- [ ] 修改 `src/app/workline/models/session.py` 或 timeline payload 需要的字段时，只做最小增量；不得新增第二套 reconciliation lifecycle。

- [ ] 运行测试。

```bash
cd backend
uv run pytest tests/workline_runtime/test_runtime_reconciliation_service.py tests/workline_runtime/test_timeout_scanner.py tests/workline_runtime/test_workline_safety_service.py
```

- [ ] Commit 4。

```bash
cd backend
git add src/app/workline/services/runtime_reconciliation_service.py src/app/workline/services/safety_service.py tests/workline_runtime/test_runtime_reconciliation_service.py tests/workline_runtime/test_timeout_scanner.py tests/workline_runtime/test_workline_safety_service.py
git commit -m "feat(workline): create holds at abnormal boundaries"
```

## 任务 5：实现 RuntimeHoldReleaseService

- [ ] 后端测试先行：新增 `tests/workline_runtime/test_runtime_hold_release_service.py`。
  - `CONTINUE` 不创建 `NgReturnItem`。
  - `RETURN_TO_NG` 缺少 `physical_handoff_evidence` 返回领域错误，不释放 WorkLine。
  - `RETURN_TO_NG` 缺少 `ng_reason_code` 返回领域错误。
  - 客户端提交 `handoff_confirmed_by` / `handoff_confirmed_at` / `material_identity` 被拒绝或忽略。
  - material identity `MISSING/AMBIGUOUS` 时拒绝 release。
  - stale `hold_version` 返回 version conflict。
  - stale `latest_evidence_hash` 返回 evidence changed。
  - 解决一个 Hold 后若 WorkLine 仍有其他 active blocking Hold，不写回 `READY`。
  - 最后一个 blocking Hold resolved 后才释放 `BLOCKED_RESOURCE` outbox。
  - 重复提交同一 Hold 不创建第二条 NG item。

- [ ] 新增 `src/app/workline/services/runtime_hold_release_service.py`。
  - 唯一入口：
    - `resolve_hold(db, hold_id, request, operator_id) -> RuntimeHoldDetailResponse`
  - 事务顺序：
    - lock `RuntimeHold`
    - lock `WorkLine`
    - lock related session / command / outbox rows
    - 校验 `hold_version`
    - 重新计算 `latest_evidence_hash`
    - 校验 checklist / legal outcome matrix
    - `RETURN_TO_NG` 时解析 material identity 并创建 `NgReturnItem`
    - 写 `release_evidence_json`
    - 标记 Hold `RESOLVED`
    - 重新查询 active blocking holds
    - 无 active blocking holds 时设置 WorkLine `READY` 并释放 blocked outbox

- [ ] 修改 `src/app/workline/repositories/outbox_repository.py`。
  - 新增 `block_by_runtime_hold` / `release_blocked_by_runtime_hold_or_workline`。
  - 后续写路径用 `blocked_by_runtime_hold_id`。
  - `blocked_by_reconciliation_session_id` 只读保留到 repair 完成。

- [ ] 修改旧 release 调用。
  - `src/app/workline/services/operation_service.py` 中 `resolve_runtime_reconciliation` 改为兼容入口：通过 session 找 active Runtime Hold，然后委托 ReleaseService。
  - `src/app/workline/services/safety_service.py` 的 `clear_estop` 不直接把 WorkLine 写回 `READY`，改为 resolve 对应 safety Hold 或调用 ReleaseService 的安全分支。

- [ ] 运行测试。

```bash
cd backend
uv run pytest tests/workline_runtime/test_runtime_hold_release_service.py tests/workline_runtime/test_workline_operation_service.py tests/workline_runtime/test_workline_safety_service.py
```

- [ ] Commit 5。

```bash
cd backend
git add src/app/workline/services/runtime_hold_release_service.py src/app/workline/repositories/outbox_repository.py src/app/workline/services/operation_service.py src/app/workline/services/safety_service.py tests/workline_runtime/test_runtime_hold_release_service.py tests/workline_runtime/test_workline_operation_service.py tests/workline_runtime/test_workline_safety_service.py
git commit -m "feat(workline): release worklines through runtime holds"
```

## 任务 6：Runtime Hold API 与错误合同

- [ ] 后端 API 测试先行：新增 `tests/api/test_runtime_hold_api.py`。
  - `GET /api/v1/workline/runtime-holds/{hold_id}` 返回 detail，不产生写入。
  - `POST /api/v1/workline/runtime-holds/{hold_id}/resolve` 支持 `CONTINUE`。
  - `POST /api/v1/workline/runtime-holds/{hold_id}/resolve` 支持 `RETURN_TO_NG`。
  - 缺证据 `422 RUNTIME_HOLD_MISSING_RELEASE_EVIDENCE`。
  - version conflict `409 RUNTIME_HOLD_VERSION_CONFLICT` 并返回最新决策模型。
  - evidence changed `409 RUNTIME_HOLD_EVIDENCE_CHANGED`。
  - already resolved `409 RUNTIME_HOLD_ALREADY_RESOLVED`。
  - `GET /api/v1/workline/runtime-holds/ng-reasons` 返回插件 reasons + 系统 fallback。
  - `GET /api/v1/workline/ng-return-items` 支持 hold/status/material key 过滤。

- [ ] 新增/整理 Pydantic schema。
  - 推荐新建 `src/app/workline/models/runtime_hold_api.py`，避免 `operation.py` 继续膨胀。
  - schema：
    - `RuntimeHoldSummary`
    - `RuntimeHoldSource`
    - `FailedCommandEvidence`
    - `RuntimeHoldReleaseEligibility`
    - `RuntimeHoldBlocker`
    - `RuntimeHoldDetailResponse`
    - `ResolveRuntimeHoldRequest`
    - `ResolveRuntimeHoldResponse`
    - `NgReasonOption`
    - `NgReturnItemResponse`

- [ ] 新增路由文件 `src/app/workline/v1/runtime_hold.py`。
  - 路径严格使用 SPEC：
    - `GET /runtime-holds/{hold_id}`
    - `POST /runtime-holds/{hold_id}/resolve`
    - `GET /runtime-holds/ng-reasons`
    - `GET /ng-return-items`
  - 权限：
    - `biz:workline:view-runtime-hold`
    - `biz:workline:resolve-runtime-hold`
    - `biz:workline:list-ng-return-item`

- [ ] 修改 `src/app/workline/v1/__init__.py` 或 app router 注册处，挂载新路由。

- [ ] 更新权限常量生成源。
  - 如果后端权限由路由 summary 扫描生成，确保 summary 使用上面的权限码。
  - 前端生成后应出现 `BIZ_PERMISSIONS.workline.viewRuntimeHold` 等稳定字段；若生成器命名不同，以生成结果为准同步前端引用。

- [ ] 错误响应实现。
  - 复用现有 `response_builder.fail`。
  - 对 `409` 错误，`data` 必须包含 `current_hold_version`、`current_status`、`release_eligibility`、`refresh_url`。
  - 不把领域错误吞成通用 `INVALID_STATE`。

- [ ] 运行 API 测试。

```bash
cd backend
uv run pytest tests/api/test_runtime_hold_api.py
```

- [ ] Commit 6。

```bash
cd backend
git add src/app/workline/models/runtime_hold_api.py src/app/workline/v1/runtime_hold.py src/app/workline/v1/__init__.py tests/api/test_runtime_hold_api.py
git commit -m "feat(workline): expose runtime hold api"
```

## 任务 7：Repair job 与迁移运行手册

- [ ] 后端测试先行：新增 `tests/workline_runtime/test_runtime_hold_repair.py`。
  - dry-run 不写数据库。
  - apply 幂等。
  - 重复 `source_idempotency_key` 不创建重复 Hold。
  - 输出 active reconciliation session 与 active runtime hold invariant。
  - 未映射 reason 和缺 material identity 被统计，不静默忽略。

- [ ] 新增 `scripts/data/repair_runtime_holds.py`。
  - CLI：

```bash
uv run python scripts/data/repair_runtime_holds.py --dry-run --limit 100
uv run python scripts/data/repair_runtime_holds.py --apply --limit 100
```

  - 只修复历史 `WorklineSession.reconciliation_state=PENDING` 但没有 RuntimeHold 的记录。
  - apply 使用唯一约束或行锁保证并发幂等。
  - 输出 JSON summary，包含：
    - `would_create`
    - `created`
    - `duplicates`
    - `unmapped_reasons`
    - `missing_material_identity`
    - `active_reconciliation_sessions`
    - `active_runtime_holds`

- [ ] 更新文档。
  - `docs/workline_diagnostics_quickstart.md` 或新建 `docs/workline_runtime_hold_quickstart.md`。
  - 包含登录 token 获取、curl、预期响应、常见 `409/422` 修复方式。

- [ ] 运行测试。

```bash
cd backend
uv run pytest tests/workline_runtime/test_runtime_hold_repair.py
```

- [ ] Commit 7。

```bash
cd backend
git add scripts/data/repair_runtime_holds.py tests/workline_runtime/test_runtime_hold_repair.py docs/workline_runtime_hold_quickstart.md
git commit -m "feat(workline): add runtime hold repair runbook"
```

## 任务 8：运行态投影和拓扑计数改为 Hold 语义

- [ ] 后端测试先行。
  - 扩展 `tests/workline_runtime/test_trace_query_service.py`、`tests/workline_runtime/test_outbox_repository.py` 或新增 `tests/workline_runtime/test_runtime_topology_projection.py`。
  - 覆盖：
    - `open_command_count_by_device` 只包含 `PENDING/SENT/ACK_RECEIVED`。
    - `FAILED/COMPLETED/CANCELLED/TIMEOUT` 不计入 open command。
    - `BLOCKED_RESOURCE` 只计入 `blocked_outbox_count_by_device`。
    - active RuntimeHold 计入 `open_issue_count_by_device`。
    - Sandbox failed history 显示 Hold 入口，不显示可操作 ACK/Result 按钮。

- [ ] 修改 `src/app/workline/models/runtime.py`。
  - `RuntimeWorklineDeviceItem` 增加：
    - `open_command_count`
    - `blocked_outbox_count`
    - `open_issue_count`
    - `active_runtime_hold_ids`
  - 兼容旧字段 `pending_command_count` 时明确等于 `open_command_count`。

- [ ] 修改 `src/app/workline/services/runtime_query_service.py`。
  - 聚合 command、blocked outbox、runtime hold 三组数字。
  - 不用 session reconciliation 直接推断 issue；只读 active RuntimeHold。

- [ ] 修改 sandbox projection。
  - `src/app/workline/services/operation_service.py`
  - `src/app/workline/repositories/outbox_repository.py`
  - pending/completed 名称可以暂时保留 API，但响应中要包含：
    - `is_actionable`
    - `runtime_hold_id`
    - `failure_summary`
    - `history_group_key` 或 session/event 分组字段

- [ ] 修改 trace/evidence builder。
  - `src/app/workline/services/trace_response_builder.py`
  - `RuntimeHoldDetailResponse.failed_command_evidence` 复用 trace、diagnostic、timeline，不重复拼第二套证据逻辑。

- [ ] 运行测试。

```bash
cd backend
uv run pytest tests/workline_runtime/test_runtime_topology_projection.py tests/workline_runtime/test_outbox_repository.py tests/workline_runtime/test_trace_query_service.py tests/workline_runtime/test_workline_operation_service.py
```

- [ ] Commit 8。

```bash
cd backend
git add src/app/workline/models/runtime.py src/app/workline/services/runtime_query_service.py src/app/workline/services/operation_service.py src/app/workline/repositories/outbox_repository.py src/app/workline/services/trace_response_builder.py tests/workline_runtime/test_runtime_topology_projection.py tests/workline_runtime/test_outbox_repository.py tests/workline_runtime/test_trace_query_service.py tests/workline_runtime/test_workline_operation_service.py
git commit -m "feat(workline): project topology from runtime holds"
```

## 任务 9：生成前端合同并接入 Runtime Hold API

- [ ] 后端 OpenAPI 生成/校验。
  - 先运行项目现有 OpenAPI 生成命令；如果命令名不确定，查看 `backend/pyproject.toml` 与 `frontend/package.json`。
  - 目标：前端 `frontend/src/api/generated/openapi-types.ts`、metadata、permissions 与 `frontend/src/api/modules/workline.ts` 同步。

- [ ] 前端类型与 API 封装。
  - 修改 `frontend/src/api/modules/workline.ts`，新增：
    - `runtimeHoldDetail`
    - `resolveRuntimeHold`
    - `runtimeHoldNgReasons`
    - `ngReturnItems`
  - 修改 `frontend/src/types/runtime.ts`，导出 Runtime Hold 页面所需的稳定 view model 类型。

- [ ] 前端 store。
  - 新增 `frontend/src/stores/runtime-hold.ts`。
  - state：
    - `detail`
    - `ngReasons`
    - `loading`
    - `submitting`
    - `lastConflict`
  - actions：
    - `loadHold(holdId)`
    - `loadNgReasons(pluginKey, contractVersion)`
    - `resolveHold(holdId, payload)`
    - `applyConflictModel(conflictData)`

- [ ] 前端测试先行。
  - 新增 `frontend/src/stores/__tests__/runtime-hold.spec.ts` 或现有测试目录对应文件。
  - 覆盖 `409` conflict 后更新 detail/eligibility。
  - 覆盖 reason catalog 失败时不允许提交。

- [ ] 运行合同和类型检查。

```bash
cd frontend
pnpm contract:verify
pnpm type:check
```

- [ ] Commit 9。

```bash
cd frontend
git add src/api/generated src/api/modules/workline.ts src/types/runtime.ts src/stores/runtime-hold.ts src/stores/__tests__/runtime-hold.spec.ts
git commit -m "feat(runtime): add runtime hold client contract"
```

## 任务 10：新增 Runtime Hold 页面和 PDA 布局

- [ ] 前端组件测试先行。
  - 新增 `frontend/src/views/runtime/holds/__tests__/RuntimeHoldPage.spec.ts`。
  - 覆盖：
    - loading
    - hold not found / permission denied
    - already resolved
    - stale version conflict
    - missing material identity
    - ambiguous material identity
    - reason catalog loading/error/empty
    - late callback invalidates decision
    - network retry
    - `RETURN_TO_NG` 必须先扫 NG 位置再扫物料

- [ ] 新增路由。
  - 修改 `frontend/src/router/routes/runtime.ts`：
    - `path: 'holds/:holdId'`
    - `name: 'RuntimeHoldDetail'`
    - `component: () => import('@/views/runtime/holds/RuntimeHoldPage.vue')`
    - `permission: BIZ_PERMISSIONS.workline.viewRuntimeHold` 或生成后的等价字段
  - 此路由可隐藏菜单，只作为拓扑/Sandbox/顶部卡入口。

- [ ] 新增页面和组件。
  - `frontend/src/views/runtime/holds/RuntimeHoldPage.vue`
  - `frontend/src/components/common/runtime/RuntimeHoldDecisionBar.vue`
  - `frontend/src/components/common/runtime/RuntimeHoldChecklist.vue`
  - `frontend/src/components/common/runtime/RuntimeHoldDispositionForm.vue`
  - `frontend/src/components/common/runtime/RuntimeHoldEvidencePanel.vue`
  - `frontend/src/components/common/runtime/RuntimeHoldAuditTrail.vue`
  - `frontend/src/components/common/runtime/RuntimeHoldConflictNotice.vue`

- [ ] 页面布局要求。
  - 桌面端：左侧决策/表单，右侧证据/timeline。
  - PDA 端：单列、扫码输入优先、底部固定主按钮、触控目标不小于 44px。
  - 第一屏先回答“能不能恢复线体”，再展示证据。
  - 不使用长段说明代替状态结构；状态必须由 badge、checklist、blocker list 表达。

- [ ] 表单规则。
  - `CONTINUE`：
    - checklist 全 true
    - operator note 必填
    - session outcome 可选 `COMPLETED/FAILED/CANCELLED`
    - 不显示 NG handoff 字段
  - `RETURN_TO_NG`：
    - ng reason 必填
    - 先扫 `ng_location_scan`
    - 再扫 `material_scan_payload`
    - `line_clear_checked` 与 `late_callback_reviewed` 必须 true
    - operator note 必填
    - 客户端不提交 `handoff_confirmed_by/at/material_identity`

- [ ] 运行前端测试和类型检查。

```bash
cd frontend
pnpm type:check
pnpm lint
```

- [ ] Commit 10。

```bash
cd frontend
git add src/router/routes/runtime.ts src/views/runtime/holds src/components/common/runtime/RuntimeHold*.vue src/stores/runtime-hold.ts
git commit -m "feat(runtime): add runtime hold page"
```

## 任务 11：改造工作线主视图、拓扑、Sandbox 入口

- [ ] 前端测试先行。
  - 扩展 Runtime workline/sandbox 组件测试。
  - 覆盖：
    - 拓扑数字只显示 `open_command_count`。
    - active Hold 显示异常标记和入口。
    - `BLOCKED_RESOURCE` 显示为 blocked outbox，不混入“待处理命令”。
    - `FAILED` 历史命令显示失败摘要和 Runtime Hold 入口，不显示模拟 ACK/RESULT。
    - 顶部卡片显示 active Hold 数、最严重 blocker、requirements 缺失数、物理交接证据状态和入口。

- [ ] 修改 `frontend/src/components/common/runtime/WorklineRouteMap.vue`。
  - `pendingCountsByDevice` 改名或语义收敛为 `openCommandCountsByDevice`。
  - 徽标文案从 “X 待处理” 改为 “X 未完成命令”。
  - `open_issue_count > 0` 显示异常标记，不混入数字徽标。
  - `blocked_outbox_count > 0` 显示 “已停靠” 状态。

- [ ] 修改 `frontend/src/views/runtime/worklines/WorklineRuntimePage.vue`。
  - 将旧 `WorklineReconciliationPanel` 替换为 Runtime Hold summary card。
  - 点击进入 `RuntimeHoldDetail`。
  - 设备 inspector 中分三组：
    - 未完成命令
    - 异常 Runtime Hold
    - 历史命令

- [ ] 修改 Sandbox 组件。
  - `frontend/src/views/runtime/sandbox/RuntimeSandboxPage.vue`
  - `frontend/src/components/common/runtime/SandboxActionList.vue`
  - `frontend/src/components/common/runtime/SandboxPendingQueue.vue`
  - `待操作命令` 文案改为 “未完成命令” 或 “需人工推进命令”；历史分组用 “历史命令”。
  - 同 Event/Session 分组展示 pending 与 history。
  - 对 terminal command 不显示操作按钮。

- [ ] 删除或降级旧入口。
  - `frontend/src/components/common/runtime/WorklineReconciliationPanel.vue` 如不再使用，删除；如仍作为兼容入口，改名或改为 Runtime Hold summary，不保留旧 release 表单。

- [ ] 运行前端检查。

```bash
cd frontend
pnpm type:check
pnpm lint
```

- [ ] Commit 11。

```bash
cd frontend
git add src/components/common/runtime/WorklineRouteMap.vue src/views/runtime/worklines/WorklineRuntimePage.vue src/views/runtime/sandbox/RuntimeSandboxPage.vue src/components/common/runtime/SandboxActionList.vue src/components/common/runtime/SandboxPendingQueue.vue src/components/common/runtime/WorklineReconciliationPanel.vue
git commit -m "feat(runtime): surface hold entrypoints in runtime views"
```

## 任务 12：端到端验证与浏览器验收

- [ ] 后端全量相关测试。

```bash
cd backend
uv run pytest tests/workline_runtime/ tests/api/test_runtime_hold_api.py tests/api/test_workline_runtime_api.py tests/api/test_workline_safety_operation_api.py
```

- [ ] 前端检查。

```bash
cd frontend
pnpm contract:verify
pnpm type:check
pnpm lint
```

- [ ] 本地联调服务。

```bash
cd backend
uv run uvicorn main:app --reload --host 0.0.0.0 --port 8001
```

```bash
cd frontend
pnpm dev
```

- [ ] 浏览器验收。
  - 使用 `admin` / `admin123` 登录。
  - 在 Sandbox 触发 ACK timeout / dispatch exhausted。
  - 验证拓扑：
    - FAILED command 不计入未完成命令。
    - 设备有异常 Hold 标记。
    - blocked outbox 独立显示。
  - 点击进入 `/runtime/holds/:holdId`。
  - 第一次选择 `RETURN_TO_NG` 但缺少 handoff evidence，预期后端 `422`，页面显示缺失证据。
  - 第二次补齐 NG 位置和物料扫码，预期 resolve 成功。
  - 查询 NG Return item，确认 `WAITING_REWORK`。
  - 若同时存在 safety hold，解决 runtime hold 后 WorkLine 仍保持隔离。

- [ ] 数据库 invariant SQL 验证。

```sql
select count(*) from wes_biz.workline_sessions
where reconciliation_state = 'PENDING';

select count(*) from wes_biz.runtime_holds
where hold_type = 'RUNTIME_RECONCILIATION'
  and status in ('OPEN', 'IN_PROGRESS', 'REOPENED')
  and blocking = true;
```

两个计数在 repair 后应一致；如不一致，运行 dry-run repair 输出差异。

- [ ] 最终提交。

```bash
cd backend
git status --short
cd ../frontend
git status --short
```

确认只有本计划相关改动后，按仓库分别提交剩余文件。

## 风险与防线

- 风险：`WorkLine.runtime_status` 被旧 service 直接写回 `READY`。
  - 防线：测试 `RuntimeHoldReleaseService is unique release path`；搜索 `runtime_status = WorkLineRuntimeStatus.READY`，逐个改为委托 release service。

- 风险：前端继续按 session reconciliation 推断可释放。
  - 防线：删除旧面板 release 表单；所有 CTA 跳到 Runtime Hold detail；表单只读 `release_eligibility`。

- 风险：material identity 被 display 字段替代。
  - 防线：resolver 缺失返回 `MISSING`；`RETURN_TO_NG` release 必须 `RESOLVED`；测试覆盖 `PkgID` 只是 display 的边界。

- 风险：NG reason taxonomy 被插件私有码冲散。
  - 防线：未映射 reason 拒绝提交；repair job 输出未映射报告；SMT 插件第一批 reason 明确映射。

- 风险：GET side effect 复发。
  - 防线：API 测试记录 GET 前后 Hold 数量不变。

- 风险：拓扑数字重新混入 blocked outbox 或 failed command。
  - 防线：后端投影测试 + 前端组件测试双层覆盖，字段命名使用 `open_command_count`、`blocked_outbox_count`、`open_issue_count`。

## 完成定义

- 后端存在 `RuntimeHold` 与 `NgReturnItem` 持久模型，异常发生事务内创建 Hold。
- 所有 WorkLine release 和 blocked outbox release 只能通过 `RuntimeHoldReleaseService`。
- Runtime Hold API 满足 SPEC 路径、权限、错误合同和 OpenAPI 生成要求。
- SMT 插件提供 material identity resolver 和 NG reason catalog 映射。
- repair job 支持 dry-run/apply，且幂等。
- 拓扑/Sandbox/顶部卡片只显示摘要和入口，不内联 release 表单。
- `/runtime/holds/:holdId` 页面支持桌面和 PDA 布局，能完成 `CONTINUE` 与 `RETURN_TO_NG`。
- `RETURN_TO_NG` 缺少物理交接证据时无法释放 WorkLine；补齐证据后创建 `NgReturnItem(status=WAITING_REWORK)`。
- 相关后端测试、前端 typecheck/lint/contract verify 和浏览器验收通过。
