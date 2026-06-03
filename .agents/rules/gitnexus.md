# GitNexus And RTK Rules

本文件是 AGY / Antigravity workspace rule，内容来自 `AGENTS.md`。`AGENTS.md` 是项目规则主真源；如有冲突，以 `AGENTS.md` 为准。

## GitNexus

本仓库已由 GitNexus 索引为 `wes_backend`。

如果工具提示索引 stale，先运行：

```bash
npx gitnexus analyze
```

常用 CLI fallback：

```bash
npx gitnexus status
npx gitnexus query "<concept>"
npx gitnexus context <symbol>
npx gitnexus impact <symbol> --direction upstream
npx gitnexus detect-changes
```

## Required Safety Flow

- 修改任何函数、类、方法前，必须运行 impact analysis。
- impact 返回 HIGH 或 CRITICAL 风险时，必须先向用户汇报并确认。
- Commit 前运行 detect changes，确认变更范围符合预期。
- 探索陌生代码时，优先用 GitNexus 查询执行流，再用 `rg` 精确定位。
- 重命名符号不要用纯文本查找替换，应使用理解调用图的重命名能力。

## RTK

项目环境通过 RTK 代理执行 Shell 命令以节省 Token。

```bash
rtk gain
rtk proxy <cmd>
```

只有在怀疑 RTK 过滤导致输出丢失时，才用 `rtk proxy <cmd>` 获取原始输出。
