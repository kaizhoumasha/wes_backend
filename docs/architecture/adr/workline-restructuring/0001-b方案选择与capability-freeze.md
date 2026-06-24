# ADR 0001: B 方案选择与 Capability Freeze

**状态**: Accepted
**日期**: 2026-06-23
**适用范围**: WORKLINE + PLUGIN 体系全面重构

## 背景

WORKLINE + PLUGIN 体系（workline 32,979 LOC + workline_plugins 3,085 LOC + workline_runtime 10,241 LOC）当前混合"配置 + 执行 + 插件"职责，难以演进。autoplan CEO/Eng 双 voice 评审识别出 4 个 CRITICAL 风险：F0（22.6K 数字错，实测 760 行）、F5（RECONCILING 黑洞）、F8（无测试基线）、F10（plane 接口无 RBAC）。

## 决策

1. **B 方案（目标态领域边界 + 增量 ACL）作为重构路径**，但**条件性**进入：完成 C 方案（增量 ACL 包装 wms_integration）+ capability freeze + 测试基线 + 4 个 critical path 任务后启动。
2. **Capability Freeze**：v0.6–v0.8 公共契约（start admission / runtime monitor / SMT inbound handoff / C0 resource projection / YAML manifest / WMS typed port / BinTransitMembership 8 队列 + RECONCILING / WMS callback normalize + circuit breaker）**不可撤销**；B 方案修改必须走 breaking change 流程。
3. **现有 typed port 包装不重建**：`wms_integration` 复用现有 `wms_integration`（实测 2,649 LOC，其中 typed_ports.py 609 行 + models/ports.py 151 行 = 760 行）；补全 5 套缺失 port（`WmsMasterDataPort` / `WmsDocumentPort` / `WmsFulfillmentPort` / `WmsEventPort` / `WmsReconciliationQueryPort`）。
4. **物理表名冻结**：`BinTransitMembership`（8 队列 + RECONCILING + 部分唯一索引）保持不变；新增 `ConveyorQueueProjectionPort` 接口层代替直接 import。
5. **per-PR 兼容声明**：每个破坏性 PR 必须附带 `docs/architecture/capability-freeze.md` 条目（受影响 capability + breaking change + 迁移路径 + 失效窗口 + feature flag 回滚方案）。

## 后果

- B 方案不是默认结论；走 C 方案 + capability freeze + 测试基线作为前置。
- v0.6–v0.8 公共契约保护；任何修改必须显式标注 breaking change。
- 已有 760 行 ACL 复用为 `wms_integration` 的 `WmsInventoryPort`；5 套新 port 在 B 方案中落地。
- 重构周期：human-team ~38 周，CC + gstack 压缩后 ~4-6 周。

## 验收

- `docs/architecture/workline-and-plugin-restructuring.md` 顶层设计发布。
- `docs/architecture/capability-freeze.md` 发布（CEO-003）。
- 测试基线建立：`uv run pytest --cov=...` 快照保存（ENG-011）。
- Critical path 4 项（ENG-011 / CEO-003 / ENG-007 / ENG-001）落地后，B 方案才能进入。

## 引用

- 顶层设计：[`../workline-and-plugin-restructuring.md`](../../workline-and-plugin-restructuring.md)
- 现有 ADR：[`../2026-05-13-wes-wms-rcs-resource-boundary.md`](../2026-05-13-wes-wms-rcs-resource-boundary.md), [`../2026-05-26-wms-integration-domain.md`](../2026-05-26-wms-integration-domain.md)
