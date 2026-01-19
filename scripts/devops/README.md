# DevOps 环境搭建指南

本目录包含用于在 Rocky Linux 上搭建 CI/CD 和测试环境的脚本。

## 预备条件
- **CI/CD 服务器:** 192.168.0.220
- **测试服务器:** 192.168.0.221
- **用户名:** root
- **操作系统:** Rocky Linux

## 安装步骤

请在您的本地机器上运行主安装脚本：

```bash
./scripts/devops/install_env.sh
```

脚本执行过程中，您需要多次输入服务器的 root 密码 `Zontec2025` (用于 SCP 文件传输和 SSH 远程执行)。

## 服务访问与管理

### GitLab
- **访问地址:** http://192.168.0.220
- **SSH 端口:** 2222
- **获取初始 Root 密码:**
  在服务器上执行以下命令：
  ```bash
  ssh root@192.168.0.220 "cat /opt/cicd/gitlab/config/initial_root_password"
  ```
  *注意: GitLab 首次启动可能需要几分钟时间。*

### Jenkins
- **访问地址:** http://192.168.0.220:8080
- **获取初始 Admin 密码:**
  在服务器上执行以下命令：
  ```bash
  ssh root@192.168.0.220 "cat /opt/cicd/jenkins_home/secrets/initialAdminPassword"
  ```

## 后续配置建议

1.  **GitLab 配置:**
    - 登录后请立即修改 root 密码。
    - 为 `wes_backend` 项目创建一个新的代码仓库 (Project)。

2.  **Jenkins 配置:**
    - 登录后完成初始化向导 (安装推荐插件)。
    - 创建一个新的 Pipeline 任务。
    - **凭据设置 (Credentials):**
        - 添加 GitLab 凭据 (用户名/Token 或 SSH Key)。
        - 添加测试服务器 (192.168.0.221) 的 SSH 凭据。

3.  **测试服务器部署:**
    - 测试服务器已安装好 Docker 环境。
    - 您的 Jenkins Pipeline 流程通常应包含：
        1.  拉取代码 (Checkout)。
        2.  构建 Docker 镜像 (Build)。
        3.  SSH 连接到 192.168.0.221。
        4.  拉取镜像或同步代码。
        5.  执行 `docker compose up -d` 启动服务。