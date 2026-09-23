# WES 职责收敛执行账本

状态：2026-09-23 执行控制面。本文只跟踪本次重构，长期规则以 SRS、外部合同、插件业务合同和 `AGENTS.md` 为准；重构结束后移出项目归档。下表保留原始候选基线，控制矩阵和切片状态记录实施进度；不因名称删除代码。

## 判断准则

WMS 决定业务意图、变化和终态；WES 将意图编排成自动化 SOP；ECS/RCS 裁决物理资源并执行；权威事实推动下一步。WES 可以等待当前步骤所需的事实，不能用本地历史任务或投影替 ECS/RCS 判断其他独立任务是否可提交。一次业务步骤的身份关联、幂等与数据库原子性不属于物理资源围栏。

技术重试沿用原 `request_id` 和正文；确定终态之后，业务 owner 如仍需执行才创建新 `request_id`。Transport 只报告确定结果，不自行决定业务重试。

在既有 19 项调用链上横向审查，不增加候选编号或状态机：

- **Resource Coupling**：WES 是否用自身历史任务或投影替 ECS/RCS 裁决物理资源。
- **Lifecycle Coupling**：父级 `picking_task.status`、`batch.status`、`workflow.status` 是否停止了仍有直接业务依据的子 Action；逐点核对原 Requirement、WMS 取消事实、目标权威事实和前次 Action 终态。`PickingTask=EXECUTION_COMPLETED` 本身不是 `plan_delta member` 的取消事实。
- **Projection Coupling**：业务状态是否直接清除、伪造或阻止应用已确认的位置、到位及资源事实。Intent 可变，已确认的物理 Fact 不可由 Intent 改写；业务取消与在途设备取消分别处理。
- **Causality Coupling**：当前状态是否由最后到达的消息决定，而不是由该对象最新的权威因果事实决定。当前位置按已有 causal token 与 Action 身份比较；首次 SCAN4 顺序则保留首次权威到位事实。规则按投影用途确定，不建通用引擎。

六类唯一归属：`KEEP — Automation SOP`、`KEEP — Dependency Gating`、`KEEP — Fact Projection`、`KEEP — Reliability / Idempotency`、`REMOVE — WMS Business Duplication`、`REMOVE — ECS/RCS Physical Control Duplication`。`待核` 是执行状态，不是第七类；混合职责必须拆开后处理。`Command ≠ Fact`、`Command order ≠ Physical order`、`Timeout ≠ Failure`。若基础能力不加载业务插件就失去完整语义，应优先移回插件。

