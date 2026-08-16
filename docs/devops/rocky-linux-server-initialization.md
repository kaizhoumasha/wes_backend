# WES 现场部署操作手册

| 项目 | 内容 |
| --- | --- |
| 文档版本 | V2.0 |
| 适用服务器 | `HOIB4-MESWES1` |
| 服务器位置 | 美国休斯顿 |
| 文档定位 | 服务器初始化、WES 在线部署和 ECS 联调前技术验收 |
| 现场记录更新时间 | 2026-08-16（CDT） |
| 现场部署配置包 | 2026-08-16，SHA-256 `270033a0f7b4dfaa048efc906e3d09d4732cc257ba5105cc8d7b3eff2e876466` |
| 应用镜像 Registry | `https://registry.happytable.cc` |

## 1. 适用范围和验收边界

本手册用于在已检查合格的 Rocky Linux 现场服务器上完成单机 WES 部署，覆盖以下工作：

1. 由 IT 部门创建变更前虚拟机快照。
2. 安装 Docker Engine 和 Docker Compose。
3. 设置 Docker 日志轮转。
4. 创建 WES 部署目录。
5. 校验项目组提供的现场部署配置包。
6. 优先通过互联网拉取固定 digest 的基础镜像和 WES 应用镜像。
7. 启动 TimescaleDB/PostgreSQL、Redis、WES 后端、Celery、前端和 Nginx。
8. 初始化空数据库 schema、系统权限、菜单和首个管理员。
9. 完成 ECS 上位机网络参数配置和联调前技术检查。

本次指定的 ECS 联调版本组合为后端 `0.12.0.4` 与前端 `0.7.2.0`。后端补丁仅修复 `0.12.0.0` 的空库迁移约束冲突、
生产镜像 Uvicorn 启动参数、权限和菜单树 `parent_id` 无法保存雪花主键，以及空库内置角色和管理员初始化问题，不改变 ECS
接口或业务逻辑。本手册通过只表示服务器、容器、数据库、应用入口和 ECS 网络配置已具备联调条件，不表示 ECS 实机业务流程、
WMS 业务流程或最终业务验收已经通过。

在线部署是标准主流程。只有现场服务器无法访问规定镜像源、问题已记录且项目负责人明确批准时，才使用附录 A 的离线部署补充流程；
主流程中不混用在线拉取和离线导入命令。

## 2. 已确认的服务器信息

| 项目 | 已确认结果 |
| --- | --- |
| 主机名 | `HOIB4-MESWES1` |
| 操作系统 | Rocky Linux 10.1，x86_64 |
| 服务器类型 | KVM 虚拟机，资源由 IT 部门保障 |
| CPU | 16 vCPU |
| 内存 | 约 64 GB |
| 系统磁盘 | 约 4 TB，根目录可用空间约 3.9 TB |
| 局域网地址 | `10.24.199.219/24` |
| 网关 | `10.24.199.1` |
| 系统时区 | `America/Chicago` |
| 时间同步 | 已同步，NTP 正常 |
| 现有软件 | Tailscale 为远程连接软件；CrowdStrike 为 IT 安全软件 |
| 系统防护 | SELinux 为 Enforcing；Firewalld 正在运行 |

以上资源满足当前单机 WES 部署的基础条件。不要修改服务器时区、网络、Tailscale、CrowdStrike、SELinux 或 Firewalld。

## 3. 现场操作规则

- 使用现有账号 `CANTAISYS` 登录，不创建新账号。
- 每次只执行一个命令块。看到“符合要求”后再继续下一步。
- 命令执行失败时，停止当前步骤，把命令和报错原文记录到第 18 节，不要自行修改配置。
- 不要把密码、密钥或 `.env.prod` 文件内容抄入记录表。
- 只允许拉取本手册明确列出的官方基础镜像 digest，以及 `registry.happytable.cc` 中明确列出的 WES 应用镜像；不得改用
  `latest`、分支标签或其它来源。
- 不要在命令历史、记录表或聊天工具中保存 Registry 密码、Personal Access Token 或 Deploy Token。
- 不要关闭 SELinux、Firewalld、Tailscale 或 CrowdStrike。
- 不要开放 PostgreSQL `5432` 或 Redis `6379` 端口。
- 在线拉取失败时停止当前步骤，不得自行使用镜像加速站、临时代理或非规定 Registry。
- 本手册不要求重启服务器。需要重启时，由 IT 部门另行安排。

## 4. 第一步：确认 IT 快照已经完成

请 IT 部门为虚拟机 `HOIB4-MESWES1` 创建变更前快照。收到快照名称或编号后填写下表。

| 记录项 | 现场填写 |
| --- | --- |
| 快照名称或编号 |  |
| 快照创建时间 |  |
| IT 确认人 |  |

没有取得快照名称或编号时，不要继续。

说明：虚拟机快照用于本次系统变更回退，不代替数据库正式备份。

## 5. 第二步：再次确认正在操作正确的服务器

依次执行：

```bash
hostnamectl --static
timedatectl
df -h /
sudo -v
```

符合要求的结果：

