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
- 幂等作用域固定为 `operation_identity + idempotency_key`，fingerprint 固定为 canonical raw body SHA-256。
- callback hint 不携带终态权威，且每个首次受理请求最多发送一次。
- 三个 operation 必须共享状态存储与 reducer 规则，不复制三套状态机。
- 故障注入和可控时钟只属于 Mock/测试环境。
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

- [ ] 编写 credential、content hash、Submit HMAC、Status HMAC 的失败测试。
- [ ] 运行新增测试并确认因模块/行为不存在而 RED。
- [ ] 实现不依赖 WES runtime import 的认证与 credential allowlist。
- [ ] 编写幂等作用域、同键重放、冲突、单调版本、typed result、拒绝和 `NOT_FOUND` 的失败测试。
- [ ] 运行新增测试并确认因状态核心不存在而 RED。
- [ ] 实现单一锁保护的北向记录存储和三个 operation 的 typed result builder。
- [ ] 编写 callback hint 单次登记和 reset 清理的失败测试。
- [ ] 实现对应最小行为并运行 `uv run pytest tests/mock/test_wms_northbound_contract.py -q`。
- [ ] 运行 `uv run pytest tests/mock/test_wms_mock_server.py -q`，确认既有 Mock 行为无回归。
- [ ] 提交 `feat(wms-mock): 建立北向认证与幂等状态核心`。

### Task 2：接入三个 Submit、Status、故障控制与 Reset HTTP 面

**Files:**

- Modify: `tests/mock/wms_mock_server.py`
- Modify: `tests/mock/test_wms_mock_server.py`
- Modify: `tests/deployment/test_docker_compose_mock_urls.py`
- Modify: `docker-compose.yml`
- Modify: `.env.dev`
- Modify: `.env.test`

**Interfaces:**

- Consumes: Task 1 的认证和状态存储接口。
- Produces: 三个 operation-specific submit endpoint、`GET /northbound/operations/status`、
  `GET /northbound/contract`、Mock-only 时钟/故障/效果计数接口，以及覆盖北向状态的 reset。

- [ ] 为三个 submit endpoint 的 202、409、完成重放、422、HMAC 拒绝和 callback 单次发送编写失败测试。
- [ ] 运行对应测试并确认 RED。
- [ ] 将三个现有 submit route 接入共享状态核心，保持原 typed response 字段兼容。
- [ ] 为五态 status query、五项 HMAC、三个 typed result、429、5xx、慢响应和响应体边界编写失败测试。
- [ ] 运行对应测试并确认 RED。
- [ ] 实现 status、contract 和 Mock-only 控制接口。
- [ ] 为 reset 后相同 key 可重新受理编写失败测试并确认 RED。
- [ ] 扩展 reset 清理，并注入 Mock 所需的版本化 HMAC secret 环境变量。
- [ ] 运行 `uv run pytest tests/mock/test_wms_mock_server.py tests/deployment/test_docker_compose_mock_urls.py -q`。
- [ ] 提交 `feat(wms-mock): 接入北向提交状态与故障控制接口`。

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

- [ ] 将探针测试改为连接实际 `wms_mock_server.app` 的公开 HTTP 路由，并删除内嵌 stub 作为验收真源。
- [ ] 运行探针测试并确认因实际 Mock 接口/调用形状未适配而 RED。
- [ ] 扩展探针支持三个 operation-specific submit path、合法 Submit/Status HMAC 和 typed payload。
- [ ] 覆盖首次提交、处理中/完成重放、冲突、五态、版本、typed result、故障矩阵、callback hint 和 reset。
- [ ] 运行 `uv run pytest tests/contracts/wms_integration/test_wms_northbound_feasibility_probe.py -q` 并确认 GREEN。
- [ ] 更新可行性报告为实际 Mock `GO`，记录 build、承诺参数、测试命令和结果。
- [ ] 更新验收与主计划状态，明确实际 Mock 是当前验收源且 P0 门禁已关闭。
- [ ] 运行相关 Mock、contract、deployment、runtime 测试和 topology guardrail。
- [ ] 运行 `./scripts/git-quality-gate.sh --profile quality`。
- [ ] 运行 GitNexus detect changes，确认无意外生产执行流影响。
- [ ] 提交 `test(wms-mock): 以实际服务关闭北向验收门禁`。

## 最终验收

- 三个 typed EFFECT 的 submit/status 黑盒矩阵全部通过。
- HMAC 篡改、同键异 payload、429、5xx、超时、超限 body、暂时不可见和 reset 全部被自动化测试覆盖。
- callback hint 只触发查询且不直接提供终态。
- Docker/E2E 使用与测试一致的 endpoint、credential reference 和 secret。
- 相关测试、架构门禁、完整质量门禁全部通过。