| 编号 | 当前实现与调用链 | 初步判断（以下方控制矩阵为准） | 处置与验证条件 | 现有测试 owner |
| --- | --- | --- | --- | --- |
| R01 | `manual-picking/application/batch_driver.py` 的 `_source_window` 用配置容量减去 `occupied_source_rack_ids`，`_fill_source_window` / `_fill_drain_window` 再排除 `fenced_source_rack_ids` 后提交 CTU01 | **删除候选：ECS/RCS Resource Duplication**；插件仍拥有何时需要进架 | 拆分“防同一步骤重复创建”和“按历史 Transport 推断资源不可用”；后者若只是替 RCS 准入，应删除。核对去掉窗口后同任务不会重复提交，独立到位架能分别推进 | `workline_plugins/manual-picking/tests/test_source_progression.py`、`test_batch_repository.py`、`test_rack_cycle_postgresql.py` |
| R02 | `manual-picking/application/batch_repository.py` 的 `occupied_source_rack_ids` / `fenced_source_rack_ids` 跨任务读取进出场 Transport 状态，计算占窗和同架复用限制 | **删除候选：Resource Fencing / Physical Prediction**；RCS 管物理资源 | 随 R01 逐调用点删除物理准入用途；不能把原业务步骤去重或同一步骤的真实前置动作一起删掉 | 同 R01 |
| R03 | `execution/services/reliable_rack_transport.py` 用 `TransportDecisionBinding` 把 `correlation_id + step` 固定到 `client_request_id`；模型同时保存 `resource_fence_id`，计划激活和插件按该字段排除货架 | **拆分：Communication Idempotency 保留，Resource Fencing 待删**；基础层保存身份，插件判断 SOP | 保留唯一决策身份与稳定请求 ID；追完 `resource_fence_id` 的全部读写后删除物理占用含义及失用字段、索引。不要为了字段名直接删整张 binding 表 | `tests/runtime/execution/`、`tests/integration/transport/`、插件货架测试 |
| R04 | `execution/services/position_projection_service.py` 按原 Transport 结果更新位置，并用 source/causal token 防止旧证据覆盖新事实；`admit_transport_member` 当前只校验工作线授权 | **Fact Projection 保留；物理准入调用待核**；权威位置来自 RCS/ECS/WMS | 保留事实来源、位置和重复结果处理。核对 token 比较只防旧事实覆盖，不能把命令创建顺序冒充货架到位顺序；逐调用点查是否还有基于投影拒绝独立 Transport | `tests/runtime/execution/test_position_projection.py`、`tests/integration/transport/test_transport_projection_candidate_postgresql.py` |
| R05 | `manual-picking/application/rack_readiness.py` 用原 Transport 成功、精确位置及货架面判断当前步骤可否启动 | **Dependency Gating 保留**；插件拥有该 SOP 前置条件 | 保留对当前货架的事实核验；不得扩展为其他货架或整线的准入锁 | `workline_plugins/manual-picking/tests/test_rack_readiness.py`、`test_source_progression.py` |
| R06 | `transport/service.py` 的 submit 路径和 Transport 合同把发送后 ACK 未知保持为 `SUBMIT_DELIVERY_UNKNOWN`，禁止原身份自动重提 | **通信可靠性重构候选**；基础 Transport | 按已确认的 WMS/RCS 合同，明确同一冻结请求 ID、正文的查询/重提和确定性结果收敛；不得换 ID 创建第二个物理动作。先写清 WES 字段到 RCS `request_id` 的端到端映射 | `tests/runtime/transport/test_transport_service.py`、`test_transport_acceptance_edges.py`、Transport 接线测试 |
| R07 | `transport/service.py` 的 `reconcile_overdue_tasks` 对已接纳但超期任务发布 `UNKNOWN`；合同据此保留较多结果恢复分支 | **收缩候选：预测性超时状态**；基础 Transport | 超时只作观测/告警或原身份查询，不推断失败；仍保存原身份及等待中的依赖。核对位置未知、证据冲突与“尚未收到终态”是否被混为一类，不能删真实冲突 Evidence | `tests/runtime/transport/test_transport_service.py`、`test_transport_observability.py` |
| R08 | `transport.task.resulted@v1` 经 WMS Event route 保存 receipt、版本身份与 Evidence，提交后 ACK；worker 随后应用并发布结果 | **Communication Reliability 保留**；基础层可独立运行测试 | 保留重复消息无重复 Evidence/副作用、提交失败不 ACK、ACK 后进程异常可恢复处理；只删除已有能力重复实现的补偿 | `tests/integration/transport/test_transport_evidence_transaction.py`、Transport 接线测试 |
| R09 | `manual-picking/application/scan_flow.py` 在首次 SCAN4 保存 `scan4_received_at + evidence_id`，设备成功后标记 `READY`；`passage_repository.py` 按首次 SCAN4 顺序取连续可回前缀 | **Automation SOP / Fact Projection 保留**；插件拥有 | 保留真实 SCAN 路径与连续前缀；核对重复/迟到 SCAN4 不改序，后闭合的前项不能被后项越过 | `workline_plugins/manual-picking/tests/test_scan_flow.py`、`test_batch_flow.py` |
| R10 | `manual-picking/application/scan_flow.py` 在 SCAN1～4 下发方向命令前检查本设备旧命令未闭合 | **待核：Dependency Gating 或 ECS Resource Duplication** | 逐命令确认该检查保护的是同一料箱的因果动作，还是替 ECS 做设备级互斥；只删后者，保留事件与原命令的身份关联 | `workline_plugins/manual-picking/tests/test_scan_flow.py` |
| R11 | `wms_integration/outbound_picking/services/picking_task_plan_activation.py` 保存 WMS plan、调用冻结插件 handler，再持久创建 Transport | **宿主编排与业务插件边界待核**；WMS 拥有计划内容，插件拥有 SOP 决定 | 保留接收、事务和可靠对象；逐分支确认宿主没有自行选择来源/目标或替插件决定货架动作 | `tests/contracts/wms_adapter/outbound_picking/`、`tests/integration/wms_adapter/outbound_picking/`、插件计划测试 |
| R12 | `resource/services/projection_service.py` 保存到位、placement 和 Cell 占用事件；`active_rack_snapshot_service.py` 形成工作线快照 | **事实投影保留，WMS Business Duplication 待核** | 按字段来源与消费者核对：现场事实可以本地投影，库存可用性、目标储位和业务资格不能成为 WES 第二主账。没有消费者和权威来源证据前不整模块删除 | `tests/resource/` 及其实际消费者测试，待逐项闭合 |
| R13 | `device/services/device_command_admission.py` 检查状态 `IDLE/AUTO`；当前搜索到的生产调用是 manual debug preflight | **诊断能力，非已证实的正常业务 Resource Fencing** | 保留或简化诊断显示；先查设备命令实际出站调用点，不能只凭函数名认定它阻断正常业务 | `tests/runtime/device_command/test_device_command_admission.py` |
| R14 | `runtime/orchestration/services/device_dispatch_policy.py` 按设备状态快照和 in-flight 数量作派发判断；当前非文档引用仅见 `tests/load/runtime_benchmark_scenarios.py` | **失用代码与 ECS Resource Duplication 候选** | 核对动态装配/入口后，如无生产消费者，删除策略及只为它存在的负载场景，不移入新通用调度层 | `tests/load/runtime_benchmark_scenarios.py` |
| R15 | `runtime/orchestration/resource_wait_evidence_bridge.py` 自称旧 Runtime 桥接副本；当前非文档精确搜索未见外部引用 | **失用兼容桥接候选** | 确认无动态导入、公开导出或生成索引消费者后删除；不保留转发模块 | 先查 Runtime 装配与测试索引，当前未找到直接测试 |
| R16 | `deployment/plugin_composition.py` 显式安装 `manual-picking`，`build_deployment_runtime(enabled_plugin_keys=())` 有独立核心装配测试 | **基础/业务隔离保留** | 保留显式装配和无插件运行；检查新增 SOP 是否仍主要落在插件，而非不断扩张基础层的业务参数 | `tests/deployment/test_core_plugin_installation.py` |
| R17 | `celery_app/tasks/transport.py` 分别驱动 submit、Evidence 应用、超期对账、投影重放与结果发布 | **可靠 worker 保留，超期分支随 R07 收缩** | 保留 ACK 后本地 Evidence 继续处理和一次性结果发布；改动 R06/R07 时只调整对应 worker 注册、定时项与调用测试，不新建恢复平台 | `tests/runtime/transport/`、`tests/integration/transport/`、真实 worker 接线 owner |
| R18 | `wms_integration/outbound_picking/services/picking_task_prepare.py` 按 WorkLine 配置领取 WMS 已排队任务，并创建可靠 prepare 义务；`picking_task_plan_delta.py` 接收并保存 WMS 发布计划 | **Automation Workflow / WMS Fact Projection 保留，选择边界待核** | 保留工作线准入和已发布计划的接收；确认优先序与来源/目标来自 WMS，WES 仅选择可执行的下一自动化步骤 | `tests/contracts/wms_adapter/outbound_picking/`、`tests/integration/wms_adapter/outbound_picking/` |
| R19 | `wms_adapter/` 的固定 operation wire、`wms_integration/` 的可靠接收与持久化按方向分离 | **Communication Reliability 保留** | 不为业务插件复制 HTTP、消息收据或 WMS 幂等；后续审查只定位 operation 特有的越权业务判断 | `tests/contracts/wms_adapter/`、`tests/integration/wms_adapter/` |

## 逐项裁决控制矩阵

上表“当前实现与调用链”是 WES 当前行为，“现有测试 owner”是现有测试入口；下表补齐权威 owner、职责移交、六类唯一判定、Next Owner 和 Wake-up Source。后两列审查尚未闭合的持久对象；删除候选或一次性诊断无持久待办时标记不适用，`待核` 表示恢复链路仍需证明。分类指应保留或删除的**能力**，不意味着整文件处置。所有 `待核` 项在实际调用点和合同修订前不得删除。实施时须重新扫描直接/间接调用、动态装配和 HEAVY 映射，不能将表中测试路径视为完整清单。

