# `outbound.picking_task.plan_delta@v1` 计划增量设计

> 2026-09-06 料箱流程目标修订：料箱业务编码、全程 BinExecution、LineRunEpoch 和 NG 出口上报的后续实施按
> [料箱编码与扫码驱动流程 SPEC](2026-09-06-bin-code-and-station-driven-flow-design.md) 收敛。
> 本文既有完成记录仅描述当时实现，不作为继续保留 NG 出口 operation、BinExecution 或 LineRunEpoch 的依据；其余 Operation 任务仍有效。

> 2026-09-06 执行目标修订（用户明确授权）：先将可运行 Operation 发布到联调服务器，供 WMS 调用并据此收敛合同。
> 本文以下 T1-A 的“书面确认后方可编码/发布”门禁由此取代：T1-A 转为联合验收项，T1-B–T5、R4 和 R2-B1 可按本设计实现并验证后联调发布。
> PENDING/503 原身份重试及修正 Evidence 受控应用后的 DUPLICATE 作为本次可执行合同；不宣称 WMS 已确认。
> 数据迁移、事务/幂等、权限、真实 worker 验证和物理围栏仍有效；基础 Operation 发布不自动开启具体插件业务或设备动作。

> 2026-09-12 执行目标修订：原 R4 管理员 `apply-correction` 路径已被
> [WES 无阻塞执行设计](2026-09-11-wes-nonblocking-execution-design.md) ENG-D5 取代并从代码删除。合法修正只允许 WMS 使用新 identity
> 和严格下一 revision 经普通 `plan_delta` record/replay 自动校验、应用；本文其他 Operation 与 prepare 边界继续有效。
> 本文后续所有要求 `plan_blocked_evidence_id` 阻断新动作、将修正先固定为 `RECONCILING`、由管理员对账应用、检查 blocker 后完成/释放，
> 或把 R4/IT2/G27–G30 作为人工续行门禁的段落，均仅为被取代的历史评审记录，不再具有规范性，禁止据此恢复已删除路径。


status: Approved（2026-09-06 基于当前 develop 的工程复审与独立外部复核通过；非 WMS 联合合同批准、代码实施或生产激活）
created_at: 2026-09-04
updated_at: 2026-09-06
scope: Phase 12 WMS Operation 修复总计划；包含共享 typed Operation 基础能力、公共 ingress 收敛、plan_delta 持久化与自动 record/replay、prepare 插件 Policy、Operation 生产能力与插件消费分离激活，以及既有 generic WMS 调用迁移

## 1. 目标

WES 在已经可靠接收 PickingTask、冻结匹配的 WorkLine，并取得 WMS 明确
`PREPARE_ACCEPTED` 后，严格校验并原子应用 `outbound.picking_task.plan_delta@v1`。若匹配的 prepare 尚未超期且结果仍未确定，
允许先暂存回调证据，但不接纳该 revision、不推进任务、不触发执行。

本切片完成计划身份、连续版本、初始接料货架面和新增来源的持久化。首批计划应用成功后，PickingTask 从
`PREPARING` 进入 `EXECUTING`。它不创建 `TransportTask`、`BinExecution`、`DeviceCommand`，也不打开生产入口。

首轮联调由 WMS 配置约束为一个接料货架面、一个五层来源货架面和后续单箱数据，不发送直接取料明细。
这是测试数据约束，不是正式 wire 限制。

### 1.1 WMS Operation 复审发现与修复闭环

本计划同时承接 2026-09-05 两轮当前基线复审的 P1/P2。所有发现都必须进入任务与验证清单，不能只作为评审备注：

| 发现 | 修复任务 | 完成证据 | 对 plan_delta 的关系 |
| --- | --- | --- | --- |
| P1：`AGENTS.md` 将 WMS→WES 业务一律写成接收提交后异步应用，与 issued/plan_delta 的合同原子提交边界冲突 | R1 两类 ACK 提交模式 | 法规定义“业务事实构成接收成功”和“Evidence 接收后异步应用”两类模式；每个 operation 合同显式归类 | 所有生产代码前置门禁 |
| P1：issued 重复解析 raw body，且可识别的非法 data 在 Evidence 前返回 422 | T0-A 公共信封结构收敛 + T0-B issued 拒绝留证 | T0-A 保持现有可观察行为；T0-B 明确旧断言到新断言并验证拒绝 Evidence | plan_delta typed handler 前置 |
| P1：prepare 只有暗构建，且宿主硬编码人工插件、flow mode 和任务领取 | R2 prepare 所有权收敛 | 宿主保留可靠事务 Coordinator、锁、PickingTask/WmsConfirmation 和提交后唤醒；插件只提供无副作用 typed Policy | 所有权收敛不阻塞 plan_delta 暗构建；插件消费激活前必须完成 |
| P1：Operation 生产能力与 `manual_bin_processing` 原子绑定，无法支持零/一/多插件消费者 | R2-B1/R2-B2 分离激活 | typed Operation 能力独立装配；插件缺席只阻止新业务触发，不切断既有可靠义务或迟到回调 | 替换原联合激活模型 |
| P1：插件通过 `CreateWmsConfirmation(operation, dict)` 和 `WmsInboundAdapter.dispatch(operation, dict)` 暴露 generic escape hatch | R3 typed Operation 全生命周期迁移 | 单一 `wms_operations` facade 暴露固定 typed methods；request intent 与 response outcome 全程 typed；generic 公开入口和字符串分派清零 | prepare 插件 Policy 和所有既有 WES→WMS operation 的基础门禁 |
| P1：`plan_blocked_evidence_id` 可永久阻塞任务，但生产激活没有受控解除路径 | R4 计划冲突对账 | 匹配证据、管理授权和任务锁内完成获批动作；无对账能力不得生产激活 | 不阻塞暗构建，阻塞 Operation 生产激活 |
| P2：generic Adapter 测试同时承载共享可靠性行为，缺少精确承接与清理顺序 | R3 测试 owner 迁移 | 每项测试标记 `MIGRATE / KEEP / DELETE`；typed 承接先绿，再删过期 generic 断言 | 防止误删共享 WmsConfirmation 可靠性覆盖 |

执行依赖固定为四条链：

- plan_delta 暗构建：`R1 → T0-A → T0-B → T1-A → T1-B → T2 → T3 → T4 → T5`；
- typed Operation 基础能力：`R1 → R3-A → R3-B`；
- prepare 所有权：`R1 → R2-0 → R2-A`，其中 R2-A 依赖 R3-A 的 typed prepare intent；
- 生产激活：`T3 → R4`，随后 `T5 + R3-B + R4 + WMS 联合 fixture → R2-B1`，再由 `R2-A + R2-B1 → R2-B2`。

T1-A 是不可绕过的 WMS 联合合同门禁，未通过时不得开始 T1-B 或任何模型、migration、Service 实施。R1 完成后，不重叠的
plan_delta、typed Operation 和 prepare Policy 切片可以按第 13.1 节同步推进；R2-B1 只激活基础 Operation，R2-B2 才启用
`manual_bin_processing` 的新业务触发。R2-0 与 R2-A 已完成；当前 Coordinator/Policy 边界与实施状态见第 17 节。
每条链在首个写操作前分别冻结文件与测试 owner；出现交叉文件时，
由 owner 清单指定唯一写入方并串行合入，禁止两个切片同时改同一文件或共享执行路径。
`src/app/wms_integration/outbound_picking/services/picking_task_confirmation_owner.py` 是跨链只读不变量：R2-A 不修改
`validate_prepare_response_owner(db, picking_task_id, operation) -> bool` 的签名、confirmation→PickingTask 锁语义或其依赖的
`WmsConfirmation` 响应证据形状。共享文件预先串行化：plan_delta 链先拥有 `models/__init__.py`、`repositories/__init__.py`、
`services/__init__.py` 以及 `heavy-test-impact.toml` 中 `wms_adapter/outbound_picking/**`、`wms_integration/outbound_picking/**` 两行并完成 T5。
R2-A 可同步修改独占文件，但不得同时修改这些共享行；T5 后 R2-A 必须基于该快照 rebase，再串行应用自身必要的导出和 mapping 变化，
并刷新被触及的 owner 测试。本次 T1-A 外部阻塞期间按第 17 节将空闲共享路径串行移交 R2-A；后续 T5 基于该快照接续。R3 是 P1 基础能力迁移，不得与 plan_delta Service 共用写 owner；R1 先完成法规和全部合同的 ACK 模式标注，
R3 再基于 R1 快照修改两个 Inbound 合同，禁止并行覆盖分类字段。

## 2. 合同边界

- WMS Operation 是与插件消费者解耦的基础能力。一个 operation 可以没有插件消费者、被一个插件使用，或被多个插件复用；一个插件及其
  任一能力节点也可以组合多个 operation。Operation 不保存消费者数量，不通过插件安装状态动态注册或注销合同。
- WES→WMS 的插件入口统一为一个无副作用 `wms_operations` facade，公开 API 只提供固定 typed method，例如
  `wms_operations.outbound_picking_task_prepare(...)`；调用方不能传入任意 operation 字符串或裸 `dict`。方法创建不可变 typed intent，
  不直接访问 HTTP、数据库或队列。
- SDK 可以承载稳定的 typed WMS Operation intent/outcome 和纯 facade 合同；OpenAPI/wire DTO、Adapter、Repository、事务、
  `WmsConfirmation`、领取/重试/恢复和 HTTP Transport 仍归宿主。内核可以使用私有通用 envelope 持久化，但不得把 generic 构造入口重新导出给插件。
- WES→WMS 返回由 typed Adapter 解析成封闭结果，宿主可靠保存后转换为 typed outcome 再交给冻结业务上下文对应的插件；插件不解析原始
  JSON、不按 operation 字符串分派。插件版本暂不可用时保留证据与资源围栏并进入对账，不能丢弃、猜测默认消费者或换 identity 重发。
- operation 固定为 `outbound.picking_task.plan_delta@v1`，不增加人工专用别名或兼容字段。
- 正式 DTO 保留出库合同定义的完整能力：一个初始 `target_rack`，以及条件可选的 `added_bin_source_racks`、
  `added_direct_picks`；任一数组出现时必须非空。revision 1 可以只有接料货架面，后续版本至少新增一种来源。
- `plan_revision` 在同一 `task_id` 下从 `1` 连续递增。WMS 必须在前一版本得到明确成功响应后再发送下一版本。
- revision 1 必须且只能携带一个 `target_rack`；revision 2 及以后禁止携带 `target_rack`，并必须新增至少一个来源。
- `plan_delta` 不选择具体 `bin_id`。具体 Bin 由后续 `outbound.bin.inbound_batch@v1` 冻结。
- 计划持久化对 `MANUAL | AUTO` 保持中立；`task_type` 只参与前序任务领取与 WorkLine 准入，不分叉同一个
  operation 的 revision、幂等或来源模型。
- `plan_delta` 只读取 PickingTask 已冻结的 WorkLine/prepare 关联，不读取当前插件、不按 plugin key 选择 owner，也不复制
  `manual_bin_processing`、flow mode 或人工任务领取判断。prepare 的业务准入与任务领取由对应插件拥有，共享宿主只提供
  PickingTask、WmsConfirmation、事务、锁和严格 operation 合同。
- WMS 拥有任务、版本和资源计划；WES 拥有本地持久化、顺序校验和执行准入；ECS/PLC 不参与本 operation。

出库主合同整体仍为 `ReviewRequired`。本设计获批冻结本切片的 WES 实施选择；但主合同当前同时规定“已保存、业务条件暂不满足”不得返回
`503`，与本设计的 prepare 未定时 `PENDING + 503 + 原 operation ID 重试` 存在冲突。T1-A 必须先与 WMS 联合选择并书面固化唯一语义、
同步主合同和联合 fixture；未完成时本计划停在合同门禁，不得开始 wire、模型、migration 或 Service 实施。若 WMS 不接受当前暂存语义，
先重设计状态机并重新评审，不能由 WES 单方面改合同或留到联调时处理。

## 3. 数据所有权

### 3.1 PickingTask

在现有统一 `PickingTask` 上保存：

- `last_applied_plan_revision`：尚未应用计划时为 `0`，成功应用后单调递增；
- 初始 `target_rack_id + target_rack_face`：只由 revision 1 写入，后续不可修改；
- `initial_plan_evidence_id`：不可变地指向写入初始接料货架面的 revision 1 `InboundEvidence`；
- `last_plan_evidence_id`：指向当前已应用 revision 的首次原始 `InboundEvidence`，用于同版本重放比较和审计；业务重复
  Evidence 不移动该指针。
- `plan_blocked_evidence_id`：可空外键，指向首次造成该任务计划阻塞的 `InboundEvidence`；非空表示计划准入被阻塞，
  不替代任务当前执行阶段，不新增任务状态或独立阻塞实体。

状态与计划保持一致：

- `QUEUED` 不允许应用计划；
- `PREPARING` 只有在匹配的 prepare confirmation 已持久化明确 `PREPARE_ACCEPTED` 后才能应用 revision 1；
  同任务及冻结 WorkLine 的 prepare 尚未超期、结果仍未确定时，按第 5 节暂存并等待原请求重试；
- 首个 revision 原子应用后进入 `EXECUTING`；
- `EXECUTING` 只接受严格的下一 revision；
- `EXECUTION_COMPLETED` 不接受新的计划；迟到 plan_delta 留 `RECONCILING` Evidence 并返回 `STATE_CONFLICT`，但不设置
  `plan_blocked_evidence_id`，终态本身已经禁止新 revision。

任务的阶段与阻塞是两个不同事实。`plan_blocked_evidence_id` 非空时，不应用新的 revision、不发起新的执行动作、不完成任务或释放
既有占用；仍允许保存已有动作的权威结果及对账证据，避免阻塞结果闭合。已成功请求的原样重放仍可返回 `DUPLICATE`，但不得触发动作。

### 3.2 计划成员

- 每个 `added_direct_picks` 建立一条 `DirectPickExecution`，业务身份为
  `picking_task_id + source_locator`；初始记录只表达计划来源，不触发设备动作。
- 每个 `added_bin_source_racks` 建立一条 PickingTask 来源货架面记录，业务身份为
  `picking_task_id + rack_id + rack_face`；它不预选 Bin。
- 每条新增成员保存首次引入它的 `plan_revision` 和 `source_evidence_id`，已接收成员不可被更高 revision 改写或重新创建。
- 本切片不建立第二套 inbox、outbox、计划 JSON 副本或通用工作流实体；完整请求的 JSON 语义载荷继续由 `InboundEvidence` 保存。
  不承诺保留原始 HTTP 字节、空白和对象键顺序；这些不是现有 Evidence 存储能力。

### 3.3 Evidence 与物料处理隔离

计划证据使用 `WMS_EVENT`，`workline_id`、`material_execution_id`、`transport_task_id` 均保持为空。
任务与来源通过上述 Evidence 外键追溯，WorkLine 由 PickingTask 冻结绑定提供；不得为方便查询向全局计划 Evidence 填工作线关联，
也不得伪造 `published_at` 或 Decision 摘要来避开扫描器。`PENDING` 仅由 WMS 原请求重试重新处理，不唤醒设备或物料处理器。
这复用已批准的 prepare 隔离方式，不给共享 Evidence 增加 PickingTask 外键、不改共享扫描规则。

### 3.4 持久化约束

- PickingTask 的 revision 为非负 int64；revision 为 0 时初始目标、initial/last 证据指针全空，revision 大于 0 时全非空。
  保留已有任务阶段、WorkLine 唯一活动任务和绑定约束；不从 revision 倒推任何物理完成。
- `DirectPickExecution` 持久化精确 `rack_id + rack_face + slot_id` 和任务、revision、原始证据；
  `PickingTaskBinSourceRack` 持久化任务、`rack_id + rack_face`、revision、原始证据。各自建立第 3.2 节的唯一约束与外键。
  本切片只需要计划成员身份，不预埋未使用的执行状态、调度字段或第二份 locator JSON。
- 业务标识和 rack/face/slot 按合同原值精确保存，复用适用的现有标识校验；禁止截断、trim、大小写折叠或从字符串猜测位置。
  wire、数据库长度和合法值必须一致，不将来源面限制成现场示例中的 A/B。
- task、Evidence 外键不级联删除审计事实；新增模型进入现有 metadata、导出和 Alembic 发现链。
  revision 与证据的跨行一致性由 Service 的任务锁内校验和 PostgreSQL 集成测试保证，不声称普通 CHECK 能校验其他表。

## 4. 接收与应用流程

