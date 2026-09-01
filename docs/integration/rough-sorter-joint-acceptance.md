# Phase 8 粗分机后端开发验收与现场边界状态

## 2026-08-31 Transport 0.3 repository alignment

**TRANSPORT 0.3 REPOSITORY ALIGNMENT: ALIGNED；LOCAL IMMUTABLE IMAGE E2E: PASS**。

当前仓内合同、backend、frontend canonical consumer 与粗分换架主链已经对齐。该状态只证明 repository/local Mock
alignment，明确保留：`NOT DEPLOYED / NOT SUPPLIER ACCEPTED / NOT PHYSICAL ACCEPTED / NOT BUSINESS ACCEPTED`。

### 当前不可变证据

| 项目 | 当前证据 |
| --- | --- |
| Backend Transport 0.3 实现 Commit / tree | `33f204bad65ce8ac689a0a03918ec5c81defc692` / `5d48e81794bf4502fa548b5b1de425ca92e4f286` |
| Backend 最终 E2E Commit / tree | `f2129982744af481f8c5a50b14f85a33e614c55b` / `1753c308a8481f5cf625ae6ea42a272907ab2bff` |
| Backend 测试镜像 | `wes-backend:phase8-rough-sorter-f2129982744a`；digest `sha256:feef7a60d8aed787c381e70b35344a247df92bfb48d01b84878a357b244b767b`；OCI revision/tree 与最终 E2E Commit/tree 精确一致 |
| Backend 当前门禁 | Commit hook QUALITY：`2403 passed, 5 skipped`；`ROUGH_SORTER_E2E_BACKEND_IMAGE=wes-backend:phase8-rough-sorter-f2129982744a uv run pytest tests/e2e -q`：`12 passed` |
| Frontend Commit / tree | `aea88687691556b0b115698fdf4056783d69fe6f` / `a52b8421f7fbae402c23cd947c0d0e0b247d27f9` |
| Frontend canonical OpenAPI | `.contract-sync-record.json` 绑定 backend `33f204bad65ce8ac689a0a03918ec5c81defc692`；`contracts/openapi.current.json` SHA-256 `cd539bae4577b69b57ee91809625ee56f115e1cc93b59ce587db7b77e81830f8` |
| Frontend 当前门禁 | 83 files / 624 tests、lint、build、`contract:test`、`contract:verify`、`permission:verify` 全部通过 |

### 核心合同能力与当前调用点边界

| 范围 | 当前状态 | 不得扩大为 |
| --- | --- | --- |
| Transport 0.3 核心合同 | 10 个批准请求场景由 core/ACL/Mock tests 支持；face 为上下文一致的不透明非空 string，不做 A/B 或数值语义转换 | 所有合同场景已有生产业务 flow |
| 粗分生产调用链 | `OLD_OUT = CTU03 / target_face="90"`、`NEW_IN = CTU01 / target_face="270"`；两腿使用不同 `transport_task_id` 并独立闭合，`NEW_IN` 成功无需等待 `OLD_OUT` 即可重新请求 target | `RACK_EXCHANGE`、现场换架已验收或 WMS 库存已闭合 |
| 其它批准的 `RACK_MOVE` | 例如其它单层货架到工作位、五层货架到 `FIVE_STATION` 仍只是核心合同能力 | 已接入生产业务调用点 |
| 510056 `TRANSPORT_DEBUG` consumer | repository-aligned；`WH01` 是 `ZONE`，`KT16` 是 `RACK_POSITION`，去程 `CTU01`、回程 `CTU03`，固定 payload `target_face="90"` | 权威生产业务链或真实物理运行 |

510056 当前状态为 `NOT PHYSICAL RUN / NOT BUSINESS AUTHORITATIVE`。本机测试数据无需脱敏，但测试通过不能替代 WMS/RCS
真实接纳、callback、供应商映射、现场位置事实或 WMS 库存事务。

## 历史 Phase 8 RC 结论（2026-08-20 冻结快照）

**BACKEND FUNCTIONAL IMPLEMENTATION PASS；LOCAL MOCK ACCEPTANCE PASS；BACKEND RC CLOSED**（2026-08-20）。

后端功能实现、本机 Mock 验收、最终快照验证和 GitLab PUSH-only 镜像发布均已完成。GitLab 发布提交为
`f51677b62f5da906d4b60fa5a528d04692aff7a2`，Jenkins #88 发布不可变镜像 `88-f51677b`。前端在独立仓库实现和发布，
不要求与后端版本或进度一一对应。真实 WMS/RCS/ECS、设备、版本选择、Docker 部署和联合验收由现场人员负责，不参与后端 RC
关闭。Mock 只证明 WES 按批准合同执行，不替代供应商实现、物理事实或业务验收。

