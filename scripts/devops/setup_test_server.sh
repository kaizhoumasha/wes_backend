#!/bin/bash
set -e

# 安装 Docker
echo "正在安装 Docker..."
dnf install -y yum-utils
dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
dnf install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl start docker
systemctl enable docker

# 创建应用目录
mkdir -p /opt/wes_backend
chmod 755 /opt/wes_backend

echo "测试服务器环境安装完成。"
echo "请将您的应用部署到 /opt/wes_backend 目录。"