| ID | 六类判定 / 状态 | 权威 owner → 删除后承接者或保留边界 | Next Owner | Wake-up Source |
| --- | --- | --- | --- | --- |
| R01 | **REMOVE — ECS/RCS Physical Control Duplication** / CTU01 物理窗口判断已删除；PostgreSQL 并发切片已通过 | ECS/RCS 裁决设备物理容量和 CTU01 接纳；WES 插件只保留 WMS 计划转动作、同一步骤去重和到位后 SOP。若主张 SOP 并发度，须给出与设备容量无关的业务依据、插件 owner 与验收例 | RCS 物理准入；WES 插件仅管步骤去重 | RCS 接纳/终态 Response |
| R02 | **REMOVE — ECS/RCS Physical Control Duplication** / `occupied/fenced` 两个查询已删除 | ECS/RCS 承接跨任务资源冲突；同一货架当前 SOP 的真实离场依赖若成立，插件用对象级因果条件保留，不能扩大为其他货架或整线禁入 | RCS 资源裁决；WES 不保留占窗对象 | 不适用；删除 WES 占窗查询 |
| R03 | **KEEP — Reliability / Idempotency** / 字段拆分待核；P0 恢复链闭合 | 基础层保留 `(workline_id, correlation_id, step) → client_request_id` 和业务对象关联；ECS/RCS 承接 `resource_fence_id` 的物理准入用途。字段、索引去留必须查完所有消费者，不能整表删除 | 未提交任务：Transport submit Worker；已有结果：Transport Evidence Worker | Beat 扫描持久 `PENDING`/Evidence；claim 租约过期重领，结果回调加速唤醒 |
| R04 | **KEEP — Fact Projection** / P0-C 已补缺失恢复关联的异常可见性；消费点待核 | 位置事实来自 RCS/ECS/WMS；基础层保存 SOP 需要的精确事实及因果 token。若消费者据此拒绝无依赖 Transport，删除该消费者的物理裁决，不删事实投影 | 已有关联 Evidence：Transport Evidence Worker；已应用结果而投影落后：Projection Worker；缺失恢复关联：运维排查生产不变量 | Beat 扫描 Evidence 与最终结果投影候选；同一扫描记录缺失 Binding、无效 causal token、投影来源不完整的稳定 reason、结构化错误日志与样本数，不静默跳过 |
| R05 | **KEEP — Dependency Gating** / 保留 | 插件保留“该货架实际到位后才能拣选”的对象级依赖；ECS/RCS 管其他货架的物理准入 | manual-picking 插件 | 货架到位 Evidence、插件推进 |
| R06 | **KEEP — Reliability / Idempotency** / WES 原冻结身份重提已实现；WMS 稳定映射已确认 | WMS/RCS 拥有 Request 接纳事实；基础 Transport 用原冻结 `operation_id + transport_task_id + 正文` 查询/重提，WMS 保证复用原 RCS `request_id`，不创建第二个物理任务 | Transport 提交 Worker | 持久 PENDING 扫描、lease expiry |
| R07 | **KEEP — Reliability / Idempotency** / 已移除已接纳任务的超时 UNKNOWN 推断 | RCS 保证已接纳任务终态，WMS 对未 ACK 的结果持续补发；基础层保留身份和观测。真实证据冲突和未知位置仍需保存 | Transport Evidence Worker | RCS 终态 Response、持久 Evidence 扫描 |
| R08 | **KEEP — Reliability / Idempotency** / 保留 | WMS 对 RCS 持久化后 ACK，WES 对 WMS 持久化后 ACK；基础层保存 Evidence、幂等应用并一次发布，插件只消费确定结果 | Transport Evidence Worker | 持久 Evidence 扫描、ACK 后唤醒 |
| R09 | **KEEP — Automation SOP** / 保留 | 首次 SCAN4 是现场排序事实；WMS 给正常放行资格；插件按首次到位顺序冻结 sequence，待资格闭合才进入 return_batch，不能按完成时间重排 | manual-picking SCAN/return 流程 | 持久 SCAN Evidence、插件推进 |
| R10 | **KEEP — Dependency Gating** / 已识别 SCAN3、SCAN4 料箱不等待其他料箱的未闭合命令；WMS completion、admission 的已知等待仍为 `DEFERRED`；其余调用点待审 | ECS 管设备物理互斥；插件只保留同一料箱或同一命令的因果依赖。当前其他调用点仍按 `workline_id + device_code` 检查未闭合命令，需逐项区分业务顺序与物理准入 | SCAN/WMS Evidence 的业务处理唯一 owner 为 Fact Processor → 插件；DeviceCommand 结果由 Device Worker 处理 | Fact Processor 按持久 Evidence、`decision_next_attempt_at` 与过期 claim 重领；SCAN2 的 PostgreSQL + 真实 Celery Worker 重领已验证，WMS completion、admission、已识别 SCAN3/SCAN4 分支快速语义测试已验证 |
| R11 | **KEEP — Automation SOP** / 四入口审查已核对 plan 来源与目标，父状态门禁待切片验证 | WMS 拥有 plan 内容；宿主只保存、关联和可靠建动作，插件决定 SOP。`PickingTaskPlanActivationService` 按已存 member 组成 Fact 并校验插件动作不越出 Fact，未见自行选择来源或目标；`EXECUTING` 门禁是否误停已成立 member 的后续动作仍需按合同验证 | Plan activation Worker / 插件 | WMS plan Evidence、持久任务扫描 |
| R12 | **KEEP — Fact Projection** / 字段级待核；当前入口未接线 | 现场系统拥有到位事实，WMS 拥有库存和业务位置；WES 仅投影 SOP 真正消费的事实。库存可用性、目标储位和业务资格由 WMS 决策 | 当前 `record_resource_fact` 与三个到位/离开方法只有模块内部和测试调用；无生产持久待办可指定异步 owner | 不适用当前生产链；不存在该服务的独立 replay Worker。先核对其他写入路径与实际消费者，再决定保留字段或删除失用能力 |
| R13 | **KEEP — Dependency Gating** / 目前仅诊断路径已证实 | ECS 拥有设备状态；WES 当前 manual-debug preflight 可以显示/校验已观察状态。若正常业务用它做物理准入，改由 ECS 接纳裁决 | manual debug 调用者；无持久待办 | 人工触发诊断；不适用自动唤醒 |
| R14 | **REMOVE — ECS/RCS Physical Control Duplication** / 当前无生产调用 | ECS 管设备派发和 in-flight 物理容量；`DeviceDispatchPolicy` 在生产树仅有定义，另有 load benchmark 实例化。删除旧策略及仅为它存在的负载场景，不移进新通用调度层 | 无生产 Next Owner | 无持久待办；不适用。删除前再核对动态装配与测试引用 |
| R15 | **KEEP — Reliability / Idempotency** / 桥接文件当前无生产调用 | 基础 Runtime 的 Evidence 能力保留；`ResourceWaitEvidence` 在生产树仅有定义，未见导入消费者。可删除失用桥接文件，不保留兼容转发 | 无生产 Next Owner | 无持久待办；不适用。删除前再核对动态装配与测试引用 |
| R16 | **KEEP — Reliability / Idempotency** / 架构验收 | 基础层负责显式装配及已有可靠义务；无插件运行必须继续成立。插件缺席只停止新业务 SOP，不丢既有可靠消息 | 各基础可靠 Worker | 各持久义务扫描、lease expiry |
| R17 | **KEEP — Reliability / Idempotency** / 随 R07 收缩 | 基础 Worker 保留 Evidence 恢复与结果一次性发布；仅删除因冗余超期恢复而失用的分支，并核对实际 Worker 注册和队列 | Transport submit/Evidence/outcome Worker | 持久状态扫描、lease expiry |
| R18 | **KEEP — Automation SOP** / WMS 顺序来源已核，具体成员义务的父状态门禁待修 | WMS 给任务池、`dispatch_sequence`、`not_before` 和 plan；WES 按已发布总序领取可执行任务、编排步骤。不得用父任务状态代替既有来源架面义务的有效性 | Prepare / plan Evidence Worker | WMS 事件、持久领取扫描 |
| R19 | **KEEP — Reliability / Idempotency** / 保留 | WMS 给 operation 业务结果；基础层保留固定 wire、可靠收发和幂等，operation 业务解释由插件消费，不复制 HTTP/receipt/重试 | WmsConfirmation / InboundEvidence Worker | 持久义务扫描、lease expiry、WMS Event |

