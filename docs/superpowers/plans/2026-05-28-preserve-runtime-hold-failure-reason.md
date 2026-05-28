# Preserve Runtime Hold Failure Reason Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 runtime hold 人工解除为 `FAILED` 时清空原始 `failure_*` 诊断信息的回归。

**Architecture:** 保持 `WorklineSessionLifecycleService` 作为纯状态流转规则入口，但区分“运行时失败新产生故障”和“人工决议为失败”。`resolve(... FAILED ...)` 只更新终态、结束时间和等待字段，不覆盖已有 `failure_domain`、`failure_code`、`failure_message`。

**Tech Stack:** Python 3.13, SQLModel/SQLAlchemy AsyncSession, pytest, ruff, GitNexus。

---

### Task 1: 用失败测试锁定 review 回归

**Files:**
- Modify: `tests/workline_runtime/test_session_lifecycle_service.py`
- Optional Verify: `tests/workline_runtime/test_runtime_hold_release_service.py`

- [ ] **Step 1: 新增生命周期服务单元测试**
  - 在 `test_session_lifecycle_service.py` 中新增测试：给 session 预置 `failure_domain="BLOCK"`、`failure_code="SCAN_NG"`、`failure_message="原始阻塞原因"`。
  - 调用 `WorklineSessionLifecycleService.resolve(..., resolution=SessionStatus.FAILED, ...)`。
  - 断言状态变为 `FAILED`、`ended_at` 被更新、等待字段被清理，并且三个 `failure_*` 字段保持原值。

- [ ] **Step 2: 运行红灯测试**
  - Run: `uv run pytest tests/workline_runtime/test_session_lifecycle_service.py -q`
  - Expected: 新增测试失败，失败点应显示 `failure_domain/code/message` 被清空。

### Task 2: 修复 FAILED 决议语义

**Files:**
- Modify: `src/app/workline/domain/services/session_lifecycle_service.py`

- [ ] **Step 1: 调整 `resolve()` 的 FAILED 分支**
  - `COMPLETED` 和 `CANCELLED` 继续复用现有终态方法。
  - `FAILED` 分支不得调用会清空故障字段的 `fail()` 默认路径。
  - `FAILED` 分支应设置 `status=FAILED`、清理等待字段、写入 `ended_at`，并保留既有 `failure_*` 字段。

- [ ] **Step 2: 运行生命周期测试**
  - Run: `uv run pytest tests/workline_runtime/test_session_lifecycle_service.py -q`
  - Expected: 全部通过。

### Task 3: 回归 Runtime Hold 解除路径

**Files:**
- Modify: `tests/workline_runtime/test_runtime_hold_release_service.py`（仅当现有覆盖不足）

- [ ] **Step 1: 检查是否需要服务级测试**
  - 如果生命周期测试已覆盖字段保留，但 release service 没有覆盖人工 FAILED 决议，则新增一个服务级测试。
  - 测试应创建带原始 `failure_*` 的 `WorklineSession`，创建 runtime hold，使用 `resolution="FAILED"` 的解除请求调用 `resolve_hold()`，然后刷新 session 并断言 `failure_*` 保留。

- [ ] **Step 2: 运行 release service 测试**
  - Run: `uv run pytest tests/workline_runtime/test_runtime_hold_release_service.py -q`
  - Expected: 全部通过。

### Task 4: 质量门禁

**Files:**
- Touched files only.

- [ ] **Step 1: 运行目标测试**
  - Run: `uv run pytest tests/workline_runtime/test_session_lifecycle_service.py tests/workline_runtime/test_runtime_hold_release_service.py tests/api/test_runtime_hold_api.py -q`
  - Expected: 全部通过。

- [ ] **Step 2: 运行格式和 lint**
  - Run: `uv run ruff format src/app/workline/domain/services/session_lifecycle_service.py tests/workline_runtime/test_session_lifecycle_service.py tests/workline_runtime/test_runtime_hold_release_service.py`
  - Run: `uv run ruff check src/app/workline/domain/services/session_lifecycle_service.py tests/workline_runtime/test_session_lifecycle_service.py tests/workline_runtime/test_runtime_hold_release_service.py`
  - Expected: `All checks passed!`

- [ ] **Step 3: GitNexus 变更检测**
  - Run: `npx gitnexus detect-changes --repo wes_backend --scope all`
  - Expected: 变更范围应集中在生命周期服务及对应测试；若仍显示 broad/critical，报告原因通常是当前 Workline 写链既有未提交改动仍在 diff 中。

## Assumptions

- Review comment 成立：人工解除为 `FAILED` 是决议，不应丢弃此前记录的故障归因。
- 本修复不改变 `fail()` 的语义；运行时新失败仍可显式写入或覆盖 `failure_*`。
- 本修复不改变 API schema、权限码、数据库结构和事务边界。
