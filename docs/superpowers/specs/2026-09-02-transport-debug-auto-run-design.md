# Transport 现场自动联调设计

- 日期：2026-09-02
- 状态：已确认，联调中
- 范围：Transport diagnostics、WMS Transport 正式合同、ECS/WES Device Evidence 消费、前端 `/ops/transport-diagnostics`

## 1. 背景与目标

现有“510056 现场联调步进”固定货架、料箱和路线，并依赖操作员逐步确认。现场已经打通 RCS → WMS → WES 链路，下一步需要把它收敛为一轮可恢复的自动诊断流程：操作员按现场情况直接录入货架、各面的料箱与原槽位并输入面值，后端根据 WMS 回调和 `SCAN12` Device Evidence 自动推进，所有选中料箱回架后才执行 `CTU03` 返库。

本设计的目标是：

1. 允许操作员直接录入任意现场货架、各面料箱及其原槽位，不依赖 WES 资源基础数据或挂载投影。
2. 面值由操作员按组直接输入，例如 `"90"`、`"270"`；系统冻结并原样下发，不解析、不归一化、不做 A/B 映射。
3. 一组 1～4 个料箱。同一货架面处理完成后，才旋转到下一个已选料箱所在面。
4. 浏览器关闭、刷新或前端断连不影响执行；后端重启后能够继续恢复。
5. 对 Transport 投递未知、位置冲突或证据歧义保持 fail-closed，不把 HTTP ACK、健康检查或猜测当作物理完成。

## 2. 明确边界

### 2.1 本次包含

- 修改正式 Transport 合同，使指定的 `RACK` 位置能够直接按字符串下发。
- 新增持久化的 Transport 诊断轮次及自动推进器。
- 消费 WMS Transport 回调和已持久化的 Device Evidence。
- 改造 Transport diagnostics 页面为“配置、启动、观察”模式。
- 保留每一步 Transport task、回调和 Device Evidence 的可追溯关系。

### 2.2 本次不包含

- 不建立货架面映射、角度枚举或 A/B 转换。
- 不把诊断流程提升为正式出入库业务流程，也不创建业务单据。
- 不在 Device 基础能力中硬编码 `SCAN12` 的业务含义或 Transport 诊断身份。
- 不用前端定时器充当流程编排器。
- 不自动重发 `DELIVERY_UNKNOWN` 的任务，不提供跳步、伪造完成或换新请求 ID 重试。
- 不自动清理现有 Transport task、回调、Evidence 或现场诊断数据。

## 3. 总体架构

自动流程由后端 Transport diagnostics 域持久化和驱动：

```text
操作员配置并启动
        │
        ▼
TransportDebugRun（冻结配置和当前进度）
        │
        ├── 创建 Transport task ──► WMS ──► RCS
        │                                  │
        │                         WMS callback
        │                                  │
        ├────────────── 唤醒并校验 ◄───────┘
        │
        ├── 读取中性 Device Evidence ◄── ECS/WES SCAN12
        │
        └── 持久化推进 / NEEDS_ATTENTION / 完成
                         │
                         └── SSE 仅通知前端重新 GET
```

权威状态始终是数据库中的诊断轮次、Transport task 和 Device Evidence。SSE 只用于降低页面刷新延迟，不承载状态，也不是流程继续运行的前提。

为避免共用 `KT16`、`CNV0301`、`CNV0302`、`SCAN12` 时发生串线，任一时刻只允许一个未释放的自动诊断轮次。`RUNNING` 和 `NEEDS_ATTENTION` 都占用该全局执行权。
活动轮次同时独占其 `rack_id`：自动轮次自身以外的 Transport 创建入口不得为同一货架创建新任务；每个自动后继步骤创建前必须在同一事务内复核货架仍位于工作位且朝向与当前步骤一致。

## 4. 正式 Transport 合同变更

### 4.1 位置类型

本次直接扩展正式合同，不在 diagnostics 或 WMS adapter 中增加兼容映射：

- `CTU01 / RACK_MOVE`：允许 `RACK → RACK_POSITION`。
- `CTU02 / RACK_ROTATE`：`position` 允许 `RACK`；其 `location_code` 必须与 `rack_id` 完全一致。
- `CTU03 / RACK_MOVE`：允许 `RACK → ZONE`。
- `target_face` 继续是非空不透明字符串。

既有精确位置边和其它模板约束保持不变。新增边必须按模板显式放行，不能把所有 `RACK`/`ZONE` 组合泛化为任意可用。

### 4.2 三类下发

货架搬出使用当前组的面值：

