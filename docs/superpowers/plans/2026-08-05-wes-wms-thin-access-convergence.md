# WES Phase 3 WMS HTTP Client 薄封装实施计划

> **Status:** `IMPLEMENTED`（2026-08-07）。
> Phase 3 只建设类似前端 Axios 的 WMS HTTP 访问封装，不实现任何具体 WMS 业务 API。

**Goal:** 在 Phase 2 `OutboundHttpTransport` 之上提供一个长期持有、统一配置、统一 JSON 请求/响应处理的
`WmsClient`，让后续业务开发只需声明具体 WMS API 的 path、DTO 和业务结果解释。

**Reference:** `/Users/kaizhou/codeDev/wes_frontend/src/api/client.ts` 与
`/Users/kaizhou/codeDev/wes_frontend/src/api/contract/client.ts`。

**Usage contract:** `docs/contracts/wms-northbound-interaction-contract.md`。

## 1. 阶段裁决

Phase 3 的定位与前端 API Client 相同：统一“怎么调用 WMS”，不定义“调用哪个业务 API”以及“业务结果意味着什么”。

Phase 3 只交付：

- 一个 `WmsClient`，统一 relative path、query、headers、严格 JSON body 和 JSON response，并区分空响应与 JSON `null`。
- `request` 以及当前 Phase 2 已支持的 `get`、`post` 便捷方法。
- 一个最小 factory，复用 Phase 2 builder，并把 `system_id` 固定为 `wms`。
- 一个由运行时/事件循环 owner 长期持有的 Client 及显式 `aclose()` 生命周期，不跨事件循环共享。
- 一组只验证访问层行为的 FAST 测试。
- 一份指导后续开发者新增 WMS API 的最小示例。

WMS 仍然给出所有业务结果，WES 只做执行决策。但该权限规则由后续具体业务 API 和执行模块落实，不在 Phase 3
预建 Business Decision Port、业务 Outcome 或业务状态模型。

## 2. NOT in scope（明确不做）

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

`WmsClient` 只提供以下异步能力；唯一结果类型命名为不可变 `WmsAccessResult`：

- `request(method, path, *, query, headers, json)`：统一入口，method 使用 Phase 2 枚举且仅允许当前支持的 `GET`、`POST`；
  内部缺省标记负责区分“未提供 JSON”与 JSON `null`。
- `get(path, *, query, headers)`：`request` 的薄便捷方法。
- `post(path, *, query, headers, json)`：`request` 的薄便捷方法。
- `aclose()`：释放内部长期 Transport；重复关闭保持幂等。

`query` 与 `headers` 的类型固定为可选 `Mapping[str, str]`，默认 `None`；Client 只转换为 Phase2 string-pair tuple，值校验
仍由 Phase2 完成。若真实 API 需要重复 query key，再按该 API 合同独立扩展。
`WmsAccessResult` 字段固定为：Phase2 的 `delivery_state` / `failure_kind`、`status_code: int | None`、
`response_headers: tuple[tuple[str, str], ...]`、`body_present: bool`、合法 JSON 值或 `None` 的 `json_body`，以及
`json_failure: Literal["INVALID_UTF8", "INVALID_JSON"] | None`。递归 JSON 类型别名保持模块私有，不增加第三个公开类型。

调用规则：

1. path 必须是 relative path；origin 只能由 factory 注入。
2. GET 必须省略 JSON；POST 的 `json` 必传，`None` 表示 JSON `null`。调用方不得以任意大小写传入
   `Content-Type`；GET 不自动添加，POST 固定为 `application/json`。
3. JSON 值域只允许 `None`、布尔、整数、有限浮点、字符串、上述值组成的 list，以及字符串 key 的 dict；拒绝 tuple、
   非字符串 key、`NaN`、正负 `Infinity` 和其他对象。POST 使用无 BOM UTF-8 紧凑编码。
4. 每次调用最多执行一次 Phase 2 `send`，不自动 retry。
5. 完整响应只做一次严格 UTF-8/RFC 8259 JSON 解码；响应 Content-Type 不参与共享 Client 解码。非 2xx 仍返回状态码
   和 JSON，由具体业务模块解释。
   最小访问结果以 `body_present` 区分空响应（`False/None`）与 JSON `null`（`True/None`）；纯空白 body 和非标准
   `NaN`/`Infinity` 均记为 `INVALID_JSON`。非法 UTF-8/JSON 不抛弃已收到的 status、headers 和交付事实，而是在同一
   结果中设置 `body_present=True`、`json_body=None`、`json_failure`。
