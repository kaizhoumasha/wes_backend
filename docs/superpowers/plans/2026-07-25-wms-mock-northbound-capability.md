# WMS MOCK 北向能力实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** 让 Docker/E2E 实际运行的 WMS MOCK 完整实现三个 typed EFFECT 的 HMAC 幂等提交、权威状态查询、
故障边界与数据重置，并成为当前北向能力验收真源。

**Architecture:** 将北向认证、幂等记录和状态机收敛到独立的 Mock-only 模块，由现有 FastAPI Mock 服务通过
公开 HTTP 路由调用。三个 operation 共享一套状态查询和幂等规则，仅 typed result builder 不同；callback
保持可选提示，终态只来自 status query。黑盒探针只访问实际 Mock 的公开 HTTP 面。

**Tech Stack:** Python 3.13、FastAPI、Pydantic、httpx、pytest、Docker Compose。

## 全局约束

- 严格 TDD：先写失败测试并确认失败原因，再写最小实现。
- `tests/mock/wms_mock_server.py` 是唯一 WMS 能力真源；不得以内嵌 FastAPI stub 或 fixture 冒充实际 Mock。
- Submit 使用 `X-WES-*` 七项 canonical HMAC；Status 使用 `X-WMS-*` 五项 canonical HMAC。
- Mock 直接复用 WES material-flow sandbox v1/v2 credential，active 为 v2；不得保留 Mock 专用 credential。
- 幂等作用域固定为 `operation_identity + idempotency_key`，fingerprint 固定为 canonical raw body SHA-256。
- typed body 在幂等写入前按冻结 wire schema 严格校验；并发首次提交必须原子化为一个 202 和单一 effect。
- callback hint 不携带终态权威，且每个首次受理请求最多发送一次。
- 三个 operation 必须共享状态存储与 reducer 规则，不复制三套状态机。
- 故障注入和可控时钟只属于 Mock/测试环境。
- 可见性与保留期使用 UTC aware 时钟及精确时间边界；故障按 method/path/operation 精确作用并原子 claim。
- Mock Docker 镜像不得依赖完整 WES 运行时包。
- 所有项目命令使用 `uv run ...`。
- 修改函数、类或方法前运行 GitNexus upstream impact analysis；HIGH/CRITICAL 必须先报告。
- 保留现有有价值注释，并更新与行为变化冲突的注释。

---

### Task 1：实现 Mock-only HMAC、幂等记录与状态核心

**Files:**

- Create: `tests/mock/wms_northbound_contract.py`
- Create: `tests/mock/test_wms_northbound_contract.py`
- Modify: `tests/mock/wms_mock_server.py`

**Interfaces:**

- Produces: 版本化 credential reference 的环境变量解析；Submit/Status HMAC 验证；线程安全的
  `operation_identity + idempotency_key` 记录存储；首次受理、处理中重放、完成重放、冲突、查询推进和 reset。
- Consumes: `docs/contracts/wms-northbound-interaction-contract.md` 的 canonical 字段顺序和三个 status replay schema。

- [x] 编写 credential、content hash、Submit HMAC、Status HMAC 的失败测试。
- [x] 运行新增测试并确认因模块/行为不存在而 RED。
- [x] 实现不依赖 WES runtime import 的认证与 credential allowlist。
- [x] 编写幂等作用域、同键重放、冲突、单调版本、typed result、拒绝和 `NOT_FOUND` 的失败测试。
- [x] 运行新增测试并确认因状态核心不存在而 RED。
- [x] 实现单一锁保护的北向记录存储和三个 operation 的 typed result builder。
- [x] 编写 callback hint 单次登记和 reset 清理的失败测试。
- [x] 实现对应最小行为并运行 `uv run pytest tests/mock/test_wms_northbound_contract.py -q`。
- [x] 运行 `uv run pytest tests/mock/test_wms_mock_server.py -q`，确认既有 Mock 行为无回归。
- [x] 提交 `feat(wms-mock): 建立北向认证与幂等状态核心`。

### Task 2：接入三个 Submit、Status、故障控制与 Reset HTTP 面

**Files:**

- Modify: `tests/mock/wms_mock_server.py`
- Modify: `tests/mock/test_wms_mock_server.py`
- Modify: `tests/mock/Dockerfile`
- Modify: `tests/mock/__init__.py`
- Modify: `tests/deployment/test_docker_compose_mock_urls.py`
- Modify: `docker-compose.yml`
- Modify: `.env.dev`
- Modify: `.env.test`
- Create: `tests/integration/test_wms_mock_northbound_live.py`

**Interfaces:**

- Consumes: Task 1 的认证和状态存储接口。
- Produces: 三个 operation-specific submit endpoint、`GET /northbound/operations/status`、
  `GET /northbound/contract`、Mock-only 时钟/故障/效果计数接口，以及覆盖北向状态的 reset。

