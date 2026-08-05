# WES Phase 2 Outbound HTTP 传输基础能力实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: 实施时使用 `superpowers:executing-plans`，逐任务执行并在每个行为变更前遵循
> `superpowers:test-driven-development`。本文使用复选框追踪；不得并行修改相互依赖的 contracts、transport 与 factory。

**Goal:** 在不切换任何生产消费者的前提下，以四个生产文件交付框架无关、单次发送、响应有界且生命周期明确的
Outbound HTTP 基础能力，供后续 WMS/RCS/ECS Adapter 显式消费。

**Architecture:** `contracts.py` 定义不暴露 HTTPX 的不可变请求、响应限制、传输事实和窄 `Protocol`；`transport.py`
用一个进程内长生命周期 `httpx.AsyncClient` 完成一次发送、阶段化错误归类和有界响应读取；`factory.py` 只负责验证
base URL/Timeout 并构造 Transport。Phase 2 不包含认证、重试、业务状态解释、生产 fake、registry 或真实 Adapter 装配。

**Tech Stack:** Python 3.13、HTTPX、`asyncio.timeout_at`、Pytest 9、Ruff、Import Linter。

**Status:** Completed — 2026-08-05 生产实现、核心测试、架构门禁和 Phase 2 退出门禁全部完成。

## Global Constraints

- 系统未发布，不保留旧 API、旧配置、兼容 shim、alias、re-export、fallback 或迁移能力。
- 严格遵守 DRY、KISS、SOLID、YAGNI；当前真实 outbound 合同无认证要求，因此不实现或预留认证能力。
- Phase 2 只新增基础能力，不修改、搬迁、删除或接入任何既有 WMS/RCS/ECS、Outbox、DeviceCommand 或 Composition Root。
- 核心只验证基础传输事实；厂商 method/path/DTO/认证/业务拒绝和业务插件推进均不得进入本阶段测试。
- 代码行为采用 TDD；本文档变更本身不新增或修改任何测试。
- 项目命令统一使用 `uv run ...`；修改任何函数、类、方法前先执行 GitNexus upstream impact analysis。

---

## 1. 复审裁决

### 1.1 采用方案：四文件精简基础层

```text
Composition Root（Phase 3/6/7）
  └─> build_outbound_http_transport(system_id, base_url, timeout_seconds)
       └─> OutboundHttpTransport
            ├─> 一次 send
            ├─> 有界读取与解码
            ├─> 传输事实分类
            └─> 显式 aclose

Adapter（Phase 3/6/7）
  ├─> 拥有 method/path/wire DTO
  ├─> 解释所有 HTTP status 和业务响应
  └─> 只依赖框架无关合同，不可见 httpx Client
```

选择理由：它是满足“共享 Client 生命周期、单次发送、传输事实、有界响应”目标的最小闭包；四个生产文件各有单一职责，
不把当前 `src/app/sys` 或 WMS 业务身份带入 `src/core`。

不采用：

1. **通用认证平台。** 当前 WMS outbound 合同和现有硬件 outbound 调用均无认证要求；`AuthStrategy`、凭据解析、HMAC、
   Clock、Nonce、BASIC 或未来 seam 均属于推测性设计。
2. **搬迁现有旧模块。** `external_http_*`、`canonical_dispatch.py` 和 WMS transport 混有 Provider、Outbox、业务结果、
   retry safety 或 WMS 签名语义；搬迁会把旧平台身份提升为新基础合同。
3. **同阶段切换生产消费者。** 会把基础能力与 WMS/设备业务交接绑成不可独立评审的大变更，并破坏十阶段原子门禁。

### 1.2 核心合同

公开符号限定为：

- `OutboundHttpMethod`：只包含当前合同实际使用的 `GET`、`POST`；不为未来协议预建其他 method。
- `OutboundHttpRequest`：不可变；持有 method、相对 path、有序 query、有序 headers、bytes body 和响应预算；repr 不显示
  headers/body。
- `OutboundHttpResponseLimits`：持有正数 header count/header wire bytes/chunk/wire/decoded/压缩比预算及允许的
  content encoding 闭集。Header 默认且硬上限固定为 64 个字段、16,384 wire bytes；调用方只能收紧，不能放大。
