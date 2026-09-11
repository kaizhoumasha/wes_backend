# 本站驱动执行恢复实施计划

> 执行入口：遵循项目 `wes-implementation`；本计划按内聚切片顺序执行，不额外串联重复 Review。已有授权已执行到后端本地 Commit 与前端 Push/PR；未授权 Merge 或 Deploy。

**目标：** 原任务可在 ECS 排障后按原身份继续；旧异常或结果未知不阻塞独立的新本站请求，相关事实在 WES 内可靠留存并可诊断。

**架构：** 复用 DeviceCommand、InboundEvidence、既有 WmsConfirmation 续送和诊断投影。WES 基础能力拥有身份、持久化、并发与恢复；ECS 继续按原 Status、Command 及结果合同提供动态状态与物理互锁。本次不修改业务插件、SDK typed operation 或 WMS 新合同。

**技术栈：** Python 3.13、FastAPI、SQLModel/SQLAlchemy、PostgreSQL、Celery；现有 Vue 诊断页。

**设计：** [顶层设计](../specs/2026-09-10-station-driven-execution-recovery-top-level-design.md)。本计划只实施其中的 WES 基础恢复与诊断切片；WMS 业务纠正、观察反馈和粗分出口属于后续独立需求，不是本次完成条件。

**状态：** 本次范围 S1、S2、S3 本地观察、既有 WMS operation 安全重试及 S5 前后端诊断已实现并验证。粗分插件的机械摘要传播已从当前工作树撤回；R1 响应纠正、WMS 观察反馈和 S4 粗分出口移为后续独立需求，不阻塞本次收尾。当前实现不修改供应商 ECS wire；后端尚未推送，前端尚未合并，均未部署，也不代表现场验收通过。

## 本次范围与后续独立需求

初始核查基线为 `develop@06e56536`；当前后端实现快照为 `51533683`，前端实现快照为 `5f64da5` / PR #121。历史处理记录不是当前服务器状态。沿用本计划，不另建项目、通用纠错接口或恢复状态机。

| 项目 | 范围归类 | 当前处理 | 完成判据 |
| --- | --- | --- | --- |
| R1：WMS 响应纠正 | 后续独立业务需求 | 保留现有冲突保护，不新增纠正 operation、编辑器或恢复按钮 | 未来按具体 operation 合同另行立项 |
| R2：现场业务消费者 | 后续独立业务需求 | 仅核对现有调用没有签名传播；Run/Step 和插件流程不修改 | 由[人工出库联调台方案](2026-09-10-manual-picking-integration-workbench-optimization.md)独立验收 |
| R3：手工接纳 | 本次范围，已完成 | 正式业务排队与既有调试安全槽分开，不扩展供应商 Command wire | 有效本站请求不被旧异常锁住；MANUAL_DEBUG、EVENT_DEBUG 仍受本地未闭合命令保护 |

**执行边界：** 本次只落实 WES 基础和诊断能力。生产补丁已按 `wes-implementation` 冻结符号、调用链、测试所有权与风险范围；后端 Commit 与前端 Push/PR 已在各自授权范围内完成，Merge 与 Deploy 仍需独立授权。业务插件、联调台业务流程和新 WMS operation 均不在本次修改范围。

**当前外部合同闭合项：** 无。本次不依赖新增 ECS 字段、错误码或 WMS operation；供应商物理互锁与现场恢复能力只按原合同在部署后单独验收。

## 全局约束

- ECS 排障并提供原身份有效结果后，WES 按既有入口自动续接；不得要求现场人员删除记录或重建数据。WES 自身处理失败由既有可靠机制恢复。本次不承诺新增 WMS 业务纠正或对账能力。
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

### 5. 本地观察

