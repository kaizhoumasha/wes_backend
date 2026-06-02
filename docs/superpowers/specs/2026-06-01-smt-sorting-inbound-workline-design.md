# SMT 分拣入库复合 WorkLine 设计

## 背景

SMT 分拣入库承接 `SMT 粗分机` 释放的单层货架。单层货架经过满箱交换判断后，仍不满足交换要求的料箱/料格需要进入分拣机，将零散料盘归集到五层货架中的目标料箱内。

该业务不是单一设备动作，而是一个复合 WorkLine 编排：

- 上游：粗分机、满箱交换、分拣机排队位。
- 现场资源：单层货架、五层货架、分拣机 Station、CTU、流水线投料口、扫码位、料箱工作位、退箱区、扫码平台、源端机械臂、目标端机械臂、NG 位。
- 外部系统：WMS 维护库存与货架元数据根源，WMS/RCS 负责 AGV/CTU 等外部搬运动作授权和调度。

现有 SRS 已定义 SMT 粗分机分箱/分格逻辑、混合入库策略、WMS/RCS 资源边界和 WorkLine 编排原则。本 SPEC 在这些约束上补齐“SMT 分拣入库”复合流程，明确首版业务边界、资源状态、数据同步、异常恢复和验收标准。

## 目标

1. 定义 SMT 分拣入库从单层货架排队、五层货架准备、CTU 投/退箱、流水线准入、机械臂分拣、目标箱回位、WMS 同步到 Session 完成的完整闭环。
2. 保持与粗分机一致的分格原则：目标箱允许多物料共箱，同一料格只接受同物料同 DC/LC。
3. 明确 WMS 与 WES 的数据边界：WMS 是货架、库存和物料数据根源；WES 在 WMS 授权作业窗口内维护运行时投影并执行局部分配。
4. 明确目标箱粒度的数据同步：目标箱回到五层货架后，WES 一次性向 WMS 同步该箱位置、格位物料和执行证据。
5. 明确异常恢复规则：WMS 同步失败允许自动重试；超过阈值暂停分拣；可自动恢复的场景尽量自动恢复，但必须先证明 WMS、WES 投影和现场状态一致。

## 首版实现切片

首版先交付 P0“本地状态闭环”，不把所有 CTU/WMS/NG 对账链路一次性做完。P0 的判定标准是：同一 WorkLine 内一盘料从源格取出后，必须在 WES 本地有唯一、可恢复、可审计的最终去向；外部 WMS 对账可以滞后，但不能影响本地闭环事实的可信度。

P0 必须落地：

- 粗分机向单层货架源料格入料时，按序记录料盘序列并做源格容量判断。
- 分拣机源端机械臂取盘成功时，通过资源事实立即完成源格出账，并在当前 Session 中创建在制物料事实。
- 扫码后用共享分格策略计算目标格，目标格信息先写入当前 Session 的在途落点，再作为目标端机械臂命令参数下发。
- 目标端机械臂成功后写目标箱投影；本地 NG 成功后写 `NgReturnItem` 和 NG evidence。
- Session 完成前检查所有在制物料均已本地闭环，没有悬挂的 `current_material` 或 `pending_target_placement`。

P0 不交付但必须保留证据和扩展点：

- 完整 CTU/WMS 全链路自动对账。
- 源端 NG 实时 WMS outbox 确认。
- NG 独立 WMS 对账 endpoint 的生产级闭环。
- 多扫码平台、多目标机械臂或跨 WorkLine 共享目标箱下的厚度感知 reservation。

P0 实施门禁：

- SPEC 中所有 P0 约束必须能映射到测试用例或沙箱场景。
- 任何会修改源格、目标格、在制物料或 NG 事实的 handler，不得直接深层修改 `context_json`；必须通过类型化业务 context 读写。
- 源/目标格分配不得出现源格候选循环中的逐格数据库查询；active snapshot 应一次读取并在内存索引中计算。

P0 分两步实施：

- Foundation PR：先交付共享分格策略、核心深度 Numeric/Decimal 迁移、`MATERIAL_UNMOUNTED` 资源投影、`NG_MATERIAL_CONFLICT` 专用异常和对应单元测试。
- Plugin PR：在 foundation 稳定后接入 `SortingInboundContext`、源端取盘、扫码分格、目标端放盘、本地 NG 和 Session 完成检查。
- 两步都必须可独立 review、可独立回滚；Plugin PR 不得再引入新的资源深度迁移或分格算法分叉。

## 非目标

- 不在本 SPEC 中定义具体数据库表、字段迁移或完整类实现。
- 不实现单层货架高级调度算法。首版只预留优先级和算法扩展点。
- 不让 WES 成为库存主账。WES 只维护运行时投影、执行事实、回写证据和对账证据。
- 不改变 WMS 是库存、货架元数据和物料数据根源的原则。
- 不定义所有供应商协议细节。设备接口按对应硬件文档和当前联调标准适配。
- 首版不实现目标格厚度感知预占模型；当前流程同一 WorkLine 同一时刻只有一个扫码平台在制料盘，用 Session 内 `pending_target_placement` 表示目标端机械臂在途落点。
- 首版不实现源端 NG 实时 WMS outbox 确认；NG 后续对账可以通过目标箱回写证据、Session 结算证据或独立对账合同承接。
- 首版不做 resource 域所有 float 字段的全量迁移；P0 只迁移 `BinCellOccupancy` 的 `used_depth_mm`、`capacity_depth_mm`、`remaining_depth_mm` 到数据库 Numeric/Decimal，并把相关容量计算切到 Decimal。

## 核心业务约定

### 目标箱与料格规则

- 目标料箱允许多物料共箱。
- 同一料格只允许同物料、同 DC、同 LC。
- 目标箱满的判定：没有可用空格，且所有已有兼容格的可用厚度均不足以放入当前料盘。
- 目标格容量首版按可用厚度判断：格主数据提供总厚度，每放入一盘按料盘厚度扣减；`available_thickness >= reel_thickness` 时才允许放入。
- 分格时先找同物料同 DC/LC 且可用厚度足够的兼容格；找不到兼容格但有空格时开新格；没有空格或厚度不足时释放当前目标箱。
- 分格算法必须从粗分机现有分格逻辑中提炼为共享纯策略，例如 `SmtBinCellAllocationPolicy`。粗分机保留换架/调度包装，分拣机只复用兼容格、空格和容量判断，不复制一套平行算法。
- `SmtBinCellAllocationPolicy` 放在 resource services 层，作为粗分机和分拣入库共享的纯策略。它的职责边界是“给定 active snapshot、物料身份和 Decimal 厚度，返回可放入目标格或无法放入原因”。它不得访问数据库、不得写 Session、不得下发命令、不得了解粗分机或分拣机的调度状态。
- 粗分机现有 rack operation、换架、命令编排仍留在粗分机服务中；这些服务只能把 snapshot 和物料输入传给共享策略，再把策略结果包装成粗分机自己的后续动作。
- 分拣入库插件只消费共享策略的分格结果，并把结果写入 `pending_target_placement`；不得复制粗分机服务内部的平行分格判断。
- 扫码平台读取料盘 6 合 1 码后再做目标料格分配，不在源端机械臂取盘前预分配目标格。
- 源端机械臂取盘前，WES 可基于源端锁定快照做目标箱可容纳性预检查，避免明显无法放入的料盘进入扫码平台；该预检查不创建目标格预占，也不替代扫码后的真实分格。
- 每次分格或源格选择应基于一次 active snapshot 建内存索引完成；不得在源格候选循环中逐格查询目标格容量，避免 N+1 查询和锁竞争。

厚度容量边界：

- 厚度单位采用 WMS 主数据约定的 Decimal 毫米值，WES 内部比较不得使用浮点近似。
- `BinCellOccupancy.used_depth_mm`、`capacity_depth_mm`、`remaining_depth_mm` 在 P0 中迁移为数据库 Numeric/Decimal；其它历史快照或数量类 float 字段不随 P0 迁移。
- 料盘厚度、料格总厚度或已用厚度缺失、非法、负数时，不允许自动放入目标格，也不得按 0 厚度处理。
- 料格快照出现 `used_thickness > total_thickness` 或 `available_thickness < 0` 时，冻结该目标格或目标箱并进入对账。
- 扫码/测量厚度与 WMS 快照厚度不一致但物料身份一致时，以现场实际厚度参与容量校验，并在后续 WMS 回写中同时携带原快照厚度、实际厚度和 mismatch evidence。
- 首版目标格分配不创建 reservation。分配结果写入 Session 内 `pending_target_placement`，并作为目标端机械臂命令参数下发。
- 目标端机械臂成功后，WES 消耗 `pending_target_placement` 并更新目标箱投影；机械臂失败且落点明确时清理在途落点并进入可恢复处理，落点未知时进入对账。
- 若未来引入多扫码平台、多目标机械臂或跨 WorkLine 共享目标箱，再启用厚度感知 reservation；该能力不属于首版本地闭环。

