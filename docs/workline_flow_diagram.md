# 工作线插件化编排完整流程

## 您的理解 vs 实际流程

### 您的理解

1. 【系统级】工作线设备上报事件, 通过设备获取所属工作线, 并获取该工作线关联的 plugin
2. 【系统级】ACK 流程, Inbox 流程
3. 【插件级】处理上报事件, 并通过设备拓扑获取下游设备
4. 【系统级】Outbox 流程, ACK 流程
5. 【插件级】处理 Outbox 事件
6. 如有需要, 重复 2~6

---

## 核心流程图（Mermaid）

### 1. 完整编排流程

```mermaid
flowchart TB
    subgraph External["外部系统"]
        Device[("设备<br/>MQTT/HTTP")]
        ExternalAPI[("外部系统<br/>HTTP")]
        Timer[("定时器<br/>Celery Beat")]
        Manual[("人工操作<br/>UI/API")]
    end

    subgraph Ingest["接入层"]
        Router["路由器<br/>解析设备ID → WorkLine → Plugin"]
        InboxWriter["写入 Inbox<br/>status=NEW"]
        HTTP202["返回 HTTP 202<br/>快速响应"]
    end

    subgraph Processing["处理层"]
        InboxQueue[("Inbox 队列<br/>status=NEW")]
        Lock["分布式锁<br/>session_id"]
        Orchestrator["编排器<br/>OrchestratorService"]
        
        subgraph PluginLayer["插件层"]
            PluginLoader["加载插件"]
            ContractCheck["契约版本校验"]
            ContextBuilder["构建 PluginContext"]
            PluginCall["调用插件方法"]
            PluginResult["返回 PluginResult"]
        end
        
        AtomicWriter["AtomicWriter<br/>原子写入"]
    end

    subgraph Dispatch["派发层"]
        OutboxQueue[("Outbox 队列<br/>status=NEW")]
        Dispatcher["派发器"]
        DeviceAPI["设备 API<br/>MQTT/HTTP"]
        ExternalAPIOut["外部系统 API"]
    end

    Device --> Router
    ExternalAPI --> Router
    Timer --> Router
    Manual --> Router
    
    Router --> InboxWriter
    InboxWriter --> HTTP202
    InboxWriter --> InboxQueue
    
    InboxQueue --> Lock
    Lock --> Orchestrator
    Orchestrator --> PluginLoader
    PluginLoader --> ContractCheck
    ContractCheck --> ContextBuilder
    ContextBuilder --> PluginCall
    PluginCall --> PluginResult
    PluginResult --> AtomicWriter
    
    AtomicWriter --> OutboxQueue
    OutboxQueue --> Dispatcher
    Dispatcher --> DeviceAPI
    Dispatcher --> ExternalAPIOut
    
    DeviceAPI -.->|命令结果| Device
    ExternalAPIOut -.->|回调结果| ExternalAPI
    
    Device -.->|产生新事件| Router
    ExternalAPI -.->|产生新事件| Router

    style External fill:#e1f5ff
    style Ingest fill:#fff3e0
    style Processing fill:#f3e5f5
    style Dispatch fill:#e8f5e9
    style PluginLayer fill:#fce4ec
```

### 2. Inbox/Outbox 数据流向

```mermaid
flowchart LR
    subgraph Input["输入（Inbox）"]
        Event1["DEVICE_EVENT<br/>设备事件"]
        Event2["COMMAND_RESULT<br/>命令结果"]
        Event3["EXTERNAL_HTTP<br/>外部回调"]
        Event4["TIMER_TIMEOUT<br/>超时"]
        Event5["MANUAL_*<br/>人工操作"]
    end

    Inbox[("Inbox<br/>统一消息入口")]
    
    subgraph Plugin["插件处理"]
        P1["on_device_event()"]
        P2["on_command_result()"]
        P3["on_external_http()"]
        P4["系统级 runtime reconciliation"]
        P5["on_manual_operation()"]
    end
    
    Result["PluginResult"]
    
    subgraph Output["输出（Outbox）"]
        Out1["DEVICE_COMMAND<br/>设备命令"]
        Out2["EXTERNAL_HTTP<br/>外部调用"]
        Out3["INTERNAL_SIGNAL<br/>内部信号"]
    end
    
    Outbox[("Outbox<br/>副作用派发")]
    
    Event1 --> Inbox
    Event2 --> Inbox
    Event3 --> Inbox
    Event4 --> Inbox
    Event5 --> Inbox
    
    Inbox --> P1
    Inbox --> P2
    Inbox --> P3
    Inbox --> P4
    Inbox --> P5
    
    P1 --> Result
    P2 --> Result
    P3 --> Result
    P4 --> Result
    P5 --> Result
    
    Result --> Out1
    Result --> Out2
    Result --> Out3
    
    Out1 --> Outbox
    Out2 --> Outbox
    Out3 --> Outbox

    style Input fill:#e3f2fd
    style Output fill:#f1f8e9
    style Plugin fill:#fce4ec
```

