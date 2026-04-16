# P9 WES 第三方设备接入白皮书 (Third-Party Device Integration White Paper)

> **文档版本**: 1.1
> **发布日期**: 2025-12-15
> **密级**: 公开 (Public) - 供供应商及集成商使用
> **变更说明**: 移除 RCS/AGV 相关内容，聚焦于 ECS 及自动化作业设备接入。

> **相关文档**:
> - 软件需求规格说明书: `../architecture/SRS.md` (第 3.7.3 节、第 4.2 节)
> - WES 内部开发规范: `../hardware/SMT粗分机接口调用说明书20260321-v1.md`
> - **本白皮书是供应商对接的唯一权威文档**

## 1. 概述 (Overview)

### 1.1 背景与目的
休斯顿 P9 智能仓储执行系统 (WES) 是整个仓储物流环节的核心调度中台。为了实现对异构硬件（机械臂、输送线、立库、贴标机等）的统一管理和快速接入，本项目采用 **“标准化协议接入”** 模式。

本白皮书定义了 P9 WES 与第三方自动化设备之间的**统一通信协议**。所有计划接入 P9 WES 的硬件设备，**必须**遵循本规范开发通信接口或中间件。

### 1.2 适用范围
本规范适用于以下设备的控制系统接入：
*   **ECS (Equipment Control System)**: 负责控制机械臂 (Robotic Arms)、输送线 (Conveyors)、堆垛机 (Stacker Cranes)、自动贴标机 (Labelers)、分拣机 (Sorters) 等。
*   **其他智能终端**: 任何需接受 WES 指令的固定式自动化作业设备。

> **注意**: AGV、E-AGV、CTU 等移动运输设备的调度系统 (RCS) 目前由现有 WMS 统一管理，**暂不在本白皮书的接入范围内**。但本项目**后续计划将 RCS 的调度对接迁移至 P9 WES 系统**，届时将发布更新版接入规范。

### 1.3 核心原则
1.  **WES 主导权**: WES 是指令的发起方，设备是指令的执行方。
2.  **零代码适配**: WES 不为特定供应商开发驱动。供应商需适配 WES 的标准协议。
3.  **异步机制**: 采用 “指令下发 (Command) -> 立即响应 (Ack) -> 异步回调 (Callback)” 的机制，避免长连接阻塞。
4.  **幂等性**: 设备端必须处理重复指令，防止物理动作重复执行。

---

## 2. 通信技术规范 (Technical Specifications)

### 2.1 传输协议
*   **推荐协议**: HTTP/1.1 或 HTTPS (推荐)
*   **方法**: POST (主要), GET (状态查询)
*   **数据格式**: JSON (Content-Type: application/json)
*   **字符编码**: UTF-8

**注意**:
- 本白皮书定义 HTTP/HTTPS 接入规范（最常用）
- SRS 第 4.2 节允许 TCP/Modbus/MQTT 等其他协议
- 如需使用其他协议，请联系 WES 项目组评估

### 2.2 网络要求
*   设备（或其中间件/网关）必须配置**静态 IP**。
*   设备必须能够访问 WES 服务器的 API 端口。
*   网络延迟建议控制在 **50ms** 以内（局域网环境）。

### 2.3 安全认证
*   (可选) 在 HTTP Header 中包含认证 Token：
    *   `Authorization: Bearer <VENDOR_TOKEN>`
    *   Token 由 WES 项目组在联调阶段分配。

---

## 3. 接口定义 (Interface Definition)

集成方（供应商）需要实现两部分工作：
1.  **服务端 (Server)**: 暴露 API 供 WES 调用（下发指令）。
2.  **客户端 (Client)**: 主动调用 WES API（回传结果）。

### 3.1 供应商需实现的接口 (Vendor Implementation)

以下接口由**供应商设备**提供，WES 作为客户端调用。

