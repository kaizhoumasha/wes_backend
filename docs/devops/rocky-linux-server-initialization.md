# 休斯顿现场服务器初始化与基础支撑环境配置手册

| 项目 | 内容 |
| --- | --- |
| 文档版本 | V1.0 |
| 适用服务器 | `HOIB4-MESWES1` |
| 服务器位置 | 美国休斯顿 |
| 适用阶段 | 服务器检查通过后的第二步 |

## 1. 本次工作范围

本手册只完成以下基础支撑环境：

1. 由 IT 部门创建变更前虚拟机快照。
2. 安装 Docker Engine 和 Docker Compose。
3. 设置 Docker 日志轮转。
4. 创建 WES 部署目录。
5. 导入项目组提供的固定版本镜像和基础环境部署包。
6. 使用 Docker Compose 启动 TimescaleDB/PostgreSQL 和 Redis。
7. 检查两个容器是否健康，并记录结果。

本次不部署 WES 后端、前端、Celery、Nginx、WMS 接口或设备接口，不执行数据库迁移，也不进行业务功能测试。基础环境检查通过，只表示 Docker、数据库和 Redis 可以正常运行，不表示 WES 业务系统已经部署或验收通过。

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
- 命令执行失败时，停止当前步骤，把命令和报错原文记录到第 13 节，不要自行修改配置。
- 不要把密码、密钥或 `.env.prod` 文件内容抄入记录表。
- 不要从互联网上自行下载 WES 部署包或 WES 镜像。
- 不要关闭 SELinux、Firewalld、Tailscale 或 CrowdStrike。
- 不要开放 PostgreSQL `5432` 或 Redis `6379` 端口。
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
| 主机名 |  |
| Time zone |  |
| System clock synchronized |  |
| 根目录 Avail |  |
| 本步骤结果（通过/失败） |  |

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

项目负责人必须在交付本手册前，把下面五个变量替换为已经在当前 Rocky Linux 版本验证通过的完整 RPM NEVRA，例如包含包名、
epoch、版本、release 和架构的精确值。现场人员不得自行选择“最新版本”，任一变量仍为占位符时必须停止操作。

```bash
WES_DOCKER_CE_NEVRA='__PROJECT_OWNER_MUST_REPLACE__'
WES_DOCKER_CE_CLI_NEVRA='__PROJECT_OWNER_MUST_REPLACE__'
WES_CONTAINERD_NEVRA='__PROJECT_OWNER_MUST_REPLACE__'
WES_DOCKER_BUILDX_NEVRA='__PROJECT_OWNER_MUST_REPLACE__'
WES_DOCKER_COMPOSE_NEVRA='__PROJECT_OWNER_MUST_REPLACE__'

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
| Docker Engine RPM NEVRA |  |
| Docker CLI RPM NEVRA |  |
| containerd RPM NEVRA |  |
| Buildx RPM NEVRA |  |
| Compose RPM NEVRA |  |
| Docker Engine 版本 |  |
| Docker Compose 版本 |  |
| Docker 服务状态 |  |
| 本步骤结果（通过/失败） |  |

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
| Docker 服务状态 |  |
| LoggingDriver |  |
| 本步骤结果（通过/失败） |  |

## 8. 第五步：创建 WES 部署目录

执行：

```bash
sudo install -d -m 0750 -o "$(id -un)" -g "$(id -gn)" /srv/wes/app /srv/wes/packages /srv/wes/images
ls -ld /srv/wes /srv/wes/app /srv/wes/packages /srv/wes/images
```

符合要求的结果：四个目录都存在，`app`、`packages` 和 `images` 的所有者是当前账号。

| 记录项 | 现场填写 |
| --- | --- |
| `/srv/wes/app` 所有者 |  |
| `/srv/wes/packages` 所有者 |  |
| `/srv/wes/images` 所有者 |  |
| 本步骤结果（通过/失败） |  |

## 9. 第六步：接收并校验项目交付文件

项目负责人必须提供以下四个文件，文件名固定。现场人员只负责接收和校验，不修改文件内容。

| 文件 | 放置目录 |
| --- | --- |
| `wes-foundation-bundle.tar.gz` | `/srv/wes/packages` |
| `wes-foundation-bundle.tar.gz.sha256` | `/srv/wes/packages` |
| `wes-foundation-images.tar` | `/srv/wes/images` |
| `wes-foundation-images.tar.sha256` | `/srv/wes/images` |

通过客户允许的文件传输方式把四个文件放到对应目录，然后执行：

```bash
cd /srv/wes/packages
sha256sum -c wes-foundation-bundle.tar.gz.sha256
cd /srv/wes/images
sha256sum -c wes-foundation-images.tar.sha256
```

符合要求的结果：两条校验结果都显示 `OK`。

| 记录项 | 现场填写 |
| --- | --- |
| 部署包校验结果 |  |
| 镜像包校验结果 |  |
| 文件提供人 |  |
| 本步骤结果（通过/失败） |  |

任一文件缺失或校验结果不是 `OK` 时，停止操作，不要解压或导入。

## 10. 第七步：解压部署包并导入镜像

### 10.1 解压部署包

```bash
if find /srv/wes/app -mindepth 1 -print -quit | grep -q .; then
    echo 'ERROR: /srv/wes/app 不是空目录，停止解压并联系项目负责人。' >&2
    false