- 第一条显示 `HOIB4-MESWES1`。
- `Time zone` 显示 `America/Chicago`。
- `System clock synchronized` 显示 `yes`。
- 根目录 `/` 的可用空间大于 `100G`。
- `sudo -v` 没有报错；该命令正常时通常没有输出。

| 记录项 | 现场填写 |
| --- | --- |
| 主机名 | `HOIB4-MESWES1` |
| Time zone | `America/Chicago` |
| System clock synchronized | `yes` |
| 根目录 Avail | `3.9T` |
| 本步骤结果（通过/失败） | 通过 |

任一结果不符合时，停止操作并记录实际结果。

## 6. 第三步：安装 Docker Engine 和 Docker Compose

### 6.1 安装所需系统工具

```bash
sudo dnf -y install dnf-plugins-core policycoreutils-python-utils
```

符合要求的结果：命令最后没有 `Error` 或 `Failed`，并显示安装完成或软件已经安装。

### 6.2 添加 Docker 官方软件源

```bash
sudo dnf config-manager --add-repo https://download.docker.com/linux/rhel/docker-ce.repo
```

符合要求的结果：没有 `Error` 或 `Failed`。

### 6.3 安装 Docker

以下五个 RPM NEVRA 已根据服务器 `HOIB4-MESWES1` 的 Docker 官方软件源查询结果冻结。现场人员必须使用以下精确版本，不得自行改成
其他版本。

```bash
WES_DOCKER_CE_NEVRA='docker-ce-3:29.7.2-1.el10.x86_64'
WES_DOCKER_CE_CLI_NEVRA='docker-ce-cli-1:29.7.2-1.el10.x86_64'
WES_CONTAINERD_NEVRA='containerd.io-0:2.3.3-1.el10.x86_64'
WES_DOCKER_BUILDX_NEVRA='docker-buildx-plugin-0:0.36.1-1.el10.x86_64'
WES_DOCKER_COMPOSE_NEVRA='docker-compose-plugin-0:5.4.0-1.el10.x86_64'

if printf '%s\n' \
    "$WES_DOCKER_CE_NEVRA" \
    "$WES_DOCKER_CE_CLI_NEVRA" \
    "$WES_CONTAINERD_NEVRA" \
    "$WES_DOCKER_BUILDX_NEVRA" \
    "$WES_DOCKER_COMPOSE_NEVRA" | grep -qx '__PROJECT_OWNER_MUST_REPLACE__'; then
    echo 'ERROR: Docker RPM 精确版本尚未由项目负责人冻结，停止安装。' >&2
    false
else
    sudo dnf -y install \
        "$WES_DOCKER_CE_NEVRA" \
        "$WES_DOCKER_CE_CLI_NEVRA" \
        "$WES_CONTAINERD_NEVRA" \
        "$WES_DOCKER_BUILDX_NEVRA" \
        "$WES_DOCKER_COMPOSE_NEVRA"
fi
```

符合要求的结果：五个变量都不是占位符；命令最后显示 `Complete!` 或等效的成功信息，没有 `Error` 或 `Failed`。若显示版本尚未冻结，
停止操作并联系项目负责人，不得删除检查或改装最新版。

### 6.4 启动 Docker，并设置为开机启动

```bash
sudo systemctl enable --now docker
sudo systemctl is-active docker
sudo systemctl is-enabled docker
sudo docker version
sudo docker compose version
```

符合要求的结果：

- `is-active` 显示 `active`。
- `is-enabled` 显示 `enabled`。
- `docker version` 同时显示 Client 和 Server 信息。
- `docker compose version` 显示版本号，版本不得低于 `2.24.4`。

| 记录项 | 现场填写 |
| --- | --- |
| Docker Engine RPM NEVRA | `docker-ce-3:29.7.2-1.el10.x86_64` |
| Docker CLI RPM NEVRA | `docker-ce-cli-1:29.7.2-1.el10.x86_64` |
| containerd RPM NEVRA | `containerd.io-0:2.3.3-1.el10.x86_64` |
| Buildx RPM NEVRA | `docker-buildx-plugin-0:0.36.1-1.el10.x86_64` |
| Compose RPM NEVRA | `docker-compose-plugin-0:5.4.0-1.el10.x86_64` |
| Docker Engine 版本 | `29.7.2` |
| Docker Compose 版本 | `v5.4.0` |
| Docker 服务状态 | `active` |
| Docker 开机启动状态 | `enabled` |
| 本步骤结果（通过/失败） | 通过 |

## 7. 第四步：设置 Docker 日志轮转

执行以下完整命令块：

```bash
sudo install -d -m 0755 /etc/docker
sudo tee /etc/docker/daemon.json >/dev/null <<'EOF'
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3"
  }
}
EOF
python3 -m json.tool /etc/docker/daemon.json >/dev/null
sudo systemctl restart docker
sudo systemctl is-active docker
sudo docker info --format '{{.LoggingDriver}}'
```

符合要求的结果：

- JSON 检查没有输出，也没有报错。
- Docker 服务状态显示 `active`。
- 最后一条命令显示 `json-file`。

