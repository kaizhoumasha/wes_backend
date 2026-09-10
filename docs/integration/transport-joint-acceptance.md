# Transport 自动联调联合验收

## 1. 目的与边界

本文用于验收 Transport diagnostics 中“指定货架、按货架面选择料箱并自动完成整轮”的能力。它覆盖 WES 代码、WMS Transport
合同、ECS/WES `SCAN12` Evidence 和现场 RCS 物理动作之间的证据交接，但不允许用代码测试、Mock、部署健康检查替代现场物理或
业务验收。

自动轮次的固定顺序为：

```text
CTU01 货架搬出
  → 当前面 1..4 个料箱到 CNV0301
  → SCAN12 为当前面已到达料箱提供有效扫码 Evidence（不等待整组扫描齐全）
  → WMS 按 SCAN12 FIFO 分配目标，料箱分批从 CNV0302 回到冻结分配 slot
  → 若还有下一面，CTU02 旋转后重复当前面流程
  → 所有选中料箱回架后，CTU03 返回 WH05
```

操作员输入的面值是区分大小写的不透明字符串，例如 `"90"`、`"270"`；WES、WMS 和 RCS 必须原样保存与下发，不做角度映射。

## 2. 分层验收状态

| 层级 | 本轮证据 | 当前边界 |
| --- | --- | --- |
| 代码/合同 | 单面、多面、重复 Evidence、未知结果、重启恢复、全局单活动轮次和活动货架独占均已有自动化验收资产 | 聚焦或集成测试通过只证明对应代码快照 |
| WMS Mock | Mock 可接受 `CTU01 RACK→RACK_POSITION`、`CTU02 RACK`、`CTU03 RACK→ZONE`，终态仍显式返回精确 `RACK_POSITION` | Mock 不证明真实 WMS/RCS 接纳、执行或回调 |
| 部署 | 待 release evidence、镜像 digest、OCI source revision 和迁移结果一致后记录 | `/health`、进程存活或 Swagger 可访问不证明业务链路 |
| `SCAN12` 现场 schema | 暂按 `device_code=SCAN12`、`event_type=SCAN_COMPLETED`、`data.barcode=<料箱编码>` 验收 | 联调时必须确认真实 ECS payload、时间戳、`source_event_id` 和 apply status |
| RCS/WMS/ECS 物理闭环 | 操作员已确认本轮直接录入进入真实 WMS/RCS，`SCAN12` 驱动料箱回架并触发最终 `CTU03` | 本轮确认不替代逐消息原始 payload、统一时间窗和现场记录归档 |
| 业务验收 | 待操作员确认选架、选箱、WMS 分配 slot 回架及最终返库均符合业务预期 | 只有现场业务 owner 可以签署 |

## 3. 前置条件

1. 后端迁移已执行，API、Celery worker 和 beat 使用同一已批准版本。
2. WMS、RCS、ECS 的时钟和事件身份可追溯；禁止手工改写数据库制造成功终态。
3. 操作员已确认现场可用，并核对货架、每面 1～4 个料箱及当前 slot；页面自动携带 `sorting-3` 作为 WMS 归属标识，不要求工作线启用。该身份须已登记，货架和料箱仍直接录入，不依赖其 WES 资源基础数据或挂载投影。
4. 系统不存在另一个 `RUNNING` 或 `NEEDS_ATTENTION` 的 Transport 自动联调轮次。
5. 已准备 WMS callback、ECS Evidence、RCS 任务和现场视频/照片或操作记录的统一时间窗口。

### 升级至 WMS 分配回架前的检查

升级前先停止页面接续，并通过 `GET /api/v1/transport/debug-runs` 核对没有 `RUNNING` 或 `NEEDS_ATTENTION` 的旧轮次。
已有轮次应在原版本中完成至可信 `CTU03` 返库终态；若原轮次需要人工处置，必须先核对关联 Transport 的权威终态和物理位置，
再按原有物理核验流程关闭。页面关闭、HTTP ACK 或 Mock 成功都不能替代这项检查。

