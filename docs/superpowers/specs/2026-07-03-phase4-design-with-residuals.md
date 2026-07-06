# Phase 4 设计推进 SPEC（含 Phase 1/2/3 遗留门禁）

> 状态：设计 SPEC 已写，未进入实现
> 父计划：`docs/architecture/workline-and-plugin-restructuring.md` §10.5
> 关联 SPEC：`cell-reservation-spec.md`、`material-location-query-spec.md`、`workline-active-objects-spec.md`、`sorter-inbound-capability-spec.md`、`smt-ng-wms-reconciliation-spec.md`

---

## 1. 目标与边界

本 SPEC 只推进 Phase 4 设计，不实现业务能力、不删除 legacy、不改变运行时热路径。Phase 4 设计可以先行，但生产热路径、上线和 Phase 5 删除必须受 Phase 1/2/3 residual gates 约束。

2026-07-04 范围调整：本项目未发布，当前开发/测试默认使用 MOCK closure。Wave2/Wave3 降级为本机开发环境 MOCK 验收，不做生产接入。`tests/mock/phase4` 只证明 sorter inbound 与 SMT/NG/WMS 对账合同可由本机 WMS/ECS mock 表达；真实 artifact 不再作为当前开发/测试推进阻塞项。`scripts/check_phase3_closure_gate.py` 无 artifact 时自动选择 mock profile；`--closure-profile production` 保留生产发布前的严格 artifact 校验。生产热路径仍记录在 Phase 1/2/3 residual gates 中，但当前实际阻塞项为 Phase 2 runtime_status 兼容投影收尾，Phase 1 callback admission 已关闭。

2026-07-06 遗留项收口：Phase4 开发/测试 mock readiness 已闭合；`scripts/check_phase4_runtime_readiness_gate.py --readiness-profile production` 已要求 Phase3 production closure artifact 与 Phase4 production evidence profile 同时成立。Phase4 production evidence profile 现在必须提供 provider contract、effect dispatch trace、RuntimeInbox worker trace、RuntimeHold/Reconciliation trace 和 benchmark 六类真实 evidence 文件，并校验每个引用文件的 SHA-256。业务承载 legacy 删除仍不得提前执行；只有真实 Phase3 production closure 与 Phase4 production evidence profile 都通过后，业务 lane 才能进入 Phase5 删除评估。

Phase 4 目标是补全 WES 作业期完整业务语义：

- MaterialLocationQuery：统一查询作业对象当前位置、来源和 evidence。
- WorklineActiveObjects：统一展示 WorkLine 当前 active 对象和冲突状态。
- 入库能力目标态重建：粗分机、满箱交换、分拣机入库按 capability / port 重建。
- SMT/NG/WMS 对账闭环：只保留 evidence、ExternalReference 和 RuntimeHold 解除条件，不复制外部主数据。

非目标：

- 不实现 RCS/AGV/CTU direct provider adapter。
- 不复用旧 plugin 入口作为目标态接口。
- 不把 `WorkLine.runtime_status` 作为 Phase 4 新业务状态源。
- 不提前进入 Phase 5 legacy 删除。

## 2. Residual Readiness Register（共享门禁，各 SPEC 引用本表）

> 本 Register 是 Phase 4 所有 SPEC 的共享前置门禁声明。各 SPEC 的 `Residual Readiness` 节只声明自己特有的门禁条件，通用门禁引用本表。

| Phase | 遗留门禁 | Phase 4 设计默认处理 | 实现前必须满足 |
| --- | --- | --- | --- |
| Phase 1 | callback API 热路径接入 provider profile admission，拒绝未声明 callback/event/result normalizer | 已在 callback API result/event/external 热路径调用 `ExternalContractProfile.ensure_inbound_normalizer_declared()`，未声明 normalizer 拒绝进入 inbox | callback API 对未声明 normalizer 的拒绝路径有合同测试和热路径测试 |
| Phase 2 | `WorkLine.runtime_status` 迁出或正式改名为兼容投影 | Phase 4 不新增对 `WorkLine.runtime_status` 的业务依赖；需要运行态时读取 runtime/orchestration 或 active projection | 兼容投影命名/迁出决策完成，文档和查询接口不再把它描述为状态 owner |
| Phase 3 | external callback 热路径切到 `RuntimeInbox` 状态机与 worker | Phase 4 只设计 RuntimeInbox 目标路径，不把旧 `WorklineInboxService` 当成可扩展依赖 | external callback 生产热路径完成 RuntimeInbox cutover |
| Phase 3 | P0 E2E closure profile | 当前开发/测试默认使用 MOCK closure；Phase 4 设计、P0/Wave1 与 Wave2/Wave3 本机 MOCK 验收可以继续 | 生产发布前显式运行 `scripts/check_phase3_closure_gate.py --closure-profile production ...`，并提供真实 P0 E2E artifact |
| Phase 3 | benchmark / queue writer PostgreSQL 证据 profile | 真实 artifact 不再作为当前开发/测试推进阻塞项；性能只声明预算和合同，不声明生产能力已满足 | 生产发布 profile 必须提供 production-scale benchmark，且 benchmark 不是 lightweight / sandbox |
| Phase 4 | production evidence profile | 开发/测试 mock readiness 已闭合；site/production 只收敛 evidence profile gate，不改变 runtime service 行为 | 生产发布 profile 必须提供六类真实 evidence 文件及 SHA-256：provider contracts、effect dispatch、RuntimeInbox worker、RuntimeHold/Reconciliation、benchmark |