else
    tar -xzf /srv/wes/packages/wes-foundation-bundle.tar.gz -C /srv/wes/app
    cd /srv/wes/app
    find . -maxdepth 1 -type f -printf '%f\n' | sort
fi
```

符合要求的结果：解压前 `/srv/wes/app` 为空，文件列表至少包含 `.env.prod` 和 `docker-compose.yml`。目录不为空或文件不在
`/srv/wes/app` 的第一层目录时，停止操作；不得覆盖、合并或自行删除旧内容。

### 10.2 设置目录权限和 SELinux 标签

```bash
cd /srv/wes/app
chmod 600 .env.prod
mkdir -p docker_data/postgres_prod docker_data/redis_prod backups
sudo semanage fcontext -a -t container_file_t '/srv/wes/app(/.*)?'
sudo restorecon -RFv /srv/wes/app
ls -Zd /srv/wes/app
```

符合要求的结果：没有报错，最后一条命令的结果中包含 `container_file_t`。

### 10.3 导入镜像

```bash
sudo docker load -i /srv/wes/images/wes-foundation-images.tar
sudo docker image ls --format 'table {{.Repository}}\t{{.Tag}}\t{{.ID}}'
```

符合要求的结果：导入命令显示 `Loaded image`，镜像列表包含项目负责人提供的 TimescaleDB/PostgreSQL 和 Redis 固定版本镜像。

| 记录项 | 现场填写 |
| --- | --- |
| `.env.prod` 是否存在 |  |
| `docker-compose.yml` 是否存在 |  |
| SELinux 类型 |  |
| TimescaleDB/PostgreSQL 镜像名称和版本 |  |
| Redis 镜像名称和版本 |  |
| 本步骤结果（通过/失败） |  |

## 11. 第八步：启动并检查基础支撑容器

### 11.1 检查部署配置

```bash
cd /srv/wes/app
grep '^ENV=' .env.prod
grep '^DATETIME_TIMEZONE=' .env.prod
sudo docker compose --env-file .env.prod -f docker-compose.yml --profile infra config --services
sudo docker compose --env-file .env.prod -f docker-compose.yml --profile infra config --images
```

符合要求的结果：

- `ENV=prod`。
- `DATETIME_TIMEZONE=America/Chicago`。
- 服务列表只有 `db` 和 `redis`，顺序可以不同。
- 镜像列表只有项目负责人提供的两个固定版本镜像。

任何一项不符合时，停止操作。不要自行编辑 `.env.prod` 或 `docker-compose.yml`。

### 11.2 启动数据库和 Redis

```bash
cd /srv/wes/app
sudo docker compose --env-file .env.prod -f docker-compose.yml --profile infra up -d --wait db redis
sudo docker compose --env-file .env.prod -f docker-compose.yml --profile infra ps db redis
sudo docker inspect --format '{{.Name}} {{.State.Health.Status}} {{.HostConfig.RestartPolicy.Name}}' wes_postgres_prod wes_redis_prod
```

符合要求的结果：

- `wes_postgres_prod` 和 `wes_redis_prod` 都为 `healthy`。
- 两个容器的重启策略都为 `always`。

### 11.3 检查数据库和 Redis 没有对局域网开放

```bash
sudo docker port wes_postgres_prod 5432
sudo docker port wes_redis_prod 6379
sudo firewall-cmd --list-ports
```

符合要求的结果：

- PostgreSQL 显示 `127.0.0.1:5432`。
- Redis 显示 `127.0.0.1:6379`。
- Firewalld 端口列表中没有 `5432/tcp` 和 `6379/tcp`。

### 11.4 检查 PostgreSQL 响应

```bash
sudo docker exec wes_postgres_prod sh -c 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

