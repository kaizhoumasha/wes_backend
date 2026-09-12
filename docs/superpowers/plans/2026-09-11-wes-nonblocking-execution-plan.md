# WES 无阻塞执行实施计划

> **For agentic workers:** 使用 `wes-implementation` 执行项目流程，按 `superpowers:executing-plans` 顺序完成下列切片；不默认并行改共享合同。每个切片独立闭合测试与评审，但不能单独冒充整体业务验收。

**Goal:** ECS/Transport 旧异常不阻止独立新任务，权威结果自动归集，正常恢复不要求人工操作 WES。
**Architecture:** 复用 DeviceCommand、TransportTask、Evidence、WmsConfirmation 和既有 worker；取消 WES 物理占用裁决，保留单消息幂等和短事务领取。业务插件负责有效决策与版本消费，基础不依赖插件。
**Tech Stack:** Python 3.13、FastAPI、SQLAlchemy、PostgreSQL、Celery；Vue 3、TypeScript、Vitest。
**Spec:** [无阻塞执行设计](../specs/2026-09-11-wes-nonblocking-execution-design.md)。
**状态：** IMPLEMENTING（代码实现已本地闭合）；后端主体 PR #246 与诊断合同补充 PR #247 已合并 `develop`。
前端已从后端 `405d9284` 冻结合同，在独立 worktree 完成实现、测试、lint 与生产构建，尚未提交；部署、供应商接入和现场验收未完成。

**CEO 评审 D1 已确认：** 完整职责收敛，复用 ECS/RCS/WMS 现有恢复机制；前端仅面向 IT、设备工程师和运维人员。撤回新增 ECS 必填 result_revision 的预设；本轮工程评审已完成并落入 ENG-D1–D6；实施先完成 T0 的合同核验，不代表接入或现场验收通过。

## 全局约束

- 基础能力可独立部署、运行、测试；业务通过基础端口执行；核心测试不得导入真实插件。
- 无兼容模式、旧版本分支或旧数据迁移；开发测试数据可清理，现场数据操作另按授权。
- 不新建恢复平台、资源调度器或重复 worker；不复制已有 Evidence/Transport revision 能力。
- 纯文档不编写测试；行为变更遵循 RED → 最小实现 → GREEN，优先修改原测试。
- 不删除 docs/hardware；退出的过程文档移出项目归档，不保留副本或跳转文件。
- 本计划不是提交、推送或部署指令；实际授权按项目规则分别判断。
- 全链路范围包含实际业务消费者，不能以“基础完成、业务另立项”替代本目标。

## 基线、复用与交付顺序

backend develop `419d2725`，frontend develop `cdaca7a`。开始实施重新记录 HEAD、dirty 和 Worktree；本计划路径清单是影响分析起点，新增调用链必须说明后纳入同切片。

顺序：T0 合同核验 → T1 ECS → T2 Transport → T3 自动事实恢复 → T4 业务消费者 → T5 前端/接口退役 → T6 分层验收。T1/T2 必须和 T3 的结果隔离一起进入候选部署，不能先删限制再补迟到保护。

现存 WMS 安全续送、本地 DEVICE_OBSERVATION、诊断 SSE、Transport outcome_revision 已实现，复用并定向回归，不重写。

## T0：冻结接入合同及消费者范围

**文件：** `docs/integration/third_party_integration_whitepaper.md`、`docs/contracts/transport-fulfillment-contract.md`、`docs/contracts/wms-outbound-picking-task-integration-requirements.md`、`docs/architecture/SRS.md`、`docs/devops/execution-recovery.md`。
**输入：** 设计第 3–6 节及当前实际路由。
**输出：** 每个接入方的接纳互斥、幂等期限、回调修订、业务纠正路径与证据表；尚未通过的供应商项明确阻止相应部署，不阻止只读设计工作。

