# 人工拣料旧排空异常退出与独立任务继续执行 SPEC

状态：工程评审完成，D1–D5 已逐项确认；下一步制定实施计划。本文不授权直接修改生产代码或服务器。

## 1. 目标与已确认需求

WMS 拥有当前业务意图、库存和人工回架处理；人工处理结果没有通知 WES 的途径。现场不增加按钮、确认、填单或补报操作。WES 保留旧过程为异常记录，不伪造 SUCCEEDED，不要求人工对账；独立后续任务不能被旧记录永久阻塞。

成功标准：KT16 类场景下新任务可自动准备和执行；旧异常排空不再创建后继动作或业务重试；原请求、接纳与迟到结果仍可追溯；重启后行为不反转。

范围：manual-picking 排空生命周期、PickingTask 准备/计划准入、排空恢复与事实消费。无新增 WMS operation、现场入口、通用恢复框架或库存/物理位置管理。

## 2. 当前证据与问题

- `deployment/plugin_composition.py` 将 `DrainRepository.is_reserved` 注入 prepare、plan activation 和事实处理路径。
- `DrainRepository.current` 从 WmsConfirmation 历史尾部推导当前排空；没有对应离场 Transport 时持续 reserved。
- `ManualPickingDrainFlow.decide_in_session` 和 `ManualPickingBatchDriver._advance_drain` 持续使用该决定；只去掉准入检查会留下旧流程继续创建动作的风险。
- 实例：drain `01a0f49e-c7ee-7c10-991d-58e841b4ef3b`，货架 `510017/270`；返料 `transport-17cfb9bc-7658-47a7-afa4-1c601f0089e4` 将箱子 `A000001624` 从 CNV0302 搬向 `510017B3F2C101`，WES 仅收到 ACCEPTED。用户确认 RCS 人工取消、WMS 人工回架。
- 新任务 `A86FE0A0825564E29B57D2FBB692B9A08` 因上述旧 drain 保持 QUEUED。2026-10-09 已经用户授权定向删除其 completed drain Confirmation，保留 Evidence/Transport/绑定，任务自动进入 PREPARING，WMS 返回 PREPARE_ACCEPTED。正式实现不再删除记录。

以上实例是设计依据，不把用户口述转成设备结果，不作为自动算法的输入。

## 3. 核心语义

业务自动化退出与物理执行终态分开：旧 drain 可以停止自动推进，原 Transport 仍可能 ACCEPTED 或 RECONCILING。退出记录表示“WES 放弃本次排空的后续业务自动化并保留异常”，不表示物理成功、失败、取消、位置已知或 WMS reservation 已释放。

新任务到达只授权新任务，不证明旧动作终态。独立动作可提交；直接依赖仍有门禁。不得换身份创建旧未知搬运的等价动作，不得因节点出现新动作就推定旧箱子已回架。

## 4. 自动触发与状态转换

采用最小的插件本地排空执行记录，区分 ACTIVE、COMPLETED、EXCEPTION_RETIRED。它是本地编排状态，不替换 WmsConfirmation 的可靠交付状态或 Transport 状态。

### 4.1 独立任务准入

prepare 不以 drain 存在作为整线阻塞；仍保留活动 PickingTask 唯一性、工作线配置和当前任务自身准入。plan activation/FactProcessor 同步移除同类全局门闩，按当前任务/对象/动作身份判定资格。正常排空可以与独立任务共存，不因新任务到达被取消。

### 4.2 异常退出条件

持久扫描在工作线事务锁内同时满足下列条件时执行 ACTIVE → EXCEPTION_RETIRED：

1. 排空有匹配且已应用的 READY 决定，能冻结原 operation/evidence 身份。
2. 当前存在有效、未取消的后续 PickingTask（QUEUED、PREPARING 或 EXECUTING），它与该 drain 无直接动作依赖。
3. 排空存在导致其后继无法确定推进的直接动作：明确非成功终态且插件确认本次不再自动业务重试；或已明确 ACCEPTED 的 Transport 超过其冻结 result_deadline_at 仍无匹配最终结果；或已经进入物理结果未知的 RECONCILING。

超时只触发本地异常退出，不写 Transport FAILED。发送未知且未明确接纳的请求不由这个规则取消，原可靠发送义务继续用原身份和正文；没有 READY 的 WAIT/请求交付也不伪造异常物理执行。

若没有新任务，维持现有直接动作处理，不靠年龄批量关闭所有历史。正常有进展、未达到上述条件的 drain 继续执行。扫描不需要 WMS 人工处理消息。

reason_code 固定为 DRAIN_ACTION_TERMINAL_EXCEPTION、DRAIN_RESULT_OVERDUE 或 DRAIN_RESULT_UNKNOWN；优先级依此顺序，选择确定的一条 blocker 身份，避免扫描结果随排序漂移。

### 4.3 正常闭合

按当前合同，各 reservation 货架离场已明确接纳、正常排空完成时 ACTIVE → COMPLETED。退出正常/异常记录不得再次成为 current，不能回退到历史前驱。不是简单过滤最新一条后重新选取更旧记录。

正常离场任务不会被 RCS 取消，不为假设中的取消另建正常流程。保留现有异常取消结果的对象级恢复：COMPLETED 不重新打开整次排空，但原离场 Requirement 仍有效、目标尚未由权威事实满足且原 Action 已明确终态时，可以按现有合同创建新身份的业务重试。EXCEPTION_RETIRED 禁止该 drain 的业务重试；两者均保留原身份技术重试与真实结果接收，必要引用在原对象整体闭合前仍属运行数据。

## 5. 最小持久化方案与必要性

增加插件拥有的 `ManualPickingDrainExecution`，放入 manual-picking application 模型/Repository，并由 deployment 显式关联迁移。

| 数据 | 用途与必要性 |
| --- | --- |
| workline_id、drain_operation_id、decision_evidence_id | 冻结 owner 与本次决定；drain_operation_id 唯一，防重复创建 |
| status | 区分当前可推进与已经退出，防 worker 重启重开旧决定 |
| closed_at、reason_code | 异常/正常退出时间与原因；ACTIVE 时为空 |
| blocker_client_request_id、trigger_picking_task_id | 异常退出的直接动作和新业务依据；正常闭合为空 |
| execution → return_batch operation_id → 原 BinLineReturn.id 关联 | 创建义务时冻结排空归属和原候选执行身份；阻止退役后的迟到响应创建后继，并从义务创建时防止未决原候选被其它批次再次申请；复用现有 ID，不复制请求/响应或新增 wire 字段 |

复用项目既有主键、审计、版本、时间与外键约定。无需复制 racks、原请求、响应、Transport 结果或库存位置。

现有 WmsConfirmation 仅表示 WMS 请求交付完成；READY/COMPLETED 不代表排空执行结束。InboundEvidence 是不可改写的接收事实；window_released_at 属于具体搬运窗口，不能代表整个多架 drain。因此现有字段不足以持久表达本次退出。新模型仅承担这一缺口，不建全局异常状态机。

存量迁移：仅为尚有运行依赖的 drain 创建执行记录；依据明确离场接纳写 COMPLETED，否则 ACTIVE。无法判定时留 ACTIVE，由上述确定规则退出。禁止以“最新历史”复活已闭合链；迁移前枚举并冻结存量运行链身份，迁移后验证 current 选择与已闭合历史无依赖。

