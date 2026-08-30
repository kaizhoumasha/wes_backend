# Workline Capability PostgreSQL 验证

本目录只允许连接到显式指定的本地隔离测试库。运行前必须设置
`INTEGRATION_DATABASE_URL`；测试夹具不会读取应用默认数据库，也不会清理开发库。

```bash
INTEGRATION_DATABASE_URL='postgresql+asyncpg://<user>:<password>@127.0.0.1:<port>/test_workline_capabilities' \
  uv run pytest tests/integration/workline_capabilities -q -o addopts=''
```

安全约束：

- 主机必须是本机安全地址，数据库名必须是 `postgres`、`template1`、`test`、`test_*` 或 `*_test`。
- 每个场景只创建并删除随机命名的 `wes_tmp_heavy_<uuid>` 临时数据库。
- 缺少 URL、目标不安全、管理员连接失败或连接容量不足时，夹具会在 preflight 阶段拒绝运行；错误中的阶段名可用于定位配置问题。
- 不要把开发库 URL 或生产凭据写入本文、测试代码或版本库。

推荐从 `.env.dev` 读取本机 PostgreSQL 端口和凭据后，仅在当前 shell 中组装
`INTEGRATION_DATABASE_URL`。验证结束后无需手工删除业务数据；夹具会关闭连接并删除临时数据库。
