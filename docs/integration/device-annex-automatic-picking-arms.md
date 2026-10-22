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
本文未决项仍是**草案**；硬件方回复的《SMT分拣机ECS接口调用说明书》V1.0.0（2026-03-18，仓库原文见 related）及最新用户确认按 §10 实施，不重复冻结。箱型直接使用2、3、6编码，货架面统一为 `rack_face`，机械臂分工见下表。原文顶层包络不覆盖统一白皮书；扫码精确关联、实际位置绑定及退料取出对象不得由示例推断。

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
| `SOURCE_ARM` | `ARM01` | 从滚筒线工作位料箱取出物料，放上扫码平台并触发 `SCAN_COMPLETED` | `BIN`（SCAN2 工作位）→ `SCAN_PLATFORM` |
| 退料路径角色待对象确认 | `ARM02` | 从退料货架取出后放上扫码平台并触发 `SCAN_COMPLETED`；整箱还是单盘尚未明确，不实现设备路径 | 位置类型及字段待确认，不套用正常链 `SOURCE_ARM` |
| `TARGET_ARM` | `ARM02` | 从扫码平台放到目标储位 | `SCAN_PLATFORM` → `FIVE_LAYER_RACK` |

WES 的业务角色“转运货架”，在 ECS 侧的 `location_type` 就是 `FIVE_LAYER_RACK`（现场确认）。
来源五层货架的料箱由 CTU 和滚筒线搬运，机械臂不直接操作，两者只是同一物理货架类型，业务角色不同。

`location_id`（如 `STATION_WORK1`、`STATION_SCAN`、`STATION_A/B`）取自 WorkLine 位置绑定，不写死在业务代码里。

## 4. 命令：`task_type = PICK_AND_PUT`

外层字段固定为白皮书 §3.1 的七个：`device_code`、`command_code`、`task_type`、`priority=1`、`timeout`（整数毫秒）、`timestamp`（整数毫秒）、`params`。

### 4.1 `params` 公共字段

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `business_key` | 不要求 | 硬件说明书未列此字段；WES 本地 Pickup/MaterialExecution 仍负责业务关联，设备动作沿原 `command_code` 追溯，不以额外回显作为正常命令前置条件 |
| `source` | 是 | 起点位置对象，见 4.2 |
| `target` | 是 | 终点位置对象，见 4.2 |

### 4.2 位置对象（`source` / `target`）

字段名沿用供应商原文，不改名。逻辑字段由 WMS 授权，WES 只转换、不计算。

| `location_type` | 必填字段 | 逻辑来源 |
| --- | --- | --- |
| `BIN` | `location_id`、`location_type`、`bin_type`、`bin_cell_location`、`reel_layer`、`reel_thickness`、`reel_totalthickness`、`reel_diameter` | `cell_index` 原定位号转字符串1–7，不能使用编码 `cell_id`；WMS `bin_type` int 2/3/6 直接转设备字符串 `"2"`/`"3"`/`"6"`，不查箱型映射 |
| 退料货架（类型未定） | 说明书 `ONE_LAYER_RACK` 的 `bin_*` 是否适用，须先明确 ARM02 取出整箱还是单盘，见 O12、O13 | 不把 `added_direct_picks[].source_locator` 自动解释为整箱定位；对象和字段来源未闭合前不创建命令 |
| `SCAN_PLATFORM` | `location_id`、`location_type` | WorkLine 位置绑定 |
| `FIVE_LAYER_RACK` | `location_id`、`location_type`、`rack_id`、`rack_face`、`rack_layer`、`rack_column`、`reel_thickness`、`reel_diameter` | 层/列转字符串，层为1–5；尺寸使用本盘原来源命令冻结值；`rack_face` 沿 WMS 授权原样传递，不转 A/B、不裁剪或转换角度 |

