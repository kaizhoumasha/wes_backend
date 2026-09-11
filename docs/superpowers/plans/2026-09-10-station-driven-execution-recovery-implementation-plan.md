# 本站驱动执行恢复实施计划

> 执行入口：遵循项目 `wes-implementation`；本计划按内聚切片顺序执行，不额外串联重复 Review。用户已接受工程推荐；这不等于授权本轮修改生产代码、Commit、Push、Merge 或 Deploy。

**目标：** 原任务可在 ECS 排障后按原身份继续；旧异常或结果未知不阻塞独立的新本站请求，执行事实可靠留存并交 WMS 对账。

**架构：** 复用 DeviceCommand、InboundEvidence、WmsConfirmation、SDK 和插件应用层。基础能力拥有身份、持久化、并发与恢复；插件拥有业务请求、WMS 数据和结果解释；ECS 校验本次请求适用性及物理互锁。

**技术栈：** Python 3.13、FastAPI、SQLModel/SQLAlchemy、PostgreSQL、Celery；现有 Vue 诊断页。

**设计：** [顶层设计](../specs/2026-09-10-station-driven-execution-recovery-top-level-design.md)。本计划细化其 CEO D1–D7、工程 D1–D3；工程 D4–D8 按用户“全部接受你的推荐”的授权收敛。

**状态：** 工程方案与验收计划已整理；未实施、未运行行为测试。供应商符合性和 WMS 接收验收尚未执行，不能据本计划宣称现场已支持新协议。

## 全局约束

- 基础与业务分离；基础部署、运行和测试不依赖具体插件。基础不导入 `workline_plugins/`，业务不复制 HTTP、outbox、重试或领取机制。
- 未发布系统直接替换旧合同和消费者，不加版本别名、兼容路径或永久开关。开发测试数据可清理，不能把真实未知物理动作当开发数据清除。
- 人类文档不编写测试代码；行为变更按 TDD，优先修改现有测试。聚焦、QUALITY、HEAVY、迁移及供应商验收分别记录。
- 点位准入、命令事实、历史分类独立。Transport、实际物理 FIFO/LIFO、资源绑定和明确停用规则仍按各自合同执行。
- 不修改无关 dirty 文件。实施前记录状态和内容指纹；本轮存在并发的 Transport、WorkLine 和部署组合变更，实施必须重新核对基线。
- 当前没有新发行包、框架或进程类型。SDK 沿现有独立安装链发布；所有长驻进程使用匹配合同和迁移版本。

## 工程判断与证据

| 决定 | 代码证据与问题 | 推荐方案 |
| --- | --- | --- |
| D1：事件身份 | `device/contracts.py:234` 无公开事件身份；`device_evidence_service.py:643` 以报文摘要生成身份 | ECS 提供稳定 `source_event_id`，WES 同身份校验内容；命令回传来源 |
| D2：替代关系 | 唯一事件 ID 不包含替代语义，顶层设计禁止按接收时间归类 | 新请求可携带 `supersedes_source_event_id`，无关联不阻塞新请求，也不猜测归类 |
| D3：历史表示 | `device/models/command.py:273` 把未知状态与设备占用合并 | 保持原状态，新增可空 `historical_success_evidence_id`；不新增 HISTORICAL 终态或异常表 |
| D4：请求级准入 | `command.py:211` 设备唯一槽；`material_execution.py:91` 未关闭 trace 唯一；`material_execution_service.py:104` 按 trace 复用/冲突 | 事件触发执行按初始 Evidence 幂等，命令按已有执行引用及 command_code 幂等；物料标识不作为本站请求独占锁 |
| D5：故障与反馈 | `device_command_service.py:704` 的超时推进只改状态；`InboundEvidenceKind` 没有本地命令观察；placement/NG 只表示真实到位 | 同事务保存本地命令观察，复用 Evidence 处理与 WMS 可靠义务；未知和迟到真实结果分别反馈 |
| D6：ECS 接纳 | 白皮书重复命令返回首次接纳事实，与可重试未接纳需重新评估的描述有歧义；Adapter 只识别固定响应组合 | 明确 ACK 后去重；明确可重试未接纳不冻结成永久拒绝。增加精确的失效拒绝分类，未知仍不重发 |
| D7：正式业务入口 | `device_position_confirmed.py:54–57` 用上一点结果触发下一点动作；`target_decided.py:58` 仍预查询设备 readiness | 下一交互点用自己的请求触发；移除本站动作的重复状态门禁。迟到结果只回到原上下文处理与反馈 |
| D8：持久化、测试与性能 | 原设备槽测试会验证旧语义；仅内存 Mock 看不到多事务竞争及重启窗口 | 更新原测试所有者；索引查询、短事务、领取 fencing、真实 worker 恢复分层验证 |

