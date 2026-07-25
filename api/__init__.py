"""Siyur api — the FastAPI service.

Google-OIDC auth dependency (verify JWT → resolve ``user_id`` → scope ``user_*``
queries) + SSE planning endpoints. Local dev uses the Firebase Auth emulator so the
flow runs without real Google credentials.

Skeleton stage (DU-00): package exists and imports; endpoints land in M1.
"""
