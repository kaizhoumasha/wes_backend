# P9 WES 作业线插件化编排与全链路追踪设计方案

> **文档版本**: 3.2
> **更新日期**: 2026-03-24
> **实施状态**: Phase 1 已完成 (100%) ✅, Phase 2 进行中 (30%) 🔄
> **评审状态**: ✅ 已通过架构评审  
> **适用范围**: P9 WES 作业线接入、设备编排、异步执行、链路追踪、排障归因  
> **目标**: 将当前“按设备类型分发”的实现，演进为“按作业线插件编排”的可实施架构

> **相关文档**:
> - 软件需求规格说明书: `../architecture/SRS.md`
> - 第三方设备接入白皮书: `../integration/third_party_integration_whitepaper.md`
> - 运行时语义 SSOT: `workline_business_data_event_flow_spec.md`

> **文档状态说明（2026-04-16）**:
> 本文保留了架构设计与分阶段落地思路，其中 Phase 百分比、完成度描述属于**设计阶段快照**，不应直接视为当前仓库实现状态；验收现状请以实际代码与 SSOT 文档为准。

> **口径修订（2026-03-25）**:
> 本文档保留作业线插件化、Inbox/Outbox、编排分层等架构设计内容；
> 涉及 `callback/event`、`callback/result`、`correlation_id`、`command_code`、`session_id`、
> 工作线边界、设备拓扑来源等运行时语义时，以 `workline_business_data_event_flow_spec.md` 为准。

---

## 1. 背景与目标

当前 P9 WES 已具备基础设备接入能力：

* 设备通过 `POST /api/v1/callback/event` 上报事件；目标口径是“立即 ACK + 写 Inbox + 异步编排”，当前代码中仍存在待收敛的旧异步处理链。
* WES 通过设备标准接口下发指令，设备同步 ACK，异步回传 `POST /api/v1/callback/result`。
* 当前代码已具备 `CallbackLog`、`DeviceCommand`、Celery Worker、设备处理器注册表等基础设施。

当前方案能支撑“单设备触发 -> 单设备动作”的简单场景，但无法稳定支撑真实产线：

* 一个业务流程通常跨多个设备协同完成，业务主语不是设备，而是作业线。
* 同一设备类型在不同作业线承担的业务角色不同，不能用 `device_type` 直接承载业务逻辑。
* 同一类作业线会有多个实例，如“装箱区-7#”、“装箱区-8#”。
* 同一套设备拓扑可能运行多种业务模式，如 SMT `INBOUND` / `OUTBOUND`。
* 现场需要快速区分硬件异常、网络异常、调度等待、算法无位、上游超时、人工介入等原因。

本方案的目标不是抽象出一个通用流程平台，而是在当前仓库能力基础上，落地一套：

* **可扩展**: 新作业线通过新增插件接入。
* **可追踪**: 任一条码、指令、设备、Session 都能反查完整链路。
* **可恢复**: 超时、结果回调、人工介入、外部系统回调都能回到同一编排主线。
* **可实施**: 可以按阶段增量上线，不要求一次性推翻现有系统。

---

## 2. 第一性原理与硬约束

### 2.1 业务事实

从业务本质出发，系统的核心事实只有四类：

1. **设备事件 (Event)**: 设备观察到的客观事实。
2. **设备指令 (Command)**: WES 请求设备执行的动作。
3. **流程实例 (Session)**: 一次业务处理的完整上下文。
4. **时间线 (Timeline)**: 对上述事实的结构化因果记录。

这是领域层必须稳定存在的四类对象。

### 2.2 分布式执行的实现事实

为了让上述四类业务事实在异步系统中可靠运行，还必须引入两类控制记录：

1. **编排收件箱 (Inbox)**: 所有进入编排器的输入统一先落库，再被异步消费。
2. **副作用发件箱 (Outbox)**: 所有对设备或外部系统的调用先持久化，再异步派发。

这两类不是业务对象，而是分布式可靠性对象。

### 2.3 系统边界

由此推导出系统边界：

* 设备层负责“看到什么、执行什么”。
* 作业线层负责“该走哪条业务流程”。
* 编排层负责“如何把事件、结果、超时、人工操作串成可恢复的主链路”。
* 追踪层负责“发生过什么、为什么失败、证据在哪里”。
* Inbox / Outbox 负责“异步系统如何不丢消息、不重复执行业务”。

### 2.4 硬约束

后续设计和编码必须遵循以下硬约束：

* **Callback API 只做接收、校验、原始落库、ACK、写 Inbox，不直接承载复杂业务逻辑。**
* **厂商协议枚举（命令 / 事件 / 结果）只能定义在对应 workline plugin 的 `contract.py`，禁止回流到 `src/app/device/models` 或 runtime 通用模型。**
* **插件不直接写 Repository，不直接调 HTTP Client，不直接构造 SQL。**
* **所有业务推进都必须回到统一编排入口，禁止出现平行状态机。**
* **所有副作用都必须通过 Outbox 产生，禁止在事务中直接发送设备命令或外部 HTTP。**
* **所有可恢复等待都必须持久化 deadline 和 wait payload，禁止“只改状态、不定义唤醒者”。**
* **所有主链路推进都必须具备幂等键和并发控制。**

---

## 3. 设计原则

### 3.1 DRY

以下横切能力必须统一实现，不能散落到插件中重复开发：

* ACK 处理
* 幂等检查
* `correlation_id` 透传
* Session 归属解析
* Timeline 记录
* 故障归因
* Timeout 扫描
* Outbox 派发与重试

### 3.2 KISS

本阶段采用最直接、最稳定的技术形态：

* 插件用 Python 类实现。
* 流程状态采用显式状态机建模。
* 编排器仍以 Celery Task 驱动。
* 不引入 BPMN、在线 DSL、可视化流程引擎。
* 不做插件热加载市场和脚本执行器。

### 3.3 SOLID

* **单一职责**:
  * Callback API 负责接入。
  * Orchestrator 负责流程推进。
  * Plugin 负责业务决策。
  * Dispatcher 负责发命令和外部请求。
  * Repository 负责持久化。
* **开放封闭**:
  * 新作业线通过新增插件接入，不修改编排器核心。
* **依赖倒置**:
  * 插件依赖 `PluginContext.services` 暴露的抽象服务，不依赖底层 HTTP 或数据库实现。

### 3.4 协议边界

必须明确区分三层协议对象：

* **Plugin Contract**
  * 厂商命令枚举
  * 厂商事件枚举
  * 厂商结果枚举
  * 字段别名、归一化、值域校验
* **Callback Minimal Envelope**
  * `device_code`
  * `event_type` / `result` 原始字符串
  * `timestamp`
  * `data`
  * 仅用于入站最小包络校验，不拥有厂商语义真相
* **Runtime Control Flow**
  * `WorklineInbox / Session / Timeline / Outbox`
  * `step_code / correlation_id / wait_token / dispatch_key`
  * 只表达控制流，不定义厂商协议枚举

禁止的实现方式：

* 在 `src/app/device/models` 中新增某条 workline 的 `EventType / ResultType / CommandType`
* 在 runtime 通用层维护某个厂商专属枚举
* callback 直接依赖设备域模型作为厂商协议真相

### 3.5 YAGNI

当前阶段不做以下能力：

* 通用流程 DSL
* 插件热插拔市场
* 图形化流程设计器
* 任意脚本在线编辑器
* 复杂多租户编排平台

---

## 4. 核心架构结论

### 4.1 业务逻辑绑定作业线，不绑定设备类型

错误做法：

* 把“装箱区逻辑”写在“读码器处理器”或“机械臂处理器”中。

正确做法：

* 业务逻辑绑定 `WorkLine` 实例。
* 设备只声明角色和能力。
* 插件根据 `workline + mode + inbox_kind + role` 决定流程。

### 4.2 事件、结果、超时、人工操作都要进入同一编排主线

以下输入全部进入统一编排器：

* `DEVICE_EVENT`
* `COMMAND_RESULT`
* `EXTERNAL_CALLBACK`
* `TIMEOUT`
* `MANUAL_OPERATION`

禁止出现如下分叉：

* `callback/event` 走编排器
* `callback/result` 只更新 `DeviceCommand`
* 超时由定时任务私自改 Session 状态
* 人工恢复直接修改数据库

### 4.3 Session 是业务主链路，Command / Callback 是证据

保留现有设备层证据：

* `CallbackLog`
* `DeviceCommand`

新增作业线主链路表：

* `WorklineSession`
* `WorklineTimeline`
* `DecisionLog`
* `ExternalCallLog`
* `WorklineInbox`
* `WorklineOutbox`

补充约束：

* 设备域不再维护厂商事件枚举或事件请求模型
* callback 层仅维护最小包络模型
* 厂商协议合法值以 plugin contract 为唯一准绳

### 4.4 插件返回“领域意图”，不直接操作基础设施

插件输出的是：

* 状态机触发器或目标迁移意图
* 上下文变更
* 待派发的设备动作
* 待调用的外部动作
* 业务决策结论
* 等待条件
* 失败结论

插件不直接输出：

* 原始 SQL
* 具体 Repository 调用
* Timeline 行对象
* 监控系统 API 调用

Timeline、Metrics、日志、落库动作都由编排层统一投影生成。

### 4.5 副作用必须通过 Outbox 执行

`Session` 状态推进和“下发设备命令 / 调用外部系统”是两个不同事务域。

因此必须采用：

* 事务内写 `WorklineSession`、`WorklineTimeline`、`DecisionLog`、`WorklineOutbox`
* 事务外由 `Dispatcher` 消费 Outbox
* 派发结果再通过回调或结果事件回到编排器

这样才能避免：

* Session 已推进，但命令没发出
* 外部系统已调用，但本地事务回滚
* Worker 崩溃导致动作丢失

---

## 5. 总体架构

