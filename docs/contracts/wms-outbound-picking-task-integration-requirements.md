---
title: WMS 自动出库 PickingTask 对接要求
status: Draft
created_at: 2026-08-07
updated_at: 2026-08-07
audience: WMS 系统开发人员、WES 出库业务开发人员
scope: PickingTask、Cell 启动锁、逐盘扫码决定、执行事实确认、运输转发边界
related:
  - docs/superpowers/specs/2026-08-06-wes-outbound-operation-top-level-design.md
  - docs/contracts/wms-northbound-interaction-contract.md
  - docs/architecture/authority-matrix.md
  - docs/architecture/adr/2026-05-13-wes-wms-rcs-resource-boundary.md
---

# WMS 自动出库 PickingTask 对接要求

## 1. 对接目标

WMS 负责业务决策，WES 负责现场执行。WMS 不向 WES 下发出库单、波次单或逐盘 `PkgID`，而是下发一张包含多个
来源 Cell 的 `PickingTask`。设备实际取盘并扫码后，WMS 再确定该盘身份、是否可用、目标储位以及当前 Cell 是否继续取料。

本文是 WMS 开发团队的能力与语义确认清单，不是已批准的 wire 合同。具体 operation、method、path、DTO 编码、错误码、
超时和同步/异步方式必须由双方另行书面冻结后才能实施。

## 2. 职责边界

| 系统 | 必须负责 | 不应负责 |
| --- | --- | --- |
| WMS | 任务版本、来源 Cell 原子锁、库存与物料资格、扫码到 `PkgID` 的唯一识别、目标储位、目标面切换授权、`CONTINUE / CELL_DONE`、空取业务处置、业务取消与恢复 | 机械臂动作、滚筒线缓存、现场位置投影或设备互锁 |
| WES | 执行冻结的 Cell 集合、持久化扫码与设备证据、资源仲裁、位置投影、可靠回写 | 自行选择物料、目标储位、替代来源或推断 Cell 完成 |
| RCS/AGV/CTU | 货架和料箱搬运、路径、排队及运输终态 | PickingTask、库存、物料资格或 Cell 业务完成 |

AGV/CTU/RCS 能力属于独立 Transport 合同。当前网络入口可由 WMS 转发，但不得把运输状态混入 PickingTask 状态机或
Phase 3 `WmsClient`。

## 3. 业务交互流程

1. WMS 下发包含多个 `pick_cells[]` 的 `PickingTask`，任务内不包含 `PkgID`、SixInOne、目标货架或目标储位。
2. 任务准备启动时，WMS 原子确认任务版本、完整 Cell 集合和 `cell_lock_generation`，并给出目标容量、目标面顺序和所需搬运目标。
3. WES 分别发起目标空架、可选退料架和五层货架搬运；五层货架可靠到位后，CTU 批次才可投入对应料箱。
4. 机械臂从指定 Cell 取盘；若设备给出可靠“无料”终态，WES 提交空取证据并等待 WMS 决定，不自行完成 Cell。
5. 成功取盘后放到扫码台；设备扫码字段必须足以让 WMS 唯一确定该盘 `PkgID`。
6. WES 提交扫码证据，WMS 返回判别结果；仅接受结果返回该盘身份、目标储位、目标面代际以及 `CONTINUE` 或 `CELL_DONE`。
7. WES 按决定完成物理动作，并逐盘向 WMS 提交位置变化事实。
8. WES 仅在全部 `CellExecution` 完成后将 `PickingTask` 标记为完成；任务完成通知不得越过尚未被 WMS 接受的逐盘事实。
9. Rack、Bin、AGV、CTU 和工作线清场继续独立闭环，不参与 PickingTask 完成判断。

## 4. WMS 必须提供的业务能力

