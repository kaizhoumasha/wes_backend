# 本站驱动执行恢复实施计划

> 执行入口：遵循项目 `wes-implementation`；本计划按内聚切片顺序执行，不额外串联重复 Review。用户已接受工程推荐；这不等于授权本轮修改生产代码、Commit、Push、Merge 或 Deploy。

**目标：** 原任务可在 ECS 排障后按原身份继续；旧异常或结果未知不阻塞独立的新本站请求，执行事实可靠留存并交 WMS 对账。

**架构：** 复用 DeviceCommand、InboundEvidence、WmsConfirmation、SDK 和插件应用层。基础能力拥有身份、持久化、并发与恢复；插件拥有业务请求、WMS 数据和结果解释；ECS 继续按原 Status、Command 及结果合同提供动态状态与物理互锁。

**技术栈：** Python 3.13、FastAPI、SQLModel/SQLAlchemy、PostgreSQL、Celery；现有 Vue 诊断页。

**设计：** [顶层设计](../specs/2026-09-10-station-driven-execution-recovery-top-level-design.md)。本计划细化其 CEO D1–D7、工程 D1–D3；工程 D4–D8 按用户“全部接受你的推荐”的授权收敛。

**状态：** S1、S2 的 WES 内部基础切片、S3 的本地命令观察留存与既有 WMS operation 安全重试，以及 S5 的后端历史/SSE 投影已实施。S3 对外反馈和 S4 仍等待已批准 WMS 合同；S5 前端等待可追溯的后端提交后冻结 canonical OpenAPI。当前实现不修改供应商 ECS wire，不代表供应商、WMS、部署或现场验收通过。

## 本轮立项：三项 P1 修复

核查基线为 `develop@06e56536`；依据 9 月 9–10 日对话与联调记录。历史处理记录不是当前服务器状态。沿用本计划，不另建项目、通用纠错接口或恢复状态机。

| 项目 | 要解决的实际问题 | 交付及唯一归属 | 完成判据 |
| --- | --- | --- | --- |
| R1：响应纠正 | WMS 修正来源数据后，同身份响应发生内容冲突，现场曾经授权清理错误记录才能继续 | 本计划 S0 冻结恢复矩阵、S3 实现公共留证及恢复约束；业务 operation 的重新求值条件由所属插件与合同决定 | 原报文应用失败可按原证据恢复；内容纠正有明确合同路径；不删旧证据、不重复物理动作、不冒充 WMS 对账 |
| R2：现场消费者 | `manual-picking` 尚无完整 handler，但 `workline_integration_debug/service.py` 已直接创建命令并维护 Run/Step | 本计划 S2/S3 负责共享签名及机械调用迁移；[人工出库联调台方案](2026-09-10-manual-picking-integration-workbench-optimization.md)负责 Run/Step、页面和业务流程改造 | 基础与现用调用者均验证；明确哪些前置条件保留、哪些跨点阻塞删除，无双重实施 owner |
| R3：手工接纳 | 无事件来源的手工命令不能使用业务 Evidence 关联，旧命令可能延迟到达 | S2 将正式业务排队与既有调试安全槽分开；不扩展供应商 Command wire | 有效本站请求不被旧异常锁住；MANUAL_DEBUG、EVENT_DEBUG 仍受本地未闭合命令保护 |

**执行边界：** 本轮立项落实设计和计划。首个生产补丁仍须按 `wes-implementation` 冻结符号、调用链、测试所有权与风险范围；不由文档立项推定 Commit、Push、Merge 或 Deploy 授权。已有独立联调台方案中的业务重构不在本计划重复实现。

**当前外部合同闭合项：** R1 涉及的每个 operation 是否允许重新求值、原义务如何权威闭合。S1/S2 不再依赖新增 ECS 字段或错误码；供应商物理互锁与现场恢复能力仍须按原合同单独验收。

## 全局约束

- ECS/WMS 排障并提供有效事实后，WES 自动续接；不得要求现场人员在 WES 恢复、解锁、重评、删除记录或重建数据。业务作废/纠正依据由正式机器接口传递；对端健康不等于原义务已闭合。WES 自身处理失败由既有可靠处理机制恢复。
- 基础与业务分离；基础部署、运行和测试不依赖具体插件。基础不导入 `workline_plugins/`，业务不复制 HTTP、outbox、重试或领取机制。
- 未发布系统直接替换旧合同和消费者，不加版本别名、兼容路径或永久开关。开发测试数据可清理，不能把真实未知物理动作当开发数据清除。
- 人类文档不编写测试代码；行为变更按 TDD，优先修改现有测试。聚焦、QUALITY、HEAVY、迁移及供应商验收分别记录。
- 点位准入、命令事实、历史分类独立。Transport、实际物理 FIFO/LIFO、资源绑定和明确停用规则仍按各自合同执行。
- 不修改无关 dirty 文件。实施前记录状态和内容指纹；本轮人工出库联调台计划存在独立修改，保持原样。实施时重新核对共享文件及运行环境是否需要隔离。
- 当前没有新发行包、框架或进程类型。SDK 沿现有独立安装链发布；所有长驻进程使用匹配合同和迁移版本。

## 工程判断与证据

