# SMT Classifier Bin Allocation And AGV Design

**最后更新**: 2026-03-29

本文档定义 `smt_classifier` 在 OK 主链路尾段引入“库位分配 + AGV 补货架”后的目标运行时设计。

设计目标：

- `ARM02` 出料前，由 `WES` 主动调用正式“库位分配接口”
- 若当前无有效 `target_bin`，由 `WES` 主动调用正式 `AGV` 接口，请求可用货架搬运到粗分机接料位
- `AGV` 完成后，`WES` 再次调用库位分配接口
- 仅当拿到完整 `target_bin` 后，才允许创建 `ARM02` 的正式出料命令

约束前提：

- `plugin` 与硬件接入统一口径，避免运行时再做语义转义
- `ARM02` 只执行明确命令，不参与业务决策
- 当前 mock 阶段，库位分配接口和 AGV 接口先由 mock 服务提供
- 后续替换成 WMS/AGV 实际系统时，不应改变 `WES` 内部控制流语义

补充说明：

- [third_party_integration_whitepaper.md](/Users/kaizhou/SynologyDrive/works/wes_backend/docs/third_party_integration_whitepaper.md) 当前版本未覆盖 AGV/RCS
- 本设计对 AGV 交互采用与白皮书一致的工程语义：`Command -> Ack -> Callback`、幂等、超时重试、异步回流
- 这部分是运行时编排设计，不代表已发布的 AGV 对外标准协议

## 1. 问题陈述

当前仓库已完成以下对齐：

- `SMT` 主链路可跑通：`SCAN_COMPLETED -> ARM01 -> PIPELINE01 -> ARM02`
- `ARM02` 出料命令的 `target_type` 已从旧值 `OUTPUT_PLATFORM` 修正为 `BIN`
- callback 契约、`step_code` 快照、`callback_logs/workline_inbox` 留痕已落地

但仍存在一个关键缺口：

- `ARM02` 出料命令还没有稳定的真实 `target_bin` 来源
- 未来应由 WMS 业务系统提供
- 当前 mock 阶段，由 mock 服务提供

因此，`MOVE_FORWARD SUCCESS` 之后不能再直接创建 `ARM02` 命令，而必须先走“库位分配”步骤。

## 2. 总体设计

### 2.1 目标主链路

OK 主链路调整为：

1. `INPUT_ARM` 上报 `SCAN_COMPLETED`
2. `ARM01` 执行输入抓取/检测
3. `PIPELINE01` 执行 `MOVE_FORWARD`
4. `WES` 调用库位分配正式接口
5. 分两种分支：
   - 若返回 `ALLOCATED`，创建 `ARM02` 出料命令
   - 若返回 `AGV_REQUIRED`，先请求 `AGV` 搬运货架到接料位
6. `AGV` 完成后，`WES` 再次调用库位分配接口
7. 拿到完整 `target_bin` 后创建 `ARM02` 命令
8. `ARM02` 回调成功，`session` 结束

### 2.2 职责边界

`plugin`

- 负责流程编排
- 负责定义何时进入库位分配、何时等待 AGV、何时允许出料
- 不负责硬编码最终 `target_bin`

`WES`

- 是库位分配和 AGV 请求的主动调用方
- 负责持久化分配请求、AGV 请求和最终 `target_bin`
- 负责在 `target_bin` 完整时才创建 `ARM02 DeviceCommand`

`ARM02`

- 只消费已决策完成的出料命令
- 不负责二次查询 bin，不负责向 WMS/AGV 自行拉取业务数据

`库位分配服务`

- 给出当前可执行的 `target_bin`
- 若当前无可用货架/料箱，明确返回 `AGV_REQUIRED`

`AGV 服务`

- 在 `WES` 请求后搬运可用货架到粗分机接料位
- 通过异步回调向 `WES` 返回执行结果

## 3. 运行时状态机设计

### 3.1 新增步骤

建议在 `smt_classifier` 契约中新增以下步骤：

- `WAITING_BIN_ALLOCATION`
- `WAITING_AGV_DELIVERY`

可选中间步骤：

- `BIN_ALLOCATED`
- `AGV_DELIVERED`

