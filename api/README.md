# Siyur API

FastAPI service for Siyur. This DU-00 runtime slice delivers **Google SSO login**
plus the ops endpoints; the SSE planning routes land in M1.

## Endpoints

| Method | Path             | Auth        | Purpose                                             |
|--------|------------------|-------------|-----------------------------------------------------|
| GET    | `/healthz`       | none        | Liveness — always `200 {"status":"ok"}`.            |
| GET    | `/auth/login`    | none        | Start Google OIDC; redirects to the consent screen. |
| GET    | `/auth/callback` | none        | OAuth code exchange → maps userinfo into the session. |
| POST   | `/auth/logout`   | none        | Clears the session cookie.                           |
| GET    | `/me`            | **session** | The signed-in user; `401` when unauthenticated.      |

## Auth & session approach

Server-side **OAuth 2.0 / OpenID Connect Authorization Code** flow via
[Authlib](https://docs.authlib.org/). The app redirects the browser to Google, and
on callback Authlib exchanges the code and validates the `id_token` (issuer /
audience against Google's discovery metadata + JWKS). The verified claims are stored
in a **signed session cookie** (Starlette `SessionMiddleware`, signed with
[itsdangerous](https://itsdangerous.palletsprojects.com/)).

**Row-scoping (privacy boundary, api/AGENTS.md · PRD §13 #4):** the identity carries
the Google `sub`, which is the stable row-scope key. Every future `user_*` query is
reachable only through the `require_user` / `CurrentUser` dependency
(`api/security.py`) and MUST filter on `current_user.sub`; the commons (`site*`) is
world-readable to any signed-in user.

> Note on the design doc: `tech-design.md` §5.4 sketches a *Bearer-token* variant
> (an SPA obtains a Google id_token from Identity Platform and sends it as
> `Authorization: Bearer`, verified by a FastAPI dependency). This slice implements
> the equivalent **server-side** login/callback flow so "SSO works" end-to-end from
> the API alone before the web PWA exists. The two are interchangeable at the seam:
> both resolve a Google `sub` and scope `user_*` by it. Flagged for the ADR.

## Configuration — environment variables only

Secrets are **never** read from or written to disk (`.env*` / `secrets/` are off
limits — Constitution Article V). The app reads everything from the process
environment. Ben provisions the real Google OAuth client in the **Google Cloud
console** (APIs & Services → Credentials → *OAuth 2.0 Client ID*, type *Web
application*), registers the callback as an *Authorized redirect URI*, and supplies
the values via the shell or Secret Manager.

| Env var                     | Required     | Purpose                                                              |
|-----------------------------|--------------|---------------------------------------------------------------------|
| `SIYUR_GOOGLE_CLIENT_ID`    | for login    | Google OAuth 2.0 client id (`…apps.googleusercontent.com`).         |
| `SIYUR_GOOGLE_CLIENT_SECRET`| for login    | Google OAuth 2.0 client secret.                                     |
| `SIYUR_SESSION_SECRET`      | in prod      | Long random string signing the session cookie. Ephemeral if unset. |
| `SIYUR_OAUTH_REDIRECT_URI`  | optional     | Absolute callback URL; derived from the request when unset.        |
| `SIYUR_POST_LOGIN_REDIRECT` | optional     | Where to send the browser after login (default `/`).               |
| `SIYUR_SESSION_HTTPS_ONLY`  | optional     | `true` marks the cookie Secure (set in production).                 |

The registered redirect URI must match `…/auth/callback` (locally
`http://localhost:8000/auth/callback`).

**Boots without credentials:** with none set, the app still starts and serves
`/healthz`; `/auth/login` and `/auth/callback` return `503` until the two Google
vars are present.

## Run locally

```bash
uv sync
# no credentials needed just to check liveness:
uv run uvicorn api.app:app --reload    # → http://localhost:8000/healthz

# enable real Google login:
export SIYUR_GOOGLE_CLIENT_ID=... SIYUR_GOOGLE_CLIENT_SECRET=... SIYUR_SESSION_SECRET=...
uv run uvicorn api.app:app --reload    # → visit http://localhost:8000/auth/login
```

## Test

```bash
uv run pytest tests/test_api_auth.py -q   # Tier-1, fully mocked — no network, no Google
```