```json
{
  "rack_id": "510056",
  "source": {
    "kind": "RACK",
    "location_code": "510056"
  },
  "target": {
    "kind": "RACK_POSITION",
    "location_code": "KT16"
  },
  "target_face": "90",
  "rcs_template_id": "CTU01"
}
```

货架旋转使用下一组的面值：

```json
{
  "rack_id": "510056",
  "position": {
    "kind": "RACK",
    "location_code": "510056"
  },
  "target_face": "270",
  "rcs_template_id": "CTU02",
  "kind": "RACK_ROTATE"
}
```

全部选中料箱回架后返库；返库面固定为已确认的 `"90"`：

```json
{
  "rack_id": "510056",
  "source": {
    "kind": "RACK",
    "location_code": "510056"
  },
  "target": {
    "kind": "ZONE",
    "location_code": "WH01"
  },
  "target_face": "90",
  "rcs_template_id": "CTU03",
  "kind": "RACK_MOVE"
}
```

WMS wire adapter 继续把旋转表达为其现有 wire 结构；当正式输入是 `RACK` 时，wire 中旋转的 source/target 都使用同一个原始 `RACK` 位置。这里是新正式合同的直接序列化，不是兼容翻译。

### 4.3 回调成功条件

`RACK` 和 `ZONE` 是请求中的宽位置引用，不能作为完成后的精确物理位置。WMS 成功回调仍必须带回可校验的精确 `RACK_POSITION` 结果及实际 `arrival_face`：

- `CTU01`：结果位置必须是 `KT16`，面值必须与当前组冻结值完全相等。
- `CTU02`：结果位置必须仍是 `KT16`，面值必须与下一组冻结值完全相等。
- `CTU03`：结果必须是 WMS 从 `WH01` 解析出的精确 `RACK_POSITION`，面值必须为 `"90"`。

若回调缺少精确位置、面值不一致、位置冲突或任务进入 `RECONCILING`，自动流程进入 `NEEDS_ATTENTION`，不创建下一任务。

## 5. 持久化模型

### 5.1 `TransportDebugRun`

轮次表保存不可变配置和可恢复游标，核心字段为：

- `id`：轮次 ID。
- `status`：`RUNNING | NEEDS_ATTENTION | COMPLETED | FAILED | ABORTED`。
- `active_scope`：未释放时固定为 `GLOBAL`；数据库唯一约束保证单实例。
- `rack_id`。
- `configuration`：按顺序冻结的面组、面值、料箱、原始槽位，以及固定位置和模板快照。
- `current_group_index`、`current_phase`、`current_step_id`。
- `attention_code`、`attention_detail`。
- `version`：乐观锁版本。
- 创建人、创建时间、更新时间和终止审计字段。

查询 snapshot 同时返回按 `ordinal` 排序、截至当前持久游标的完整步骤历史。页面用该历史集中展示每一步的货架面、料箱、槽位、SCAN12 扫码进度和关联 Transport 任务；轮次进入终态后历史仍保留可见。

固定快照包含：

- `storage_zone = WH01`
- `workstation = KT16`
- `infeed_position = CNV0301`
- `outfeed_position = CNV0302`
- `rack_out_template = CTU01`
- `rack_rotate_template = CTU02`
- `rack_return_template = CTU03`
- `rack_return_face = "90"`

轮次启动后不能修改这些值。它们当前不是站点配置平台的一部分，避免为单一现场诊断引入额外抽象。

### 5.2 `TransportDebugRunStep`

步骤表保存每次外部动作及其证据：

- `run_id`、`ordinal`、`group_index`、`phase`。
- `status`：`PENDING | WAITING | SUCCEEDED | FAILED | NEEDS_ATTENTION`。
- 确定性的 `client_request_id`。
- `transport_task_id`。
- 当前面开始前的 `device_evidence_high_watermark`。
- 已观察的目标料箱编码集合及对应 Evidence ID。
- 错误码、错误详情和时间戳。

步骤 `(run_id, ordinal)`、`client_request_id` 均唯一。轮次当前指针和步骤状态在同一事务中更新，防止重复回调或并发 worker 重复推进。

### 5.3 启动校验

创建轮次时必须在同一一致性检查中确认：

- 至少一组面；后端用 `face.strip()` 仅判断原值是否全为空白，但保存和下发的仍是未经变换的原始字符串。
- 每组 1～4 个料箱。
- 面值按字符串完全匹配去重，`"90"` 与 `"090"` 是不同值。
- 同一料箱不能出现在多个组。
- 货架编码、料箱编码和原货架槽位均为非空字符串。
- 操作员负责确保录入值与现场实物一致；WES 不查询或校验资源基础数据与挂载投影。
- 当前没有占用 `GLOBAL` 的轮次。

