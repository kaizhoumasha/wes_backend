# 人工拣料标准件（`manual-picking`）

## 标准件卡片

| 项目 | 当前标准定义 |
| --- | --- |
| 插件身份 | `plugin_key=manual-picking`，版本由 `src/manual_picking/definition.py` 声明，显示名称“人工拣料” |
| 适用工作线 | 仅 `MANUAL` 工作线；不承担人工入库 |
| 已交付主链路 | 五层来源架 → 四点扫码 → WMS/PDA 人工 Bin 作业准入与完成 → `RETURN_BUFFER` 回架 → 任务完成/排空；退料货架直接取料的到位/换面/离场 |
| 业务所有权 | 插件拥有扫码、经过、FIFO、货架循环、退料货架直接取料推进和任务完成条件；宿主拥有可靠 Evidence、WmsConfirmation、DeviceCommand、Transport 和调度 |
| WMS/PDA 边界 | PDA、Cell 选择和人工拣料明细归 WMS；WES 只处理准入决定、Bin 级最终释放结果和退料货架直接取料面级完成事实的应用 |
| 当前未交付 | 真实 WMS、ECS/RCS 和现场业务验收；退料货架入线 Transport 已完成代码与自动化验证 |
| 验收结论 | 代码测试证明 WES 决策与可靠边界，不等于真实 WMS、ECS/RCS 或现场业务验收 |

插件目标覆盖传送带料箱人工拣料和退料货架直接取料两条出库路径；两条路径的代码实施与 §8.1 自动化回归均已闭合。
`manual_bin_processing` 已废弃，本插件不导入、不复用，也不提供兼容入口。

## 双向合同验证（2026-09-19）

以下矩阵以
[`wms-manual-outbound-picking-integration-requirements.md`](../../docs/contracts/wms-manual-outbound-picking-integration-requirements.md)
为人工线差异真源，以
[`wms-outbound-picking-task-integration-requirements.md`](../../docs/contracts/wms-outbound-picking-task-integration-requirements.md)
为复用合同真源。`PASS` 表示代码、测试和边界一致；`PARTIAL` 表示合同已有定义但当前仍有明确未交付项；`OUT OF SCOPE` 表示合同未授权或项目主动放弃。

### A. 插件 → 合同（实现是否有合同归属）

