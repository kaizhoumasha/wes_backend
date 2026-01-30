from cryptography.fernet import Fernet

from src.core.conf import settings


class EncryptionService:
    def __init__(self):
        self.cipher = Fernet(settings.API_SECRET_ENCRYPTION_KEY.encode())

    def encrypt(self, plaintext: str) -> str:
        return self.cipher.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        return self.cipher.decrypt(ciphertext.encode()).decode()


encryption_service = EncryptionService()
