"""Fernet encryption for secrets at rest (bot tokens, API keys)."""

from __future__ import annotations

from cryptography.fernet import Fernet

from lucy.config import settings

_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        key = settings.fernet_key
        if not key:
            raise RuntimeError(
                "LUCY_FERNET_KEY is not set. "
                "Generate one with: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
            )
        _fernet = Fernet(key.encode() if isinstance(key, str) else key)
    return _fernet


def encrypt(plaintext: str) -> str:
    """Encrypt a string and return the URL-safe base64 ciphertext."""
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    """Decrypt a ciphertext string back to plaintext."""
    return _get_fernet().decrypt(ciphertext.encode()).decode()
