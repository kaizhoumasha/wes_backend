# WES Phase 3 无状态 WMS ACL 收敛计划

> **Status:** `BLOCKED_AT_TASK_1`。
> 当前只允许继续完善文档合同，不得启动生产代码、测试或 Migration 实施。

**Goal:** 消费 Phase 2 `OutboundHttpTransport`，为当前 SRS 业务消费者提供无状态、类型化、最小化的 WMS
Anti-Corruption Layer；不实现或承载 WMS 转发的 RCS/AGV/CTU Transport 能力。

**Requirements baseline:** `docs/architecture/SRS.md`。

**Business wire owner:** `docs/contracts/wms-northbound-interaction-contract.md`。

**Architecture baseline:**
`docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md`。

## 1. 阶段裁决

### 1.1 Phase 3 是什么

Phase 3 只暗构建 WMS 业务 ACL：

- WMS 权威业务事实查询。
- WES → WMS `PickingTask` 来源绑定请求与业务事实通知。
- WMS → WES 业务命令的 DTO 与无状态校验/标准化。
- 固定 method/path、严格 DTO、请求编码、响应解析和单次结果翻译。
- 消费 Phase 2 Transport，不复制连接池、超时、响应上限或传输事实分类。

每项公开能力必须有当前消费者。厂商初稿有接口但当前业务无消费者时，不实施。

### 1.2 Phase 3 不是什么

Phase 3 不包含：

- RCS/AGV/CTU 搬运、补给、交换、旋转、status 或 cancel。
- `Transport Port`、`TransportTask` 或 WMS 转发 RCS Client。
- `WmsConfirmation`、`InboundEvidence` 的持久化和业务生命周期。
- 数据库表、Repository、Service、Alembic Migration、调用 evidence 或 Circuit Breaker。
- retry、scanner、claim、fencing、reconciliation 生命周期或投影推进。
- WorkLine Decision、设备命令、厂商 Payload 或生产 Composition Root 接线。
- 旧 Provider/Profile/Registry、兼容 alias、fallback、双读、双写或 shadow request。

即使搬运 HTTP 最终发给 WMS，只要语义是 WMS 转发 RCS，仍属于 Phase 4 Transport，而不是 Phase 3 WMS ACL。

## 2. 与前后阶段的唯一接缝

| 阶段 | 向 Phase 3 提供 / 从 Phase 3 接收 |
| --- | --- |
| Phase 2 | 提供单次发送、长期 Client、有界响应和传输事实；不提供 WMS 业务解释 |
| Phase 3 | 提供类型化 WMS Query、Picking Source Binding、Confirmation Port、inbound DTO/normalizer 和封闭单次调用结果 |
| Phase 4 | 直接消费 Phase 2 构建 WMS 转发 RCS Adapter；消费 Phase 3 三条业务端口构建 `WmsConfirmation` 与 `InboundEvidence` 可靠 owner |
| Phase 5 | 原子切换生产消费者和 Composition Root，删除旧 WMS/Transport owner；不保留双轨 |

Phase 4 可以直接依赖 Phase 2 的 Transport Protocol 构建 Transport Adapter，但不得绕过 Phase 3 直接调用 WMS
业务查询或确认接口。

## 3. 消费者驱动准入

Phase 3 不再维护固定“33 项 operation”目标。能力清单只来自
`docs/contracts/wms-northbound-interaction-contract.md` 的消费者矩阵，并遵守：

1. 一项能力对应至少一个当前业务消费者。
2. 同一业务事实只允许一个主要 wire owner。
3. 查询只返回 WMS 权威事实，不用于为 `PickingTask` 拼装、锁定或重新分配来源，也不建立本地主数据同步。
4. `request_picking_source_bindings` 只请求 WMS 原子生成完整来源绑定；WMS 拥有库存资格、库存锁、料格冻结和跨任务分配。
5. 确认只提交已发生物理事实或明确业务义务，不隐式触发 Transport；逐项位置事实与整单完成事实不得合并或双写。
6. 未获 WMS 批准的 wire 保持缺席，不用占位 DTO、generic payload 或旧实现补全。

当前保留候选分为四类：

