# 插件开发指南

本指南将帮助您使用装饰器驱动的声明式模式快速开发 WES 工作线插件。

---

## 快速开始

### 5分钟创建第一个插件

```python
from src.workline_runtime.plugin_base import (
    PluginResultBuilder,
    WorklinePlugin,
    on_command,
    on_event,
    step,
)
from src.workline_runtime.payloads import ScanEventPayload

class MyFirstPlugin(WorklinePlugin):
    """我的第一个插件"""
    
    plugin_key = "my_first"
    contract_version = "1.0"
    
    @on_event("SCAN_COMPLETED")
    @step("IDLE", "PROCESSING")
    async def handle_scan(self, ctx, event: ScanEventPayload):
        """处理扫码完成事件"""
        return (
            PluginResultBuilder(ctx)
            .transition("scan_ok")
            .command(
                device_role="INPUT_ARM",
                command_type="PICK",
                parameters={"barcode": event.barcode}
            )
            .build()
        )
```

**完成！** 这段代码实现了：
- ✅ 自动路由扫码事件
- ✅ Pydantic 自动验证 payload
- ✅ 状态校验（IDLE → PROCESSING）
- ✅ 设备角色自动解析为设备ID
- ✅ 链式响应构建

---

## 核心概念

### 1. 装饰器声明意图

#### `@on_event(event_type)` - 事件路由

```python
@on_event("SCAN_COMPLETED")
async def handle_scan(self, ctx, event: ScanEventPayload):
    """处理扫码完成"""
    # 自动路由：event_type == "SCAN_COMPLETED" → 此方法
```

**特点**：
- 无需手动判断 `event_type`
- 支持任意事件类型
- 重复定义会覆盖

#### `@on_command(command_type, result=None)` - 命令结果路由

```python
# 精确匹配：command_type + result
@on_command("PICK", result="SUCCESS")
async def handle_pick_success(self, ctx, result):
    """只处理成功的抓取结果"""
    pass

# 模糊匹配：只匹配 command_type
@on_command("PICK")
async def handle_pick_any(self, ctx, result):
    """处理所有抓取结果（成功+失败）"""
    pass
```

**优先级**：精确匹配 > 模糊匹配

#### `@step(expected=None, target=None)` - 状态迁移声明

```python
# 期望状态 + 目标状态
@step("IDLE", "PROCESSING")
async def handle_scan(self, ctx, event):
    """前置：IDLE，执行后自动设置为 PROCESSING"""
    pass

# 任意状态 → 目标状态
@step(None, "ERROR")
async def handle_error(self, ctx, event):
    """从任意状态迁移到 ERROR"""
    pass

# 保持当前状态
@step("PROCESSING", None)
async def handle_keep_state(self, ctx, event):
    """保持 PROCESSING 状态"""
    pass
```

### 2. Pydantic 自动解析

```python
from src.workline_runtime.payloads import ScanEventPayload

@on_event("SCAN_COMPLETED")
async def handle_scan(self, ctx, event: ScanEventPayload):
    # event 自动解析，类型安全
    barcode = event.barcode  # str
    location = event.location_id  # str（支持字段别名）
    
    # 如果 payload 缺少字段，自动返回 FailureIntent
    # 无需手动验证
```

**自定义Payload**：

```python
from pydantic import BaseModel, Field
from src.workline_runtime.plugin_base import EventPayload

class MyCustomPayload(EventPayload):
    """自定义事件Payload"""
    
    device_code: str
    custom_field: str
    optional_field: str | None = None
    aliased_field: str = Field(alias="api_field")

@on_event("CUSTOM_EVENT")
async def handle_custom(self, ctx, event: MyCustomPayload):
    # event.custom_field 自动可用
    pass
```

### 3. Transition 命名规范

**⚠️ 重要**：`.transition(name)` 接受的是**触发器名称字符串**，不是状态枚举。

#### 命名规范

推荐使用 `<event>_<result>` 模式：

| 模式 | 示例 | 说明 |
|------|------|------|
| `<event>_ok` | `scan_ok`, `inspection_ok`, `pick_ok` | 事件处理成功 |
| `<event>_ng` | `scan_ng`, `inspection_ng`, `pick_ng` | 事件处理失败/NG |
| `<event>_<action>` | `pick_after_ng`, `conveyor_move_start` | 特定操作 |
| `<event>_timeout` | `scan_timeout`, `inspection_timeout` | 超时处理 |

#### 为什么使用字符串？