6. 未发送、交付未知和响应阶段失败必须保留 Phase 2 `delivery_state` / `failure_kind`，并返回
   `body_present=False`、`json_body=None`；不得改写为业务失败，也不得触发 JSON 解码。
7. `CancelledError`、关闭异常和 Phase 2 关闭后调用异常原样传播；Client 不复制 Transport 关闭状态。关闭后的合法请求
   调用一次 `send` 并由 Transport 在底层 HTTP 前拒绝；值域或编码错误调用零次 `send`。
8. 不接受数据库 Session、Repository、业务上下文或 operation 名称。

只在真实业务合同需要 Phase 2 尚未支持的 HTTP method、非 JSON body 或特殊 header 时，再通过独立变更扩展；本阶段不预留。

## 6. 后续新增 WMS API 标准

新增一个具体 WMS API 时，开发者只做以下五步：

1. 在对应业务模块定义具名方法以及该 API 的 request/response DTO。
2. request DTO 先用 `model_dump(mode="json")` 转成标准 JSON 值，再使用固定 relative path 调用 `WmsClient.get()` 或
   `WmsClient.post()`；Client 不依赖 Pydantic。
3. 先检查 `failure_kind`、`json_failure` 与 `body_present`，再使用业务 response DTO `model_validate(...)` 校验合法 JSON，
   并解释 WMS 返回的成功、拒绝和业务字段；WMS 是业务结果权威。
4. 为该 API 编写合同测试，并使用双方确认的 fixture/schema；不得用 Phase 3 Client 测试替代业务合同测试。
5. 只在出现真实重复后提取共享业务 helper；不得修改 `WmsClient` 来承载业务语义。

指导示例只表达结构，不承诺真实 WMS API：

```text
ExampleWmsApi.query_example(request DTO)
  → request DTO.model_dump(mode="json")
  → WmsClient.post("/example/query", json=标准 JSON 值)
  → 检查 result.failure_kind / json_failure / body_present
  → response DTO.model_validate(result.json_body)
  → 返回 WMS 给出的业务结果
```

禁止把示例 path、字段或返回值接入生产，也禁止把示例扩展为 registry、基类体系或 API 生成器。

## 7. Implementation Tasks

- [x] **T1（P1，human: ~2h / Codex: ~20min）— 访问合同测试**
  - 来源：测试评审——必须覆盖第 9 节的每条分支，不能用基础 Transport 或具体业务测试替代。
  - 文件：`tests/contracts/wms_adapter/test_client.py`、`tests/contracts/wms_adapter/test_factory.py`。
  - 动作：按 TDD 先建立 GET/POST body 约束、query/header 默认值与重复项、Content-Type 所有权、严格 JSON 值域/编码、
    循环输入、0/1 次 send、空响应与 JSON `null`、响应 Content-Type、非 2xx、解码失败事实、所有 Phase2 失败事实、
    取消和关闭测试；不含业务断言。
  - 验证：新增 FAST 测试先红；实施后执行 `uv run pytest tests/contracts/wms_adapter -q`。
- [x] **T2（P1，human: ~3h / Codex: ~30min）— 实现两类型薄 Client**
  - 来源：架构与代码质量评审——统一 JSON 访问但不得复制 Phase2 或引入业务抽象。
  - 文件：`src/app/wms_adapter/client.py`。
  - 动作：实现 `WmsClient` 与一个不可变最小访问结果，直接消费 Phase2 公共合同，不导入 `httpx`、不访问数据库、
    不保存调用证据、不增加 Gateway/异常体系。
  - 验证：T1 全绿；每条有效调用最多一次 `send`，编码失败零次，关闭后合法请求一次并由 Transport 拒绝。
- [x] **T3（P2，human: ~1h / Codex: ~10min）— 实现 factory 与公开导出**
  - 来源：架构评审——长期 Client 生命周期与固定 WMS 身份必须只有一个构造入口。
  - 文件：`src/app/wms_adapter/factory.py`、`src/app/wms_adapter/__init__.py`。
  - 动作：factory 只接收 `base_url`、`timeout_seconds` 并固定 `system_id="wms"`；只导出两个公开类型和 factory。
  - 验证：factory FAST 测试证明每次构造一个长期 Client、零裸 `httpx`、配置原样交给 Phase2 builder。