### 分拣机设备角色

分拣入库插件不得写死 `ARM01/ARM02` 等设备编码。设备由 WorkLine topology 绑定为角色，运行时根据角色解析真实 `device_code`。

首版角色：

- `SORTING_SOURCE_ARM`：源端机械臂，负责从单层货架源料格取盘到扫码平台。
- `SORTING_TARGET_ARM`：目标端机械臂，负责从扫码平台放盘到目标箱目标格。
- `SORTING_NG_ARM`：本地 NG 机械臂角色；若现场与源端机械臂或目标端机械臂为同一物理设备，由 topology 绑定到同一设备。
- `SORTING_SCAN_PLATFORM`：扫码平台。
- `SORTING_NG_STATION`：本地 NG 位，例如 `SCAN_NG` / `STATION_NG`。
- `SORTING_WORKSTATION`：目标箱工作位。

角色与实际设备编码的映射属于 WorkLine 配置事实，不属于本 SPEC 的业务流程事实。

插件合同必须显式声明命令/事件与角色的映射，方式与粗分机插件一致，落在插件 `command_target_roles` / `event_source_roles` 配置中。

首版角色映射建议：

| 命令或事件 | 角色 |
| --- | --- |
| 源料格取盘到扫码平台命令 | `SORTING_SOURCE_ARM` |
| 扫码平台放盘到目标格命令 | `SORTING_TARGET_ARM` |
| 扫码平台放盘到本地 NG 位命令 | `SORTING_NG_ARM` |
| 源端取盘完成事件或命令结果 | `SORTING_SOURCE_ARM` |
| 目标端放盘完成事件或命令结果 | `SORTING_TARGET_ARM` |
| 本地 NG 放盘完成事件或命令结果 | `SORTING_NG_ARM` |
| 工作位二次扫码事件 `WORKING_BIN_SCAN` | `SORTING_WORKSTATION` |

具体 `task_type`、事件名和硬件字段可在插件详细合同中命名，但不得跳过角色映射直接写设备编码。

### 五层货架数据边界

- 五层货架元数据由 WMS 维护。
- WES 请求 WMS 分配并搬运五层货架。
- 五层货架到达分拣机工作位后，WMS 同步该货架授权快照到 WES。
- WES 只在授权快照范围内进行目标箱选择、回位分配和格位投影更新。
- WES 分配结果必须回写 WMS。WMS 确认后，才成为最终业务事实。

授权快照至少应包含：

- 五层货架 ID、活动面、可操作面、授权有效期。
- 料箱 ID、料箱类型、料箱所在面/层/列。
- 每个料格的物料、DC、LC、数量或层数、总厚度、已用厚度和可用厚度。
- 冻结、异常、占用标记。
- WMS 侧版本号或快照版本。

### 单层货架 Station 规则

- 分拣机有两个单层货架 Station。
- 两个 Station 应交替工作，但首版优先完成某一个 `primary_station` 上的单层货架。
- `secondary_station` 只在 primary station 没有合适源料格，或需要归集同物料同 DC/LC 时参与。
- Station 清空后，WES 请求 WMS/RCS 将单层货架移出至空架区，并可调入排队位下一架单层货架。

上游粗分机交接前提：

- 粗分机向单层货架料格入料时，WES 必须按实际入格顺序记录料盘序列、物料身份、厚度、来源命令和入格证据。
- 粗分机入料前必须对目标源料格做容量判断，容量不足、厚度缺失或料格身份不兼容时不得继续入格。
- 分拣入库插件选择“源料格顶部一盘”时，必须基于上游已落库的有序料盘序列；若源格没有可信顺序记录或容量记录，本插件不得自动取盘，应进入对账或人工确认。
- 源料格顶部一盘首版定义为后进先出：`cell_stack_position` 最大的 active 料盘先被源端机械臂取出。`cell_stack_position=1` 仍表示最早入格。

### CTU 独占规则

- CTU 背篓上的料箱必须全部投放完毕后，才能接受其它非当前任务。
- CTU 背篓非空时，CTU 对当前 Session 独占。
- CTU 投料完成后，优先处理流水线退箱区待回收目标箱。
- CTU 夹取退箱前，也必须通过 WES 与流水线确认可出料。
- CTU 物理调度链路固定为 `WES -> WMS -> RCS`。WES 只发业务请求、许可和约束，不直接控制 CTU 设备。

### WMS 同步粒度

- WMS 物料数据按目标箱同步。
- 目标端机械臂每次放盘后，WES 更新本地运行时事实和目标箱投影。
- 目标箱离开工作位、进入退箱区、被 CTU 放回五层货架后，WES 向 WMS 一次性同步该目标箱的位置、格位物料和执行证据。
- WMS 同步未确认前，该目标箱和对应回位位置不得作为最终可用库存参与其它业务。
- 源端机械臂取盘成功是源格物理出账点。WES 在取盘成功时将源格顶部料盘转为在制物料，扣减源格投影并记录取盘命令、源格版本和扫码平台占用。
- 源端不一致 NG 不再承担源格扣减职责，只更新该在制物料的实际身份、实际状态、当前位置和异常证据。
- NG 事实必须在 WES 本地可信落账，并可随目标箱回写证据、Session 结算或后续对账同步给 WMS；NG 当下不要求 WMS 实时确认，也不得把 Session 切入 `EXTERNAL_HTTP` wait。
- Session 完成前必须保证所有在制物料都有最终本地去向：进入目标箱、进入本地 NG 位，或进入明确 RuntimeHold/对账。未闭环的在制物料会阻止 Session 完成。

### 源端不一致 NG 规则

扫码平台扫码结果与源端锁定快照不一致时，首版不自动重归类，也不阻塞整个 Session。

- 不一致料盘进入本地 NG 流程，不进入目标箱、流水线或 CTU 链路。
- WES 下发本地机械臂动作，将料盘从扫码平台移动到 `SORTING_NG_STATION`。
- NG 成功后，只更新在制物料的实际状态和 NG 去向；源料格扣减已在源端机械臂取盘成功时完成，不在 NG 阶段重复扣减。
- 目标箱投影不更新，不占用目标格，不触发该盘的目标箱 WMS 物料变化。
- WES 必须记录 `SOURCE_SNAPSHOT_MISMATCH` evidence，包含源端预期身份、实际扫码身份、源料格引用、取盘命令、扫码事件和 NG 命令。
- NG 结果必须落到现有 `NgReturnItem` 语义中：`disposition=RETURN_TO_NG`，`ng_reason_code=SOURCE_SNAPSHOT_MISMATCH`，`created_from_runtime_hold_id` 允许为空，`physical_handoff_evidence_json` 记录本地 NG 去向和动作证据。
- `NgReturnItem.material_identity_key` 首版沿用现有 active 唯一语义：同一物料身份同一时刻只能有一个 active NG item。若新的源端不一致 NG 命中已有 active `material_identity_key` 且不是同一 Session/同一命令的幂等重放，不静默聚合、不生成单盘唯一变体，进入 `NG_MATERIAL_CONFLICT` 对账或人工处理。
- `NG_MATERIAL_CONFLICT` 是业务阻断，不是可忽略异常。WES 必须创建或关联 RuntimeHold，或将插件业务态切入 `RECONCILING` 并让通用状态进入 `MANUAL_HOLD`；证据中必须包含已有 active NG item、新事件的 `material_identity_key`、源 Session、源命令、扫码事件、预期身份和实际身份。冲突解除前不得完成 Session、释放相关源格或继续处理该冲突料盘。
- 同一 Session/同一命令的重复上报仍按幂等重放处理，返回已有 `NgReturnItem` 和同步证据，不创建新的冲突。
- NG 成功后的实际状态和 NG 去向先作为 WES 本地事实闭环；是否同步给 WMS 由目标箱回写证据、Session 结算或后续对账合同承接，不作为本地继续处理下一盘的阻塞点。
- 普通源端不一致 NG 不默认创建 RuntimeHold；只有 NG 命令失败、落点未知、NG 位满或超过冻结阈值时，才进入 RuntimeHold、人工暂停或对账。
- 同一源格或同一单层货架连续/累计不一致超过阈值时，冻结源格或货架并进入对账；首版建议源格连续 2 次冻结、单层货架累计 5 次冻结，最终以配置为准。

## 资源与状态

### Runtime Session 状态映射

以下状态是分拣入库插件的业务阶段或资源投影，不是对通用 `workline_sessions.status` / `SessionStatus` 的枚举扩展。实现时不得为了本插件新增通用 Session 状态；通用 Session 只表达运行时大类，分拣细分阶段保存在插件 context、资源投影或事件证据中。

下文提到 Session 进入某个分拣业务态，均指插件业务态或资源投影变化；只有需要跨 WorkLine runtime 通用语义时，才映射到 `SessionStatus`。