- 新增封闭内部 Evidence kind `DEVICE_OBSERVATION`，表示 WES 的 `NOT_ACCEPTED` 或 `RESULT_UNKNOWN`，不伪造 `DEVICE_RESULT`。复用 InboundEvidence、领取、处理、唤醒和恢复扫描；同步修改 kind 检查约束及静态分派。
- 命令被明确拒绝/未发送到期，或进入交付/结果未知时，在相同事务保存观察 Evidence。每条命令、每种观察事实只有一个稳定身份；重复扫描不重复创建。已接纳后才获知的未知只记录事实，不形成重发请求。
- 本次只保存并展示 WES 内部观察，不把它交付给业务插件，也不创建新的 WMS 可靠义务。基础能力没有业务消费者时仍可独立运行。

### 6. 后续业务插件与 WMS 扩展（不属于本次范围）

原设计提出的 `inbound.execution.observation_report@v1`、粗分出口 `target_decide`、迟到结果业务补充及插件消费，均需要业务所有者和 WMS 合同。它们不属于本次补丁、验证或发布门禁；未来立项时仍须复用 WmsConfirmation、保持稳定 identity/payload，且不能覆盖原 Evidence 或把未知伪造成物理结果。

现有 `rough_sorter`、`manual_bin_processing`、`manual-picking`、`workline_integration_debug` 和 Transport debug 的业务流程均保持原样。本次不修改插件代码、SDK typed operation、WMS Adapter/Handler、Run/Step 推进或 Transport 围栏。

### 7. 后续 WMS 响应纠正（不属于本次范围）

R1 继续保留为独立业务问题。当前补丁只保持 `InboundEvidenceService.accept` 的同身份内容冲突保护，以及既有 WMS operation 对 `RETRY / NOT_SENT / DELIVERY_UNKNOWN` 的原身份安全续送；不放开已保存响应，不新增 generic correction operation、任意报文编辑器或人工改库恢复路径。

未来若某个 operation 需要接受 WMS 的不同内容，必须由该 operation 的合同明确原响应是否已应用、旧义务如何权威闭合、重新求值资格及新身份关联。本次实现和验收均不依赖这些决定。

### 8. 本轮必须继承的已实现基线

- WMS/ECS 合同允许的冗余字段继续忽略；身份摘要基于规范化后的合同字段，不能因本次新增事件字段退回整报文严格拒绝。
- 当前 `transport/service.py` 已在匹配原任务的成功结果应用中，按 WMS/RCS 的面向保证使用冻结 `target_face` 补齐缺失面向；原 Evidence 和摘要不改写。保留省略/null/空字符串的接收约定，以及失败/未知不推导、显式冲突不覆盖。这里只要求不回归，不在本站项目重复实现 Transport。
- 实际物理队列、货架区域容量和 Transport 围栏继续按所属合同生效；扫码事件独立不能作为释放依据。
- 发布验证复用[稳定性总计划](2026-09-09-stability-recovery-master.md)的进程、schema 和制品核对，确认 API、worker、Beat 指向正确数据库与匹配代码；健康检查不代替实际业务路径验证。

## 实施切片与验证

### S0 执行记录：隔离基线与范围冻结

本轮已进入 Execution Lock，分类 `LARGE/HIGH-RISK`。后端实施目录为 `/Users/kaizhou/.codex/worktrees/d6a5/wes_backend`，分支 `codex/01a0890328f17031919c23095735e1f0`，由基线 `06e56536` 形成提交 `51533683`；本地主工作区 `develop` 已快进到同一提交，`origin/develop` 仍为 `06e56536`。前端同名隔离 worktree 形成提交 `5f64da5` 并已 Push、创建 PR #121。未 Merge 或 Deploy。

迁入的五份文档均保留：本文、顶层设计、人工出库联调台方案、superpowers 索引及架构文件索引，并随后端实现一并提交。实施期间保护主工作区和前端原有无关 dirty；未以主工作区内容覆盖 worktree，也未改写供应商原始协议或硬件资料。

**工具证据：** 初始 worktree 未索引时先降级为精确符号/调用搜索；最终 staged 快照的 GitNexus 变更检查覆盖 44 个文件、203 个符号，报告 affected processes 0、risk low。生产补丁、直接/间接测试消费者、迁移和 HEAVY mapping 已按下表冻结；后续业务合同不属于当前 manifest。

