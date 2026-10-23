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
本文未决项仍是**草案**；硬件方回复的《SMT分拣机ECS接口调用说明书》V1.0.0（2026-03-18，仓库原文见 related）及最新用户确认按 §10 实施，不重复冻结。箱型直接使用2、3、6编码，货架面统一为 `rack_face`，机械臂分工见下表。原文顶层包络不覆盖统一白皮书；2026-10-23 最新基线要求可靠的取盘与扫码归属，不限定 SCAN_COMPLETED 回显 command_code；ARM02 从退料架取出单盘已确认。取盘结果携带 barcode 仅为待设备确认方案，字段、时序/补报保证和实际定位参数不得由示例推断。

## 2. 现场事实（2026-09-21 读取 `GET http://10.24.209.28:8080/api/v1/device/status`）

| `device_code` | `device_type` | `role` | `supported_commands` | `supported_events` |
| --- | --- | --- | --- | --- |
| `ARM01` | `ROBOTIC_ARM` | `SORTER_ARM` | `PICK_AND_PUT` | `SCAN_COMPLETED`、`ESTOP_PRESSED` |
| `ARM02` | `ROBOTIC_ARM` | `SORTER_ARM` | `PICK_AND_PUT` | `SCAN_COMPLETED`、`ESTOP_PRESSED` |

- 历史状态里没有独立扫码平台设备；最新分工已确认由放料机械臂触发扫码，正常来源 ARM01、退料单盘 ARM02，不再询问扫码设备身份。
- `ESTOP_PRESSED` 不得发送到 WES 事件回调（白皮书 §4.2），由 ECS 独立处理。

## 3. 设备分工

| WES 业务角色 | `device_code` | 动作 | 起点 → 终点（ECS `location_type`） |
| --- | --- | --- | --- |
| `SOURCE_ARM` | `ARM01` | 从滚筒线工作位料箱取出物料，放上扫码平台并触发 `SCAN_COMPLETED` | `BIN`（SCAN2 工作位）→ `SCAN_PLATFORM` |
| 退料单盘来源（复用 ARM02 实际绑定） | `ARM02` | 从退料货架精确单盘储位取出物料，放上扫码平台并触发 `SCAN_COMPLETED` | 单盘物料→`SCAN_PLATFORM`；起点设备位置类型及字段待确认，不切换正常链 `SOURCE_ARM` 绑定 |
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
| 退料货架单盘来源（设备类型未定） | ARM02 的抓取对象已明确为单盘；需设备给出其 `location_type` 和完整必填定位字段，不能照抄 `ONE_LAYER_RACK` 的 `bin_*` | 沿 `added_direct_picks[].source_locator` 的 `RACK_SLOT` 单盘语义；已知 rack_id/rack_face/slot_id，不反推层列或 bin 参数，设备参数未闭合不创建取盘命令 |
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

退料货架→扫码平台已确认由 ARM02 取出单盘并触发扫码，与现有 `RACK_SLOT` 单盘合同一致，不增加整箱分支或 WMS 字段。设备位置类型及完整字段仍未明确，不提供占位执行报文，不影响上述 ARM01→ARM02 正常出库链。

上述正常 BIN_CELL 链依次使用 ARM01 来源取盘、ARM02 目标放置；可靠取出及扫码决定门禁沿既有实现。退料单盘来源与目标放置均由 ARM02 执行，不能套用“所有物料都先 ARM01”的顺序，也不能因此重写正常链绑定。

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
| `device_code` | 是 | 放料并触发扫码的机械臂；正常来源为 ARM01，退料单盘路径为 ARM02 |
| `event_type` | 是 | 固定 `SCAN_COMPLETED` |
| `timestamp` | 是 | 整数毫秒 |
| `data.location` | 是 | 固定为扫码平台 `location_id`（如 `STATION_SCAN`） |
| 取盘与扫码归属证据（承载方式待定） | 接入待确认 | 必须唯一指向把本盘放上平台的原取盘；不要求本事件增加 command_code。原命令回显、其他任务关联或结果携带条码均须先有设备合同依据，不预设字段 |
| `data.barcode` | 是 | 单个原始字符串，ECS 不拆分，WES 原样保存并转发（O3 已确定，见下方） |

