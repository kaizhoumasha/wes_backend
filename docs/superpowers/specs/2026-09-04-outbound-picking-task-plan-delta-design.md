# `outbound.picking_task.plan_delta@v1` 计划增量设计

status: Approved（WES 设计；非 WMS 联合合同批准或生产激活）
created_at: 2026-09-04
scope: Phase 12 人工拣料单箱正常闭环的计划增量持久化暗构建；不包含生产激活、Transport 或物理执行

## 1. 目标

WES 在已经可靠接收 PickingTask、冻结匹配的 WorkLine/Epoch，并取得 WMS 明确
`PREPARE_ACCEPTED` 后，严格校验并原子应用 `outbound.picking_task.plan_delta@v1`。若匹配的 prepare 尚未超期且结果仍未确定，
允许先暂存回调证据，但不接纳该 revision、不推进任务、不触发执行。

本切片完成计划身份、连续版本、初始接料货架面和新增来源的持久化。首批计划应用成功后，PickingTask 从
`PREPARING` 进入 `EXECUTING`。它不创建 `TransportTask`、`BinExecution`、`DeviceCommand`，也不打开生产入口。

首轮联调由 WMS 配置约束为一个接料货架面、一个五层来源货架面和后续单箱数据，不发送直接取料明细。
这是测试数据约束，不是正式 wire 限制。

## 2. 合同边界

- operation 固定为 `outbound.picking_task.plan_delta@v1`，不增加人工专用别名或兼容字段。
- 正式 DTO 保留出库合同定义的完整能力：一个初始 `target_rack`，以及条件可选的 `added_bin_source_racks`、
  `added_direct_picks`；任一数组出现时必须非空。revision 1 可以只有接料货架面，后续版本至少新增一种来源。
- `plan_revision` 在同一 `task_id` 下从 `1` 连续递增。WMS 必须在前一版本得到明确成功响应后再发送下一版本。
- revision 1 必须且只能携带一个 `target_rack`；revision 2 及以后禁止携带 `target_rack`，并必须新增至少一个来源。
- `plan_delta` 不选择具体 `bin_id`。具体 Bin 由后续 `outbound.bin.inbound_batch@v1` 冻结。
- 计划持久化对 `MANUAL | AUTO` 保持中立；`task_type` 只参与前序任务领取与 WorkLine 准入，不分叉同一个
  operation 的 revision、幂等或来源模型。
- WMS 拥有任务、版本和资源计划；WES 拥有本地持久化、顺序校验和执行准入；ECS/PLC 不参与本 operation。

出库主合同整体仍为 `ReviewRequired`。本设计获批冻结本切片的 WES 实施选择；实施时将本文的 `plan_delta` 特定约定同步到
主合同对应小节和独立 schema，不改其它 operation。WMS 联合合同/fixture 确认必须另有证据，不由本次 WES 评审推定。

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
  同任务及冻结 WorkLine/Epoch 的 prepare 尚未超期、结果仍未确定时，按第 5 节暂存并等待原请求重试；
- 首个 revision 原子应用后进入 `EXECUTING`；
- `EXECUTING` 只接受严格的下一 revision；
- `EXECUTION_COMPLETED` 不接受新的计划。

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

计划证据使用 `WMS_EVENT`，`line_run_epoch_id`、`material_execution_id`、`transport_task_id` 均保持为空。
任务与来源通过上述 Evidence 外键追溯，Epoch 由 PickingTask 冻结绑定提供；不得为方便查询向 Evidence 填 Epoch，
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
  -> [后续激活] 唯一 Event route：鉴权、限流量/正文大小、静态分派；不访问 DB
  -> 独立 plan_delta handler：严格 JSON/DTO 校验
  -> Service：开始一个事务
       -> EvidenceService：锁 operation identity、保存/核对完整载荷
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

事务内不调用 WMS、broker、Transport、ECS 或其它外部系统。任何一项校验失败时，整个 revision 不产生部分计划成员写入。
确定冲突的证据与阻塞指针属于诊断/控制事实，应一同提交；SQL/flush/commit 失败则整个事务回滚，不能在失效事务中补写成功或冲突 ACK。
handler 在本轮只由测试直接调用；图中的生产 route 仍不接入。