四入口审查尚未发现 WES 自行计算库存、改写 WMS 来源/目标或重排业务优先级；`BinInboundBatchOwnerService` 曾用父任务 `EXECUTING` 推断既有货架面义务失效，现已删除这一资格推断，保留请求身份、WorkLine 归属、member 关联和 WMS 响应读取。其余订单/任务有效性、业务资格和终态仍须按合同逐点核对，证据不足保持待核。

### P0：Next Owner / Wake-up 静态核查（2026-09-23）

本轮沿现有 19 项中尚有 `待核` 的生产入口核对，并对 P0-A/B/C 做最小切片；不能把“有 Beat 任务”直接当作恢复已闭合。非终态对象的正确性来自持久状态和可重领查询；Celery 通知只缩短等待。

| 对象 / 候选 | 唯一 Next Owner、触发与 crash / lost-wake 恢复 | 结论及最小后续动作 |
| --- | --- | --- |
| Transport `PENDING`、未应用 Evidence、已确定但未投影结果（R03/R04） | Submit Worker 扫描未发送且无 Evidence 的 `PENDING`；Evidence Worker 扫描原 Evidence；Projection Worker 扫描终态成员。Beat 分别每 30/10/10 秒唤醒，claim 到期或事务回滚后重领。原身份与冻结请求体保持不变 | **主体闭合；P0-C 异常可见性已补**。生产创建路径在同一事务生成 Binding 和正 causal token；缺失时属于不变量破坏。独立查询记录稳定 reason、结构化错误日志和样本数，不静默丢弃，也不臆造恢复动作 |
| SCAN / WMS 业务 Evidence 等待旧 DeviceCommand 关闭（R10） | Fact Processor 重领持久 Evidence，插件判断前置事实；`DEFERRED` 用 `decision_next_attempt_at`、Beat 与过期 claim 恢复，DeviceCommand 有独立定时 Worker | **P0-A 恢复链已验证**：SCAN2 的独立 PostgreSQL + Redis + 真实 Celery Worker 测试证明同一 Evidence 在旧命令闭合后重领并发布；`_apply_completed` 和 `_apply_admission_result` 的等待有快速语义回归。SCAN4 已识别料箱的跨对象设备门禁在 P1 删除，首次到位事实立即冻结。其他设备级门禁待审；跨进程测试由测试 producer 定向发送任务，未验证生产 Beat 的周期触发 |
| plan activation 与 prepare（R11/R18） | Plan Worker 每 10 秒扫描活动工作线和 `EXECUTING` task，按已存 plan/member 创建动作；Prepare Worker 每 10 秒扫描活动工作线、领取 WMS 排队任务并创建 WMS Confirmation，后者由独立交付 Worker 扫描 pending/过期 lease | **P0-B 固定前 100 漏扫已修复**：两个服务均按 WorkLine ID 分页扫描，单条处理失败不阻断后续工作线；超过 100 条的合同与 PostgreSQL 回归已通过。`EXECUTION_COMPLETED` 后 member 是否仍有未创建 Action 义务继续按 WMS 合同核实 |
| ResourceProjectionService 与旧策略/桥接（R12/R14/R15） | `record_resource_fact` 及内部三个到位/离开方法没有生产调用；`DeviceDispatchPolicy`、`ResourceWaitEvidence` 只有定义/导出或负载测试引用 | **当前没有这三者自己的非终态持久待办，因此无 zombie owner**。R12 的字段来源和实际消费者仍须审查；R14/R15 先核对动态装配及测试再删除失用代码，不能把未接线服务称为可靠恢复能力 |
| 已创建 WMS Confirmation / 已应用待发布 InboundEvidence（R16/R19） | WMS Confirmation Worker 领取 `PENDING` 或过期 `DISPATCHING`，Beat 每 10 秒扫描，通信恢复复用原 identity/正文；Fact Processor 扫描已应用未发布 Evidence，claim 过期重领 | **恢复链闭合于活动 WorkLine 与已装配消费者边界**。`claim_decision_batch` 排除停用 WorkLine；若合同要求停线后仍发布已形成义务，须单独核对停线/归档语义，不能把停线误当作父 PickingTask 完成 |

P1 已确认 SCAN3 接收 SCAN1 NG 直达和 SCAN2 释放两个来源，不存在点1→点2 FIFO；已识别料箱可用自己的 Passage 和命令关联判断因果依赖，因此不再因另一未知料箱的 SCAN3 Command 未闭合而等待。SCAN4 已识别料箱的首次到位事实也不等待其他料箱的设备命令，避免遗漏实际出口顺序。原 SCAN4 跨进程重领测试已迁至仍有等待的 SCAN2 分支并通过。未知料箱重复扫码、SCAN1/SCAN2 的明确 FIFO 和 WMS 结果放行仍需逐调用点审查，不能按函数名批量删除。P3 `inbound_batch` 的 `409/422` 缺少可判定 Requirement 后果的结构化 reason，继续保持当前映射，不从 message 猜测 `MEMBER_CANCELLED` 或临时繁忙。尚未通过生产 Beat 周期触发、故障注入或真实 WMS/ECS 验证。

P1 剩余调用点分类（同属 R09/R10，不增加候选编号）：