上述为已核对代码的设计冲突，置信度 9/10；不称为本轮代码回归。`assert_fifo_head` 已发现定义但此次精确调用搜索未发现生产调用，不把它描述成已经发生的现场阻塞。实施时扫描后删除因本次语义替换失去用途的入口；真实物理队列的实现保留。

## 合同与持久化决定

### 1. 事件及来源

- 公开事件身份为 `(device_code, source_event_id)`；`device_code` 必须代表合同中的明确交互点，不能把多个独立点位混为一个来源。复用现有设备绑定，不建立新的站点注册表。
- 所有事件必填非空、长度受限的 `source_event_id`，沿现有 wire token 约束；同设备不得跨重启复用。同身份的 `event_type`、时间、调试标记、业务数据、替代引用任一合同内容改变均冲突。
- `supersedes_source_event_id` 只允许在合同声明的本站业务请求上使用；缺省表示未声明替代。不能跨点、指向自己或形成循环。引用目标未到达时保存引用，不要求人为补录。
- InboundEvidence 继续保存唯一 `source_identity` 和不可变内容摘要。内部唯一键由设备来源及公开事件 ID 规范化构成，不能再用整份内容摘要辨认“是不是同一事件”。结果仍以原 `command_code` 识别，不改变结果回调为可覆盖终态。
- 命令、WMS 业务等待保存初始事件 Evidence 关联，以便 WMS 响应和命令重试跨事务仍能找到原请求；不能从当前设备状态或最近一条事件补填。事件触发命令对外回传 `source_event_id`。非事件触发命令不伪造该字段，静态合同明确两类合法输入。

### 2. 替代、历史分类与并发

- 在 InboundEvidence 增加仅设备事件使用的可空 `supersedes_source_event_id` 检索列，与冻结 payload 一致；建立按设备和被替代身份查询的部分索引。复用原 Evidence 表，旧事件尚不存在也能持久保存该事实。
- 新事件可靠提交时即可使明确被替代的请求不再生成或重试指令；这一步不等待后续成功。已在途指令最终由 ECS 接纳校验挡住，不把数据库检查宣称为跨系统原子锁。
- `DeviceCommand.historical_success_evidence_id` 指向后续命令的真实成功 Evidence；写入前验证同交互点、D2 直接替代关系、原命令确为异常或已满足结果缺失期限。原命令仍在正常等待期时不提前制造异常，之后达到期限再应用已存在的成功依据。
- 归类与原结果应用按命令行锁串行；先到真实终态、后到分类或相反顺序，最终都不丢事实。只对被明确引用的旧请求归类，不递归推断所有祖先请求已失效。已填引用不因重复回调改写。
- 新旧事件乱序：处理旧事件及创建命令前查持久化替代索引；新成功已保存而旧记录后来建立时，用相同校验补齐分类。查找或补齐由既有 Evidence 处理和恢复扫描承接，不建新调度器。
- 关系校验复用 PostgreSQL 事务和现有锁工具，按交互点做有界短事务；不得持锁等待 WMS/ECS。循环校验限制查询预算，超预算保留待核验事实并显式报错，不能默认通过；无关请求仍继续。历史查询分页，禁止每次到位扫描整台设备全部历史。

### 3. 准入与命令结果

