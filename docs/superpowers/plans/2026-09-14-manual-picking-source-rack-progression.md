# 人工拣料来源货架换面与换架实施计划

> **执行方式：**获批后由当前 Agent 按内聚切片实施；只有用户明确要求独立分工时才使用 Subagent。实施前按 `AGENTS.md` §3.4 冻结变更面和影响分析。

**Goal:** 让 `manual-picking` 在一张任务含同架多面或多架五层来源时，按已应用计划和权威物理结果连续完成入站、换面、换架与退箱，不因首面结束而停滞。

**Architecture:** 保留现有 `plan_delta` 聚合、每架一次进场 Transport、CTU 批次和 `RETURN_BUFFER` FIFO。插件只按当前对象的精确 Transport 权威事实推进；计划顺序只用于确定性创建意图，不成为已到位货架的运行门禁，PositionProjection 只表达最近确认位置或未知。RCS/ECS 负责货架占位、进出场互斥和物理恢复，WES 不关联前后货架的 Transport 终态，也不建立第二套调度或恢复状态机。

**Tech Stack:** Python 3.13、SQLAlchemy、PostgreSQL、Celery、pytest、现有 WMS typed operation 与 Transport 合同。

**Spec:** `docs/contracts/wms-outbound-picking-task-integration-requirements.md` §8.3、§9.2、§9.4、§13；`docs/contracts/wms-manual-outbound-picking-integration-requirements.md` §1.1、§2.1、§3.4；`docs/integration/manual-outbound-rack-transport.md`。

## 当前基线与合同确认

- `PickingTaskPlanAppliedHandler` 已为目标转运架和每个物理五层货架生成一个进场意图；多个五层架可同时向同一 `FIVE_RACK` 工作位提交，由 RCS 排队。保留此行为，不增加 WES 容量租约。
- 当前工作树已补 `RACK_ROTATE`、旧架离场和下一架进位的名义分支，但 `ManualPickingBatchDriver` 仍通过工作线级 PositionProjection 发现当前架，任意其他货架 `position_unknown` 都可能阻塞已取得原 Transport `SUCCEEDED` 的来源架；扫码应用和任务完成判断也仍存在历史未发布 Evidence 的设备级全局门禁。下一步先移除这两类非因果限制，再收口跨架流程。
- **已确定按现场规则执行：**五层来源货架离场使用 `CTU03`，Transport 从 `RACK`（货架号）到 WMS 决定的 `ZONE`；请求中的 `current_location/current_face` 仍取权威实际位置。主合同 §9.4 和当前 `departure_decide.READY.rack_destination` typed wire 尚限定 `RACK_POSITION`；实施时响应 wire/SDK typed outcome 表达 `ZONE | RACK_POSITION`，在消费结果时按原任务计划中的货架角色校验：五层来源架仅接受 `ZONE`，转运架仍按自身合同接受 `RACK_POSITION`。不向请求增加 `rack_role`；角色不符时保留原证据并拒绝派发。同步 Adapter、OpenAPI、合同并由 WMS 验证收发。不能把 `ZONE` 偷换成位置码，也不保留旧五层架响应的兼容双路径。WMS 合同与实际接收未验证前，不开放跨架自动实线离场。
- 本切片的稳定 Transport binding 步骤分别为 `MANUAL_PICKING_SOURCE_RACK_ROTATE`、`MANUAL_PICKING_SOURCE_RACK_OUT`；旋转以当前任务和目标来源面成员身份冻结一次，离场以 `departure_decide` 的原 `operation_id` 冻结一次。重复 worker 唤醒、同一结果重放只读取原 binding，不生成替代 `client_request_id`。
- `completion_confirm` 只按主合同 §13 的本地业务义务和必要移动事实发起；任务完成不要求退箱或货架离场全部结束。已完成任务的原退箱、离场义务继续按稳定 binding 和原身份独立闭合，不依赖全局 PositionProjection 重新发现，也不成为后来货架或任务的门禁。
- 后一 PickingTask 可先行 `prepare`。R1 的 CTU03 与 R2 的进场 Transport 没有完成依赖：R1 离场 `ACCEPTED` 后立即使 R1 的 KT16 已确认投影失效；R1 的最终位置结果只补充诊断。只要 R2 属于已应用计划且其原进场 Transport 精确匹配并 `SUCCEEDED`，就可以立即推进 R2 当前面，即使 R1 离场仍为 `ACCEPTED`、结果未知或最终位置从未回调。
- `return_batch` 是工作线范围的决定，不按当前 `EXECUTING` 任务或任一候选 Bin 的任务归属整个响应。读取原 `WmsConfirmation` 不可变请求，按顺序对齐 `RETURN_BUFFER` 跨任务 FIFO 队首候选；各 Bin 保留各自任务身份。承接货架只按该货架最近一次匹配的进场/换面 `SUCCEEDED` 与已应用计划校验，可与候选 Bin 的原货架/面不同；R1 的离场结果不参与 R2 的退箱准入。
- 所有搬运在 `PENDING/REJECTED` 时保留原已知投影；`ACCEPTED` 或 `DELIVERY_UNKNOWN` 后将被移动对象标为 `position_unknown=true`，但不推定已离开来源或到达目标；`SUCCEEDED/FAILED` 只有携带最终位置时才更新最终投影，否则保持未知。投影未知只影响依赖该对象位置的动作，不阻塞其他对象。
- `departure_decide WAIT` 按主合同 §14.2 在 `retry_after_ms` 到期或新事实到达后重新判断，使用新的 `operation_id` 和当时可证明的货架位置；`UNAVAILABLE` 或响应未知仅以原身份和原请求重试。已创建的离场 Transport 无论成功、失败或结果未知，都不得换身份重新派发。

