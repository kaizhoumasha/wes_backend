# 人工拣料插件

插件标识 `manual-picking`，显示名称“人工拣料”，仅支持 `MANUAL` 工作线。
负责传送带料箱人工拣料和退料货架直接取料两条出库路径，不承担人工入库。

当前代码已包含声明与装配、PickingTask prepare 与计划资源进场决策、原 Transport 结果接收、
四点扫码、WMS 料箱准入与完成、进箱/退箱批次、五层来源架窗口与换面/离场、PickingTask 完成及任务完成后的 RETURN_BUFFER 排空。
退料货架直接取料与现场物理验收仍未完成；本机 Mock 和单元测试不代表现场验收。
`manual_bin_processing` 已废弃，本插件不导入、不复用，也不提供兼容入口。

## 声明与装配

`src/manual_picking/definition.py` 是插件身份、版本和资源需求的唯一来源，包版本从声明提取。
设备角色为 `SCAN1`、`SCAN2`、`SCAN3`、`SCAN4`；工作位为 `FIVE_RACK`、`RETURN_RACK`、
`TRANSFER_RACK`、`INLET`、`OUTLET`，每个插槽绑定一个本线实际资源。

五层货架区对 WES 只有一个绑定工作位。绑定 FIVE_LAYER/FIVE_RACK 点位的 `workline_positions.capacity` 是
CTU01 准入义务窗口，按配置 `position_code` 读取；运输目标使用冻结绑定的实际 `location_id`。同一物理工作位最多有一个权威当前货架。
RCS 负责 AGV 排队、互斥和自主进位；WES 不保存排队位或队尾状态。实际工作位编码与货架编号是不同身份。

后续业务直接引用具名对象，例如 `from manual_picking.definition import SCAN2, FIVE_RACK`；
需要字符串的现有端口使用 `SCAN2.role_key` 或 `FIVE_RACK.slot_key`。

## 安装与验证

在后端根目录运行 `uv sync --dev --extra manual-picking` 安装本插件。镜像构建 extra 和部署启用键均为
`manual-picking`。本机开发 Compose 只安装并启用人工拣料；依赖变化后通过 `scripts/dev-env.sh up` 重建。

工作线页面可选择本插件、绑定资源并保存草稿或完整配置。完整装配通过归属、位置类型和设备实时准入后可 START；
没有业务启动计划时 `flow_mode` 为 `null`。基础准入使用统一设备合同，状态时效与命令超时由宿主
`src/core/conf.py` 的 `WORKLINE_DEVICE_STATUS_MAX_AGE_MS`、`WORKLINE_DEVICE_COMMAND_TIMEOUT_MS` 管理，START 时冻结。

`src/manual_picking/application/plugin.py` 显式构造 prepare 策略、计划 handler 和 Transport 结果适配器，不扫描模块，
构造时不访问数据库、HTTP 或 Celery。策略只为活动、AUTO、`manual-picking` 人工线选择 MANUAL PickingTask；
设备与位置绑定完整性由 WorkLine START 准入检查，prepare 不重复校验。
设备实时状态和历史位置投影不作为新 prepare 的准入依据，物理接纳由 ECS/RCS 判断。
任务领取、并发锁、状态迁移和可靠 `outbound.picking_task.prepare@v1` 义务仍由宿主统一负责。

`PickingTaskPlanAppliedHandler` 只消费宿主从已提交计划和位置绑定构造的 typed fact，不解析 WMS JSON。
它只把转运架映射为 `F01 → TRANSFER_RACK`；`ManualPickingBatchDriver` 是来源架 CTU01/CTU02/CTU03 的唯一 owner，
按 `plan_revision / id` 稳定选取窗口内来源架，每架只创建一次 CTU01，多面仍保留在同一货架上下文。宿主校验返回意图是冻结 fact 的合法子集。
`added_direct_picks` 对应的退料货架进场尚无已批准运输映射，本轮不猜测其模板或执行时机。
宿主通过静态 Celery 任务只扫描精确匹配的活动工作线，调用该 handler 并在同一事务内创建可靠 TransportTask；插件未启用时不执行其业务决策。

