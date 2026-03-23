import hashlib
import hmac


class SignatureService:
    @staticmethod
    def calculate(app_secret: str, app_id: str, timestamp: str, method: str, path: str) -> str:
        """计算 API 签名

        签名字符串格式: {app_id}{timestamp}{method}{path}
        注意: 不包含 body，避免 JSON 序列化导致的签名不一致问题

        Args:
            app_secret: 应用密钥
            app_id: 应用 ID
            timestamp: 时间戳（秒）
            method: HTTP 方法（大写）
            path: 请求路径

        Returns:
            签名字符串（小写十六进制）
        """
        sign_string = f"{app_id}{timestamp}{method}{path}"
        return hmac.new(app_secret.encode("utf-8"), sign_string.encode("utf-8"), hashlib.sha256).hexdigest()

    @staticmethod
    def verify(expected: str, actual: str) -> bool:
        return hmac.compare_digest(expected, actual)