| 方向 | 能力 | 最低语义要求 |
| --- | --- | --- |
| WMS → WES | 创建 PickingTask | 一次下发完整且封闭的来源 Cell 集合 |
| WES → WMS | 取得启动授权 | 原子校验任务版本、Cell 集合和锁代际 |
| WMS → WES | 任务控制 | 以新 `task_version` 或独立带版本控制消息更新未开始任务优先级；明确暂停、恢复和取消决定 |
| WES → WMS | 请求逐盘决定 | 使用任务、Cell、锁代际和扫码证据关联一次实际取盘 |
| WMS → WES | 返回逐盘决定 | 返回接受/拒绝/等待判别结果；仅 `ACCEPT` 给出权威物料身份、目标储位和 Cell 后续动作 |
| WES → WMS | 提交空取证据 | 使用任务、Cell、锁代际和设备无料终态关联一次未产生扫码的取料尝试 |
| WMS → WES | 返回空取决定 | 明确 `CELL_DONE | RETRY | WAIT`，WES 不根据物理无料自行完成 Cell |
| WES → WMS | 确认逐盘事实 | 独立确认每盘已发生的位置变化或异常落点 |
| WES → WMS | 确认任务事实 | 报告任务完成或取消，不携带 Rack/Bin/Transport 聚合状态 |
| 双向 | 异常处置 | 对身份冲突、容量不足、结果未知和人工消歧给出可关联的封闭结果 |

## 5. 最低字段与语义

### 5.1 PickingTask

| 字段 | 要求 |
| --- | --- |
| `task_id` / `task_version` | 稳定任务身份与版本；同一版本内容不可变化 |
| `priority` / `issued_at` | WMS 提供的初始排队依据；优先级变更不得静默改写同版本任务载荷 |
| `workline_code` | 指定执行工作线 |
| `cell_lock_generation` | 本次任务启动授权对应的 Cell 锁代际 |
| `pick_cells[]` | 本任务必须执行完成的来源执行单元闭集 |
| `cell_execution_id` | 任务内稳定且唯一 |
| `source_locator` | 只允许 `BIN_CELL` 或 `RACK_SLOT` 两种闭集类型 |

`BIN_CELL` 至少包含 `rack_id + rack_face + bin_id + cell_id`；`RACK_SLOT` 至少包含
`rack_id + rack_face + slot_id`。`RACK_SLOT` 作为单盘 `CellExecution` 参与任务聚合，不把退料货架生命周期纳入任务。

PickingTask 中禁止包含：`PkgID`、SixInOne、预估盘数、顶部顺序、目标货架/储位、AGV/CTU 任务或缓存状态。

### 5.2 启动授权

WMS 返回的启动授权至少应包含：

- `task_id`、`task_version`、`cell_lock_generation`。
- 与任务完全一致的已锁 Cell 集合或可验证的集合摘要。
- 目标转运货架容量、目标面使用顺序及其业务授权。
- 初始开放目标面和 `face_window_generation`。
- 五层货架、可选退料货架和目标空架的搬运目标。
- 决定版本、生成时间和有效性信息。

启动后的 Cell 集合不可静默增删或替换。需要变更时，WMS 应取消当前任务并创建新版本或新任务。未开始任务的优先级若需更新，必须
生成新 `task_version`，或使用独立的 `task_control_id + control_version` 控制消息；两种方式只能在正式合同中冻结一种。

### 5.3 逐盘决定请求

请求至少应包含：

- 唯一 `decision_request_id`。
- `task_id`、`task_version`、`cell_execution_id`、`cell_lock_generation`。
- 唯一 `scan_evidence_id`、原始扫码字段和扫码时间。
- 来源 locator、当前工作线/位置上下文和当前目标面窗口。

### 5.4 逐盘决定响应

所有响应至少应包含：

- 与请求唯一关联的 `decision_request_id` 和稳定 `decision_id`。
- `ACCEPT | REJECT | WAIT` 之一；不得用空值或 HTTP 状态代替业务决定。
- 与结果匹配的封闭原因类别和后续处置要求。

`ACCEPT` 结果还必须包含：