## 变更面与文件职责

| 范围 | 文件 | 职责 |
| --- | --- | --- |
| 计划与运行准入 | `src/app/wms_integration/outbound_picking/repositories/plan_delta_repository.py`、`services/picking_task_plan_activation.py`；`workline_plugins/manual-picking/src/manual_picking/handlers/picking_task_plan_applied.py` | 保留 `plan_revision`、计划成员和每架一次的稳定进场 identity；不新增运行顺序字段。运行时只验证实际到位架属于已应用计划，不要求它等待前一架或前一任务终态。 |
| 投影生命周期 | `src/app/transport/service.py`、`src/app/execution/services/position_projection_service.py`、`repositories/position_projection_repository.py` | Transport `ACCEPTED/DELIVERY_UNKNOWN` 使被移动对象的原已知位置失效；最终位置有回调则记录，无回调保持未知。删除“工作线任意货架未知即无当前架”的查询语义，PositionProjection 不作为独立对象的资源授权。 |
| 插件调度 | `workline_plugins/manual-picking/src/manual_picking/application/batch_driver.py`、`batch_flow.py`、`batch_repository.py`、`rack_readiness.py` | 在工作线锁下按精确计划成员、稳定 binding 和该对象原 Transport 终态推进；R2 进场 `SUCCEEDED` 足以启动 R2，R1 CTU03 状态只由 R1 自身消费。`RETURN_BUFFER` FIFO 和同一动作幂等保留。 |
| 旋转执行 | `src/app/transport/service.py`、`src/app/execution/services/reliable_rack_transport.py`；`workline_plugins/manual-picking/src/manual_picking/application/transport_outcome.py`；`deployment/plugin_composition.py` | 为现有 `RACK_ROTATE` 补事务内创建入口，复用稳定 binding/client identity、回调和投影；插件以当前实际 `RACK_POSITION` 和下一计划面创建 `CTU02`，等待匹配权威成功后才请求该面的 `inbound_batch`。 |
| 跨架离场 | `src/app/wms_adapter/outbound_picking/departure_wire.py`、`departure_typed.py`、`openapi.py`；`src/wes_plugin_sdk/src/wes_plugin_sdk/wms_types.py`；`src/app/wms_integration/outbound_picking/services/picking_task_plan_activation.py` 和 operation 专属调度/结果读取；`workline_plugins/manual-picking/src/manual_picking/application/plugin.py`、`scan_flow.py`、`batch_driver.py`、`transport_outcome.py`；`deployment/plugin_composition.py` | 按现场五层架 `RACK→ZONE/CTU03` 统一合同，用 typed `departure_decide` 与 WmsConfirmation 冻结 WMS 给出的 `ZONE`；`WAIT` 留原架待重求值，`READY` 派生一项稳定离场 Transport。R1 离场义务与 R2 进场义务独立记录和消费；R2 原进场 `SUCCEEDED` 后立即推进，不等待 R1 最终结果。 |
| 扫码因果门禁 | `workline_plugins/manual-picking/src/manual_picking/application/scan_flow.py`、`passage_repository.py`、`completion_repository.py` | SCAN1 以当前 Bin 的原 `BIN_MOVE SUCCEEDED`、INLET 位置和精确计划成员为准，不要求来源架随后仍在原面。SCAN1～SCAN4 均只受当前 Passage、明确物理 FIFO 队头或已冻结未闭合命令阻塞；无 Passage/命令归属的历史 Evidence 只保留诊断，不阻塞新事件、批次推进或任务完成。 |
| 合同与测试 | 上述三个合同文档；`workline_plugins/manual-picking/tests/test_plan_applied_handler.py`、`test_batch_flow.py`、`test_scan_flow.py`、`test_transport_outcome.py` 及新增的插件 PostgreSQL 场景；`tests/contracts/wms_adapter/outbound_picking/test_departure.py`、`test_plan_activation_service.py` 和宿主 Transport 对应测试；`docs/architecture/heavy-test-impact.toml` | 修正示例中省略的货架到位、`BIN_MOVE`、换面/换架、退箱物理闭合和任务完成条件；各测试只证明所属层的规则。 |

