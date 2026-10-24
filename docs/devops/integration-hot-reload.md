# 联调服务器源码热更新

该模式用于开发阶段快速把本机前后端源码同步到 `CANTAISYS@100.94.216.118`。它复用联调服务器当前后端镜像中的 Python
依赖、现有 PostgreSQL/Redis、环境变量和日志目录，不构建或推送新镜像。

它不是正式发布证据。Jenkins immutable image、release checker、migration、供应商一致性和业务验收仍按各自流程执行。

## 工作方式

- 后端只同步 `src/`、`deployment/`、人工/自动两个插件的 `workline_plugins/*-picking/src/` 和 `main.py`；API 由 Uvicorn 自动重载，Celery Worker 与 Beat 使用开发重载入口，显式监听两个插件目录；Flower 使用相同源码挂载并在同步后重启。
- 前端同步 Vite 运行源码和配置，使用服务器持久化 `node_modules`/pnpm store，通过 Vite HMR 更新页面。
- `.env*`、`.git`、数据库、Redis、日志、报告、本机虚拟环境和前端 `node_modules` 不进入上传包。Vite 仅可写服务器侧热更新源码副本中的生成文件。
- 后端 `pyproject.toml`、`uv.lock`、人工/自动插件各自的 `pyproject.toml` 或 `migrations/` 与当前基础镜像不一致时停止，必须先完成正式后端发布和数据库迁移。
- 已启用后，前端依赖清单或热更新控制文件变化时，普通 `sync` 停止；使用 `bootstrap` 重新建立基线。
- 正式 Jenkins TEST 发布前先执行 `disable`，恢复启用热更新前保存的前后端 immutable image。
- 日常 `sync` 原子更新目录挂载中的源码，然后重启原容器中的 API、Celery Worker/Beat、Flower 和前端应用，再检查就绪状态；不重建容器、镜像或数据库。
  `main.py` 单文件挂载以及三个 Celery 重载 shell 脚本均纳入控制指纹，发生变化时必须使用 `bootstrap` 重建。

## 使用

从后端仓库执行，前端仓库默认是相邻的 `../wes_frontend`，服务器默认使用 SSH 配置中的认证信息：

```bash
# 首次启用；也用于前端依赖或热更新控制文件变化后的重建
./scripts/integration-hot-sync.sh bootstrap

# 日常前后端源码更新
./scripts/integration-hot-sync.sh sync

# 只读健康检查
./scripts/integration-hot-sync.sh check

# 正式 Jenkins TEST 发布前恢复 immutable image
./scripts/integration-hot-sync.sh disable
```

若仓库不在默认位置：

```bash
WES_FRONTEND_ROOT=/absolute/path/to/wes_frontend \
./scripts/integration-hot-sync.sh sync
```

可通过 `WES_INTEGRATION_HOST`、`WES_INTEGRATION_USER` 和 `WES_INTEGRATION_ROOT` 覆盖默认服务器、SSH 用户和
`/srv/wes/app/current-single` 部署目录；需要指定非默认私钥时使用 `WES_INTEGRATION_SSH_KEY`。私钥和密码不得写入仓库。
远端复用 `current-single/compose.sh` 中已经冻结的 Compose 文件和候选镜像组合，不自行拼接或替换正式发布输入。

## 失败边界

传输先进入服务器的 `.integration-hot/uploads/`，两个压缩包均校验并解压后才同步到运行源码目录。同步失败不会修改
数据库或环境文件。`bootstrap` 重建应用容器并等待 Compose 健康；日常 `sync` 在源码完整更新后重启应用，确认旧进程退出后再检查 API、两个 Worker、Beat、Flower、前端和
Nginx 的实际就绪状态；失败时保留上传包和日志供排查。

成功条件由健康探针决定，不用固定启动时间判断。为防止网络或进程异常导致无限等待，仅设置可调整的总超时：`bootstrap` 和
`disable` 默认 300 秒，可分别通过 `HOT_BOOTSTRAP_TIMEOUT`、`HOT_DISABLE_TIMEOUT` 调整；`sync` 末尾与独立 `check` 的运行态检查
默认 60 秒，可通过 `HOT_CHECK_TIMEOUT` 调整。源码传输由 SSH 保活和连接超时单独约束。`HOT_CHECK_INTERVAL` 只控制探针频率，
默认 1 秒。当前联调服务器实测首次前端依赖安装约 143 秒、
API 源码变更到重新就绪约 13 秒；这些数据用于设置默认上限，不作为成功条件。

源码热更新不会执行 migration、权限收敛或初始化业务数据。出现 schema、依赖或 Compose 变化时，不得删除拦截或直接覆盖服务器；
应回到 Jenkins 正式发布，完成后再运行 `bootstrap` 建立新基线。


## 双插件联调发布准备（2026-10-24）

