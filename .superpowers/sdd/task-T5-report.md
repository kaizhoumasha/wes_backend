# T5 实施报告：WMS Provider conformance、replay 与最小 simulator

## 状态与范围

T5 已实现并通过本任务目标门禁。交付只覆盖 Provider quality：统一核心题库、纯 conformance 评估与不可变报告、真实 adapter/进程内 simulator/纯 replay 三执行面，以及显式 staging live 入口。

本任务没有实现 T6 shadow/readiness、T7 粗分机迁移、T8+ EFFECT runtime，也没有新增服务、容器、动态规则 DSL、独立 HTTP client 或第二 transport lifecycle。

## 交付内容

### 不可覆写的共同题库

`QUERY_INVENTORY_CONFORMANCE_CASES` 是 inventory QUERY 的唯一核心题库，`WMS_CONFORMANCE_MANIFEST` 直接派生其 case identity，不再维护第二份 QUERY case 清单。题库固定覆盖：

- success、empty、缺少 `items`、非法 Decimal；
- business reject、timeout、429、5xx、malformed；
- pagination、Decimal 精度、wire budget、evidence failure。

报告构建器拒绝缩减、替换或增加题目；观察必须对每个核心 case 恰好出现一次。Provider 无法通过自定义参数、skip 或 xfail 绕过核心语义。

### 三个执行面

- 真实 adapter：使用唯一 `InventoryQueryOperationAdapter` 和 T3 `WmsQueryTransportExecutor`，对 canonical scripted HTTP response 执行完整题库。
- 进程内 simulator：仅在 `tests/mock/` 提供单个 handler，只有冻结 case 状态；不启动 FastAPI/uvicorn，不持有 endpoint/credential，不创建 client、服务、容器或第二 transport。
- canonical replay factory：只从冻结 replay record 重建 T3 四分支 outcome，再生成固定 observation；不导入网络、credential、runtime factory、持久化或 callback 能力。

三个执行面使用同一 fixture case tuple 和同一 expectation tuple，合同测试校验执行顺序与 case 集完全一致。

### 纯 runner 与报告安全

纯评估器只比较固定 expectation 与脱敏 observation，本身没有 endpoint、credential、transport、数据库或网络能力。报告和 fixture 均为 frozen Pydantic 模型并携带 SHA-256 内容摘要；报告重载时会重算并校验摘要。

报告 schema 只允许固定 case ID、封闭 outcome/reason/semantic code、布尔 evidence 状态与不可逆失败摘要，不接收 raw payload、message 或 header。endpoint revision 只允许短 revision token，并在调用 staging executor 前拒绝 URL、header/signature、credential/secret 形态。

非 staging 目标只能使用 sandbox profile 且不能携带 endpoint revision；`STAGING_LIVE` 只能使用 staging profile、合法 endpoint revision 和包含唯一 canonical query binding 的 profile。production profile 在调用任何 executor 前 fail closed。

## TDD 记录

1. RED：报告合同首先因 `provider_conformance` 模块缺失而收集失败；实现纯题库/报告后，摘要规范化测试暴露 UTC `+00:00`/`Z` 差异，修复后 10 项 GREEN。
2. RED：共同试卷首先因 test support 不存在而收集失败；最小实现后暴露 malformed case identity 与 skip/xfail guard 测试缺陷，修复后真实 adapter/simulator/replay 6 项 GREEN。
3. RED：staging 空 revision 起初在执行完 case 后才失败；把环境和 revision 校验前移后 GREEN。随后 URL/header 形态及缺 canonical binding 的 profile 均先 RED，再收紧为调用 executor 前 fail closed。
4. RED：observation 起初允许 raw secret/header/URL 字符串；改为受约束 case/code schema 后 GREEN。
5. RED：报告构建器起初接受缩减题库；加入固定题库一致性门禁后 GREEN。

## GitNexus 影响分析

