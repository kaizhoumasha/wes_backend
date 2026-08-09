# WES Phase 4 AGV/CTU 通用搬运能力实施计划

> **供智能代理实施：** 使用 `superpowers:subagent-driven-development` 或 `superpowers:executing-plans`，按任务顺序实施；
> 代码行为遵循测试驱动开发（TDD）。

**目标：** 为后续工作线插件提供简单、稳定的 AGV/CTU 搬运方法，支持搬货架、货架原地换面、批量搬料箱和协调交换料箱；
业务插件只说明“搬什么、从哪里搬到哪里、谁发起”，不接触 WMS/RCS 协议和内部可靠性机制。

**架构：** `TransportService` 对插件暴露四个明确方法，内部以一个 Transport 聚合完成任务、成员、证据、位置投影和可靠结果发布，
经 WMS 转发 RCS。当前只显式装配一个 WMS Transport Adapter，不建立动态 Provider、通用命令平台或设备运行时。

**状态：** `IMPLEMENTED_DARK`（2026-08-10 后端 QA 验收通过）。四类请求、异步证据收敛、统一结果和 `exchange_bins()` 协调交换已完成暗构建与验收；
未注册生产 route、Celery task、beat、worker hook 或工作线消费者。Phase 4 只依赖
自身与已完成的 Phase 3，不受后续 Phase 5 是否完成影响。

## 0. 名词与边界

| 中文名称 | 英文/代码名称 | 本计划含义 |
| --- | --- | --- |
| 基础设施能力（Foundation） | HTTP、数据库、事务、幂等、并发控制 | 跨领域技术底座；Phase 4 只复用，不新建通用平台 |
| 设备接入能力（Device） | 设备命令、状态、回调和供应商适配 | 具体设备协议接入；不属于 Phase 4 |
| 搬运能力（Transport） | `TransportService`、`TransportTask` | 搬运已确定的货架或料箱；是 Phase 4 唯一建设范围 |
| 调用方（caller） | `TransportCaller` | 发起搬运的工作线和可选工作站信息，只用于追踪与结果路由 |
| 搬运句柄（handle） | `TransportHandle` | 调用成功创建可靠任务后立即返回的任务标识 |
| 搬运结果（outcome） | `TransportOutcome` | Phase 4 异步通知插件的统一成功、失败、拒绝或未知结果 |
| 适配器（Adapter） | `WmsTransportAdapter` | 把内部搬运请求转换为 WMS 线上接口并执行单次 HTTP 调用 |
| 权威证据（evidence） | WMS/RCS 位置事实和最终结果 | 可改变任务状态或位置投影的可靠外部事实 |

代码标识符、状态值和协议字面量保持原文；其他专业名词优先使用中文，必要时括注英文。

## 1. 核心裁决

1. Phase 4 只提供四个公共方法：`move_rack()`、`rotate_rack()`、`move_bins()`、`exchange_bins()`。
2. 工作线插件或 WMS 负责选择空货架、空料箱、可用储位和本批次成员；Phase 4 只接收已确定的对象、来源和目标。
3. `workline_id`、可选 `station_id` 和 `correlation_id` 只标识调用来源，不参与资源选择和 RCS 调度。
4. 方法创建可靠任务后立即返回 `transport_task_id`，不阻塞等待物理搬运，不要求插件轮询。
5. 最终结果异步通知原调用方；只有 `SUCCEEDED` 可以推进依赖该搬运的下一业务步骤。
6. `exchange_bins()` 是一个协调物理动作：一次调用只生成一个任务和一个 WMS/RCS 请求，不拆成多个普通搬运请求。
7. 当前经 WMS 转发 RCS；WES 不直连 AGV、CTU 或 ECS，不选择车辆、路径、交通策略和设备内部动作顺序。
8. 插件不接触 `TransportTask` Repository、领取、租约、令牌、证据表或 WMS 线上接口。
9. 系统未发布，不保留旧 API、旧表、别名、兼容层、双写、双读或旧数据迁移。

## 2. 面向工作线插件的公共能力

### 2.1 公共数据

`TransportCaller`：

- `workline_id`：必填，发起搬运的工作线；
- `station_id`：可选，用于区分同一工作线下的 `STATION_A / STATION_B` 等工作站；
- `correlation_id`：可选，用于关联一次工作线流程中的多个搬运任务。

每次调用还必须携带唯一的调用幂等号 `client_request_id`。它与 WMS HTTP 信封中的 `request_id` 不是同一字段：

- 相同 `client_request_id`、相同参数重复调用，返回原 `transport_task_id`；
- 相同 `client_request_id`、不同参数，返回幂等冲突；
- 同一次开工流程中的多个搬运任务共享 `correlation_id`，但各自使用不同的 `client_request_id`。

`TransportHandle` 只包含 `transport_task_id` 和 `client_request_id`，不暴露内部状态机。

### 2.2 四个公共方法

```text
TransportService.move_rack(
    client_request_id, caller, rack_id, source, target
) -> TransportHandle

TransportService.rotate_rack(
    client_request_id, caller, rack_id, position, target_face
) -> TransportHandle

TransportService.move_bins(
    client_request_id, caller, moves
) -> TransportHandle

TransportService.exchange_bins(
    client_request_id, caller, exchange_pairs
) -> TransportHandle
```

