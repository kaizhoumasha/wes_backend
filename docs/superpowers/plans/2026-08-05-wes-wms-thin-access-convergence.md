# WES Phase 3 WMS HTTP Client 薄封装实施计划

> **Status:** `READY_FOR_IMPLEMENTATION`。
> Phase 3 只建设类似前端 Axios 的 WMS HTTP 访问封装，不实现任何具体 WMS 业务 API。

**Goal:** 在 Phase 2 `OutboundHttpTransport` 之上提供一个长期持有、统一配置、统一 JSON 请求/响应处理的
`WmsClient`，让后续业务开发只需声明具体 WMS API 的 path、DTO 和业务结果解释。

**Reference:** `/Users/kaizhou/codeDev/wes_frontend/src/api/client.ts` 与
`/Users/kaizhou/codeDev/wes_frontend/src/api/contract/client.ts`。

**Usage contract:** `docs/contracts/wms-northbound-interaction-contract.md`。

## 1. 阶段裁决

Phase 3 的定位与前端 API Client 相同：统一“怎么调用 WMS”，不定义“调用哪个业务 API”以及“业务结果意味着什么”。

Phase 3 只交付：

- 一个 `WmsClient`，统一 relative path、query、headers、JSON body 和 JSON response。
- `request` 以及当前 Phase 2 已支持的 `get`、`post` 便捷方法。
- 一个最小 factory，复用 Phase 2 builder，并把 `system_id` 固定为 `wms`。
- 一个进程内长期 Client 的显式 `aclose()` 生命周期。
- 一组只验证访问层行为的 FAST 测试。
- 一份指导后续开发者新增 WMS API 的最小示例。

WMS 仍然给出所有业务结果，WES 只做执行决策。但该权限规则由后续具体业务 API 和执行模块落实，不在 Phase 3
预建 Business Decision Port、业务 Outcome 或业务状态模型。

## 2. 明确不做

Phase 3 不包含：

- 任何库存、物料、货架、料箱、PickingTask、NG、确认或人工任务 API。
- `WmsBusinessQueryPort`、`WmsBusinessDecisionPort`、`WmsBusinessConfirmationPort`。
- operation 清单、消费者矩阵、业务 DTO、业务拒绝码或业务结果翻译。
- WMS inbound API、DTO、normalizer 或路由。
- RCS、AGV、CTU 的搬运、状态、取消或 `TransportTask`；这些属于 Phase 4。
- 数据库、Repository、Service、Migration、evidence、retry、breaker、reconciliation、cache 或分页。
- Provider、Profile、Registry、动态发现、代码生成、配置驱动 API 或兼容层。
- HMAC、凭据、IP allowlist 等当前局域网部署不需要的认证设计。
- 生产消费者切换、旧 `src/app/wms_integration/` 修改或删除；统一切换仍由后续阶段负责。

业务 API 未确认不会阻断 Phase 3。具体 WMS API 只在对应业务进入开发时按真实合同实现。

## 3. 最小架构

```text
后续业务模块
  └─ 具名 WMS API 方法 + 该 API 的 request/response DTO + 业务结果解释
       └─ WmsClient.request/get/post
            └─ Phase 2 OutboundHttpTransport.send
```

职责只有两层：

| 层 | 负责 | 不负责 |
| --- | --- | --- |
| Phase 3 `WmsClient` | WMS origin、超时、HTTP 请求构造、JSON 编解码、传输事实保留、资源关闭 | 业务字段校验、业务拒绝解释、执行状态推进 |
| 后续业务模块 | 固定 path、业务 DTO、业务结果解释、业务合同测试 | HTTP Client、连接池、超时和传输失败分类 |

Phase 3 不再增加一层 Gateway 或三类业务 Port。`WmsClient` 本身就是唯一共享访问入口。

## 4. 目标代码边界

目标生产文件保持最小：

```text
src/app/wms_adapter/
├── __init__.py
├── client.py
└── factory.py
```

- `client.py`：`WmsClient`、最小 JSON 访问结果、JSON 编解码以及对 Phase 2 Transport 的一次调用。
- `factory.py`：构造长期 `WmsClient`，固定 `system_id="wms"`。
- `__init__.py`：只导出后续业务开发需要的公开入口。

不得提前创建 `apis/`、`operations/`、`ports/`、`models/`、`services/`、`repositories/` 或 `registry/`。
未来具体业务代码放在哪里，由该业务实施计划根据真实 owner 决定；Phase 3 不为未知业务预建目录。

## 5. WmsClient 最小合同

`WmsClient` 只提供以下能力：

