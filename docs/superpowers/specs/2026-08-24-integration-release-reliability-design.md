# 联调发布可靠性改进设计

**状态：** APPROVED INPUT FOR PLANNING

**日期：** 2026-08-24

**范围：** `/Users/kaizhou/codeDev/wes_backend` 与既有成对前端发布流程

## 1. 问题

2026-08-24 向 `10.24.199.219` 发布设备诊断版本时，最终部署成功，但过程证明现有门禁仍可能把以下状态误判为完成：

- 前端制品内容哈希正确，但契约记录绑定的后端 Commit 不是本次批准 Commit；
- Nginx 容器刚启动但尚未 ready，立即请求产生 connection reset；
- 数据库已有超级管理员，但其密码与部署环境 `BOOTSTRAP_ADMIN_PASSWORD` 不一致；
- 菜单同步只处理清单内项目，无法识别数据库中额外的历史菜单；
- 本机 frontend 容器挂载一个最新 worktree，但 `dev-env.sh check` 默认读取另一个旧 checkout；
- 现场 Compose 只有 7 个服务，而当前标准生产 Compose 定义 9 个服务；
- 临时脚本复制进非 root 容器后因宿主 UID/mode 不可读；
- 最新一次数据库 dump 权限为 `0644`，且现有证据只有 SHA-256，没有隔离恢复演练。

问题的共同根因是：发布流程验证了局部结果，却没有始终绑定同一份源码、制品、运行拓扑、凭据和恢复证据。

## 2. 已有能力与唯一所有者

以下能力已经存在或已有批准计划，本设计禁止重复实现：

1. 前后端 Commit、OpenAPI SHA、权限 SHA 与镜像 OCI label 的成对校验已由前端 Jenkins 和 `Jenkinsfile.test-deploy` 拥有；本设计只补充操作记录，不新增第二个 release manifest 格式。
2. 菜单多真源问题由 `/Users/kaizhou/codeDev/wes_frontend/docs/superpowers/plans/2026-08-20-frontend-owned-menu-convergence.md` 唯一拥有；目标是删除后端菜单表/API/同步链，而不是扩展 `sync_menus.py`。
3. PostgreSQL 权威备份、`0600` 权限、SHA-256、异机副本和隔离恢复演练由 `docs/superpowers/plans/2026-08-18-wes-onsite-data-recovery.md` 唯一拥有；本设计不再增加临时 backup helper。
4. Redis/THP、Nginx nofile/logrotate、Beat 和 PostgreSQL idle-transaction 加固由 `docs/superpowers/plans/2026-08-18-wes-onsite-runtime-hardening.md` 唯一拥有。
5. 管理员密码修改必须复用现有 `UserService.reset_password`；bootstrap 不承担密码轮换。

## 3. 决策

### 3.1 管理员登录门禁

- `bootstrap_foundation` 继续只创建首个超级管理员；已有超级管理员时不修改密码。
- 应用启动、Nginx 恢复前，使用部署环境中的 `BOOTSTRAP_ADMIN_USERNAME`、`BOOTSTRAP_ADMIN_PASSWORD` 对新 API 容器执行一次真实 `/api/v1/auth/login`。
- 登录必须返回 `code == "1000"` 且 `user.is_superuser is True`；随后调用 `/api/v1/auth/logout` 撤销本次验证会话。
- 脚本不输出密码、Access Token、Refresh Token、Cookie 或完整响应体。
- 登录失败保持维护态。密码恢复属于单独授权的运维动作，不在发布脚本中自动执行。

### 3.2 Nginx readiness

- Nginx 使用 `compose up -d --no-deps --wait --wait-timeout 60 nginx` 启动。
- Compose health 通过后仍使用一个版本化、可测试的 HTTP wait helper 验证 `/health` 和 `/`。
- Jenkins、生产 Runbook 和 Rocky Linux 初始化文档复用同一 helper，不分别维护 retry loop。
- 任一检查失败都重新停止 Nginx；不得把容器 running 当成 ready。