| 记录项 | 现场填写 |
| --- | --- |
| Docker 服务状态 | `active` |
| LoggingDriver | `json-file` |
| 日志轮转配置 | 单文件 `10m`，保留 `3` 个文件 |
| 本步骤结果（通过/失败） | 通过 |

## 8. 第五步：创建 WES 部署目录

执行：

```bash
sudo install -d -m 0750 -o "$(id -un)" -g "$(id -gn)" /srv/wes/app /srv/wes/packages /srv/wes/images
ls -ld /srv/wes /srv/wes/app /srv/wes/packages /srv/wes/images
```

符合要求的结果：四个目录都存在，`app`、`packages` 和 `images` 的所有者是当前账号。

| 记录项 | 现场填写 |
| --- | --- |
| `/srv/wes` 所有者及权限 | `root:root`，`755` |
| `/srv/wes/app` 所有者及权限 | `CANTAISYS:CANTAISYS`，`750` |
| `/srv/wes/packages` 所有者及权限 | `CANTAISYS:CANTAISYS`，`750` |
| `/srv/wes/images` 所有者及权限 | `CANTAISYS:CANTAISYS`，`750` |
| 本步骤结果（通过/失败） | 通过 |

## 9. 第六步：接收并校验现场部署配置包

### 9.1 在线部署交付内容

在线部署只要求项目负责人交付配置和校验文件，不交付镜像 tar 包。现场人员接收以下两个文件：

| 文件 | 放置目录 | 用途 |
| --- | --- | --- |
| `wes-onsite-deployment-bundle.tar.gz` | `/srv/wes/packages` | Compose、环境文件和运行配置 |
| `wes-onsite-deployment-bundle.tar.gz.sha256` | `/srv/wes/packages` | 配置包完整性校验 |

配置包必须由项目负责人从本次指定版本对应的部署文件生成，至少包含：

- `.env.prod`；
- `docker-compose.yml`；
- `IMAGE-MANIFEST.txt`；
- `nginx/`、`postgresql/` 和 `redis/` 运行配置；
- 空的 `logs/`、`backups/` 和 `docker_data/` 目录结构。

配置包不得包含 Git 仓库、源代码、开发环境 volume、镜像 tar、Registry 密码或现场执行记录。`.env.prod` 可以包含运行所需密钥，
因此整个配置包按敏感文件管理。

> V1.3 使用的 `wes-foundation-bundle.tar.gz` 只覆盖数据库和 Redis，不能继续作为 V2.0 的现场部署包。本次已经重新生成
> `wes-onsite-deployment-bundle.tar.gz` 及其校验文件，现场不得混用两个配置包。

### 9.2 校验配置包

```bash
chmod 600 /srv/wes/packages/wes-onsite-deployment-bundle.tar.gz
cd /srv/wes/packages
sha256sum -c wes-onsite-deployment-bundle.tar.gz.sha256
```

符合要求的结果：显示 `wes-onsite-deployment-bundle.tar.gz: OK`。缺少任一文件或校验失败时停止，不要解压、重新压缩或自行修改
校验文件。

本次冻结的配置包 SHA-256 为：

`270033a0f7b4dfaa048efc906e3d09d4732cc257ba5105cc8d7b3eff2e876466`

### 9.3 本次固定镜像

本次 ECS 联调固定使用以下应用镜像：

| 组件 | 人工识别标签 | 部署使用的不可变引用 | 平台 | Git 提交 |
| --- | --- | --- | --- | --- |
| 后端 `0.12.0.4` | `registry.happytable.cc/wes/wes_backend:0.12.0.4-amd64-d719111c025a` | `registry.happytable.cc/wes/wes_backend@sha256:44c7ca6cc3b840d423d18c2e59ef8be8829f171d264d49dca7adb87f34654a5d` | `linux/amd64` | `d719111c025acf430daaf69ba0d2e893b3b473ae` |
| 前端 `0.7.2.0` | `registry.happytable.cc/wes/wes_frontend:0.7.2.0-amd64-9c5346d43d15` | `registry.happytable.cc/wes/wes_frontend@sha256:4afc9a4259f6989ea0f72158e8b2ad1e3ecdaced906239b1f5b228cc37281fec` | `linux/amd64` | `9c5346d43d15a3af6c975d05bd128730236df93d` |

基础镜像固定为：

| 组件 | 不可变来源 |
| --- | --- |
| TimescaleDB | `timescale/timescaledb@sha256:6e4b469dee0395a8a6d8c818384b0226a749997a29a312f314413f98e4161f82` |
| Redis | `redis@sha256:a7859ed111db3c1f5404a973a4747505d559fb5ca32d37e447afc0ef845a2103` |
| Nginx 网关 | `nginx@sha256:62223d644fa234c3a1cc785ee14242ec47a77364226f1c811d2f669f96dc2ac8` |

`docker-compose.yml` 必须使用上述 digest 或项目固定的本地基础镜像标签，不得使用 `latest`、`develop`、`prod` 或其它可漂移标签。

| 记录项 | 现场填写 |
| --- | --- |
| 配置包校验结果 |  |
| 配置包提供人 |  |
| 本步骤结果（通过/失败） |  |

