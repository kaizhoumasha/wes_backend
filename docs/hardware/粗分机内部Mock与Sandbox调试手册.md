# 粗分机内部 Mock 与 Sandbox 调试手册

> 面向 WES 项目组内部调试、本地开发和 E2E 验证。
> 供应商正式联调请使用 [`粗分机硬件供应商联调操作手册.md`](./粗分机硬件供应商联调操作手册.md)。
> 最近同步：2026-06-08，从供应商版手册拆出 Sandbox、Mock ECS 和 Mock WMS 操作。

---

## 0. 调试边界

这份文档只说明 WES 内部无真实设备调试路径。

- Sandbox：WES 后台调试接口，创建真实的 Session、Command、Outbox、Inbox、Timeline 和 Trace，但不访问设备地址。
- Mock ECS：设备侧服务替身，接收 WES 下发命令、提供设备状态查询，并转发手动事件或命令结果到 WES callback。
- Mock WMS：WMS 库存服务替身，用于粗分机 `PICK_AND_PUT` 成功后的库存准入校验。

WES 下发命令和查询状态时只读取设备基础数据里的 `{protocol}://{host}:{port}{callback_path}` 和
`{status_path}`。如果 `host:port` 指向 Mock ECS，就是 Mock 联调；如果指向真实设备服务，就是真实设备联调。WES
运行时不关心目标是 Mock 还是真实设备，也不为 Mock 单独切派发分支。

供应商自己的程序不要调用 `/api/v1/mock/*` 或 `/api/v1/workline/operations/sandbox/*`。供应商应直接调用 WES
`/api/v1/callback/event` 和 `/api/v1/callback/result`。

---

## 1. 启动本地环境

```bash
./scripts/init-env.sh dev
docker-compose up -d
uv sync --dev
./scripts/migrate.sh upgrade

uv run uvicorn main:app --reload --host 0.0.0.0 --port 8001
sh src/celery_app/dev_worker_autoreload.sh
```

同步粗分机测试工作线和设备：

```bash
uv run python scripts/data/sync_test_workline_devices.py
```

脚本会准备这些测试数据：

- WorkLine：`WL-ROUGH-SORTER-TEST`
- 入料机械臂：`RS-INPUT-ARM-01`
- 输送线：`RS-CONVEYOR-01`
- 出料机械臂：`RS-OUTPUT-ARM-01`

出料入箱前置条件：

- 已注入粗分机 `bin_allocator` 服务。
- 当前工作线存在可用料箱、料格或有效的 active bin rack 投影。
- 料箱分配器能为当前 `PkgID` 返回 `ALLOCATED` 决策。

如果条件不满足，`MOVE_FORWARD` 成功后可能进入 `MANUAL_HOLD` 或 blocking-point，不会继续生成 `PUT_TO_BIN`。这是 WES
资源配置问题，不算设备协议失败。

---

## 2. Sandbox + CURL 无真实设备调试

Sandbox 使用后台用户 Token，不使用设备侧 `APP_ID` / `APP_SECRET` 签名。

注意字段名差异：

| 场景 | 接口 | 结果数据字段 |
| --- | --- | --- |
| 真实设备回传 Result | `/api/v1/callback/result` | `data` |
| Sandbox 模拟 Result | `/api/v1/workline/operations/results` | `payload` |

### 2.1 获取后台调试 Token

本地开发环境可以用初始化管理员账号：

```bash
export WES_API=http://localhost:8001/api/v1
export WES_TOKEN=$(
  curl -sS -X POST "$WES_API/auth/login" \
    -H "Content-Type: application/json" \
    -d '{"username":"admin","password":"admin123"}' \
  | jq -r '.data.access_token'
)
```

如果在正式联调地址操作 Sandbox，让 WES 项目组提供具备 `biz:workline:list` 和 `biz:workline:update` 权限的后台用户 Token：

```bash
export WES_API=https://mcs.happytable.cc/api/v1
export WES_TOKEN="<由 WES 项目组提供>"
```

### 2.2 查询 WorkLine 和设备 ID

```bash
curl -sS "$WES_API/workline/runtime/worklines?excludeSimulation=false" \
  -H "Authorization: Bearer $WES_TOKEN" | jq

export WID=<WL-ROUGH-SORTER-TEST 的 id>

curl -sS "$WES_API/workline/runtime/devices?worklineId=$WID" \
  -H "Authorization: Bearer $WES_TOKEN" | jq

export INPUT_ARM_ID=<RS-INPUT-ARM-01 的 id>
```

### 2.3 触发 Sandbox START

如果 WorkLine 处于 `STOPPED`，先触发 START 准入：

```bash
curl -sS -X POST "$WES_API/workline/operations/sandbox/worklines/$WID/start" \
  -H "Authorization: Bearer $WES_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "device_code": "RS-INPUT-ARM-01",
    "trace_id": "sandbox-start-rough-sorter"
  }' | jq
```