- 移除 `ux_device_commands_unclosed_device` 及所有用旧命令生命周期阻塞独立本站请求的准入消费者；保留 `command_code`、执行引用、调试请求身份的唯一约束与单命令领取 token。
- 不把该索引改成“只有历史归类后才释放”；新请求不等待替代引用、成功结果或旧异常关闭。ECS 负责实际接纳互斥。
- 手工调试继续要求权限、明确目标、固定上下文和本地停用校验；其允许动作由静态合同定义，不以另一条旧未知记录作为设备永久锁。不会因为取消数据库设备槽而获得事件请求的业务授权。
- `MaterialExecution` 复用为本站请求的处理上下文：初始事件关联唯一，`material_trace_id` 为业务事实索引而非全局活动唯一键。`create_or_get_for_initial_evidence` 按同一初始 Evidence 幂等返回；同料再次到达或其他点位请求形成独立上下文。不新增 StationSession/全程链。
- SDK、FactProcessor、DecisionApplier 与插件保存来源关联；WMS 等待及结果回到原上下文。旧上下文 RECONCILING/CLOSED 不能挡新上下文；旧动作默认仍不自动重发。
- `PENDING` 明确未发出而到期仍为 TIMED_OUT；`DISPATCHING` 租约过期及 ACK 后无结果保持 RECONCILING。迟到原结果沿已有状态转换闭合。TIMED_OUT 与本地拒绝不能描述为 ECS 物理失败。
- 为支持“同一旧命令确实恢复”，业务 Fact 允许读取 RECONCILING 原上下文并保存/反馈原真实结果，但是否产生新动作必须单独通过原请求仍适用和业务授权校验。不能全局删除生命周期检查后任意恢复旧业务链。

### 4. ECS 接纳及重试

| 外部事实 | WES 行为 |
| --- | --- |
| `200 / ACK` | 接纳事实冻结，重复同命令不得再次驱动；后续以原命令结果闭合 |
| `429 / CAPACITY_EXCEEDED` 且有效 Retry-After；`503 / TEMPORARILY_UNAVAILABLE` 明确未接纳 | 原身份原载荷有界重试；ECS 重新评估临时拒绝，不能永远重放第一次“忙” |
| 新 `410 / SOURCE_EVENT_NOT_APPLICABLE` | 明确未接纳且不可重试，停止该请求派发；不推定物理退出 |
| 新 `423 / EXECUTION_NOT_ALLOWED` | 明确未接纳且不可重试，记录本次执行禁止；与临时容量不足分开 |
| `409 / IDEMPOTENCY_CONFLICT`、超时、无可信响应、未定义组合 | 交付未知，保留身份与证据，不自动重发 |

新响应组合是本方案提出的目标合同，尚未获得供应商实现证据。严格匹配 HTTP、code、message，沿用固定响应校验。已接纳命令重复到达时，优先返回原接纳事实，不因请求后来失效而把历史 ACK 改为“未接纳”；身份内容漂移仍返回冲突。ECS 的内部缓存与 PLC 实现不由 WES 规定。

基础派发和插件均取消本站请求命令的状态预查询门禁；保留本地停用、业务授权、设备绑定、期限和来源适用性。状态查询只服务观察/诊断；不删除其他确有独立用途的状态消费者。

### 5. 本地观察与 WMS 反馈

- 新增封闭内部 Evidence kind `DEVICE_OBSERVATION`，表示 WES 的 `NOT_ACCEPTED` 或 `RESULT_UNKNOWN`，不伪造 `DEVICE_RESULT`。复用 InboundEvidence、领取、处理、唤醒和恢复扫描；同步修改 kind 检查约束及静态分派。
- 命令被明确拒绝/未发送到期，或进入交付/结果未知时，在相同事务保存观察 Evidence。每条命令、每种观察事实只有一个稳定身份；重复扫描不重复创建。已接纳后才获知的未知只记录事实，不形成重发请求。
- 插件消费观察后，在同一事务保存 typed WMS intent 对应的 WmsConfirmation 并标记该观察已消费；崩溃后可重取。基础独立运行时保存观察即可，无业务消费者不要求调用 WMS，更不能调用默认业务插件。
- 粗分域最小增加 `inbound.execution.observation_report@v1`，WES→WMS，使用现有 `POST /api/v1/wes/facts` 与公共信封。公开 typed method 为 `inbound_execution_observation_report`；wire/Adapter 放 `wms_adapter/inbound_material/`，不新建通用异常 API 或 outbox。
- 该 operation 的业务数据包含原执行/物料 trace、设备、原 command_code、来源 source_event_id、观察事实及时间、必要原因；`NOT_ACCEPTED | RESULT_UNKNOWN | EXECUTION_FAILED | EXECUTION_SUCCEEDED` 为封闭事实分类。无实际位置时不填位置，不把未知变为 NG；只有原匹配结果支持后两类。
- 沿用现有 `RECORDED | DUPLICATE` 事实响应和公共错误处理；表示 WMS 已记录事实，不表示库存对账完成。每一独立观察或迟到补充使用不同稳定 operation_id，同一次重试身份和 payload 不变。复用现有 `stable_operation_id`，不能在扫描时取当前时间重新造身份。
- 如旧命令已有未知反馈，原真实结果到达时生成独立补充义务，并继续按已有成功业务合同判断是否还需 placement/NG 上报。新增观察报告只提供事实，不重复修改库存。乱序到达 WMS 时以原命令关联和事实类型理解，不能按网络接收顺序覆盖事实。
- 已冻结的反馈不能因该请求被替代、历史归类或当前点位进入新任务而取消。WMS 不可用仍复用既有期限/重试政策；预算耗尽保留可见技术投递问题，技术投递恢复不等于 WES 承担业务对账。冻结插件版本缺席时保留待消费观察，已创建义务继续可靠投递。
- S0 把上述语义落实为严格 DTO/OpenAPI、错误码、字段约束和双方样例。外部 WMS 未支持新 operation 前，不能发布需要该反馈的完整业务切片；基础候选验证可独立进行。