## 10. 第七步：解压配置包并检查部署配置

### 10.1 解压到空目录

```bash
if find /srv/wes/app -mindepth 1 -print -quit | grep -q .; then
    echo 'ERROR: /srv/wes/app 不是空目录，停止解压并联系项目负责人。' >&2
    false
else
    tar -xzf /srv/wes/packages/wes-onsite-deployment-bundle.tar.gz -C /srv/wes/app
fi
```

目录不为空时不得覆盖、合并、删除或移动原内容。先由项目负责人确认原目录所有权和处理方式。

### 10.2 检查文件、权限和 SELinux 标签

```bash
cd /srv/wes/app
test -f .env.prod
test -f docker-compose.yml
test -f IMAGE-MANIFEST.txt
test -d nginx
test -d postgresql
test -d redis
chmod 600 .env.prod
mkdir -p logs/nginx backups docker_data/postgres_prod docker_data/redis_prod
sudo chown "$(id -u):999" logs
sudo chmod 2770 logs
sudo semanage fcontext -a -t container_file_t '/srv/wes/app(/.*)?'
sudo restorecon -RFv /srv/wes/app
stat -c '%a %U:%G %n' .env.prod
stat -c '%a %u:%g %U:%G %n' logs logs/nginx
ls -Zd /srv/wes/app
```

符合要求的结果：所有 `test` 命令无输出且退出成功；`.env.prod` 权限为 `600`；`logs` 为当前现场用户所有、数字组为 `999`、
权限为 `2770`；SELinux 类型包含 `container_file_t`。宿主机可能把 GID `999` 显示为其它系统组名，以数字 GID 为准。只修改
`logs` 顶层目录，不递归修改 `logs/nginx`。

### 10.3 检查 Compose 主机配置

```bash
cd /srv/wes/app
grep '^ENV=' .env.prod
grep '^VERSION=0.12.0.4$' .env.prod
grep '^DATETIME_TIMEZONE=' .env.prod
grep '^BACKEND_IMAGE=registry.happytable.cc/wes/wes_backend@sha256:44c7ca6cc3b840d423d18c2e59ef8be8829f171d264d49dca7adb87f34654a5d$' .env.prod
grep '^FRONTEND_IMAGE=registry.happytable.cc/wes/wes_frontend@sha256:4afc9a4259f6989ea0f72158e8b2ad1e3ecdaced906239b1f5b228cc37281fec$' .env.prod
sudo docker compose --env-file .env.prod -f docker-compose.yml config -q
sudo docker compose --env-file .env.prod -f docker-compose.yml config --services
sudo docker compose --env-file .env.prod -f docker-compose.yml config --images
if sudo docker compose --env-file .env.prod -f docker-compose.yml config | \
    grep -Eq '^[[:space:]]+build:|/app/src:[^[:space:]]*:rw'; then
    echo 'ERROR: 现场部署配置包含本机构建或源码挂载，停止部署。' >&2
    false
fi
```

符合要求的结果：

- `ENV=prod`；
- `VERSION=0.12.0.4`；
- `DATETIME_TIMEZONE=America/Chicago`；
- `BACKEND_IMAGE` 和 `FRONTEND_IMAGE` 分别匹配第 9.3 节的不可变引用；
- 服务列表为 `db`、`redis`、`api`、`celery_worker`、`celery_beat`、`frontend` 和 `nginx`，顺序可以不同；
- 镜像列表与 `IMAGE-MANIFEST.txt` 一致；
- 不包含 `build` 或 `/app/src` 读写挂载。

任一结果不符合时停止。现场人员不得自行编辑 `.env.prod`、Compose 或 Nginx 配置。

## 11. 第八步：验证在线镜像通道并登录 Registry

### 11.1 检查项目 Registry

```bash
curl -sSI https://registry.happytable.cc/v2/ | tr -d '\r' | \
    grep -iE 'docker-distribution-api-version|www-authenticate'
```

符合要求的结果：

- 包含 `Docker-Distribution-Api-Version: registry/2.0`；
- 包含 `service="container_registry"`；
- 包含 `realm="https://git.zontecmes.com/jwt/auth"`。

返回 `http://` realm、`5xx` 或连接超时时停止。不要把 Registry 改为内网地址。

### 11.2 使用只读凭据登录

```bash
sudo docker login registry.happytable.cc
sudo stat -c '%a %U:%G %n' /root/.docker/config.json
```

使用项目负责人安全交付的 `read_registry` 凭据。符合要求的结果：登录显示 `Login Succeeded`；凭据文件权限为 `600`，所有者为
`root:root`。Docker 提示凭据未加密保存不表示登录失败，但不得复制、提交或打印该文件。

| 记录项 | 现场填写 |
| --- | --- |
| Registry 端点检查 |  |
| JWT realm |  |
| Registry 登录 |  |
| 凭据文件权限 |  |
| 本步骤结果（通过/失败） |  |

## 12. 第九步：在线拉取并核验全部镜像

### 12.1 拉取基础镜像

