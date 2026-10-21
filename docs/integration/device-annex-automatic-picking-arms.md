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
| `BIN` | `location_id`、`location_type`、`bin_cell_location` | WMS 已确认 `cell_id` 保持 `BIN_ID + INDEX` 业务编码，并单独提供 `cell_index`；WES 按 O16 冻结的设备映射生成 `bin_cell_location` |
| `ONE_LAYER_RACK` | 供应商原文要求 `location_id`、`location_type`、`bin_type`、`bin_location`、`bin_cell_location` 等；退料货架储位直接放料盘，字段是否适用见 O12、O13 | 来自 `plan_delta.added_direct_picks[].source_locator`（`rack_id`、`rack_face`、`slot_id`） |
| `SCAN_PLATFORM` | `location_id`、`location_type` | WorkLine 位置绑定 |
| `FIVE_LAYER_RACK` | `location_id`、`location_type`、`rack_id`、`rack_side`、`rack_layer`、`rack_column` | WMS 已接受在 `material.decide.ACCEPT.target_locator` 给出层/列；`rack_side` 与 `rack_face` 值域仍待 O11 确认 |

供应商原文还要求 `bin_type`、`reel_layer`、`reel_thickness`、`reel_totalthickness`、`reel_diameter`。
**2026-09-21 更新**：`reel_totalthickness` 不需要 WMS 提供，WES 本地按 `reel_thickness × reel_layer` 计算。
其余四个字段（`bin_type`、`reel_layer`、`reel_thickness`、`reel_diameter`）由 WMS 已接受的
`work_plan.READY` 目标响应提供，见[出库主合同 §9.3](../contracts/wms-outbound-picking-task-integration-requirements.md)；已确认的公共命令/事实接线见 §10.1；设备私有参数编码仍待 O4/O16 等项冻结。

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

**WES 不解析这个字符串**，原样保存为扫码证据，并按 WMS 已接受的 `outbound.material.decide@v1` 目标请求以
`data.barcode` 原样转发，见[出库主合同 §10.2](../contracts/wms-outbound-picking-task-integration-requirements.md)。
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
| O4 | WMS 已接受在 `work_plan.READY` 按 Cell 提供 `bin_type`、`reel_layer`、`reel_thickness`、`reel_diameter`；`reel_totalthickness` 由 WES 本地计算，接线与设备验收仍待完成 | WMS |
| O5 | 设备能否明确区分“Cell 无料（空取）”与“取料失败”？ | ECS 供应商 |
| O6 | `params.business_key` 是否需要，或仅靠 `command_code` 追溯 | ECS 供应商 |
| O7 | `error_detail.code` 取值表及原厂码对应关系 | ECS 供应商 |
| O8 | 读不出码或不完整时，ECS 发什么事件、是否重扫、何时放弃 | ECS 供应商 |
| O9 | `PICK_AND_PUT` 的合理 `timeout`、结果回调最长延迟 | ECS 供应商 |
| O10 | WMS 已接受在 `material.decide.ACCEPT.target_locator` 提供 `rack_layer`、`rack_column`；设备值域与映射仍需验收 | WMS |
| O11 | `rack_side`（A/B）与合同 `rack_face` 是否同一值域 | WMS + ECS 供应商 |
| O12 | 退料货架在 ECS 里的 `location_type` 是否为 `ONE_LAYER_RACK`，`ARM01` 能否从它直接取单个料盘 | ECS 供应商 |
| O13 | 退料货架直接取料的位置字段：`bin_*` 是否适用于“储位直接放料盘”，`slot_id` 如何映射到 ECS 字段（字段名和 O10 不同，但可以是同一种做法：WMS 直接给出 ECS 需要的字段，不用 WES 反推） | ECS 供应商 + WMS |
| O14 | 直接取料的“空取”信号，是否与 O5 同一机制 | ECS 供应商 |
| O15 | 2026-09-21 联调样例的 ARM02 `target` 缺少 `location_type`；硬件文档 §8.1 要求 `FIVE_LAYER_RACK` 必填该字段，请确认是遗漏还是有意省略 | ECS 供应商 |
| O16 | WMS 已确认 `cell_id` 保持 `BIN_ID + INDEX` 编码并单独提供料箱内定位号 `cell_index`；`cell_index` 的设备类型/值域及到 `bin_cell_location` 的映射由 WMS 与 ECS 供应商冻结 | WMS + ECS 供应商 |
| O17 | 直接取料的 ARM01 测量字段（`bin_type`、`reel_layer`、`reel_thickness`、`reel_diameter`，如果 O13 确认 `ONE_LAYER_RACK` 需要）目前没有任何 wire 来源：`plan_delta.added_direct_picks[]` 只给 `source_locator`，直接取料没有 `work_plan` 等价物。如果确实需要，建议同 O4/变更 1 的解法，扩展 `added_direct_picks[]` 每项带上这些字段 | WMS |


