# 人工拣料四点扫码实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 使启用 `manual-picking` 的工作线可靠处理四点 `data.bin_code` 扫码、人工拣料决定与 SCAN4 物理成功后的退箱 FIFO。

**Architecture:** 宿主复用现有 Evidence、DeviceCommand、WmsConfirmation 和静态 Celery worker，只补无物料执行身份的工作线插件消费端口。插件用四个纯点位 handler 决策，由插件应用层持久化本次经过并通过宿主端口冻结命令与 WMS 意图。基础实现和测试不导入具体插件。

**Tech Stack:** Python 3.13、FastAPI、SQLModel/SQLAlchemy、PostgreSQL、Celery、pytest、Alembic。

**Spec:** `docs/superpowers/specs/2026-09-13-manual-picking-scan-flow-design.md`

## Global Constraints

- ECS 扫码只读 `SCAN_COMPLETED.data.bin_code`；6 合一是物料编码；不读 `barcode`，不做兼容路径。实际值含方向后缀，WMS 只接收去后缀的 `bin_code`。
- SCAN1/3/4 接受 `-B`，SCAN2 接受 `-A`；四点规则各自拥有，不做全局策略表。
- SCAN1 NG `MOVE_RIGHT`；SCAN2 本地 NG `MOVE_FORWARD`；SCAN3 无确定正常授权则 `MOVE_LEFT` 且不停箱；SCAN4 不可读或身份不确定时零命令、零入队。
- SCAN4 命令匹配 ECS `SUCCESS` 后才进入 `RETURN_BUFFER`；严格 FIFO 不越过未闭合队头。设备 ACK 与 WMS 接收确认都不是物理完成。
- 宿主/SDK 不导入具体插件；插件测试只在 `workline_plugins/manual-picking/tests/`；核心测试只用 fake 插件。未获授权不 Push、Merge 或 Deploy。

## 变更面与文件职责

| 所有者 | 文件 | 职责 |
| --- | --- | --- |
| 基础 | `src/app/execution/services/fact_processor.py`、`src/app/execution/plugin_binding.py`、`src/app/workline/installed_plugin.py` | 无物料执行身份的 Evidence 精确交给当前工作线已冻结的插件；其余现有 Fact 路径不变。 |
| 基础 | `src/app/device/services/device_evidence_service.py` | 保持结果 Evidence 的原命令关联并唤醒现有执行 worker，不新增业务判断或第二个 worker。 |
| 基础测试 | `tests/runtime/execution/test_fact_processor.py`、`tests/runtime/execution/test_plugin_binding.py`、`tests/runtime/device_command/test_evidence_service.py` | fake consumer 验证路由、原身份重放及失败保留。 |
| 插件 | `workline_plugins/manual-picking/src/manual_picking/handlers/scan1.py`～`scan4.py`、`application/plugin.py` | 四个纯点位决定及显式装配。 |
| 插件 | `workline_plugins/manual-picking/src/manual_picking/application/scan_flow.py`、`application/passage_repository.py`、`application/passage_model.py` | 事务内关联本次经过、保存本线状态、调用既有宿主端口。 |
| 插件测试 | `workline_plugins/manual-picking/tests/test_scan_handlers.py`、`test_scan_flow.py`、`test_return_buffer.py` | 点位规则、WMS 完成与设备结果应用、退箱 FIFO。 |
| WMS 结果 | `src/app/workline/plugin_routing.py`、`src/app/wms_integration/outbound_picking/services/manual_bin_completed.py`、`src/wes_plugin_sdk/src/wes_plugin_sdk/wms_types.py`、`src/app/wms_adapter/outbound_picking/manual_bin_typed.py` | 复用 WorkLine owner、构造 typed 准入结果，并把 WMS 完成 Evidence 绑定到冻结任务工作线。 |
| 迁移与合同 | Alembic 生成的新 revision、`deployment/plugin_models.py`、`migrations/env.py`、`docs/architecture/heavy-test-impact.toml`、`docs/contracts/wms-manual-outbound-picking-integration-requirements.md` | 插件业务表、部署侧条件模型装配、精确 HEAVY 映射和现场流程真源。 |

