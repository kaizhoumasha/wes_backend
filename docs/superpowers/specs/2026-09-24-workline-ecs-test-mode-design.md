# WORKLINE ECS_TEST 设备集成测试模式设计

**状态：已批准**

**日期：2026-09-24**

## 1. 目标与边界

`ECS_TEST` 是 WORKLINE 的真实设备集成测试模式。现场持续产生真实 ECS 事件；WES 对每个新的、已配置的事件可靠创建一条预设的固定 `DeviceCommand`，并通过 `DeviceEvidence → DeviceCommand → Result` 核对接收、派发、接纳、终态和身份关联。它允许观察 ECS 与物理设备在连续事件驱动下的长期运行；WES 不主动制造事件、计时循环或重复下发。

测试模式不接纳新的 WMS 业务、不创建 WMS operation、不运行插件业务 Decision，不根据扫码数据判断 OK/NG、选择目的地、改写参数或编排后继动作。每次只验证一条预先设定的固定动作；一个新事件对应至多一条命令，精确重报沿用原结果。同一目标设备上的不同事件仍是独立命令，ECS 负责容量与物理互斥。

## 2. 合同依据

`docs/integration/third_party_integration_whitepaper.md` 是已批准的四个 ECS 网络接口真源：命令发送至 `POST /api/v1/device/command`，动作参数位于 `params`；Event 与 Result 分别经 WES 回调接口接收，ACK 只证明接收，匹配的 Result 才证明设备报告的终态。`docs/architecture/device-command-contract.md` 规定持久化命令身份、冻结载荷、未知结果及调试命令与业务 Decision 的隔离。

`docs/hardware/SMT流水线接口调用说明书20260320-v1.md` 和 `docs/hardware/SMT粗分机接口调用说明书20260321-v1.md` 仅用于选择设备动作和事件场景。两者的旧 URL、顶层 `source`/`target`、时间格式、`ESTOP_PRESSED` 上报和成功 Result 的 `error_detail` 示例不得覆盖已批准统一 wire。具体设备的 `event_type`、`task_type`、`params` 二级字段及其安全含义须由获批设备合同附录确认；未确认时不能把示例当成真实 ECS 验收依据。

统一 Event wire 没有独立事件 ID，目前以规范化报文（包括毫秒 `timestamp` 和 `data`）识别重报。要满足“每个新的真实事件独立触发”，ECS 的设备附录须确认：同一来源的两个真实扫码即使数据相同，也产生不同的规范化事件身份；同一事件重报保持完全相同的身份字段。若供应商不能保证，需先评审新增事件身份的统一 wire 变更，不能把内容相同的两个真实事件误当作一次测试成功。

流水线可用 `SCAN_COMPLETED` 驱动一条预设 `MOVE_FORWARD`；粗分机可用 `SCAN_COMPLETED` 驱动一条预设 `PICK_AND_PUT` 或设备附录批准的其它动作。粗分机命令的固定 `source`/`target` 均在 `params` 内；该动作不代表 OK/NG 分流。事件缺少六个扫码字段仍可触发测试规则，因为模式不解析业务数据。`ESTOP_PRESSED` 仍由 ECS 自行处理，不成为测试触发事件。

## 3. 配置与启停

在 WORKLINE 的运行配置中保存 `ecs_test_rules`，每条规则包含：

- `source_device_code`：上报 `SCAN_COMPLETED` 的本线设备；每个来源在一条 WORKLINE 上最多一条规则。
- `target_device_code`：执行命令的本线设备，可与来源相同，也可不同。多个来源可以指向同一目标；不据此建立 WES 设备占槽。
- `task_type`、`params`：固定动作及其完整参数。`params` 是对象，不从 Event `data` 插值、复制或动态推算。

规则不包含超时重试策略、条件表达式、动作序列、循环次数或 WMS 字段。`SCAN_COMPLETED` 是本模式唯一触发类型。没有匹配规则的其它合法 ECS 事件仍可靠留存供诊断，但不产生测试命令，也不进入插件业务。

