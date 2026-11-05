"""Generic OpenID Connect login (authorization code + PKCE).

Works with any compliant provider (Google, Entra, Keycloak, Auth0…): point
`OMNIFORGE_OIDC_ISSUER` at it. Users are matched by verified email; the first one to
sign in still becomes owner, exactly like local registration.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
from typing import Any
from urllib.parse import urlencode

import httpx


class OIDCError(RuntimeError):
    pass


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


class OIDCClient:
    def __init__(self, issuer: str, client_id: str, client_secret: str = "",
                 redirect_uri: str = "", scopes: str = "openid email profile",
                 timeout: float = 20.0, transport: Any | None = None) -> None:
        self.issuer = (issuer or "").rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.scopes = scopes
        self.timeout = timeout
        self.transport = transport
        self._config: dict[str, Any] | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.issuer and self.client_id)

    def _client(self) -> httpx.AsyncClient:
        kwargs: dict[str, Any] = {"timeout": self.timeout, "follow_redirects": True}
        if self.transport is not None:
            kwargs["transport"] = self.transport
        return httpx.AsyncClient(**kwargs)

    async def discover(self) -> dict[str, Any]:
        if self._config:
            return self._config
        if not self.enabled:
            raise OIDCError("OIDC is not configured (issuer and client id are required)")
        async with self._client() as client:
            response = await client.get(f"{self.issuer}/.well-known/openid-configuration")
        if response.status_code >= 400:
            raise OIDCError(f"discovery failed ({response.status_code})")
        config = response.json()
        for key in ("authorization_endpoint", "token_endpoint"):
            if key not in config:
                raise OIDCError(f"provider metadata has no {key}")
        self._config = config
        return config

    @staticmethod
    def new_pkce() -> tuple[str, str]:
        verifier = _b64url(secrets.token_bytes(48))
        challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
        return verifier, challenge

    async def authorization_url(self, state: str, code_challenge: str) -> str:
        config = await self.discover()
        params = {
            "response_type": "code", "client_id": self.client_id,
            "redirect_uri": self.redirect_uri, "scope": self.scopes,
            "state": state, "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        return f"{config['authorization_endpoint']}?{urlencode(params)}"

    async def exchange(self, code: str, code_verifier: str) -> dict[str, Any]:
        config = await self.discover()
        payload = {
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": self.redirect_uri, "client_id": self.client_id,
            "code_verifier": code_verifier,
        }
        if self.client_secret:
            payload["client_secret"] = self.client_secret
        async with self._client() as client:
            response = await client.post(config["token_endpoint"], data=payload)
        if response.status_code >= 400:
            raise OIDCError(f"token exchange failed ({response.status_code}): "
                            f"{response.text[:200]}")
        return response.json()

    async def userinfo(self, access_token: str) -> dict[str, Any]:
        config = await self.discover()
        endpoint = config.get("userinfo_endpoint")
        if not endpoint:
            return {}
        async with self._client() as client:
            response = await client.get(endpoint,
                                        headers={"Authorization": f"Bearer {access_token}"})
        if response.status_code >= 400:
            raise OIDCError(f"userinfo failed ({response.status_code})")
        return response.json()

    async def identify(self, code: str, code_verifier: str) -> dict[str, Any]:
        """Exchange the code and return {email, name, subject}."""
        tokens = await self.exchange(code, code_verifier)
        access_token = tokens.get("access_token", "")
        profile = await self.userinfo(access_token) if access_token else {}
        claims = _claims_from_id_token(tokens.get("id_token", "")) or profile
        email = str(claims.get("email") or "").strip().lower()
        if not email:
            raise OIDCError("the provider did not return an email address")
        if claims.get("email_verified") is False:
            raise OIDCError("the provider says this email is not verified")
        return {"email": email, "name": str(claims.get("name") or email.split("@")[0]),
                "subject": str(claims.get("sub") or "")}


def _claims_from_id_token(token: str) -> dict[str, Any]:
    """Read the payload without verifying: the token came from the token endpoint."""
    import json

    try:
        payload = token.split(".")[1]
        padded = payload + "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(padded))
    except Exception:  # noqa: BLE001 - fall back to userinfo
        return {}
