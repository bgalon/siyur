"""Runtime configuration for the Siyur API — env-only, secrets never on disk.

Every value comes from an environment variable (Article V: secrets and ``.env*``
are never read or written by the app; real Google OAuth credentials live in the
Google Cloud console / Secret Manager and are injected as env vars at deploy).

The app MUST boot and serve ``/healthz`` with *no* credentials present. Auth is
only wired when both Google client id and secret are set (:attr:`Settings.is_auth_configured`);
until then the login/callback routes answer ``503``.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass

# Env var names Ben provisions to enable real Google login. Only NAMES live in the
# repo (also in api/.env.example); the VALUES are set in the Cloud console.
ENV_GOOGLE_CLIENT_ID = "SIYUR_GOOGLE_CLIENT_ID"
ENV_GOOGLE_CLIENT_SECRET = "SIYUR_GOOGLE_CLIENT_SECRET"  # noqa: S105 (var NAME, not a secret)
ENV_SESSION_SECRET = "SIYUR_SESSION_SECRET"  # noqa: S105 (var NAME, not a secret)
ENV_OAUTH_REDIRECT_URI = "SIYUR_OAUTH_REDIRECT_URI"
ENV_POST_LOGIN_REDIRECT = "SIYUR_POST_LOGIN_REDIRECT"
ENV_SESSION_HTTPS_ONLY = "SIYUR_SESSION_HTTPS_ONLY"

# Google's OpenID Connect discovery document — Authlib pulls the auth/token/jwks
# endpoints from here lazily (no network at import/registration time).
GOOGLE_OIDC_METADATA_URL = "https://accounts.google.com/.well-known/openid-configuration"


def _env_flag(name: str, *, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """Immutable snapshot of the process environment relevant to the API."""

    google_client_id: str | None
    google_client_secret: str | None
    session_secret: str
    session_secret_is_ephemeral: bool
    oauth_redirect_uri: str | None
    post_login_redirect: str
    session_https_only: bool

    @classmethod
    def from_env(cls) -> Settings:
        session_secret = os.environ.get(ENV_SESSION_SECRET)
        ephemeral = session_secret is None
        if session_secret is None:
            # Boot-without-credentials path: a per-process random key so the app runs
            # and /healthz serves. Cookies won't survive a restart and auth is
            # inert without OAuth creds anyway — real deployments MUST set the env var.
            session_secret = secrets.token_urlsafe(48)
        return cls(
            google_client_id=os.environ.get(ENV_GOOGLE_CLIENT_ID) or None,
            google_client_secret=os.environ.get(ENV_GOOGLE_CLIENT_SECRET) or None,
            session_secret=session_secret,
            session_secret_is_ephemeral=ephemeral,
            oauth_redirect_uri=os.environ.get(ENV_OAUTH_REDIRECT_URI) or None,
            post_login_redirect=os.environ.get(ENV_POST_LOGIN_REDIRECT) or "/",
            session_https_only=_env_flag(ENV_SESSION_HTTPS_ONLY),
        )

    @property
    def is_auth_configured(self) -> bool:
        """True only when both Google OAuth credentials are present."""
        return bool(self.google_client_id and self.google_client_secret)