| 决定 | 代码证据与问题 | 推荐方案 |
| --- | --- | --- |
| D1：事件身份 | `device/contracts.py:234` 无公开事件身份；`device_evidence_service.py:643` 以报文摘要生成身份 | 保持原 Event wire；WES 使用完整规范化包络摘要作为内部 Evidence 身份 |
| D2：替代关系 | 原合同没有替代语义，且 WES 不能从时间或条码可靠推断 | 不新增供应商字段，不建立推测性替代；新旧请求各自保留事实 |
| D3：历史表示 | `device/models/command.py:273` 把未知状态与设备占用合并 | 保持原状态，不新增历史归类字段；当前准入与历史诊断分离 |
| D4：请求级准入 | `command.py:211` 设备唯一槽；`material_execution.py:91` 未关闭 trace 唯一；`material_execution_service.py:104` 按 trace 复用/冲突 | 事件触发执行按初始 Evidence 幂等，命令按已有执行引用及 command_code 幂等；物料标识不作为本站请求独占锁 |
| D5：故障与反馈 | `device_command_service.py:704` 的超时推进只改状态；`InboundEvidenceKind` 没有本地命令观察；placement/NG 只表示真实到位 | 同事务保存本地命令观察，复用 Evidence 处理与 WMS 可靠义务；未知和迟到真实结果分别反馈 |
| D6：ECS 接纳 | 白皮书已有 Status、ACK、429/503 明确未接纳与 delivery unknown 分类 | 保持供应商合同；Status 动态忙碌在 WES 内延后，429/503 仍按原身份有界重试，未知不重发 |
| D7：正式业务入口 | `device_position_confirmed.py:54–57` 用上一点结果触发下一点动作；`target_decided.py:58` 仍预查询设备 readiness | 下一交互点用自己的请求触发；移除本站动作的重复状态门禁。迟到结果只回到原上下文处理与反馈 |
| D8：持久化、测试与性能 | 原设备槽测试会验证旧语义；仅内存 Mock 看不到多事务竞争及重启窗口 | 更新原测试所有者；索引查询、短事务、领取 fencing、真实 worker 恢复分层验证 |

上述为已核对代码的设计冲突，置信度 9/10；不称为本轮代码回归。`assert_fifo_head` 已发现定义但此次精确调用搜索未发现生产调用，不把它描述成已经发生的现场阻塞。实施时扫描后删除因本次语义替换失去用途的入口；真实物理队列的实现保留。

## 合同与持久化决定

### 1. 事件及来源

- ECS Event wire 保持 `device_code + event_type + timestamp + is_debug + data`，不新增公开事件 ID 或替代字段；`device_code` 继续代表明确交互点。
- InboundEvidence 使用完整规范化包络 SHA-256 形成内部 `source_identity`。完全相同报文重传幂等；现有字段任一变化即形成独立 Evidence。
- 命令、WMS 业务等待通过 MaterialExecution 保存初始 Evidence 关联，跨事务仍能找到原请求；不能从当前设备状态或最近一条事件补填。
- Command wire 保持原样，只以 `command_code` 做供应商幂等；内部 Evidence 身份不回传 ECS。

### 2. 独立上下文、历史事实与并发

- MaterialExecution 按初始 Evidence 唯一；同一 Evidence 并发只产生一个上下文，同物料不同事件产生独立上下文。
- 不新增替代关系、`historical_success_evidence_id` 或历史状态。新任务成功只更新自身，不能覆盖旧任务的 `RECONCILING`、FAILED 或迟到结果。
- 正式业务 DeviceCommand 不再使用同设备未闭合命令作为创建拒绝；每条命令仍按执行引用和 `command_code` 幂等。
- 派发领取使用短事务全局闸门，并以唯一索引保证同设备至多一条 `DISPATCHING`；不同设备仍可依次领取并并行执行外部 I/O。设备忙、非 AUTO、离线或状态过旧时，在该命令原 deadline 内延后。已 ACK 或 delivery unknown 不重发。
- MANUAL_DEBUG、EVENT_DEBUG 继续检查本地未闭合命令并使用同设备创建锁；正式业务放宽不扩散到调试动作。

### 3. 准入与命令结果

- 移除 `ux_device_commands_unclosed_device` 及所有用旧命令生命周期阻塞独立本站请求的准入消费者；新增仅约束同设备 `DISPATCHING` 的唯一索引，并保留 `command_code`、执行引用、调试请求身份的唯一约束与单命令领取 token。
- 不把该索引改成“只有历史归类后才释放”；新请求不等待替代引用、成功结果或旧异常关闭。ECS 负责实际接纳互斥。
- 手工调试继续要求权限、明确目标、固定上下文和本地停用校验。关联真实本站请求的动作使用已持久化来源，不伪造事件；无来源事件的动作须由自身静态合同规定 ECS 接纳时的适用性、有效期和执行互斥。操作者点击、设备 IDLE 和 WES 发送前检查不能替代接纳时校验。依据不足时拒绝该手工动作；原未知动作保留身份与必要围栏，但不阻断独立事件请求。删除全设备槽与落实这些约束必须在同一切片完成，不能先删索引后补合同。
- `MaterialExecution` 复用为本站请求的处理上下文：初始事件关联唯一，`material_trace_id` 为业务事实索引而非全局活动唯一键。`create_or_get_for_initial_evidence` 按同一初始 Evidence 幂等返回；同料再次到达或其他点位请求形成独立上下文。不新增 StationSession/全程链。
- SDK、FactProcessor、DecisionApplier 与插件保存来源关联；WMS 等待及结果回到原上下文。旧上下文 RECONCILING/CLOSED 不能挡新上下文；旧动作默认仍不自动重发。
- `PENDING` 明确未发出而到期仍为 TIMED_OUT；`DISPATCHING` 租约过期及 ACK 后无结果保持 RECONCILING。迟到原结果沿已有状态转换闭合。TIMED_OUT 与本地拒绝不能描述为 ECS 物理失败。
- 为支持“同一旧命令确实恢复”，业务 Fact 允许读取 RECONCILING 原上下文并保存/反馈原真实结果，但是否产生新动作必须单独通过原请求仍适用和业务授权校验。不能全局删除生命周期检查后任意恢复旧业务链。

### 4. ECS 接纳及重试