| 切片 | 生产符号及已确认消费者 | 测试/持久化影响 |
| --- | --- | --- |
| S1 | `EcsDeviceEventReport`、`DeviceEvidenceService.accept_event/process_one`；`MaterialExecutionService.create_or_get_for_initial_evidence` 由 `FactProcessor` 调用 | wire/API、Evidence identity、MaterialExecution/FactProcessor 测试；Evidence 关联列及 active trace 唯一约束迁移，不迁移插件消费者 |
| S2 | `DeviceCommandService.create_command_in_session` → DecisionApplier；`create_manual_debug_command` → device API、workline integration service；`create_event_debug_command_in_session` → DeviceEvidenceService；DeviceDispatchService | 命令唯一性、准入、派发、回调测试及 PostgreSQL 竞争；设备槽及历史依据字段迁移 |
| S3 | DeviceDispatchService 的超时/拒绝观察；InboundEvidenceService 的内部 kind；WmsConfirmationService 既有安全重试 | reconciliation、Evidence、confirmation 与 return_batch wiring；不新增 WMS operation 或业务消费者 |
| S5 | device ingress history、诊断响应/SSE；前端诊断消费者 | 已按后端提交 `51533683` 冻结 canonical OpenAPI，生成类型并完成前端聚焦测试、全量测试、构建与浏览器 QA |

HEAVY mapping 已随生产模块和迁移更新，由 selector 生成精确 manifest；隔离 PostgreSQL、Redis、真实 worker、WMS operation wiring 与 schema 集合共 `133 passed`。基础与插件测试未混用。新增随机 revision `f7cf0cd8c6d4`，前端生成物由 canonical contract freeze 产生；未为人类文档编写 pytest。

#### 具体 operation 核查结果

| 合同/路径 | 已有依据 | 尚缺内容与切片结论 |
| --- | --- | --- |
| `outbound.bin.return_batch@v1`，WES→WMS 同步 decision | 出库合同 §9.2.2：READY 已完成储位分配；NO_BATCH 结束本次请求，后续重新求值用新身份；响应未知/UNAVAILABLE 使用原身份重试。用户进一步确认对端恢复后不得要求 WES 人工续接 | 公共 dispatcher 已将内部 deadline 收敛为观测窗口：仅 `RETRY / NOT_SENT / DELIVERY_UNKNOWN` 保持原身份原正文自动重试，确定响应、内容冲突或 owner 失效仍 fail closed。已保存错误响应的机器纠正合同仍待闭合 |
| `outbound.picking_task.prepare@v1` | 既有 supersede_after_wms_void 仅承接原请求作废；有响应时禁止直接重排队/替换 | 保持现状；响应纠正属于后续独立需求，不阻塞本次范围 |
| ECS 事件及命令接纳 | 白皮书现有 Event、Status、ACK、明确未接纳和交付未知合同足以支撑 WES 内部排队 | 不新增公开来源、替代字段或错误码；S1/S2 在 WES 内闭合，供应商现场行为仍单独验收 |
| `inbound.execution.observation_report@v1` | 原大计划提出的业务扩展 | 本次不实现、不冻结，也不作为收尾条件 |
| 粗分出口 MATERIAL_ARRIVED 与 target_decide | 原大计划提出的业务扩展 | 本次不修改插件、SDK 或 WMS 合同，也不作为收尾条件 |

**本轮结论：** WMS 明确可安全重试的既有可靠义务不再因 WES 内部 deadline 转入人工恢复；对端恢复后由现有 worker 继续原身份原正文。S1/S2/S3 已收敛为不改变供应商 wire、业务插件或 WMS 新合同的 WES 基础切片：Evidence 摘要身份、按 Evidence 幂等的 MaterialExecution、正式业务命令内部排队、Status 动态延后及本地观察留存。

**继续执行后的实现：** `WmsConfirmationService._dispatch_claimed` 不再把已过内部 deadline 的可安全重试义务直接改为 `RECONCILING`；`RETRY / NOT_SENT / DELIVERY_UNKNOWN` 始终保留原 `operation_id`、正文和退避时间。确定响应、响应内容冲突、owner 失效及无法分类的结果仍进入 `RECONCILING`，因此没有放宽业务事实或用健康检查冒充结果。自动联调原请求由现有 Celery dispatcher 和提交后唤醒自动续接，不新增恢复枚举、人工按钮或第二套 worker。