### 3. 完整时序图（SMT 扫码示例）

```mermaid
sequenceDiagram
    participant D as 设备（扫码枪）
    participant R as 路由器
    participant I as Inbox
    participant O as 编排器
    participant P as 插件
    participant W as AtomicWriter
    participant OB as Outbox
    participant A as 机械臂

    Note over D,A: T0: 设备上报事件
    D->>R: 上报 SCAN_COMPLETED<br/>{device_code, barcode}
    
    Note over R,I: T1: 系统级路由 + Inbox 写入
    R->>R: 解析 device_code → WorkLine → plugin_key
    R->>I: 写入 Inbox(status=NEW)
    R-->>D: HTTP 202 Accepted
    
    Note over I,P: T2: 异步处理 Inbox
    I->>O: 查询 status=NEW
    O->>O: 获取分布式锁(session_id)
    O->>I: 更新 status=PROCESSING
    
    O->>P: 加载插件实例
    O->>P: 校验契约版本
    O->>P: 构建 PluginContext
    
    Note over P: 根据 Inbox.kind 路由
    P->>P: on_device_event()
    P->>P: 业务逻辑：验证条码
    P->>P: 获取 devices_by_role["INPUT_ARM"]
    
    P-->>O: PluginResult<br/>{transition, commands}
    
    Note over O,W: T3: 原子写入
    O->>W: 更新 Session(context={stage: "WAITING_INSPECTION"})
    W->>W: 插入 Timeline 记录
    W->>W: 写入 Outbox(status=NEW)
    W->>I: 更新 status=PROCESSED
    O->>O: 释放锁
    
    Note over OB,A: T4: Outbox 派发
    OB->>OB: 查询 status=NEW
    OB->>OB: 解析 dispatch_type=DEVICE_COMMAND
    OB->>A: 调用机械臂 API (PICK_AND_PUT)
    OB->>OB: 更新 status=SENT
    
    Note over A: 机械臂执行中...
    
    Note over D,I: T5: 设备回调
    A->>R: 上报 COMMAND_RESULT<br/>{result: "SUCCESS"}
    R->>I: 写入 Inbox(status=NEW)
    
    Note over I,P: T6: 新一轮编排
    I->>O: 处理命令结果
    O->>P: on_command_result()
    P-->>O: PluginResult<br/>{transition: "pick_place_ok", complete: true}
    
    Note over D,A: 编排循环继续...
```

---

## 设备触发的两种来源（核心理解）

### 触发来源分类

```mermaid
flowchart TB
    subgraph Sources["设备触发来源"]
        direction TB
        
        subgraph Active["主动触发（设备自发）"]
            A1["传感器检测<br/>（扫码枪、光电开关）"]
            A2["设备状态变化<br/>（故障、急停、完成）"]
            A3["定时上报<br/>（心跳、状态同步）"]
        end
        
        subgraph Reactive["被动触发（命令驱动）"]
            R1["Outbox 下发命令"]
            R2["设备执行命令"]
            R3["回调上报结果<br/>（COMMAND_RESULT）"]
        end
        
        subgraph Other["其他触发源"]
            O1["人工操作<br/>（UI/API）"]
            O2["外部系统<br/>（WMS/MES 回调）"]
            O3["定时器<br/>（Celery Beat）"]
        end
    end
    
    Inbox[("Inbox<br/>统一入口")]
    
    Active --> Inbox
    Reactive --> Inbox
    Other --> Inbox
    
    Plugin["插件处理"]
    Outbox[("Outbox")]
    Device[("设备")]
    
    Inbox --> Plugin
    Plugin --> Outbox
    Outbox --> Device
    
    Device -.->|产生新事件| Active
    Device -.->|命令结果| R3

    style Active fill:#c8e6c9
    style Reactive fill:#bbdefb
    style Other fill:#fff9c4
```