## 6. 退出事务与后续行为

同一工作线锁内重新读取 execution、当前任务和 blocker，条件仍成立才闭合；冻结原因和引用、停止新业务后继、解除该 drain 的本地编排窗口。未提交动作停止创建；已持久化可靠义务不得删除、改正文或停技术重试。

窗口解除仅表示本地业务责任结束，不证明地点为空。新物理任务由 ECS/RCS 接纳，禁止依据旧投影建立跨任务物理围栏。原 Transport/DeviceCommand 的对象因果依赖和已提交身份保留，不能转成“可等价重发”。

drain 创建 `return_batch` 可靠义务时，同事务持久冻结该义务所属的 drain execution 关联；不以当前 rack/face、箱码或历史时间重新推断归属。结果消费先按原关联核验业务资格；所属 execution 已 EXCEPTION_RETIRED 时，可靠保存响应并完成该响应的消费，不创建新的 BIN_MOVE，不改写已创建动作。实施计划先核对现有持久化结构能否承接这一必要关联，不新增 wire 字段或另建可靠交付机制。

退出后原料箱/批次不能重新入队或重新分配到新 return_batch。通过既有 Return/批次成员身份保持在途或异常归属，不凭新 PickingTask 的出现重放旧 FIFO 成员。新 SCAN 只有按现有合同可建立独立新业务轮次时才准入；不能跳过同一未闭合 FIFO 队头。

创建 drain 的 return_batch 义务时，沿原 execution 关联同事务冻结候选 BinLineReturn.id。在既有 WorkLine 事务内，drain_flow.return_in_session 和 batch_flow.advance_in_session 两个请求创建入口均核验原成员资格：已有未决 drain return_batch 的同一 Return.id 不得被另一个批次再次申请，无论原 execution 是 ACTIVE 还是 EXCEPTION_RETIRED；同 rack/face 既有门禁保持。响应未知时保留原身份和成员依赖；ACTIVE 收到确定 NO_BATCH 后按现有合同换面继续，不新增 Return 状态或容量预占机制。结果消费核验原成员；退役后尚未整体闭合的原成员不得被其它批次重新分配，即使它尚未创建 BIN_MOVE、原 return_state 仍为 READY。普通 ACTIVE drain 的 NO_BATCH/换面仍按现有合同推进。同箱码合法新轮次使用新的 Return.id，旧成员整体闭合后关联可清理；这些关联只约束原执行成员，不建立跨任务物理围栏，也不能通过从候选集合排除旧成员来越过未闭合物理 FIFO 队头。

若新任务实际上依赖同一旧箱子或同一未闭合操作，停在那个直接依赖步骤；不扩大为整线暂停。

## 7. 恢复、可靠义务与迟到事实

- execution 持久扫描是本地 ACTIVE 的 Next Owner；通过已有批量 driver 执行，不新增 Beat/队列。
- EXCEPTION_RETIRED 的 drain 只有历史记录，无业务后继 owner；其在途 Transport 仍由现有 Transport 结果接收/对账机制负责。
- WmsConfirmation 的未 ACK/未交付义务继续依冻结 identity 派发，不因本地退出丢弃。
- 原因果身份匹配的迟到结果先可靠留存；原对象可以闭合，必要时按现有因果 fencing 更新投影，但不得覆盖更晚事实。
- drain 的业务终态不重新打开；EXCEPTION_RETIRED 不再创建离场/旋转/返料或业务重试。COMPLETED 不重启整次排空，仅按 §4.3 保留原离场的对象级异常恢复；完成回调、事实重放、任务取消和 worker 重启不改变上述资格。
- 清除已可清理历史后，当前任务仍能启动与恢复；未闭合 Transport 所需引用仍属运行数据，不提前清理。

## 8. 合同调整与兼容边界

SRS 第 0 章不变量保持。修订 outbound picking 合同 §9.2.3：正常 drain 不被后续任务取消；独立 PickingTask 不受整线 reservation 阻塞；异常退出仅停止本地业务后继，不取消物理动作或释放 WMS 容量 reservation。

澄清现有“已创建 drain 链不会被后来任务绕过”和“无关 Transport 不构成 WorkLine 门闩”的适用范围。删除不再适用的“无业务表或字段”实现限制，保留 wire 字段不变。

不新增外部接口，无新 API 请求/响应示例。本文的 reason_code、模型和转换是拟批准内部合同；现有 WMS/RCS wire 保持原样。WMS reservation 的释放仍由 WMS 管理，本地退出不承诺其已释放；若 WMS 因 reservation 拒绝后续分配，保留该明确拒绝与现有重试语义，不伪造可用容量。

## 9. 实施变更面

- manual-picking：新增 execution 模型/Repository；drain_repository current/for_evidence；drain_flow、batch_driver、相关 completion/return/恢复调用点。
- deployment/plugin_composition：拆除全局 drain blocker，显式注册插件模型与当前编排策略。
- 宿主 prepare、plan activation、FactProcessor：核实 blocker 其他消费者；仅移除 manual drain 注入，不无边界删除公共扩展点。
- migrations：新表、约束/索引、存量运行链初始化；不改 Transport/WmsConfirmation 状态枚举。
- 合同 §9.2.3、TODOS、file_index 只同步本能力涉及内容。
- 各生产符号补丁前执行 GitNexus upstream impact；闭合直接/间接测试和精确 HEAVY mapping。

## 10. 验收矩阵

| 场景 | 预期 |
| --- | --- |
| 实例旧 ACCEPTED 过期 + 新任务 | 新任务可准备；旧 drain 自动异常退出，Transport 不改成功 |
| 正常 drain + 新任务 | 独立任务继续，正常 drain 不取消 |
| 旧动作明确失败/未知 | 满足触发条件时退出；不产生等价旧动作 |
| 同一箱子的直接依赖 | 只阻止直接依赖；不重新入 FIFO、不重复搬运 |
| 请求已创建、尚无 READY/BIN_MOVE 即退役 | 原 Return.id 归属持久保留，新批次不重新选择原成员；同箱码合法新 Return 不继承旧归属；重启后不反转 |
| ACTIVE 分配请求未决、不同 rack/face 并发申请 | 原 Return.id 从义务创建时即不可被另一批次再次申请；关联与请求同事务，回滚不留孤立关联；确定 NO_BATCH 后 ACTIVE 按原合同继续，响应未知保持原身份和依赖 |
| 多货架/多面 | 整次退出停止尚未创建后继；保留所有已提交动作和可靠义务 |
| 并发扫描/任务取消 | 工作线事务锁内重验，幂等一次转换 |
| 退出与迟到结果竞态 | 结果保存；业务终态不重开；最新投影不被覆盖 |
| 退役后的迟到 return_batch READY | 按冻结 drain execution 关联完成结果消费，不创建 BIN_MOVE；同 rack/face 被新任务复用也不改变原归属；退出/响应并发及重启重放遵守同一规则 |
| worker 重启/通知丢失 | 持久扫描恢复 ACTIVE，退出记录保持退出 |
| 正常 COMPLETED/历史清理 | 不回退旧 drain；独立运行不依赖已闭合历史 |
| 两种终态与原离场异常恢复 | 正常离场不设计 RCS 取消流程；COMPLETED 保留现有异常取消结果的原离场恢复，已满足目标不重试；EXCEPTION_RETIRED 禁止业务重试；重放不重开排空 |
| WAIT/发送未知/未 ACK | 沿原交付合同恢复，不冒充物理异常成功 |
| WMS reservation 未释放 | 不声称已释放；后续拒绝按真实响应处理 |

