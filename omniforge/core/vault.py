"""Encrypted secret storage.

Values are encrypted with Fernet (AES-128-CBC + HMAC) before they reach the
database, so a leaked SQLite file is not a leaked credential. The master key comes
from OMNIFORGE_SECRET_KEY, or from a 0600 key file in the data directory that is
created on first use.

Secrets are write-only through the API: you can list names and set values, never
read them back. Tools read them server-side via `vault.get(name)`.
"""
from __future__ import annotations

import base64
import hashlib
import os
import stat
from pathlib import Path

try:  # optional dependency: the vault degrades honestly when it is missing
    from cryptography.fernet import Fernet, InvalidToken

    CRYPTO_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only without cryptography
    CRYPTO_AVAILABLE = False

    class InvalidToken(Exception):
        pass


class VaultError(RuntimeError):
    pass


class Vault:
    def __init__(self, data_dir: Path | str, secret_key: str = "") -> None:
        self.data_dir = Path(data_dir)
        self.secret_key = secret_key
        self._fernet = None
        if CRYPTO_AVAILABLE:
            self._fernet = Fernet(self._derive_key(secret_key))

    # ------------------------------------------------------------------- keys
    def _derive_key(self, secret_key: str) -> bytes:
        if secret_key:
            digest = hashlib.sha256(("omniforge-vault:" + secret_key).encode()).digest()
            return base64.urlsafe_b64encode(digest)
        key_file = self.data_dir / ".vault.key"
        if key_file.exists():
            raw = key_file.read_bytes().strip()
            try:
                Fernet(raw)  # validate
                return raw
            except Exception as exc:  # noqa: BLE001
                raise VaultError(f"vault key file is corrupt: {exc}") from exc
        self.data_dir.mkdir(parents=True, exist_ok=True)
        raw = Fernet.generate_key()
        key_file.write_bytes(raw)
        try:
            os.chmod(key_file, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:  # pragma: no cover - non-POSIX
            pass
        return raw

    @property
    def available(self) -> bool:
        return self._fernet is not None

    def status(self) -> dict:
        return {
            "available": self.available,
            "backend": "fernet" if self.available else "unavailable",
            "source": "env" if self.secret_key else "keyfile",
            "requires": "" if self.available else "pip install cryptography",
        }

    # ------------------------------------------------------------------ values
    def encrypt(self, value: str) -> str:
        if self._fernet is None:
            raise VaultError("cryptography is not installed: the vault is unavailable")
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, token: str) -> str:
        if self._fernet is None:
            raise VaultError("cryptography is not installed: the vault is unavailable")
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken as exc:
            raise VaultError("cannot decrypt this secret (wrong master key?)") from exc

    # ------------------------------------------------------------------- store
    def put(self, store, name: str, value: str, owner_id: str | None, ts: str) -> None:
        if not name or not name.strip():
            raise VaultError("a secret needs a name")
        store.put_secret(name.strip(), self.encrypt(value), owner_id, ts)

    def get(self, store, name: str) -> str | None:
        record = store.get_secret(name)
        return self.decrypt(record["ciphertext"]) if record else None

    def names(self, store) -> list[dict]:
        return store.list_secrets()

    def delete(self, store, name: str) -> int:
        return store.delete_secret(name)

    def rotate(self, store, new_secret_key: str, *, persist_keyfile: bool = False) -> dict:
        """Re-encrypt every stored secret under a new master key.

        Reads with the current key, writes with the new one, and reports how many
        values moved. Nothing is deleted until every value re-encrypts cleanly.
        """
        if not self.available:
            raise VaultError("cryptography is not installed: cannot rotate")
        if not new_secret_key:
            raise VaultError("rotation needs a new master key")
        new_fernet = Fernet(self._derive_key(new_secret_key))
        moved, failed = 0, []
        for record in store.list_secrets():
            try:
                plaintext = self.decrypt(store.get_secret(record["name"])["ciphertext"])
                store.put_secret(
                    record["name"], new_fernet.encrypt(plaintext.encode()).decode(),
                    record["owner_id"], record["updated_at"],
                )
                moved += 1
            except Exception as exc:  # noqa: BLE001 - report, never half-migrate silently
                failed.append({"name": record["name"], "error": str(exc)})
        if failed:
            raise VaultError(f"rotation failed for {len(failed)} secret(s): {failed[:3]}")
        self.secret_key = new_secret_key
        self._fernet = new_fernet
        if persist_keyfile:
            key_file = self.data_dir / ".vault.key"
            key_file.write_bytes(self._derive_key(new_secret_key))
            try:
                os.chmod(key_file, stat.S_IRUSR | stat.S_IWUSR)
            except OSError:  # pragma: no cover
                pass
        return {"rotated": moved, "failed": failed, "source": "env" if new_secret_key else "keyfile"}
