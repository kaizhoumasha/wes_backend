# WorklineActiveObjects / WorklineCurrentWorkView SPEC

> 状态：Phase 4 Wave1 开发/测试已落地；生产 SLA/benchmark 随 production closure profile 验收
> 父计划：`workline-and-plugin-restructuring.md` §10.5

---

## 1. 边界声明

WorklineActiveObjects 是 WorkLine 当前作业对象的只读聚合视图。它与 `ActiveObjectRegistry` 协同展示 active/current work，不拥有业务终态，不直接写 owner 状态，也不绕过 ReconciliationManager。

不复用旧 plugin context，不新增跨域 session FK，不依赖 WorkLine 运行态推导当前作业状态。

## 2. Residual Readiness

| 遗留门禁 | 本 SPEC 处理方式 |
| --- | --- |
| Phase 1 callback admission 已关闭 | 所有入站来源必须保留 source/provider/evidence，视图不接受无 profile 的来源 |
| Phase 2 WorkLine 运行态 final cleanup 已完成 | 视图只读 runtime/orchestration、active projection 和 evidence，不读 WorkLine 运行状态作为 owner |
| Phase 3 closure profile | 允许定义视图合同；当前开发/测试默认使用 MOCK closure，真实 artifact 不再作为当前开发/测试推进阻塞项；正式上线前必须显式通过 `--closure-profile production` |

## 3. 视图职责

视图向 API / 运维界面提供：

- WorkLine 当前 active 对象集合。
- 对象所在阶段、位置、关联 work item 和设备/队列上下文。
- 多来源冲突、瞬态冲突窗口和 RECONCILING 状态。
- 父批次与子 work item 的收敛情况。

## 4. 归一化输入

首版只读聚合下列来源：

| 来源 | 说明 |
| --- | --- |
| ActiveObjectRegistry | 跨投影唯一 active 归属仲裁结果 |
| ExecutionWorkItem | 当前对象级 work item、父子关系、deadline |
| ConveyorQueueMembership | 队列/工作位 active membership |
| RuntimeHold / ReconciliationRecord | hold、freeze、manual recovery 与冲突解释 |
| MaterialLocationQuery | 对象位置摘要、CellReservation 状态和 ExternalReference |

## 5. 冲突展示

同一 object 在多个 active 来源出现时，必须展示：

- all_sources：所有来源、位置和 evidence。
- primary_source：若存在唯一可解释主来源，指出选择依据。
- conflict_state：OK / TRANSIENT / RECONCILING。
- operator_hint：只给出恢复方向，不直接写状态。

超过 transient_until 的冲突必须进入 RECONCILING，交由 ReconciliationManager 决议。

## 6. 行为契约测试

- 单来源 active object 返回 OK。
- ON_CONVEYOR + AT_WORK_POSITION 同时出现时进入 RECONCILING。
- IN_TRANSFER + ON_CONVEYOR 在 transient window 内返回 TRANSIENT，超时后返回 RECONCILING。
- RuntimeHold open 时视图展示 freeze scope 和 allowed_next_effect_scope。
- 父 work item 成功但子项缺失时，视图不得显示父批次业务成功。
- WorkLine 运行态不参与 active object 状态推导。

## 7. 实施前置条件

实现可以作为只读查询薄片先行；若要展示 reservation deadline、冻结格位或 RECONCILING 来源，必须先引用 `cell-reservation-spec.md` 中的状态映射；若要接入生产运维界面，必须先通过 `scripts/check_phase3_closure_gate.py --closure-profile production ...`。

## 8. 性能预算

| 指标 | 目标 | 备注 |
|------|------|------|
| 单次查询 P95 延迟 | < 500ms | 5 来源聚合 + 冲突检测 |
| 最大 active objects 返回数 | 200 条/WorkLine | 超限返回 `truncated=true` + `total_count` |
| 最大 conflicts 返回数 | 50 条/WorkLine | 按 `detected_at DESC` |
| 轮询频率 | 1 Hz（scene）/ 250ms（snapshot） | 对齐 §5.2 plane 接口频率 |
| transient_until 默认窗口 | 30s | 超时后 TRANSIENT → RECONCILING |
| 缓存策略 | 无缓存（active projection 实时性优先） | 每次查询实时聚合 |

## 9. Phase 5 legacy 判定

当 WorklineActiveObjects 覆盖旧当前作业视图、旧 active projection 查询和相关 characterization tests 后，Phase 5 才能删除对应 legacy 读入口。承载业务写入或恢复动作的 legacy 入口不得因本视图存在而删除。
