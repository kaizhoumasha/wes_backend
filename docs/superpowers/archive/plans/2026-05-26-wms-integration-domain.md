# WMS 对接辅助域 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立独立的 `wms_integration` 辅助域，统一 WMS 同步调用、WMS/RCS 异步派发合同、回调标准化、短时查询缓存、DB-backed 熔断、脱敏证据留痕和接入方错误合同。

**Architecture:** `wms_integration` 是 WMS Anti-Corruption Layer，不是 WES 主数据域。业务域只能调用内部 typed ports；WES 只保存调用证据、执行快照、资源投影和对账证据。WMS 仍是库存、预留、账务和空箱授权唯一真实源。查询/预留/确认走同步 client；搬运/交换继续走现有 `SystemOutbox(EXTERNAL_HTTP)`，但 endpoint code、payload 合同和 callback normalizer 收口到 `wms_integration`。

**Tech Stack:** Python 3.13, FastAPI service layer, SQLModel, Alembic, httpx, RedisCache, SystemOutbox, pytest, ruff, GitNexus.

---

## Summary

已确认决策：

- 域名：`src/app/wms_integration/`
- 对外 API：首版不新增 `/api/v1/wms/...` 业务代理接口，只提供内部 service/port。
- 同步端口：明确 5 个 typed ports：`query_inventory`、`reserve_inventory`、`release_reservation`、`confirm_inbound`、`confirm_outbound`。
- 证据：新增独立 WMS evidence 表；同步调用保存脱敏 request/response snapshot + canonical hash；异步 outbox/callback 只保存关联 ID、dispatch_key/request_id/trace_id 和脱敏摘要，不复制完整事实源。
- 熔断：使用 DB 持久化 breaker state，按 `target_code + operation_name` 共享 `CLOSED/OPEN/HALF_OPEN` 状态。
- 缓存：仅 read-only 查询允许 Redis 短缓存，TTL 硬上限 30 秒；Redis miss/bad/unavailable 必须回源 WMS；写操作永不缓存。
- 错误合同：WMS client 暴露 typed exception hierarchy；调用方通过 `WmsUnavailableError` 系列识别 timeout/5xx/circuit-open，并按 caller checklist 创建 RuntimeHold 或诊断。
- 回调 normalizer：只做 WMS/RCS 最小包络校验和字段标准化，不做 rack/handling/workline 业务路由。
- 迁移：rack/handling/callback 保留原入口，内部逐步迁入 `WmsTransportContractService` 和 `WmsExecutionCallbackNormalizer`。

## Data Flow

同步 WMS 调用：

```text
Business Service
  -> WmsInventoryService typed port
      -> WmsEndpointConfig(resolve operation URL/timeout)
      -> WmsCircuitBreakerService(check DB state)
      -> WmsHttpClient(httpx)
      -> WmsCallEvidenceService(redact + hash + persist)
      -> WmsCircuitBreakerService(record success/failure)
  -> typed response OR typed WMS exception
  -> caller handles WmsUnavailableError with RuntimeHold/diagnostic policy
```

异步搬运/交换：

```text
rack/handling service
  -> WmsTransportContractService(build endpoint code + payload)
  -> SystemOutbox(EXTERNAL_HTTP)
  -> sys EndpointRegistry(resolve target_code)
  -> WMS/RCS
  -> /api/v1/callback/external
  -> WmsExecutionCallbackNormalizer(validate + normalize only)
  -> CallbackOrchestrationService(existing rack/handling/workline routing)
  -> WorklineInbox / lifecycle services / CallbackLog
```

DB-backed WMS 熔断：

```text
CLOSED --timeout/5xx threshold--> OPEN
OPEN --retry_after elapsed--> HALF_OPEN
HALF_OPEN --success threshold--> CLOSED
HALF_OPEN --failure--> OPEN
```

## Key Changes

- 新增 `wms_integration` 域，最小结构：
  - `models/`: `WmsCallEvidence`, `WmsCircuitBreakerState`, typed request/response models。
  - `repositories/`: evidence repository、breaker state repository。
  - `services/`: HTTP client、endpoint config、inventory/write typed ports、transport contract service、callback normalizer、redaction utility、breaker service。
  - 每个 public service 必须在对应 `__init__.py` 导出，业务域不得直接 import HTTP client 或私有 helper。
- 新增 `WmsCallEvidence`：
  - `evidence_key` unique。
  - 索引：`trace_id/request_id/dispatch_key`、`operation_name + started_at`、`status + started_at`。
  - 保存 redacted snapshot 和 canonical sha256 hash；敏感字段包括 token/secret/signature/authorization/cookie/api_key/password 等嵌套字段。
  - retention 策略在本 PR 定义，清理/归档 job 后续排期。
- 新增 `WmsCircuitBreakerState`：
  - 按 `target_code + operation_name` 维护状态、失败计数、last_failure_at、opened_until、half_open 计数。
  - 状态更新必须和 evidence 写入保持可追踪；并发更新需测试。
