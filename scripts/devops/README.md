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

## 后续配置建议 (详细流程)

### 1. GitLab 初始化配置

1.  **登录与修改密码**
    - 使用 `root` 和上一步获取的初始密码登录 GitLab (http://192.168.0.220)。
    - 系统会强制要求修改密码，请设置一个安全的管理员密码。

2.  **创建项目 (Create Project)**
    - 点击 "Create a project" -> "Create blank project"。
    - **Project name:** `wes_backend`
    - **Visibility Level:** 选择 `Private` (私有) 或 `Internal` (内部)。
    - 点击 "Create project"。

3.  **获取 Git 地址**
    - 进入项目页面，点击蓝色的 "Code" 按钮。
    - 复制 **Clone with HTTP** 的地址 (例如: `http://192.168.0.220/root/wes_backend.git`)。
    - *注意: 如果您的开发机无法直接解析 gitlab.local 域名，请确保使用 IP 地址。*

### 2. Jenkins 初始化与插件安装

1.  **解锁 Jenkins**
    - 访问 Jenkins (http://192.168.0.220:8080)。
    - 输入上一步获取的 `initialAdminPassword`。

2.  **安装插件**
    - 选择 **"Install suggested plugins"** (安装推荐插件)。
    - 等待安装完成 (可能需要几分钟)。

3.  **创建管理员用户**
    - 设置 Jenkins 的管理员用户名、密码、全名和邮箱。
    - **Instance Configuration:** 确认 Jenkins URL 为 `http://192.168.0.220:8080/`。

4.  **安装必要插件 (补充)**
    - 进入 **Dashboard** -> **Manage Jenkins** -> **Plugins** -> **Available plugins**。
    - 搜索并安装以下插件 (如果尚未安装):
        - `GitLab Plugin` (用于触发构建和状态回传)
        - `SSH Agent Plugin` (用于 SSH 远程执行)
        - `Docker Pipeline` (用于构建 Docker 镜像)

### 3. 配置 Jenkins 凭据 (Credentials)

进入 **Dashboard** -> **Manage Jenkins** -> **Credentials** -> **System** -> **Global credentials (unrestricted)** -> **Add Credentials**。

我们需要添加两组凭据：

**A. GitLab 访问凭据 (用于拉取代码)**
- **Kind:** `Username with password`
- **Username:** `root` (或您在 GitLab 创建的其他用户)
- **Password:** (您的 GitLab 密码)
- **ID:** `gitlab-auth`
- **Description:** GitLab Root Auth

**B. 测试服务器 SSH 凭据 (用于部署)**
- **Kind:** `SSH Username with private key`
- **Username:** `root`
- **ID:** `test-server-ssh`
- **Description:** SSH key for Test Server (192.168.0.221)
- **Private Key:** 选择 **Enter directly**。
    - 您需要在 Jenkins 容器内生成 SSH Key，或者使用现有的 Key。
    - **推荐做法:**
        1. 在本地或 CI/CD 服务器上生成一对 Key: `ssh-keygen -t rsa -b 4096 -f jenkins_key`
        2. 将公钥 (`jenkins_key.pub`) 内容追加到 **测试服务器 (192.168.0.221)** 的 `/root/.ssh/authorized_keys` 文件中。
        3. 将私钥 (`jenkins_key`) 内容复制到 Jenkins 的 **Private Key** 框中。

### 4. 创建并配置 Pipeline 任务

1.  **新建任务**
    - 点击 **New Item**。
    - 输入任务名称: `wes_backend_deploy`。
    - 选择 **Pipeline**，点击 OK。

2.  **配置 Pipeline**
    - 在 **Pipeline** 部分:
    - **Definition:** 选择 `Pipeline script from SCM`。
    - **SCM:** 选择 `Git`。
    - **Repository URL:** 输入 GitLab 的项目地址 (例如 `http://192.168.0.220/root/wes_backend.git`)。
    - **Credentials:** 选择之前创建的 `gitlab-auth`。
    - **Branch Specifier:** `*/main` (或 `*/master`)。
    - **Script Path:** `Jenkinsfile` (确保您的项目根目录下有此文件)。

3.  **保存 (Save)**。

### 5. 项目中的 Jenkinsfile 示例

在您的 `wes_backend` 项目根目录下创建一个名为 `Jenkinsfile` 的文件，内容如下：

```groovy
pipeline {
    agent any

    environment {
        // 定义环境变量
        REGISTRY_URL = "192.168.0.220:5000" // 如果您搭建了私有仓库，否则直接构建并在本地传输
        TEST_SERVER = "192.168.0.221"
        PROJECT_NAME = "wes_backend"
    }

    stages {
        stage('Checkout') {
            steps {
                checkout scm
            }
        }

        stage('Build Docker Image') {
            steps {
                script {
                    // 构建镜像
                    sh "docker build -t ${PROJECT_NAME}:latest ."
                    // 保存镜像为 tar 包 (简单部署模式)
                    sh "docker save -o ${PROJECT_NAME}.tar ${PROJECT_NAME}:latest"
                }
            }
        }

        stage('Deploy to Test Server') {
            steps {
                sshagent(['test-server-ssh']) {
                    // 1. 清理测试服务器旧镜像文件
                    sh "ssh -o StrictHostKeyChecking=no root@${TEST_SERVER} 'rm -f /tmp/${PROJECT_NAME}.tar'"
                    
                    // 2. 传输新镜像文件
                    sh "scp -o StrictHostKeyChecking=no ${PROJECT_NAME}.tar root@${TEST_SERVER}:/tmp/"
                    
                    // 3. 在测试服务器上加载镜像并重启服务
                    sh """
                        ssh -o StrictHostKeyChecking=no root@${TEST_SERVER} '
                            docker load -i /tmp/${PROJECT_NAME}.tar
                            cd /opt/wes_backend
                            # 确保 docker-compose.yml 存在 (首次需手动上传或通过代码库拉取)
                            # 如果代码库包含 compose 文件，可以使用 git pull
                            docker compose down
                            docker compose up -d
                            rm -f /tmp/${PROJECT_NAME}.tar
                        '
                    """
                }
            }
        }
    }
}
```

*注意：上述 Jenkinsfile 是一个基于“镜像传输”的简单示例。在生产环境中，通常会使用 Docker Registry (如 Harbor 或 GitLab Container Registry) 来存储和分发镜像。*

### 6. 配置 LDAP 集成 (可选)

如果您在企业环境中使用 LDAP/Active Directory 管理用户，建议配置 LDAP 集成。

#### GitLab LDAP 配置

推荐通过编辑挂载的配置文件 `gitlab.rb` 进行配置：

1.  在 CI/CD 服务器上，编辑文件 `/opt/docker/gitlab/config/gitlab.rb`：
    ```bash
    vi /opt/docker/gitlab/config/gitlab.rb
    ```

2.  追加或修改以下配置 (根据实际 LDAP 信息调整)：
    ```ruby
    gitlab_rails['ldap_enabled'] = true
    gitlab_rails['ldap_servers'] = {
      'main' => {
        'label' => 'LDAP',
        'host' =>  'ldap.example.com', # LDAP 服务器地址
        'port' => 389,
        'uid' => 'uid', # Active Directory 通常为 'sAMAccountName'
        'bind_dn' => 'cn=admin,dc=example,dc=com', # 用于查询的管理员账号
        'password' => 'your_password',
        'encryption' => 'plain', # 可选 'start_tls', 'simple_tls', 'plain'
        'base' => 'dc=example,dc=com', # 搜索根节点
        'user_filter' => '', # 可选过滤条件
      }
    }
    ```

3.  重启 GitLab 容器以应用配置：
    ```bash
    docker restart scripts-devops-gitlab-1
    # 或者在 docker-compose 目录下
    docker compose restart gitlab
    ```

#### Jenkins LDAP 配置

1.  **安装插件**: 
    - 进入 **Manage Jenkins** -> **Plugins** -> **Available plugins**。
    - 搜索并安装 `LDAP Plugin`。

2.  **配置安全域**:
    - 进入 **Manage Jenkins** -> **Security**。
    - 在 **Security Realm** 下找到并选择 **LDAP**。
    - 填写配置信息：
        - **Server:** `ldap://ldap.example.com` (如果使用 SSL 则是 `ldaps://...`)
        - **root DN:** `dc=example,dc=com`
        - **User search base:** (留空通常即可，或指定 ou)
        - **User search filter:** `uid={0}` (Active Directory 通常用 `sAMAccountName={0}` 或 `mail={0}`)
        - **Manager DN:** `cn=admin,dc=example,dc=com` (如果 LDAP 禁止匿名查询)
        - **Manager Password:** (对应密码)
    - 点击 **Test SASL** 或页面底部的 **Test** 按钮验证能否成功登录。

3.  **保存配置**。

### 7. 配置 Jenkins 构建节点 (可选)

随着项目增多，单台 Jenkins Master 可能负载过高。您可以添加额外的构建节点 (Agent) 来分担构建任务。

**场景：将测试服务器 (192.168.0.221) 兼作构建节点**
> 我们的初始化脚本 (`setup_test_server.sh`) 已经自动安装了 Java 环境并创建了 Agent 目录，您**无需**手动执行准备工作，请直接跳至 **步骤 2**。

#### 1. 准备节点服务器 (仅限添加其他新服务器)

如果您要添加一台全新的服务器 (例如 `192.168.0.222`)：

1.  **安装 Java:** Jenkins Agent 需要 Java 环境运行。
    ```bash
    dnf install -y java-17-openjdk
    ```
2.  **创建工作目录:**
    ```bash
    mkdir -p /opt/jenkins_agent
    ```
3.  **安装构建工具:** 根据需求安装 Docker, git 等。

#### 2. 在 Jenkins 中添加节点

以添加 **测试服务器** 为例：

1.  进入 **Manage Jenkins** -> **Nodes**。
2.  点击 **New Node**。
3.  **Node name:** 输入名称 (例如 `test-server-agent`)，选择 **Permanent Agent**，点击 **Create**。
4.  **配置节点详情:**
    - **Remote root directory:** `/opt/jenkins_agent`。
    - **Labels:** `test-env` `docker` (用于在 Pipeline 中指定，如 `agent { label 'test-env' }`)。
    - **Usage:** `Use this node as much as possible`。
    - **Launch method:** 选择 `Launch agents via SSH`。
        - **Host:** `192.168.0.221` (或其他节点 IP)
        - **Credentials:** 选择之前创建的 `test-server-ssh` (使用 root 权限方便调用 Docker)。
        - **Host Key Verification Strategy:** `Non verifying Verification Strategy` (内网测试环境)。
5.  点击 **Save**。

#### 3. 验证连接

保存后，Jenkins 会自动尝试连接。如果日志显示 "Agent successfully connected and online"，则配置成功。