完整事件样例（2026-09-21 联调实际收到的报文，`device_code` 为 `SCANNER_001`——该历史样例不代表最新确认的机械臂上报身份；本轮正常链使用 ARM01。样例没有取盘归属证据，不能从中推定关联；不要求仅靠本事件新增字段解决）：

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
| O1（已明确） | ARM01 正常取料、ARM02 退料单盘取料后分别触发扫码；扫码事件须与原来源 Command 的设备一致 | 用户确认 |
| O2（可靠归属待确认） | 请提供原取盘请求及对应的完整结果/扫码报文，说明条码如何唯一归属于本次取盘，并给出延迟、重复、重启补报和新旧轮次隔离保证。无需固定在 SCAN_COMPLETED 回显 command_code；取盘结果携带 barcode 仅为待确认方案，详见 §10.2 | ECS 供应商 |
| ~~O3~~ | 已确定：WES 不解析，原样转发 `barcode` 给 WMS；`material.decide` 请求字段需要合同变更，见 §6 更新 | 已关闭 |
| O4 | 字段、字符串格式及毫米/寸单位已有说明书依据，插件映射已接入；箱型已确认直接为2/3/6，设备侧按原合同转字符串；仅 WMS 尺寸单位对齐仍按对应输入核对，不重问字段结构 | WMS + ECS 供应商 |
| O5 | 设备能否明确区分“Cell 无料（空取）”与“取料失败”？ | ECS 供应商 |
| O6（不再作门禁） | 说明书未要求 `business_key`；正常命令不生成此扩展，追溯使用原 `command_code` 与本地执行身份 | 已收窄 |
| O7 | `error_detail.code` 取值表及原厂码对应关系 | ECS 供应商 |
| O8 | 读不出码或不完整时，ECS 发什么事件、是否重扫、何时放弃 | ECS 供应商 |
| O9 | 命令时限复用既有配置与 START 冻结（基础默认30000毫秒）；实际节拍和回调延迟在联调校准，超时不改写原物理结果，不重复索取整套配置 | 联调校准 |
| O10 | 说明书明确层/列为字符串、层1–5；插件按此转换，不猜列数上限，不修改 WMS int DTO | 已明确 |
| O11（已明确） | 统一使用 `rack_face`，原样传递授权值；删除 `rack_side` 和 A/B 转换前提，不做双字段兼容 | 用户最新确认 |
| O12（对象已明确） | ARM02 从退料架取出单盘物料；与 WMS `RACK_SLOT` 一致，不再确认整箱/单盘。仅设备来源 `location_type` 及定位正文待确认 | ECS 供应商 |
| O13 | 请提供 ARM02 从单盘储位到扫码台的完整 `params.source/target`；逐项说明已知 `rack_id/rack_face/slot_id` 如何对应设备字段及字段来源。设备若使用额外坐标，给明确对应或权威输入，不由 WES 拆解 slot_id | ECS 供应商；仅确有额外 WMS 输入时再与 WMS 对齐 |
| O14 | 直接取料的“空取”信号，是否与 O5 同一机制 | ECS 供应商 |
| O15 | 按说明书 §8.1，ARM02 目标必须包含 `location_type=FIVE_LAYER_RACK` 及 `rack_id`，不照抄漏字段样例 | 已明确 |
| O16 | `cell_index` 表示同一料箱内定位号，设备 `bin_cell_location` 为其字符串1–7；不拆解业务 `cell_id` | 已明确 |
| O17 | 先由设备说明单盘取料是否确需尺寸或其他非定位字段及其权威来源；现有 `added_direct_picks[]` 只提供来源定位，不从 BIN work_plan 借参数、不预先新增 WMS 字段。确有缺口再一次性对齐所需字段 | ECS 供应商；必要时 WMS |


## 10. 正常业务装配与真实接口门禁（2026-10-22 核对）