#### `move_rack()`

搬运一个已确定货架到已确定目标位置。单层货架、五层货架、空架和目标架只属于业务属性，不增加专用方法。

#### `rotate_rack()`

把一个已确定货架在当前位置切换到已确定工作面。首版工作面是闭集 `A | B`；当前位置或当前面未知时失败关闭。

#### `move_bins()`

一次搬运 1～4 个已确定料箱。每个成员包含 `bin_id + source + target`；调用方必须在提交前按
`min(CTU 背篓容量 4, 目标位当前可承接容量, 可搬运料箱数量)` 完成批次选择。Phase 4 不查询、计算或预占目标位容量。

#### `exchange_bins()`

一次提交 1～2 个交换对，即最多 2 个待换出料箱与 2 个待换入料箱。每个交换对固定包含两个不同 `bin_id` 和两个不同位置，
结果是两个料箱互换位置。合同不传递“满箱/空箱”分类，不选择对象，也不规定 CTU 内部取放顺序。

同一料箱或位置不得在一次交换请求中重复。WMS/RCS 必须原生支持一次请求内的协调交换；不支持时返回 `REJECTED`，WES
不得拆成多个 `move_bins()` 模拟。

### 2.3 最小结构校验

Phase 4 只校验搬运合同自身，不判断空箱、满箱、容量、业务资格、工作站占用或业务顺序：

| 方法 | 失败关闭条件 |
| --- | --- |
| 全部方法 | 标识为空、位置类型或必填字段不符合闭集 |
| `move_rack()` | 来源与目标相同，或来源/目标不是 `RACK_POSITION` |
| `rotate_rack()` | 目标面不在 `A/B`、当前面未知，或目标面等于当前面 |
| `move_bins()` | 成员数不在 `1..4`、重复 `bin_id`、单成员来源与目标相同、重复使用 `RACK_BIN_SLOT` |
| `exchange_bins()` | 交换对数量不是 1～2、料箱或储位重复、位置不是 `RACK_BIN_SLOT` |

多个成员可以使用同一个 `HANDOFF_POSITION`；该位置可能代表允许排队的滚筒线入口或出口，其容量由 WMS/工作线插件确定。

### 2.4 位置类型

首版只使用三个可判别位置类型：

| 位置类型 | 必填字段 | 用途 |
| --- | --- | --- |
| `RACK_POSITION` | `location_code` | 货架来源、目标和原地换面位置 |
| `RACK_BIN_SLOT` | `rack_id + slot_id` | 料箱所在的货架储位 |
| `HANDOFF_POSITION` | `location_code` | 滚筒线入料口、出料口等 CTU 交接位置 |

不得使用拼接字符串表达位置，也不得由 Phase 4 根据 `bin_id` 反推货架或储位。

### 2.5 异步结果

插件只需要理解一个 `TransportOutcome` 和四种状态：

| 状态 | 中文含义 | 插件处理 |
| --- | --- | --- |
| `SUCCEEDED` | 搬运成功且最终位置明确 | 可以执行下一业务步骤 |
| `FAILED` | 已执行失败，但相关对象最终位置明确 | 按业务规则终止、重新分配或人工处理 |
| `REJECTED` | WMS/RCS 未接纳，没有开始搬运 | 修正请求或重新分配资源 |
| `UNKNOWN` | 是否执行或最终位置不确定 | 停止依赖该资源的后续动作并人工核验 |

结果必须携带 `transport_task_id`、`client_request_id`、单调递增的 `outcome_version`、原 `TransportCaller`、稳定结果码和已确认
最终位置。内部 `RECONCILING` 映射为插件可理解的 `UNKNOWN`；后续权威结果完成消歧时，可以用更高版本再发布同任务的确定结果，
插件按 `transport_task_id + outcome_version` 幂等处理，并允许版本号跳跃。

批量任务只按成员事实聚合：全部成员成功且位置明确才是 `SUCCEEDED`；至少一个成员失败、但全部成员位置明确时是 `FAILED`；
任一成员位置未知时是 `UNKNOWN`。不按“满箱/空箱”等业务分类改变聚合规则。

所有位置明确的货架类成功/失败结果还必须携带到达面 `arrival_face: A | B`；只有位置未知时可以缺少。它是 WMS/RCS
回传的当前工作面权威事实，WES 只保存和投影，不从目标面、旧数据或业务流程推断。

Phase 4 通过一个显式注入的 `TransportOutcomePublisher` 发布结果，不建立动态订阅注册表。Phase 5 才把它接到生产工作线消费者。

## 3. 最小内部结构

```text
工作线插件
    │ 四个公共方法
    ▼
TransportService
    │ 同一套校验、幂等和可靠任务创建
    ▼
Transport 聚合
（Task + Member + Evidence + PositionProjection）
    │ 后台领取，事务外单次发送
    ▼
TransportProviderPort
    ▼
WmsTransportAdapter → WMS → RCS → AGV/CTU
                                      │
WMS Event → TransportService.record_evidence() ─┘
    │ 持久化后 ACK；后台批次再更新任务和位置投影
    ▼
待发布 outcome_version
    │ 有界领取，事务外发布
    ▼
TransportOutcomePublisher → 工作线插件
```

