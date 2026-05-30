# 粗分机 Runtime 流程与设备协议示例

> 日期：2026-05-30
> 适用范围：`rough_sorter` WorkLine 插件、设备事件上报、设备命令下发、Result Callback、WMS/RCS 外部回调。

本文只描述当前未发布合同。设备协议不保留旧兼容字段：WES 下发命令只使用 `task_type`，不再下发 `command_type`；`command_code` 由 WES 统一生成，插件不得传入或覆盖。

## 1. 链路标识约定

| 字段 | 归属 | 用途 |
| --- | --- | --- |
| `session_id` | WES 内部事务主锚点 | 串联一笔粗分机物料处理流程和该流程下所有设备命令。 |
| `command_code` | WES 生成，对设备可见 | 设备命令幂等键；Result Callback 必须原样回传。格式：`CMD-{YYYYMMDD}-S{session_id}-{TASK_TYPE}-{RANDOM8}`。 |
| `trace_id` | 观测与跨系统串联 | 用于日志、诊断、WMS/RCS 外部等待恢复；不进入 `command_code`。 |
| `event_id` | 事件源生成或 WES 补齐 | 设备事件、外部回调的幂等输入。 |

同一 Session 下的命令示例：

```text
Session 417
  CMD-20260530-S417-MEASUREMENT_REEL-ABCDEF12
  CMD-20260530-S417-PICK_AND_PUT-9F2A1C0B
  CMD-20260530-S417-MOVE_FORWARD-7A8B9C0D
  CMD-20260530-S417-PUT_TO_BIN-0D1E2F3A
```

## 2. 扫码设备：事件入口

执行角色：扫码设备或入料扫码点。

设备向 WES 上报 `SCAN_COMPLETED`：

```http
POST /api/v1/callback/event
Content-Type: application/json
```

```json
{
  "device_code": "RS-SCAN-01",
  "event_type": "SCAN_COMPLETED",
  "timestamp": 1777046400000,
  "event_id": "RS-SCAN-01-20260530-000001",
  "data": {
    "HHPN": "HHPN-001",
    "MfrPN": "MFR-001",
    "Qty": 1,
    "DateCode": "2522",
    "LotCode": "LOT-001",
    "PkgID": "PKG-20260530-000001"
  }
}
```

WES 行为：

- 解析 `data` 中的六合一码，生成或恢复 `WorklineSession`。
- 新 Session 示例：`session_id=417`，`trace_id=trace_...`。
- 生成第一条设备命令 `MEASUREMENT_REEL`，进入等待设备 Result。

## 3. 入料机械臂：测量与入料搬运

执行角色：`ROUGH_SORTER_INPUT_ARM`，示例设备 `RS-INPUT-ARM-01`。

### 3.1 WES 下发测量命令

```json
{
  "device_code": "RS-INPUT-ARM-01",
  "command_code": "CMD-20260530-S417-MEASUREMENT_REEL-ABCDEF12",
  "task_type": "MEASUREMENT_REEL",
  "priority": 5,
  "timeout": 300000,
  "timestamp": 1777046400100,
  "params": {
    "business_key": "PKG-20260530-000001",
    "six_in_one": {
      "HHPN": "HHPN-001",
      "MfrPN": "MFR-001",
      "Qty": 1,
      "DateCode": "2522",
      "LotCode": "LOT-001",
      "PkgID": "PKG-20260530-000001"
    }
  }
}
```

设备完成后回传 Result：

```http
POST /api/v1/callback/result
Content-Type: application/json
```

```json
{
  "device_code": "RS-INPUT-ARM-01",
  "command_code": "CMD-20260530-S417-MEASUREMENT_REEL-ABCDEF12",
  "result": "SUCCESS",
  "finish_time": 1777046409000,
  "data": {
    "measurement_result": "OK",
    "reel_diameter": 180,
    "reel_thickness": 16
  },
  "error_detail": null
}
```

WES 行为：

- 通过 `command_code` 找到 `DeviceCommand` 和等待中的 Session。
- 按六合一码查询 WMS 库存准入。
- 准入成功后生成入料搬运命令。

### 3.2 WES 下发入料搬运命令

```json
{
  "device_code": "RS-INPUT-ARM-01",
  "command_code": "CMD-20260530-S417-PICK_AND_PUT-9F2A1C0B",
  "task_type": "PICK_AND_PUT",
  "priority": 5,
  "timeout": 300000,
  "timestamp": 1777046410000,
  "params": {
    "business_key": "PKG-20260530-000001",
    "source_location": "SCAN_STATION",
    "target_location": "CONVEYOR_INPUT"
  }
}
```

Result Callback：

