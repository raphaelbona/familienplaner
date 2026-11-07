"""Fernet symmetric encryption for sensitive data like OAuth tokens."""

import base64
import hashlib

from cryptography.fernet import Fernet

from app.core.config import get_settings


def _derive_fernet_key(secret_key: str) -> bytes:
    """Derive a Fernet-compatible key from a secret string.

    Takes the SHA256 hash of the secret key and encodes it in base64 URL-safe format.
    """
    return base64.urlsafe_b64encode(hashlib.sha256(secret_key.encode()).digest())


def encrypt_secret(raw: str) -> str:
    """Encrypt a raw string using Fernet symmetric encryption.

    Args:
        raw: The plaintext string to encrypt (e.g. OAuth token).

    Returns:
        The encrypted token as a string, ready to store in the database.
    """
    settings = get_settings()
    key = _derive_fernet_key(settings.secret_key)
    fernet = Fernet(key)
    encrypted_bytes = fernet.encrypt(raw.encode())
    return encrypted_bytes.decode()


def decrypt_secret(token: str) -> str:
    """Decrypt a Fernet-encrypted token back to plaintext.

    Args:
        token: The encrypted token string (from the database).

    Returns:
        The decrypted plaintext string.

    Raises:
        cryptography.fernet.InvalidToken: If the token is invalid or corrupted.
    """
    settings = get_settings()
    key = _derive_fernet_key(settings.secret_key)
    fernet = Fernet(key)
    decrypted_bytes = fernet.decrypt(token.encode())
    return decrypted_bytes.decode()