校验通过后冻结配置。后续输入或资源投影变化不会改变执行目标；执行期间仍以 WMS 回调、精确位置与 `SCAN12` Evidence 为推进依据，事实缺失或冲突时 fail-closed。

## 6. 自动状态机

```text
RACK_TO_STATION
        │ CTU01 SUCCEEDED
        ▼
BINS_TO_INFEED ──► WAIT_SCAN12 ──► BINS_TO_RACK
                                         │
                      ┌──────────────────┴──────────────────┐
                      │ 还有下一面                          │ 已无下一面
                      ▼                                    ▼
             ROTATE_TO_NEXT_FACE                     RACK_TO_STORAGE
                      │ CTU02 SUCCEEDED                     │ CTU03 SUCCEEDED
                      └────► BINS_TO_INFEED                  ▼
                                                       COMPLETED
```

### 6.1 搬出货架

自动推进器创建 `CTU01`，source 为当前货架的 `RACK` 引用，target 为 `KT16`，`target_face` 为第一组面值。只有对应 Transport task 被正式收敛为 `SUCCEEDED` 且结果位置、面值校验通过，才进入第一组料箱处理。

### 6.2 当前面料箱送入

在创建 `BIN_MOVE` 前，步骤先持久化 Device Evidence 当前最大 ID 作为 high-watermark，再把当前组全部 1～4 个料箱从冻结的原始槽位一次性搬到 `CNV0301`。不得拆批。

Transport task 成功只证明搬运动作完成，不证明料箱已到达 `CNV0302`。成功后进入 `WAIT_SCAN12`。

### 6.3 `SCAN12` 证据门槛

现阶段按已确认的现场事实设计：ECS/WES 的扫码或设备 Evidence 中，`SCAN12` 携带料箱编码。由于现场精确 JSON 尚待联调确认，解析器必须隔离在 diagnostics 内的单一窄适配器中，暂定匹配：

- `device_code == "SCAN12"`
- `event_type == "SCAN_COMPLETED"`
- 料箱编码来自 `data.barcode`

Device ingress 继续只负责中性地持久化 Evidence、去重和发布处理结果，不知道“当前诊断轮次”“当前货架面”或“料箱已到 CNV0302”。diagnostics 适配器收到 Evidence 更新后，根据 `evidence_id` 读取已持久化的 `normalized_payload` 并匹配。

只接受 high-watermark 之后、已成功完成通用 Evidence 处理，且规范化事件时间不早于本面 `BIN_MOVE` 步骤开始边界的记录。任一边界字段缺失或互相矛盾时 fail-closed。旧 Evidence、延迟到达但事件时间过早的 Evidence、重复 `source_event_id`、重复条码和当前组之外的条码都不能推进：

- 重复记录幂等忽略。
- 非本组条码保留诊断记录，但不计入完成集合。
- 当前组所有冻结料箱各出现至少一次后，才能创建回架任务。
- Evidence 冲突、处理状态不确定或载荷无法解释时，进入 `NEEDS_ATTENTION`。

现场确认真实事件名和字段路径后，只修改这一适配器及其契约测试，不改变 Device 基础合同和状态机。

### 6.4 当前面料箱回架

当前组全部通过 `SCAN12` 后，创建一个 `BIN_MOVE`，把 1～4 个料箱从 `CNV0302` 一次性搬回各自冻结的原始槽位。只有任务 `SUCCEEDED` 且结果逐项校验通过，当前面才算完成。

### 6.5 旋转或返库

- 若还有下一组：创建 `CTU02`，position 使用当前货架的 `RACK` 引用，`target_face` 原样使用下一组面值。成功且精确位置、面值校验通过后处理下一组。
- 若没有下一组：创建 `CTU03`，source 使用当前货架的 `RACK` 引用，target 使用 `WH01` 的 `ZONE` 引用，`target_face` 固定为 `"90"`。成功且回调结果校验通过后，轮次进入 `COMPLETED` 并释放全局执行权。

## 7. 失败、未知与人工处置

### 7.1 状态收敛

- Transport `REJECTED` 或明确 `FAILED`：轮次进入 `FAILED`，记录失败 task 和原因，释放全局执行权。
- Transport `RECONCILING`、`DELIVERY_UNKNOWN`、结果位置未知或回调冲突：轮次进入 `NEEDS_ATTENTION`，保留全局执行权并停止派发。
- Evidence 歧义或冲突：进入 `NEEDS_ATTENTION`。
- 后端自身可重试的瞬时数据库/进程错误：不改变物理结论，恢复 worker 重新读取权威状态。