- 权威事实查询：物料、货架、料箱、GRN/Package、库存和预留。
- 来源绑定请求：`STARTING` 的 PickingTask 请求 WMS 返回完整原子来源绑定或明确无完整方案。
- 业务确认：预留/释放、入库、非 PickingTask 库存转移、PKG 绑定、来源 NG、逐项位置完成、PickingTask 完成、人工工作位就绪。
- 业务输入：PickingTask 目标项创建/更新、原子替代来源批次或明确无完整替代方案、恢复/取消，以及人工作业完成。

其中条件能力和未批准 wire 以合同 §5 为准；Task 1 关闭前不得生成代码文件清单。

## 4. 目标代码边界

Task 1 退出后，目标包最多包含以下责任：

```text
src/app/wms_adapter/
├── __init__.py       # 只导出业务端口、配置、outcome、factory
├── config.py         # base_url + timeout；当前认证 NONE
├── outcomes.py       # 一次调用的业务/依赖/合同结果
├── ports.py          # WMS Query、Picking Source Binding 与 Confirmation 窄端口
├── gateway.py        # method/path/DTO 编码、一次 send、结果翻译
├── factory.py        # 消费 Phase 2 builder；不接收 session_factory
├── _shared.py        # 至少三个能力真实共享的严格值对象
├── queries/          # 仅 Task 1 批准的查询
├── source_bindings/  # PickingTask 完整原子来源绑定
├── confirmations/    # 仅 Task 1 批准的业务确认
└── inbound/          # WMS 业务命令 DTO 与无状态 normalizer
```

明确不得出现：

```text
models/
repositories/
services/
call_control.py
circuit_breaker.py
wms_outbound_call_evidence
wms_outbound_circuit_breaker
transport/
reconciliation/
```

`HttpWmsGateway` 行为无状态，只持有 Phase 2 Transport。每个公开调用恰好形成一次 `send`；无自动 retry、轮询、
分页聚合或 callback 等待。

## 5. 公开端口

Phase 3 提供三条职责互斥的 outbound 业务端口：

- `WmsBusinessQueryPort`：读取 WMS 权威事实，不产生副作用。
- `WmsPickingSourceBindingPort`：只暴露语义 operation `request_picking_source_bindings`，请求 WMS 为 `STARTING`
  PickingTask 原子生成完整来源绑定。
- `WmsBusinessConfirmationPort`：提交同步业务确认，不拥有可靠义务生命周期。

`request_picking_source_bindings` 的成功结果必须精确覆盖全部目标任务项，每项携带完整 SixInOne 和唯一 `PkgID`；
另一合法结果是明确“无完整方案”。部分绑定不得生效，也不得提前申请目标架。method/path/wire 仍须由 WMS 批准。

WMS inbound 只提供 DTO 和 normalizer，不在 Phase 3 暴露绕过 `InboundEvidence` 的业务处理端口。Phase 4 API/应用层
负责先持久化证据再 ACK，并调用对应业务对象。

禁止提供：

- `WmsForwardedTransportClient`
- `WmsCallControl`
- 字符串 operation selector
- generic `call`
- 生产 registry 或动态发现

## 6. Outcome 边界

Phase 3 的 outcome 只表达本次调用事实：

- WMS 成功并通过 DTO 校验。
- WMS 明确业务拒绝。
- 请求未发送。
- 交付状态未知。
- 远端响应或合同无效。

Outcome 不决定 retry、dependency pause、terminal 或投影变化。Phase 4 可靠对象根据 Phase 2 交付事实、Phase 3
业务结果和自身持久化状态作出裁决。

## 7. 实施任务

### Task 1：冻结消费者矩阵与 WMS wire

**范围：纯文档。**

- [x] 撤销“33 项固定 surface”和 Phase 3 READY 结论。
- [x] 将 WMS 转发 RCS 的 submit/status/cancel 全部移交 Phase 4。
- [x] 删除 reconciliation、通用 load-unit transport、复合粗分 admission 和错误方向的 manual task 候选。
- [x] 为保留、条件保留和排除能力登记消费者及 successor/`NONE`。
- [x] 冻结 PickingTask 只含目标项、STARTING 原子来源绑定、WMS 库存权威、逐项位置事实与整单完成独立、替代批次原子语义。
- [ ] 由业务方确认库存查询、reserve/release、transfer 的真实消费者及原子边界。
- [ ] 由 WMS 批准 `request_picking_source_bindings` 的 method/path/request/result/错误码/幂等语义。
- [ ] 由 WMS 批准全部保留能力的 method/path/request/result/错误码/幂等语义。
- [ ] 由 WMS 批准 PickingTask 与人工作业 inbound wire。

