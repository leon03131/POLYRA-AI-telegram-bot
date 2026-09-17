"""Authenticated encryption helpers based on Fernet."""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken


class CryptoBox:
    """Fernet (AES-128-CBC + HMAC, authenticated) wrapper.

    ``master_key`` may be either a urlsafe base64 Fernet key or an arbitrary
    passphrase (then a key is derived via SHA-256 -> urlsafe_b64encode).
    """

    def __init__(self, master_key: str) -> None:
        if not master_key:
            raise ValueError("master_key must not be empty")
        self._fernet = Fernet(self._to_fernet_key(master_key))

    @staticmethod
    def _to_fernet_key(master_key: str) -> bytes:
        raw = master_key.encode("utf-8")
        try:
            decoded = base64.urlsafe_b64decode(raw)
        except ValueError:
            decoded = b""
        if len(decoded) == 32:
            return raw
        return base64.urlsafe_b64encode(hashlib.sha256(raw).digest())

    def encrypt(self, plaintext: str) -> str:
        """Encrypt UTF-8 text and return a urlsafe base64 token string."""
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str) -> str:
        """Decrypt a token back to text.

        Raises:
            ValueError: if the token is invalid or cannot be decrypted.
        """
        try:
            return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError) as exc:
            raise ValueError("decryption failed") from exc


def mask_secret(secret: str, head: int = 4, tail: int = 4) -> str:
    """Return ``<head>...<tail>``; ``***`` for short secrets, ``""`` for an empty one."""
    if not secret:
        return ""
    if len(secret) <= head + tail + 3:
        return "***"
    return f"{secret[:head]}...{secret[-tail:]}"
