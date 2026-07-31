"""Siyur api — the FastAPI service.

Google-OIDC auth (server-side Authorization Code flow via Authlib → signed session
cookie → resolve ``sub`` → scope ``user_*`` queries) plus the ops ``/healthz`` and
``/me`` endpoints. Real Google credentials are provisioned in the Google Cloud
console and injected as env vars; the app boots and serves ``/healthz`` without them.

DU-00 runtime slice: the SSO login flow works end-to-end (mocked in Tier-1 tests).
SSE planning endpoints land in M1.
"""

from api.app import app, create_app

__all__ = ["app", "create_app"]
