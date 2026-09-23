---
title: 设备合同附录：自动拣料机械臂（ARM01 / ARM02）
status: Draft
created_at: 2026-09-21
audience: ECS 供应商、WES 与 WMS 开发人员、联调人员
scope: 自动拣料线两台分拣机械臂的 PICK_AND_PUT 命令、结果回调和扫码平台 SCAN_COMPLETED 事件
related:
  - docs/integration/third_party_integration_whitepaper.md
  - docs/contracts/wms-outbound-picking-task-integration-requirements.md
  - docs/hardware/SMT分拣机ECS接口调用说明书V1-20260318.md
---

# 设备合同附录：自动拣料机械臂（ARM01 / ARM02）

## 1. 定位

本附录是[第三方设备统一接口白皮书](third_party_integration_whitepaper.md)对 `ARM01`、`ARM02` 的补充，只定义白皮书留给附录的
`task_type`、`params`、结果 `data`、`error_detail.code` 和事件 `data`。路径、包络、ACK、幂等、重试规则全部以白皮书为准，本文不重复。

`docs/hardware/` 的供应商原文保持原样。原文中的 `source` / `target` 顶层字段，在统一接口里必须封装进 `params`。
本文是**草案**：标记为“待确认”的条目未经供应商书面确认前，不得据此编写生产 Adapter 或 handler。

## 2. 现场事实（2026-09-21 读取 `GET http://10.24.209.28:8080/api/v1/device/status`）

| `device_code` | `device_type` | `role` | `supported_commands` | `supported_events` |
| --- | --- | --- | --- | --- |
| `ARM01` | `ROBOTIC_ARM` | `SORTER_ARM` | `PICK_AND_PUT` | `SCAN_COMPLETED`、`ESTOP_PRESSED` |
| `ARM02` | `ROBOTIC_ARM` | `SORTER_ARM` | `PICK_AND_PUT` | `SCAN_COMPLETED`、`ESTOP_PRESSED` |

- 状态里没有独立的扫码平台设备，扫码平台读码事件由谁的 `device_code` 上报，见待确认 O1。
- `ESTOP_PRESSED` 不得发送到 WES 事件回调（白皮书 §4.2），由 ECS 独立处理。

## 3. 设备分工

| WES 业务角色 | `device_code` | 动作 | 起点 → 终点（ECS `location_type`） |
| --- | --- | --- | --- |
| `SOURCE_ARM` | `ARM01` | 从工作位料箱吸出料盘 | `BIN`（SCAN2 工作位）→ `SCAN_PLATFORM` |
| `SOURCE_ARM` | `ARM01` | 从退料货架直接取料（`added_direct_picks`） | `ONE_LAYER_RACK`（待确认 O12）→ `SCAN_PLATFORM` |
| `TARGET_ARM` | `ARM02` | 从扫码平台放到目标储位 | `SCAN_PLATFORM` → `FIVE_LAYER_RACK` |

WES 的业务角色“转运货架”，在 ECS 侧的 `location_type` 就是 `FIVE_LAYER_RACK`（现场确认）。
来源五层货架的料箱由 CTU 和滚筒线搬运，机械臂不直接操作，两者只是同一物理货架类型，业务角色不同。

`location_id`（如 `STATION_WORK1`、`STATION_SCAN`、`STATION_A/B`）取自 WorkLine 位置绑定，不写死在业务代码里。

## 4. 命令：`task_type = PICK_AND_PUT`

外层字段固定为白皮书 §3.1 的七个：`device_code`、`command_code`、`task_type`、`priority=1`、`timeout`（整数毫秒）、`timestamp`（整数毫秒）、`params`。

### 4.1 `params` 公共字段

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `business_key` | 是 | WES 生成的稳定业务键，对应一个 `MaterialExecution`；ECS 只原样回显，用于追溯（待确认 O6 是否必要） |
| `source` | 是 | 起点位置对象，见 4.2 |
| `target` | 是 | 终点位置对象，见 4.2 |

### 4.2 位置对象（`source` / `target`）

字段名沿用供应商原文，不改名。逻辑字段由 WMS 授权，WES 只转换、不计算。

