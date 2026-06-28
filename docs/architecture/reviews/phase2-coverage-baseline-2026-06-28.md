# Phase 2 启动前 P0-003 行为契约核心路径覆盖率基线

**生成日期**:2026-06-28
**生成命令**: `uv run pytest --cov=src/app/contracts --cov=src/app/runtime/orchestration --cov=src/app/wms_integration/provider_simulator_registry --cov=src/tests/support/workline_contracts --cov-report=term-missing tests/contracts/workline/`
**测试集合**: `tests/contracts/workline/` 8 个 contract 文件,31 passed + 2 xfailed
**§10.3.1 第一条硬约束**: 核心路径覆盖率 ≥ 70%

## 覆盖率总览

| 域 | Stmts | Miss | Cover |
|---|---:|---:|---:|
| `src/app/contracts/external_contract_profile.py` | 89 | 15 | **83%** |
| `src/app/runtime/orchestration/` 7 实体 (TOTAL) | 131 | 0 | **100%** |
| `src/app/runtime/orchestration/services/runtime_snapshot_assembler.py` | 30 | 1 | **97%** |
| `src/app/runtime/orchestration/repositories/idempotency_key_repository.py` | 23 | 12 | 48% |
| `src/app/runtime/orchestration/services/idempotency_guard.py` | 42 | 22 | 48% |
| **TOTAL** | **381** | **50** | **87%** |

**结论**: **核心路径覆盖率 87% ≥ 70%** — §10.3.1 第一条**未触发**。

## 域分布分析

- **contracts 域**: 83% — ExternalContractProfile 是 packet B 的 contract fixture 主入口,coverage 强
- **runtime/orchestration 7 实体**: 100% — Phase 1 Packet C CEO-007 落地骨架被 contract 测试完整覆盖
- **runtime_snapshot_assembler**: 97% — runtime 视图组装,contract snapshot 触发
- **idempotency_guard / repository**: 48% — contract 测试只触发 happy path 的 idempotency_record;lease expiry / replay / dead-letter 路径属于 Phase 3 ENG-009 + ENG-011 + ENG-020 范畴

## Phase 2 启动时需明确的覆盖范围

`tests/contracts/workline/` 不覆盖:
- `src/app/workline/services/` 33 个 service (21213 行) — 业务编排层,Phase 2 task 2 "WorkLine 执行逻辑清空"的目标
- `src/workline_runtime/` 50 个文件 (旧 plugin 框架) — Phase 2 task 3 "Legacy 执行入口删除"的目标

这两块的覆盖率属于 Phase 2 内业务逻辑迁移后,新 contract tests 应该触发的范围;Phase 2 内会有专项 behavior contract tests 跟随迁移完成度补齐。

## Phase 3 关联

`idempotency_guard.py` 48% 与 `idempotency_key_repository.py` 48% 的覆盖率缺口必须在 Phase 3 ENG-009(idempotency 跨域)+ ENG-011(inbox backpressure)+ ENG-020(replay runner)落地时补齐——Phase 3 §10.4 已列入。

## 与 §10.3.1 第一条的对照

> "Phase 0 P0-003 行为契约测试未覆盖关键业务语义,核心路径覆盖率低于 70%"

| 检查项 | 状态 |
|---|---|
| Phase 0 P0-003 行为契约测试覆盖关键业务语义 | ✅ PASS — 8 contract 文件覆盖 handoff / admission / runtime snapshot / full box exchange / resource projection / rough sorter inbound / external event / external contract profile fixtures |
| 核心路径覆盖率 ≥ 70% | ✅ PASS — TOTAL 87% |
| 暂停 B 方案 | ❌ 不触发 |

## 决策

§10.3.1 第一条**PASS**,B 方案**不暂停**。