# 生产环境部署 Runbook

> 本文只描述当前独立发布入口。前端、后端和 release checker 分别构建、测试和发布；生产环境只能由独立 release orchestrator 改变。Producer 发布成功仅表示 `PUBLISHED — NOT DEPLOYED`。

## 1. 发布边界

- 发布范围只有 `FRONTEND`、`BACKEND`、`BOTH`。
- `FRONTEND` 只提供 frontend candidate digest；`BACKEND` 只提供 backend candidate digest；`BOTH` 必须同时提供两侧 candidate digest。
- 单侧发布的当前对端由 orchestrator 从 live container 和最近成功的 `compatibility-report.json` 自动发现并交叉验证，操作员不得重新指定对端。
- 跨镜像兼容只判断“前端实际需求是否被后端实际能力满足”。前后端 Commit、tree 和 digest 仅作制品身份与审计，不要求相等。
- 镜像内原始发布制品及其 `org.wes.release.*` OCI label 是兼容与模式分类输入；外部参数不得覆盖或伪造这些事实。
- 发布基础能力不读取菜单 manifest，不同步菜单数据库。菜单由前端静态路由拥有。
- WMS/ECS 联通、Callback 闭环、设备物理完成和业务验收不属于本 Runbook 的成功证据。

## 2. 唯一操作入口

生产部署使用与 `Jenkinsfile.test-deploy` 同合同的独立 release orchestrator。本文解释输入、门禁和失败处理，不复制 Jenkins shell；实际命令与顺序以批准的 deploy-source 中 orchestrator 为准。

必填输入：

| 参数 | 规则 |
| --- | --- |
| `DEPLOY_SCOPE` | 只能是 `FRONTEND`、`BACKEND`、`BOTH` |
| `FRONTEND_CANDIDATE_DIGEST` | `FRONTEND`、`BOTH` 必填；`BACKEND` 必须为空 |
| `BACKEND_CANDIDATE_DIGEST` | `BACKEND`、`BOTH` 必填；`FRONTEND` 必须为空 |
| `DEPLOY_SOURCE_COMMIT_SHA` | 固定 Compose、orchestrator、运行配置清单和 checker digest 的部署源码完整 Commit |

可选输入：

| 参数 | 规则 |
| --- | --- |
| `FORCE_FULL` | 只允许把自动判定的 FAST 升级为 FULL；不存在 force-FAST |
| `WARN_APPROVAL_REASON` | checker 返回 WARN 时由获授权操作员填写的非空理由；PASS 时留空 |

禁止接受旧参数或等价替代，包括 paired image tag、目标 backend Commit、frontend backend-contract revision、操作员提供的 OpenAPI/permission SHA 或 checker digest。

## 3. 发布前准备

操作员在运行 orchestrator 前确认：

- deploy-source 是已批准的干净 Commit，且其中固定的 checker digest 可从受控 Registry 拉取；
- 所选 candidate 使用不可变 digest，而不是 channel tag；
- `.env.prod`、WMS provider profile 和 Compose 所需秘密已在现场受控配置中提供，日志只记录批准配置文件的 SHA-256，不记录值；
- `/srv/wes/releases/` 可写，发布目录及报告只允许授权运维账号访问；
- PostgreSQL 备份目标、恢复命令和维护窗口已确认；
- 外部入口、管理员凭据、Registry、磁盘空间和 Docker/Compose 满足既有现场要求；
- WMS Provider profile 可读且能通过当前后端配置校验。该预检不探测真实 WMS/ECS，也不证明物理或业务验收完成。

首次使用新门禁时没有上一份有效新格式报告，自动进入 FULL，并以 `DEPLOY_SCOPE=BOTH` 建立基线。

## 4. 维护前门禁

Orchestrator 必须在关闭外部入口之前完成以下工作；任一失败都以 `PRE_CUTOVER_ABORTED` 结束，现场运行环境不得改变：

1. 校验 scope 与 candidate 输入矩阵，并把候选引用固定到 digest。
2. 从 deploy-source 固定 checker digest。
3. 单侧发布时同时读取 live peer digest 与最近成功报告；缺失、不一致或无法证明时阻断。
4. 校验两侧镜像各自的 OCI revision、原始发布制品和 `org.wes.release.*` SHA-256 label。revision 只与镜像自身核对，不做跨镜像相等比较。
5. 读取当前 DB head，计算有效配置、Compose 和 cutover 输入的 SHA-256；不得输出秘密内容。
6. 由 checker 验证 required permissions 是 provided permissions 的子集，并只针对 required operations 检查 OpenAPI。
7. 使用所选后端镜像编译校验 WMS Provider profile。
8. 生成并归档确定性的 `compatibility-report.json`。

Checker 硬超时 60 秒：

- `PASS`：继续。
- `WARN`：暂停；只有提供 `WARN_APPROVAL_REASON` 才能继续。批准必须绑定本次 frontend、backend、checker digest 和 diff hash，任一变化即失效。
- `ERR`、超时、异常或报告不合法：阻断，不进入维护态。

## 5. FAST 与 FULL

模式由当前成功发布证据、现场事实和候选镜像内容决定，不使用候选 Commit changed paths 代替内容指纹。

以下任一变化或证据缺失自动进入 FULL：