### 6. 已有插件消费者

| 消费者 | 本轮处理 |
| --- | --- |
| EVENT_DEBUG / MANUAL_DEBUG | 改用统一身份、接纳分类和命令可靠机制；没有 WMS 业务 owner 的诊断不伪造业务反馈 |
| `rough_sorter` | 实际已有执行链，必须完成本站触发、独立上下文、异常与迟到反馈；不能用核心测试代替插件验收 |
| `manual_bin_processing` | 当前 `build_handlers()` 返回空 tuple；仅核对受影响 SDK/合同使用，不新增尚未实现的人工流程 |
| `manual-picking` | 当前仅资源定义及声明；不把四个扫码角色当成已实现的四条业务流程，不在核心硬编码其拓扑 |

粗分测量点沿用 `SCAN_COMPLETED` 发起准入；出口决策由该出口所属设备的 `MATERIAL_ARRIVED` 请求提供本站物料 trace 与严格位置对象。被 ECS 自主控制的输送段不再由上一个动作结果逐段触发 WES `MOVE_FORWARD`。同一请求确实需要 WES 指令的动作按其批准合同保留。

出口 WMS target 决策不能要求 WES 先找到正常完成的上一点执行。复用 `inbound.material.target_decide@v1` 并直接修订目标合同：输入采用本站执行/请求、物料 trace、实际出口位置和货架上下文，WMS 查业务主账并返回后续 placement 所需的 `pkg_id`、准入与目标关联。查不到时明确 WAIT/REJECT，不猜测授权。原上游业务关联作为 WMS 判断结果使用，不再是 WES 阻断本站调用的前提。SDK、wire、插件、Mock 和合同测试一次性替换旧 DTO。

## 实施切片与验证

每个行为切片：先更新所属失败验收 → 运行聚焦测试确认失败类别 → 修改生产实现及全调用点 → 运行同一领域测试转绿。纯合同文字不做 RED；机器合同与运行时行为一起验证。最终仅一次主 Review/闭环和必需门禁，不按每个任务重复完整 QUALITY。

### S0 — 固定实施基线与机器合同

输入为本文与顶层设计；输出为精确变更 manifest、目标 DTO/响应表及测试所有权。

- [ ] 重新记录 HEAD、dirty/staged/untracked 指纹。因当前有共享文件并发修改，使用独立实施 worktree；迁移本设计及计划时保留原有修改，按项目要求初始化环境，不复用其他 worktree 的本地状态。
- [ ] 对下列切片生产符号批量运行 GitNexus upstream impact，固定直接/间接调用点、fixture、HEAVY mapping、迁移和生成物。新增清单外高风险再说明；不在本轮纯文档评审中冒称 impact 已完成。
- [ ] 修订设备白皮书、粗分设备附录、粗分 WMS 合同及其 DTO/OpenAPI；冻结 D1/D2 的字段缺省规则、新拒绝分类、新 observation operation、出口事件及 target 数据来源。同步错误码、payload 大小限制和唯一配置入口。
- [ ] 梳理旧 `DeviceEventCommandBlock` 与手工重评接口的所有实际用途：随新事件准入替换而失去用途的模型/路由/展示整体删除，保留不属于该设备槽机制的技术诊断能力。

**验收：** 每个对外字段有唯一生产定义和相同域合同测试 owner；无“缺字段仍接受”的兼容路径。供应商/WMS 样例分别标为目标合同与真实验收结果。

