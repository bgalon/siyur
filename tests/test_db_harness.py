"""The Tier-2 harness's own guardrail (FAIL-011).

``conftest.db_session`` deletes every row in every table before each test. That is correct
against a database created to be thrown away and destroys someone's work against one they
are using — and until 2026-08-14 the two were the same database, because
``docker-compose.yml`` binds a fixed port and two checkouts land on one container.

The consequence was not a red test. It was a *live dev stack* losing its rows mid-session
while a concurrent operator debugged the disappearance as a persistence bug in code that was
committing perfectly correctly. Silent, and pointed at the wrong file.

These tests exist so that never becomes true again by accident. They are Tier 1: the
derivation is pure string work up to the point it connects, and the two cases that matter
most — "already disposable" and "no database named" — never connect at all.
"""

from __future__ import annotations

import pytest

from tests.conftest import TEST_DB_SUFFIX, UndisposableDatabase, _disposable_url


def test_a_configured_database_is_never_the_one_tests_truncate() -> None:
    """The property FAIL-011 is about: the derived URL is not the configured URL.

    Asserted on the *name*, not merely on inequality, so a future change that returns some
    other database still has to return one whose name says it is disposable.
    """
    derived = _disposable_url("postgresql+psycopg://siyur:siyur@localhost:5432/siyur")

    assert derived != "postgresql+psycopg://siyur:siyur@localhost:5432/siyur"
    assert derived.endswith(f"/siyur{TEST_DB_SUFFIX}")


def test_deriving_is_idempotent_so_a_test_database_is_used_as_given() -> None:
    """``siyur_test`` in, ``siyur_test`` out — no ``siyur_test_test``, and no connection.

    Someone who has already pointed the variable at a disposable database should not have a
    second one created underneath them.
    """
    already = "postgresql+psycopg://siyur:siyur@localhost:5432/siyur_test"

    assert _disposable_url(already) == already


def test_a_url_naming_no_database_is_refused_rather_than_guessed() -> None:
    """No database name means nothing safe to derive, so it refuses instead of picking one.

    Refusing is the whole point: the alternative to a loud failure here is a quiet one
    somewhere with rows in it.
    """
    with pytest.raises(UndisposableDatabase, match="names no database"):
        _disposable_url("postgresql+psycopg://siyur:siyur@localhost:5432/")


@pytest.mark.integration
def test_the_harness_hands_out_a_disposable_database(postgis_url: str) -> None:
    """End to end: what the fixture actually yields is a ``_test`` database.

    The Tier-1 cases above pin the derivation; this pins that the fixture *uses* it. Without
    it, a future edit could reintroduce ``yield configured`` and every unit test here would
    still pass while the hazard was fully restored.
    """
    assert postgis_url.rstrip("/").endswith(TEST_DB_SUFFIX)