- `OutboundHttpDeliveryState`：仅 `NOT_SENT`、`DELIVERY_UNKNOWN`、`RESPONSE_RECEIVED`。
- `OutboundHttpFailureKind`：成员严格限定为 `POOL_TIMEOUT`、`CONNECT_TIMEOUT`、`CONNECT_ERROR`、`WRITE_TIMEOUT`、
  `WRITE_ERROR`、`READ_TIMEOUT`、`READ_ERROR`、`REMOTE_PROTOCOL_ERROR`、`TOTAL_TIMEOUT`、
  `RESPONSE_HEADER_LIMIT_EXCEEDED`、`RESPONSE_METADATA_INVALID`、`RESPONSE_CHUNK_LIMIT_EXCEEDED`、
  `RESPONSE_WIRE_LIMIT_EXCEEDED`、`RESPONSE_DECODED_LIMIT_EXCEEDED`、`RESPONSE_COMPRESSION_RATIO_EXCEEDED`、
  `RESPONSE_CONTENT_ENCODING_UNSUPPORTED`、`RESPONSE_CONTENT_ENCODING_INVALID`、`RESPONSE_CLEANUP_FAILED`。
- `OutboundHttpResult`：持有 delivery state、可选 status code、不可变 response headers、有序重复 Header、可选
  `decoded_body` 和可选 failure kind；repr 不显示 headers/body。response headers 固定为
  `tuple[tuple[str, str], ...]`，保持 wire 到达顺序和重复项，不使用 `dict` 或 `httpx.Headers`。
- `OutboundHttpTransport`：窄 `Protocol`，只暴露异步 `send(request)` 与 `aclose()`。
- `OutboundHttpRequestError`：请求值不合法时在发送前抛出。
- `OutboundHttpClosedError`：Transport 关闭后的 send 明确抛出。
- `build_outbound_http_transport`：简单构造函数，只接收稳定、非敏感的 `system_id`、base URL 和正数 timeout，返回已可使用的
  Transport；`system_id` 只用于日志、不从 URL 推导，并满足 `[a-z][a-z0-9_-]{0,63}`。

合同约束：

- path 必须是以 `/` 开头且不含 scheme/host/query/fragment/控制字符的相对路径；base URL 必须只有 scheme、host、可选 port
  和根路径，消除 URL merge 歧义。
- query key/value 保留输入顺序和重复项，并拒绝 CR/LF/NUL；编码只由包内 HTTPX 实现一次。
- headers 名和值必须单行，名称按大小写不敏感去重；不提供自动签名或隐式业务 Header。
- response Header 预算按底层 raw name/value 的 wire bytes 与字段数量计算；预算通过后再转成有序不可变字符串对，重复 Header
  必须保留。
- 所有 HTTP status 都是 `RESPONSE_RECEIVED`；2xx/3xx/4xx/5xx 含义完全由 Adapter 解释。
- pool/connect 失败为 `NOT_SENT`；write/read/远端协议失败为 `DELIVERY_UNKNOWN`；无法从 HTTPX 明确识别内部阶段的外层总超时
  一律保守归为 `DELIVERY_UNKNOWN`。收到响应头后的 Header/body 有界读取、解码或总超时仍为 `RESPONSE_RECEIVED`，但携带
  稳定 failure kind。
- `decoded_body` 只包含完成 content-encoding 解码后的 bytes，不做字符集或 JSON 解码；raw wire body 不进入公开结果。
- 只转换明确列出的 HTTPX/有界响应异常；`CancelledError` 和未知编程异常必须原样传播。
- Transport 不重试；不输出 `safe_to_retry`、`protocol_result`、业务错误码或持久化 evidence。

结果合法状态矩阵：

| delivery state | status/headers | decoded body | 允许的 failure kind |
| --- | --- | --- | --- |
| `NOT_SENT` | 必须为空 | 必须为空 | 只允许 pool/connect 三类 |
| `DELIVERY_UNKNOWN` | 必须为空 | 必须为空 | 只允许 write/read/remote protocol/外层 send total timeout |
| `RESPONSE_RECEIVED` 成功 | status 必填、headers 为可为空的 tuple | bytes 必填，允许 `b""` | 必须为空 |
| `RESPONSE_RECEIVED` 处理失败 | status 必填；Header 超限时 headers 为空，否则保留 | 必须为空 | 只允许 read/remote protocol/total timeout/response/cleanup 类 |

已知异常映射严格冻结为：