### S1 — 事件身份、替代关系、独立本站上下文

**修改：** `src/app/device/contracts.py`、`services/device_evidence_service.py`；`src/app/execution/models/inbound_evidence.py`、`models/material_execution.py`、对应 Repository、`services/material_execution_service.py`、`services/fact_processor.py`；SDK 来源上下文消费者及随机 revision 迁移。

- [ ] 先完善 `tests/contracts/device/test_uniform_ecs_wire.py`、`tests/api/test_device_ecs_callbacks.py`、`tests/runtime/device_command/test_evidence_identity.py`、`test_evidence_service.py`，覆盖身份不变、内容冲突、新旧请求乱序。
- [ ] 修改事件规范化与持久化关联；建立直接替代查询，保持成功 ACK 晚于 Evidence 提交。
- [ ] 修改 `create_or_get_for_initial_evidence`：按原 Evidence 幂等，取消活动 trace 唯一作为本站入口限制；独立新请求不复用旧 RECONCILING 上下文。
- [ ] 更新 `tests/runtime/execution/test_material_execution.py`、`test_fact_processor.py` 及对应 PostgreSQL 测试，证明同物料不同请求可独立存在、同请求竞争只生成一个上下文。

**输出：** 后续切片可读取稳定原事件、直接替代依据和唯一本站上下文，不需要物料全程状态正常。

### S2 — 命令准入、原身份派发与历史分类

**修改：** `src/app/device/models/command.py`、`repositories/command_repository.py`、`services/device_command_service.py`、`services/device_dispatch_service.py`、`services/device_command_admission.py`、`ecs_adapter.py`、事件结果应用；`src/app/execution/services/decision_applier.py` 和 SDK `CreateDeviceCommand` 来源关联。

- [ ] 在 `test_device_command_model.py`、`test_device_command_service.py`、`test_dispatch_service.py`、`test_dispatch_admission.py` 更新旧槽语义并增加 D3/D6/D7 验收。
- [ ] 一次性迁移命令创建调用点，冻结事件来源和 digest；移除旧设备槽索引/准入查询，保留请求幂等、claim token 和数据库唯一性。
- [ ] 实现 `historical_success_evidence_id` 及原结果应用的事务串行；未归类仍允许独立新请求，不把旧结果改写成 FAILED。
- [ ] 更新 `tests/integration/device_command/test_device_command_constraints.py` 和 `test_event_command_blocking_reconciliation_postgresql.py`：同身份竞争幂等、同点不同请求并存、迟到结果/归类竞争、引用先到而原事件后到。
- [ ] 更新当前接纳响应测试，证明临时拒绝可重试、ACK 后不重复驱动、失效拒绝不重试、网络超时不误判未接纳。

**输出：** 单条命令可靠机制独立可测；没有插件或 WMS 也能验证原结果与后续命令互不阻塞。

### S3 — 本地命令观察和可靠业务反馈接入

**修改：** 命令超时/拒绝路径、InboundEvidence kind/约束/静态处理、SDK typed fact 与 `wms_operations.py`；`src/app/wms_adapter/inbound_material/` 新 operation 接入及 WmsConfirmation 原义务生命周期。

- [ ] 完善 `tests/runtime/device_command/test_reconciliation_service.py`：状态推进与观察同事务、重复扫描幂等、无结果不伪造 DEVICE_RESULT。
- [ ] 接入 `DEVICE_OBSERVATION` 的正常和重启处理；提交后主动唤醒，既有扫描兜底，不另建队列/Beat 任务。
- [ ] 按第 5 节增加固定 typed operation；测试位于 `tests/contracts/wms_adapter/inbound_material/` 和需要真实事务时的 `tests/integration/wms_adapter/inbound_material/`，不复制共享 HTTP 测试矩阵。
- [ ] 完善 `test_wms_confirmation_service.py`、`test_wms_confirmation_dispatch.py`：旧业务已归类仍保留投递、同反馈重试不换号、迟到补充不覆盖首次反馈、WMS 故障/期限耗尽可见。

**输出：** 基础能留存并恢复观察及可靠义务；业务消费者缺席时不丢事实、不伪造 operation。

### S4 — 粗分业务按本站请求执行