## Task 1：解耦计划顺序与实际到位准入

- [x] RED：用同 revision 的 R1/R2、R1 多面和后续 revision 新增面构造用例；证明每架只有一个稳定进场 intent，但 R2 的原进场 Transport 一旦 `SUCCEEDED` 即可选择 R2，不等待 R1 离场结果、R1 投影清理、计划数组前序或前一任务终态。R2 只有 `PENDING/ACCEPTED` 时不得提前请求批次。
- [x] DEV：计划持久化只保存成员身份，不增加运行排序字段；调整 `ManualPickingBatchDriver`，按实际触发对象的计划成员和原 Transport 结果选择来源架，删除 `previous_complete`、计划数组前序、货架编号顺序及工作线全局 unknown 对已到位架的运行限制。
- [x] GREEN：运行 `uv run pytest workline_plugins/manual-picking/tests/test_plan_applied_handler.py workline_plugins/manual-picking/tests/test_batch_flow.py -q` 和受影响的计划增量持久化聚焦测试；核对旧进场 identity 不变。

## Task 2：完成同架换面

- [x] RED：覆盖当前面完整清单已搬完且 SCAN1 实扫匹配、`RACK_FACE_DONE` 空面、CTU 携箱、未闭合退箱决定、未知位置、原旋转结果未知和重复唤醒。只有前两类闭合且安全门槛成立时，才允许唯一 `CTU02/RACK_ROTATE`；`RETURN_BUFFER` 中尚未冻结退箱目标的 Bin 不单独阻塞换面。
- [x] DEV：在宿主 Transport 增加与 `move_rack_in_session` 同事务语义的旋转入口；复用 `ReliableRackTransportCreator` 的稳定 binding。插件从当前架最近一次匹配的进场/旋转 `SUCCEEDED` 读取位置和面，使用计划原面字符串并保留原命令及结果身份；CTU02 的原 Transport 成功后即可运行该面的 `inbound_batch` 分支。
- [x] GREEN：运行插件批次/Transport 结果聚焦测试、宿主 Transport 事务与幂等测试；核对失败或 `UNKNOWN/RECONCILING` 时不重发替代旋转、不提前分配下一面。

## Task 3：独立处理货架离场与进位