- `WMS_CONFORMANCE_MANIFEST`、`OperationConformanceRequirement`：LOW，0 个直接依赖、0 条 execution flow。
- `InventoryQueryOperationAdapter`：LOW，4 个直接 import、0 条 execution flow；本任务仅复用，未修改。
- `WmsQueryTransportExecutor`：MEDIUM，6 个直接 import、0 条 execution flow；本任务仅复用，未修改。
- 新增 `_ConformanceVerdict`、`_validate_execution_environment`、`build_wms_conformance_report` 在修改前的 CLI impact 均为 LOW，0 个直接依赖、0 条 execution flow。
- MCP GitNexus 读取器与本机索引存储版本一度不兼容；改用同 worktree CLI 得到上述精确结果。后续增量分析又暴露 Ladybug FTS 索引不一致，列入 concern，不把工具故障解释为无影响。
- staged detect 使用同 worktree GitNexus CLI 完成：9 个文件、65 个符号、0 条 affected process，最终风险 LOW；MCP detect 因相同存储版本不兼容失败。

## 验证

- WMS integration contracts：`92 passed`。
- T2/T3/T5 architecture + test topology：`19 passed`。
- T5 contract/architecture 组合：`25 passed`。
- 显式 mock simulator：`2 passed`（使用 `-o addopts=''` 显式收集重测试目录）。
- 测试拓扑：`6 passed`；默认收集审计：`3520 tests collected`。
- `uv run ruff format --check .`、`uv run ruff check .`、`git diff --check`：通过。
- `./scripts/git-quality-gate.sh --profile quality`：通过；Bandit 0 issue、348 项 runtime contract guardrails、11 项 process naming、import-linter、architecture guardrails 和 topology 均通过。

## 文件

- `src/app/runtime/system_capabilities/wms/provider_conformance.py`：固定 QUERY 题库、纯评估器、报告摘要校验和 staging live 入口。
- `src/app/runtime/system_capabilities/wms/conformance_manifest.py`：QUERY required cases 直接派生共同题库。
- `tests/support/wms_provider_conformance.py`：冻结 fixture、真实 adapter/simulator/replay 三个 test factory。
- `tests/mock/wms_scripted_provider.py`：进程内最小 scripted handler。
- `tests/contracts/wms_integration/test_provider_conformance_*.py`：共同试卷、报告与 staging fail-closed 合同。
- `tests/architecture/test_wms_provider_conformance_boundaries.py`：生产排除、runner/replay/simulator 能力门禁。
- `tests/mock/test_wms_provider_conformance_simulator.py`：显式 mock 目录 simulator 确定性与最小能力测试。

## Concern

- `uv run python scripts/generate_wms_operation_index.py --check` 报告既有 generated index 漂移：HEAD 文件 digest 为 `3782ce41...`，当前 T2/T3 catalog 派生为 `d4b9edc3...`。追溯显示 T3 提交修改了 query operation contract，但 `generated_operation_index.py` 自 T2 后未更新；T5 没有改任何 operation contract，因此未把该基座修正混入本提交。
- staging live 入口只负责在执行前验证 staging profile/revision/canonical binding，并接受外部注入的受控 live executor；本任务未创建 Provider 专用真实 endpoint/credential composition。该组合必须由部署侧显式提供，runner 自身仍无生产 endpoint/credential 能力。

## 审查修复追加

### staging executor 身份与 fail-closed

- live 入口不再接受无身份 callback，改为只接受实现 `StagingQueryConformanceExecutor` 的具名 executor。
- 部署 composition 必须提供冻结 `StagingConformanceExecutorAttestation`；证明绑定 staging profile identity/profile revision、canonical query binding identity/binding revision、endpoint identity digest 与 endpoint revision。
- runner 在执行第一题前重新派生并逐字段比对 attestation；production profile、缺 canonical binding、endpoint/revision 不匹配、attestation 篡改或无 attestation executor 均 fail closed。
- attestation 只携带 SHA-256 摘要；报告只保留 endpoint revision digest，不保存 endpoint identity、URL、credential reference 或 secret。

### 报告验证与 revision 收紧

- `endpoint_revision` 收紧为 64 位小写 SHA-256 opaque digest；普通 release token、URL、`sk-*`、`api_key=*`、Bearer/authorization 形态都在执行前拒绝。
- `verify_wms_conformance_report` 除报告自摘要外，还校验固定核心 suite digest、完整 case ID/顺序/数量、author-time profile digest、target/profile 环境组合，并逐题重算 `passed` 与 failure evidence digest。
- 合同测试覆盖“篡改后重新计算 report digest”的攻击面，证明自洽 hash 不能绕过固定题库、case verdict 或环境门禁。

### 独立 replay asset 与无状态 factory