```text
设备事件 / 指令结果 / 外部系统回调 / 人工操作 / 超时扫描
                        |
                        v
                  Callback / Job API
         - 请求校验
         - 幂等预校验
         - 原始日志落库
         - 写 WorklineInbox
         - 立即 ACK
                        |
                        v
              Celery: workline_orchestrator
         - 消费 Inbox
         - 解析 device -> workline -> plugin -> mode
         - 解析 / 创建 / 恢复 WorklineSession
         - 锁定 Session
         - 调用插件
         - 原子持久化 Session / Timeline / Decision / Outbox
                        |
                        v
                Celery: outbox_dispatcher
         - 读取 WorklineOutbox
         - 发送设备命令 / 外部 HTTP
         - 更新派发状态
                        |
                        v
       设备结果回调 / 外部回调 / 超时事件 / 人工操作回到 Inbox
```

---

## 6. 运行时契约

### 6.1 统一输入模型

所有进入编排器的输入统一落为 `WorklineInbox` 记录，并以统一字段口径进入编排器：

```python
class InboxKind(str, Enum):
    DEVICE_EVENT = "DEVICE_EVENT"
    COMMAND_RESULT = "COMMAND_RESULT"
    EXTERNAL_CALLBACK = "EXTERNAL_CALLBACK"
    TIMEOUT = "TIMEOUT"
    MANUAL_OPERATION = "MANUAL_OPERATION"


class WorklineInbox(BaseModel):
    inbox_id: int
    kind: InboxKind
    source_system: str
    source_message_id: str | None
    workline_id: int | None
    device_id: int | None
    command_id: int | None
    command_code: str | None
    session_id: int | None
    correlation_id: str | None
    event_time: datetime
    payload: dict
```

设计意图：

* `source_message_id` 用于来源系统幂等与来源侧追踪
* `correlation_id` 用于业务主链路追溯
* `command_code` 用于控制流归属
* `command_id` 若出现，仅表示内部数据库外键

* Callback 层只负责把外部请求归一化为 `WorklineInbox`。
* 编排器只消费统一输入模型，不关心来源 API。

### 6.2 Session 归属规则

这是整个方案必须先确定的运行契约。

#### 6.2.1 主键定义

* `WorklineSession.id`: 内部主键，所有系统内关联以此为准。
* `session_code`: 对外展示编码，便于查询和人工沟通。
* `business_key`: 作业线内业务归属键，用于“创建/恢复 Session”。

#### 6.2.2 business_key 生成原则

`business_key` 不是固定只用某一个扫码字段，而是：

* 必须由插件根据作业线业务定义。
* 对同一作业线内一次物理流程必须稳定。
* 可以是：
  * `LotCode`
  * `tray_code`
  * `LotCode + route_step`
  * `order_no + station_no`

插件需提供 `BusinessKeyResolver` 实现：

```python
from abc import ABC, abstractmethod

class BusinessKeyResolver(ABC):
    """业务键解析器基类 - 强制插件实现校验逻辑"""

    @abstractmethod
    def derive_business_key(self, inbox: "WorklineInbox") -> str | None:
        """从输入中提取业务键"""
        ...

    @abstractmethod
    def validate_business_key(self, business_key: str) -> bool:
        """校验业务键格式是否合法"""
        ...

    @abstractmethod
    def is_session_start(self, inbox: "WorklineInbox") -> bool:
        """判断是否为会话起始事件"""
        ...


class WorklinePlugin(Protocol):
    business_key_resolver: BusinessKeyResolver  # 类型安全的业务键解析器
    # ...
```

**设计意图**：

* 框架层强制插件实现校验逻辑，避免不同插件实现不一致
* 类型安全的接口便于 IDE 自动补全和静态检查
* 统一的校验入口便于框架层做日志和监控

#### 6.2.3 归属优先级

编排器按以下顺序归属 Session：

1. `inbox.session_id` 已给定，则直接命中该 Session。
2. `COMMAND_RESULT` 优先通过 `command_code -> DeviceCommand -> session_id` 命中；若运行时已显式解析出内部 `command_id`，可作为实现细节辅助恢复。
3. `TIMEOUT` 通过超时任务写入的 `session_id` 命中。
4. `MANUAL_OPERATION` 通过 UI 显式传入 `session_id` 命中。
5. `DEVICE_EVENT` / `EXTERNAL_CALLBACK` 通过 `workline_id + business_key + open_session` 命中。
6. 若插件判定为起始事件且不存在 open session，则创建新 Session。
7. 若不是起始事件且仍找不到 Session，则写入异常 Timeline，并标记 `ORCHESTRATION.NO_MATCHED_SESSION`。

#### 6.2.4 唯一约束

为避免一条物理流程生成多条主链路，建议增加以下约束：

* `WorklineSession(session_code)` 唯一。
* `WorklineSession(workline_id, business_key)` 上建立“仅对未终态 Session 生效”的部分唯一索引。
* `DeviceCommand(command_code)` 唯一。
* `WorklineInbox(idempotency_key)` 唯一。
* `WorklineOutbox(dispatch_key)` 唯一。

### 6.3 幂等模型

#### 6.3.1 接入层幂等

不同来源使用不同幂等键：

* `DEVICE_EVENT`: 优先使用厂商事件 ID；若无，则 `device_code + event_type + timestamp + payload_hash`
* `COMMAND_RESULT`: `command_code + result + finish_time + payload_hash`
* `EXTERNAL_CALLBACK`: `source_system + source_message_id`
* `MANUAL_OPERATION`: `session_id + operation_id`
* `TIMEOUT`: `session_id + wait_token + timeout_deadline`

接入层命中重复幂等键时：

* 返回成功 ACK
* 不重复写 Inbox
* 不重复推进业务

#### 6.3.2 编排层幂等

编排器消费 `WorklineInbox` 时必须保证：

* 一个 `inbox_id` 至多被成功处理一次。
* 因 worker 崩溃或重试导致的重复消费，最多重放，不得产生重复业务副作用。

建议通过以下组合实现：

* `WorklineInbox.status`: `NEW / PROCESSING / PROCESSED / FAILED`
* `processed_at` 与 `processor_token`
* Session 版本号校验
* Outbox `dispatch_key` 去重

### 6.4 并发控制

制造现场存在如下并发风险：

* 同一设备重复上报同一事件
* 结果回调和超时扫描同时命中同一 Session
* 人工操作与自动恢复同时推进同一状态

因此单条 Session 的推进必须遵循：

1. 先定位目标 Session。
2. 对 Session 行加锁。
3. 基于当前 `status + version + wait_token` 判断输入是否合法。
4. 只允许一个事务提交新的 Session 状态。

建议实现策略：

* 读取 Session 时使用 `SELECT ... FOR UPDATE`
* 更新时仍携带 `version` 做二次保护
* 冲突时按短退避重试
* 连续冲突超过阈值后记录 `ORCHESTRATION.CONCURRENT_MODIFICATION`

### 6.5 事务边界

#### 6.5.1 接入层事务

Callback API 的单次事务只做：

* 校验请求
* 写原始回调日志
* 解析设备 / 指令 / 作业线基础关联
* 写 `WorklineInbox`
* 返回 ACK

接入层事务**不做**：

* 执行业务决策
* 直接改 Session
* 直接发设备命令

#### 6.5.2 编排层事务

编排器的单次事务只做：

* 加载并锁定 Session
* 调用插件得到领域意图
* 更新 `WorklineSession`
* 追加 `WorklineTimeline`
* 记录 `DecisionLog`
* 必要时记录 `ExternalCallLog` 初始行
* 生成 `WorklineOutbox`
* 标记 `WorklineInbox` 已处理

编排层事务**不做**：

* 真正调用设备或外部 HTTP

#### 6.5.3 派发层事务

Dispatcher 的事务只做：

* 抢占待派发 Outbox
* 更新派发状态
* 写本次请求与响应证据

设备执行结果不在这里闭环，而是等待：

* 设备回调 `callback/result`
* 或外部系统回调
* 或 timeout 事件

### 6.6 Timeout 契约

任何进入等待态的 Session，都必须同时持久化以下字段：

* `wait_type`
* `wait_token`
* `waiting_since`
* `deadline_at`
* `wait_payload_json`

Timeout 由专门扫描任务负责产生：

* 周期性扫描 `deadline_at < now AND status in WAITING_*`
* 为每个过期等待生成一条 `WorklineInbox(kind=TIMEOUT)`
* 幂等键为 `session_id + wait_token + deadline_at`

这样可以保证：

* 超时事件可审计
* 超时与正常结果回调可以竞争同一 Session，但最终只会有一方提交成功
* Session 的恢复入口始终统一

### 6.7 人工介入契约

人工操作必须被视为结构化输入，而不是“人工改库”。

`ManualOperationDTO` 至少包含：

* `session_id`
* `operation_id`
* `operation_type`
* `operator_id`
* `operator_name`
* `remark`
* `payload`

建议的 `operation_type`：

* `RETRY_LAST_COMMAND`
* `MARK_SUCCESS_AND_CONTINUE`
* `MARK_NG_AND_CLOSE`
* `CANCEL_SESSION`
* `CUSTOM_ACTION`

人工操作进入 `WorklineInbox(kind=MANUAL_OPERATION)` 后，由插件决定：

* 是否允许当前状态接收该操作
* 该操作触发什么迁移
* 是否需要再次派发设备命令

---

## 7. 分层职责设计

### 7.1 Device Access Layer

职责：

* 接收设备事件和结果回调
* 接收外部系统回调
* 原始请求校验、鉴权、ACK
* 记录原始通信证据
* 生成 `WorklineInbox`

不负责：

* 作业线业务判断
* Session 状态推进
* 多设备协同编排

### 7.2 Workline Orchestration Layer

职责：

* 消费 `WorklineInbox`
* 解析 `device -> workline -> plugin -> mode`
* 创建 / 恢复 / 锁定 `WorklineSession`
* 调用插件进行业务决策
* 原子写入 `Session / Timeline / Decision / Outbox`
* 做统一故障归因

### 7.3 Scene Coordination Layer

这是对“线内设备编排”和“线外资源协同”的显式分层。

职责：

* 管理场景级资源协同，而不只是单线单设备动作
* 将 `RCS / AGV / CTU / WMS` 视为场景参与方，而不是普通设备
* 对外暴露统一的场景协同抽象，如：
  * `request_empty_shelf()`
  * `request_move_rack()`
  * `request_agv_replenishment()`