- [ ] 逐项登记设计第 6 节五项能力，引用真实协议条款和供应商实测结果；不能用 WES Mock 替代。
- [ ] 核对 ECS 既有恢复回调与 WES 单 command_code 冲突规则，明确消息去重与原任务事实应用的映射；不新增恢复协议或预设必填 result_revision。保留未能判定顺序的事实，禁止猜测覆盖。
- [ ] 对 return_batch、prepare 分别画出现有失联重试和确定响应纠正路径，标明原身份、新业务身份、作废关系和 WMS 接收方。缺失机器纠正合同必须先与业务所有者闭合再实施 T4，不能改库或假定任意重报可覆盖。
- [ ] 枚举 Device/Transport、位置投影、调试轮次、插件、API/SDK、权限和测试 owner；对生产符号做项目规定的影响分析。
- [ ] 文档自检链接和当前/目标区分，不运行 pytest。记录是否具备后续代码实施和部署条件。

## T1：ECS 独立下发和原命令修订

**修改：** `src/app/device/contracts.py`、`src/app/device/models/command.py`、`repositories/command_repository.py`、`services/device_command_service.py`、`services/device_dispatch_service.py`、`services/device_evidence_service.py`（均在 device 下）；`src/app/execution/services/inbound_evidence_service.py` 及其模型/身份解析消费者；由 Alembic 生成的 migration。
**原测试：** `tests/runtime/device_command/test_dispatch_service.py`、`test_evidence_service.py`、`test_device_command_service.py`、`test_evidence_identity.py`；`tests/contracts/device/test_uniform_ecs_wire.py`；`tests/integration/device_command/test_device_command_constraints.py`。
**接口：** `claim_next_pending` 仍返回一条 DeviceCommand，claim token 只隔离该命令；回调按 T0 核对的既有恢复语义接入，不增设一套恢复 wire。

- [ ] 修改原测试复现：旧 ACK/RECONCILING 不挡新命令；同设备不同命令可分别领取；同一命令并发仍仅一人领取；重复事件不生成第二条命令。运行聚焦集确认目标断言 RED。
- [ ] 删除跨命令状态排除、全局领取闸门、设备 DISPATCHING 唯一约束和 debug 的旧任务占槽准入；保留命令身份唯一及 token 写回校验。
- [ ] 移除 Status 动态运行态的发送准入依赖，保留请求静态配置/能力合同校验及明确未接纳退避；接纳责任落在 ECS。
- [ ] 依据 T0 的既有合同映射分别处理消息重复、恢复事实和原任务更新；无法判定的事实留存，不阻止新任务；不得因恢复事实生成第二次物理执行。
- [ ] 落实工程评审 ENG-D1：修改当前 `RESULT:{command_code}` 单结果身份规则，复用 Evidence 和规范化报文摘要区分消息，仍用原 command_code 关联命令；同步调整结果应用分支与 DeviceCommand 状态机，使合同可判定的 FAILED 后恢复成功能更新原命令。摘要不作为排序依据；重复消息不重复推进业务，不新建任务或重发动作。
- [ ] 优先修改原 evidence/identity/command 测试，覆盖失败后恢复成功、重复回调、迟到失败和无法关联；同时断言历史事实保留、无法判定顺序时不覆盖当前结果、业务推进不重复。不得只验证新消息能入库而遗漏命令状态更新。
- [ ] 落实 CEO D7：Device 与 Transport 对端明确接纳后不重送提交；结果超时、重复 ACK、worker 重启都不能重新调用提交接口。交付未知按既有幂等及请求有效性规则处理，使用原 dispatch/submit 测试区分两条路径。
- [ ] 使用原测试验证迟到成功、FAILED 后既有合同可判定的恢复成功、未知任务 ACK、无效设备和同版本冲突。运行 `uv run pytest tests/runtime/device_command tests/contracts/device`；DB 并发测试按 tests/README 的真实 PostgreSQL 门禁执行。
- [ ] 核对数据库索引及旧占槽消费者残留；同切片评审。此时不单独部署。

## T2：Transport 退出资源占用裁决

