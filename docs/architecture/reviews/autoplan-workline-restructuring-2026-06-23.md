# autoplan 评审全文 — WORKLINE + PLUGIN 体系全面重构

**日期**: 2026-06-23
**plan 文件**: `docs/architecture/workline-and-plugin-restructuring.md`（原 `docs/superpowers/specs/2026-06-23-wes-domain-boundary-and-dispatch-adapter-design.md`）
**autoplan restore point**: `~/.gstack/projects/kaizhoumasha-wes_backend/develop-autoplan-restore-20260623-171204.md`

本文件是 autoplan 评审产物的存档，**主 plan 文件不再包含评审章节**。

## CEO 评审

### 共识表

| Dimension | Claude | Codex | Consensus |
| --- | --- | --- | --- |
| 1. Premises valid? | MIXED | MIXED | DISAGREE |
| 2. Right problem to solve? | NO | NO | CONFIRMED |
| 3. Scope calibration correct? | NO | NO | CONFIRMED |
| 4. Alternatives sufficiently explored? | NO | NO | CONFIRMED |
| 5. Competitive/market risks covered? | N/A | N/A | N/A |
| 6. 6-month trajectory sound? | NO | NO | CONFIRMED |

### 关键发现（双 voice 独立命中）

- **F1（CRITICAL）**：relocation + 词汇重建，不是 boundary 问题（"22.6K" 数字错误，autoplan F0 修正）
- **F2（CRITICAL）**：B 方案被"目标态重写"假设跳过，无 4 方案决策
- **F3（CRITICAL）**：6 个月重写风险，v0.6–v0.8 公共契约被 B 方案作废
- **F4（HIGH）**：`WorklineQueueMembership` 命名过早画死
- **F5（HIGH）**：WES 不是库存系统边界会滑向影子库存
- **F6（HIGH）**：RuntimeIntent vs state 自相矛盾
- **F7（HIGH）**：WMS 权威假设过粗（设备到位归 PLC/RCS，不能都塞 WMS）

## Design 评审

7 维度评分：3.0/10 ~ 7.0/10（平均 5.3/10）。backend-defined read model（plane scene/snapshot）不需要 visual mockup。5 个机械修复（schema_version、scene/snapshot 独立、枚举冻结、label/code 分离、极态清单）。

## Eng 评审

### 共识表

| Dimension | Claude | Codex | Consensus |
| --- | --- | --- | --- |
| 1. Architecture sound? | MIXED | MIXED | DISAGREE |
| 2. Test coverage sufficient? | NO | NO | CONFIRMED |
| 3. Performance risks addressed? | NO | NO | CONFIRMED |
| 4. Security threats covered? | NO | NO | CONFIRMED |
| 5. Error paths handled? | NO | NO | CONFIRMED |
| 6. Deployment risk manageable? | NO | NO | CONFIRMED |

### 4 个 CRITICAL gap

- **F0**：`wms_integration` 实测 760 行（不是 22.6K）
- **F5**：`BinTransitQueue` 8 值 + RECONCILING 已生产存在，重命名破坏性
- **F8**：32,979 LOC 改造无测试基线
- **F10**：`GET /worklines/{id}/plane` 无 RBAC/审计/脱敏

### 事实核查

- CEO 决策 #1 引用 "wms_integration/typed_ports.py 22.6K" 数字错误，实测 609 行（整个 `wms_integration` = 2,649 行）
- TODOS P0 头部"test_start_admission_service 19 失败"陈旧，实测 22 passed in 4.13s（v0.8.0.0 → v0.8.1.0 已修复）

## DX 评审

跳过——无 developer-facing surface（此 plan 是内部架构设计）。

## 28 个 auto-decision（已反向写回 plan body）

详见 [`decision-audit-trail.md`](decision-audit-trail.md)。