| 入口 | 当前门禁的实际语义 | 裁决与下一切片 |
| --- | --- | --- |
| SCAN1 | `scan1_unclosed` 的同线 FIFO 与本对象重复检查是 SOP 因果；`has_unclosed_for_device` 另按整台设备阻止不同料箱 | 保留 FIFO/同对象幂等；设备级检查进入 ECS 物理准入删除候选，先验证当前 Evidence 的重领与命令身份 |
| SCAN2 | 当前 Passage 的 SCAN1 成功及明确 FIFO 队首是因果；`has_unclosed_for_device` 对其他料箱也生效 | 保留前两者；设备级检查单独裁决，不能把当前 SCAN2 恢复测试误当成永久保留该门禁的证据 |
| SCAN3 | 已识别料箱不再等其他料箱；本料箱 SCAN2 结果未决时原 Evidence `DEFERRED`，确定成功后重领并决定方向 | **Safety 切片已修**：直达且能唯一关联的箱按本点当前有效 `-B` 正常向前，不沿用 SCAN1 旧 NG；经过 SCAN2 的箱仍按 WMS 确定结果。`ACKNOWLEDGED/RECONCILING` 等可闭合状态不解释为 NG，终态 `TIMED_OUT` 不无限等待。无法关联 Passage 的有效 `-B` 如何建经过待核 |
| SCAN4 | 跨对象设备门禁已删；已识别正常料箱首次到位先冻结 Evidence 身份和时间，本料箱 SCAN3 结果未决时只延迟方向 Action | **Fact/Action 切片已修**：同一 Evidence 重领保持首次时间，命令仅创建一次；未知 Passage 或合同不允许的到位仍按异常路径处理 |
| WMS admission/completion | WMS Response Evidence 已保存；`has_unclosed_for_device(SCAN2)` 延迟业务结果应用和后继命令 | 前者不应因其他料箱的设备命令丢失；把结果应用与后继 Action 资格分开，逐分支保留真正的同对象因果等待 |

点位事实审查（仍属 R09/R10）：原始 `InboundEvidence` 在插件处理前已持久化，但 SCAN1/SCAN2 的 Passage 应用仍受历史命令、FIFO 和位置门禁影响。已确认 SCAN1 非 `-B` 直达 SCAN3、且本次经过能唯一关联时，SCAN3 当前有效 `-B` 足以正常放行；该历史 NG 覆盖当前 Fact 的分支已修正。无可关联 Passage 时的建经过语义仍待核；SCAN4 无法关联已有 SCAN3 Passage 时也未冻结本点首次到位投影。现场已确认 SCAN4 `MOVE_FORWARD` 后料箱按序进入 `RETURN_BUFFER`，按现行合同以匹配的 ECS `SUCCESS` 作为资格事实；`return_batch` 只消化已具备资格的 FIFO 连续队首，不能按命令完成先后重排。当前排序仍使用 WES `received_at`；ECS 跨箱回调物理排序保证尚未确认，旧厂商资料只提供事件时间字段。直达箱当前扫码授权不等于经过 SCAN2 的箱可绕过 WMS 结果；本地接收顺序也不能冒充未确认的现场顺序。

身份切片：同一 PickingTask 的同一物理料箱允许多次经过；`work_completed.data.admission_operation_id` 关联本次准入 Action 和 Passage，信封顶层 `operation_id` 仍是完成事件投递身份。插件按原准入身份查询并核对 task/bin，旧 `(task_id, bin_code)` 终态唯一索引已退出；同箱两次经过、迟到和重复完成由聚焦测试覆盖，迁移与接收已在独立临时 PostgreSQL 验证。SCAN1/SCAN3 的新 Passage 创建及关联规则不在本切片内，不能因完成事件身份已修复而推定点位身份也已闭合。

左边界因果审查：`plan_delta` 连续版本写入 `PickingTask.last_applied_plan_revision`；`PickingTaskBinSourceRack` 保存成员的 `plan_revision/source_evidence_id/cancelled_evidence_id`，同一任务的同架面可由更高 revision 新增独立成员。`inbound_batch` 冻结请求现以 `task_id + plan_revision + rack_id + rack_face` 指向原成员，`operation_id` 关联 `WmsConfirmation` 和 Response Evidence；插件按同一 revision 关联结果，V1 的迟到结果不能结清 V2。CTU01 按原计划 Evidence 与 rack 去重；取消和直接取料面完成事实也按 revision 命中成员。本次身份修正不涉及 SCAN4 FIFO 或公共包抽取。

**历史关联只能解释 Evidence，不能批准下一 Action。** `scan_flow._apply_batch_result` 已按冻结请求的 `task_id` 找原任务和成员，不再依赖工作线当前 `EXECUTING` 父任务；已取消成员的迟到 `READY` 仍被匹配和应用为 Evidence，但不创建 BIN_MOVE。`batch_driver` 对已到位、尚未形成的取消面继续创建当前合同要求的首次 `inbound_batch` 义务；已有结果后不再追加入站分段，也不向尚未到位的已取消后续面创建旋转 Action。`PickingTaskBinSourceRack` 只可作为当前 Requirement 的最小查询投影与因果索引，不能作为物理占用、准入或永久历史围栏；逐消费者删除失用字段/查询，若现有 Evidence 与身份足以替代该表，就直接删除，不为保留旧表建设重放框架。系统尚未发布，开发数据可清理，不能以旧数据重建成本作为保留理由。

`PickingTaskBinSourceRack` 生产读取用途盘点（不据此预设整表保留）：

| 消费者 | 当前用途 | 当前裁决 |
| --- | --- | --- |
| `plan_delta_repository` 接收/取消、`picking_task_plan_activation`、`batch_driver._submit_source_racks` | 当前成员集合、取消 Evidence、原计划 Evidence 与同一步骤身份 | 当前 Requirement 投影和 Action 幂等；只读未取消成员可创建新 CTU01 |
| `bin_batch` owner、`scan_flow._apply_batch_result` | 冻结义务的原成员关联；迟到 Response 的当前动作资格 | 派发保留历史关联；插件已将“历史存在”与“当前未取消”拆开，取消后不创建新 BIN_MOVE |
| `batch_driver._advance_current_rack`、`completion_repository`、`plan_delta_repository` 的 completed-owner 查询 | 已到位来源的后续 SOP、完成/离场及原 Transport 身份关联 | 允许读取历史因果以收口已发动作；新分段和新旋转已按成员取消情况停止。其余投影是否仍阻挡独立动作须按调用点继续缩减 |
| `scan_flow` 的 Transport Result 与 SCAN1 来源匹配、`workline_integration_debug` | 原 Action/来源解释、扫码追溯与诊断 | 历史关联不自动授予新物理动作；逐条核对是否能直接使用原 Binding、冻结请求和 Evidence 替代历史成员查询 |

