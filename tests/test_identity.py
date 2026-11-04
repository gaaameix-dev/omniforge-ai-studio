"""Identity, RBAC, vault and rate limiting."""
from __future__ import annotations

import pytest
from conftest import make_settings

from omniforge.core import auth
from omniforge.core.auth import (
    AuthError,
    action_for,
    allows,
    hash_password,
    is_expired,
    login,
    register_user,
    resolve_identity,
    token_hash,
    verify_password,
)
from omniforge.core.ratelimit import RateLimiter
from omniforge.core.store import RunStore
from omniforge.core.vault import Vault, VaultError


@pytest.fixture
def store(tmp_path):
    s = RunStore(tmp_path / "db.sqlite")
    yield s
    s.close()


# ------------------------------------------------------------------ passwords
def test_password_hash_roundtrip_and_salting():
    first = hash_password("correct horse battery")
    second = hash_password("correct horse battery")
    assert first != second                      # different salts
    assert verify_password("correct horse battery", first)
    assert not verify_password("wrong password", first)


def test_password_minimum_length():
    with pytest.raises(AuthError):
        hash_password("short")


def test_verify_rejects_garbage():
    assert not verify_password("x", "not-a-hash")
    assert not verify_password("x", "md5$1$aa$bb")


# ----------------------------------------------------------------------- roles
def test_role_ladder():
    assert allows("viewer", "read") and not allows("viewer", "run")
    assert allows("member", "run") and not allows("member", "manage")
    assert allows("admin", "manage") and not allows("admin", "own")
    assert allows("owner", "own")
    assert not allows("nobody", "read")


def test_action_mapping():
    assert action_for("GET", "/api/runs") == "read"
    assert action_for("POST", "/api/runs") == "run"
    assert action_for("POST", "/api/keys") == "manage"
    assert action_for("POST", "/api/control/pause") == "manage"
    assert action_for("DELETE", "/api/runs/x") == "run"
    assert action_for("GET", "/api/integrations") == "read"
    assert action_for("POST", "/api/integrations/github/verify") == "manage"


# ------------------------------------------------------------------ accounts
def test_first_user_is_owner_then_members(store):
    first = register_user(store, "a@example.com", "hunter2hunter2", "A")
    second = register_user(store, "b@example.com", "hunter2hunter2", "B")
    assert first["role"] == "owner" and second["role"] == "member"
    assert store.get_user_by_email("A@Example.com")["id"] == first["id"]


def test_duplicate_and_invalid_email(store):
    register_user(store, "a@example.com", "hunter2hunter2")
    with pytest.raises(AuthError) as dup:
        register_user(store, "a@example.com", "hunter2hunter2")
    assert dup.value.status == 409
    with pytest.raises(AuthError):
        register_user(store, "not-an-email", "hunter2hunter2")


def test_login_issues_session_and_audits(store, tmp_path):
    settings = make_settings(tmp_path)
    register_user(store, "a@example.com", "hunter2hunter2")
    token, user = login(store, settings, "a@example.com", "hunter2hunter2")
    assert token.startswith(auth.SESSION_PREFIX)
    assert store.get_session(token_hash(token))["user_id"] == user["id"]
    assert any(e["action"] == "auth.login" for e in store.list_audit())


def test_login_failures_are_audited(store, tmp_path):
    settings = make_settings(tmp_path)
    register_user(store, "a@example.com", "hunter2hunter2")
    with pytest.raises(AuthError):
        login(store, settings, "a@example.com", "wrongpassword")
    assert any(e["action"] == "auth.failed" for e in store.list_audit())


def test_disabled_account_cannot_log_in(store, tmp_path):
    settings = make_settings(tmp_path)
    user = register_user(store, "a@example.com", "hunter2hunter2")
    store.set_user_disabled(user["id"], True)
    with pytest.raises(AuthError) as exc:
        login(store, settings, "a@example.com", "hunter2hunter2")
    assert exc.value.status == 403


def test_session_expiry_and_purge(store, tmp_path):
    settings = make_settings(tmp_path, session_ttl_hours=1)
    register_user(store, "a@example.com", "hunter2hunter2")
    token, _ = login(store, settings, "a@example.com", "hunter2hunter2")
    session = store.get_session(token_hash(token))
    assert not is_expired(session["expires_at"])
    assert is_expired("2000-01-01T00:00:00+00:00")
    store._conn.execute("UPDATE sessions SET expires_at='2000-01-01T00:00:00+00:00'")
    store._conn.commit()
    assert store.purge_expired_sessions(auth.utcnow()) == 1