```python
# ✅ 推荐：使用字符串解耦插件与状态机
.transition("scan_ok")  # 插件只声明业务意图

# ❌ 不推荐：直接引用状态机枚举
.transition(SmtClassifierStageMachine.Triggers.SCAN_OK)  # 强依赖
```

**设计权衡**：
- ✅ **解耦**：插件不需要知道状态机实现
- ✅ **灵活**：可自由命名，支持业务语义
- ✅ **简洁**：比枚举更易读
- ⚠️ **运行时验证**：框架在执行时检查有效性，而非编译时

#### 最佳实践

```python
# 1. 使用小写 + 下划线
.transition("scan_ok")       # ✅ 清晰
.transition("SCAN_OK")       # ❌ 避免全大写

# 2. 体现业务语义
.transition("scan_ok")              # ✅ 自解释
.transition("t1")                   # ❌ 无意义

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

### 4. PluginResultBuilder 链式构建

```python
return (
    PluginResultBuilder(ctx)
    .transition("scan_ok")                    # 状态迁移触发器
    .command(
        device_role="INPUT_ARM",              # 设备角色（自动解析为ID）
        command_type="PICK",                   # 命令类型
        parameters={"barcode": "ABC123"}      # 命令参数
    )
    .wait(
        event_type="INSPECTION_COMPLETED",     # 等待事件类型
        timeout_seconds=300                    # 超时秒数
    )
    .context({"last_barcode": "ABC123"})      # 更新上下文
    .build()
)
```

**可用方法**：

| 方法 | 参数 | 说明 |
|------|------|------|
| `.transition(name)` | 触发器名称字符串 | 触发状态迁移，使用 `<event>_<result>` 模式 |
| `.command(...)` | device_role, command_type, parameters | 添加设备命令 |
| `.wait(...)` | event_type, timeout_seconds | 等待外部回调 |
| `.failure(...)` | domain, code, message | 设置失败归因 |
| `.complete()` | 无 | 标记会话完成 |
| `.context(...)` | dict | 更新 session.context_json |

---

## 完整示例

### 示例1：简单扫码插件

```python
from src.workline_runtime.plugin_base import (
    PluginResultBuilder,
    WorklinePlugin,
    on_command,
    on_event,
    step,
)
from src.workline_runtime.payloads import (
    PickPlaceResultPayload,
    ScanEventPayload,
)

class SimpleScanPlugin(WorklinePlugin):
    """简单扫码插件"""
    
    plugin_key = "simple_scan"
    contract_version = "1.0"
    
    @on_event("SCAN_COMPLETED")
    @step("IDLE", "WAITING_PICK")
    async def handle_scan(self, ctx, event: ScanEventPayload):
        """扫码完成 → 抓取"""
        # 业务逻辑：验证条码
        if len(event.barcode) < 3:
            return (
                PluginResultBuilder(ctx)
                .failure(
                    domain="DATA",
                    code="BARCODE_INVALID",
                    message=f"条码过短: {event.barcode}"
                )
                .build()
            )
        
        # 派发抓取命令
        return (
            PluginResultBuilder(ctx)
            .transition("scan_ok")
            .command(
                device_role="INPUT_ARM",
                command_type="PICK",
                parameters={"barcode": event.barcode}
            )
            .context({"current_barcode": event.barcode})
            .build()
        )
    
    @on_command("PICK", result="SUCCESS")
    @step("WAITING_PICK", "COMPLETED")
    async def handle_pick_success(self, ctx, result: PickPlaceResultPayload):
        """抓取成功 → 完成"""
        return (
            PluginResultBuilder(ctx)
            .transition("pick_ok")
            .complete()
            .build()
        )
    
    @on_command("PICK", result="FAILED")
    @step("WAITING_PICK", "ERROR")
    async def handle_pick_failed(self, ctx, result: PickPlaceResultPayload):
        """抓取失败 → 错误"""
        return (
            PluginResultBuilder(ctx)
            .failure(
                domain="HARDWARE",
                code=result.error_code or "PICK_FAILED",
                message=f"抓取失败: {result.error_message}"
            )
            .build()
        )
```

### 示例2：带检测的插件

```python
from src.workline_runtime.payloads import (
    InspectionEventPayload,
    ScanEventPayload,
)

