# MaterialLocationQuery SPEC

> 状态：Phase 4 设计 SPEC，未实现
> 父计划：`workline-and-plugin-restructuring.md` §10.5

---

## 1. 边界声明

MaterialLocationQuery 是作业期位置查询能力，不是状态 owner。它只汇总已存在的本地物理事实、active projection、reservation、ExternalReference 和 WMS 对账 snapshot，向 API / 运维视图提供可解释的位置事实。

不复用旧 plugin 入口，不直接读取 WMS DTO/client，不复制 WMS 主数据，不把 `WorkLine.runtime_status` 作为位置判断来源。

## 2. Residual Readiness

| 遗留门禁 | 本 SPEC 处理方式 |
| --- | --- |
| Phase 1 callback normalizer admission 已关闭 | 查询响应仍必须保留 `provider_code` / `source_event_id` / `ExternalReference`，不信任裸 callback payload |
| Phase 2 `WorkLine.runtime_status` 兼容投影未清空 | 查询接口不输出或推导 WorkLine 运行状态，只输出对象位置和 evidence |
| Phase 3 RuntimeInbox / closure artifact 未关闭 | 查询设计可落地为只读合同；生产上线前必须确认 RuntimeInbox cutover 和 Phase 3 closure gate |

## 3. 查询入口

首版支持 6 个入口，所有入口返回同一类位置结果：

| 入口 | 用途 |
| --- | --- |
| by material identity | 按 material_code / batch / material_unit_key 查询物料位置 |
| by package or bin | 按 package_id / bin_code 查询料箱和格位 |
| by rack and side | 按 rack_code / rack_side 查询单层货架和满箱交换状态 |
| by workline active object | 查询某 WorkLine 当前 active 对象的位置事实 |
| by ExternalReference | 从 WMS/ECS/device external reference 反查本地位置 |
| by correlation_id | 从 ExecutionCorrelation / work item 追踪位置 |

## 4. 来源优先级

同一对象出现多个位置事实时，按下列优先级解释，并保留低优先级 evidence 供审计：

1. 本地物理完成事实：设备/ECS 成功 evidence 后落库的位置事实。
2. ActiveObjectRegistry 当前归属：ON_CONVEYOR / AT_WORK_POSITION / IN_TRANSFER 等 active projection。
3. CellReservation：尚未物理完成但已声明的目标格位预约；生命周期和状态映射以 `cell-reservation-spec.md` 为准。
4. WMS reconciliation snapshot：只读外部权威事实，用于 drift 或补充说明。
5. Legacy characterization evidence：仅用于迁移期解释，不作为新业务写入依据。

若优先级 1-3 互相冲突，结果必须标记 `RECONCILING`，并指向对应 evidence；不得静默选择任一位置。

## 5. 响应合同

响应至少表达：

- object identity：object_type、object_key、workline_id。
- location summary：location_type、location_key、source_priority。
- source and authority：source_system、provider_code、source_version、ExternalReference。
- evidence：trace_id、correlation_id、source_event_id、evidence_at。
- conflict state：OK / STALE / RECONCILING / UNKNOWN。

## 6. 行为契约测试

- 同一物料只存在本地物理完成事实时，返回该位置。
- 本地物理事实与 WMS snapshot 不一致时，返回本地位置并标记 WMS drift。
- active projection 多源冲突时，返回 RECONCILING，不吞掉任一 evidence。
- CellReservation 过期或被占用时，不得作为可用目标位置返回。
- ExternalReference 能反查到 correlation_id 和位置 evidence。
- legacy characterization evidence 只能作为解释来源，不能覆盖目标态事实。

## 7. 实施前置条件

实现前必须确认 Phase 1 callback normalizer admission 不改变 ExternalReference 字段口径；`CellReservation` 的 RESERVED/OCCUPIED/RECONCILING 与现有 `WorklineBinCellReservation` 映射已按 `cell-reservation-spec.md` 锁定；生产上线前必须通过 Phase 3 closure gate。

## 8. 性能预算

| 指标 | 目标 | 备注 |
|------|------|------|
| 单次查询 P95 延迟 | < 300ms | 6 入口统一预算；5 来源 UNION 查询 |
| 最大返回行数 | 500 行/次 | 超限返回 `truncated=true` + `total_count` |
| 分页策略 | cursor-based（`correlation_id + evidence_at`） | 不使用 offset/limit 深分页 |
| 缓存策略 | 无缓存（位置事实实时性优先） | 查询服务是只读聚合，不缓存结果 |
| WMS snapshot 查询超时 | 5s | 异步查询，不在请求链内；超时后跳过优先级 #4，标记 `source=WMS_UNAVAILABLE`；snapshot 新鲜度由上次成功查询时间决定 |
| 并发查询限流 | 50 req/s per WorkLine | 超过限流返回 429 |

## 9. Phase 5 legacy 判定

只有当 MaterialLocationQuery 的 6 个入口合同测试通过，且旧 plugin 中对应位置查询入口已被 characterization mapping 覆盖后，Phase 5 才能删除这些 legacy 查询入口。