验证：插件纯策略 FAST、Repository/driver PostgreSQL 集成、真实 worker 的扫描恢复与迟到事实；宿主 blocker 相关聚焦测试；干净库迁移升级/降级/重升；QUALITY 和 selector 必选 HEAVY。后端不启动浏览器 QA，不把 Mock 当现场物理验收。

## 11. 交付边界

批准此 SPEC 后再制定实施计划，按高风险/schema 变更冻结变更清单并 TDD。实施完成不自动包含 Commit、Ship 或部署；按用户后续目标执行。不再提供人工结束按钮，不再以删除 Confirmation 作为恢复方式。

## 工程评审分析

评审基线：develop / 99ffe143；目标为本 SPEC，不评审或修改其它 dirty 文件。D1 保留完整范围；D2、D3、D4、D5 分别批准问题 1–4 的方案 A。四个问题均为拟实施设计缺口/回归风险，不表示退役功能已经实现或上线。

### What already exists

- DrainRepository、ManualPickingDrainFlow、ManualPickingBatchDriver：复用原决定读取、进场/旋转/回料/离场编排和持久扫描入口；新增状态消除“历史尾部即当前”的依据。
- ReturnRepository/BinLineReturn：复用单次 Return.id、首次 SCAN4 FIFO 事实和现有成员状态；必要关联不复制库存、位置或请求正文。
- WmsConfirmation、InboundEvidence、TransportTask、DeviceCommand、TransportDecisionBinding：复用可靠义务、原身份重提、结果接收、因果引用及事实投影，业务终态不替换其终态。
- WorkLine 行锁、deployment 显式 Composition/model 注册：复用既有事务和安装边界，不增加锁框架、动态 registry、Beat 或队列。

### Architecture review

问题 1（D2 A）：原 drain 与 return_batch 冻结关联，退役迟到响应只完成结果消费，不创建新 BIN_MOVE。
问题 2（D3 A）：正常 COMPLETED 与 EXCEPTION_RETIRED 的业务重试资格分开；正常离场不设计 RCS 取消流程，只保留已有对象级异常恢复。
问题 3（D4 A，测试追踪发现）：请求时冻结原 Return.id，退役后原未闭合候选不能被其它批次重分配；同箱码合法新轮次不继承旧归属。
问题 4（D5 A，Outside Voice 核验缩窄）：原成员关联从义务创建时生效；两个请求创建入口在同一 WorkLine 事务内阻止 ACTIVE/退役的未决原成员被跨 rack/face 再次申请。保留原 NO_BATCH/换面与同面门禁。
四个方案均已纳入本文和验收矩阵；无剩余架构决策。

```text
新 PickingTask -> 自身准入 -> prepare/plan/facts -> 独立自动化
                        （不查询全线 drain reservation 作门闩）

READY 已应用 -> ACTIVE execution -> 原 driver -> 原后继
                        |
                        +-- 正常排空、各架离场获接纳 -> COMPLETED
                        |                               不重开排空；
                        |                               仅保留原离场异常恢复资格
                        |
                        +-- 有效独立后续任务 + 确定 blocker
                            -> 锁内重验 -> EXCEPTION_RETIRED
                                           停止业务后继/业务重试

execution -> return_batch operation_id -> 原 Return.id
                      |
                      +-- 迟到 Response -> 原身份可靠留存/消费
                      |                      |
                      |                      +-- 原 ACTIVE：原成员/事实/合同核验后推进
                      |                      +-- 已退役：不创建新 BIN_MOVE
                      |
                      +-- 旧成员未闭合：保持归属，不被新批次重新分配

已持久化外部义务 -> 原 owner / 原身份技术恢复 -> 权威终态
原 Transport 迟到结果 -> Evidence -> 因果核验 -> 原对象闭合/当前投影
                                                不重开退役业务
```

实施时只在 execution 模型的状态含义和 drain_flow/scan_flow 的资格分流处放必要的小型 ASCII 图；不在每个 helper 重复整图。

### Code quality review

无新增问题。保持一个 execution 状态真源；沿已有 Repository/driver 和插件消费路径传播。不新建通用状态机、HTTP/outbox、异常恢复服务或重复 DTO 转换。D2/D4/D5 的关联须先核对既有持久结构可否承接，以最少字段/关系表达原归属；不能仅为行数少而按当前箱码、rack/face 或历史时间推断身份。

现有 for_evidence 的用途要保持明确：可定位原事实不等于有资格创建后继。已有“进场/旋转要求 current、离场可定位原 Evidence”的说明须在实施补丁中同步更新两种终态的差异。

### Test review

框架：pytest / pytest-asyncio。以下是源码级测试覆盖审阅；本轮没有运行测试，新增机制尚未实现。G1–G12 为必需实施测试组，U1–U4 为对应用户流程证明，均来自原成功标准和 D2–D5 已批准行为，不是新策略或可选测试深度。

```text
CODE PATHS                                      USER FLOWS
DrainRepository / execution                      U1 [GAP->PLAN, ->E2E]
 + [EXISTING ★★] READY/各架接纳闭合                 旧 ACCEPTED 超期 + 新任务
 |   test_drain_repository.py:23                   -> prepare 成功；旧 drain 退役
 + G1 [GAP->PLAN] 创建幂等、owner/Evidence 严格关联  -> 原 Transport 仍非伪造成功
 + G2 [GAP->PLAN] 两终态不作 current、不退回前驱
drain_flow / driver                              U2 [GAP->PLAN, ->E2E]
 + [EXISTING ★★] 正常 READY 复用/NO_BATCH 换面       正常 drain + 新任务
 |   test_drain_flow.py:117,135                     -> 两者独立继续，正常 drain 不取消
 + G3 [GAP->PLAN] 三类 blocker 正向/反向谓词        -> 真正同对象依赖只停其步骤
 + G4 [GAP->PLAN] 无任务/取消任务/无READY/期限前
 + G5 [GAP->PLAN] 锁内重验、双扫描、多架后继停建
Composition / prepare / activation / facts       U3 [GAP->PLAN, ->E2E]
 + [EXISTING ★★] 宿主注入 blocker 的通用语义        请求已发、结果尚未返回时退役
 |   test_prepare_service.py:161                   -> 原响应留存，不新增搬运
 |   test_plan_activation_service.py:181           -> 旧成员不重分配，同码新轮次独立
 |   test_fact_processor.py:602
 + G6 [GAP->PLAN] manual drain 不再注入三入口      U4 [GAP->PLAN, ->E2E]
scan_flow / batch_result / ReturnRepository        退出后重启、通知丢失、迟到结果
 + G7 [GAP->PLAN] 原 drain/Return.id 原子关联        -> 原义务恢复；业务终态不反转
 + G8 [GAP->PLAN] 迟到READY不新建/不进入无意义对账   -> 最新位置不被旧结果覆盖
 + G9 [GAP->PLAN] 未决跨面防重申请/退役防重分配/同码新ID/NO_BATCH
 + [EXISTING ★★★] 原离场换current后恢复/目标核验
 |   test_scan_flow.py:1099,1335,1399
 + G10 [GAP->PLAN] 两种终态对照、迟到事实因果核验
worker / migration
 + G11 [GAP->PLAN, ->E2E] 崩溃/重启/通知丢失恢复
 + G12 [GAP->PLAN, ->E2E] 干净库迁移/存量身份/
                         历史清理/降级重升

Legend: EXISTING = 已阅读既有测试源码，非本轮 fresh 通过证据
GAP->PLAN = 尚无新增机制的可执行测试，已列为实施必需证明
->E2E = PostgreSQL/真实 worker 集成，不代表浏览器或物理验收
SOURCE COVERAGE: 4/20 组有既有测试源码；16/20 组需实施证明
NEW MECHANISM: 0/16 组有本轮可执行通过证据；不可据图宣称测试已通过
```

