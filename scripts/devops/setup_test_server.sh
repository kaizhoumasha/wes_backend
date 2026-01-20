#!/bin/bash
set -e

# 安装 Docker
echo "正在安装 Docker..."
rm -f /etc/yum.repos.d/docker-ce.repo
dnf install -y yum-utils
dnf config-manager --add-repo http://mirrors.aliyun.com/docker-ce/linux/centos/docker-ce.repo
dnf install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# 配置 Docker 镜像加速
mkdir -p /etc/docker
cat > /etc/docker/daemon.json <<EOF
{
  "registry-mirrors": [
    "https://docker.nju.edu.cn",
    "https://docker.m.daocloud.io",
    "https://docker.1panel.live",
    "http://hub-mirror.c.163.com",
    "https://mirror.iscas.ac.cn"
  ]
}
EOF
systemctl daemon-reload

systemctl start docker
systemctl enable docker

# 创建应用目录
mkdir -p /opt/wes_backend
chmod 755 /opt/wes_backend

# 安装 Java (用于 Jenkins Agent)
echo "正在安装 Java..."
dnf install -y java-17-openjdk

# 创建 Jenkins Agent 工作目录
mkdir -p /opt/jenkins_agent
chmod 755 /opt/jenkins_agent

echo "测试服务器环境安装完成。"
echo "请将您的应用部署到 /opt/wes_backend 目录。"
echo "若需将此服务器作为 Jenkins 构建节点，Agent 根目录为: /opt/jenkins_agent"