### 4.1 锁与 prepare 并发

统一锁顺序为 plan_delta Evidence identity → 可信任务行。同任务所有版本和阻塞设置由同一任务行锁串行化；不同任务互不使用全局锁。
锁内只以普通查询读取匹配的 prepare confirmation 及其响应证据，不再申请 confirmation 行锁：现有 dispatcher 的顺序是
confirmation → PickingTask，反向加锁会形成死锁。若并发响应尚未提交，只读到原来的未定状态并返回 503，允许下次重试。
只接受唯一匹配 task、prepare operation、冻结绑定且状态为 `COMPLETED`、响应为 `PREPARE_ACCEPTED` 的确认；零条、多条或证据不匹配
均不当作成功。成功确认不可被回调改写；超期门禁针对尚未成功确定的 prepare，不让后来重试把已持久化的有效成功改成超期。
生产激活时 STOP/Epoch 关闭必须与任务准入一致串行化，并保留未完成任务围栏；本轮不改该生产生命周期。

## 5. 幂等、冲突与失败

- 相同 `operation_id` 和相同完整正文重放：只有首次已成功应用的请求才返回 `DUPLICATE`，不重复创建成员；
  `PENDING` 必须重新检查前置条件，已确定的冲突不得改报成功。
- 相同 `operation_id` 但正文变化：返回 `CONFLICT`，保留首次证据和业务结果。
- 当前 revision 使用新的 `operation_id`、但完整业务内容与已应用 revision 相同：保存本次 Evidence，返回
  `200 / DUPLICATE`，不重复应用；内容不同则进入 `RECONCILING`。
- revision 1 先于 prepare 响应落库到达，且任务、冻结 WorkLine/Epoch 与 prepare 匹配、prepare 尚未超期且结果仍未确定：
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
- `PENDING` 暂存本身不设置计划冲突指针；阻塞后的新请求即使 revision 正确，也不能绕过既有阻塞应用计划。
  不自动清空指针，不通过新 operation ID、改 revision 或改任务阶段解锁。本切片不提供解除入口；后续解除须有明确获批的对账流程、
  匹配证据及任务锁内校验，不仅凭 HTTP 成功或人工改状态。
- 数据库、事务或证据保存失败：不返回成功 ACK；调用方只能重试原 identity 和原正文。
- 未获得匹配的权威结果前，不通过修改 revision、operation ID 或任务状态绕过冲突。

### 5.1 精确重放与 ACK

- 同 operation ID 比较完整信封的规范化 JSON（包含 timestamp、operation 和 data）；复用 Evidence 的现有摘要能力。
  DTO 导出保留字段是否出现的语义，不注入显式 null；对象键顺序/空白不参与比较，数组顺序、大小写和所有业务值参与比较。
- 新 operation ID 的同当前 revision 业务重复，只比较完整 `data` 与 `last_plan_evidence_id` 指向的首次载荷；
  不比较新信封的 operation ID/timestamp，不排序数组、不删除字段、不新增摘要副本或历史 revision 表。
  该重复 Evidence 记为 `APPLIED`，但 initial/last 和成员原始指针不移动。更早 revision 换新 ID 仍属于版本倒退。
- 先判断已成功的精确重放，再判断当前阶段/阻塞，避免任务已推进或结束后将历史成功改报冲突。
  尚未成功的 `PENDING` 不享有这一成功重放分支；新 ID 的业务重复不得解除现有阻塞。
- 确定拒绝重放必须维持原拒绝原因。复用已有 `InboundEvidenceConflict.reason_code` 保存领域拒绝依据，
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
| 可关联的非法 DTO/未知 operation | `422 / REJECTED` | `INVALID_DATA` / `UNSUPPORTED_OPERATION`，按统一错误 data 格式 |
| prepare 暂未确定或存储/处理失败 | `503 / UNAVAILABLE` | `{}` |

