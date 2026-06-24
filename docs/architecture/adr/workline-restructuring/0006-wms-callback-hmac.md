# ADR 0006: WMS Callback Body HMAC + Nonce TTL

**状态**: Accepted
**日期**: 2026-06-23
**适用范围**: `src/app/callback/v1/callback.py:91` 的所有 WMS callback 入口

## 背景

实测 `src/app/wms_integration/services/callback_normalizer.py` 已有 `signature` + `timestamp` 字段但**无 nonce / replay window 检查**——同一个合法签名的 callback payload 可以重放。`api_security.py:58,87` 签名基类只绑定 `app_id/timestamp/method/path`，**不绑 body**，存在篡改窗口。normalizer 在 `callback_normalizer.py:61` 只校验字段，不验 callback body 签名。

## 决策

1. **HMAC-SHA256 body 签名**：签名 canonical string 必须包含：
   ```text
   canonical = method + "\n" + path + "\n" + timestamp + "\n" + nonce + "\n" + sha256(body) + "\n" + app_id
   signature = HMAC-SHA256(secret, canonical)
   ```
2. **nonce + timestamp**：
   - `timestamp` 与 WES 时钟偏差 > 30s → 拒绝
   - `nonce` 按 `app_id` 做 5 分钟 TTL 去重
3. **callback_type allow-list**：callback_type 必须匹配未终结 fulfillment request 的允许列表；不匹配返回 400。
4. **Body 完整性**：signature 校验失败立即返回 401，**不触发**业务处理；防重放窗口 5 分钟。
5. **入口统一**：所有 callback 走 `src/app/callback/v1/callback.py:91` 的统一入口；不引入新的 callback 路径。
6. **签名基类统一**：升级 `src/core/api_security.py:58,87` 的 signature 校验，强制包含 body hash + nonce + path。

## 后果

- WMS callback 鉴权从"字段级"升级为"body 完整性级"。
- replay 攻击在 5 分钟窗口内被 nonce 去重拦截。
- body 篡改立即 401，不进入业务处理。
- callback_type 防止攻击者用未授权 callback 类型绕过业务校验。

## 验收

- `docs/architecture/specs/workline-restructuring/40-resource-projection.md` §6 发布。
- `src/app/wms_integration/services/callback_normalizer.py` 实现 HMAC body 签名 + nonce TTL。
- `src/core/api_security.py` 签名基类升级。
- 安全测试覆盖重放、篡改、时钟偏差、type allow-list 路径。

## 引用

- 顶层设计：[`../../workline-and-plugin-restructuring.md`](../../workline-and-plugin-restructuring.md)
- Sub-spec 40 §6：[`../../specs/workline-restructuring/40-resource-projection.md`](../../specs/workline-restructuring/40-resource-projection.md)
- ADR 0005：[`0005-idempotency-composite-key.md`](0005-idempotency-composite-key.md)