#### 后续业务合同（不属于本次收尾）

1. **WMS 原请求长期恢复（本次已闭合）：** 既有合同已规定响应未知或 `UNAVAILABLE` 使用原 `operation_id` 和原正文重试；deadline 只作为内部观测窗口，不刷新 identity、payload 或 deadline。
2. **WMS 错误结果纠正：** 未来按实际 operation 单独立项。
3. **WMS 观察反馈与粗分出口：** 未来由业务插件和 WMS 所有者单独立项。

第 2、3 项不进入当前代码、测试、Review、发布或验收 manifest；其未闭合不影响本次基础切片完成。

每个行为切片：先更新所属失败验收 → 运行聚焦测试确认失败类别 → 修改生产实现及全调用点 → 运行同一领域测试转绿。纯合同文字不做 RED；机器合同与运行时行为一起验证。最终仅一次主 Review/闭环和必需门禁，不按每个任务重复完整 QUALITY。

### S0 — 固定实施基线与机器合同

输入为本文与顶层设计；输出为精确变更 manifest、目标 DTO/响应表及测试所有权。

- [x] 重新记录 HEAD、dirty/staged/untracked 指纹；后端与前端使用同名隔离 worktree，保留并隔离主工作区原有修改，不复用其他 worktree 的本地状态。
- [x] 固定生产符号、直接/间接调用点、fixture、HEAVY mapping、迁移和生成物；初始降级搜索与最终 staged GitNexus 变更检查均已记录。
- [x] 核对设备白皮书、粗分设备附录及 DTO/OpenAPI：保持原 Event/Command/Status wire，不新增字段、错误码或 WMS operation。
- [x] 梳理旧 `DeviceEventCommandBlock` 与手工重评接口的实际用途；它们仍承接 EVENT_DEBUG/MANUAL_DEBUG 技术诊断，不参与正式业务准入，因此本次保留且不作为旧路径退出项。
- [x] R3 已闭合为正式业务排队与调试安全槽分离；R1 已移为后续独立业务需求，不是本次机器合同。
- [x] 已将 `workline_integration_debug/service.py` 的命令创建、结果读取、point2/point3 推进及所属测试纳入 manifest；共享入口无需机械签名迁移，Run/Step 业务改造继续由联调台方案拥有。

**验收：** 当前对外 ECS wire 无变化；新增字段仅属于 WES 诊断合同且有唯一生产定义和测试 owner，无“缺字段仍接受”的兼容路径。

### S1 — Event Evidence 与独立本站上下文

**修改：** `src/app/execution/models/material_execution.py`、对应 Repository、`services/material_execution_service.py` 及随机 revision 迁移。Event wire、Evidence 摘要算法和业务插件保持不变。

- [x] 复核 `tests/contracts/device/test_uniform_ecs_wire.py`、`tests/api/test_device_ecs_callbacks.py` 和 Evidence 测试，确认原 Event wire、摘要身份及 ACK 时序不变。
- [x] 不新增直接替代关系；完全相同报文重传复用现有 Evidence，不同规范化包络形成独立 Evidence。
- [x] 修改 `create_or_get_for_initial_evidence`：按原 Evidence 幂等，取消活动 trace 唯一作为本站入口限制；独立新请求不复用旧 RECONCILING 上下文。
- [x] 更新 `tests/runtime/execution/test_material_execution.py`、`test_fact_processor.py` 及对应 PostgreSQL 测试，证明同物料不同请求可独立存在、同请求竞争只生成一个上下文。

**输出：** 基础能力可按初始 Evidence 建立独立上下文，不需要物料全程状态正常，也不依赖供应商新增字段；业务插件是否采用该能力另行实施。

#### S1 执行记录