正文超过现有 256 KiB 上限返回 413；严格 JSON/UTF-8/信封身份尚不可关联的错误沿用现有 400 空响应，不伪造 operation ID。
以上信封均保留原 operation ID，timestamp 用统一时区工具。数据库异常后不能声称 Evidence 已保存；只能返回 503 并允许原请求重试。

## 6. 暗构建与生产激活

本切片实现 DTO、独立 OpenAPI schema、模型、Repository、Service、migration 和聚焦测试。独立 schema 只做合同验证，
不加入公开 `WMS_EVENT_REQUEST_SCHEMA` 的 `oneOf`；公开 OpenAPI 与 production event handler 留到同一生产激活切片接入。
本切片不注册：

- production event route；
- Celery task、Beat schedule 或 worker hook；
- WorkLine START/STOP composition；
- Transport 或设备后继调度。

生产激活必须与真实 `manual_bin_processing` WorkLine START、prepare production route、PickingTask STOP/Epoch blocker
和 PickingTask confirmation owner 的 worker wiring 在后续原子切片共同完成。单独打开 `plan_delta` route 会允许 WMS 提交 WES
无法继续执行的计划，因此禁止。
该激活切片还必须让新动作准入、完成和资源释放路径检查任务的计划阻塞；已有动作结果接收路径不得被这一准入检查拦截。

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
  改绑、未知任务不污染其他任务、迟到消息不重开已结束任务；验证 `PENDING` 不设置冲突指针和事务回滚不留下半个阻塞事实。
- 架构 guardrail：暗构建不得出现在公开 OpenAPI、production route、Celery/Beat 或插件 runtime 中。
- migration、生产模块和新增 HEAVY 资产必须在 `docs/architecture/heavy-test-impact.toml` 中具有精确映射。

## 9. 验收标准

1. revision 1 原子保存唯一接料货架面和全部新增来源，并将任务迁移为 `EXECUTING`。
2. 后续 revision 只能连续追加，不修改既有接料货架面或来源。
3. 同一来源身份在并发或重放下最多产生一条业务记录。
4. 任一成员冲突会使整个 revision 回滚，不出现部分应用。
5. 完整原始消息和业务成员可以通过 Evidence 追溯。
6. revision 2 及以后到达后，初始接料货架面仍通过 `initial_plan_evidence_id` 直接追溯到 revision 1。
7. 首轮单来源、无直接取料 fixture 通过，但正式 DTO 不把这一联调限制写死。
8. 本切片没有 production route、Celery/Beat、TransportTask、BinExecution 或 DeviceCommand 副作用。
9. prepare 尚未超期且结果未确定时，先到的匹配回调只保存 `PENDING` 并返回 `503`；确认成功后，原请求重试可原子应用，
   不会因正常时序竞争永久冲突，也不会把尚未应用的消息误报为 `DUPLICATE`。
10. 确定计划冲突通过现有 PickingTask 的首次阻塞证据指针持续阻止新计划应用；本切片不自动解除，不改变原执行阶段或既有物理事实。

## 10. 未包含内容

- `outbound.picking_task.queue_changed@v1`；
- `outbound.bin.inbound_batch@v1` 和具体 Bin 选择；
- 货架、Bin、CTU、滚筒线及人工工作位物理执行；
- point2 人工准入、PDA 完成、释放与应用结果报告；
- RETURN_BUFFER、退箱回库、NG、换面换架、任务完成确认；
- 部署、真实 WMS 联调和现场业务验收。

上述项目各自涉及尚未闭合的执行或供应商合同，不为本次计划持久化预埋兼容层、空消费者或通用恢复框架。
生产激活复用 `TODOS.md` 的“人工 PickingTask 自动准备生产激活”，不新增同义 TODO；本设计第 6 节作为其具体验收依赖。
解除计划阻塞归后续获批对账流程，未定义解除方式前保持阻塞，不以临时管理 API 补洞。

## 11. 复用、组织与性能