| 分拣业务阶段 | 通用 SessionStatus 映射 | 说明 |
| --- | --- | --- |
| `PENDING` | `NEW` | Session 已创建，等待资源。 |
| `WAITING_SOURCE_RACK` | `WAITING_EXTERNAL` | 等待单层货架进入分拣机 Station。 |
| `WAITING_TARGET_RACK` | `WAITING_EXTERNAL` | 等待五层货架授权并到位。 |
| `RUNNING` | `RUNNING` | 正常分拣中。 |
| `WAITING_DEVICE_RESULT` 类动作 | `WAITING_DEVICE_RESULT` | 等待机械臂、流水线、CTU/WMS/RCS 命令结果。 |
| `AUTO_RECOVERABLE_SUSPENDED` | `RUNNING` | 系统仍在后台重试或校验，且不需要人工介入。插件业务态阻止新动作，通用状态保持可调度。 |
| `AUTO_RECOVERABLE_HOLD` | `MANUAL_HOLD` | 已停止新动作，等待人工确认、对账或外部系统确认后才能继续。 |
| `RECOVERING` | `RUNNING` | 自动恢复校验通过后，正在恢复资源锁和执行上下文。 |
| `MANUAL_SUSPENDED` | `MANUAL_HOLD` | 必须人工介入，系统不得自动继续。 |
| `RECONCILING` | `MANUAL_HOLD` | WMS、WES 投影或现场状态不一致，等待对账。 |
| `COMPLETED` | `COMPLETED` | Session 业务闭环。 |
| `FAILED` | `FAILED` | 业务失败并终止。 |

映射原则：

- 等待 WMS/RCS、货架、流水线等外部业务结果时，通用状态优先使用 `WAITING_EXTERNAL`。
- 等待已下发设备命令结果时，通用状态优先使用 `WAITING_DEVICE_RESULT`。
- 可自动恢复暂停如果仍由系统后台重试，不应表现为最终失败；超过自动恢复能力、需要人工确认或必须阻止人工外操作时，插件业务态切到 `AUTO_RECOVERABLE_HOLD`，通用状态进入 `MANUAL_HOLD`。
- 分拣状态机的细粒度状态用于插件内部决策、恢复和可观测性，不作为跨插件通用状态。

### 单层货架状态

- `QUEUED_FOR_SORTER`：在分拣机排队位。
- `MOVING_TO_STATION`：WMS/RCS 正在搬运到 Station。
- `DOCKED_AT_STATION`：已到 Station。
- `ACTIVE_SOURCE_RACK`：作为当前源货架参与分拣。
- `DRAINED`：所有源料格清空。
- `MOVING_TO_EMPTY_RACK_AREA`：正在移出到空架区。
- `REMOVED`：已移出分拣机。

### 五层货架状态

- `REQUESTED_FROM_WMS`：WES 已请求 WMS 分配。
- `MOVING_TO_SORTER`：WMS/RCS 正在搬运。
- `DOCKED`：已到五层货架工作位。
- `AUTHORIZED_PROJECTED`：WES 已建立授权投影。
- `FACE_ACTIVE`：当前面可作业。
- `FACE_SWITCHING`：正在换面。
- `RETURNING_TO_STORAGE`：正在回库。
- `HELD`：冻结或待对账。

### 目标箱状态

- `AVAILABLE_ON_TARGET_RACK`：在五层货架授权投影中可选。
- `RESERVED_FOR_FEEDING`：被 WES 选中准备投料。
- `IN_CTU_BASKET_TO_INPUT`：在 CTU 背篓中，准备投向流水线。
- `ON_PIPELINE_WAITING_SCAN`：已进入流水线，等待扫码位。
- `SCAN_ALLOWED`：扫码位校验通过，可进工作位。
- `SCAN_REJECTED_TO_RETURN`：扫码位校验不通过，进入退箱区。
- `ACTIVE_AT_WORKSTATION`：到达工作位并二次扫码确认。
- `LOCAL_MUTATING`：正在接收目标端机械臂放盘，WES 更新本地投影。
- `WAITING_RETURN_TO_RACK`：离开工作位，等待 CTU 回收。
- `IN_CTU_BASKET_RETURNING`：在 CTU 背篓中，准备回五层货架。
- `PLACED_BACK_PENDING_WMS_SYNC`：已回位，等待 WMS 同步确认。
- `WMS_SYNC_RETRYING`：WMS 同步失败，自动重试中。
- `WMS_SYNCED`：WMS 已确认目标箱位置和物料数据。
- `RECONCILING`：目标箱状态不可信，需对账。

### 扫码平台状态

- `EMPTY`：空闲。
- `OCCUPIED_BY_REEL`：已有料盘，未完成目标端放置或本地 NG。
- `WAITING_TARGET_BIN_SWITCH`：料盘已扫码且身份可信，但当前目标箱无可用格，正在切换目标箱；此状态下不得启动新的源端取盘。
- `SCAN_FAILED`：扫码失败。
- `NG_MOVING`：扫码结果与源端快照不一致，正在执行本地 NG 搬运动作。
- `MANUAL_CONFIRM_REQUIRED`：需要人工确认。

### 料盘异常状态

- `SOURCE_SNAPSHOT_MISMATCH_TO_NG`：料盘扫码身份与源端锁定快照不一致，已判定进入本地 NG。
- `NG_MOVING`：料盘正在从扫码平台移动到本地 NG 位。
- `NG_CONFIRMED`：料盘已到本地 NG 位，在制物料状态、实际身份和 NG evidence 已记录。

### 资源事件落账约定

P0 不允许插件绕过资源投影直接修改源格或目标格表。所有源格出账和目标格入账都必须通过资源事实进入投影链路，保证幂等、审计和后续对账语义一致。

- 源端机械臂取盘成功：写入或处理 `MATERIAL_UNMOUNTED` 资源事实，结束源格顶部 active 料盘挂载，扣减源格容量或层数，并记录源端命令、源格版本和 `cell_stack_position`。
- 目标端机械臂放盘成功：写入或复用 `MATERIAL_MOUNTED` 资源事实，在目标格创建 active 料盘挂载，更新目标格容量投影，并记录目标端命令、目标格快照版本和容量 evidence。
- 本地 NG 成功：不写目标格 `MATERIAL_MOUNTED`；只关闭在制物料、写 `NgReturnItem` 和 NG evidence。
- 源端已取出的料盘后续无论进入目标箱、本地 NG、人工暂停还是对账，均不得重新打开源格 active 挂载。

`MATERIAL_UNMOUNTED` 的 P0 投影语义：

- 必须按源格内 `cell_stack_position` 最大的 active 料盘结束挂载。
- 幂等键必须包含源 Session、源端命令、源料箱/源格、被取出料盘身份或挂载 ID。
- 重放同一事件应返回同一出账结果，不得重复扣减容量。
- 找不到预期 active 顶部料盘、顶部料盘身份不匹配、源格版本不匹配或源格容量异常时，不得猜测扣减，必须进入 `RECONCILING` 或人工确认。

### 在制物料本地闭环

首版不新增独立在制物料表。当前扫码平台同一时刻最多只有一盘料，WES 使用 `WorklineSession.context_json` 保存当前在制物料和目标端在途落点：

- `sorting.current_material`：当前已从源格取出、尚未完成目标放盘或本地 NG 的料盘事实。
- `sorting.pending_target_placement`：扫码后已计算目标格、目标端机械臂尚未返回结果的在途目标落点。

实现必须新增类型化 `SortingInboundContext` 作为插件 context 合同。handler 不得散落读取或深层修改 `ctx.session.context_json["sorting"]`，必须先解析为类型化对象，再通过明确的更新入口写回 Session。

`SortingInboundContext` 至少负责：

- 解析和校验 `sorting.context_schema_version`。
- 解析 `current_material`、`pending_target_placement`、`active_target_bin`、`primary_station`、`secondary_station` 等分拣入库上下文字段。
- 提供关闭在制物料、写入在途目标落点、清理在途目标落点、记录失败 evidence 的一致写回语义。
- 保证 JSON 字段更新通过重新赋值或 runtime 统一更新入口持久化，避免 ORM 不感知嵌套 dict 原地变更。

context 版本要求：

- P0 使用 `sorting.context_schema_version=1`。
- 无版本或版本不兼容时，插件不得继续自动分拣，应进入 `RECONCILING` 或人工确认。
- context 中 Decimal 厚度保留字符串原值，计算阶段再转为 Decimal；不得把 float 近似值作为 evidence 原值。

`sorting.current_material` 至少包含：

- 源单层货架、源料箱、源料格、`cell_stack_position`。
- 源端取盘命令、源格版本、取盘时间。
- 源端预期物料身份、扫码实际物料身份、料盘厚度 Decimal 原值。
- 当前状态：`PICKED_TO_SCAN_PLATFORM`、`SCANNED_OK`、`TARGET_MOVING`、`TARGET_PLACED`、`NG_MOVING`、`NG_PLACED`、`RECONCILING`。
- 关联扫码事件、目标端命令、本地 NG 命令或 RuntimeHold/对账 ID。