2026-10-23 生产接入准备补充：机械臂位置沿既有工作线位置配置入口，不新增环境变量或设备 wire 字段。`WORK_BIN`（BIN）和 `SCAN_PLATFORM`（SCAN_PLATFORM）声明为两个可选 STATION 插槽，实际工作位及执行位置编码必须显式配置；`TRANSFER_RACK` 继续复用原转运架位置。通用 START 校验本线工作位并冻结执行位置，`PickingArmBindingReader` 只读取匹配工作线、精确插件版本且已启用的当前快照，不从设备编码、INLET、扫码文本或历史推导。缺项保持未知，类型错误或位置冲突拒绝准入；已创建原 Command 的恢复先于新参数读取。

Web/worker 继续共用 `build_deployment_runtime`。只有调用方显式注入内部精确关联读口 `automatic_picking_source_commands`，才会在同一个原搬运插件上安装完整正常链，并将同一实例交给 FactProcessor、宿主持久扫描、完成驱动和结果发布。该参数不是设备回显字段或生产开关；现有 Web/worker 生产调用均未注入，仍为搬运装配。未提供目标旋转 reader 时只阻止新旋转动作，保留原旋转 flow 的原动作结果与就绪恢复，不以假 reader 或 CTU02 默认值替代 CK04；普通目标和 F01 换架保持可接续。退料真实定位适配和扫码归属读口仍待设备输入；首批等待已接入原完成 owner，只有显式配置期限才冻结到期点，未配置不猜值。内部关联读口仍无设备实现。

本轮将料箱到工作位后的 BIN_CELL 正常链统一装配：Work READY→来源 Command→精确扫码绑定→material.decide（含确定 WAIT）→普通/ROTATE/REPLACE 目标就绪→PUT→movement_report→RECORDED/DUPLICATE 关闭→下一来源或点2正常流出/SCAN3。已确认的设备事实读口与完整参数映射已实现；箱型直接使用2/3/6、货架面统一为 `rack_face`；实际位置绑定及精确扫码关联由调用方显式提供；报告 barcode 直接沿本盘扫码 Evidence；生产调用尚未注入精确归属依赖，仍未启用完整拣料装配，不发送 WMS 请求或启用设备派发。离线输入不能当作下表未决项已确认。

REPLACE 已接入首次 `departure_decide`、持久当前引用与确定 WAIT 到期续问，再沿原 F01 旧架离场/新架进场和共用 PUT/report/close 继续，不重新请求物料资格。首次输入只需精确旧架、原进场轮次及到位事实；当前请求、轮次和到期点保存在 Pickup，恢复不按“最新请求”猜身份。所有搬运和机械臂成功事实仍必须匹配并已发布；未知交付不创建等价新动作。这证明显式权威输入下的离线业务闭环，不代表下表接口门禁已解除。

| 依赖路径 | 已确认基线 | 真正缺少的接口证据 / 阻塞范围 |
| --- | --- | --- |
| Cell 取盘参数 | WMS 数字 DTO 不变；设备单元格/层数/尺寸转字符串，总厚度按十进制厚度×层数计算；说明书明确单元格1–7、毫米/寸和全部必填字段 | 箱型直接使用2/3/6，不另配映射；仅 WMS 尺寸单位对齐及实际位置绑定；已明确字段不再等待 O4/O16 整体冻结 |
| 单盘扫码关联 | barcode 原样转发，str 最长1000；本地 Pickup/原来源 Command 精确关联 | ARM01 上报身份已确认；可靠取盘归属的承载报文、字段、时序/补报保证待确认，不限定 SCAN 回显；事件去重不等于物料归属，不能猜“最近一条命令” |
| 来源/目标命令统一字段与时限 | 统一 DeviceCommand / PICK_AND_PUT 包络及每条命令稳定身份，不生成未要求的 business_key | O9 正式 timeout 与回调最长延迟按实际绑定落实；示例30000不是生产默认值，不阻塞离线映射 |
| 目标臂参数 | ACCEPT 的目标层列转字符串，层1–5；必带 FIVE_LAYER_RACK、rack_id 和本盘尺寸，字段结构已确定 | rack_face 原样传递，取消 A/B 映射；仅实际目标位置绑定，不重问层列类型或 location_type |
| 成功位置与报告 | 匹配原 PUT 的确定成功才保存位置；RECORDED/DUPLICATE 后关闭 | 统一使用本盘扫码 Evidence 的 barcode 原文（1–1000字符），核对已发布 decide 与冻结报告；独立物料编号读口和来源待确认项已撤销，不新增报告身份门禁 |
| 目标架 ROTATE | 目标就绪与 PUT/report 业务接续可离线验证 | [现场搬运基线](manual-outbound-rack-transport.md)记录 CK04，现行 typed RACK_ROTATE 仅支持 CTU02；需独立合同对齐后才能真实派发，不能将来源架 CTU02 默认为目标架 CK04。F01 进出场不受此项阻塞 |
| 直接取料与异常 | 当前正常 BIN_CELL 链不依赖这些分支 | O12/O13/O14/O17 只限制直接取料；O5/O7/O8 只限制空取、失败/不可读码等异常动作，保留对象/原身份，不替代正常确认 |