**修改：** `workline_plugins/rough_sorter/src/rough_sorter/` 的初始事件关联、facts、application、handlers、wms_requests 及 SDK/domain DTO 对应消费者。

- [ ] 更新 `tests/test_material_and_admission.py`、`test_device_and_target.py`、`test_placement_and_replacement.py`、`test_transport_and_recovery.py` 和本插件应用测试。
- [ ] 出口事件独立取得 WMS 决策，不要求前一点回调或正常执行状态；取消纯输送段结果驱动的 WES 接力命令与重复 readiness 查询。
- [ ] 新增 typed 观察的业务消费及 WMS 报告；迟到原结果只影响原请求，对已失效请求不产生后续动作。真实 placement/NG 保留其位置与业务授权校验。
- [ ] 原换架/Transport 物理围栏与真实队列规则保留，测试不得用本站独立原则越过它们；未实现插件记录为不适用，不建立空的“已通过”验收。
- [ ] 运行插件自己的 `tests/integration/test_decision_processing_postgresql.py` 和 `tests/e2e/test_business_loop.py` 所属必要场景，独立于核心 QUALITY/HEAVY。

**输出：** 实际正式业务消费者覆盖两条恢复路径；不是仅调试入口可用。

### S5 — 历史与当前状态展示

**后端：** `src/app/device/services/device_ingress_history_service.py`、共享响应 DTO 与 SSE 更新；**前端：** `src/views/ops/device-diagnostics/DeviceEvidenceTable.vue`、`DeviceDiagnosticsPage.vue`、`useDeviceEvidenceStream.ts` 及 API 生成合同。

- [ ] 历史快照和 SSE 使用同一字段语义；展示原状态、历史归类及后续成功依据，不把 APPLIED 当物理成功，不凭历史未知显示当前设备故障。
- [ ] 删除随旧 blocker 退役失去用途的“先关闭再重评”操作；保留纯技术错误详情与原身份追踪。不新增 WMS 业务对账按钮。
- [ ] 更新前端 `tests/unit/views/ops/device-diagnostics/DeviceEvidenceTable.test.ts`、`DeviceDiagnosticsPage.test.ts`、`useDeviceEvidenceStream.test.ts`；覆盖先实时后快照、加载失败、空记录、原结果迟到、查看详情及重复操作。
- [ ] 在真实页面做浏览器 QA，沿现有 DESIGN.md 保持键盘可达和文字状态说明，不仅用颜色区分。

**输出：** 设备已恢复时不再因旧未知显示持续故障；旧记录和 WMS 待处理事实仍能查到。

### S6 — 整体验证、移交与旧路径退出

- [ ] 闭合实际测试消费者与精确 HEAVY mapping；新增 kind/字段/索引、域 operation、迁移及工具资产全部纳入影响清单。
- [ ] 使用独占临时 PostgreSQL 验证迁移与并发；真实 worker 检查 `tests/e2e/device_command/test_device_command_production_wiring.py` 及 selector 实际选择的主动唤醒/恢复场景。
- [ ] 一次主 Review 修复闭环后，运行最终快照所需 QUALITY、selector 选中 HEAVY、独立插件测试及前端合同/QA。失败按所属阶段处理，不放宽旧断言掩盖环境或传播遗漏。
- [ ] 扫描旧摘要身份、设备生命周期槽、全程 trace 准入、结果驱动纯输送、无用 blocker/API 和被替换的 SDK 导出；按 scope 删除残留，不改无关 Transport 行为。
- [ ] 更新 SRS、设备白皮书、设备附录、WMS 合同和索引。只将确被替换且不再承担当前职责的过程文档移到 `../archive_docs/wes_backend/`，不碰 `docs/hardware/`，不提前归档仍有有效内容的整份设计。
- [ ] 外部验收分别记录：ECS 临时卡料恢复/物料清除/失效拒绝/明确未接纳重试；WMS 异常接收/乱序补充/最终对账。缺少任一外部结果，不称为现场恢复闭环。

## 测试覆盖与失败路径

以下全部是计划要求，不是已运行的测试证据。现有用例将承接新合同，禁止靠增加文档测试“证明实现”。

