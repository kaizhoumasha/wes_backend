import hashlib
import hmac


class SignatureService:
    @staticmethod
    def calculate(app_secret: str, app_id: str, timestamp: str, method: str, path: str, body: str) -> str:
        sign_string = f"{app_id}{timestamp}{method}{path}{body}"
        return hmac.new(app_secret.encode("utf-8"), sign_string.encode("utf-8"), hashlib.sha256).hexdigest()

    @staticmethod
    def verify(expected: str, actual: str) -> bool:
        return hmac.compare_digest(expected, actual)