`sorting.pending_target_placement` 至少包含：

- 目标箱、目标格、目标格来源快照版本。
- 分格输入的物料身份、料盘厚度 Decimal 原值、容量判断 evidence。
- 目标端机械臂命令和命令参数中的目标格位。

在制物料状态机：

```text
source cell top reel (max cell_stack_position)
  |
  | source arm success
  v
PICKED_TO_SCAN_PLATFORM
  |
  | scan ok                         | scan mismatch
  v                                 v
SCANNED_OK                    NG_MOVING
  |                                 |
  | target cell chosen              | NG arm success
  v                                 v
TARGET_MOVING                  NG_PLACED
  |
  | target arm success
  v
TARGET_PLACED

Any unknown physical location -> RECONCILING
Session can complete only when no current_material is open.
```

源格 LIFO 取盘：

```text
cell_stack_position: 1     2     3
入格时间:             old -> newer -> newest
                               ^
                               source arm picks this first
```

扫码后目标格计算与放盘：

```text
scan platform reads 6-in-1 code
  |
  v
shared SmtBinCellAllocationPolicy
  |
  +-- compatible cell with enough Decimal depth -> target cell
  +-- empty cell with enough Decimal depth      -> target cell
  +-- no target cell                            -> WAITING_TARGET_BIN_SWITCH
  |
  v
write pending_target_placement into Session context
  |
  v
send target-arm command(target_bin, target_cell)
  |
  +-- success -> BinMaterialMount + occupancy projection + close current_material
  +-- unknown -> RECONCILING
```

## 主流程

### 1. 创建分拣入库 Session

满箱交换判断后，若单层货架不满足交换要求或交换后仍需零散入库，WES 创建 `SMT_SORTING_INBOUND` Session。

Session 记录：

- 来源粗分机和来源任务批次。
- `rack_release_id`。
- 单层货架 ID。
- 源料箱、源料格和料盘快照。
- 分拣机排队位。
- 优先级。
- 幂等业务键。

Celery Beat 可扫描待处理排队位和异常等待状态，但不能作为业务真相源。Session 创建应来自上游事件、满箱交换结果或 WMS/RCS 回调。

### 2. 调度单层货架进入 Station

WES 检查分拣机 `STATION_A/B`。

- 有空 Station：请求 WMS/RCS 将排队位单层货架搬入。
- 两个 Station 都占用：保持排队，等待 Station 释放。
- 搬运完成后：WMS/RCS 回调，WES 将单层货架置为 `DOCKED_AT_STATION`。

两个 Station 都有货架时，WES 选择一个作为 `primary_station`，优先清空该 Station。

### 3. 准备五层货架工作位

WES 检查五层货架工作位是否有可用五层货架。

- 没有：WES 请求 WMS 分配并调度五层货架。
- 有但未授权：等待 WMS 同步授权快照。
- 有且授权有效：进入目标箱选择。

五层货架到位后，WMS 同步元数据和授权快照到 WES。WES 建立运行时投影，用于当前作业窗口内的选箱、回位和分格计算。

### 4. 选择目标箱批次

WES 在五层货架当前活动面投影中选择目标箱批次。

批次数量取以下值的最小值：

- 当前活动面可取目标箱数量。
- 流水线投料缓存可用数量。
- CTU 背篓可用数量。

被选目标箱进入 `RESERVED_FOR_FEEDING`，避免重复选择。

### 5. CTU 从五层货架取箱

WES 向 WMS 发起 CTU 取箱业务请求，由 WMS 调度 RCS 执行物理动作。CTU 将目标箱取到背篓后，WMS/RCS 回调 WES。

约束：

- 背篓非空时，CTU 进入当前 Session 独占。
- 取箱过程中目标箱不得再被其它任务选择。
- 取箱失败时，目标箱和五层货架位置进入异常或对账。

### 6. CTU 逐箱投料到流水线

CTU 到达流水线投料口后，每投一个目标箱前都必须通过 WMS/RCS 请求 WES 业务许可。

WES 判断：

- 流水线投料口是否可接收。
- 投料缓存是否有空位。
- 当前 Session 是否未暂停。
- 目标箱是否仍属于当前授权批次。

条件满足后，WES 确认可投料，WMS/RCS 执行 CTU 投箱。投料成功回调后，目标箱进入 `ON_PIPELINE_WAITING_SCAN`。

CTU 背篓全部清空后，才允许进入下一阶段。

### 7. CTU 优先处理退箱区

CTU 投料完成后，WES 优先检查流水线退箱区。

若退箱区存在 `WAITING_RETURN_TO_RACK` 目标箱：

1. WES 向 WMS 发起退箱回收业务请求。
2. WMS/RCS 调度 CTU 到退料口。
3. CTU 到位后通过 WMS/RCS 询问 WES 是否可出料。
4. WES 与流水线确认可出料。
5. CTU 夹取目标箱到背篓。
6. 目标箱进入 `IN_CTU_BASKET_RETURNING`。

### 8. 流水线扫码位准入

目标箱到扫码位后，流水线扫码并向 WES 推送事件。

WES 校验：

- 目标箱是否属于当前 Session。
- 目标箱是否来自当前五层货架授权投影。
- 目标箱是否未冻结、未对账。
- 工作位是否可接收。
- 分拣机是否未暂停。

校验通过时，WES 放行目标箱进入工作位。校验不通过时，目标箱进入退箱区，并记录拒绝原因。

常见拒绝原因：

- `UNEXPECTED_BIN`：非当前 Session 目标箱。
- `SESSION_MISMATCH`：目标箱属于其它 Session。
- `WORKSTATION_BUSY`：工作位忙。
- `SCAN_FAILED`：扫码失败或关键字段缺失。
- `RESOURCE_HELD`：目标箱或货架冻结。

### 9. 工作位目标箱确认

目标箱到工作位后再次扫码。工作位二次扫码事件定义为 `WORKING_BIN_SCAN`，事件负载必须可解析出 `device_code`、`location` 和目标箱身份。

扫码成功后：

- WES 设置 `active_target_bin`。
- 工作位锁定该目标箱。
- 目标箱进入 `ACTIVE_AT_WORKSTATION`。

工作位同一时刻只能有一个 active 目标箱。

### 10. 选择源料格

WES 从单层货架 Station 中选择源料格。

首版策略：

1. 优先在 `primary_station` 查找可放入当前目标箱已有兼容格的源料格。
2. 若没有，查找可在当前目标箱开新格的源料格。
3. 若 primary station 没有合适源料格，再检查 `secondary_station`。
4. 尽量减少 Station 来回切换，优先清空 primary station。
5. 基于源端锁定快照做可容纳性预检查，过滤当前目标箱明显无法容纳的源料格；预检查不创建 `pending_target_placement`，最终分格仍以扫码平台真实结果为准。

源料格只做源端锁定，不做目标格预分配。

### 11. 源端机械臂取盘到扫码平台

WES 锁定源料格顶部一盘，向 `SORTING_SOURCE_ARM` 下发从源料格到扫码平台的搬运动作。

约束：

- 扫码平台必须为空。
- 源端机械臂取盘成功后，WES 必须写入或处理 `MATERIAL_UNMOUNTED` 资源事实，从源格有序料盘序列中结束 `cell_stack_position` 最大的 active 料盘挂载，创建或更新在制物料事实，并扣减源格可用厚度或层数。
- 源端机械臂已取盘后，该料盘进入不可逆阶段，后续扫码一致、扫码不一致或放盘失败都只能更新该在制物料的状态和落点，不得把源格扣减延迟到 NG 或目标端放盘阶段。
- 扫码平台被占用时，不允许源端机械臂取下一盘。
- `MATERIAL_UNMOUNTED`、`sorting.current_material` 创建和扫码平台占用记录必须处于同一事务边界或等价原子提交；任一失败时，不得认为取盘已被 WES 本地闭环。

### 12. 扫码平台扫码与分格

扫码平台读取料盘 6 合 1 码，WES 先校验扫码结果与源端锁定快照，再基于真实扫码结果分配目标料格。

分格规则：

1. 使用共享 `SmtBinCellAllocationPolicy`，输入当前目标箱 active snapshot、扫码物料身份和 Decimal 厚度。
2. 查找当前目标箱内同物料、同 DC、同 LC 且可用厚度足够的料格。
3. 若找到，分配到该兼容格，并写入 `sorting.pending_target_placement`。
4. 若找不到兼容格但目标箱有空格，开新格，并写入 `sorting.pending_target_placement`。
5. 若目标箱没有空格或没有厚度足够的可用格，释放当前目标箱，并进入已扫码料盘等待目标箱切换流程；切换完成前不再启动新的源端机械臂取盘。

`pending_target_placement` 只是当前 Session 的在途目标落点，不是格位预占。首版同一 WorkLine 只有一个扫码平台在制料盘，不需要扩展 WorkLine reservation。

若扫码结果异常或与源端锁定快照不一致，不得继续目标端放盘：