def test_resolve_identity_paths(store, tmp_path):
    settings = make_settings(tmp_path, api_key="legacy-key")
    user = register_user(store, "a@example.com", "hunter2hunter2")
    token, _ = login(store, settings, "a@example.com", "hunter2hunter2")

    assert resolve_identity(settings, store, None, None) is None
    assert resolve_identity(settings, store, f"Bearer {token}", None).kind == "session"
    assert resolve_identity(settings, store, None, token).kind == "session"
    legacy = resolve_identity(settings, store, "Bearer legacy-key", None)
    assert legacy.kind == "legacy" and legacy.role == "owner"

    api_token, _ = auth.issue_api_key(store, user["id"], "test")
    assert resolve_identity(settings, store, f"Bearer {api_token}", None).kind == "api_key"

    store.revoke_api_key(store.list_api_keys(user["id"])[0]["id"])
    assert resolve_identity(settings, store, f"Bearer {api_token}", None) is None
    assert resolve_identity(settings, store, "Bearer nonsense", None) is None


def test_delete_user_removes_sessions_and_keys(store, tmp_path):
    settings = make_settings(tmp_path)
    user = register_user(store, "a@example.com", "hunter2hunter2")
    token, _ = login(store, settings, "a@example.com", "hunter2hunter2")
    auth.issue_api_key(store, user["id"], "k")
    assert store.delete_user(user["id"]) == 1
    assert store.get_session(token_hash(token)) is None
    assert store.list_api_keys(user["id"]) == []
    assert store.get_user(user["id"]) is None


# ---------------------------------------------------------------- workspaces
def test_workspace_membership(store):
    owner = register_user(store, "a@example.com", "hunter2hunter2")
    member = register_user(store, "b@example.com", "hunter2hunter2")
    workspace = store.create_workspace("Studio", owner["id"], auth.utcnow())
    assert store.member_role(workspace["id"], owner["id"]) == "owner"
    store.add_member(workspace["id"], member["id"], "viewer", auth.utcnow())
    assert store.member_role(workspace["id"], member["id"]) == "viewer"
    assert len(store.list_members(workspace["id"])) == 2
    assert len(store.list_workspaces(member["id"])) == 1
    assert store.remove_member(workspace["id"], member["id"]) == 1
    assert store.member_role(workspace["id"], member["id"]) is None


# --------------------------------------------------------------------- vault
def test_vault_encrypts_at_rest(tmp_path, store):
    vault = Vault(tmp_path)
    assert vault.available
    vault.put(store, "OPENAI_KEY", "sk-super-secret", None, auth.utcnow())
    record = store.get_secret("OPENAI_KEY")
    assert "sk-super-secret" not in record["ciphertext"]
    assert vault.get(store, "OPENAI_KEY") == "sk-super-secret"
    assert [s["name"] for s in vault.names(store)] == ["OPENAI_KEY"]
    assert vault.delete(store, "OPENAI_KEY") == 1


def test_vault_with_wrong_master_key_cannot_decrypt(tmp_path, store):
    Vault(tmp_path, secret_key="key-one").put(store, "K", "value", None, auth.utcnow())
    other = Vault(tmp_path, secret_key="key-two")
    with pytest.raises(VaultError):
        other.get(store, "K")


def test_vault_key_file_permissions(tmp_path):
    Vault(tmp_path)
    key_file = tmp_path / ".vault.key"
    assert key_file.exists()
    assert oct(key_file.stat().st_mode)[-3:] in {"600", "400"}


def test_vault_requires_a_name(tmp_path, store):
    vault = Vault(tmp_path)
    with pytest.raises(VaultError):
        vault.put(store, "  ", "x", None, auth.utcnow())


# --------------------------------------------------------------- rate limits
def test_rate_limiter_bucket():
    limiter = RateLimiter(per_minute=60, burst=2)
    assert limiter.check("a", now=0.0)[0] is True
    assert limiter.check("a", now=0.0)[0] is True
    allowed, retry = limiter.check("a", now=0.0)
    assert allowed is False and retry > 0
    assert limiter.check("a", now=2.0)[0] is True        # refilled
    assert limiter.check("b", now=2.0)[0] is True        # separate bucket


def test_rate_limiter_disabled_and_reset():
    limiter = RateLimiter(per_minute=0)
    assert limiter.check("a")[0] is True
    other = RateLimiter(per_minute=1, burst=1)
    other.check("a")
    other.reset("a")
    assert other.check("a")[0] is True
    assert other.size() == 1