**修改：** `src/app/transport/models.py`、`repository.py`、`service.py`、`debug_run_repository.py`、`debug_run_service.py`、`debug_reset.py`、`v1/tasks.py`；`src/app/execution/services/position_projection_service.py` 与模型/仓储；随机 revision migration。
**原测试：** `tests/runtime/transport/test_transport_service.py`、`test_transport_execution_authority.py`、`test_transport_submit_fencing.py`、`test_transport_debug_run_service.py`；`tests/integration/transport/test_transport_schema.py`、`test_transport_repository.py`。
**接口：** 公共 Transport 请求/handle 保持既有业务意义；删除 TransportResourceBinding、release_bindings 及 active_binding_count 对外字段，任务资源关系复用现有 members。

- [ ] 修改原资源冲突测试为同资源独立请求可创建并提交；加入旧任务未知、跨工作线旧投影、活动 debug run 三条真实阻塞路径，确认 RED。
- [ ] 删除 TransportResourceBinding 占用 owner 和全部消费者；不建立同名非唯一替代物；IntegrityError 只按真实幂等冲突处理，不再一律包装资源占用。
- [ ] PositionProjection 不再因未知位置或旧工作线拒绝有效请求；仍校验请求自身字段、明确停用和业务授权。RotateRack 的位置参数必须来自明确请求/有效业务决策，不以旧投影兜底猜测；所需公共签名同步传播。
- [ ] 删除 debug run 对外的货架占用；保留自身步骤领取和重复启动控制，调整 abort/reset 对已删除 binding 的依赖。
- [ ] 运行上述 runtime 原测试；真实库验证同资源并发创建、同 client_request_id 同正文幂等、不同正文冲突及 schema 最终状态。扫描全部 binding 引用，评审后进入 T3。

## T3：结果留存、自动关联和投影防回退

**修改：** `src/app/transport/service.py`、`repository.py`、`contracts.py`；`src/app/device/services/device_evidence_service.py`；`src/app/execution/services/inbound_evidence_service.py`、`wms_confirmation_service.py`、`position_projection_service.py`；相应模型与仓储。
**原测试：** `tests/runtime/transport/test_transport_reconciling_facts.py`、`test_transport_outcome_revision.py`；`tests/runtime/execution/test_inbound_evidence_service.py`、`test_wms_confirmation_dispatch.py`；`tests/integration/transport/test_transport_evidence_transaction.py`。
**接口：** 未关联是 Evidence 处理结果，不是创建任务的锁；复用现有 worker 对已登记匹配工作有界领取和提交后唤醒，不轮询全部未关联历史。位置记录只由来源事实更新，不增加观察任务引用。

- [ ] 复现 A5–A7/A10/A11：先回调后建任务、失效上下文、高低版本乱序、旧任务晚到、新任务成功后旧任务回调；确认原实现在哪些断言 RED。
- [ ] 落实 CEO D8：未关联事实留存后退出活跃重试；任务建立时按精确身份可靠登记匹配工作，复用现有持久化义务与 worker，不建历史轮询服务。Beat 只补未完成工作；验证任务与回调并发提交、通知丢失、登记后重启均能匹配，无任务变化的历史记录不反复领取。
- [ ] 分离事实落库与业务有效性：失效 owner 不阻断原结果留存及已有反馈义务，不允许恢复旧业务动作。
- [ ] 落实 CEO D6：删除创建任务时改写位置/观察归属的方案，不新增观察字段。位置保留来源与时间；仅在既有合同可证明先后时更新聚合，否则分别保留事实并显示当前未确认。修改原投影测试覆盖任务创建、拒绝不改变事实，以及跨任务乱序不伪造当前位置；核对业务读取消费者不使用诊断投影作为执行授权。
- [ ] 复用 Transport outcome_revision/outcome_version 和 WmsConfirmation 安全续送；证明重复发布/重启不会重复业务推进。
- [ ] 运行原 runtime 测试及 `tests/integration/transport/test_transport_evidence_transaction.py`；T1–T3 合并评审同任务幂等、孤立结果和跨任务隔离。

