# `resource_*` 数据所有权审计

本表依据重构前生产代码的模型、路由、写入与读取调用链；未以联调库空表作为删除依据。`DELETE` 表示现有模型及入口没有继续存在的依据；`MERGE` 表示先改消费者和事实来源，再删除旧表。实施结果见文末。

| 表 | 表达事实 | 权威来源 | 生产写入者 | 生产读取者与业务用途 | 当前未闭合执行依赖 | 重复模型 | 承接 | 结论 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `resource_rack_types` | 货架类型主数据 | WMS；设备几何以设备合同为准 | 无已接线写入；仅通用 Service | Resource 只读 API 展示 | 否 | WMS 主数据 | 当前 WMS 意图；WES `WorkLinePosition` 仅保留本线配置 | DELETE |
| `resource_rack_slot_templates` | 货架槽位模板及承载资格 | WMS；设备动作由 ECS 裁决 | 无已接线写入；仅通用 Service | Resource 只读 API 展示；模块内 Repository | 否 | WMS 槽位与资格 | WMS 精确目标、当前执行请求及设备合同 | DELETE |
| `resource_racks` | 货架实例主数据 | WMS | 无已接线写入；仅通用 Service | Resource 只读 API 展示；模块内 Repository | 否 | WMS Rack 主账 | 当前 WMS 意图中的 Rack 身份；执行对象按身份引用 | DELETE |
| `resource_bin_types` | 料箱类型主数据 | WMS | 无已接线写入；仅通用 Service | Resource 只读 API 展示 | 否 | WMS 主数据 | WMS 当前业务结果 | DELETE |
| `resource_bin_slot_templates` | 料箱内部槽位与容量模板 | WMS；物理能力以设备合同为准 | 无已接线写入；仅通用 Service | Resource 只读 API 展示 | 否 | WMS Cell/容量主账 | WMS 精确资格及目标，ECS 接纳结果 | DELETE |
| `resource_bins` | 料箱实例主数据 | WMS | 无已接线写入；仅通用 Service | Resource 只读 API 展示；模块内 Repository | 否 | WMS Bin 主账 | 当前业务意图与执行对象中的 Bin 身份 | DELETE |
| `resource_state_events` | 泛资源事件副本 | WMS/RCS/ECS 的原始权威事件 | 未接线的 Resource projection/relation 服务 | Resource 只读 API、模块内投影服务 | 否；可靠入口另有 Evidence owner | `InboundEvidence`、Transport/Command 结果证据 | 各现有可靠 Evidence 与其执行 owner | DELETE |
| `resource_rack_placements` | 货架位置当前值及历史 | RCS/ECS 权威到位与终位事实 | 未接线的 Resource projection/relation 服务 | WorkLine 活动对象展示、基础配置占位判断、模块内快照 | 展示/配置读取存在；旧表无可靠生产写入路径 | `PositionProjection` | 仅经权威 Fact 更新的 `PositionProjection`；配置操作按现场 SOP，未知不宣称 EMPTY | MERGE |
| `resource_rack_bin_mounts` | 货架槽位上的 Bin 挂载 | WMS 业务关系与 RCS/ECS 物理结果 | 未接线的 Resource projection 服务 | Resource 只读 API、模块内快照服务 | 否 | WMS 资源关系、当前执行 Evidence | 当前 WMS 结果、Transport/设备结果及业务执行对象 | DELETE |
| `resource_bin_placements` | Bin 非货架位置当前值及历史 | RCS/ECS 权威到位与终位事实 | 未接线的 Resource projection/relation 服务 | WorkLine 活动对象展示、基础配置占位判断、模块内快照 | 展示/配置读取存在；旧表无可靠生产写入路径 | `PositionProjection` | 仅经权威 Fact 更新的 `PositionProjection`；配置操作按现场 SOP，未知不宣称 EMPTY | MERGE |
| `resource_bin_material_mounts` | 料盘/PKG 在 Bin 格位的明细及历史 | WMS 业务主账，设备动作结果为过程 Fact | 未接线的 Resource projection 服务 | Resource 只读 API、模块内快照服务 | 否 | WMS 库存/物料关系 | WMS 当前业务结果及本次执行 Evidence | DELETE |
| `resource_bin_cell_occupancies` | Bin Cell 聚合占用、容量、剩余量 | WMS | 未接线的 Resource projection 服务 | Resource 只读 API、模块内快照服务 | 否 | WMS Cell/库存主账 | WMS 当前业务资格和精确目标 | DELETE |
| `resource_bin_content_snapshots` | Bin 内容过程快照头 | WMS 内容快照；WES 可保存本次观察 Evidence | 未接线的 Resource snapshot/projection 服务 | Resource 只读 API | 否 | WMS 快照及本次执行 Evidence | WMS 当前结果、本次执行 Evidence | DELETE |
| `resource_bin_content_snapshot_items` | Bin 内容过程快照明细 | 同上 | 未接线的 Resource snapshot/projection 服务 | Resource 只读 API | 否 | WMS 快照及本次执行 Evidence | 同上 | DELETE |