#### 3.1.1 接收作业指令 (Receive Command)
*   **功能**: 接收 WES 下发的抓取、放置、扫描、加工等指令。
*   **URL**: `http://<DEVICE_IP>:<PORT>/api/v1/device/command`
*   **Method**: `POST`
*   **WES 请求示例**:
    ```json
    {
      "command_id": "CMD-20251215-1001",   // 全局唯一指令ID (必须用于去重)
      "task_type": "PUT_INSTRUCTION",      // 指令类型：PUT, PICK, SCAN 等
      "priority": 1,                       // 优先级 (1-10, 10最高)
      "timeout": 30000,                    // 期望完成时间(ms)
      "params": {                          // 业务参数，随类型变化
        "source_loc": "BIN-01-A",
        "target_loc": "CONVEYOR-02",
        "material_code": "M123456",
        "quantity": 10
      },
      "timestamp": 1702627200000
    }
    ```
*   **供应商响应示例 (同步应答)**:
    *   *注意：此响应仅代表“收到并接受任务”，不代表任务完成。*
    ```json
    {
      "code": 200,          // 200: 成功接收; 400: 参数错误; 503: 设备忙/故障
      "message": "Accepted",
      "trace_id": "DEV-LOG-998877" // 可选，供应商内部日志ID
    }
    ```

#### 3.1.2 设备状态查询 (Health Check)
*   **功能**: WES 心跳检测或异常时主动查询设备状态。
*   **URL**: `http://<DEVICE_IP>:<PORT>/api/v1/device/status`
*   **Method**: `GET`
*   **供应商响应示例**:
    ```json
    {
      "device_id": "ARM_01",
      "status": "RUNNING",        // IDLE, RUNNING, ERROR, OFFLINE
      "current_cmd_id": "CMD-20251215-1001",
      "error_code": "NONE"        // 若 status=ERROR, 填写具体错误码
    }
    ```

#### 3.1.3 任务取消 (Cancel Command)
*   **功能**: WES 强制取消正在执行或排队的任务。
*   **URL**: `http://<DEVICE_IP>:<PORT>/api/v1/device/cancel`
*   **Method**: `POST`
*   **请求**: `{"command_id": "CMD-20251215-1001"}`
*   **响应**: `{"code": 200, "message": "Cancelled"}`

---

### 3.2 WES 提供的回调接口 (WES Callback)

以下接口由 **P9 WES** 提供，供应商设备在任务结束时**必须**调用。

#### 3.2.1 任务结果回传 (Report Result)
*   **功能**: 物理动作完成后，通知 WES 更新业务状态。
*   **URL**: `http://<WES_IP>:<PORT>/api/v1/callback/result`
*   **Method**: `POST`
*   **供应商请求示例**:
    ```json
    {
      "command_id": "CMD-20251215-1001",   // 必须回传原指令ID
      "device_id": "ARM_01",
      "result": "SUCCESS",                 // SUCCESS 或 FAILED
      "finish_time": 1702627250000,
      "data": {                            // 选填，业务回传数据
        "actual_qty": 10,
        "scan_result": "PKG-X-99",
        "image_url": "http://..."          // 可选，视觉照片
      },
      "error_detail": {                    // 若 result=FAILED 必填
        "code": "E-MOTOR-01",
        "msg": "Servo motor timeout"
      }
    }
    ```
*   **WES 响应**: `{"code": 200, "message": "ACK"}`

#### 3.2.2 设备事件上报 (Event Push)
*   **功能**: 设备发生状态变更（如急停、上线）或 **传感器触发业务信号**（如到位、读码完成）。
*   **URL**: `http://<WES_IP>:<PORT>/api/v1/callback/event`
*   **请求示例**:
    ```json
    {
      "device_id": "CONVEYOR_01",
      "event_type": "MATERIAL_ARRIVED", // 事件类型: ESTOP_PRESSED, MATERIAL_ARRIVED, SCAN_COMPLETED
      "timestamp": 1702627300000,
      "data": {                         // 选填，业务负载数据
        "location": "STATION_04",
        "barcode": "PKG12345678"        // 若是主动扫码设备，可在此携带数据
      }
    }
    ```

---

### 3.3 典型交互场景 (Interaction Scenarios)