- [ ] 落实 CEO D9：按已有异常类和协议结果逐条映射暂时故障、已接纳待结果、内容冲突/字段错误及数据库不可用；改原错误路径测试验证仅可重试故障退避、永久错误不反复领取、单条错误不拖住其他任务、落库失败不返回成功。回调纠正仍按来源合同接纳，不以旧错误状态永久屏蔽。

## T4：业务消费者与 WMS 恢复闭环

**修改入口：** `workline_plugins/rough_sorter/src/rough_sorter/handlers/device_position_confirmed.py`、`target_decided.py`、`material_evidence_ready.py`；`src/app/transport/debug_run_service.py`；`src/app/workline_integration_debug/service.py`；`src/app/wms_adapter/outbound_picking/return_batch_*`；`src/app/wms_integration/outbound_picking/services/picking_task_prepare.py`。只修改调用链确认有旧状态依赖的消费者。
**原测试：** `workline_plugins/rough_sorter/tests/test_device_and_target.py`、`test_transport_and_recovery.py`；`tests/runtime/transport/test_transport_debug_run_advancement.py`；`tests/runtime/workline_integration_debug/test_transport_mapping.py`。其他实际插件测试在插件包内，不能寄存核心。
**接口：** 新本站事件进入其自己的业务决策；同任务事实按身份及来源合同消费；WMS 已纠正的业务请求使用对应 operation 明确合同，不添加通用改报文接口。

- [ ] 按实际工作线列出“本站事件 → WMS 决策 → 指令 → 结果”调用点和测试 owner；未实现的插件不以声明或 fake 假装交付。
- [ ] 在各自业务测试复现旧对象未知、新对象独立继续、旧结果不重启失效动作；自动联调失败步不能无限等待或重用旧目标。
- [ ] 实施最小消费者改动：独立事件独立接纳、原任务结果独立归集、后继动作先确认有效业务决策；需要前一步物理完成的真实同任务依赖不得自动跳过。
- [ ] 落实工程评审 ENG-D2：prepare 协调器不再用整线未完成记录总览作为统一准入条件。历史异常及待反馈继续处理；准入按有效 WMS 决策及业务规则判断，保留真实的有效 PickingTask 约束，不伪造旧任务完成。共享统计用于配置等其他消费者，不通过清空统计放行。
- [ ] 修改现有 `tests/contracts/wms_adapter/outbound_picking/test_prepare_service.py` 和 `tests/integration/wms_adapter/outbound_picking/test_prepare_postgresql.py`，验证旧 Device/Transport/Evidence 异常留存时独立且有效的新请求仍能进入 prepare，以及真实业务约束仍有效；同时核对 `tests/architecture/test_outbound_picking_prepare_activation_guardrail.py` 的原断言与新合同。
- [ ] 依 T0 已闭合合同验证 WMS 503 自动续送和确定错误响应后的机器纠正；无合同不能放宽正文冲突校验。记录每个 operation 的真实验证结果，未闭合则本切片不完成。
- [ ] 分别运行核心协调器测试与插件独立测试入口；review 不以基础测试推断业务正确性。

**工程评审 ENG-D3 补充到 T4：** 修改 `src/app/workline/services/workline_start_service.py` 的工作线重新启用准入，取消历史未闭合负载的统一阻断，保留配置合法性、有效业务约束和显式停用语义。原测试为 `tests/workline/test_workline_start_service.py`、`tests/integration/workline_capabilities/test_workline_start_postgresql.py`；进程启动测试 `tests/deployment/test_execution_worker_startup.py` 是独立消费者，不能把工作线启用变化扩展成自动开启工作线。

- [ ] 验证历史异常/待反馈留存时可明确重新启用工作线，配置错误仍拒绝，未收到启用请求时停用状态保持；不通过修改共享统计或旧任务状态放行。独立核查 safety incident 与插件 blocker 的来源、解除路径及真实业务约束，不能换一种历史记录继续要求人工解锁。

