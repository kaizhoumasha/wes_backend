# SMT / NG / WMS Reconciliation SPEC

> 状态：Phase 4 runtime capability 已落地；evidence profile 未闭合
> 父计划：`workline-and-plugin-restructuring.md` §10.5

---

## 1. 边界声明

本 SPEC 定义 SMT、NG 与 WMS 同步/对账闭环。WES 不复制 WMS/NG/PDA 主数据，不拥有返工工单主档，不实现 PDA 离线原生流程。WES 只保留 evidence、ExternalReference、RuntimeHold、ReconciliationRecord 和解除条件。

不复用旧 plugin 入口，不把 WMS 确认结果写成 WES 主数据，不绕过 ReconciliationManager 直接改跨域 owner 状态。

## 2. Residual Readiness

| 遗留门禁 | 本 SPEC 处理方式 |
| --- | --- |
| Phase 1 callback admission 已关闭 | NG/WMS callback 设计必须依赖 provider profile admission 和 typed normalizer |
| Phase 2 runtime status 兼容投影未清空 | 对账状态不写 WorkLine 运行状态，使用 RuntimeHold / ReconciliationRecord |
| Phase 3 closure profile | 设计与本机 MOCK 验收可完成；当前开发/测试默认使用 MOCK closure，真实 artifact 不再作为当前开发/测试推进阻塞项；生产闭环接入前必须通过 RuntimeInbox cutover 与 `--closure-profile production` |

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
- 乱序 callback：按 occurred_at、source_version 和 source_event_id 建立因果边界；迟到旧版本只能登记 evidence，不能回滚本地物理事实或释放 RuntimeHold。

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
- 乱序 callback 不覆盖更新版本状态，不触发未授权 effect 重放。

## 7. 实施前置条件

生产热路径实现前必须确认 RuntimeInbox production hot path 已可重试/死信/人工重放，CellReservation RECONCILING 持久化或冻结口径已按 `cell-reservation-spec.md` 锁定，并显式通过 `scripts/check_phase3_closure_gate.py --closure-profile production ...`。

### 7.1 本机开发环境 MOCK 验收

Wave3 SMT/NG/WMS 对账本轮降级为本机开发环境 MOCK 验收，不做生产接入。验收入口固定为 `tests/mock/phase4` 与本机 WMS reconciliation mock：

- mock 必须能表达 NG evidence、本地物理事实缺失、WMS 拒绝、目标箱回写失败、重复 callback、乱序 callback 与 source_version drift。
- mock 返回的对账快照必须标记 `LOCAL_MOCK_ONLY`，且 `production_write_path=false`。
- mock 验收不得注册生产 callback cutover、真实 WMS reconciliation query client、RuntimeInbox worker 或 SMT/NG/WMS 生产热路径。
- 生产热路径仍必须等待 Phase 2 residual gate 与 production closure profile 通过，并保持 Phase1 callback admission 证据绿灯；mock 通过只说明本机合同可验收。

## 8. Phase 5 legacy 判定

旧 SMT/NG/WMS 对账入口只有在上述行为契约通过，并能用 ExternalReference 追溯所有旧关键场景后才能删除。仍承载 NG/PDA/WMS 主数据语义的 legacy 不得删除，只能冻结或迁出 owner。