- 同步 client：
  - `wms_integration` 自有 endpoint config 管同步 WMS base URL、operation path、timeout 和 operation name。
  - `sys.EndpointRegistry` 继续只负责 SystemOutbox EXTERNAL_HTTP target_code。
  - httpx timeout 必须显式配置 connect/read/write/pool 或等价总超时；禁止裸 URL 从业务域传入。
- 错误层：
  - 定义 `WmsIntegrationError`、`WmsBusinessRejectedError`、`WmsUnavailableError`、`WmsCircuitOpenError`、`WmsTimeoutError`。
  - 异常携带 `operation_name`、`evidence_key`、`http_status`、`reason_code`、`retryable`。
  - WMS 4xx 业务拒绝不默认创建 RuntimeHold；timeout/5xx/circuit-open 由调用方按 checklist 暂停业务。
- 迁移现有合同：
  - `rack`、`handling` gateway 保留原入口，内部改用 `WmsTransportContractService` 生成 target_code/payload。
  - `callback_ingress_service` 将 WMS/RCS 最小包络校验委托给 `WmsExecutionCallbackNormalizer`，但业务分发仍留在 callback orchestration。

## Test Plan

- 新增 `tests/wms_integration/`：
  - typed ports：5 个同步端口 happy path、WMS 4xx、timeout、5xx、circuit-open、evidence_key 传播。
  - HTTP client：httpx connect/read timeout、network error、HTTPStatusError、response parse error。
  - evidence/redaction：敏感字段脱敏、canonical hash 稳定、异步 path 不复制完整 payload。
  - DB breaker：CLOSED/OPEN/HALF_OPEN 状态转换、跨 service 实例共享状态、half-open 成功/失败。
  - cache：read-only query hit/miss/bad cache/Redis unavailable；TTL 不超过 30 秒；写端口不触发缓存。
  - callback normalizer：既有 WMS rack/full-box payload 继续通过，缺少 source envelope/status/dispatch_key 时拒绝。
  - transport contract：rack/handling 迁移后 target_code、dispatch_key、callback_type、payload shape 与迁移前一致。
- 回归测试：
  - `tests/api/test_callback_api.py`: WMS/RCS payload 接受/拒绝和 duplicate idempotency 行为不变。
  - `tests/rack/test_rack_operation_service.py`: rack envelope/callback_type 不变。
  - `tests/handling/`: handling move/full-box exchange envelope 不变。
  - `tests/sys/test_system_outbox_engine.py`: EXTERNAL_HTTP 仍由 sys EndpointRegistry 派发，禁止 raw URL。
- 架构测试：
  - 不新增公开 `/api/v1/wms/...` 路由。
  - API 层不得直接 DB 查询；业务域不得直接 import WMS HTTP client。
  - `wms_integration` 不提供本地物料、GRN、库存或货架主账 CRUD。
- 调用方合同测试：
  - 使用 fake caller 验证 `WmsUnavailableError` 系列必须转成 RuntimeHold/诊断暂停协议。
  - 真实业务 caller 接入作为后续 TODO，不在本 PR 强行选择。

## Failure Modes

| Codepath | Production failure | Test | Error handling | User/Ops outcome |
|---|---|---|---|---|
| WMS typed query | timeout/read timeout | yes | `WmsTimeoutError` + evidence + breaker failure | caller 暂停或提示 WMS 不可用 |
| WMS typed write | 4xx business reject | yes | `WmsBusinessRejectedError` | 用户看到明确 WMS 拒绝原因，不建系统 hold |
| WMS typed write | 5xx | yes | `WmsUnavailableError` + breaker failure | caller 按 checklist 暂停业务 |
| Circuit breaker | OPEN | yes | `WmsCircuitOpenError` fast-fail | 不继续等待 WMS，现场看到依赖不可用 |
| Redis cache | unavailable/bad value | yes | 回源 WMS，不抛缓存错误 | 用户只承受 WMS 实时查询延迟 |
| Evidence write | unique conflict | yes | 返回/复用 existing evidence | 幂等重试不制造重复证据 |
| Transport contract migration | payload drift | regression | envelope contract test fails | 防止 WMS/RCS 接口静默破坏 |
| Callback normalizer migration | missing required envelope | yes | 400 validation failure + callback log | 对方收到明确拒绝，不入业务流程 |

## What Already Exists

- `SystemOutbox` 和 `SystemOutboxEngine` 已提供 EXTERNAL_HTTP at-least-once 派发、dispatch_key 幂等、endpoint code 解析和重试状态。
- `CallbackLog`、`WorklineInbox`、`CallbackOrchestrationService` 已提供 callback 入口日志、幂等和业务分发。
- `rack` / `handling` gateway 已生成 WMS/RCS endpoint code、dispatch_key、callback_type 和 payload。
- `RedisCache` / `cache_helpers` 已有缓存降级、坏缓存清理和基础熔断思路，但 WMS breaker 需要 DB 共享状态，不能直接使用进程内 Redis breaker。
- `RuntimeHoldCreationService` 已拥有 RuntimeHold 创建模式；`wms_integration` 不直接创建 RuntimeHold，只提供 typed exception 和 evidence_key 给调用方。

## NOT in Scope