## 第二切片：WMS 业务判断边界

本切片沿四个入口检查“解释 WMS Intent”与“替 WMS 决定业务”。不新增候选编号；下列结论回填 R11/R18/R19 和 Lifecycle 横向维度。`inbound_batch` 已沿“到位事实 → 唯一请求义务 → 父状态变化 → 真实 worker 派发 → WMS 结果 Evidence → 重复派发 NOOP”完成纵向验证；业务产生与可靠交付分别由插件和基础层负责。

| 入口 | 权威 owner 与 WES 当前判断 | 分类、处置与承接者 | 直接消费者与测试 owner |
| --- | --- | --- | --- |
| `picking_task_prepare.py` → `claim_next_queued` | WMS 发布 `dispatch_sequence`、`not_before`、WorkLine 和 `task_type`；WES 按这些已存字段排序并原子领取，插件选择匹配线型的自动化流程 | **KEEP — Automation SOP / Reliability**。单线已有任务、WorkLine 装配和 prepare 可靠义务由 WES 保留；没有发现 WES 自算业务优先级或库存资格 | prepare worker、`PickingTaskRepository`、WMS Confirmation；`test_prepare_service.py`、`test_prepare_postgresql.py` |
| `picking_task_plan_activation.py` | WMS 提供 plan revision、来源架面和目标架；宿主组装已存 Fact，插件选择设备 Action，宿主核对动作没有越出冻结 Fact | **KEEP — Automation SOP / Idempotency**。保留身份、版本和越界校验；父级 `EXECUTING` 门禁不能被复用为已成立 member Action 的失效证据，按具体步骤另审 | plan activation worker、manual-picking plan handler、Transport binding；plan activation 合同/集成测试与插件 plan 测试 |
| `bin_batch.py` 的 `BinInboundBatchOwnerService` | WMS 取消的直接边界是来源架面 member；原 `validate_owner` 在创建和每次派发时要求父任务 `EXECUTING`，会使已落库义务在父任务完成后进入 `RECONCILING` | **REMOVE — WMS Business Duplication / 已修正**：删除父状态和 `MANUAL` 插件类型资格推断；保留冻结请求的 task、WorkLine 和原 rack/face 关联。已到位后取消仍请求 WMS 返回 `RACK_FACE_DONE`；未到位且已取消则不创建新请求。身份 owner 已移到零插件也可运行的基础装配，`BinBatchScheduler/ResultReader` 保持可靠收发与 typed outcome | `WmsConfirmationLifecycleService.create_or_get` / `_dispatch_claimed`、manual-picking batch driver；`test_bin_batch_scheduler.py`、`test_inbound_batch_production_wiring.py`、插件来源架取消测试 |
| `return_batch_owner.py` | WMS 决定退箱目标与业务资格；WES 当前只校验活动 WorkLine 和冻结请求中的线码，不决定 FIFO 或库存 | **KEEP — Reliability / Idempotency**。WorkLine 归属保留，SCAN4 首次顺序和正常放行资格由插件判断；旧义务遇 WorkLine 停用的可靠收口另按 WorkLine 合同核验，不扩成 WMS 业务决策 | WMS Confirmation 派发、manual-picking return flow；`test_return_batch.py`、`test_return_batch_production_wiring.py`、插件 return 测试 |

## 横向审查：Action 依据与事实投影

以下是既有 R03/R04/R09/R11/R12 的交叉证据，不另立候选。每次修复先回答“这个 Action 为什么存在”，再沿 `Requirement 有效 → 目标事实未满足 → 同一次尝试未在途 → 前次 Action 明确终态 → 退避到期` 判断是否创建新 Action；已在途 Action 的设备取消按设备合同处理，不把业务取消当成物理回滚。

| 维度与调用链 | 直接依据、权威 owner 与现状 | 裁决、消费者及测试 owner |
| --- | --- | --- |
| Lifecycle / Projection：`TransportService` 即时结果及回放 → `PositionProjectionService` | Rack Move/Rotate 服务于冻结的货架/面 Requirement；WMS member 取消只决定后续 SOP，RCS/ECS 终态决定位置事实。原基础 Transport 与候选查询用父级 `PickingTask.status ∈ {PREPARING, EXECUTING}` 拦截投影 | **已修正并聚焦验证**：父状态与历史 drain 是否仍为当前业务 owner 不再拦截物理事实；Binding 身份与已有 causal token / source transport / effect phase 决定当前投影是否更新。消费者是 `rack_readiness`、`scan_flow`、plan owner 查询；测试 owner 为 `tests/runtime/transport/test_transport_projection_replay.py`、`tests/runtime/execution/test_position_projection.py`、`tests/integration/transport/test_transport_projection_candidate_postgresql.py`。旧事实仍由 Transport Evidence 保存，不能据此触发已取消 SOP |
| Lifecycle：`batch_driver.advance_in_session` 的 `_submit_source_racks`、`_advance_current_rack` 及 `BinInboundBatchOwnerService.validate_owner` | 新 CTU01 服务于尚有效的 member；已到位货架面即使随后取消仍须请求 WMS 给出最终 `RACK_FACE_DONE`。旧实现的 `allow_inbound` 和 owner 派发门禁都依赖父状态/取消标记 | **已拆分**：未到位且取消不创建新 CTU01/批次请求；原 Transport 证明到位后，该面按唯一身份请求 WMS 最终批次；已持久化义务不再受父状态重新裁决。插件测试 owner 为 `test_source_progression.py`、`test_batch_flow.py`，基础 owner 与真实 worker owner 为 `test_bin_batch_scheduler.py`、`test_inbound_batch_production_wiring.py` |
| Lifecycle：`picking_task_plan_activation._activate_workline`、`picking_task_cancel.apply` | WMS 计划接收、取消范围和任务阶段由外部合同约束；宿主当前只领取 `EXECUTING` 任务，`PLAN_MEMBERS` 取消也只接受 `EXECUTING` | **合同边界待核**：先确认 completed 后 WMS 是否仍可撤销 member，再决定是修宿主准入还是只修已存在 Action 的继续执行；测试 owner 为 outbound picking 合同/集成测试。不得以插件重试需要为由私自改写 WMS 合同 |
| Projection：`ResourceProjectionService.record_resource_fact` → `RACK_ARRIVED` / `BIN_ARRIVED` / `BIN_DEPARTED` / material mount 事件；`scan_flow` → `apply_device_position_result` | 当前检查到的写入由资源事实、设备 SCAN 或 Transport Evidence 触发，未见 `PickingTask.status` 或取消事件直接赋值为新物理位置；事实 owner 是设备/RCS/ECS，WMS 库存另有权威 | **KEEP — Fact Projection**，继续核对来源身份、版本和消费者；不能因业务取消清空已确认到位。测试 owner 为 Resource 投影测试、`test_scan_flow.py` 和 Transport 投影测试。此项是已检查写路径的结论，不等于全仓所有投影已验证 |
| Projection：`_invalidate_submission_positions_if_current` → `invalidate_transport_member` | Transport 接纳或投递未知使“当前所在位置”待核，但不是业务取消或确认离开；当前服务将 `position_unknown=True`，保留原 `position_json`，并依因果 token 防旧结果覆盖 | **语义待核**：区分“原到位事实已发生”与“对象当前是否仍在该位置”；不要把 unknown 展示成历史事实被删除。测试 owner 为 Transport 投影回放及位置投影测试 |
| Causality：`scan_flow._apply_scan3/_apply_scan4`、`passage_repository.unfinished_return_prefix_for_update` | SCAN3/SCAN4 各自首次 Evidence 写入后重复消息不重写；`scan4_received_at + scan4_evidence_id` 是出口先后顺序事实，业务 READY 可晚于它 | **KEEP — Automation SOP / Fact Projection**：迟到或重复 SCAN 不更改首次 SCAN4 顺序；return_batch 按首次到位排序且等待正常放行资格。测试 owner 为插件 `test_scan_flow.py`、`test_batch_flow.py`。当前位置采用最新权威因果事实，不能套用 SCAN4 的首次规则 |
| Causality：`TransportDecisionBinding.causal_token` → `compare_projection_sources` | token 由 WES 创建 Binding 时分配，单独不代表物理执行顺序。RCS 已确认：同一货架或料箱的前一 Transport 未释放时，第二个 Transport 被拒绝，不会并发实际执行。被拒绝的动作没有位置结果；后续已接纳动作与前一动作形成串行因果关系 | **KEEP — Fact Projection**：只用 token、原 Transport 身份与结果阶段防止旧已接纳动作的迟到/重复结果覆盖后续事实；不以 token 预测货架到位顺序或替 RCS 做接纳。若 RCS 此合同变化，必须先获得跨任务物理顺序依据再继续使用该比较。测试 owner 为 `test_position_projection.py` 与 Transport 真实结果集成测试 |