### 3.1 唯一职责

| 组件 | 负责 | 不负责 |
| --- | --- | --- |
| `TransportService` | 四个公共方法，以及四个内部批处理入口、ACK/证据收敛、超时收敛和可靠结果发布 | HTTP、WMS DTO、车辆和路径 |
| `TransportRepository` | Task/Member/Evidence/Projection、活动资源和待发布结果的 SQL/flush | 业务决策和自行 commit |
| `TransportProviderPort` | 单次提交类型化搬运请求 | 持久化、重试和状态查询 |
| `WmsTransportAdapter` | 固定 WMS path/operation/DTO/ACK 映射 | 任务生命周期和数据库 |
| `TransportOutcomePublisher` | 发布统一搬运结果 | 动态发现插件和修改任务状态 |

### 3.2 内部可靠性

Phase 4 在同一个 `TransportService` 中交付四个可测试、但尚未注册生产调度的内部批处理入口：
`submit_pending_tasks(limit)`、`process_pending_evidence(limit)`、`reconcile_overdue_tasks(limit)`、
`publish_pending_outcomes(limit)`。它们不是四个 Service，也不是四个动态 worker；如何接入未来生产调度由后续任务决定，
不影响 Phase 4 实施或验收。

内部 `TransportTask` 保留 `PENDING / ACCEPTED / REJECTED / SUCCEEDED / FAILED / RECONCILING` 六态，处理：

- 先持久化任务，再发送外部请求；
- 同一资源最多属于一个非终态任务；
- PostgreSQL 小批量领取、租约和令牌隔离并发 worker；
- HTTP 不进入数据库事务；
- `submit_attempt_count` 创建时为 `0`；实际调用 HTTP 前在独立短事务中原子加一并写入 `send_started_at`，达到 `3` 后禁止
  再次发送；只有尚无 `send_started_at` 的过期领取可以重新领取；
- 已有 `send_started_at` 后 worker 崩溃或租约过期一律收敛为 `RECONCILING/UNKNOWN`，不得假设未送达后重提；
- 相同身份和 Payload 安全收敛，交付结果未知时不换身份重提；
- 单次 HTTP 硬超时 10 秒；每个任务最多实际发送 3 次，次数只以持久化的 `submit_attempt_count` 为准；
- 只有 `NOT_SENT`、明确未接纳的 `429/503` 可以使用原身份重提；`NOT_SENT/503` 固定等待 2 秒，`429` 只使用 ACK
  的正整数 `data.retry_after_ms`，缺失或不是正整数时固定等待 2 秒；Transport 合同不使用 HTTP `Retry-After`；
- 只有保存上述明确未送达/未接纳结果时，才在同一事务清除本次 `send_started_at` 并安排下一次固定重提；
- `DELIVERY_UNKNOWN` 永不自动重提；预算耗尽时发布 `REJECTED / TRANSPORT_SUBMIT_RETRY_EXHAUSTED`；
- WMS/RCS 位置事实和最终结果持久化后应答；
- `record_evidence()` 只保存原始 evidence，不在 WMS 回调请求内更新任务、投影或发布结果；
- 原始 evidence 以 `PENDING | APPLIED | CONFLICT` 记录处理状态；待处理项使用领取令牌和短租约有界领取，崩溃后可重领；
- 位置或结果未知时进入对账，不猜测成功、失败或原位置；
- 首次进入 `ACCEPTED` 时冻结 `result_deadline_at = 当前时间 + 10 分钟`；ACK 或先到的位置证据均可首次设置，后续重复 ACK、
  位置事实和其他更新不得刷新；超时发布 `UNKNOWN / TRANSPORT_RESULT_TIMEOUT` 并保持资源绑定；
- `reconcile_overdue_tasks(limit)` 按稳定顺序有界领取超期 `ACCEPTED` 任务，在事务内转为 `RECONCILING`、递增
  `outcome_version` 并形成待发布结果，不查询 WMS/RCS、不释放资源；
- 只有权威最终结果推进物理终态；
- 形成 `UNKNOWN` 或确定结果的事务同时递增 `outcome_version`；
- `publish_pending_outcomes()` 有界领取最新未发布结果快照，使用领取令牌和租约隔离 worker，在事务外发布；
- `TransportOutcomePublisher.publish()` 正常返回才记账，异常或取消均保留待发布；
- 尚未发布的低版本允许被更高版本合并；保证最新权威结果最终送达，不建设逐版本结果历史；
- 发布成功后、记录前崩溃只会造成重复通知，不会丢失最新结果。

这些机制全部是 `TransportService` 内部实现，不出现在工作线插件接口中。首版不增加状态查询、取消、暂停、恢复、车辆改派、
动态 Provider、通用命令、通用工作流或独立提交尝试子系统。

## 4. Task 0：合同入口门禁

WMS/WES 已共同冻结：

