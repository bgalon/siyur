# 0011 — Google SSO: Authlib server-side OIDC Authorization-Code flow (DU-00 auth slice)

- Status: accepted
- Decision Maker(s): Ben
- drafted-by: claude-code · approved-by: Ben · Date: 2026-07-31

## Context and Problem Statement

DU-00's runtime slice requires "Google SSO login works" (delivery-plan DU-00; Constitution: Google SSO is the one sanctioned hosted identity dependency). The `api/` scaffold had to choose (a) an OAuth2/OIDC client library and (b) which auth *flow* is canonical for M1. `tech-design.md` §5.4 sketches a **Bearer-token** variant (an SPA obtains a Google `id_token` from Identity Platform and sends `Authorization: Bearer …`), but the web PWA does not exist yet, so an SPA-first flow could not be demonstrated at DU-00. Auth is security-critical (Constitution Article V restricts autonomy here and forbids hand-rolled crypto/validation), so the choice had to favor a mature, standard implementation over bespoke token handling. This ADR formalizes the decision the DU-00 api scaffold (PR #15) made and flagged for ratification.

## Considered Options

- **Library — Authlib vs. google-auth/oauthlib vs. fastapi-users/authx.** Authlib is a mature OAuth2/OIDC client with first-class Starlette/FastAPI integration that performs issuer/audience + JWKS `id_token` validation for us. google-auth/oauthlib direct means owning token exchange and JWKS validation by hand (more security-critical surface). fastapi-users/authx are heavier user-management frameworks — more than a login slice needs.
- **Flow — server-side Authorization-Code (session cookie) vs. Bearer-token (SPA id_token).** Server-side runs the full code→token→session exchange in the API and stores a signed session cookie; it is demoable *before* the web app exists. Bearer (tech-design §5.4) has the SPA acquire the `id_token` and send it per-request; it needs the SPA to exist and pushes token handling to the client. Both resolve a Google `sub` and are interchangeable at the `require_user` seam.

## Decision Outcome

Chosen: **Authlib + the server-side OIDC Authorization-Code flow**, with a signed session cookie (Starlette `SessionMiddleware` + `itsdangerous`), because it is the only combination that (a) keeps `id_token` validation in a mature library rather than hand-rolled (Article V), and (b) is demonstrable at DU-00 with no web app. The Google `sub` is exposed as the mandatory `user_*` scope key in `api/security.py` (`require_user`/`CurrentUser`) per `api/AGENTS.md` row-scoping.

**Server-side is canonical for M1; the Bearer variant (tech-design §5.4) is deferred, not rejected** — both meet at the `require_user` seam, so switching later is a seam-local change, not a rewrite. Secrets (Google client id/secret, session secret) are read from environment variables only; nothing is written to `.env*`/`secrets/`, and the app serves `/healthz` with no credentials present.

### Consequences

- Good: SSO is demoable at DU-00 without the PWA; `id_token` issuer/audience/JWKS validation is the library's job, not ours; the `sub`-scoped seam means the Bearer flow (or a second IdP) can be added behind it without touching call sites.
- Good: pins follow ADR-0007 resolve-then-pin — `authlib~=1.7` (resolved 1.7.2), `itsdangerous~=2.2` (resolved 2.2.0); `uv.lock` consistent.
- Bad / accepted cost: a server-side session cookie is a small amount of server state the pure-Bearer design would not have; accepted for M1 because it is demoable now and swappable at the seam. Revisit if/when the SPA-issued-token model (tech-design §5.4) becomes the chosen client architecture.

### Confirmation

- **Live now (PR #15, merged):** Tier-1 mocked tests — `/healthz` returns 200 with no credentials; `/me` returns 401 unauthenticated; the OAuth callback maps a mocked token to a session. No real Google calls / no network in CI. ruff + mypy --strict + Semgrep (0 findings) green on merge.
- **Standing guard:** `require_user`/`CurrentUser` in `api/security.py` is the single seam every per-user route depends on — the point where a later Bearer flow or second IdP is added without changing call sites.
