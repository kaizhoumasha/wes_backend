# CellReservation SPEC

> 状态：CellReservation 开发/测试已落地；生产投放热路径未接入
> 父计划：`workline-and-plugin-restructuring.md` §10.5

---

## 1. 边界声明

CellReservation 是作业期格位预约能力，用于防止粗分机、分拣机和 SMT/NG/WMS 对账场景中同一格位被并发双占。它不是库存主数据，不表示 WMS 已确认库存，也不复用旧 plugin 入口。

目标态必须复用并演进现有 `WorklineBinCellReservation`、`WorklineBinCellReservationRepository` 和 `WorklineBinCellReservationService`。禁止新建第二套 reservation model；确需改名或扩展状态时，必须通过迁移和合同测试证明现有 caller 已对齐。

## 2. Residual Readiness

| 遗留门禁 | 本 SPEC 处理方式 |
| --- | --- |
| Callback admission 已关闭 | 预约创建、投放成功、WMS reject 和 source_version drift 只接受带 provider profile / normalizer evidence 的输入 |
| WorkLine runtime projection cleanup 已完成 | 预约状态归 `WorklineBinCellReservation`、`BinCellOccupancy`、`RuntimeHold` 和 `ReconciliationRecord`，不写 WorkLine 运行状态 |
| Runtime production closure profile | 设计与本机开发/测试可完成；当前开发/测试默认使用 MOCK closure，真实 artifact 不再作为当前开发/测试推进阻塞项；生产热路径接入前必须通过 RuntimeInbox cutover 与 `--closure-profile production` |

## 3. 现有模型复用与状态映射

目标语义使用业务可读状态，落库实现先与现有状态映射：

| 目标语义 | 当前持久状态 / 服务结果 | 含义 | 实现前门禁 |
| --- | --- | --- | --- |
| `RESERVED` | `BinCellReservationStatus.PLANNED` | 已预约目标格位，等待物理投放 | 继续作为 active unique 约束口径 |
| `OCCUPIED` | `BinCellReservationStatus.CONSUMED` + `BinCellOccupancy` active fact | 投放成功并已被物理占用 | 确认投放 success evidence 与占用投影同事务或同一幂等链路 |
| `RELEASED` | `BinCellReservationStatus.RELEASED` | 未投放或失败后的预约释放 | 释放必须移动 reservation_key 到 released namespace 或等价幂等口径 |
| `RECONCILING` | `BinCellReservationStatusCode.RECONCILING` + `RuntimeHold` / `ReconciliationRecord` | 预约状态不确定或跨 owner 冲突 | 持久状态缺口必须在 material-flow runtime capability 接入前关闭：新增持久 enum，或明确用 hold/reconciliation 冻结格位且不释放 active 约束 |

现有 `CANCELLED` 只作为管理取消或历史兼容状态；目标态业务流不得把它当成投放失败、WMS reject 或 source_version drift 的常规结果。

## 4. 生命周期合同

```text
RESERVED ──投放成功 evidence──> OCCUPIED
    │
    ├──投放失败 / TTL 未投放过期──> RELEASED
    │
    └──投放结果未知 / WMS reject / source_version drift / owner mismatch──> RECONCILING
          │
          ├──人工确认未占用 / 可重试分配──> RELEASED
          └──人工确认已占用 / WMS 补偿确认──> OCCUPIED
```

不允许在 `RECONCILING` 状态下静默释放格位。只要现场物理事实可能已发生，格位必须保持冻结，直到 RuntimeHold 或 ReconciliationRecord 给出 owner-scoped resolution decision。

## 5. 唯一约束、TTL 与证据

| 合同 | 目标口径 |
| --- | --- |
| active cell 唯一约束 | 当前 `bin_code + bin_cell_index where PLANNED` 必须扩展到目标 active/frozen 语义，避免 RECONCILING 期间被再次预约 |
| object 幂等 | `reservation_key` 或等价 idempotency key 必须覆盖 object identity、correlation_id、target cell 和 source_event_id |
| TTL | 粗分机预约默认 30s，分拣机预约默认 60s；已物理投放或结果未知时 TTL 不得自动释放 |
| evidence | 创建、消费、释放、冻结、人工恢复都必须保留 trace_id、correlation_id、source_event_id、provider_code 和 source_version |

## 6. 行为契约测试

- RESERVED/`PLANNED` 同一格位只能存在一个 active reservation。
- 投放成功 evidence 将 RESERVED 转为 OCCUPIED，并生成或确认 `BinCellOccupancy` active fact。
- 投放失败且确认未投放时释放预约，并保持幂等重复释放不报错。
- 投放超时或 owner mismatch 时进入 RECONCILING，不释放 active/frozen 格位。
- WMS reject 已物理投放对象时保留本地物理事实，并登记 RuntimeHold / ReconciliationRecord。
- source_version drift 触发 WMS reconciliation query，不覆盖已有预约或占用 evidence。
- `MaterialLocationQuery` 读取 RESERVED/OCCUPIED/RECONCILING 时分别返回预约位置、物理位置、冲突状态。

## 7. 实施前置条件

实现 sorter inbound 或对账热路径前，必须先完成下列门禁：

- 明确 `RECONCILING` 是否成为 `BinCellReservationStatus` 持久 enum；若不新增 enum，必须用 RuntimeHold/ReconciliationRecord 冻结格位并补合同测试。
- 明确 `PLANNED`/`CONSUMED` 是否保留为数据库内部命名，或通过迁移改名为 `RESERVED`/`OCCUPIED`。
- 确认现有 `WorklineBinCellReservationService.claim_bin_cell()`、`consume_bin_cell()`、`release_bin_cell()` 与目标生命周期映射一致。
- Runtime residual gate 未关闭或 production closure profile 未通过时，只允许设计、characterization mapping 和本机 MOCK 验收，不允许生产热路径上线；callback admission 证据需保持绿灯。

## 8. Legacy cleanup 判定

只有当 CellReservation 的状态映射、active/frozen 唯一约束、TTL、投放成功、失败释放、WMS reject 和 source_version drift 行为契约全部通过后，legacy cleanup 才能删除旧 plugin 中等价格位预约或预占逻辑。未覆盖到的 legacy 只能冻结入口并保留 characterization tests。