### 两种触发来源的详细对比

```mermaid
sequenceDiagram
    participant D as 设备
    participant I as Inbox
    participant P as 插件
    participant OB as Outbox
    
    Note over D,OB: 场景1：设备主动触发（自发事件）
    rect rgb(200, 230, 201)
        D->>I: 上报 SCAN_COMPLETED<br/>{device_code, barcode}<br/><b>设备自己发起</b>
        I->>P: 触发 on_device_event()
        P->>P: 业务逻辑处理
        P->>OB: 返回命令意图
        OB->>D: 下发 PICK_AND_PUT
    end
    
    Note over D,OB: 场景2：命令驱动触发（被动响应）
    rect rgb(187, 222, 251)
        D->>I: 上报 COMMAND_RESULT<br/>{command_code, result: "SUCCESS"}<br/><b>命令执行后的回调</b>
        I->>P: 触发 on_command_result()
        P->>P: 解析结果，决定下一步
        P->>OB: 返回新命令或完成
    end
    
    Note over D,OB: 编排循环：命令 → 执行 → 回调 → 新命令 → ...
```

### 详细示例：SMT 粗分机的完整触发链

```mermaid
sequenceDiagram
    participant Scanner as 扫码枪
    participant I as Inbox
    participant P as 插件
    participant OB as Outbox
    participant Arm as 机械臂
    participant Conveyor as 流水线
    
    Note over Scanner,Conveyor: 主动触发：扫码枪检测到条码
    Scanner->>I: DEVICE_EVENT<br/>event_type=SCAN_COMPLETED<br/>barcode="ABC123"<br/><b>传感器触发</b>
    I->>P: on_device_event()
    P->>OB: CommandIntent(机械臂抓取)
    OB->>Arm: PICK_AND_PUT
    
    Note over Scanner,Conveyor: 被动触发：机械臂完成命令后回调
    Arm->>I: COMMAND_RESULT<br/>result="SUCCESS"<br/><b>命令执行结果</b>
    I->>P: on_command_result()
    P->>OB: CommandIntent(流水线前进)
    OB->>Conveyor: MOVE_FORWARD
    
    Note over Scanner,Conveyor: 被动触发：流水线完成命令后回调
    Conveyor->>I: COMMAND_RESULT<br/>result="SUCCESS"<br/><b>命令执行结果</b>
    I->>P: on_command_result()
    P->>P: 判断完成
    P-->>I: PluginResult(complete=true)
    
    Note over Scanner,Conveyor: 编排循环结束
```

### 触发来源分类表

| 触发类型 | 来源 | Inbox.kind | 示例 | 插件方法 |
|---------|------|-----------|------|---------|
| **主动触发** | 设备自发 | `DEVICE_EVENT` | 扫码完成、急停、故障 | `on_device_event()` |
| **被动触发** | 命令驱动 | `COMMAND_RESULT` | 机械臂完成、流水线到位 | `on_command_result()` |
| **外部系统** | WMS/MES | `EXTERNAL_HTTP` | AGV 到站、上层系统指令 | `on_external_http()` |
| **定时器** | 系统定时 | `TIMER_TIMEOUT` | Callback deadline 对账隔离 | 系统级 runtime reconciliation |
| **人工操作** | UI/API | `MANUAL_*` | 人工暂停、恢复、取消 | `on_manual_operation()` |

### 关键设计：编排循环

