# T8c Typed EXTERNAL_HTTP Transport Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**目标：** 用唯一冻结的 typed transport result 替换全部 EXTERNAL_HTTP bool sender，并把 delivery certainty、protocol result 与 retry safety 精确落入 SystemOutbox 和 WorklineDispatchAttempt。

**架构：** sender 只返回三态 delivery outcome（`NOT_SENT`、`ACCEPTED`、`AMBIGUOUS`），协议接受/显式拒绝与 delivery certainty 分离。两个现有 dispatcher 复用同一 result 类型和映射规则，但仍沿用各自现有调度入口；不新建 WMS dispatcher、ledger 或语义 reducer。

**技术栈：** Python 3.13、FastAPI/SQLModel、SQLAlchemy、httpx、PostgreSQL/Alembic、pytest。

## 全局约束

- 仅 `NOT_SENT` 且 `safe_to_retry=true` 可进入有界自动重试。
- HTTP 2xx 为 `ACCEPTED + protocol ACCEPTED`；3xx/4xx 为 `ACCEPTED + protocol REJECTED`，均进入 Outbox `SENT`。
- HTTP 5xx、timeout、连接 reset、发送/读取阶段的不明错误为 `AMBIGUOUS`，进入 `UNKNOWN` 且不再自动领取。
- 能证明请求未离开本地边界的 connect failure 为 retry-safe `NOT_SENT`；endpoint/canonical/URL 合同错误为不可重试 `NOT_SENT`。
- 不修改 RuntimeIntentLog，不实现 T8d callback/reconciliation reducer、T8e lease/fencing 或 T8f credentials。
- 所有命令使用 `uv run ...` 并通过 RTK 执行；所有生产符号修改前执行 GitNexus upstream impact。

---

### Task 1：冻结 typed transport result 与 HTTP 分类

**文件：**

- 新建：`src/app/sys/external_http_transport.py`
- 修改：`src/app/sys/services/outbox_delivery.py`
- 修改：`src/app/sys/services/outbox_engine.py`
- 测试：`tests/contracts/system_capabilities/test_external_http_transport_result.py`
- 测试：`tests/sys/test_outbox_delivery.py`

**接口：**

- `ExternalHttpTransportOutcome`：`NOT_SENT | ACCEPTED | AMBIGUOUS`
- `ExternalHttpTransportPhase`：`PREPARING | CONNECTING | SENDING | AWAITING_RESPONSE | RESPONSE_RECEIVED`
- `ExternalHttpProtocolResult`：`NOT_AVAILABLE | ACCEPTED | REJECTED | UNKNOWN`
- `ExternalHttpTransportResult`：冻结值对象，包含 outcome、phase、protocol result、safe retry、HTTP status、error code/message，并提供可持久化 evidence 投影。
- `ExternalHttpSender`：唯一签名 `ExternalHttpDispatchRequest -> Awaitable[ExternalHttpTransportResult]`。

- [ ] 先写值对象不变量、2xx/4xx/5xx、connect/timeout/reset 分类与 bool sender 拒绝测试。
- [ ] 运行定向测试，确认因类型/分类尚未实现而失败。
- [ ] 实现最小三态值对象、preflight fail-closed 和 httpx 分类。
- [ ] 重跑定向测试，确认 sender 不再返回 bool，T8b 原始 bytes 仍直接作为 body。

### Task 2：Outbox 精确终态与重试资格

**文件：**

- 修改：`src/app/sys/repositories/outbox_repository.py`
- 修改：`src/app/sys/services/outbox_engine.py`
- 测试：`tests/integration/test_system_outbox_repository.py`
- 测试：`tests/sys/test_system_outbox_engine.py`

**状态映射：**

| Typed result | SystemOutbox | 自动重试 |
| --- | --- | --- |
| `ACCEPTED`（含 protocol reject） | `SENT` | 否 |
| `NOT_SENT + safe` | `RETRY_WAIT`，预算耗尽后 `FAILED` | 有界 |
| `NOT_SENT + unsafe` | `FAILED` | 否 |
| `AMBIGUOUS` | `UNKNOWN` | 否 |

- [ ] 先写四条映射测试及 UNKNOWN 不再被 pending query 领取的测试。
- [ ] 运行测试，确认现有 bool 分支错误地把 ambiguity 当成 retry failure。
- [ ] 增加 Repository 的 UNKNOWN、terminal failure 原子转移，并让 generic engine 只按 typed result 分支。
- [ ] 重跑 sys 测试，确认 stats、fencing 与非 HTTP bool dispatcher 行为不变。

### Task 3：DispatchAttempt typed evidence

**文件：**

- 修改：`src/app/runtime/orchestration/models/dispatch_attempt.py`
- 修改：`src/app/runtime/orchestration/models/__init__.py`
- 修改：`src/app/runtime/orchestration/services/inbox/dispatch_attempt_service.py`
- 修改：`src/app/runtime/orchestration/services/inbox/outbox_dispatch_service.py`
- 修改：`src/app/sys/services/outbox_engine.py`
- 新建：由 Alembic generator 生成的 T8c revision
- 测试：`tests/workline_runtime/test_external_http_transport_attempt.py`
- 测试：`tests/integration/test_external_http_transport_attempt_postgresql.py`

**持久化字段：** `transport_outcome`、`transport_phase`、`protocol_result`、`safe_to_retry`、`http_status_code`；错误码、消息和 outbox finalization 继续进入现有 evidence/response 字段。

- [ ] 先写 Attempt finalized 状态与证据测试，并覆盖 generic/workline 两条 dispatcher。
- [ ] 运行定向测试，确认当前 success bool 无法表达 UNKNOWN 与 protocol reject。
- [ ] 实现 typed attempt finalize；只给 EXTERNAL_HTTP 写 typed 字段，device/internal attempt 保留既有合同。
- [ ] 用 Alembic generator 创建 nullable 新列 revision，不 backfill 旧数据。
- [ ] 在本机 Docker PostgreSQL 执行 upgrade 与 BYTEA/result evidence 往返测试。

### Task 4：全量收口与提交

**文件：**

- 新建：`.superpowers/sdd/task-T8c-report.md`
- 按失败结果更新相关旧测试 fixture，不修改用户维护的 `AGENTS.md`、`CLAUDE.md`。

- [ ] 搜索所有 EXTERNAL_HTTP sender/call site，确认 bool sender 合同归零。
- [ ] 运行定向域、`tests/sys`、runtime capability、测试拓扑与显式 collect-only。
- [ ] 运行 `./scripts/git-quality-gate.sh --profile quality`。
- [ ] 精确暂存 T8c 文件并运行 staged `gitnexus_detect_changes`，审阅 affected processes。
- [ ] 写中文实施报告并以 Conventional Commit 中文 subject 提交。

## 自审

- 覆盖三态 delivery、独立 protocol reject、retry safety、Outbox/Attempt 映射、PostgreSQL evidence 与 bool 合同归零。
- 类型名和状态映射在四个任务中一致。
- 无 T8d–T8f、WMS 特例、兼容 fallback、旧数据迁移或完整实现代码。
