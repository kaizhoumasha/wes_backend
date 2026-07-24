# T8b 实施报告：冻结 EXTERNAL_HTTP canonical 请求体

## 结论

T8b 已完成。所有本任务覆盖的 `EXTERNAL_HTTP` 出站路径在领域派发边界只序列化一次，并把冻结的
`canonical_payload_bytes` 与其 SHA-256 一同写入 `SystemOutbox`。发送、失败重试与恢复发送只读取原始 bytes/hash，
不再读取或重新序列化 `payload_json`；后者仅保留为查询投影。非 HTTP outbox 不要求 canonical 字段。

本任务没有实现 T8c typed transport result、T8d reducer、T8e lease 或 T8f credential snapshot，也没有添加旧字段
alias、兼容 fallback 或旧数据迁移。

## 合同与实现

- 新增冻结值对象 `CanonicalPayload`、typed endpoint 快照和 `ExternalHttpDispatchRequest`。
- canonical JSON 固定为 UTF-8、key 排序、无多余空白、拒绝 NaN；hash、HMAC 和 HTTP body 直接使用同一 bytes。
- `DispatchEnvelope` 只对 `EXTERNAL_HTTP` 强制 bytes/hash，并在写入前核对查询投影；其他 dispatch type 保持原合同。
- `SystemOutbox`、`SystemOutboxCreate`、Repository 与数据库约束均对缺失 canonical 字段 fail closed；持久化后的
  `payload_json`、bytes、hash 不允许通过更新 Schema、Repository 或 ORM update 改写。
- endpoint registry 返回 typed endpoint；sender 只接收 frozen typed request，并以 `content=原始 bytes` 发送。
- WMS inventory/fulfillment、handling、rack 与 station lease 派发链路均传递冻结 bytes/hash；重复派发复用已持久化
  bytes，不以新投影修补已存在的 outbox。
- 生成 Alembic revision `df58f4068f02`，为 `wes_biz.system_outbox` 增加 nullable BYTEA、64 位 hash 与
  EXTERNAL_HTTP 条件约束；没有 backfill。

## 验证结果

- 本机 Docker PostgreSQL：从空库 `alembic upgrade head` 成功，当前版本为 `df58f4068f02 (head)`。
- PostgreSQL BYTEA 精确往返集成测试：`1 passed`。
- canonical、SystemOutbox、handling、rack、WMS、runtime capability 联合回归：`582 passed`。
- `tests/sys`：`25 passed`。
- canonical + EFFECT fixture 最终复验：`20 passed`。
- 测试拓扑守卫：`6 passed`；显式 collect-only：`3619 tests collected`。
- `./scripts/git-quality-gate.sh --profile quality`：通过（Ruff format/check、Bandit、runtime guardrails、
  import-linter、测试拓扑均通过）。
- 默认快速全集初跑：`3609 passed, 5 skipped, 5 failed`；其中 2 个失败是本任务严格合同暴露的旧 EFFECT
  测试 fixture，修复后该文件 `8 passed`。剩余 3 个是本分支既有 cleanup/legacy/northbound inventory 生成清单
  漂移，与 T8b 执行路径无关。
- `alembic check` 仍报告仓库既有的大范围 schema/autogenerate 漂移；T8b canonical 字段注释对齐后，输出中不再有
  `canonical_payload_bytes` 差异。

## 影响分析

GitNexus 对共享 `DispatchEnvelope`、`SystemOutboxBase/SystemOutbox`、`SystemOutboxCreate` 报告 HIGH，主要来自共享
模型的广泛 import；其余生产符号最高为 MEDIUM。按任务授权继续后，已覆盖直接消费域与完整质量门禁。提交前另执行
staged `gitnexus_detect_changes`：28 个文件、134 个 changed symbols、12 个 affected processes、风险 HIGH；影响流程集中在
handling retry、rack request 与 SystemOutbox dispatch，均已被上述回归覆盖。提交范围不包含用户维护的 `AGENTS.md`、
`CLAUDE.md`。