- [x] 四个公共方法及其请求类型、必填字段、错误闭集和示例；
- [x] `client_request_id` 的 WES 本地幂等语义，以及 WMS 不可变请求以 `transport_task_id` 为幂等身份；
- [x] `move_bins()` 单次成员上限为 4，调用方按目标位可承接容量缩小批次；
- [x] `exchange_bins()` 一次 1～2 个交换对、单任务、单次 WMS/RCS 请求和协调执行保证；
- [x] `RACK_POSITION / RACK_BIN_SLOT / HANDOFF_POSITION` 三种位置结构；
- [x] WMS submit、成员位置事实和最终结果的固定 path、operation、闭集 DTO 与 `256 KiB` Payload 上限；其中 WMS → WES
  位置与结果回调复用 `docs/contracts/wms-async-callback-envelope-contract.md`，同步 Transport 提交/ACK 继续由 Transport
  合同独立定义，不抽取全局 WMS 交互信封；
- [x] 所有位置明确的货架类成功/失败结果必须回传 `arrival_face: A | B`，只有位置未知时可以缺少，WES 不推断当前工作面；
- [x] WMS/RCS 不支持协调交换时返回 `422 / REJECTED / COORDINATED_BIN_EXCHANGE_UNSUPPORTED`；
- [x] 同步 ACK 只代表接纳，最终结果必须异步回调；
- [x] 单次 HTTP 超时 10 秒、最多发送 3 次，以及 `NOT_SENT/429/503` 的固定重提规则和预算耗尽结果；
- [x] `DELIVERY_UNKNOWN` 禁止自动重提并进入 `UNKNOWN/RECONCILING`；
- [x] `ACCEPTED` 后等待结果 10 分钟，超时发布 `UNKNOWN`，后续确定结果使用更高版本修正；
- [x] `outcome_version` 的单调版本与插件幂等消费规则；
- [x] 允许未发布低版本被更高版本合并、插件允许版本跳跃，以及 Publisher 正常返回才代表成功；
- [x] 业务货架/料箱分配接口不属于 Phase 4，不得在本阶段补建。

**退出条件：** `docs/contracts/transport-fulfillment-contract.md` 为 `Approved`，以上 15 项均已批准。Phase 5 生产接线和旧 owner
处置是后续任务，不是 Phase 4 的入口或退出条件。

## 5. 目标文件结构

```text
src/app/transport/
├── __init__.py
├── contracts.py                  # caller、四类请求、handle、outcome、两个窄 Port
├── composition.py                # 暗装配，不注册生产消费者
├── models.py                     # Task、Member、Evidence、Projection 和活动资源绑定
├── repository.py                 # 一个 Transport 聚合 Repository
└── service.py                    # 四个公共方法及内部提交、证据、超时收敛和结果发布

src/app/wms_adapter/
├── transport_wire.py
├── transport_adapter.py
└── transport_event_handler.py
```

首版不增加通用 Repository、UnitOfWork、Service Locator、Provider Registry 或插件 SDK。

### 5.1 已有能力（What already exists）

| 已有能力 | Phase 4 处理 |
| --- | --- |
| `src/core/outbound_http/` 的 `OutboundHttpTransport` | 仅由 `WmsClient` 在内部继续复用单次有界 HTTP 发送、交付状态和失败事实；Phase 4 核心与 Adapter 不直接依赖 Phase 2 |
| `src/app/wms_adapter/WmsClient` | Phase 4 唯一外发入口；复用 JSON 编解码和访问结果，只按首个真实消费者需要扩展逐请求请求体/响应预算，不加入搬运语义 |
| PostgreSQL + SQLAlchemy 的部分唯一索引、`FOR UPDATE SKIP LOCKED` | 复用数据库原生能力实现活动资源约束和有界领取，不引入 Redis 锁或新队列 |
| `BaseRepository` / 现有事务约定 | 复用数据库会话和 flush 约定，但只建立一个 Transport 聚合 Repository |
| 旧 `SystemOutbox`、Runtime Effect 和 `src/app/wms_integration/` | 只作为删除范围与事故经验参考，不复用其通用平台结构，也不作为 Phase 4 测试真源 |

首版新增的是 Transport 领域事实，不是新的 Foundation。任务、成员、证据和位置投影不能用旧 Outbox JSON 代替，否则会重新
耦合待删除平台；领取、锁和 HTTP 传输则必须复用现有基础设施模式。

## 6. 实施任务（Implementation Tasks）

### Task 1：建立公共合同和持久化模型

**文件：**

- Create: `src/app/transport/contracts.py`
- Create: `src/app/transport/models.py`
- Create: `src/app/transport/__init__.py`
- Modify: `migrations/env.py`
- Create via Alembic generator: one Transport revision
- Test: `tests/runtime/transport/test_transport_contracts.py`
- Test: `tests/integration/transport/test_transport_schema.py`

**产出：** `TransportCaller`、`TransportHandle`、带 `outcome_version` 的 `TransportOutcome`、四类请求、两个窄 Port、
含 `submit_attempt_count` 和 `result_deadline_at` 的任务及成员/证据/投影模型。