```text
WMS plan_delta
  -> [后续激活] 唯一 Event route：鉴权、有界读取、严格 JSON 与公共信封在 issued/plan_delta 路径只解析一次、静态分派；不访问 DB
       -> 真正未知 operation：无状态 422，仅发布脱敏 ingress telemetry，不进入 Evidence/幂等流程
  -> plan_delta handler：接收已校验的公共信封，保留信封并产出 typed data 或 validation error；两种结果都交给 operation recorder/service
  -> Service：开始一个事务
       -> EvidenceService：锁 operation identity、保存/核对完整载荷
       -> identity 可识别但 data 非法：WMS_EVENT + IGNORED 提交完整规范化请求，返回 422
       -> 已应用的原样重放：DUPLICATE（不再应用）
       -> 锁定可信 PickingTask（未知任务仅留证拒绝）
       -> 检查阻塞、阶段、冻结绑定、prepare 和 revision
            |-- 匹配且 prepare 未定、未超期：PENDING，无计划写入
            |-- 确定冲突：RECONCILING + 首次任务阻塞，无计划写入
            `-- 允许应用：校验全部成员 -> 批量写入 -> 更新目标/版本/证据
                         -> revision 1: PREPARING -> EXECUTING
                         -> Evidence: APPLIED
       -> 提交
  -> handler：按持久化结果返回 ACK；提交失败返回 503
```

公共信封、正文上限、严格 JSON、operation identity 和静态分派属于共享入口，不由 `plan_delta` 重复实现。Event ingress 的 256 KiB
上限只由 `wire_common.MAX_WMS_EVENT_BODY_BYTES` 拥有，`v1/events.py`、issued 与 transport handler 直接引用它；删除 transport handler 的
重复数值常量，但不改变 transport 的解析、ACK 或业务行为。Inbound 与 WES→WMS Adapter 的独立上限属于其它合同，不在本计划合并。
本计划的“只解析一次”验收范围
明确限于 T0-A 迁移后的 `issued` 和 T1-B 新增的 `plan_delta`；Transport、recovery 与既有 Inbound 仍按当前 raw-body 路径运行，后续由各自
独立迁移计划收敛，不得为满足全局措辞扩张本切片。若当前代码没有可直接传递的
公共信封结果，前置任务只从现有 route/`wire_common.py` 提取无状态 helper，并让现有 `issued` 同步复用；不得新增动态 registry、通用
operation Runtime、默认 handler、数据库访问型 API 层或第二个接收入口。公共 identity 尚不可识别时返回既有 400/413；只有静态注册且
identity 可识别的 operation 才进入可靠 Evidence 边界，包括 data DTO 非法在内的首次接收或拒绝必须通过共享 Evidence 能力留证。真正未知的
operation 保持无状态 `422 / UNSUPPORTED_OPERATION`，只发布现有脱敏 ingress telemetry；不得把任意 operation 字符串写入数据库或为其建立
幂等状态。首次非法 data 保存为 `WMS_EVENT + IGNORED`，不进入
`RECONCILING` 或运行就绪阻塞；原 operation ID 与完全相同正文重放时保持首次 `422 / INVALID_DATA` 和首次 Evidence timestamp，修改正文返回
`409 / IDEMPOTENCY_CONFLICT`。WMS 修正 data 后必须使用新的 operation ID。

typed handler 不得在 data DTO 失败时本地短路 422。parser 必须保留公共信封并返回 typed data 或 validation error；handler 将两种结果
交给同一个 operation recorder/service。非法分支由该 Service 在事务内调用现有 `InboundEvidenceService.accept`，以 `IGNORED` 保存后再形成
422 结果；这只是 operation 薄编排，不新增拒绝 Service、数据库 API 层或通用 Runtime。T0-B 后的 issued 遵守同一调用形状。

事务内不调用 WMS、broker、Transport、ECS 或其它外部系统。任何一项校验失败时，整个 revision 不产生部分计划成员写入。
确定冲突的证据与阻塞指针属于诊断/控制事实，应一同提交；SQL/flush/commit 失败则整个事务回滚，不能在失效事务中补写成功或冲突 ACK。
构成 `RECEIVED` 的 Evidence、计划成员、任务状态和 revision 必须在同一事务提交；提交后才异步启动 Transport、DeviceCommand 等外部副作用。
不得把“业务异步应用”解释为 Evidence 单独提交后即返回 202。plan_delta handler 在本轮只由测试通过共享入口边界调用；图中的生产 route
仍不接入 plan_delta。

### 4.1 锁与 prepare 并发

统一锁顺序为 plan_delta Evidence identity → 可信任务行。同任务所有版本和阻塞设置由同一任务行锁串行化；不同任务互不使用全局锁。
锁内只以普通查询读取匹配的 prepare confirmation 及其响应证据，不再申请 confirmation 行锁：现有 dispatcher 的顺序是
confirmation → PickingTask，反向加锁会形成死锁。若并发响应尚未提交，只读到原来的未定状态并返回 503，允许下次重试。
只接受唯一匹配 task、prepare operation、冻结绑定且状态为 `COMPLETED`、响应为 `PREPARE_ACCEPTED` 的确认；零条、多条或证据不匹配
均不当作成功。成功确认不可被回调改写；超期门禁针对尚未成功确定的 prepare，不让后来重试把已持久化的有效成功改成超期。
生产激活时 WorkLine 停用必须与任务准入一致串行化，并保留未完成任务围栏；本轮不改该生产生命周期。

### 4.2 Operation 基础能力与插件消费

```text
插件纯 Decision 节点
  -> wms_operations.<fixed_typed_method>(typed fields)
  -> SDK typed intent（无 I/O）
  -> 宿主 DecisionApplier / Coordinator
       -> 同业务事务冻结 operation identity、规范化 payload、owner 与 WmsConfirmation
  -> 提交后共享 worker 领取
  -> operation typed Adapter 单次发送
  -> WMS 封闭响应
  -> 宿主可靠保存响应 Evidence
  -> typed outcome factory
  -> 按已冻结业务上下文交给匹配插件能力节点