符合要求的结果：显示 `accepting connections`。

Redis 已通过容器自身的带密码健康检查验证，不需要现场人员输入或查看 Redis 密码。

### 11.5 验证数据库备份命令可以执行

```bash
cd /srv/wes/app
install -m 0600 /dev/null backups/foundation-globals.sql
sudo docker exec wes_postgres_prod sh -c 'pg_dumpall -U "$POSTGRES_USER" --globals-only' > backups/foundation-globals.sql
test -s backups/foundation-globals.sql && echo OK
stat -c '%a %n' backups/foundation-globals.sql
```

符合要求的结果：显示 `OK`，并且 `stat` 显示权限为 `600`。该文件可能包含数据库角色口令哈希，只允许当前部署账号和 root 读取；
不得复制到记录表、共享目录或聊天工具。

该文件只用于验证备份命令，不是正式生产备份。正式数据库备份位置和周期在业务系统上线前另行确认；同一块磁盘上的文件不能代替独立备份。

## 12. 最终记录表

| 检查项 | 正确结果 | 现场填写 |
| --- | --- | --- |
| IT 快照 | 已取得快照名称或编号 |  |
| Docker 服务 | `active`、`enabled` |  |
| Docker 日志驱动 | `json-file` |  |
| Docker Compose | 版本不低于 `2.24.4` |  |
| 部署包校验 | `OK` |  |
| 镜像包校验 | `OK` |  |
| 配置时区 | `America/Chicago` |  |
| Compose 服务 | 只有 `db`、`redis` |  |
| PostgreSQL 容器 | `healthy`、`always` |  |
| Redis 容器 | `healthy`、`always` |  |
| PostgreSQL 绑定地址 | `127.0.0.1:5432` |  |
| Redis 绑定地址 | `127.0.0.1:6379` |  |
| Firewalld | 未开放 `5432/tcp`、`6379/tcp` |  |
| PostgreSQL 响应 | `accepting connections` |  |
| 备份命令验证 | `OK` |  |

全部项目符合后，填写：

| 记录项 | 现场填写 |
| --- | --- |
| 执行日期和时间 |  |
| 现场执行人 |  |
| IT 配合人 |  |
| 基础支撑环境结果（通过/失败） |  |
| 备注 |  |

## 13. 失败记录

发生错误时，只记录文字，不需要截图。

| 发生步骤 | 执行的命令 | 报错原文 | 发生时间 |
| --- | --- | --- | --- |
|  |  |  |  |
|  |  |  |  |
|  |  |  |  |

出现失败后，不要通过关闭 SELinux、关闭 Firewalld、删除数据目录、修改密码或重启服务器来尝试解决。把本表返回项目负责人，待确认后再继续。