已在实施 worktree 完成按初始 Evidence 唯一的本站上下文及数据库迁移：同一 Evidence 并发幂等，同物料不同 Evidence 独立存在。
本次不迁移粗分或其他业务插件消费者；未修改供应商 Event/Command wire，也未建立推测性替代关系。

先前包含供应商字段的验证快照已失效，不作为当前证据；S2 完成后的当前快照证据见下节。实现已纳入后端提交 `51533683`；后端未 Push，未 Deploy，也未取得供应商现场验收。

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
- GitNexus 最终 staged 变更检查覆盖 44 个文件、203 个符号，affected processes 0、risk low；并以精确调用点、测试所有权、HEAVY mapping 和最终 diff 交叉闭合。
- 后端当前结论为 `IMPLEMENTED - VERIFIED - COMMITTED LOCALLY`，提交 `51533683`；未 Push、Merge、Deploy，也未完成供应商/WMS/现场业务验收。

### S3 — 本地命令观察和既有可靠义务续送

**修改：** 命令超时/拒绝路径、InboundEvidence 内部 kind/约束、历史/SSE 投影，以及 WmsConfirmation 既有义务的安全重试。无 SDK、业务插件或新 WMS operation。

- [x] 完善 `tests/runtime/device_command/test_reconciliation_service.py`：未发送到期/确定拒绝记录 `NOT_ACCEPTED`，ACK/交付未知记录 `RESULT_UNKNOWN`；状态推进与观察同事务，重复身份幂等且无结果不伪造 `DEVICE_RESULT`。
- [x] `DEVICE_OBSERVATION` 只作为 WES 内部 Evidence 稳定幂等留存，并由历史与 SSE 在事务提交后展示；不进入插件处理队列，不创建 WMS 业务义务。
- [x] 对既有 WMS operation 的 `RETRY / NOT_SENT / DELIVERY_UNKNOWN` 取消内部 deadline 人工门禁；跨窗口重复派发仍使用同一 identity/payload，真实 worker 的 `return_batch` 生产 wiring 同步验证。

**输出：** 基础能力可靠留存本地观察并继续既有可安全重试义务；不依赖业务消费者，也不伪造 WMS operation。

### S4 — 粗分业务按本站请求执行（后续独立需求）

本次不修改 `workline_plugins/rough_sorter/`、SDK/domain DTO、WMS request 或插件测试。出口独立决策、观察业务反馈、迟到结果业务解释及相关 integration/e2e 验收，在业务需求和 WMS 合同批准后另行实施；它们不是本计划当前交付的缺口。

### S5 — 历史与当前状态展示

**后端：** `src/app/device/services/device_ingress_history_service.py`、共享响应 DTO 与 SSE 更新；**前端：** `src/views/ops/device-diagnostics/DeviceEvidenceTable.vue`、`DeviceDiagnosticsPage.vue`、`useDeviceEvidenceStream.ts` 及 API 生成合同。

- [x] 后端历史快照和 SSE 使用同一字段语义；`DEVICE_OBSERVATION` 保留原命令事实，不把 APPLIED 当物理成功。
- [x] 前端展示原状态与当前请求，不凭历史未知显示当前设备故障。
- [x] 更新前端 `tests/unit/api/deviceEvidenceStream.test.ts`、`DeviceEvidenceTable.test.ts`、`DeviceDiagnosticsPage.test.ts`、`useDeviceEvidenceStream.test.ts`；覆盖严格观察事件、先实时后快照、加载失败、空记录、原结果迟到、查看详情及重复操作。
- [x] 在本地真实 `/ops/device-diagnostics` 页面完成浏览器 QA；登录、导航、过滤和控制台均正常，并以文字区分 WES 本地观察、Evidence 应用和物理结果。当前本地历史无真实观察记录，具体有值行语义由组件测试覆盖。

现有 blocker/reconcile/reprocess 与联调台 Run/Step 属于既有调试或业务能力，本次不修改、不退役，也不新增 WMS 业务对账按钮。

**输出：** 历史未知不会被展示成当前设备持续故障；旧记录仍可追踪。

#### S5 前后端执行记录