| 发生位置/异常 | delivery state | failure kind |
| --- | --- | --- |
| `httpx.PoolTimeout` | `NOT_SENT` | `POOL_TIMEOUT` |
| `httpx.ConnectTimeout` | `NOT_SENT` | `CONNECT_TIMEOUT` |
| `httpx.ConnectError` | `NOT_SENT` | `CONNECT_ERROR` |
| `httpx.WriteTimeout` / `httpx.WriteError` | `DELIVERY_UNKNOWN` | 对应 write kind |
| response 尚未创建时的 `httpx.ReadTimeout` / `ReadError` / `RemoteProtocolError` | `DELIVERY_UNKNOWN` | 对应 read/protocol kind |
| response 尚未创建时的外层 `TimeoutError` | `DELIVERY_UNKNOWN` | `TOTAL_TIMEOUT` |
| response 已创建后的 read/protocol/外层 total timeout | `RESPONSE_RECEIVED` | 对应 read/protocol/total kind |
| `HttpResponseContractError` | `RESPONSE_RECEIVED` | `RESPONSE_METADATA_INVALID` |
| `HttpChunkBudgetExceeded` / `HttpWireBudgetExceeded` | `RESPONSE_RECEIVED` | 对应 chunk/wire kind；先匹配子类 |
| `HttpDecodedBodyBudgetExceeded` / `HttpCompressionRatioExceeded` | `RESPONSE_RECEIVED` | 对应 decoded/ratio kind |
| `HttpUnsupportedContentEncoding` / `HttpContentEncodingFailure` | `RESPONSE_RECEIVED` | 对应 unsupported/invalid kind |
| cleanup 超时或异常且无其他主失败 | `RESPONSE_RECEIVED` | `RESPONSE_CLEANUP_FAILED` |

`httpx.LocalProtocolError`、请求构造缺陷和未列出的异常不转换，原样传播，避免把代码错误伪装成远端失败。

### 1.3 生命周期与性能

- 每次 builder 调用创建一个 Transport 和一个 `httpx.AsyncClient`；后续 Composition Root 对每个外部系统、每个进程只调用
  一次 builder。
- Client 使用 `trust_env=False`、禁止自动重定向、HTTPX 标准连接池和 connect/read/write/pool timeout；不暴露 PID、event-loop
  guard、网络信任模式、limits 调参矩阵或全局 singleton。
- 一次 send 使用绝对总 deadline 包围 HTTP send、Header/body 读取与解码，不覆盖最终 cleanup。`AsyncClient.send()` 内触发的外层
  deadline 无法可靠区分 connect/write 时，统一返回 `DELIVERY_UNKNOWN + TOTAL_TIMEOUT`；response 已赋值后的 deadline 返回
  `RESPONSE_RECEIVED + TOTAL_TIMEOUT`。
- response cleanup 使用 `min(timeout_seconds, 1.0)` 的独立有界预算，并通过 shield 确保调用方取消时仍尝试关闭；清理结束后
  `CancelledError` 原样传播。cleanup 是唯一失败时返回 `RESPONSE_RECEIVED + RESPONSE_CLEANUP_FAILED`；已有主失败时保留主
  failure kind，并额外记录不含原始异常文本的稳定 cleanup failure 日志，禁止静默吞掉。
- `aclose()` 显式且幂等；close 后 send 明确失败。并发关闭与在途请求不增加协调框架，由 Composition Root 在停机阶段先停止
  新请求再关闭。
- 复用 `src/core/bounded_http_response.py`，不得复制或改名；响应预算阻止无界内存增长。

### 1.4 日志边界

只允许记录 method、builder 显式接收的 `system_id`、delivery state、status code、failure kind、cleanup failure 布尔值和耗时；
不得记录 URL query、headers、body、
原始异常文本或未来凭据。Phase 2 不注入审计框架，不持久化调用证据。

## 2. 文件与所有权冻结

### 2.1 ADD / REUSE / HANDOFF

| 类型 | 精确路径 | 当前阶段裁决 |
| --- | --- | --- |
| ADD | `src/core/outbound_http/__init__.py` | 只导出 §1.2 的公开合同与 builder；无兼容 re-export |
| ADD | `src/core/outbound_http/contracts.py` | 框架无关值对象、枚举、异常和 Protocol；不得 import httpx 或 `src.app` |
| ADD | `src/core/outbound_http/transport.py` | 唯一 HTTPX 发送实现、阶段化归类、有界读取、日志和 close |
| ADD | `src/core/outbound_http/factory.py` | 最小参数校验和 Client/Transport 构造；无类层次、registry 或 singleton |
| ADD | `tests/core/outbound_http/test_contracts.py` | 核心合同与输入校验 |
| ADD | `tests/core/outbound_http/test_transport.py` | 单次发送、结果矩阵、取消、清理、脱敏 |
| ADD | `tests/core/outbound_http/test_factory.py` | builder 与 Client 生命周期 |
| ADD | `tests/architecture/test_outbound_http_boundary_guardrail.py` | import/export、职责和既有裸 Client allowlist 门禁 |
| MODIFY | `docs/architecture/heavy-test-impact.toml` | 为新增生产路径添加精确 mapping；`heavy_tests = []`，因本阶段无真实消费者或外部服务 |
| REUSE | `src/core/bounded_http_response.py` | 直接 import，内容与路径保持不变 |
| HANDOFF | `src/app/sys/external_http_transport.py` 等旧模块 | 只登记到 Phase 3/4/6/7；Phase 2 不改不删 |
| HANDOFF | 所有现有 `httpx.AsyncClient` 创建点 | 冻结现有 allowlist，不得新增；由其真实 Adapter/typed port 接入阶段原子删除 |