若当前不希望扩大步骤枚举，可仅保留前两个等待态，并将成功语义保存在 `context_json`。

### 3.2 Session 推进规则

`PIPELINE_MOVE_FORWARD SUCCESS`

- 不再直接创建 `ARM02` 命令
- 进入 `WAITING_BIN_ALLOCATION`

`BIN ALLOCATION = ALLOCATED`

- 将完整 `target_bin` 写入 `session.context_json`
- 创建 `ARM02 DeviceCommand`
- 进入 `OUTPUT_PICK_PLACE`

`BIN ALLOCATION = AGV_REQUIRED`

- 创建 `AGV` 请求
- 进入 `WAITING_AGV_DELIVERY`

`AGV RESULT = SUCCESS`

- 不直接创建 `ARM02`
- 再次进入库位分配调用

`AGV RESULT = FAILED`

- `session` 失败

`ARM02 RESULT = SUCCESS`

- `session` 进入 `COMPLETED`

## 4. 接口契约

### 4.1 库位分配正式接口

由 `WES` 主动调用。

职责：

- 根据当前工作线、物料属性、接料位状态返回可执行 `target_bin`
- 若当前无可用 bin，则明确返回 `AGV_REQUIRED`

建议请求字段：

```json
{
  "request_code": "ALLOC-20260329-0001",
  "workline_code": "WL-CONVEYOR-01",
  "business_key": "PKG1_20260329_OK_03",
  "barcode": "PKG1_20260329_OK_03",
  "reel_diameter": "15inch",
  "reel_thickness": "20",
  "inspection_result": "OK",
  "source_location": "STATION_PIPELINE1_OUTPUT1",
  "timestamp": 1774788311950
}
```

建议成功返回：

```json
{
  "code": 200,
  "message": "ALLOCATED",
  "data": {
    "allocation_status": "ALLOCATED",
    "target_bin": {
      "station_location_id": "STATION_OUTPUT1",
      "rack_id": "RACK_001",
      "bin_id": "BIN_104",
      "bin_type": "三格箱",
      "bin_cell_location": "1",
      "reel_layer": "15",
      "reel_thickness": "20",
      "reel_diameter": "15inch",
      "reel_totalthickness": "300"
    }
  }
}
```

建议需要 AGV 时返回：

```json
{
  "code": 200,
  "message": "AGV_REQUIRED",
  "data": {
    "allocation_status": "AGV_REQUIRED",
    "agv_request": {
      "request_code": "AGV-20260329-0001",
      "from_location": "RACK_BUFFER_A",
      "to_location": "STATION_OUTPUT1",
      "rack_type": "SMT_BIN_RACK",
      "reason": "NO_AVAILABLE_BIN"
    }
  }
}
```

### 4.2 AGV 正式接口

由 `WES` 主动调用。

职责：

- 请求 AGV 将可用货架搬运到粗分机接料位

建议请求字段：

```json
{
  "command_id": "AGV-20260329-0001",
  "task_type": "MOVE_RACK",
  "priority": 5,
  "timeout": 300000,
  "params": {
    "workline_code": "WL-CONVEYOR-01",
    "business_key": "PKG1_20260329_OK_03",
    "from_location": "RACK_BUFFER_A",
    "to_location": "STATION_OUTPUT1",
    "rack_type": "SMT_BIN_RACK",
    "reason": "NO_AVAILABLE_BIN"
  },
  "timestamp": 1774788312950
}
```

同步 ACK 沿用白皮书：

```json
{
  "code": 200,
  "message": "Accepted",
  "trace_id": "AGV-TRACE-001"
}
```

异步结果回调沿用白皮书 `callback/result` 语义：

```json
{
  "command_id": "AGV-20260329-0001",
  "device_id": "AGV_01",
  "result": "SUCCESS",
  "finish_time": 1774788342950,
  "data": {
    "to_location": "STATION_OUTPUT1"
  }
}
```

## 5. 失败策略

失败处理遵循 [third_party_integration_whitepaper.md](/Users/kaizhou/SynologyDrive/works/wes_backend/docs/third_party_integration_whitepaper.md) 已定义的通信原则：