| 外部事实 | WES 行为 |
| --- | --- |
| `200 / ACK` | 接纳事实冻结，重复同命令不得再次驱动；后续以原命令结果闭合 |
| `429 / CAPACITY_EXCEEDED` 且有效 Retry-After；`503 / TEMPORARILY_UNAVAILABLE` 明确未接纳 | 原身份原载荷有界重试；ECS 重新评估临时拒绝，不能永远重放第一次“忙” |
| `409 / IDEMPOTENCY_CONFLICT`、超时、无可信响应、未定义组合 | 交付未知，保留身份与证据，不自动重发 |

不新增响应组合。严格匹配现有 HTTP、code、message，沿用固定响应校验。已接纳命令重复到达时仍按原合同返回接纳事实；身份内容漂移仍返回冲突。ECS 的内部缓存与 PLC 实现不由 WES 规定。

基础派发保留 Status 预查询。正式业务命令遇到离线、非 AUTO、非 IDLE、存在 active command 或状态过旧时，在原 deadline 内延后；身份、合同、本地停用和期限错误仍失败。调试命令维持既有 fail-closed 行为。

### 5. 本地观察与 WMS 反馈

- 新增封闭内部 Evidence kind `DEVICE_OBSERVATION`，表示 WES 的 `NOT_ACCEPTED` 或 `RESULT_UNKNOWN`，不伪造 `DEVICE_RESULT`。复用 InboundEvidence、领取、处理、唤醒和恢复扫描；同步修改 kind 检查约束及静态分派。
- 命令被明确拒绝/未发送到期，或进入交付/结果未知时，在相同事务保存观察 Evidence。每条命令、每种观察事实只有一个稳定身份；重复扫描不重复创建。已接纳后才获知的未知只记录事实，不形成重发请求。
- 插件消费观察后，在同一事务保存 typed WMS intent 对应的 WmsConfirmation 并标记该观察已消费；崩溃后可重取。基础独立运行时保存观察即可，无业务消费者不要求调用 WMS，更不能调用默认业务插件。
- 粗分域最小增加 `inbound.execution.observation_report@v1`，WES→WMS，使用现有 `POST /api/v1/wes/facts` 与公共信封。公开 typed method 为 `inbound_execution_observation_report`；wire/Adapter 放 `wms_adapter/inbound_material/`，不新建通用异常 API 或 outbox。
- 该 operation 的业务数据包含原执行/物料 trace、设备、原 command_code、内部初始 Evidence 身份、观察事实及时间、必要原因；`NOT_ACCEPTED | RESULT_UNKNOWN | EXECUTION_FAILED | EXECUTION_SUCCEEDED` 为封闭事实分类。无实际位置时不填位置，不把未知变为 NG；只有原匹配结果支持后两类。
- 沿用现有 `RECORDED | DUPLICATE` 事实响应和公共错误处理；表示 WMS 已记录事实，不表示库存对账完成。每一独立观察或迟到补充使用不同稳定 operation_id，同一次重试身份和 payload 不变。复用现有 `stable_operation_id`，不能在扫描时取当前时间重新造身份。
- 如旧命令已有未知反馈，原真实结果到达时生成独立补充义务，并继续按已有成功业务合同判断是否还需 placement/NG 上报。新增观察报告只提供事实，不重复修改库存。乱序到达 WMS 时以原命令关联和事实类型理解，不能按网络接收顺序覆盖事实。
- 已冻结的反馈不能因当前点位进入新任务而取消。WMS 不可用仍复用既有期限/重试政策；预算耗尽保留可见技术投递问题，技术投递恢复不等于 WES 承担业务对账。冻结插件版本缺席时保留待消费观察，已创建义务继续可靠投递。
- S0 把上述语义落实为严格 DTO/OpenAPI、错误码、字段约束和双方样例。外部 WMS 未支持新 operation 前，不能发布需要该反馈的完整业务切片；基础候选验证可独立进行。

### 6. 已有插件消费者

| 消费者 | 本轮处理 |
| --- | --- |
| EVENT_DEBUG / MANUAL_DEBUG | 改用统一身份、接纳分类和命令可靠机制；没有 WMS 业务 owner 的诊断不伪造业务反馈 |
| `rough_sorter` | 实际已有执行链，必须完成本站触发、独立上下文、异常与迟到反馈；不能用核心测试代替插件验收 |
| `manual_bin_processing` | 当前 `build_handlers()` 返回空 tuple；仅核对受影响 SDK/合同使用，不新增尚未实现的人工流程 |
| `manual-picking` | 当前仅资源定义及声明；不把四个扫码角色当成已实现的四条业务流程，不在核心硬编码其拓扑 |
| `src/app/workline_integration_debug/service.py` | 已有真实 DeviceCommand 调用与 Run/Step 推进，不因插件为空而跳过。共享调用迁移归本计划；业务关联、步骤推进和页面改造归联调台方案，按固定共享合同交接 |
| `src/app/transport/debug_run_service.py` | 自动联调回架 `outbound.bin.return_batch@v1` 的结果消费和 `WMS_RETURN_RECONCILING` 自动续接纳入 R1/R2；复用已保存请求、分配和运输事实，不重建 Run，不修改 Transport 物理围栏 |

粗分测量点沿用 `SCAN_COMPLETED` 发起准入；出口决策由该出口所属设备的 `MATERIAL_ARRIVED` 请求提供本站物料 trace 与严格位置对象。被 ECS 自主控制的输送段不再由上一个动作结果逐段触发 WES `MOVE_FORWARD`。同一请求确实需要 WES 指令的动作按其批准合同保留。

出口 WMS target 决策不能要求 WES 先找到正常完成的上一点执行。复用 `inbound.material.target_decide@v1` 并直接修订目标合同：输入采用本站执行/请求、物料 trace、实际出口位置和货架上下文，WMS 查业务主账并返回后续 placement 所需的 `pkg_id`、准入与目标关联。查不到时明确 WAIT/REJECT，不猜测授权。原上游业务关联作为 WMS 判断结果使用，不再是 WES 阻断本站调用的前提。SDK、wire、插件、Mock 和合同测试一次性替换旧 DTO。