### 2.2 明确禁止新增

- `authentication.py`、`credentials.py`、`testing.py`、`config.py`、registry、strategy hierarchy 或生产 fake。
- `AuthStrategy`、`NONE`/`HMAC_SHA256`/`BASIC` 枚举、credential reference、Secret resolver、Clock、Nonce。
- 对任何 `src/app/**`、`device_adapters/**`、`workline_plugins/**`、Composition Root 或部署配置的 Phase 2 修改。

## 3. 顺序实施任务

本阶段只有一条实施泳道，必须按“合同 → 传输实现 → 生命周期 → 门禁 → 验证”顺序推进；前序合同会直接约束后续任务，
因此无可安全并行的实施机会（Sequential implementation, no parallelization opportunity）。

### Task 1：冻结合同与 TDD 基线

**Files:**

- Create: `src/core/outbound_http/__init__.py`
- Create: `src/core/outbound_http/contracts.py`
- Create: `tests/core/outbound_http/test_contracts.py`

**Produces:** §1.2 全部框架无关公开类型；后续任务只能消费这些名称，不得另建平行结果模型。

- [x] 先对 `src/core/bounded_http_response.py` 和拟新增公开符号执行 GitNexus upstream impact analysis；新增符号无影响时记录为 LOW。
- [x] 编写失败测试，逐项覆盖不可变性、合法请求/结果、绝对 URL、CR/LF、大小写不敏感重复请求 Header、全部非正预算、
  Header 默认值及 64/16,384 硬上限、
  failure enum 完整集合、四种结果状态矩阵、重复响应 Header 保真、decoded body 语义、repr 脱敏，以及 `contracts.py` 不 import httpx。
- [x] 运行 `uv run pytest tests/core/outbound_http/test_contracts.py -q`，确认因目标模块或符号不存在而失败。
- [x] 只实现使合同测试通过的值对象、枚举、异常和 Protocol；不得加入 factory、网络发送、认证或业务解释。
- [x] 重跑同一测试并执行 `uv run ruff check src/core/outbound_http tests/core/outbound_http/test_contracts.py`，预期全部通过。
- [x] 提交：`feat(core): 定义 outbound http 传输合同`。

### Task 2：实现单次发送与完整传输事实矩阵

**Files:**

- Create: `src/core/outbound_http/transport.py`
- Create: `tests/core/outbound_http/test_transport.py`

**Consumes:** Task 1 的 request/limits/result/delivery/failure/Protocol。

**Produces:** 实现 `OutboundHttpTransport` 的包内 HTTPX Transport；供 Task 3 builder 构造。

- [x] 修改具体实现符号前运行 GitNexus upstream impact analysis；新符号无调用者时记录为 LOW。
- [x] 使用 `httpx.MockTransport` 编写失败测试，覆盖一次请求只发送一次、一个 Client 被并发请求复用、所有 HTTP status 均只返回
  response fact、重复响应 Header 顺序保真、raw body 只解码一次且只公开 `decoded_body`、repr 脱敏。
- [x] 补齐失败矩阵测试：pool/connect error/timeout → `NOT_SENT`；write/read/remote protocol error/timeout →
  `DELIVERY_UNKNOWN`；send 内外层 total timeout 保守 unknown；收到 response 后的 total timeout 为 response received。
- [x] 补齐响应矩阵测试：Header 数量/bytes、metadata/chunk/wire/decoded/compression/content-encoding 失败 → 已收到响应且
  `decoded_body` 不可用；成功和失败路径都执行独立有界 cleanup。