| 插件能力 | 实现证据 | 合同归属 | 结论 |
| --- | --- | --- | --- |
| 声明、资源槽位和静态装配 | `definition.py`、`application/plugin.py`、`tests/test_declaration.py` | 人工合同 §2.1；插件顶层设计 | `PASS` |
| prepare、plan_delta、取消和 Transport 可靠接收 | `prepare_policy.py`、`handlers/picking_task_plan_applied.py`、`application/transport_outcome.py` | 人工合同 §2.1；出库合同任务/Transport章节 | `PASS` |
| `target_rack` → `TRANSFER_RACK` + F01 | `handlers/picking_task_plan_applied.py` 产出 F01 intent；`picking_task_plan_activation.py` 派发到 `TARGET_RACK_IN_STEP` | 人工合同 §3.1；出库合同 §9.1 | `PASS` |
| `added_bin_source_racks` → `FIVE_RACK` + CTU01 | `_pending_bin_racks` 拉取；activation 派发到 `BIN_SOURCE_RACK_IN_STEP`；`batch_driver` 维护 CTU01 窗口 | 出库合同 §9.1、§9.2.1 | `PASS` |
| `added_direct_picks` → `RETURN_RACK` + F01 入线 Transport | SDK `PickingTaskPlanAppliedFact.pending_return_racks`；`picking_task_plan_activation._pending_return_racks` + `RETURN_RACK_IN_STEP` 常量；插件 handler 产出 F01 intent；下游 `_advance_return_rack` 自然消费 `PositionProjection` | 人工合同 §3.5、§5.5；合同 §3.1 入线 Transport 形态 | `PASS`（已闭合"退料货架入线 Transport 缺口"，对应 `TODOS.md` 已删除条目） |
| CTU01 窗口、同架换面、CTU03 离场 | `application/batch_driver.py`、`batch_repository.py`、`tests/test_batch_*`、`tests/test_source_progression.py` | 出库合同 §9.1、§9.2.1、§9.4 | `PASS` |
| SCAN1～SCAN4、FIFO、NG 分流 | `handlers/scan*.py`、`application/scan_flow.py`、`tests/test_scan_handlers.py`、`tests/test_scan_flow.py` | 人工合同 §3.1～§3.4 | `PARTIAL`（C4/C5：现有行为与局部测试一致，不替代现场物理验收） |
| `MANUAL_PICK_NG` 持久化 | `passage_model.reason_code VARCHAR(64)`；`scan_flow._apply_completed` 在 `result=NG` 时写入 `MANUAL_PICK_NG`；新 migration `20260919_0409_1d3045ea8e62_add_manual_picking_passage_reason_code.py` | 人工合同 §5.2 | `PASS` |
| per-Bin 终态唯一索引 | `passage_model.py:27-34` `ux_manual_picking_passages_wms_terminal`：`(task_id, bin_code) WHERE wms_result IS NOT NULL` 部分唯一索引 | 人工合同 §9.1 | `PASS` |
| point2 `WORK_REQUIRED/NO_WORK/WAIT` 与 Bin 完成释放 | 宿主 `src/app/wms_adapter/outbound_picking/manual_bin_*` + 插件 `scan_flow.py`；FastTests 覆盖；真实 worker owner 为 `tests/test_business_loop.py`，统一入口为 `scripts/run-integration-tests.sh` | 人工合同 §5.1～§5.4 | `PARTIAL`（合同已批准；人工 Bin `NORMAL/NG` 纵向 owner 待补，自动化验收依赖 `RUN_WORKLINE_INTEGRATION=1`） |
| 任务完成、跨任务 `RETURN_BUFFER` FIFO 和 drain | `completion_flow.py`、`drain_flow.py`、`tests/test_completion_flow.py`、`tests/test_drain_flow.py`；集成 `test_rack_cycle_postgresql.py` 覆盖 PostgreSQL 路径 | 人工合同 §2.1、§5.6；出库合同 §9.2.3 | `PARTIAL`（C6：行为与局部测试一致；真实 PG 由集成测试契约承担） |
| 退料货架直接取料 | 宿主 `src/app/wms_adapter/outbound_picking/manual_rack_direct_pick_*` + `manual_rack_direct_pick_completed.py`；插件通过 `completion_repository` 消费面级完成事实推进 `_advance_return_rack` | 人工合同 §3.5、§5.5 | `PARTIAL`（代码与 §8.1 自动化 owner 落地；现场物理验收仍 `NOT ACCEPTED`） |
| 任务完成触发 drain | `batch_driver.py`、`completion_flow.py`、`drain_flow.py`、`tests/test_drain_flow.py` | 人工合同 §5.6；出库合同 §9.2.3 | `PASS` |
| 停线触发全量排空 | `WorkLineConfigurationService._trigger_plugin_drain` 在 `deactivate()` 前调用 `drain_flow.trigger_full_drain_in_session`；端口 `InstalledWorkLinePlugin.drain_trigger` | 人工合同 §5.6；出库合同 §9.2.3 | `PASS` |
| 插件切换触发 drain | 项目决定不再引入插件切换，不需要该分支 | 人工合同 §5.6；出库合同 §9.2.3 | `OUT OF SCOPE` |

### B. 合同 → 插件（合同要求是否有实现承接）