- [x] 先写失败测试锁定四个方法所需 DTO、最小结构校验、位置闭集和结果闭集。
- [x] 覆盖 `exchange_pairs` 为 0、1、2、3，对内料箱/位置重复，以及合法的 1～2 个交换对。
- [x] 覆盖 `client_request_id` 唯一、同请求同摘要幂等、同请求异摘要冲突。
- [x] 建立最小活动资源唯一约束：货架任务绑定该货架；料箱任务绑定每个料箱，以及来源/目标储位引用的全部不同货架。
- [x] 建立活动资源部分唯一索引、待提交/重提领取索引、`event_id` 唯一索引、待处理 evidence 索引和待发布结果领取索引；不增加缓存。
- [x] 使用 Alembic generator 生成迁移；不手写 revision ID，不迁移旧数据。
- [x] 在隔离 PostgreSQL 空库验证 upgrade、约束和索引。

### Task 2：实现简单的 `TransportService`

**文件：**

- Create: `src/app/transport/repository.py`
- Create: `src/app/transport/service.py`
- Test: `tests/runtime/transport/test_transport_service.py`
- Test: `tests/integration/transport/test_transport_repository.py`
- Modify: `docs/architecture/heavy-test-impact.toml`

**产出：** 四个公共方法、一个聚合 Repository、可靠任务创建，以及 `submit_pending_tasks(limit)`、
`process_pending_evidence(limit)`、`reconcile_overdue_tasks(limit)`、`publish_pending_outcomes(limit)` 四个内部批处理入口。

- [x] 每个公共方法先写成功、边界和失败测试，再实现最小行为。
- [x] 四个方法复用同一个私有任务创建路径，不复制幂等、资源绑定和持久化逻辑。
- [x] `move_rack()` 每次只处理一个货架；调用方需要 1～2 个货架时分别调用，可并行等待结果。
- [x] `exchange_bins()` 生成一个 `EXCHANGE` 请求，不拆分、不在 WES 排序 CTU 内部动作。
- [x] 创建成功立即返回 `TransportHandle`；不得等待 WMS ACK 或物理结果。
- [x] PostgreSQL 并发测试覆盖重复调用、资源冲突、领取租约回收和两个 worker 不重复领取。
- [x] 资源冲突测试覆盖料箱来源货架、目标货架和同批次去重；任一相关货架存在非终态 AGV/CTU 任务时创建失败关闭。
- [x] 覆盖只有 `REJECTED / SUCCEEDED / FAILED` 的确定终态释放活动资源；`RECONCILING/UNKNOWN`、结果超时和交付未知均
  保持绑定，直到权威确定结果完成消歧。
- [x] 外部 HTTP 始终在事务外；使用硬超时，不引入 heartbeat。
- [x] 四个批处理入口均按显式 `limit` 有界领取，并使用稳定 `ORDER BY + 主键`；不得无界扫描或依赖数据库偶然返回顺序。
- [x] 覆盖领取后、`send_started_at` 写入前崩溃可重领，以及写入后、ACK 写回前崩溃只收敛为 `UNKNOWN` 且禁止重提。
- [x] 覆盖 `submit_attempt_count` 创建为 `0`，每次发送开始事务原子递增，并在达到 `3` 后零次调用 Adapter；重启后不得
  重新获得发送预算。
- [x] 覆盖只有 `NOT_SENT`、`429/503` 的确定性结果事务可以清除本次 `send_started_at`，并在单任务最多发送 3 次的
  预算内重提。
- [x] 覆盖 10 秒 HTTP 超时、最多发送 3 次、2 秒固定间隔、合法/非法 `data.retry_after_ms`、`DELIVERY_UNKNOWN` 禁止重提和
  `TRANSPORT_SUBMIT_RETRY_EXHAUSTED`。
- [x] 覆盖首次 ACK 或先到的位置证据进入 `ACCEPTED` 时设置同一个 `result_deadline_at`，重复 ACK/位置事实不得刷新；最终结果
  先到时无须设置。
- [x] 覆盖 `reconcile_overdue_tasks(limit)` 只按 `result_deadline_at` 领取超过结果截止时间的 `ACCEPTED` 任务，形成
  `TRANSPORT_RESULT_TIMEOUT`，保持资源绑定；未超时任务不变，迟到确定结果使用更高版本修正。

### Task 3：实现 WMS Adapter、证据和统一结果

**文件：**

- Create: `src/app/wms_adapter/transport_wire.py`
- Create: `src/app/wms_adapter/transport_adapter.py`
- Create: `src/app/wms_adapter/transport_event_handler.py`
- Modify: `src/app/wms_adapter/client.py`
- Modify: `src/app/wms_adapter/__init__.py`
- Modify: `src/app/transport/service.py`
- Modify: `src/app/transport/repository.py`
- Modify: `docs/contracts/wms-northbound-interaction-contract.md`
- Test: `tests/contracts/wms_adapter/test_client.py`
- Test: `tests/contracts/wms_adapter/test_transport_adapter.py`
- Test: `tests/contracts/wms_adapter/test_transport_event_handler.py`
- Test: `tests/runtime/transport/test_transport_outcome.py`
- Test: `tests/integration/transport/test_transport_evidence_transaction.py`

