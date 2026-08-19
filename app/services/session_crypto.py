"""
Optional Fernet encryption for Telegram session strings at rest.

If SESSION_ENCRYPTION_KEY is not set, sessions are stored in plaintext
(with a logged warning). Set the key in production.
"""
import logging
from cryptography.fernet import Fernet, InvalidToken
from flask import current_app

logger = logging.getLogger(__name__)

_fernet_cache = {}


def _get_fernet():
    key = current_app.config.get('SESSION_ENCRYPTION_KEY', '')
    if not key:
        return None
    if key not in _fernet_cache:
        try:
            _fernet_cache[key] = Fernet(key.encode() if isinstance(key, str) else key)
        except Exception as e:
            logger.error("Invalid SESSION_ENCRYPTION_KEY: %s. Sessions will be plaintext.", e)
            return None
    return _fernet_cache[key]


def encrypt_session(session_string: str) -> str:
    """Encrypt a Telethon StringSession for DB storage."""
    if not session_string:
        return session_string
    f = _get_fernet()
    if not f:
        return session_string
    try:
        return f.encrypt(session_string.encode()).decode()
    except Exception as e:
        logger.error("Failed to encrypt session: %s", e)
        return session_string


def decrypt_session(stored_value: str) -> str:
    """Decrypt a session string from DB for Telethon use."""
    if not stored_value:
        return stored_value
    f = _get_fernet()
    if not f:
        return stored_value  # Plaintext mode
    try:
        return f.decrypt(stored_value.encode()).decode()
    except InvalidToken:
        # Not encrypted (legacy data) — return as-is
        return stored_value
    except Exception as e:
        logger.error("Failed to decrypt session: %s", e)
        return stored_value
