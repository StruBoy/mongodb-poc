"""Application-layer field encryption for sensitive PII.

The PoC uses AES-256-GCM at the application layer for clarity — the encrypt /
decrypt calls are right next to the application code that handles PII, which
makes the demo's "what does the database actually store?" moment land cleanly.

In production, MongoDB Queryable Encryption (CSFLE) is the right answer:
the database itself does the encryption, the key vault is centrally managed,
and equality / range queries work against the ciphertext. The architectural
pattern shown here — encrypt-on-write, decrypt-only-by-authorized-callers — is
the same. Mention this distinction during the demo.
"""
import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from dotenv import load_dotenv

load_dotenv()

_key = base64.b64decode(os.environ["ENCRYPTION_KEY"])
if len(_key) != 32:
    raise ValueError(
        f"ENCRYPTION_KEY must decode to 32 bytes (AES-256); got {len(_key)} bytes"
    )
_aes = AESGCM(_key)

# Fields that always go through encrypt_pii before write
PII_FIELDS = {"national_id", "dob", "tax_file_number", "partner_national_id"}


def encrypt_field(plaintext) -> dict:
    if plaintext is None:
        return None
    nonce = os.urandom(12)
    ct = _aes.encrypt(nonce, str(plaintext).encode("utf-8"), None)
    return {
        "_encrypted": True,
        "nonce": base64.b64encode(nonce).decode(),
        "ciphertext": base64.b64encode(ct).decode(),
    }


def decrypt_field(field) -> str:
    if field is None or not isinstance(field, dict) or not field.get("_encrypted"):
        return field
    nonce = base64.b64decode(field["nonce"])
    ct = base64.b64decode(field["ciphertext"])
    return _aes.decrypt(nonce, ct, None).decode("utf-8")


def _is_encrypted_envelope(v) -> bool:
    return isinstance(v, dict) and v.get("_encrypted") is True


def encrypt_pii(doc: dict) -> dict:
    out = {}
    for k, v in doc.items():
        if k in PII_FIELDS and v is not None and not _is_encrypted_envelope(v):
            out[k] = encrypt_field(v)
        elif isinstance(v, dict) and not _is_encrypted_envelope(v):
            out[k] = encrypt_pii(v)
        else:
            out[k] = v
    return out


def decrypt_pii(doc: dict) -> dict:
    if doc is None:
        return None
    out = {}
    for k, v in doc.items():
        if k in PII_FIELDS and _is_encrypted_envelope(v):
            out[k] = decrypt_field(v)
        elif isinstance(v, dict) and not _is_encrypted_envelope(v):
            out[k] = decrypt_pii(v)
        else:
            out[k] = v
    return out