**产出：** `WmsClient` 逐请求字节预算、四类请求到固定 WMS wire 的转换、原始回调 Body 限制、持久化后应答、
位置/终态收敛和 `TransportOutcome` 发布。

- [x] 先扩展 `WmsClient`：`request/post` 接收可选正整数 `max_request_body_bytes` 和 `max_response_body_bytes`；JSON 编码后、
  发送前执行请求体上限，并在 Client 内部把响应上限映射为 Phase 2 `OutboundHttpResponseLimits`；Adapter 不导入 Phase 2
  类型，默认行为保持现有共享 Client 合同，不增加第二套编码或 Transport。
- [x] 覆盖请求体恰好等于/超过 `256 KiB` 时一次发送/零发送，以及响应 wire/decoded body 超限时保留 Phase 2 失败事实。
- [x] `TransportEventHandler.handle(raw_body: bytes)` 在 JSON 解码前执行 `256 KiB` 上限；覆盖恰好等于、超过上限、非法
  UTF-8、非法 JSON、未知字段和合法闭集 DTO。
- [x] 覆盖固定 path/operation、Transport 提交自有信封、WMS 异步回调统一信封、四类闭集 DTO、ACK、拒绝、冲突和
  交付结果未知。
- [x] 覆盖 `BIN_MOVE` 成员数 1、4、5，以及调用方缩小批次后 Phase 4 只验证冻结成员、不查询目标容量。
- [x] 覆盖 `EXCHANGE` 的 1～2 个交换对在一个 Payload 中提交，禁止拆成多个 HTTP 请求。
- [x] 覆盖逐箱取出、放置、最终结果、重复、倒序、未知位置和矛盾结果。
- [x] 覆盖批量成员全成功、部分失败但位置完整、任一位置未知三种聚合结果。
- [x] 覆盖 `final_position` 与字面量 `position_unknown=true` 严格二选一，以及 `SUCCEEDED` 必须位置明确、`FAILED` 必须携带
  `failure_code`；拒绝两者同时存在、同时缺少和 `position_unknown=false`。
- [x] 覆盖 `RACK_MOVE`、`RACK_ROTATE` 成功结果、位置明确的失败结果、位置未知结果对 `arrival_face` 的要求，以及面向投影单调更新。
- [x] evidence 按稳定顺序小批量领取；覆盖保存后崩溃、租约过期重领、并发 worker 和旧领取令牌拒绝写回。
- [x] `record_evidence()` 的合同测试证明回调事务只保存 `PENDING` evidence 并应答，不在请求内应用任务/投影或发布结果。
- [x] 证据应用在一个事务内锁定任务，更新任务/成员/投影并标记证据；任一步失败整体回滚。
- [x] 覆盖证据早于/晚于 ACK、晚到 ACK 不回退状态，以及 `UNKNOWN` 后确定结果使用更高版本修正。
- [x] `SUCCEEDED / FAILED / REJECTED / UNKNOWN` 统一携带 caller、调用幂等号和 `outcome_version`。
- [x] 终态事务形成待发布版本；覆盖发布失败、崩溃恢复、重复发布和发布版本记账。
- [x] 覆盖未发布低版本被更高版本合并、版本跳跃，以及旧领取令牌不得覆盖新版本记账。
- [x] 使用假的 `TransportOutcomePublisher` 验证发布；不建立动态消费者注册表或独立 Outbox 表。

### Task 4：暗装配与最终验收

**文件：**

- Create: `src/app/transport/composition.py`
- Modify: `docs/architecture/file_index.md`
- Modify: `docs/superpowers/plans/2026-08-03-wes-architecture-convergence-master-plan.md`
- Test: `tests/architecture/test_transport_boundaries.py`
- Test: `tests/integration/transport/test_dark_transport_loop.py`

**产出：** 可显式构造但不注册生产路径的 Phase 4 搬运能力。

- [x] 显式装配 `TransportService`、唯一 WMS Adapter、一个 Transport Repository 和结果 Publisher；不读取全局容器。
- [x] 暗调用四个内部批处理入口，证明未来生产接线无需理解 Repository、领取令牌或 Adapter 细节。
- [x] 暗闭环验证四个公共方法中至少各一个请求，其中交换覆盖两个交换对。
- [x] 架构测试证明 Transport 核心不依赖 httpx、ECS、DeviceCommand、PickingTask 或工作线插件；Transport 核心和
  `WmsTransportAdapter` 均不得直接导入 `src.core.outbound_http`，只能经 `WmsClient` 使用 Phase 2 能力。
- [x] 确认新 API、Celery task、beat、worker hook 和生产消费者均未注册。
- [x] 确认四个内部批处理入口均可由测试显式调用，且当前验收不依赖任何 Phase 5 生产调度或 successor/NONE 清单。
- [x] Commit 前运行 GitNexus detect changes，确认没有意外生产调用链。

## 7. 测试所有权