- [x] 补齐控制流测试：取消发生于 send/read/cleanup 时，cleanup shield 后 `CancelledError` 传播；cleanup 单独失败返回稳定 failure；
  主失败叠加 cleanup 失败时保留主 failure 并记录布尔标识；未知异常传播；日志不含 query/header/body/原始异常；实现不 catch
  `Exception`。
- [x] 运行 `uv run pytest tests/core/outbound_http/test_transport.py -q`，确认因实现不存在而失败。
- [x] 实现最小一次发送流水线；直接复用 `read_bounded_wire_body` 与 `decode_bounded_http_body`，不复制 primitive，不添加重试。
- [x] 重跑 Task 1–2 测试及 Ruff，预期全部通过。
- [x] 提交：`feat(core): 实现 outbound http 单次发送`。

### Task 3：构造与生命周期

**Files:**

- Create: `src/core/outbound_http/factory.py`
- Modify: `src/core/outbound_http/__init__.py`
- Create: `tests/core/outbound_http/test_factory.py`

**Consumes:** Task 1 的 Protocol 与 Task 2 的包内实现。

**Produces:** `build_outbound_http_transport`，是后续 Composition Root 唯一公共构造入口。

- [x] 修改 builder/close 符号前运行 GitNexus upstream impact analysis；记录 direct caller 与风险。
- [x] 编写失败测试，覆盖合法 `system_id` 和 http/https base URL、拒绝空/含控制符 system id、userinfo/query/fragment/非正
  timeout、`trust_env=False`、redirect 关闭、
  Client 跨 send 复用、幂等 close、close 后 send 失败和 close 异常不被吞掉。
- [x] 运行 `uv run pytest tests/core/outbound_http/test_factory.py -q`，确认 builder 不存在而失败。
- [x] 实现简单函数 builder 和最小生命周期状态；不得新增 Factory 类、配置对象、registry、singleton、PID/loop guard 或 test seam。
- [x] 运行 `uv run pytest tests/core/outbound_http -q` 与 Ruff，预期全部通过。
- [x] 提交：`feat(core): 装配 outbound http 生命周期`。

### Task 4：架构门禁与 HEAVY 映射

**Files:**

- Create: `tests/architecture/test_outbound_http_boundary_guardrail.py`
- Modify: `docs/architecture/heavy-test-impact.toml`

**Consumes:** Task 1–3 的最终文件树和公开导出。

**Produces:** Phase 2 职责不可回退的机器门禁，以及 selector 对新增生产路径的显式 NONE 裁决。

- [x] 编写失败门禁，证明：`contracts.py` 无 httpx；整个新包无 `src.app`/数据库/Celery/Adapter/插件 import；公开导出无
  httpx 类型、认证、credential、HMAC、Clock、Nonce、registry 或 fake；新包不存在额外生产文件。
- [x] 冻结当前 Phase 2 开始前的裸 `httpx.AsyncClient` 创建点 allowlist，并断言本阶段不增加旧路径；不得把最终全局零裸 Client
  门禁提前伪装为已完成。
- [x] 在 `heavy-test-impact.toml` 为 `src/core/outbound_http/**` 增加精确 mapping 和 `heavy_tests = []`；原因固定为本阶段无生产消费者、
  全部行为由 MockTransport 核心测试覆盖。
- [x] 运行架构门禁与 selector 合同测试，先确认新门禁能识别故意构造的违规样例，再恢复样例并确认通过。
- [x] 提交：`test(architecture): 冻结 outbound http 基础边界`。

### Task 5：Phase 2 完整验证与交接

**Files:** 不新增生产或测试文件；只验证 Task 1–4 已冻结路径。

- [x] 运行 `uv run pytest tests/core/outbound_http tests/architecture/test_outbound_http_boundary_guardrail.py -q`。
- [x] 运行 `uv run pytest tests/architecture/test_suite_topology_guardrail.py tests/architecture/test_core_plugin_test_ownership_guardrail.py -q`。
- [x] 运行 `uv run pytest tests/scripts -q` 和 `uv run scripts/select_heavy_tests.py --scope unstaged`；预期 selector 接受显式 NONE。
- [x] 运行 `uv run pytest --collect-only -q -o addopts='' | tail -5`，确认新增核心测试位于允许目录且未混入 HEAVY。
- [x] 运行 `uv run ruff format --check src/core/outbound_http tests/core/outbound_http tests/architecture/test_outbound_http_boundary_guardrail.py`、
  `uv run ruff check ...` 和 `./scripts/git-quality-gate.sh --profile quality`。