class InspectionPlugin(WorklinePlugin):
    """检测插件"""
    
    plugin_key = "inspection"
    contract_version = "1.0"
    
    @on_event("SCAN_COMPLETED")
    @step("IDLE", "WAITING_INSPECTION")
    async def handle_scan(self, ctx, event: ScanEventPayload):
        """扫码完成 → 等待检测"""
        return (
            PluginResultBuilder(ctx)
            .transition("scan_ok")
            .wait(
                event_type="INSPECTION_COMPLETED",
                timeout_seconds=300
            )
            .context({"barcode": event.barcode})
            .build()
        )
    
    @on_event("INSPECTION_COMPLETED")
    @step("WAITING_INSPECTION", "WAITING_CONVEYOR")
    async def handle_inspection_ok(self, ctx, event: InspectionEventPayload):
        """检测完成 → 流水线传输"""
        if event.inspection_result == "NG":
            # 检测NG → 分流
            return (
                PluginResultBuilder(ctx)
                .transition("inspection_ng")
                .command(
                    device_role="NG_ARM",
                    command_type="PICK_NG",
                    parameters={"barcode": ctx.session.context_json.get("barcode")}
                )
                .build()
            )
        
        # 检测OK → 流水线传输
        return (
            PluginResultBuilder(ctx)
            .transition("inspection_ok")
            .command(
                device_role="CONVEYOR",
                command_type="MOVE_FORWARD",
                parameters={"speed": "normal"}
            )
            .build()
        )
```

---

## 状态机集成

### 声明式状态迁移

```python
class MyStateMachine:
    """状态机定义（示例）"""
    
    IDLE = "IDLE"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    ERROR = "ERROR"

class MyPlugin(WorklinePlugin):
    @on_event("START")
    @step(MyStateMachine.IDLE, MyStateMachine.PROCESSING)
    async def handle_start(self, ctx, event):
        """开始处理：IDLE → PROCESSING"""
        pass
```

### 状态校验逻辑

框架自动校验：

1. **前置状态检查**：调用方法前验证 `session.context_json["step_code"] == expected`
2. **非法迁移拦截**：不匹配返回 `FailureIntent(domain="SOFTWARE", code="STATE_MISMATCH")`
3. **目标状态设置**：方法执行后自动设置 `result.transition = target`

### 状态通配符

```python
# 从任意状态迁移到ERROR
@step(None, "ERROR")
async def handle_error(self, ctx, event):
    """错误处理：任意状态 → ERROR"""
    pass

# 从特定状态保持
@step("PROCESSING", None)
async def handle_keep_processing(self, ctx, event):
    """保持 PROCESSING 状态"""
    pass
```

---

## 设备拓扑

### 设备角色自动解析

```python
# 配置设备拓扑（WorkLine配置）
# INPUT_ARM: [Device1(id=123), Device2(id=456)]
# CONVEYOR: [Device3(id=789)]

@on_event("SCAN_COMPLETED")
async def handle_scan(self, ctx, event):
    # 框架自动注入 ctx.devices_by_role
    return (
        PluginResultBuilder(ctx)
        .command(
            device_role="INPUT_ARM",  # 自动解析为 Device1.id=123
            command_type="PICK",
        )
        .build()
    )
```

**解析规则**：
- 取第一个设备：`devices_by_role["INPUT_ARM"][0]`
- 如果设备列表为空：抛出 `ValueError`

---

## 错误处理

### FailureIntent 失败归因

```python
return (
    PluginResultBuilder(ctx)
    .failure(
        domain="HARDWARE",      # 失败域：HARDWARE, SOFTWARE, DATA, TIMEOUT
        code="DEVICE_TIMEOUT",  # 错误码
        message="设备响应超时"    # 人类可读消息
    )
    .build()
)
```

**失败域（domain）**：
- `HARDWARE`: 硬件故障（设备离线、传感器故障）
- `SOFTWARE`: 软件错误（状态不匹配、异常）
- `DATA`: 数据错误（Payload无效、业务规则违反）
- `TIMEOUT`: 超时（设备响应超时、等待超时）
- `UPSTREAM`: 上游系统（WMS/MES回调失败）

### Payload验证失败

```python
@on_event("SCAN_COMPLETED")
async def handle_scan(self, ctx, event: ScanEventPayload):
    # 如果 payload 缺少必需字段：
    # 1. Pydantic 自动抛出 ValidationError
    # 2. 框架捕获并返回 FailureIntent(domain="DATA", code="PAYLOAD_INVALID")
    # 3. session 状态标记为失败
    # 4. 无需手动处理
    pass