### 7. 接口恢复：重新应用、内容纠正与物理未知

本节修复 R1。`InboundEvidenceService.accept` 的同身份内容冲突保护继续保留；`WmsConfirmationService.requeue_reconciling` 与 `supersede_after_wms_void` 不能未经 operation 合同论证就放开已保存响应的限制。

| 已知事实 | 恢复动作与身份 | 禁止行为 |
| --- | --- | --- |
| 原报文内容合法，持久接收后因 WES 处理故障未应用 | 修复处理故障后，复用原 Evidence、原请求及已有领取/恢复机制；事务校验是否已应用，已生成义务按原身份去重 | 重新调用设备、复制 Evidence 或通过新身份绕过已应用事实 |
| WES 校验实现有误，原始报文本身符合当前批准合同 | 先修复规范化/校验，按原 operation 既有接收及拒绝记录语义重新处理原报文；同请求只应用一次 | 修改原始报文、将纠正后的推导值写回原接收摘要 |
| WMS 返回内容本身错误，后续给出不同内容 | 原响应和冲突记录均保留。所属 operation 明确旧响应未被业务应用、旧义务的权威闭合及重新求值条件后，创建新的业务请求身份并关联原请求；不改变旧身份的不可变内容 | 共享入口自动接受最新响应、默认所有 operation 可重新求值、为继续流程删除旧响应 |
| 原响应已应用、已产生外部义务或物理动作可能被接纳 | 继续原身份结果与可靠义务处理；实际业务纠正由 WMS 按所属合同决定。独立本站请求仍可继续 | 把本地回滚、超时、人工确认“重试”当作旧动作未执行，换号重发 |

恢复矩阵须覆盖现场失败的具体 operation，而非只验证 prepare 的既有作废路径。S0 对每项记录实际 operation、原响应校验阶段、是否应用、派生动作、允许恢复入口与权威依据。合同没有纠正路径时明确列为外部依赖；本计划不新增通用 correction operation、任意报文编辑器或异常对账平台。技术恢复不等于 WMS 业务对账。

上述恢复由正式结果/纠正事实触发，或由合同允许的原身份重试与既有恢复扫描触发，不依赖 WES 人工操作。现有 `requeue_reconciling` 的“人工确认对端未接收”不是目标恢复流程；不能简单删除检查或重置状态，须让该 operation 的权威机器事实决定是否可恢复。对已保存错误响应的纠正，机器合同必须区分原响应与纠正事实；没有这个区别就仍存在内容冲突，不能承诺自动消解。S0 须将该合同缺口闭合，而不是留给 WES 操作员处理。

基础服务只负责原身份、证据关联、事务与幂等；插件解释重新求值资格和业务结果。UI 展示具体阻塞原因及原请求，不以整 Run 清空告警代替按事实重算。基础独立运行时无需业务插件，也不自行判定某个错误业务响应可替换。

### 8. 本轮必须继承的已实现基线

- WMS/ECS 合同允许的冗余字段继续忽略；身份摘要基于规范化后的合同字段，不能因本次新增事件字段退回整报文严格拒绝。
- 当前 `transport/service.py` 已在匹配原任务的成功结果应用中，按 WMS/RCS 的面向保证使用冻结 `target_face` 补齐缺失面向；原 Evidence 和摘要不改写。保留省略/null/空字符串的接收约定，以及失败/未知不推导、显式冲突不覆盖。这里只要求不回归，不在本站项目重复实现 Transport。
- 实际物理队列、货架区域容量和 Transport 围栏继续按所属合同生效；扫码事件独立不能作为释放依据。
- 发布验证复用[稳定性总计划](2026-09-09-stability-recovery-master.md)的进程、schema 和制品核对，确认 API、worker、Beat 指向正确数据库与匹配代码；健康检查不代替实际业务路径验证。

## 实施切片与验证

### S0 执行记录：隔离基线与合同阻塞

本轮已进入 Execution Lock，分类 `LARGE/HIGH-RISK`。实施目录为 `/Users/kaizhou/.codex/worktrees/d6a5/wes_backend`，分支 `codex/01a0890328f17031919c23095735e1f0`，HEAD `06e56536`。本次仅允许聚焦验证，不 Commit、Push 或 Deploy。

迁入的五份文档均保留：本文、顶层设计、人工出库联调台方案、superpowers 索引及架构文件索引。迁入后五份均为 staged 修改；暂存不代表已提交。人工出库联调台方案原内容 SHA256 为 `9c99e47be16e4f954e78746ebda1a163a0c0f3c9644525f0e606042d35020edb`，本轮不修改。主工作区内容与迁入快照不同，不从主工作区覆盖当前文件、不反向回写。

**工具证据：** `npx gitnexus status` 返回 Repository not indexed；对当前 worktree 的 upstream impact 返回 Repository not found。未借用主仓索引冒充当前快照，降级为精确符号/调用搜索。无生产补丁，以下是冻结的候选变更范围；合同解除阻塞后还须补齐实际签名、间接测试消费者及迁移清单，不能称为生产补丁前完整闭合。

