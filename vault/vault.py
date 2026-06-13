from __future__ import annotations

import hashlib
import hmac
import os
import sqlite3
import secrets as _secrets

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from paths import VAULT_DB

_vault_key: bytes | None = None


class VaultLockedException(Exception):
    pass


def _init_schema(con: sqlite3.Connection) -> None:
    con.execute(
        "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    con.execute("""
        CREATE TABLE IF NOT EXISTS secrets (
            name_hash TEXT PRIMARY KEY,
            name_enc  TEXT NOT NULL,
            value_enc TEXT NOT NULL
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS rekey_log (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            rekeyed_at TEXT NOT NULL,
            secret_count INTEGER NOT NULL
        )
    """)
    con.commit()


def _get_or_create_salt(con: sqlite3.Connection) -> bytes:
    row = con.execute("SELECT value FROM meta WHERE key='salt'").fetchone()
    if row:
        return bytes.fromhex(row[0])
    salt = _secrets.token_bytes(32)
    con.execute("INSERT INTO meta (key, value) VALUES ('salt', ?)", (salt.hex(),))
    con.commit()
    return salt


_VERIFY_PLAINTEXT = b"kanbai-vault-v1"


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    kdf = Scrypt(salt=salt, length=32, n=2**14, r=8, p=1)
    return kdf.derive(passphrase.encode("utf-8"))


def _name_index_key(master_key: bytes) -> bytes:
    """Derive a separate sub-key for HMAC name indexing (never reuse the AES key)."""
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=b"kanbai-name-index",
    ).derive(master_key)


def _name_hash(master_key: bytes, name: str) -> str:
    """Deterministic, keyed lookup index for a secret name. Useless without the key."""
    return hmac.new(
        _name_index_key(master_key), name.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def _encrypt(key: bytes, data: bytes) -> str:
    nonce = _secrets.token_bytes(12)
    return (nonce + AESGCM(key).encrypt(nonce, data, None)).hex()


def _decrypt(key: bytes, blob_hex: str) -> bytes:
    blob = bytes.fromhex(blob_hex)
    return AESGCM(key).decrypt(blob[:12], blob[12:], None)


def _write_verify_blob(con: sqlite3.Connection, key: bytes) -> None:
    """Encrypt a known sentinel and store it in meta so we can verify the key later."""
    aesgcm = AESGCM(key)
    nonce = _secrets.token_bytes(12)
    blob = (nonce + aesgcm.encrypt(nonce, _VERIFY_PLAINTEXT, None)).hex()
    con.execute(
        "INSERT INTO meta (key, value) VALUES ('verify', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (blob,),
    )
    con.commit()


def _check_verify_blob(con: sqlite3.Connection, key: bytes) -> bool:
    """Return True if the key decrypts the stored sentinel correctly."""
    from cryptography.exceptions import InvalidTag
    row = con.execute("SELECT value FROM meta WHERE key='verify'").fetchone()
    if not row:
        # Fresh vault — no sentinel yet; write one and trust it.
        _write_verify_blob(con, key)
        return True
    try:
        blob = bytes.fromhex(row[0])
        nonce, ct = blob[:12], blob[12:]
        AESGCM(key).decrypt(nonce, ct, None)
        return True
    except InvalidTag:
        return False


def _migrate_plaintext_names(con: sqlite3.Connection, key: bytes) -> None:
    """Upgrade an old secrets table (plaintext `name` PK) to encrypted names.

    The stored `value_enc` was already encrypted under the same key, so it is
    carried over untouched; only the name is hashed + encrypted.
    """
    cols = [r[1] for r in con.execute("PRAGMA table_info(secrets)")]
    if "name" not in cols:
        # Already migrated or a fresh new-schema table.
        return
    old_rows = con.execute("SELECT name, value_enc FROM secrets").fetchall()
    con.execute("DROP TABLE secrets")
    _init_schema(con)
    for name, value_enc in old_rows:
        con.execute(
            "INSERT INTO secrets (name_hash, name_enc, value_enc) VALUES (?, ?, ?)",
            (_name_hash(key, name), _encrypt(key, name.encode("utf-8")), value_enc),
        )
    con.commit()


def init_vault() -> bool:
    """Derive key from KANBAI_VAULT_PASS env var. Returns True if passphrase is correct."""
    global _vault_key
    passphrase = os.environ.get("KANBAI_VAULT_PASS")
    if not passphrase:
        return False
    con = sqlite3.connect(str(VAULT_DB))
    _init_schema(con)
    salt = _get_or_create_salt(con)
    key = _derive_key(passphrase, salt)
    if not _check_verify_blob(con, key):
        con.close()
        return False
    _migrate_plaintext_names(con, key)
    con.close()
    _vault_key = key
    return True


def is_unlocked() -> bool:
    return _vault_key is not None


def store_secret(name: str, value: str) -> None:
    if not _vault_key:
        raise VaultLockedException()
    con = sqlite3.connect(str(VAULT_DB))
    _init_schema(con)
    con.execute(
        """
        INSERT INTO secrets (name_hash, name_enc, value_enc) VALUES (?, ?, ?)
        ON CONFLICT(name_hash) DO UPDATE SET
            name_enc = excluded.name_enc,
            value_enc = excluded.value_enc
        """,
        (
            _name_hash(_vault_key, name),
            _encrypt(_vault_key, name.encode("utf-8")),
            _encrypt(_vault_key, value.encode("utf-8")),
        ),
    )
    con.commit()
    con.close()


def get_secret(name: str) -> str:
    if not _vault_key:
        raise VaultLockedException()
    con = sqlite3.connect(str(VAULT_DB))
    row = con.execute(
        "SELECT value_enc FROM secrets WHERE name_hash = ?",
        (_name_hash(_vault_key, name),),
    ).fetchone()
    con.close()
    if not row:
        raise KeyError(name)
    return _decrypt(_vault_key, row[0]).decode("utf-8")


def list_secret_names() -> list[str]:
    """Return decrypted secret names. Requires an unlocked vault."""
    if not _vault_key:
        raise VaultLockedException()
    if not VAULT_DB.exists():
        return []
    con = sqlite3.connect(str(VAULT_DB))
    _init_schema(con)
    rows = con.execute("SELECT name_enc FROM secrets").fetchall()
    con.close()
    return sorted(_decrypt(_vault_key, r[0]).decode("utf-8") for r in rows)


def delete_secret(name: str) -> None:
    if not _vault_key:
        raise VaultLockedException()
    con = sqlite3.connect(str(VAULT_DB))
    con.execute(
        "DELETE FROM secrets WHERE name_hash = ?", (_name_hash(_vault_key, name),)
    )
    con.commit()
    con.close()


def rekey_vault(old_passphrase: str, new_passphrase: str) -> int:
    """
    Re-encrypt all secrets under a new passphrase.
    A new salt is generated so the new key is fully independent.
    Logs the event to rekey_log. Returns number of secrets rekeyed.
    """
    from datetime import datetime, timezone

    con = sqlite3.connect(str(VAULT_DB))
    _init_schema(con)

    # derive old key
    old_salt = _get_or_create_salt(con)
    old_key = _derive_key(old_passphrase, old_salt)

    # decrypt all secrets (name + value) with old key
    rows = con.execute("SELECT name_enc, value_enc FROM secrets").fetchall()
    plaintext: dict[str, str] = {}
    for name_enc, value_enc in rows:
        name = _decrypt(old_key, name_enc).decode("utf-8")
        plaintext[name] = _decrypt(old_key, value_enc).decode("utf-8")

    # generate new salt + key
    new_salt = _secrets.token_bytes(32)
    new_key = _derive_key(new_passphrase, new_salt)

    # re-encrypt everything under the new key (name_hash changes, so rebuild)
    con.execute("DELETE FROM secrets")
    for name, value in plaintext.items():
        con.execute(
            "INSERT INTO secrets (name_hash, name_enc, value_enc) VALUES (?, ?, ?)",
            (
                _name_hash(new_key, name),
                _encrypt(new_key, name.encode("utf-8")),
                _encrypt(new_key, value.encode("utf-8")),
            ),
        )

    # replace salt in meta
    con.execute("UPDATE meta SET value = ? WHERE key = 'salt'", (new_salt.hex(),))

    # write new verification sentinel under new key
    _write_verify_blob(con, new_key)

    # write audit log entry
    con.execute(
        "INSERT INTO rekey_log (rekeyed_at, secret_count) VALUES (?, ?)",
        (datetime.now(timezone.utc).isoformat(), len(plaintext)),
    )
    con.commit()
    con.close()

    # update in-memory key to the new one
    global _vault_key
    _vault_key = new_key
    return len(plaintext)


def get_rekey_log() -> list[dict]:
    """Return all rekey audit entries, newest first."""
    if not VAULT_DB.exists():
        return []
    con = sqlite3.connect(str(VAULT_DB))
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT id, rekeyed_at, secret_count FROM rekey_log ORDER BY id DESC"
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]