START 返回成功后再发送生产事件。如果返回 `START_ADMISSION_DEVICE_NOT_IDLE`，先检查设备基础数据指向的状态接口是否返回
`AUTO` / `IDLE`。

### 2.4 发送 Sandbox 扫码事件

```bash
export TRACE_ID="rough-sorter-curl-$(date +%s)"

curl -sS -X POST "$WES_API/workline/operations/sandbox/events" \
  -H "Authorization: Bearer $WES_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"workline_id\": $WID,
    \"device_id\": $INPUT_ARM_ID,
    \"event_type\": \"SCAN_COMPLETED\",
    \"trace_id\": \"$TRACE_ID\",
    \"payload\": {
      \"device_code\": \"RS-INPUT-ARM-01\",
      \"data\": {
        \"location\": \"ARM01\",
        \"HHPN\": \"CAP001\",
        \"MfrPN\": \"V0001-CAP-0402\",
        \"Qty\": \"100\",
        \"DateCode\": \"20260409\",
        \"LotCode\": \"LOT-A\",
        \"PkgID\": \"PKG-CAP001-LOT-A-001\"
      }
    }
  }" | jq
```

### 2.5 查询待执行命令

```bash
curl -sS "$WES_API/workline/operations/sandbox/pending?workline_id=$WID&limit=10" \
  -H "Authorization: Bearer $WES_TOKEN" | jq
```

每回传一次 Result 后，都重新查 pending，并从最新响应第一条取值：

```bash
export DISPATCH_KEY=<data[0].dispatch_key>
export COMMAND_CODE=<data[0].payload_json.command_code>
export DEVICE_CODE=<data[0].target_code>
export TASK_TYPE=<data[0].payload_json.task_type>
```

不要复用上一轮 `COMMAND_CODE`。`command_code` 只对应一条命令；复用旧值会导致 Result 被拒绝，或者落到错误命令上。

### 2.6 模拟 ACK

```bash
curl -sS -X POST "$WES_API/workline/operations/sandbox/ack" \
  -H "Authorization: Bearer $WES_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"dispatch_key\":\"$DISPATCH_KEY\"}" | jq
```

### 2.7 模拟 Result

先看当前命令类型：

```bash
echo "$TASK_TYPE"
```

`PICK_AND_PUT` 成功结果需要带测量值：

```bash
curl -sS -X POST "$WES_API/workline/operations/results" \
  -H "Authorization: Bearer $WES_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"command_code\": \"$COMMAND_CODE\",
    \"device_code\": \"$DEVICE_CODE\",
    \"result\": \"SUCCESS\",
    \"payload\": {
      \"reel_diameter\": \"178.0\",
      \"reel_thickness\": \"15.0\"
    }
  }" | jq
```

后续 `MOVE_FORWARD`、`PUT_TO_BIN` 的成功结果可以先用空 payload：

```bash
curl -sS -X POST "$WES_API/workline/operations/results" \
  -H "Authorization: Bearer $WES_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"command_code\": \"$COMMAND_CODE\",
    \"device_code\": \"$DEVICE_CODE\",
    \"result\": \"SUCCESS\",
    \"payload\": {}
  }" | jq
```

重复执行：

```text
查询 pending -> 取 command_code/target_code/dispatch_key -> ACK -> Result
```

重复到没有待处理命令，或 Trace 显示 `COMPLETED` / `MANUAL_HOLD`。

如果 `MOVE_FORWARD` 成功后没有新的 `PUT_TO_BIN`，先查 blocking-point。看到 `ROUGH_SORTER_ALLOCATOR_UNAVAILABLE`、
`ROUGH_SORTER_ALLOCATION_BLOCKED` 或等待货架相关阻塞时，说明出料资源没配好。补齐资源域或料箱分配配置后，再跑正常主流程。

### 2.8 清理 Sandbox 残留运行态

多轮调试后，如果设备状态残留为 `RUNNING`、WorkLine 残留为 `READY` / `RECONCILING`，或存在旧 Session / Outbox 影响下一轮测试，可以使用 Sandbox 清理接口。

先预览影响范围：

```bash
curl -sS -X POST "$WES_API/workline/operations/sandbox/worklines/$WID/cleanup" \
  -H "Authorization: Bearer $WES_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"dry_run": true}' | jq
```

确认删除时，`confirmation` 必须等于 WorkLine 编码：

```bash
curl -sS -X POST "$WES_API/workline/operations/sandbox/worklines/$WID/cleanup" \
  -H "Authorization: Bearer $WES_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "dry_run": false,
    "confirmation": "WL-ROUGH-SORTER-TEST"
  }' | jq
```

清理后，Sandbox 会删除该工作线的调试运行图，并将可重置的设备运行态恢复到空闲状态。生产环境不要使用 Sandbox 清理接口。

---

## 3. Mock ECS 调试

本地和 E2E 环境使用一个 ECS Mock 服务统一模拟多台设备，不再按摄像头、机械臂或粗分机组件拆成多个端口。