| 已有能力 | 本切片用途 | 不做什么 |
| --- | --- | --- |
| `wms_adapter/strict_json.py`、`wire_common.py` | 严格 JSON、256 KiB 上限、UUIDv7、int64、闭合 DTO | 不复制解析器，不放宽 issued/prepare |
| `wms_adapter/outbound_picking/` | 新增同域 plan_delta DTO、独立 schema、独立 handler | 不抽取仅有单一消费者的通用 operation 框架 |
| `InboundEvidenceService.accept/record_conflict` | 原身份留证、载荷比较、冲突原因 | 不新增 inbox、响应缓存或 payload 表 |
| `PickingTaskRepository` | 精确任务行锁、当前版本和首次阻塞 | 不扫描全部任务、不新增分布式锁 |
| prepare confirmation owner 与响应证据 | 只读判断准备是否明确成功 | 不重发 prepare、不模拟接受响应、不改共享 dispatcher |
| 现有 PostgreSQL 临时库与迁移支持 | 约束、事务、并发与迁移链验证 | 不在共享开发库执行迁移验收 |

新增代码只进入现有 outbound_picking 两个域目录：wire/handler/schema 在 `wms_adapter/outbound_picking/`，
模型/Repository/Service 在 `wms_integration/outbound_picking/`。Service 和模型按既有 `__init__.py` 规则导出，
但不修改部署装配以接入暗构建 handler。Service 保留第 4 节的短流程注释；PickingTask 只注释“阶段与阻塞正交”，不复制整篇设计。

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

框架：pytest/pytest-asyncio。下列是计划覆盖的 18 组路径；目前没有 plan_delta 实现或测试，18 组均为待实现 GAP，
不是 18 个已证实的生产故障。既有 issued/prepare 测试只提供模式和回归基线，不能算作 plan_delta 覆盖。

```text
代码路径                                     调用方流程
handler
  |-- G01 身份/UTF-8/JSON/大小错误             WMS 错请求 -> 400/413/422，无业务写入
  |-- G02 revision/数组/locator/边界校验       单目标、单来源、多来源、直接取料均按合同
  `-- G03 DTO 与独立 schema 一致              公开 API 仍不接受本 operation
