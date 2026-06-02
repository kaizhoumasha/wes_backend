# WorkLine Fast-Fail 与 SMT 分拣入库计划依赖说明

日期：2026-06-01

## 计划清单

1. [Workline Fast-Fail START Admission Implementation Plan](2026-06-01-workline-fast-fail-start-admission-plan.md)
2. [SMT Sorting Inbound WorkLine P0 Implementation Plan](2026-06-01-smt-sorting-inbound-workline-p0-plan.md)

## 总体依赖

`Workline Fast-Fail START Admission` 是平台级运行时底座，必须先于 SMT Sorting Plugin PR 完成。它定义 WorkLine 是否允许接收生产事件、START 准入如何进入 `READY`、生产事件何时 fast-fail、设备命令下发前如何做 realtime status guard。

`SMT Sorting Inbound P0` 是插件级业务闭环，必须消费上述平台合同。它不应该重新定义 WorkLine 是否接收生产事件，也不应该绕过设备命令 status guard。

## 可并行范围

可以并行：

- SMT Sorting Foundation PR 中的共享分格策略。
- `BinCellOccupancy` Decimal/Numeric 深度迁移。
- `MATERIAL_UNMOUNTED` 资源投影。
- `NG_MATERIAL_CONFLICT` 结构化冲突。
- SMT Sorting 插件的纯 context 合同设计和单元测试骨架。

这些工作只依赖 resource/workline 本地模型和服务，不依赖 `/callback/event` START 入口已经落地。

## 必须串行范围

必须等 Fast-Fail/START 合并后再做或再验收：

- SMT Sorting 插件的真实生产 event 入口联调。
- SMT Sorting 插件通过 WorkLine command dispatch 下发机械臂命令。
- SMT Sorting 沙箱从 `STOPPED -> START -> READY -> sorting event -> command` 的端到端验证。
- 与前端/Swagger/mock 相关的普通生产事件发送体验。

原因：这些路径必须遵守平台级 `READY` guard 和 command 前 realtime device status guard。否则插件 P0 可能在旧的默认 `READY` 语义下通过测试，但上线到新运行时后行为不一致。

## 推荐落地顺序

1. 执行 Fast-Fail/START plan 到完成，含后端、mock、frontend 和 `TODOS.md` follow-up。
2. 执行 SMT Sorting Foundation PR，合并共享策略、Decimal/Numeric、`MATERIAL_UNMOUNTED`、`NG_MATERIAL_CONFLICT`。
3. 执行 SMT Sorting Plugin PR，接入 `SortingInboundContext`、源端取盘、扫码分格、目标放盘、本地 NG 和 Session 完成检查。
4. 做跨计划验收：`STOPPED -> START -> READY` 后触发 SMT Sorting P0 happy path，确认生产事件、命令下发、资源投影和 NG 分支都遵守同一运行时合同。

## 跨计划验收

状态：已补充后端 `pytest + SQLite + service/plugin/gateway stitching smoke`：
`tests/integration/workline_runtime/test_cross_plan_sandbox_smoke.py`。该 smoke 覆盖 `STOPPED -> START -> READY`
后串联 SMT Sorting 源端取盘、扫码分格、目标放盘、命令派发前 realtime status guard、本地 NG 和 Session 完成。

必须同时满足：

- [x] WorkLine 初始为 `STOPPED`，普通 SMT Sorting 生产事件返回 HTTP 409，且不创建 inbox。
- [x] ECS/mock 发送 `WORKLINE_START_REQUESTED`，START 准入成功后 WorkLine 进入 `READY`。
- [x] SMT Sorting 目标端命令下发前，`DeviceCommandGateway` 完成 realtime status GET。
- [x] 源端取盘成功后，插件 intent 产出一次 `MATERIAL_UNMOUNTED`，重复上报不会重复出账。
- [x] 扫码成功后，Session context 写入 `pending_target_placement`，目标端命令使用该落点。
- [x] 目标端成功后，插件 intent 产出 `MATERIAL_MOUNTED`，`current_material` 关闭。
- [x] 源端快照不一致时进入本地 NG，插件 intent/context 记录 `LOCAL_SORTING_NG`，不触发该盘目标箱 WMS 物料变化。
- [ ] 任一 `NG_MATERIAL_CONFLICT` 会阻止 Session 完成，直到人工或对账解除。

剩余跟踪：

- [ ] 补一条 runtime orchestrator/effect 层 thin smoke，覆盖 `intent -> outbox/resource fact/session context`
      持久化的真实衔接。
- [ ] 补 `NG_MATERIAL_CONFLICT` 阻止 Session 完成的跨计划 smoke；当前 smoke 已覆盖本地 NG success 分支。

## 冲突与回滚边界

- Fast-Fail/START plan 回滚时，SMT Sorting Plugin PR 不应继续验收真实 callback/command 流；只能保留 Foundation PR 和纯单元测试。
- SMT Sorting Foundation PR 回滚时，不影响 Fast-Fail/START 平台运行时合同。
- SMT Sorting Plugin PR 回滚时，应保留 Foundation PR，除非共享策略或 Decimal/Numeric 迁移本身被证明有问题。

## 执行提示

- 两个计划都要求执行前运行 GitNexus impact，提交前运行 `gitnexus_detect_changes()`。
- 如果使用 worktree，并行 worktree 必须按仓库规则放在 `/Users/kaizhou/SynologyDrive/works/worktrees/wes_backend`，且各自初始化 `.env` 和 `.venv`。
- 如果两个计划并行开发，避免两个分支同时修改 `src/app/workline/services` 中同一批 runtime service；Foundation PR 优先在 `src/app/resource` 和 `src/workline_plugins` 边界内推进。