`NEEDS_ATTENTION` 保留执行权是为了避免在物理状态未确认时启动第二轮。若对应 Transport task 之后由既有对账机制安全收敛为 `SUCCEEDED`，推进器可重新校验并继续；不会换新 `client_request_id`。

### 7.2 审计后终止

若现场确认无法继续，需要释放全局执行权，提供窄接口：

```http
POST /api/v1/transport/debug-runs/{run_id}/abort
```

请求必须包含非空原因及显式断言 `PHYSICAL_STATE_VERIFIED`，且轮次必须为 `NEEDS_ATTENTION`。所有关联 Transport task 还必须已经处于 `REJECTED`、`SUCCEEDED` 或 `FAILED` 确定终态，并且不存在活跃资源绑定；`RECONCILING`/`DELIVERY_UNKNOWN` 必须先经既有 Transport 对账收敛，不能靠 abort 绕过。满足条件后，该动作只把轮次标记为 `ABORTED` 并记录操作者、时间和原因：

- 不取消或重置远端 Transport task。
- 不删除 task、回调或 Evidence。
- 不推断货架或料箱位置。
- 不自动创建返库动作。

这是解除诊断单实例占用的审计出口，不是跳步或强制成功。

## 8. 幂等与恢复

每个外部步骤首次创建意图时生成一个 UUIDv7 `client_request_id`，并以唯一 `(run_id, ordinal, phase)` 固定这项映射。推进器遵循“先持久化步骤意图并提交，再调用既有 Transport 创建能力并绑定 task”的顺序；恢复时读取已经持久化的相同请求 ID，不生成新 ID。

推进器可以被以下事件唤醒：

- WMS callback 已持久化并使 Transport task 状态变化。
- Device Evidence 已持久化并完成通用处理。
- 后端启动后的周期恢复扫描。
- 人工完成既有 Transport reconciliation 后的状态变化。

所有唤醒最终都执行同一个幂等 `advance(run_id)`：事务内锁定轮次、重读 task/Evidence、验证当前阶段前置条件，只允许一次状态跃迁。重复回调、重复 SSE 通知、worker 并发和进程崩溃都不会产生第二个物理任务。

`SCAN12` 消费还必须核对 Evidence 独立持久化的 `source_identity`、`device_code`、`contract_key`、`contract_version` 与标准化 payload 一致；任一矛盾均 fail closed 并进入 `NEEDS_ATTENTION`。

运行中的诊断轮次禁止使用现有 debug reset 清除其关联 task。reset 端点必须拒绝仍被 `RUNNING` 或 `NEEDS_ATTENTION` 轮次引用的任务。

## 9. Diagnostics API

### 9.1 创建

```http
POST /api/v1/transport/debug-runs
```

示例请求：

```json
{
  "rack_id": "510056",
  "face_groups": [
    {
      "face": "90",
      "bins": [
        {"bin_id": "A000001922", "slot_id": "SLOT-01"},
        {"bin_id": "A000002653", "slot_id": "SLOT-02"}
      ]
    },
    {
      "face": "270",
      "bins": [
        {"bin_id": "A000003001", "slot_id": "SLOT-03"}
      ]
    }
  ]
}
```

服务端校验非空值、分组数量与重复项后冻结完整配置，不读取资源基础数据或挂载投影；响应返回轮次详情。客户端不能覆盖固定站点、模板或返库面。

### 9.2 查询与通知

```http
GET /api/v1/transport/debug-runs
GET /api/v1/transport/debug-runs/{run_id}
GET /api/v1/transport/debug-runs/stream
```

- 列表用于发现当前活动轮次和历史轮次。
- 详情返回冻结配置、当前面、当前阶段、关联 task、已收到的 `SCAN12` 料箱及 attention 信息。
- stream 只发送轮次 ID、版本和更新时间等失效通知；页面收到后重新 GET 详情。
- 断流后页面直接 GET 即可恢复，不要求补齐所有 SSE 消息。

### 9.3 终止

```http
POST /api/v1/transport/debug-runs/{run_id}/abort
```

仅用于第 7.2 节定义的人工物理审计后终止。

## 10. 前端交互

页面保持 `/ops/transport-diagnostics`，把“510056 现场联调步进”改为“自动联调”。

### 10.1 配置

1. 按现场实际情况直接输入货架编码。
2. 增加有顺序的货架面分组。
3. 每组直接输入非空面字符串。
4. 每组直接输入 1～4 个料箱编码及其原货架槽位。
5. 禁止重复面字符串和重复料箱。
6. 启动前展示将被原样下发的 CTU01、每次 CTU02、各组 BIN_MOVE 和最终 CTU03 摘要。

