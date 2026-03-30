# SMT Classifier Hardware Gap Analysis

**最后更新**: 2026-03-28

本文档分析当前仓库中的 `smt_classifier` 运行时实现，与真实硬件协议文档 [SMT粗分机接口调用说明书20260321-v1.md](/Users/kaizhou/SynologyDrive/works/wes_backend/docs/hardware/SMT粗分机接口调用说明书20260321-v1.md) 之间的偏差。

对比基线：

- 真实硬件协议文档: [SMT粗分机接口调用说明书20260321-v1.md](/Users/kaizhou/SynologyDrive/works/wes_backend/docs/hardware/SMT粗分机接口调用说明书20260321-v1.md)
- 当前插件实现: [plugin.py](/Users/kaizhou/SynologyDrive/works/wes_backend/src/workline_plugins/smt_classifier/plugin.py)
- Celery 编排/派发实现: [workline.py](/Users/kaizhou/SynologyDrive/works/wes_backend/src/celery_app/tasks/workline.py)
- 回调入口: [callback.py](/Users/kaizhou/SynologyDrive/works/wes_backend/src/app/callback/v1/callback.py)
- Mock 机械臂: [arm_mock.py](/Users/kaizhou/SynologyDrive/works/wes_backend/tests/mock/smt_classifier/arm_mock.py)
- Mock 流水线: [pipeline_mock.py](/Users/kaizhou/SynologyDrive/works/wes_backend/tests/mock/smt_classifier/pipeline_mock.py)

## 1. 结论摘要

当前 `smt_classifier` 在“控制流建模”上与真实硬件业务意图基本接近，但在“协议细节”上存在多处高风险偏差。
如果直接对接 `SMT粗分机接口调用说明书20260321-v1` 所描述的真实设备，当前实现大概率无法直接联调成功。

最高风险偏差有 5 类：

1. 命令请求体结构不兼容
2. 命令/设备标识字段命名不兼容
3. 扫码与检测结果的数据来源建模不兼容
4. 位置模型不兼容
5. 错误回调字段不兼容

## 2. 对比范围

本次偏差分析覆盖以下维度：

- 设备职责与业务流程
- 命令接口
- 结果回调接口
- 事件推送接口
- 位置与料箱模型
- 错误码与失败处理
- Mock 与真实硬件协议的一致性

## 3. 可对齐部分

以下内容当前实现与硬件文档方向一致：

| 维度 | 真实硬件 | 当前实现 | 结论 |
|------|----------|----------|------|
| 设备角色 | `INPUT_ARM / CONVEYOR / OUTPUT_ARM` | 插件中同名角色 | 一致 |
| 大流程 | 扫码 NG、扫码 OK、移料、出料 | 插件中同样建模 | 方向一致 |
| 扫码事件来源 | `ARM01 / ARM03` 上报 `SCAN_COMPLETED` | 当前运行时集成测试也按 `INPUT_ARM` 处理 | 一致 |
| 流水线移动语义 | `MOVE_FORWARD` | 插件中也使用 `MOVE_FORWARD` 语义 | 方向一致 |
| 结果回调机制 | 设备执行完成后回调 WES | `callback_result()` 统一接收 | 一致 |

这些对齐项说明：当前系统的“业务阶段划分”不是问题，问题主要出在“协议映射层”。

## 4. 高风险偏差

## 4.1 命令接口路径和请求体不兼容

### 真实硬件要求

真实硬件命令接口定义为：

- `POST /api/v1/command`

命令体核心字段为：

- `device_id`
- `command_id`
- `timestamp`
- `task_type`
- `priority`
- `timeout`
- `source`
- `target`

其中 `source` 和 `target` 是对象，不是扁平参数。

### 当前实现

当前派发代码向设备发送：

- `POST /api/v1/device/command`

发送体核心字段来自 `WorklineOutbox.payload_json`：

- `command_code`
- `task_type`
- `priority`
- `timeout`
- `params`

对应位置：