| 测试层 | 只验证 | 不得验证 |
| --- | --- | --- |
| `tests/runtime/transport/` | 四个方法、任务状态、幂等、统一结果 | 分拣机开工或 WMS 资源分配业务 |
| `tests/integration/transport/` | PostgreSQL 约束、领取、事务、位置和暗闭环 | 供应商设备行为 |
| `tests/contracts/wms_adapter/` | 固定 WMS wire、DTO、ACK 和回调转换 | Transport 数据库生命周期 |
| 工作线插件测试 | 业务顺序、并行、容量和资源选择 | Phase 4 内部领取与持久化 |
| WMS/RCS 联调验收 | 真实 AGV/CTU、协调交换和回调可靠性 | WES 核心单元行为 |

纯文档变更不运行或编写 pytest。后续代码实施按任务运行目标测试、架构护栏、Ruff、HEAVY selector 和质量门禁。

### 7.1 代码路径与测试覆盖图

```text
四个公共方法
├─ 结构校验失败 → FAST 合同/服务测试
├─ 新建 / 同幂等返回 / 异摘要冲突 / 并发重复 → FAST + PostgreSQL
└─ 立即返回 TransportHandle → FAST

后台提交
├─ RECEIVED / DUPLICATE / REJECTED → WMS Adapter 合同 + Service
├─ NOT_SENT / 429 / 503 → 最多发送 3 次、固定等待/ACK retry_after_ms + PostgreSQL
└─ DELIVERY_UNKNOWN → 禁止重提，形成 UNKNOWN

位置与最终结果
├─ 重复 / 倒序 / 冲突 / 早于 ACK / 晚于 ACK → Service + PostgreSQL
├─ 持久化后崩溃 / 并发领取 / 租约过期 / 旧令牌写回 → PostgreSQL
├─ 货架成功/失败/位置未知结果的 arrival_face、面向投影更新 → WMS Adapter + PostgreSQL
├─ 完整成员 / 缺少成员 / 增加成员 / 位置未知 → Service + PostgreSQL
├─ 全部成功 / 部分失败且位置完整 / 任一位置未知 → Service + PostgreSQL
└─ BIN_EXCHANGE 部分完成 → 核心只验证结果收敛；真实动作由 WMS/RCS 联调

结果等待截止
├─ 未超过截止时间 → 保持 ACCEPTED
└─ 超过截止时间 → reconcile_overdue_tasks 形成 UNKNOWN，保持资源绑定

可靠结果发布
├─ 终态事务同时生成 outcome_version → PostgreSQL
├─ 未发布低版本被更高版本合并 → 最新状态最终送达，插件允许版本跳跃
├─ 发布失败或发布前崩溃 → 可重新领取
├─ 发布后记账前崩溃 → 重复通知，插件幂等
└─ UNKNOWN 后收到确定结果 → 更高版本修正
```

计划内测试要求覆盖以上全部分支；“分拣机开工、容量计算、空箱/满箱选择”只进入插件或 WMS/RCS 验收，不进入核心覆盖率。

实现阶段只在两个复杂位置保留内联 ASCII 图：`models.py` 标注六态及 `RECONCILING` 修正关系，`service.py` 标注
“领取 → 事务外发送/发布 → 带令牌写回”以及“evidence 领取 → 任务/投影收敛”流水线。`contracts.py`、`repository.py` 和 Adapter
职责直接，不增加装饰性图示。

### 7.2 现实失败模式

| 失败模式 | 处理 | 测试所有者 | 是否静默 |
| --- | --- | --- | --- |
| 参数或位置结构非法 | 创建前稳定拒绝 | FAST Transport | 否，调用方立即得到错误 |
| 并发重复或活动资源冲突 | 数据库唯一约束 + 幂等收敛 | PostgreSQL Transport | 否，返回原任务或冲突 |
| WMS 明确未送达或暂时未接纳 | 原身份重提，单任务最多发送 3 次，耗尽后 `REJECTED` | Transport + WMS Adapter | 否，发布稳定结果 |
| 请求可能已送达 | 不重提，进入 `UNKNOWN` | Transport + WMS Adapter | 否，暂停依赖资源 |
| WMS 已接纳但 10 分钟无最终结果 | 发布 `TRANSPORT_RESULT_TIMEOUT`，保持资源绑定 | PostgreSQL Transport | 否，插件收到 `UNKNOWN` |
| 已记录发送开始、但 worker 在 ACK 写回前退出 | 租约到期后进入 `UNKNOWN`，禁止自动重提 | PostgreSQL Transport | 否，发布并等待核验 |
| 证据重复、倒序或冲突 | 幂等、单调推进或进入对账 | PostgreSQL Transport | 否，冲突证据可诊断 |
| evidence 已应答后进程崩溃 | 待处理状态、领取租约和令牌支持安全重领 | PostgreSQL Transport | 否，恢复后继续处理 |
| 交换只完成部分动作 | 按逐箱最终位置收敛；任一未知则 `UNKNOWN` | 核心结果合同 + WMS/RCS 联调 | 否 |
| 终态提交后进程崩溃 | 待发布版本由后续 worker 重新领取 | PostgreSQL Transport | 否，可能延迟但不丢失 |
| 发布后记账前进程崩溃 | 允许重复发布，插件按任务和版本幂等 | Transport + 插件合同 | 否，不重复推进业务 |