## 第一切片：manual-picking CTU01 准入

当前链路：WMS plan / drain reservation → 插件 `batch_driver.py` 按已确认货架和步骤去重 → `ReliableRackTransportCreator` 固定同一步骤 `client_request_id`，创建 Transport → Worker 提交 WMS/RCS → 权威结果和到位事实落库 → `rack_readiness.py` 放行依赖该架的下一步。`PENDING` 是本地待提交义务，不能视为 RCS 已并发接纳；RCS 裁决物理容量与实际到位顺序。

| 判断点 | 处置与原因 | 承接测试 |
| --- | --- | --- |
| `capacity - occupied/fenced` 限制 CTU01 创建 | 物理接纳交 ECS/RCS；若确有业务 SOP 并发度，应独立命名、定义业务依据和插件 owner，不能沿用设备容量 | `test_source_progression.py`、`test_batch_repository.py`、`test_rack_cycle_postgresql.py` 的物理准入断言重审 |
| `decided` 与 `repository.transport(..., DRAIN_RACK_IN_STEP, rack_id)` | 保留同一 plan/drain 步骤只创建一次动作的身份约束 | 插件计划/货架测试及基础 binding 幂等测试各证其 owner |
| Transport 成员、精确位置和 rack face 核对 | 保留对象级到位依赖；B 先到位即可推进 B，不等待先下发的 A | `test_rack_readiness.py`、`test_source_progression.py` |
| `current_rack_count == 0`、`has_unclosed_rack_action` | 逐调用点拆分：前者若拒绝其他独立 CTU01，交 RCS；后者若保护同架当前 SOP 未闭合动作，保留 | drain 与当前架测试，按拆分后的能力分别归属 |

Wave 1 已将 SRS、Transport/WMS 合同和 `AGENTS.md` 中 CTU01 物理准入条文改为“原执行身份、Evidence、对象级因果依赖保留；独立动作不由 WES 围栏阻止”。WMS 已确认冻结 Transport 请求稳定映射到原 RCS `request_id`。迁移链和 CTU01 PostgreSQL 并发切片已通过；剩余退出条件是核对所有当前有效文档的一致性和当前代码快照的完整门禁。

## 四个 Wave 与退出门槛

| Wave | 交付与退出门槛 |
| --- | --- |
| 1 — Architecture Freeze | 修订 SRS、Transport/WMS 合同、`AGENTS.md`；写清三项外部可靠性事实、同 ID 技术重试与新 ID 业务重试、SOP 并发度与物理容量；冲突条文清零后冻结裁决。纯文档不新增 pytest |
| 2 — Remove Physical Control Duplication | 先完成 R01/R02/R03 的调用、字段/索引、测试和文档闭环；保留身份与到位依赖，删除已证实的物理裁决；R04/R10/R13/R14/R15 按消费者逐点核查；旧符号和动态入口残留清零 |
| 3 — Remove Business Duplication | 按 WMS 决策队列逐项查真实调用链；只删除有权威 owner 与承接测试的重复判断，保留插件 SOP；只清理失用投影与过期过程文档，`docs/hardware/` 不动 |
| 4 — Collapse Tests & HEAVY | 先确定每个行为的唯一测试 owner，再删重复/废弃测试；插件、Transport、可靠投递、Schema/DB、Worker/Wiring 分开验证；最后评估并简化 `heavy-test-impact.toml` |

2026-09-23 验证快照：Wave 1 的当前权威文本已统一到“WMS 意图、WES SOP、ECS/RCS 物理执行、事实推进”；CTU01 的物理窗口、`occupied/fenced` 查询及对应旧测试已删除。Transport 原身份重提、已接纳任务不因等待超时生成 UNKNOWN、迁移链和 PostgreSQL CTU01 并发切片已验证。QUALITY 与当前 selector 选中的 HEAVY 已通过。manual-picking 对明确 `CANCELLED` 的货架 Action，按原业务依据仍有效、目标事实未满足和前次终态已确认决定是否创建新身份的重试；同步 `REJECTED` 的线上原因码仍待确认。