| `location_type` | 必填字段 | 逻辑来源 |
| --- | --- | --- |
| `BIN` | `location_id`、`location_type`、`bin_cell_location` | WMS 希望 `cell_id` 保持 `BIN_ID + INDEX` 业务编码；提议使用另行提供的 `cell_index` 定位料箱内料格并映射 `bin_cell_location`（待 O16 与主合同修订确认），不得拆解或直接下发 `cell_id` |
| `ONE_LAYER_RACK` | 供应商原文要求 `location_id`、`location_type`、`bin_type`、`bin_location`、`bin_cell_location` 等；退料货架储位直接放料盘，字段是否适用见 O12、O13 | 来自 `plan_delta.added_direct_picks[].source_locator`（`rack_id`、`rack_face`、`slot_id`） |
| `SCAN_PLATFORM` | `location_id`、`location_type` | WorkLine 位置绑定 |
| `FIVE_LAYER_RACK` | `location_id`、`location_type`、`rack_id`、`rack_side`、`rack_layer`、`rack_column` | 提议由 WMS `material.decide.ACCEPT.target_locator` 给出层/列（待主合同修订确认）；`rack_side` 与 `rack_face` 值域仍待 O11 确认 |

供应商原文还要求 `bin_type`、`reel_layer`、`reel_thickness`、`reel_totalthickness`、`reel_diameter`。
**2026-09-21 更新**：`reel_totalthickness` 不需要 WMS 提供，WES 本地按 `reel_thickness × reel_layer` 计算。
其余四个字段（`bin_type`、`reel_layer`、`reel_thickness`、`reel_diameter`）仍然没有来源——这是出库合同 `work_plan.READY`
的真实缺口，需要合同新增字段，已转入 [WMS/WES 主合同修订提案](wms-joint-confirmation-automatic-picking.md) 第 6 节，不是设备附录能单独解决的问题。

### 4.3 示例

ARM01（工作位料箱 → 扫码平台）：

```json
{
  "device_code": "ARM01",
  "command_code": "019f3410-af77-71fd-9bde-0df75fcdeea1",
  "task_type": "PICK_AND_PUT",
  "priority": 1,
  "timeout": 30000,
  "timestamp": 1790019600000,
  "params": {
    "business_key": "MEXEC-0001",
    "source": {"location_id": "STATION_WORK1", "location_type": "BIN", "bin_cell_location": "1"},
    "target": {"location_id": "STATION_SCAN", "location_type": "SCAN_PLATFORM"}
  }
}
```

ARM02（扫码平台 → 转运货架储位）：

```json
{
  "device_code": "ARM02",
  "command_code": "019f3410-af77-71fd-9bde-0df75fcdeea2",
  "task_type": "PICK_AND_PUT",
  "priority": 1,
  "timeout": 30000,
  "timestamp": 1790019610000,
  "params": {
    "business_key": "MEXEC-0001",
    "source": {"location_id": "STATION_SCAN", "location_type": "SCAN_PLATFORM"},
    "target": {
      "location_id": "STATION_A", "location_type": "FIVE_LAYER_RACK",
      "rack_id": "RACK_01", "rack_side": "A", "rack_layer": "1", "rack_column": "1"
    }
  }
}
```

ARM01（退料货架直接取料 → 扫码平台，示例，`source` 字段待 O12/O13/O17 确认前不得用于生产实现）：

```json
{
  "device_code": "ARM01",
  "command_code": "019f3410-af77-71fd-9bde-0df75fcdeea3",
  "task_type": "PICK_AND_PUT",
  "priority": 1,
  "timeout": 30000,
  "timestamp": 1790019700000,
  "params": {
    "business_key": "MEXEC-0002",
    "source": {
      "location_id": "STATION_RETURN_RACK",
      "location_type": "ONE_LAYER_RACK",
      "bin_type": "1",
      "bin_location": "A-03",
      "bin_cell_location": "1"
    },
    "target": {"location_id": "STATION_SCAN", "location_type": "SCAN_PLATFORM"}
  }
}
```

`source` 里除 `location_id`/`location_type` 外的字段全部是占位值：`bin_type`、`bin_cell_location` 是否适用、`bin_location` 是否就是
`plan_delta.added_direct_picks[].source_locator.slot_id`，以及这些值从哪条 wire 拿到，均未确认（O12、O13、O17）。

同一个 `MaterialExecution` 依次产生两条命令，顺序固定：先 ARM01，收到 `SUCCESS` 且扫码事件到达并经 WMS 决定 `ACCEPT` 后，才发 ARM02。

## 5. 结果回调

外层字段按白皮书 §4.1，`error_detail` 只含 `code` 和 `msg`（供应商原文的 `error_code` / `error_message` 发送前必须改名）。

| 顶层 `result` | `data.pick_and_put_result` | 含义 | WES 处理 |
| --- | --- | --- | --- |
| `SUCCESS` | `PUT_FINISHED` | 已放下，物理动作完成 | 更新本地位置证据，推进下一步 |
| `FAILED` | `PICK_FAILED` | 取料失败 | 受影响对象暂停并对账 |
| `FAILED` | `PUT_FAILED` | 放料失败 | 受影响对象暂停并对账 |