- [x] **T4（P2，human: ~1h / Codex: ~10min）— 完成边界与质量验证**
  - 来源：工程退出评审——基础、Adapter 与业务能力必须分别证明。
  - 文件：上述生产文件与测试、`docs/architecture/heavy-test-impact.toml`、`tests/scripts/`；不新增演示业务生产模块。
  - 动作：核对零业务 API、零数据库、零旧 WMS 包、零 RCS/AGV/CTU、零认证、零动态机制；执行 Phase2 回归、
    Ruff、类型检查、Import Linter 和 quality profile；为 `src/app/wms_adapter/**` 增加经评审的 HEAVY 显式 NONE，证明该
    未接线、无持久化 Client 不触发真实 HEAVY 套件。
  - 验证：`uv run pytest tests/core/outbound_http tests/contracts/wms_adapter tests/scripts -q`、
    `uv run scripts/select_heavy_tests.py --scope unstaged` 与仓库质量门禁全部通过。

## 8. What already exists（复用清单）

| 现有能力 | 真实位置 | Phase 3 裁决 |
| --- | --- | --- |
| 框架无关请求与结果合同 | `src/core/outbound_http/contracts.py` | 直接复用 path/query/header 校验和传输事实，不复制 |
| 长期异步 Transport factory | `src/core/outbound_http/factory.py` | 由 WMS factory 固定 `system_id="wms"` 后调用 |
| 单次发送、响应预算与关闭语义 | `src/core/outbound_http/transport.py` | 只委托，不增加 retry、连接池或关闭状态 |
| Phase2 基础测试 | `tests/core/outbound_http/` | 只作基础回归，不用于证明 WMS Client 合同 |
| 前端共享 Client 模式 | `/Users/kaizhou/codeDev/wes_frontend/src/api/client.ts` | 只参考“共享 Client + 便捷方法”；不复制前端认证/错误处理 |

旧 `src/app/wms_integration/` 不是实现模板或兼容目标；Phase 3 不修改它，也不让新测试依赖它。

## 9. 测试覆盖图与失败模式

实现与合同测试已经完成；下图记录当前单模块 FAST 覆盖，Phase 3 不需要 E2E。

```text
CODE PATHS                                              DEVELOPER FLOW
[+] WmsClient.request/get/post                          [+] 新增具名 WMS API
  ├─ [TESTED ★★★] GET 无 body / POST 必须有 json         ├─ [DOCUMENTED ★★★] DTO model_dump(mode="json")
  ├─ [TESTED ★★★] relative path/query/header 默认与重复   ├─ [DOCUMENTED ★★★] 固定 path 调用 get/post
  ├─ [TESTED ★★★] Content-Type 冲突在发送前拒绝           ├─ [DOCUMENTED ★★★] status + JSON 交给业务解释
  ├─ [TESTED ★★★] JSON 递归值域合法                       └─ [DOCUMENTED ★★★] response DTO model_validate
  ├─ [TESTED ★★★] tuple/非字符串 key/非有限数/对象/循环拒绝
  ├─ [TESTED ★★★] UTF-8 紧凑编码 → 一次 send
  ├─ [TESTED ★★★] Phase2 failure → body_present=False
  ├─ [TESTED ★★★] 空 body → False/None
  ├─ [TESTED ★★★] JSON null → True/None
  ├─ [TESTED ★★★] 合法 JSON（含非 2xx，忽略 Content-Type）→ True/value
  ├─ [TESTED ★★★] 空白/非法 UTF-8/非法 JSON/非标准数值 → json_failure + 保留 HTTP 事实
  └─ [TESTED ★★★] 取消/关闭异常/关闭后调用原样传播
[+] build_wms_client / aclose
  ├─ [TESTED ★★★] 固定 system_id="wms" 并透传配置
  └─ [TESTED ★★★] 长期持有、重复关闭幂等、关闭失败传播

CURRENT COVERAGE: 上述访问层合同均已通过 FAST 测试 | E2E: 0 | EVAL: 0
```

| 生产失败方式 | 测试 | 处理合同 | 调用方可见性 |
| --- | --- | --- | --- |
| DNS/连接/超时导致未发送或交付未知 | T1 注入各 Phase2 failure | 保留传输事实，body 不存在 | 明确结果，不静默 |
| WMS 返回非 2xx 但合法 JSON 或空 body | T1 覆盖两类 body | 返回访问结果，不作业务判断 | 业务模块可见 status/body |
| WMS 返回非法 UTF-8、非法 JSON 或非标准数值 | T1 分别触发 | 返回 `json_failure` 并保留 HTTP/交付事实 | 明确结果，不伪装业务拒绝 |
| 调用方提交非 JSON DTO、非字符串 key 或非有限数 | T1 覆盖值域边界 | `send` 前拒绝 | 明确编码/值域错误 |
| 关闭期间取消、关闭失败或关闭后调用 | T1 覆盖生命周期 | 原样传播 Phase2 行为 | 明确异常，不静默 |

