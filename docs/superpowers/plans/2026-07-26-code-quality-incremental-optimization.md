# 代码质量渐进优化实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 渐进降低复杂度与类型债务，消除本次变更范围内的重复实现和 basedpyright 告警，并收紧已确认的运行时边界。

**Architecture:** 保留 API → Service → Repository → Database 分层，通过小型纯函数和既有 typed contract
收窄职责；持久化恢复、Provider binding 与 QUERY transport 分别拥有单一校验入口。

**Tech Stack:** Python 3.13、Ruff 0.15、basedpyright、Pytest 9、Bandit、GitNexus。

## Global Constraints

- 使用中文沟通、文档和提交说明。
- 保持 API → Service → Repository → Database 分层。
- 修改符号前执行 GitNexus upstream impact analysis。
- 使用 `uv run ...` 执行项目工具。
- 保留并同步有价值注释。

---

### Task 1: 降低资源位置一致性诊断复杂度

**Files:**

- Modify: `src/app/resource/services/material_location_consistency_service.py`
- Test: `tests/resource/test_resource_c0_projection_contract.py`

**Interfaces:**

- Consumes: material units、active mounts、active occupancies 三组可迭代输入。
- Produces: `MaterialLocationConsistencyIssue` 有序列表；公开签名与原因码不变。

- [x] 运行 GitNexus 影响分析，确认 `diagnose` 和所属 Service 的上游影响。
- [x] 以 Ruff `C901` 检查作为 RED 基线，确认目标方法当前失败。
- [x] 将索引构建、挂载基数判断、单挂载投影判断拆为私有职责。
- [x] 运行目标文件 Ruff 复杂度检查，确认不再产生 `C901`。
- [x] 运行资源域契约测试，确认原因码、字段和修复路径保持不变。

### Task 2: 清理重复质量配置并执行完整验证

**Files:**

- Modify: `pyproject.toml`

**Interfaces:**

- Consumes: 现有 uv、Ruff、Pytest 和 Bandit 配置。
- Produces: 无重复开发依赖和重复 Ruff ignore 项的等价配置。

- [x] 删除重复的 Locust 开发依赖声明和重复 `ISC001` ignore 项。
- [x] 运行 Ruff format、Ruff lint 和 Bandit。
- [x] 运行仓库 `quality` profile。
- [x] 运行 GitNexus detect changes，核对受影响符号与流程。
- [x] 检查最终 diff，确认没有无关改动。

### Task 3: 收窄动态边界类型

**Scope:**

- Runtime Inbox / effect reducer / WMS status flow
- SystemOutbox canonical payload 与 frozen binding
- WMS Provider、QUERY transport 与 migration inventory

- [x] 用 basedpyright JSON 输出建立可复现基线。
- [x] 在 ORM、Pydantic 和持久化恢复边界显式收窄动态值。
- [x] 保留 fail-closed 校验，并把有意忽略的调用结果显式赋给 `_`。
- [x] 全仓 basedpyright 保持 0 error，本次触达文件达到 0 warning。

### Task 4: 消除 canonical payload 重复校验

- [x] 用失败测试证明单次投递执行了两次 SHA-256 完整性计算。
- [x] 让 `ExternalHttpDispatchRequest` 工厂成为唯一恢复与校验入口。
- [x] 合并两个完全重复的持久化字符串类型收窄函数。
- [x] 运行 canonical dispatch、Outbox delivery 与 frozen binding 契约测试。

### Task 5: 收紧 WMS Provider 与 QUERY transport

- [x] 用领域合同替换 Provider composition helper 中的 `Any`。
- [x] 用失败测试证明 QUERY transport 接受了未被当前合同支持的 POST。
- [x] 将 QUERY transport 收敛为当前唯一 GET 合同，并删除推测性 POST 分支。
- [x] 修复缺失 plugin logical route 未被空字符串校验拦截的问题。

### Task 6: 最终质量门禁

- [x] Ruff format check、Ruff lint 与 `git diff --check`。
- [x] C901 总量从 87 降至 86，未引入新的复杂度超限。
- [x] 运行完整 pytest。
- [x] 运行仓库 `quality` profile 与 Bandit。
- [x] 运行 GitNexus detect changes，核对最终影响范围。