后端历史查询已纳入 `DEVICE_OBSERVATION`，并复用同一 `DeviceEvidenceUpdate` 投影向历史响应和专用 SSE 输出
`observation`、`reason_code`、`observed_at`。命令派发和到期对账在状态与 Evidence 事务提交后才做 best-effort 发布；
回滚不发布，Redis 通知失败不影响已提交事实，页面仍可刷新历史恢复。`DeviceIngressKind` 及供应商 callback DTO、路径、ACK、
Command/Event/Result wire 均保持原样；新增 `DeviceEvidenceKind` 只属于 WES 诊断合同。

前端隔离 worktree 已按后端提交 `51533683857d3729f01c91d02fe2b1ac5ae7109b` 正式执行 canonical contract freeze，
`.contract-sync-record.json` 记录 OpenAPI SHA-256 `e721eb07bcce2b646a5e7acfacdac38a1a7a29c70fc9d68225f8f507d3e40860`。
提交 `5f64da5` 更新生成类型、SSE 严格解析、历史合并与诊断展示；4 个聚焦测试文件 `41 passed`，全量 `113` 个测试文件
`844 passed`，lint、typecheck、build、合同和权限检查通过。PR #121 为 OPEN/CLEAN，代码检查与测试、构建两项 CI 均成功；尚未合并或部署。

### S6 — 整体验证、移交与旧路径退出

- [x] 闭合当前实现切片的实际测试消费者与精确 HEAVY mapping；新增 kind/字段/索引、迁移及前端生成物全部纳入影响清单。尚未批准的域 operation 不计作已实施。
- [x] 使用独占临时 PostgreSQL 验证迁移与并发；真实 worker、WMS operation wiring、Redis 及 selector 实际选择的恢复场景均纳入 `133 passed` 的 HEAVY 证据。
- [x] 一次主 Review 修复闭环后，后端 QUALITY、selector 选中 HEAVY及前端合同/测试/构建/QA 均通过当前实现快照；插件变更已撤回并以聚焦启动测试复核。
- [x] 扫描当前范围的旧摘要身份、设备生命周期槽和全程 trace 准入；未新增或替换 SDK/WMS 导出。既有 blocker/API、业务插件、纯输送和 Transport 行为明确保持原样。
- [x] 当前 spec/plan 及两处文档索引已同步；SRS、WMS 合同、供应商白皮书、设备附录及 `docs/hardware/` 均不修改。
- [ ] Merge/Deploy 后按原 ECS 合同验证临时未接纳重试、未知不重发、原结果迟到和新请求不被旧异常阻塞；这属于技术部署/现场验收，不是业务插件或 WMS 合同验收。

## 测试覆盖与失败路径

以下仅覆盖本次 WES 基础与诊断范围；已执行证据以上述各切片记录为准。禁止靠增加文档测试“证明实现”。

```text
事件 -> 严格解析/身份去重 [现有 wire/API 测试需扩充]
     -> 同身份漂移拒绝；新身份同物料允许
     -> 原事件唯一上下文 [现有 MaterialExecution 测试改合同]
命令 -> 领取竞争 -> 一个 token 有效 [PostgreSQL]
     -> ACK / 临时未接纳 / 明确拒绝 / 交付未知 [现有 Adapter/dispatch 扩充]
结果 -> 原身份校验 -> 同结果幂等 / 冲突拒绝 / 迟到闭合
     -> 新旧结果分别保存，不互相改写 [结果乱序仍按各自身份闭合]
观察 -> 与命令状态同事务留证 -> 历史/SSE 提交后可见 [基础服务 + API]
既有 WMS 义务 -> RETRY / NOT_SENT / DELIVERY_UNKNOWN 原身份续送 [dispatcher + worker]
UI -> 历史/当前分开 -> SSE/快照乱序、断线、空记录、原结果迟到 [前端 + QA]
```