```

---

## 最佳实践

### 1. 使用共享Payload

```python
# ✅ 推荐：使用共享Payload
from src.workline_runtime.payloads import ScanEventPayload

@on_event("SCAN_COMPLETED")
async def handle_scan(self, ctx, event: ScanEventPayload):
    pass

# ❌ 不推荐：重复定义
class MyScanPayload(BaseModel):
    barcode: str  # 重复定义
```

### 2. 声明式而非命令式

```python
# ✅ 推荐：声明式
@on_event("SCAN_COMPLETED")
@step("IDLE", "PROCESSING")
async def handle_scan(self, ctx, event):
    pass

# ❌ 不推荐：命令式
async def on_device_event(self, ctx, inbox):
    payload = inbox.payload_json
    if payload["event_type"] == "SCAN_COMPLETED":
        if ctx.session.context_json["step_code"] == "IDLE":
            # ... 大量if-else
```

### 3. 链式构建而非手动赋值

```python
# ✅ 推荐：链式构建
return (
    PluginResultBuilder(ctx)
    .transition("ok")
    .command(...)
    .context({"key": "value"})
    .build()
)

# ❌ 不推荐：手动赋值
result = PluginResult()
result.transition = "ok"
result.commands = [CommandIntent(...)]
result.context_patch = {"key": "value"}
```

### 4. 插件只关注业务逻辑

```python
# ✅ 推荐：只写业务逻辑
@on_event("SCAN_COMPLETED")
async def handle_scan(self, ctx, event):
    if not self._is_valid_barcode(event.barcode):
        return PluginResultBuilder(ctx).failure(...).build()
    # ... 业务逻辑

# ❌ 不推荐：关心系统细节
async def handle_scan(self, ctx, inbox):
    # ❌ 手动获取分布式锁
    # ❌ 手动解析 payload_json
    # ❌ 手动查询设备拓扑
    # ❌ 手动写入 Outbox
```

---

## 常见模式

### 模式1：条件分支

```python
@on_event("SCAN_COMPLETED")
async def handle_scan(self, ctx, event):
    # 条件：条码有效性
    if not self._is_valid_barcode(event.barcode):
        return PluginResultBuilder(ctx)
            .failure(domain="DATA", code="BARCODE_INVALID", message="...")
            .build()
    
    # 条件：库存检查
    if not self._check_inventory(event.barcode):
        return PluginResultBuilder(ctx)
            .failure(domain="BUSINESS", code="OUT_OF_STOCK", message="...")
            .build()
    
    # 默认：正常流程
    return PluginResultBuilder(ctx)
        .transition("scan_ok")
        .command(...)
        .build()
```

### 模式2：等待回调

```python
@on_event("SCAN_COMPLETED")
@step("IDLE", "WAITING_INSPECTION")
async def handle_scan(self, ctx, event):
    """扫码完成 → 等待检测"""
    return (
        PluginResultBuilder(ctx)
        .transition("scan_ok")
        .wait(
            event_type="INSPECTION_COMPLETED",
            timeout_seconds=300
        )
        .context({"barcode": event.barcode})
        .build()
    )

@on_event("INSPECTION_COMPLETED")
@step("WAITING_INSPECTION", "COMPLETED")
async def handle_inspection(self, ctx, event):
    """检测完成 → 完成"""
    return (
        PluginResultBuilder(ctx)
        .transition("inspection_ok")
        .complete()
        .build()
    )
```

### 模式3：多命令派发

```python
@on_event("SCAN_COMPLETED")
async def handle_scan(self, ctx, event):
    """扫码完成 → 多个设备协同"""
    barcode = event.barcode
    
    return (
        PluginResultBuilder(ctx)
        .transition("scan_ok")
        # 命令1：机械臂抓取
        .command(
            device_role="INPUT_ARM",
            command_type="PICK",
            parameters={"barcode": barcode}
        )
        # 命令2：同时启动检测
        .command(
            device_role="INSPECTION_SENSOR",
            command_type="START_INSPECTION",
            parameters={"barcode": barcode}
        )
        .build()
    )
```

### 模式4：外部系统集成

```python
from src.workline_runtime.payloads import MESInspectionResultPayload