```

`wms_operations` 只有一个插件可见 facade；每个方法固定一个 operation 的字面量、typed 输入和 typed outcome 联合。它不实现网络发送、
重试、幂等、并发领取或插件 registry。共享 `WmsConfirmationLifecycleService`、dispatcher 和 `WmsClient` 继续分别拥有这些机制。
同一插件节点可以创建多个不同 intent；同一 typed method 可以由多个插件调用。调用实例仍通过已冻结 owner/WorkLine/plugin version 关联其
业务后续，不按 operation 名称猜测消费者。

零消费者是合法部署状态：基础 Operation 的 DTO、Adapter、可靠 worker 和 WMS→WES 静态 route 可以存在，但不会凭空产生新业务请求。
插件消费能力缺失只禁止新的业务触发；已有 `WmsConfirmation`、已发出的请求和迟到回调继续由宿主接收并可靠保存。若冻结版本的插件
暂不可用，后续业务应用保持围栏并进入明确对账，不能注销 route、丢弃结果或路由给默认插件。

## 5. 幂等、冲突与失败

- 相同 `operation_id` 和相同完整正文重放：只有首次已成功应用的请求才返回 `DUPLICATE`，不重复创建成员；
  `PENDING` 必须重新检查前置条件；已确定的冲突不得由相同 identity 修改正文后改报成功。合法修正使用新 identity 和严格下一 revision，
  其原身份、原正文重放返回 `200 / DUPLICATE`，不得再次应用。
- 相同 `operation_id` 但正文变化：返回 `CONFLICT`，保留首次证据和业务结果。
- 当前 revision 使用新的 `operation_id`、但完整业务内容与已应用 revision 相同：保存本次 Evidence，返回
  `200 / DUPLICATE`，不重复应用；内容不同则进入 `RECONCILING`。
- revision 1 先于 prepare 响应落库到达，且任务、冻结 WorkLine 与 prepare 匹配、prepare 尚未超期且结果仍未确定：
  将 Evidence 持久化为 `PENDING`，返回 `503 / UNAVAILABLE + data={}`，不修改业务计划、revision 或任务状态。
  这是本 operation 对“证据已保存但尚未成功处理”的明确重试约定，不引入 `WAIT` 响应或新的通用状态。
- WMS 使用原 `operation_id`、原 timestamp 和原正文重试上述 `PENDING` 请求；WES 复用同一 Evidence 并重新检查前置条件。
  匹配的 `PREPARE_ACCEPTED` 已可靠保存时，才在同一事务应用计划并返回 `202 / RECEIVED`；仍未确定且未超期时继续返回 `503`。
  prepare 已超期、进入 `RECONCILING` 或出现确定冲突时，转入下述冲突分支。不得根据计划回调推定 prepare 已成功。
  不新增后台扫描器或唤醒机制；重试由 WMS 驱动，未重试的 `PENDING` 不会自动应用或解除既有任务占用。
- revision 跳号、倒退、同版本内容不同、重复来源身份、不符合上述暂存条件的未准备任务或任务已经结束：fail closed，保留 Evidence
  并进入 `RECONCILING`；不修改当前任务计划，不启动任何后继动作。
- 对可以可靠关联到现有任务的确定计划冲突，在持有任务锁的同一事务内保存冲突证据并首次设置 `plan_blocked_evidence_id`；
  后续冲突继续留证，不覆盖首次阻塞指针。相同 operation identity 的正文冲突复用 `InboundEvidenceConflict`，指针指向其
  `first_evidence_id`，不把原先已应用的 Evidence 改成未应用；任务归属以原始可信证据为准，不依据冲突正文改绑其他任务。
  无法可靠确定任务归属的消息只留证并拒绝，不创建任务或污染其他任务。已结束任务不因迟到消息重新打开或恢复资源占用。
  命中 `EXECUTION_COMPLETED` 的迟到消息只保存 `RECONCILING` Evidence 并返回 `409 / STATE_CONFLICT`；不得设置
  `plan_blocked_evidence_id`，避免把无控制作用的终态噪声计入活动阻塞与告警。
- `PENDING` 暂存本身不设置计划冲突指针；阻塞后的新请求即使 revision 正确，也不能绕过既有阻塞应用计划。
  不自动清空指针，不通过新 operation ID、改 revision 或改任务阶段解锁。T1-B–T5 暗构建不提供解除入口；R4 必须在生产激活前交付
  明确获批的对账动作、匹配证据、管理授权、审计记录及任务锁内校验，不仅凭 HTTP 成功或人工改状态。
- WMS 修正计划时，使用新的 `operation_id` 发送当前任务严格期望的下一 `plan_revision`。任务已阻塞时，WES 仍可靠保存该请求为
  `RECONCILING` Evidence，但不立即应用、不覆盖首次 `plan_blocked_evidence_id`。管理端随后调用固定的 outbound picking 对账入口，显式引用
  原始阻塞 Evidence、修正 Evidence、预期任务乐观锁版本和有界审计原因。
- 对账 Service 在 PickingTask 行锁内验证 blocker 仍为所引用的首次 Evidence、两份 Evidence 均属于同一任务与
  `outbound.picking_task.plan_delta@v1`、修正 Evidence 是当前严格期望的下一 revision，且当前阶段、冻结绑定和成员均无新冲突；随后在一个事务中
  应用修正计划、推进 `last_applied_plan_revision`/`last_plan_evidence_id`、清空 blocker 并记录审计。重复相同对账在修正 Evidence 已成为最后应用证据时
  返回既有成功结果；引用漂移、正文漂移、版本变化、终态或并发冲突均 fail closed。任务取消属于独立生命周期合同，不由 R4 提供。
- R4 成功事务同时将修正 Evidence 从 `RECONCILING` 标记为 `APPLIED`，保留其首次拒绝记录和独立对账审计，不改写原始阻塞
  Evidence 或冲突历史。此后 WMS 使用该修正请求的原 operation ID、原 timestamp 和原正文重试，优先按已应用事实返回
  `200 / DUPLICATE`，timestamp 沿用该修正 Evidence 的首次接收时间；任务后来推进 revision、再次阻塞或结束均不改变此成功重放。
  原身份改正文仍返回 `409 / IDEMPOTENCY_CONFLICT`，实际被拒绝的冲突正文不因另一份修正 Evidence 成功而变为成功。
  若 blocker 引用原本已 `APPLIED` 的首次 Evidence，该 Evidence 的原始成功正文仍返回 `DUPLICATE`，不能因被 blocker 引用而改报拒绝。
  对账尚未提交或已回滚时，修正请求仍返回首次 `409 / STATE_CONFLICT`。WMS 取得修正版本的明确成功 ACK 后才发布下一 revision。
  这项恢复例外与等待语义一并纳入 T1-A 联合合同和 fixture；当前仅为 WES 已批准实施选择，不代表 WMS 已确认。
- 数据库、事务或证据保存失败：不返回成功 ACK；调用方只能重试原 identity 和原正文。
- 未获得匹配的权威结果前，不通过修改 revision、operation ID 或任务状态绕过冲突。

### 5.1 精确重放与 ACK

- 同 operation ID 比较完整信封的规范化 JSON（包含 timestamp、operation 和 data）；复用 Evidence 的现有摘要能力。
  plan_delta 合法 DTO 固定使用 `model_dump(mode="json", exclude_none=True)`，缺省可选字段不注入显式 null；wire 中显式 null 仍属于
  `INVALID_DATA` 并按拒绝原始形态留证。对象键顺序/空白不参与比较，数组顺序、大小写和所有业务值参与比较；T1-B 不照抄 issued
  当前不带 `exclude_none` 的导出调用。
- 新 operation ID 的同当前 revision 业务重复，只比较完整 `data` 与 `last_plan_evidence_id` 指向的首次载荷。双方分别复用
  `normalize_payload(..., EXACT)` 在内存生成规范化结果和摘要并比较摘要；对象键序不影响结果，数组顺序保留并参与身份。
  不比较新信封的 operation ID/timestamp，不新增数据库摘要列、摘要副本、业务表、自定义排序或历史 revision 表。
  该重复 Evidence 记为 `APPLIED`，但 initial/last 和成员原始指针不移动。更早 revision 换新 ID 仍属于版本倒退。
- 先判断已成功的精确重放，再判断当前阶段/阻塞，避免任务已推进或结束后将历史成功改报冲突。
  尚未成功的 `PENDING` 不享有这一成功重放分支；新 ID 的业务重复不得解除现有阻塞。
- data DTO 非法属于确定且终结的 wire 拒绝：保存完整规范化信封并把 Evidence 标记为 `IGNORED`，原样重放重新执行同一确定性 DTO 校验，
  返回首次 Evidence timestamp 和 `INVALID_DATA`；不新增 `REJECTED` 状态、字段、响应缓存或自关联 conflict。相同 operation ID 修改正文仍由
  Evidence 摘要冲突返回 `IDEMPOTENCY_CONFLICT`，修正正文必须换新 operation ID。
- 除 R4 已原子应用的修正 Evidence 外，其它确定领域拒绝的重放必须维持原拒绝原因。修正 Evidence 的 `APPLIED` 判定优先于历史拒绝，
  但不优先于原 identity 的正文冲突。复用已有 `InboundEvidenceConflict.reason_code` 保存领域拒绝依据，
  由该 operation 的 Service 调用现有 `InboundEvidenceService.record_conflict` 写入；对首次状态/引用/版本冲突可关联同一 Evidence 的载荷与摘要，
  不新增响应缓存表，不让共享 EvidenceService 理解 PickingTask 业务。后续错误不能覆盖首次拒绝语义。

| 情况 | HTTP / code | data |
| --- | --- | --- |
| 成功应用并提交 | `202 / RECEIVED` | `{}` |
| 已成功请求或允许的业务重复 | `200 / DUPLICATE` | `{}` |
| 同 operation ID 改正文 | `409 / CONFLICT` | `reason_code=IDEMPOTENCY_CONFLICT` |
| revision 跳号/倒退/同版本异内容 | `409 / CONFLICT` | `reason_code=REVISION_CONFLICT` |
| 任务阻塞/阶段不允许/prepare 确定异常 | `409 / CONFLICT` | `reason_code=STATE_CONFLICT` |
| 未知任务/重复来源身份/无效关联 | `409 / CONFLICT` | `reason_code=REFERENCE_CONFLICT` |
| 可关联的非法 plan_delta data | `422 / REJECTED` | `reason_code=INVALID_DATA`；`WMS_EVENT + IGNORED` 拒绝 Evidence 已提交；原样重放保持首次 timestamp，修正请求换新 operation ID |
| 未知 operation | `422 / REJECTED` | `reason_code=UNSUPPORTED_OPERATION`；共享 Event ingress 无状态处理并发布脱敏 telemetry，不写 Evidence，不进入 plan_delta handler |
| prepare 暂未确定或存储/处理失败 | `503 / UNAVAILABLE` | `{}` |

正文超过现有 256 KiB 上限返回 413；严格 JSON/UTF-8/信封身份尚不可关联的错误沿用现有 400 空响应，不伪造 operation ID。
以上信封均保留原 operation ID，timestamp 用统一时区工具。数据库异常后不能声称 Evidence 已保存；只能返回 503 并允许原请求重试。

## 6. 暗构建、Operation 激活与插件消费激活

本计划先以 T0-A 收敛公共信封结构并保持 issued 可观察行为不变，再由 T0-B 独立改变可识别拒绝的留证语义；两者都不注册 plan_delta，
且分别在独立有效快照完成 issued 回归。随后先通过 T1-A 联合合同门禁，再由 T1-B–T5 实现 plan_delta DTO、独立 OpenAPI schema、模型、Repository、Service、migration 和聚焦测试。
独立 schema 只做合同验证，
不加入公开 `WMS_EVENT_REQUEST_SCHEMA` 的 `oneOf`；公开 OpenAPI 与 production event handler 留到同一生产激活切片接入。
plan_delta 暗构建不注册：

- production event route；
- Celery task、Beat schedule 或 worker hook；
- WorkLine START/STOP composition；
- Transport 或设备后继调度。

生产激活分成两个边界：

1. **R2-B1 基础 Operation 激活**：在 T5、R3-B、R4 和 WMS 联合 fixture 完成后，装配 prepare typed Adapter、
   PickingTask confirmation owner、plan_delta 静态 Event route/OpenAPI 及共享 worker。它不要求 `manual_bin_processing` 当前启用，
   也不从插件清单动态注册或注销 operation。已有可靠义务和迟到回调始终可接收、保存和对账。
2. **R2-B2 插件消费激活**：在 R2-A 与 R2-B1 之后，才让 `manual_bin_processing` WorkLine START/业务节点创建新的 prepare intent，
   并装配 PickingTask STOP/插件切换 blocker 和后继执行。未来其它插件通过同一 typed method 接入，不修改 operation route 或可靠内核。

R2-B1/R2-B2 的新动作准入、完成和资源释放只检查当前有效业务计划及真实同任务依赖；历史计划冲突 Evidence 不形成统一 blocker。
已有动作结果接收路径不得被历史拒绝拦截。
插件缺失、版本不匹配或能力未装配时 fail closed：禁止新业务触发；已在途响应保留原 identity、Evidence 和资源围栏并进入对账。

## 7. 第一轮联调数据

第一轮只使用：

```text
task_type = MANUAL
plan_revision = 1
target_rack = 一个接料货架面
added_bin_source_racks = 一个五层来源货架面
added_direct_picks = 省略
```

该 revision 成功应用的验收结果仅为：任务和计划成员持久化正确、PickingTask 为 `EXECUTING`、Evidence 为 `APPLIED`。
它不表示货架或 Bin 已经搬运，也不表示现场物理完成。

## 8. 测试所有权

- `tests/contracts/wms_adapter/outbound_picking/`：严格 DTO、未知字段、条件必填、revision 形状、ACK/错误联合和独立 OpenAPI schema。
- `tests/integration/wms_adapter/outbound_picking/`：PostgreSQL 约束、migration、任务锁、连续版本、并发重复、整批回滚和 Evidence 关联。
- outbound picking Service 聚焦测试：状态门禁、prepare confirmation 门禁、首批状态迁移和后续 revision 追加。
- prepare 与计划回调时序测试：回调先到、prepare 响应未知、原请求重复等待、确认落库后原请求成功应用及随后返回 `DUPLICATE`；
  覆盖超期、确定冲突、绑定不匹配和等待期间正文变化；PostgreSQL 验证并发重试只应用一次、失败回滚以及 `PENDING` 不产生业务副作用。
- 计划阻塞测试：首次冲突原子设置指针、后续冲突不覆盖、正确 revision 不能绕过、已成功重放不解除阻塞、原任务归属不可被冲突正文
  改绑、未知任务不污染其他任务、迟到消息不重开已结束任务；验证 `EXECUTION_COMPLETED` 迟到消息保存 `RECONCILING` Evidence 并返回
  `STATE_CONFLICT`、但不设置阻塞指针；验证 `PENDING` 不设置冲突指针和事务回滚不留下半个阻塞事实。
- 架构 guardrail：暗构建不得出现在公开 OpenAPI、production route、Celery/Beat 或插件 runtime 中。
- migration、生产模块和新增 HEAVY 资产必须在 `docs/architecture/heavy-test-impact.toml` 中具有精确映射。
  本切片固定更新五项：`wire_common.py`、`wms_adapter/outbound_picking/**`、`wms_integration/outbound_picking/**` 均增加
  `test_plan_delta_postgresql.py`；`wms_adapter/v1/**` 因 T0-A 路由变化增加 `test_issued_postgresql.py`；CLI 生成的新 migration 文件建立
  精确条目，映射初始 schema baseline、outbound picking schema 和 plan_delta PostgreSQL 三项。
- typed Operation 基础能力测试按现有 owner 分层：SDK 只证明 intent/outcome 不可变与固定方法合同；domain Adapter 证明 wire 差异；
  `DecisionApplier`、`FactProcessor`、`WmsConfirmation` 与 PostgreSQL owner 继续证明共享事务、领取、重试和恢复；插件测试只证明业务节点
  创建/消费正确 typed 值。不得为每个 operation 复制公共可靠性矩阵。
- generic 测试清理前必须生成逐测试 `MIGRATE / KEEP / DELETE` 清单。typed 承接测试先通过，再删除
  `CreateWmsConfirmation(operation, dict)`、`WmsInboundAdapter.dispatch(operation, dict)` 和插件字符串分派断言；同一文件中的共享可靠性测试
  必须保留或迁到其唯一 owner，禁止整文件盲删。

## 9. 验收标准

1. revision 1 原子保存唯一接料货架面和全部新增来源，并将任务迁移为 `EXECUTING`。
2. 后续 revision 只能连续追加，不修改既有接料货架面或来源。
3. 同一来源身份在并发或重放下最多产生一条业务记录。
4. 任一成员冲突会使整个 revision 回滚，不出现部分应用。
5. 完整规范化消息和业务成员可以通过 Evidence 追溯；不声称保留 HTTP 原始字节、空白或对象键顺序。
6. revision 2 及以后到达后，初始接料货架面仍通过 `initial_plan_evidence_id` 直接追溯到 revision 1。
7. 首轮单来源、无直接取料 fixture 通过，但正式 DTO 不把这一联调限制写死。
8. 本切片没有 production route、Celery/Beat、TransportTask、BinExecution 或 DeviceCommand 副作用。
9. prepare 尚未超期且结果未确定时，先到的匹配回调只保存 `PENDING` 并返回 `503`；确认成功后，原请求重试可原子应用，
   不会因正常时序竞争永久冲突，也不会把尚未应用的消息误报为 `DUPLICATE`。
10. 确定计划冲突通过现有 PickingTask 的首次阻塞证据指针持续阻止新计划应用；本切片不自动解除，不改变原执行阶段或既有物理事实。
11. 第 9 项只有在 WMS 联合确认、主合同修订和联合 fixture 三项证据齐全后才成为可实施合同；任一缺失时 T1-A 失败并停止后续实施。
12. `wms_operations` 对插件只暴露固定 typed methods；公开 SDK、插件源码和生产调用点不存在可传任意 operation 字符串/裸 `dict` 的入口。
13. typed intent 经共享生命周期可靠持久化，封闭 wire 响应经可靠保存后转换为 typed outcome；插件不解析 WMS 原始 JSON 或按 operation 字符串分派。
14. 零插件消费者时基础 Operation 仍可装配；一个插件节点可组合多个 operation，多个插件可复用同一 operation，且不修改 route 或内核。
15. R4 对账入口完成前不得生产激活 plan_delta；修正请求必须以新 identity 先保存为 `RECONCILING` Evidence，对账动作必须引用原始阻塞 Evidence
    与该修正 Evidence，在任务锁内验证严格下一 revision，并原子应用计划、推进指针、清空 blocker 和记录审计；任务取消不属于该入口。
16. typed 承接测试全部通过后才清理过期 generic 测试；共享 `WmsConfirmation` 可靠性覆盖不减少。

## 10. NOT in scope（当前 plan_delta 暗构建切片未包含内容）

- `outbound.picking_task.queue_changed@v1`；
- `outbound.bin.inbound_batch@v1` 和具体 Bin 选择；
- 货架、Bin、CTU、滚筒线及人工工作位物理执行；
- point2 人工准入、PDA 完成、释放与应用结果报告；
- RETURN_BUFFER、退箱回库、NG、换面换架、任务完成确认；
- 部署、真实 WMS 联调和现场业务验收；其中 prepare/plan_delta 接口装配由 R2-B1、`manual_bin_processing` 新业务消费由 R2-B2 承接，
  现场物理和业务验收仍不在本计划内。

上述项目各自涉及尚未闭合的执行或供应商合同，不为 T1-B–T5 计划持久化暗构建预埋兼容层、业务空实现或通用恢复框架。
生产激活复用 `TODOS.md` 的“人工 PickingTask 自动准备生产激活”，不新增同义 TODO；本设计第 6 节作为其具体验收依赖。
R4 只交付 plan_delta 阻塞所需的最小受控对账，不扩展为全系统通用对账平台。

## 11. 复用、组织与性能

### What already exists（现有能力）

| 已有能力 | 本切片用途 | 不做什么 |
| --- | --- | --- |
| 唯一 `v1/events.py`、`strict_json.py`、`wire_common.py` | 有界读取、严格 JSON、公共信封/identity、静态分派、Event ingress 正文上限唯一常量 | issued/plan_delta 不二次解析 raw body；transport 只改为直接引用共享上限；其它 operation 保持现状；不新增 route、registry 或默认 handler |
| `wms_adapter/outbound_picking/` | 新增同域 plan_delta data DTO、独立 schema、typed handler | handler 不拥有数据库、业务状态、幂等锁或重试循环 |
| `InboundEvidenceService.accept/record_conflict` | 原身份留证、载荷比较、冲突原因 | 不新增 inbox、响应缓存或 payload 表 |
| `PickingTaskRepository` | 精确任务行锁、当前版本和首次阻塞 | 不扫描全部任务、不新增分布式锁 |
| prepare confirmation owner 与响应证据 | 只读判断准备是否明确成功 | 不重发 prepare、不模拟接受响应、不改共享 dispatcher |
| 已冻结 PickingTask WorkLine 关联 | 校验本任务的 prepare 归属 | 不读取当前插件、不硬编码 plugin key/flow mode、不领取任务 |
| 现有 PostgreSQL 临时库与迁移支持 | 约束、事务、并发与迁移链验证 | 不在共享开发库执行迁移验收 |
| `CreateWmsConfirmation`、`WmsConfirmationLifecycleService`、dispatcher、`WmsClient` | 提取并保留唯一可靠生命周期；typed intent 在提交前冻结，typed Adapter 单次发送 | 不把字符串/裸 dict 构造入口继续暴露给插件，不复制幂等、重试或并发领取 |
| `InstalledWorkLinePlugin` 与 deployment 静态 composition | 装配插件对 typed Operation 的消费能力，并按冻结业务上下文精确选择 | Operation 不保存消费者清单，不动态注册 route，不提供默认插件 |

plan_delta 新增代码只进入现有 outbound_picking 两个域目录：wire/handler/schema 在 `wms_adapter/outbound_picking/`，
模型/Repository/Service 在 `wms_integration/outbound_picking/`。Service 和模型按既有 `__init__.py` 规则导出，
但不修改部署装配以接入暗构建 handler。前置共享入口修正只能触及现有 route/`wire_common.py`、issued handler 及其测试，不能借机迁移
平铺 Transport operation 或建立动态框架。R3 仅按已批准范围迁移既有 Inbound WES→WMS 调用到 typed facade。Service 保留第 4 节的
短流程注释；PickingTask 只注释“阶段与阻塞正交”，不复制整篇设计。

性能约束：

- 正文先限 256 KiB 再解析。revision/timestamp 和字符串先按 wire 上界验证；复用现有严格数值解析器，
  不为极大指数、超长数字或嵌套输入执行无界转换。恶意小输入与最大合法正文均纳入有硬超时的解析测试。
- 一次请求的来源去重用精确 tuple set，复杂度 O(n)；查询只查本任务和本次候选身份，不扫描历史全部 Evidence/成员。
  数据库查询/写入按有界批次处理；不逐成员一次 SELECT 或 flush，不用 `ON CONFLICT DO NOTHING` 掩盖整批冲突。
- 数据库唯一约束是并发最后防线。确定冲突在业务写入前一次性检查；意外约束冲突或死锁须回滚并返回 503，原请求重试后重新分类。
  事务重试不换 identity；不在持锁事务内 sleep、轮询或调用外部服务。
- 没有缓存需求：任务阶段、阻塞、prepare 结果都要求当前数据库事实。不同任务可独立处理；同任务串行是业务要求。
  性能验收记录候选数、SQL 数与耗时，验证增长来自批次数而非逐项往返，不承诺未经实测的毫秒 SLA。

## 12. 测试路径、失败模式与所有权

框架：pytest/pytest-asyncio。下列是计划覆盖的 30 组路径；目前没有 plan_delta 或 typed Operation facade 实现，新增路径均为待实现 GAP，
不是 30 个已证实的生产故障。既有 issued/prepare 测试只提供模式和回归基线，不能算作 plan_delta 或 typed Operation 覆盖。

```text
代码路径                                     调用方流程
共享 ingress + typed handler
  |-- G01 公共信封单次解析与可识别拒绝留证      400/413 不伪造身份；可识别的 422 提交 Evidence
  |-- G02 revision/数组/locator/边界校验       单目标、单来源、多来源、直接取料均按合同
  `-- G03 DTO 与独立 schema 一致              公开 API 仍不接受本 operation
Service 单事务
  |-- G04 首批计划/后续连续版本                WMS 发下一批 -> 提交后才得到 202
  |-- G05 同 ID 已成功重放                     响应丢失 -> 原请求 200，无重复成员
  |-- G06 同 ID 改内容                         409，首次内容不变
  |-- G07 新 ID 同当前版本业务重复             EXACT data 摘要相同为 200；键序无关、数组顺序敏感，原始追溯指针不移动
  |-- G08 跳号/倒退/同版本异内容               409，整个版本不应用
  |-- G09 来源冲突/无效引用                    409，无部分成员
  |-- G10 未知/QUEUED/已结束/绑定不匹配        拒绝，不改绑、不重开任务；终态不设置阻塞指针
  |-- G11 prepare 未定 -> 成功/超期/冲突       503 原请求重试；确认成功才 202
  |-- G12 持续阻塞与首次拒绝重放               换 ID/正确 revision 也不能解锁
  `-- G13 flush/commit 失败与响应丢失          503 或原请求去重，无虚假成功
真实 PostgreSQL [integration]
  |-- G14 同任务重复/版本/阻塞并发             最多应用一次；串行符合提交顺序
  |-- G15 prepare 回写与计划争抢任务锁         无反向锁死；允许先 503 再成功
  |-- G16 迁移/约束/旧 issued+prepare          干净库可迁移，已有任务数据无伪计划
  `-- G17 Evidence 处理隔离                   PENDING/APPLIED 不被通用处理器误领
暗构建 [architecture + API]
  `-- G18 公开 schema/route/进程入口缺席       不发送 WMS 请求，不唤醒 worker/设备
typed Operation 生命周期
  |-- G19 fixed method -> fixed intent          插件不能传 operation 字符串或裸 dict
  |-- G20 一个插件节点创建多个 intent           各 operation 身份、DTO 与 owner 独立正确
  |-- G21 多插件复用同一 typed method           不改 route/core，不产生默认消费者
  |-- G22 零消费者部署                          基础能力可装配；不发起新业务
  |-- G23 wire 响应 -> typed outcome            封闭联合正确，非法响应 fail closed
  |-- G24 插件缺席时已有响应                    可靠保存并围栏对账，不丢弃或改绑
  |-- G25 generic 公开入口残留                  SDK/插件/生产调用点扫描为零
  `-- G26 测试 owner 承接与清理                 typed 先绿；共享可靠性 KEEP；过期断言 DELETE
R4 受控计划对账
  |-- G27 阻塞后收到修正 plan_delta             新 ID/严格下一 revision 保存 RECONCILING，不应用、不改 blocker
  |-- G28 授权、Evidence、版本或状态不匹配       fail closed，不修改计划、revision、blocker 或资源围栏
  |-- G29 成功、重复与并发对账                  行锁内原子应用并清 blocker；修正 Event 从首次 409 经对账提交后原样重试为 200
  `-- G30 零消费者/插件缺失                     基础计划可对账落库；新执行保持冻结，不切断已有可靠义务
```

所有者（以下新增文件是实施目标，并非已存在产物）：

| 路径组 | 主要测试文件 | 关键断言与失败表现 |
| --- | --- | --- |
| G01 | 共享 ingress owner 测试、现有 `test_event_handler.py` issued 回归 | 公共信封在 issued 路径只解析一次；route/issued/transport 直接引用唯一正文上限并保持边界断言；复用重复 key/identity 断言，不复制全套 fixture；可识别非法 data 不在 handler 短路，经 operation recorder/service 以 `WMS_EVENT + IGNORED` 留证后返回 422，原样重放保持首次 timestamp，改正文返回幂等冲突；Transport/recovery/既有 Inbound 行为保持不变 |
| G02–G03 | `tests/contracts/wms_adapter/outbound_picking/test_plan_delta_wire.py`、`test_plan_delta_event_handler.py` | 只覆盖 plan_delta 的真/假值整数、null、未知字段、空数组、locator 和数值边界；parser 对非法 data 保留公共信封与 validation error，handler 的合法/非法结果都调用 recorder/service；不重复 raw-body/公共 identity 矩阵；schema 与 DTO 对同一 fixture 一致 |
| G04–G12 | `tests/contracts/wms_adapter/outbound_picking/test_plan_delta_service.py` | fake 端口验证 revision、状态、prepare、ACK/原因码、首次指针和 MANUAL/AUTO 中立；G07 证明双方 `data` 复用 `normalize_payload(..., EXACT)` 的内存摘要，键序无关且数组顺序敏感；公共摘要/锁只做一次接入证明，不能把 fake 测试当事务证明 |
| G13–G15 | `tests/integration/wms_adapter/outbound_picking/test_plan_delta_postgresql.py` | 提交前响应不可成功；中途/提交失败无半批；独立连接验证任务 revision/阻塞并发和 prepare 锁顺序，不重复 Evidence 内核的全套 identity 竞争矩阵 |
| G16 | 现有 `tests/integration/wms_adapter/outbound_picking/test_schema.py` + 新增 PostgreSQL 文件 | 真实 CHECK/FK/唯一性、旧任务 revision 0、base→head 与空库→head；复跑 issued/prepare PostgreSQL 回归 |
| G17 | 新增 `test_plan_delta_postgresql.py` + 现有 FactBuilder owner 测试 | 直接调用真实 `claim_decision_batch()`：`PENDING` 由状态条件排除，`APPLIED` plan Evidence 不进入工作线插件发布队列，按 WorkLine/发布用途边界隔离，合法 MaterialExecution Evidence 仍可领取；再直接验证 FactBuilder 对非 recovery 的 `WMS_EVENT` fail closed。不得修改共享扫描器或重复通用内核全套测试 |
| G18 | `tests/architecture/test_outbound_picking_plan_delta_activation_guardrail.py`、现有 `tests/api/test_wms_events.py` | 检查公开 schema 无此 oneOf、生产 route 对该 operation 仍拒绝，main/celery_worker/deployment/src/workline_plugins 无生产激活；保留 issued/prepare 原行为 |
| G19–G20 | SDK typed Operation 测试、`tests/runtime/execution/test_decision_applier.py`、对应插件测试 | 固定方法生成不可变 typed intent；一个节点组合多个 operation 时分别冻结正确 identity/owner，且没有 I/O |
| G21–G22 | deployment composition 与插件路由 owner 测试 | 同一 method 可由两个静态插件消费者复用；零消费者时 Operation 能力仍装配，新业务触发 fail closed，无动态 route 注册 |
| G23–G24 | 各 domain Adapter 合同、`test_fact_processor.py`、插件 typed outcome 测试和 WmsConfirmation PostgreSQL owner | 封闭响应先可靠保存再转换 typed outcome；冻结插件版本缺失时保留 Evidence/围栏并进入对账，不回退默认插件 |
| G25 | `tests/architecture/test_plugin_sdk_boundary_guardrail.py`、WMS integration boundary guardrail、精确 `rg` | `CreateWmsConfirmation(operation, dict)`、`WmsInboundAdapter.dispatch(operation, dict)`、插件 operation 字符串分派和兼容 import 残留为零 |
| G26 | R3 的逐测试 `MIGRATE / KEEP / DELETE` 清单及对应 owner | `test_inbound_adapter.py` 中 typed Adapter 专属断言迁移，WmsConfirmation 共享可靠性断言保留或精确迁移；承接绿灯前不删除旧测试 |
| G27 | outbound picking Event/API 合同测试 | 阻塞后新的严格下一 revision 使用新 identity 保存 `RECONCILING` Evidence，不立即应用且不覆盖首次 blocker；修正正文重放/漂移继续遵守公共 identity 规则 |
| G28–G29 | R4 Service 聚焦测试、`tests/integration/wms_adapter/outbound_picking/test_plan_delta_postgresql.py` | 管理授权、两份 Evidence、任务/operation/revision/乐观锁版本与阶段逐项 fail closed；独立连接验证行锁、原子应用+清 blocker+修正 Evidence APPLIED、提交失败回滚、相同修正 Evidence 重放和并发竞争；完整覆盖修正 Event 首次 409→管理对账提交→原 Event 200 DUPLICATE→下一 revision；后续推进/再次阻塞/终态仍重放成功，改正文仍 409，实际被拒绝的冲突正文保持拒绝、原本 APPLIED 的 blocker Evidence 原样重放仍成功，拒绝与对账历史不被覆盖 |
| G30 | deployment composition、outbound picking Service 与插件执行门禁测试 | 零消费者或冻结插件缺失时仍可完成基础计划对账和持久化；不触发新的插件动作，已有在途结果仍可靠保存并保持围栏 |

G18 必须覆盖实际进程根 `main.py`、`celery_worker.py` 和 `deployment/`；不能照抄只扫描 main/src/plugins 的旧 guardrail。
API 只通过 ASGI 测试，不打开浏览器或真实生产入口。已有动作结果不会被阻塞的执行路径测试属于后续激活/插件切片，
本轮只验证 plan_delta 没有注册该类拦截器，不声称已完成物理链路验收。

现实失败方式与可见结果已逐组列在图和表中：格式/引用错误明确拒绝，状态/版本冲突留证阻塞，暂时失败原请求重试，
事务失败不返回成功；G14–G17 与 G28–G29 不允许以 Mock 替代真实数据库。G19–G26 防止 typed facade 被 generic 入口绕过、消费者缺失时丢失
既有可靠义务，以及清理过期测试时误删共享可靠性覆盖；G27–G30 证明修正 Evidence 先可靠留存，再由受控事务恢复计划且不越过插件执行门禁。
设计层没有剩余“无测试要求、无错误处理且静默”的关键路径。

## 13. 实施与验证任务

以下为实施任务，当前进度与证据见第 17 节。R1 是治理门禁，T0-A 是行为不变的公共入口结构修正，T0-B 是 issued 拒绝留证行为变更，
T1-A 是 WMS 联合合同硬门禁，T1-B–T5 是 outbound_picking 暗构建切片；R2 负责 prepare Coordinator/Policy 与分离激活，
R3 负责共享 typed Operation 生命周期及既有调用迁移，R4 负责计划阻塞对账。按第 1.1 节四条依赖链实施；同步推进前先冻结各自 owner 清单，
只允许不重叠切片并行，共享写状态或交叉文件必须由唯一 owner 串行完成。
生产代码前遵守 Execution Lock：冻结 HEAD/dirty 指纹、生产符号/调用点、测试/fixture 所有者与 HEAVY mapping；
按需 GitNexus upstream impact，索引不可用则明确降级为精确调用点分析。高风险实施按 TDD，禁止借评审自动提交或部署。

- [x] **R1（P1，法规门禁）— 固化 WMS Operation 基础能力与两类 ACK 提交模式**：修订 `AGENTS.md` §4.3/§4.4，先固定以下法规：
  - Operation 与消费者解耦，允许零/一/多插件消费者；Operation 不按插件安装状态动态注册或注销。
  - 插件只通过单一 `wms_operations` facade 的固定 typed methods 创建无副作用 intent；typed outcome 后再进入插件；禁止公开 generic
    operation 字符串/裸 dict 入口。SDK 可承载 typed intent/outcome，宿主保留 wire、持久化、HTTP 和可靠生命周期。
  - 每个 WMS→WES operation 合同显式声明以下一种 ACK 模式：

    1. 业务事实构成接收成功：Evidence 与该业务事实同事务提交后才返回 `RECEIVED`；issued 建立 PickingTask、plan_delta 写计划成员/版本属于此类。
    2. Evidence 接收后异步应用：Evidence 提交即可返回接收 ACK，业务由后续事务/worker 应用；Transport 结果等现有异步事实路径保持此类语义。

    两类模式均在提交后才启动 Transport、DeviceCommand 或其它外部副作用，ACK 均不代表物理完成。
  - 文件：`AGENTS.md` 保留法规；在 `docs/contracts/wms-async-callback-envelope-contract.md` 规定分类字段，并在当前所有含 WMS→WES
    operation 的所属合同就地标注模式：`transport-fulfillment-contract.md`、`wms-rough-sorter-inbound-integration-requirements.md`、
    `wms-outbound-picking-task-integration-requirements.md`、`wms-manual-outbound-picking-integration-requirements.md`、
    `wms-inbound-putaway-integration-requirements.md`。不在 `AGENTS.md` 建重复中央清单，不 grandfather 存量，不新增运行时 enum/registry，
    不借分类改变既有 wire 或 ACK。
  - 验证：对上述法规与合同运行 `git diff --check`；以静态 route/handler 常量和全部合同中的 WMS→WES 行做双向清单，逐一核对 issued/plan_delta、
    Transport、recovery、manual completion、putaway reconciliation 及其它已声明入口，保证每项在所属合同恰有一种模式、无遗漏或重复，
    也不把现有异步路径误判为必须同步应用。再以 SDK/宿主/插件边界清单证明 typed intent/outcome 不把 HTTP、DB 或重试带入 SDK。
    R1 未闭合时四条实施链均不得继续。
- [x] **T0-A（P1）— 公共信封结构收敛，行为不变**：让唯一 Event route/`wire_common.py` 产出一次解析的公共信封，issued handler
  改为消费该边界；只提取无状态 helper，不新增 Runtime、registry、默认业务 handler 或数据库 API 层。同时让 route、issued、transport
  handler 直接引用 `wire_common.MAX_WMS_EVENT_BODY_BYTES`，删除 transport 重复数值常量，不触及其解析流程。
  - 来源：2026-09-05 WMS Operation 法则复审；文件：现有 `v1/events.py`、`wire_common.py`、issued/transport handler 及其直接测试。
  - 验证：issued 当前 400/413/422、ACK、幂等和事务断言保持不变；公共信封在 issued 路径只解析一次。不得借此迁移其它 operation、
    修改公开 path 或改变 Evidence 写入；Transport、recovery 与既有 Inbound 路径保持原有解析和回归结果。
- [ ] **T0-B（P1）— issued 可识别拒绝可靠留证**：在 T0-A 稳定边界上，将“合法公共信封但 issued data 非法”的首次请求通过
  API→handler→operation recorder/service→共享 Evidence 以 `WMS_EVENT + IGNORED` 提交完整规范化请求后返回 422；这是一项已生产 operation 的可观察行为变更，
  不与结构迁移合并，不新增共享状态或响应缓存。
  - 前置：核对出库合同 §7.1 与 R1 分类允许该拒绝留证；对 Event route、issued handler/service 运行强制 upstream impact，
    HIGH/CRITICAL 影响在修改前按 Execution Lock 报告。
  - 规范化与切换门禁：issued 合法 DTO 同样使用 `model_dump(mode="json", exclude_none=True)`，避免合法省略 `not_before` 与非法显式
    null 的摘要碰撞。旧实现曾自动补 null；目标库切换到 T0-B 前必须只读检查全部 issued Evidence 的完整 payload，证明不存在
    `data.not_before = JSON null`，不得只检查活动任务或某一 apply_status。非零时停止该库切换并保留原 identity、payload、digest 和证据；
    数据处置需独立决策，不得清库、重写摘要、换 ID 或引入兼容分支。未发布不等于没有历史数据，此门禁仅针对本次规范化规则切换。
  - 验证：明确把旧断言“422 且 recorder 未调用”改为“422 且 `IGNORED` 拒绝 Evidence 已提交”；增加真实 PostgreSQL 首次拒绝、
    原样重放保持首次 timestamp/原因、同 ID 改内容冲突、修正内容换新 ID 和提交失败无虚假 ACK。未知 operation 保持现有无状态 422，
    并断言不写 Evidence、不调用 operation handler；不得混入本任务建立未知 operation 持久化。issued handler 的 DTO 失败必须把
    公共信封与 validation error 交给 operation recorder/service，不得自行返回 422。
- [ ] **T1-A（P1，硬退出门禁）— WMS 联合冻结 prepare 未定语义**：向 WMS 明示主合同冲突，共同确认是否采用
  `PENDING + 503 / UNAVAILABLE + 原 operation ID/原正文重试`，并把唯一结论写入主合同和双方可执行 fixture。
  - 文件：`docs/contracts/wms-outbound-picking-task-integration-requirements.md` 对应公共 ACK 与 §8 小节、双方联合 fixture；不写生产代码。
  - 退出条件：WMS 书面确认、主合同无自相矛盾、联合 fixture 固定首次等待/原样重试/prepare 成功后接收/超期或确定冲突四类结果。
    同时联合确认 R4 修正 Evidence 在受控应用后原样重放返回 `200 / DUPLICATE` 的例外，并以 fixture 覆盖首次 409、对账提交/回滚、
    原身份成功重放、正文漂移及下一 revision 发布；任一未满足即停止。若 WMS 不接受当前语义，先修改本设计并重新评审，不得开始 T1-B–T5。
  - WES 待联合确认材料：[协议差异、响应示例与联合用例矩阵](../../integration/outbound-picking-plan-delta-joint-freeze.md)；尚未发送，不代表 WMS 确认或可执行 fixture 已完成。
- [ ] **T1-B（P1，人工约 2h / Agent 约 30min）— typed wire 与 ACK**：仅在 T1-A 通过后，补完整 data DTO、独立 schema 和消费共享公共信封的 handler；不修改公开 schema。
  - 来源：D4、D9、D11；文件：`docs/contracts/wms-outbound-picking-task-integration-requirements.md`、`src/app/wms_adapter/outbound_picking/`。
  - 验证：G02–G03 聚焦测试；parser 的 typed/validation-error 联合均保留公共信封并进入 recorder/service，handler 不短路非法 data；
    合法 DTO 使用 `model_dump(mode="json", exclude_none=True)`，缺省字段不写成 null，显式 null 以原始拒绝形态留证；新 ID 比较完整 data、
    原 ID 比较完整信封；实现必须消费 T1-A 已冻结的联合 fixture，不得重解释 ACK。
- [ ] **T2（P1，人工约 3h / Agent 约 45min）— 模型与迁移**：扩展 PickingTask，新增两类精确计划成员和约束，保留旧任务 revision 0。
  - 来源：D2、D3、D6、D12；文件：`src/app/wms_integration/outbound_picking/models/`、现有 metadata/模型导出入口、Alembic 新 revision。
  - 验证：G16；通过 `uv run alembic revision -m "add picking task plan delta"` 生成随机 revision，再编辑，禁止改写既有 migration。
- [ ] **T3（P1，人工约 4h / Agent 约 1h）— 原子应用与失败收敛**：实现同域 Service/Repository、完整重放、prepare 暂存、持续阻塞与 Evidence 隔离。
  - 来源：D5–D11；文件：`src/app/wms_integration/outbound_picking/services/`、`repositories/` 与相应导出。
  - 验证：G04–G15、G17；先失败用例再实现；G07 使用 `normalize_payload(..., EXACT)` 分别计算当前与首次 `data` 的内存摘要，
    验证对象键序变化仍为业务重复、数组换序不是业务重复，且无新增持久化摘要字段。G17 明确覆盖 `claim_decision_batch()` 的状态与工作线关联门禁、合法 MaterialExecution
    正向领取及 FactBuilder 对非 recovery `WMS_EVENT` 的防御性拒绝，不改共享 dispatcher/扫描器；不得读取当前插件或硬编码
    `manual_bin_processing`/flow mode，prepare 业务准入迁移由其独立所有权切片负责。真实 PostgreSQL 用例还须证明
    `EXECUTION_COMPLETED` 迟到消息留 `RECONCILING` Evidence/返回 `STATE_CONFLICT`，任务阻塞指针仍为空。
- [ ] **T4（P1，人工约 1h / Agent 约 20min）— 暗构建与回归门禁**：补实际进程根隔离、公开接口缺席和既有消费者回归。
  - 来源：D7、D13；文件：第 12 节测试所有者目录，`docs/architecture/heavy-test-impact.toml`。
  - mapping：在 `wire_common.py`、`wms_adapter/outbound_picking/**`、`wms_integration/outbound_picking/**` 三个既有条目增加
    `tests/integration/wms_adapter/outbound_picking/test_plan_delta_postgresql.py`；在 `wms_adapter/v1/**` 增加
    `tests/integration/wms_adapter/outbound_picking/test_issued_postgresql.py`；为 T2 生成的 migration 精确文件名新增条目，绑定
    `tests/integration/test_initial_schema_baseline_postgresql.py`、`tests/integration/wms_adapter/outbound_picking/test_schema.py` 和
    `tests/integration/wms_adapter/outbound_picking/test_plan_delta_postgresql.py`。
  - 验证：G18；运行 selector 合同并核对上述五项进入 manifest，不把插件测试并入核心 selector，不以全量 HEAVY 代替精确 mapping。
- [ ] **T5（P1，人工约 2h / Agent 约 40min，不含环境等待）— 最终快照验证**：闭合 SQL 批次、解析边界、迁移及所选 HEAVY。
  - 来源：D10、D12–D14；文件：相关测试和 mapping；不增加独立 benchmark 服务或吞吐框架。
  - 验证：聚焦域测试、暗构建 guardrail、QUALITY、selector manifest 及迁移；记录指纹与未验证边界。
- [x] **R2-0（P1，评审门禁）— prepare 专项计划获批**：专项计划已按本轮结论修订为“宿主可靠事务 Coordinator + 插件无副作用 typed Policy”，
  再完成独立工程评审并取得明确批准；当前所有权边界与实施记录见第 17 节，
  顶层架构设计 §7.6 的插件插槽与资源绑定合同只作为相邻角色/基础装配边界参考，不能替代本计划的 prepare 所有权约束。
  - 退出条件：专项计划状态、Coordinator/Policy typed 端口、生产/测试 owner、事务与可靠性边界、暗构建守卫和 HEAVY mapping 均评审闭合；
    显式确认 `picking_task_confirmation_owner.py` 的公开签名、confirmation→PickingTask 锁语义及 `WmsConfirmation` 响应证据形状保持不变；
    未通过不得开始 R2-A。
- [x] **R2-A（P1）— prepare 业务所有权收敛**：仅在 R2-0 通过后，按第 17 节保留宿主 `PickingTaskPrepareCoordinator`：统一拥有事务、锁顺序、
  PickingTask 绑定、`WmsConfirmationLifecycleService`、typed prepare intent 落库和提交后唤醒；将人工插件键、flow mode、WorkLine 准入、
  候选任务选择规则迁入 `workline_plugins/manual_bin_processing/` 的无副作用 Policy。
  - 文件：第 17 节限定的宿主、插件、deployment 和测试 owner；Policy 只接收 Coordinator 提供的事实快照并返回 typed 选择结果，
    不访问 Repository、数据库、HTTP 或队列；不得扩大为 WMS 全域搬迁或通用任务调度器。
    `picking_task_confirmation_owner.py` 及其锁/响应证据合同为只读边界，不属于 R2-A 修改范围。
  - 并行边界：可与 plan_delta 暗构建链同步推进独占文件；上述三个域 `__init__.py` 和两条 HEAVY glob 行由 plan_delta 链先写至 T5，
    R2-A 不在并行阶段改写。本次无 T5 并行写入，按第 17 节串行移交并完成导出、mapping 与验证；后续 T5 以该快照接续。
  - 验证：插件测试证明人工 Policy，核心测试证明 Coordinator 事务、锁、typed prepare intent、可靠义务和提交后唤醒；旧宿主业务常量及
    `claim_next_manual` 直接调用残留为零，但共享 Coordinator 与 Repository 能力继续保留。
- [x] **R3-A（P1，基础能力）— 建立 typed `wms_operations` 全生命周期**：在 SDK 定义无副作用 typed intent/outcome 与单一 facade 的固定方法；
  宿主将 typed intent 适配到现有 `WmsConfirmationLifecycleService`，Adapter 解析封闭 wire 响应，Fact Factory 从可靠 Evidence 构造 typed outcome。
  - 文件：`src/wes_plugin_sdk/`、WMS confirmation applier/fact owner、`wms_adapter/<domain_key>/`、deployment 静态 composition 和边界 guardrail。
    SDK 不含 HTTP、数据库、Repository、OpenAPI wire DTO、重试或恢复；内核私有通用 envelope 不对插件导出。
  - 验证：G19–G24；一个插件节点组合多个 intent、多个插件复用同一 method、零消费者装配、冻结插件缺失时保存响应并围栏对账；
    每个 operation 只验证 typed 接入差异，不复制共享可靠性矩阵。
- [x] **R3-B（P1）— 迁移既有 WES→WMS operation 并关闭 generic 入口**：消费 `TODOS.md` 中已有迁移项，将五个
  `inbound.material/source_rack.*` operation 及 prepare 按域接入 `wms_operations.<fixed_typed_method>`，请求和结果全生命周期 typed；删除公开
  `CreateWmsConfirmation(operation, dict)`、`WmsInboundAdapter.dispatch(operation, dict)`、插件字符串分派、旧平铺 import 和所有兼容路径。
  - 文件：SDK、`wms_adapter/inbound_material/`、`wms_adapter/outbound_picking/`、rough_sorter/manual_bin_processing 调用点、deployment、镜像合同、
    直接测试和 HEAVY mapping。修改两个 Inbound 合同时保留 R1 已标注的 ACK 模式。
  - 测试清理：修改前列出逐测试 `MIGRATE / KEEP / DELETE`；先让 typed domain/SDK/plugin 测试承接，再删除过期 generic 断言。
    `tests/contracts/wms_adapter/test_inbound_adapter.py` 中共享 WmsConfirmation 可靠性用例必须保留或精确迁至共享 owner，禁止整文件盲删。
  - 验证：G23–G26；精确残留扫描为零，插件不读取原始 WMS JSON；共享 WmsClient 单次发送、WmsConfirmation 生命周期和 PostgreSQL
    可靠性测试保持唯一 owner 且覆盖不下降。
- [x] **R4（P1，已被 ENG-D5 取代）— 计划修正并入普通 record/replay**：不提供管理员 `apply-correction` Service/API。
  WMS 使用新 `operation_id` 和严格下一 revision 提交修正；WES 在 PickingTask 行锁内按普通 Event 路径验证 identity、revision、owner、阶段与并发，
  合法时原子应用并保留历史 Evidence。任何引用、正文、版本、终态或并发冲突均 fail closed；不新增通用对账平台或数据库运维流程。
  - 验证：G27–G30 由普通 plan_delta record/replay owner 承接；人工 route、DTO、service method 与审计 helper 残留必须为零。
- [ ] **R2-B1（P1）— 独立激活基础 Operation 能力**：仅在 T1-A、T1-B–T5、R3-B、普通 plan_delta record/replay 和 WMS 联合 fixture 均完成后，装配 prepare typed Adapter、
  PickingTask confirmation owner、plan_delta 静态 Event route/OpenAPI 和真实 Worker；不要求任何具体插件当前启用，也不动态注册/注销 route。
  - 验证：ASGI 合同、真实 worker 派发/恢复、prepare→plan_delta 联合 fixture、零消费者启动、进程重启及插件缺失时在途响应可靠保存；
    结果只证明接口与运行机制，不能替代现场物理或业务验收。
- [ ] **R2-B2（P1）— 激活 `manual_bin_processing` 消费能力**：仅在 R2-A 和 R2-B1 完成后，让该插件 WorkLine START/业务节点创建新的 typed prepare intent，
  并装配 PickingTask STOP/插件切换 blocker 与后继执行；未来其它插件复用同一 operation 时只增加静态消费装配，不修改 route/core。
  - 验证：真实 worker、START/STOP/插件切换、一个节点组合多个 operation、计划阻塞对新动作/完成/释放的门禁，以及已有动作结果仍可闭合；
    插件停用只禁止新触发，不切断已存在可靠义务和迟到回调。

最小执行命令与门禁（在实施后的有效快照执行，本次不运行）：

- R1 先完成规则/合同相称检查；
- T0-A 聚焦行为不变的公共 ingress 与 issued handler 回归；T0-B 单独运行 issued 拒绝 Evidence 合同/持久化回归；
- `uv run pytest tests/contracts/wms_adapter/outbound_picking/ -q`
- `uv run pytest tests/architecture/test_outbound_picking_plan_delta_activation_guardrail.py tests/api/test_wms_events.py -q`
- R3-A/R3-B 运行 SDK boundary、`DecisionApplier`、`FactProcessor`、各 typed Adapter 与插件 intent/outcome 聚焦测试；按
  `MIGRATE / KEEP / DELETE` 清单核对旧测试，并对 generic 公开入口、字符串分派和兼容 import 做精确残留扫描。
- R4 运行授权、证据匹配、任务锁、并发/replay、事务失败和 blocker 一致性的聚焦与 PostgreSQL 测试。
- `uv run scripts/select_heavy_tests.py --scope unstaged` 与 `./scripts/run_selected_heavy_local.sh --scope unstaged`；最终获准暂存后改用 staged。
- `./scripts/git-quality-gate.sh --profile quality` 只对最终有效代码快照执行一次；纯文档后改不重复。
- R2-B1 必须增加零消费者启动、真实 worker、ASGI 和进程重启恢复验证；R2-B2 单独验证插件新业务触发与 STOP/插件切换 blocker，
  两者均不复用 plan_delta 暗构建绿灯。

迁移/HEAVY 所需环境先检查就绪，在独占临时 PostgreSQL 中使用干净逻辑库；`skipped` 不算通过。migration 新 revision 产生后，
一次性枚举并更新 schema head、metadata 与 HEAVY 所有受影响测试，不靠反复失败发现遗漏。无 Celery wiring 变更，
本切片不要求真实 worker 测试；生产激活任务必须另行真实 worker 验证。以上均不替代 WMS 联调或现场物理验收。

### 13.1 Worktree 并行化策略

| 步骤 | 模块目录 | 依赖 |
| --- | --- | --- |
| R1 | `AGENTS.md`、`docs/contracts/` | — |
| T0-A–T5 | `src/app/wms_adapter/v1/`、`wms_adapter/outbound_picking/`、`wms_integration/outbound_picking/`、对应测试 | R1；T1-B 另依赖 T1-A |
| R3-A–R3-B | `src/wes_plugin_sdk/`、`src/app/execution/`、`wms_adapter/inbound_material/`、插件调用点、对应测试 | R1 |
| R2-0–R2-A | `docs/superpowers/plans/`、`wms_integration/outbound_picking/`、`workline_plugins/manual_bin_processing/` | R1；R2-A 另依赖 R3-A |
| R4 | outbound picking 对账 Service/API、授权与对应测试 | T3 |
| R2-B1 | route/OpenAPI、worker、deployment | T5、R3-B、R4、WMS 联合 fixture |
| R2-B2 | `workline_plugins/manual_bin_processing/`、WorkLine START/STOP/插件切换 | R2-A、R2-B1 |

- Lane A：R1。
- Lane B：T0-A → T0-B → T1-A → T1-B → T2 → T3 → T4 → T5。
- Lane C：R3-A → R3-B；R1 后可与 Lane B 并行。
- Lane D：R2-0；R3-A 完成后执行 R2-A。T3 完成后可由独立 owner 并行执行 R4。
- 汇合顺序：T5、R3-B、R4 和 WMS fixture 完成后执行 R2-B1；R2-A 与 R2-B1 完成后执行 R2-B2。
- 冲突标记：Lane B 与 Lane D 都触及 `wms_integration/outbound_picking/`，Lane B 与 Lane C 都可能触及
  `wms_adapter/outbound_picking/` 和 HEAVY mapping。按第 1.1 节冻结唯一 owner；共享目录增量串行合入，不允许两个 worktree 同时改同一文件。

## Implementation Tasks

本节汇总当前 develop 复审新增的可执行任务；第 13 节保留完整依赖和门禁。

- [ ] **IT1（P1，人工约 0.5 天 / Agent 约 1h）— prepare ownership — 保留宿主 Coordinator 并提取插件 typed Policy**
  - 来源：架构 D37；文件：`src/app/wms_integration/outbound_picking/`、`workline_plugins/manual_bin_processing/`、`deployment/`。
  - 验证：插件纯 Policy 测试 + 核心 Coordinator 事务/锁/typed intent/提交后唤醒测试。
- [ ] **IT2（P1，人工约 1 天 / Agent 约 2h）— plan reconciliation — 在生产激活前交付 PickingTask 计划冲突对账**
  - 来源：架构 D38、D47；文件：outbound picking Service/API、授权、审计与 PostgreSQL 测试。
  - 行为：新 identity 的严格下一 revision 先保存为 `RECONCILING` 修正 Evidence；管理入口引用原始 blocker + 修正 Evidence，在任务锁内原子应用计划并清 blocker。
  - 验证：G27–G30，覆盖授权、Evidence/版本/阶段匹配、任务锁、并发/replay、事务失败、零消费者和插件缺失门禁。
- [ ] **IT3（P1，人工约 0.5 天 / Agent 约 1h）— activation — 拆分基础 Operation 与插件消费激活**
  - 来源：架构 D39–D40；文件：`src/app/wms_adapter/`、`deployment/`、`workline_plugins/manual_bin_processing/`。
  - 验证：零消费者启动、插件启停、在途响应恢复和静态 route 不变。
- [ ] **IT4（P1，人工约 1–2 天 / Agent 约 2–3h）— typed operations — 建立 intent/outcome 全生命周期**
  - 来源：架构 D41–D44；文件：`src/wes_plugin_sdk/`、`src/app/execution/`、各 domain Adapter 与 deployment。
  - 验证：G19–G24，固定 typed methods、多 Operation 组合、多插件复用及封闭 outcome。
- [ ] **IT5（P1，人工约 1 天 / Agent 约 2h）— generic migration — 迁移既有调用并删除 generic 公开入口**
  - 来源：架构 D42；文件：SDK、Inbound/outbound picking Adapter、rough_sorter/manual_bin_processing 调用点。
  - 验证：G25 与精确残留扫描；不保留 shim、双路径、字符串分派或插件原始 JSON 解析。
- [ ] **IT6（P2，人工约 2–3h / Agent 约 30–45min）— test ownership — 精确迁移并清理过期测试**
  - 来源：测试 D45–D46；文件：WMS Adapter、execution、architecture 与插件测试 owner。
  - 验证：逐测试 `MIGRATE / KEEP / DELETE`，typed 承接先绿，共享可靠性覆盖不下降。

_No new tasks from Code Quality Review._

_No new tasks from Performance Review._

## 14. 评审结论与决策依据

2026-09-04：D1–D7 用户逐项确认；随后用户明确授权“后续都按建议”，D8–D14 据此采用推荐方案，不追加逐项问答。
2026-09-05：按已固化的 WMS Operation 法则复审，D15–D18 修正共享入口、事务 ACK、插件所有权和测试所有权；用户要求据此修改计划。
评审基线：`feature/phase12-manual-bin-processing`，HEAD `033850764c23205509d8d4ea2f669dfaa381401a`。
保留原有 `.gitignore`、`AGENTS.md`、`CLAUDE.md` 及 index；本次只改本设计和仓库外评审元数据。
2026-09-05 修订基线为 `develop`，HEAD `a3a0ada130b201cf60a412269088d11d44035e01`；工作区已有其它架构文档变更，本次未读取或改写其完整 diff，
正式实施前必须重新冻结最终规则、合同和 dirty 指纹。
2026-09-05 本轮工程评审将总路线拆为显式切片，并由用户逐项采纳 19 个推荐选项；Claude 外部 reviewer 进行了初审、修订闭环和最终 fresh review，
最终 verdict 为 `CLEAR`。外部评审仅是设计证据，不替代 T1-A 的 WMS 联合合同确认、R2-0 专项评审或后续代码验证。
2026-09-06 当前基线复审固定为 `develop`，HEAD `873b2a4798c2f98d51fac988a244519295ac0b84`。用户选择完整总计划范围，并逐项确认
D37–D46；独立 Codex 子 Agent 对当前文件和实现只读复核，4 个 P1 与已确认项重合，新增 1 个 P2 测试 owner 问题已闭合。
本轮只修改本设计及其直接依赖的 prepare 所有权计划；其它 staged 文档保持原样，未执行代码、测试或 Git 交付动作。

| 决策 | 类别 | 证据与采纳结果 |
| --- | --- | --- |
| D1 | 范围 | 完整正式合同，单箱仅 fixture 约束；不缩成一次性 wire |
| D2 | 架构 | 共用 task_type 与 issued 模型，计划中立于 MANUAL/AUTO |
| D3 | 架构 | 仅 last 指针无法直接追溯初始目标；增加不可变 initial 指针 |
| D4 | 架构 | `v1/events.py` 的公开 oneOf 与静态路由一致激活；本轮仅独立 schema |
| D5 | 架构 | `wms_confirmation_service.py:429` HTTP 与结果事务分离；PENDING/503 原请求重试 |
| D6 | 架构 | 原设计仅 Evidence RECONCILING 无持续准入事实；使用任务首次阻塞指针 |
| D7 | 架构 | `inbound_evidence_repository.py:149` join Epoch；复用 prepare 空关联隔离 |
| D8 | 架构 | 原图让 route 保存 Evidence；改为 handler → Service 单事务 → Repository，API 不访问 DB |
| D9 | 代码质量 | 原“完整业务内容相同”未定义；精确 data 比较，不引入排序或自定义摘要 |
| D10 | 架构 | `picking_task_confirmation_owner.py:25` 在 confirmation 锁内锁任务；计划只读确认，避免反向锁 |
| D11 | 代码质量 | issued handler 只接受两类冲突原因，不能原样作为 plan_delta 联合；明确 ACK、首次拒绝原因、JSON 语义留证 |
| D12 | 架构 | 新字段/成员原计划缺数据库约束与旧任务初值；增加精确 FK/唯一性/配套 CHECK 与迁移要求 |
| D13 | 测试 | 18 组 plan_delta 路径缺实施测试；明确文件、断言、真实事务和实际进程根 guardrail |
| D14 | 性能 | 原计划未约束逐项查询和输入规模；正文上界、O(n) 去重、有界批次，无缓存或全局锁 |
| D15 | 架构 | 公共 ingress 只做一次有界读取、严格 JSON/公共信封解析和静态分派；plan_delta handler 只校验 data，不复制 raw-body 接收机制 |
| D16 | 合同 | WMS→WES 明确两类 ACK 提交模式；issued/plan_delta 的 Evidence 与构成接收成功的业务事实同事务提交，异步事实类可在 Evidence 提交后 ACK；外部物理副作用始终在提交后启动 |
| D17 | 所有权 | plan_delta 只读 PickingTask 已冻结关联，不读取当前插件或复制 `manual_bin_processing`/flow mode/人工领取判断 |
| D18 | 测试 | 公共信封、正文限制、严格 JSON 和 identity 竞争归共享 owner；operation 只验证接入差异与 revision/状态/prepare/原子应用业务规则 |
| D19 | 架构 | 可识别但 data 非法的请求保存为 `WMS_EVENT + IGNORED`；原样重放维持首次 422/timestamp，同 ID 改正文冲突，修正请求换新 ID；不新增状态、字段或实体 |
| D20 | 合同 | `PENDING + 503` 与主合同公共 ACK 规则冲突；设 T1-A 为 WMS 书面确认、主合同修订和联合 fixture 三项齐全的硬退出门禁，未通过不得实施 |
| D21 | 执行 | plan_delta 暗构建与 R2-A 使用两条显式依赖链并在 R2-B 汇合；允许不重叠切片同步推进，交叉文件和共享执行路径由唯一 owner 串行完成 |
| D22 | 架构 | 真正未知 operation 保持无状态 422，只发布脱敏 ingress telemetry；可靠留证和幂等只覆盖静态注册且 identity 可识别的 operation，避免无界数据库写入面 |
| D23 | 范围 | 本计划的公共信封单次解析保证只覆盖 issued 与 plan_delta；Transport、recovery 和既有 Inbound 保持现状，后续按各自计划迁移 |
| D24 | 依赖 | R2-A 改为依赖 prepare 专项计划；新增 R2-0 独立工程评审与批准门禁，角色归属计划只作为相邻边界参考 |
| D25 | 测试 | G17 验证 `claim_decision_batch()` 的状态与活动 Epoch 内连接隔离、合法 MaterialExecution 正向领取，以及 FactBuilder 对非 recovery WMS_EVENT 的防御性拒绝；不改共享扫描器 |
| D26 | 代码质量 | Event ingress 正文上限统一由 `wire_common.MAX_WMS_EVENT_BODY_BYTES` 拥有；route、issued、transport handler 直接引用，删除 transport 重复常量，其它合同上限不合并 |
| D27 | 代码质量 | 新 ID 同 revision 的业务重复复用 `normalize_payload(..., EXACT)` 分别计算当前与首次 data 的内存摘要；对象键序无关、数组顺序敏感，不新增数据库字段或摘要实体 |
| D28 | 测试 | 固定更新 wire_common、outbound_picking Adapter/Integration、v1 route 四个既有 HEAVY 条目，并为新 migration 建立精确三测试映射；selector manifest 必须包含这些 owner |
| D29 | 法规 | R1 不在 AGENTS 建中央分类表；公共回调合同定义分类字段，每个现行 WMS→WES operation 在所属合同就地标注唯一 ACK 模式，并以静态入口与合同双向清单验收 |
| D30 | 代码质量 | plan_delta 合法 DTO 使用 `model_dump(mode="json", exclude_none=True)`；缺省字段不注入 null，显式 null 按 INVALID_DATA 原始留证，T1-B 不照抄 issued 导出调用 |
| D31 | 依赖 | 冻结 `validate_prepare_response_owner` 签名、confirmation→PickingTask 锁语义与 WmsConfirmation 响应证据形状；R2-A 将该文件视为只读边界 |
| D32 | 执行 | plan_delta 链先拥有 outbound_picking 三个域 `__init__.py` 和两条 HEAVY glob 行至 T5；R2-A 只并行改独占文件，随后 rebase 并串行收口共享增量 |
| D33 | 架构 | issued/plan_delta parser 对非法 data 保留公共信封与 validation error；handler 的合法/非法结果均进入 operation recorder/service，由 Service 事务复用 InboundEvidence 留证，不在 handler 短路 |
| D34 | 架构 | `EXECUTION_COMPLETED` 的迟到 plan_delta 保存 RECONCILING Evidence 并返回 STATE_CONFLICT，但不设置 plan_blocked_evidence_id；终态本身承担拒绝门禁 |
| D35 | 执行 | R1 先完成所有合同的 ACK 模式标注；R3 基于 R1 快照 rebase 后再修改两个 Inbound 合同，必须保留分类字段与语义 |
| D36 | 范围 | 当前 develop 基线按完整总计划复审，不缩减为 plan_delta 单一代码切片 |
| D37 | 所有权 | prepare 使用宿主可靠事务 Coordinator + 插件无副作用 typed Policy；取代“整个 Service 迁入插件”的旧方案 |
| D38 | 恢复 | `plan_blocked_evidence_id` 的受控对账入口成为生产激活硬前置；不允许直接改库解锁 |
| D39 | 架构 | Operation 是与消费者解耦的基础能力，支持零/一/多插件消费者；插件可组合多个 operation |
| D40 | 激活 | 基础 Operation 能力与 `manual_bin_processing` 消费能力分离激活；插件缺席不切断在途可靠义务 |
| D41 | API | 插件通过单一 `wms_operations` facade 的固定 typed methods 声明意图，不传 operation 字符串或裸 dict |
| D42 | 收敛 | 删除公开 generic `CreateWmsConfirmation`/`WmsInboundAdapter.dispatch` 入口，不保留 shim、双路径或逃生口 |
| D43 | SDK | SDK 可定义无副作用 typed Operation intent/outcome；wire、DB、HTTP、重试和恢复继续归宿主 |
| D44 | 类型 | 请求 intent 与返回 outcome 全生命周期 typed；插件不解析原始 WMS JSON 或按字符串分派 |
| D45 | 测试 | 补齐固定方法、多 Operation 组合、多插件复用、零消费者、typed outcome 和插件缺失恢复矩阵；公共可靠性矩阵只测一次 |
| D46 | 测试 | 过期测试按 `MIGRATE / KEEP / DELETE` 精确迁移，typed 承接先绿，再删 generic 断言；禁止整文件误删共享可靠性 owner |
| D47 | 恢复 | WMS 以新 identity 提交严格下一 revision 并先形成 RECONCILING 修正 Evidence；管理对账引用原 blocker 与修正 Evidence，在任务锁内原子应用计划并清 blocker；任务取消保持独立合同 |

D8/D10/D11 的现有实现依据置信度 9/10；D9/D12–D18 是设计或现状缺项，置信度 8/10；D19–D35 已由当时源码/合同核对和 Claude 外部闭环复审。
当前 develop 复审中的 D37–D47 均由现有实现、计划行和已确认恢复合同验证，置信度 9–10/10；未声称已经观测到生产故障。
D1–D47 与测试 G01–G30 均已转为本设计的实施要求。没有新增独立 TODO；复用现有激活 TODO，不把现场物理执行、全系统通用对账平台或
动态 Operation registry 拉入本轮。四项完整性选择均采用 10/10 方案，其余决策属于不同架构方式，不编造完整性分数。
当前外部复核由独立 Codex 子 Agent 只读执行，报告的 4 个 P1 与已批准架构项一致，新增 1 个 P2 测试 owner 问题已按 D46 闭合；
未允许 reviewer 修改文件、运行测试、QUALITY、HEAVY、migration 或 Git 操作。

## 15. 首个实施切片进度（2026-09-06）

用户已授权修复评审意见并开始实施。基线 `develop@bf98b4fee7824ff9da073d962bacf663a1e82a91`，开始时工作区干净；
当时只完成 R1 和 T0-A 的代码及聚焦验证，T0-A 总验收复选框保留未完成。T0-B、T1-A–T5、R2–R4 尚未实施，不能把本节作为总计划完成证据。

- R1：公共合同定义 `ack_mode / ack_commit_facts`，五份所属合同为 8 个 WMS→WES operation 标注唯一模式；AGENTS 同步 SDK typed
  intent/outcome、零/一/多消费者与两类 ACK 提交规则。分类不改变 wire，也不代表联合合同或生产激活获批。
- P2 修复：R4 修正 Evidence 在受控事务提交后变为 `APPLIED`，原 Event 重试返回 `DUPLICATE`；保留拒绝历史，明确原本成功的
  blocker Evidence 不能被改报拒绝。联合 fixture 要求加入 T1-A/G29，WMS 确认仍未取得。
- T0-A：共享 `parse_wms_event_envelope` 保留一次严格 JSON 解析结果；issued handler 消费该结果，runtime 缺席也不重复解析。
  route 与 Transport handler 直接引用共享正文上限；issued DTO 拒绝、recorder、摘要和事务语义保持不变。
- 变更清单：生产仅 `wire_common.py`、`v1/events.py`、`outbound_picking/event_handler.py`、`transport_event_handler.py`；
  直接测试为 `tests/api/test_wms_events.py`、`tests/contracts/wms_adapter/outbound_picking/test_event_handler.py` 和
  `tests/contracts/wms_adapter/test_transport_event_handler.py`。删除的 handler 正文错误测试由共享 ASGI 坏 JSON/坏 identity 和已有 413
  上限测试承接。HEAVY 的 `wms_adapter/v1/**` 增加已有 issued PostgreSQL owner；无 migration、生成 DTO 或新的公开 operation。
- GitNexus：刷新 stale 索引一次；`receive_wms_event / _extract_operation` 为 LOW，issued `handle` 为 MEDIUM；动态 route→handler
  调用和 composition/测试消费者用精确引用补齐。索引工具生成的超范围 AGENTS/CLAUDE 改动已移除。
- RED：正常 issued 和 runtime 缺席路径均出现 decoder `2 != 1`；GREEN：API + outbound picking 域 + Transport handler 共 140 项通过。
  另执行 Inbound wire、严格 JSON、prepare 暗构建守卫、WMS 边界守卫和 selector 合同，共 198 项通过、1 项失败。
- 失败为基线已有 `test_prepare_has_no_production_activation_or_execution_reverse_import`：`wms_confirmation.py:14` 为注册外键目标
  导入 PickingTask，违反该守卫。失败源文件及守卫均与 HEAD 字节一致，本切片没有修改或放宽其断言；后续需闭合该架构矛盾。
- 定向 Ruff 与 `git diff --check` 通过；selector 成功选出 6 个 HEAVY 文件，但尚未执行真实 HEAVY。未执行完整 QUALITY、提交、推送、部署或 WMS 联调。
  可执行快照（上述 4 个生产文件、3 个测试文件及 mapping，路径排序后以 NUL 分隔路径和内容）的 SHA-256 为
  `180711c4bbb648bf8e3531156f76354eb40a2de45339784396f1504065fe3cc5`。
- 独立只读代码评审未发现 T0-A 可操作缺陷；对账文档措辞已按反馈限定实际拒绝正文。当前只有聚焦证据，不是 `MERGE READY`。

## 16. 继续实施与验证（2026-09-06）

用户确认继续后，完成 T0-B 代码、T0-A 架构守卫闭环及对应验证。仍以 `develop@bf98b4fee7824ff9da073d962bacf663a1e82a91`
为基线；本节取代第 15 节的当前状态，不代表整个 plan_delta 总计划完成。

- T0-B：可识别 identity 的非法 issued data 经 typed invalid receipt 进入原 Service，复用 `InboundEvidenceService.accept` 保存完整请求为
  `WMS_EVENT + IGNORED`，提交后返回 422。原样拒绝重放保留首次 timestamp，同 ID 内容漂移为 409，提交失败为 503 且事务回滚。
  合法 DTO 使用 `exclude_none=True`，非法显式 null 保留在原始请求中，避免与合法省略字段发生摘要碰撞。
- 架构闭环：PickingTask 的外键目标注册从 execution model 移至中立 deployment composition；测试 schema fixture 显式注册该模型。
  冷启动仍能解析字符串外键，且不导入 prepare Service；原反向依赖/暗构建守卫保持原断言并通过。
- 测试所有权：T0-B 的薄接入由既有 issued handler 合同测试负责；并发拒绝重放、真实提交/回滚、修正与摘要漂移由既有
  `tests/integration/wms_adapter/outbound_picking/test_issued_postgresql.py` 负责。新增 2 项 PostgreSQL 测试后该文件 7 项通过；
  API、outbound 域、WMS confirmation 与架构守卫聚焦集合 121 项通过。无新增 migration、公开 operation、Celery 注册或插件激活。
- 独立只读评审闭环了规范化切换风险：目标库必须满足 T0-B 的历史 null Evidence 零记录门禁。当前未检查任何目标运行库，
  临时库的成功验证不能替代此门禁；T0-B 代码已完成，但总验收复选框仍保留未完成。
- QUALITY：`./scripts/git-quality-gate.sh --profile quality` 通过，FAST 为 2621 passed、5 skipped；跳过项为既有 4 个外部 signature
  用例和 1 个容器层检查。Ruff、Bandit、脚本与拓扑门禁均通过。额外定向 basedpyright 发现 2 个 HEAD 已有类型问题
  （prepare 响应查询 key 与 issued task_type 类型），未将其记为通过，也未扩面修改。
- HEAVY 过程：首轮 140 passed、1 failed，为 schema 测试固定旧 head；仅更新 `HEAD_REVISION` 至现有 `627291489210`，全部 schema
  断言保留，定向 PostgreSQL 验证通过。复跑时旧临时逻辑库残留 `rough_sorter / ACTIVE` Epoch，真实 worker 启动守卫按合同拒绝启动；
  诊断捕获 `ActiveLineRunEpochExistsError`，未放宽守卫。随后在同一独占临时 PostgreSQL 实例创建干净逻辑库并从空库迁移至 head。
- 最终 HEAVY：干净逻辑库上执行 `uv run scripts/run_selected_heavy_tests.py reports/plan-delta-t0-heavy-manifest.txt
  reports/plan-delta-t0-heavy-clean.xml`，17 个选中文件共 141 passed、0 skipped（93.57s），真实 broker/worker/HTTP/数据库路径通过；
  日志为 `reports/plan-delta-t0-heavy-clean.log`。仅 schema 测试常量修正不触及 QUALITY 覆盖内容，复用已有 QUALITY 证据；最终 Ruff 和 diff 检查通过。
- 最终可执行快照 SHA-256：`274aa1d57cd0f2187e9beb39884252895e1d565486f44514a232fd6459f359c1`；按 `src / tests / scripts / deployment`
  与 HEAVY mapping 的所有变更路径排序，逐项以 NUL 分隔路径和文件内容。环境 Python 3.13.14、独占 Timescale PostgreSQL 与 Redis 8；
  精确环境、文件清单和 selector 的 17 文件 manifest 记录在本地 `reports/plan-delta-t0-snapshot.json` 与 `reports/plan-delta-t0-heavy-manifest.txt`。
- 下一退出门禁仍是 T1-A：WMS 书面确认、联合合同修订和可执行联合 fixture 尚未齐全，不开始 T1-B–T5。R2–R4 尚未实施；
  未 Commit、Push、PR、Merge、Deploy 或执行 WMS/现场验收。

## 17. 持续授权下的 R3 与 R2-A 实施（2026-09-06）

用户已明确后续不再分阶段确认。本节取代第 16 节的当前状态；基线仍为
`develop@bf98b4fee7824ff9da073d962bacf663a1e82a91`，全部修改留在工作区，未提交。

- R3-A/R3-B：SDK 提供六个固定 typed method 与不可变 intent/outcome；既有五个 inbound operation 和 prepare 通过同一 facade
  声明意图。宿主负责严格 wire 转换与持久化，rough_sorter 的结果解释、WAIT follow-up 和恢复消费 typed 数据。
  删除公开 generic intent/Adapter 和旧平铺 import；宿主私有 `WmsConfirmationAdapter` 仅桥接既有持久化信封与域 Adapter。
  Decision 摘要直接采用固定 typed Intent 标识与 dataclass 字段，不保留旧 generic 摘要桥接；确认身份、可靠重试、冻结 owner 和物理围栏保持不变。
- R2-0/R2-A：专项计划独立评审 CLEAR；`PickingTaskPrepareCoordinator` 保留事务、锁、任务绑定和可靠义务，显式注入
  无副作用 `ManualPickingPreparePolicy`。事实 Repository 只读不可变事实，候选 SQL 保留有界过滤、顺序与 SKIP LOCKED。
  核心测试注入 SDK stub Policy，人工规则由插件测试拥有；confirmation owner 签名、锁语义与响应证据边界保持不变。
- 串行所有权调整：T1-A 未取得外部证据，T5 未启动，因此将原预留给 T5 的空闲导出和 mapping 路径串行移交 R2-A。
  R3 SDK owner 完成后才接续 prepare 端口；同一文件始终只有一个写 owner。未来 T5 基于本快照接续，不覆盖 R2-A 增量。
  此调整只解决写入顺序，不解除 T1-A、R4 或生产激活门禁。
- 测试所有权：typed 承接先通过，再迁移旧 generic 断言；原共享 WmsConfirmation 测试保留。
  补齐多 Operation 组合、双插件复用、零消费者装配和冻结插件缺失围栏测试；插件测试不进入核心 selector。
  无新增 schema/migration、prepare route/OpenAPI 激活、Celery 注册或 manual_bin_processing 消费启动。
- 聚焦验证：组合核心集合 632 passed，rough_sorter FAST 167 passed，manual Policy 20 passed；插件 basedpyright 无错误。
  最终独立只读代码评审 CLEAR，新增双插件/零消费者覆盖闭合评审意见。
- 最终 QUALITY：`./scripts/git-quality-gate.sh --profile quality` 通过，FAST 2641 passed、5 skipped；跳过项为既有四个外部
  signature 用例和一个容器层检查。Ruff、Bandit、脚本、拓扑和速度预算通过。日志：`reports/wms-typed-quality-complete.log`。
- 最终核心 HEAVY：selector 的 18 文件 manifest 在独占临时 PostgreSQL/Redis、干净逻辑库和真实 worker 上执行，
  143 passed、0 skipped（102.76s）；独立 rough_sorter PostgreSQL 6 passed。两个逻辑库均从空库迁移至 `627291489210`。
  证据为 `reports/wms-typed-heavy-manifest.txt`、`reports/wms-typed-heavy.log`、`reports/wms-typed-plugin-postgresql.log`。
  后续仅补测试断言和人类文档，未改变该 HEAVY 覆盖的生产输入；QUALITY 已在最后测试快照刷新。
- 最终可执行快照 SHA-256：`f60db4c03dbe6b85c73bd8452f03209b340755d67c75635fde0f79f69fd07aa9`，
  路径清单及环境见 `reports/wms-typed-final-snapshot.json`。`git diff --check` 通过。
- 未验证边界：rough_sorter 容器业务 E2E 因现有镜像来源 revision 过旧，在镜像前置检查失败；未伪造标签或放宽测试。
  核心真实 worker HEAVY 与插件 PostgreSQL 通过不能替代该容器业务闭环或现场验收。
- T0-B 目标运行库的历史 null Evidence 检查仍未执行；T1-A 的 WMS 书面确认、联合合同与可执行 fixture 仍未齐全，
  因此 T1-B–T5、R4、R2-B1/B2 未实施。当前不是整个计划完成、生产激活或 MERGE READY；未 Commit、Push、PR、Merge、Deploy。

## 18. Operation 联调发布切片（2026-09-06）

用户已明确持续授权实施和联调服务器发布，要求以代码和联调推进双方合同收敛，不再逐阶段确认。此前 §15–17 的阻塞与未实施状态是历史快照；
T1-A 现为 WMS 联合验收项，不阻塞 T1-B–T5、R4 和 R2-B1 的实施及独立联调部署。R2-B2 工作线业务启动另列范围。

- T1-B–T5：严格 plan_delta DTO、静态 Event route、共享 Evidence 接收、原子计划/成员/版本提交、冲突阻塞和成功重放已实现。
- R4：超级管理员对账 API 与现有审计服务已接通；锁定 correction Evidence 后锁任务，管理重放校验当前状态和版本，WMS 已成功 Event 仍永久重放成功。
- R2-B1：零插件组合根接通 issued、plan_delta 与 prepare Adapter/owner；复用现有 WMS fulfillment worker，不新增 operation 运行时或重试基础设施。
- 数据库 revision `864351b8d0c6` 使用标准 `pgcrypto` 摘要索引支持有界请求内的长 face，保留原文精确比较；查询/写入按 250 个候选分批。
  downgrade 保留共享 extension。任务版本复用既有 `increment_version()`，不另建并发机制。
- 聚焦 PostgreSQL、ASGI、真实 worker 以及修复闭环已通过；完整 QUALITY：2719 passed、5 skipped，profile passed；最终选中 HEAVY：176 passed、0 skipped。
  使用项目固定 PostgreSQL 17.10 / TimescaleDB 2.27.1 镜像，空库到 head 迁移及 Alembic schema check 已通过。证据在 `reports/operation-quality-final.log`、
  `reports/plan-delta-activation-heavy-final.log`、`reports/plan-delta-pinned-migration.log`。
- 唯一主 Review 与后续修复闭环结论 CLEAR。部署配置另做只读评审，使用独立项目、DB、Redis 和端口，明确禁用具体工作线插件。
- 当前发布制品源码树 `21f6101dcda81b9aa01b56d5db1c22a750678d23`，基于 `bf98b4fee7824ff9da073d962bacf663a1e82a91` 未提交树构建，标签明确 `dirty-worktree`。
  本段属于构建后的人类文档更新，不改变镜像中的可执行树。未 Commit、Push、PR 或 Merge。
- 已部署至 `http://10.24.199.219:8003`，实际 HTTP、持久化、worker、重启和静态资源验收通过；详见
  [联调交付记录](../../integration/operation-integration-delivery-2026-09-06.md)。WMS 联合接受、真实 prepare 正向联调和具体工作线启动尚未完成。

## 19. face 长度收敛与联调升级（2026-09-06）

用户将 rack_face、target_face、arrival_face 及相关字段限定为 10 个字符以内，覆盖 §18 的长 face 方案。

- SDK、严格 DTO、Transport／WMS OpenAPI 和相关消费者统一为非空 1–10 个 Unicode 字符，保留原值，不 trim、截断或数值转换。
- 新 revision `3d040b37c049` 在锁定相关表并确认历史数据合法后，将六个字段改为 `VARCHAR(10)`；不修改已部署的历史迁移。
  来源唯一索引改为原文字段索引，查询保持精确比较和既有分批；共享 pgcrypto extension 仅为历史迁移保留。
- 主 Review CLEAR；QUALITY 2759 passed、5 skipped，干净环境 HEAVY 323 passed、0 skipped，聚焦 PostgreSQL／迁移 34 passed。
- 源码树 `ab43c0bae996a1238346ca650493a59626275c37` 已部署至独立 8003 联调实例；仍明确标记 dirty-worktree，未 Commit／Push／Merge。
  备份后完成迁移并重建四个应用进程，六个字段实际为 VARCHAR(10)，原数据库／Redis 与旧现场实例保持不变。
- 实际 10 字符通过 wire 校验、11 字符返回 422；历史请求完整重放响应及首次接收时间保持一致。
  详见 [联调交付记录](../../integration/operation-integration-delivery-2026-09-06.md)。WMS 联合验收仍需双方实际联调完成。

## 20. R2-B2 生命周期围栏前置切片（2026-09-06）

R2-B2 尚未整体完成。当前切片先修复宿主 PickingTask owner 的停用围栏，不将插件骨架描述为可启动业务。

- 已复现：prepare 得到 PREPARE_ACCEPTED、确认完成后，任务仍为 PREPARING，但旧 WorkLine 摘要漏掉任务，导致停用错误关闭 Epoch。
- WorkLine 统一未完成负载摘要纳入 PREPARING／EXECUTING 或带 plan blocker 的 PickingTask；picking-owned 未完成 WmsConfirmation
  与既有 material owner 一起计数。查询保持单条 SQL、准确数量与原身份样本，不引入插件判断、新状态或重试设施。
- START、配置更新／删除、停用与 prepare 准入继续复用该摘要；停用沿用 WorkLine → Epoch lifecycle fence，随后关闭 Epoch，未复制检查路径。
- 正常完成且无 blocker 的任务不阻塞；未绑定 QUEUED、其他工作线任务不污染；完成后的计划 blocker 与未闭合确认仍保留围栏。
- 真实 PostgreSQL 并发用例通过 pg_blocking_pids 证明停用等待绑定事务提交后读取新任务并拒绝关闭；原 WorkLine／Epoch／任务绑定保持不变。
- 代码与部署准备评审 CLEAR；QUALITY 2759 passed、5 skipped；本轮精确 selector 的四文件 HEAVY 15 passed、0 skipped。
  证据：`reports/r2b2-stop-red.log`、`reports/r2b2-quality.log`、`reports/r2b2-heavy.log`、`reports/r2b2-concurrent-stop.log`。
- 源码树 `72c7c62e564d49209a005a798dd82df4b0361775` 已部署至 8003，历史 issued／plan_delta 重放与 Transport 查询通过，无新增 migration。详细发布证据以 [联调交付记录](../../integration/operation-integration-delivery-2026-09-06.md) 为准。
- 后续仍需人工插件 START builder、固定 handler、事实/后继装配及其生命周期验收。不能复用自动出库字段或编造设备/位置绑定补齐人工语义；
  未批准的停线排空 operation 保持 BLOCKED。真实 prepare 联调需 WMS decisions 地址、认证与可用任务／工作线身份；这不阻碍本切片交付。

## 21. WMS Operation 优先实施：queue_changed（2026-09-06）

用户调整推进顺序：先实现 WMS Operation，随后统一安排联调服务器核实与验证。本轮不继续人工插件激活，也不要求当前提供现场配置。

- 新增 `outbound.picking_task.queue_changed@v1`，补齐 issued → queue_changed → prepare → plan_delta 的队列入口。
- 复用主合同 §7.1 的严格 DTO：连续 queue_revision，仅 QUEUED，dispatch_sequence／not_before 至少提供一项；省略保持原值，null 禁止，明确 0 可解除最早领取时间限制。
- 复用 InboundEvidence、冲突记录、已有任务身份／优先序锁和任务行锁；队列字段及版本同事务提交。无新实体、migration、operation 运行时或插件依赖。
- 保持成功 identity 永久重放、首次拒绝原因重放和同 identity 正文漂移冲突；与 issued 的优先序竞争由同一围栏处理，prepare 领取由任务行锁串行化。
- 严格 parser／Handler、唯一静态 Event route、OpenAPI 与组合根已接通。部署和 WMS 联合验收仍待后续统一执行。
- 当前源码快照 `05f1a9e4e64d2b3df0648b670ecf0277fb51bd3d` 独立实施评审 CLEAR；聚焦 FAST 409 passed，QUALITY 2812 passed、5 skipped，精确 selector 的 11 文件 HEAVY 58 passed、0 skipped。
- PostgreSQL 回归覆盖队列修改、幂等／拒绝重放、并发优先序竞争、状态拒绝、提交失败整体回滚及真实 ASGI 到数据库链路；HEAVY 同时覆盖 issued、prepare、plan_delta 和 Transport。
- 证据：`reports/queue-changed-evidence.json`、`reports/queue-quality.log`、`reports/queue-heavy.log`；本轮未部署、未执行远端验证。

## 22. WMS Operation 优先实施：退料货架到位上报（2026-09-06）

按主合同 §9.1.1 实现 `outbound.return_rack.arrival_report@v1` 的宿主能力，固定发送至 WMS `POST /api/v1/wes/facts`。

- 完整 typed intent、严格 wire 与封闭响应已接入静态 WmsConfirmation Adapter；`arrival_face` 保持 1–10 字符。
- 复用 WmsClient 单次发送和 WmsConfirmation 的事务、原身份重试、响应 Evidence 与确认闭合；未新增业务实体或 migration。
- PickingTask 的到位事实确认义务在 PREPARING、EXECUTING、EXECUTION_COMPLETED 中仍可派发，不以任务推进或 Epoch 关闭代替 WMS 确认；prepare 保持原 PREPARING owner 约束。
- 本切片只实现 operation 基础能力及既有可靠义务派发。插件仍须依据真实 Transport 成功结果、冻结目标和计划绑定，在业务事务中创建到位上报；本轮未接入该业务触发，不能据此声称物理到位流程完成。
- 本地真实 PostgreSQL／Redis／worker／HTTP 验证覆盖零插件既有义务派发、503 后原身份原正文重试及响应 Evidence 闭合。部署及 WMS 联合验证后续统一执行。
- 独立评审 CLEAR；最终 QUALITY 2888 passed、5 skipped；精确 selector 的 13 文件 HEAVY 83 passed、0 skipped，包含 issued／queue_changed／prepare／plan_delta／Transport 回归。
- 当前 18 文件快照与证据见 `reports/arrival-snapshot.json`、`reports/arrival-evidence.json`、`reports/arrival-quality.log`、`reports/arrival-heavy.log`。未提交、未部署、未访问联调服务器。

## 23. 未提交内容评审修复（2026-09-06）

- plan_delta 的 rack_id／slot_id 与公共业务编号正则统一，面向字段继续保持 1–10 个 Unicode 字符；避免计划接收的编号在到位上报中被拒绝。
- plan_delta／queue_changed 的 timestamp 与公共合同统一为非负 int64，OpenAPI 同步；issued 本轮未修改。
- prepare 与 arrival_report 复用单次有界收发检查及派发结果类型，各自保留固定路径、严格 parser 和业务响应解释；不新增动态 registry。
- plan_delta／queue_changed 复用纯 ACK 拼装与封闭映射，各自 parser、recorder 和业务事务保持独立。
- 4 项评审意见已闭环，独立复核 CLEAR。修复范围和当前内容指纹见 `reports/review-fix-scope.json`、`reports/review-fix-snapshot.json`；最终验证见 `reports/review-fix-evidence.json`。本轮不提交或部署。

## 24. WMS Operation 优先实施：入站批次（2026-09-06）

本切片按主合同 §9.2.1 补齐 `outbound.bin.inbound_batch@v1` 的宿主 operation 能力，固定调用 WMS `POST /api/v1/wes/decisions`。

- 请求冻结 task_id、rack_id、rack_face 和 1–4 的 max_bin_count；面向字段保持 Unicode 1–10 字符。
- 封闭响应为 READY、NO_BATCH、RACK_FACE_DONE；READY 必须满足请求数量、来源 rack/face 与成员唯一性。
- 复用已有单次有界收发、PickingTask-owned WmsConfirmation、响应 Evidence 与可靠派发。NO_BATCH 是本次请求的确定业务结果，等待时间保存在响应中，不触发 MaterialExecution 后继调度。
- 插件仍负责来源面真实到位、CTU/缓存容量、退箱优先级、跨批次成员历史及后续 Transport；本轮不接入业务调度，不部署或访问联调服务器。
- 聚焦验证 586 passed；最终 QUALITY 2980 passed、5 skipped；精确 selector 的 13 文件 HEAVY 71 passed、0 skipped。真实 PostgreSQL／Redis／worker／HTTP 验证确认 NO_BATCH 和 RACK_FACE_DONE 持久化响应 Evidence 并闭合义务，不产生自动重复请求。
- 独立评审 CLEAR，当前 17 文件快照无漂移，无关既有改动保持原指纹。范围与验证记录见 `reports/batch-scope.json`、`reports/batch-snapshot.json`、`reports/batch-evidence.json`；未提交、未部署，WMS 联合验证后续统一安排。

## 25. Operation 公共能力精简（2026-09-06）

- WMS Adapter 统一使用中立 `WmsDispatchCode`／`WmsDispatchResult` 与单次有界 `receive_json`，旧域内类型与路径直接移除；各 operation 保留封闭响应解释，Material WAIT 与 Picking NO_BATCH 的后继语义保持独立。
- Picking 错误 DTO 和 face 类型复用，prepare 拒绝显式 null 与非法 JSON Pointer；响应序列化保留字段省略语义，Adapter→typed outcome 回归已覆盖。
- 公共收发测试集中，三份真实 worker 测试共用数据库／HTTP／owner 装配；既有可靠派发测试迁至 `tests/runtime/execution/test_wms_confirmation_dispatch.py`，不保留旧路径或转发 import。
- prepare 已完成过程计划移出项目归档，当前 Coordinator/Policy 边界仍以第 17 节为准，索引与引用已修正；硬件原始资料保持不变。
- 本轮源码和测试净减少 227 行。独立评审及修复闭环 CLEAR；最终聚焦验证 854 passed，QUALITY 2990 passed、5 skipped，11 文件 HEAVY 63 passed、0 skipped。
- 当前快照与证据见 `reports/compact-snapshot.json`、`reports/compact-evidence.json`。未提交、未部署，未执行联调服务器验证。

## 26. 批次重复求值的持久化围栏修复（2026-09-06）

- 继续实施前发现旧 `(picking_task_id, operation)` 唯一索引误将所有 Picking operation 限制为每任务一次，导致 inbound_batch 在 NO_BATCH 后使用新 identity 的合法请求落库失败；真实 PostgreSQL 已复现。
- 迁移 `5d3e6e4df5be` 将任务唯一索引限定为 `outbound.picking_task.prepare@v1`；全局 `(operation, operation_id)` 唯一约束保持，prepare 仍不得为同一任务创建第二条义务，批次允许使用新 identity。
- 本轮只修复既有 operation 的持久化阻塞。下一项 `outbound.bin.return_batch@v1` 的合同属于跨 PickingTask 的 Epoch FIFO，现有 WmsConfirmation 仅有 MaterialExecution／BinExecution／PickingTask owner，尚不能正确承载该义务。后续须先完成共享 Epoch owner 的模型、生命周期围栏、Evidence 与派发接入，禁止借用任意候选的 PickingTask 或复制一套可靠机制；本轮未实现 return_batch。
- 新鲜临时 PostgreSQL 的批次及 prepare 唯一性回归通过；schema owner 验证新 HEAD／索引 predicate，并通过 `alembic check`。独立复评 CLEAR；最终 QUALITY 2992 passed、5 skipped，精确 7 文件 HEAVY 40 passed、0 skipped。
- 快照与证据见 `reports/batch-repeat-snapshot.json`、`reports/batch-repeat-evidence.json`。未提交、未部署，联调服务器验证后续统一安排。

## 27. WMS Operation 优先实施：Epoch 退箱批次（2026-09-06）

- 已实现 `outbound.bin.return_batch@v1` 的 typed SDK、严格 wire、固定 Adapter 和可靠派发，调用 WMS `POST /api/v1/wes/decisions`。候选为 1–4 个连续编号且唯一的 Bin；READY 只接受候选 FIFO 前缀，目标必须匹配冻结 rack/face，储位不得重复；face 保持 1–10 字符。
- 共享 WmsConfirmation 增加 Epoch owner，迁移 `5098dc1b2b63` 保持四类 owner 恰选一及公开 operation identity 唯一。创建、派发和响应处理均必须绑定活动 Epoch；关闭前必须排空业务及物理执行并闭合可靠义务。异常关闭后的义务保留原身份进入对账，禁止继续派发或转交新 Epoch。未闭合义务进入 Epoch 关闭、WorkLine 未完成负载与活动对象围栏。
- 响应 Evidence 绑定 Epoch。READY／NO_BATCH 均结束本次义务，NO_BATCH 不进入 MaterialExecution 后继队列；未来重求值时机、真实 FIFO 候选、容量、Transport 及物理闭合由插件负责，本切片不实现业务触发。
- inbound／return 复用 `BinBatchNoBatch`，旧 SDK 名称直接移除；Picking outcome 共用严格 JSON Pointer 校验。独立评审发现的 ORM 缓存竞争已通过双事务 RED→GREEN 修复，锁定读取刷新 Epoch 状态，禁止并发关闭后新建义务。
- 独立复评 CLEAR；聚焦验证 762 passed，QUALITY 3018 passed、5 skipped。精确 selector 的 23 文件 HEAVY 145 passed、0 skipped，包含新鲜库迁移与 `alembic check`；证据见 `reports/return-evidence.json`，范围和源码指纹见 `reports/return-scope.json`、`reports/return-snapshot.json`。
- 后续简化已移除 `require_active` 模式开关，所有 Epoch owner 校验统一要求 ACTIVE。正常 READY／NO_BATCH 使用真实 HTTP＋共享 service 验证；异常 CLOSED 由零插件真实 worker 验证拒绝发送。独立评审 CLEAR；本次 QUALITY 3017 passed、5 skipped，精确 15 文件 HEAVY 89 passed、0 skipped，证据见 `reports/epoch-simplify-evidence.json`。
- 未提交、未部署、未访问联调服务器；后续统一安排 WMS 联合核实与验证。

## 28. WMS Operation 优先实施：Bin 工作计划（2026-09-06）

- 按主合同 §9.3 实现 `outbound.bin.work_plan@v1`：不可变 SDK intent/outcome、严格 wire、静态 Adapter 和 PickingTask owner 校验；固定调用 WMS `POST /api/v1/wes/decisions`，扫码时间原样冻结，不用发送时间替代。
- `READY` 只接受非空且唯一的 Cell；`NO_WORK` 禁止附加业务字段；`WAIT` 保留等待参数并结束当前可靠义务，不进入 MaterialExecution 后继队列，也不自动生成新 identity。共享 HTTP、Evidence、重试与持久化机制保持复用，无新增模型或 migration。
- 只允许已绑定 WorkLine/Epoch 的 EXECUTING PickingTask 派发。插件仍负责扫码触发、预期 Bin 匹配、最终计划唯一性、WORK_BUFFER FIFO、Cell 内 LIFO 和后续设备动作；本切片不能证明这些业务流程已经实现。
- 主合同 §9.3 的 `bin_id` 字段表与同节正文统一：可识别但不匹配的 Bin 保存证据并冻结等待恢复，不自动进入退箱 FIFO。
- TDD 已覆盖新增能力缺失到通过；聚焦回归 1157 passed，独立只读评审 CLEAR。最终 QUALITY 通过（FAST 3051 passed、5 skipped）；精确 selector 的 15 文件 HEAVY 77 passed、0 skipped，覆盖零插件真实 worker/HTTP 的三类结果持久化、既有 issued/prepare/plan_delta/Transport 回归。
- 当前执行快照与验证记录见 `reports/work-plan-snapshot.json`、`reports/work-plan-evidence.json`、`reports/work-plan-quality.log`、`reports/work-plan-heavy.log`。未提交、未部署、未访问联调服务器；WMS 联合核实与插件业务验收后续统一安排。

## 29. WMS Operation 优先实施：货架离场决策（2026-09-06）

- 按主合同 §9.4 实现 `outbound.rack.departure_decide@v1`：不可变 SDK intent/outcome、严格 wire、静态 Adapter 和 PickingTask owner 校验；固定调用 WMS `POST /api/v1/wes/decisions`。请求不携带 rack_role，face 保持 1–10 字符，READY 的目的地必须不同于请求中的当前位置。
- 允许 EXECUTING 和 EXECUTION_COMPLETED 任务请求离场，不重开任务。READY／WAIT 均结束本次可靠义务；WAIT 保留等待参数，后续新 identity 求值由插件触发，不进入 MaterialExecution 后继队列。
- 复用共享 WmsConfirmation、HTTP、Evidence 和严格 RackPosition；arrival_report 的旧位置类直接移除，不保留别名或双通道。无新增模型或 migration。
- 插件仍负责真实位置、未闭合设备动作／PUT／报告、CTU 与本地使用条件，以及 READY 后唯一 Transport 的创建和物理完成；本切片不实现业务触发，也不把 READY 当作搬运完成。
- TDD 覆盖能力缺失与 owner 状态，聚焦回归 1198 passed。独立评审发现的共享持久化 HEAVY 消费者映射遗漏已修复，selector 回归 180 passed，复评 CLEAR。最终 QUALITY 通过（FAST 3092 passed、5 skipped）；最终门禁与快照记录见 `reports/departure-evidence.json`、`reports/departure-snapshot.json`。
- 精确 selector 的 16 文件 HEAVY 80 passed、0 skipped，包含零插件真实 worker／HTTP 的 READY／WAIT 持久化、完成态任务离场，以及既有 issued／prepare／plan_delta／Transport 回归。映射修复前后本轮 manifest 与生产／HEAVY 资产未变，复用该有效执行证据。
- 未提交、未部署、未访问联调服务器；WMS 联合核实与插件业务验收后续统一安排。

## 30. WMS Operation 优先实施：出库物料决定（2026-09-06）

- 按主合同 §10.2 实现 `outbound.material.decide@v1` 的不可变 SDK、严格 wire、静态 Adapter 和 EXECUTING PickingTask owner 接入，固定调用 WMS `POST /api/v1/wes/decisions`。
- 来源为 `RACK_SLOT` 或 `BIN_CELL`，复用同域货架位置合同。出库六合一码保留 HHPN、MfrPN、Qty、DateCode、LotCode、PkgID 的 1–256 字符原文，不套用字段不同的入库六合一码，也不展开数量的 exponent 或解析日期。
- ACCEPT 提供唯一 SLOT 和来源动作，物理准备省略／ROTATE／REPLACE 三选一；REPLACE 必须带旧架离场目的地。REJECT 的 Cell 不匹配固定 CLOSE；DirectPick 只允许 SOURCE_DONE 或 MATERIAL_REJECTED／CLOSE。非法分支、字段漂移和响应 identity 不匹配保留响应证据并进入对账。
- ACCEPT／REJECT／WAIT 均闭合本次可靠义务；WAIT 不触发 MaterialExecution 后继调度。插件仍负责锁定来源匹配、扫码台单盘串行、同一盘最终决定唯一性、业务重求值，以及换面／换架／PUT／NG 动作和物理完成。
- 聚焦回归 1268 passed；独立只读评审 CLEAR。最终 QUALITY 通过（FAST 3151 passed、5 skipped）；精确 selector 的 17 文件 HEAVY 83 passed、0 skipped，包含零插件真实 worker／HTTP 下 ACCEPT／REJECT／WAIT 的持久化闭合，以及既有 issued／prepare／plan_delta／Transport 回归。
- 无新增数据库模型或 migration，无兼容入口。共享 WmsConfirmation、HTTP、响应 Evidence 与既有测试装配保持复用；当前切片验证记录见 `reports/material-evidence.json`，变更面与快照见 `reports/material-scope.json`、`reports/material-snapshot.json`。
- 未提交、未部署、未访问联调服务器；WMS 联合核实与插件业务验收后续统一安排。

## 31. WMS Operation 优先实施：确定空取决定（2026-09-06）

- 按主合同 §12.2 实现 `outbound.source.empty_decide@v1` 的不可变 SDK、严格 wire、静态 Adapter 和 EXECUTING PickingTask owner 接入；固定调用 WMS `POST /api/v1/wes/decisions`。复用现有 `RACK_SLOT`／`BIN_CELL` 来源类型，原样冻结设备结果的 `observed_at`，不以发送时间替代。
- 封闭结果为 RETRY／WAIT／SOURCE_DONE，禁止嵌入替代来源或分支外字段。三类业务决定均闭合当前可靠义务；业务 RETRY 不走 HTTP 技术重试，WAIT 的等待参数保存在结果中，不触发 MaterialExecution 后继调度。
- 插件负责确定空取证据、锁定来源匹配、RETRY 后再取原位置、WAIT 后新 identity 求值，以及 SOURCE_DONE 后来源关闭与最终决定唯一性；不以请求成功或 ACK 证明设备无料，不新增空取状态机、资源锁或替代来源机制。
- 聚焦回归 1316 passed，selector 196 passed；独立只读评审 CLEAR。最终 QUALITY 通过（FAST 3191 passed、5 skipped）；精确 18 文件 HEAVY 86 passed、0 skipped，覆盖零插件真实 worker／HTTP 的三结果持久化与无再次 HTTP／Transport／DeviceCommand，以及既有 issued／prepare／plan_delta／Transport 回归。
- 无新增数据库模型、migration 或兼容入口。当前范围、源码指纹和验证记录见 `reports/empty-scope.json`、`reports/empty-snapshot.json`、`reports/empty-evidence.json`。
- 未提交、未部署、未访问联调服务器；WMS 联合核实与插件业务验收后续统一安排。

## 32. Operation 发布前收敛（2026-09-06）

本次交付统一 WMS 料箱业务字段为 `bin_code`，移除 NG 出口上报及专属 owner/SDK/Adapter/测试。
Transport 对外 `container_id` 与内部 BinExecution 暂不调整；完整生命周期退役按独立简化 SPEC 推进。
旧 NG 实施过程已移出项目，不能复用其历史测试数量作为本次交付证据。最终验证以当前提交快照为准。

## 33. WMS Operation 继续实施：单盘放置结果（2026-09-07）

- 基于 `develop@cdbc63f0`，按主合同 §12.4–12.5 实现 `outbound.material.movement_report@v1` 的不可变 SDK intent/outcome、固定 facade、严格 wire 和静态 Adapter，调用 WMS `POST /api/v1/wes/facts`。
- 来源复用 `RACK_SLOT | BIN_CELL`，去向为 `RACK_SLOT | NG_ZONE`；`PkgID` 保持 1–256 字符扫码原文，`occurred_at` 保持设备完成时间。禁止附加六合一码、设备命令编号或重复业务异常分类。
- `RECORDED | DUPLICATE` 闭合本次可靠义务；未知响应和 `UNAVAILABLE` 复用现有同身份、同正文重试，冲突或非法响应保留证据并进入对账。既有 PickingTask-owned 放置事实义务允许在 EXECUTING／EXECUTION_COMPLETED 阶段派发，不重开任务。
- 插件仍负责匹配前序最终物料决定与设备 `SUCCEEDED` 证据、创建报告及确认后的来源／容量释放；本切片不实现这些业务触发，不把 WMS ACK 当作设备完成。无新增模型、migration、worker 或兼容入口。
- RED→GREEN 与聚焦回归 1295 passed；独立只读评审 CLEAR。最终 QUALITY 通过（FAST 3241 passed、5 skipped）；精确 selector 的 19 文件 HEAVY 89 passed、0 skipped，覆盖正常 PUT／NG 的零插件真实 worker／HTTP、响应 Evidence 持久化、任务阶段保持及无重复发送。
- GitNexus 增量分析失败且目标 impact 不可用，本轮以精确调用点、直接／间接测试及 HEAVY owner 完成影响核对。快照和证据见 `reports/movement-snapshot.json`、`reports/movement-evidence.json`。代码已随 `32f32783` 提交并推送，版本为 `0.33.1.0`；未部署、未访问联调服务器，WMS 联合验收与插件业务实施另行推进。

## 34. WMS Operation 继续实施：PickingTask 完成确认（2026-09-07）

- 按主合同 §13 接入 outbound.picking_task.completion_confirm@v1：严格请求、三个封闭业务结果、不可变 SDK intent/outcome、固定 facade 与静态 Adapter，复用 WMS decisions 端点和 WmsConfirmation 可靠机制。
- 请求携带 task_id 和 last_applied_plan_revision，允许尚无计划的 revision 0。PLAN_REVISION_STALE 必须携带高于请求的 current_plan_revision；三个业务结果均结束当前可靠义务，重求值使用新 identity。
- PickingTask owner 允许 PREPARING／EXECUTING；插件负责检查 prepare 成功与首批等待期限、本地业务义务闭合、无待应用计划及必须的物理结果确认。基础 Adapter 不扫描历史结果、不推进任务状态、不释放物理资源。
- Swagger 显示正常 COMPLETED 完整响应，等待和错误说明保持简要。无新增数据库模型、migration、worker 或兼容入口。
- RED→GREEN：新增合同测试 30 passed；聚焦领域回归 1329 passed，selector 排序期望修正后 207 passed。独立只读 Review CLEAR；最终 QUALITY 通过（FAST 3275 passed、5 skipped），20 文件 HEAVY 95 passed、0 skipped，覆盖两种任务阶段下三个业务结果的真实 worker／HTTP／Evidence 持久化及无重复派发。
- GitNexus 找不到当前符号，已使用精确调用点、测试 owner 和 mapping 核对。快照与证据记录在 reports/completion-snapshot.json、reports/completion-evidence.json。代码已随 `32f32783` 提交并推送，版本为 `0.33.1.0`；未部署，插件业务触发与 WMS 联合验收不在本切片。
- 本次 Ship 最终快照：QUALITY FAST 3275 passed、5 skipped；23 文件 HEAVY 183 passed、0 skipped；Swagger/API 聚焦回归 65 passed，独立审查 CLEAR。Swagger 的 7 个入站请求示例覆盖全部 6 种 Operation，并展示 10 种出站 Operation 的正常业务响应；真实浏览器已逐项验证示例切换，未向共享联调库发送模拟物理事实。最终日志见 `reports/ship-code-commit.log`、`reports/ship-heavy.log` 和 `reports/qa-report-localhost-8001-2026-09-07.md`。

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
| --- | --- | --- | --- | --- | --- |
| CEO Review | `/plan-ceo-review` | 产品/范围重审 | 0 | NOT REQUIRED | 既定后端合同、可靠机制与插件 SPI 收敛，不改变产品方向 |
| Codex Review | 独立只读子 Agent | 独立工程复核 | 1 | CLEAR | 4 个 P1 与主评审一致；新增 1 个 P2 测试 owner 问题已闭合 |
| Eng Review | `/plan-eng-review` | 架构、代码质量、测试、性能 | 当前计划 3 | CLEAR (PLAN) | 当前轮 11 项全部纳入；0 unresolved、0 critical |
| Design Review | `/plan-design-review` | UI/交互 | 0 | NOT REQUIRED | 纯后端合同与运行机制，无页面变化 |
| DX Review | `/plan-devex-review` | 开发者体验 | 0 | NOT REQUIRED | 复用现有 SDK、WmsConfirmation、WmsClient、静态 composition 和测试 owner |

**CODEX:** 独立复核验证了 Operation/插件激活耦合、generic escape hatch、prepare 所有权倒置和永久阻塞风险；测试 owner 清理问题已补入 R3-B/G26。

**VERDICT:** ENG + CODEX CLEARED（2026-09-06，设计评审时点）。当时 develop 基线的 11 项发现均已转为实施要求；该 verdict 仅证明计划通过评审，后续实施与验证见第 15–17 节。

评审测试清单：`/Users/kaizhou/.gstack/projects/kaizhoumasha-wes_backend/kaizhou-develop-eng-review-test-plan-20260905-235824.md`。
实施任务产物：`/Users/kaizhou/.gstack/projects/kaizhoumasha-wes_backend/tasks-eng-review-20260905-235824.jsonl`。

NO UNRESOLVED DECISIONS