执行前按 AGENTS.md §3.4 冻结上述生产符号、调用点、fixture/间接测试、HEAVY、migration 与 dirty 指纹；对将修改的生产符号批量运行 GitNexus upstream impact。若出现清单外 HIGH/CRITICAL 影响，先报告确认。`DeviceCommandRequest` 已支持 `material_execution_id=None`，`create_command_in_session` 已在调用方事务中做身份去重；不新造命令服务。出站方向命令按当前统一协议传 `device_code/task_type/params`，本轮 `params={}` 只作为本地开发合同；现场发命令前必须用设备附录或真实受控联调确认 `params` 可空及 ECS 对 `SUCCESS` 的物理含义，未确认不开放自动实线运行。

### Task 1: 宿主无物料身份的插件消费接缝

**Files:** 修改 `src/app/execution/services/fact_processor.py`、`src/app/execution/plugin_binding.py`、`src/app/workline/installed_plugin.py`、`src/app/device/services/device_evidence_service.py`；优化 `tests/runtime/execution/test_fact_processor.py`、`test_plugin_binding.py`、`tests/runtime/device_command/test_evidence_service.py`；同步 HEAVY mapping。

**Interfaces:** 插件暴露 `apply_in_session(db, evidence_id: int, *, workline_id: int) -> BusinessEvidenceDisposition`；`BusinessEvidenceDisposition` 只含 `APPLIED | IGNORED | RECONCILING`，由宿主统一更新 Evidence 状态。此入口仅接 `DEVICE_EVENT/DEVICE_RESULT` 和已由固定 operation route 验证的 WMS 完成 Evidence。分派以 Evidence/原 DeviceCommand 的 `workline_id` 和该工作线冻结的 plugin key/version 选择插件；工作线现有未闭合负载门禁加插件 `business_blocker`，未闭合经过不允许更换插件或版本。冻结版本不可用时保留原状态对账，不按当前默认插件重路由。插件应用层按 ID 从宿主读取 ECS typed 事件/命令结果或 operation 专属 typed WMS 事实，不接收原始 WMS JSON；可在同一事务调用既有 DeviceCommand/WMS Confirmation 端口。

- [ ] RED：在现有核心测试里用不含人工业务规则的 fake consumer 证明精确插件版本路由、无 consumer 时保留 Evidence、重放不重复调用、结果按原命令 WorkLine 路由、冻结版本不可用时不误投、失败保持原身份与重试状态；用原测试证明 `MaterialExecution` Fact 路径不变。
- [ ] 运行 `uv run pytest tests/runtime/execution/test_fact_processor.py tests/runtime/execution/test_plugin_binding.py -q`，确认新增断言先失败。
- [ ] DEV：实现单一消费分支与静态装配端口；不要修改 `FactReference` 去容纳假 `MaterialExecution`，不要在宿主判断 `SCAN1` 或 `manual-picking`。
- [ ] GREEN：重跑上述测试；再跑 `uv run pytest tests/runtime/device_command/test_evidence_service.py -q`，确认基础接收、结果与唤醒未回退。
- [ ] Review：核对宿主、SDK、部署基础测试不导入插件；核对未启用线不触发新业务而迟到结果仍保存。

### Task 2: 本次经过的最小持久状态与四个纯 handler

**Files:** 新建插件 `handlers/scan1.py`～`scan4.py`、`application/passage_model.py`、`application/passage_repository.py`；修改 `handlers/__init__.py` 和 `application/plugin.py`，装配当前经过 `business_blocker`；新建插件 `tests/test_scan_handlers.py`；用 `uv run alembic revision -m "添加人工拣料经过状态"` 生成随机 revision，再编辑生成文件；新增 `deployment/plugin_models.py`，由 `migrations/env.py` 调用以条件加载已安装插件模型，不让 `src` 导入插件。