- 扫码失败或关键字段缺失：进入 `SCAN_FAILED` 或 `MANUAL_CONFIRM_REQUIRED`。
- 核心身份字段不一致：进入 `SOURCE_SNAPSHOT_MISMATCH_TO_NG`，执行本地 NG 流程。
- 厚度与源端快照不同但身份一致：以实际扫码/测量厚度参与容量校验，并记录 mismatch evidence。
- 厚度缺失、非法或为负数：不允许自动分格，进入 `MANUAL_CONFIRM_REQUIRED` 或对账，不得按默认厚度继续。
- 目标格快照厚度异常：冻结目标格或目标箱，释放当前扫码平台动作链路前不得启动新的源端取盘。

首版不做自动重归类。

### 12A. 已扫码料盘等待目标箱切换

当扫码平台上料盘身份可信，但当前目标箱无可用格或厚度不足时，该料盘不能退回源格，也不能进入本地 NG。

处理规则：

1. 扫码平台进入 `WAITING_TARGET_BIN_SWITCH`，料盘继续绑定当前 Session、源料格和扫码事件。
2. 当前目标箱进入退箱流程，不再接收该料盘。
3. WES 暂停新的源端取盘，直到新的 active 目标箱到工作位并完成 `WORKING_BIN_SCAN`。
4. 新目标箱确认后，WES 用同一已扫码料盘重新执行分格；若可放入，则进入目标端机械臂放盘。
5. 若五层货架当前面没有可用目标箱，WES 请求换面或调入下一架五层货架；等待期间通用状态使用 `WAITING_EXTERNAL`，插件业务态保持 `WAITING_TARGET_BIN_SWITCH`。
6. 若超时、目标箱切换失败或扫码平台料盘归属变得不可信，进入 `RECONCILING` 或 `MANUAL_SUSPENDED`。

### 13. 本地 NG 分支

当料盘进入 `SOURCE_SNAPSHOT_MISMATCH_TO_NG` 时，WES 向 `SORTING_NG_ARM` 下发从扫码平台到本地 NG 位的搬运动作。

成功后：

- 源料格扣减不在 NG 阶段发生；取盘成功时已经完成源格出账和在制物料创建。
- 目标箱和目标格不变。
- 在制物料状态更新为已进入本地 NG 位，并记录实际扫码身份、NG 原因、当前位置和落点证据。
- WES 记录 `SOURCE_SNAPSHOT_MISMATCH` evidence 和 NG 去向。
- WES 创建或更新 `NgReturnItem` 记录，原因码为 `SOURCE_SNAPSHOT_MISMATCH`，并关联源端快照、扫码事件、取盘命令、本地 NG 命令和 NG 位。
- 在制物料 NG 状态、`NgReturnItem` 创建或幂等命中、NG evidence 必须作为同一事务边界提交；任一写入失败时，不得释放扫码平台或继续下一盘。
- 若写入过程中命中不同来源的 active `material_identity_key` 冲突，进入 `NG_MATERIAL_CONFLICT` 对账或人工暂停，不得继续下一盘，也不得把冲突料盘视为普通 NG 成功。
- NG 事实不要求 WMS 实时确认；扫码平台释放和下一盘处理只依赖本地事务持久化成功、NG 位落点可信和在制物料已闭环。
- 扫码平台释放。
- Session 继续处理下一盘。

失败后：

- 若料盘落点明确且可人工接管，Session 进入 `MANUAL_SUSPENDED`。
- 若料盘落点未知，Session 进入 `RECONCILING`。

### 14. 目标端机械臂放盘到目标料格

WES 向 `SORTING_TARGET_ARM` 下发从扫码平台到目标箱目标格的搬运动作。

下发命令前，WES 必须已在 `sorting.pending_target_placement` 中记录目标箱、目标格、分格快照版本和容量判断 evidence。目标箱和目标格作为命令参数传给目标端机械臂。

成功后：

- 源料格已在源端机械臂取盘成功时减少一盘，本阶段不再重复扣减源格。
- 通过 `MATERIAL_MOUNTED` 资源事实让目标格增加一盘。
- 在制物料状态更新为已进入目标箱目标格。
- WES 更新目标箱本地投影。
- WES 记录后续 WMS 同步所需的执行证据。
- WES 清理 `sorting.pending_target_placement` 并关闭当前在制物料。
- 扫码平台释放。

失败后：

- 若落点明确且料盘仍在扫码平台或可人工接管，WES 清理或保留 `pending_target_placement` 的失败 evidence，并进入 `MANUAL_SUSPENDED`。
- 若落点未知，Session 进入 `RECONCILING` 或 `MANUAL_SUSPENDED`。

### 15. 当前目标箱继续分拣

若目标箱仍有可用空格或厚度足够的兼容格，继续选择源料格并重复源端机械臂、扫码平台、目标端机械臂流程。

若目标箱没有可用空格或没有厚度足够的兼容格：

- WES 下发流水线移动命令，将当前目标箱移出工作位。
- 目标箱进入退箱区。
- 工作位释放后，流水线可放行下一个已准入目标箱。

### 16. 目标箱回五层货架

CTU 从退箱区夹取目标箱后，WES 在五层货架授权投影内分配回位位置，并通过 WMS/RCS 完成 CTU 回位动作。

CTU 放回成功后：

- WES 更新五层货架投影。
- 目标箱进入 `PLACED_BACK_PENDING_WMS_SYNC`。
- 对应回位位置进入待确认状态，不能分配给其它目标箱。

目标箱不要求回到原位置。WES 可在当前五层货架授权投影内重新分配位置。

### 17. 按目标箱同步 WMS

目标箱回到五层货架后，WES 向 WMS 同步该目标箱的最终变化。

同步内容至少包含：

- Session、Trace 和目标箱标识。
- 五层货架 ID、面、层、列。
- 目标箱类型。
- 目标箱每个料格的物料、DC、LC、数量或层数、总厚度、已用厚度和可用厚度。
- 来源单层货架、源料箱、源料格引用。
- 源端机械臂、扫码平台、目标端机械臂、CTU 的执行证据。
- 本 Session 内与该源料格相关的 NG evidence 引用，用于解释源端物理扣减但未进入目标箱的料盘。
- WES 基于的 WMS 快照版本。
- 幂等请求键和时间戳。

WMS 确认后：

- 目标箱进入 `WMS_SYNCED`。
- 回位位置解除待确认。
- WES 更新 WMS 确认版本。

WMS 未确认前，该目标箱不能作为最终库存事实参与其它业务。

NG 事实与目标箱同步分离：

- 目标箱同步只负责目标箱最终位置和目标格物料变化。
- 源端不一致 NG 的核心事实是“已取出的在制物料没有进入目标箱，而是进入本地 NG 位”，不再承担源格扣减。
- NG 去向和 `NgReturnItem` 引用可以作为目标箱回写证据、Session 结算证据或后续对账材料提供给 WMS，但不要求在 NG 当下通过独立 WMS outbox 确认。
- 如果目标箱同步成功但存在未闭环在制物料，Session 仍不得完成；如果所有在制物料已经本地闭环，NG 后续对账不阻塞本地继续分拣。

### 18. 单层货架清空与换架

当 primary station 上单层货架所有源料箱、源料格清空：

1. WES 将其置为 `DRAINED`。
2. WES 请求 WMS/RCS 搬运到空架区。
3. Station 释放。
4. 若 secondary station 仍有货，提升为 primary station。
5. 若分拣机排队位有下一架，调入空 Station。

### 19. 五层货架换面或回库

当前活动面没有可投目标箱或没有可回位空间时：

- 若另一面可用，WES 请求 WMS/RCS 进行换面。
- 若两面都不可用，WES 请求 WMS/RCS 将该五层货架回库，并请求下一架五层货架。

换面或回库期间：

- 暂停新的 CTU 取箱批次。
- 已在流水线或 CTU 背篓中的目标箱按安全规则处理。

### 20. Session 完成

Session 完成必须同时满足：

- 所有源单层货架已清空并移出。
- 分拣机工作位无 active 目标箱。
- 流水线无当前 Session 在途目标箱。
- 退箱区无待回收目标箱。
- CTU 背篓为空。
- 扫码平台为空。
- 所有已回位目标箱均已完成 WMS 同步确认。
- 所有源端已取出的在制物料均已本地闭环：进入目标箱、进入本地 NG 位，或进入明确 RuntimeHold/对账。
- 没有待处理 RuntimeHold、资源对账或人工确认。

## 异常与恢复

### WMS 同步失败

目标箱回位后，同步 WMS 失败时：

1. 目标箱进入 `WMS_SYNC_RETRYING`。
2. WES 使用稳定幂等键自动重试。
3. 重试期间冻结该目标箱和回位位置。
4. 超过阈值后，插件业务态进入 `AUTO_RECOVERABLE_SUSPENDED`；若需要人工确认或停止所有外部继续动作，则切到 `AUTO_RECOVERABLE_HOLD` 并让通用状态进入 `MANUAL_HOLD`。
5. WES 停止新投料、新取盘、新目标箱准入。
6. 后台继续对失败目标箱做幂等重试。