- 派发 URL: [workline.py](/Users/kaizhou/SynologyDrive/works/wes_backend/src/celery_app/tasks/workline.py#L1014)
- 派发 payload 构造: [workline.py](/Users/kaizhou/SynologyDrive/works/wes_backend/src/celery_app/tasks/workline.py#L192)

### 偏差结论

这是直接协议不兼容：

- 路径不一致
- `command_id` 被替换成了 `command_code`
- `device_id` 不在 body 中
- 缺失 `timestamp`
- `source/target` 结构被简化成了 `params`

### 影响

若接真实硬件：

- 设备端很可能直接返回 404 或 400
- 即使路径兼容，请求体验证也会失败

### 建议

引入明确的“硬件适配层”，把内部 `DeviceCommand` 映射成真实硬件所要求的命令协议：

```json
{
  "device_id": "ARM01",
  "command_id": "CMD-20260327-0001",
  "timestamp": "1710000000000",
  "task_type": "PICK_AND_PUT",
  "priority": 1,
  "timeout": 30000,
  "source": {...},
  "target": {...}
}
```

## 4.2 `task_type` 映射不兼容

### 真实硬件要求

真实硬件支持的任务类型包括：

- `PICK_AND_PUT`
- `SCAN`
- `TEST`
- `MOVE_FORWARD`
- `MOVE_BACKWARD`
- `MOVE_LEFT`
- `MOVE_RIGHT`

### 当前实现

当前实现需要区分“内部建模”和“对外派发”两层：

- `PICK_AND_PUT -> PICK_AND_PLACE`
- `MOVE_FORWARD -> PROCESS`

其中：

- `DeviceCommand.task_type` 和 `command_code` 仍沿用内部映射值
- 但对外发往设备的 `Outbox.payload_json.task_type` 已优先使用 `params.action`，即保持 `PICK_AND_PUT`、`MOVE_FORWARD`

对应逻辑见 [workline.py](/Users/kaizhou/SynologyDrive/works/wes_backend/src/celery_app/tasks/workline.py#L177) 和 [workline.py](/Users/kaizhou/SynologyDrive/works/wes_backend/src/celery_app/tasks/workline.py#L193)。

### 偏差结论

内部抽象本身仍然存在，但“对外派发给设备”这一步已经按真实硬件要求保留原始任务枚举值。

### 影响

若直接把内部值原样发给真实硬件：

- `PICK_AND_PLACE`
- `PROCESS`

真实硬件大概率无法识别。

### 建议

当前分层更合理的口径应是：

- 内部允许继续保留 `DeviceCommand.task_type` 的抽象映射
- 对外派发时必须保持供应商协议枚举原样
- 若后续继续演进，建议显式区分“内部任务类型”和“供应商任务类型”，避免文档和实现再次混淆

## 4.3 位置模型不兼容

### 真实硬件要求

硬件文档要求命令显式传入：

- `source.location_id`
- `source.location_type`
- `target.location_id`
- `target.location_type`

对于出料命令，还需要：

- `rack_id`
- `bin_id`
- `bin_type`
- `bin_cell_location`
- `reel_layer`
- `reel_thickness`
- `reel_diameter`
- `reel_totalthickness`

### 当前实现

当前插件只生成逻辑位类型：

- `source_type`
- `target_type`

如：

- `INPUT_PLATFORM`
- `NG_PLATFORM`
- `PIPELINE_PLATFORM`
- `OUTPUT_PLATFORM`

对应位置：

- 扫码 OK 命令: [plugin.py](/Users/kaizhou/SynologyDrive/works/wes_backend/src/workline_plugins/smt_classifier/plugin.py#L581)
- NG 命令: [plugin.py](/Users/kaizhou/SynologyDrive/works/wes_backend/src/workline_plugins/smt_classifier/plugin.py#L672)
- 出料命令: [plugin.py](/Users/kaizhou/SynologyDrive/works/wes_backend/src/workline_plugins/smt_classifier/plugin.py#L802)

### 偏差结论

当前插件表达的是“逻辑位置类型”，而真实硬件需要“可执行的物理位置对象”。

尤其是最终出料：

- 当前插件已经将目标位类型修正为 `BIN`
- 但仍未补齐真实硬件要求的 `bin_id / bin_type / bin_cell_location / reel_*`

因此，`location_type` 这一层语义已经基本对齐，但真实落箱参数仍不完整。

### 影响

当前实现无法告诉真实设备：

- 具体该去哪个 `STATION_PIPELINE1_INPUT1`
- 去哪个 `STATION_NG_PLATFORM1`
- 放到哪个 `BIN_104`
- 放到料箱哪个格位

### 建议

需要引入真实位置映射配置，例如：

```yaml
INPUT_ARM:
  left_default_input: STATION_INPUT1
  left_ng_positions:
    - STATION_NG_PLATFORM1
    - STATION_NG_PLATFORM2
CONVEYOR:
  left_input_positions:
    - STATION_PIPELINE1_INPUT1
    - STATION_PIPELINE1_INPUT2
  left_output_position: STATION_PIPELINE1_OUTPUT1
```

并在命令派发前将逻辑位置转换成真实 `source/target` 对象。

## 4.4 扫码事件载荷模型不兼容

### 真实硬件要求

扫码完成事件示例中，上报内容为：

- `device_id`
- `event_type = SCAN_COMPLETED`
- `data.location`
- `data.barcode1 ~ data.barcode6`

硬件文档附录流程写法是：

1. 设备上报扫码事件
2. WES 判断条码 OK/NG

这意味着：

- 条码判定逻辑主要在 WES
- 设备上报的是扫码结果原始信息

### 当前实现

当前插件在 `SCAN_COMPLETED` 事件里直接读取：

- `data.result` 或 `scan_result`
- `data.barcode` 或 `barcode`

对应代码： [plugin.py](/Users/kaizhou/SynologyDrive/works/wes_backend/src/workline_plugins/smt_classifier/plugin.py#L542)

### 偏差结论

当前插件假设“设备已经告诉我 OK/NG”，而真实硬件文档更像是“设备只上报扫码信息，WES 自己判断 OK/NG”。

同时当前插件只支持单个条码字段：

- `barcode`

而真实硬件协议支持：

- `barcode1 ~ barcode6`

### 影响

若接真实硬件：

- 插件可能拿不到 `scan_result`
- 插件也拿不到单字段 `barcode`
- 多条码物料信息会丢失

### 建议

需要在 `callback_event()` 或更早的适配层中做协议归一：

- `barcode1~barcode6 -> barcode_list`
- 在 WES 内部做扫码判定
- 再把统一后的内部 `DEVICE_EVENT Inbox` 交给插件

换句话说，真实硬件事件不能直接作为当前插件输入。

## 4.5 检测/测厚流程建模不兼容

### 真实硬件要求

真实硬件文档描述的 OK 主流程是：

1. 设备上报扫码事件
2. WES 判断条码 OK
3. WES 下发进料 OK 命令：串杆位置 -> 流水线进料位置
4. 设备回传任务结果，携带条码、尺寸、厚度信息
5. WES 下发移料命令：流水线进料位置 -> 流水线出料位置
6. 设备回传移料结果
7. WES 下发出料命令：流水线出料位置 -> 料箱

这里最关键的是：

- 尺寸检测和测厚结果，是在进料机械臂执行完“进料 OK 命令”的结果回传里带回来的
- 不是单独的 `INSPECTION_COMPLETED` 事件

### 当前实现

当前插件显式要求第二个事件：

- `INSPECTION_COMPLETED`

代码见 [plugin.py](/Users/kaizhou/SynologyDrive/works/wes_backend/src/workline_plugins/smt_classifier/plugin.py#L601)。

### 偏差结论

这是当前实现与真实硬件文档之间最大的业务建模偏差之一。

真实硬件是：

- “扫码事件 + 进料命令结果回传中带检测数据”

当前插件是：

- “扫码事件 + 独立检测事件”

### 影响

若接真实硬件：

- 插件永远等不到 `INSPECTION_COMPLETED`
- OK 流程无法继续到 `MOVE_FORWARD`

### 建议

优先级最高的修正方向有两个：

1. 修改插件，使“进料 OK 命令的 `COMMAND_RESULT`”承载检测/测厚判断
2. 如果必须保留现有插件结构，则在回调适配层把进料 OK 回调拆解成一条内部 `INSPECTION_COMPLETED Inbox`

其中第 1 种更贴近真实硬件协议。

## 4.6 结果回调字段命名不兼容

### 真实硬件要求

结果回调体使用：

- `command_id`
- `device_id`
- `error_detail.error_code`
- `error_detail.error_message`

### 当前实现

当前回调入口和 mock 使用：

- `command_code`
- `device_code`
- `error_detail.code`
- `error_detail.message` 或 `msg`

对应位置：

- 回调 schema: [command.py](/Users/kaizhou/SynologyDrive/works/wes_backend/src/app/device/models/command.py#L145)
- 插件失败处理读取字段: [plugin.py](/Users/kaizhou/SynologyDrive/works/wes_backend/src/workline_plugins/smt_classifier/plugin.py#L832)

### 偏差结论

即使真实硬件回调成功进入接口层，当前错误处理链路仍可能提取不到错误码和错误消息。

### 影响

失败时会出现：

- `error_code` 被当成空
- `error_message` 被当成默认值
- 自动重试策略无法准确匹配

### 建议

在 `callback_result()` 入口增加协议归一化：

- `command_id -> command_code`
- `device_id -> device_code`
- `error_detail.error_code -> error_detail.code`
- `error_detail.error_message -> error_detail.message`

## 4.7 Inspection NG 被误判为硬件失败

### 真实硬件要求

真实文档里，“尺寸检测 / 测厚 NG”属于业务判定结果，不一定是设备故障。

示例里表现为：

- `result = FAILED`
- `error_detail.error_code = 1001`
- `error_message = 料盘尺寸检测异常`

### 当前实现

当前插件中，任何 `COMMAND_RESULT.result != SUCCESS` 都走 `_handle_command_failure()`，会被视为命令失败或硬件故障。

对应代码： [plugin.py](/Users/kaizhou/SynologyDrive/works/wes_backend/src/workline_plugins/smt_classifier/plugin.py#L746)

### 偏差结论

真实硬件中的“业务 NG”与当前插件中的“设备失败”混在一起了。

### 影响

会导致两类错误：

- 本应走 NG 分流的物料，被标成硬件错误
- 自动重试策略错误介入

### 建议

需要在回调适配层或插件中区分：

- 业务 NG：应转成 `inspection_ng` 或内部 NG 事件
- 硬件故障：才走 `command_failed`

## 4.8 出料目标语义不兼容

### 真实硬件要求

出料命令目标是：

- `location_type = BIN`
- 且必须携带 `bin_id / bin_type / bin_cell_location / reel_*`

### 当前实现

当前插件出料命令目标为：

- `target_type = BIN`
- 但尚未稳定携带 `bin_id / bin_type / bin_cell_location / reel_*`

对应代码： [plugin.py](/Users/kaizhou/SynologyDrive/works/wes_backend/src/workline_plugins/smt_classifier/plugin.py#L806)

### 偏差结论

当前插件已经从“出料平台”语义切换到“料箱”语义，但还停留在“放入某类 BIN”的抽象层，尚未达到“放入具体料箱格位”的真实硬件要求。

### 影响

即使主流程继续到出料阶段，当前实现也无法给真实设备提供足够的落箱信息。

### 建议

插件至少要补充：

- `bin_id`
- `bin_type`
- `bin_cell_location`
- `reel_layer`
- `reel_thickness`
- `reel_diameter`
- `reel_totalthickness`

如果这些数据来自上游业务系统，应在 session context 中提前准备。

## 5. 中风险偏差

## 5.1 设备状态查询接口不兼容

### 真实硬件要求

- `GET /api/v1/status`

### 当前 mock

- `GET /api/v1/device/status`
- `GET /api/v1/pipeline/status`

### 结论

这不影响当前主编排闭环，但会影响运维联调、健康检查和设备诊断接口统一性。

## 5.2 取消命令接口不兼容

### 真实硬件要求

- `POST /api/v1/command/cancel`
- 请求体为 `{ "command_id": "..." }`

### 当前 mock

- `POST /api/v1/device/cancel`
- 请求体是当前 mock 自定义格式

### 结论

若后续需要支持取消命令，当前 mock 和运行时实现仍需额外适配。

## 5.3 多工位/双侧单元支持不完整

### 真实硬件要求

文档明确区分：

- 左侧 `PIPELINE01 / ARM01 / ARM02`
- 右侧 `PIPELINE02 / ARM03 / ARM04`
- 多个实际位置，如 `STATION_PIPELINE1_INPUT1`、`STATION_PIPELINE1_INPUT2`

### 当前实现

当前拓扑推导支持按上下游关系找到一条链：

- `INPUT_ARM -> CONVEYOR -> OUTPUT_ARM`

但命令层仍未选择真实工位：

- 没有显式选择 `INPUT1 / INPUT2`
- 没有左右侧位置库

### 结论

当前实现更像“单链抽象模型”，还不是“可执行的双侧设备模型”。

## 6. Mock 与真实硬件的一致性评估

### 6.1 Mock 机械臂

与真实硬件相比，`arm_mock.py` 的主要偏差：

- 路径使用 `/api/v1/device/command`，不是真实硬件的 `/api/v1/command`
- 消费 `params.source_loc / target_loc`，而不是 `source/target` 对象
- 回调使用 `command_code / device_code`
- 回调 `error_detail` 字段命名不统一

结论：

- 适合验证当前 WES 内部闭环
- 不适合作为真实硬件协议兼容性证明

### 6.2 Mock 流水线

与真实硬件相比，`pipeline_mock.py` 的主要偏差：

- 支持 `SCAN_COMPLETED`，但真实文档中扫码职责在 `ARM01 / ARM03`
- 检测事件使用 `PROCESS_COMPLETED`
- 真实协议更接近“检测结果由命令回调带回”，而不是独立事件

结论：

- 更像测试辅助工具
- 不是按照真实粗分机协议建模的高保真模拟器

## 7. 修正优先级

## 7.1 P0: 必须先修

1. 建立真实硬件命令适配层
2. 建立真实硬件事件/回调适配层
3. 修正 `task_type` 对外发送值
4. 修正 `command_id/device_id` 与 `command_code/device_code` 的对外映射
5. 修正检测/测厚结果来源建模

## 7.2 P1: 联调前应修

1. 建立真实位置映射和 `source/target` 对象生成
2. 建立 `BIN` 出料参数模型
3. 区分“业务 NG”与“硬件失败”
4. 对齐错误码字段名

## 7.3 P2: 完整设备模型应修

1. 对齐状态查询接口
2. 对齐取消命令接口
3. 建立左右双侧、多工位位置选择策略
4. 升级 mock 为真实协议高保真模拟器

## 8. 推荐改造方案

本轮工程评审后，推荐方案不再是“在 callback / Celery / mock 多层分散兼容真实硬件协议”，而是明确收口为：

- `plugin` 与厂商绑定，是该工作线唯一的协议真相源
- `plugin` 提供并维护：
  - 命令枚举
  - 事件枚举
  - 结果枚举
  - 字段映射规则
- 这些厂商协议枚举只能存在于 plugin contract，不能再放回 `src/app/device/models` 或 runtime 通用层
- `Device` 必须绑定这套约定
- callback 入站必须先解析 `device -> workline -> plugin`，再按 plugin contract 做校验和 ACK
- `Device / DeviceCommand / WorklineSession` 必须留下 contract 快照，保证历史证据可解释；事件原始报文保留在 `callback_logs`
- Celery 出站只负责派发，不再二次改写 vendor payload 语义

### 8.1 最终职责边界

```text
Device
  -> callback/event | callback/result
  -> raw JSON ingress
  -> resolve device
  -> resolve workline.plugin_key
  -> load plugin contract
  -> validate + normalize
  -> ACK success | ACK error
  -> persist evidence snapshot
  -> WorklineInbox
  -> Orchestrator
  -> Plugin decision
  -> DeviceCommand(vendor payload)
  -> WorklineOutbox(vendor payload)
  -> dispatch as-is
  -> Device
```

职责说明：

- `plugin`
  - vendor contract truth
  - 负责厂商枚举、字段规则、归一化映射、拓扑语义
- `callback`
  - minimal envelope only
  - 只保留最小包络模型，不维护厂商事件枚举
- `Device`
  - instance binding
  - 负责角色、上下游拓扑、能力声明、contract/profile/version 绑定
- `Runtime / Orchestrator / Celery`
  - control flow only
  - 不再重写设备协议字段
  - 不维护厂商专属命令/事件/结果枚举

### 8.2 流程推进原则

工作线控制流必须遵守以下口径：

- 下一跳设备：
  - 由 `devices.upstream_device_id` 推导
- 下一步业务分支：
  - 由当前 `step_code` 和业务结果字段推导
- 位置字段：
  - 仅用于校验和观测
  - 不再作为主分支判断依据

### 8.3 入站校验原则

callback 入口不应直接把全局 Pydantic schema 作为唯一协议真相，而应采用两阶段解析：

1. 最小包络解析
   - 原始 JSON
   - `device_code`
   - `event_type` / `result` 原始字符串
   - 时间戳、请求元信息
2. plugin-aware contract 校验
   - `device_code -> Device -> WorkLine.plugin_key -> plugin contract`
   - 事件枚举、结果枚举、字段结构、必填项、值域校验
   - 失败时返回明确 ACK 错误

补充约束：

- callback 最小包络模型允许出现 `event_type: str`
- callback 最小包络模型不允许维护 `SmtClassifierEventType` 之类厂商枚举
- 若某条工作线需要新增事件类型，只能修改对应 plugin 的 `contract.py`

### 8.4 出站派发原则

plugin 生成的 vendor payload 必须是设备实际收到的 payload。

禁止以下模式继续扩大：

- `task_type` 存一套内部映射值
- `params.action` 再藏一套 vendor 值
- `dispatch_outbox_batch` 出站时再把 vendor 值翻回来

如确有内部统计需求，应显式新增内部字段，不污染设备协议字段。

## 9. 附带发现：当前仓库内部基线也存在偏差

本次分析还发现一个仓库内部问题：

- `docs/workline_smt_classifier_runtime_flow.md` 的旧版本曾把某些 OK 流程描述成“扫码 OK 后不下命令、等待检测事件”
- 但当前插件代码已经在 `scan_ok` 分支直接生成 `PICK_AND_PUT` 命令，把物料从输入位搬到流水线进料位

当前代码基线应以 [plugin.py](/Users/kaizhou/SynologyDrive/works/wes_backend/src/workline_plugins/smt_classifier/plugin.py#L576) 为准。

## 10. 总结

当前 `smt_classifier` 的问题，不在“有没有流程”，而在“流程所依赖的协议对象不是硬件真实协议”。

简化地说：

- 当前实现更像“面向内部抽象和测试 mock 的编排系统”
- 真实硬件文档要求的是“面向实际设备控制对象的执行协议”

两者之间缺的不是小修小补，而是一层明确的协议适配与业务语义归一。

## 11. 可执行整改清单

本节把上述架构结论收敛成可以按阶段推进的整改计划。

### 11.1 整改目标

完成整改后，应达到以下状态：

1. `smt_classifier` plugin 成为该工作线唯一的厂商协议真相源
2. `Device` 明确绑定 plugin contract，违约请求能收到明确 ACK 错误
3. callback 入站可以稳定区分：
   - 合法请求
   - 幂等重复
   - 协议违约
   - 运行期内部异常
4. `Device / DeviceCommand / WorklineSession` 能完整追溯“当时按哪版 contract 解释”，并能结合 `callback_logs` 复盘原始事件
5. Celery 出站不再改写命令口径，plugin 与硬件始终统一语言

### 11.2 P0 整改项

| 编号 | 任务 | 目标 | 涉及文件 | 完成标准 |
|------|------|------|----------|----------|
| P0-1 | 定义 `smt_classifier` plugin contract | 在 plugin 内集中声明命令枚举、事件枚举、结果枚举、字段映射规则 | `src/workline_plugins/smt_classifier/plugin.py`、`src/workline_plugins/smt_classifier/__init__.py` | plugin 内存在单一可引用的 contract 定义；callback / mock / runtime 不再各自定义同义枚举 |
| P0-2 | callback 改为 raw JSON 两阶段解析 | callback 入站先按最小包络解析，再按 `device -> workline -> plugin` 做协议校验 | `src/app/callback/v1/callback.py`、`src/app/callback/models/event.py` | 非法枚举、缺字段、错误字段结构都能返回明确 ACK 错误；不再依赖 FastAPI 默认 422 作为主要行为；callback 层不再维护厂商枚举 |
| P0-3 | Device 绑定 contract | 让设备实例可声明其遵循的 plugin/profile/version | `src/app/device/models/device.py`、相关 migration | `Device` 可明确看出绑定的 `plugin_key / contract_profile / contract_version` |
| P0-4 | 证据表落 contract 快照 | 为设备、命令、会话补充 contract 快照字段，并保留事件原始报文 | `src/app/device/models/device.py`、`src/app/device/models/command.py`、`src/app/workline/models/session.py`、相关 migration | 任一历史 `Device / DeviceCommand / WorklineSession` 都可追溯其 `plugin_key / contract_profile / contract_version / step_code`，事件原始报文可在 `callback_logs` 复盘 |
| P0-5 | 移除 Celery 对设备协议的二次改写 | 出站 payload 与 plugin 产出保持一致 | `src/celery_app/tasks/workline.py`、`tests/workline_runtime/test_outbox_dispatcher.py` | 不再依赖 `params.action` 回填设备协议值；派发 payload 为 plugin 直接产物 |
| P0-6 | 把流程推进从位置字符串切到步骤语义 | 下一跳依赖 `upstream_device_id`，分支依赖 `step_code + result` | `src/workline_plugins/smt_classifier/event_handlers.py`、`src/workline_plugins/smt_classifier/plugin.py` | 主流程不再依赖 `_is_pipeline_input_location()` 之类字符串规则作为主判断 |

### 11.3 P1 整改项

| 编号 | 任务 | 目标 | 涉及文件 | 完成标准 |
|------|------|------|----------|----------|
| P1-1 | 工作线保存时补 contract 校验 | 不仅校验设备角色数量，还校验设备是否满足 plugin contract | `src/app/workline/services/workline_service.py`、`src/workline_plugin_registry.py`、`src/workline_plugins/smt_classifier/plugin.py` | 保存工作线或调整设备绑定时，错误的 command/event/result/profile/version 会被拒绝 |
| P1-2 | 启动期 fail-fast 校验 | 服务启动时扫描启用中的 workline/device 绑定并检测 contract 漂移 | 启动入口、工作线加载相关模块 | contract 漂移能在启动时失败或至少产生强告警，而不是等到设备首包回调才暴露 |
| P1-3 | 区分业务 NG 与硬件故障 | 避免把业务 NG 当成 command failure | `src/workline_plugins/smt_classifier/plugin.py`、`src/workline_plugins/smt_classifier/event_handlers.py` | inspection NG 可走 NG 分流；硬件故障才进入失败/重试路径 |
| P1-4 | 真实物理位置对象映射 | 将逻辑位置转换为真实 `source/target` 位置对象与 `BIN` 信息 | `src/workline_plugins/smt_classifier/plugin.py`、设备配置、相关文档 | 出站命令可直接生成真实设备可执行的 `source/target` 结构 |
| P1-5 | mock 与 plugin contract 对齐 | mock 服务以 plugin contract 为准，而不是本地调试惯例为准 | `tests/mock/smt_classifier/arm_mock.py`、`tests/mock/smt_classifier/pipeline_mock.py`、`tests/mock/smt_classifier/mock_support.py` | mock 正式接口只接受 plugin 约定的 vendor payload；调试接口继续保留但不污染正式协议 |

### 11.4 P2 整改项

| 编号 | 任务 | 目标 | 涉及文件 | 完成标准 |
|------|------|------|----------|----------|
| P2-1 | 对齐状态查询与取消命令协议 | 补齐真实硬件联调时所需的外围接口口径 | mock 服务、相关 docs | 健康检查、取消命令、辅助接口都与最终协议口径一致 |
| P2-2 | 扩展多工位/双侧单元模型 | 为后续 `PIPELINE02 / ARM03 / ARM04` 等场景准备 | 设备配置、plugin 拓扑解析、位置映射 | 多工位和双侧场景不再依赖硬编码位置命名 |
| P2-3 | 契约变更文档化机制 | plugin contract 变更时有固定升级说明和回写流程 | `docs/workline_smt_classifier_runtime_flow.md`、本文件、相关设计文档 | 每次 contract 变更都能明确记录变更点、迁移方式、验收结果 |

### 11.5 测试清单

整改完成前，至少补齐以下测试：

| 层级 | 用例 | 目标 |
|------|------|------|
| callback contract | 合法 `event` / `result` 请求 | 返回 ACK success，并进入 inbox / evidence 流程 |
| callback contract | 非法事件枚举 | 返回 ACK error，且不进入 inbox |
| callback contract | 非法结果枚举 | 返回 ACK error，且不进入 inbox |
| callback contract | 缺失必填字段 / 字段类型错误 | 返回 ACK error，并写 callback 日志 |
| binding | device 绑定错误 plugin/profile/version | 保存时拒绝，或启动期 fail-fast |
| runtime | `step_code + result` 推进主流程 | 不依赖位置字符串仍能完成 OK / NG 主链路 |
| outbox | plugin 产出 payload 原样派发 | Celery 不再改写 `task_type/action` |
| evidence | 命令 / 事件 / 会话快照 | 能查到 `plugin_key / contract_version / step_code` |

### 11.6 推荐执行顺序

按以下顺序推进，返工最少：

1. 先固化 plugin contract
2. 再改 callback 入站为 plugin-aware 解析
3. 同步补 `Device / DeviceCommand / WorklineSession` 字段和 migration，并确认 `callback_logs` 保留原始事件报文
4. 再删掉 Celery 出站改写逻辑
5. 再替换流程推进中的位置字符串启发式
6. 最后补启动校验、契约测试和 mock 对齐

### 11.7 验收标准

本轮整改完成的验收信号应是：

1. 任何一条设备回调，都可以回答：
   - 它属于哪台 `Device`
   - 绑定哪条 `WorkLine`
   - 由哪个 `plugin`
   - 按哪版 `contract`
   - 为什么 ACK success 或 ACK error
2. 任何一条下发命令，都可以回答：
   - plugin 产出了什么 vendor payload
   - 数据库保存了什么 vendor payload
   - 设备实际收到的 payload 是否完全一致
3. 任何一条历史事件或命令，都不需要靠“猜当时代码版本”来解释其语义

### 11.8 本轮整改的非目标

为控制范围，本轮整改不包含：

- 抽象出跨所有厂商共享的通用 adapter framework
- 运行期热更新 plugin contract
- 单独建设协议管理后台
- 双侧多工位场景的一次性完全重构