### 2.1 per-SPEC 就绪条件

| SPEC | 可立即启动 | 等待条件 | 备注 |
| --- | --- | --- | --- |
| `material-location-query-spec.md` | ✅ 是（只读查询，不依赖 Phase 3 closure） | — | 需要 `ExternalReferenceCatalog` 稳定（Phase 3 已落地） |
| `workline-active-objects-spec.md` | ✅ 是（只读聚合视图，不依赖 Phase 3 closure） | — | 依赖 `ActiveObjectRegistry`（Phase 3 已落地）和 `MaterialLocationQuery`（本 Phase 先实施） |
| `sorter-inbound-capability-spec.md` | ⚠️ 部分（设计 + 本机 MOCK 可完成，生产热路径需等待） | Phase 2 runtime_status 兼容投影收尾 + RuntimeInbox cutover；生产发布前再切 `--closure-profile production` | 查询/只读部分和本机 mock 可先行；DeviceCommand 下发、WMS fulfillment 生产热路径不得接入 |
| `smt-ng-wms-reconciliation-spec.md` | ⚠️ 部分（设计 + 本机 MOCK 可完成，生产闭环需等待） | Phase 2 runtime_status 兼容投影收尾 + RuntimeInbox cutover + 入库能力 SPEC 稳定；生产发布前再切 `--closure-profile production` | evidence/ExternalReference 和本机 mock 可先行；对账闭环生产热路径不得接入 |
| `docs/architecture/cell-reservation-spec.md` | ✅ 是（独立状态机设计） | — | Phase 4 P0 前置项：3 个 SPEC 依赖 CellReservation，必须先锁定其生命周期 |

## 3. Phase 4 设计包

| SPEC | 设计职责 | 不做 |
| --- | --- | --- |
| `docs/architecture/cell-reservation-spec.md` | 复用/演进现有 `WorklineBinCellReservation`，定义目标语义与现有状态映射、唯一约束、TTL、RECONCILING 持久化门禁 | 不新建第二套 reservation model，不把 WMS 库存确认写成 WES 主数据 |
| `docs/architecture/material-location-query-spec.md` | 6 个查询入口、5 类来源优先级、ExternalReference/evidence 口径 | 不直接拼旧 plugin context，不把 WMS 主数据复制到 WES |
| `docs/architecture/workline-active-objects-spec.md` | `ActiveObjectRegistry` 协同、active/current view 归一化、冲突展示与 RECONCILING | 不新建跨域 FK，不绕过 owner-scoped resolution |
| `docs/architecture/sorter-inbound-capability-spec.md` | 粗分机、满箱交换、分拣机入库目标态流程和行为契约映射 | 不保留旧 plugin 兼容入口，不提前实现 direct provider |
| `docs/architecture/smt-ng-wms-reconciliation-spec.md` | NG evidence、WMS 确认/拒绝、目标箱回写失败、版本冲突恢复 | 不复制 WMS/NG/PDA 主档，不做离线 PDA 原生流程 |

## 4. 推进顺序与依赖图

### 4.1 跨 SPEC 依赖图

```text
                    ┌──────────────────────────────┐
                    │  cell-reservation-spec.md    │  ← Phase 4 P0 前置
                    │  (CellReservation 生命周期)   │
                    └──────────────┬───────────────┘
                                   │ 被 3 个 SPEC 依赖
              ┌────────────────────┼────────────────────┐
              ▼                    ▼                    ▼
┌─────────────────────────┐ ┌─────────────────────────┐ ┌─────────────────────────┐
│ material-location-      │ │ sorter-inbound-         │ │ smt-ng-wms-             │
│ query-spec.md           │ │ capability-spec.md      │ │ reconciliation-spec.md  │
│                         │ │                         │ │                         │
│ 6 入口 + 5 来源优先级    │ │ 粗分机/满箱交换/分拣机   │ │ NG/WMS 对账闭环         │
│ 依赖: CellReservation,  │ │ 依赖: CellReservation,  │ │ 依赖: CellReservation,  │
│       ExternalReference │ │       DeviceCommand,    │ │       Reconciliation   │
│       Catalog           │ │       WMS Fulfillment   │ │       Manager           │
└───────────┬─────────────┘ └───────────┬─────────────┘ └───────────┬─────────────┘
            │                           │                           │
            └───────────────────────────┼───────────────────────────┘
                                        │ 被消费
                                        ▼
                          ┌─────────────────────────┐
                          │ workline-active-        │
                          │ objects-spec.md         │
                          │                         │
                          │ 跨投影 active 对象聚合   │
                          │ 依赖: MaterialLocation  │
                          │       Query, Active     │
                          │       ObjectRegistry    │
                          └─────────────────────────┘
```