首版推荐阈值：

- 单目标箱最多自动重试 3 次。
- 重试间隔递增，例如 10 秒、30 秒、60 秒。
- 超过阈值暂停当前分拣 Session。
- 如果连续多个目标箱同步失败，可触发 WMS 通信熔断。

### NG 本地闭环失败

源端不一致 NG 已经完成物理交接，但 WES 未能可信记录在制物料状态、`NgReturnItem` 或 NG 落点证据时：

1. 已取盘源格扣减不回滚。
2. 若本地 NG 位落点可信但记录失败，Session 进入 `AUTO_RECOVERABLE_HOLD` 或 `MANUAL_SUSPENDED`，等待补录或重试本地事务。
3. 若料盘落点未知，Session 进入 `RECONCILING`，并冻结相关源格、扫码平台和 NG 位。
4. 恢复条件只能是本地在制物料状态、NG 去向和审计证据全部补齐，或人工纠正并形成新的可审计证据。

### 暂停时的不可逆动作

暂停分拣时，只完成已经不可逆的一盘，随后暂停。

- 源端机械臂尚未取盘：立即暂停，不再取新盘。
- 源端机械臂已取盘且料盘已在扫码平台：允许扫码；若扫码一致则分格并由目标端机械臂放入目标箱，若扫码不一致则完成本地 NG，然后暂停。
- 目标端机械臂或本地 NG 机械臂已执行中：等待命令结果；成功则更新对应投影或 NG evidence，失败则进入异常处理。
- CTU 已背负料箱：只允许安全停靠或完成当前不可中断动作到安全状态，不接受新任务。

暂停期间不允许：

- 新目标箱进入工作位。
- CTU 新投料。
- 源端机械臂新取盘。
- 新的五层货架取箱批次。

### 自动恢复

可自动恢复的场景尽量自动恢复。自动恢复前必须证明 WMS、WES 投影和现场状态一致。

自动恢复前检查：

- 所有暂停前待同步目标箱均已被 WMS 确认。
- 五层货架投影版本与 WMS 返回版本一致。
- CTU 背篓为空，或背篓内箱号与 WES 投影一致。
- 流水线扫码位、工作位、退箱区状态可确认。
- 扫码平台为空，或平台上料盘有明确 Session 归属。
- 源端机械臂、目标端机械臂和本地 NG 机械臂无未知结果命令。
- 分拣机没有急停、离线或故障。
- 当前 primary station 单层货架仍在原 Station，源料格投影可信。

检查通过后：

- Session 从 `AUTO_RECOVERABLE_SUSPENDED` 进入 `RECOVERING`。
- 恢复资源锁和执行上下文。
- Session 回到 `RUNNING`。

检查不通过：

- 进入 `RECONCILING` 或 `MANUAL_SUSPENDED`。
- 需要人工确认或资源对账后才能继续。

### 不可自动恢复场景

以下场景不得自动恢复：

- WMS 明确业务拒绝。
- WMS 版本冲突无法自动合并。
- 目标箱实物位置未知。
- CTU 背篓内容不可信。
- 扫码平台料盘归属不明确。
- 源端机械臂、目标端机械臂或本地 NG 机械臂命令结果未知。
- 扫码结果与 WES 投影冲突，且无法通过本地 NG 流程安全闭环。
- 分拣机急停或安全状态未解除。

## 数据一致性原则

1. WMS 是库存、物料和货架元数据根源。
2. WES 是分拣入库作业窗口内的执行编排者，维护运行时投影和执行事实。
3. WES 的目标箱投影在 WMS 确认前不是最终库存事实。
4. 所有外部请求和 WMS 同步必须有稳定幂等键。
5. 迟到、重复、乱序回调只能追加证据，不得静默覆盖可信版本。
6. WMS 同步未完成的目标箱不能参与其它业务。
7. Session 完成必须以 WMS 同步确认作为必要条件。

## 事件与合同要求

### WMS/RCS 回调

WMS/RCS 回调应支持：

- 单层货架到达 Station。
- 单层货架移出完成。
- 五层货架到达工作位。
- 五层货架换面完成。
- 五层货架回库完成。
- CTU 取箱完成。
- CTU 投箱完成。
- CTU 退箱夹取完成。
- CTU 回位完成。

回调至少应携带：

- 稳定业务键。
- 来源事件 ID。
- 来源版本。
- 发生时间。
- 设备或任务 ID。
- 签名或可信证据。

### WMS 接口合同

完整 CTU/WMS/NG 对账实施前必须向 WMS 提出并锁定以下合同；P0 本地闭环只要求保留这些合同所需的 evidence，不要求生产级实时对接全部完成：

- 五层货架授权快照接口：WMS 向 WES 提供当前作业窗口允许操作的货架、面、料箱、格位、冻结标记、可用厚度和版本。
- 目标箱格位回写接口：目标箱回位后，WES 按目标箱一次性回写位置、每格物料、每格剩余厚度、执行证据、NG evidence 引用和基于的 WMS 快照版本。
- 源端 NG 对账接口：若 WMS 需要消费 NG 去向，WES 将预期身份、实际扫码身份、NG 去向、`NgReturnItem` 引用、源 Session、源命令、扫码事件、本地 NG 命令、源格版本和本地 NG 位证据作为对账材料提供；该接口不承担源格扣减，也不是 NG 当下的同步阻塞点。
- NG 对账幂等键：首版建议由源 Session、源命令、扫码事件、`NgReturnItem` 和源格版本组成；WMS 必须保证同键重放不会重复处理同一 NG 事实。
- NG 对账合同若在完整流程阶段实现，必须纳入 WMS typed port、endpoint registry 和 mock WMS；若不实现实时接口，则必须在目标箱回写或 Session 结算证据中保留可追溯 NG evidence。
- WMS 确认返回：目标箱格位回写必须包含确认结果、WMS 确认版本、幂等请求键和业务拒绝原因。
- 版本冲突处理：WMS 返回版本冲突时，WES 不自动覆盖，进入 `RECONCILING` 或 `MANUAL_SUSPENDED`。
- CTU 业务请求接口：WES 向 WMS 发出取箱、投料、退箱夹取和回位业务请求，由 WMS 调度 RCS；WES 不直接向 RCS 下发 CTU 物理命令。

### 流水线事件

流水线至少需要支持：

- 目标箱到扫码位。
- 扫码完成。
- 目标箱到工作位。
- 工作位二次扫码事件 `WORKING_BIN_SCAN`。
- 目标箱离开工作位。
- 目标箱进入退箱区。
- 目标箱从退箱区被 CTU 取走。

`WORKING_BIN_SCAN` 是实施前必须锁定的接口变更项，不是可选临时字段。若现场复用同一个物理扫码器，也必须通过 `event_type` 和 `location` 区分扫码位准入事件与工作位二次扫码事件。

`WORKING_BIN_SCAN` 最小负载要求：

- `event_type=WORKING_BIN_SCAN`。
- `device_code`：真实设备编码，由 WES 按 `SORTING_WORKSTATION` 角色校验。
- `timestamp`：事件发生时间。
- `data.location`：工作位或二次扫码位置编码。
- `data.bin_id` 或可解析目标箱身份的等价字段。
- `data.command_code` 或 `data.current_command_code`：若事件由流水线命令触发，应携带当前命令标识。
- `trace_id` / `session_key`：如设备侧可获得，应携带用于幂等和追踪。

### 分拣机/机械臂事件

分拣机至少需要支持：

- 源端机械臂取盘放到扫码平台完成。
- 扫码平台扫码完成或失败。
- 目标端机械臂从扫码平台放到目标格完成。
- 本地 NG 机械臂从扫码平台放到 NG 位完成。
- 机械臂失败、急停、离线。

## 首版系统边界

以下是本 SPEC 覆盖的系统边界。实施阶段应先按“首版实现切片”交付本地状态闭环，再逐步补齐完整 CTU/WMS/NG 对账链路。

- 单分拣机。
- 两个单层货架 Station。
- 一个五层货架工作位，容量按配置。
- 一个 CTU。
- 固定流水线投料缓存和退箱区。
- 目标箱按目标箱粒度同步 WMS。
- primary station 优先清空，secondary station 有界参与。
- 不实现复杂单层货架优先级算法，只保留调度扩展点。

P0 交付边界：

- Foundation PR 必须实现共享 `SmtBinCellAllocationPolicy`、`BinCellOccupancy` 核心深度 Numeric/Decimal 迁移、源端 `MATERIAL_UNMOUNTED` 投影、目标端 `MATERIAL_MOUNTED` 投影复用和 `NG_MATERIAL_CONFLICT` 专用异常。
- Plugin PR 必须实现 typed `SortingInboundContext`、分拣入库源端取盘、扫码分格、目标端放盘、本地 NG `NgReturnItem` 闭环和 Session 完成前本地闭环检查。
- 必须有单元测试和集成测试覆盖 P0 闭环，不接受只靠人工沙箱验证。
- 必须证明 active snapshot 查询是有界的，不能在源格候选或目标格候选循环里产生 N+1 查询。