- replay 改为读取独立冻结资产 `tests/fixtures/wms_provider_conformance/query_inventory_replay.v1.json`，固定 digest 为 `4584ece449cdcfa69f6a46ac4315b3f11a285f3f832a82bc04685c21ac22bf52`。
- replay record 自身携带重建 T3 outcome 所需的最小领域事实，不再从当前 conformance expectation 或 scripted case 的 `recorded_observation` 复制结果。
- adapter/simulator/replay factory 均不保存执行可变状态；执行顺序记录移入 `RecordingConformanceTarget` 测试 wrapper。

### 审查修复 TDD 记录

1. RED：新增 opaque revision 与重新签名报告篡改用例，`26` 项中 `8` 项按预期失败；GREEN：固定 suite/profile/environment/逐题复核与严格 digest 后 `26 passed`。
2. RED：architecture 门禁证明 staging 入口仍暴露裸 `execute` callback；GREEN：改为 attested executor 签名并通过门禁。
3. RED：独立 replay asset 与 wrapper 记录用例 `2 failed`，分别暴露 asset 缺失和 factory 持有可变记录；GREEN：独立资产、T3 outcome 重建与无状态 factory 后 suite `8 passed`。

### 审查修复影响分析与验证

- GitNexus：`build_wms_conformance_report` 为 MEDIUM（7 个直接调用、0 条 execution flow）；报告模型、verify、staging entry、环境/profile 校验与 replay/factory 均为 LOW（最多 4 个直接依赖、0 条 execution flow），无 HIGH/CRITICAL 风险。
- MCP 读取器仍与本机 LadybugDB 索引版本不兼容；已在目标 worktree 完整重建索引，并使用同版本 GitNexus CLI 完成 query/context/impact。
- staged detect：`7 files / 44 symbols / 0 affected process`，最终风险 LOW；暂存范围不包含用户已有的 `AGENTS.md`、`CLAUDE.md` 改动。
- WMS integration contracts：`105 passed`；T5 目标 contracts：`34 passed`；显式 mock simulator：`2 passed`；T5 architecture + topology：`11 passed`。
- 默认收集审计：`3534 tests collected`；`ruff format --check`、`ruff check`、Bandit、import-linter、architecture guardrails、runtime contract guardrails 与 test topology 均由 quality profile 验证通过。
- 本轮未运行或修改 generated operation index，不处理既有 generated index drift。

## 最终复审修复追加

### canonical staging composition 复验

- live runner 在读取 attestation 和执行第一题前，重新通过 `WmsProviderProfile.model_validate` 触发完整组合校验，并按完整 identity 从 author-time `WMS_PROVIDER_PROFILES` registry 解析 canonical staging profile。
- runner 要求重建后的完整 profile 与 registry 中 canonical profile 深度相等；不仅校验外层 environment，也覆盖全部 bindings、callbacks、operation contract 与 auth composition。
- 使用 production profile 的 bindings 再通过 `model_copy` 伪造 staging 外层 identity、或在 staging profile 上删减 binding，都会在 attestation/execute 前 fail closed。

### 部署签发与公钥可信根

- `StagingConformanceExecutorAttestation` 升级为 Ed25519 签名声明，不再携带调用方可重算的自声明 checksum。
- 部署受控 signer 签发的声明绑定 canonical profile revision、canonical query binding revision、endpoint identity digest、内部 revision digest、composition identity digest 与 signing key identity。
- runner 与报告验证器只接受 concrete `Ed25519StagingConformanceAttestationVerifier`，由部署 composition 注入受信公钥；未知签发 key、自签 attestation、伪造 signature、调用方自定义“永远通过” verifier 和裸 callback 均在执行前 fail closed。
- executor 必须同时提供签名 attestation 与匹配的 composition identity digest；runner 验签、复核 canonical claims 并比较 executor composition 后才调用 `execute`。
- 报告只携带签名声明中的不可逆摘要、公钥签名和 key identity，不携带 endpoint 原文、内部 revision 原文、composition identity 原文、credential、header 或私钥材料。部署私钥的生成、存储和轮换仍由部署系统负责，本仓库不提供或保存私钥。

### endpoint revision 内部派生

- 公共报告构建器与 live runner 均移除 `endpoint_revision` 调用参数；合法 64 位十六进制字符串也不能作为 caller-owned revision 注入。
- staging 报告 revision 只由已验签 attestation 中的 endpoint identity digest、内部 revision digest、profile revision 与 binding revision做 canonical hash 派生。
- staging 报告包含签名 attestation；持久化报告复验必须再次使用部署 Ed25519 公钥 verifier，并重新派生、比较 endpoint revision。