退箱补架容量接续与回库分配核销仍待 WMS 补证，仅限制相应 drain 路径。首批期限只限制准备后未到首批计划的路径，不作为已到工作位正常链的新门禁。

### 10.1 已确认设备接线与隔离验收

`PickingMaterialScanReader` 已直接读取归一化 `SCAN_COMPLETED` 的原样 `data.barcode`、`data.location` 和时间，校验 Evidence 身份/摘要、本 Work 或退料成员的原 Pickup/来源 Command，以及冻结命令目标平台。来源 Command 尚未下发、精确关联尚未取得或工作线暂停时，原 Evidence 保持未发布，由现有 FactProcessor 的 DEFERRED 重扫恢复；关联和单盘身份提交后，直接读取 Evidence 已存关联，不再依赖外部关联端口。扫码触发臂分工已确认，当前事件读口校验设备与原来源 Command 一致；内部端口仍须提供精确归属，但不限定外部必须在 SCAN 事件回显该命令。`source_command_code` 未成为 wire 合同，不按条码、时间或最近命令关联；当前读口仅处理 DEVICE_EVENT，若最终选择结果携带条码，须按冻结合同补读取/准入适配，不能称其已支持任意结果报文。本次不新增扫码设备角色、业务状态或 timer。

本次隔离装配替换原语义扫码替身，使用上述真实读口；新增验收只覆盖缺关联保留、暂停恢复、并发绑定和清空外部关联后的重建防重。主要 PostgreSQL owner 仍为 `tests/integration/test_picking_device_ports_postgresql.py`；FAST owner `tests/test_picking_device_ports.py` 负责原样条码边界、错误平台/身份和未下发命令，不复制 9H、租约或真实进程矩阵。测试平台和位置参数均为显式离线输入，不能作为设备坐标/数值格式冻结证据。

`application/picking_device_ports.py` 提供来源事实、当前目标/REPLACE 原架、PUT 成功事实读口和 `PickingArmCommandMapper`。来源准入读取本 Work 原 SCAN2 的归一化料箱编码、设备/工作线/时间和已发布 Evidence；可靠取出要求原来源 Command 的 `SUCCESS + PUT_FINISHED + actual_qty` 严格整数 `1`，结果已由 FactProcessor 发布。当前目标沿原进场轮次、已发布结果、未释放窗口和权威当前位置核对，复用既有换架读口的因果检查；第二盘晚于旧架 ACK 加入时，由原换架 owner 从相同目标架/架面、尚未闭合的换架义务恢复唯一原轮次，不查询 latest，不要求旧架重新在位。目标已权威就绪时读取现有 ReadyTarget，不重做换架；ACK、计划目标、未知位置均不构成 ReadyTarget。

