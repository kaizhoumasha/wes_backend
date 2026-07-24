# T12 实施报告：北向 legacy removal 全量归零

## 结论

T12 已完成北向 WMS 遗留路径硬切换。T1 operation inventory 由 14 行收口为仅表头，
生成式零搜索报告由初始 157 个 finding 收口为：

- `finding_count=0`
- `remaining_inventory_count=0`

生产与测试代码不再保留字符串式方法清单、`effect_contracts`、binding/WorkLine 方法快照、
旧 dispatch 状态字段、boolean sender、孤立 WMS typed family service、旧 endpoint config 或
`sys.EndpointRegistry` 中的重复 WMS 北向 target。没有新增 alias、compat adapter、fallback、
旧数据回填或平行 dispatcher。

## `full_box_exchange` typed EFFECT

最后一个真实 inventory operation `wms.fulfillment.full_box_exchange@v1` 已接入既有 T8 EFFECT 平台：

- typed request/result、effect admission/precondition、intent/effect/callback adapter、handler 和 Definition；
- `OUTBOX_ASYNC` 双账本准备服务复用现有 durable acceptance 边界；
- gateway 从 typed Provider catalog 冻结 target/auth binding，不执行外部 I/O；
- operation key 与 dispatch key 由 provider、rack、empty box、full box 稳定派生；
- rough-sorter consumer 只产出 `SYSTEM_CAPABILITY` intent，并保留满箱对象过滤后的逐件分拣候选证据；
- workline plugin 与 system capability 生成索引同步为 1 个 plugin、7 个 system capabilities。

TDD 首轮 full-box 合同/consumer 测试为 6 failed、1 passed；最小实现后 7 passed。

## 元数据与运行时收口

- `ExternalContractProfile` 只保留 provider identity、入站 normalizer、安全和 fixture 元数据；
  query/effect 方法字符串和对应 admission API 已删除。
- `RuntimeCapabilityContext` 只负责 typed Port 注册与实例获取，不再按 provider 字符串方法清单代理或过滤。
- plugin activation 只冻结 provider profile、typed config、device snapshot 和生成索引；
  不再生成方法字符串快照。
- `WorklinePluginBinding.port_requirements_json`、
  `WorkLine.active_plugin_port_requirements_json` 以及 migration inventory 对应 DTO/摘要均删除。
- attempt runtime 使用 dispatch pin 对应的完整 provider profile identity，不再构造收窄副本。
- `FixtureCase.expected_port` 与 WMS/ECS fixture 字符串字段删除；
  integration lab 仅保留与方法清单无关的 case coverage。

## WMS endpoint 唯一真源

孤立且无生产调用方的 `WmsTypedPortService`、`WmsEndpointConfig`、旧 HTTP client、service locator
及其专属测试已删除。QUERY factory 和 EFFECT gateway 统一使用 typed Provider catalog：

- `WMS_SYNC_BASE_URL` 是 WMS typed operation 唯一根地址；
- operation 自身声明 `endpoint_path / target_code / http_method / budget`；
- EFFECT 冻结时按 pinned provider profile 构造 typed endpoint registry；
- 通用 `sys.EndpointRegistry` 仅保留 RCS target，不再重复声明 WMS 北向 operation target；
- workline generic external-http allow-list 同步移除旧 WMS inventory transaction target。

## Migration

Alembic 通过 revision generator 生成 `5d251fdbb1e8`：

- upgrade 直接删除两个字符串方法快照列；
- downgrade 只恢复空列结构，不恢复或迁移旧数据；
- 未实现旧数据 backfill、转换或双写。

PostgreSQL 17 使用独立 `timescale/timescaledb:latest-pg17` 临时容器验证：

- 空库 `alembic upgrade head` 成功；
- upgrade 后两个目标列均不存在；
- downgrade 到 `7824db01402d` 后两个非空 JSON 列恢复；
- re-upgrade head 成功；
- migration inventory PostgreSQL heavy tests：12 passed；
- 验证后临时容器已停止并删除。

## 生成报告与架构守卫