- WMS 唯一识别出的 `PkgID`、完整 SixInOne 和权威版本。
- 当前目标面内唯一且可用的目标货架、目标面、目标储位和 `face_window_generation`。
- `CONTINUE | CELL_DONE` 之一，表示当前盘物理闭合后是否允许从同一 Cell 创建下一次取盘。

`REJECT` 可在已唯一识别物料时携带身份证据，但不得携带正常目标储位或 Cell 后续动作。`WAIT` 或身份冲突不得伪造 `PkgID`、
SixInOne、目标储位或 `CONTINUE | CELL_DONE`；WES 保持当前扫码台与资源占用，等待新的可关联决定。

WMS 只能以递增的 `face_window_generation` 明确授权目标面从 A 切换到 B。WES 校验代际、面顺序和旧面未决物理动作；
校验通过后只决定旋转的安全执行时机，不自行决定是否换面。切到 B 后的任何 A 面目标或迟到代际都必须拒绝。

`CELL_DONE` 只表示“不再从该 Cell 取下一盘”，不表示当前已扫码料盘已经完成物理放置。

### 5.5 空取决定

当设备在指定 Cell 给出可靠“无料”终态且没有扫码证据时，WES 必须先持久化 `source_observation_id`、任务/Cell/锁代际、设备命令终态和发生时间，
再请求 WMS 返回 `CELL_DONE | RETRY | WAIT`。`CELL_DONE` 是唯一可以结束该 Cell 的空取业务决定；`RETRY` 只允许 WES 重新执行同一来源；
`WAIT` 保持资源与 Cell 未决。设备结果未知或无法关联时不得调用该业务决定来猜测空 Cell。

### 5.6 逐盘与任务事实

逐盘位置变化事实至少包含唯一 `fact_id`、任务/Cell、`material_execution_id`、`scan_evidence_id`、来源、目标或异常落点、设备终态证据、
发生时间和事实版本。只有 WMS 已唯一识别物料时才携带 `PkgID` 和物料版本；未识别的异常落点依靠执行与扫码证据关联，不伪造物料身份。

任务完成事实至少包含唯一 `completion_fact_id`、`task_id`、`task_version` 和完成时间。其业务含义仅为：

```text
ALL(CellExecution.status == COMPLETED)
```

不得要求 WES 在任务完成事实中聚合货架回库、料箱退线、AGV/CTU 清场或工作位释放状态。

## 6. 必须冻结的交互规则

- **字段闭集**：正式 DTO 不接受开放字段、兼容别名或未批准扩展。
- **幂等**：同一请求/事实 ID 与相同载荷重复提交必须返回同一业务结果；同一 ID 不同载荷必须明确冲突。
- **代际隔离**：迟到响应必须按任务版本、锁代际和请求 ID 拒绝，不能作用于新任务或新锁。
- **失败关闭**：WMS 超时、交付结果未知、响应非法或无法关联时，WES 保持当前资源占用，不推断 `REJECT`、`CONTINUE` 或 `CELL_DONE`。
- **身份唯一**：设备扫码字段必须让 WMS 唯一确定一盘 `PkgID`；不能唯一确定时返回明确冲突/人工处置结果。
- **目标唯一**：同一目标储位不能重复分配；目标面从 A 切换到 B 后不得再返回 A 面储位。
- **换面授权**：目标面切换必须来自 WMS 递增代际授权；WES 只根据物理安全与资源互锁决定何时执行已授权的旋转。
- **容量不足**：WMS 必须返回明确 `WAIT` 或业务处置，不能要求 WES 自行换架、换面或提前结束 Cell。
- **因果顺序**：任务完成确认必须依赖全部逐盘事实先被 WMS 接受，但远端接受状态不得反向阻塞本地 Cell 状态机。
- **业务与传输分离**：HTTP 200 不等于业务成功；`WmsClient` 只保留传输和 JSON 事实，业务模块解释 WMS 结果。

