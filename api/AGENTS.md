# AGENTS.md — `api/`

Nested override for work in this package; extends the root `AGENTS.md` (read that first).

**Scope:** the FastAPI service (`docs/design/tech-design.md` §5.4) — a Google-OIDC auth dependency
(verify the JWT: issuer/audience + Google public keys → resolve `user_id`) plus SSE planning endpoints
that stream planner output. Local dev uses the Firebase Auth emulator so the flow runs without real
Google credentials.

**Invariants enforced here:**
- **Auth is security-critical → restricted agent autonomy (agent-ops D4).** Changes to the JWT-verify
  dependency get extra review; covered by SAST + component tests. Never weaken issuer/audience checks.
- **Row-level scoping is the privacy boundary (PRD §13 #4):** every `user_*` query is scoped to the
  authenticated subject; the commons (`site*`) is world-readable to any signed-in user. A missing scope
  is a data-leak bug, not a style nit.
- **Never read or write `.env*` / `secrets/`.** Real keys live in Secret Manager / local keychain.

**Status (DU-00):** package imports; endpoints land in M1.