- `data.actual_qty`：实际搬运数量，成功为 `1`。
- `PICK_FAILED` 不等于“空取”。空取要求设备明确确认该 Cell 无料，附录当前没有对应值，第一版所有失败一律暂停对账，不自动走 `source.empty_decide`（待确认 O5）。
- `error_detail.code` 的取值集合、与供应商原厂 `1001/1002/1003` 的对应、以及 `supplier_raw_code` 的带法，按
  [设备错误语义](workline_device_error_code_standardization.md)，待确认 O7。

ACK 只表示接纳。只有匹配 `command_code` 的 `SUCCESS` 回调才是物理完成。

`SUCCESS` 示例：

```json
{
  "command_code": "019f3410-af77-71fd-9bde-0df75fcdeea2",
  "device_code": "ARM02",
  "result": "SUCCESS",
  "finish_time": 1790019620000,
  "data": {"actual_qty": 1, "pick_and_put_result": "PUT_FINISHED"},
  "error_detail": null
}
```

`FAILED`（取料失败）示例，`error_detail.code` 是占位值，实际取值表见 O7：

```json
{
  "command_code": "019f3410-af77-71fd-9bde-0df75fcdeea1",
  "device_code": "ARM01",
  "result": "FAILED",
  "finish_time": 1790019605000,
  "data": {"actual_qty": 0, "pick_and_put_result": "PICK_FAILED"},
  "error_detail": {"code": "DEVICE_FAULT", "msg": "取料失败"}
}
```

`FAILED`（放料失败）示例：

```json
{
  "command_code": "019f3410-af77-71fd-9bde-0df75fcdeea2",
  "device_code": "ARM02",
  "result": "FAILED",
  "finish_time": 1790019620000,
  "data": {"actual_qty": 0, "pick_and_put_result": "PUT_FAILED"},
  "error_detail": {"code": "TARGET_BLOCKED", "msg": "目标位置被阻挡"}
}
```

## 6. 事件：`SCAN_COMPLETED`（扫码平台）

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `device_code` | 是 | 上报设备，见 O1 |
| `event_type` | 是 | 固定 `SCAN_COMPLETED` |
| `timestamp` | 是 | 整数毫秒 |
| `data.location` | 是 | 固定为扫码平台 `location_id`（如 `STATION_SCAN`） |
| `data.source_command_code` | 是 | 把该料盘放上平台的 ARM01 `command_code`，用于关联（待确认 O2） |
| `data.barcode` | 是 | 单个原始字符串，ECS 不拆分，WES 原样保存并转发（O3 已确定，见下方） |

完整事件样例（2026-09-21 联调实际收到的报文，`device_code` 为 `SCANNER_001`——这个设备不在 §2 的状态查询结果里，供应商尚未
书面确认这就是最终身份，见 O1；这份样例也没有 `source_command_code`，O2 仍然开放）：

```json
{
  "device_code": "SCANNER_001",
  "event_type": "SCAN_COMPLETED",
  "timestamp": 1789851780522,
  "data": {
    "location": "STATION_SCAN",
    "barcode": "P032-0561-000HF,Q3000,M0805S106K160CT,D260505,L100R028291,S72052872716100149602"
  }
}
```

**WES 不解析这个字符串**，原样保存为扫码证据，并原样转发给 WMS。这意味着 `outbound.material.decide@v1` 的请求字段
（合同 §10.2 的 `data.six_in_one.{HHPN,MfrPN,Qty,DateCode,LotCode,PkgID}` 六个独立字段）需要改成一个原始 `barcode`
字符串字段——这是合同变更，不是设备附录能单独解决的，见 [WMS/WES 主合同修订提案](wms-joint-confirmation-automatic-picking.md) 第 6 节。
六合一码的拆分和业务解释完全由 WMS 负责，WES 只做证据保存和原样转发，不做任何解析、拆分或格式假设。

## 7. 时限、互锁与恢复

- `timeout`：暂定 `30000` 毫秒，由 ECS 按实际节拍确认（O9）。
- 扫码平台单盘承载、双臂防撞和干涉区互斥完全由 ECS/PLC 硬件锁保证。WES 不建占用锁，也不等待“平台已释放”事件。
- ACK 后无回调、交付结果未知：WES 保留原 `command_code` 和围栏进入对账，不换身份重发。ECS 必须支持同一 `command_code` 重报完全相同的回调，
  并让 `status.current_command_code` 反映真实活动命令。