新增唯一生成器 `scripts/generate_northbound_legacy_removal_report.py` 和 JSON snapshot。
守卫扫描 `src/`、`tests/` 下的 Python/JSON/YAML/TOML，检查：

- 字符串式方法清单及快照；
- `effect_contracts`；
- 旧 WMS config/service/target；
- 旧 dispatch 状态字段；
- `dispatch_external_http` boolean return annotation；
- T1 inventory 必须为空。

仅守卫自身与完成态 inventory 测试允许保存禁词。原 T1 大型迁移扫描器已替换为稳定 CSV schema +
零行断言，避免把已完成的 typed operation identity/metric 继续误报为待迁移项。

cleanup matrix 在 P1 复核中通过唯一生成器重建为 608 entries、112 phase4 carriers、0 pending-review；
新增的两条 full-box consumer 测试符号按目标态 `test-only + kept-config-only` 规则进入 closure ledger，
112 个 carrier 全部关闭且仍无 active carrier。矩阵、ledger、closure 定向合同与完整架构回归结果见下方 P1 记录。

## 回归与质量门禁

- full-box typed 合同与 consumer：7 passed。
- T12 主要元数据与 runtime 定向回归：首轮 208 passed、1 个旧断言失败；修复后对应 52 passed。
- 首轮默认回归：3779 passed、5 skipped、5 failed；失败均为删除后测试快照未同步。
- 失败域定向复验：106 passed。
- 第二轮完整默认回归：3784 passed、5 skipped、0 failed，耗时 401.50s。
- 显式 collect：3789 tests。
- `./scripts/git-quality-gate.sh --profile quality`：通过。
  - 1057 files Ruff format check；
  - Ruff lint 全绿；
  - Bandit 104536 行、0 issues；
  - runtime toggle/readiness/production closure；
  - 345 项 runtime contract guardrails；
  - business legacy absence final gate；
  - 11 项 process naming；
  - import-linter；
  - enforced architecture 0 violations；
  - topology 6 passed。

## GitNexus 风险与变更检测

写前已对所有修改的既有生产 symbol 执行 upstream impact analysis：

- `from_provider_profile`：CRITICAL，2 direct / 28 total；
- `validate_activation_configuration`：CRITICAL，2 direct / 24 total；
- `create_attempt_runtime`：CRITICAL，10 direct / 34 total；
- `WorkLine`：HIGH，26 direct / 198 total；
- `ExternalContractProfile`、`RuntimeCapabilityContext`、binding/migration inventory 等为 MEDIUM；
- full-box operation/gateway/Definition、endpoint loader、QUERY factory 等为 LOW。

HIGH/CRITICAL 均在修改前上报；授权后继续，并以完整默认回归、PG17 和 quality gate 验证。
最终 unstaged detect 为 LOW：71 files、73 symbols、0 affected processes。该结果包含用户维护中的
`AGENTS.md`、`CLAUDE.md`。排除两者后的 staged detect 仍为 LOW：83 files、118 symbols、
0 affected processes；提交明确不包含这两个用户文件。

## P1 复核：cleanup matrix 与 closure 同步

评审发现唯一 generator 对当前 parser surface 识别为 608 条，而已提交 CSV 仍为 606 条，
缺失项恰为 `test_full_box_exchange_typed_effect_consumer.py` 的两个测试符号。P1 未手补 matrix CSV、
未新增 allowlist、未放宽 guard；只重跑 `scripts/generate_legacy_matrix.py`，并按生成结果同步审计文档和
business closure ledger。

- matrix：608 entries、165 tests、338 rebuild、112 phase4 carriers、155 workline_runtime、0 pending-review；
- closure：112 entries，其中 53 moved、10 test-only-migrated、29 kept-config-only、20 already-removed；
- 两个 full-box consumer entry 均为现存目标态测试，登记为 `test-only + kept-config-only`。
- business legacy absence final gate：通过；
- matrix / absence / closure 定向合同：38 passed；
- 完整 architecture：376 passed、1 skipped；
- 完整默认回归：3784 passed、5 skipped、0 failed，耗时 404.57s；
- `./scripts/git-quality-gate.sh --profile quality`：通过。
