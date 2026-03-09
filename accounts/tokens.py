import hashlib
import secrets

KEY_PREFIX = "cf_"
KEY_PREFIX_LENGTH = 12


def generate_raw_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(32)


def hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()
