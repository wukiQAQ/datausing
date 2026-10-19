"""审计溯源：哈希链 + HMAC 签名。"""

from .chain import (
    GENESIS,
    append_entry,
    canonical_hash,
    list_entries,
    sign,
    verify_chain,
)

__all__ = [
    "GENESIS",
    "append_entry",
    "canonical_hash",
    "list_entries",
    "sign",
    "verify_chain",
]
