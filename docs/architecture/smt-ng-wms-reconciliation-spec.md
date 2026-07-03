# SMT / NG / WMS Reconciliation SPEC

> 状态：Phase 4 设计 SPEC，未实现
> 父计划：`workline-and-plugin-restructuring.md` §10.5

---

## 1. 边界声明

本 SPEC 定义 SMT、NG 与 WMS 同步/对账闭环。WES 不复制 WMS/NG/PDA 主数据，不拥有返工工单主档，不实现 PDA 离线原生流程。WES 只保留 evidence、ExternalReference、RuntimeHold、ReconciliationRecord 和解除条件。

不复用旧 plugin 入口，不把 WMS 确认结果写成 WES 主数据，不绕过 ReconciliationManager 直接改跨域 owner 状态。

## 2. Residual Readiness

| 遗留门禁 | 本 SPEC 处理方式 |
| --- | --- |
| Phase 1 callback admission 未关闭 | NG/WMS callback 设计必须依赖 provider profile admission 和 typed normalizer |
| Phase 2 runtime status 兼容投影未清空 | 对账状态不写 WorkLine 运行状态，使用 RuntimeHold / ReconciliationRecord |
| Phase 3 closure 未关闭 | 生产闭环实现必须等待 RuntimeInbox cutover、P0 E2E artifact 和 production benchmark artifact |

## 3. 业务事实边界

| 事实 | Owner | WES 保存 |
| --- | --- | --- |
| NG 原因主数据 | WMS / NG 系统 | reason_code、reason_label snapshot、ExternalReference |
| PDA 操作记录 | PDA / WMS | evidence envelope、operator reference、trace_id |
| WMS 库存确认 | WMS | confirmation reference、source_version、callback payload hash |
| 本地物理完成 | WES runtime/resource/material owner | 本地位置事实、MaterialUnit 状态、CellReservation 结果 |
| 冲突决议 | WES ReconciliationManager | ReconciliationRecord、RuntimeHold、resolution_decision |

## 4. 对账场景

- NG evidence 到达但本地对象未处于可 NG 状态：登记 RECONCILING。
- WMS 确认成功但本地物理事实缺失：登记 MISSING_LOCAL_PHYSICAL_FACT。
- 本地物理完成但 WMS 拒绝：进入 WMS_SYNC_PENDING 或 RECONCILING。
- 目标箱回写失败：保留本地事实，创建 RuntimeHold，允许重试 WMS effect。
- source_version 漂移：保留旧 evidence，等待 WMS drift query 决议；涉及格位预约时按 `cell-reservation-spec.md` 冻结 active/frozen 语义。
- 重复 callback 同 hash：幂等合并；不同 hash：409 audit + RECONCILING。

## 5. RuntimeHold 解除条件

RuntimeHold 解除必须声明：

- scope：object / device / resource / queue。
- allowed_next_effect_scope：解除后允许的下一类 effect。
- evidence requirements：WMS callback、operator decision、reconciliation query 或 manual override。
- audit：operator、reason、trace_id、before/after state。

人工解除不得默认释放整条 WorkLine；单对象异常只释放声明 scope。

## 6. 行为契约测试

- NG evidence 与本地状态一致时，正常进入 NG 处理视图。
- NG evidence 与本地状态不一致时，进入 RECONCILING。
- WMS 确认成功但缺本地事实时，不显示业务完成。
- WMS 拒绝目标箱回写时，本地物理事实仍保留。
- source_version drift 能触发 WMS reconciliation query。
- RuntimeHold 解除只释放声明 scope。
- 重复 callback 同 hash 幂等，不同 hash 进入冲突审计。

## 7. 实施前置条件

实现前必须确认 NG/WMS callback 已经过 provider profile admission，RuntimeInbox production hot path 已可重试/死信/人工重放，CellReservation RECONCILING 持久化或冻结口径已按 `cell-reservation-spec.md` 锁定，Phase 3 closure gate 已通过。

## 8. Phase 5 legacy 判定

旧 SMT/NG/WMS 对账入口只有在上述行为契约通过，并能用 ExternalReference 追溯所有旧关键场景后才能删除。仍承载 NG/PDA/WMS 主数据语义的 legacy 不得删除，只能冻结或迁出 owner。