- [x] 将主合同 §9.4 的五层来源货架 `READY.rack_destination` 改为 WMS 返回的 `ZONE`，保持 `current_location/current_face` 为实际位置；响应 wire/SDK typed outcome 接受 `ZONE | RACK_POSITION`，消费时根据原任务计划验证货架角色与去向类型，角色不符保留证据且不创建 Transport。同步 Adapter、OpenAPI 与合同测试；五层架收到 `RACK_POSITION` 必须拒绝，转运架仍按自身合同处理，不新增请求 `rack_role` 或扩大本切片的离场规则。
- [x] RED：覆盖 R1 `CTU03 ACCEPTED/DELIVERY_UNKNOWN` 后投影立即失效、无最终位置回调时长期未知、迟到结果仍闭合原 R1；同时证明 R2 `ACCEPTED` 不推进、R2 原进场 `SUCCEEDED` 立即推进，且不读取 R1 CTU03 终态。重复 ACK、结果重放和并发唤醒不能产生替代离场或重复 R2 批次。
- [x] DEV：增加 operation 专属 scheduler/typed 结果读取并接入插件业务消费；复用 WmsConfirmation 与 `ReliableRackTransportCreator` 创建唯一 CTU03。Transport 接纳分支使被移动对象的确认投影失效；BatchDriver 以 R2 自身进场成功为准，R1 离场只按原 binding 异步闭合。WMS 决定去向，RCS/ECS 决定物理占位和移动顺序。
- [x] GREEN：运行插件跨架场景、WMS departure Adapter 合同、Transport 结果/投影测试及真实 PostgreSQL 并发用例；改变 worker 注册或路由时补真实 worker 验证，不把 skipped 当通过。

## Task 4：移除所有扫码点的无归属历史 Evidence 门禁

- [x] RED：分别为 SCAN1～SCAN4 插入更早的未发布、无 Passage、无 DeviceCommand 归属 Evidence，证明当前合法扫码仍按自身 identity 推进；同类历史 Evidence 也不得阻塞批次换面/换架或 `completion_confirm`。同时证明属于当前 Passage、明确 FIFO 队首或已接纳未闭合命令的 Evidence 仍保持原物理顺序。另覆盖当前 Bin 原 `BIN_MOVE SUCCEEDED` 且已到 INLET、来源架随后投影未知或已离场时 SCAN1 正常推进。
- [x] DEV：`scan_flow._scan1_physical_readiness` 只核对当前 Bin、原 Transport、INLET、目标转运架必要条件和精确计划成员；删除来源架继续在位检查。删除 `has_prior_unpublished_scan` 及 `completion_repository.ready_to_confirm` 中按设备和 `published_at` 扫描历史 Evidence 的全局门禁；SCAN1～SCAN4、批次推进和任务完成统一只读取精确 Passage、DeviceCommand、Transport 与 WMS 义务，不新增人工 ignore/reprocess 路径。
- [x] GREEN：运行 `test_scan_flow.py`、任务完成测试及对应 PostgreSQL Evidence/DeviceCommand 场景，证明四个扫码点的无归属历史事实均保留但不参与运行准入，当前事件只推进一次，已归属且未闭合的物理动作仍等待 ECS 原结果。

## Task 5：合同示例与最终验收

- [x] 将人工合同 §1.1 的子流程 A 明确为两条独立链：R1 `departure_decide→CTU03` 只闭合 R1，R2 原进场 `SUCCEEDED` 独立触发 R2 当前面；删除“等待旧架离位结果后才能处理新架”的表述。补充 `ACCEPTED` 使原位置失效、最终位置回调可选且不作为后续门禁。文档不新增正文 pytest。
- [x] 按最终变更面闭合插件、宿主、WMS wire、fixture/间接消费者和 HEAVY mapping；聚焦通过后只运行当前快照必选的 QUALITY、selector 选中 HEAVY 和必要的 PostgreSQL/worker 验证各一次。执行旧符号/旧字面量残留扫描、`git diff --check` 和一次完整 Review。
- [x] 报告区分代码/测试通过、WMS/ECS 合同与接收证据、真实货架离位/进位、现场完整业务验收。未经单独授权不 Commit、Push、PR、Merge 或部署。

## 工程评审核对

### 关键执行流