| 切片 | 生产符号及已确认消费者 | 测试/持久化影响 |
| --- | --- | --- |
| S1 | `EcsDeviceEventReport`、`DeviceEvidenceService.accept_event/process_one`；`MaterialExecutionService.create_or_get_for_initial_evidence` 由 `FactProcessor` 调用 | wire/API、Evidence identity、MaterialExecution/FactProcessor 测试；Evidence 关联列及 active trace 唯一约束迁移，SDK 同步 |
| S2 | `DeviceCommandService.create_command_in_session` → DecisionApplier；`create_manual_debug_command` → device API、workline integration service；`create_event_debug_command_in_session` → DeviceEvidenceService；DeviceDispatchService | 命令唯一性、准入、派发、回调测试及 PostgreSQL 竞争；设备槽及历史依据字段迁移 |
| S3/R1 | WmsConfirmationService 的 dispatch、record_delivery_unknown、supersede_after_wms_void；InboundEvidenceService.accept；`TransportDebugRunService._prepare_return_batch/_advance_claimed_run` → `advance_active_runs` → Celery transport task | confirmation/Evidence、return_batch adapter、debug run 领域测试；新增 Evidence kind 需迁移；不增加纠正通用入口 |
| S4/R2 | rough_sorter 的 device/target handlers；`workline_integration_debug/service.py` 的真实命令创建与 point2/point3 推进 | 粗分插件测试独立运行；联调台业务改造由其方案拥有，本计划只负责共享调用迁移及交接 |
| S5 | device ingress history、诊断响应/SSE；前端诊断消费者 | 响应合同冻结后生成前端类型；前端聚焦与浏览器验收独立，当前不改前端 |

HEAVY 既有映射已定位于 `heavy-test-impact.toml`：device services（609–639 附近）、execution services/models（1162–1186 附近）、workline integration（2632 附近）及 Transport 模块映射。实际变更后由 selector 决定精确 manifest；未执行、未宣称 NONE。基础与插件测试不混用。尚无 schema、生成物或运行时改动，不初始化运行环境、不运行测试，也不为文档写 pytest。

#### 具体 operation 核查结果

| 合同/路径 | 已有依据 | 尚缺内容与切片结论 |
| --- | --- | --- |
| `outbound.bin.return_batch@v1`，WES→WMS 同步 decision | 出库合同 §9.2.2：READY 已完成储位分配；NO_BATCH 结束本次请求，后续重新求值用新身份；响应未知/UNAVAILABLE 使用原身份重试。用户进一步确认对端恢复后不得要求 WES 人工续接 | 公共 dispatcher 已将内部 deadline 收敛为观测窗口：仅 `RETRY / NOT_SENT / DELIVERY_UNKNOWN` 保持原身份原正文自动重试，确定响应、内容冲突或 owner 失效仍 fail closed。已保存错误响应的机器纠正合同仍待闭合 |
| `outbound.picking_task.prepare@v1` | 既有 supersede_after_wms_void 仅承接原请求作废；有响应时禁止直接重排队/替换 | 现有 WES 人工确认不能作为目标机制。需要 WMS 通过已批准接口给出原义务权威闭合依据；不能将 prepare 作废泛化为任意响应可覆盖。R1 阻塞 |
| ECS 事件及命令接纳 | 白皮书现有 Event、Status、ACK、明确未接纳和交付未知合同足以支撑 WES 内部排队 | 不新增公开来源、替代字段或错误码；S1/S2 在 WES 内闭合，供应商现场行为仍单独验收 |
| `inbound.execution.observation_report@v1` | 计划提出复用 WmsConfirmation 与现有 facts 路径 | 缺少批准的具体请求/响应合同及 WMS 接收依据；不能仅按计划语义臆造 DTO。S3 对外反馈阻塞 |
| 粗分出口 MATERIAL_ARRIVED 与 target_decide | 目标设计要求本站独立决策 | 新出口请求及 WMS 输入来源合同尚未冻结，依赖 S1–S3。S4 阻塞；S5 新字段展示随其等待 |

**本轮结论：** WMS 明确可安全重试的可靠义务不再因 WES 内部 deadline 转入人工恢复；对端恢复后由现有 worker 继续原身份原正文。S1/S2 已收敛为不改变供应商 wire 的独立 WES 切片：Evidence 摘要身份、按 Evidence 幂等的 MaterialExecution、正式业务命令内部排队及 Status 动态延后。WMS 内容纠正和新增业务反馈仍属于后续合同切片。

**继续执行后的实现：** `WmsConfirmationService._dispatch_claimed` 不再把已过内部 deadline 的可安全重试义务直接改为 `RECONCILING`；`RETRY / NOT_SENT / DELIVERY_UNKNOWN` 始终保留原 `operation_id`、正文和退避时间。确定响应、响应内容冲突、owner 失效及无法分类的结果仍进入 `RECONCILING`，因此没有放宽业务事实或用健康检查冒充结果。自动联调原请求由现有 Celery dispatcher 和提交后唤醒自动续接，不新增恢复枚举、人工按钮或第二套 worker。

#### 最小待确认合同（不要求 WES 人工操作）

1. **WMS 原请求长期恢复（已闭合）：** 既有合同已规定响应未知或 `UNAVAILABLE` 使用原 `operation_id` 和原正文重试；结合用户“对端恢复后无需 WES 操作”的确认，deadline 只作为内部观测窗口，不再终止这三类已由 Adapter 证明可安全重试的可靠义务。实现不刷新 identity、payload 或 deadline。
2. **WMS 错误结果纠正：** 对实际发生错误的 operation，明确机器可识别的原响应关联、纠正依据及旧义务处置；未应用与已应用分别处理。现有同步重复响应合同只能重放同一内容，不能承载同身份不同内容的纠正。确认后复用同域 typed 接入，不新增通用编辑/纠错 API。
3. **WMS 观察反馈与粗分出口：** 明确 observation 报告封闭 DTO/响应及独立出口 target 决策的数据来源。此项仅阻塞相应业务切片，不作为 S1/S2 基础能力运行前置条件。

第 2、3 项合同未闭合前，RED 不应通过伪造 WMS 纠正事件、手动改数据库为 COMPLETED 或假设 ECS 接纳保证来构造；它们继续阻塞响应纠正和新增反馈，不阻塞已闭合的原请求可靠重试。