新轮次自动携带固定 `workline_code`，回架使用正式 `outbound.bin.return_batch@v1` 的 WMS 分配槽位。联调可靠义务按轮次冻结的完整请求校验归属，不以正式工作线的启用状态作为准入。
旧活动轮次没有冻结的工作线或回架分配时，新版本将其置为 `NEEDS_ATTENTION / DEBUG_RUN_CONFIGURATION_UPGRADE_REQUIRED`，
保留原 task、请求身份、Evidence 和资源围栏，不推测 WMS 分配、不补写身份、不自动恢复后续步骤。已有 Transport 仍按原身份接收结果；
人工处理必须先核对原执行结果。有未知 WMS 分配或未闭合 Transport 时不能用本地 abort 释放围栏。

## 4. 合同核对

### 4.1 货架搬出

```json
{
  "rack_id": "510056",
  "source": {"kind": "RACK", "location_code": "510056"},
  "target": {"kind": "RACK_POSITION", "location_code": "KT16"},
  "target_face": "90",
  "rcs_template_id": "CTU01"
}
```

WMS 成功结果必须返回 `final_position={"kind":"RACK_POSITION","location_code":"KT16"}`，且 `arrival_face` 与本组面值完全相等。

### 4.2 下一面旋转

```json
{
  "rack_id": "510056",
  "position": {"kind": "RACK", "location_code": "510056"},
  "target_face": "270",
  "rcs_template_id": "CTU02",
  "kind": "RACK_ROTATE"
}
```

WMS wire 的 `source` 使用 `RACK` 引用；`target` 使用准入时确认的货架原点位 `RACK_POSITION`（本例 `KT16`），不得将货架编号作为目标点位。成功结果返回该精确 `RACK_POSITION` 和原样 `arrival_face`。

### 4.3 最终返库

```json
{
  "rack_id": "510056",
  "source": {"kind": "RACK", "location_code": "510056"},
  "target": {"kind": "ZONE", "location_code": "WH05"},
  "rcs_template_id": "CTU03",
  "kind": "RACK_MOVE"
}
```

WES 对 `CTU03` 省略 `target_face`，由 RCS 自主确定返库朝向。WMS 必须显式返回其在 `WH05` 内解析出的精确
`RACK_POSITION`（例如测试数据 `WH05-01`）；`arrival_face` 可省略或为 `null`，此时 WES 保存空朝向，不沿用旧值。
提供非空实际值时原样记录；WES 不从 `ZONE` 猜测最终地码，也不按请求目标面比较。

## 5. 必验场景

### 5.1 单面

1. 输入一个面及 1～4 个现场料箱与当前 slot，记录页面预览和创建响应中的 `run_id`；无需填写工作线编码。
2. 核对只创建一个 `CTU01`，面值与输入完全一致。
3. `CTU01` 精确成功后，核对一个 `BIN_MOVE` 把本组全部料箱送到 `CNV0301`。
4. 在首个选中料箱的有效 `SCAN12` Evidence 到达前，确认不存在回架 task；形成有效扫码 FIFO 后允许分批回架。
5. 已有选中料箱的有效扫码 Evidence 后，即可申请回架，不等待本面全部料箱扫描齐全。核对正式 `outbound.bin.return_batch@v1` 请求按实际扫码 FIFO 排序。每个 `READY` 前缀创建一个 `BIN_MOVE`，从 `CNV0302` 返回 WMS 分配的精确 slot；部分批次完成后才为剩余 FIFO 申请下一批；当前 FIFO 已回完但仍有未扫描料箱时，回到扫码等待并保留本面最初的取箱证据边界，不重发已回架料箱。本面全部料箱确认回架后才允许转面或整架返库。自动联调收到有效 `NO_BATCH` 后，按请求中的实际扫码 FIFO，将剩余料箱退回同组已成功出库任务记录的原货架、原朝向和原 slot；冻结批次保留原 WMS operation identity，并标记 `DEBUG_NO_BATCH_ORIGINAL_SLOTS` 及出库任务 ID。原出库记录缺失或货架/朝向不匹配时停止并提示 `DEBUG_RETURN_SOURCE_MISSING`。此规则仅用于自动联调，正式工作线仍遵循 WMS 分配。
6. 当前面所有批次的成员均精确成功前，确认不转面；本轮全部选中箱均精确成功前，确认不存在 `CTU03`。核对响应 `returned_bins` 为实际确认槽位，下一轮以这些槽位为来源。
7. 核对最终只创建一个省略 `target_face` 的 `CTU03`；WMS 返回精确库位且成功结果校验通过后轮次才进入
   `COMPLETED`。分别核对省略 `arrival_face`、传 `null` 和提供实际非空值的结果均按合同接受；前两者清空朝向投影，后者原样记录。

