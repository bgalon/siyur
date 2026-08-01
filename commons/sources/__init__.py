"""Research source adapters (ADR-0009) — one module per source, one stamping boundary.

``base`` holds the protocol and the drop/degradation vocabulary; ``overture`` reads the
Overture places theme via DuckDB. Slice-002 sources are additive: one more module here.
"""

from __future__ import annotations

from commons.sources.base import FetchResult, SourceAdapter

__all__ = ["FetchResult", "SourceAdapter"]