公共映射固定 SOURCE_ARM/TARGET_ARM、PICK_AND_PUT、原 Pickup/MaterialExecution/步骤身份，使用当前 WorkLine 冻结绑定的合同与命令时限。`PickingArmCommandMapper` 在同一事务读取显式 `PickingArmBinding`，按说明书构造完整 `params.source/target`；绑定仅提供工作位、扫码平台和目标位，不新增生产配置默认或箱型/面向映射表。来源缺工作位/平台时不创建来源命令、不消费授权；目标缺实际位置时只不创建 PUT。箱型编码直接转字符串，rack_face 原样传递。PUT 尺寸沿本盘原来源 Command 冻结正文读取，不能读取已推进的 Work 后继参数；平台不一致或原 Pickup/Command/单盘身份错误则拒绝。普通/ROTATE/REPLACE 共用原 PUT/report/close 与恢复 owner，已创建 Command 仍优先原身份恢复，不重映射或改写正文。

`build_picking_plugin` 显式接收同一个 `arm_commands` 映射器，将来源和 PUT 接入正常链；不增加生产 deployment 注册。图中 STATION_WORK1/STATION_SCAN2/PIPELINE_WORK1 以及结果示例 ARM_01 的命名差异只由实际绑定解决，不硬编码、自动别名或选最新配置。内部扫码端口仍要求明确原取盘归属；原文示例未提供归属证据，不据此解除真实接入门禁，也不把事件增加命令字段作为唯一方案。

真实设备事件入口不提供本地 `command_code` 元数据。精确语义关联显式给定后，插件核对原 Work/Pickup/Command，再将该关联与 MaterialExecution 同事务保存，不改写原始报文或其摘要。来源结果先由 DeviceEvidenceService 应用，再由插件 business consumer 发布；PUT 结果在 FactProcessor 内读取确定成功，位置确认、movement_report 义务和发布仍同事务。设备摘要复用宿主 UNIFORM_WIRE 规则；设备 Fact 的 `1.1` 与既有 WMS Fact 的 `1.0` 由相应静态 handler 明确消费，不更改 Evidence 的版本或身份。

隔离装配的主要验收 owner 是 `tests/integration/test_picking_device_ports_postgresql.py`：调用真实 DeviceEvidenceService 与 FactProcessor，以明确提供的参数/扫码关联 跑通普通目标取盘→扫码→ACCEPT→PUT→报告→关闭，并验证扫码绑定回滚、未发布来源结果、非整数单盘数量、未知/错误架面、缺参数、并发唯一命令、重建/重放及 REPLACE 原架/轮次发现。旧换架、WAIT、报告关闭和历史清理矩阵继续引用既有测试，不另建一套。测试将“已下发但尚未 ACK”显式作为前提，不执行发送，也不宣称物理设备成功。

恢复 owner 不变：Work/source 与 target/batch 推进当前业务；DeviceCommand/Transport 持有原动作；DeviceEvidenceService 应用设备 Evidence，FactProcessor 应用并发布业务事实，Confirmation 持有已冻结请求/报告。精确扫码关联和完整参数仍是实际接入门禁；退箱补架容量接续与回库分配核销只限制对应 drain 路径，CK04 只限制目标 ROTATE 派发。本轮不改 deployment、不启动 worker 或真实派发、不发送 WMS 请求。


### 10.2 可靠取盘与扫码归属、退料单盘的接线边界（2026-10-23）