```bash
sudo docker pull --platform linux/amd64 \
    timescale/timescaledb@sha256:6e4b469dee0395a8a6d8c818384b0226a749997a29a312f314413f98e4161f82
sudo docker pull --platform linux/amd64 \
    redis@sha256:a7859ed111db3c1f5404a973a4747505d559fb5ca32d37e447afc0ef845a2103
sudo docker tag \
    timescale/timescaledb@sha256:6e4b469dee0395a8a6d8c818384b0226a749997a29a312f314413f98e4161f82 \
    wes-foundation/timescaledb:2.27.1-pg17-amd64-c6262240a63f
sudo docker tag \
    redis@sha256:a7859ed111db3c1f5404a973a4747505d559fb5ca32d37e447afc0ef845a2103 \
    wes-foundation/redis:8.2.8-alpine-amd64-3790f2652609
```

### 12.2 拉取 WES 应用镜像

```bash
sudo docker pull --platform linux/amd64 \
    registry.happytable.cc/wes/wes_backend@sha256:44c7ca6cc3b840d423d18c2e59ef8be8829f171d264d49dca7adb87f34654a5d
sudo docker pull --platform linux/amd64 \
    registry.happytable.cc/wes/wes_frontend@sha256:4afc9a4259f6989ea0f72158e8b2ad1e3ecdaced906239b1f5b228cc37281fec
sudo docker pull --platform linux/amd64 \
    nginx@sha256:62223d644fa234c3a1cc785ee14242ec47a77364226f1c811d2f669f96dc2ac8
```

任一拉取失败时停止，不启动已有的部分镜像。Compose 的 `pull_policy: never` 用于防止启动阶段发生镜像漂移，因此本节必须显式执行
`docker pull`，不能改用 `docker compose pull`。

### 12.3 核验平台、版本和提交

```bash
sudo docker image inspect \
    registry.happytable.cc/wes/wes_backend@sha256:44c7ca6cc3b840d423d18c2e59ef8be8829f171d264d49dca7adb87f34654a5d \
    registry.happytable.cc/wes/wes_frontend@sha256:4afc9a4259f6989ea0f72158e8b2ad1e3ecdaced906239b1f5b228cc37281fec \
    --format '{{.Architecture}} {{index .Config.Labels "org.opencontainers.image.version"}} {{index .Config.Labels "org.opencontainers.image.revision"}}'
sudo docker image inspect \
    wes-foundation/timescaledb:2.27.1-pg17-amd64-c6262240a63f \
    wes-foundation/redis:8.2.8-alpine-amd64-3790f2652609 \
    --format '{{.Architecture}} {{.Id}}'
```

前两行必须分别为：

- `amd64 0.12.0.4 d719111c025acf430daaf69ba0d2e893b3b473ae`；
- `amd64 0.7.2.0 9c5346d43d15a3af6c975d05bd128730236df93d`。

后两行必须以 `amd64` 开头。结果不符时停止，不得用同名标签的其它镜像代替。

## 13. 第十步：启动并检查数据库和 Redis

### 13.1 启动基础设施

```bash
cd /srv/wes/app
sudo docker compose --env-file .env.prod -f docker-compose.yml up -d --wait db redis
sudo docker compose --env-file .env.prod -f docker-compose.yml ps db redis
sudo docker inspect --format '{{.Name}} {{.State.Health.Status}} {{.HostConfig.RestartPolicy.Name}}' \
    wes_postgres_prod wes_redis_prod
```

符合要求的结果：两个容器都是 `healthy`，重启策略符合 `IMAGE-MANIFEST.txt` 的现场部署要求。

### 13.2 检查端口没有向局域网开放

```bash
sudo docker port wes_postgres_prod 5432
sudo docker port wes_redis_prod 6379
sudo firewall-cmd --list-ports
```

符合要求的结果：PostgreSQL 和 Redis 只绑定 `127.0.0.1`；Firewalld 没有开放 `5432/tcp` 或 `6379/tcp`。显示
`0.0.0.0`、`[::]` 或服务器局域网地址时立即停止并关闭本次 Compose 项目，不要继续启动应用。

### 13.3 检查版本和响应

```bash
sudo docker exec wes_postgres_prod sh -c 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
sudo docker exec wes_postgres_prod sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SHOW server_version; SELECT default_version FROM pg_available_extensions WHERE name = '\''timescaledb'\'';"'
sudo docker exec wes_redis_prod redis-server --version
```

符合要求的结果：PostgreSQL 显示 `accepting connections`，版本为 `17.10`，TimescaleDB 为 `2.27.1`，Redis 为 `8.2.8`。

### 13.4 验证备份命令

```bash
cd /srv/wes/app
install -m 0600 /dev/null backups/foundation-globals.sql
sudo docker exec wes_postgres_prod sh -c 'pg_dumpall -U "$POSTGRES_USER" --globals-only' > backups/foundation-globals.sql
test -s backups/foundation-globals.sql && echo OK
stat -c '%a %n' backups/foundation-globals.sql
```

显示 `OK` 且权限为 `600` 才通过。该文件可能包含数据库角色口令哈希，只用于验证命令，不是正式生产备份。

## 14. 第十一步：初始化 schema 并启动 WES 应用

### 14.1 初始化空数据库 schema