Transport 结果按原 binding 及对应计划或 drain READY Evidence 校验后，由插件适配器保存为 `APPLIED` 的
`TRANSPORT_RESULT` Evidence；只有该事务提交成功，宿主才推进结果发布游标。插件消费时再次核对原
Transport 身份和成功终点；`UNKNOWN` 只留证，不推定货架到位或解除任务占用。
CTU01 在 `PENDING | ACCEPTED | RECONCILING | SUCCEEDED | FAILED` 占窗，`REJECTED` 不占窗；CTU02 不释放名额。
只有匹配同一 WorkLine、货架和原进场 Evidence 的 CTU03 接纳才释放：持久化 `ACCEPTED | SUCCEEDED | FAILED`，
或带非空 `result_deadline_at` 的 `RECONCILING`；接纳前 delivery-unknown/conflict 仍占窗。接纳仅允许补窗，不证明物理离场。
当前架按原 CTU01/CTU02 成功、成员结果和绑定工作位的精确 rack/face 投影确定，不按计划顺序选取；CTU02 成功表示旋转后已返回工作位。

`feed_complete` 只要求当前面最终冻结清单的全部 inbound 分段 Transport 与成员权威成功、结果已发布且终点为绑定 HANDOFF_POSITION；
无分段的最终 `RACK_FACE_DONE` 同样成立。它不等待 SCAN、业务完成或后续回架。已有可靠义务闭合后，立即创建同架下一面 CTU02，
或在该架所有面投料完成时直接创建 CTU03 到固定 `ZONE WH01`。投料未完成时只在分段间隙最多尝试一次机会式 `return_batch`，
`NO_BATCH` 不阻断下一段投料。中间分段继续等待前段 SCAN1 清空入口；末段成功后的换面/换架不等待 SCAN1。

RETURN_BUFFER 是 WorkLine 级跨任务 FIFO，正常回架使用当前权威 rack/face，不要求回原货架或原面。
PickingTask 完成后，同一 WorkLine 锁内先原子准备/领取下一任务；已有绑定的 `PREPARING | EXECUTING` 或成功 claim 的任务优先承接 FIFO。
只有无可准备任务且 FIFO 非空时才创建 WorkLine-owned `workline.return_buffer.drain_rack_decide@v1`，原因是 `PICKING_TASK_COMPLETED`。
请求冻结非空 FIFO 前缀；READY 选择 rack/face 并保留该前缀容量，WAIT 到期以新 identity 和直接 `previous_operation_id` 重求值。
已创建 drain 链不被后来任务取消：共享窗口创建 CTU01，等待精确权威到位后连续使用普通 `return_batch`，保留 FIFO 及未闭合义务直到排空，再创建 CTU03。
完整 wire 见[出库合同 §9.2.3](../../docs/contracts/wms-outbound-picking-task-integration-requirements.md#923-return-buffer-drain)。
没有新增 Epoch、兼容路径、窗口表、缓存计数器、schema 或 migration；停线/插件切换触发仍留在 TODO。

SCAN1 正常箱码须匹配计划内来源架面，并具备当前转运架和 Bin 入口的权威位置投影及当前 Bin 原入站
`SUCCEEDED` Transport；不要求来源架随后仍保持原位置投影。结果未到时保留原扫码 Evidence 等待，确定失败或位置未知进入对账。
批次调度和扫码流程已有本地实现，但不能把代码测试当作完整出库或真实设备验收。

部署通过 `InstalledWorkLinePlugin.picking_task_prepare_policy` 显式关联该能力。宿主静态注册通用 Celery 任务，只扫描精确版本匹配的
活动工作线；插件未安装或没有活动工作线时不执行插件策略。新任务入站与工作线 START 在事务提交后主动唤醒；
任务完成时同事务准备/排空新建的 WmsConfirmation 由既有 Beat 扫描派发，Beat 同时承接丢失唤醒恢复。
已接入的 SCAN1—SCAN4 设备事件按冻结插件消费；未接入事件仍按声明态留证，不自动重放历史观察事件。
急停、已发命令和已关联业务执行的结果继续走已有可靠处理。

在后端根目录运行 `uv run --extra manual-picking pytest workline_plugins/manual-picking/tests -m "not integration" -q -o addopts=''` 验证插件 FAST。
`test_rack_cycle_postgresql.py` 与 `test_business_loop.py` 仅在独占 PostgreSQL/Redis/worker 环境中显式运行，不以 skipped 或本地通过代表现场验收。
基础声明校验与绑定规则由 SDK / 宿主测试承接，不复制到插件测试。
完整业务顺序与联调边界见[人工出库拣料交互要求](../../docs/contracts/wms-manual-outbound-picking-integration-requirements.md)
和[人工出库货架搬运规则](../../docs/integration/manual-outbound-rack-transport.md)。
