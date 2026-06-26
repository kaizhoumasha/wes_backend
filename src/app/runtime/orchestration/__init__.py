"""Phase 1 CEO-007 runtime/orchestration 域 (Packet C 起步)。

主计划 §9.2 7 个 runtime core 实体:
1. ExecutionSession (Packet C 起步)
2. ExecutionCorrelation (Packet C 起步)
3. ExecutionWorkItem (Packet C 后续)
4. RuntimeInbox (Packet C 后续)
5. RuntimeTimeline (Packet C 后续)
6. RuntimeHold (Packet C 后续)
7. RuntimeIntentLog (Packet C 后续)

设计约束 (主计划 §9.2 + §3.5 + P0-004 §4.6):
- execution_session_id 仅在 runtime/orchestration 域内强 FK; 跨域只持
  ExecutionCorrelation.correlation_id (无 session FK 泄漏)
- 对象级 work item 不被 session 串行锁阻塞 (Session 是聚合根, WorkItem 是
  runtime capability 最小推进单位)
- RuntimeInbox status 5 态: RECEIVED -> PROCESSING -> PROCESSED /
  FAILED / DEAD_LETTER (P0-004 §4.6 验证)
- RuntimeIntentLog 是 outbox/effect ledger, dispatch_status:
  PENDING -> DISPATCHING -> DISPATCHED/ACKED/FAILED
- InboundEventPort / WmsEventPort / DeviceEventPort / RuntimeInbox consumer
  不在业务 capability 注册表 (R-I3a/R-I3b)

Phase 1 起步: ExecutionSession + ExecutionCorrelation 模型 (2/7), 后续
会话按依赖顺序加 WorkItem / Inbox / Timeline / Hold / IntentLog。
"""