**工程评审 ENG-D5 补充到 T4：** 修改 `src/app/wms_integration/outbound_picking/services/picking_task_plan_delta.py` 的正常接收入口，复用 `validate_plan`、`apply_plan` 自动应用来源合同可确认的合法纠正。历史 blocker 不能提前拒绝所有修正；检查已保存 RECONCILING 消息重报时的快捷返回，避免合法纠正仍需人工应用。

- [x] T4b 已修改原 `tests/contracts/wms_adapter/outbound_picking/test_plan_delta_service.py`、`tests/integration/wms_adapter/outbound_picking/test_plan_delta_postgresql.py`，覆盖新修正及已留存修正重报、重复/并发纠正、旧版本迟到、非法版本和归属错误。合法纠正原子更新计划并解除对应 blocker，保留旧 Evidence；无效纠正不推进计划、不阻断独立新任务。真实 PostgreSQL owner 已纳入 PR #246 selected HEAVY 通过。
- [x] T5c 已退役 `src/app/wms_integration/outbound_picking/v1/plan_correction.py` 人工应用入口及真实消费者、权限/OpenAPI 合同，调整 `tests/api/test_outbound_picking_plan_correction.py`；正常入口复用的校验和应用逻辑保留，不提供兼容管理员恢复接口。后端代码门禁已通过，整体 T6 仍待部署与现场验收。

**工程评审 ENG-D6 补充到 T4：** 在 `src/app/transport/debug_run_service.py` 删除 `DEBUG_NO_BATCH_ORIGINAL_SLOTS` 自动原槽位兜底及失去消费者的源任务拼装逻辑。NO_BATCH 保留为 WMS 本次决策，不生成搬运动作或标记执行失败；本步骤等待决策，其他独立任务继续。

- [ ] 按现有 return_batch 合同再次请求使用新 operation_id；传输失败的原请求重试保持身份和正文。复用现有 worker 和可靠发送能力，按既有 NO_BATCH.retry_after_ms 持久化下一次决策请求的等待时间并校验候选仍有效，避免每次唤醒立即创建请求；READY 才创建对应 Transport，不新建重试服务。
- [ ] 修改 `tests/runtime/transport/test_transport_debug_run_advancement.py` 的原 NO_BATCH 用例，验证不回原槽位、不产生 Transport、不标执行失败；到期再次请求、后续 READY、重复响应、重启等待、候选失效和独立任务继续。联调说明同步退役旧原槽位约定，历史事实保留。

## T5：退役人工恢复链路及前端合同同步

**工程评审 ENG-D4：** 急停记录、物理急停、复位和恢复执行全部归 ECS。`ESTOP_PRESSED` 不属于 WES ECS callback 合同：在 Evidence 和任何 wake 前以统一校验错误拒绝，不得进入普通、插件或联调事件链。WES 保留原 DeviceCommand 身份、状态和资源围栏，只等待既有 result/reconciliation 合同的权威终态；明确管理停用不因 ECS 恢复而自动解除。关联修改入口为 ECS callback 公共包络、callback/runtime event taxonomy、`device_evidence_service.py`、`workline_start_service.py`、incident 模型/仓储/API/权限和 worker/配置消费者。与 T4 的启用准入一并闭合。

- [ ] 核对并退役 incident 建立/排空/人工清除链路及权限、生成合同；删除 WES 内 `ESTOP_PRESSED` 的 reserved runtime event/helper/export/normalization 特例和 Evidence 应用语义。先枚举全部消费者，不保留 no-op 或兼容字段。
- [ ] 修改 `tests/api/test_device_ecs_callbacks.py`、`tests/workline/test_workline_start_service.py`、`tests/contracts/workline/test_callback_runtime_contracts.py`、`tests/api/test_workline_safety_operation_api.py` 及相关架构/selector guardrail。覆盖急停在 Evidence 和 execution/transport-debug wake 前被拒绝、明确停用不被自动解除，并删除原 evidence service 的急停应用测试语义；物理急停/复位/恢复由 ECS 接入验收证明，不用 WES Mock 代替。