**关键依赖链**:
- `CellReservation` → `MaterialLocationQuery`（位置来源优先级 #3）
- `CellReservation` → `SorterInbound`（格位分配/预约/投放转占用）
- `CellReservation` → `SMT/NG/WMS Reconciliation`（预约冲突 → RECONCILING）
- `MaterialLocationQuery` → `WorklineActiveObjects`（归一化输入 #5）
- `SorterInbound` → `SMT/NG/WMS Reconciliation`（WMS 同步失败 → 对账）
- `ReconciliationManager` → `WorklineActiveObjects`（归一化输入 #4）

### 4.2 推荐实现顺序

1. **P0 前置**: `cell-reservation-spec.md` — 定义 CellReservation 完整生命周期（创建/确认占用/释放/TTL/WMS reject/source_version drift），作为 3 个 SPEC 的共同依赖
2. **Wave 1（查询读模型）**: `MaterialLocationQuery` + `WorklineActiveObjects` — 只读，不依赖 production closure，可立即实施
3. **Wave 2（入库能力）**: `SorterInbound` — 设计与本机 MOCK 验收可完成，生产热路径等待 Phase 2 收尾与 production closure profile
4. **Wave 3（对账闭环）**: `SMT/NG/WMS Reconciliation` — 本机 MOCK 验收可完成；生产闭环等待入库能力稳定、Phase 2 收尾与 production closure profile

## 5. 验收方式

### 5.1 文档合同测试

- 文档合同测试确认 Phase 4 SPEC registry 中的五份 SPEC 存在并被主计划引用。
- 文档合同测试确认每份 SPEC 都包含边界声明、Residual Readiness（引用共享 Register + 特有门禁）、行为契约测试、实施前置条件和 Phase 5 legacy 判定。
- 主计划不得把 Phase 4 设计状态描述为已交付运行时能力，也不得允许 Phase 5 legacy 提前删除。
- Phase3 production closure 仍以 `scripts/check_phase3_closure_gate.py` 为 gate；当前开发/测试默认使用 MOCK closure，生产发布前必须显式切换 `--closure-profile production` 并提供真实 artifact。
- Phase4 production evidence profile 以 `scripts/check_phase4_runtime_readiness_gate.py --readiness-profile production` 为 gate；该 gate 要求 Phase3 production closure 与 Phase4 evidence manifest 同时通过。
- 业务承载 legacy 删除不得由 mock readiness 触发；只有真实 Phase3 production closure 与 Phase4 production evidence profile 都通过后，业务承载 legacy 才能进入 Phase5 business lane 删除评估。

### 5.2 跨 SPEC 集成测试矩阵

> 各 SPEC 的行为契约测试覆盖域内场景。以下跨域集成测试覆盖 SPEC 之间的交互边界。

| # | 场景 | 涉及 SPEC | 验证点 |
| --- | --- | --- | --- |
| IC1 | CellReservation 创建 → MaterialLocationQuery 返回预约位置 | cell-reservation, material-location-query | 预约在优先级 #3 正确展示，过期后不展示 |
| IC2 | CellReservation 投放成功 → MaterialLocationQuery 返回物理位置 | cell-reservation, material-location-query | 投放后优先级升至 #1，预约不再展示 |
| IC3 | SorterInbound 格位分配 → CellReservation 创建 → WorklineActiveObjects 展示 | sorter-inbound, cell-reservation, workline-active-objects | active object 展示预约状态和 deadline |
| IC4 | WMS reject CellReservation → ReconciliationManager 登记 → MaterialLocationQuery 标记 RECONCILING | sorter-inbound, smt-ng-wms-reconciliation, material-location-query | 冲突状态正确传播到查询视图 |
| IC5 | SCAN1 未授权料箱 → RuntimeHold → WorklineActiveObjects 展示 hold scope | sorter-inbound, workline-active-objects | hold scope 和 allowed_next_effect_scope 正确展示 |
| IC6 | CTU 批次部分到达 → MaterialLocationQuery 展示部分位置 → WorklineActiveObjects 展示批次收敛状态 | sorter-inbound, material-location-query, workline-active-objects | 子项缺失不显示父批次成功 |
| IC7 | NG evidence 到达 → ReconciliationManager 登记 → MaterialLocationQuery 标记对象状态 | smt-ng-wms-reconciliation, material-location-query | NG 对象在查询中正确标记 |
| IC8 | WMS source_version drift → ReconciliationManager 检测 → SorterInbound 暂停该对象 effect | smt-ng-wms-reconciliation, sorter-inbound | drift 触发 effect 禁发闸门 |