### 5.2 两面

按顺序选择 `"90"`、`"270"` 两组：

```text
CTU01("90")
→ 第一组去 CNV0301 / SCAN12 / WMS 分配 slot 回架
→ CTU02("270")
→ 第二组去 CNV0301 / SCAN12 / WMS 分配 slot 回架
→ CTU03（省略 target_face）
```

必须确认只生成一次 `CTU02`，且第二组全部成员回架成功前没有 `CTU03`。

### 5.3 幂等、异常与恢复

- 同一料箱重复扫码只计一次；旧于步骤 high-watermark 或 `not_before` 的扫码不得推进。
- 非选中料箱、其它设备和其它事件类型不得推进当前面。
- `InboundEvidence.apply_status=PENDING|RECONCILING`、无效 barcode 或身份冲突必须进入 `NEEDS_ATTENTION`，不得创建回架 task。
- Transport `RECONCILING`/`DELIVERY_UNKNOWN`、`position_unknown=true` 或精确位置不一致时不得创建后继 task；
  携带目标面的请求仍须校验面值，省略目标面的 `CTU03` 允许省略 `arrival_face` 或传 `null`，但不放宽精确位置及其它终态校验。
- 对 `DELIVERY_UNKNOWN` 只能等待同一个 `transport_task_id` 的权威终态；不得生成新 `client_request_id` 重发。
- worker 或 API 重启后使用持久化 step、`client_request_id` 和 `transport_task_id` 恢复，不得重复创建物理任务。
- 第二个全局活动轮次必须被拒绝。
- 活动轮次冻结的 `rack_id` 不得被其它 Transport 创建入口使用；其它货架不应被误拦截。
- abort 只允许在现场已确认物理静止、关联 Transport 全部确定终态且无活动资源绑定时执行。

## 6. 证据记录

每次联调至少保存：

- Git revision、release evidence、后端镜像 digest、迁移 revision；
- `run_id`、每步 ordinal/phase/status、固定配置和全部 `client_request_id`/`transport_task_id`；
- WMS submit/ACK/callback 的 `operation_id`、时间戳和原始 body digest；
- `SCAN12` Evidence id、`source_event_id`、设备时间戳、barcode、apply status；
- RCS 对应任务号、实际位置和面向证据；
- 操作员确认的工作线、货架、料箱、初始及 WMS 分配 slot、异常处理和最终业务结论。

验收结论必须分别写为“代码/Mock”“已部署”“物理闭环”“业务验收”，禁止合并成一个“已完成”。

### 6.1 中断恢复证据的分工

| 证据 | 测试/验收所有者 | 不覆盖的结论 |
| --- | --- | --- |
| 原身份、持久 Evidence、期限、资源绑定与单调发布 | Transport 基础测试；真实 worker 中断窗口位于 `tests/e2e/test_execution_interruption_recovery.py` | 不证明调试轮次或正式插件的业务推进 |
| 重启读取原 step/task，仅创建合法下一步；迟到与重复结果 | `tests/runtime/transport/test_transport_debug_run_advancement.py`、`tests/integration/transport/test_transport_debug_run_recovery.py` | 不证明供应商动作已经完成，不覆盖正式插件规则 |
| 供应商终态与实际位置/面向 | WMS/ECS 联合验收，记录原 operation identity 与外部物理证据 | 不可用本地 HTTP stub 或数据库状态代替 |
| FIFO、NG、任务切换及最终业务放行 | 对应插件包测试和现场业务负责人 | 不纳入基础查询测试的通过结论 |

2026-09-09 稳定性实施已完成原联调消费者的 FAST 和独占 PostgreSQL 恢复验证；
基础中断测试使用独占 broker、worker 与 HTTP stub，尚未部署或完成供应商/正式业务验收。
现场操作按[执行恢复手册](../devops/execution-recovery.md)复核，不能从“结果已发布”直接推断“业务已推进”。