**硬件原文证据与已确认边界：**[说明书 §5.1](../hardware/SMT分拣机ECS接口调用说明书V1-20260318.md#51-任务结果回传report-result) 的任务结果有原 command_code，data 只定义 actual_qty、pick_and_put_result；§5.2 的独立扫码事件定义 location、barcode 和事件时间，未定义原任务关联或扫码轮次。配套 TaskResultData/EventData 模型一致。这里的“未定义”不等于已验证实际设备一定不返回额外字段。状态查询 current_command_code 只表示查询时的活动命令；[统一白皮书 §4.2](third_party_integration_whitepaper.md#42-上报设备事件event-callback) 的核心内容去重只识别同一事件重报，不证明其取盘归属，诊断扩展也不能直接提升为业务身份。timestamp 没有已批准的跨重启顺序保证。

**唯一业务要求：**每份完整扫码证据必须可靠、唯一地关联原 Pickup/取盘动作，延迟、重复和重启补报不能绑定到后续物料。扫码信息仍为本盘 barcode 原文及原扫码时间；已有精确关联一旦持久绑定便沿原 Evidence 恢复，不重写原始报文/摘要。SCAN_COMPLETED 不强制回显 command_code，也不固定替代字段或承载渠道。没有归属证据时保留原 Evidence、沿既有延期/对账处理；“当前唯一等待扫码者”、扫码台单盘容量、HTTP 顺序、当前活动命令和 barcode 均不能单独成为关联依据。

**待设备确认方案，不是已实施合同：**可先核实能否在已有带原 command_code 的取盘结果中，一并提供本盘 barcode 与原扫码时间。字段名称/路径、类型、完整报文、是否具备首次结果回传前取得扫码的能力，均由设备回复后冻结；本附录不新增结果字段、类型默认或示例 JSON。该方案须在首次结果回传时形成并冻结同一正文，保留真实物理完成时间与结果；扫码未完成/失败时的结果及补报方式也须说明，不得把“没有条码”擅自改成物理失败。如果扫码晚于原结果，不能以同一 command_code 改写已确认成功回调，必须提供独立且有可靠归属的扫码事实。设备已有其他稳定任务/轮次关联亦可评估；若主张仅靠状态和时序关联，必须先给跨轮次、迟到、重复和重启隔离依据，不能由 WES 自行假定。

**设备确认清单：**

1. 请提供同一盘的原取盘请求、完整首次结果回调及完整扫码报文（若所选方案仍单独发送事件），逐项说明 barcode、原扫码时间与原取盘的对应关系。明确实际承载报文、字段名/层级/类型、时间语义，以及重复、乱序、迟到、重启补报和无法恢复归属时的处理；不要只回复“单盘平台”或字段片段。
2. 如选择结果携带条码，回复首次回传前是否已具备完整扫码、同命令重报是否正文不变，以及扫码晚于原结果时的独立归属方式。不支持可直接说明，不能把本方案当成强制接口。
3. 请提供 ARM02 从退料单盘储位到扫码平台的完整 PICK_AND_PUT 参数，说明 rack_id/rack_face/slot_id 如何对应设备定位，确需额外尺寸时注明权威来源。不重问退料取单盘、两臂分工、箱型2/3/6、rack_face 或 barcode 原文规则。

**装配与恢复范围：**保留既有物料入口、单一精确归属端口及 Pickup/MaterialExecution，不增加另一套物料状态机、平台资源锁、通用关联模型或 timer。设备方案冻结后只补其静态事实读取/归属/准入适配差异，继续由 FactProcessor 应用、延期与发布，Confirmation 负责 decide/报告可靠交付，原 Command 持有物理动作；不在 HTTP 基础接收层解释工作线归属。当前 provider 仅有隔离测试显式关联，不能宣称真实设备接入已完成，也不能预先将未知报文适配为现有事件 Fact。

正常 BIN_CELL 的当前业务仍由 Work/source、target/batch 恢复。退料单盘来源已确认由 ARM02 执行，与既有 RACK_SLOT 一致；DirectPick 的当前成员业务 driver/扫码上下文已在显式离线装配中接通，复用原单盘 decide/目标准备/PUT/报告关闭及恢复 owner，不伪造 Work、不消费 BIN_CELL 授权。退料取盘请求和来源事实仍须显式提供；生产 BIN_CELL 参数映射不作为退料定位方案，设备归属字段/保证和退料定位未冻结前不启用真实路径。可靠归属接入与退料定位分别限制各自真实路径，不阻塞给定权威事实下的离线业务接续。主合同 §10.3 的双臂并发不变，不增加扫码台锁、平台释放事件或等待整盘报告才下发下一来源的统一门槛；CK04 只限制目标 ROTATE，退箱补架容量接续与回库分配核销只限制对应 drain。