```text
plan_delta -> 工作线锁 -> 工作线退箱 FIFO（可跨任务）
                       -> 各架稳定进场 Transport（由 RCS 排队）
                       -> 当前对象原 Transport SUCCEEDED
                       -> 当前面 inbound_batch -> 入站 BIN_MOVE -> 实扫与面级完成
                       -> 同架下一面：CTU02 旋转 -> 权威新面 -> 回到 inbound_batch
                       -> R1：departure_decide -> CTU03 独立闭合
                       -> R2：原进场 SUCCEEDED -> 立即进入当前面，与 R1 CTU03 终态无关
WMS WAIT：到期后新决定身份；Transport ACCEPTED/UNKNOWN：原位置失效，原身份继续等待结果但不阻塞其他对象。
```

已存在并复用：`PickingTaskPlanActivationService` 的工作线锁与进场意图、`ReliableRackTransportCreator` 的稳定 binding、`BatchRepository` 的面级进度、`PassageRepository` 的 `RETURN_BUFFER` FIFO、`PositionProjection` 与 Transport 权威终态、`WmsConfirmation` 的可靠收发。新增逻辑只补换面、离场及新旧任务并存时的业务选择。

已完成任务的未闭合离场按稳定 binding 和精确计划成员继续处理；不存在 binding 时只从该任务自身已完成来源面派生一次离场决定，不通过工作线全局 PositionProjection 扫描历史任务。后来货架的进场成功始终按自身 identity 推进。

| 故障入口 | 必要行为 | 验证归属 |
| --- | --- | --- |
| 旋转或离场 Transport 结果未知 | 保留原 binding、围栏与位置未知状态，等待匹配权威结果 | 插件结果与宿主 Transport 测试 |
| R1 离场未终结但 R2 已进场成功 | R2 立即继续；R1 独立保留原义务和未知投影 | 跨架 PostgreSQL/Transport 场景 |
| 其他货架或 SCAN1～SCAN4 无归属历史 Evidence 未确认 | 仅保留诊断；不形成事件、批次、任务完成或工作线全局门禁 | PositionProjection、扫码与任务完成测试 |
| 五层架收到 `RACK_POSITION` 或来源归属不唯一 | 保留原 Evidence，拒绝派发并进入对账 | departure 合同与插件场景测试 |
| 新任务执行时跨任务退箱响应到达 | 按原请求和 FIFO 前缀逐项核对候选，独立校验当前承接货架；不匹配则不应用 | `test_scan_flow.py` 与 PostgreSQL 场景 |
| WMS `WAIT` 或 `UNAVAILABLE` | 分别以新决定身份重新求值、以原请求身份技术重试 | 插件调度与 WMS 合同测试 |

不在本次范围：转运架回库和 `CK04` 换面仍按其独立现场合同推进；共同 drain operation 尚未获批，不借换架实现；不增加业务清理 worker、待离场状态表或第二套物理恢复；真实 WMS/ECS 接收和货架物理闭环属于后续联调验收，不以本地测试代替。

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
| --- | --- | --- | ---: | --- | --- |
| CEO Review | `/plan-ceo-review` | 范围与产品策略 | 10 | 本计划未运行；历史记录另有未决项 | 最近一次为其他文档，2026-09-12 |
| Codex Review | `/codex review` | 独立意见 | 17 | 本计划未运行 | 历史记录已过期 |
| Eng Review | `/plan-eng-review` | 架构、代码、测试与性能 | 108 | 原计划 CLEAR；本次无阻塞增量尚未刷新 | 原评审未覆盖 ACCEPTED 投影失效、R1/R2 解耦和四个扫码点无归属历史 Evidence 门禁移除 |
| Design Review | `/plan-design-review` | UI/UX | 2 | 本计划无需运行 | 仅后端流程与合同 |
| DX Review | `/plan-devex-review` | 开发体验 | 3 | 本计划无需运行 | 没有开发工具或公共 API 设计变更 |

**VERDICT:** 原换面/换架计划的工程评审已完成；本次增量已在工作树实施并通过聚焦、QUALITY 和隔离 HEAVY 验证。WMS/ECS 实际接收、部署和现场物理闭环尚未验收。

**UNRESOLVED DECISIONS:**
- + 2 unresolved from prior CEO review of another document; this plan has 0 unresolved decisions.