```mermaid
stateDiagram-v2
    [*] --> ActiveEvent: 设备主动触发
    
    state ActiveEvent "主动事件<br/>(SCAN_COMPLETED)"
    state Plugin "插件处理"
    state Command "下发命令"
    state Execute "设备执行"
    state Callback "命令结果回调<br/>(COMMAND_RESULT)"
    
    ActiveEvent --> Plugin
    Plugin --> Command: 返回 CommandIntent
    Command --> Execute: Outbox 派发
    Execute --> Callback: 执行完成
    
    state choice <<choice>>
    Callback --> choice
    
    state NewCommand "下发新命令"
    state Complete "完成"
    
    choice --> NewCommand: 还有下一步
    choice --> Complete: 流程结束
    
    NewCommand --> Execute
    
    Complete --> [*]
    
    note right of ActiveEvent
        触发类型1：设备自发
        - 传感器检测
        - 状态变化
    end note
    
    note right of Callback
        触发类型2：命令驱动
        - Outbox 下发
        - 设备执行后回调
    end note
```

### 核心洞察

```mermaid
mindmap
  root((设备触发来源))
    主动触发（设备自发）
      传感器检测
        扫码枪
        光电开关
        测厚仪
      设备状态变化
        急停按下
        设备故障
        任务完成
      定时上报
        心跳
        状态同步
    被动触发（命令驱动）
      Outbox 下发命令
      设备执行
      回调上报结果
        COMMAND_RESULT
        成功/失败
    编排循环
      主动事件 → 命令 → 回调 → 新命令 → ...
      状态驱动
      循环直到完成
```

**核心要点**：

1. **主动触发** = 设备自发事件
   - 来源：传感器、状态机、定时器
   - Inbox.kind = `DEVICE_EVENT`
   - 触发 `on_device_event()`

2. **被动触发** = 命令驱动回调
   - 来源：Outbox 下发命令的执行结果
   - Inbox.kind = `COMMAND_RESULT`
   - 触发 `on_command_result()`

3. **编排循环** = 主动 + 被动交替
   ```
   主动事件 → 下发命令 → 被动回调 → 下发新命令 → ... → 完成
   ```

---

## 关键修正点

### 1. Inbox 不是"系统级"，是**统一消息入口**

```mermaid
flowchart LR
    subgraph Sources["外部事件源"]
        D[设备]
        E[外部系统]
        T[定时器]
        M[人工操作]
    end
    
    Inbox[("Inbox<br/>统一消息入口<br/><b>所有外部消息都写这里</b>")]
    
    Sources --> Inbox
    
    Inbox -->|持久化| DB1[("持久化存储")]
    Inbox -->|幂等性| ID{"source_message_id<br/>去重"}
    Inbox -->|状态追踪| ST["NEW → PROCESSING<br/>→ PROCESSED/FAILED"]
    Inbox -->|重试| RT["失败后自动重试"]

    style Inbox fill:#ffeb3b,stroke:#f57c00,stroke-width:3px
```

### 2. 两个不同层次的 ACK

```mermaid
sequenceDiagram
    participant D as 设备
    participant R as 接入层路由器
    participant P as 编排器+插件
    participant OB as Outbox派发器
    
    Note over D,OB: 第一层 ACK：HTTP 202（接入层）
    D->>R: 上报事件
    R-->>D: ✅ HTTP 202 Accepted<br/><b>只确认"消息已接收"</b><br/>不等待处理完成
    
    Note over D,OB: 第二层 ACK：Outbox ACK（派发层）
    P->>OB: 派发命令到设备
    OB->>D: 执行命令
    D-->>OB: ✅ 命令执行成功<br/><b>确认"命令已被执行"</b>
    
    Note over D,OB: 闭环：命令结果产生新 Inbox
    D->>R: 上报命令结果
    R-->>D: HTTP 202
```

### 3. 插件不"处理 Outbox 事件"

```mermaid
flowchart TB
    Inbox[("Inbox<br/><b>输入队列</b>")]
    
    subgraph Plugin["插件（纯函数）"]
        P["插件方法<br/>on_device_event()<br/>on_command_result()<br/>..."]
    end
    
    Result["PluginResult<br/>{transition, commands, ...}"]
    
    Outbox[("Outbox<br/><b>输出队列</b>")]
    
    Device[("设备/外部系统")]
    
    Inbox -->|触发| P
    P --> Result
    Result -->|写入| Outbox
    Outbox -->|派发| Device
    
    Device -.->|回调产生新 Inbox| Inbox
    
    style Inbox fill:#4caf50,color:#fff
    style Outbox fill:#2196f3,color:#fff
    style Plugin fill:#ff9800
```