| 故障 | 必须观察到的救济 | 主要测试所有者 |
| --- | --- | --- |
| 同请求重传/新请求同料 | 原请求无重复动作，新请求不被旧上下文挡住 | wire、执行关联、真实并发 |
| 旧命令迟到 | 未发送命令按原 deadline 排队；已 ACK 或交付未知不重发 | 基础派发、供应商边界分别验证 |
| 新成功先于旧结果 | 原未知保持原样；原结果迟到正常应用且不影响新任务 | Evidence/命令事务 |
| 最后一件异常、没有后续事件 | 到期形成本地观察，不等待下一件 | 超时服务、历史/SSE |
| 数据提交后 SSE 发布失败 | 已提交观察仍可通过历史查询恢复 | 基础持久化与 API |
| 既有 WMS 请求响应丢失 | 已证明可安全重试的义务使用同身份、同正文继续 | confirmation dispatcher 与真实 worker |
| 同物料新 Evidence 到达 | 形成独立上下文；不推测替代或覆盖旧请求 | Evidence/执行关联 |
| 共享资源确实被未知 Transport 占用 | 仍按原 Transport 合同阻断；本次不修改该行为 | Transport 原测试 |
| ECS 提供原身份有效结果 | WES 沿既有入口接收并闭合原命令；不换身份重发 | 基础可靠机制及供应商边界 |
| 手工旧命令延迟到新对象到达后 | 调试安全槽继续拒绝同设备混发，不阻断正式业务请求持久化与排队 | 基础准入与供应商边界 |

重点回归：供应商事件身份和 wire 不变；普通事件、结果、手工调试不能被本站基础请求约束错误拒绝。实现不得重放已 ACK 的动作，也不得丢弃合法迟到结果。

## 性能、发布与执行顺序

事件幂等继续使用现有规范化摘要；初始 Evidence 唯一索引保证上下文幂等，不增加替代关系查询。历史分页及 worker 批量遵循既有请求/任务预算，数据库事务内不进行外部 HTTP。动态 Status 阻塞只更新原命令的 `next_attempt_at`，保留 deadline，不能改写为永久失败或重发已接纳动作。延迟与吞吐以实施环境实测，不承诺未经测量的 p99。

本次按 S0 → S1 → S2 → S3 → S5 → S6 执行；S4 及第 6–7 节业务扩展不参与当前顺序。后端和前端分别在同名隔离 worktree 实施；当前授权已执行到后端本地 Commit 与前端 Push/PR，未经后续授权不合并、部署或清理。

发布前核清真实在途事实，停止旧相关进程，迁移并部署匹配的新核心与前端，再启动并验证数据库路径和真实 worker。业务插件不在本次发布差异中。失败时先停止新触发并保留事实；仅在数据库和原外部合同允许时恢复匹配旧版本，否则修复前进，禁止盲启旧版。未发布不要求兼容双跑；本轮未授权执行以上发布动作。

## GSTACK REVIEW REPORT

| Review | Runs | Status | Findings |
| --- | --- | --- | --- |
| CEO | 1 | CLEAR（顶层） | D1–D7 已确认，范围保持 |
| 工程 | 3 | CURRENT SCOPE COMPLETE | S1、S2、S3 本地观察/既有安全重试及 S5 前后端已实施并通过当前快照门禁与固定 diff Review；业务/WMS 扩展已移出本次范围 |
| Outside Voice | 0 | SKIPPED | Codex 宿主按技能跳过嵌套同系统评审，无跨模型结论 |
| 界面 | 1 | FOCUSED QA PASS | S5 诊断页聚焦测试、全量测试、构建、CI 与本地浏览器 QA 已通过；未做部署环境验收 |

**VERDICT:** 本次 WES 基础与诊断实施范围已完成，业务插件与新 WMS 合同不属于当前收尾条件。后端尚未 Push，前端 PR 尚未合并，均未 Deploy；部署和现场验证仍与代码完成分层记录。

**后续独立业务需求（非当前 blocker）：**

- R1：具体 operation 的纠正/重新求值资格与旧义务权威闭合方式，须有双方合同依据。
- S3：`inbound.execution.observation_report@v1` 的严格 DTO、响应与 WMS 接收保证尚未批准。
- S4：粗分出口 `target_decide` 的本站输入与 WMS 决策合同尚未冻结。
