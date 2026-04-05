# 系统能力 vs 插件能力边界

## 核心原则

**插件开发者只关注业务逻辑，框架提供所有系统级能力。**

---

## 能力分层图

```mermaid
flowchart TB
    subgraph SystemLayer["系统级能力（框架提供）"]
        direction TB
        
        subgraph Ingestion["接入能力"]
            I1["路由解析<br/>设备ID → WorkLine → Plugin"]
            I2["Inbox 写入<br/>幂等性、持久化"]
            I3["HTTP 202<br/>快速响应"]
        end
        
        subgraph Concurrency["并发控制"]
            C1["分布式锁<br/>Redis/PostgreSQL"]
            C2["锁自动续期<br/>防止死锁"]
            C3["并发隔离<br/>Session 级别"]
        end
        
        subgraph Orchestration["编排能力"]
            O1["插件加载<br/>惰性实例化"]
            O2["契约版本校验<br/>向后兼容"]
            O3["PluginContext 构建<br/>依赖注入"]
            O4["事件路由<br/>kind → method"]
        end
        
        subgraph Transaction["事务能力"]
            T1["AtomicWriter<br/>原子写入"]
            T2["Session 更新<br/>状态 + 上下文"]
            T3["Timeline 记录<br/>因果链"]
            T4["Outbox 写入<br/>副作用队列"]
        end
        
        subgraph Dispatch["派发能力"]
            D1["Outbox 派发<br/>可靠投递"]
            D2["设备 API 调用<br/>MQTT/HTTP"]
            D3["外部系统调用<br/>HTTP"]
            D4["重试机制<br/>失败恢复"]
        end
        
        subgraph StateMachine["状态机能力"]
            S1["状态迁移校验<br/>前置状态检查"]
            S2["迁移拦截<br/>非法迁移拒绝"]
            S3["通配符迁移<br/>错误处理"]
        end
    end
    
    subgraph PluginLayer["插件级能力（开发者实现）"]
        direction TB
        
        subgraph BusinessLogic["业务逻辑"]
            B1["事件处理<br/>on_device_event()"]
            B2["命令结果处理<br/>on_command_result()"]
            B3["超时处理<br/>on_timeout()"]
            B4["人工操作处理<br/>on_manual_operation()"]
        end
        
        subgraph BusinessRules["业务规则"]
            R1["数据验证<br/>条码格式、参数校验"]
            R2["业务判断<br/>OK/NG、路由决策"]
            R3["设备选择<br/>根据角色选择设备"]
            R4["状态决策<br/>下一步动作"]
        end
        
        subgraph Response["响应构建"]
            E1["状态迁移<br/>transition"]
            E2["命令意图<br/>CommandIntent"]
            E3["等待条件<br/>WaitIntent"]
            E4["失败归因<br/>FailureIntent"]
        end
    end
    
    Inbox[("Inbox<br/>输入")]
    Outbox[("Outbox<br/>输出")]
    
    Inbox --> SystemLayer
    SystemLayer --> PluginLayer
    PluginLayer --> SystemLayer
    SystemLayer --> Outbox

    style SystemLayer fill:#e3f2fd,stroke:#1976d2,stroke-width:3px
    style PluginLayer fill:#fce4ec,stroke:#c2185b,stroke-width:3px
```

---

## 详细对比表

| 能力维度 | 系统级能力（框架） | 插件级能力（开发者） |
|---------|------------------|-------------------|
| **接入** | ✅ 路由解析、Inbox 写入、HTTP 202 | ❌ 不关心 |
| **并发** | ✅ 分布式锁、自动续期、Session 隔离 | ❌ 不关心 |
| **事务** | ✅ AtomicWriter、原子写入、因果链 | ❌ 不关心 |
| **派发** | ✅ Outbox 派发、设备 API、重试 | ❌ 不关心 |
| **状态机** | ✅ 状态校验、迁移拦截 | ⚠️ 声明迁移意图（transition） |
| **设备拓扑** | ✅ 解析设备角色 → 设备 ID | ⚠️ 选择设备角色 |
| **事件路由** | ✅ Inbox.kind → 插件方法 | ❌ 不关心 |
| **Payload 解析** | ✅ 自动解析、Pydantic 验证 | ⚠️ 定义 Schema |
| **日志** | ✅ 自动注入 session_id、correlation_id | ⚠️ 调用 ctx.logger |
| **业务逻辑** | ❌ 不关心 | ✅ 条码验证、OK/NG 判断 |
| **业务规则** | ❌ 不关心 | ✅ 路由决策、设备选择 |
| **异常处理** | ✅ 框架异常捕获、Inbox 失败标记 | ⚠️ 业务异常返回 FailureIntent |