@on_event("MES_CALLBACK")
async def handle_mes_callback(self, ctx, event: MESInspectionResultPayload):
    """MES检测回调"""
    if event.inspection_result == "OK":
        return (
            PluginResultBuilder(ctx)
            .transition("inspection_ok")
            .command(
                device_role="CONVEYOR",
                command_type="MOVE_FORWARD"
            )
            .context({
                "reel_diameter": event.reel_diameter,
                "reel_thickness": event.reel_thickness
            })
            .build()
        )
    else:
        return (
            PluginResultBuilder(ctx)
            .transition("inspection_ng")
            .command(
                device_role="NG_ARM",
                command_type="PICK_NG"
            )
            .build()
        )
```

---

## 故障排查

### 问题1：装饰器路由不生效

**症状**：定义了 `@on_event("SCAN_COMPLETED")`，但方法没被调用

**排查**：
```python
# 检查事件类型是否匹配
@on_event("SCAN_COMPLETED")  # ← 精确匹配
async def handle_scan(self, ctx, event):
    pass

# 检查 inbox.payload_json["event_type"]
# 应为 "SCAN_COMPLETED"，不是 "SCAN" 或其他
```

### 问题2：Payload验证失败

**症状**：收到 `FailureIntent(domain="DATA", code="PAYLOAD_INVALID")`

**排查**：
```python
# 检查Payload定义
class ScanEventPayload(EventPayload):
    barcode: str  # ← 必需字段
    location_id: str  # ← 必需字段

# 检查 payload_json 是否包含所有字段
# {"device_code": "SCANNER01", "barcode": "ABC123", "location": "LOC01"}
#                                                    ↑↑↑ 别名匹配
```

### 问题3：状态不匹配错误

**症状**：收到 `FailureIntent(domain="SOFTWARE", code="STATE_MISMATCH")`

**排查**：
```python
# 检查当前状态
@step("IDLE", "PROCESSING")  # ← 期望 IDLE
async def handle_scan(self, ctx, event):
    pass

# 检查 session.context_json["step_code"]
# 应为 "IDLE"，不是其他状态
```

### 问题4：设备角色未找到

**症状**：`ValueError: Device role 'INPUT_ARM' not found`

**排查**：
```python
# 检查 WorkLine 设备配置
# workline.devices_by_role 应该包含 "INPUT_ARM"
# 或者检查 ctx.devices_by_role 是否注入
```

---

## 进阶话题

### 自定义状态机

```python
from enum import Enum

class MyStateMachine(str, Enum):
    """自定义状态机"""
    IDLE = "IDLE"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    ERROR = "ERROR"

class MyPlugin(WorklinePlugin):
    @on_event("START")
    @step(MyStateMachine.IDLE, MyStateMachine.PROCESSING)
    async def handle_start(self, ctx, event):
        pass
```

### 插件间协作

```python
# 插件A：扫码
class ScanPlugin(WorklinePlugin):
    @on_event("SCAN_COMPLETED")
    async def handle_scan(self, ctx, event):
        return (
            PluginResultBuilder(ctx)
            .context({"barcode": event.barcode})
            .build()
        )

# 插件B：检测（可读取插件A写入的上下文）
class InspectionPlugin(WorklinePlugin):
    @on_event("INSPECTION_COMPLETED")
    async def handle_inspection(self, ctx, event):
        barcode = ctx.session.context_json.get("barcode")
        # ... 使用 barcode
```

---

## 迁移指南

### 从传统方式迁移到新框架

**传统方式**：
```python
class OldPlugin:
    async def on_device_event(self, ctx, inbox):
        payload = inbox.payload_json or {}
        event_type = payload.get("event_type")
        
        if event_type == "SCAN_COMPLETED":
            barcode = payload.get("barcode")
            if not barcode:
                return PluginResult(failure=FailureIntent(...))
            
            # ... 50行逻辑
        elif event_type == "INSPECTION_COMPLETED":
            # ... 50行逻辑
```

**新框架**：
```python
class NewPlugin(WorklinePlugin):
    @on_event("SCAN_COMPLETED")
    async def handle_scan(self, ctx, event: ScanEventPayload):
        # Pydantic自动验证
        # 12行逻辑
        pass
    
    @on_event("INSPECTION_COMPLETED")
    async def handle_inspection(self, ctx, event):
        # 12行逻辑
        pass
```

**代码减少**：~70%

---

## 参考资料

- **源码**：`src/workline_runtime/plugin_base.py`
- **Payload定义**：`src/workline_runtime/payloads.py`
- **示例插件**：`src/workline_plugins/simple_plugin.py`
- **测试示例**：`tests/workline_runtime/test_plugin_base.py`
- **完整提案**：`docs/plugin_simplification_proposal.md`