供应商原文还要求 `bin_type`、`reel_layer`、`reel_thickness`、`reel_totalthickness`、`reel_diameter`。
**2026-09-21 更新**：`reel_totalthickness` 不需要 WMS 提供，WES 本地按 `reel_thickness × reel_layer` 计算。
其余四个字段（`bin_type`、`reel_layer`、`reel_thickness`、`reel_diameter`）由 WMS 已接受的
`work_plan.READY` 或 `next_source_action` 提供，见[出库主合同 §9.3](../contracts/wms-outbound-picking-task-integration-requirements.md)。位置及料盘参数在设备侧均为字符串；厚度/总厚度为毫米，直径按寸（说明书列出7/13/15寸，但未证明是封闭枚举，不新增 WMS 枚举）。十进制总厚度不引入浮点尾差，不做额外取整、裁剪或单位换算。箱型和货架面按上述已确认规则传递；实际位置不填默认值。

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
    "source": {
      "location_id": "STATION_WORK1", "location_type": "BIN", "bin_type": "3",
      "bin_cell_location": "1", "reel_layer": "10", "reel_thickness": "20",
      "reel_totalthickness": "200", "reel_diameter": "15"
    },
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
    "source": {"location_id": "STATION_SCAN", "location_type": "SCAN_PLATFORM"},
    "target": {
      "location_id": "STATION_A", "location_type": "FIVE_LAYER_RACK",
      "rack_id": "RACK_01", "rack_face": "A", "rack_layer": "1", "rack_column": "1",
      "reel_thickness": "20", "reel_diameter": "15"
    }
  }
}
```

退料货架→扫码平台已确认由 ARM02 执行并触发扫码，但取出整箱还是单盘、位置类型及完整字段仍未明确；撤下原占位报文，不提供可执行示例，不影响上述 ARM01→ARM02 正常出库链。

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
| `device_code` | 是 | 放料并触发扫码的机械臂；正常来源为 ARM01，退料路径为 ARM02（取出对象仍待确认） |
| `event_type` | 是 | 固定 `SCAN_COMPLETED` |
| `timestamp` | 是 | 整数毫秒 |
| `data.location` | 是 | 固定为扫码平台 `location_id`（如 `STATION_SCAN`） |
| 精确来源关联字段（名称待定） | 待确认 | 必须可靠指向把本盘放上平台的原 `command_code`；`data.source_command_code` 仅为讨论候选，不是已冻结字段 |
| `data.barcode` | 是 | 单个原始字符串，ECS 不拆分，WES 原样保存并转发（O3 已确定，见下方） |

完整事件样例（2026-09-21 联调实际收到的报文，`device_code` 为 `SCANNER_001`——该历史样例不代表最新确认的机械臂上报身份；本轮正常链使用 ARM01。样例没有精确来源 Command 引用，关联字段仍待确认）：

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
| O1（已明确） | ARM01 正常取料后触发扫码；ARM02 退料取出后触发扫码，取出对象仍待确认；正常扫码必须与原来源 Command 的设备一致 | 用户最新确认 |
| O2 | 事件能否带 `source_command_code` 或提供等价精确关联？未确认前保留原 Evidence，不按平台单盘、时间或最近命令推断 | ECS 供应商 |
| ~~O3~~ | 已确定：WES 不解析，原样转发 `barcode` 给 WMS；`material.decide` 请求字段需要合同变更，见 §6 更新 | 已关闭 |
| O4 | 字段、字符串格式及毫米/寸单位已有说明书依据，插件映射已接入；箱型已确认直接为2/3/6，设备侧按原合同转字符串；仅 WMS 尺寸单位对齐仍按对应输入核对，不重问字段结构 | WMS + ECS 供应商 |
| O5 | 设备能否明确区分“Cell 无料（空取）”与“取料失败”？ | ECS 供应商 |
| O6（不再作门禁） | 说明书未要求 `business_key`；正常命令不生成此扩展，追溯使用原 `command_code` 与本地执行身份 | 已收窄 |
| O7 | `error_detail.code` 取值表及原厂码对应关系 | ECS 供应商 |
| O8 | 读不出码或不完整时，ECS 发什么事件、是否重扫、何时放弃 | ECS 供应商 |
| O9 | `PICK_AND_PUT` 的合理 `timeout`、结果回调最长延迟 | ECS 供应商 |
| O10 | 说明书明确层/列为字符串、层1–5；插件按此转换，不猜列数上限，不修改 WMS int DTO | 已明确 |
| O11（已明确） | 统一使用 `rack_face`，原样传递授权值；删除 `rack_side` 和 A/B 转换前提，不做双字段兼容 | 用户最新确认 |
| O12 | ARM02 已确认负责退料取出；需明确整箱还是单盘及相应 `location_type`，不能把旧单盘设计当成整箱合同 | ECS 供应商 + WMS |
| O13 | 退料货架取出对象确定后的字段：`bin_*` 是否适用、`slot_id` 如何映射到 ECS 字段（字段名和 O10 不同，但可以是同一种做法：WMS 直接给出 ECS 需要的字段，不用 WES 反推） | ECS 供应商 + WMS |
| O14 | 直接取料的“空取”信号，是否与 O5 同一机制 | ECS 供应商 |
| O15 | 按说明书 §8.1，ARM02 目标必须包含 `location_type=FIVE_LAYER_RACK` 及 `rack_id`，不照抄漏字段样例 | 已明确 |
| O16 | `cell_index` 表示同一料箱内定位号，设备 `bin_cell_location` 为其字符串1–7；不拆解业务 `cell_id` | 已明确 |
| O17 | 退料路径 ARM02 的测量字段（`bin_type`、`reel_layer`、`reel_thickness`、`reel_diameter`，如果 O13 确认 `ONE_LAYER_RACK` 需要）目前没有任何 wire 来源：`plan_delta.added_direct_picks[]` 只给 `source_locator`，直接取料没有 `work_plan` 等价物。如果确实需要，建议同 O4/变更 1 的解法，扩展 `added_direct_picks[]` 每项带上这些字段 | WMS |


## 10. 正常业务装配与真实接口门禁（2026-10-22 核对）

本轮将料箱到工作位后的 BIN_CELL 正常链统一装配：Work READY→来源 Command→精确扫码绑定→material.decide（含确定 WAIT）→普通/ROTATE/REPLACE 目标就绪→PUT→movement_report→RECORDED/DUPLICATE 关闭→下一来源或点2正常流出/SCAN3。已确认的设备事实读口与完整参数映射已实现；箱型直接使用2/3/6、货架面统一为 `rack_face`；实际位置绑定及精确扫码关联由调用方显式提供；报告 barcode 直接沿本盘扫码 Evidence；`build_picking_plugin` 不注册到生产 deployment，不发送 WMS 请求或启用设备派发。离线输入不能当作下表未决项已确认。

REPLACE 已接入首次 `departure_decide`、持久当前引用与确定 WAIT 到期续问，再沿原 F01 旧架离场/新架进场和共用 PUT/report/close 继续，不重新请求物料资格。首次输入只需精确旧架、原进场轮次及到位事实；当前请求、轮次和到期点保存在 Pickup，恢复不按“最新请求”猜身份。所有搬运和机械臂成功事实仍必须匹配并已发布；未知交付不创建等价新动作。这证明显式权威输入下的离线业务闭环，不代表下表接口门禁已解除。

| 依赖路径 | 已确认基线 | 真正缺少的接口证据 / 阻塞范围 |
| --- | --- | --- |
| Cell 取盘参数 | WMS 数字 DTO 不变；设备单元格/层数/尺寸转字符串，总厚度按十进制厚度×层数计算；说明书明确单元格1–7、毫米/寸和全部必填字段 | 箱型直接使用2/3/6，不另配映射；仅 WMS 尺寸单位对齐及实际位置绑定；已明确字段不再等待 O4/O16 整体冻结 |
| 单盘扫码关联 | barcode 原样转发，str 最长1000；本地 Pickup/原来源 Command 精确关联 | ARM01 上报身份已确认；仅来源 Command 精确关联字段待确认；现有样例缺 source_command_code，不能猜“最近一条命令” |
| 来源/目标命令统一字段与时限 | 统一 DeviceCommand / PICK_AND_PUT 包络及每条命令稳定身份，不生成未要求的 business_key | O9 正式 timeout 与回调最长延迟按实际绑定落实；示例30000不是生产默认值，不阻塞离线映射 |
| 目标臂参数 | ACCEPT 的目标层列转字符串，层1–5；必带 FIVE_LAYER_RACK、rack_id 和本盘尺寸，字段结构已确定 | rack_face 原样传递，取消 A/B 映射；仅实际目标位置绑定，不重问层列类型或 location_type |
| 成功位置与报告 | 匹配原 PUT 的确定成功才保存位置；RECORDED/DUPLICATE 后关闭 | 统一使用本盘扫码 Evidence 的 barcode 原文（1–1000字符），核对已发布 decide 与冻结报告；独立物料编号读口和来源待确认项已撤销，不新增报告身份门禁 |
| 目标架 ROTATE | 目标就绪与 PUT/report 业务接续可离线验证 | [现场搬运基线](manual-outbound-rack-transport.md)记录 CK04，现行 typed RACK_ROTATE 仅支持 CTU02；需独立合同对齐后才能真实派发，不能将来源架 CTU02 默认为目标架 CK04。F01 进出场不受此项阻塞 |
| 直接取料与异常 | 当前正常 BIN_CELL 链不依赖这些分支 | O12/O13/O14/O17 只限制直接取料；O5/O7/O8 只限制空取、失败/不可读码等异常动作，保留对象/原身份，不替代正常确认 |

退箱补架容量接续与回库分配核销仍待 WMS 补证，仅限制相应 drain 路径。首批期限只限制准备后未到首批计划的路径，不作为已到工作位正常链的新门禁。

### 10.1 已确认设备接线与隔离验收

`PickingMaterialScanReader` 已直接读取归一化 `SCAN_COMPLETED` 的原样 `data.barcode`、`data.location` 和时间，校验 Evidence 身份/摘要、本 Work 的原 Pickup/来源 Command，以及冻结命令目标平台。来源 Command 尚未下发、精确关联尚未取得或工作线暂停时，原 Evidence 保持未发布，由现有 FactProcessor 的 DEFERRED 重扫恢复；关联和单盘身份提交后，直接读取 Evidence 已存关联，不再依赖外部关联端口。扫码触发臂分工已确认，读口校验事件设备与原来源 Command 一致；精确关联字段仍未冻结：该端口必须显式提供精确原命令，不能把讨论中的 `source_command_code` 当成已确认 wire 字段，不能按条码、时间或最近命令关联。本次不新增扫码设备角色、业务状态或 timer。

本次隔离装配替换原语义扫码替身，使用上述真实读口；新增验收只覆盖缺关联保留、暂停恢复、并发绑定和清空外部关联后的重建防重。主要 PostgreSQL owner 仍为 `tests/integration/test_picking_device_ports_postgresql.py`；FAST owner `tests/test_picking_device_ports.py` 负责原样条码边界、错误平台/身份和未下发命令，不复制 9H、租约或真实进程矩阵。测试平台和位置参数均为显式离线输入，不能作为设备坐标/数值格式冻结证据。

`application/picking_device_ports.py` 提供来源事实、当前目标/REPLACE 原架、PUT 成功事实读口和 `PickingArmCommandMapper`。来源准入读取本 Work 原 SCAN2 的归一化料箱编码、设备/工作线/时间和已发布 Evidence；可靠取出要求原来源 Command 的 `SUCCESS + PUT_FINISHED + actual_qty` 严格整数 `1`，结果已由 FactProcessor 发布。当前目标沿原进场轮次、已发布结果、未释放窗口和权威当前位置核对，复用既有换架读口的因果检查；第二盘晚于旧架 ACK 加入时，由原换架 owner 从相同目标架/架面、尚未闭合的换架义务恢复唯一原轮次，不查询 latest，不要求旧架重新在位。目标已权威就绪时读取现有 ReadyTarget，不重做换架；ACK、计划目标、未知位置均不构成 ReadyTarget。

公共映射固定 SOURCE_ARM/TARGET_ARM、PICK_AND_PUT、原 Pickup/MaterialExecution/步骤身份，使用当前 WorkLine 冻结绑定的合同与命令时限。`PickingArmCommandMapper` 在同一事务读取显式 `PickingArmBinding`，按说明书构造完整 `params.source/target`；绑定仅提供工作位、扫码平台和目标位，不新增生产配置默认或箱型/面向映射表。来源缺工作位/平台时不创建来源命令、不消费授权；目标缺实际位置时只不创建 PUT。箱型编码直接转字符串，rack_face 原样传递。PUT 尺寸沿本盘原来源 Command 冻结正文读取，不能读取已推进的 Work 后继参数；平台不一致或原 Pickup/Command/单盘身份错误则拒绝。普通/ROTATE/REPLACE 共用原 PUT/report/close 与恢复 owner，已创建 Command 仍优先原身份恢复，不重映射或改写正文。

`build_picking_plugin` 显式接收同一个 `arm_commands` 映射器，将来源和 PUT 接入正常链；不增加生产 deployment 注册。图中 STATION_WORK1/STATION_SCAN2/PIPELINE_WORK1 以及结果示例 ARM_01 的命名差异只由实际绑定解决，不硬编码、自动别名或选最新配置。扫码端口仍要求精确原命令，原文示例未提供该字段，不据此解除精确关联字段门禁。

真实设备事件入口不提供本地 `command_code` 元数据。精确语义关联显式给定后，插件核对原 Work/Pickup/Command，再将该关联与 MaterialExecution 同事务保存，不改写原始报文或其摘要。来源结果先由 DeviceEvidenceService 应用，再由插件 business consumer 发布；PUT 结果在 FactProcessor 内读取确定成功，位置确认、movement_report 义务和发布仍同事务。设备摘要复用宿主 UNIFORM_WIRE 规则；设备 Fact 的 `1.1` 与既有 WMS Fact 的 `1.0` 由相应静态 handler 明确消费，不更改 Evidence 的版本或身份。

隔离装配的主要验收 owner 是 `tests/integration/test_picking_device_ports_postgresql.py`：调用真实 DeviceEvidenceService 与 FactProcessor，以明确提供的参数/扫码关联 跑通普通目标取盘→扫码→ACCEPT→PUT→报告→关闭，并验证扫码绑定回滚、未发布来源结果、非整数单盘数量、未知/错误架面、缺参数、并发唯一命令、重建/重放及 REPLACE 原架/轮次发现。旧换架、WAIT、报告关闭和历史清理矩阵继续引用既有测试，不另建一套。测试将“已下发但尚未 ACK”显式作为前提，不执行发送，也不宣称物理设备成功。

恢复 owner 不变：Work/source 与 target/batch 推进当前业务；DeviceCommand/Transport 持有原动作；DeviceEvidenceService 应用设备 Evidence，FactProcessor 应用并发布业务事实，Confirmation 持有已冻结请求/报告。精确扫码关联和完整参数仍是实际接入门禁；退箱补架容量接续与回库分配核销只限制对应 drain 路径，CK04 只限制目标 ROTATE 派发。本轮不改 deployment、不启动 worker 或真实派发、不发送 WMS 请求。