延期边界：

- 完整 CTU/WMS/NG 对账链路、生产级 NG WMS 对账 endpoint、多设备并发 reservation、resource 域全量 float 清理，均不属于 P0。
- 延期项不得改变 P0 的本地事实语义；后续只是在已落账证据上补同步、对账和并发能力。

## 风险

- WES 在授权快照内选箱和回位，如果缺少 WMS 版本控制，会产生 WMS 与 WES 双真相源。
- WMS 同步失败后，目标箱实物已回位但 WMS 未确认，必须冻结该箱和回位位置。
- 目标箱允许多物料共箱后，分格算法必须严格防止同格混料。
- 两个 Station 交替处理时，如果源格锁、扫码平台占用和 active target bin 快照边界不清，会出现同一源料格重复取盘或目标箱投影覆盖。
- CTU 背篓非空时若允许接其它任务，会破坏投料与退箱闭环。
- 暂停恢复若缺少现场状态校验，可能在扫码平台、CTU 背篓或工作位存在未闭环实物时继续运行。
- 如果把分拣业务阶段直接扩展到通用 `SessionStatus`，会污染 WorkLine runtime 的跨插件状态语义，后续插件恢复和监控会变复杂。
- 如果本地 NG 另建一套独立模型，会绕开现有 RuntimeHold/NgReturnItem 对账链路，导致 NG 料盘后续追踪断裂。
- 厚度容量如果未定义缺失值、负数和版本冲突规则，容易出现目标格超装或 WMS/WES 可用厚度双写冲突。
- 已扫码料盘如果遇到当前目标箱无容量但没有等待换箱流程，会占住扫码平台并阻塞后续源端取盘。
- 如果源格扣减延迟到 NG 或目标端放盘阶段，暂停、扫码失败或放盘失败会让 WES 源格投影与现场已取出实物不一致。
- 如果粗分机入源料格时没有按序记录和容量判断，分拣插件无法可信判断“顶部一盘”和源格剩余容量。
- 如果为重复 NG 料盘绕过现有 `material_identity_key` active 唯一约束，会破坏 RuntimeHold/NG return 的冲突检测语义；首版应显式进入冲突对账。
- 如果分拣机复制粗分机分格算法而不是提炼共享策略，两个流程会在同格同物料、厚度边界和换箱规则上逐步漂移。
- 如果目标端机械臂在途落点只存在于命令参数而不写入 `pending_target_placement`，迟到回调、失败恢复和人工对账会缺少可信上下文。
- 如果 handler 直接深层修改 `context_json`，ORM 可能不感知嵌套 dict 原地变更，导致在制物料或在途落点只存在于内存中。
- 如果源端取盘不走 `MATERIAL_UNMOUNTED` 投影链路，源格出账会绕过资源事实审计，后续重复回调、暂停恢复和对账都无法证明扣减只发生一次。
- 如果 active snapshot 没有一次读取并内存索引，源格/目标格候选越多，N+1 查询越容易把分拣节拍拖慢并放大锁竞争。

## 验收标准

### P0 门禁

P0 门禁是进入实施和合并 P0 两步 PR 的必要条件；不要求完整 CTU/WMS/NG 外部对账链路生产可用。

1. Foundation PR 必须提供共享 `SmtBinCellAllocationPolicy`，粗分机和分拣入库只在策略外做各自调度包装，不复制平行分格算法。
2. 共享策略必须用 Decimal 比较厚度，覆盖兼容格优先、开新格、无容量、厚度缺失、非法、负数、`used_depth > total_depth` 等边界。
3. `BinCellOccupancy.used_depth_mm`、`capacity_depth_mm`、`remaining_depth_mm` 必须迁移为数据库 Numeric/Decimal；resource 域其它 float 字段不属于 P0 迁移范围。
4. 源端机械臂取盘成功必须写入或处理 `MATERIAL_UNMOUNTED` 资源事实，按 `cell_stack_position` 最大的 active 料盘出账。
5. `MATERIAL_UNMOUNTED`、`sorting.current_material` 创建和扫码平台占用记录必须同事务或等价原子提交；后续 NG 或目标端放盘不得重复扣减源格。
6. 目标端机械臂成功必须通过 `MATERIAL_MOUNTED` 更新目标格投影，并关闭当前在制物料。
7. 分拣入库插件必须通过 typed `SortingInboundContext` 读写 `context_json`，并显式维护 `sorting.context_schema_version`。
8. 目标端机械臂下发前必须写入 `sorting.pending_target_placement`；成功后清理，失败或落点未知时保留 evidence 进入人工暂停或对账。
9. 扫码结果与源端锁定快照不一致时，料盘进入本地 NG，不更新目标箱投影，不触发该盘目标箱 WMS 物料变化。
10. 本地 NG 成功后必须同事务或等价原子提交在制物料 NG 状态、`NgReturnItem` 和 NG evidence；任一失败时不得释放扫码平台或推进 Session。
11. `NgReturnItem.material_identity_key` 沿用 active 唯一约束；不同来源命中同一 active key 时必须抛出或返回结构化 `NG_MATERIAL_CONFLICT`，不得依赖 `ValueError` 文本解析。
12. `NG_MATERIAL_CONFLICT` 必须落到 RuntimeHold、`RECONCILING` 或 `MANUAL_HOLD`，并阻止 Session 完成，直到冲突解除。
13. Session 完成必须检查所有在制物料均已本地闭环；NG 后续对账不把 Session 切入 `EXTERNAL_HTTP` 等待。
14. 设备命令必须通过 WorkLine 角色解析，不得在插件业务逻辑中写死 `ARM01/ARM02` 等设备编码。
15. 插件合同必须声明命令/事件到 `SORTING_SOURCE_ARM`、`SORTING_TARGET_ARM`、`SORTING_NG_ARM`、`SORTING_WORKSTATION` 等角色的映射。
16. 分拣业务阶段不得新增通用 `SessionStatus` 枚举；细粒度阶段必须保存在插件 context、资源投影或事件证据中，并能映射到现有通用状态。
17. P0 测试必须覆盖 active snapshot 查询有界性，防止源格候选或目标格候选循环中出现 N+1 查询。

### 完整流程验收

以下验收属于完整 CTU/WMS/NG 对账阶段，不阻塞 P0 本地闭环合并。

1. WES 可从满箱交换后的零散入库结果创建 SMT 分拣入库 Session。
2. 单层货架可按 Station 进入分拣机，并支持 primary station 优先清空。
3. WES 可请求 WMS 分配五层货架，并建立授权投影。
4. WES 选择目标箱批次时同时考虑活动面可取箱数、流水线投料缓存和 CTU 背篓容量。
5. CTU 背篓非空时不能接受其它非当前任务。
6. CTU 每次投料前必须通过 WES 与流水线确认可投料。
7. 目标箱扫码位不通过校验时进入退箱区，并记录拒绝原因。
8. 目标箱到工作位后必须通过 `WORKING_BIN_SCAN` 二次扫码，才能成为 active 目标箱。
9. `WORKING_BIN_SCAN` 必须有设备侧样例 payload 或 mock payload，且可与扫码位准入事件区分。
10. 已扫码料盘遇到当前目标箱无可用格时，扫码平台进入 `WAITING_TARGET_BIN_SWITCH`，不退回源格、不进本地 NG、不启动新的源端取盘，直到新目标箱确认后重新分格。
11. 目标箱回到五层货架后，WES 按目标箱粒度同步 WMS。
12. WMS 同步失败时，WES 自动重试；超过阈值后暂停当前 Session。
13. WMS 后续确认成功且恢复前检查通过时，Session 可自动恢复。
14. WMS 同步未确认的目标箱不能参与其它业务。
15. 完整流程 Session 完成必须等待所有目标箱 WMS 同步确认。
16. 若实现 NG 对账接口，该合同必须进入 typed port、endpoint registry 和 mock WMS；若不实现实时接口，目标箱回写或 Session 结算证据中必须保留 NG evidence 引用。

## 验证计划

### 文档和模型验证

- 校验本 SPEC 与 SRS 中粗分机分格规则、混合入库策略和 WMS/RCS 资源边界不冲突。
- 校验所有状态均有进入条件和退出条件。
- 校验分拣业务阶段与通用 `SessionStatus` 的映射完整，未要求扩展通用枚举。
- 校验 WMS 同步失败、自动恢复和人工对账边界清晰。

### 单元测试方向

Foundation PR 必跑：