- `WES` 调用第三方接口 10 秒未收到 HTTP 200，则按指数退避重试
- 重试间隔 `1s / 2s / 4s`
- 最多重试 3 次
- 必须保证请求幂等

### 5.1 库位分配接口失败

若 `WES` 在 10 秒内未收到 HTTP 200：

- 指数退避重试最多 3 次
- 最终失败则 `session` 进入失败态
- 不创建 `ARM02 DeviceCommand`

需区分以下失败类型：

- 网络超时
- HTTP 4xx 参数错误
- HTTP 5xx 服务异常
- 返回体业务失败

### 5.2 库位分配返回 `AGV_REQUIRED`

这不是失败，而是业务分支：

- `session` 进入 `WAITING_AGV_DELIVERY`
- 创建 `AGV` 请求
- 记录 `agv_request_code`

### 5.3 AGV 请求 ACK 失败

若 AGV 接口未在 10 秒内 ACK：

- 按 `1s / 2s / 4s` 最多重试 3 次
- 最终仍失败则 `session` 失败
- 不继续轮询 bin 分配

### 5.4 AGV 已 ACK 但回调超时

- `session` 保持 `WAITING_AGV_DELIVERY`
- 由 timeout 扫描生成超时事件
- 超时后进入失败或待人工介入
- 不自动无限重发 AGV 请求，避免重复搬运

### 5.5 AGV 成功后二次分配仍失败

- 允许一次 `AGV_REQUIRED -> AGV SUCCESS -> BIN ALLOCATION` 闭环
- 二次分配仍拿不到有效 `target_bin` 时，直接失败
- 不进入无限循环

### 5.6 ARM02 前置校验失败

若最终 `target_bin` 缺少以下关键字段，则 `WES` 不得创建 `ARM02 DeviceCommand`：

- `rack_id`
- `bin_id`
- `bin_type`
- `bin_cell_location`

建议同时校验：

- `reel_layer`
- `reel_thickness`
- `reel_diameter`
- `reel_totalthickness`
- `station_location_id`

### 5.7 幂等要求

以下请求必须具备全局唯一请求号，且重试时复用原值：

- `allocation request_code`
- `agv command_id`
- `arm02 command_code`

禁止出现以下情况：

- 重复发起 AGV 搬运
- 重复分配不同 bin
- 重复派发 `ARM02`

## 6. 数据持久化建议

最少保证以下信息可追踪：

- allocation 请求参数
- allocation 返回结果
- `agv_request_code`
- AGV 回调结果
- 最终 `target_bin`

若当前不引入新表，建议优先落到：

- `workline_sessions.context_json`
- `workline_inbox.payload_json`
- `device_commands.params`
- `callback_logs`

建议 `session.context_json` 至少包含：

```json
{
  "allocation_request_code": "ALLOC-20260329-0001",
  "allocation_status": "ALLOCATED",
  "agv_request_code": "AGV-20260329-0001",
  "target_bin": {
    "station_location_id": "STATION_OUTPUT1",
    "rack_id": "RACK_001",
    "bin_id": "BIN_104",
    "bin_type": "三格箱",
    "bin_cell_location": "1",
    "reel_layer": "15",
    "reel_thickness": "20",
    "reel_diameter": "15inch",
    "reel_totalthickness": "300"
  }
}
```

## 7. Mock 设计

当前 mock 阶段新增两个正式接口：

- allocation mock
- AGV mock

要求：

- 主链路不得依赖 `/debug/*`
- mock 服务只通过正式接口参与业务链路
- 调试行为通过环境变量或正式请求体控制

建议控制模式：

- `allocation_mode=allocated|agv_required|fail|timeout`
- `agv_mode=success|fail|timeout`

### 7.1 Allocation Mock

职责：

- 根据配置返回 `ALLOCATED` 或 `AGV_REQUIRED`
- 若为 `ALLOCATED`，返回完整 `target_bin`

### 7.2 AGV Mock

职责：

- 先对 `WES` 请求返回同步 ACK
- 再异步回调 `WES`
- 成功后不直接创建 `ARM02` 命令，由 `WES` 再次调用 allocation