**图例**：
- ✅ 完全由框架提供
- ⚠️ 开发者声明/使用，框架实现
- ❌ 不关心/不负责

---

## 插件开发者需要做的 vs 不需要做的

### ✅ 需要做的（业务逻辑）

```mermaid
mindmap
  root((插件开发者<br/>只需关注业务))
    数据验证
      条码格式校验
      参数合法性检查
      业务约束验证
    业务判断
      OK/NG 判定
      路由决策
      流程分支选择
    设备选择
      根据角色选择设备
      指定命令类型
      设置命令参数
    状态决策
      下一步动作
      迁移意图
      等待条件
    异常归因
      失败原因
      错误码
      错误消息
```

### ❌ 不需要做的（系统能力）

```mermaid
mindmap
  root((框架自动处理<br/>开发者无需关心))
    接入层
      设备ID解析
      WorkLine查询
      Plugin加载
      Inbox写入
    并发控制
      分布式锁获取
      锁续期
      死锁预防
    事务管理
      原子写入
      Session更新
      Timeline记录
      Outbox写入
    派发机制
      设备API调用
      外部系统调用
      重试逻辑
    状态机
      前置状态校验
      非法迁移拦截
      通配符处理
    设备拓扑
      角色解析为设备ID
      设备可用性检查
```

---

## 代码对比：传统方式 vs 简化方式

### 传统方式（需要关心系统级细节）

```python
class SmtClassifierPlugin:
    async def on_device_event(self, ctx, inbox):
        # ❌ 开发者需要关心：Payload 解析
        payload = inbox.payload_json or {}
        device_code = payload.get("device_code")
        barcode = payload.get("barcode") or payload.get("data", {}).get("barcode")
        
        if not device_code or not barcode:
            # ❌ 需要手动构建错误响应
            result = PluginResult()
            result.failure = FailureIntent(
                domain="DATA",
                code="MISSING_FIELD",
                message="缺少必要字段"
            )
            return result
        
        # ✅ 业务逻辑：验证条码
        if not self._validate_barcode(barcode):
            result = PluginResult()
            result.transition = "scan_ng"
            result.failure = FailureIntent(
                domain="DATA",
                code="BARCODE_NG",
                message=f"条码格式错误: {barcode}"
            )
            return result
        
        # ❌ 需要关心：设备拓扑解析
        devices_by_role = ctx.devices_by_role
        input_arm = devices_by_role.get("INPUT_ARM", [])
        if not input_arm:
            result = PluginResult()
            result.failure = FailureIntent(
                domain="ORCHESTRATION",
                code="DEVICE_NOT_FOUND",
                message="未配置 INPUT_ARM 设备"
            )
            return result
        
        device = input_arm[0]
        
        # ❌ 需要关心：设备 ID 解析
        result = PluginResult()
        result.transition = "scan_ok"
        result.commands = [
            CommandIntent(
                target_device_id=device.id,  # 手动解析
                action="PICK_AND_PUT",
                parameters={"barcode": barcode}
            )
        ]
        result.context_patch = {"last_barcode": barcode}
        return result
```

**问题**：
- ❌ 大量系统级细节（Payload 解析、设备拓扑、设备 ID）
- ❌ 样板代码过多（~40 行中只有 5 行是业务逻辑）
- ❌ 容易出错（手动解析、错误处理）