### replay 纯 test-support 与独立 asset pin

- replay asset model、record/fixture loader、T3 outcome reconstruction、observation projection、无状态 factory 与 replay report verifier 已拆到独立 `tests/support/wms_provider_replay.py`。
- architecture guard 解析该模块 AST，禁止导入 `httpx`、query transport、credential、adapter composition 或 runtime factory，并验证 replay 模型、loader、reconstruction、factory 均由该纯模块定义。
- loader 同时校验固定 record ID/顺序、asset 自摘要，以及代码侧独立 pin `4584ece449cdcfa69f6a46ac4315b3f11a285f3f832a82bc04685c21ac22bf52`；即使篡改 asset 后同步重算内嵌 digest，也无法绕过代码 pin。
- adapter/simulator 报告继续携带 scripted fixture digest；REPLAY factory 单独暴露实际 replay asset digest，REPLAY 报告携带该 digest，并由纯 replay verifier 对代码 pin 复验。

### 最终复审 TDD 记录

1. RED：可信签名、公钥 verifier、完整 canonical profile 复验、受控 composition 与 caller-owned revision 拒绝共 `6 failed`；GREEN 后新增伪 verifier 攻击用例，先确认 `DID NOT RAISE`，再收紧为 concrete Ed25519 verifier。
2. RED：纯 replay 模块、loader pin/order 与 report provenance 共 `4 failed`；GREEN 后 replay 模块 ownership、AST import guard、重排 asset、篡改后重算内嵌 digest、实际 asset report digest 均通过。
3. 既有 T5 tests 随公共 API 收紧先出现旧 attestation import collection error；迁移到签名 runner、移除 caller-owned revision 并改用 replay 实际 asset digest 后，T5 定向组合 `48 passed`。

### 最终复审影响分析与验证

- GitNexus 在目标 worktree 重建为 `43,007 nodes / 72,000 edges / 300 flows` 后完成既有符号 impact：`build_wms_conformance_report` 与 `verify_wms_conformance_report` 为 MEDIUM，其余 staging/replay 符号为 LOW；无 HIGH/CRITICAL、无 execution flow 受影响。
- GitNexus 全工作区 detect 为 LOW、`0 affected process`；提交前另对仅暂存 T5 文件执行 staged detect。
- WMS integration contracts：`112 passed`；T5 contracts/architecture 组合：`48 passed`；显式 mock simulator：`2 passed`；T5 architecture + topology：`13 passed`。
- 默认收集审计：`3543 tests collected`；`ruff format --check .`、`ruff check .`、`git diff --check` 全部通过。
- `./scripts/git-quality-gate.sh --profile quality` 通过：Bandit 0 issue、348 项 runtime contracts、11 项 process naming、import-linter、architecture guardrails 与 test topology 全部通过。
- 扩大运行的 northbound/WMS architecture 组合为 `52 passed, 1 failed`；唯一失败是既有 inventory 清点仍记录旧测试路径，而当前发现路径已迁移到 `tests/support/rough_sorter_inventory_admission.py`。本轮未修改相关 inventory/generated 文件，按最终复审范围留给独立任务处理。
- 本轮没有运行或修改 WMS generated operation index，也没有新增独立服务、容器、DSL、HTTP client 或第二 transport lifecycle。

## 最终审批修复追加

### 部署 trust root 与 composition capability

- runner 与持久化报告 verifier 已移除所有 caller-owned verifier 参数；两者使用模块导入时从部署环境一次读取并冻结的 trust-root registry。
- 新增最小不可变 `StagingConformanceTrustRootRegistry`，生产从环境变量
  `WMS_STAGING_CONFORMANCE_TRUST_ROOTS` 加载 `key identity -> Ed25519 public key(base64url)` JSON；未配置时保持空 registry，
  staging composition/report 验证 fail closed。测试在设置导入环境后隔离 reload trust-root/provider 模块，不猴补丁生产 registry，
  也不向生产入口增加注入参数。
- 公开 attestation issuer 已删除；签发逻辑只存在于
  `compose_query_inventory_staging_conformance_executor` 部署 composition 入口内，且 composition 在返回 executor 前用冻结的部署
  trust root 复验签名。