每个行为切片：先更新所属失败验收 → 运行聚焦测试确认失败类别 → 修改生产实现及全调用点 → 运行同一领域测试转绿。纯合同文字不做 RED；机器合同与运行时行为一起验证。最终仅一次主 Review/闭环和必需门禁，不按每个任务重复完整 QUALITY。

### S0 — 固定实施基线与机器合同

输入为本文与顶层设计；输出为精确变更 manifest、目标 DTO/响应表及测试所有权。

- [ ] 重新记录 HEAD、dirty/staged/untracked 指纹。若实际共享写入范围或独立环境要求需要隔离，使用实施 worktree；迁移本设计及计划时保留原有修改，按项目要求初始化环境，不复用其他 worktree 的本地状态。
- [ ] 对下列切片生产符号批量运行 GitNexus upstream impact，固定直接/间接调用点、fixture、HEAVY mapping、迁移和生成物。新增清单外高风险再说明；不在本轮纯文档评审中冒称 impact 已完成。
- [x] 核对设备白皮书、粗分设备附录及 DTO/OpenAPI：S1/S2 保持原 Event/Command/Status wire，不新增字段或错误码；后续 observation operation、出口事件及 target 数据来源另行冻结。
- [ ] 梳理旧 `DeviceEventCommandBlock` 与手工重评接口的所有实际用途：随新事件准入替换而失去用途的模型/路由/展示整体删除，保留不属于该设备槽机制的技术诊断能力。
- [ ] 闭合 R1 operation 恢复矩阵和 R3 两类接纳合同；对未确认的外部合同记录责任方、所需真实报文/保证与受阻切片，不把 proposed DTO 当作供应商已实现能力。
- [ ] 将 `workline_integration_debug/service.py` 的命令创建、结果读取、point2/point3 推进及所属测试列入 manifest；与联调台方案冻结交接表：本计划迁移公共调用，联调台方案修改业务流程，同一符号一次只由一个 owner 修改。

**验收：** 每个对外字段有唯一生产定义和相同域合同测试 owner；无“缺字段仍接受”的兼容路径。供应商/WMS 样例分别标为目标合同与真实验收结果。

### S1 — Event Evidence 与独立本站上下文

**修改：** `src/app/execution/models/material_execution.py`、对应 Repository、`services/material_execution_service.py`；粗分插件执行持久化消费者及随机 revision 迁移。Event wire 和 Evidence 摘要算法保持不变。

- [x] 复核 `tests/contracts/device/test_uniform_ecs_wire.py`、`tests/api/test_device_ecs_callbacks.py` 和 Evidence 测试，确认原 Event wire、摘要身份及 ACK 时序不变。
- [x] 不新增直接替代关系；完全相同报文重传复用现有 Evidence，不同规范化包络形成独立 Evidence。
- [x] 修改 `create_or_get_for_initial_evidence`：按原 Evidence 幂等，取消活动 trace 唯一作为本站入口限制；独立新请求不复用旧 RECONCILING 上下文。
- [x] 更新 `tests/runtime/execution/test_material_execution.py`、`test_fact_processor.py` 及对应 PostgreSQL 测试，证明同物料不同请求可独立存在、同请求竞争只生成一个上下文。

**输出：** 后续切片可读取稳定的初始 Evidence 和唯一本站上下文，不需要物料全程状态正常，也不依赖供应商新增字段。

#### S1 执行记录

已在实施 worktree 完成按初始 Evidence 唯一的本站上下文及数据库迁移：同一 Evidence 并发幂等，同物料不同 Evidence 独立存在。
粗分插件的执行 `code_digest` 纳入内部 Evidence ID，避免同料新事件复用旧执行。未修改供应商 Event/Command wire，也未建立推测性替代关系。

先前包含供应商字段的验证快照已失效，不作为当前证据；S2 完成后的当前快照证据见下节。未 Commit、Push、Deploy，也未取得供应商现场验收。

### S2 — 正式业务排队与原身份派发

**修改：** `src/app/device/models/command.py`、`services/device_command_service.py`、`services/device_dispatch_service.py` 及数据库迁移；供应商 Adapter、Command DTO 和结果应用保持原合同。

- [x] 以 PostgreSQL 并发 RED 证明两个 worker 会同时领取同设备命令；增加短事务领取闸门、同设备 `DISPATCHING` 排除查询及数据库唯一约束后转绿。

- [x] 在 `test_device_command_service.py`、`test_dispatch_service.py` 以 RED 证明正式业务同设备可并存、动态 Status 忙碌不应永久失败，再修改实现转绿。
- [x] 移除全设备未闭合命令唯一索引及正式创建路径的 capacity 拒绝；保留命令身份幂等、claim token 和调试创建锁。
- [x] 保持旧 ACK、RECONCILING、FAILED 和迟到结果事实不变；新请求不覆盖或替代旧记录。
- [x] 更新 `tests/integration/device_command/test_device_command_constraints.py`，证明正式业务状态不再占全局设备槽。
- [x] 保留现有 ECS `429/503` 明确未接纳重试、ACK 后不重复驱动和交付未知不重发语义；Status 动态阻塞只在原 deadline 内延后。
- [x] R3 保留 MANUAL_DEBUG、EVENT_DEBUG 的本地未闭合命令检查；正式业务准入放宽不扩散到调试动作。
- [x] R2 核对真实联调入口：正式业务只由 `DecisionApplier` 调用 `create_command_in_session`，联调台继续使用隔离的 `create_manual_debug_command`，无需机械签名迁移；相关 runtime/API/DecisionApplier 测试 `129 passed`。Run/Step 业务变更仍由联调台方案承接。

**输出：** 单条命令可靠机制独立可测；没有插件或 WMS 也能验证原结果与后续命令互不阻塞。