- [x] 为三个 submit endpoint 的 202、409、完成重放、422、HMAC 拒绝和 callback 单次发送编写失败测试。
- [x] 运行对应测试并确认 RED。
- [x] 将三个现有 submit route 接入共享状态核心，保持原 typed response 字段兼容。
- [x] 为五态 status query、五项 HMAC、三个 typed result、429、5xx、慢响应和响应体边界编写失败测试。
- [x] 运行对应测试并确认 RED。
- [x] 实现 status、contract 和 Mock-only 控制接口。
- [x] 为 reset 后相同 key 可重新受理编写失败测试并确认 RED。
- [x] 扩展 reset 清理，并注入 Mock 所需的版本化 HMAC secret 环境变量。
- [x] 运行 `uv run pytest tests/mock/test_wms_mock_server.py tests/deployment/test_docker_compose_mock_urls.py -q`。
- [x] 提交 `feat(wms-mock): 接入北向提交状态与故障控制接口`。

### Task 3：让黑盒探针验收实际 Mock 并关闭文档门禁

**Files:**

- Modify: `scripts/verify_wms_northbound_feasibility.py`
- Modify: `tests/contracts/wms_integration/test_wms_northbound_feasibility_probe.py`
- Modify: `tests/contracts/wms_integration/test_task9_release_evidence_assets.py`
- Modify: `docs/operations/wms-northbound-feasibility-report.md`
- Modify: `docs/operations/wms-northbound-acceptance-and-cutover.md`
- Modify: `docs/superpowers/plans/2026-07-24-northbound-capability-simplification.md`

**Interfaces:**

- Consumes: Task 2 的实际 Mock 公开 HTTP 面。
- Produces: 覆盖三个 operation 的黑盒验收命令和 `PASS/GO` 证据，不读取 Mock 内部状态。

- [x] 将探针测试改为连接实际 `wms_mock_server.app` 的公开 HTTP 路由，并删除内嵌 stub 作为验收真源。
- [x] 运行探针测试并确认因实际 Mock 接口/调用形状未适配而 RED。
- [x] 扩展探针支持三个 operation-specific submit path、合法 Submit/Status HMAC 和 typed payload。
- [x] 覆盖首次提交、处理中/完成重放、冲突、五态、版本、typed result、故障矩阵、callback hint 和 reset。
- [x] 运行 `uv run pytest tests/contracts/wms_integration/test_wms_northbound_feasibility_probe.py -q` 并确认 GREEN。
- [x] 更新可行性报告为实际 Mock `GO`，记录 build、承诺参数、测试命令和结果。
- [x] 更新验收与主计划状态，明确实际 Mock 是当前验收源且 P0 门禁已关闭。
- [x] 运行相关 Mock、contract、deployment、runtime 测试和 topology guardrail。
- [x] 运行 `./scripts/git-quality-gate.sh --profile quality`。
- [x] 运行 GitNexus detect changes，确认无意外生产执行流影响。
- [x] 提交 `test(wms-mock): 以实际服务关闭北向验收门禁`。

### 2026-07-25 最终复核增补

- [x] 以真实 WES material-flow v1/v2 credential reference 替换 Mock 专用凭据，并用真实 sender/signature 验证 active v2。
- [x] 冻结三个 typed wire body 的 required/allowed/type/value 校验，证明非法请求无记录。
- [x] 将可见性/保留期改为 `visible_at`/`expires_at` 精确时间语义，覆盖边界前后 effect count。
- [x] 将故障改为 method/path/operation 精确作用域和并发原子 claim，固定 5xx 并流式发送超限响应。
- [x] 分离 typed full-box callback hint 与 legacy completion callback 路由。
- [x] 新增并发同键 HTTP 重放，证明一个 202、其余 409、effect count=1。
- [x] 修复独立 WMS Docker 镜像入口并完成真实 Compose build、live pytest 与 45-case CLI 探针。
- [x] 运行完整相关回归、默认测试收集、quality gate 和 GitNexus detect changes。
- [x] 更新最终修复报告并完成提交前检查。

## 最终验收

- 三个 typed EFFECT 的 submit/status 黑盒矩阵全部通过。
- HMAC 篡改、同键异 payload、429、5xx、超时、超限 body、暂时不可见和 reset 全部被自动化测试覆盖。
- callback hint 只触发查询且不直接提供终态。
- Docker/E2E 使用与测试一致的 endpoint、WES material-flow v1/v2 credential reference 和 secret。
- 实际 Compose `mock_wms` 必须通过真实 TCP heavy/live pytest 与 CLI 探针，不能仅依赖 ASGITransport。
- 相关测试、架构门禁、完整质量门禁全部通过。