以下是配置形状的**讨论示例，非设备合同**；`params.source` 的实际字段及目标设备能力仍以获批附录和 ECS 声明为准：

```json
{
  "ecs_test_rules": [
    {
      "source_device_code": "STATION_SCAN1",
      "target_device_code": "STATION_SCAN1",
      "task_type": "MOVE_FORWARD",
      "params": {"source": {"location_id": "STATION_SCAN1", "location_type": "SCAN_PLATFORM"}}
    }
  ]
}
```

配置仅在 WORKLINE 停用且相关业务、WMS、Transport、设备命令和待应用 Evidence 的未闭合义务均已清空时可修改。`ECS_TEST` 启动从 WORKLINE 所拥有的 Device 清单校验并冻结来源与目标的 `device_code`、Endpoint、设备合同及命令超时；不要求选择业务插件或补齐插件设备/位置角色。启动时核对 ECS 连通性以及来源事件、目标命令的静态能力；启动之后不以每次 Status 查询授权命令。活动期间规则和设备归属不可修改。

WORKLINE 可保留停用前的插件草稿，`ECS_TEST` 启动将活动 `plugin_version` 与业务 `flow_mode` 置空，不激活插件。当前启动路径要求插件，worker 启动检查又枚举所有活动 WORKLINE 的插件身份；实施时两者都必须按运行模式区分，活动测试线不要求安装业务插件。停用测试线同样跳过插件专属 drain 和业务后继，但仍检查当前线是否存在原业务未闭合义务，不能用模式切换抹掉它们。以后切回业务模式时，正常 START 重新校验并冻结当前已安装插件版本。

当前 `WorkLineRepository.list_bindings()` 只枚举插件 `config.device_bindings`，设备 Event 准入和命令创建都依赖它。实施时须让活动 `ECS_TEST` 线按冻结的测试来源/目标设备提供同一内部 binding 查询能力，而不是仅在配置页面保存规则；业务模式仍沿原插件角色绑定。

退出模式时复用 WORKLINE 停用及未闭合义务检查，并对测试命令补充原身份的未知结果检查：尚未处理的事件、待派发/已接纳/`RECONCILING`/`TIMED_OUT` 的测试命令和待应用 Result 阻止切换。现有通用 workload 查询未覆盖 `TIMED_OUT`，因此不能直接把它当作本模式的完整切换门禁。迟到回调始终按原 `command_code` 留存和收敛，不能因模式切换交给业务插件或改用新身份重发。已闭合历史不参与新测试准入。

`WorkLineRunMode` 增加 `ECS_TEST` 需要相应数据库约束迁移；测试规则复用现有运行配置 JSON，不另建配置表。`ECS_TEST` 使用真实 ECS，不沿用仅限开发/测试环境的 `SIMULATION` 语义。

## 4. 可靠数据流

1. ECS 发出真实 `SCAN_COMPLETED`。公共 Event 入口按统一 wire 校验，使用现有规范化事件身份与 `InboundEvidence` 持久化并在提交后 ACK，首次接收时绑定 WORKLINE。模式与规则在活动期间不可修改，停用又受待处理 Evidence 阻断，因此 worker 处理时的路由不会因配置切换漂移；重复接收沿用原 Evidence 身份。
2. 现有 DeviceEvidence worker 领取 Event。它在 WORKLINE 行锁内读取不可变测试规则与目标设备的冻结 binding，按 `workline_id + event identity` 建立唯一测试执行关联，并在同一事务创建一条 `DeviceCommand`，随后按现有调试事件惯例将 Event 标记为 `IGNORED`，表示它不进入 FactProcessor；命令关联仍证明该 Event 已被测试链路消费。先有持久 Evidence，后有持久命令，事务提交后唤醒现有命令派发；通知丢失由持久扫描接手。截止时间在首次原子创建命令时按当时的时间预算冻结，不按可能延迟很久的 Event 时间计算；重复处理先读取原命令及冻结截止时间，不生成第二个 `command_code` 或重算原命令载荷。
3. 命令使用目标设备自己的 `device_code`、Endpoint、合同、`task_type` 与固定 `params`。统一 ECS Adapter 发送一次，冻结载荷和身份不随规则改变。ECS ACK 只表示接纳；超时或结果未知保留原命令及受影响的事件关联，不换身份重发等价动作，也不阻止其它独立事件生成命令。
4. ECS Result 经公共入口按 `command_code`、目标设备及冻结合同匹配，可靠留存并闭合对应命令。测试 Result 不唤醒插件业务 Decision、WMS 或 Transport，也不借其推断其它命令完成。重复或相冲突的 Result 按现有证据与对账规则处理。

