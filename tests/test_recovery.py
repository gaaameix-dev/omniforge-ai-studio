"""Password reset, MFA and vault rotation."""
from __future__ import annotations

import pytest
from conftest import make_settings
from fastapi.testclient import TestClient

from omniforge.api.app import create_app
from omniforge.core import auth, mfa
from omniforge.core.mailer import ConsoleMailer, NullMailer, build_mailer
from omniforge.core.store import RunStore
from omniforge.core.vault import Vault, VaultError

PASSWORD = "hunter2hunter2"


# ------------------------------------------------------------------- mailer
def test_console_mailer_keeps_an_outbox():
    import asyncio

    mailer = ConsoleMailer()
    assert asyncio.run(mailer.send("a@b.co", "subject", "body")) is True
    assert mailer.outbox[0].subject == "subject"
    assert mailer.status()["backend"] == "console"


def test_mail_backend_selection(tmp_path):
    assert build_mailer(make_settings(tmp_path)).backend == "console"
    assert build_mailer(make_settings(tmp_path, mail_backend="off")).backend == "off"
    # smtp without a host degrades to console instead of failing silently
    assert build_mailer(make_settings(tmp_path, mail_backend="smtp", smtp_host="")).backend == "console"
    smtp = build_mailer(make_settings(tmp_path, mail_backend="smtp", smtp_host="smtp.example.com"))
    assert smtp.backend == "smtp"


def test_null_mailer_reports_failure():
    import asyncio

    assert asyncio.run(NullMailer().send("a@b.co", "s", "b")) is False


# ------------------------------------------------------------ password reset
@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(make_settings(tmp_path, auth_enabled=True))) as c:
        yield c


def _account(client, email="owner@example.com"):
    body = client.post("/api/auth/register",
                       json={"email": email, "password": PASSWORD, "name": "Owner"}).json()
    return body["token"], {"Authorization": f"Bearer {body['token']}"}


def _reset_token(client) -> str:
    mailer = client.app.state.mailer
    client.post("/api/auth/forgot", json={"email": "owner@example.com"})
    assert mailer.outbox, "a reset mail should have been queued"
    body = mailer.outbox[-1].body
    # the link is followed by a footer line, so take the token on its own
    return body.split("#reset=")[-1].split()[0].strip()


def test_forgot_does_not_leak_account_existence(client):
    _account(client)
    known = client.post("/api/auth/forgot", json={"email": "owner@example.com"}).json()
    unknown = client.post("/api/auth/forgot", json={"email": "ghost@example.com"}).json()
    assert known == unknown
    assert known["sent"] is True and known["delivery"] == "console"


def test_reset_flow_changes_the_password(client):
    _account(client)
    token = _reset_token(client)
    assert client.post("/api/auth/reset", json={"token": token, "new_password": "brandnewpass1"}
                       ).json()["reset"] is True
    assert client.post("/api/auth/login", json={"email": "owner@example.com",
                                                "password": PASSWORD}).status_code == 401
    assert client.post("/api/auth/login", json={"email": "owner@example.com",
                                                "password": "brandnewpass1"}).status_code == 200


def test_reset_token_is_single_use_and_expires(client):
    _account(client)
    token = _reset_token(client)
    assert client.post("/api/auth/reset", json={"token": token, "new_password": "brandnewpass1"}
                       ).status_code == 200
    assert client.post("/api/auth/reset", json={"token": token, "new_password": "anotherpass1"}
                       ).status_code == 400
    assert client.post("/api/auth/reset", json={"token": "ofr_bogus", "new_password": "anotherpass1"}
                       ).status_code == 400


def test_reset_invalidates_existing_sessions(client):
    _, headers = _account(client)
    token = _reset_token(client)
    client.post("/api/auth/reset", json={"token": token, "new_password": "brandnewpass1"})
    assert client.get("/api/auth/me", headers=headers).status_code == 401


def test_reset_rejects_weak_password(client):
    _account(client)
    token = _reset_token(client)
    assert client.post("/api/auth/reset", json={"token": token, "new_password": "short"}
                       ).status_code == 422


# ---------------------------------------------------------------------- MFA
def test_totp_codes_verify_with_drift():
    secret = mfa.new_secret()
    code = mfa.code_at(secret)
    assert mfa.verify(secret, code)
    assert mfa.verify(secret, code, timestamp=None, drift=1)
    assert not mfa.verify(secret, "123456" if code != "123456" else "654321")
    assert not mfa.verify(secret, "not-a-code")
    assert not mfa.verify("", code)