前端不提供面值下拉枚举，不把 `"90"` 转为数字，也不改变大小写、前后空白或字符串内容。输入控件可以用 trim 后长度判断原值是否全为空白，但不能把 trim 结果写回；提交的值与预览值必须逐字一致。

### 10.2 运行观察

启动后冻结表单，展示：

- 轮次状态及当前货架。
- 当前第几组、当前面原始字符串。
- 当前阶段及关联 Transport task。
- 本组 `SCAN12` 已观察/待观察的料箱。
- 最近更新时间和 attention 原因。

刷新或重新打开页面时，先查询活动轮次并恢复观察。SSE 断开时显示连接状态并采用低频 GET 兜底，但不改变后端执行。

`NEEDS_ATTENTION` 提供关联 Transport task 和 Device Evidence 的诊断入口，不提供强制跳过、伪造扫码、强制成功或换 ID 重发。只有具备相应权限、完成现场物理核对后，才显示带二次确认和必填原因的“终止轮次”。

## 11. 权限与审计

沿用 Transport diagnostics 的只读权限边界，并新增最小动作权限：

- 查看诊断轮次。
- 启动自动诊断轮次。
- 在 `NEEDS_ATTENTION` 状态终止诊断轮次。

创建和终止均记录操作者。终止权限不隐含 Transport reset、远端取消或业务库存调整权限。

## 12. 测试与验收

### 12.1 后端自动化测试

- 正式 DTO 与 OpenAPI：三种指定请求结构可接受；非法模板边和 `rack_id` 不一致 fail-closed。
- WMS adapter：`RACK`/`ZONE` 字符串位置原样序列化；回调必须收敛为精确位置。
- 数据库：迁移、单活动轮次唯一约束、乐观锁和历史保留。
- 启动校验：空货架/料箱/槽位、空面、重复面、重复料箱、每组 1～4 个料箱；不要求资源基础数据。
- 状态机：单面、多面、组间 CTU02、全部料箱回架后才 CTU03。
- SCAN12：high-watermark 之前的旧扫码、重复 `source_event_id`、重复条码、无关条码、乱序到达及完整集合。
- 幂等：重复回调、重复 Evidence 通知、并发 worker、外部调用后进程崩溃。
- 恢复：后端重启、SSE 不可用、Transport reconciliation 后恢复。
- fail-closed：`RECONCILING`、`DELIVERY_UNKNOWN`、精确位置缺失、面值不一致和 Evidence 冲突。
- 单实例：`RUNNING`/`NEEDS_ATTENTION` 阻止新轮次；合规 abort 后释放。

### 12.2 集成与前端测试

- WMS mock 完成单面、多面、旋转和最终返库闭环。
- 页面可直接录入货架、料箱和原槽位，每组限制 1～4 个料箱，不加载资源基础数据。
- 面字符串在输入、预览、创建请求、详情展示和 Transport 下发中完全一致。
- 启动后配置冻结；刷新、断开 SSE、重新登录后恢复同一轮次。
- 正确显示 task、SCAN12 集合、失败和 attention 状态。
- 不出现跳步、假完成或新 ID 重发入口。

### 12.3 契约与现场门禁

- 后端完成 OpenAPI/合同测试后，前端从干净后端 `develop` 候选冻结 canonical OpenAPI，并重新生成类型、Zod 和权限常量。
- 前后端基础门禁、构建和相关契约校验全部通过。
- 现场必须采集一份真实 `SCAN12` Evidence，确认事件名、字段路径、条码格式和处理状态；更新窄适配器及测试后，才把扫码推进标记为现场验收通过。
- 代码通过只证明设计和模拟链路成立，不等于 RCS/WMS/ECS 现场物理闭环已验收。

## 13. 实施顺序与交付边界

1. 后端正式 Transport 合同、WMS adapter 和合同测试。
2. 诊断轮次迁移、repository、状态机、Evidence 窄适配器和恢复 worker。
3. Diagnostics API、SSE 失效通知、权限和审计。
4. 前端冻结新 OpenAPI 后实现选择、预览、启动和观察页面。
5. mock 集成测试和浏览器 QA。
6. 独立审批后再提交、推送、创建 PR、合并和部署。
7. 现场联调采集真实 SCAN12 Evidence，补齐窄适配器细节并完成物理验收。

本设计不授权提交、推送、合并、部署或现场设备动作；这些仍是独立交付阶段。