现有 `DeviceEvidenceService` 对状态为 `TIMED_OUT` 的命令会忽略后到 Result，而已批准设备命令合同要求未知结果沿原身份接受迟到回调。实施时须将这条共享结果应用路径收敛到当前合同；不能让测试模式把 `TIMED_OUT` 当作物理失败或新命令许可。

`ECS_TEST` 需有独立于 `WORKLINE_BUSINESS` 和现有 `EVENT_DEBUG` 的内部执行引用类型；其命令归属当前 WORKLINE，以便未闭合义务阻止停用，Result 仍只闭合命令和 Evidence。该类型不进入外部 wire。

## 5. 与现有入口的互斥

`ECS_TEST` 活动线不启动业务插件准备、Fact-to-Decision、WMS 确认创建或其它业务驱动。新的 WMS 业务意图若指向该线，按既有可靠接收合同保存或拒绝并给出明确冲突结果，但不创建测试线业务对象；旧身份的精确重报仍返回原幂等结果。启用前必须闭合原 WMS 可靠义务，不能靠模式标志丢弃它们。

现有 Transport 自动联调 `test_mode` 会把 `STATION_SCAN` 加数字的事件提升为 `EVENT_DEBUG`，且可能覆盖正式 WORKLINE 绑定。本模式启动与 Transport debug-run 创建必须检查来源设备交集，拒绝同时接管。活动 `ECS_TEST` 来源不走固定 `MOVE_FORWARD`、全局 Endpoint、Event `data` 转 `params` 的旧 `EVENT_DEBUG` 路径。正常测试事件由 ECS 发送 `is_debug=false` 或省略该字段；活动 `ECS_TEST` 来源显式发送 `is_debug=true` 时，入口拒绝该事件而不创建任何命令。当前白皮书规定显式 debug 事件走 `EVENT_DEBUG`，因此实施前须修订该冲突场景的公共调试合同，不能静默改变其语义。

## 6. 观察与验收

复用现有 Event Evidence、DeviceCommand 和 Result 记录，按 WORKLINE、来源设备、目标设备与观察时间段查询。一次连续运行的技术核对至少包括：新事件数、精确重报数、创建命令数、命令 ACK 数、匹配终态 Result 数、失败/超时/对账数及未闭合原身份清单；逐条可追溯 `Event identity → command_code → Result`。精确重报不得增加命令数；每个新且匹配规则的事件最终恰有一条命令记录。测试模式运行期间新 WMS operation、业务对象及插件 Decision 创建数均为零。

验收时由现场持续提供真实事件，WES 不按固定频率发命令或自动重开物理动作。FAST/集成验证覆盖事件接收与重报、不同来源到同一目标、固定参数不受 Event `data` 影响、worker 崩溃后续处理、命令/结果关联、模式互斥和 WMS 业务隔离。真实 ECS 连续运行需记录观察时段、对端版本、事件与命令样本、Result 终态及现场设备观察；本地 Mock 绿色不能替代 ECS 或物理设备稳定性验收。时长、频率和允许失败阈值由具体现场验收任务给出，本模式不内建主动负载发生器或成功率判定器。
