#!/bin/bash
set -e

# 安装 Docker
echo "正在安装 Docker..."
rm -f /etc/yum.repos.d/docker-ce.repo
dnf install -y yum-utils
# dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
dnf config-manager --add-repo http://mirrors.aliyun.com/docker-ce/linux/centos/docker-ce.repo
dnf install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# 配置 Docker 镜像加速
mkdir -p /etc/docker
cat > /etc/docker/daemon.json <<EOF
{
  "registry-mirrors": [
    "https://docker.happyjack.cn/"
  ]
}
EOF
systemctl daemon-reload

systemctl start docker
systemctl enable docker

# 创建目录
mkdir -p /opt/docker/gitlab/config
mkdir -p /opt/docker/gitlab/logs
mkdir -p /opt/docker/gitlab/data
mkdir -p /opt/docker/jenkins_home

# 设置 Jenkins 权限 (uid 1000 通常是容器内的默认用户)
chown -R 1000:1000 /opt/docker/jenkins_home

# 创建 Docker Compose 文件
cat > /opt/docker/docker-compose.yml <<EOF
services:
  gitlab:
    image: gitlab/gitlab-ce:latest
    restart: always
    hostname: 'gitlab.local'
    environment:
      GITLAB_OMNIBUS_CONFIG: |
        external_url 'http://192.168.0.220:9080'
        gitlab_rails['gitlab_shell_ssh_port'] = 2222
    ports:
      - '9080:9080'
      - '443:443'
      - '2222:22'
    volumes:
      - '/opt/docker/gitlab/config:/etc/gitlab'
      - '/opt/docker/gitlab/logs:/var/log/gitlab'
      - '/opt/docker/gitlab/data:/var/opt/gitlab'
    shm_size: '256m'

  jenkins:
    image: jenkins/jenkins:lts
    restart: always
    privileged: true
    user: root
    ports:
      - '9081:8080'
      - '50000:50000'
    volumes:
      - '/opt/docker/jenkins_home:/var/jenkins_home'
      - '/var/run/docker.sock:/var/run/docker.sock'
      - '/usr/bin/docker:/usr/bin/docker'
EOF

# 启动服务
echo "正在启动 GitLab 和 Jenkins..."
cd /opt/docker
docker compose up -d

echo "安装完成！"
echo "GitLab 地址: http://192.168.0.220:9080 (初始 root 密码位置: /opt/docker/gitlab/config/initial_root_password)"
echo "Jenkins 地址: http://192.168.0.220:9081 (初始 admin 密码位置: /opt/docker/jenkins_home/secrets/initialAdminPassword)"