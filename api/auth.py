"""Google OIDC login — server-side Authorization Code flow via Authlib.

Flow (DU-00 "Google SSO login works"):
``GET /auth/login`` → redirect to Google's consent screen →
``GET /auth/callback`` → Authlib exchanges the code for tokens, validates the
``id_token`` (issuer/audience against Google's discovery metadata + JWKS), and we
map the ``userinfo`` claims into the signed session → ``GET /auth/logout`` clears it.

Security posture (api/AGENTS.md): auth is security-critical — issuer/audience checks
are Authlib's, never weakened here; the app boots without credentials and these
routes answer ``503`` until :attr:`Settings.is_auth_configured`.
"""

from __future__ import annotations

from typing import Any

from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from starlette.responses import Response

from api.config import GOOGLE_OIDC_METADATA_URL, Settings
from api.security import SessionUser, login_user, logout_user

router = APIRouter(prefix="/auth", tags=["auth"])

CALLBACK_ROUTE_NAME = "auth_callback"


def build_oauth(settings: Settings) -> OAuth:
    """Register a Google OIDC client from settings. Call only when configured."""
    oauth = OAuth()
    oauth.register(
        name="google",
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        server_metadata_url=GOOGLE_OIDC_METADATA_URL,
        client_kwargs={"scope": "openid email profile"},
    )
    return oauth


def _google_client(request: Request) -> Any:
    """The registered Google client, or ``503`` when SSO is unconfigured."""
    oauth: OAuth | None = getattr(request.app.state, "oauth", None)
    if oauth is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Google SSO is not configured. Set SIYUR_GOOGLE_CLIENT_ID and "
                "SIYUR_GOOGLE_CLIENT_SECRET (and SIYUR_SESSION_SECRET) to enable login."
            ),
        )
    return oauth.google


def _redirect_uri(request: Request, settings: Settings) -> str:
    """Absolute callback URL — the explicit env override, else derived from the request."""
    if settings.oauth_redirect_uri:
        return settings.oauth_redirect_uri
    return str(request.url_for(CALLBACK_ROUTE_NAME))


@router.get("/login")
async def login(request: Request) -> Response:
    """Kick off the OAuth Authorization Code flow → redirect to Google."""
    google = _google_client(request)
    settings: Settings = request.app.state.settings
    response: Response = await google.authorize_redirect(request, _redirect_uri(request, settings))
    return response


@router.get("/callback", name=CALLBACK_ROUTE_NAME)
async def callback(request: Request) -> Response:
    """Exchange the code, validate the id_token, map userinfo → session."""
    google = _google_client(request)
    settings: Settings = request.app.state.settings
    token: dict[str, Any] = await google.authorize_access_token(request)
    userinfo = token.get("userinfo")
    if not userinfo:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google did not return an id_token / userinfo.",
        )
    login_user(request, SessionUser.from_userinfo(dict(userinfo)))
    return RedirectResponse(url=settings.post_login_redirect, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/logout")
async def logout(request: Request) -> Response:
    """Clear the session and redirect home."""
    logout_user(request)
    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