- Backend：migration tree、依赖输入、provider OpenAPI、provided permissions、生产 recipe/entrypoint；
- Frontend：依赖/lockfile、consumer OpenAPI、required operations、required permissions、生产 Dockerfile/Nginx 配置；
- Deploy：实际 Compose、cutover 脚本或其声明的运行配置；
- Runtime：DB head、有效 `.env`、WMS provider profile 等批准配置 hash；
- 首次基线、上一版证据缺失或任一事实读取异常。

FAST 还要求现场 DB head 与候选 backend expected schema head 精确一致。FULL 只允许数据库从已知祖先向前迁移；多 head、未知 revision、倒退或分叉均在维护前阻断。

## 6. Cutover

进入维护态前，单侧发布必须再次读取当前 peer digest；若与报告不一致，立即以 `PRE_CUTOVER_ABORTED` 结束并重新预检。

### 6.1 FAST

- 只切换 scope 选中的一侧。
- `FRONTEND` 不重建后端服务。
- `BACKEND` 必须让 API、Celery、WMS fulfillment、Beat 和 Flower 一次使用同一 backend digest，禁止混合版本。
- 完成后执行内部 readiness、管理员真实 login/logout、精确 Compose topology 和最终外部 HTTP 检查。

### 6.2 FULL

`FRONTEND` FULL 关闭 Nginx 后只停止并重建 frontend；它不备份数据库、不执行 migration，也不运行权限 mutation。恢复入口前仍必须使用当前 backend 执行权限 `--check`，并通过 frontend asset、管理员 login/logout、内部 readiness 和精确 topology。

`BACKEND` FULL（以及包含 backend 的 `BOTH` FULL）关闭 Nginx 后按 orchestrator 顺序完成：

1. 停止应用服务并保持 PostgreSQL、Redis 可用于备份和取证；
2. 创建可验证的数据库备份；
3. 仅执行已批准的 forward migration；
4. 对已有数据库执行权限收敛并重新运行独立 `--check`；精确 post-commit cache marker 仍只允许 repair 一次，禁止重跑 mutation；
5. 原子重建全部 backend services；`BACKEND` 不重建已部署 frontend，`BOTH` 才同时重建两侧；
6. 执行真实数据库查询、管理员 login/logout、精确 topology 和共享 HTTP readiness；
7. 全部门禁通过后才恢复 Nginx，并再次验证外部 `/health` 与首页。

菜单收敛按两个独立候选顺序发布：先以 `DEPLOY_SCOPE=FRONTEND` 发布新 frontend，并由 checker 允许“新 frontend + 当前旧 backend”；再以 `DEPLOY_SCOPE=BACKEND` 发布新 backend。此时 checker 必须拒绝仍要求菜单 API 或 `/auth/my.menus` 的旧 frontend，不能用前后端 Commit 相等替代兼容判定。frontend 镜像内的静态路由与 `meta.menu` 是菜单唯一真源，backend FULL 不依赖 frontend 源码、menu manifest 或其它菜单产物。

首次空站点初始化不是日常发布分支。只有明确确认数据库为空且执行现场初始化计划时，才可在 FULL 维护态执行 migration、注入 `BOOTSTRAP_ADMIN_*`、运行 `bootstrap_foundation.sh` 和新的权限 `--check`；后续发布不得新建或替换数据库来规避 migration。

## 7. 失败与恢复

- 维护前失败：`PRE_CUTOVER_ABORTED`，当前环境保持不变。
- 进入维护态后失败：`CUTOVER_FAILED_MAINTENANCE_HELD`，Nginx 保持关闭，PostgreSQL 与 Redis 保留用于诊断。
- 未发生 migration 时，可在确认上一份成功报告、先前 digest 和配置仍有效后，由同一 orchestrator 选择恢复。
- migration 后禁止无条件自动切回旧镜像。只能 forward-fix，或在单独授权后恢复已验证的数据库备份和与其匹配的先前镜像。
- 禁止临时恢复 exact-commit 门禁、旧标签、旧 schema、双路径或 force-FAST。

## 8. 发布证据

每次运行在 `/srv/wes/releases/${RELEASE_ID}/` 保存并由 Jenkins 归档：

- `compatibility-report.json`；
- candidate、current 与 checker digest；
- 两侧原始发布制品 hash；
- deploy-source Commit、两侧 OCI revision 和 source identity；
- 自动/有效模式、排序后的 mode reasons；
- 不含秘密内容的配置 hash、部署 Compose/cutover 输入 hash、DB head；
- WARN 批准绑定与理由（如有）；
- 最终状态和各门禁结果。

成功状态只能说明本次 FAST 或 FULL 已由 orchestrator 完成。它不证明供应商协议一致、真实 WMS/ECS callback loop、设备物理完成或生产业务验收。

## 9. 操作检查清单

- [ ] scope 与 candidate digest 输入矩阵正确，未填写任何旧 paired/hash 参数；
- [ ] deploy-source 和其固定的 checker digest 已批准；
- [ ] 当前 peer、上一份成功报告、配置 hash 和 DB head 已被预检交叉验证；
- [ ] checker 为 PASS，或 WARN 已提供绑定本次三个 digest 与 diff hash 的理由；
- [ ] 自动/有效 FAST 或 FULL 原因可解释，不存在 force-FAST；
- [ ] FULL 已完成备份和仅向前 migration，FAST 未执行数据库 mutation；
- [ ] 后端切换没有混合 backend service digest；
- [ ] 管理员 login/logout、精确 topology、内部及外部 readiness 通过；
- [ ] `compatibility-report.json` 与最终状态已落盘并归档；
- [ ] 外部联调、物理完成和业务验收仍明确标为未验证，除非另有对应证据。