- `request(method, path, *, query, headers, json)`：统一入口，method 仅允许 Phase 2 当前支持的 `GET`、`POST`。
- `get(path, *, query, headers)`：`request` 的薄便捷方法。
- `post(path, *, query, headers, json)`：`request` 的薄便捷方法。
- `aclose()`：释放内部长期 Transport；重复关闭保持幂等。

调用规则：

1. path 必须是 relative path；origin 只能由 factory 注入。
2. POST JSON 统一编码为 UTF-8，并设置 `Content-Type: application/json`。
3. 每次调用最多执行一次 Phase 2 `send`，不自动 retry。
4. 完整响应只做一次 JSON 解码；非 2xx 仍返回状态码和 JSON，由具体业务模块解释。
5. 未发送、交付未知和响应阶段失败必须保留 Phase 2 `delivery_state` / `failure_kind`，不得改写为业务失败。
6. 不接受数据库 Session、Repository、业务上下文或 operation 名称。

只在真实业务合同需要 Phase 2 尚未支持的 HTTP method、非 JSON body 或特殊 header 时，再通过独立变更扩展；本阶段不预留。

## 6. 后续新增 WMS API 标准

新增一个具体 WMS API 时，开发者只做以下五步：

1. 在对应业务模块定义具名方法以及该 API 的 request/response DTO。
2. 使用固定 relative path 调用 `WmsClient.get()` 或 `WmsClient.post()`。
3. 在业务模块内解释 WMS 返回的成功、拒绝和业务字段；WMS 是业务结果权威。
4. 为该 API 编写合同测试，并使用双方确认的 fixture/schema；不得用 Phase 3 Client 测试替代业务合同测试。
5. 只在出现真实重复后提取共享业务 helper；不得修改 `WmsClient` 来承载业务语义。

指导示例只表达结构，不承诺真实 WMS API：

```text
ExampleWmsApi.query_example(request DTO)
  → WmsClient.post("/example/query", json=request DTO)
  → response DTO 校验
  → 返回 WMS 给出的业务结果
```

禁止把示例 path、字段或返回值接入生产，也禁止把示例扩展为 registry、基类体系或 API 生成器。

## 7. Implementation Tasks

### Task 1：建立访问层合同测试

按 TDD 建立 `tests/contracts/wms_adapter/` 下的最小测试：

- GET query 和 POST JSON 的请求映射。
- relative path、UTF-8 JSON 与默认 Content-Type。
- 每次调用 0/1 次 send。
- 完整响应 JSON 解码及非 2xx 原样返回。
- `NOT_SENT`、`DELIVERY_UNKNOWN` 和响应阶段失败事实保留。
- 取消传播、幂等关闭和关闭失败传播。

不测试任何库存、PickingTask、NG、业务拒绝或插件执行行为。

### Task 2：实现 WmsClient

- 建立 `client.py`，使 Task 1 测试通过；不把薄 Client 拆成额外 Gateway 或合同层。
- 直接消费 Phase 2 公共合同，不导入 `httpx`。
- 保持无状态，不访问数据库，不保存调用证据。

### Task 3：实现 factory 与公开导出

- factory 只接收 `base_url` 和 `timeout_seconds`，并固定 `system_id="wms"`。
- 每次 factory 调用构造一个长期 Client；生产 Composition Root 的接线不在本阶段。
- `__init__.py` 只导出 `WmsClient`、最小访问结果和 factory。

### Task 4：验证示例与架构边界

- 在 WMS Client 使用合同中保留一份短示例，不新增演示业务生产模块。
- 验证新包零业务 API、零数据库、零旧 WMS 包、零 RCS/AGV/CTU、零认证和零动态机制。
- 运行 WMS Client FAST 测试、Phase 2 回归、Ruff、类型检查、Import Linter 和 quality profile。

## 8. 实施准入与退出标准

Phase 2 已交付 `OutboundHttpTransport`、`OutboundHttpRequest`、`OutboundHttpResult` 和 builder，Phase 3 所需依赖已满足。
具体 WMS 业务 API、消费者矩阵、PickingTask wire 或业务尺寸预算都不属于 Phase 3 入口条件。

因此 Phase 3 当前可以进入实施。

退出标准：

- 目标三个生产文件存在，公开面符合第 5 节。
- 所有访问层测试通过，并且没有业务断言。
- 新包只依赖 Phase 2 `src/core/outbound_http/`。
- 无业务 API、业务 Port、DTO registry、持久化、retry、breaker、分页、认证或生产接线。
- 后续开发者可以按第 6 节在不修改 Phase 3 核心的情况下增加一个具体 WMS API。

**VERDICT：Phase 3 范围已收敛为 Axios 式 WMS HTTP Client，可进入实施；具体业务 API 随后续业务逐项开发。**