## 删除前必须闭合的消费者

- `WorkLineRepository.list_target_active_object_facts()` 当前把两张 placement 表作为活动对象来源；应改为仅展示已有执行对象与可信当前位置事实，不把“查不到”显示为现场 `EMPTY`。
- `WorkLinePositionRepository.has_active_placements()` 被 `WorkLineConfigurationService.save_base()` 用作配置变更阻断。旧表没有可靠生产写入路径，应移除这项重复查询。当前未闭合负载由其执行对象阻断；位置投影用于诊断，不单独阻断停用或配置。没有投影记录不证明现场 `EMPTY`。停用、插件切换和基础配置修改由现场人员按清线 SOP 操作，界面应提示。
- `RackKind` 仍被 WorkLine/工作位配置使用，它是 WES 本线配置类型，不随 Rack 主数据表删除；应移至配置 owner。
- `PositionProjection` 有人工拣选与 Transport 实际调用链。它只保存当前对象位置及原执行因果身份，不吸收 Rack/Bin/Material/Inventory 主数据。旧 Action 的迟到结果由原身份及因果关系处理；新 revision 不能从已闭合业务历史推断当前业务资格。

历史数据清理的准入条件是关联业务执行、设备 Command、Transport、可靠交付和必要补偿全部闭合；不能仅按父 `PickingTask` 终态判断。当前投影若仍是现场当前事实，不因所属业务结束就直接当作已证实 EMPTY。对外重复回调的安全窗口仍需与所属合同的 ACK/幂等义务一起闭合。

现有 ECS `GET /api/v1/device/status` 只返回设备运行状态、在线状态及当前命令，不返回 Rack/Bin/工作位占用。`IDLE` 或无活动命令不能作为 EMPTY 证据。

## 实施结果

- 两张 placement 的 WorkLine 消费者已改读 `PositionProjection`；当前位置未知时只报告 `UNKNOWN`，不输出伪造的空位事实。
- 配置保存继续检查当前未闭合工作负载；位置投影保留诊断显示，但不单独阻断停用、插件切换或基础配置。现场清线由 SOP 确认，缺少投影记录不作为 EMPTY 证明。
- 14 张表的模型、Repository、Service、API 和对应旧测试已退出；数据库由 `b6b5d9240f51` 删除旧表，运行数据 reset 清单同步收敛。

## 闭合历史无依赖：仍需整改的调用点

本次 14 表收敛和迁移通过，不等于全系统已通过 SRS 的“历史清空测试”。以下是针对位置投影调用链确认的剩余问题，后续整改须以当前 Requirement 和权威现场事实为依据，不能把旧 Transport 结果直接当成新 Revision 的准入事实：

- `manual_picking/application/rack_readiness.py::ready_rack_projection()` 读取 `PositionProjection.source_transport_task_id` 指向的原 Transport，并以其 `SUCCEEDED` 判定货架 ready。它尚未证明该 Transport 属于当前任务/Revision。若原 Transport 已整体闭合并被清理，相同物理 Rack 的新 Revision 可能失去判定依据；若原记录未清理，则可能误用上一 Revision 的结果。
- `manual_picking/application/batch_driver.py::_advance_drain()` 以 `current_rack_count() == 0` 允许活动任务进入 drain。零条当前位置投影只能表示 WES 未记录货架，不能证明现场 `EMPTY`。这里须改用当前步骤可验证的权威事实，或把物理接纳交给 RCS/ECS；在依据明确前不得把零记录当作空位。

这两处属于后续执行链整改范围，不由删除 `resource_*` 表的测试绿灯覆盖。