每项开工前记录当前 Git 快照、权威 owner、所有调用者、保留的因果条件、承接测试、字段/Repository/Worker/文档依赖；完成后扫描旧符号。保留字段尽量使用 `rack_arrived_at_workstation`、`picking_session_active`、`transport_result_pending`、`scan4_received_at` 等事实语义；确定消费者与语义后再改名，避免机械全仓替换。事实投影只为当前 SOP 的真实消费建字段，不建设第二套仓库 Digital Twin。

最终架构验收：无插件启动且基础可靠能力可独立测试；新增 SOP 主要只需新增业务插件、复用 Port/SDK、插件测试与少量装配，而不必同步修改 Transport、Resource、Execution、Runtime 和大量 HEAVY 映射。HEAVY 触发率只是后果，不是主指标。

## 覆盖范围与下一步

### RECONCILING 横向审查（按现有候选跟踪，不增加编号）

| 调用链 | 当前证据与分类 | 后续判断 |
| --- | --- | --- |
| `WmsConfirmationService` + `receive_json` | `NOT_SENT / DELIVERY_UNKNOWN / UNAVAILABLE` 保留原 `operation_id`、冻结请求并回到 `PENDING`；领取租约过期也可重领。`DETERMINATE` 的 Response Evidence 与 `COMPLETED` 同事务提交 | **KEEP — Reliability**。WMS 重放首次完整响应是合同要求；远端处理后本地崩溃窗口尚无故障注入或真实 WMS 验证 |
| WMS operation adapters | 冻结请求身份不符、响应 identity/结构不符、未定义 code 及部分明确 `409/422` 均映射 `RECONCILING` | **逐 operation 核对**：前三者可能是未解合同违例；`409/422` 只有在对应合同无法给出确定处理时才应对账，不能按 HTTP 类别一律推断 |
| Transport submit / Evidence | pre-ACK `SUBMIT_DELIVERY_UNKNOWN` 留在 `PENDING` 并按原身份重提；**已修正**：`TRANSPORT_EVIDENCE_PENDING` 也留在 `PENDING`，提交查询排除它，Evidence Worker 自动重领并应用；位置未知、Evidence 冲突和提交冲突另有 `RECONCILING` 路径 | **KEEP — Reliability / Idempotency**。回归证明失去唤醒后仍重放原 Evidence，且待应用期间不重提交物理任务；不能因名称相同就删除真正的位置未知保护 |
| manual-picking / WMS inbound Evidence | 插件读取已有 `RECONCILING` 以暂停依赖步骤；SCAN handler 对身份、线体或结果不匹配返回对账；WMS issued/queue changed 对身份、版本或引用冲突保存冲突 Evidence | **保留因果依赖**，逐分支核对能否由原身份重放或合同确定结果自动闭合；业务插件不应解释 HTTP、租约或 `DELIVERY_UNKNOWN` |

这是一轮生产调用点静态审查，不等于全部路径的动态验证；先明确每个分支的合同语义和测试 owner，再做纵向代码切片。`inbound_batch` 的同身份、同请求重放返回首次完整业务响应已写入当前合同，不能把该合同要求表述为真实 WMS 联调已验收。

`inbound_batch` Adapter 的最小合同语义表（同一 `operation_id` 与冻结正文）：

| Typed Outcome / reason | Requirement Impact | Next Owner / 当前处理 |
| --- | --- | --- |
| `200 / DECIDED / READY` | WMS 冻结该面最终有序料箱清单；继续对应 Bin SOP | manual-picking 插件；Adapter `DETERMINATE`，Response Evidence 持久化后闭合义务 |
| `200 / DECIDED / RACK_FACE_DONE` | 该面没有待搬料箱；对应批次需求闭合 | manual-picking 插件；同上。原身份重放仍返回首次完整 `DECIDED`，不返回简化 `DUPLICATE` |
| `503 / UNAVAILABLE`、timeout、连接中断或租约过期 | 尚无确定业务结果；Requirement 不因通信异常改变 | WmsConfirmation Worker；原 identity 与冻结正文重提，持久扫描/lease expiry 唤醒 |
| `409 / CONFLICT / IDEMPOTENCY_CONFLICT` | 原 identity 与请求正文不一致，无法安全改写旧请求 | Reconciliation；当前 Adapter `RECONCILING` |
| `409 / CONFLICT / REVISION_CONFLICT、STATE_CONFLICT、REFERENCE_CONFLICT` | WMS 已明确拒绝，但仅凭 reason 不能推断 member 取消或 Requirement 失效，也不能换 identity 重试 | 当前 Reconciliation；若逐 reason 合同补齐安全业务后果，再交插件决定新 Action |
| `422 / REJECTED / INVALID_ENVELOPE、UNSUPPORTED_OPERATION、INVALID_DATA` | 请求被明确拒绝；不是 `READY / RACK_FACE_DONE`，也没有已批准的业务重试规则 | 当前 Reconciliation / 集成合同 owner；修正合同或请求后再确定恢复方式，不原样无限重试 |
| 身份不符、未定义 code、Response 不合冻结请求 | 当前合同无法确定唯一安全结果 | Reconciliation；保留原义务与收到的 Evidence |

当前 `inbound_batch` 合同**没有** `REJECTED / member cancelled` 或“临时拒绝”reason；不能从 `INVALID_DATA` 等现有 code 推断这两种业务语义。这张表只记录已确认合同和当前实现，不新增结果 enum；其他 operation 的 `409/422` 须分别核对，不照搬本表。

Transport 首切片验证：既有回调先于任务登记、丢失唤醒时，待应用任务保持 `PENDING` 且不再次提交；原 Evidence 经 Worker 重领后完成。独立 PostgreSQL 上，两种任务/回调提交顺序及 Projection 首次应用失败后的事务回滚、租约过期重放共 3 个集成场景通过。`inbound_batch` 的 `409/422` typed decoder 虽有 `OperationConflict / OperationRejected` 类型，当前 Adapter 仍使义务进入 `RECONCILING`，插件未消费这两个结果；需先逐原因码确定业务后果，再决定是否打通确定失败路径。

已对 `src/` 与 `workline_plugins/` 的父级状态判断、位置/占用写入做全局静态检索，并沿 Transport、Execution、manual-picking、WMS outbound 与 Resource 的高风险调用链核对；这不是逐文件的动态行为证明。Transport 父状态投影门禁和 `inbound_batch` 已有义务派发门禁已移除；下一轮逐动作确认其他插件与 WMS 合同中的阶段判断。Resource 投影的来源身份与 Runtime 消费者继续按四个横向维度核验。每项需标明**权威事实来源、产生的动作、直接消费者、测试 owner**。`admin/auth/sys` 等非业务/物理调度模块仅做边界抽查，不因名称加入删除范围。