## 10. 正常业务装配与真实接口门禁（2026-10-22 核对）

本轮将料箱到工作位后的 BIN_CELL 正常链统一装配：Work READY→来源 Command→精确扫码绑定→material.decide（含确定 WAIT）→普通/ROTATE/REPLACE 目标就绪→PUT→movement_report→RECORDED/DUPLICATE 关闭→下一来源或点2正常流出/SCAN3。已确认的设备事实读口与公共命令映射已实现；完整设备位置正文、精确扫码关联及权威 PkgID 仍须由调用方显式注入；`build_picking_plugin` 不注册到生产 deployment，不发送 WMS 请求或启用设备派发。离线输入不能当作下表未决项已确认。

REPLACE 已接入首次 `departure_decide`、持久当前引用与确定 WAIT 到期续问，再沿原 F01 旧架离场/新架进场和共用 PUT/report/close 继续，不重新请求物料资格。首次输入只需精确旧架、原进场轮次及到位事实；当前请求、轮次和到期点保存在 Pickup，恢复不按“最新请求”猜身份。所有搬运和机械臂成功事实仍必须匹配并已发布；未知交付不创建等价新动作。这证明显式权威输入下的离线业务闭环，不代表下表接口门禁已解除。

| 依赖路径 | 已确认基线 | 真正缺少的接口证据 / 阻塞范围 |
| --- | --- | --- |
| Cell 取盘参数 | WMS `cell_index` 为 int 1–7；`bin_type` 为 int 2/3/6；`reel_layer` 为 int，厚度/直径为 float；总厚度按既有约定计算 | O16：ECS `bin_cell_location` 的最终类型/映射；O4 的设备侧数值序列化和适用字段。只限制真实来源命令映射，不阻塞授权/持久事务 |
| 单盘扫码关联 | barcode 原样转发，str 最长1000；本地 Pickup/原来源 Command 精确关联 | O1/O2：最终扫码设备身份及可可靠回显的来源 Command 关联字段；现有样例缺 source_command_code，不能猜“最近一条命令” |
| 来源/目标命令统一字段与时限 | 统一 DeviceCommand / PICK_AND_PUT 包络及每条命令稳定身份 | O6/O9：business_key 最终要求、正式 timeout 与回调最长延迟；示例30000不是生产默认值 |
| 目标臂参数 | WMS rack_layer/rack_column 已确认为 int；业务目标 rack/face/slot 冻结在 ACCEPT | O10/O11/O15：ECS 层列编码、rack_side/rack_face 映射和 location_type 最终要求；不从字符串示例反推 |
| 成功位置与报告 | 匹配原 PUT 的确定成功才保存位置；RECORDED/DUPLICATE 后关闭 | 报告 PkgID 权威来源/关联仍缺双方合同，不解析 barcode、不用 Pickup UUID 代替；显式成功/PkgID 读口可先验业务事务 |
| 目标架 ROTATE | 目标就绪与 PUT/report 业务接续可离线验证 | [现场搬运基线](manual-outbound-rack-transport.md)记录 CK04，现行 typed RACK_ROTATE 仅支持 CTU02；需独立合同对齐后才能真实派发，不能将来源架 CTU02 默认为目标架 CK04。F01 进出场不受此项阻塞 |
| 直接取料与异常 | 当前正常 BIN_CELL 链不依赖这些分支 | O12/O13/O14/O17 只限制直接取料；O5/O7/O8 只限制空取、失败/不可读码等异常动作，保留对象/原身份，不替代正常确认 |

