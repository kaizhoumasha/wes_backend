#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os

# 获取项目根目录的绝对路径
BasePath = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 日志文件路径
LOG_DIR = os.path.join(BasePath, "logs")

# 离线 IP 数据库路径
IP2REGION_XDB = os.path.join(BasePath, "src", "static", "ip2region.xdb")

# 挂载静态目录
STATIC_DIR = os.path.join(BasePath, "src", "static")
UPLOAD_DIR = os.path.join(BasePath, "upload")

# jinja2 模版文件路径
JINJA2_TEMPLATE_DIR = os.path.join(BasePath, "templates")