```json
{
  "device_code": "RS-INPUT-ARM-01",
  "command_code": "CMD-20260530-S417-PICK_AND_PUT-9F2A1C0B",
  "result": "SUCCESS",
  "finish_time": 1777046415000,
  "data": {
    "actual_source_location": "SCAN_STATION",
    "actual_target_location": "CONVEYOR_INPUT"
  },
  "error_detail": null
}
```

## 4. 输送线：前进到出料位

执行角色：`ROUGH_SORTER_CONVEYOR`，示例设备 `RS-CONVEYOR-01`。

WES 下发输送命令：

```json
{
  "device_code": "RS-CONVEYOR-01",
  "command_code": "CMD-20260530-S417-MOVE_FORWARD-7A8B9C0D",
  "task_type": "MOVE_FORWARD",
  "priority": 5,
  "timeout": 300000,
  "timestamp": 1777046416000,
  "params": {
    "business_key": "PKG-20260530-000001",
    "from_position": "CONVEYOR_INPUT",
    "to_position": "CONVEYOR_OUTPUT"
  }
}
```

Result Callback：

```json
{
  "device_code": "RS-CONVEYOR-01",
  "command_code": "CMD-20260530-S417-MOVE_FORWARD-7A8B9C0D",
  "result": "SUCCESS",
  "finish_time": 1777046422000,
  "data": {
    "current_position": "CONVEYOR_OUTPUT"
  },
  "error_detail": null
}
```

WES 行为：

- 按本地资源投影为物料分配可用 `bin_cell_location`。
- 如果没有可用料格，进入 WMS/RCS 货架补给等待分支。
- 如果分配成功，下发出料入箱命令。

## 5. 出料机械臂：放入料箱

执行角色：`ROUGH_SORTER_OUTPUT_ARM`，示例设备 `RS-OUTPUT-ARM-01`。

WES 下发出料入箱命令：

```json
{
  "device_code": "RS-OUTPUT-ARM-01",
  "command_code": "CMD-20260530-S417-PUT_TO_BIN-0D1E2F3A",
  "task_type": "PUT_TO_BIN",
  "priority": 5,
  "timeout": 300000,
  "timestamp": 1777046423000,
  "params": {
    "business_key": "PKG-20260530-000001",
    "source_location": "CONVEYOR_OUTPUT",
    "bin_id": "BIN-A-001",
    "bin_cell_location": "A01"
  }
}
```

Result Callback：

```json
{
  "device_code": "RS-OUTPUT-ARM-01",
  "command_code": "CMD-20260530-S417-PUT_TO_BIN-0D1E2F3A",
  "result": "SUCCESS",
  "finish_time": 1777046430000,
  "data": {
    "bin_id": "BIN-A-001",
    "bin_cell_location": "A01"
  },
  "error_detail": null
}
```

WES 行为：

- 写入料格占用事实。
- 挂载物料到目标料格。
- Session 进入 `COMPLETED`。

## 6. WMS/RCS：货架补给等待分支

当本地资源投影没有可用料格时，WES 发起外部请求并等待外部回调。外部回调仍使用 `trace_id` 或稳定 `dispatch_key` 恢复等待中的 Session；这属于跨系统观测/恢复链路，不替代内部 `session_id`。

WMS/RCS 回调示例：

```http
POST /api/v1/callback/external
Content-Type: application/json
```

```json
{
  "callback_type": "WMS_RACK_ARRIVED",
  "trace_id": "trace_7f7a9e9f3c4a4a0b8f8f2b6a9a1c2d3e",
  "dispatch_key": "rack-operation:op-20260530-000001:2:ALLOCATE_AND_MOVE_RACK",
  "source_system": "WMS",
  "source_event_id": "WMS-RACK-ARRIVED-20260530-000001",
  "source_version": "1",
  "occurred_at": "2026-05-30T09:20:00+08:00",
  "request_id": "REQ-WMS-RACK-ARRIVED-20260530-000001",
  "timestamp": "2026-05-30T09:20:01+08:00",
  "signature": "test-signature",
  "rack_id": "RACK-01",
  "position_code": "ROUGH_SORTER_OUTPUT",
  "active_bin_rack": {
    "rack_id": "RACK-01",
    "rack_kind": "SINGLE_LAYER",
    "bins": [
      {
        "bin_id": "BIN-A-001",
        "empty_cells": ["A01", "A02", "A03"]
      }
    ]
  }
}
```

`source_system` 只能取 `WMS` 或 `RCS`；`dispatch_key` 必须回传 WES 外部 Outbox 的派发键，用于恢复 `WAITING_EXTERNAL` Session。

WES 行为：

- 落资源事实，更新货架/料箱/料格投影。
- 创建内部 `ROUGH_SORTER_STORAGE_RETRY` 恢复事件。
- 下一轮重新分配料格，并继续下发 `PUT_TO_BIN`。
