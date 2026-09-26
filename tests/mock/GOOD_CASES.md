# 现场 GOOD CASE 验收依据

本目录保留真实执行事实、身份和观察时间。成功主线与恢复分支分别标注，不把模拟通过或最终成功当成所有中间环节无异常。

## KT16 / A950FC259A78C43DB8D6D82FC110C8B03

- [完整过程与验收断言](good_cases/KT16-A950FC259A78C43DB8D6D82FC110C8B03/README.md)
- [跨系统时间线 CSV](good_cases/KT16-A950FC259A78C43DB8D6D82FC110C8B03/timeline.csv)
- [原始持久化证据与 ECS 入口日志](good_cases/KT16-A950FC259A78C43DB8D6D82FC110C8B03/evidence.json)

现场区间：2026-09-25 21:16:47～21:46:22 UTC（北京时间次日 05:16:47～05:46:22）。
六箱完整入站、人工工作位放行及退箱；24 条 ECS 命令、30 个 Transport 均成功；包含任务业务完成后的独立退箱收尾。
同时保留一次冲突储位重新分配及一条 RECONCILING 取消通知，不将它们隐去。

这份归档是后续验收设计和数据取样依据，尚未接入可执行 Mock replay runner。现有
`data/kt16_manual_picking_good_case.json` 是另一条历史任务的 fixture，本次未覆盖或替换它。