本次部署只允许使用空数据库。确认未遗留历史 WES 数据后执行：

```bash
cd /srv/wes/app
sudo docker compose --env-file .env.prod -f docker-compose.yml run --rm --no-deps \
    --entrypoint alembic api upgrade head
sudo docker compose --env-file .env.prod -f docker-compose.yml run --rm --no-deps \
    --entrypoint alembic api current
```

符合要求的结果：升级命令成功，`current` 显示当前 `head`。失败时保持应用服务停止，不执行 downgrade，不导入历史数据库，也不切换
旧镜像规避错误。

### 14.2 开放唯一的 WES 现场访问端口

先取得 IT 对 WES Web 入口端口的确认。本次交付将 `NGINX_HTTP_PORT` 固定为标准 HTTP `80/tcp`；只开放该端口，API、PostgreSQL
和 Redis 不向局域网开放：

```bash
cd /srv/wes/app
NGINX_HTTP_PORT=$(sed -n 's/^NGINX_HTTP_PORT=//p' .env.prod | tail -n 1)
NGINX_HTTP_PORT=${NGINX_HTTP_PORT:-80}
sudo firewall-cmd --permanent --add-port="${NGINX_HTTP_PORT}/tcp"
sudo firewall-cmd --reload
sudo firewall-cmd --query-port="${NGINX_HTTP_PORT}/tcp"
```

符合要求的结果：`NGINX_HTTP_PORT` 为 `80`，最后显示 `yes`。未经 IT 确认不得执行；不要同时开放 API、PostgreSQL、Redis、
Flower 或开发调试端口。

### 14.3 启动应用服务

```bash
cd /srv/wes/app
sudo docker compose --env-file .env.prod -f docker-compose.yml up -d --wait \
    --remove-orphans --no-build api celery_worker celery_beat frontend nginx
sudo docker compose --env-file .env.prod -f docker-compose.yml ps
sudo docker port wes_api_prod 8001
sudo docker port wes_nginx_prod 80
```

符合要求的结果：`db`、`redis`、`api`、`celery_worker`、`celery_beat`、`frontend` 和 `nginx` 均为运行状态；定义健康检查的服务均为
`healthy`；API 只绑定 `127.0.0.1`；Nginx 映射到已批准的 `NGINX_HTTP_PORT`。不要在本次联调部署中临时增加 Flower、Mock ECS、
Mock WMS 或开发容器。

### 14.4 检查关键日志

```bash
cd /srv/wes/app
sudo docker compose --env-file .env.prod -f docker-compose.yml logs --tail=200 \
    api celery_worker celery_beat frontend nginx
```

重点检查数据库迁移失败、容器反复重启、Redis 认证失败、前端资源 `404` 和 Nginx 上游连接失败。日志异常时先记录，不要通过删除
数据库目录或重建容器掩盖问题。

## 15. 第十二步：初始化系统数据并配置 ECS 上位机

### 15.1 初始化内置角色并同步权限和菜单

先初始化固定的 5 个内置角色，再从固定前端镜像提取菜单清单，最后由后端执行幂等同步：

```bash
cd /srv/wes/app
sudo docker compose --env-file .env.prod -f docker-compose.yml exec -T api \
    python scripts/data/bootstrap_roles.py
manifest_container="wes_frontend_manifest_$(date +%s)"
sudo docker create --name "$manifest_container" \
    registry.happytable.cc/wes/wes_frontend@sha256:4afc9a4259f6989ea0f72158e8b2ad1e3ecdaced906239b1f5b228cc37281fec >/dev/null
sudo docker cp "$manifest_container":/opt/wes/menu-manifest.json ./menu-manifest.json
sudo docker rm "$manifest_container" >/dev/null
sudo docker compose --env-file .env.prod -f docker-compose.yml exec -T api \
    python scripts/data/sync_permissions.py
sudo docker compose --env-file .env.prod -f docker-compose.yml exec -T api \
    sh -c 'cat > /tmp/menu-manifest.json' < menu-manifest.json
sudo docker compose --env-file .env.prod -f docker-compose.yml exec -T api \
    python scripts/data/sync_menus.py --manifest-path /tmp/menu-manifest.json
```

任一同步失败时停止。生产现场不得执行开发数据 seed。

### 15.2 创建首个管理员

仅在系统中没有超级管理员时执行：

```bash
cd /srv/wes/app
sudo docker compose --env-file .env.prod -f docker-compose.yml exec -T api \
    python -c 'import os; from pydantic import EmailStr, TypeAdapter; TypeAdapter(EmailStr).validate_python(os.environ["BOOTSTRAP_ADMIN_EMAIL"]); print("BOOTSTRAP_ADMIN_EMAIL: OK")'
sudo docker compose --env-file .env.prod -f docker-compose.yml exec -T api \
    python scripts/data/bootstrap_admin.py
```

管理员初始化参数必须由项目负责人通过受控的 `.env.prod` 提供。不要把用户名、密码或密钥写入本手册、命令行参数或现场记录表。

初始化后执行数据闭环检查：