**关键理解**：
- ✅ **Inbox = 输入**：插件读取 Inbox
- ✅ **Outbox = 输出**：插件写入 Outbox
- ❌ **错误**：插件处理 Outbox（Outbox 是单向输出）

### 4. 设备拓扑获取流程

```mermaid
sequenceDiagram
    participant P as 插件
    participant C as PluginContext
    participant T as 设备拓扑<br/>(devices_by_role)
    participant O as 编排器
    participant D as 设备

    Note over P,D: 系统级能力（编排器准备）
    O->>C: 注入 devices_by_role<br/>{INPUT_ARM: [Device1, ...], OUTPUT_ARM: [Device2, ...]}
    
    Note over P,D: 插件级逻辑（业务选择）
    P->>C: 获取 ctx.devices_by_role
    C-->>P: 返回设备拓扑
    P->>P: 根据业务规则选择设备角色<br/>"INPUT_ARM"
    P->>P: 返回 CommandIntent<br/>{device_role: "INPUT_ARM", ...}
    
    Note over P,D: 系统级能力（自动解析设备 ID）
    O->>T: 将 device_role 解析为设备 ID
    T-->>O: Device1.id = 123
    O->>D: 调用设备 API

    Note over P: 插件只关心"角色"<br/>不关心"具体设备"
```

---

## 完整流程示例：SMT 粗分机扫码流程

### 场景：扫码枪上报扫码完成事件

```mermaid
stateDiagram-v2
    [*] --> T0: 设备上报事件
    
    T0 --> T1: 扫码枪上报<br/>SCAN_COMPLETED<br/>{device_code, barcode}
    
    state T1 {
        [*] --> 路由
        路由 --> 写入Inbox: 解析 WorkLine → plugin_key
        写入Inbox --> 返回202: status=NEW
    }
    
    T1 --> T2: Celery Task 异步处理
    
    state T2 {
        [*] --> 获取锁
        获取锁 --> 调用插件: session_id 锁
        调用插件 --> 处理结果: on_device_event()
    }
    
    T2 --> T3: 插件返回<br/>PluginResult
    
    state T3 {
        [*] --> 原子写入
        原子写入 --> 更新Session: stage=WAITING_INSPECTION
        更新Session --> 写入Outbox: DEVICE_COMMAND
        写入Outbox --> 更新Inbox: status=PROCESSED
    }
    
    T3 --> T4: Outbox 派发
    
    state T4 {
        [*] --> 调用机械臂
        调用机械臂 --> 等待执行: PICK_AND_PUT
    }
    
    T4 --> T5: 机械臂执行完成<br/>上报 COMMAND_RESULT
    
    T5 --> T6: 新一轮编排<br/>on_command_result()
    
    T6 --> [*]: 编排循环继续

    note right of T0
        外部事件到达
    end note
    
    note right of T2
        插件核心逻辑
        - 验证条码
        - 选择设备角色
        - 返回命令意图
    end note
    
    note right of T4
        副作用派发
        单向输出
    end note
```

---

## 架构层次图

```mermaid
graph TB
    subgraph ExternalLayer["外部层"]
        Device[("设备<br/>MQTT/HTTP")]
        External[("外部系统<br/>HTTP")]
        Timer[("定时器<br/>Celery Beat")]
        Manual[("人工操作<br/>UI/API")]
    end

    subgraph SystemLayer["系统层"]
        Ingest["接入层<br/>路由 + Inbox 写入"]
        Process["处理层<br/>编排器 + 插件调用"]
        Dispatch["派发层<br/>Outbox 派发"]
    end

    subgraph PluginLayer["插件层"]
        Plugin["插件<br/>业务逻辑"]
        StateMachine["状态机<br/>状态迁移"]
        Context["上下文<br/>Session + Devices"]
    end

    subgraph DataLayer["数据层"]
        Inbox[("Inbox<br/>消息队列")]
        Outbox[("Outbox<br/>命令队列")]
        Session[("Session<br/>会话状态")]
        Timeline[("Timeline<br/>因果链")]
    end

    Device --> Ingest
    External --> Ingest
    Timer --> Ingest
    Manual --> Ingest

    Ingest --> Inbox
    Inbox --> Process
    Process --> Plugin
    
    Plugin --> StateMachine
    Plugin --> Context
    
    Plugin --> Outbox
    Plugin --> Session
    Plugin --> Timeline
    
    Outbox --> Dispatch
    Dispatch --> Device
    Dispatch --> External

    style ExternalLayer fill:#e3f2fd
    style SystemLayer fill:#f3e5f5
    style PluginLayer fill:#fce4ec
    style DataLayer fill:#fff3e0
```

