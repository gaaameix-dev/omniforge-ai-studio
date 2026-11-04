"""Identity, sessions, API keys and role-based access control.

Passwords: PBKDF2-HMAC-SHA256 with a per-user salt and a stored iteration count,
so the cost can be raised later without invalidating old hashes.
Tokens: random, returned once, stored only as a SHA-256 hash.
Roles: viewer < member < admin < owner, checked against an action ladder.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

PBKDF2_ITERATIONS = 210_000
SESSION_PREFIX = "of_"
APIKEY_PREFIX = "ofk_"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")

ROLE_RANK = {"viewer": 1, "member": 2, "admin": 3, "owner": 4}
ACTION_RANK = {"read": 1, "run": 2, "manage": 3, "own": 4}


class AuthError(Exception):
    def __init__(self, message: str, status: int = 401) -> None:
        super().__init__(message)
        self.status = status


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------- passwords
def hash_password(password: str, *, iterations: int = PBKDF2_ITERATIONS) -> str:
    if not password or len(password) < 8:
        raise AuthError("password must be at least 8 characters", status=400)
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt_hex, digest_hex = stored.split("$")
    except (ValueError, AttributeError):
        return False
    if scheme != "pbkdf2_sha256":
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations))
    return hmac.compare_digest(digest.hex(), digest_hex)


# ------------------------------------------------------------------------ tokens
def new_session_token() -> str:
    return SESSION_PREFIX + secrets.token_urlsafe(32)


def new_api_key() -> str:
    return APIKEY_PREFIX + secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def key_prefix(token: str) -> str:
    return token[:12]


def session_expiry(hours: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat(timespec="seconds")


def is_expired(expires_at: str, now: str | None = None) -> bool:
    try:
        deadline = datetime.fromisoformat(expires_at)
    except ValueError:
        return True
    current = datetime.fromisoformat(now) if now else datetime.now(timezone.utc)
    return current >= deadline


# ------------------------------------------------------------------------ roles
def allows(role: str, action: str) -> bool:
    return ROLE_RANK.get(role, 0) >= ACTION_RANK.get(action, 99)


@dataclass
class Identity:
    user_id: str | None
    email: str | None
    role: str
    kind: str                     # session | api_key | legacy | anonymous
    workspace_id: str | None = None

    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "email": self.email,
            "role": self.role,
            "kind": self.kind,
            "workspace_id": self.workspace_id,
        }


ANONYMOUS = Identity(user_id=None, email=None, role="viewer", kind="anonymous")


def action_for(method: str, path: str) -> str:
    """Map an HTTP call onto the role ladder."""
    method = method.upper()
    if path.startswith("/api/auth"):
        return "read"
    if path.startswith(("/api/keys", "/api/workspaces", "/api/audit", "/api/secrets", "/api/users")):
        return "manage" if method != "GET" else "read"
    if path.startswith("/api/control"):
        return "manage"
    if path.startswith("/api/integrations"):
        return "manage" if method == "POST" else "read"
    if path.startswith("/api/runs"):
        return "read" if method == "GET" else "run"
    if path.startswith("/api/projects"):
        return "read" if method == "GET" else "run"
    if method == "GET":
        return "read"
    return "run"


# -------------------------------------------------------------------- resolving
def resolve_identity(settings, store, authorization: str | None, query_token: str | None) -> Identity | None:
    """Turn a bearer token into an Identity, or None when nothing valid was given."""
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if not token:
        token = (query_token or "").strip()
    if not token:
        return None

    if settings.api_key and hmac.compare_digest(settings.api_key, token):
        return Identity(user_id=None, email=None, role="owner", kind="legacy")

    hashed = token_hash(token)
    if token.startswith(SESSION_PREFIX):
        session = store.get_session(hashed)
        if not session or is_expired(session["expires_at"]):
            return None
        user = store.get_user(session["user_id"])
        if not user or user["disabled"]:
            return None
        store.touch_session(session["id"], utcnow())
        return Identity(user_id=user["id"], email=user["email"], role=user["role"], kind="session")

    if token.startswith(APIKEY_PREFIX):
        key = store.find_api_key(hashed)
        if not key:
            return None
        user = store.get_user(key["user_id"])
        if not user or user["disabled"]:
            return None
        store.touch_api_key(key["id"], utcnow())
        return Identity(user_id=user["id"], email=user["email"], role=user["role"], kind="api_key")

    return None


# ------------------------------------------------------------------ registration
def register_user(store, email: str, password: str, name: str = "") -> dict:
    email = (email or "").strip().lower()
    if not EMAIL_RE.match(email):
        raise AuthError("a valid email address is required", status=400)
    if store.get_user_by_email(email):
        raise AuthError("an account with that email already exists", status=409)
    role = "owner" if store.count_users() == 0 else "member"
    user = store.create_user(email, hash_password(password), name or email.split("@")[0], role, utcnow())
    store.add_audit(email, "user.registered", user["id"], f"role={role}", None, utcnow())
    return user


def authenticate(store, email: str, password: str) -> dict:
    """Verify credentials only. MFA (if enrolled) is checked by the caller."""
    user = store.get_user_by_email((email or "").strip().lower())
    if not user or not verify_password(password, user["password_hash"]):
        store.add_audit(email, "auth.failed", None, "bad credentials", None, utcnow())
        raise AuthError("invalid email or password", status=401)
    if user["disabled"]:
        raise AuthError("this account is disabled", status=403)
    return user


def issue_session(store, settings, user: dict, note: str = "session issued") -> str:
    token = new_session_token()
    store.create_session(user["id"], token_hash(token), utcnow(),
                         session_expiry(settings.session_ttl_hours))
    store.add_audit(user["email"], "auth.login", user["id"], note, None, utcnow())
    return token


def login(store, settings, email: str, password: str) -> tuple[str, dict]:
    user = authenticate(store, email, password)
    return issue_session(store, settings, user), user


RESET_PREFIX = "ofr_"


def issue_reset_token(store, settings, email: str) -> tuple[str, dict] | tuple[None, None]:
    """Create a single-use reset token. Returns (None, None) for unknown emails so
    callers cannot be used to enumerate accounts."""
    user = store.get_user_by_email((email or "").strip().lower())
    if user is None or user["disabled"]:
        return None, None
    token = RESET_PREFIX + secrets.token_urlsafe(32)
    expires = (
        datetime.now(timezone.utc) + timedelta(minutes=settings.reset_ttl_minutes)
    ).isoformat(timespec="seconds")
    store.create_reset_token(user["id"], token_hash(token), utcnow(), expires)
    return token, user


def reset_password(store, token: str, new_password: str) -> dict:
    record = store.get_reset_token(token_hash(token or ""))
    if record is None or record["used"] or is_expired(record["expires_at"]):
        raise AuthError("this reset link is invalid or has expired", status=400)
    user = store.get_user(record["user_id"])
    if user is None or user["disabled"]:
        raise AuthError("this account is unavailable", status=400)
    store.set_user_password(user["id"], hash_password(new_password))
    store.consume_reset_token(record["token_hash"])
    store.delete_sessions_for_user(user["id"])
    store.add_audit(user["email"], "auth.password_reset", user["id"], "via reset token", None, utcnow())
    return user


def second_factor_required(store, user: dict) -> bool:
    record = store.get_mfa(user["id"])
    return bool(record and record["enabled"])


def check_second_factor(store, user: dict, code: str) -> bool:
    """Accept a TOTP code or a single-use backup code."""
    from . import mfa

    record = store.get_mfa(user["id"])
    if not record or not record["enabled"]:
        return True
    if not code:
        return False
    if mfa.verify(record["secret"], code):
        return True
    if store.use_backup_code(user["id"], mfa.hash_code(code)):
        store.add_audit(user["email"], "auth.backup_code_used", user["id"], None, None, utcnow())
        return True
    return False


def logout(store, token: str) -> bool:
    return store.delete_session(token_hash(token)) > 0


def issue_api_key(store, user_id: str, name: str) -> tuple[str, dict]:
    token = new_api_key()
    key_id = store.create_api_key(user_id, name or "api key", token_hash(token), key_prefix(token), utcnow())
    store.add_audit(user_id, "api_key.created", key_id, name, None, utcnow())
    return token, {"id": key_id, "name": name, "prefix": key_prefix(token)}