```bash
sudo docker exec -i wes_postgres_prod sh -c \
    'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -P pager=off' <<'SQL'
SELECT count(*) AS builtin_role_count
FROM wes_sys.roles
WHERE name IN ('系统管理员', '管理员', '运营人员', '财务人员', '普通用户')
  AND is_deleted = false;

SELECT
    (SELECT count(*) FROM wes_sys.permissions) AS permission_count,
    (SELECT count(*) FROM wes_sys.menus) AS menu_count,
    (SELECT count(*) FROM wes_sys.role_permissions) AS role_permission_count,
    (SELECT count(*) FROM wes_sys.role_menus) AS role_menu_count;

SELECT count(*) AS admin_role_link_count
FROM wes_sys.user_roles ur
JOIN wes_sys.users u ON u.id = ur.user_id
JOIN wes_sys.roles r ON r.id = ur.role_id
WHERE u.is_superuser = true
  AND r.name = '系统管理员'
  AND r.is_deleted = false;
SQL
```

`builtin_role_count` 必须为 `5`；权限、菜单及两类角色关联计数必须大于 `0`；`admin_role_link_count` 必须大于等于 `1`。
任一结果不符合时停止，不得绕过授权数据继续 ECS 联调。

### 15.3 检查 ECS 网络可达性

取得 ECS 供应商确认的上位机地址、端口、协议和状态路径后执行。以下占位符必须替换为现场确认值：

```bash
curl --fail --show-error --max-time 10 \
    '<ECS_SCHEME>://<ECS_HOST>:<ECS_PORT><ECS_STATUS_PATH>'
```

返回 ECS 供应商约定的成功响应才表示网络和状态端点可达。连接失败、超时或返回非预期状态时停止，不修改服务器路由、Firewalld、
SELinux 或客户网络策略，由 IT 和 ECS 供应商共同确认。

### 15.4 在前端配置 ECS 上位机

1. 浏览器访问 `http://10.24.199.219/`（标准 HTTP `80/tcp`）并使用现场管理员登录。
2. 进入工作线配置页面，选择本次粗分机工作线。
3. 按 ECS 供应商确认值填写 `scheme`、`host`、`port` 和状态路径；不得填写 `localhost` 或开发 Mock 地址。
4. 保存后重新打开配置，确认页面回显与现场参数一致。
5. 在项目负责人和 ECS 供应商确认前，不激活工作线，不下发设备命令。

本步骤只完成参数和网络准备。只有后续 ECS Command、ACK、Callback 和业务结果都按联调方案闭环后，才能单独记录“ECS 联调通过”。

## 16. 第十三步：执行现场部署技术验收

### 16.1 健康检查和首页检查

```bash
cd /srv/wes/app
APP_HOST_PORT=$(sed -n 's/^APP_HOST_PORT=//p' .env.prod | tail -n 1)
NGINX_HTTP_PORT=$(sed -n 's/^NGINX_HTTP_PORT=//p' .env.prod | tail -n 1)
APP_HOST_PORT=${APP_HOST_PORT:-8002}
NGINX_HTTP_PORT=${NGINX_HTTP_PORT:-80}
curl --fail "http://127.0.0.1:${APP_HOST_PORT}/health"
curl --fail "http://127.0.0.1:${APP_HOST_PORT}/ready"
curl --fail --output /dev/null "http://127.0.0.1:${NGINX_HTTP_PORT}/health"
curl --fail --output /dev/null "http://127.0.0.1:${NGINX_HTTP_PORT}/"
```

四条请求全部成功才通过。`/health` 只证明进程存活，首页成功只证明前端入口可访问。当前后端版本的 `/ready` 是进程内缓存快照：
HTTP `200`、`ready=true` 且三个 `components` 均为 `true` 时可以放行；`stale=true` 表示快照已过期，必须记录，不能描述成实时依赖
探测通过。数据库、Redis 和 Celery 的实时技术状态以容器健康检查及本手册对应的直接检查为准。上述结果都不等于 ECS 或 WMS
业务验收。

### 16.2 最终容器和镜像检查

```bash
cd /srv/wes/app
sudo docker compose --env-file .env.prod -f docker-compose.yml ps
sudo docker compose --env-file .env.prod -f docker-compose.yml images
sudo docker inspect --format '{{.Name}} {{.Config.Image}} {{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}' \
    wes_postgres_prod wes_redis_prod wes_api_prod wes_frontend_prod
```

确认没有 `Restarting`、`Exited` 或 `unhealthy`，应用容器使用第 9.3 节固定版本。现场实际容器名与配置包不一致时，以 Compose 输出为准
补充检查，但不得改容器名绕过差异。

## 17. 最终记录表