| 合同要求 | 插件承接 | 结论 |
| --- | --- | --- |
| 复用 PickingTask、plan_delta、取消、arrival、inbound/return batch、departure、completion_confirm | 宿主 operation + 插件 typed facade/业务 driver；插件未复制 HTTP、Evidence 或重试 | `PASS` |
| point2 只提交实际 Bin、固定 `task_id`，不查询 Cell/PDA | `scan_flow.py` 构造 admission intent；WMS completion 只按 `task_id + bin_code` 绑定 | `PASS`（实现闭合；自动化 owner 由 `test_manual_bin_completed_postgresql.py`、`test_business_loop.py` 等覆盖） |
| `work_completed` 先可靠接收，应用时才绑定当前 point2 等待 | `manual_bin_completed_event_handler.py` + `scan_flow.py`；早到/冲突进入 `RECONCILING` | `PASS`（实现闭合；FastTests + 既有 `test_manual_bin_completed_postgresql.py` 覆盖幂等与冲突） |
| 点3不能由 FIFO 猜测正常授权，点4须等 ECS `SUCCESS` 才入队 | `scan3.py`、`scan4.py`、`test_scan_flow.py`、`test_scan_handlers.py` | `PARTIAL`（C4/C5：现有行为与局部测试一致；不替代 ECS 设备验收） |
| 来源架按精确 rack/face、原 Transport 和 READY evidence 推进 | `batch_repository.py`、`transport_outcome.py`、`source_progression.py` | `PASS` |
| 任务完成先原子准备下一任务，无下一任务才 drain | `batch_driver.py`、`completion_flow.py`、`test_completion_flow.py`、`test_drain_flow.py`；集成测试 `test_rack_cycle_postgresql.py` 覆盖 PostgreSQL 路径 | `PARTIAL`（C6：行为闭合；真实 PG 验证需 `RUN_WORKLINE_INTEGRATION=1`） |
| 直接取料完成通知后才允许退料架换面/离场 | 宿主 `manual_rack_direct_pick_*` + `manual_rack_direct_pick_completed.py`；插件 `completion_repository` 推进 `_advance_return_rack` | `PARTIAL`（代码 + 自动化 owner 落地；现场物理验收仍 `NOT ACCEPTED`） |
| 集成测试契约（`RUN_WORKLINE_INTEGRATION=1` + 本地 PG/Redis/Celery） | `scripts/run-integration-tests.sh` + `tests/integration/conftest.py` 的 `integration_guard` | `PASS`（契约统一；本机/CI 同入口） |
| 现场 worker、ECS/RCS 与设备验收 | FastTests + 集成测试 + `test_business_loop.py` 真实 worker 路径（依赖 PG/Redis/Celery） | `PARTIAL`（测试不替代 ECS/设备/现场验收） |

双向验证的收敛结论（2026-09-19 重新对账）：

- `target_rack` / `added_bin_source_racks` / `added_direct_picks` 三类货架位在 `plan_delta` 落库后均由 `PickingTaskPlanActivationService` 派发到对应 step（F01 for `TRANSFER_RACK` & `RETURN_RACK`，CTU01 for `FIVE_RACK`），不引入新窗口表、不维护占用计数器，capacity 解释完全交给 ECS/RCS。
- `MANUAL_PICK_NG` 与 per-Bin 终态唯一索引均已落地（迁移 `20260919_0409_1d3045ea8e62` + 既有 `ux_manual_picking_passages_wms_terminal`）；合同 §6 现场物理验收仍 `NOT ACCEPTED`。
- 集成测试通过 `scripts/run-integration-tests.sh` + `RUN_WORKLINE_INTEGRATION=1` 统一入口接入，不依赖旧的"本机通过代表现场"假设。

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
`added_direct_picks` 对应的退料货架进场由宿主创建 `RETURN_RACK_IN_STEP` 的 F01 可靠 Transport，目标位置来自冻结的 `RETURN_RACK` 绑定。
宿主通过静态 Celery 任务只扫描精确匹配的活动工作线，调用该 handler 并在同一事务内创建可靠 TransportTask；插件未启用时不执行其业务决策。

Transport 结果按原 binding 及对应计划或 drain READY Evidence 校验后，由插件适配器保存为 `APPLIED` 的
`TRANSPORT_RESULT` Evidence；只有该事务提交成功，宿主才推进结果发布游标。插件消费时再次核对原
Transport 身份和成功终点；`UNKNOWN` 只留证，不推定货架到位或解除任务占用。
CTU01 在 `PENDING | ACCEPTED | RECONCILING | SUCCEEDED | FAILED` 占窗，`REJECTED` 不占窗；CTU02 不释放名额。
同一 WorkLine、同一货架且晚于该进场 binding 的 CTU03 接纳即释放名额；不要求离场与进场使用相同 Evidence。
已接纳后的失败或对账不重新占窗，提交前 delivery-unknown/conflict 仍占窗。该释放只允许补充其他货架；同架复用仍等待
CTU03 `SUCCEEDED`、成功成员和明确 `RACK_POSITION`，实际库位不要求等于请求中的动态 `ZONE`。
当前架按原 CTU01/CTU02 成功、成员结果和绑定工作位的精确 rack/face 投影确定，不按计划顺序选取；CTU02 成功表示旋转后已返回工作位。

