# WorkLine Runtime Hold Quickstart

Runtime Hold 是运行时异常恢复的唯一操作入口。Session、Command、Outbox、WorkLine 状态都会在 Hold release 时统一收敛。

## 获取 Token

```bash
curl -s -X POST http://localhost:8001/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin123"}'
```

后续示例假设：

```bash
export TOKEN='<access_token>'
export BASE='http://localhost:8001/api/v1/workline'
```

## 查看 Hold 明细

```bash
curl -s "$BASE/runtime-holds/1" \
  -H "Authorization: Bearer $TOKEN"
```

关键字段：

- `data.summary.version`: resolve 时传入 `hold_version`
- `data.release_eligibility.latest_evidence_hash`: resolve 时传入 `latest_evidence_hash`
- `data.release_eligibility.required_checks`: 现场 checklist，提交时每项必须为 `true`
- `data.blockers`: 同一 WorkLine 上仍阻断 READY 的其他 Hold

## 继续生产

```bash
curl -s -X POST "$BASE/runtime-holds/1/resolve" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "resolution": "COMPLETED",
    "checks": {
      "device_inspected": true,
      "physical_state_confirmed": true,
      "inventory_or_position_reconciled": true,
      "late_callback_reviewed": true
    },
    "operator_note": "现场确认物料位置正确，继续生产",
    "material_disposition": "CONTINUE",
    "result_payload": {},
    "hold_version": 0,
    "latest_evidence_hash": "sha256:replace-with-detail-hash"
  }'
```

预期响应：

```json
{
  "code": "1000",
  "data": {
    "status": "RESOLVED",
    "workline_runtime_status": "READY",
    "released_outbox_count": 1
  }
}
```

## 退回 NG 暂存

```bash
curl -s -X POST "$BASE/runtime-holds/1/resolve" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "resolution": "FAILED",
    "checks": {
      "device_inspected": true,
      "physical_state_confirmed": true,
      "inventory_or_position_reconciled": true,
      "late_callback_reviewed": true
    },
    "operator_note": "物料已放入 NG-01，等待周期性重做",
    "material_disposition": "RETURN_TO_NG",
    "ng_reason": {
      "source": "PLUGIN",
      "code": "SCAN_NG",
      "label": "扫码异常"
    },
    "physical_handoff_evidence": {
      "ng_location_code": "NG-01",
      "ng_location_scan": "NG-01",
      "material_scan_payload": {"PkgID": "PKG001"},
      "line_clear_checked": true,
      "late_callback_reviewed": true
    },
    "hold_version": 0,
    "latest_evidence_hash": "sha256:replace-with-detail-hash"
  }'
```

`RETURN_TO_NG` 必须由插件解析出 `MaterialIdentity.RESOLVED`。显示字段如 `HHPN`、`MfrPN`、`Qty` 只能帮助现场识别，不能替代物料身份。

## 查询 NG 原因与 NG Items

```bash
curl -s "$BASE/runtime-holds/ng-reasons" \
  -H "Authorization: Bearer $TOKEN"

curl -s "$BASE/ng-return-items?runtime_hold_id=1&status=WAITING_REWORK" \
  -H "Authorization: Bearer $TOKEN"
```

## 常见错误

### 409 RUNTIME_HOLD_VERSION_CONFLICT

页面持有的 `hold_version` 已过期。读取响应中的：

- `data.current_hold_version`
- `data.release_eligibility.latest_evidence_hash`
- `data.refresh_url`

刷新明细后重新提交。

### 409 RUNTIME_HOLD_EVIDENCE_CHANGED

页面加载后出现迟到 callback 或其他证据变化。必须重新查看 Hold 明细并重新确认 checklist。

### 409 RUNTIME_HOLD_ALREADY_RESOLVED

该 Hold 已被其他人解除。刷新 `data.refresh_url` 查看最新状态和 NG item。

### 422 RUNTIME_HOLD_MISSING_RELEASE_EVIDENCE

`RETURN_TO_NG` 缺少 `ng_reason`、`physical_handoff_evidence`，或插件无法解析物料身份。重新扫描 NG 位置和物料，确保 `material_scan_payload` 包含插件可解析的身份字段。

## 修复历史数据

只预览：

```bash
uv run python scripts/data/repair_runtime_holds.py --dry-run --limit 100
```

执行修复：

```bash
uv run python scripts/data/repair_runtime_holds.py --apply --limit 100
```

输出 JSON 中应关注：

- `would_create` / `created`
- `duplicates`
- `unmapped_reasons`
- `missing_material_identity`
- `active_reconciliation_sessions`
- `active_runtime_holds`