**后端（T5b 已合并且代码门禁通过）：** 已删除 `src/app/device/v1/reconciliation.py`、`event_block_contracts.py`、`models/event_command_block.py`、`repositories/event_command_block_repository.py` 及 service/composition 引用，并以随机 migration 删除辅助表；对应 blocker/reprocess/reconcile-device-idle 注册、权限和 OpenAPI 入口不保留兼容路径。
**后端（T5c 已合并且代码门禁通过）：** 已删除 plan `apply-correction` route、DTO、service method、人工审计 helper 及注册；合法修正只经正常 plan_delta record/replay 自动校验应用，历史 Evidence 与严格 identity/version/owner/concurrency 校验保留。
**前端根：** `/Users/kaizhou/codeDev/wes_frontend`。
**前端入口：** `src/views/ops/device-diagnostics/DeviceEvidenceTable.vue`、`useDeviceEvidenceStream.ts`；`src/views/ops/transport-diagnostics/TransportTaskDetail.vue`、`TransportDebugResetDialog.vue`、`useTransportDiagnostics.ts`；`src/views/ops/transport-debug/useTransportDebugRun.ts` 与当前合同生成物。
**原测试：** 对应 `tests/unit/views/ops/device-diagnostics/`、`transport-diagnostics/`、`transport-debug/`；后端 `tests/api/test_device_reconciliation_api.py`、`test_transport_tasks.py`、Evidence stream 测试。
**接口：** 面向 IT、设备工程师、运维人员展示未关联、历史未知、来源结果和近期交互；日常仓库工作人员无需登录；没有“解锁后才能继续”的交互。测试数据清理是独立调试工具，不包装为恢复必需步骤。

- [x] 后端已列出实际路由消费者并整体删除被替代的恢复接口；旧 blocker/reprocess/reconcile-device-idle 测试按 NONE 退役，独立命令行为由既有 T1 owner 承接；前端按钮消费者已在 frontend worktree 删除。
- [x] 扩展既有组件测试证明异常记录与点位近期正常可同时显示、旧任务修订不会替换新任务、无资源解锁步骤。
- [x] 同步 canonical OpenAPI、类型、Zod、权限；删除弃用接口所有生成残留，不提供旧字段默认值或兼容别名。
- [x] 运行 `pnpm contract:test`、`pnpm contract:verify`、`pnpm permission:verify` 和相关 Vitest 文件；再次生成指纹一致。
- [ ] 浏览器检查有数据的异常/恢复列表和页面关闭后后端继续运行；此项仍需可用联调数据和运行环境。

- [x] 实施 CEO 扩展 D3：在现有任务详情串联请求、决策、下发、接纳和结果；复用最新 Evidence 的精确身份查询，缺失环节显示“未观察到”。组件/API 测试覆盖完整链、部分链、无法关联、分页、查询失败和跨任务响应乱序；未建设第二套日志或恢复平台。

- [x] 实施 CEO 扩展 D4：复用现有持久化状态展示等待环节、时长及尝试时间；后端任务详情补充 `next_submit_at`，前端测试覆盖真实退避、已接纳待结果、本地积压、无重试计划和缺失时间。不增加恢复按钮或独立监控平台。

- [x] 实施 CEO 扩展 D5：复用 D3 关联查询导出单任务固定字段 JSON，包含导出时刻、缺失/截断说明并排除凭据；组件测试覆盖权限、无记录、部分记录、上限、敏感字段排除及下载失败。未重建查询 owner、自动发送或新增导出平台。

## T6：分层验收与交付

**文件：** 本计划、设计验收表、`docs/devops/execution-recovery.md`、两端有效合同/发布文档；HEAVY 所有权以 `docs/architecture/heavy-test-impact.toml` 为准。

当前代码门禁证据（不替代下列部署、供应商和现场验收）：后端 PR #246 的 QUALITY 为 3845 FAST passed、selected HEAVY 为 395 passed；PR #247 的 QUALITY 为 3846 FAST passed、11 个 selector 文件在干净 PostgreSQL/Redis 上为 78 passed。前端最终未提交快照为 884 tests passed，`pnpm lint`、`pnpm build`、合同/权限校验及生成幂等均通过。