无“无测试、无处理且静默”的关键缺口。

## 10. Worktree 与实施顺序

Sequential implementation, no parallelization opportunity。T1–T4 都围绕同一 `wms_adapter` 小模块和同一合同，拆分 worktree
只会增加合同与测试冲突。顺序为 `T1（RED）→ T2 → T3（GREEN）→ T4（REFACTOR/VERIFY）`。

## 11. 实施准入与退出标准

Phase 2 已交付 `OutboundHttpTransport`、`OutboundHttpRequest`、`OutboundHttpResult` 和 builder，Phase 3 所需依赖已满足。
具体 WMS 业务 API、消费者矩阵、PickingTask wire 或业务尺寸预算都不属于 Phase 3 入口条件。

因此 Phase 3 已完成实施并满足退出标准。

退出标准：

- 目标三个生产文件存在，公开面符合第 5 节。
- `WmsClient` 与最小不可变访问结果是仅有的两个新增公开类型；结果包含 `body_present`，不增加独立合同文件。
- 所有访问层测试通过，并且没有业务断言。
- 新包只依赖 Phase 2 `src/core/outbound_http/`。
- 无业务 API、业务 Port、DTO registry、持久化、retry、breaker、分页、认证或生产接线。
- 后续开发者可以按第 6 节增加符合当前共享 JSON 合同的具体 WMS API。不同响应预算、非 JSON body、额外 method 或
  特殊 header 仍按真实需求独立扩展，不承诺 Client 永不变化。
- `src/app/wms_adapter/**` 已有 HEAVY selector 精确 mapping；本阶段因未接线、无持久化而使用显式 NONE，并由 selector
  合同测试证明不会 fail closed。

## 12. 工程评审结论

- **Scope Challenge：** 按已批准目标保持 Phase3 独立薄 Client；拒绝等待首个业务 API 才提取的建议，因为这会取消本阶段
  明确要建立的开发标准，但不借机增加任何具体业务 API。
- **Architecture Review：** 3 项问题已关闭——空响应/JSON `null`、解码失败时传输事实保留、运行时/事件循环 owner 生命周期。
- **Code Quality Review：** 5 项问题已关闭——严格 JSON 值域、公开签名与结果字段、Content-Type 所有权、DTO 转换示例、
  Master Plan 的 `contracts.py` 双真源。
- **Test Review：** 覆盖图已生成，3 类缺口已补入计划——请求/响应边界、失败与关闭路径、HEAVY selector 显式 NONE。
- **Performance Review：** 无新增问题。Phase2 已提供长期连接池、有界响应和一次发送；本阶段无数据库、N+1、cache 或聚合。
- **Failure modes：** 无未测试、无处理且静默的关键缺口。
- **TODOS.md：** 无新增项；不同响应预算、非 JSON body、额外 method 和特殊 header 只有在真实 API 需要时才形成独立任务，
  当前写入 TODO 会制造推测性 backlog。
- **Outside Voice：** Codex 只读挑战提出 8 点；事实保留、selector、生命周期、公开合同和覆盖精度已吸收，取消 Phase3 与提前
  暴露每 API 预算/Transport 并发控制的建议按既定范围和 YAGNI 拒绝。
- **Parallelization：** 1 条顺序 lane，无适合并行的独立 worktree。
- **Prior learning applied：** `outbound-http-fact-boundary`（10/10）——基础 Transport 与 WMS Client 只表达技术事实，业务
  结果解释留在未来具体业务 owner。

**VERDICT：Phase 3 Axios 式 WMS HTTP Client 已完成实施；具体业务 API 随后续业务逐项开发。**

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | 本次后端薄封装不需要产品范围复审 |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | — | 未运行 diff 级代码复审 |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR | 11 issues closed, 0 critical gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | 无 UI 范围 |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | 开发体验已纳入本次工程评审 |

**CODEX:** 外部计划挑战提出 8 点；可执行缺口已回写，取消既定 Phase3 与推测性扩展建议被拒绝。

**CROSS-MODEL:** 独立设计评审与 Codex 均确认两类型/三生产文件边界可行；Codex 额外发现 JSON 解码事实和 HEAVY
selector 缺口，均已关闭。

**VERDICT:** SHIP READY — Phase3 implemented and verified。

NO UNRESOLVED DECISIONS
