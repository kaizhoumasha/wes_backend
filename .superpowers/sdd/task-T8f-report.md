# T8f 实施报告：冻结外部 HTTP 目标与版本化凭据引用

## 结论

T8f 已完成。所有生产 `EXTERNAL_HTTP` outbox 在 author-time 由 typed Provider profile 冻结 profile identity/hash、
operation identity、binding revision、非秘密 target snapshot/hash、认证方案与版本化 credential reference。派发与重试
只从持久化快照重建请求；发送前按精确 reference 解析 secret，reference 缺失、不可用或已撤销时在任何网络 I/O 前
fail closed，且不会切换到当前配置或最新 key。

本任务没有持久化自由 URL、自由 header、secret material 或签名值，没有兼容映射、业务数据回填、T8g crash matrix、
业务 capability 迁移、Jenkins/GitLab 验证。

## 冻结合同与 author-time 边界

- `ExternalHttpProviderProfileDefinition` 与 `ExternalHttpBindingDefinition` 只接受显式声明的 operation/target；
  `freeze_external_http_binding` 只在 outbox 创建时读取 endpoint registry，并拒绝自由 URL、未知 target、非 POST、
  URL userinfo/query/fragment 及非版本化 credential reference。
- `ExternalHttpTargetSnapshot` 固定 target code、完整 HTTP(S) URL、POST 与正 timeout；canonical JSON 的 SHA-256 作为
  `target_snapshot_hash`。profile canonical contract 同样生成 `provider_profile_hash`，binding revision 作为独立冻结版本。
- `FrozenExternalHttpBinding.from_persisted` 在派发前重新校验 snapshot/hash、profile/hash、operation 与 target code 一致性；
  任一篡改都在网络 I/O 前收口为受控 NOT_SENT。
- typed WMS material-flow、legacy handling/rack transport 与 plugin runtime 分别拥有固定 profile、operation、target allowlist
  与 credential reference。generic runtime 的 WMS target 也是 catalog 内显式 authoring，不能通过请求注入 URL。
- `DispatchEnvelope` 要求 EXTERNAL_HTTP 携带冻结 binding；`SystemOutbox` 的模型、create/update schema、repository update
  hook 与重复 outbox shape 校验共同保证冻结字段不可变。非 EXTERNAL_HTTP 不允许携带这些字段。

## 凭据、认证与失败语义

- `VersionedCredentialProvider` 是 sender 前的 secret provider 边界。默认实现只把 allowlist 中的版本化 reference 精确
  映射到单个环境变量；内部 mapping 复制为只读视图，调用方不能在运行中改写 allowlist。
- `WES_REVOKED_EXTERNAL_HTTP_CREDENTIAL_REFERENCES` 提供显式撤销清单。撤销优先于环境变量解析；旧 outbox 收到
  `CREDENTIAL_REVOKED` 且 `safe_to_retry=False`，不自动改用新 reference。
- credential rotation 通过新 Provider profile/binding revision/reference 生效：旧 outbox 保留原 URL/hash/ref，新 outbox
  才使用新版本。
- `ExternalHttpDispatchRequest` 仅由冻结 binding、canonical bytes/hash、当次 timestamp/nonce 与刚解析的 secret 构建。
  HMAC-SHA256 覆盖 method、path、timestamp、nonce、payload hash；secret 不保存在 request，签名字段不进入 repr。
- sender header 为固定封闭集合，不接受自由 mapping。凭据解析异常、sender 异常和 transport 异常只记录受控 code 或
  exception type，不记录原始异常文本、URL、header、reference、secret 或签名；diagnostic/evidence 同样只收到受控消息。

## 持久化与 Migration

- `SystemOutbox` 新增 `provider_profile_hash`、`binding_revision`、`target_snapshot_json`、`target_snapshot_hash`、
  `auth_scheme`、`credential_reference` 六个 EXTERNAL_HTTP-only 字段；数据库 CHECK 要求完整冻结 shape、64 位 hash 与
  `HMAC_SHA256`。
- Alembic generator 创建 revision `7824db01402d`，父 revision 为 T8e 的 `2c1407a3606e`。migration 只新增/删除列与
  CHECK，没有 UPDATE、INSERT、backfill 或兼容数据迁移。
- Docker PostgreSQL 已完成 `2c1407a3606e → 7824db01402d → 2c1407a3606e → 7824db01402d` 往返，最终为
  `7824db01402d (head)`。真实数据库用例确认 canonical BYTEA、六个冻结字段精确 round-trip，以及 ORM 更新
  credential reference 被 immutable hook 拒绝。

## TDD 与验证结果

- RED → GREEN 覆盖 profile/target rotation、未知 target/自由 URL 拒绝、snapshot/hash 篡改、精确版本解析、凭据缺失
  与撤销、provider allowlist 不可变、固定认证 header、provider/sender 异常脱敏、无网络调用 fail-closed、模型/schema/
  migration 合同及所有生产 authoring 入口。