测试所有权及具体断言：

| 组 | 主要 owner / 实施时路径 | 必需证明 |
| --- | --- | --- |
| G1–G2 | 插件 test_drain_repository.py；新增 test_drain_execution_postgresql.py | 同 operation 并发创建幂等、错 owner/Evidence 拒绝；正常/异常终态不回退前驱；删除已整体闭合历史后 current 仍正确 |
| G3–G5 | 插件 test_drain_flow.py、test_source_progression.py、test_rack_cycle_postgresql.py | 每个退出谓词的成立/不成立；ACCEPTED 期限前后、UNKNOWN 与技术发送未知区分；已取消任务不触发；工作线锁内重新核验；一架异常停止所有尚未提交后继，已提交身份保留 |
| G6 | 宿主三个既有聚焦测试文件；插件 test_rack_cycle_postgresql.py 的真实 Composition | **CRITICAL 回归**：只移除 manual drain 的注入，不删除其它 blocker 语义；prepare、plan activation、FactProcessor 均能推进独立任务 |
| G7–G9 | 插件 test_scan_flow.py、test_batch_flow.py、test_rack_cycle_postgresql.py | D2/D4/D5：两个请求入口在 WorkLine 事务内核验未决原成员；不同 rack/face 并发不得重复申请同一 Return.id；请求与原关联同事务，回滚无孤立关联；迟到 READY 保存/完成消费、不创建 BIN_MOVE；退役原 Return 不重分配；同码新 ID 不误关联；确定 NO_BATCH 后 ACTIVE 换面继续，未知响应保留依赖 |
| G10 | 插件 test_scan_flow.py、test_transport_outcome.py（现有文件） | **CRITICAL 回归**：D3 两终态对照；目标已满足不业务重试；原动作结果留存并按已有因果规则更新，不覆盖更新事实 |
| G11、U1–U4 | 插件 test_business_loop.py | 真实独占 worker 的退役/响应竞态、提交后崩溃、重启和通知丢失；保留原可靠义务；无按钮/填单/补报步骤 |
| G12 | 插件 test_drain_execution_postgresql.py；宿主迁移基础测试只证明通用 schema | 同一独占 PostgreSQL 的独立干净逻辑库；固定迁移存量身份；升级/降级/重升；不凭历史尾部复活旧链；未闭合原引用不清理 |

实施计划应确认上表现有文件名及 fixture 的直接/间接消费者；新增测试保持插件所有权，不导入核心测试树。G1–G12/U1–U4 是原验收与已批准方案的必要证明，无独立测试策略待决。