| 检查项 | 正确结果 | 现场填写 |
| --- | --- | --- |
| IT 快照 | 已取得快照名称或编号 |  |
| Docker 服务 | `active`、`enabled` | `active`、`enabled` |
| Docker 日志驱动 | `json-file` | `json-file` |
| Docker Compose | 版本不低于 `2.24.4` | `v5.4.0` |
| 现场部署配置包 | 校验为 `OK` |  |
| 部署方式 | 标准路径为“在线” | 在线 |
| Registry 和 HTTPS realm | 登录成功，realm 使用 HTTPS |  |
| 后端镜像 | `0.12.0.4`、固定 digest、`amd64` |  |
| 前端镜像 | `0.7.2.0`、固定 digest、`amd64` |  |
| Compose 服务 | `db`、`redis`、`api`、`celery_worker`、`celery_beat`、`frontend`、`nginx` |  |
| PostgreSQL/TimescaleDB | `healthy`，版本 `17.10` / `2.27.1` |  |
| Redis | `healthy`，版本 `8.2.8` |  |
| 数据库和 Redis 端口 | 只绑定 `127.0.0.1` |  |
| API 端口 | 只绑定 `127.0.0.1` |  |
| WES Web 入口 | 仅开放 IT 批准的 `80/tcp` |  |
| schema | `alembic current` 为当前 `head` |  |
| API | `/health` 为 `200`；`/ready` 为 `200`、`ready=true`，记录 `stale` 状态 |  |
| 前端入口 | 首页和 Nginx `/health` 成功 |  |
| 权限、菜单、管理员 | 初始化成功 |  |
| ECS 网络 | 状态端点返回供应商约定结果 |  |
| ECS 页面配置 | 地址、端口和协议回显正确 |  |
| ECS 实机业务联调 | 本手册不判定，另行记录 | 未执行 |
| WMS 联调和业务验收 | 本手册不判定，另行记录 | 未执行 |

全部技术检查符合后填写：

| 记录项 | 现场填写 |
| --- | --- |
| 执行日期和时间 |  |
| 现场执行人 |  |
| IT 配合人 |  |
| 项目负责人 |  |
| 现场部署技术验收（通过/失败） |  |
| 备注 |  |

## 18. 失败记录和停止条件

发生错误时只记录文字，不记录密码、Token、`.env.prod` 内容或完整业务 Payload。

| 发生步骤 | 执行的命令 | 报错原文 | 发生时间 |
| --- | --- | --- | --- |
|  |  |  |  |
|  |  |  |  |
|  |  |  |  |

出现失败后，不要关闭 SELinux 或 Firewalld，不要删除数据库目录，不要执行 `docker compose down -v`，不要修改密码或重启服务器尝试
解决。保留现场状态和日志，把本表返回项目负责人确认下一步。

## 附录 A：离线部署补充流程

### A.1 启用条件和边界

本附录不是默认部署方式。只有同时满足以下条件时才能使用：

1. 第 11 或第 12 节的在线访问确实失败，错误已经写入第 18 节；
2. IT 已确认短期内无法恢复规定镜像源访问；
3. 项目负责人书面确认使用离线镜像包；
4. 第 9 节的现场部署配置包已经校验通过。

离线流程只替代 Registry 登录和在线镜像拉取，不替代配置包、Compose 校验、schema 初始化、应用启动或技术验收。不得从其它现场、
个人电脑缓存或非项目来源临时导出镜像。

### A.2 接收并校验离线镜像包

项目负责人提供：

| 文件 | 放置目录 |
| --- | --- |
| `wes-onsite-images-linux-amd64.tar` | `/srv/wes/images` |
| `wes-onsite-images-linux-amd64.tar.sha256` | `/srv/wes/images` |

该镜像包必须一次性包含 `IMAGE-MANIFEST.txt` 中的 TimescaleDB、Redis、Nginx、后端 `0.12.0.4` 和前端 `0.7.2.0` 全部固定镜像。

```bash
chmod 600 /srv/wes/images/wes-onsite-images-linux-amd64.tar
cd /srv/wes/images
sha256sum -c wes-onsite-images-linux-amd64.tar.sha256
```

只有显示 `wes-onsite-images-linux-amd64.tar: OK` 才能继续。校验失败时不得导入。

### A.3 导入并核验镜像

```bash
sudo docker load -i /srv/wes/images/wes-onsite-images-linux-amd64.tar
cd /srv/wes/app
sudo docker compose --env-file .env.prod -f docker-compose.yml config --images
sudo docker image inspect \
    registry.happytable.cc/wes/wes_backend@sha256:44c7ca6cc3b840d423d18c2e59ef8be8829f171d264d49dca7adb87f34654a5d \
    registry.happytable.cc/wes/wes_frontend@sha256:4afc9a4259f6989ea0f72158e8b2ad1e3ecdaced906239b1f5b228cc37281fec \
    --format '{{.Architecture}} {{index .Config.Labels "org.opencontainers.image.version"}} {{index .Config.Labels "org.opencontainers.image.revision"}}'
```

导入后的镜像列表必须与 `IMAGE-MANIFEST.txt` 完全一致，前后端检查结果必须与第 12.3 节一致。缺少任一镜像、平台不是 `amd64` 或
版本信息不符时停止。

核验通过后直接从第 13 节继续，不再执行第 11.2 节 Registry 登录和第 12 节在线拉取。

| 记录项 | 现场填写 |
| --- | --- |
| 启用离线部署批准人和时间 |  |
| 在线失败记录编号 |  |
| 离线镜像包校验 |  |
| 镜像清单核验 |  |
| 后续恢复步骤 | 从第 13 节继续 |