- 不新增公开 `/api/v1/wms/...` 代理接口。
- 不在 WES 本地维护物料、GRN、库存、预留、账务或货架主账 CRUD。
- 不把 SystemOutbox 或 CallbackLog 的完整 payload 复制进 WMS evidence；只关联和脱敏摘要。
- 不在本 PR 实现 evidence retention cleanup job；只定义 retention 策略和索引。
- 不在本 PR 强行接入第一个真实业务 caller；只提供 caller contract test 和接入 checklist。
- 不新增 WMS 监控看板/告警；后续基于 evidence/breaker 数据排期。

## Worktree Parallelization

| Step | Modules touched | Depends on |
|---|---|---|
| Core WMS domain | `src/app/wms_integration/`, `migrations/` | - |
| Callback migration | `src/app/callback/`, `tests/api/` | Core WMS domain normalizer |
| Rack/handling migration | `src/app/rack/`, `src/app/handling/`, tests | Core WMS domain contract service |
| Architecture/test guardrails | `tests/wms_integration/`, architecture tests | Core WMS domain |

Parallel lanes:

- Lane A: Core WMS domain -> architecture/test guardrails (sequential, shared new domain).
- Lane B: Callback migration (waits for normalizer).
- Lane C: Rack/handling migration (waits for contract service).

Execution order: build Lane A first. After public normalizer/contract service APIs stabilize, run Lane B and Lane C in parallel worktrees. Merge B/C after contract tests pass.

Conflict flags: Lane B and Lane C should not edit the same module directories, but both depend on `src/app/wms_integration/services/__init__.py`; coordinate exports before parallel work starts.

## Implementation Tasks

Synthesized from /plan-eng-review findings. Each task derives from a specific finding above.

- [x] **T1 (P1, human: ~0.5 day / CC: ~45 min)** — Evidence — Define WMS evidence table, redaction, hash, indexes, and retention policy.
  - Surfaced by: Architecture D3, Code Quality D10, Performance D17.
  - Files: `src/app/wms_integration/models/`, `repositories/`, `services/`, `migrations/`.
  - Verify: `uv run pytest tests/wms_integration/ -k "evidence or redaction"`.
- [x] **T2 (P1, human: ~0.5 day / CC: ~45 min)** — Breaker — Implement DB-backed WMS circuit breaker state and transitions.
  - Surfaced by: Architecture D4, Performance D17.
  - Files: `src/app/wms_integration/models/`, `repositories/`, `services/`.
  - Verify: `uv run pytest tests/wms_integration/ -k breaker`.
- [x] **T3 (P1, human: ~0.5 day / CC: ~45 min)** — Typed Ports — Implement 5 WMS synchronous ports with endpoint config and typed errors.
  - Surfaced by: Code Quality D8, D11, Architecture D5.
  - Files: `src/app/wms_integration/services/`, `models/`.
  - Verify: `uv run pytest tests/wms_integration/ -k "client or port or error"`.
- [x] **T4 (P1, human: ~0.5 day / CC: ~40 min)** — Cache — Restrict Redis caching to read-only queries with canonical keys and TTL <= 30s.
  - Surfaced by: Performance D18.
  - Files: `src/app/wms_integration/services/`.
  - Verify: `uv run pytest tests/wms_integration/ -k cache`.
- [x] **T5 (P1, human: ~0.5 day / CC: ~45 min)** — Contract Migration — Move rack/handling payload construction and callback normalization into WMS integration without behavior drift.
  - Surfaced by: Architecture D6, Test Review regression rule.
  - Files: `src/app/rack/`, `src/app/handling/`, `src/app/callback/`, `src/app/wms_integration/`.
  - Verify: `uv run pytest tests/rack/ tests/handling/ tests/api/test_callback_api.py tests/wms_integration/`.
- [x] **T6 (P2, human: ~2h / CC: ~25 min)** — Caller Contract — Add caller checklist and fake caller contract test for WMS unavailable handling.
  - Surfaced by: Test Review D15.
  - Files: `docs/`, `tests/wms_integration/`.
  - Verify: `uv run pytest tests/wms_integration/ -k caller`.

## Deferred / TODO Decisions

- Accepted TODO: WMS evidence retention cleanup/archive job after production retention requirements are known.
- Accepted TODO: First real WMS synchronous caller integration and end-to-end RuntimeHold/diagnostic validation.
- Accepted TODO: WMS breaker/evidence observability and alerting.
- These TODOs were accepted during plan review but are not written to `TODOS.md` in Plan Mode; append them when leaving Plan Mode or during implementation setup.

## Assumptions

- Alembic revision IDs must be generated by Alembic, then edited; do not hand-write template IDs.
- `wms_integration` may depend on `sys` models for outbox correlation IDs, but must not take over `SystemOutboxEngine`.
- `callback` remains the owner of HTTP callback endpoint behavior and business orchestration.
- `resource` remains runtime resource projection; it does not become WMS inventory availability authority.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | - | - |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | - | - |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR | 30 issues resolved into plan, 0 critical gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | - | - |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | - | - |

- **UNRESOLVED:** 0
- **VERDICT:** ENG CLEARED — ready to implement.