* 将跨线或跨工位的外部调度请求转换为 `ExternalRequestIntent`

边界：

* **业务上**: RCS 属于场景管理的一部分，因为其结果直接决定 Session 是否继续推进。
* **技术上**: RCS 仍是外部协同系统，不并入 WES 核心领域，不承载其内部路径规划和车队调度逻辑。

因此在 WES 中：

* 需要建模“什么时候请求 RCS”
* 需要建模“等待 RCS 什么结果”
* 需要建模“RCS 超时或失败时如何归因和恢复”

但不需要在 WES 中重建：

* RCS 路径规划
* AGV 交通管制
* 车队调度算法

### 7.4 Workline Dispatcher Layer

职责：

* 读取 `WorklineOutbox`
* 向设备发送命令
* 向外部系统发送请求
* 记录发送结果与重试信息

### 7.5 Domain Service Layer

职责：

* 提供领域能力，不感知 FastAPI / Celery
* 例如：
  * 扫码主标识 / Six-In-One 校验
  * 分箱算法
  * AGV 调度申请
  * WMS / RCS 接口适配

### 7.6 Traceability Layer

职责：

* 记录完整因果链
* 提供结构化查询、排障、审计、回放能力
* 产出监控指标基础数据

---

## 8. 领域模型设计

### 8.1 WorkLine

`WorkLine` 是业务编排边界，而不是简单的设备分组。

建议在现有 `work_lines` 表基础上增加字段：

* `plugin_key`: 如 `packing_zone`、`smt`、`return_area`
* `run_mode`: 如 `INBOUND`、`OUTBOUND`
* `topology_version`
* `config_json`
* `status`

约束：

* `plugin_key` 决定使用哪个插件
* `run_mode` 只影响新建 Session，不影响运行中的 Session
* `config_json` 必须由插件定义的 Pydantic 模型验证

### 8.2 Device

设备是作业线成员，不承载业务流程。

建议在现有 `devices` 表基础上增加字段：

* `device_role`: 如 `SCANNER`、`ROBOT_ARM`、`XRAY`
* `role_index`: 同角色多设备序号（如 `ROBOT_ARM_1`、`ROBOT_ARM_2`）
* `upstream_device_id`: 上游设备ID（线性拓扑，符合 KISS 原则）
* `capabilities`: 能力列表
* `vendor_type`: 厂商类型

关键原则：

* 插件按角色选设备，不按 `device_code` 写死设备
* 设备拓扑采用简单的线性模型（上游→下游），避免过度设计

#### 8.2.1 线性拓扑设计

**为什么选择简单模型？**

| 模型 | 复杂度 | WES 适用性 |
|------|--------|------------|
| **上游设备字段** | 低 | ✅ 大部分是线性流程 |
| 复杂拓扑配置 | 高 | ❌ 过度设计 |

**设备拓扑示例**：
```
装箱区-7#：
  SCANNER_1 → ROBOT_ARM_1 → ROBOT_ARM_2

设备表：
| id  | device_code    | device_role | role_index | upstream_device_id |
|-----|----------------|-------------|------------|-------------------|
| 101 | SCANNER_7_1    | SCANNER     | 1          | NULL              |
| 201 | ROBOT_ARM_7_1  | ROBOT_ARM   | 1          | 101               |
| 202 | ROBOT_ARM_7_2  | ROBOT_ARM   | 2          | 201               |
```

**查询下游设备**：
```python
async def get_downstream_devices(db: AsyncSession, device_id: int) -> list[Device]:
    """获取设备的下游设备列表"""
    result = await db.execute(
        select(Device).where(Device.upstream_device_id == device_id)
    )
    return result.scalars().all()
```

**插件使用示例**：
```python
class PackingZonePlugin:
    async def on_device_event(self, ctx: PluginContext, inbox: WorklineInbox) -> PluginResult:
        # 按角色和序号获取设备
        main_arm = ctx.get_device_by_role(DeviceRole.ROBOT_ARM, index=1)
        aux_arm = ctx.get_device_by_role(DeviceRole.ROBOT_ARM, index=2)

        if aux_arm:
            # 双臂协作：主臂抓取，辅助臂放置
            return PluginResult(
                commands=[
                    CommandIntent(target_device_id=main_arm.device_id, action="GRAB"),
                    CommandIntent(target_device_id=aux_arm.device_id, action="PLACE"),
                ]
            )
        else:
            # 单臂模式：抓取+放置
            return PluginResult(
                commands=[CommandIntent(target_device_id=main_arm.device_id, action="GRAB_AND_PLACE")]
            )
```

#### 8.2.2 处理并行设备

如果存在"一个设备对应多个下游"的场景（如分流传送带），在插件逻辑中处理：

```python
# 不需要复杂的拓扑配置
# 在插件中根据业务逻辑选择下游设备
class ConveyorPlugin:
    async def on_device_event(self, ctx: PluginContext, inbox: WorklineInbox) -> PluginResult:
        # 根据业务主键前缀决定走哪个分支
        if ctx.session.business_key.startswith("A"):
            target = ctx.get_device_by_role(DeviceRole.CONVEYOR, index=1)  # A线
        else:
            target = ctx.get_device_by_role(DeviceRole.CONVEYOR, index=2)  # B线

        return PluginResult(commands=[CommandIntent(target_device_id=target.device_id, ...)])
```

#### 8.2.3 设备表 Schema

```python
class Device(DataTableMixin, EnterpriseMixin, table=True):
    """设备表"""
    __tablename__ = "devices"

    name: str
    code: str                                    # 设备编码
    device_role: str                             # 角色：SCANNER, ROBOT_ARM, XRAY, ...
    role_index: int = 1                          # 同角色序号
    upstream_device_id: int | None = None        # 上游设备ID（外键）
    capabilities: list[str] = []                 # 能力列表
    vendor_type: str | None = None               # 厂商类型
    workline_id: int | None = None               # 所属作业线
    status: str = "ONLINE"                       # 状态：ONLINE, OFFLINE, FAULT

    # 关系
    workline: "WorkLine" = Relationship(back_populates="devices")
    upstream_device: "Device" = Relationship(
        sa_relationship_kwargs={"remote_side": "Device.id", "foreign_keys": "[Device.upstream_device_id]"}
    )
```

### 8.3 WorklineSession ✅ 已实现 (2026-03-17)

一条 `WorklineSession` 表示一次完整业务链路。

建议字段：

* `id`
* `session_code`
* `workline_id`
* `plugin_key`
* `run_mode`
* `business_key`
* `primary_identifier`
* `status`
* `context_json`
* `context_schema_version`
* `started_at`
* `ended_at`
* `correlation_id`
* `current_wait_type`
* `current_wait_token`
* `waiting_since`
* `deadline_at`
* `awaiting_command_id`
* `failure_domain`
* `failure_code`
* `failure_message`
* `failure_message`
* `last_inbox_id`

建议状态：

* `NEW`
* `RUNNING`
* `WAITING_DEVICE_RESULT`
* `WAITING_EXTERNAL`
* `MANUAL_HOLD`
* `COMPLETED`
* `FAILED`
* `CANCELLED`

说明：

* `status` 必须由插件定义的状态机管理
* `context_json` 由插件自己的上下文模型做版本化管理
* `awaiting_command_id` 若保留，仅表示内部数据库外键（`DeviceCommand.id`）
* 业务语义层仍应以 `command_code` 表示“正在等待哪条控制流命令结果”
* `last_inbox_id` 用于辅助重放和排障

### 8.4 WorklineTimeline ✅ 已实现 (2026-03-17)

`WorklineTimeline` 是排障主视图。

建议字段：

* `id`
* `session_id`
* `workline_id`
* `correlation_id`
* `seq_no`
* `occurred_at`
* `stage`
* `action_type`
* `actor_type`
* `actor_code`
* `from_status`
* `to_status`
* `status`
* `failure_domain`
* `message`
* `payload_json`
* `related_inbox_id`
* `related_command_id`  # 内部数据库外键；业务语义仍以 `command_code` 表达

建议 `stage`：

* `INGEST`
* `ROUTE`
* `DECISION`
* `DISPATCH_PREPARE`
* `WAITING`
* `CALLBACK`
* `MANUAL`
* `TIMEOUT`
* `COMPENSATION`
* `COMPLETE`
* `FAIL`

约束：

* `seq_no` 必须在同一 `session_id` 内单调递增
* Timeline 由编排层统一生成，插件不直接构造行对象

### 8.5 DecisionLog ❌ 未实现

记录关键业务判断过程。

建议字段：

* `id`
* `session_id`
* `workline_id`
* `decision_type`
* `step_code`
* `input_json`
* `output_json`
* `decision_result`
* `reason_code`
* `reason_text`
* `created_at`

适用场景：

* 条码是否可用
* 分箱算法是否找到位置
* 为什么请求 AGV
* 为什么进入 NG

### 8.6 ExternalCallLog ❌ 未实现

记录外部系统调用证据。

建议字段：

* `id`
* `session_id`
* `workline_id`
* `outbox_id`
* `service_name`
* `request_type`
* `request_payload`
* `response_payload`
* `http_status`
* `duration_ms`
* `result`
* `failure_domain`
* `error_message`
* `started_at`
* `finished_at`

### 8.7 WorklineInbox ✅ 已实现 (2026-03-17)

这是统一编排入口的持久化载体。

建议字段：

* `id`
* `kind`
* `idempotency_key`
* `source_system`
* `source_message_id`  # 来源系统消息标识，如 WMS request_id
* `workline_id`
* `device_id`
* `command_id`         # 可选内部数据库外键
* `command_code`       # 控制流主键
* `session_id`
* `correlation_id`
* `payload_json`
* `status`
* `processor_token`
* `received_at`
* `processed_at`
* `error_message`

建议状态：

* `NEW`
* `PROCESSING`
* `PROCESSED`
* `FAILED`

### 8.8 WorklineOutbox ✅ 已实现 (2026-03-17)

这是所有副作用的统一派发出口。

建议字段：