Service 单事务
  |-- G04 首批计划/后续连续版本                WMS 发下一批 -> 提交后才得到 202
  |-- G05 同 ID 已成功重放                     响应丢失 -> 原请求 200，无重复成员
  |-- G06 同 ID 改内容                         409，首次内容不变
  |-- G07 新 ID 同当前版本业务重复             200，原始追溯指针不移动
  |-- G08 跳号/倒退/同版本异内容               409，整个版本不应用
  |-- G09 来源冲突/无效引用                    409，无部分成员
  |-- G10 未知/QUEUED/已结束/绑定不匹配        拒绝，不改绑、不重开任务
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
```

所有者（以下新增文件是实施目标，并非已存在产物）：

| 路径组 | 主要测试文件 | 关键断言与失败表现 |
| --- | --- | --- |
| G01–G03 | `tests/contracts/wms_adapter/outbound_picking/test_plan_delta_wire.py`、`test_plan_delta_event_handler.py` | 真/假值冒充整数、null、未知字段、空数组、错误 locator、超长/超范围、重复 JSON key；非法输入不调用 recorder，schema 与 DTO 对同一 fixture 一致 |
| G04–G12 | `tests/contracts/wms_adapter/outbound_picking/test_plan_delta_service.py` | fake 端口验证全部分支、顺序、ACK/原因码、首次指针、MANUAL/AUTO 中立；不能把 fake 测试当事务证明 |
| G13–G15 | `tests/integration/wms_adapter/outbound_picking/test_plan_delta_postgresql.py` | 提交前响应不可成功；中途/提交失败无半批；独立连接并发、响应丢失重放、prepare 持 confirmation 锁等待任务时不死锁 |
| G16 | 现有 `tests/integration/wms_adapter/outbound_picking/test_schema.py` + 新增 PostgreSQL 文件 | 真实 CHECK/FK/唯一性、旧任务 revision 0、base→head 与空库→head；复跑 issued/prepare PostgreSQL 回归 |
| G17 | 新增 `test_plan_delta_postgresql.py` | 直接调用真实 Evidence candidate query，计划 Evidence 不被领取，合法 MaterialExecution Evidence 仍可领取；不重复通用内核全套测试 |
| G18 | `tests/architecture/test_outbound_picking_plan_delta_activation_guardrail.py`、现有 `tests/api/test_wms_events.py` | 检查公开 schema 无此 oneOf、生产 route 对该 operation 仍拒绝，main/celery_worker/deployment/src/workline_plugins 无生产激活；保留 issued/prepare 原行为 |

G18 必须覆盖实际进程根 `main.py`、`celery_worker.py` 和 `deployment/`；不能照抄只扫描 main/src/plugins 的旧 guardrail。
API 只通过 ASGI 测试，不打开浏览器或真实生产入口。已有动作结果不会被阻塞的执行路径测试属于后续激活/插件切片，
本轮只验证 plan_delta 没有注册该类拦截器，不声称已完成物理链路验收。

现实失败方式与可见结果已逐组列在图和表中：格式/引用错误明确拒绝，状态/版本冲突留证阻塞，暂时失败原请求重试，
事务失败不返回成功；G14–G17 不允许以 Mock 替代真实数据库。设计层没有剩余“无测试要求、无错误处理且静默”的关键路径。

## 13. 实施与验证任务

本轮仅评审文档，未执行以下任务。所有任务触及同一个 outbound_picking 域，顺序实施，不拆并行 worktree。
生产代码前遵守 Execution Lock：冻结 HEAD/dirty 指纹、生产符号/调用点、测试/fixture 所有者与 HEAVY mapping；
按需 GitNexus upstream impact，索引不可用则明确降级为精确调用点分析。高风险实施按 TDD，禁止借评审自动提交或部署。

- [ ] **T1（P1，人工约 2h / Agent 约 30min）— wire 与 ACK**：同步主合同对应小节，补完整 DTO、独立 schema 和 handler；不修改公开 schema。
  - 来源：D4、D9、D11；文件：`docs/contracts/wms-outbound-picking-task-integration-requirements.md`、`src/app/wms_adapter/outbound_picking/`。
  - 验证：G01–G03 聚焦测试；新 ID 比较完整 data、原 ID 比较完整信封；WMS 联合 fixture 确认单独留证。
- [ ] **T2（P1，人工约 3h / Agent 约 45min）— 模型与迁移**：扩展 PickingTask，新增两类精确计划成员和约束，保留旧任务 revision 0。
  - 来源：D2、D3、D6、D12；文件：`src/app/wms_integration/outbound_picking/models/`、现有 metadata/模型导出入口、Alembic 新 revision。
  - 验证：G16；通过 `uv run alembic revision -m "add picking task plan delta"` 生成随机 revision，再编辑，禁止改写既有 migration。
- [ ] **T3（P1，人工约 4h / Agent 约 1h）— 原子应用与失败收敛**：实现同域 Service/Repository、完整重放、prepare 暂存、持续阻塞与 Evidence 隔离。
  - 来源：D5–D11；文件：`src/app/wms_integration/outbound_picking/services/`、`repositories/` 与相应导出。
  - 验证：G04–G15、G17；先失败用例再实现，不改共享 dispatcher/扫描器，不修改已有 issued/prepare 签名。
- [ ] **T4（P1，人工约 1h / Agent 约 20min）— 暗构建与回归门禁**：补实际进程根隔离、公开接口缺席和既有消费者回归。
  - 来源：D7、D13；文件：第 12 节测试所有者目录，`docs/architecture/heavy-test-impact.toml`。
  - 验证：G18；新文件精确 mapping，原有聚合映射按实际影响更新，不把插件测试并入核心 selector。
- [ ] **T5（P1，人工约 2h / Agent 约 40min，不含环境等待）— 最终快照验证**：闭合 SQL 批次、解析边界、迁移及所选 HEAVY。
  - 来源：D10、D12–D14；文件：相关测试和 mapping；不增加独立 benchmark 服务或吞吐框架。
  - 验证：聚焦域测试、暗构建 guardrail、QUALITY、selector manifest 及迁移；记录指纹与未验证边界。

最小执行命令与门禁（在实施后的有效快照执行，本次不运行）：

- `uv run pytest tests/contracts/wms_adapter/outbound_picking/ -q`
- `uv run pytest tests/architecture/test_outbound_picking_plan_delta_activation_guardrail.py tests/api/test_wms_events.py -q`
- `uv run scripts/select_heavy_tests.py --scope unstaged` 与 `./scripts/run_selected_heavy_local.sh --scope unstaged`；最终获准暂存后改用 staged。
- `./scripts/git-quality-gate.sh --profile quality` 只对最终有效代码快照执行一次；纯文档后改不重复。

迁移/HEAVY 所需环境先检查就绪，在独占临时 PostgreSQL 中使用干净逻辑库；`skipped` 不算通过。migration 新 revision 产生后，
一次性枚举并更新 schema head、metadata 与 HEAVY 所有受影响测试，不靠反复失败发现遗漏。无 Celery wiring 变更，
本切片不要求真实 worker 测试；生产激活任务必须另行真实 worker 验证。以上均不替代 WMS 联调或现场物理验收。

## 14. 评审结论与决策依据

2026-09-04：D1–D7 用户逐项确认；随后用户明确授权“后续都按建议”，D8–D14 据此采用推荐方案，不追加逐项问答。
评审基线：`feature/phase12-manual-bin-processing`，HEAD `033850764c23205509d8d4ea2f669dfaa381401a`。
保留原有 `.gitignore`、`AGENTS.md`、`CLAUDE.md` 及 index；本次只改本设计和仓库外评审元数据。

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

D8/D10/D11 的现有实现依据置信度 9/10；D9/D12–D14 是设计缺项，置信度 8/10，未声称已经观测到生产故障。
架构 9 项、代码质量 2 项、测试 18 组 GAP、性能 1 项均已转为本设计的实施要求。没有新增独立 TODO；复用现有激活 TODO，
不把物理执行或恢复功能拉入本轮。完整性选择：完整正式合同与全部 18 组测试要求已采纳，其余是方式选择，不编造完整性分数。
外部第二模型评审按 Codex 宿主保护跳过，未调用嵌套 Codex；本评审不宣称跨模型一致。

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
| --- | --- | --- | --- | --- | --- |
| CEO Review | `/plan-ceo-review` | 产品/范围重审 | 0 | NOT REQUIRED | 既定 Phase 12 切片，不扩大产品范围 |
| Codex Review | `/codex review` | 独立第二意见 | 本计划 0；历史 2 | SKIPPED | Codex 宿主保护；历史 prepare 结果不用于本计划 |
| Eng Review | `/plan-eng-review` | 架构、代码质量、测试、性能 | 本计划 1；历史 2 | CLEAR (PLAN) | 9 架构 + 2 代码质量 + 18 测试路径 GAP + 1 性能要求，全部纳入计划 |
| Design Review | `/plan-design-review` | UI/交互 | 0 | NOT REQUIRED | 纯后端暗构建，无页面变化 |
| DX Review | `/plan-devex-review` | 独立开发者体验评审 | 0 | NOT REQUIRED | 复用既有同域 API、测试与迁移工具 |

**VERDICT:** ENG CLEARED（WES 设计评审）。T1–T5 待实施；本次仅完成文档审阅、结构与路径核对、`git diff --check`。
未执行生产代码变更、测试、迁移、QUALITY、HEAVY、Commit、Push 或 Deploy；WMS 联合合同确认和真实物理验收不在本结论内。
评审测试清单：`/Users/kaizhou/.gstack/projects/kaizhoumasha-wes_backend/kaizhou-feature-phase12-manual-bin-processing-eng-review-test-plan-20260904-204439.md`。
实施任务产物：`/Users/kaizhou/.gstack/projects/kaizhoumasha-wes_backend/tasks-eng-review-20260904-204439.jsonl`。

NO UNRESOLVED DECISIONS