C1/C2 继续只限制 drain 容量接续与回库核销。首批期限只限制准备后未到首批计划的路径，不作为已到工作位正常链的新门禁。

### 10.1 已确认设备接线与隔离验收

`application/picking_device_ports.py` 提供来源事实、当前目标/REPLACE 原架、PUT 成功事实读口和 `PickingArmCommandMapper`。来源准入读取本 Work 原 SCAN2 的归一化料箱编码、设备/工作线/时间和已发布 Evidence；可靠取出要求原来源 Command 的 `SUCCESS + PUT_FINISHED + actual_qty` 严格整数 `1`，结果已由 FactProcessor 发布。当前目标沿原进场轮次、已发布结果、未释放窗口和权威当前位置核对，复用既有换架读口的因果检查；第二盘晚于旧架 ACK 加入时，由原换架 owner 从相同目标架/架面、尚未闭合的换架义务恢复唯一原轮次，不查询 latest，不要求旧架重新在位。目标已权威就绪时读取现有 ReadyTarget，不重做换架；ACK、计划目标、未知位置均不构成 ReadyTarget。

公共映射固定 SOURCE_ARM/TARGET_ARM、PICK_AND_PUT、原 Pickup/MaterialExecution/步骤身份，使用当前 WorkLine 冻结绑定的合同与命令时限。请求工厂在同一数据库事务内异步读取，完整 `source/target` 正文由必填参数端口提供，未提供则不创建命令、不消费来源授权。这里没有实现 O4/O10/O11/O15/O16 尚未冻结的设备编码，也没有使用示例坐标、30000 或扫码样例作为生产默认值。

真实设备事件入口不提供本地 `command_code` 元数据。精确语义关联显式给定后，插件核对原 Work/Pickup/Command，再将该关联与 MaterialExecution 同事务保存，不改写原始报文或其摘要。来源结果先由 DeviceEvidenceService 应用，再由插件 business consumer 发布；PUT 结果在 FactProcessor 内读取确定成功，位置确认、movement_report 义务和发布仍同事务。设备摘要复用宿主 UNIFORM_WIRE 规则；设备 Fact 的 `1.1` 与既有 WMS Fact 的 `1.0` 由相应静态 handler 明确消费，不更改 Evidence 的版本或身份。

隔离装配的主要验收 owner 是 `tests/integration/test_picking_device_ports_postgresql.py`：调用真实 DeviceEvidenceService 与 FactProcessor，以明确提供的参数/扫码关联/PkgID 跑通普通目标取盘→扫码→ACCEPT→PUT→报告→关闭，并验证扫码绑定回滚、未发布来源结果、非整数单盘数量、未知/错误架面、缺参数/PkgID、并发唯一命令、重建/重放及 REPLACE 原架/轮次发现。旧换架、WAIT、报告关闭和历史清理矩阵继续引用既有测试，不另建一套。测试将“已下发但尚未 ACK”显式作为前提，不执行发送，也不宣称物理设备成功。

恢复 owner 不变：Work/source 与 target/batch 推进当前业务；DeviceCommand/Transport 持有原动作；DeviceEvidenceService 应用设备 Evidence，FactProcessor 应用并发布业务事实，Confirmation 持有已冻结请求/报告。精确扫码关联、权威 PkgID 和完整参数仍是实际接入门禁；C1/C2 只限制对应 drain 容量路径，CK04 只限制目标 ROTATE 派发。本轮不改 deployment、不启动 worker 或真实派发、不发送 WMS 请求。
