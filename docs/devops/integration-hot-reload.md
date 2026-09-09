# 联调服务器源码热更新

该模式用于开发阶段快速把本机前后端源码同步到 `CANTAISYS@100.94.216.118`。它复用联调服务器当前后端镜像中的 Python
依赖、现有 PostgreSQL/Redis、环境变量和日志目录，不构建或推送新镜像。

它不是正式发布证据。Jenkins immutable image、release checker、migration、供应商一致性和业务验收仍按各自流程执行。

## 工作方式

- 后端只同步 `src/`、`deployment/`、`workline_plugins/rough_sorter/src/` 和 `main.py`；API 由 Uvicorn 自动重载，Celery Worker 与 Beat 使用开发重载入口。
- 前端同步 Vite 运行源码和配置，使用服务器持久化 `node_modules`/pnpm store，通过 Vite HMR 更新页面。
- `.env*`、`.git`、数据库、Redis、日志、报告、本机虚拟环境和前端 `node_modules` 不进入上传包。Vite 仅可写服务器侧热更新源码副本中的生成文件。
- 后端 `pyproject.toml`、`uv.lock` 或 `migrations/` 与当前基础镜像不一致时停止，必须先完成正式后端发布和数据库迁移。
- 已启用后，前端依赖清单或热更新控制文件变化时，普通 `sync` 停止；使用 `bootstrap` 重新建立基线。
- 正式 Jenkins TEST 发布前先执行 `disable`，恢复启用热更新前保存的前后端 immutable image。
- 日常 `sync` 复制源码后重建应用容器并等待就绪，确保单文件挂载和运行进程都使用本次源码；不重建镜像或数据库。

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
数据库或环境文件。`bootstrap` 和日常 `sync` 都先重建应用容器并等待 Compose 健康，再检查 API、两个 Worker、Beat、前端和 Nginx
的实际就绪状态；失败时保留上传包和日志供排查。

成功条件由健康探针决定，不用固定启动时间判断。为防止网络或进程异常导致无限等待，仅设置可调整的总超时：`bootstrap` / `sync` 的容器重建和
`disable` 默认 300 秒，可分别通过 `HOT_BOOTSTRAP_TIMEOUT`、`HOT_DISABLE_TIMEOUT` 调整；`sync` 末尾与独立 `check` 的运行态检查
默认 60 秒，可通过 `HOT_CHECK_TIMEOUT` 调整。源码传输由 SSH 保活和连接超时单独约束。`HOT_CHECK_INTERVAL` 只控制探针频率，
默认 1 秒。当前联调服务器实测首次前端依赖安装约 143 秒、
API 源码变更到重新就绪约 13 秒；这些数据用于设置默认上限，不作为成功条件。

源码热更新不会执行 migration、权限收敛或初始化业务数据。出现 schema、依赖或 Compose 变化时，不得删除拦截或直接覆盖服务器；
应回到 Jenkins 正式发布，完成后再运行 `bootstrap` 建立新基线。