**Interfaces:** 四个 handler 接收不可变 `ScanFact(role, evidence_id, raw_bin_code, passage, task_id)` 并返回 `ScanDecision(route, normal_bin_code, ng_reason)`；`passage` 只表示本次 SCAN1 Evidence 建立的经过。业务表 `manual_picking_passages` 以 `scan1_evidence_id` 唯一，保存 `workline_id/task_id/bin_code/scan1_evidence_id/scan2_evidence_id/scan3_evidence_id/scan4_evidence_id/disposition/wms_result/wms_completed_at/move_command_code/return_state`；`wms_result IS NOT NULL` 时用 `(task_id, bin_code)` 部分唯一索引保证 WMS 单终态。退箱顺序由 SCAN4 Evidence 的 `received_at,id` 冻结，不增第二张队列表。SCAN3/4 只匹配唯一未闭合经过，不依据 SCAN1→2 FIFO 推断。`deployment.plugin_models.load_plugin_models(settings.ENABLED_WORKLINE_PLUGINS)` 只对显式启用的插件导入模型，基础独立部署不依赖插件包。

- [ ] RED：插件测试逐点覆盖 `-B/-C/-B/-B`、其他后缀、不可读、非法基础箱码、SCAN3 双来源交错与未知走左、SCAN4 未知保持；断言 handler 无 I/O。
- [ ] 运行 `uv run pytest workline_plugins/manual-picking/tests/test_scan_handlers.py -q`，确认新用例先失败。
- [ ] DEV：只实现四个小 handler、一个通过状态对象和最窄持久查询；同一后缀值不合并为全局验证策略。迁移以 DB 唯一约束保护经过身份与 WMS 单终态。
- [ ] GREEN：重跑插件测试；在干净临时 PostgreSQL 做 base→head 迁移及 schema 核查，确认插件未安装时基础仍可迁移/启动。
- [ ] Review：检查模型不进入 SDK，`migrations/env.py` 不直接导入插件，未闭合经过通过插件 blocker 阻止工作线改装，迁移 revision 是 Alembic 随机生成且已列入 HEAVY 映射。

### Task 3: SCAN1、SCAN2 与 WMS 人工作业

**Files:** 新建 `workline_plugins/manual-picking/src/manual_picking/application/scan_flow.py`；修改 `application/plugin.py`、`src/app/workline/plugin_routing.py`、`src/app/wms_integration/outbound_picking/services/manual_bin_completed.py`、`src/wes_plugin_sdk/src/wes_plugin_sdk/wms_types.py`、`src/app/wms_adapter/outbound_picking/manual_bin_typed.py`；新建插件 `tests/test_scan_flow.py`；优化 `tests/workline/test_plugin_routing.py` 与对应 WMS Adapter 合同测试。

**Interfaces:** `ScanFlow.apply_in_session` 复用 Task 1 消费接缝。SCAN1 以原 Evidence 身份冻结唯一方向命令并建立本次经过；SCAN2 按当前点1→2队首与实际 `bin_code` 关联，合法 `-A` 用现有 `wms_operations.outbound_manual_bin_work_admission` 创建完整 typed intent；`WORK_REQUIRED` 等待 `work_completed`，`NO_WORK`/本地 NG 创建一次 `MOVE_FORWARD`，`WAIT` 与未知零命令。WMS Confirmation 以已有 `workline_id` 为 owner，现有 follow-up planner 增加此 owner 分支，固定 Adapter 构造 SDK `ManualBinAdmissionOutcome(operation_id, result, task_id, retry_after_ms)` 交插件，其中 `result` 只允许 `WORK_REQUIRED/NO_WORK/WAIT`，后两字段按固定 wire 联合可空，不把原始 JSON 给插件。`ManualBinCompletedService` 用当前 `PickingTask.task_id` 冻结 Evidence 的 WorkLine；无唯一任务则留证对账，并提供按 Evidence ID 重读严格 typed 完成事实的端口。`work_completed` 只应用到同任务、同箱、同一次等待，保存单终态后才释放。WMS 输入只传去后缀箱码。

- [ ] RED：插件应用测试覆盖 SCAN1 正常/NG、SCAN2 正常准入/本地 NG/错箱、WMS `WORK_REQUIRED/NO_WORK/WAIT`、完成事件正常/NG/错任务/重复/冲突/迟到；断言 WMS 只收到正常箱码，NG 不发准入。
- [ ] 运行 `uv run pytest workline_plugins/manual-picking/tests/test_scan_flow.py -q`，确认先失败。
- [ ] DEV：实现事务性经过更新、现有 DeviceCommand 与 WMS Confirmation 调用；对同一 Evidence 重放复用原身份，不创建新命令或 operation。
- [ ] GREEN：重跑该插件测试，以及 `uv run pytest tests/contracts/wms_adapter/outbound_picking/test_manual_bin_adapters.py tests/contracts/wms_adapter/outbound_picking/test_manual_bin_completed_handler.py tests/contracts/wms_adapter/outbound_picking/test_typed.py tests/workline/test_plugin_routing.py -q`；WMS Adapter 不增加业务状态或重试循环。
- [ ] Review：确认 WMS `RECEIVED` 不被解释为人工作业完成，完成结果只匹配冻结的 `task_id + bin_code`。

