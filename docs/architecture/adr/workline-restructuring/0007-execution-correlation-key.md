# ADR 0007: ExecutionCorrelation Correlation Key 替代 Session FK 跨域扩散

**状态**: Accepted
**日期**: 2026-06-23
**适用范围**: runtime 之外的所有域的 session/execution_session FK 引用

## 背景

实测 16+ 模型文件包含 `session_id` / `execution_session_id` / `current_session_id` 跨域 FK（workline/models/runtime.py 13 处、timeline.py 7 处、inbox.py 4 处、smt_inbound_handoff.py 3 处、operation.py 3 处、handling/models/bin_transit_membership.py 2 处、object_transition_event.py 2 处、material_unit.py 2 处、bin_cell_reservation.py 2 处、resource/models/resource.py 2 处 等）。**runtime 之外的域不能把 `execution_session.id` 作为强 FK 扩散**——session 域重命名/拆分/迁移会破坏所有这些引用，构成"分布式单体"耦合。

## 决策

1. **引入 `ExecutionCorrelation` correlation key**：
   ```text
   ExecutionCorrelation
     correlation_id          (UUID, 跨域唯一)
     execution_session_id    (FK -> RuntimeSessionAggregate, runtime 域内强 FK)
     trace_id                (跨域 trace 标识)
     source_event_id         (来源事件)
     business_owner_key      (业务 owner, 12 审计用)
     created_at
   ```
2. **跨域约束**：跨域读写都通过 `correlation_id`，不通过 `execution_session.id`；runtime 域内才使用 `execution_session_id` 强 FK；其他域只持 `correlation_id` 引用。
3. **现有 16+ 文件迁移**：输出 `docs/architecture/session-correlation-matrix.md` 列出 per-file 迁移路径（rename / dual-write / drop-FK）。
4. **start_admission_service 改造方案**：从单字段 `session_id: str` 迁到 `correlation_id: UUID` + 保留 `workline_session_id: int` 仅在 runtime 域内。
5. **不保留旧 string `session_id` 兼容入口**：C0 已决定破坏性切换；新写面统一 `correlation_id`。

## 后果

- 跨域 session 引用从"FK 强耦合"变成"correlation key 弱耦合"。
- session 域重命名/拆分/迁移不会破坏 16+ 文件。
- 现有 16+ 文件需要 per-file 迁移矩阵；EN-001 是 L effort。
- start_admission_service 27.8K LOC 流程需要适配。

## 验收

- `docs/architecture/specs/workline-restructuring/10-runtime-orchestration.md` §ExecutionCorrelation 发布。
- `docs/architecture/session-correlation-matrix.md` 列出 16+ 文件迁移路径。
- `src/app/runtime/`（新建）含 `ExecutionCorrelation` 模型。
- Alembic 迁移 + downgrade。
- 新写面 grep 测试：无新代码用 string `session_id` 跨域。

## 引用

- 顶层设计：[`../../workline-and-plugin-restructuring.md`](../../workline-and-plugin-restructuring.md)
- Sub-spec 10 §ExecutionCorrelation：[`../../specs/workline-restructuring/10-runtime-orchestration.md`](../../specs/workline-restructuring/10-runtime-orchestration.md)
- C0 sub-spec：[`../../../superpowers/specs/2026-06-23-workline-c0-resource-projection-foundation.md`](../../../superpowers/specs/2026-06-23-workline-c0-resource-projection-foundation.md) §C0-1