def test_backup_codes_are_single_use(tmp_path):
    store = RunStore(tmp_path / "db.sqlite")
    user = auth.register_user(store, "a@example.com", PASSWORD)
    store.set_mfa_secret(user["id"], mfa.new_secret(), auth.utcnow(), enabled=True)
    codes = mfa.new_backup_codes(2)
    store.add_backup_codes(user["id"], [mfa.hash_code(c) for c in codes])
    assert auth.check_second_factor(store, user, codes[0]) is True
    assert auth.check_second_factor(store, user, codes[0]) is False
    assert auth.check_second_factor(store, user, codes[1]) is True
    store.close()


def test_mfa_enrolment_and_login(client):
    _, headers = _account(client)
    setup = client.post("/api/auth/mfa/setup", headers=headers).json()
    assert setup["otpauth_url"].startswith("otpauth://totp/")
    secret = setup["secret"]

    assert client.post("/api/auth/mfa/enable", json={"code": "000000"},
                       headers=headers).status_code == 400
    enabled = client.post("/api/auth/mfa/enable", json={"code": mfa.code_at(secret)},
                          headers=headers).json()
    assert enabled["enabled"] is True and len(enabled["backup_codes"]) == 8

    # password alone is no longer enough
    no_code = client.post("/api/auth/login", json={"email": "owner@example.com", "password": PASSWORD})
    assert no_code.status_code == 401
    assert no_code.headers.get("x-mfa-required") == "1"
    assert no_code.json()["detail"] == "mfa_required"

    bad = client.post("/api/auth/login", json={"email": "owner@example.com", "password": PASSWORD,
                                               "code": "999999"})
    assert bad.status_code == 401

    good = client.post("/api/auth/login", json={"email": "owner@example.com", "password": PASSWORD,
                                                "code": mfa.code_at(secret)})
    assert good.status_code == 200 and good.json()["mfa"] is True

    # a backup code also works, once
    code = enabled["backup_codes"][0]
    assert client.post("/api/auth/login", json={"email": "owner@example.com", "password": PASSWORD,
                                                "code": code}).status_code == 200
    assert client.post("/api/auth/login", json={"email": "owner@example.com", "password": PASSWORD,
                                                "code": code}).status_code == 401

    status = client.get("/api/auth/mfa", headers=headers).json()
    assert status["enrolled"] is True and status["backup_codes_left"] == 7


def test_mfa_disable_requires_password(client):
    _, headers = _account(client)
    secret = client.post("/api/auth/mfa/setup", headers=headers).json()["secret"]
    client.post("/api/auth/mfa/enable", json={"code": mfa.code_at(secret)}, headers=headers)
    assert client.post("/api/auth/mfa/disable", json={"current_password": "wrongwrong1",
                                                      "new_password": "irrelevant12"},
                       headers=headers).status_code == 403
    assert client.post("/api/auth/mfa/disable", json={"current_password": PASSWORD,
                                                      "new_password": "irrelevant12"},
                       headers=headers).json()["enabled"] is False
    assert client.post("/api/auth/login", json={"email": "owner@example.com",
                                                "password": PASSWORD}).status_code == 200


def test_mfa_backup_codes_rotation_needs_a_valid_code(client):
    _, headers = _account(client)
    secret = client.post("/api/auth/mfa/setup", headers=headers).json()["secret"]
    client.post("/api/auth/mfa/enable", json={"code": mfa.code_at(secret)}, headers=headers)
    assert client.post("/api/auth/mfa/backup-codes", json={"code": "000000"},
                       headers=headers).status_code == 403
    rotated = client.post("/api/auth/mfa/backup-codes", json={"code": mfa.code_at(secret)},
                          headers=headers).json()
    assert len(rotated["backup_codes"]) == 8


# ------------------------------------------------------------ vault rotation
def test_vault_rotation_reencrypts_every_secret(tmp_path):
    store = RunStore(tmp_path / "db.sqlite")
    vault = Vault(tmp_path, secret_key="old-key")
    vault.put(store, "A", "alpha", None, auth.utcnow())
    vault.put(store, "B", "beta", None, auth.utcnow())
    report = vault.rotate(store, "new-key")
    assert report["rotated"] == 2 and not report["failed"]
    assert vault.get(store, "A") == "alpha" and vault.get(store, "B") == "beta"
    # the old key can no longer read the values
    stale = Vault(tmp_path, secret_key="old-key")
    with pytest.raises(VaultError):
        stale.get(store, "A")
    store.close()


def test_vault_rotation_needs_a_key(tmp_path):
    vault = Vault(tmp_path)
    store = RunStore(tmp_path / "db.sqlite")
    with pytest.raises(VaultError):
        vault.rotate(store, "")
    store.close()