- composition 使用 weak identity registry 将 capability 绑定到具体 executor 对象。公开入口会拒绝自建对象、挂载落盘
  attestation/digest 的对象、裸 callback 和 `copy.copy` 副本；这是信任边界内的 API 误用防护，不是对任意同进程恶意代码的隔离保证。

### runtime-neutral replay support

- `tests/support/wms_provider_replay.py` 已移除全部 `src.app` 导入；固定 case identity、asset/record、纯 outcome DTO、纯
  observation DTO、重建/投影和无状态 replay factory 均由该 test-support 模块自有。
- replay factory 只返回 runtime-neutral 冻结 observation；`tests/support/wms_provider_conformance.py` 的外层 adapter 才把它转换为
  runtime `ConformanceObservation`，并在外层完成 runtime report 与 replay asset provenance 验证。
- architecture guard 现禁止纯 replay 模块的全部 `src.app` import，并显式禁止 runtime、HTTP、transport、credential、adapter
  composition 能力；纯模块不再传递 runtime report/verifier 能力。

### 最终审批 TDD、影响分析与验证

1. RED：trust-root/replay 边界组合收集 `15` 项，出现 `5 failed, 5 errors`，分别暴露 caller verifier、公开 issuer、可挂
   attestation/digest executor，以及 replay `src.app` 导入；最小实现后定向组合 GREEN。
2. RED：新增 copied executor 攻击用例，现有 private-slot executor 出现 `DID NOT RAISE`；改为 factory weak identity registry
   后单项 GREEN，最终定向组合 `26 passed`。
3. GitNexus 在目标 worktree 重建到 HEAD 后使用同版本 CLI 完成 impact；`verify_wms_conformance_report` 和旧 verifier 为
   MEDIUM，其余 staging/replay 符号为 LOW，均为 `0 affected process`，无 HIGH/CRITICAL。MCP 读取器仍因 LadybugDB
   存储版本不兼容返回 UNKNOWN，未将工具故障解释为低风险。
4. staged detect 汇总为 HIGH：`9 files / 99 symbols / 10 affected processes`。10 条均是本次 staging conformance 内部
   `compose/run -> canonical profile / signature verification / digest-render` 链路，无 API、数据库或其它业务编排外溢；
   已向用户说明该汇总风险并获得继续提交授权。
5. WMS integration contracts：`114 passed`；显式 mock simulator：`2 passed`；默认收集审计：`3544 tests collected`。
6. 扩大 northbound/WMS architecture：`52 passed, 1 failed`；唯一失败仍是任务明确排除的既有 inventory 测试路径漂移
   （旧 `tests/workline_plugins/rough_sorter/test_conformance.py` 对当前
   `tests/support/rough_sorter_inventory_admission.py`），本轮未修改 inventory/generated 文件。
7. `ruff format --check`、`ruff check`、Bandit、348 项 runtime contracts、11 项 process naming、import-linter、
   architecture guardrails 与 test topology 均由 quality profile 验证通过；`git diff --check` 通过。

## 最终 capability 公开 API 修复追加

### 模块 creator/resolver 暴露面收紧

- 删除模块级 `_build_controlled_executor_boundary`、`_create_controlled_staging_executor` 与
  `_resolve_controlled_staging_executor`。签发、capability 创建、weak identity 注册和 resolve 不再作为模块级 raw helper 暴露。
- 外部仅保留 `compose_query_inventory_staging_conformance_executor` 公开 composition 入口。该入口仍先复验 canonical
  staging profile、部署 trust root 与 Ed25519 签名，之后才在 import-time boundary 中登记 capability 和 executor。
- live runner 与公开 compose 使用同一 import-time boundary；公开模块属性不提供 raw creator/resolver，合法 compose → run →
  持久化报告复验路径保持不变。该约束只描述公开 API 形状，不把 private/closure 当作同进程安全隔离。

### TDD 与回归

1. RED：新增回归先生成并持久化合法 staging report，再枚举模块暴露的 private creator，并用
   `_create_controlled_staging_executor` 把合法 attestation 挂到伪 delegate；现状成功执行全部题目，测试以
   `accepted_forgery=['_create_controlled_staging_executor']` 按预期失败。
2. GREEN：移除模块级 raw creator/resolver 后，单项回归通过；完整 trust-root 合同 `10 passed`，
   同时保留合法 composition、caller executor、copy executor、裸 callback 与 caller-owned revision 防护。

