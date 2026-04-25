# 第二 WORKLINE 插件薄 Spike：料箱入库称重复核

## 目的

本 spike 用一个不同于 `smt_classifier` 的 WORKLINE 场景反向约束插件平台原语。

当前阶段不实现完整插件闭环，也不修改 runtime 私有分支；它只提供 PR2 之后 manifest、业务键解析、设备拓扑、业务 NG / 系统异常分类和 sandbox 调试的验收输入。

## 场景选择

场景：`inbound_tote_qc`，料箱进入入库复核工作线后，WES 下发称重指令，根据称重结果把料箱放行或分流到异常线。

选择原因：

- 业务键不是 SMT 的 Six-In-One / `PkgID`，而是 `data.tote_id`。
- 首个输入是设备事件，后续至少等待一次设备命令结果回调。
- 业务 NG 是正常业务拒收，例如重量超差；它不应污染系统失败指标。
- 系统异常仍然存在，例如称重设备失败、回调缺字段、状态不匹配。
- sandbox 调试必须走完整派发链路，只是派发到沙箱通道，由调试人员手工回调。

## 薄规格

### 插件身份

```yaml
plugin_key: inbound_tote_qc
contract_version: spike-2026.04
runtime_private_branch_allowed: false
```

### 业务键来源

业务键由 `event.data.tote_id` 解析。

示例：

```json
{
  "device_code": "SCAN01",
  "event_type": "TOTE_ARRIVED",
  "timestamp": 1777046400000,
  "data": {
    "tote_id": "TOTE-20260425-001",
    "station_code": "INBOUND_QC_01",
    "expected_weight_kg": 12.5,
    "tolerance_kg": 0.2
  }
}
```

### 设备角色

| 角色 | 数量 | 能力 |
| --- | --- | --- |
| `ENTRY_SCANNER` | 1 | `scan_tote` |
| `WEIGH_SCALE` | 1 | `measure_weight` |
| `DIVERT_CONVEYOR` | 1 | `divert_lane` |

上下游关系不在插件规格中重复定义，由 `Device.upstream_device_id` 推导。

### 事件输入 `data` 模型

`TOTE_ARRIVED.data`：

```json
{
  "tote_id": "TOTE-20260425-001",
  "station_code": "INBOUND_QC_01",
  "expected_weight_kg": 12.5,
  "tolerance_kg": 0.2
}
```

### 命令输出 `params` 模型

`WEIGH_TOTE.params`：

```json
{
  "tote_id": "TOTE-20260425-001",
  "station_code": "INBOUND_QC_01"
}
```

`DIVERT_TOTE.params`：

```json
{
  "tote_id": "TOTE-20260425-001",
  "destination_lane": "PASS_LANE",
  "reason_code": "WEIGHT_OK"
}
```

### 等待回调

`WEIGH_TOTE` 下发后等待 `/callback/result`：

```json
{
  "command_code": "CMD-WEIGH-001",
  "device_code": "SCALE01",
  "result": "SUCCESS",
  "finish_time": 1777046405000,
  "data": {
    "tote_id": "TOTE-20260425-001",
    "actual_weight_kg": 12.58
  }
}
```

### 业务 NG

重量超出允差时分类为业务决策：

```json
{
  "classification": "business_decision",
  "reason_code": "WEIGHT_OUT_OF_TOLERANCE",
  "business_key": "TOTE-20260425-001",
  "evidence": {
    "expected_weight_kg": 12.5,
    "actual_weight_kg": 13.1,
    "tolerance_kg": 0.2
  }
}
```

Session 可以完成，并下发 `DIVERT_TOTE` 到 `HOLD_LANE`。

### 系统异常

以下情况是系统异常或硬件异常，不应被记录成业务 NG：

- `TOTE_ARRIVED.data.tote_id` 缺失，业务键无法稳定解析。
- `WEIGH_TOTE` 回调 `result=FAILED` 且带 `error_detail`。
- `WEIGH_TOTE` 回调缺少 `data.actual_weight_kg`。
- 当前插件状态不允许处理该回调。

### Sandbox happy path

`WorkLine.run_mode=SIMULATION` 时：

- runtime 仍创建命令和 outbox / timeline。
- effect adapter 把命令派发到 sandbox 通道，而不是真实设备。
- 消息 payload 不增加 sandbox 标志字段，仍保持白皮书两层结构。
- 调试人员通过 `/callback/result` 手工回调 `WEIGH_TOTE` 成功结果，流程继续推进。

## PR2 之后的验收边界

- `business_key_resolver` 必须来自插件 manifest / contract，不允许在 `SessionResolver` 中新增 `inbound_tote_qc` 私有分支。
- 设备拓扑必须从 `Device.upstream_device_id` 推导，不允许插件持有数据库对象。
- 命令业务数据必须只出现在 `params`。
- 回调业务数据必须只出现在 `data`。
- 业务 NG 必须进入 business decision / trace 投影，不进入系统 failure 指标。
