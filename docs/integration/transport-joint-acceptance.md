# Transport 自动联调联合验收

## 1. 目的与边界

本文用于验收 Transport diagnostics 中“指定货架、按货架面选择料箱并自动完成整轮”的能力。它覆盖 WES 代码、WMS Transport
合同、ECS/WES `SCAN12` Evidence 和现场 RCS 物理动作之间的证据交接，但不允许用代码测试、Mock、部署健康检查替代现场物理或
业务验收。

自动轮次的固定顺序为：

```text
CTU01 货架搬出
  → 当前面 1..4 个料箱到 CNV0301
  → SCAN12 为当前面全部选中料箱提供扫码 Evidence
  → 全部料箱从 CNV0302 回到冻结原 slot
  → 若还有下一面，CTU02 旋转后重复当前面流程
  → 所有选中料箱回架后，CTU03 返回 WH01
```

操作员输入的面值是区分大小写的不透明字符串，例如 `"90"`、`"270"`；WES、WMS 和 RCS 必须原样保存与下发，不做角度映射。

## 2. 分层验收状态

| 层级 | 本轮证据 | 当前边界 |
| --- | --- | --- |
| 代码/合同 | 单面、多面、重复 Evidence、未知结果、重启恢复和全局单活动轮次均已有自动化验收资产 | 聚焦或集成测试通过只证明对应代码快照 |
| WMS Mock | Mock 可接受 `CTU01 RACK→RACK_POSITION`、`CTU02 RACK`、`CTU03 RACK→ZONE`，终态仍显式返回精确 `RACK_POSITION` | Mock 不证明真实 WMS/RCS 接纳、执行或回调 |
| 部署 | 待 release evidence、镜像 digest、OCI source revision 和迁移结果一致后记录 | `/health`、进程存活或 Swagger 可访问不证明业务链路 |
| `SCAN12` 现场 schema | 暂按 `device_code=SCAN12`、`event_type=SCAN_COMPLETED`、`data.barcode=<料箱编码>` 验收 | 联调时必须确认真实 ECS payload、时间戳、`source_event_id` 和 apply status |
| RCS/WMS/ECS 物理闭环 | 待现场逐动作核对 | HTTP ACK、Mock 成功、任务数据库 `SUCCEEDED` 均不能单独替代物理事实 |
| 业务验收 | 待操作员确认选架、选箱、原 slot 回架及最终返库均符合业务预期 | 只有现场业务 owner 可以签署 |

## 3. 前置条件

1. 后端迁移已执行，API、Celery worker 和 beat 使用同一已批准版本。
2. WMS、RCS、ECS 的时钟和事件身份可追溯；禁止手工改写数据库制造成功终态。
3. 操作员已按现场实物核对货架、每面 1～4 个料箱及原 slot；页面直接录入这些值，不依赖 WES 资源基础数据或挂载投影。
4. 系统不存在另一个 `RUNNING` 或 `NEEDS_ATTENTION` 的 Transport 自动联调轮次。
5. 已准备 WMS callback、ECS Evidence、RCS 任务和现场视频/照片或操作记录的统一时间窗口。

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

WMS wire 的 `source`、`target` 均为同一个 `RACK` 引用；成功结果仍必须返回 `KT16` 的精确 `RACK_POSITION` 和原样 `arrival_face`。

### 4.3 最终返库

```json
{
  "rack_id": "510056",
  "source": {"kind": "RACK", "location_code": "510056"},
  "target": {"kind": "ZONE", "location_code": "WH01"},
  "target_face": "90",
  "rcs_template_id": "CTU03",
  "kind": "RACK_MOVE"
}
```

WMS 必须显式返回其在 `WH01` 内解析出的精确 `RACK_POSITION`，例如测试数据 `WH01-01`。WES 不从 `ZONE` 猜测最终地码。

## 5. 必验场景

### 5.1 单面

1. 直接录入一个面及 1～4 个现场料箱与原 slot，记录页面预览和创建响应中的 `run_id`。
2. 核对只创建一个 `CTU01`，面值与输入完全一致。
3. `CTU01` 精确成功后，核对一个 `BIN_MOVE` 把本组全部料箱送到 `CNV0301`。
4. 在最后一个选中料箱的 `SCAN12` Evidence 到达前，确认不存在回架 task。
5. 全部选中料箱均被扫描后，核对一个 `BIN_MOVE` 从 `CNV0302` 返回冻结原 slot。
6. 回架 task 的每个成员均精确成功前，确认不存在 `CTU03`。
7. 核对最终只创建一个 `CTU03`；WMS 返回精确库位和 `arrival_face="90"` 后轮次才进入 `COMPLETED`。

### 5.2 两面

按顺序选择 `"90"`、`"270"` 两组：

```text
CTU01("90")
→ 第一组去 CNV0301 / SCAN12 / 原 slot 回架
→ CTU02("270")
→ 第二组去 CNV0301 / SCAN12 / 原 slot 回架
→ CTU03("90")
```

必须确认只生成一次 `CTU02`，且第二组全部成员回架成功前没有 `CTU03`。

### 5.3 幂等、异常与恢复

- 同一料箱重复扫码只计一次；旧于步骤 high-watermark 或 `not_before` 的扫码不得推进。
- 非选中料箱、其它设备和其它事件类型不得推进当前面。
- `InboundEvidence.apply_status=PENDING|RECONCILING`、无效 barcode 或身份冲突必须进入 `NEEDS_ATTENTION`，不得创建回架 task。
- Transport `RECONCILING`/`DELIVERY_UNKNOWN`、`position_unknown=true`、面值或精确位置不一致时不得创建后继 task。
- 对 `DELIVERY_UNKNOWN` 只能等待同一个 `transport_task_id` 的权威终态；不得生成新 `client_request_id` 重发。
- worker 或 API 重启后使用持久化 step、`client_request_id` 和 `transport_task_id` 恢复，不得重复创建物理任务。
- 第二个全局活动轮次必须被拒绝。
- abort 只允许在现场已确认物理静止、关联 Transport 全部确定终态且无活动资源绑定时执行。

## 6. 证据记录

每次联调至少保存：

- Git revision、release evidence、后端镜像 digest、迁移 revision；
- `run_id`、每步 ordinal/phase/status、固定配置和全部 `client_request_id`/`transport_task_id`；
- WMS submit/ACK/callback 的 `operation_id`、时间戳和原始 body digest；
- `SCAN12` Evidence id、`source_event_id`、设备时间戳、barcode、apply status；
- RCS 对应任务号、实际位置和面向证据；
- 操作员确认的货架、料箱、原 slot、异常处理和最终业务结论。

验收结论必须分别写为“代码/Mock”“已部署”“物理闭环”“业务验收”，禁止合并成一个“已完成”。
