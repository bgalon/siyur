"""Siyur commons — the shared data spine.

Home of the data model (``SourcedValue``, ``SiteRecordV1``, ``ItineraryV1``,
``BundleManifestV1``), PostGIS access, per-field merge, and the ``commons/llm.py``
``ModelRouter`` seam (ADR-0004). Seam-purity rule: no provider SDK (anthropic /
openai / litellm) may be imported anywhere above ``commons/llm.py``.

Skeleton stage (DU-00): package exists and imports; models land at DU-00 unit d.
"""
