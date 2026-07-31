"""Tier-1 unit tests for the API auth slice — fully mocked, no network, no Google.

Covers the DU-00 "SSO works" contract: /healthz is always up, /me is 401 until a
session exists, and the OAuth callback maps a (mocked) token's userinfo into the
signed session so /me then returns the user. The 503-when-unconfigured guard keeps
the "boots without credentials" invariant honest.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from api.app import create_app
from api.config import Settings

_SESSION_SECRET = "test-session-secret-not-a-real-key"  # noqa: S105 (test fixture)


def _settings(*, configured: bool) -> Settings:
    return Settings(
        google_client_id="test-client-id.apps.googleusercontent.com" if configured else None,
        google_client_secret="test-client-secret" if configured else None,
        session_secret=_SESSION_SECRET,
        session_secret_is_ephemeral=False,
        oauth_redirect_uri="http://testserver/auth/callback",
        post_login_redirect="/",
        session_https_only=False,
    )


def test_healthz_is_ok_without_credentials() -> None:
    client = TestClient(create_app(_settings(configured=False)))
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_me_is_401_when_unauthenticated() -> None:
    client = TestClient(create_app(_settings(configured=False)))
    resp = client.get("/me")
    assert resp.status_code == 401


def test_login_is_503_when_sso_unconfigured() -> None:
    client = TestClient(create_app(_settings(configured=False)))
    resp = client.get("/auth/login", follow_redirects=False)
    assert resp.status_code == 503


def test_callback_maps_mocked_token_to_session() -> None:
    app = create_app(_settings(configured=True))
    userinfo = {
        "sub": "google-oidc-sub-123",
        "email": "traveler@example.com",
        "name": "Test Traveler",
        "picture": "https://example.com/avatar.png",
    }
    # Replace the network-touching token exchange with a mock: no real Google call,
    # no JWKS fetch. The route still exercises userinfo → session mapping.
    app.state.oauth.google.authorize_access_token = AsyncMock(return_value={"userinfo": userinfo})

    client = TestClient(app)
    callback = client.get("/auth/callback?state=x&code=y", follow_redirects=False)
    assert callback.status_code == 303
    assert callback.headers["location"] == "/"

    # The session cookie set by the callback now authenticates /me.
    me = client.get("/me")
    assert me.status_code == 200
    body = me.json()
    assert body["sub"] == "google-oidc-sub-123"
    assert body["email"] == "traveler@example.com"


def test_logout_clears_the_session() -> None:
    app = create_app(_settings(configured=True))
    userinfo = {"sub": "google-oidc-sub-123", "email": "traveler@example.com"}
    app.state.oauth.google.authorize_access_token = AsyncMock(return_value={"userinfo": userinfo})

    client = TestClient(app)
    client.get("/auth/callback?state=x&code=y", follow_redirects=False)
    assert client.get("/me").status_code == 200

    client.post("/auth/logout", follow_redirects=False)
    assert client.get("/me").status_code == 401