`feed_complete` 只要求当前面最终冻结清单的全部 inbound 分段 Transport 与成员权威成功、结果已发布且终点为绑定 HANDOFF_POSITION；
无分段的最终 `RACK_FACE_DONE` 同样成立。它不等待 SCAN、业务完成或后续回架。已有可靠义务闭合后，立即创建同架下一面 CTU02，
或在该架所有面投料完成时请求 `outbound.rack.departure_decide@v1`，READY 后按 WMS 原样 destination 创建 CTU03。投料未完成时只在分段间隙最多尝试一次机会式 `return_batch`，
`NO_BATCH` 即闭合本次回架决定，不重试、不阻断下一段投料；当前货架继续 CTU02/CTU03，剩余料箱最终由 drain 请求空载货架承接。中间分段继续等待前段 SCAN1 清空入口；末段成功后的换面/换架不等待 SCAN1。

RETURN_BUFFER 是 WorkLine 级跨任务 FIFO，正常回架使用当前权威 rack/face，不要求回原货架或原面。
PickingTask 完成后，同一 WorkLine 锁内先原子准备/领取下一任务；已有绑定的 `PREPARING | EXECUTING` 或成功 claim 的任务优先承接 FIFO。
只有无可准备任务且 FIFO 非空时才创建 WorkLine-owned `workline.return_buffer.drain_rack_decide@v1`。请求只携带
`workline_code + required_slot_count`；任务完成、停线或插件切换原因留在 WES 本地。READY 返回无序 `racks[]` reservation，各 rack 内
`rack_face[]` 有序；WAIT 到期以新 identity 和当前数量重求值。已创建 drain 链不被后来任务取消：在 CTU01 窗口内提交各 reservation
货架，任一货架精确权威到位后即可独立使用普通 `return_batch`，不等待其它 AGV；同架按面顺序复用 CTU02，保留 FIFO 及未闭合义务直到
排空，再请求 `outbound.rack.departure_decide@v1` 并按 READY destination 创建 CTU03。
完整 wire 见[出库合同 §9.2.3](../../docs/contracts/wms-outbound-picking-task-integration-requirements.md#923-return-buffer-drain)。
没有新增 Epoch、兼容路径、窗口表、缓存计数器、业务表或字段；仅为既有 `wms_confirmations`
增加 `workline_id + operation + operation_id` 查询索引。停线/插件切换触发仍留在 TODO。

WMS 可通过 `outbound.picking_task.cancel@v1` 取消 `QUEUED | PREPARING` 的整单任务，或在 `EXECUTING` 中按
`PLAN_MEMBERS` 撤销仍未执行的计划成员。取消请求与任务/成员边界在同一事务内保存 Evidence；已发出的 prepare 不撤销，迟到结果仍按原身份留证，取消后的新计划与调度入口保持 fail closed。

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

### 本地集成测试契约

集成测试入口统一由 `scripts/run-integration-tests.sh` 提供，契约如下：

- `RUN_WORKLINE_INTEGRATION=1` 必须显式开启；脚本默认拒绝运行，避免误连生产/共享 dev 库。
- `INTEGRATION_DATABASE_URL` 必须指向 `localhost`/`127.0.0.1`/`::1`/`db` host，且数据库名以 `test_` 开头或 `_test` 结尾；脚本在启动 pytest 前会用 `alembic current` 主动认证，失败时立即报错而非延迟到 pytest。
- 未设置时，`INTEGRATION_REDIS_URL` 默认使用 Redis DB 0，`CELERY_BROKER_URL` 使用 DB 1，`CELERY_RESULT_BACKEND` 使用 DB 2；若设置 `REDIS_PASSWORD` 和 `REDIS_PORT`，脚本会沿用这两个值。
- 用户需自行执行 `ALEMBIC_DATABASE_URL=$INTEGRATION_DATABASE_URL uv run alembic upgrade head` 保证 schema 与代码同步；脚本不自动执行，避免误改。

```bash
# 在 docker compose up -d db redis 之后
export RUN_WORKLINE_INTEGRATION=1
export INTEGRATION_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/test_manual_picking
ALEMBIC_DATABASE_URL=$INTEGRATION_DATABASE_URL uv run alembic upgrade head
./scripts/run-integration-tests.sh workline_plugins/manual-picking/tests/
```

未设置 `RUN_WORKLINE_INTEGRATION` 时，集成测试在 conftest 的 `integration_guard` fixture 处 SKIPPED，FAST 套件不受影响。
完整业务顺序与联调边界见[人工出库拣料交互要求](../../docs/contracts/wms-manual-outbound-picking-integration-requirements.md)
和[人工出库货架搬运规则](../../docs/integration/manual-outbound-rack-transport.md)。
