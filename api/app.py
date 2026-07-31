"""FastAPI application factory for the Siyur API.

Endpoints (DU-00 runtime slice):
- ``GET  /healthz`` — liveness; always ``200``, no auth, no credentials required.
- ``GET  /auth/login`` · ``GET /auth/callback`` · ``POST /auth/logout`` — Google OIDC.
- ``GET  /me`` — the signed-in user, or ``401`` when unauthenticated.

The signed session cookie (Starlette ``SessionMiddleware`` + itsdangerous) is the
auth/session mechanism; identity is scoped by Google ``sub`` (api/security).
"""

from __future__ import annotations

from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from api.auth import build_oauth
from api.auth import router as auth_router
from api.config import Settings
from api.security import CurrentUser, SessionUser


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the app. Boots and serves ``/healthz`` even with no credentials set."""
    settings = settings or Settings.from_env()

    app = FastAPI(
        title="Siyur API",
        summary="Research → plan → compile → travel offline. This service handles auth.",
        version="0.0.0",
    )
    app.state.settings = settings

    # Signed session cookie. `lax` lets the OAuth redirect back from Google carry
    # the cookie; `https_only` is opt-in for prod (SIYUR_SESSION_HTTPS_ONLY).
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        https_only=settings.session_https_only,
        same_site="lax",
    )

    # Only wire the Google client when real credentials are present; otherwise the
    # auth routes answer 503 (see api/auth._google_client).
    if settings.is_auth_configured:
        app.state.oauth = build_oauth(settings)

    @app.get("/healthz", tags=["ops"])
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/me", tags=["auth"], response_model=SessionUser)
    def me(user: CurrentUser) -> SessionUser:
        return user

    app.include_router(auth_router)
    return app


app = create_app()