## 7. WMS 转发运输的最低要求

如 WMS 作为 RCS 网络转发入口，双方还需单独批准 Transport 合同：

- AGV 货架搬运必须返回可验证的终态和到位位置；ACK、已受理或已派发不表示到位。
- CTU 投箱/退箱按冻结成员批次提交，至少具有批次身份、成员闭集、ACK/状态/终态和成员最终事实。
- AGV 五层货架到位是对应 CTU 批次可执行的前置条件。
- WES 不要求 CTU 上报设备内部子步骤；滚筒线三段缓存容量和安全互锁由 WES/ECS 现场合同负责。
- 运输失败只影响相应 `TransportTask`、Bin 或现场准入，不修改已完成的 PickingTask。

## 8. WMS 团队交付物与实施门禁

WMS 开发前应与 WES 共同冻结并交付：

1. 每项业务能力的 operation、method、relative path 和同步/异步模式。
2. 请求/响应 DTO、字段闭集、枚举、版本与关联规则。
3. 业务成功、拒绝、等待、冲突、取消、恢复和终态语义。
4. 幂等键、同键异载荷冲突、乱序/重复/迟到消息处理规则。
5. 超时、交付未知、查询或回调补偿方式，以及 SLA。
6. HTTP status、业务错误类别和可重试性的明确映射。
7. 认证方式；当前隔离网络若无需认证，应明确为 `NONE`，不得预留未使用机制。
8. 双方共享的正式 schema 与合同 fixture，以及成功和失败示例。

上述内容未批准前，只能评审本方案，不得据此创建占位 API、宽泛 DTO、兼容层或业务状态机。

## 9. 联调验收清单

| 场景 | 预期结果 |
| --- | --- |
| 一张任务包含多个 `BIN_CELL` / 可选 `RACK_SLOT` | 完整 Cell 集合一次接纳，不含逐盘身份和运输状态 |
| Cell 缺项、重复或 locator 非法 | 整单拒绝，不部分接纳 |
| 启动锁代际不匹配 | 不授权启动，不创建新的物理动作 |
| 扫码可唯一识别一盘 | 返回稳定 `PkgID`、SixInOne、业务决定、目标储位和 Cell 后续动作 |
| 扫码无法唯一识别 | 返回 `WAIT` 或明确冲突及人工处置，不携带伪造身份、目标储位或 Cell 后续动作 |
| 同一决定请求重复提交 | 返回相同 `decision_id` 和业务结果 |
| 同一请求 ID 载荷变化 | 明确冲突，不覆盖原决定 |
| WMS 超时或响应无法关联 | WES 保持扫码台占用，不推断 NG、继续或完成 |
| 返回 `CONTINUE` | 当前盘物理闭合后，WES 才允许同 Cell 下一次取盘 |
| 返回 `CELL_DONE` | 当前最后一盘物理闭合后，该 Cell 才完成 |
| 设备可靠返回空取 | WES 提交稳定无料证据；仅 WMS 返回 `CELL_DONE` 才结束 Cell，`RETRY/WAIT` 保持未完成 |
| WMS 授权目标面 A 切换 B | `face_window_generation` 递增且旧面无未决动作后执行旋转；切换后拒绝 A 面目标 |
| 全部 Cell 完成 | PickingTask 完成，不读取 Rack/Bin/Transport 状态 |
| 逐盘事实尚未被 WMS 接受 | 任务完成通知等待，不越过逐盘确认 |
| PickingTask 已完成但仍在退箱 | PickingTask 不回退；现场资源独立闭环 |

## 10. 明确不在本合同内

- WES 复制或管理出库单、波次单、库存主账和来源分配。
- Phase 3 `WmsClient` 自动重试、业务结果解释、数据库、Outbox 或状态机。
- WES 直连 RCS/AGV/CTU SDK，或由 WES 规划车辆路径。
- 未获批准的 method、path、字段、错误码、认证、兼容模式或历史接口复用。