### 3.3 运行拓扑

- 当前生产 Compose 渲染结果是期望服务集合的唯一真源。
- 恢复 Nginx 前，比较 `compose config --services` 与 `compose ps --status running --services`；两者排序后必须完全相等。
- 不增加 `ALLOW_MISSING_SERVICES`、忽略列表或兼容服务别名。若现场需要缩减拓扑，必须提交受测试的 Compose overlay 并单独批准。
- 当前现场缺少 `celery-wms-fulfillment` 和 `flower` 只记录为部署偏差，不把 ECS 诊断通过提升为 WMS fulfillment 验收。

### 3.4 本机源码身份

- `dev-env.sh up/check` 必须验证 frontend 容器 `/app` bind mount 与当前解析的 `WES_FRONTEND_ROOT` 是同一路径。
- macOS Docker Desktop 返回的 `/host_mnt/Users/kaizhou/codeDev/wes_frontend` 与宿主 `/Users/kaizhou/codeDev/wes_frontend` 只做路径规范化，不形成第二份配置。
- 不一致时在 HTTP、seed 和数据检查前失败，并输出 expected/actual 路径。
- 版本输出包含 frontend 绝对路径、Commit 以及 branch；detached worktree 明确显示 `detached`。
- 不自动 stash、切分支、fast-forward 或覆盖 frontend 主 checkout 的 dirty 文件。

### 3.5 发布记录

- 当前 Runbook 增加一份最小发布记录字段清单：前后端 revision/digest、前端绑定的后端 revision、OpenAPI/权限 SHA、schema head、有效 Compose 服务、备份目录/hash、授权目录结果、管理员登录门禁、HTTP readiness 和未验证外部边界。
- 发布记录不保存密码、token、Cookie、`.env.prod` 内容或完整业务 payload。
- 历史记录放在现场受控发布目录和项目外归档，不在项目内累积按日期复制的过程文档。

## 4. 非目标

- 不在本计划中实现或扩展菜单同步。
- 不在本计划中实现备份系统、PITR、Redis 恢复或异机复制。
- 不自动重置现有管理员密码。
- 不创建动态拓扑注册中心、发布数据库或第二套部署框架。
- 不保留旧 schema、旧菜单、旧接口、shim、alias 或双路径。
- 不以健康检查、管理员登录、ECS status 枚举或 SSE 握手证明硬件动作、Callback、Event、WMS 或业务验收。

## 5. 实施顺序

1. 先完成本设计对应的发布门禁增量：登录、readiness、拓扑和 dev-env 源码身份。
2. 独立执行 `2026-08-20-frontend-owned-menu-convergence.md`，删除菜单多真源；若其修改 `dev-env.sh`，必须保留本设计的 frontend mount 身份检查。
3. 独立执行 `2026-08-18-wes-onsite-data-recovery.md`；在其现场部署前，先单独授权把已发现的 `0644` dump 收紧为 `0600`。
4. 按 `2026-08-18-wes-onsite-runtime-hardening.md` 部署宿主运行时资产。
5. 最后进行一次成对发布演练，分别记录工程门禁、部署技术门禁和未验证的现场业务边界。

## 6. 完成条件

- 使用错误管理员密码时，发布在 Nginx 恢复前失败；正确密码通过后验证会话被登出。
- 延迟启动的 Nginx 不再因首次 connection reset 误判失败，永不 ready 的 Nginx 保持入口关闭。
- 标准 Compose 少一个、多个或出现未知服务时，拓扑门禁失败。
- `dev-env.sh check` 无法再使用 A worktree 的容器验证 B checkout 的 seed/版本。
- 当前文档只引用版本化 helper，不复制 retry 实现。
- 菜单与备份改进继续由其既有批准计划拥有，没有新增平行实现。
- 聚焦测试、QUALITY、selector HEAVY 和最终 Review 基于同一 staged 快照通过。