* `id`
* `session_id`
* `workline_id`
* `dispatch_type`
* `dispatch_key`
* `target_type`
* `target_code`
* `payload_json`
* `status`
* `attempt_count`
* `next_retry_at`
* `last_error`
* `created_at`
* `sent_at`
* `finished_at`

建议状态：

* `NEW`
* `DISPATCHING`
* `SENT`
* `ACKED`
* `FAILED`
* `CANCELLED`

`dispatch_type` 示例：

* `DEVICE_COMMAND`
* `EXTERNAL_HTTP`
* `INTERNAL_SIGNAL`

### 8.9 配置管理

`config_json` 和 `binding_config` 必须结构化。

规则：

1. 每个插件定义自己的 Pydantic 配置模型。
2. 作业线加载时按插件模型验证。
3. 验证失败时只禁用对应作业线，不阻断整个系统启动。
4. 插件收到的是类型安全的配置对象，而不是原始字典。

---

## 9. 插件化设计

### 9.1 插件目标

插件封装的是“作业线业务编排逻辑”，不是“设备协议”。

每个插件表示一种作业线模板，例如：

* `packing_zone`
* `smt`
* `return_area`

### 9.2 插件目录结构建议

```text
src/workline_plugins/
  base.py
  registry.py
  context.py
  types.py
  packing_zone/
    plugin.py
    state_machine.py
    config_models.py
  smt/
    plugin.py
    inbound.py
    outbound.py
  return_area/
    plugin.py
```

### 9.3 插件接口建议

```python
from typing import Protocol, Type
import pydantic


class WorklinePlugin(Protocol):
    """作业线插件协议 - 定义插件必须实现的接口"""

    # 插件标识
    plugin_key: str

    # 类型安全的配置模型
    config_model: Type[pydantic.BaseModel]
    binding_config_model: Type[pydantic.BaseModel]
    context_model: Type[pydantic.BaseModel]

    # 状态机类（基于 transitions 库）
    state_machine_class: type["WorklineStateMachine"]

    # 业务键解析器
    business_key_resolver: "BusinessKeyResolver"

    # 拓扑验证（启动时调用）
    async def validate_topology(self, ctx: "PluginBootstrapContext") -> None: ...

    # 事件处理入口
    async def on_device_event(self, ctx: "PluginContext", inbox: "WorklineInbox") -> "PluginResult": ...
    async def on_command_result(self, ctx: "PluginContext", inbox: "WorklineInbox") -> "PluginResult": ...
    async def on_external_callback(self, ctx: "PluginContext", inbox: "WorklineInbox") -> "PluginResult": ...
    async def on_timeout(self, ctx: "PluginContext", inbox: "WorklineInbox") -> "PluginResult": ...
    async def on_manual_operation(self, ctx: "PluginContext", inbox: "WorklineInbox") -> "PluginResult": ...
```

**设计说明**：

* `state_machine_class` 使用 `WorklineStateMachine` 子类（基于 transitions 库），提供成熟的回调机制
* `business_key_resolver` 使用 `BusinessKeyResolver` 基类，强制实现校验逻辑
* 插件不需要实现 `is_session_start` 和 `derive_business_key`，统一由 `BusinessKeyResolver` 处理

### 9.4 PluginContext 设计建议

`PluginContext` 至少包含：

* `workline`
* `session`
* `devices_by_role`
* `correlation_id`
* `config`
* `services`
* `logger`
* `clock`

插件只能通过 `services` 调用外部能力，不直接操作 Repository 或 HTTP Client。

### 9.5 PluginResult 设计建议

插件返回的是“领域意图”，建议结构如下：

```python
class PluginResult(BaseModel):
    transition: str | None = None
    context_patch: dict = Field(default_factory=dict)
    decisions: list[DecisionIntent] = Field(default_factory=list)
    commands: list[CommandIntent] = Field(default_factory=list)
    external_requests: list[ExternalRequestIntent] = Field(default_factory=list)
    wait: WaitIntent | None = None
    failure: FailureIntent | None = None
    complete: bool = False
```

说明：

* `transition`: 状态机触发器，而不是直接写死 `next_state`
* `context_patch`: 对 `session.context_json` 的增量修改
* `commands`: 待生成 `DeviceCommand` 与 `Outbox`
* `external_requests`: 待生成外部调用 `Outbox`
* `decisions`: 领域判断证据
* `wait`: 进入等待态时的等待定义
* `failure`: 失败归因

编排器根据 `PluginResult` 统一生成：

* Session 更新
* Timeline
* DecisionLog
* ExternalCallLog 初始记录
* Outbox
* Metrics

### 9.6 状态机管理

状态机必须是显式定义，不允许只靠 `if/elif` 隐式维护。