- 顶层协议不提供 Cancel。

## 8. 验收场景

| 场景 | 期望 |
| --- | --- |
| 正常：ARM01 → 扫码 → ACCEPT → ARM02 | 两条命令各一次 `SUCCESS`，位置证据和 WMS 位置上报各一次 |
| ACK 后 `FAILED`（取料 / 放料） | 对象暂停，保留旧位置证据，不自动重发 |
| 同一 `command_code` 相同载荷重发 | 返回相同 ACK，设备不重复动作 |
| 同一 `command_code` 不同载荷 | `409 IDEMPOTENCY_CONFLICT` |
| 结果回调重复 | 只处理一次 |
| 结果回调早于 ACK / 乱序 | 保留证据，按原身份收敛 |
| 扫码事件重复 | 只关联一次，不生成第二个 `MaterialExecution` |
| ARM02 命令超时无回调 | 保留原命令身份与冻结请求，按设备合同取得确定结果；只有无法依据合同和权威 Evidence 确定安全下一动作时才进入对账，不以超时推断物理失败或释放目标储位 |

## 9. 待确认清单

| # | 问题 | 询问对象 |
| --- | --- | --- |
| O1 | 扫码平台事件用哪个 `device_code` 上报（`ARM01`、`ARM02` 还是独立设备）？状态查询里没有独立扫码设备 | ECS 供应商 |
| O2 | 事件能否带 `source_command_code`？否则 WES 只能按“平台同一时刻仅一盘”推断 | ECS 供应商 |
| ~~O3~~ | 已确定：WES 不解析，原样转发 `barcode` 给 WMS；`material.decide` 请求字段需要合同变更，见 §6 更新 | 已关闭 |
| O4 | `bin_type`、`reel_layer`、`reel_thickness`、`reel_diameter` 拟由 WMS 在 `work_plan.READY` 里按 Cell 提供（见主合同修订提案第 6 节，待 WMS 确认）；`reel_totalthickness` 由 WES 本地计算 | WMS |
| O5 | 设备能否明确区分“Cell 无料（空取）”与“取料失败”？ | ECS 供应商 |
| O6 | `params.business_key` 是否需要，或仅靠 `command_code` 追溯 | ECS 供应商 |
| O7 | `error_detail.code` 取值表及原厂码对应关系 | ECS 供应商 |
| O8 | 读不出码或不完整时，ECS 发什么事件、是否重扫、何时放弃 | ECS 供应商 |
| O9 | `PICK_AND_PUT` 的合理 `timeout`、结果回调最长延迟 | ECS 供应商 |
| O10 | 提议在 `material.decide.ACCEPT.target_locator` 增加 `rack_layer`、`rack_column`，由 WMS 直接给出；待主合同修订提案第 6 节确认 | WMS |
| O11 | `rack_side`（A/B）与合同 `rack_face` 是否同一值域 | WMS + ECS 供应商 |
| O12 | 退料货架在 ECS 里的 `location_type` 是否为 `ONE_LAYER_RACK`，`ARM01` 能否从它直接取单个料盘 | ECS 供应商 |
| O13 | 退料货架直接取料的位置字段：`bin_*` 是否适用于“储位直接放料盘”，`slot_id` 如何映射到 ECS 字段（字段名和 O10 不同，但可以是同一种做法：WMS 直接给出 ECS 需要的字段，不用 WES 反推） | ECS 供应商 + WMS |
| O14 | 直接取料的“空取”信号，是否与 O5 同一机制 | ECS 供应商 |
| O15 | 2026-09-21 联调样例的 ARM02 `target` 缺少 `location_type`；硬件文档 §8.1 要求 `FIVE_LAYER_RACK` 必填该字段，请确认是遗漏还是有意省略 | ECS 供应商 |
| O16 | WMS 希望 `cell_id` 保持 `BIN_ID + INDEX` 编码，可单独提供料箱内定位号 `cell_index`；请 WMS 与 ECS 供应商确认 `cell_index` 的类型/值域及到 `bin_cell_location` 的映射，WES 不从 `cell_id` 推算 | WMS + ECS 供应商 |
| O17 | 直接取料的 ARM01 测量字段（`bin_type`、`reel_layer`、`reel_thickness`、`reel_diameter`，如果 O13 确认 `ONE_LAYER_RACK` 需要）目前没有任何 wire 来源：`plan_delta.added_direct_picks[]` 只给 `source_locator`，直接取料没有 `work_plan` 等价物。如果确实需要，建议同 O4/变更 1 的解法，扩展 `added_direct_picks[]` 每项带上这些字段 | WMS |
