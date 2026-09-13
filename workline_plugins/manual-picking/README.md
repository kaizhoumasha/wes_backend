# 人工拣料插件

插件标识 `manual-picking`，显示名称“人工拣料”，仅支持 `MANUAL` 工作线。
负责传送带料箱人工拣料和退料货架直接取料两条出库路径，不承担人工入库。

当前交付声明、工作线装配、基础启用、事件观察、PickingTask prepare 纯业务策略、
`PickingTaskPlanAppliedHandler` 的纯计划资源进场决策，以及原 Transport 结果的可靠接收。
`manual_bin_processing` 已废弃，本插件不导入、不复用，也不提供兼容入口。

## 声明与装配

`src/manual_picking/definition.py` 是插件身份、版本和资源需求的唯一来源，包版本从声明提取。
设备角色为 `SCAN1`、`SCAN2`、`SCAN3`、`SCAN4`；工作位为 `FIVE_RACK`、`RETURN_RACK`、
`TRANSFER_RACK`、`INLET`、`OUTLET`，每个插槽绑定一个本线实际资源。

五层货架区对 WES 只有一个绑定工作位。多个进场 CTU01 可以指向同一位置，由 RCS 排队与互斥；
插件声明不保存 RCS 容量、现场编码或排队位清单。实际工作位编码与货架编号是不同身份。

后续业务直接引用具名对象，例如 `from manual_picking.definition import SCAN2, FIVE_RACK`；
需要字符串的现有端口使用 `SCAN2.role_key` 或 `FIVE_RACK.slot_key`。

## 安装与验证

在后端根目录运行 `uv sync --dev --extra manual-picking` 安装本插件；需要同时保留粗分插件时增加
`--extra rough-sorter`。镜像构建 extra 为 `manual-picking`，部署启用键为 `manual-picking`。
本机开发 Compose 已同时安装并启用粗分和人工拣料声明；依赖变化后通过 `scripts/dev-env.sh up` 重建。

工作线页面可选择本插件、绑定资源并保存草稿或完整配置。完整装配通过归属、位置类型和设备实时准入后可 START；
没有业务启动计划时 `flow_mode` 为 `null`。基础准入使用统一设备合同，状态时效与命令超时由宿主
`src/core/conf.py` 的 `WORKLINE_DEVICE_STATUS_MAX_AGE_MS`、`WORKLINE_DEVICE_COMMAND_TIMEOUT_MS` 管理，START 时冻结。

`src/manual_picking/plugin.py` 显式构造 `ManualPickingPreparePolicy` 和当前 handler 集合，不扫描模块，也不访问数据库、HTTP 或 Celery。
策略只为活动、AUTO、`manual-picking` 人工线选择 MANUAL PickingTask，并核对静态设备角色与位置角色完整绑定；
设备实时状态和历史位置投影不作为新 prepare 的准入依据，物理接纳由 ECS/RCS 判断。
任务领取、并发锁、状态迁移和可靠 `outbound.picking_task.prepare@v1` 义务仍由宿主统一负责。

`PickingTaskPlanAppliedHandler` 只消费宿主从已提交计划和位置绑定构造的 typed fact，不解析 WMS JSON。
它把转运架映射为 `F01 → TRANSFER_RACK`，把五层来源架按物理 `rack_id` 一架一次映射为
`CTU01 → FIVE_RACK`；多面保留在同一货架上下文，每架只创建一次进场任务，不用 WES 本地容量延期其他货架。
`added_direct_picks` 对应的退料货架进场尚无已批准运输映射，本轮不猜测其模板或执行时机。
宿主通过静态 Celery 任务只扫描精确匹配的活动工作线，调用该 handler 并在同一事务内创建可靠 TransportTask；插件未启用时不执行其业务决策。

Transport 结果按原 binding 和计划 Evidence 校验后，由插件适配器保存为 `PENDING` 的
`TRANSPORT_RESULT` Evidence；只有该事务提交成功，宿主才推进结果发布游标。重复结果沿用原身份，
`UNKNOWN` 只留证，不推定货架到位或解除任务占用。到位判定及后续业务 handler 尚未实现。

部署通过 `InstalledWorkLinePlugin.picking_task_prepare_policy` 显式关联该能力。宿主静态注册通用 Celery 任务，只扫描精确版本匹配的
活动工作线；插件未安装或没有活动工作线时不执行插件策略。新任务入站与工作线 START 在事务提交后主动唤醒，Beat 仅负责丢失唤醒恢复。
设备事件仍按声明态可靠留证并标记为 `IGNORED`，不会自动重放历史观察事件。SCAN1—SCAN4 设备 handler 均未实现。
急停、已发命令和已关联业务执行的结果继续走已有可靠处理。

在后端根目录运行 `uv run --extra manual-picking pytest workline_plugins/manual-picking/tests -q` 验证插件装配。
基础声明校验与绑定规则由 SDK / 宿主测试承接，不复制到插件测试。