### 简化方式（只关心业务逻辑）

```python
class SmtClassifierPlugin(WorklinePlugin):
    @on_event("SCAN_COMPLETED")
    @step("IDLE", "WAITING_INSPECTION")
    async def handle_scan(self, ctx, event: ScanEventPayload) -> PluginResult:
        # ✅ Payload 自动解析和验证（Pydantic）
        # ✅ 设备拓扑已注入 ctx.devices_by_role
        # ✅ 状态校验自动完成
        
        # ✅ 业务逻辑：验证条码（核心逻辑）
        if not self._validate_barcode(event.barcode):
            return (
                PluginResultBuilder(ctx)
                .transition("scan_ng")
                .failure(domain="DATA", code="BARCODE_NG", 
                        message=f"条码格式错误: {event.barcode}")
                .build()
            )
        
        # ✅ 业务逻辑：派发命令（设备角色自动解析）
        return (
            PluginResultBuilder(ctx)
            .transition("scan_ok")
            .command(
                device_role="INPUT_ARM",  # 框架自动解析为设备 ID
                command_type="PICK_AND_PUT",
                parameters={"barcode": event.barcode}
            )
            .context({"last_barcode": event.barcode})
            .build()
        )
```

**改进**：
- ✅ 只关注业务逻辑（验证条码、选择设备角色）
- ✅ 代码减少 70%（40 行 → 12 行）
- ✅ 类型安全（Pydantic 自动验证）
- ✅ 声明式（装饰器 + Builder）

---

## 系统能力注入机制

```mermaid
sequenceDiagram
    participant Framework as 框架
    participant PluginContext as PluginContext
    participant Plugin as 插件
    
    Note over Framework,Plugin: 框架自动注入系统级能力
    Framework->>PluginContext: 注入 session（会话状态）
    Framework->>PluginContext: 注入 workline（工作线配置）
    Framework->>PluginContext: 注入 devices_by_role（设备拓扑）
    Framework->>PluginContext: 注入 services（领域服务）
    Framework->>PluginContext: 注入 logger（日志记录器）
    
    Framework->>Plugin: 调用插件方法
    Plugin->>PluginContext: 获取 ctx.devices_by_role
    PluginContext-->>Plugin: 返回设备拓扑
    Plugin->>Plugin: 业务逻辑：选择设备角色
    Plugin-->>Framework: 返回 PluginResult
    
    Note over Framework,Plugin: 框架自动处理系统级细节
    Framework->>Framework: 解析 device_role → 设备 ID
    Framework->>Framework: 原子写入 Session + Timeline + Outbox
    Framework->>Framework: 派发命令到设备
```

---

## 标准能力清单

### 系统级标准能力（框架必须提供）

```mermaid
mindmap
  root((系统级标准能力))
    接入能力
      路由解析
        设备ID → WorkLine → Plugin
      Inbox管理
        写入、幂等性、状态追踪
      快速响应
        HTTP 202
    并发能力
      分布式锁
        Redis/PostgreSQL
      锁管理
        自动续期、死锁预防
      隔离级别
        Session 级别并发控制
    编排能力
      插件生命周期
        加载、实例化、销毁
      事件路由
        Inbox.kind → method
      上下文注入
        PluginContext 依赖注入
      契约校验
        版本兼容性检查
    事务能力
      原子写入
        Session + Timeline + Outbox
      因果链
        Timeline 追踪
      失败恢复
        Inbox 重试
    派发能力
      Outbox管理
        状态追踪、重试
      设备通信
        MQTT/HTTP 调用
      外部系统
        HTTP 回调
    状态机能力
      状态校验
        前置状态检查
      迁移拦截
        非法迁移拒绝
      通配符处理
        错误、取消
```

### 插件级业务能力（开发者实现）