#### S1/S2、S3 本地观察/安全重试及 S5 后端当前快照验证记录

- 当前设备、执行、WMS 可靠续送及诊断投影聚焦回归：`494 passed`；粗分插件启动回归：`37 passed`。
- migration/HEAVY selector 选中的隔离 PostgreSQL、Redis、真实 worker、WMS operation wiring 与 schema 集合：升级至 `f7cf0cd8c6d4`，`133 passed`。
- `./scripts/git-quality-gate.sh --profile quality`：`3668 passed, 5 skipped`，其余静态、安全、架构和脚本门禁全部通过。
- GitNexus 索引已刷新，但当前 MCP 的 LadybugDB reader 版本落后于索引版本，不能提供可信符号影响结果；本快照降级按精确调用点、测试所有权、HEAVY mapping 和最终 diff 闭合。
- 当前结论为 `IMPLEMENTED - VERIFIED IN WORKTREE`；未 Commit、Push、Merge、Deploy，也未完成供应商/现场业务验收。

### S3 — 本地命令观察和可靠业务反馈接入

**修改：** 命令超时/拒绝路径、InboundEvidence kind/约束/静态处理、SDK typed fact 与 `wms_operations.py`；`src/app/wms_adapter/inbound_material/` 新 operation 接入及 WmsConfirmation 原义务生命周期。

- [x] 完善 `tests/runtime/device_command/test_reconciliation_service.py`：未发送到期/确定拒绝记录 `NOT_ACCEPTED`，ACK/交付未知记录 `RESULT_UNKNOWN`；状态推进与观察同事务，重复身份幂等且无结果不伪造 `DEVICE_RESULT`。
- [ ] 接入 `DEVICE_OBSERVATION` 的正常和重启处理；提交后主动唤醒，既有扫描兜底，不另建队列/Beat 任务。
- [ ] 按第 5 节增加固定 typed operation；测试位于 `tests/contracts/wms_adapter/inbound_material/` 和需要真实事务时的 `tests/integration/wms_adapter/inbound_material/`，不复制共享 HTTP 测试矩阵。
- [ ] 完善 `test_wms_confirmation_service.py`、`test_wms_confirmation_dispatch.py`：旧业务已归类仍保留投递、同反馈重试不换号、迟到补充不覆盖首次反馈、WMS 故障/期限耗尽可见。
- [x] 对既有 WMS operation 的 `RETRY / NOT_SENT / DELIVERY_UNKNOWN` 取消内部 deadline 人工门禁；跨窗口重复派发仍使用同一 identity/payload，真实 worker 的 `return_batch` 生产 wiring 同步验证。
- [ ] R1 先在现有 Evidence/confirmation 测试复现原响应内容冲突，分别验证合法原报文重新应用与内容变更仍冲突。所属 operation 的合同测试验证仅在批准条件下允许新请求；插件测试验证业务资格，不能由共享机制测试代替。
- [ ] 使用相应 PostgreSQL 测试验证恢复与原响应应用并发时只产生一次业务义务；进程在提交后退出由既有 worker 恢复。已派生动作或已保存有效终态的情况不得走未应用响应的重新求值路径。
- [ ] R1/R2 闭合已保存错误响应的机器纠正合同；原请求可安全重试已由公共 dispatcher 自动续接，不再要求 `TransportDebugRunService` 绕过 `WMS_RETURN_RECONCILING`。确定错误响应仍不得靠重发、人工改库或健康检查冒充有效结果。

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

- [x] 后端历史快照和 SSE 使用同一字段语义；`DEVICE_OBSERVATION` 保留原命令事实，不把 APPLIED 当物理成功。
- [ ] 前端展示原状态与当前请求，不凭历史未知显示当前设备故障。
- [ ] 删除随旧 blocker 退役失去用途的“先关闭再重评”操作；保留纯技术错误详情与原身份追踪。不新增 WMS 业务对账按钮。
- [ ] 更新前端 `tests/unit/views/ops/device-diagnostics/DeviceEvidenceTable.test.ts`、`DeviceDiagnosticsPage.test.ts`、`useDeviceEvidenceStream.test.ts`；覆盖先实时后快照、加载失败、空记录、原结果迟到、查看详情及重复操作。
- [ ] 与联调台页面交接原 Evidence、纠正请求关联及局部告警重算语义；本切片只修改设备诊断页，联调台页面及 Run/Step 恢复交互由联调台方案验收，不增加通用“删除错误数据继续”按钮。
- [ ] 在真实页面做浏览器 QA，沿现有 DESIGN.md 保持键盘可达和文字状态说明，不仅用颜色区分。

**输出：** 设备已恢复时不再因旧未知显示持续故障；旧记录和 WMS 待处理事实仍能查到。

#### S5 后端执行记录

后端历史查询已纳入 `DEVICE_OBSERVATION`，并复用同一 `DeviceEvidenceUpdate` 投影向历史响应和专用 SSE 输出
`observation`、`reason_code`、`observed_at`。命令派发和到期对账在状态与 Evidence 事务提交后才做 best-effort 发布；
回滚不发布，Redis 通知失败不影响已提交事实，页面仍可刷新历史恢复。`DeviceIngressKind` 及供应商 callback DTO、路径、ACK、
Command/Event/Result wire 均保持原样；新增 `DeviceEvidenceKind` 只属于 WES 诊断合同。

前端隔离 worktree 已从 `wes_frontend@f9111d4` 创建，相关基线 `38 passed`。前端 canonical 冻结工具要求输入必须是干净的
后端 `develop` commit；当前 feature 快照未获 Commit 授权，因此未伪造 `.contract-sync-record.json`，也未加入临时字段映射。
后端提交并形成可追溯输入后，再执行正式 contract freeze、生成类型、页面 RED→GREEN 和浏览器 QA。

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
     -> 原事件唯一上下文 [现有 MaterialExecution 测试改合同]