镜像安装与运行启用分开：CI/testing/runtime 构建和本地 Compose 构建均选用 `WES_PLUGIN_EXTRAS="manual-picking automatic-picking"`，发布依赖指纹覆盖两个插件的 manifest。现有基础 Dockerfile 仍支持零插件制品，不新增另一套镜像构建流程。

API、两个业务 worker、Beat、Flower 沿同一部署环境读取 `ENABLED_WORKLINE_PLUGINS`。省略仍只启用人工插件；后续经授权开启自动插件时，在实际 Compose 插值环境和容器环境统一给出 `["manual-picking","automatic-picking"]`，不要只改工作线记录。联调热更新 overlay 为五个消费者挂载相同的核心、deployment、SDK 和两个插件源码；重建、重启、探针和基础依赖核对均包含 Flower。插件安装/加载不替代工作线 START，也不自动把人工线改为自动线。

首批期限沿唯一入口 `AUTOMATIC_PICKING_FIRST_PLAN_TIMEOUT_MS`，实际值未确认时不设置，不使用空串或示例数字；只在显式启用自动插件的部署中提供经确认的正整数毫秒值。Web/worker 注入及已有到期点冻结语义沿[配置索引](configuration-index.md)，不增加发布层默认值。扫码归属、退料定位仍按[机械臂附录](../integration/device-annex-automatic-picking-arms.md#103-现场接入最后输入与回复清单)补实际报文，不在发布配置中虚构端口、身份或位置。

本轮只读现场快照（不代表后续时刻）：五个后端消费者使用同一镜像 `wes-backend:return-rack-task-completed-20261013`，镜像标签 revision 为 `a4ddde57`；核心/部署/人工插件及迁移的逐文件散列在五个进程中一致，但现场通过源码和单文件迁移挂载叠加了后续修复，不能以该镜像标签当作实际源码版本。五个进程均只启用人工插件，未安装自动插件包，未设置首批时长。数据库迁移头为 `a72d4e6c9b13`；KT16、KT18 均是已启用的 MANUAL/manual-picking 工作线。ARM01、ARM02 均已登记 `http://10.24.209.28:8080`，但 `work_line_id` 为空；该地址不再作为未知项，亦不据此把任一人工线推定为自动线。

发布执行顺序：

1. 最终发布树已在自动插件分支 `35cc42a0` 上合入最新 develop `a0416eea` 和包含该 develop 的现场清退退避分支 `6fb022f7`，合并保留在工作区，尚未提交。来源架独立到位、人工撤出及人工确认搬运等未入分支的现场补丁，只从五个消费者散列一致的实际运行文件收回，不复制其他工作区内容；保留已确认到位与原 Transport 结果的独立性。HEAVY 映射、selector 和主合同三处冲突按两侧所有权合并。迁移保留现场实际 `c6e209af714b → e3fec1d9ffae → f6d0319c8a42 → a72d4e6c9b13`，通过无业务 DDL 的 `0f1be9a476d2` 与自动分支 `3a17b9c40d62` 汇成单头；现场 a72 文件的父 revision 与已提交补丁分支不同，以实际部署文件为准，不改写服务器版本表。
2. 在上述最终合并树构建同一个双插件镜像、导出 provider/schema/依赖指纹；对照现场挂载清单收敛已经进入镜像的补丁，避免残留单文件挂载盖住新版本。不能用当前旧基础镜像直接做一次源码 `sync` 来安装新插件或跨迁移差异。
3. 经部署授权后，沿正式发布完成必要迁移及五个消费者的一致更新，默认保留人工插件启用和既有人工线；需要自动接入时，再统一启用自动插件、配置实际自动线和两个机械臂归属、WORK_BIN/SCAN_PLATFORM/转运架位置。KT16/KT18 已有配置是当前人工线事实，不能自动复制为自动线的权威绑定。
4. 经授权重建热更新基线后，核对五个消费者的镜像/源码挂载、包安装、队列、启用列表和实际配置；后续只补部署差异 smoke，复用已完成的正常链、真实 worker＋Beat 和恢复证据。真实机械臂动作仍须可靠扫码归属；退料定位只限制退料，未确认首批时长只限制无首批计划的超期确认。

本轮现场证据为忽略目录 `reports/automatic-release-server-readonly.json`，本地发布差异验收记录见同目录 `automatic-release-*`。没有修改服务器配置、执行迁移/重启/部署、发送业务请求或启用真实派发；工作区基线已收口；尚需提交授权、最终镜像构建和部署授权，不能直接覆盖在服版本。 差异验收已覆盖空库/自动头/现场头到单头、当前义务保留及真实worker＋Beat恢复；双插件和宿主安装包离线构建通过，独立完整review无未关闭问题。完整提交门禁和最终镜像进程smoke仍留对应授权边界，最终证据索引见 `reports/release-baseline-verification.json`。