- [ ] 基础 FAST：汇总 T1–T3/T5 精确快照结果；只重跑被后续改动失效的证据。
- [ ] 基础真实进程：PostgreSQL 并发、Celery worker、提交后唤醒丢失、进程在持久化/发布边界重启；无具体插件导入。
- [ ] 业务独立验证：T4 各实际消费者运行自己的用例，记录请求、任务、结果版本和 WMS 接收证据。
- [ ] 供应商验证：按设计第 6 节记录 ECS/RCS 实际接纳与恢复，特别是同资源两个任务、ACK 丢失和同命令恢复修订。
- [ ] 最终代码快照执行项目所需 QUALITY、selector HEAVY、迁移与前端门禁；不把历史 159/41 项聚焦结果计作新实现完成证据。
- [ ] 落实 CEO D10：演练维护窗口内停止新调度与旧写入进程、更新 schema/前后端、启动验证后恢复调度；确认窗口内回调可由既有链路重传。失败时保持维护，只有代码/schema 匹配才切回，不删任务回滚、不重发已接纳命令。取得实际部署授权后再执行；验证真实数据库路径和前端合同一致，不只检查 health。
- [ ] 逐项验收 A1–A12，记录未完成项。现场全过程不使用 WES 人工解锁/改库/重建；任一关键场景未通过，整体不得标记完成。
- [ ] 更新唯一当前文档，归档本次退出的过程文档；保留 docs/hardware 和其他有效设计。提交/推送/部署结果分层报告。

## 文档自审记录

本次仅建立目标与计划：设计职责对应 T0/T4；去围栏对应 T1/T2；身份、修订与投影对应 T1/T3；WMS 业务纠正对应 T0/T4；界面与接口退役对应 T5；A1–A12 对应 T6。外部幂等和业务纠正路径尚未经本次实测，不伪称已支持。

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
| --- | --- | --- | --- | --- | --- |
| CEO Review | /plan-ceo-review | 职责、范围与风险 | 1（本方案） | DONE_WITH_CONCERNS | 3 项扩展已接受，D6–D10 五项修正已接受；2 项接口细节待工程核对 |
| Independent scope review | 技能范围审查 | 范围一致性 | 1 | PASS（仅范围） | 同模型独立审查 9/10；不等于完整工程或跨模型审查 |
| Eng Review | /plan-eng-review | 当前代码、架构、测试与性能 | 1（本方案，多轮确认） | DONE_WITH_CONCERNS | ENG-D1–D6 已批准并落文档；2 类接入证据待 T0/T4 核验。尚未实施或运行测试 |
| Design Review | /plan-design-review | 专业诊断界面 | 0（本方案） | NOT RUN | 已定义交互边界，尚无独立界面评审 |
| DX Review | /plan-devex-review | 二次开发维护 | 0（本方案） | NOT RUN | 本轮按既有 owner 与简单实现审查 |

**VERDICT:** 本轮工程评审完成，DONE_WITH_CONCERNS。当前 develop 代码核对所得 ENG-D1–D6 均已确认并写入方案；已有能力复用、测试所有权与有界等待要求已核对。实施按 T0–T6 顺序进行；尚无生产代码修改、测试执行、提交或部署证据。嵌套 Codex 按技能跳过，不宣称独立跨模型工程评审。

**UNRESOLVED DECISIONS / 接入证据：**
- ECS 原 command_code 恢复报文的状态/顺序权威性仍须核验；ENG-D1 已确定消息身份与原命令应用分离，不再重复架构选择。
- WMS plan_delta 已找到现有自动应用改造入口；prepare、return_batch 的确定响应纠正仍须逐 operation 核验。503 安全续送与 NO_BATCH 新请求不能代替这项证据。
- 较早基线的 3 项历史工程问题不重复累计为本轮发现；本轮仅以上 2 类接入证据待闭合，不能据此宣称现场无阻塞恢复已验收。
