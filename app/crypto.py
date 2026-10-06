import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"SC01"


def encrypt_bytes(data: bytes, key: bytes) -> bytes:
    if len(key) != 32:
        raise ValueError("AES-256-GCM requires a 32-byte key")
    nonce = os.urandom(12)
    ciphertext = AESGCM(key).encrypt(nonce, data, None)
    return MAGIC + nonce + ciphertext


def decrypt_bytes(blob: bytes, key: bytes) -> bytes:
    if len(key) != 32:
        raise ValueError("AES-256-GCM requires a 32-byte key")
    if len(blob) < 4 + 12 + 16 or blob[:4] != MAGIC:
        raise ValueError("Encrypted payload is not a screen-capture file")
    nonce = blob[4:16]
    ciphertext = blob[16:]
    return AESGCM(key).decrypt(nonce, ciphertext, None)