**验证：** Markdown 格式、项目内引用闭包、硬件原文 hash、`git diff --check`。不新增或修改测试代码。

### Task 2：建立共享无状态边界

**前置：Task 1 全部完成。**

- 使用 TDD 建立严格 DTO 基类、最小配置和封闭 outcome。
- 配置只包含 WMS origin 与 timeout；当前认证固定为 `NONE`。
- 不接受 Session、Repository、Provider、credential、retry 或 breaker 参数。

### Task 3：实现获批查询

- 只为 Task 1 矩阵中获批的查询建立语义模块和显式端口方法。
- 每个查询一次请求、一次有界响应；没有缓存、分页 seam 或自动续页。
- 查询不得用于 PickingTask 来源选择、库存锁、料格冻结或跨任务分配。
- 测试只证明 WMS DTO/wire，不测试 WorkLine 决策或库存算法。

### Task 4：实现获批来源绑定与业务确认

- 以独立 `WmsPickingSourceBindingPort` 实现 `request_picking_source_bindings`；不并入 Query 或 Confirmation Port。
- 只翻译完整原子来源绑定或无完整方案，不决定 `WAITING_STOCK`、目标架调度或业务推进。
- 只实现 Task 1 获批的同步业务确认。
- 每个方法只负责 DTO、固定 wire 和结果翻译。
- 逐项位置通知与 `confirm_picking_completed` 分别建模；不复用 `transfer_inventory` 双写同一事实。
- 不持久化来源绑定或确认义务，不自动 retry，不把成功直接解释为 WorkLine 推进。

### Task 5：实现 WMS inbound DTO 与 normalizer

- 实现获批的 PickingTask 和人工作业输入 DTO。
- 只验证封闭 wire 和规范化结果，不创建任务、不访问数据库、不返回业务完成。
- Phase 4 负责 `InboundEvidence`、幂等、ACK 和业务对象推进。

### Task 6：实现 Gateway 与 factory

- Gateway 只依赖 Phase 2 `OutboundHttpTransport`。
- factory 只消费 Phase 2 builder，不接收 `session_factory`。
- 每个方法固定映射一个获批 method/path 和 request/result 类型。
- 取消原样传播；不实现 retry、breaker 或持久化 evidence。

### Task 7：边界门禁与退出验证

- 新包零数据库模型、Migration、Repository、Service、breaker、transport 和旧 WMS import。
- 生产 `src/` 在 Phase 5 前不得 import 新包，不建立 feature flag 或双轨。
- 运行新 WMS ACL FAST 合同、Phase 2 回归、Ruff、类型检查、Import Linter 和 quality profile。
- 若新增测试路径，按测试治理更新精确 HEAVY selector；纯 DTO/Gateway 路径经评审可显式 NONE。

## 8. Task 1 当前阻断

- `release_reservation` 当前 method 尚未获 WMS 确认。
- 库存 query/reserve/release/transfer 的当前消费者和原子边界尚未全部确认。
- `request_picking_source_bindings` 业务语义已冻结，但完整 wire 尚未获 WMS 批准。
- NG、逐项位置完成、PickingTask 完成、原子替代来源/无完整替代方案、人工工作位和全部 inbound 命令缺少获批 wire。

任一阻断未关闭时，Phase 3 保持 `BLOCKED_AT_TASK_1`。不得以厂商初稿样例、旧实现字段或历史测试结果代替 WMS
合同批准。

## 9. 退出标准

- Phase 3 只有消费者驱动的 WMS 业务 ACL，无固定 operation 数量目标。
- WMS 转发 RCS 的全部能力和可靠 Transport 生命周期只存在 Phase 4 范围。
- Adapter 无状态，零数据库、零 Migration、零 evidence/breaker owner。
- 每项能力都有唯一消费者、固定获批 wire、封闭 DTO 和业务拒绝码。
- SRS 保持用户需求真源，不含阶段实现参数、operation 编号或“已冻结”自证语句。
- 项目内已过期的 33 项蓝图和旧 WMS 辅助域 ADR 已移至项目外归档。

**VERDICT：Phase 3 尚不可实施；只允许继续完成 Task 1 的业务与 WMS 合同确认。**