- [x] 扫描 Phase 2 边界：新生产包只有四文件；既有 WMS/RCS/ECS/Outbox/DeviceCommand/Composition Root 无修改、无新包 import；
  `src/core/bounded_http_response.py` 内容未变；`docs/hardware/` 无修改。
- [x] 运行 `gitnexus_detect_changes()`，确认只影响 Phase 2 新符号、核心测试、架构门禁和 HEAVY mapping。
- [x] 提交最终验证修正（如有）：`chore(core): 完成 outbound http phase2 门禁`。

## 4. 测试设计复审

```text
OutboundHttpRequest
  ├─ 合法值 ───────────────> 不可变、repr 脱敏
  └─ 非法 URL/Header/预算 ──> 发送前显式失败

send
  ├─ pool/connect 失败 ─────> NOT_SENT
  ├─ write/read/协议失败 ───> DELIVERY_UNKNOWN
  ├─ send 内总超时 ─────────> DELIVERY_UNKNOWN（保守分类）
  ├─ 收到任意 HTTP status ──> RESPONSE_RECEIVED
  │    ├─ bounded/decode 成功 -> status/重复 headers/decoded body
  │    ├─ header/body/decode 失败 -> status + failure kind，body 不可用
  │    └─ cleanup 单独失败 -> RESPONSE_CLEANUP_FAILED
  ├─ CancelledError ─────────> 独立有界 cleanup 后原样传播
  └─ 未知异常 ───────────────> 原样传播，不静默转码

lifecycle
  ├─ 多次/并发 send ─────────> 同一 Client
  ├─ aclose 两次 ─────────────> 幂等
  └─ close 后 send ───────────> 显式失败
```

计划冻结上图与 Task 1–4 列出的全部行为分支；分支数量以最终参数化测试收集结果为准，不在文档中制造数量合同。全部由核心
单元测试或架构门禁拥有。没有真实数据库、HTTP 服务、Celery、Redis、容器、E2E、
resilience 或 load 测试；厂商和业务能力测试数量为零，避免以业务能力测试基础层或反向替代。

## 5. 失败模式与可观测性

| 失败模式 | 公开事实 | 测试所有者 | 静默风险裁决 |
| --- | --- | --- | --- |
| 请求不合法 | 发送前异常 | contracts | 不发送，不转换为业务结果 |
| pool/connect 失败 | `NOT_SENT` + stable kind | transport | Adapter 不据此自动重试 |
| write/read/total timeout | phase-sensitive delivery fact | transport | 不声称安全重试 |
| HTTP 3xx/4xx/5xx | `RESPONSE_RECEIVED` | Adapter 在后续阶段解释 | Phase 2 不判定 accepted/rejected |
| response 超预算/编码失败 | response received + stable kind | transport | 不丢失“已送达且有响应”事实 |
| 取消 | 原样传播并清理 | transport | 不伪装成传输失败 |
| 未知代码缺陷 | 原样传播 | transport | 禁止 catch-all 隐藏缺陷 |
| close 后调用 | 显式生命周期错误 | factory/transport | 不隐式重建 Client |

## 6. 性能与运行特征

- 热路径不逐请求创建 Client；HTTPX Client 提供连接池复用。
- 响应按 chunk/wire/decoded/压缩比预算限制，内存上界由请求合同显式给出。
- 不自动重试，不产生隐藏的网络放大；没有数据库查询、锁、队列或 O(n²) 遍历。
- 本阶段没有生产消费者，性能验收以资源所有权和有界行为为准，不建设没有真实负载依据的 benchmark。

## 7. NOT in scope

- WMS/RCS/ECS Adapter、Composition Root、部署配置或任何生产调用切换。
- method/path/wire DTO、HTTP status 业务解释、Circuit Breaker、重试、Outbox、`TransportTask`、`WmsConfirmation`。
- outbound/inbound 认证、HMAC/BASIC、凭据、Secret、canonical string、Header 签名、Clock、Nonce。
- 生产 fake、sandbox、动态插件、registry、Service Locator、拦截器、中间件链、全局 Client。
- 修改或归档 `docs/hardware/`；旧模块删除与文档归档分别由后续真实替代阶段负责。
- 真实网络、数据库、Celery、E2E、resilience、load 和业务插件测试。

## 8. 完成定义

Phase 2 只有在 Task 1–5 全部完成、TDD 证据和质量门禁通过、四文件边界及旧消费者零修改得到证明后才算完成。
完成仅表示基础能力可被后续 Adapter 消费，不表示 WMS、设备命令或业务工作线已切换，也不允许提前启动 Phase 4。