验证分两条显式执行链：插件 FAST、插件 PostgreSQL 与真实 worker 测试单独运行；核心 QUALITY、selector 输出的 HEAVY 与必要迁移单独执行。workline_plugins/** 被核心 selector 忽略，不能把 QUALITY/HEAVY 绿灯当作插件行为通过。实施计划须写明每条命令的数据库、Redis、worker readiness 和显式启用条件；环境 skip 不算证据。本轮不生成或运行生产测试。

### Performance review

无新增问题。新 current 应按 workline 和活动 execution 查询，精确 operation 身份使用唯一键；索引和约束在实施清单中随查询闭合。不扫描全部闭合历史、不按每次 wake 重建历史状态，不复制 racks/正文或建缓存计数。既有 Return 查询有固定 limit 与 FIFO 排序；候选关联沿原批次范围读取。沿既有 WorkLine/Confirmation 锁序验证，不在读取冻结响应时新增反向行锁；test_rack_cycle_postgresql.py:589 的锁序回归需保留。

### Failure modes

下表为实施验收要求；“计划覆盖”不表示已经实现或测试通过。

| 新路径 | 现实失败 | 测试 | 处理和可见结果 |
| --- | --- | --- | --- |
| READY → execution | 重复发布/并发创建、owner 不符 | G1 | 幂等一条；身份漂移明确拒绝，不静默改归属 |
| current 选择 | 退役后回退到旧前驱 | G2/G12 | 只认当前运行状态；闭合历史清理不影响新任务 |
| 退出判定 | 新任务刚取消或结果刚到达 | G3–G5 | 锁内重验；条件失效不退役，持久 reason/blocker 确定 |
| 三入口准入 | 漏拆一个注入，新任务仍等待 | G6/U1 | 三入口真实 Composition 覆盖；真实任务状态与拒绝原因可查 |
| 批次归属/成员冻结 | 请求事务回滚、晚到响应、同码再用 | G7–G9/U3 | 原关联原子提交；旧结果消费但不创动作；未闭合原成员不重分配 |
| 未决原候选选择 | 不同 rack/face 同时申请同一 READY Return | G7/G9/U2 | 两个请求入口在 WorkLine 事务内按原成员关联防重；未知响应保留依赖，确定 NO_BATCH 依原合同继续 |
| 多架推进 | 退役后另一架又创建旋转/离场 | G5/U1 | 统一资格停建；原已提交动作持续履约 |
| 原离场异常恢复 | 终态过滤误删已有恢复、已满足目标又重试 | G10 | 按 D3 区分两终态；正常离场不增加取消流程 |
| 迟到物理结果 | 覆盖更晚位置或重新打开业务 | G10/U4 | Evidence 留存、因果 fencing、原对象闭合，不重开业务 |
| 持久扫描恢复 | 提交后 worker 崩溃或通知丢失 | G11/U4 | 原 driver 与持久扫描再发现；退出不反转 |
| 迁移/清理 | 存量链误识别、清掉未闭合引用 | G12 | 迁移前冻结运行身份并验收；无法判定不伪造闭合；相关引用继续保留 |
| WMS reservation | 本地退役后 WMS 拒绝新分配 | U1/U2 | 按真实拒绝和现有重试语义处理，不声称容量已释放 |

关键缺口：问题 1–4 已通过 D2–D5 补入设计及必要测试；没有未处理的“无测试、无处理且静默”的新路径。运行实现与验收仍未发生。

### NOT in scope

- 生产代码、迁移脚本、Commit、Ship、部署：本轮只评审 SPEC，后续需制定并执行实施计划。
- 新 WMS operation、RCS 取消正常流程、人工结束按钮/填单/补报：需求未授权，也不解决本地可靠编排缺口。
- WMS reservation 自动释放、库存/位置主账或物理容量围栏：由 WMS/ECS/RCS 拥有。
- 通用异常状态机、恢复平台、新 Beat/队列、兼容分支：复用当前能力即可。
- 其它 dirty 测试和 TODO 清理：与本次评审无关，保留原现场。
- 新独立分发产物和浏览器/现场物理验收：本次未引入；后端软件测试不替代设备验收。

### TODOS.md

现有“旧排空异常退出设计待评审”条目已覆盖该能力，无新增独立待办。问题 1–4 属于本能力必须完成的工作，不拆成后续 TODO。本轮不修改已有 dirty TODOS.md；没有需逐项确认的新 TODO。

### Worktree parallelization strategy

Sequential implementation, no parallelization opportunity.
execution/关联、Repository、driver、事实消费和迁移共享同一插件模块及身份合同；先在一个工作区闭合这条内聚路径，再处理宿主 Composition 和验证。无需为形式拆 worktree 或实施 Subagent。

## Implementation Tasks

以下仅列评审发现产生的补充任务；原 §9 的完整实施范围仍保留。制定实施计划时合并基线工作与这些任务，不以此四项代替整个功能。

- [ ] **T1（P1，human: ~半天–1 天 / CC: ~30–60 分钟）** — manual-picking — 冻结 return_batch 原 drain 归属并收敛退役后的结果消费
  - Surfaced by: Architecture / 问题 1 / D2 A。
  - Files: drain_flow.py、drain_repository.py、scan_flow.py、batch_result.py、必要插件关联模型/Repository、插件 test_scan_flow.py 与 test_rack_cycle_postgresql.py。
  - Verify: G7–G8/U3；迟到 READY、rack/face 复用、退出/响应并发、重启；不改 wire、不复制可靠机制。
- [ ] **T2（P1，human: ~半天 / CC: ~30 分钟）** — manual-picking — 区分正常完成与异常退役的原离场恢复资格
  - Surfaced by: Architecture / 问题 2 / D3 A；正常离场不被 RCS 取消。
  - Files: drain_repository.py、scan_flow.py、test_drain_repository.py、test_scan_flow.py、合同 §9.2.3。
  - Verify: G10；现有原离场恢复与目标已满足回归；EXCEPTION_RETIRED 不业务重试，不新增正常取消机制。
- [ ] **T3（P1，human: ~半天–1 天 / CC: ~45–90 分钟）** — manual-picking — 冻结原 Return.id 并防止退役候选重新分配
  - Surfaced by: Test tracing / 问题 3 / D4 A。
  - Files: drain_flow.py、bin_line/return_repository.py、batch_result.py、T1 所属关联模型/Repository、插件 test_scan_flow.py/test_batch_flow.py/test_rack_cycle_postgresql.py。
  - Verify: G7/G9/U3；请求回滚、请求未返回即退役、新批次选择、同码新轮次、ACTIVE NO_BATCH/换面及重启。
- [ ] **T4（P1，human: ~半天 / CC: ~30–45 分钟）** — manual-picking — 从义务创建时校验未决原成员资格
  - Surfaced by: Outside Voice 核验 / 问题 4 / D5 A。
  - Files: drain_flow.py、batch_flow.py、bin_line/return_repository.py、T1/T3 所属关联模型/Repository、插件 test_batch_flow.py/test_rack_cycle_postgresql.py。
  - Verify: G7/G9/U2；不同 rack/face 的请求并发、事务回滚、确定 NO_BATCH 后继续、未知响应原身份与依赖；复用 T3 关联，不新增枚举或物理容量围栏。
_No new tasks from Code Quality or Performance._
测试审阅新增的证明要求随原功能和 T1–T4 实施，不额外创建测试策略任务。

### Suppressed findings

外部意见 2 要求强制拆分 current()：已批准 D2 按原 execution 关联消费，全局 drain 注入移除，不要求 current 同时承担历史 owner 与全线 blocker。属重复意见，confidence 4/10，不新增方法或任务。外部意见 1 的“任何第二次调用”过宽，保留现有同面门禁，仅接受 D5 核验后的跨 rack/face 未决原成员问题。迁移存量实例在本轮未查询服务器，具体身份集合和新索引查询计划由实施前置清单验证；不能把未知存量推测为已观察故障。

## 工程评审决策记录

评审目标：本 SPEC。范围选择 D1：用户回复 A，保留完整生命周期范围，复用现有 Repository/driver；此选择不授权生产代码、服务器、Commit 或部署。

### R1：drain return_batch 的冻结归属与退役后消费

Finding：问题 1 / P1 / confidence 9/10 / 本文 §6、§7（原始行 74、88）/ 主 Reviewer。
Plan baseline：原文要求停止新业务后继、事实重放不重建返料，但未定义 return_batch 与 drain execution 的冻结关联。D1 的 A 仅批准评审范围。
Runtime evidence：scan_flow.py:213 使用 `drain = await self._drains.current(db, workline.id)`，:219 以 rack/face 判定 drain 归属，:227 可走 source rack 分支；batch_result.py:111–150 校验当前 READY 前缀箱码后创建 BIN_MOVE；drain_flow.py:120–127 创建 return_batch 只传 workline_id，未冻结 drain execution 关联。本项是拟实施机制的缺口，不声称退役功能已上线或已发生生产回归。

| 选择项 | 当前草案 | A | B | 批准依据 |
| --- | --- | --- | --- | --- |
| return_batch 原 drain 归属 | 未规定持久关联 | 创建义务时同事务冻结执行关联 | 仍未规定，保持 pending | 无，待 D2 |
| 退役后晚到响应 | 概括要求不创建后继 | 按原关联核验，保存并完成消费，不创建新 BIN_MOVE | 具体机制保持 pending | 无，待 D2 |
| 原动作与可靠交付 | 保留身份、正文、义务 | 保留 | 保留 | 草案固定目标；D1 A 保留范围 |
| 必要验证 | 概括迟到事实与并发 | 覆盖迟到 READY、rack/face 复用、退出/响应并发、重启 | 该项验证保持 pending | 随 D2 A 一并批准 |
| 其它架构问题 | 尚待逐项评审 | 未决定 | 未决定 | 无 |

Question D2：
**D2 — 问题 1：迟到的 return_batch 如何保留原 drain 归属？**

请回复 **A 或 B**。当前没有 AskUserQuestion 工具，按 Skill 的交互降级规则使用文字确认。
Project/branch/task: wes_backend / develop / 旧排空异常退出设计评审。

[P1] (confidence: 9/10) SPEC:74、88：草案要求“停止新业务后继”以及“事实重放……均不能重新创建……返料”，但未定义已持久化 return_batch 的原 drain 归属如何冻结、消费时如何核验。

ELI10：旧排空可能先发出返料分配请求，再因为另一个动作异常而退出。WMS 的分配结果稍后才到达；如果系统只看此刻的货架和箱码，可能把旧结果当成新流程的授权，再创建一次搬运。响应仍需保存，但创建新搬运必须核验它原来属于哪次排空。

Stakes if we pick wrong: 旧排空退役后仍可能创建 BIN_MOVE，或者把需要正常消费的旧响应反复送入对账。

Recommendation: A，因为冻结原业务归属能复用现有可靠交付，同时明确阻止旧响应获得新业务资格；符合明确优于隐式推断、最小充分改动的偏好。[Layer 3]

A) 1A 补齐原 drain 归属与退役结果消费规则。（recommended）Completeness: 10/10。预计 human：约半天–1 天 / CC：约 30–60 分钟，包含实施与聚焦验证。创建 drain 的 return_batch 义务时，同事务持久冻结其 drain execution 关联；消费结果按原关联核验生命周期，退役后保存和完成该响应消费，不创建新的 BIN_MOVE，也不改写已创建动作。测试覆盖迟到 READY、同 rack/face 被新任务复用、退出与响应并发及重启重放；代价是增加必要的本地关联和消费检查，实施计划须先核对现有持久化结构能否承接，不能按货架面或历史时间猜归属。

B) 1B 保留现有概括描述，推迟具体关联设计。Completeness: 6/10。预计 human：本轮约 10 分钟 / CC：约 5 分钟；实施时仍需补齐该机制。文档改动少，但该缺口保持未解决，不能据此认定退役后不创建后继已闭合。风险和后续维护成本转移到实施阶段，本轮不批准任何替代消费机制。

Net: 取舍是现在把迟到响应的业务资格写清并绑定验证，还是把这项 P1 设计缺口留给实施阶段。

Header: 迟到返料归属
Options: A / 1A 补齐原 drain 归属与退役结果消费规则。（recommended）；B / 1B 保留现有概括描述，推迟具体关联设计。完整选项正文以上述 Question D2 为准。

State: approved
Actual answer: 用户在 D2 后回复 A，批准选项 1A。
Accepted scope: 创建 drain return_batch 义务时同事务冻结原 execution 关联；消费按原归属核验，退役后保存并完成响应消费但不创建新 BIN_MOVE，不改写已创建动作；覆盖迟到 READY、rack/face 新任务复用、退出/响应并发及重启重放；实施计划核对既有持久化结构，保持 wire 与可靠交付机制不变。
History: 初始 pending / unanswered / none；用户 D2 A 后转 approved。

### R2：正常 COMPLETED 与 EXCEPTION_RETIRED 的原离场恢复资格

Finding：问题 2 / P1 / confidence 9/10 / 本文 §4.3、§7 / 主 Reviewer。
Plan baseline：原文离场接纳即 COMPLETED；随后概括所有业务终态不再创建离场。D1 A 未批准删除正常恢复，D2 A 仅处理退役后的 return_batch 结果。
Runtime evidence：scan_flow.py:356–365 的 _resolve_drain_decision 为原离场通过 for_evidence 找回冻结决定；:456–460 检查该决定；:464–469 排除已满足目标；test_scan_flow.py:1099、1335、1399 已有原 drain 离场重试回归。本项是计划措辞可能误伤现有路径的风险，不是已实施回归。

| 选择项 | 当前草案 | A | B | 批准依据 |
| --- | --- | --- | --- | --- |
| COMPLETED 是否恢复整次排空 | 不应恢复 | 不选 current、不重开进场/旋转/返料 | 不改变原目标，细则 pending | 原草案目标 |
| 原正常离场明确终态后的业务重试 | 概括禁令未区分 | 原 Requirement 有效且目标未满足且原 Action 明确终态时允许 | 具体机制 pending，不删除现有路径 | 无，待 D3 |
| EXCEPTION_RETIRED 业务重试 | 禁止 | 禁止 | 保留原目标 | 原草案目标 |
| 原身份技术重试、迟到事实 | 保留 | 保留，所需引用在对象整体闭合前留存 | 保留 | 原草案目标 |
| 必要证明 | 验收矩阵未区分两种终态 | 状态对照、取消离场、已满足目标、重放 | 细则 pending | 随 D3 A 批准 |

Question D3：
**D3 — 问题 2：正常 COMPLETED 后，原离场动作还能恢复吗？**

请回复 **A 或 B**；当前没有 AskUserQuestion 工具，按 Skill 规则使用文字确认。
Project/branch/task: wes_backend / develop / 旧排空异常退出设计评审。

[P1] (confidence: 9/10) SPEC §4.3、§7：草案在各货架离场获接纳后写 COMPLETED，又统称“drain 的业务终态不重新打开；……均不能重新创建离场/旋转/返料”。这未区分正常结束编排与异常退役停止业务重试。

ELI10：货架离场被 RCS 接纳后，本地排空可以结束，但原离场随后仍可能收到明确取消结果。现有实现允许在原离场需求仍有效、目标尚未满足时恢复这个动作；若把 COMPLETED 和 EXCEPTION_RETIRED 一起禁止所有新离场身份，会把原有恢复路径切断。原动作恢复不应重新启动整次排空。

Stakes if we pick wrong: 正常 drain 的取消离场无法按现有合同恢复，或者异常退役又借恢复路径创建后继。

Recommendation: A，因为正常编排完成和异常放弃后继具有不同资格，必须保留原对象恢复并精准停止退役重试；符合保留现有行为、明确职责和最小改动的偏好。[Layer 1]

A) 2A 明确两种终态的业务重试资格。（recommended）Completeness: 10/10。预计 human：约半天 / CC：约 30 分钟，包含实现检查和回归。COMPLETED 不再选为 current、不重启进场/旋转/返料；原离场的对象级恢复仅在原直接 Requirement 仍有效、目标未被权威事实满足且原 Action 已明确终态时允许，新 Action 使用新身份。EXCEPTION_RETIRED 禁止该 drain 的业务重试，技术重试仍沿原身份；以状态对照测试覆盖取消离场、目标已满足和通知重放，防止全局终态过滤误伤。代价是 for_evidence 等调用点必须区分事实关联和业务资格；运行所需原引用在对象闭合前不可清理。

B) 2B 保留原文，把终态对原离场恢复的影响留待实施。Completeness: 6/10。预计 human：本轮约 10 分钟 / CC：约 5 分钟。草案暂不增加细则，但该 P1 保持 pending；不批准删除现有恢复，也不批准任何新的业务重试策略。实施时仍须解释 COMPLETED 与 EXCEPTION_RETIRED 的差异，现有恢复测试不能被直接改成“不重试”来迎合概括措辞。

Net: 取舍是现在明确终态对原动作的资格，还是带着正常恢复可能被误删的歧义进入实施。

Header: 正常离场恢复
Options: A / 2A 明确两种终态的业务重试资格。（recommended）；B / 2B 保留原文，把终态对原离场恢复的影响留待实施。完整选项正文以上述 Question D3 为准。

State: approved
Actual answer: 用户在 D3 后回复“A，正常来说，离场任务不会被 rcs 取消”。
Accepted scope: 选项 2A；COMPLETED 保留符合原 Requirement、目标未满足和明确终态条件的现有原离场异常恢复，不重启排空；EXCEPTION_RETIRED 禁止业务重试；原技术重试、事实接收与必要引用保留；状态对照回归。用户澄清正常离场不被 RCS 取消，该路径仅为已有异常恢复，不新增正常取消流程或机制。
History: 初始 pending / unanswered / none；用户 D3 A 并澄清正常流程后转 approved。

### R3：尚未搬运候选的原 Return 执行身份

Finding：问题 3 / P1 / confidence 9/10 / 本文 §6 / 主 Reviewer；测试追踪发现的新增独立缺口。
Plan baseline：原文禁止退役后的旧成员重新分配；D2 A 仅冻结原 drain 归属及退役响应处理，未明确冻结候选 Return.id。
Runtime evidence：drain_flow.py:117–127 创建请求仅使用 row.bin_code；batch_result.py:117–121 对照当前 READY 前缀箱码，:145–147 创建搬运后才写 RETURN_REQUESTED/批次 Evidence；bin_line/return_repository.py:66–77 的 READY 查询不带旧 drain 请求归属。本项针对拟退役机制的候选保留缺口，不声称生产退役已发生。

| 选择项 | 当前草案 | A | B | 批准依据 |
| --- | --- | --- | --- | --- |
| 冻结候选执行身份 | 未规定请求时冻结 Return.id | 同事务绑定原 Return.id | 具体机制 pending | 无，待 D4 |
| 退役后旧成员重新分配 | 禁止，机制缺失 | 按原关联保持未闭合归属，禁止其它批次重分配 | 原要求保留，机制 pending | 草案固定目标 |
| ACTIVE drain 的 NO_BATCH/换面 | 现有合同 | 保留现有推进 | 保留 | 已有合同与 D1 A 范围 |
| 同箱码新轮次、历史清理 | 原要求独立新轮次可继续 | 新 ID；整体闭合后关联可清理 | 原要求保留 | SRS 原则 |
| 同一物理 FIFO 直接依赖 | 不越序 | 不越序，不以排除候选证明可越过队头 | 不越序 | 草案固定目标 |
| 必要证明 | 概括成员归属 | 请求未返回即退役、新批次选择、同箱码新轮次、重启 | 保持 pending | 随 D4 A 批准 |

Question D4：
**D4 — 问题 3：退役前已请求分配、尚未搬运的旧候选如何防止再次分配？**

请回复 **A 或 B**；当前没有 AskUserQuestion 工具，按 Skill 规则使用文字确认。
Project/branch/task: wes_backend / develop / 旧排空异常退出设计评审。

[P1] (confidence: 9/10) SPEC §6 要求“退出后原料箱/批次不能重新入队或重新分配到新 return_batch”，但现有请求创建只冻结 bin_code，候选 Return 在收到 READY 并创建 BIN_MOVE 前仍为 READY。D2 已批准的 drain 关联解决响应归属，尚未解决这些原候选被新批次选中的问题。

ELI10：旧排空已经把几个箱子报给 WMS 等待储位分配，随后排空退役。这些箱子还没有开始搬运，在本地仍属于“可以分配”的列表；新批次可能再次选中它们。必须记住这次请求选的是哪几个 Return 执行记录，不能只记可重复使用的箱码。

Stakes if we pick wrong: 退役后的旧候选被新 return_batch 再次分配，或同箱码的新轮次被误认成旧候选。

Recommendation: A，因为候选的执行身份是已有 BinLineReturn.id，直接复用它即可实现草案的归属要求，无需增加全局物理围栏或队列状态机。[Layer 1]

A) 3A 在请求事务内冻结原 Return 身份并据此维护候选资格。（recommended）Completeness: 10/10。预计 human：约半天–1 天 / CC：约 45–90 分钟，包含实现和并发验证。沿 D2 的本地关联，同事务冻结 return_batch 候选的原 BinLineReturn.id；结果消费核验原成员，退役后这些尚未闭合成员不能被其它批次重新分配，普通 ACTIVE drain 的 NO_BATCH/换面仍按现有合同推进。同箱码合法新 Return 使用新 ID，旧成员整体闭合后关联可清理；测试覆盖请求未返回即退役、新批次选择、同箱码新轮次和重启。代价是维护必要的候选关联及选择校验；同一未闭合物理 FIFO 的直接依赖仍等待，不能用关联排除来越过队头。

B) 3B 保留现有成员归属概括，延后冻结候选身份的设计。Completeness: 6/10。预计 human：本轮约 10 分钟 / CC：约 5 分钟。暂不增加设计细则，但这个 P1 保持 pending；不批准重新分配旧候选，也不批准新 FIFO 越序策略。实施时仍须补齐原成员归属，单凭旧响应不创建 BIN_MOVE 的测试不能证明候选不被重复分配。

Net: 取舍是冻结已有执行身份来闭合成员归属，还是把“停止旧响应”误当成“旧候选已不能再分配”。

Header: 原候选执行身份
Options: A / 3A 在请求事务内冻结原 Return 身份并据此维护候选资格。（recommended）；B / 3B 保留现有成员归属概括，延后冻结候选身份的设计。完整选项正文以上述 Question D4 为准。

State: approved
Actual answer: 用户在 D4 后回复 A，批准选项 3A。
Accepted scope: 在原 drain return_batch 请求事务内冻结候选 BinLineReturn.id，按原成员维护资格；退役后原未闭合成员不能被其它批次重分配；保持 ACTIVE 的 NO_BATCH/换面合同，同箱码新轮次用新 ID，整体闭合后关联可清理，物理 FIFO 不越序；覆盖请求未返回即退役、新批次选择、同箱码新轮次及重启。
History: 初始 pending / unanswered / none；用户 D4 A 后转 approved；不是重开 R1、R2。

### R4：未决请求的原成员资格在退役前是否生效

Finding：问题 4 / P1 / confidence 8/10 / 本文 §4.1、§6 / Outside Voice 提出，主 Reviewer 核验缩窄。
Plan baseline：D4 A 冻结请求时原 Return.id，并明确退役后不重分配；未明确 ACTIVE 且请求未决时跨 rack/face 的资格。此问题不重开原 drain 归属和退役后成员规则。
Runtime evidence：batch_repository.py:329–335 调用 has_unclosed_return 传入 workline_id/rack_id/rack_face；batch_driver.py:336 和 batch_flow.py:153 复用同面门禁；drain_flow.py:117、batch_flow.py:166/202 读取同一 WorkLine READY 候选，ReturnRepository 的查询不带未决 drain 关联。外部“任何第二次调用都会重复”的说法过宽；现有同面门禁已存在。核验后的问题仅为拆除整线门闩后的不同 rack/face 请求侧成员资格，是拟实施风险，非已证明生产回归。

| 选择项 | 当前已批准值 | A | B | C | D |
| --- | --- | --- | --- | --- | --- |
| 原 drain/Return 身份与退役后资格 | D2/D4 A | 保持 | 保持 | 保持 | 保持 |
| ACTIVE 未决原 drain 候选的跨面重申请 | 未明确 | 同事务禁止同一 Return.id 再次申请 | 未决定 | 限定核验后再决定 | 待办候选，未决定 |
| 同面门禁、ACTIVE NO_BATCH/换面 | 已有合同 | 保持 | 保持 | 保持 | 保持 |
| Return 状态/物理容量预占框架 | 无新增 | 不新增，复用关联 | 不新增 | 不修改 | 不新增 |
| 新证明 | 未决 | 跨面并发/回滚/NO_BATCH | pending | 核验两个入口，不实施 | pending |
| TODO 写入 | 无新增批准 | 无 | 无 | 无 | 本轮未批准正文，另问 |

Question D5：
**D5 — 问题 4：旧 drain 尚未退役、分配请求仍未决时，也要防止同一 Return 被再次申请吗？**

请回复 **A、B、C 或 D**；当前没有 AskUserQuestion 工具，按 Skill 规则使用文字确认。
Project/branch/task: wes_backend / develop / 独立复核的请求侧意见。

[P1] (confidence: 8/10) SPEC §4.1 允许正常 drain 与独立任务共存；§6/D4 明确了退役后的原成员归属，但未明确退役前未决请求的原成员选择资格。现有 has_unclosed_action_for_face 只约束同 rack/face，新任务使用其它 rack/face 的调用路径仍读取同一 WorkLine READY Return。

ELI10：箱子已随旧 drain 向 WMS 申请储位，但响应还没回来；这时旧 drain 仍是 ACTIVE。整线门闩拆除后，另一批次也可能走到申请步骤。D4 已经要求冻结原 Return.id，现在需要决定这个关联是否从请求创建时就阻止同一执行成员被其它批次再次申请，而不是等到退役后才生效。

Stakes if we pick wrong: 不同 rack/face 的批次可能对同一 Return 发出重复的 WMS 储位分配请求；这不是 RCS 物理搬运指令。

Recommendation: A，复用已批准的原成员关联，在请求创建侧校验资格，不另加 Return 状态或容量围栏；它比依赖稍后的响应核验更直接。[Layer 1]
Note: options differ in kind, not coverage — no completeness score.

A) 4A 接受请求侧补充，关联从创建义务时生效。（recommended）预计 human：约半天 / CC：约 30–45 分钟。沿 D4 的关联，在同一 WorkLine 事务内核验：已有未决 drain return_batch 的原 Return.id 不得被另一个批次重新申请，不论原 execution 是 ACTIVE 还是 EXCEPTION_RETIRED；同 rack/face 的既有门禁保持。收到确定 NO_BATCH 后 ACTIVE drain 按原合同换面继续；响应未知仍维持原身份和成员依赖；退役后按 D4 处理。新增跨 rack/face 的请求并发、事务回滚和 NO_BATCH 后继续测试；代价是给两个现有请求创建路径增加原成员资格检查，不引入新枚举或通用预占机制。

B) 4B 保留当前明确的退役后约束，暂不批准退役前规则。预计 human：约 10 分钟 / CC：约 5 分钟。D2–D4 保持，原同 rack/face 门禁保持；本项 P1 未解决，不能把“请求未决时防重申请”写成已保证。维护上留待实施进一步解释，请求侧风险仍需闭合才能宣布完整验收。

C) 4C 先限定核验两个现有请求入口和其测试，再决定。预计 human：约 1 小时 / CC：约 15–30 分钟。只核验 drain_flow.return_in_session、batch_flow.advance_in_session、成员查询及对应测试是否已有跨 rack/face 排除依据；不检查服务器、不修改代码。D2–D4 保持，本项机制仍 pending；核验结果再单独确认，不授权条件式修复。

D) 4D 将请求侧规则作为待办候选，暂不纳入本次设计。预计 human：约 15 分钟 / CC：约 5 分钟。保留 D2–D4，本项保持未解决；随后需按 Skill 单独确认有上下文的 TODO 内容，不能现在自动写入 TODOS.md。代价是设计尚不能保证该未决窗口没有重复申请，也不能将本轮标记为已完全闭合。

Net: 取舍是让已有执行成员关联从请求时就承担防重申请，还是保留这一明确的未决窗口继续调查/排期。

Header: 未决请求成员资格
Options: A / 4A 接受请求侧补充，关联从创建义务时生效。（recommended）；B / 4B 保留当前明确的退役后约束，暂不批准退役前规则；C / 4C 先限定核验两个现有请求入口和其测试，再决定；D / 4D 将请求侧规则作为待办候选，暂不纳入本次设计。完整选项正文以上述 Question D5 为准。

State: approved
Actual answer: 用户在 D5 后回复 A，批准选项 4A。
Accepted scope: 原关联从 return_batch 义务创建时生效；两个请求创建入口在 WorkLine 事务内禁止同一未决原 Return.id 被其它批次再次申请，无论 ACTIVE 或 EXCEPTION_RETIRED；保持同面门禁与确定 NO_BATCH 后原合同推进；响应未知保留原身份和依赖；覆盖跨面并发、回滚、NO_BATCH 后继续，不新增状态或容量框架。
History: 初始 pending / unanswered / none；用户 D5 A 后转 approved；不重开 D2–D4。

### Outside Voice 核验

Claude Code 完成外部 plan-review；报告模型 claude-sonnet-5，session 44d51d8b-3740-4fce-9252-2aa28351ea06。
外部意见 1：其中“退役后旧候选”已由 D4 覆盖；“ACTIVE 未决请求的跨面再申请”经现有调用点核验后独立形成 R4，D5 A 已批准请求侧补充并纳入 §6、验收矩阵和 T4。
外部意见 2：current() 不必同时返回 blocker 和历史 owner；全局注入被移除，D2 已批准按原 execution 关联消费，不靠 current/新 source face 重新授权。无需强制拆分方法；该意见属 D2 的重复且机制尚未定，confidence 4/10，进入 Suppressed findings，不新增任务。
外部报告中的“重复物理请求”应解释为 WMS 储位分配义务，不是 RCS 搬运指令。完整原文单独留存为评审产物，不代表全部意见已接受。

## 工程评审完成摘要

| 项目 | 结论 |
| --- | --- |
| Step 0 Scope Challenge | 保留完整范围，用户 D1 A |
| Architecture Review | 3 项：问题 1–2 与外部核验问题 4，均获单独批准 |
| Code Quality Review | 0 项 |
| Test Review | 已生成 ASCII 图；16 组新增机制/用户流程证明待实施，其中追踪形成问题 3 |
| Performance Review | 0 项 |
| NOT in scope / What already exists | 已写明 |
| TODOS.md | 0 个新增独立候选；已有 dirty 文件保持 |
| Failure modes | 0 个尚无设计处理的 critical gap；实现/可执行证明尚未发生 |
| Outside Voice | Claude Code / claude-sonnet-5 完成；2 条原始意见，1 条核验缩窄后 D5 A 接受，1 条重复抑制 |
| Parallelization | 1 条顺序执行链，无独立并行 lane |
| Lake Score | 4/4；D5 的方案类型不同，不计完整度分数 |
| Unresolved decisions | 0 |

评审结论仅为设计决策闭合。T1–T4 未实施，新增机制没有本轮可执行通过证据；未运行 QUALITY、HEAVY、迁移、服务器检查或部署。

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
| --- | --- | --- | --- | --- | --- |
| CEO Review | /plan-ceo-review | 范围与策略 | 0 | — | 本轮无产品方向扩张 |
| Outside Review | Claude Code / plan-eng-review | 独立第二意见 | 1 | completed | 2 条原始意见：1 条缩窄后 D5 A 接受，1 条重复抑制 |
| Eng Review | /plan-eng-review | 架构与测试，必需 | 本 SPEC 1；近 7 天 4 | clean / CLEAR (PLAN) | 19 项计数：3 个架构问题 + 16 组待实施证明；测试追踪另形成问题 3，四个设计问题均获批准；0 critical gap，0 unresolved |
| Design Review | /plan-design-review | UI/UX | 0 | — | 本轮无 UI 范围 |
| DX Review | /plan-devex-review | 开发者体验 | 0 | — | 本轮无新增开发者接口 |

**OUTSIDE COVERAGE:** host codex；provider claude-code；phase plan-review；outside_status completed；报告模型 claude-sonnet-5。外部原文已展示并单独留存；该次审查提出意见，未冒充外部 clean 复审。

**CROSS-MODEL:** 原生 Codex 工程审查与已完成的 Claude Code 独立审查共同核对原执行身份和请求侧资格；外部“任意第二次调用”缩窄为跨 rack/face 的未决原 Return.id，D5 A 接受。强制拆分 current() 的意见被识别为 D2 重复；不据 host 名称推断未知模型版本。

**VERDICT:** ENG CLEARED — 设计决策闭合，可制定实施计划；T1–T4 和验证仍待执行，不授权 Commit、Ship 或部署。

NO UNRESOLVED DECISIONS