#### 3.3.1 传感器触发模式 (Sensor-Triggered Workflow)
针对 **"传感器检测 -> WES 决策 -> 执行动作"** 的场景（如：托盘到达工位 -> 询问 WES 下一步动作），需遵循 **异步事件驱动** 流程：

1.  **触发 (Trigger)**: ECS 检测到传感器信号。
2.  **上报 (Report)**: ECS 调用 WES `Event_Push` 接口。
    *   `event_type`: `MATERIAL_ARRIVED`
    *   `data`: `{"location": "STATION_A"}`
3.  **响应 (Ack)**: WES 立即返回 `200 OK` (不含业务指令)。
4.  **决策 (Decision)**: WES 异步计算业务逻辑（分拣/上架等）。
5.  **下发 (Dispatch)**: WES 调用 ECS `Receive Command` 接口下发下一步指令。
    *   `task_type`: `SCAN` 或 `MOVE`

> **禁止事项**: 严禁在 `Event_Push` 的 HTTP 响应 Body 中直接返回具体的动作指令。所有动作必须通过标准的 `Receive Command` 下发，以保证指令的可追踪性和统一管理。

---

## 4. 业务规则与约束 (Business Rules)

### 4.1 幂等性要求 (Idempotency)
*   **场景**: 由于网络波动，WES 可能会重发同一个 `command_id`。
*   **要求**: 供应商系统必须缓存最近 1 小时内处理过的 `command_id`。
    *   如果收到已执行成功的 ID -> 直接返回 200 OK，**不执行物理动作**。
    *   如果收到正在执行的 ID -> 返回 200 OK，继续执行。
    *   **严禁**因为重试导致机械臂重复抓取或设备重复动作。

### 4.2 超时与重试
*   **WES 策略**: WES 发出请求后，若 **10 秒**内未收到 HTTP 200 应答，将视为网络超时，会进行指数退避重试（最多 3 次）。
    *   **依据**: SRS 第 3.7.3 节 - 通信异常与重试机制
    *   **重试间隔**: 指数退避（1s, 2s, 4s）
*   **供应商策略**: 供应商调用 WES 回调接口时，若失败也应有重试机制（建议至少保留 1 小时的本地数据缓存，待网络恢复后补传）。

### 4.3 坐标与地图
*   WES 使用**逻辑位置 (Logical ID)** (例如: `STATION_A`, `RACK_01`)。
*   供应商设备需自行维护逻辑位置到**物理坐标**的映射。WES 不下发具体的 X,Y,Z 坐标或关节角度，只下发“去哪里(Where)”和“做什么(What)”。

---

## 5. 数据字典 (Data Dictionary)

### 5.1 指令类型 (task_type)
| 类型代码 | 含义 | 备注 |
| :--- | :--- | :--- |
| `PICK` | 抓取/取货 | 机械臂从指定位置取料 |
| `PUT` | 放置/卸货 | 机械臂将物料放入指定位置 |
| `SCAN` | 扫码/识别 | 视觉/扫码器进行条码或OCR识别 |
| `ROTATE` | 旋转 | 转盘/机械臂旋转操作 |
| `PROCESS`| 加工/检测 | 贴标、X-Ray检测等原子动作 |

### 5.2 状态枚举 (status)
| 状态代码 | 含义 |
| :--- | :--- |
| `IDLE` | 空闲，可接收新任务 |
| `RUNNING` | 忙碌，正在执行任务 |
| `ERROR` | 故障，需人工介入 |
| `OFFLINE` | 离线 (WES 判定) |

---

## 6. 接入验收流程 (Onboarding Process)

1.  **文档评审**: 供应商阅读本白皮书，确认技术可行性。
2.  **开发/配置**: 供应商在设备控制器或网关上实现上述 HTTP 接口。
3.  **模拟测试**: 使用 Postman 或 WES 提供的 Mock 工具进行接口连通性测试。
4.  **沙箱联调**: 在 P9 现场测试环境（Sandbox）接入，进行空跑测试。
5.  **场景验收**: 执行规定的标准动作（如连续抓取 50 次无报错）。
6.  **正式上线**: 切换至生产环境 IP，正式投产。

---