### Task 4: SCAN3 分流与 SCAN4 权威入队

**Files:** 完成插件 `application/scan_flow.py`、`application/passage_repository.py`；新建插件 `tests/test_return_buffer.py`；用 Task 1 已建立的设备结果消费分支推进退箱状态。

**Interfaces:** SCAN3 唯一匹配当前经过且确定正常才 `MOVE_FORWARD`，否则 `MOVE_LEFT`；不能以队首或同箱历史结果授予正常通行。SCAN4 合法且有前序正常授权时冻结 `MOVE_FORWARD`；`DeviceCommand` ACK、超时和未知不入队，匹配 `SUCCESS` 才设 `return_state=READY`。`return_order` 按 SCAN4 可靠到达顺序冻结；`outbound.bin.return_batch@v1` 只从 READY 的严格前缀取候选，未闭合队头阻止越序。

- [ ] RED：插件测试覆盖 SCAN1→SCAN3、SCAN1→SCAN2→SCAN3 交错，SCAN3 未知不停箱走左；SCAN4 不可读停箱；ACK/超时/未知零入队、成功一次入队、重复/迟到结果不重复入队、未闭合队头阻止后箱 `return_batch`。
- [ ] 运行 `uv run pytest workline_plugins/manual-picking/tests/test_return_buffer.py -q`，确认先失败。
- [ ] DEV：只补本点分流、SCAN4 结果应用及 FIFO 前缀查询；复用现有 typed `outbound_bin_return_batch`，不在插件重建 HTTP/队列/重试。
- [ ] GREEN：重跑插件测试和 Task 1 基础接缝测试；涉及真实 worker 路由时运行 `tests/integration/test_celery_async_runtime_postgresql.py` 的已选场景，不把 skipped 算通过。
- [ ] Review：核对 `MOVE_FORWARD` 创建/ACK 与 ECS `SUCCESS` 三种状态没有混用；队首未知时没有任何绕行或换身份重发。

### Task 5: 合同收敛与最终证据

**Files:** 修改 `docs/contracts/wms-manual-outbound-picking-integration-requirements.md`；处理被本方案替代的 `docs/superpowers/plans/2026-09-10-manual-picking-integration-workbench-optimization.md` 的当前引用并移至 `../archive_docs/wes_backend/`（只在确认其剩余任务已被当前真源承接时）；同步 `docs/architecture/file_index.md` 与精确 HEAVY mapping。

- [ ] 把合同 §3 的点1自主步进、点2不可读、点3未知停箱、点4只记录扫码改成已确认的四点流程；只改与本次行为冲突的段落和对应图表，保留历史厂商原件。
- [ ] 运行 `rg` 限定扫描当前合同、插件、SDK、部署与测试，确认 `data.barcode` 兼容读取和旧流程表述没有仍被当作当前真源；文档只做链接/结构和 diff 检查，不添加 pytest。
- [ ] 固定最终可执行树和环境指纹，按 AGENTS.md 测试所有权清单闭合插件、基础、WMS 合同、迁移及 HEAVY；运行聚焦测试、QUALITY、selector 选中的 HEAVY 和干净库迁移各一次，失败只重跑受修复影响的证据。
- [ ] 完成一次当前 diff 主 Review；按审查意见闭合后只刷新失效的验证。报告“代码合同通过”与“真实 ECS/WMS/物理验收未做”两种不同结论；未经单独授权不 Commit/Push/Deploy。

## 执行选择

本仓库默认由当前 Agent inline 执行并在各任务边界复核；仅用户明确要求分工时使用 Subagent。计划获批后按 `superpowers:executing-plans` 执行，不因计划已批准再次重做设计。