---

## 关键设计决策

### 1. 为什么用 Inbox/Outbox 模式？

```mermaid
mindmap
  root((Inbox/Outbox<br/>模式))
    外部系统不稳定
      网络故障
      超时
      重试机制
    并发处理冲突
      多事件同时到达
      分布式锁
      Session 级别隔离
    副作用可靠派发
      Outbox 确保不丢失
      原子写入
      事务保证
    因果链追溯
      Timeline 记录
      完整审计
      排障主视图
```

### 2. 为什么插件不直接操作设备？

```mermaid
flowchart LR
    subgraph Bad["❌ 错误设计"]
        P1["插件直接调用设备 API"]
        D1[("设备")]
        P1 --> D1
    end
    
    subgraph Good["✅ 正确设计"]
        P2["插件返回 CommandIntent"]
        O["编排器"]
        D2[("设备")]
        P2 --> O
        O --> D2
    end
    
    Good -.->|优势| A1["解耦：插件不关心通信细节"]
    Good -.->|优势| A2["可测试：无需 mock 设备"]
    Good -.->|优势| A3["原子性：事务统一写入"]
    Good -.->|优势| A4["可追溯：完整派发历史"]

    style Bad fill:#ffcdd2
    style Good fill:#c8e6c9
```

### 3. PluginContext 的作用

```mermaid
flowchart TB
    subgraph Context["PluginContext（依赖注入）"]
        S["session<br/>会话状态"]
        W["workline<br/>工作线配置"]
        D["devices_by_role<br/>设备拓扑"]
        SV["services<br/>领域服务"]
        L["logger<br/>日志记录器"]
    end
    
    Builder["PluginContextBuilder<br/>灵活构建"]
    
    Test["单元测试<br/>Mock 所有依赖"]
    Prod["生产环境<br/>真实依赖"]
    
    Builder --> Test
    Builder --> Prod
    
    Test --> Context
    Prod --> Context
    
    Plugin["插件方法"]
    Context --> Plugin

    style Context fill:#e1f5fe
    style Builder fill:#fff9c4
    style Plugin fill:#f8bbd0
```

---

## 总结：您的理解 vs 实际设计

| 您的理解 | 实际设计 | 评价 |
|---------|---------|------|
| 1. 【系统级】设备上报 → 获取工作线和插件 | ✅ 正确 | 接入层路由逻辑 |
| 2. 【系统级】ACK 流程，Inbox 流程 | ⚠️ 部分正确 | Inbox 是统一消息入口，HTTP 202 只是快速响应 |
| 3. 【插件级】处理上报事件，获取下游设备 | ✅ 正确 | 插件核心逻辑 |
| 4. 【系统级】Outbox 流程，ACK 流程 | ⚠️ 部分正确 | Outbox 是副作用派发，单向输出 |
| 5. 【插件级】处理 Outbox 事件 | ❌ 不正确 | 插件处理 Inbox，Outbox 是输出 |
| 6. 重复 2~6 | ✅ 正确 | 编排循环 |

**准确率**：**70%**（核心概念正确，细节需要补充）

### 核心要点

```mermaid
mindmap
  root((核心理解))
    Inbox = 输入
      所有外部消息
      统一入口
      触发插件
    Outbox = 输出
      插件返回结果
      副作用派发
      单向队列
    插件 = 纯函数
      Inbox → PluginResult
      不直接操作设备
      不处理 Outbox
    编排循环
      外部回调 → 新 Inbox
      持续处理
      状态驱动
```

**建议**：
- 区分 Inbox（输入）和 Outbox（输出）
- 理解两个不同层次的 ACK
- 插件是**纯函数**：Inbox → PluginResult → Outbox