## 分层状态

| 层级 | 状态 | 当前有效证据 | 证据边界 / 阻塞 |
| --- | --- | --- | --- |
| 核心与共享基础能力 | PASS；最终工作树已验证 | 当前候选快照 QUALITY：`3625 passed, 4 skipped`；本次 selector HEAVY：`85 passed, 0 skipped`；计划锁定的核心 FAST owner：`21 passed` | 本次未修改 Phase 8 生产代码、插件 E2E、migration 或 schema；对应既有相同指纹证据继续有效 |
| WMS Adapter 合同 | PASS | FAST owner、QUALITY 与 HEAVY 均通过；operation、DTO、幂等、`WAIT` 后继与投递未知由仓库合同测试拥有 | 只证明 WES 侧 ACL 与合同；真实 WMS 联调为 `NOT RUN` |
| 粗分业务能力本机 Mock 联调 | PASS | `HEAD@c8144050` 干净 archive production image（source manifest `78313267…`）插件 E2E：`11 passed, 0 skipped` | 真实 WES HTTP/PostgreSQL/Redis/Celery/Beat；WMS/ECS 为 stdlib mock，不证明供应商、PLC、RCS 或现场物理闭环 |
| 后端 RC 镜像 | PASS；PUBLISHED | 不可变标签 `88-f51677b`，manifest `sha256:e38dec0294d406540c734d86c70da85682438627a9f8e685d54e3a2f3883a453`；OCI revision=`f51677b62f5da906d4b60fa5a528d04692aff7a2`，source-manifest=`bd1a1d33d4e27ac54ddbddd126eef660aea1c13c` | Jenkins #88 由 GitLab push 触发并成功；`develop` channel 仅表示最新候选，不作为 RC 关闭或现场选版证据 |
| 供应商与现场联合验收 | OUTSIDE BACKEND RC；NOT RUN | 见 [`rough-sorter-supplier-conformance.md`](rough-sorter-supplier-conformance.md) | 由现场部署验收人员选择版本并执行，不阻塞后端或前端各自发布 RC |

## 已通过的仓内工程与本机 Mock 验收

| 项目 | 值 |
| --- | --- |
| 执行日期 | 2026-08-20 |
| WES Commit / tree | `c81440509f7ab1a312630a00bde3bbc877d7c35a` / `783132672d9a0ac6f5626f56368ad15a53244e0a` |
| 插件 / SDK 版本 | `wes-rough-sorter-plugin 1.0.0` / `wes-plugin-sdk 0.1.0` |
| 后端镜像 | `sha256:19852fdfb89abf8fb77ccc91036a87ccaa049a0aea39ff16d630db39b437fac5`；OCI revision 与 source-manifest 分别严格匹配上述 Commit/tree |
| 环境 | 随机隔离 Docker network；真实 PostgreSQL、Redis、WES API、Celery worker + embedded Beat、fulfillment worker |
| 模拟边界 | stdlib WMS mock、stdlib ECS mock；没有真实供应商或现场设备 |
| 主路径 | `SCAN_COMPLETED → ACCEPT → PICK_AND_PUT → MOVE_FORWARD → ASSIGNED → PICK_AND_PUT → RECORDED → CLOSED` |
| 本机 Mock 边界路径 | WMS `WAIT` 完成本次确认并创建新 operation，`ACCEPT` 前不创建设备命令；ECS `ACK` 后跨真实下一次 Beat 保持 `ACKNOWLEDGED:1` 且不重放，匹配 callback 后才关闭 execution |
| 插件 E2E | `11 passed, 0 skipped` |
| QUALITY / 本次 selector HEAVY | `3624 passed, 4 skipped` / `85 passed, 0 skipped` |

测试启动后用 direct SQL 设置初始环境：静态 WorkLine/Epoch/设备/位置配置，以及 Phase 7 DeviceStatusObservation 和 Transport RackPlacement 两个可信运行态投影。direct SQL 不证明这两个投影的生产 owner，也不写本次 material execution、evidence、command 或 confirmation 状态；这些对象全部从 HTTP ingress 开始由真实 WES 路径形成。最终以只读 SQL 断言可靠对象状态与唯一性。该结果是可重复的本机模拟验收，不是生产路径或现场证据。

## 现场部署与验收边界

- 现场人员自行选择已发布的前端和后端版本，完成 Docker 配置、部署、真实系统联调和验收记录。
- 产品仓库不提供专用现场 runner、session、bundle、跨仓库 manifest 或证据平台。
- 现场发现问题后按 WMS、WES、RCS、ECS 或部署 owner 回流；需要修改软件时发布新镜像，不覆盖原 RC。