### 影响分析与验证

- 写前 GitNexus CLI impact：boundary builder 为 LOW（1 个模块内直接调用、0 execution flow）；公开 compose 与 live runner
  均为 LOW（0 个直接依赖、0 flow），无 HIGH/CRITICAL。MCP 读取器仍因 LadybugDB 存储版本不兼容返回 UNKNOWN，未将其
  解释为低风险。
- staged detect：`2 files / 12 symbols / 10 affected processes`，汇总风险 HIGH；10 条均为本次 staging conformance 内部
  `compose/run → canonical profile / signature verification / digest-render` 链路，无 API、数据库或其它业务编排外溢。
- T5 target contracts/architecture/topology：`55 passed`；WMS integration contracts：`115 passed`；显式 mock simulator：
  `2 passed`；默认收集审计：`3546 tests collected`。
- 扩大 northbound/WMS architecture：`28 passed, 1 failed`；唯一失败仍为任务明确排除的既有 inventory 测试路径漂移
  （旧 `tests/workline_plugins/rough_sorter/test_conformance.py` 对当前
  `tests/support/rough_sorter_inventory_admission.py`），本轮未修改 inventory/generated 文件。
- `./scripts/git-quality-gate.sh --profile quality` 通过：Ruff、Bandit、348 项 runtime contracts、11 项 process naming、
  import-linter、architecture guardrails 与 test topology 全部通过；`git diff --check` 通过。

## 最终 trust boundary 审计整改追加

### import-time trust root

- 部署 trust root 继续由 `WMS_STAGING_CONFORMANCE_TRUST_ROOTS` 在模块导入阶段读取；composition、live runner 与持久化报告
  verifier 现在绑定同一个 import-time boundary 实例及其中的不可变 registry。
- 运行期间重绑 provider/trust-root 模块属性或修改环境变量，不会替换该 boundary 已持有的 root。测试 trust root 通过先设置部署环境、
  再隔离 reload 两个模块安装，不再猴补丁全局 registry。
- 合法 compose → run → report verify 路径保持，未改 replay support；纯 replay 仍不导入 runtime 或外部 effect 能力。

### 明确威胁模型与公开 API

- 架构设计明确：attestation 可信性依赖同进程代码与部署环境均受信；任意同进程恶意代码执行视为进程完全失陷，不属于本
  conformance attestation 威胁模型。
- private、closure、sealed executor 与 weak registry 只约束公开 API 和误用面，不再描述为抵抗 reflection、`object.__new__`、
  环境或内存读取的安全隔离，也不再作“不可伪造”保证。
- 新增 closure reflection 回归，公开 compose/run/report verifier 不暴露可直接调用的 raw capability creator/resolver；该回归只验证
  API 形状，不声称可抵抗任意同进程执行。

### 本轮 TDD、影响分析与验证

1. RED：trust-root/architecture 定向组合出现 `3 failed, 15 passed`，分别证明模块 registry 重绑定会替换验签 root、公开 compose
   closure 暴露 capability/executor creator，以及架构文档缺少进程信任边界。
2. GREEN：import-time boundary、隔离 reload fixture、closure reflection 与架构约定落地后，定向组合 `18 passed`。
3. GitNexus 写前 impact：`_verify_deployment_attestation` 为 HIGH，3 个直接调用，仅影响 compose/run 两条 staging conformance
   流程；`verify_wms_conformance_report` 为 MEDIUM；安装边界与测试 fixture 为 LOW。无 API、数据库或业务编排外溢，已获得继续
   实施授权。
4. T5 contracts/architecture/topology 定向组合：`60 passed`；WMS integration contracts：`117 passed`；显式 mock simulator：
   `2 passed`；默认收集审计：`3549 tests collected`。
5. `uv run ruff format --check .`、`uv run ruff check .`、`git diff --check` 与
   `./scripts/git-quality-gate.sh --profile quality` 全部通过；Bandit 0 issue、348 项 runtime contracts、11 项 process naming、
   import-linter、architecture guardrails 与 test topology 均通过。
6. staged GitNexus detect：`5 files / 49 symbols / 7 affected processes`，汇总风险 HIGH；7 条均为本次
   compose/run → canonical profile/signature/digest-render 内部链路，无 API、数据库或业务编排外溢。暂存范围不包含用户已有的
   `AGENTS.md`、`CLAUDE.md` 改动。