| 项目 | 值 |
| --- | --- |
| 服务名 | `mock_ecs` |
| 本地端口 | `8010` |
| 命令入口 | `POST /api/v1/device/command` |
| 状态查询 | `GET /api/v1/device/status?device_code=RS-CONVEYOR-01` |
| 手动事件 | `POST /api/v1/mock/event` |
| 故障注入 | `POST /api/v1/mock/devices/{device_code}/scenario` |

支持的正式测试设备码：

- `CAMERA-CONVEYOR-01`
- `ROBOT-ARM-01`
- `RS-INPUT-ARM-01`
- `RS-CONVEYOR-01`
- `RS-OUTPUT-ARM-01`

粗分机标准测试设备使用 `RS-*` 编码。旧 `ARM01`、`PIPELINE01`、`ARM02` 只作为历史文档中的位置示例，不再作为正式 Mock 设备码。

启动 ECS Mock：

```bash
uv run python tests/mock/ecs_mock_server.py
```

查询全部 Mock 设备状态：

```bash
curl -sS "http://127.0.0.1:8010/api/v1/device/status" | jq
```

单设备状态应包含：

```json
{
  "state": {
    "mode": "AUTO",
    "status": "IDLE",
    "current_command_id": null
  }
}
```

手动触发现场 START 事件：

```bash
curl -sS -X POST "http://127.0.0.1:8010/api/v1/mock/event" \
  -H "Content-Type: application/json" \
  -d '{
    "device_code": "RS-INPUT-ARM-01",
    "event_type": "WORKLINE_START_REQUESTED",
    "trace_id": "trace-start-rough-sorter-001",
    "data": {
      "operator": "mock-start"
    }
  }' | jq
```

手动上报扫码事件：

```bash
curl -sS -X POST "http://127.0.0.1:8010/api/v1/mock/event" \
  -H "Content-Type: application/json" \
  -d '{
    "device_code": "RS-INPUT-ARM-01",
    "event_type": "SCAN_COMPLETED",
    "data": {
      "location": "ARM01",
      "HHPN": "CAP001",
      "MfrPN": "V0001-CAP-0402",
      "Qty": "100",
      "DateCode": "20260409",
      "LotCode": "LOT-A",
      "PkgID": "PKG-CAP001-LOT-A-001"
    }
  }' | jq
```

`/api/v1/mock/event` 是 Mock ECS 的调试入口，会由 Mock ECS 按签名规则转发到 WES `/api/v1/callback/event`。

设置下一条命令失败：

```bash
curl -sS -X POST "http://127.0.0.1:8010/api/v1/mock/devices/RS-CONVEYOR-01/scenario" \
  -H "Content-Type: application/json" \
  -d '{"scenario":"fail"}' | jq
```

可选场景为 `success`、`fail`、`timeout`。`timeout` 场景会 ACK 命令但不回调 Result，用于验证 WES 的执行超时和对账流程。

Mock ECS 接收每条 WES 命令时，会为该命令随机生成一个 2~8 秒的模拟运行时间。设备会立即返回 ACK，并在
本条命令的 `command_delay_seconds` 后回调 Result；期间状态接口会保持 `RUNNING` 和 `current_command_id`，
用于观察 WES 对设备运行态、占用和并发准入的处理。

---

## 4. Mock WMS 调试

粗分机 `PICK_AND_PUT` 成功并回传有效测量值后会查 WMS 库存。Sandbox 正常流程使用：

| 字段 | 值 |
| --- | --- |
| `HHPN` | `CAP001` |
| `LotCode` | `LOT-A` |
| `PkgID` | `PKG-CAP001-LOT-A-001` |

启动 WMS Mock：

```bash
uv run python tests/mock/wms_mock_server.py
```

库存查询：

```bash
curl -sS -X POST "http://127.0.0.1:8011/api/wms/inventory/query" \
  -H "Content-Type: application/json" \
  -d '{"sku":"CAP001","lot_no":"LOT-A"}' | jq
```

返回的 `data.items` 应非空。

未知物料或批次会返回空库存，粗分机进入 `WMS_REJECTED` 业务 NG。

---

## 5. Trace 快速检查

联调时不要只看 HTTP 200。用 Trace 核对 Session、Command、Inbox、Outbox、Timeline 是否按顺序推进。

```bash
curl -sS "$WES_API/workline/trace/trace/$TRACE_ID" \
  -H "Authorization: Bearer $WES_TOKEN" | jq

curl -sS "$WES_API/workline/trace/command/$COMMAND_CODE" \
  -H "Authorization: Bearer $WES_TOKEN" | jq

curl -sS "$WES_API/workline/trace/$TRACE_ID/blocking-point" \
  -H "Authorization: Bearer $WES_TOKEN" | jq
```

正常流程应能看到：

- `callback_logs` 中有 `event` 和后续 `result`。
- `inboxes` 已处理完成。
- `commands` 按顺序生成并完成。
- `outboxes` 已派发或 Sandbox 已处理。
- `sessions` 最终为 `COMPLETED`。
- 没有未处理的 blocking diagnostic。
