from pathlib import Path

# 获取项目根目录的绝对路径
BasePath = Path(__file__).parent.parent.parent.resolve()

# 日志文件路径
LOG_DIR = BasePath / "logs"

# 离线 IP 数据库路径
IP2REGION_XDB = BasePath / "src" / "static" / "ip2region.xdb"

# 挂载静态目录
STATIC_DIR = BasePath / "src" / "static"
UPLOAD_DIR = BasePath / "upload"

# jinja2 模版文件路径
JINJA2_TEMPLATE_DIR = BasePath / "templates"