- 冻结合同与派发安全测试：`15 passed`；sys/contracts 与定向 workline 回归：`208 passed`；outbox engine/delivery/
  repository 子集：`55 passed`；canonical/transport：`27 passed`；rack/transport：`76 passed`。
- 完整相关域回归：`1614 passed`，覆盖 WMS contracts/integration、handling、rack、workline runtime 与 runtime
  orchestration；仅有 5 条既有 deprecation warning。
- Docker PostgreSQL integration：`2 passed`；测试拓扑守卫：`6 passed`；显式默认收集：`3774 tests collected`。
- `git diff --check`、Ruff format/check 均通过。
- `./scripts/git-quality-gate.sh --profile quality` 通过：Ruff、Bandit（0 issues）、348 项 runtime contract
  guardrails、11 项 process naming、import-linter、architecture guardrails（0 violations）与测试拓扑均通过。

## 影响分析与提交边界

写前 GitNexus impact：`dispatch_external_http` 为 CRITICAL（17 个上游、8 个直接调用、1 条 dispatch process、5 个
模块）；`ExternalHttpDispatchRequest`、`DispatchEnvelope`、`SystemOutbox` 及 create/update schema 为 HIGH（最高约
167 个上游、17 个直接调用）；handling 主入口为 MEDIUM；typed WMS/legacy/plugin authoring helper、engine 与 sender
多为 LOW。上述 HIGH/CRITICAL 传播面均在编辑前报告，并由 1614 项相关域回归、真实 PostgreSQL、完整 quality 与
安全失败路径覆盖。

内置 MCP 绑定主工作区，不能读取本 worktree 的 staged diff；因此使用 `--index-only --branch` 刷新 worktree 专属
索引（不写 AGENTS/CLAUDE/skills），再执行等价 staged GitNexus detect。最终结果为 `38 files / 265 symbols /
7 affected processes / HIGH`；affected flows 集中于 handling retry/envelope 与共享外部 HTTP 派发，文件清单和传播面
均符合 T8f 预期，没有出现范围外业务迁移。

提交范围仅包含 T8f 实现、generator migration、测试与本报告，明确排除用户维护的 `AGENTS.md`、`CLAUDE.md`。

## P1 复审：PostgreSQL fixture 与 rotation 重入

复审发现两处 P1：PostgreSQL integration fixture 仍在构造旧版 `EXTERNAL_HTTP` outbox，缺少六个冻结字段；rack
operation 与 single-layer rack orchestration 的同键重入会先读取当前 endpoint registry，再与历史 outbox 比较。
这会让 profile/credential rotation 后的合法 replay 因当前配置变化而失败，并违反“已存在 outbox 只信任持久化冻结
快照”的合同。

修复后，rack 请求的 canonical identity 构建与 author-time binding freeze 分离。新 dispatch 仍冻结当前 typed profile；
同 `dispatch_key` 的既有 dispatch 则只通过 `FrozenExternalHttpBinding.from_persisted` 重建并校验旧 outbox 的六个冻结
字段，既不读取 endpoint registry，也不重新解析当前 profile。immutable request/payload 变化仍按冲突拒绝。没有放宽
`SystemOutbox` 模型约束，也没有新增兼容、fallback 或 backfill。

PostgreSQL fixture 改用统一 frozen outbox factory 构造完整快照。对所有同时出现 `SystemOutbox(` 与
`EXTERNAL_HTTP` 的生产/测试文件做了直接构造审计：生产 authoring 均携带完整 frozen binding；测试中仅严格拒绝
用例有意构造缺失 shape，其余均通过 typed envelope、真实 freeze 或统一 factory 创建。

本轮写前 GitNexus impact 中 integration fixture helper 为 MEDIUM（6 个直接调用、8 个受影响符号），rack 与
single-layer replay 相关符号均为 LOW；没有新增 HIGH/CRITICAL 风险。RED 先复现 PostgreSQL fixture 在 seed 前触发
`EXTERNAL_HTTP SystemOutbox requires frozen target and credential binding`，并证明 rotation replay 会访问 registry；
GREEN 后同键 replay 在 registry 禁用时成功，immutable request 变化仍冲突。

复审验证结果：rotation 定向 `4 passed`；rack/transport/single-layer `82 passed`；完整相关域 `142 passed`；原始
Docker PostgreSQL 文件 `8 passed`，相关 PostgreSQL 集合 `16 passed`；测试拓扑守卫 `6 passed`；显式默认收集
`3780 tests collected`。Ruff、`git diff --check` 与完整 `quality` profile 均通过，quality 包含 Bandit 0 issues、
348 项 runtime guardrails、11 项 process naming、import-linter、architecture 0 violations 及测试拓扑守卫。

刷新 worktree 索引后的 staged GitNexus detect 为 `9 files / 49 symbols / 5 affected processes / MEDIUM`；受影响流程
仅涉及 `plan_single_layer_rack_dispatch` 与 `request_operation_tasks` 的既有 rack 派发路径，符合本次复审范围。