无“既无测试、又无错误处理、且对调用方静默”的已知失败路径。

### 7.3 实施顺序

顺序实施，无值得使用多工作树（worktree）的并行机会（Sequential implementation, no parallelization opportunity）。合同和聚合模型是后续
Repository、Service、WMS Adapter、事件处理和暗闭环的共同依赖；强行并行只会增加同模块冲突。可以在 Task 1 合并后由不同人员
分别准备 WMS DTO fixture 与 PostgreSQL fixture，但不应拆成并行代码分支。

## 8. 分拣机开工验算

以下流程只验证公共能力是否够用，不进入 Phase 4 核心测试：

1. 分拣机插件请求 WMS 分配 1～2 个粗分完成的单层货架，并得到确定 `rack_id`、来源和 `STATION_A / STATION_B`。
2. 插件分别调用 `move_rack()`；两个单层货架是独立任务，可以并行。
3. 插件同时请求 WMS 分配具有可用料箱/料格的五层货架，并调用 `move_rack()` 送到 `FIVE_STATION`。
4. WMS/RCS 回调先由 Phase 4 收敛为 `TransportOutcome`；只有五层货架 `SUCCEEDED` 才允许进入下一步。
5. 插件请求 WMS 返回本批次确定的 `bin_id`、来源储位和滚筒线入料位置，再调用 `move_bins()`。
6. 退箱时，WMS 返回确定料箱和五层货架目标空储位，插件反向调用 `move_bins()`。
7. 满箱换空箱时，WMS 返回 1～2 个确定交换对，插件调用一次 `exchange_bins()`。
8. 货架需要原地换面时，插件调用 `rotate_rack()`；换面成功后才继续依赖目标面的业务动作。

每次位置明确的货架搬运或换面结果均由 WMS 回传 `arrival_face`，WES 据此维护当前工作面；位置未知或没有该权威事实时不得发起换面。

这里没有 `request_empty_rack()`、`feed_sorter_bins()` 等业务方法。Phase 4 不理解“粗分完成”“有空料格”“满箱”“空箱”或
“分拣机开工”，只执行工作线插件和 WMS 已冻结的搬运事实。

## 9. 退出标准

1. 工作线插件只需理解四个公共方法、`TransportHandle` 和 `TransportOutcome`。
2. 1～2 个单层货架、一个五层货架可以作为独立任务并行提交，`station_id` 可区分 STATION A/B。
3. 五层货架只有 `SUCCEEDED` 后才能触发 CTU 料箱搬运。
4. `move_bins()` 支持 1～4 个确定料箱，调用方在提交前根据目标位可承接容量缩小批次。
5. `exchange_bins()` 支持 1～2 个交换对，一次只生成一个任务和一个 WMS/RCS 请求。
6. 相同 `client_request_id` 和相同参数返回原任务；异参数失败关闭。
7. 插件不接触内部状态机、数据库、领取租约、WMS wire 或设备回调。
8. Phase 4 不选择货架、料箱、储位、车辆、路径和设备动作顺序。
9. 核心、WMS Adapter、工作线插件和供应商联调测试互不代测。
10. Phase 4 保持暗装配，不进入当前生产路径。
11. 所有位置明确的货架类成功/失败结果都携带 `arrival_face`；只有位置未知时可以缺少，WES 不自行推断当前工作面。

## 10. 明确不在范围内（NOT in scope）

- WMS 业务资源分配接口及“空货架、空料箱、可用储位”选择；
- 分拣机、粗分机、滚筒线、机械臂、扫码和 NG 的业务编排；
- ECS/DeviceCommand、设备状态、供应商私有协议和设备回调；
- WES 直连 RCS/AGV/CTU；
- 车辆、路径、交通、充电和 CTU 内部交换顺序；
- 通用 Runtime/Effect、动态 Provider、Service Locator、插件 SDK 或工作流引擎；
- 状态轮询、取消、暂停、恢复、改派和自动补偿；
- Phase 5 生产切换及旧 owner/旧表/旧测试删除；
- 旧数据迁移、兼容 schema、alias、shim、fallback 或双轨；
- `docs/hardware/` 厂商原始资料。

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Codex Review | `/codex review` | Independent 2nd opinion | 6 | RESOLVED | 本轮外部复核发现均已收敛 |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 10 | CLEAN | 本轮 10 项问题已修复，0 critical gaps，15 个 Task 0 项已批准 |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | 后端基础能力，不适用 |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

**CROSS-MODEL:** 共同确认 Phase 4 只经 `WmsClient` 使用 Phase 2、发送次数和结果截止时间必须持久化、记录
`send_started_at` 后的崩溃必须收敛为 `UNKNOWN`、WMS 异步回调只共享统一信封，且 evidence 处理、超时收敛与结果发布
分别由明确批处理入口负责；修订均保持在单一 Transport 聚合内。

**VERDICT:** ACCEPTED_DARK — Phase 4 已完成 Task 1～4 后端 QA 验收；当前仅可作为未接生产流量的暗能力，Phase 5 生产接线、
真实 WMS/RCS 联调和现场上线仍须分别验收。

NO UNRESOLVED DECISIONS