**使用 `transitions` 库**：项目应使用成熟的 [transitions](https://github.com/pytransitions/transitions) 库，而非自研状态机。

#### 9.6.1 依赖安装

```bash
uv add transitions
```

#### 9.6.2 状态机基类设计

```python
from transitions import Machine
from typing import Protocol, Any
from enum import Enum


class SessionStatus(str, Enum):
    “””通用 Session 状态枚举”””
    NEW = “NEW”
    RUNNING = “RUNNING”
    WAITING_DEVICE_RESULT = “WAITING_DEVICE_RESULT”
    WAITING_EXTERNAL = “WAITING_EXTERNAL”
    MANUAL_HOLD = “MANUAL_HOLD”
    COMPLETED = “COMPLETED”
    FAILED = “FAILED”
    CANCELLED = “CANCELLED”


class WorklineStateMachine(Machine):
    “””作业线状态机基类 - 基于 transitions 库”””

    def __init__(self, model: Any, initial: str | None = None, **kwargs):
        “””
        初始化状态机

        Args:
            model: 状态机绑定的模型对象（通常是 Session）
            initial: 初始状态，默认使用类定义的 initial_state
        “””
        super().__init__(
            model=model,
            states=self.get_states(),
            transitions=self.get_transitions(),
            initial=initial or self.get_initial_state(),
            auto_transitions=False,  # 禁用自动迁移，强制显式定义
            queued=True,  # 启用队列模式，支持回调中触发新迁移
            **kwargs
        )

    @classmethod
    def get_states(cls) -> list[str]:
        “””获取状态列表”””
        raise NotImplementedError(“子类必须实现 get_states()”)

    @classmethod
    def get_initial_state(cls) -> str:
        “””获取初始状态”””
        raise NotImplementedError(“子类必须实现 get_initial_state()”)

    @classmethod
    def get_transitions(cls) -> list[dict | list]:
        “””获取迁移规则列表”””
        raise NotImplementedError(“子类必须实现 get_transitions()”)

    def is_valid_trigger(self, trigger: str) -> bool:
        “””检查触发器是否有效”””
        return trigger in self.get_triggers(self.model.state)
```

#### 9.6.3 插件状态机实现示例

```python
from src.workline_plugins.base import WorklineStateMachine, SessionStatus


class PackingZoneStateMachine(WorklineStateMachine):
    “””装箱区状态机”””

    @classmethod
    def get_states(cls) -> list[str]:
        return [
            SessionStatus.NEW.value,
            SessionStatus.RUNNING.value,
            SessionStatus.WAITING_DEVICE_RESULT.value,
            SessionStatus.WAITING_EXTERNAL.value,
            SessionStatus.MANUAL_HOLD.value,
            SessionStatus.COMPLETED.value,
            SessionStatus.FAILED.value,
        ]

    @classmethod
    def get_initial_state(cls) -> str:
        return SessionStatus.NEW.value

    @classmethod
    def get_transitions(cls) -> list[dict | list]:
        return [
            # 简写格式: [trigger, source, dest]
            ['start', SessionStatus.NEW.value, SessionStatus.RUNNING.value],
            ['wait_device', SessionStatus.RUNNING.value, SessionStatus.WAITING_DEVICE_RESULT.value],
            ['wait_external', SessionStatus.RUNNING.value, SessionStatus.WAITING_EXTERNAL.value],
            ['device_success', SessionStatus.WAITING_DEVICE_RESULT.value, SessionStatus.COMPLETED.value],
            ['device_failed', SessionStatus.WAITING_DEVICE_RESULT.value, SessionStatus.MANUAL_HOLD.value],
            ['external_success', SessionStatus.WAITING_EXTERNAL.value, SessionStatus.RUNNING.value],
            ['external_failed', SessionStatus.WAITING_EXTERNAL.value, SessionStatus.MANUAL_HOLD.value],
            ['retry', SessionStatus.MANUAL_HOLD.value, SessionStatus.RUNNING.value],
            ['complete', SessionStatus.MANUAL_HOLD.value, SessionStatus.COMPLETED.value],

            # 完整格式: 支持 conditions, before, after 等回调
            {
                'trigger': 'fail',
                'source': '*',  # 通配符：任意状态都可失败
                'dest': SessionStatus.FAILED.value,
                'after': 'on_failure',  # 迁移后回调
            },
            {
                'trigger': 'cancel',
                'source': [SessionStatus.NEW.value, SessionStatus.RUNNING.value],
                'dest': SessionStatus.CANCELLED.value,
                'conditions': 'can_cancel',  # 条件检查
            },
        ]


# 使用示例
class SessionModel:
    “””Session 模型（简化示例）”””
    state: str = SessionStatus.NEW.value

    def on_failure(self):
        “””失败回调 - 记录日志、发送告警等”””
        print(f”Session failed at state: {self.state}”)

    def can_cancel(self) -> bool:
        “””取消条件检查”””
        return self.state in [SessionStatus.NEW.value, SessionStatus.RUNNING.value]


# 创建状态机
session = SessionModel()
machine = PackingZoneStateMachine(model=session)

# 触发迁移
session.start()  # NEW -> RUNNING
session.wait_device()  # RUNNING -> WAITING_DEVICE_RESULT
session.device_success()  # WAITING_DEVICE_RESULT -> COMPLETED

# 检查当前状态
print(session.state)  # “COMPLETED”
```

#### 9.6.4 回调机制

`transitions` 库提供完整的回调机制：

```python
# 迁移回调执行顺序
# 1. transition.prepare    - 迁移准备
# 2. transition.conditions - 条件检查（可阻断迁移）
# 3. transition.before     - 迁移前
# 4. state.on_exit         - 离开源状态
# 5. <STATE CHANGE>        - 状态变更
# 6. state.on_enter        - 进入目标状态
# 7. transition.after      - 迁移后
```

**回调示例**：

```python
transitions = [
    {
        'trigger': 'wait_device',
        'source': SessionStatus.RUNNING.value,
        'dest': SessionStatus.WAITING_DEVICE_RESULT.value,
        'prepare': ['setup_wait_context'],      # 准备阶段
        'conditions': 'has_device_available',   # 条件检查
        'before': ['log_wait_start'],           # 迁移前
        'after': ['schedule_timeout'],          # 迁移后
    },
]
```

#### 9.6.5 状态机校验规则

1. 每个插件必须提供 `WorklineStateMachine` 子类。
2. 编排器根据当前 `session.status` 加载状态机。
3. 插件返回 `transition` 后，由编排器调用 `is_valid_trigger()` 校验。
4. 非法迁移直接拒绝，并记录 `SOFTWARE.INVALID_TRANSITION`。
5. 状态机只负责”允许什么迁移”，不负责持久化。

#### 9.6.6 状态机校验工具

```python
def validate_state_machine(machine_class: type[WorklineStateMachine]) -> list[str]:
    “””
    校验状态机定义的完整性，返回错误列表

    Args:
        machine_class: 状态机类（非实例）

    Returns:
        错误消息列表，空列表表示校验通过
    “””
    errors = []
    states = machine_class.get_states()
    initial = machine_class.get_initial_state()
    transitions = machine_class.get_transitions()

    # 校验初始状态在状态列表中
    if initial not in states:
        errors.append(f”初始状态 '{initial}' 不在状态列表中”)

    # 校验所有迁移的源状态和目标状态都合法
    for t in transitions:
        if isinstance(t, dict):
            trigger = t.get('trigger')
            source = t.get('source')
            dest = t.get('dest')
        else:
            trigger, source, dest = t[0], t[1], t[2]

        # 检查源状态
        sources = [source] if isinstance(source, str) else source
        for s in sources:
            if s != “*” and s not in states:
                errors.append(f”迁移 '{trigger}' 的源状态 '{s}' 不在状态列表中”)

        # 检查目标状态
        if dest not in states:
            errors.append(f”迁移 '{trigger}' 的目标状态 '{dest}' 不在状态列表中”)

    # 校验所有状态都有可达路径
    reachable = {initial}
    changed = True
    while changed:
        changed = False
        for t in transitions:
            if isinstance(t, dict):
                source = t.get('source')
                dest = t.get('dest')
            else:
                source, dest = t[1], t[2]

            sources = [source] if isinstance(source, str) else source
            if any(s in reachable or s == “*” for s in sources) and dest not in reachable:
                reachable.add(dest)
                changed = True

    unreachable = set(states) - reachable
    if unreachable:
        errors.append(f”存在不可达状态: {unreachable}”)

    return errors
```

#### 9.6.7 transitions 库的优势

| 特性 | 自研状态机 | transitions 库 |
|------|------------|----------------|
| 成熟度 | 需要大量测试 | 10+ 年历史，广泛使用 |
| 回调机制 | 需自己实现 | 完整的 before/after/conditions |
| 可视化 | 需额外开发 | 内置 Graphviz 支持 |
| 嵌套状态 | 需自己实现 | 原生支持 HierarchicalMachine |
| 并发安全 | 需自己实现 | 支持 queued 模式 |
| 类型安全 | 弱 | 中等（experimental 支持更好） |
| 维护成本 | 高 | 低（社区维护）|

---

## 10. 运行时主流程

### 10.1 标准业务闭环

所有场景都应符合以下闭环模板：

1. **输入进入系统**
   * 设备事件、设备结果、RCS/WMS 回调、人工操作、超时事件进入 API 或扫描任务。
2. **接入层落 Inbox**
   * 计算幂等键
   * 记录原始请求证据
   * 写 `WorklineInbox`
   * 立即返回 ACK
3. **编排层恢复主链路**
   * 根据归属规则创建或恢复 `WorklineSession`
   * 锁定 Session
   * 调用插件
4. **插件输出领域意图**
   * 决策结论
   * 状态迁移
   * 设备动作
   * 外部调度请求
   * 等待条件
   * 失败结论
5. **编排层原子落库**
   * 写 `Session / Timeline / Decision / Outbox / Inbox`
6. **派发层执行副作用**
   * 设备命令发送给设备
   * 外部请求发送给 RCS / WMS / AGV 平台
7. **外部结果回流**
   * 设备结果回调
   * RCS / WMS 回调
   * 或超时扫描生成 `TIMEOUT`
8. **再次进入 Inbox**
   * 回到同一 Session
   * 推进到下一步或结束

如果一个流程步骤不能形成上述闭环，则说明该步骤定义不完整。

### 10.2 接入阶段

1. 设备 / 外部系统 / 人工终端调用 API，或定时任务扫描超时。
2. API 层完成鉴权、校验、幂等键计算。
3. 写原始日志和 `WorklineInbox`。
4. 返回 ACK。
5. 提交 Celery 任务消费对应 `inbox_id`。

### 10.3 编排阶段

1. Orchestrator 读取 `WorklineInbox`。
2. 解析 `device -> workline -> plugin -> mode`。
3. 根据归属规则创建或恢复 `WorklineSession`。
4. 锁定 Session。
5. 构建 `PluginContext`。
6. 根据 `inbox.kind` 调用插件入口。
7. 校验 `PluginResult.transition` 合法性。
8. 在单一事务中更新 `Session / Timeline / Decision / Outbox / Inbox`。

### 10.4 派发阶段

1. Dispatcher 抢占 `WorklineOutbox`。
2. 若是设备命令：
   * 创建或更新 `DeviceCommand`
   * 发送设备 HTTP 指令
   * 记录 ACK
3. 若是外部请求：
   * 调用 `scene coordination service`
   * 由其适配 RCS / WMS / AGV / CTU 协议
   * 记录请求与响应证据
4. 后续结果统一通过回调重新进入 `WorklineInbox`。

### 10.5 等待与恢复阶段

1. 若插件要求等待设备结果，则 Session 进入 `WAITING_DEVICE_RESULT`。
2. 若插件要求等待 RCS / WMS / AGV 等外部资源，则 Session 进入 `WAITING_EXTERNAL`。
3. 等待态必须同时写入：
   * `current_wait_type`
   * `current_wait_token`
   * `deadline_at`
   * `wait_payload`
4. 正常回调优先通过 `wait_token / command_code / source_message_id` 命中同一 Session。
5. 若超过 `deadline_at` 仍无回调，则 Timeout Scanner 生成 `TIMEOUT Inbox`。
6. 插件决定超时后是：
   * 重试
   * 转人工
   * 失败结束
   * 回滚到前一步

---

## 11. 典型场景

### 11.1 装箱区-7#

设备组成：

* `SCANNER`
* `ROBOT_ARM`

外部协同系统：

* `RCS`，负责 AGV / CTU 的调度

场景目标：

* 物料扫码后，系统为其分配可用库位
* 若无位，则必须向 `RCS` 发起调度，释放或搬运货架资源
* 资源就绪后再继续分箱和机械臂下发

关键业务判断：

* 条码是否合法
* 当前是否存在可用库位
* 若无位，是否需要请求 `RCS`
* `RCS` 返回后是否已形成新的可用库位
* 机械臂执行结果是否成功

#### 11.1.1 主成功路径

1. `SCANNER` 上报扫码结果，接入层写 `WorklineInbox(DEVICE_EVENT)`。
2. `packing_zone` 插件判定该事件为起始事件，并生成 `business_key=LotCode`（或该插件定义的主业务键）。
3. 编排器创建 `WorklineSession`，记录 `Timeline(stage=INGEST)`。
4. 插件完成扫码主标识校验，写入 `DecisionLog(decision_type=BARCODE_VALIDATION)`。
5. 插件调用分箱领域服务计算目标库位。
6. 若已找到可用库位：
   * 写 `DecisionLog(decision_type=BIN_ALLOCATION, decision_result=ALLOCATED)`
   * 生成 `CommandIntent(task_type=PUT_TO_BIN, target_role=ROBOT_ARM)`
   * Session 迁移到 `WAITING_DEVICE_RESULT`
   * 写等待信息：`wait_type=COMMAND_RESULT`
7. Dispatcher 发送机械臂命令，并创建 / 更新 `DeviceCommand`。
8. 机械臂执行完成后回调 `callback/result`，接入层写 `WorklineInbox(COMMAND_RESULT)`。
9. 编排器恢复同一 Session，插件校验结果成功。
10. Session 迁移到 `COMPLETED`，记录完成 Timeline。

#### 11.1.2 无位路径：必须请求 RCS

1. 执行到“分箱决策”步骤时，插件发现当前无可用库位。
2. 插件写 `DecisionLog(decision_type=BIN_ALLOCATION, decision_result=NO_AVAILABLE_BIN)`。
3. 插件生成 `ExternalRequestIntent`，语义为：
   * `service_name=RCS`
   * `request_type=DISPATCH_SHELF` 或同等业务语义
   * 请求 RCS 调度 AGV / CTU 搬运空货架、释放库位或调整货架位置
4. 编排层原子写入：
   * `DecisionLog`
   * `ExternalCallLog` 初始记录
   * `WorklineOutbox`
   * Session 迁移到 `WAITING_EXTERNAL`
   * `current_wait_type=RCS_DISPATCH`
   * `current_wait_token=<rcs_request_no>`
   * `deadline_at=<RCS响应超时时间>`
5. Dispatcher 通过 `scene coordination service` 调用 RCS。
6. RCS 受理后，可能异步回调“已完成货架搬运”或“调度失败”。
7. RCS 回调进入 `WorklineInbox(EXTERNAL_CALLBACK)`，并通过 `wait_token / source_message_id` 命中原 Session。
8. 插件收到 RCS 成功结果后，再次调用分箱领域服务重新计算库位。
9. 若此时已有位：
   * 写 `DecisionLog(decision_result=ALLOCATED_AFTER_RCS)`
   * 生成 `CommandIntent(PUT_TO_BIN)`
   * Session 迁移到 `WAITING_DEVICE_RESULT`
10. 若 RCS 失败或超时：
   * 记录 `failure_domain=UPSTREAM` 或 `TIMEOUT`
   * 由插件决定重试、转人工或失败结束

#### 11.1.3 关键原则

* “无位”不是流程结束，而是进入“场景资源协同”分支。
* 仅写 `DecisionLog` 不足以完成业务，必须同时产生面向 `RCS` 的调度请求。
* `RCS` 是场景协同参与方，不是普通设备处理器。
* `RCS` 回调必须回到同一 `WorklineSession`，否则链路会断裂。

### 11.2 SMT 双模式

核心特点：

* 同一套设备拓扑
* 两套业务逻辑：`INBOUND` / `OUTBOUND`

设计策略：

* 使用一个插件 `smt`
* 内部按 `run_mode` 分派到不同 handler

模式切换约束：

* 仅对新建 Session 生效
* 若当前存在 `RUNNING / WAITING_* / MANUAL_HOLD` Session，则禁止切换
* 切换必须记录审计日志和 Timeline

### 11.3 人在环路

当 Session 因机械臂抓取失败进入 `MANUAL_HOLD`：

* 操作终端展示结构化恢复选项
* 用户提交 `ManualOperationDTO`
* 系统写 `WorklineInbox(MANUAL_OPERATION)`
* 插件决定：
  * 重试上一步
  * 人工完成后继续
  * 进入 NG 并结束

---

## 12. 故障归因设计

### 12.1 failure_domain

统一枚举建议：

* `HARDWARE`
* `NETWORK`
* `SOFTWARE`
* `ORCHESTRATION`
* `ALGORITHM`
* `UPSTREAM`
* `DOWNSTREAM`
* `CONFIG`
* `DATA`
* `TIMEOUT`
* `MANUAL_INTERVENTION`

### 12.2 failure_code

统一编码建议：

* `HARDWARE.GRIPPER_TIMEOUT`
* `HARDWARE.DEVICE_BUSY`
* `NETWORK.DEVICE_ACK_TIMEOUT`
* `SOFTWARE.PLUGIN_EXCEPTION`
* `SOFTWARE.INVALID_TRANSITION`
* `ORCHESTRATION.NO_MATCHED_SESSION`
* `ORCHESTRATION.CONCURRENT_MODIFICATION`
* `ALGORITHM.NO_AVAILABLE_BIN`
* `UPSTREAM.WMS_TIMEOUT`
* `CONFIG.MISSING_DEVICE_ROLE`
* `DATA.INVALID_BARCODE`

### 12.3 归因原则

* 归因优先落在最贴近根因的边界
* 若暂时无法判定根因，可先落到 `ORCHESTRATION` 或 `SOFTWARE`，后续人工修订
* 同一 Session 的最终失败归因以最后一次失败结论为准，Timeline 保留中间证据

---

## 13. 查询、监控与报表

### 13.1 查询入口

必须支持以下查询：

* 按 `business_key`
* 按 `session_code`
* 按 `command_code`
* 按 `device_code`
* 按 `workline_id`
* 按 `failure_domain`

### 13.2 统一关联字段

以下字段建议贯穿核心表：

* `session_id`
* `workline_id`
* `correlation_id`
* `business_key`

### 13.3 TimescaleDB 使用建议

#### 13.3.1 Hypertable 配置

首批建议转成 Hypertable 的表：

| 表名 | 分区键 | 分区间隔 | 压缩策略 |
|------|--------|----------|----------|
| `workline_timeline` | `occurred_at` | 1 天 | 7 天后压缩 |
| `external_call_logs` | `started_at` | 1 天 | 30 天后压缩 |
| `decision_logs` | `created_at` | 1 周 | 90 天后压缩（可选） |

**创建 Hypertable 示例**：

```sql
-- Timeline 表（高频写入）
SELECT create_hypertable(
    'wes_biz.workline_timeline',
    'occurred_at',
    chunk_time_interval => INTERVAL '1 day'
);

-- 压缩策略（降低存储成本）
ALTER TABLE wes_biz.workline_timeline SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'session_id, workline_id'
);
SELECT add_compression_policy(
    'wes_biz.workline_timeline',
    INTERVAL '7 days'
);

-- 保留策略（自动清理历史数据）
SELECT add_retention_policy(
    'wes_biz.workline_timeline',
    INTERVAL '90 days'
);
```

#### 13.3.2 索引策略

```sql
-- Timeline 表索引
CREATE INDEX idx_timeline_session_time ON wes_biz.workline_timeline (session_id, occurred_at DESC);
CREATE INDEX idx_timeline_workline_time ON wes_biz.workline_timeline (workline_id, occurred_at DESC);
CREATE INDEX idx_timeline_stage ON wes_biz.workline_timeline (stage, occurred_at);
CREATE INDEX idx_timeline_correlation ON wes_biz.workline_timeline (correlation_id);

-- 部分索引（仅索引活跃 Session）
CREATE INDEX idx_session_active ON wes_biz.workline_sessions (workline_id, business_key)
WHERE status NOT IN ('COMPLETED', 'FAILED', 'CANCELLED');
```

YAGNI 约束：

* `WorklineSession` 不建议一开始做 Hypertable（状态表，查询模式不同）
* 不要求第一阶段就建设复杂看板，先把数据结构打通

### 13.4 核心指标

#### 13.4.1 Prometheus 指标定义

**Session 级别指标**：

```python
from prometheus_client import Counter, Histogram, Gauge

# Session 吞吐量
workline_session_total = Counter(
    'workline_session_total',
    'Session 总数',
    ['workline_id', 'plugin_key', 'run_mode', 'status']
)

# Session 端到端耗时
workline_session_duration = Histogram(
    'workline_session_duration_seconds',
    'Session 端到端耗时',
    ['workline_id', 'plugin_key'],
    buckets=[0.1, 0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600]
)

# Session 各状态停留时间
workline_session_state_duration = Histogram(
    'workline_session_state_duration_seconds',
    'Session 各状态停留时间',
    ['workline_id', 'status'],
    buckets=[0.1, 0.5, 1, 5, 10, 30, 60, 300, 600]
)
```

**Inbox/Outbox 指标**：

```python
# Inbox 处理延迟
workline_inbox_process_delay = Histogram(
    'workline_inbox_process_delay_seconds',
    'Inbox 从接收到处理的延迟',
    ['kind'],
    buckets=[0.01, 0.05, 0.1, 0.5, 1, 5, 10]
)

# Inbox 队列深度
workline_inbox_queue_depth = Gauge(
    'workline_inbox_queue_depth',
    'Inbox 待处理数量',
    ['kind', 'status']
)

# Outbox 派发延迟
workline_outbox_dispatch_delay = Histogram(
    'workline_outbox_dispatch_delay_seconds',
    'Outbox 从创建到派发的延迟',
    ['dispatch_type'],
    buckets=[0.01, 0.05, 0.1, 0.5, 1, 5]
)

# Outbox 重试次数
workline_outbox_retry_total = Counter(
    'workline_outbox_retry_total',
    'Outbox 重试总数',
    ['dispatch_type', 'target_type']
)
```

**外部调用指标**：

```python
# 外部调用延迟
workline_external_call_duration = Histogram(
    'workline_external_call_duration_seconds',
    '外部系统调用耗时',
    ['service_name', 'request_type'],
    buckets=[0.01, 0.05, 0.1, 0.5, 1, 5, 10, 30]
)

# 外部调用错误率
workline_external_call_errors = Counter(
    'workline_external_call_errors_total',
    '外部系统调用错误数',
    ['service_name', 'request_type', 'failure_domain']
)
```

**故障归因指标**：

```python
# 故障统计
workline_failure_total = Counter(
    'workline_failure_total',
    '故障总数',
    ['workline_id', 'failure_domain', 'failure_code']
)
```

#### 13.4.2 关键业务指标

| 指标名称 | 计算方式 | 预警阈值 |
|----------|----------|----------|
| Session 吞吐量 | `rate(workline_session_total[5m])` | 下降 > 50% |
| Session 成功率 | `completed / total` | < 95% |
| Session P99 耗时 | `histogram_quantile(0.99, ...)` | > 300s |
| Inbox 积压量 | `workline_inbox_queue_depth{status="NEW"}` | > 1000 |
| 外部调用错误率 | `errors / total` | > 5% |
| 设备命令超时率 | `timeout / total` | > 2% |

#### 13.4.3 Grafana 看板建议

1. **概览看板**：Session 吞吐量、成功率、P95/P99 耗时
2. **排障看板**：故障分布（按 domain/code）、Timeline 详情
3. **性能看板**：Inbox/Outbox 深度、处理延迟、外部调用延迟
4. **业务看板**：各作业线独立指标、按 business_key 查询链路

---

## 14. 与当前代码的关系

当前仓库可复用能力：

* Callback API
* Celery App
* `CallbackLog`
* `DeviceCommand`
* 设备命令发送服务
* `WorkLine / Device` 基础模型

当前仓库主要不足：

* 业务逻辑按 `device_type` 分发
* 目标设备编码硬编码在处理器中
* 缺少 `Session` 主链路
* `callback/result` 未继续驱动编排
* 缺少统一 `Timeline / Decision / ExternalCall`
* 缺少 Inbox / Outbox

重构原则：

* **保留设备通信与基础模型**
* **把当前设备处理器逐步下沉为“设备协议 / payload 归一化适配层”**
* **把业务编排上移到 workline plugin**

---

## 15. 推荐模块结构

### 15.1 新增运行时模块

```text
src/app/workline_runtime/
  models/
    workline_session.py
    workline_timeline.py
    decision_log.py
    external_call_log.py
    workline_inbox.py
    workline_outbox.py
  repositories/
  services/
    inbox_service.py
    session_service.py
    timeline_service.py
    decision_service.py
    outbox_service.py
    orchestrator_service.py
    dispatcher_service.py
    role_resolver_service.py
    state_machine_service.py
    timeout_service.py
  metrics/
    emitter.py
  v1/
```

### 15.2 新增插件目录

```text
src/workline_plugins/
  base.py                    # 插件基类和协议
  state_machine.py           # WorklineStateMachine 基类（基于 transitions）
  registry.py                # 插件注册表
  context.py                 # PluginContext 定义
  types.py                   # 类型定义
  packing_zone/
    plugin.py                # 插件实现
    state_machine.py         # PackingZoneStateMachine
    config_models.py         # 配置模型
  smt/
    plugin.py
    state_machine.py
    inbound.py
    outbound.py
  return_area/
    plugin.py
    state_machine.py
```

### 15.3 依赖要求

```toml
# pyproject.toml
[project.dependencies]
transitions = ">=0.9.0"  # 状态机库
```

### 15.4 建议拆分 Celery 任务

```text
src/celery_app/tasks/
  workline_ingest.py
  workline_orchestrator.py
  outbox_dispatcher.py
  timeout_scanner.py
```

### 15.5 建议新增脚本

```text
scripts/
  debug_plugin.py
```

`debug_plugin.py` 的目标：

* 直接构造 `PluginContext`
* 单线程同步执行插件逻辑
* Mock 掉外部依赖
* 打印 `PluginResult`

---

## 16. 分阶段落地方案

> **实施状态同步** (2026-03-24)
>
> | 阶段 | 状态 | 完成度 | 验证报告 |
> |------|------|--------|----------|
> | Phase 0: 建立运行契约 | ✅ 已完成 | 100% | - |
> | Phase 1: 打通统一入口和主链路 | ✅ 已完成 | 100% | 已合并到代码 |
> | Phase 2: 引入统一编排器 | ✅ 已完成 | 100% | [设计文档](~/.gstack/projects/workline-smt_coarse_ceparator/kaizhou-workline-smt_coarse_ceparator-design-phase2-orchestrator-20260324-101742.md) |
> | Phase 3: 引入插件和 Outbox | ⚠️ 模型就绪 | 20% | - |
> | Phase 4: 补齐决策证据 | ❌ 未开始 | 0% | - |
>
> **关键成果**：
> - ✅ 4 个核心表模型已创建：`WorklineSession`, `WorklineTimeline`, `WorklineInbox`, `WorklineOutbox`
> - ✅ 所有枚举类型使用 VARCHAR + CHECK 约束
> - ✅ 外键设计避免循环依赖（辅助追溯字段不设外键）
> - ✅ `Device`、`DeviceCommand`、`WorklineSession` 已承载 contract 快照；事件原始报文保存在 `callback_logs`
> - ✅ **Callback API 集成完成**：`callback/event` 和 `callback/result` 写入 WorklineInbox
> - ✅ **幂等性控制实现**：白皮书 6.3.1 节规范（厂商 ID 优先 + hash 备选）
> - ✅ **Inbox Service 完成**：创建设备事件和指令结果 Inbox 消息
> - ✅ **单元测试完成**：31 个测试全部通过（枚举测试 + 幂等键计算测试）
> - ✅ **Phase 2 运行时基础设施** (2026-03-24)：
>   - ✅ `RedisDistributedLock` - 分布式锁（12 测试通过）
>   - ✅ `PluginResult & Types` - 插件返回类型（18 测试通过）
>   - ✅ `PluginContext` - 插件上下文（7 测试通过）
>   - ✅ `NullPlugin` - 默认插件（10 测试通过）
> - ✅ **Phase 2 编排器核心服务** (2026-03-24)：
>   - ✅ `TransitionValidator` - 状态迁移校验（11 测试通过）
>   - ✅ `OrchestratorService` - 核心编排服务（13 测试通过）
>   - ✅ `InboxConsumer` - Celery Inbox 消费者（9 测试通过）
>   - ✅ `TimeoutScanner` - 超时扫描器（6 测试通过）
>   - ✅ `OutboxDispatcher` - Outbox 派发器（9 测试通过）
>   - ✅ `SessionResolver` - Session 归属解析器（10 测试通过）
>   - ✅ `PluginContextBuilder` - 插件上下文构建器（11 测试通过）
>   - ✅ `TimelineGenerator` - 时间线生成器（12 测试通过）
>   - ✅ `AtomicWriter` - 原子事务写入器（13 测试通过）
>
> **下一步**：Phase 3 插件化架构验证，实现第一个业务插件

### Phase 0: 建立运行契约 ✅ 已完成 (2026-03-17)

目标：

* 明确 `business_key` 规则
* 明确 `failure_domain / failure_code`
* 明确状态机建模规范
* 定义 Inbox / Outbox 表与枚举

产出：

* ✅ 文档冻结 (v3.1)
* ✅ 数据模型评审通过 (迁移已生成并验证)

### Phase 1: 打通统一入口和主链路 ✅ 已完成 (100%)

**已完成** (2026-03-17)：

* ✅ 新增 `WorklineSession` (8.3 节所有字段已实现)
* ✅ 新增 `WorklineTimeline` (8.4 节所有字段已实现)
* ✅ 新增 `WorklineInbox` (8.7 节所有字段已实现)
* ✅ 设备事件入口统一收敛到：
  * ✅ `callback_logs` 保留原始请求报文
  * ✅ `workline_inbox` 作为统一编排入口
  * ✅ `DeviceCommand` 作为控制流证据
* ✅ **Callback API 集成**：
  * ✅ `callback/event` 写 WorklineInbox
  * ✅ `callback/result` 写 WorklineInbox
  * ✅ 幂等性控制（白皮书 6.3.1 节）
* ✅ **Inbox Service 实现**：
  * ✅ `create_device_event_inbox()` - 创建设备事件 Inbox
  * ✅ `create_command_result_inbox()` - 创建指令结果 Inbox
  * ✅ `mark_as_processing()` - 标记为处理中
  * ✅ `mark_as_processed()` - 标记为已处理
  * ✅ `mark_as_failed()` - 标记为失败
* ✅ **单元测试完成**：31 个测试全部通过

上线收益：

* ✅ 具备完整链路追踪能力（数据模型层 + API 层）
* ✅ 设备事件和指令结果统一纳入 Inbox 编排入口
* ✅ 幂等性保证，防止重复处理
* ✅ 为 Phase 2 编排器服务奠定基础

### Phase 2: 引入统一编排器 ✅ 已完成 (100%)

**设计文档**: [Phase 2 Orchestrator Design](~/.gstack/projects/workline-smt_coarse_ceparator/kaizhou-workline-smt_coarse_ceparator-design-phase2-orchestrator-20260324-101742.md)

**已完成** (2026-03-24) - 运行时基础设施：

* ✅ **RedisDistributedLock** - Redis 分布式锁 (`src/workline_runtime/lock.py`)
  * 基于 Redis SET NX 实现互斥锁
  * 支持 TTL 自动过期（默认 30 秒）
  * 支持重试获取锁（100 次重试，100ms 间隔）
  * 支持自动续期（处理长时间运行的任务）
  * 支持 Redis 故障时降级到 PostgreSQL 行锁
  * 单元测试：12 个测试全部通过

* ✅ **PluginResult & Types** - 插件返回结果类型 (`src/workline_runtime/types.py`)
  * `WaitIntent` - 等待意图
  * `CommandIntent` - 设备命令意图
  * `FailureIntent` - 失败归因意图
  * `PluginResult` - 插件返回结果（领域意图集合）
  * 单元测试：18 个测试全部通过

* ✅ **PluginContext** - 插件上下文 (`src/workline_runtime/plugin_context.py`)
  * 包含 workline, session, devices_by_role 等核心实体
  * 包含 correlation_id 追踪信息
  * 包含 config, binding_config 配置
  * 包含 services 服务依赖容器
  * 包含 logger, clock 工具
  * 单元测试：7 个测试全部通过

* ✅ **NullPlugin** - Phase 2 默认插件 (`src/workline_runtime/null_plugin.py`)
  * 空实现插件，用于测试编排流程
  * 所有方法返回空的 PluginResult
  * 支持 on_device_event, on_command_result, on_timeout, on_external_http
  * 单元测试：10 个测试全部通过

* ✅ **TransitionValidator** - 状态迁移校验器 (`src/workline_runtime/transition_validator.py`)
  * Phase 2 默认行为：无状态机时允许所有迁移
  * Phase 3 状态机校验：使用 transitions 库验证迁移有效性
  * 返回 (is_valid, error_message) 元组
  * 单元测试：11 个测试全部通过

* ✅ **OrchestratorService** - 核心编排服务 (`src/workline_runtime/orchestrator.py`)
  * 支持分布式锁获取与释放
  * 支持插件加载与调用
  * 处理 PluginResult 各字段（transition, commands, wait, failure, complete）
  * 集成 TransitionValidator 验证状态迁移
  * 支持依赖注入 lock_provider（便于测试）
  * 单元测试：13 个测试全部通过

**已完成** (2026-03-24) - Celery 任务与编排组件：

* ✅ **InboxConsumer** - Inbox 消费者 (`src/celery_app/tasks/workline.py`)
  * 批量获取 Inbox 消息
  * 并发控制（processor_token）
  * 加载关联实体（Session, Workline, Devices）
  * 调用 OrchestratorService 处理
  * 单元测试：9 个测试全部通过

* ✅ **TimeoutScanner** - 超时扫描器 (`src/celery_app/tasks/workline.py`)
  * 扫描超时 Session
  * 创建 TIMEOUT Inbox 消息
  * 单元测试：6 个测试全部通过

* ✅ **OutboxDispatcher** - Outbox 派发器 (`src/celery_app/tasks/workline.py`)
  * 支持设备指令派发（DEVICE_COMMAND）
  * 支持外部 HTTP 调用（EXTERNAL_HTTP）
  * 支持内部信号派发（INTERNAL_SIGNAL）
  * 重试机制与指数退避
  * 单元测试：9 个测试全部通过

* ✅ **SessionResolver** - Session 归属解析器 (`src/workline_runtime/session_resolver.py`)
  * DEVICE_EVENT: 按 business_key 查找或创建 Session
  * EXTERNAL_HTTP: 按 correlation_id 恢复 Session
  * TIMER_TIMEOUT/MANUAL_*/REPLAY_REQUEST: 按 session_id 恢复 Session
  * 单元测试：10 个测试全部通过

* ✅ **PluginContextBuilder** - 插件上下文构建器 (`src/workline_runtime/plugin_context.py`)
  * 从 Workline 提取配置
  * 提供默认 logger 和 clock
  * 单元测试：11 个测试全部通过

* ✅ **TimelineGenerator** - 时间线生成器 (`src/workline_runtime/timeline_generator.py`)
  * 从 Session 提取关联信息
  * 自动设置 occurred_at 时间戳
  * 支持多种阶段类型
  * 单元测试：12 个测试全部通过

* ✅ **AtomicWriter** - 原子事务写入器 (`src/workline_runtime/atomic_writer.py`)
  * 单事务更新 Session、Timeline、Outbox、Inbox
  * 使用 PostgreSQL 序列生成单调递增 seq_no
  * 单元测试：13 个测试全部通过

**总测试数**: 151 个测试全部通过

目标：

* ✅ 新增 `orchestrator_service`
* ✅ 建立 Session 归属、并发控制、Timeline 生成机制
* ✅ 超时扫描写 `TIMEOUT Inbox`
* ✅ Outbox 派发机制（设备指令、外部 HTTP、内部信号）

上线收益：

* ✅ 统一推进路径形成闭环
* ✅ 支持分布式并发控制
* ✅ 支持插件化扩展
* ✅ 支持可靠的消息派发

### Phase 3: 引入插件和 Outbox ⚠️ 模型就绪 (20%)

**已完成** (2026-03-17)：

* ✅ 新增 `WorklineOutbox` (8.8 节所有字段已实现)

**待实现**：

* ❌ 新增 `dispatcher_service`
* ❌ 实现第一个插件 `packing_zone`
* ❌ 将装箱区业务从设备处理器迁移到插件

上线收益：

* ⚠️ 插件化架构尚未验证
* ❌ “事务内直接发命令”的一致性风险尚未消除

### Phase 4: 补齐决策证据与外部调用证据 ❌ 未开始

目标：

* 新增 `DecisionLog`
* 新增 `ExternalCallLog`
* 建立归因模型与查询接口

上线收益：

* 快速区分硬件、软件、调度、算法、上游系统问题

### Phase 5: 支持 SMT 双模式与开发工具链

目标：

* 实现 `smt` 插件
* 支持 `INBOUND / OUTBOUND`
* 提供 `debug_plugin.py`
* 补充插件单元测试模板

上线收益：

* 验证同拓扑多模式能力
* 降低插件开发与调试门槛

---

## 17. 编码实施约束

在后续架构分析、模块设计与编码时，必须遵循以下要求：

### 17.1 第一性原理

新增模型、服务、任务前先判断：

* 这是“业务事实”还是“分布式控制记录”
* 这是“设备层问题”还是“作业线层问题”
* 这是“状态推进”还是“副作用派发”

### 17.2 DRY

禁止在不同插件中重复实现：

* 幂等
* ACK 处理
* Session 归属
* 角色解析
* Timeline 记录
* 故障归因
* Timeout 扫描
* Outbox 重试

### 17.3 KISS

* 优先使用 Python 插件类
* 优先保持显式流程和显式状态机
* 优先做一条作业线先跑通
* 避免提前引入复杂流程平台

### 17.4 SOLID

* 插件不直接操作数据库底层细节
* Repository 不包含业务判断
* Adapter 不承担业务编排
* Celery Task 只作为入口和驱动，不承载复杂领域逻辑

### 17.5 YAGNI

仅实现当前业务明确需要的能力，不提前建设：

* 通用 DSL
* 热插拔脚本市场
* 图形化流程建模平台
* 复杂规则引擎
* 多租户流程平台

---

## 18. 未来展望

本方案为后续能力预留了明确演进路径，但不要求当前阶段一次性实现：

* 基于 `WorklineTimeline` 的仿真回放
* 基于历史 Session 的算法对比
* 基于 Outbox / Inbox 的数字孪生沙箱

这些能力建立在当前方案的三个前提之上：

* 业务主链路已统一到 `WorklineSession`
* 副作用已统一经过 Outbox
* 运行证据已统一经过 Timeline

---

## 20. 开发难度评估

### 20.1 技术栈要求

| 技术点 | 难度 | 团队能力要求 | 现有基础 |
|--------|------|--------------|----------|
| SQLModel/SQLAlchemy 2.0 | 中等 | 熟练使用异步 ORM | ✅ 已有 |
| Celery 异步任务 | 中等 | 理解任务分发和重试机制 | ✅ 已有 |
| Inbox/Outbox 模式 | 高 | 理解分布式事务和最终一致性 | ⚠️ 需学习 |
| 状态机建模 | 中等 | 有状态机设计经验 | ⚠️ 需规范 |
| 插件化架构 | 高 | 有插件框架设计经验 | ⚠️ 需设计 |
| TimescaleDB | 低 | 时序数据库基础 | ✅ 已有 |
| Prometheus 监控 | 低 | 指标定义和看板设计 | ✅ 已有 |

### 20.2 开发工作量估算

| 阶段 | 工作量 | 核心产出 | 风险点 |
|------|--------|----------|--------|
| Phase 0 | 1 周 | 文档冻结、数据模型评审 | 需求不明确 |
| Phase 1 | 2 周 | WorklineSession/Timeline/Inbox 表结构 | 数据库迁移 |
| Phase 2 | 3 周 | 编排器核心、Session 归属、并发控制 | 并发测试 |
| Phase 3 | 3 周 | Outbox/Dispatcher/第一个插件 | 业务迁移 |
| Phase 4 | 2 周 | DecisionLog/ExternalCallLog/归因模型 | 数据量 |
| Phase 5 | 2 周 | SMT 双模式/调试工具 | 模式切换 |

**总计**：约 13 周（3 个月），需要 2-3 人的核心团队。

### 20.3 风险评估与缓解

| 风险 | 影响 | 概率 | 缓解措施 |
|------|------|------|----------|
| **业务逻辑迁移复杂** | 高 | 高 | 先在新架构跑一个简单作业线，逐步迁移 |
| **并发场景测试不足** | 高 | 中 | 专门的压力测试和混沌工程 |
| **插件开发门槛** | 中 | 中 | 提供 `debug_plugin.py` 和单元测试模板 |
| **外部系统回调幂等** | 中 | 中 | 在接入层实现统一的幂等检查 |
| **TimescaleDB 性能** | 低 | 低 | 提前做压测，准备压缩和清理策略 |
| **状态机定义错误** | 中 | 中 | 提供 `validate_state_machine()` 校验工具 |

### 20.4 团队技能建议

**核心开发者画像**：

1. **架构师（1人）**：
   - 精通分布式系统和异步架构
   - 熟悉 Inbox/Outbox 模式
   - 有状态机设计经验

2. **后端开发（1-2人）**：
   - 熟练 Python/FastAPI/SQLModel
   - 有 Celery 异步任务经验
   - 了解 PostgreSQL 事务隔离级别

3. **测试工程师（兼职）**：
   - 有并发测试经验
   - 会使用 Locust 或 k6 做压力测试

### 20.5 开发优先级建议

**P0（必须）**：
1. Phase 1：打通链路追踪（先验证数据模型）
2. Phase 2：统一编排入口（Session 归属、并发控制）
3. Phase 3：第一个插件（选择最简单的作业线）

**P1（重要）**：
4. Phase 4：决策证据和归因（提升排障效率）
5. Phase 5：开发工具链（降低插件开发门槛）

**P2（可选）**：
6. 复杂作业线迁移
7. SMT 双模式
8. 高级监控看板

### 20.6 验收标准

| 阶段 | 验收标准 |
|------|----------|
| Phase 1 | 能通过 business_key 查询完整链路（Session → Timeline → Command） |
| Phase 2 | 并发 100 Session 无数据丢失，超时事件正确触发 |
| Phase 3 | 第一个插件上线运行，业务闭环无异常 |
| Phase 4 | 能在 1 分钟内定位故障根因（domain + code） |
| Phase 5 | 新插件开发时间 < 2 天（含单元测试） |

---

## 21. 结论

### 21.1 架构评审结论

本设计方案经过架构评审，**评审通过**，主要结论如下：

| 维度 | 评分 | 说明 |
|------|------|------|
| **架构完整性** | ⭐⭐⭐⭐⭐ | 第一性原理推导清晰，四类业务事实定义准确 |
| **可实施性** | ⭐⭐⭐⭐ | 分阶段落地路径明确，部分细节实现复杂度较高 |
| **开发难度** | ⭐⭐⭐⭐ | 中高难度，需要团队具备分布式系统和领域建模经验 |
| **原则遵循** | ⭐⭐⭐⭐⭐ | DRY/KISS/SOLID/YAGNI 执行到位 |

### 21.2 核心实施方案

最适合 P9 WES 当前阶段的可实施方案是：

* 保留现有 `CallbackLog` 和 `DeviceCommand` 作为设备层证据。
* 新增 `WorklineSession` 作为业务主链路，其状态由插件定义的显式状态机管理。
* 新增 `WorklineTimeline` 作为排障主视图。
* 新增 `WorklineInbox` 作为统一编排入口，解决异步输入可靠消费问题。
* 新增 `WorklineOutbox` 作为统一副作用出口，解决状态推进与动作派发的一致性问题。
* 用"作业线插件"替代"按设备类型硬编码业务"，用"设备角色绑定"替代"把业务绑到设备编码"。
* 用结构化 `failure_domain + failure_code` 提供可检索、可统计、可审计的故障归因能力。
* 用 `debug_plugin.py` 和插件单元测试模板降低后续开发成本。

### 21.3 关键落地闭环

该方案既保留了当前仓库的可复用资产，又补齐了真实产线系统最关键的四个落地闭环：

* `Session 归属`
* `并发与幂等`
* `事务与 Outbox`
* `Timeout / 人工恢复`

从实施角度看，这是一条可增量演进、可上线验证、可逐步替换旧逻辑的路线，而不是一次性重构。