```mermaid
mindmap
  root((插件级业务能力))
    数据处理
      验证
        格式、约束、业务规则
      转换
        数据映射、标准化
      计算
        业务计算、推导
    业务决策
      流程控制
        分支选择、循环判断
      路由决策
        OK/NG、设备选择
      异常处理
        失败归因、错误码
    状态迁移
      迁移意图声明
        transition 字符串
      命名规范
        <event>_<result> 模式
      业务语义
        scan_ok, inspection_ng
    设备交互
      命令生成
        参数设置、目标设备
      结果解析
        成功/失败判断
      状态同步
        上下文更新
    人工协作
      等待人工
        手动确认、数据补录
      超时处理
        业务超时逻辑
```

### Transition 字符串命名规范

**⚠️ 重要说明**：插件通过 `.transition(name)` 声明**迁移意图**，但**不直接操作状态机**。

#### 设计理念

```python
# ✅ 插件：声明业务意图（解耦）
.transition("scan_ok")        # "扫码成功"
.transition("inspection_ng")  # "检测失败"

# ❌ 不需要：引用状态机枚举（强耦合）
.transition(SmtClassifierStageMachine.Triggers.SCAN_OK)
```

#### 为什么使用字符串？

| 优势 | 说明 |
|------|------|
| **解耦** | 插件不需要知道状态机实现 |
| **灵活** | 可自由命名，支持业务语义 |
| **简洁** | 比枚举更易读、更直观 |
| **自主** | 插件开发者定义，无需注册中央列表 |

#### 推荐命名模式

| 模式 | 示例 | 使用场景 |
|------|------|----------|
| `<event>_ok` | `scan_ok`, `inspection_ok` | 事件处理成功 |
| `<event>_ng` | `scan_ng`, `inspection_ng` | 事件处理失败/NG |
| `<event>_<action>` | `pick_after_ng`, `conveyor_move` | 特定操作 |
| `<event>_timeout` | `scan_timeout`, `wait_timeout` | 超时处理 |

#### 命名最佳实践

```python
# 1. 小写 + 下划线
.transition("scan_ok")    # ✅ 清晰
.transition("SCAN_OK")    # ❌ 避免全大写

# 2. 体现业务语义
.transition("scan_ok")    # ✅ 自解释
.transition("t1")         # ❌ 无意义

# 3. 保持一致性
@on_event("SCAN_COMPLETED")
async def handle_scan(self, ctx, event):
    return PluginResultBuilder(ctx).transition("scan_ok")  # 对应

@on_event("INSPECTION_COMPLETED")
async def handle_inspection(self, ctx, event):
    return PluginResultBuilder(ctx).transition("inspection_ok")  # 对应

# 4. 特殊流程使用描述性名称
.transition("pick_after_ng")    # ✅ NG后的抓取
.transition("conveyor_ok")      # ✅ 流水线传输成功
```

#### 框架如何处理 transition 字符串

```mermaid
flowchart LR
    Plugin["插件<br/>.transition('scan_ok')"] --> Result["PluginResult<br/>transition='scan_ok'"]
    Result --> Framework["框架处理"]
    Framework --> Validate{运行时校验}
    Validate -->|有效| Execute["执行状态迁移"]
    Validate -->|无效| Error["返回 STATE_MISMATCH"]
    
    style Plugin fill:#fce4ec
    style Framework fill:#e3f2fd
    style Error fill:#ffcdd2
    style Execute fill:#c8e6c9
```

**关键点**：
1. **插件声明**：`.transition("scan_ok")` 只是声明意图
2. **框架验证**：运行时检查该触发器在当前状态下是否有效
3. **状态机执行**：如果有效，状态机执行迁移；否则返回错误
4. **解耦设计**：插件不知道状态机内部，只声明"我想触发 scan_ok"

---

## 框架设计的核心目标

### 1. 降低插件开发复杂度

| 维度 | 传统方式 | 简化方式 | 改善 |
|------|---------|---------|------|
| **代码量** | ~40 行/事件 | ~12 行/事件 | **-70%** |
| **样板代码** | ~35 行（Payload解析、设备拓扑、错误处理） | ~3 行（装饰器声明） | **-91%** |
| **学习曲线** | 需要理解 Inbox/Outbox、锁、事务 | 只需理解装饰器和 Builder | **大幅降低** |
| **错误率** | 高（手动解析、错误处理遗漏） | 低（自动验证、类型安全） | **显著降低** |