## 8. 测试改造

### 8.1 单元测试

补以下场景：

- `MOVE_FORWARD SUCCESS` 后进入 `WAITING_BIN_ALLOCATION`
- allocation 返回 `ALLOCATED` 时才创建 `ARM02` 命令
- allocation 返回 `AGV_REQUIRED` 时进入 `WAITING_AGV_DELIVERY`
- `target_bin` 缺字段时拒绝创建 `ARM02`

重点文件：

- [test_plugin.py](/Users/kaizhou/SynologyDrive/works/wes_backend/tests/workline_plugins/smt_classifier/test_plugin.py)

### 8.2 API/Callback 测试

补以下场景：

- AGV 成功回调
- AGV 失败回调
- allocation/AGV 契约失败时返回明确 ACK 错误

重点文件：

- [test_callback_api.py](/Users/kaizhou/SynologyDrive/works/wes_backend/tests/api/test_callback_api.py)

### 8.3 Runtime/E2E 测试

补以下主链路：

1. `allocation = ALLOCATED`
   - `MOVE_FORWARD SUCCESS`
   - allocation 成功
   - 创建 `ARM02`
   - `ARM02 SUCCESS`
   - `session COMPLETED`

2. `allocation = AGV_REQUIRED`
   - `MOVE_FORWARD SUCCESS`
   - allocation 返回 `AGV_REQUIRED`
   - `WES -> AGV`
   - `AGV SUCCESS`
   - 再次 allocation 成功
   - `ARM02 SUCCESS`
   - `session COMPLETED`

3. 失败分支
   - allocation 网络失败
   - allocation 返回业务失败
   - AGV ACK 失败
   - AGV 回调超时
   - 二次 allocation 仍无有效 `target_bin`

重点目录：

- `tests/e2e/smt_classifier/`
- `tests/integration/workline_runtime/`

## 9. 代码改造清单

### 9.1 `plugin`

文件：

- [plugin.py](/Users/kaizhou/SynologyDrive/works/wes_backend/src/workline_plugins/smt_classifier/plugin.py)
- [contract.py](/Users/kaizhou/SynologyDrive/works/wes_backend/src/workline_plugins/smt_classifier/contract.py)

改造目标：

- 新增 `WAITING_BIN_ALLOCATION`
- 新增 `WAITING_AGV_DELIVERY`
- `MOVE_FORWARD SUCCESS` 后不直接派发 `ARM02`

### 9.2 runtime service

建议新增：

- `src/workline_runtime/services/bin_allocation_service.py`
- `src/workline_runtime/services/agv_dispatch_service.py`

改造目标：

- 封装第三方接口调用
- 统一重试、ACK 校验、错误映射

### 9.3 callback

文件：

- [callback.py](/Users/kaizhou/SynologyDrive/works/wes_backend/src/app/callback/v1/callback.py)

改造目标：

- 接入 AGV 结果回调
- 统一写入 inbox / callback log

### 9.4 mock

建议新增：

- `tests/mock/smt_classifier/allocation_mock.py`
- `tests/mock/smt_classifier/agv_mock.py`

改造目标：

- 提供正式接口，不依赖 `/debug/*`

## 10. 推荐实施顺序

1. 补 `plugin` 状态机与 allocation 编排入口
2. 补 allocation mock 和 allocation service
3. 补 AGV mock、AGV service、AGV 回调处理
4. 改 runtime/e2e 测试
5. 同步文档与 gap analysis

## 11. 验收标准

满足以下条件才算完成：

1. `MOVE_FORWARD SUCCESS` 后不会在无 `target_bin` 情况下直接创建 `ARM02`
2. allocation 返回 `ALLOCATED` 时，`ARM02` 命令中带完整 `target_bin`
3. allocation 返回 `AGV_REQUIRED` 时，`WES` 会先调 AGV，再重新 allocation
4. AGV / allocation 的失败、超时、重试均可在数据库中完整追踪
5. mock 主链路不依赖 `/debug/*`
6. runtime/e2e 测试覆盖 direct allocation 与 AGV 分支