WMS 决定 -> 原请求适用？ -> 否：留证，无动作
                       -> 是：原身份命令 [DecisionApplier + 插件分别测试]
命令 -> 领取竞争 -> 一个 token 有效 [PostgreSQL]
     -> ACK / 临时未接纳 / 明确拒绝 / 交付未知 [现有 Adapter/dispatch 扩充]
结果 -> 原身份校验 -> 同结果幂等 / 冲突拒绝 / 迟到闭合
     -> 新旧结果分别保存，不互相改写 [结果乱序仍按各自身份闭合]
观察 -> 同事务留证 -> 崩溃恢复 -> 插件产生 WMS 义务 [真实 worker + 插件测试]
WMS -> 不可用/到期可见；未知与真实补充身份独立 [可靠义务 + operation 测试]
UI -> 历史/当前分开 -> SSE/快照乱序、断线、空记录、原结果迟到 [前端 + QA]
```

| 故障 | 必须观察到的救济 | 主要测试所有者 |
| --- | --- | --- |
| 同请求重传/新请求同料 | 原请求无重复动作，新请求不被旧上下文挡住 | wire、执行关联、真实并发 |
| 旧决定与旧命令迟到 | 未发送命令按原 deadline 排队；已 ACK 或交付未知不重发 | 基础派发、插件、供应商分别验证 |
| 新成功先于旧结果 | 原未知保持原样；原结果迟到正常应用且不影响新任务 | Evidence/命令事务 |
| 最后一件异常、没有后续事件 | 到期形成观察和可靠业务报告，不等待下一件 | 超时服务、worker、插件 |
| 数据提交后唤醒失败/进程退出 | 既有扫描重取，不丢观察、不重复业务义务 | 真实 worker |
| WMS 已记录但响应丢失 | 同身份重试得到重复接收；库存不重复变更 | WMS operation 与外部验收 |
| 同物料新 Evidence 到达 | 形成独立上下文；不推测替代或覆盖旧请求 | Evidence/执行关联 |
| 共享资源确实被未知 Transport 占用 | 仍按该资源合同阻断，不能用新点位事件释放 | Transport 原测试和插件资源测试 |
| 合法原响应处理失败 | 原 Evidence 重新应用一次，既有动作不重发 | Evidence/confirmation 及 worker |
| 错误响应被 WMS 修正 | 保留旧响应和冲突；按 operation 批准条件重新求值，不能无条件换号 | operation 合同、插件及事务测试 |
| ECS/WMS 已恢复并提供有效原结果或纠正事实 | WES 自动接收、应用并重算对应阻塞，无恢复按钮、数据库操作或数据重建；未恢复的其他异常仍保留 | 基础可靠机制、各业务消费者及外部验收 |
| 手工旧命令延迟到新对象到达后 | 调试安全槽继续拒绝同设备混发，不阻断正式业务请求持久化与排队 | 基础准入与供应商边界 |
| 人工出库当前步骤异常 | 已确认的后续本站请求按所属业务条件处理，原结果只更新原动作；不得用手填条码冒充事件 | 联调台方案的 Run/Step、插件及页面验收 |

重点回归：修订事件身份时仍保留合同允许的纯诊断扩展忽略规则；普通事件、结果、手工调试不能被本站业务请求字段错误拒绝。新合同不得重放已 ACK 的动作，也不得丢弃合法迟到结果。

## 性能、发布与执行顺序

事件幂等继续使用现有规范化摘要；初始 Evidence 唯一索引保证上下文幂等，不增加替代关系查询。历史分页及 worker 批量遵循既有请求/任务预算，数据库事务内不进行外部 HTTP。动态 Status 阻塞只更新原命令的 `next_attempt_at`，保留 deadline，不能改写为永久失败或重发已接纳动作。延迟与吞吐以实施环境实测，不承诺未经测量的 p99。

S0 → S1 → S2 → S3 → S4 → S5 → S6 顺序执行。S1–S4 共享 SDK、Evidence、命令和业务合同，默认不并行写入；S5 在后端响应合同冻结后可独立开展，后端核心/插件最终测试由各自 owner 执行。后端和前端已分别迁入同名隔离 worktree；未经授权不提交、推送、合并、部署或清理。

发布前核清真实在途事实，停止旧相关进程，迁移并部署匹配的新核心/SDK/插件/前端，再启动并验证数据库业务路径和真实 worker。失败时先停止新触发并保留事实；仅在数据库和外部合同均允许时恢复匹配旧版本，否则修复前进，禁止盲启旧版。未发布不要求兼容双跑；本轮未授权执行以上发布动作。

## GSTACK REVIEW REPORT

| Review | Runs | Status | Findings |
| --- | --- | --- | --- |
| CEO | 1 | CLEAR（顶层） | D1–D7 已确认，范围保持 |
| 工程 | 3 | CURRENT SCOPE CLEAR / PLAN PARTIAL | S1、S2、S3 本地观察/安全重试及 S5 后端已实施并通过当前快照门禁与固定 diff Review；R1 对外纠正、S4 和 S5 前端未完成 |
| Outside Voice | 0 | SKIPPED | Codex 宿主按技能跳过嵌套同系统评审，无跨模型结论 |
| 界面 | 0 | QA PLANNED | S5 修改现有诊断页，独立浏览器 QA 尚未执行 |

**VERDICT:** 当前 WES 内部实施范围 Review 无阻断问题；R1 对外纠正、S4、S5 前端及外部运行时验收仍未完成。当前结果不代表整份计划完成。

**UNRESOLVED DECISIONS:**

- R1：具体 operation 的纠正/重新求值资格与旧义务权威闭合方式，须有双方合同依据。