### 2. 提高代码可维护性

```mermaid
flowchart LR
    subgraph Before["传统方式（难以维护）"]
        B1["业务逻辑"]
        B2["系统细节"]
        B3["错误处理"]
        B1 -.->|耦合| B2
        B2 -.->|耦合| B3
    end
    
    subgraph After["简化方式（易于维护）"]
        A1["业务逻辑"]
        A2["系统细节"]
        A3["错误处理"]
        A1 -->|清晰边界| A2
        A2 -->|自动处理| A3
    end
    
    Before -->|重构| After

    style Before fill:#ffcdd2
    style After fill:#c8e6c9
```

### 3. 提升开发效率

```mermaid
gantt
    title 插件开发时间对比
    dateFormat X
    axisFormat %s
    
    section 传统方式
    学习系统架构     :0, 3
    理解 Inbox/Outbox :3, 5
    实现业务逻辑     :5, 8
    调试系统问题     :8, 12
    
    section 简化方式
    学习装饰器      :0, 1
    实现业务逻辑     :1, 3
    单元测试        :3, 4
```

**时间节省**：12 天 → 4 天 = **-67%**

---

## 实施建议

### 1. 标准能力抽象清单

| 序号 | 能力 | 抽象方式 | 优先级 |
|------|------|---------|--------|
| 1 | 事件路由 | `@on_event()` 装饰器 | 🔴 高 |
| 2 | Payload 解析 | Pydantic 自动验证 | 🔴 高 |
| 3 | 状态机集成 | `@step()` 装饰器 | 🔴 高 |
| 4 | 响应构建 | `PluginResultBuilder` | 🔴 高 |
| 5 | 设备拓扑注入 | `PluginContext` | 🟡 中 |
| 6 | 分布式锁 | 框架自动管理 | 🟡 中 |
| 7 | 原子写入 | 框架自动处理 | 🟡 中 |
| 8 | 契约版本校验 | 框架自动校验 | 🟢 低 |

### 2. 渐进式迁移策略

```mermaid
flowchart LR
    Step1["Phase 1<br/>核心装饰器<br/>@on_event/@on_command"]
    Step2["Phase 2<br/>Payload 自动解析<br/>Pydantic 集成"]
    Step3["Phase 3<br/>状态机集成<br/>@step 装饰器"]
    Step4["Phase 4<br/>迁移现有插件<br/>并行验证"]
    
    Step1 --> Step2
    Step2 --> Step3
    Step3 --> Step4
    
    T1["1 周"]
    T2["1 周"]
    T3["1 周"]
    T4["2 周"]
    
    Step1 -.-> T1
    Step2 -.-> T2
    Step3 -.-> T3
    Step4 -.-> T4

    style Step1 fill:#c8e6c9
    style Step2 fill:#c8e6c9
    style Step3 fill:#fff9c4
    style Step4 fill:#ffcdd2
```

---

## 总结

### 核心原则

```
系统级能力 = 框架提供（开发者无需关心）
插件级能力 = 开发者实现（框架不干涉）
```

### 能力边界

| 系统级 | 插件级 |
|--------|--------|
| 路由、锁、事务、派发、状态机 | 验证、判断、决策、归因 |
| 基础设施 | 业务逻辑 |
| 通用能力 | 领域知识 |

### 最终目标

```mermaid
mindmap
  root((插件开发终极目标))
    只写业务逻辑
      无需关心系统细节
      无需理解 Inbox/Outbox
      无需处理并发锁
    声明式开发
      装饰器声明意图
      Builder 构建响应
      类型安全保障
    快速迭代
      开发时间从 2 周 → 3 天
      代码量减少 70%
      错误率显著降低
    易于测试
      Mock 系统能力
      只测业务逻辑
      快速验证
```

**您的理解完全正确！这正是插件框架设计的核心目标。**