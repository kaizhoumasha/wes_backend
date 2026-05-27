import asyncio
import uuid
from contextlib import suppress
from typing import Any

from loguru import logger

from src.workline_runtime.orchestrator import OrchestratorResult, OrchestratorService


class InboxBatchProcessor:
    def __init__(self, write_back_service: Any = None) -> None:
        self.write_back_service = write_back_service

    async def process_batch(self, db: Any, limit: int = 10) -> Any:  # noqa: PLR0912
        """批量处理 Inbox 消息

        处理流程：
        1. 从数据库获取 status='NEW' 的待处理消息（limit 限制数量）
        2. 遍历每个消息：
           a. 尝试加锁标记为 PROCESSING（并发控制）
           b. 入站后 malformed gate：空的 SCAN_COMPLETED payload 直接失败
           c. 加载关联实体（session/workline/device/devices_by_role）
           d. 调用 OrchestratorService.process_inbox() 执行编排
           e. 成功：应用编排结果，更新状态为 PROCESSED
           f. 失败：更新状态为 FAILED
        3. 提交数据库事务

        并发控制：
        - 使用 SELECT ... FOR UPDATE SKIP LOCKED 获取消息
        - 使用 processor_token 标记处理 worker
        - 已被锁定的消息会被标记为 SKIPPED

        Args:
            db: 数据库会话
            limit: 批处理数量，默认 10

        Returns:
            处理结果统计 {
                "processed": 处理总数,
                "success": 成功数,
                "failed": 失败数,
                "skipped": 跳过数（已被其他 worker 锁定）
            }
        """
        from src.app.workline.services.inbox_service import inbox_service
        from src.celery_app.tasks.workline import (
            INBOX_PROCESS_TIMEOUT_SECONDS,
            ErrorCode,
            ErrorDomain,
            ProcessResult,
            SessionResolveError,
            WorkLineSafetyBlocked,
            _assert_workline_accepting_runtime_event,
            _build_orchestrator_lock_provider,
            _canonical_event_type,
            _duplicate_entry_material_conflict,
            _enqueue_outbox_dispatch,
            _is_duplicate_entry_event_for_session,
            _is_late_or_duplicate_command_result_for_session,
            _load_related_entities,
            _problem_class_for_error_domain,
            _record_diagnostic,
            _record_duplicate_entry_archive_timeline,
            _record_late_command_result_archive_timeline,
            _resolve_entity_id,
            _resolve_required_pk,
            _result_requires_outbox_dispatch,
            _scan_completed_has_any_barcode_payload,
            _session_status_value,
            _session_write_snapshot,
            _snapshot_inbox_for_diagnostic,
            map_failure_to_diagnostic,
            payload_dict,
        )

        result: ProcessResult = {
            "processed": 0,
            "success": 0,
            "failed": 0,
            "skipped": 0,
        }

        # 获取待处理消息
        messages = await inbox_service.get_new_messages(db, limit=limit)

        for inbox in messages:
            diagnostic_inbox = _snapshot_inbox_for_diagnostic(inbox)
            inbox_pk_text = str(diagnostic_inbox.id or getattr(inbox, "id", "unknown"))
            inbox_pk: int | None = None  # 初始化，避免 basedpyright 警告
            try:
                inbox_pk = _resolve_required_pk(inbox, "inbox", "id", "inbox_id")
                # 尝试标记为处理中（并发控制）
                processor_token = str(uuid.uuid4())
                try:
                    _ = await inbox_service.mark_as_processing(db, inbox_pk, processor_token, auto_commit=False)
                except ValueError:
                    # 已被其他 worker 处理
                    result["skipped"] += 1
                    continue

                # ========== 前置验证：检查必填字段 ==========
                payload = payload_dict(getattr(inbox, "payload_json", None))
                resolved_event_type = _canonical_event_type(payload)

                # SCAN_COMPLETED 事件必须包含条码信息。
                # 优先使用 canonical_event_type，缺失时回退 event_type。
                # 这里只做 registry 无关的 payload 最小校验，避免把错误归因绑定到
                # plugin_key / registry / session 解析结果上。
                if resolved_event_type == "SCAN_COMPLETED" and not _scan_completed_has_any_barcode_payload(payload):
                    error_msg = "SCAN_COMPLETED 缺少条码信息（HHPN/MfrPN/Qty/DateCode/LotCode/PkgID 或 barcode）"
                    logger.warning(f"Inbox {inbox_pk} {error_msg}")
                    await _record_diagnostic(
                        db,
                        inbox=inbox,
                        error_code=ErrorCode.CALLBACK_SCHEMA_INVALID,
                        message=error_msg,
                    )
                    _ = await inbox_service.mark_as_failed(db, inbox_pk, error_msg, auto_commit=False)
                    await db.commit()
                    result["failed"] += 1
                    result["processed"] += 1
                    continue

                # 加载关联实体
                entities = await _load_related_entities(db, inbox, resolved_event_type=resolved_event_type)
                session = entities["session"]
                workline = entities["workline"]

                if resolved_event_type == "ESTOP_PRESSED":
                    from src.app.workline.services.safety_service import workline_safety_service

                    workline_pk = _resolve_entity_id(workline)
                    if workline_pk is None:
                        error_msg = "ESTOP_PRESSED missing workline context"
                        await _record_diagnostic(
                            db,
                            inbox=inbox,
                            error_code=ErrorCode.SESSION_CONTEXT_MISSING,
                            message=error_msg,
                            session=session,
                            workline=workline,
                            device=entities["device"],
                            command=entities["command"],
                        )
                        _ = await inbox_service.mark_as_failed(db, inbox_pk, error_msg, auto_commit=False)
                        await db.commit()
                        result["failed"] += 1
                        result["processed"] += 1
                        logger.warning(f"Inbox {inbox_pk} 处理失败: {error_msg}")
                        continue

                    incident = await workline_safety_service.handle_estop(
                        db,
                        workline_id=workline_pk,
                        source_inbox_id=inbox_pk,
                        source_device_id=_resolve_entity_id(entities["device"]) or getattr(inbox, "device_id", None),
                        source_command_id=_resolve_entity_id(entities["command"]) or getattr(inbox, "command_id", None),
                        trigger_payload=payload,
                    )
                    _ = await inbox_service.mark_as_processed(db, inbox_pk, auto_commit=False)
                    await db.commit()
                    result["success"] += 1
                    result["processed"] += 1
                    logger.warning(f"Inbox {inbox_pk} 已处理 WorkLine 急停: incident_id={incident.id}")
                    continue

                inbox_kind = getattr(getattr(inbox, "kind", None), "value", getattr(inbox, "kind", None))
                if inbox_kind == "TIMER_TIMEOUT":
                    from src.app.workline.services.runtime_reconciliation_service import (
                        workline_runtime_reconciliation_service,
                    )

                    _ = await workline_runtime_reconciliation_service.handle_timer_timeout(db, inbox=inbox)
                    await db.commit()
                    result["success"] += 1
                    result["processed"] += 1
                    logger.warning(f"Inbox {inbox_pk} 已处理系统级 TIMER_TIMEOUT 对账")
                    continue

                if not entities.get("safety_checked", True):
                    _ = await _assert_workline_accepting_runtime_event(
                        db, workline=workline, resolved_event_type=resolved_event_type
                    )

                if session is None or workline is None:
                    error_msg = "Inbox processing missing session/workline context"
                    await _record_diagnostic(
                        db,
                        inbox=inbox,
                        error_code=ErrorCode.SESSION_CONTEXT_MISSING,
                        message=error_msg,
                        session=session,
                        workline=workline,
                        device=entities["device"],
                        command=entities["command"],
                    )
                    _ = await inbox_service.mark_as_failed(db, inbox_pk, error_msg, auto_commit=False)
                    await db.commit()
                    result["failed"] += 1
                    result["processed"] += 1
                    logger.warning(f"Inbox {inbox_pk} 处理失败: {error_msg}")
                    continue

                if _is_duplicate_entry_event_for_session(
                    inbox=inbox, payload=payload, session=session, workline=workline
                ):
                    material_conflict = _duplicate_entry_material_conflict(
                        session=session,
                        workline=workline,
                        payload=payload,
                    )
                    if material_conflict is not None:
                        conflict_message, conflict_details = material_conflict
                        await _record_diagnostic(
                            db,
                            inbox=inbox,
                            error_code=ErrorCode.CALLBACK_SCHEMA_INVALID,
                            message=conflict_message,
                            session=session,
                            workline=workline,
                            device=entities["device"],
                            command=entities["command"],
                            extra=conflict_details,
                        )
                        _ = await inbox_service.mark_as_dead_letter(
                            db,
                            inbox_pk,
                            conflict_message,
                            auto_commit=False,
                        )
                        await db.commit()
                        result["failed"] += 1
                        result["processed"] += 1
                        logger.warning(
                            f"Inbox {inbox_pk} rejected conflicting duplicate entry event: "
                            f"session_id={_resolve_entity_id(session)}, conflicts={conflict_details['conflicts']}"
                        )
                        continue

                    await _record_duplicate_entry_archive_timeline(
                        db,
                        session=session,
                        workline=workline,
                        inbox=inbox,
                        payload=payload,
                        reason="SESSION_ALREADY_IN_PROGRESS_OR_TERMINAL",
                    )
                    _ = await inbox_service.mark_as_processed(db, inbox_pk, auto_commit=False)
                    await db.commit()
                    result["success"] += 1
                    result["processed"] += 1
                    logger.warning(
                        f"Inbox {inbox_pk} archived duplicate entry event: "
                        f"session_id={_resolve_entity_id(session)}, "
                        f"status={_session_status_value(session)}, "
                        f"awaiting_command_id={getattr(session, 'awaiting_command_id', None)}"
                    )
                    continue

                if _is_late_or_duplicate_command_result_for_session(
                    inbox=inbox,
                    payload=payload,
                    session=session,
                    command=entities["command"],
                ):
                    await _record_late_command_result_archive_timeline(
                        db,
                        session=session,
                        workline=workline,
                        inbox=inbox,
                        command=entities["command"],
                        payload=payload,
                        reason="COMMAND_RESULT_NO_LONGER_MATCHES_SESSION_WAIT",
                    )
                    _ = await inbox_service.mark_as_processed(db, inbox_pk, auto_commit=False)
                    await db.commit()
                    result["success"] += 1
                    result["processed"] += 1
                    logger.warning(
                        f"Inbox {inbox_pk} archived late command result: "
                        f"session_id={_resolve_entity_id(session)}, "
                        f"command_id={_resolve_entity_id(entities['command'])}, "
                        f"status={_session_status_value(session)}, "
                        f"awaiting_command_id={getattr(session, 'awaiting_command_id', None)}"
                    )
                    continue

                write_effects_applied = False
                enqueue_outbox_dispatch = False
                session_snapshot = _session_write_snapshot(session)

                async def _write_callback(
                    write_result: OrchestratorResult,
                    _session: Any = session,
                    _workline: Any = workline,
                    _inbox: Any = inbox,
                    _devices_by_role: dict[str, list[Any]] = entities["devices_by_role"],
                    _device: Any | None = entities["device"],
                    _command: Any | None = entities["command"],
                    _inbox_pk: int = inbox_pk,
                    _session_snapshot: tuple[Any, Any] = session_snapshot,
                ) -> None:
                    nonlocal write_effects_applied, enqueue_outbox_dispatch
                    try:
                        await db.refresh(_session)
                        _payload = payload_dict(getattr(_inbox, "payload_json", None))
                        if _is_late_or_duplicate_command_result_for_session(
                            inbox=_inbox,
                            payload=_payload,
                            session=_session,
                            command=_command,
                        ):
                            await _record_late_command_result_archive_timeline(
                                db,
                                session=_session,
                                workline=_workline,
                                inbox=_inbox,
                                command=_command,
                                payload=_payload,
                                reason="COMMAND_RESULT_BECAME_STALE_BEFORE_WRITE",
                            )
                            _ = await inbox_service.mark_as_processed(db, _inbox_pk, auto_commit=False)
                            await db.commit()
                            write_effects_applied = True
                            enqueue_outbox_dispatch = False
                            return

                        if _session_write_snapshot(_session) != _session_snapshot:
                            raise RuntimeError(
                                "Session state changed before WRITE apply; refusing stale orchestrator effects"
                            )
                        from src.workline_runtime.session_resolver import reapply_pending_session_ingress_metadata

                        _ = reapply_pending_session_ingress_metadata(_session)
                        from src.app.workline.services.write_back_service import orchestrator_write_back_service

                        await orchestrator_write_back_service.write_back(
                            db,
                            session=_session,
                            workline=_workline,
                            inbox=_inbox,
                            devices_by_role=_devices_by_role,
                            source_device=_device,
                            orch_result=write_result,
                        )
                        _ = await inbox_service.mark_as_processed(db, _inbox_pk, auto_commit=False)
                        await db.commit()
                        write_effects_applied = True
                        enqueue_outbox_dispatch = _result_requires_outbox_dispatch(write_result)
                        # 通知前端工作线运行态已变更，key 用于增量刷新定位
                        from src.app.sys.services.event_stream_service import (
                            WORKLINE_RUNTIME_CHANGED_EVENT,
                            defer_sse_event,
                        )

                        defer_sse_event(
                            db,
                            WORKLINE_RUNTIME_CHANGED_EVENT,
                            {
                                "domain": "workline_trace",
                                "entity": "session",
                                "action": "updated",
                                "keys": {
                                    "workline_id": getattr(_workline, "id", None),
                                    "session_id": getattr(_session, "id", None),
                                },
                            },
                        )
                    except Exception:
                        await db.rollback()
                        raise

                # 调用编排器（带超时保护）
                orchestrator = OrchestratorService(lock_provider=_build_orchestrator_lock_provider(db))
                orch_result: OrchestratorResult = await asyncio.wait_for(
                    orchestrator.process_inbox(
                        session=session,
                        workline=workline,
                        inbox=inbox,
                        devices_by_role=entities["devices_by_role"],
                        services=entities["services"],
                        trace_id=inbox.trace_id or "",
                        write_callback=_write_callback,
                    ),
                    timeout=INBOX_PROCESS_TIMEOUT_SECONDS,
                )

                # 根据结果更新状态
                if orch_result.success:
                    if not write_effects_applied:
                        raise RuntimeError("WRITE lock callback was not executed for successful orchestrator result")

                    result["success"] += 1
                    logger.info(f"Inbox {inbox_pk} 处理成功")

                    if enqueue_outbox_dispatch:
                        _enqueue_outbox_dispatch()
                else:
                    error_msg = orch_result.error or "Unknown error"
                    mapped_error_code, mapped_error_domain = map_failure_to_diagnostic(
                        failure=None,
                        error_code=orch_result.error_code,
                    )
                    await _record_diagnostic(
                        db,
                        inbox=inbox,
                        error_code=mapped_error_code,
                        error_domain=mapped_error_domain,
                        problem_class=_problem_class_for_error_domain(mapped_error_domain),
                        message=error_msg,
                        session=session,
                        workline=workline,
                        device=entities["device"],
                        command=entities["command"],
                    )
                    _ = await inbox_service.mark_as_failed(db, inbox_pk, error_msg, auto_commit=False)
                    await db.commit()
                    result["failed"] += 1
                    logger.warning(f"Inbox {inbox_pk} 处理失败: {error_msg}")

                result["processed"] += 1

            except SessionResolveError as e:
                logger.warning(f"Inbox {inbox_pk_text} session resolve failed: {e}")
                with suppress(Exception):
                    await db.rollback()
                await _record_diagnostic(
                    db,
                    inbox=diagnostic_inbox,
                    error_code=ErrorCode.SESSION_RESOLVE_FAILED,
                    message=str(e),
                )
                try:
                    inbox_pk = diagnostic_inbox.id
                    if inbox_pk is not None:
                        _ = await inbox_service.mark_as_failed(db, inbox_pk, str(e), auto_commit=False)
                        await db.commit()
                except Exception as mark_error:
                    logger.warning(f"Inbox {inbox_pk_text} session resolve 失败补记失败: {mark_error}")
                result["failed"] += 1
                result["processed"] += 1

            except WorkLineSafetyBlocked as e:
                logger.warning(f"Inbox {inbox_pk_text} blocked by WorkLine safety state: {e}")
                with suppress(Exception):
                    await db.rollback()
                await _record_diagnostic(
                    db,
                    inbox=diagnostic_inbox,
                    error_code=ErrorCode.UNKNOWN,
                    error_domain=ErrorDomain.WORKFLOW,
                    message=str(e),
                )
                try:
                    inbox_pk = diagnostic_inbox.id
                    if inbox_pk is not None:
                        _ = await inbox_service.mark_as_failed(db, inbox_pk, str(e), auto_commit=False)
                        await db.commit()
                except Exception as mark_error:
                    logger.warning(f"Inbox {inbox_pk_text} safety blocked 补记失败: {mark_error}")
                result["failed"] += 1
                result["processed"] += 1

            except TimeoutError:
                # 处理超时，不阻塞其他消息
                logger.error(f"Inbox {inbox_pk} 处理超时 (> {INBOX_PROCESS_TIMEOUT_SECONDS}s)")
                with suppress(Exception):
                    await db.rollback()
                await _record_diagnostic(
                    db,
                    inbox=diagnostic_inbox,
                    error_code=ErrorCode.INBOX_PROCESSING_TIMEOUT,
                    message=f"Inbox processing timeout (> {INBOX_PROCESS_TIMEOUT_SECONDS}s)",
                )
                try:
                    # 使用已解析的 inbox_pk（如果在前面解析成功）
                    pk_to_mark = locals().get("inbox_pk") or diagnostic_inbox.id
                    if pk_to_mark is not None:
                        _ = await inbox_service.mark_as_failed(
                            db,
                            pk_to_mark,
                            f"处理超时 (> {INBOX_PROCESS_TIMEOUT_SECONDS}s)",
                            auto_commit=False,
                        )
                        await db.commit()
                except Exception as mark_error:
                    logger.warning(f"Inbox 超时标记失败: {mark_error}")
                result["failed"] += 1
                result["processed"] += 1

            except Exception as e:
                logger.exception(f"Inbox {inbox_pk_text} 处理异常")
                with suppress(Exception):
                    await db.rollback()
                await _record_diagnostic(
                    db,
                    inbox=diagnostic_inbox,
                    error_code=ErrorCode.UNKNOWN,
                    message=str(e),
                )
                try:
                    inbox_pk = diagnostic_inbox.id
                    if inbox_pk is not None:
                        _ = await inbox_service.mark_as_failed(db, inbox_pk, str(e), auto_commit=False)
                        await db.commit()
                except Exception as mark_error:
                    logger.warning(f"Inbox {inbox_pk_text} 异常补记失败: {mark_error}")
                result["failed"] += 1
                result["processed"] += 1

        from src.app.sys.services.event_stream_service import publish_deferred_sse_events

        await publish_deferred_sse_events(db)
        return result