- 共享分格策略：兼容格优先、开新格、无空格释放目标箱；粗分机和分拣机都调用同一纯策略。
- 共享分格策略纯度：输入相同 snapshot 时输出稳定，不访问数据库、不修改 Session、不下发命令。
- Decimal/Numeric 深度：`BinCellOccupancy` 三个核心 depth 字段迁移、Decimal 计算、非法值拒绝、容量边界证据。
- 源端取盘出账：取盘成功即通过 `MATERIAL_UNMOUNTED` 结束源格有序序列中 `cell_stack_position` 最大的顶部料盘 active 挂载、创建在制物料并扣减源格容量。
- `MATERIAL_UNMOUNTED` 幂等：同一源端命令重复上报只出账一次；顶部料盘缺失、身份不匹配、源格版本冲突时进入对账。
- `NgReturnItem` 结构化冲突：active `material_identity_key` 冲突必须返回结构化结果或专用异常，不能依赖 `ValueError` 文本解析。
- 性能门禁：用 fake repo、query counter 或等价手段验证源格选择和目标格分配只读取一次 active snapshot，并在内存中完成候选计算。

Plugin PR 必跑：

- `SortingInboundContext`：版本解析、缺失字段拒绝、`current_material` 写入/关闭、`pending_target_placement` 写入/清理、Decimal 字符串原值保留、嵌套 JSON 通过统一入口持久化。
- Station 选择：primary station 优先、secondary station 有界参与。
- Runtime 状态映射：分拣业务阶段映射到 `WAITING_EXTERNAL`、`WAITING_DEVICE_RESULT`、`RUNNING`、`MANUAL_HOLD` 等现有通用状态。
- 厚度容量算法：兼容格厚度足够、厚度不足开新格、所有格厚度不足释放目标箱、厚度缺失/非法/负数拒绝自动分格、`used_thickness > total_thickness` 进入对账。
- 已扫码待换箱：当前目标箱容量不足时，扫码平台保持占用，释放当前目标箱，切换新目标箱后对同一料盘重新分格。
- `pending_target_placement`：分格后记录在途目标落点，目标端放盘成功后清理，失败时按落点证据进入人工暂停或对账。
- Decimal 边界：算法内部使用 Decimal 比较厚度，现有 float 投影字段只作为输入边界；Decimal 原值必须进入 evidence 或 metadata。
- 粗分机入源料格：按序记录料盘序列，容量不足或身份不兼容时拒绝入格。
- 在制物料状态机：`current_material` 从取盘、扫码、目标移动、本地 NG 到最终闭环的状态转换。
- 本地 NG 分支：源端快照不一致、NG 成功继续、写入 `NgReturnItem`、更新在制物料 NG 状态、NG 失败暂停或对账、同源格超阈值冻结。
- 本地 NG 原子性：模拟 `NgReturnItem`、在制物料 NG 状态或 NG evidence 任一写入失败，验证该 NG 不被视为完成，扫码平台和 Session 不推进。
- NG 后续对账：验证 NG 不触发 Session 级 `EXTERNAL_HTTP` 等待，后续 WMS 对账或证据同步不阻塞本地继续分拣。
- `NgReturnItem` 幂等键：同一 Session/同一命令重复上报返回同一 item；不同来源命中同一 active `material_identity_key` 时进入 `NG_MATERIAL_CONFLICT`。
- `NG_MATERIAL_CONFLICT` 落点：验证冲突会创建 RuntimeHold 或切入 `RECONCILING`/`MANUAL_HOLD`，并阻止 Session 完成和源格释放。
- 暂停规则：源端机械臂未取盘、扫码平台已有盘、目标端机械臂执行中、本地 NG 执行中四种分支。
- 插件合同：命令/事件角色映射覆盖源端机械臂、目标端机械臂、本地 NG 机械臂和工作位二次扫码。

完整流程后续测试方向：

- 目标箱状态机：投料、扫码、工作位、退箱、回位、同步。
- WMS 同步重试：成功、超时、超过阈值暂停、后续自动恢复。
- WMS typed port：目标箱格位回写请求/响应模型、endpoint registry、mock WMS 的成功、幂等重放、业务拒绝和版本冲突；若实现 NG 对账接口，也覆盖 NG 对账模型。

P0 测试覆盖图：

```text
SortingInboundContext
  |
  +-- source arm success -> MATERIAL_UNMOUNTED -> current_material open
  |
  +-- scan ok -> SmtBinCellAllocationPolicy -> pending_target_placement
  |      |
  |      +-- target arm success -> MATERIAL_MOUNTED -> current_material closed
  |
  +-- scan mismatch -> NG arm success -> NgReturnItem -> current_material closed
  |
  +-- conflicts/failures -> RuntimeHold / RECONCILING / MANUAL_HOLD
```

### 集成测试方向

P0 集成测试：

- 粗分机向源料格入料时按序记录并校验容量，分拣取盘时只能取顶部一盘。
- 源端机械臂取盘成功后立即通过 `MATERIAL_UNMOUNTED` 完成源格出账；后续扫码一致进目标箱或扫码不一致进本地 NG 都不重复扣减源格。
- 扫码后计算目标格、写入 `pending_target_placement`、下发目标机械臂命令，目标端成功后清理在途落点并关闭在制物料。
- 源端快照不一致料盘本地 NG 后继续分拣，并可在本地 NG evidence 或后续对账材料中追踪到对应 `NgReturnItem`。
- NG 后续对账未完成时，验证 Session 未进入外部 HTTP 等待态，且后续安全动作仍可调度。

完整流程集成测试：

- 完整分拣入库 happy path。
- 五层货架无可用目标箱时请求调度。
- CTU 背篓容量限制。
- 流水线扫码拒绝进入工作位。
- 工作位 `WORKING_BIN_SCAN` 二次扫码确认。
- `WORKING_BIN_SCAN` 使用样例 payload 验证 `device_code`、`location`、目标箱身份和幂等字段解析。
- 已扫码料盘等待目标箱切换后成功放入新目标箱。
- 目标箱满后退箱并回五层货架。
- WMS 同步失败后自动重试并暂停。
- WMS 后续成功确认后自动恢复。

### 沙箱验证方向

- 已补充跨计划 stitching smoke：`tests/integration/workline_runtime/test_cross_plan_sandbox_smoke.py`，
  覆盖 `STOPPED -> START -> READY`、源端取盘、扫码分格、目标放盘、命令前 realtime status guard、
  本地 NG 和 Session completion。该 smoke 不启动完整 runtime orchestrator/effect applier。
- 构造两个 Station 上不同源料格。
- 构造一个目标箱已有兼容格。
- 构造一个目标箱无兼容格但有空格且厚度足够。
- 构造一个目标箱有兼容格但厚度不足。
- 构造厚度缺失、厚度为负、已用厚度超过总厚度、现场厚度与 WMS 快照不一致四类容量边界。
- 构造当前目标箱容量不足但其它目标箱可用，验证扫码平台等待换箱不丢盘。
- 构造目标端机械臂命令失败且落点明确，验证 `pending_target_placement` 的失败 evidence 可用于人工暂停。
- 构造目标端机械臂命令失败且落点未知，验证进入 `RECONCILING`。
- 构造一个扫码结果与源端快照不一致并成功进入本地 NG。
- 构造同一 Session/同一命令重复上报，验证幂等返回同一 `NgReturnItem`。
- 构造不同来源同 6 合 1 身份两盘连续进入 NG，验证进入 `NG_MATERIAL_CONFLICT` 而不是静默聚合。
- 构造本地 NG 位满、NG 命令失败和 NG 落点未知三类异常。
- 构造源端 NG 本地事务部分失败，验证不产生半完成 NG、不释放扫码平台、不完成 Session。
- 构造取盘成功后扫码失败、目标端放盘失败、本地 NG 成功三类分支，验证源格只在取盘成功时扣减一次。
- 构造 NG 后续对账未执行或失败，验证本地已闭环 NG 不阻塞继续分拣，但证据可追溯。
- 构造 WMS 同步超时和后续成功确认。

## 待后续确认

以下问题不阻塞本 SPEC 的业务方向，但实施前需要在接口合同或详细计划中明确：

- WMS 授权快照和目标箱同步接口的具体字段名、版本语义、签名方式、幂等键格式和返回版本字段。
- `WORKING_BIN_SCAN` 的设备侧最终字段名、目标箱身份来源、location 编码和 mock 样例。
- CTU 背篓容量、流水线投料缓存、退箱区容量的配置来源。
- 本地 NG 位的设备编码、角色绑定、满位检测和 NG 料盘后续 WMS 对账流程。
- 同一源格或同一单层货架 mismatch 冻结阈值；首版建议源格连续 2 次冻结、单层货架累计 5 次冻结，并做成可配置参数。

## 后续 TODO

- 拆分完整 CTU/WMS/NG 对账 SPEC：覆盖目标箱回写失败后的自动恢复、NG evidence 消费、Session 结算对账和 WMS 版本冲突人工解除流程。
- 补 runtime orchestrator/effect 层 thin smoke：覆盖 SMT Sorting plugin intent 到 outbox、resource fact
  和 session context 持久化的真实衔接，避免 stitching smoke 漏掉 effect applier 回归。