```text
事件 -> 严格解析/身份去重 [现有 wire/API 测试需扩充]
     -> 同身份漂移拒绝；新身份同物料允许
     -> 替代引用先到/后到/冲突 [需 PostgreSQL 事务验收]
     -> 原事件唯一上下文 [现有 MaterialExecution 测试改合同]
WMS 决定 -> 原请求适用？ -> 否：留证，无动作
                       -> 是：原身份命令 [DecisionApplier + 插件分别测试]
命令 -> 领取竞争 -> 一个 token 有效 [PostgreSQL]
     -> ACK / 临时未接纳 / 明确拒绝 / 交付未知 [现有 Adapter/dispatch 扩充]
结果 -> 原身份校验 -> 同结果幂等 / 冲突拒绝 / 迟到闭合
     -> 后续成功归类旧异常 [结果先到/分类先到均验证]
观察 -> 同事务留证 -> 崩溃恢复 -> 插件产生 WMS 义务 [真实 worker + 插件测试]
WMS -> 不可用/到期可见；未知与真实补充身份独立 [可靠义务 + operation 测试]
UI -> 历史/当前分开 -> SSE/快照乱序、断线、空记录、原结果迟到 [前端 + QA]
```

| 故障 | 必须观察到的救济 | 主要测试所有者 |
| --- | --- | --- |
| 同请求重传/新请求同料 | 原请求无重复动作，新请求不被旧上下文挡住 | wire、执行关联、真实并发 |
| 旧决定与旧命令迟到 | WES 排除已知失效；ECS 最终适用性拒绝 | 基础派发、插件、供应商分别验证 |
| 新成功先于旧结果 | 分类保留原未知；原结果迟到正常应用且不影响新任务 | Evidence/命令事务 |
| 最后一件异常、没有后续事件 | 到期形成观察和可靠业务报告，不等待下一件 | 超时服务、worker、插件 |
| 数据提交后唤醒失败/进程退出 | 既有扫描重取，不丢观察、不重复业务义务 | 真实 worker |
| WMS 已记录但响应丢失 | 同身份重试得到重复接收；库存不重复变更 | WMS operation 与外部验收 |
| 替代关系缺失或查询预算耗尽 | 新请求继续，归类未确认可见；不伪造关系 | 基础关系查询/页面 |
| 共享资源确实被未知 Transport 占用 | 仍按该资源合同阻断，不能用新点位事件释放 | Transport 原测试和插件资源测试 |

重点回归：修订事件身份时仍保留合同允许的纯诊断扩展忽略规则；普通事件、结果、手工调试不能被本站业务请求字段错误拒绝。新合同不得重放已 ACK 的动作，也不得丢弃合法迟到结果。

## 性能、发布与执行顺序

事件幂等与替代查询使用精确索引；历史分类只更新确定关联的命令，避免 N+1 全设备历史遍历。关系检索、历史分页及 worker 批量均遵循既有请求/任务预算，数据库事务内不进行外部 HTTP。重试保留 deadline 和 next_attempt_at，不能因旧任务持续重试阻塞其他领取。延迟与吞吐以实施环境实测，不承诺未经测量的 p99。

S0 → S1 → S2 → S3 → S4 → S5 → S6 顺序执行。S1–S4 共享 SDK、Evidence、命令和业务合同，默认不并行写入；S5 在后端响应合同冻结后可独立开展，后端核心/插件最终测试由各自 owner 执行。当前不创建或清理 worktree。

发布前核清真实在途事实，停止旧相关进程，迁移并部署匹配的新核心/SDK/插件/前端，再启动并验证数据库业务路径和真实 worker。失败时先停止新触发并保留事实；仅在数据库和外部合同均允许时恢复匹配旧版本，否则修复前进，禁止盲启旧版。未发布不要求兼容双跑；本轮未授权执行以上发布动作。

## GSTACK REVIEW REPORT

| Review | Runs | Status | Findings |
| --- | --- | --- | --- |
| CEO | 1 | CLEAR（顶层） | D1–D7 已确认，范围保持 |
| 工程 | 1 | CLEAR（方案） | D1–D8 及 S0–S6 已落实；尚未实施、未验证现场 |
| Outside Voice | 0 | SKIPPED | Codex 宿主按技能跳过嵌套同系统评审，无跨模型结论 |
| 界面 | 0 | QA PLANNED | S5 修改现有诊断页，独立浏览器 QA 尚未执行 |

**VERDICT:** 工程设计收敛；进入实施时先执行 S0 冻结机器合同和实际影响清单。该结论不是代码、供应商或 WMS 验收。

NO UNRESOLVED DECISIONS
