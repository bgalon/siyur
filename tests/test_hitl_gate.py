"""The HITL approval gate — ADR-0023's four assertions, T022/T026 of Spec 002.

`commons/repository.py` models the pause as **row state** rather than a suspended
coroutine, and every guarantee that follows is the *database's*, not the ORM's. So the
tests that matter run under ``-m integration`` against real PostGIS; a mocked session can
assert the shape of a statement, and that is all it can assert.

**What would make each of these fail.** This slice has shipped assertions that were green
*because* the code was wrong, so each test below names the defect it exists to catch, and
the three named in `docs/design/test-strategy.md`'s mutation discipline were re-run with
the defect deliberately reintroduced:

- drop the ``itinerary_hash`` predicate from :func:`~commons.repository.approve_plan` and
  ``test_an_in_place_mutation_is_stale_not_idempotent`` fails;
- let :func:`~commons.repository.claim_plan_for_compile` read the status and then write, and
  ``test_two_compilers_racing_produce_one_claim`` fails — note that
  ``test_a_proposed_plan_is_unclaimable`` does **not**, because a sequential second call
  reads ``compiling`` and declines just as the real claim does. Only the forced interleave
  discriminates, which is why it is its own test;
- clear the approval on an edit and
  ``test_editing_an_approved_plan_supersedes_it_and_keeps_its_history`` fails **two
  different ways**, both verified by running them: clearing ``approved_at`` *and*
  ``approved_by`` loses the history silently (the assertion catches it), while clearing
  ``approved_at`` alone — ADR-0023's literal earlier wording — raises
  ``CheckViolation: ck_user_plan_approver`` and rolls the transaction back, because those
  two columns move together by biconditional;
- widen :func:`~commons.repository.record_feasibility`'s ``where`` to ``[]`` and
  ``test_a_post_approval_plan_refuses_a_second_feasibility_verdict`` fails — silently
  reverting an approved plan to ``proposed`` rather than raising, because the same statement
  writes a status the ``CHECK`` does not cover;
- add any compile state to :data:`~commons.repository.EDITABLE_STATUSES` and
  ``test_a_plan_handed_to_the_compiler_is_no_longer_editable`` fails;
- drop the ``except`` that unwinds :func:`~commons.repository.supersede_plan`'s savepoint and
  ``test_a_replayed_edit_raises_without_poisoning_the_callers_transaction`` fails.

**Tier-2 tests here are labelled per test, and the label is asserted.** A missing
``@pytest.mark.integration`` is invisible in both CI lanes — deselected by ``-m integration``
in job 3, skipped for want of a database in job 2 — so
``test_every_database_backed_test_here_carries_the_integration_marker`` checks it in Tier 1,
where neither escape exists.

The Tier-1 half is two things a database cannot tell us: that a stored itinerary rehashes
to the digest stored beside it (if it did not, **every** approve would return
``plan_stale``), and that every statement this module emits carries ``user_id`` in its
``WHERE`` — the PRD §13 #4 privacy boundary, asserted on the SQL rather than on a response
code, because a ``404`` can be produced by code that read another subject's row first.
"""

from __future__ import annotations

import inspect
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Final
from uuid import UUID, uuid4

import pytest
from geoalchemy2.shape import from_shape
from shapely.geometry import box
from sqlalchemy import Engine, exc, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from commons.db import DATABASE_URL_ENV, SRID, Area, UserPlan
from commons.models import ItineraryV1
from commons.repository import (
    PlanApproved,
    PlanRecord,
    PlanRefused,
    approve_plan,
    claim_plan_for_compile,
    create_plan,
    finish_compile,
    load_plan,
    record_feasibility,
    supersede_plan,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

SUBJECT = "google-sub-a"
OTHER = "google-sub-b"
#: The dialect the Tier-1 assertions compile against — the one production speaks.
_PG_DIALECT: Any = postgresql.dialect()  # type: ignore[no-untyped-call]
#: A generic 1 km box — no place literal, and `user_plan` never touches its geometry.
AREA_POLYGON = box(28.216, 36.440, 28.232, 36.451)

#: What makes a test in this file Tier 2: it reaches, directly or through a local fixture,
#: for a live PostGIS. ``area``/``feasible_plan`` are here because a test may request only
#: those and still need the database — the fixture graph is what decides the tier, not the
#: heading a test happens to be written under.
DB_FIXTURES: Final[frozenset[str]] = frozenset(
    {"postgis_url", "db_engine", "db_session", "area", "feasible_plan"}
)


def _itinerary(area_id: UUID, *, user_id: str = SUBJECT, walking_m: float = 4000.0) -> ItineraryV1:
    """A minimal valid day. ``stops`` stays empty on purpose — `user_plan` has no FK to
    ``site``, and involving the commons here would blur the boundary this file defends."""
    return ItineraryV1.model_validate(
        {
            "id": str(uuid4()),
            "user_id": user_id,
            "area_id": str(area_id),
            "date": "2026-08-14",
            "lang": "en",
            "budgets": {"walking_m": walking_m, "hours": 4.0},
        }
    )


# ── Tier 1 ─────────────────────────────────────────────────────────────────────────


def test_a_stored_itinerary_rehashes_to_the_digest_stored_beside_it() -> None:
    """The compare-and-set's silent catastrophe: a hash that does not survive a round trip.

    ``approve_plan`` recomputes the digest from the *stored* document — the approve endpoint
    has an empty body, so there is nothing for a client to send — and compares it to the
    ``itinerary_hash`` column written at insert time. If ``model_dump(mode="json")`` →
    ``model_validate`` → ``canonical_sha256()`` were not a fixed point, the predicate would
    never match and **every** approval would come back ``plan_stale`` with nothing wrong.
    """
    original = _itinerary(uuid4())
    stored = original.model_dump(mode="json")
    assert ItineraryV1.model_validate(stored).canonical_sha256() == original.canonical_sha256()
    # And it is stable across a second round trip — tuples→lists→tuples, date→str→date.
    twice = ItineraryV1.model_validate(ItineraryV1.model_validate(stored).model_dump(mode="json"))
    assert twice.canonical_sha256() == original.canonical_sha256()


def _fake_row(**overrides: Any) -> SimpleNamespace:
    """The column tuple ``_plan_of`` reads, without a database behind it."""
    itinerary = _itinerary(uuid4())
    row = SimpleNamespace(
        id=itinerary.id,
        user_id=SUBJECT,
        area_id=itinerary.area_id,
        itinerary=itinerary.model_dump(mode="json"),
        status="proposed",
        revision=1,
        superseded_by=None,
        approved_at=None,
        approved_by=None,
        itinerary_hash=itinerary.canonical_sha256(),
        feasible=True,
        violations=[],
        feasibility_checked_at=datetime.now(UTC),
    )
    return SimpleNamespace(**{**vars(row), **overrides})


class _ScriptedSession:
    """A stand-in ``Session``: records every statement, returns scripted rows in order.

    Enough to compile the SQL each transition emits without a database — which is the only
    thing Tier 1 can honestly say about a guarantee that belongs to the transaction. Rows
    beyond the script are ``None``, i.e. "matched nothing", which is the branch under test.
    """

    def __init__(self, *rows: Any) -> None:
        self.statements: list[Any] = []
        self._rows = list(rows)

    def execute(self, statement: Any, *args: Any, **kwargs: Any) -> Any:
        self.statements.append(statement)
        row = self._rows.pop(0) if self._rows else None
        return SimpleNamespace(first=lambda: row, one=lambda: row)

    def begin_nested(self) -> SimpleNamespace:
        return SimpleNamespace(commit=lambda: None, rollback=lambda: None)

    def compiled(self, index: int) -> Any:
        """The compiled statement — ``str()`` for the text, ``.params`` for the bound values.

        Compiled with **bound parameters, not literals**: `jsonb` has no literal renderer,
        so ``literal_binds`` cannot compile the statements that carry an itinerary at all.
        """
        return self.statements[index].compile(dialect=_PG_DIALECT)


def test_every_plan_statement_filters_on_user_id() -> None:
    """PRD §13 #4, asserted on the SQL. A missing scope is invisible in a response code.

    A handler that loaded a row unscoped and *then* compared subjects would still answer
    ``404`` for a foreign plan — and would still have read it. The boundary is the ``WHERE``
    clause, so that is what this checks, for every statement the module emits.
    """
    plan_id = uuid4()
    for call in (
        lambda s: load_plan(s, plan_id, user_id=SUBJECT),
        lambda s: approve_plan(s, plan_id, user_id=SUBJECT, approved_by=SUBJECT),
        lambda s: claim_plan_for_compile(s, plan_id, user_id=SUBJECT),
        lambda s: finish_compile(s, plan_id, user_id=SUBJECT, outcome="compiled"),
        lambda s: record_feasibility(s, plan_id, user_id=SUBJECT, feasible=True, violations=[]),
        lambda s: supersede_plan(s, plan_id, user_id=SUBJECT, itinerary=_itinerary(uuid4())),
    ):
        session = _ScriptedSession(_fake_row(), _fake_row(), _fake_row())
        call(session)  # a stand-in, deliberately not a Session
        assert session.statements, "a transition that emits no SQL cannot be scoped"
        for index in range(len(session.statements)):
            compiled = session.compiled(index)
            text_sql = str(compiled)
            if text_sql.startswith("INSERT"):
                # `supersede_plan`'s successor: scoped by the *value it writes*, not a WHERE.
                assert compiled.params["user_id"] == SUBJECT
                continue
            assert "user_plan.user_id = " in text_sql, text_sql
            assert compiled.params["user_id_1"] == SUBJECT, compiled.params


def test_the_compile_claim_is_a_conditional_update_not_a_read() -> None:
    """ADR-0023 Confirmation (a), at the SQL level: the gate is a ``WHERE``, not an ``if``."""
    session = _ScriptedSession()
    assert claim_plan_for_compile(session, uuid4(), user_id=SUBJECT) is None  # type: ignore[arg-type]
    assert len(session.statements) == 1, "a claim that reads first is not a claim"
    compiled = session.compiled(0)
    assert str(compiled).startswith("UPDATE user_plan SET"), str(compiled)
    assert compiled.params["status"] == "compiling", "the SET"
    assert compiled.params["status_1"] == "approved", "the gate, as a WHERE"


def test_approval_predicates_on_status_hash_and_feasibility() -> None:
    """The three predicates that make double-approve, staleness and FR-005 mechanical.

    ``feasible`` is in the ``WHERE`` so an infeasible plan draws a *refusal* rather than a
    `CHECK` violation that would abort the caller's transaction; the `CHECK` is still what
    makes approval impossible. Removing any one of the three leaves this red — and removing
    the hash predicate is the mutation ADR-0023 was corrected over.
    """
    row = _fake_row()
    # Read the row, fail to update it, read it back: the discrimination path.
    session = _ScriptedSession(row, None, row)
    approve_plan(session, row.id, user_id=SUBJECT, approved_by=SUBJECT)  # type: ignore[arg-type]

    assert str(session.compiled(0)).startswith("SELECT"), "the digest comes from the stored row"
    update = session.compiled(1)
    assert str(update).startswith("UPDATE user_plan SET"), str(update)
    assert update.params["status"] == "approved"
    assert update.params["status_1"] == "proposed", "the compare half of the compare-and-set"
    assert update.params["itinerary_hash_1"] == row.itinerary_hash, "the set half"
    assert "user_plan.feasible IS true" in str(update), str(update)
    assert str(session.compiled(2)).startswith("SELECT"), "a zero-row update must re-read"


def test_a_zero_row_update_branches_on_the_row_state_not_the_failed_predicate() -> None:
    """The correction ADR-0023 makes by name, exercised without a database.

    Because an edit writes a **new** row, a superseded row's ``itinerary_hash`` still
    matches and it is the *status* predicate that fails. An implementation that inferred
    "status failed ⇒ idempotent" would answer ``200`` for every real stale approve, and
    ``itinerary_hash`` would never be load-bearing at all. Each row below is the *same*
    zero-row update with a different row underneath it.
    """
    successor = uuid4()
    mutated = _itinerary(uuid4())
    cases: list[tuple[SimpleNamespace, type[PlanApproved] | type[PlanRefused], str | None]] = [
        (_fake_row(status="approved", approved_at=datetime.now(UTC)), PlanApproved, None),
        (_fake_row(status="compiling", approved_at=datetime.now(UTC)), PlanApproved, None),
        (
            _fake_row(status="superseded", superseded_by=successor),
            PlanRefused,
            "plan_superseded",
        ),
        (_fake_row(itinerary=mutated.model_dump(mode="json")), PlanRefused, "plan_stale"),
        (_fake_row(feasible=False, violations=["over budget"]), PlanRefused, "infeasible"),
        (_fake_row(status="proposing"), PlanRefused, "plan_not_approvable"),
        (_fake_row(status="failed"), PlanRefused, "plan_not_approvable"),
    ]
    for row, expected_type, reason in cases:
        session = _ScriptedSession(row, None, row)
        outcome = approve_plan(session, row.id, user_id=SUBJECT, approved_by=SUBJECT)  # type: ignore[arg-type]
        assert isinstance(outcome, expected_type), (row.status, outcome)
        if isinstance(outcome, PlanRefused):
            assert outcome.reason == reason, (row.status, outcome.reason)
        else:
            assert not outcome.newly_approved, "a zero-row update approved nothing"


def test_every_database_backed_test_here_carries_the_integration_marker() -> None:
    """The tier label is a *selector*, and an unlabelled Tier-2 test runs in neither lane.

    This file once carried ``pytestmark_integration = pytest.mark.integration`` under the
    Tier-2 heading. pytest's magic name is ``pytestmark``; that one was an ordinary module
    attribute nothing read, and it was harmless only because every test below it also
    carried its own decorator. The next one added under that heading would have been
    **deselected** in CI job 3 (``-m integration`` does not match it) and **skipped** in job
    2 (``postgis_url`` skips when ``SIYUR_DATABASE_URL`` is unset) — green in both lanes,
    executed in neither.

    Promoting it to a real ``pytestmark`` is the wrong repair: a module-level mark applies
    to the whole file, so the five Tier-1 tests above would be labelled ``integration`` too
    and would start claiming to need a database they do not touch. So the marker stays
    per-test and this asserts it, in Tier 1, where it is checked with no database present.

    Mutation: delete any ``@pytest.mark.integration`` below and this goes red — in **job
    2**, which is the lane the missing marker would otherwise hide in.
    """
    unmarked = sorted(
        name
        for name, function in globals().items()
        if name.startswith("test_")
        and callable(function)
        and DB_FIXTURES & set(inspect.signature(function).parameters)
        and not any(mark.name == "integration" for mark in getattr(function, "pytestmark", ()))
    )
    assert not unmarked, f"Tier-2 tests missing @pytest.mark.integration: {unmarked}"


# ── Tier 2 — the guarantees are the transaction's ──────────────────────────────────
#
# Every test below MUST carry `@pytest.mark.integration` — asserted, not asked for, by
# `test_every_database_backed_test_here_carries_the_integration_marker` above.


@pytest.fixture
def area(db_session: Session) -> Area:
    row = Area(polygon=from_shape(AREA_POLYGON, srid=SRID), created_by=SUBJECT)
    db_session.add(row)
    db_session.flush()
    return row


@pytest.fixture
def feasible_plan(db_session: Session, area: Area) -> PlanRecord:
    """A plan that has been proposed and checked, sitting in the pause: ``proposed``."""
    plan = create_plan(db_session, _itinerary(area.id))
    checked = record_feasibility(db_session, plan.id, user_id=SUBJECT, feasible=True, violations=[])
    assert checked is not None
    db_session.commit()
    return checked


#: Backends waiting on a lock **this** session holds. ``pg_blocking_pids`` resolves a
#: row-level wait to the transaction actually holding the row (Postgres reports the wait as
#: a ``transactionid`` lock; the function untangles that), so a non-zero count is the
#: database's own statement that the other session is stuck behind us — not an inference
#: from how long it has taken to answer.
_BLOCKED_BY_US: Final = text(
    "SELECT count(*) FROM pg_stat_activity WHERE pg_backend_pid() = ANY (pg_blocking_pids(pid))"
)

#: **``pg_stat_activity`` is cached for the life of a transaction**, and the poller below is
#: *inside* one — it is the session holding the row. Without this the first read is the only
#: read: every later poll re-returns a snapshot taken before the worker's backend existed,
#: the loop times out, and the whole mechanism is dead. Measured against PostGIS 16-3.4: the
#: worker appears one poll after the clear, ``wait_event_type='Lock'``,
#: ``wait_event='transactionid'``, ``pg_blocking_pids`` naming the holder; without the clear
#: it never appears at all. (``stats_fetch_consistency`` defaults to ``cache`` in PG 15+.)
_CLEAR_STAT_SNAPSHOT: Final = text("SELECT pg_stat_clear_snapshot()")


def _await_contention(holder: Session, thread: threading.Thread, timeout: float = 60.0) -> None:
    """Block until the database says a backend is waiting on a lock ``holder`` holds.

    **Why not a wall clock.** The predecessor of this function set an event and then asserted
    ``thread.is_alive()`` after a 2 s join. That assertion is satisfied by a worker still
    inside ``Session(db_engine)`` — pool checkout, TCP connect, TLS, the ``psycopg`` startup
    packet — before it has issued a single statement. On a loaded runner or a cold
    testcontainer the whole 2 s can go there, and then the holder commits, the worker's
    ``SELECT`` finally runs against the *committed* row, reads ``compiling`` and declines,
    and a read-then-write implementation passes. The mutation would survive intermittently,
    which is worse than surviving outright: the file's own docstring would be false and the
    suite would keep saying otherwise. Asking ``pg_stat_activity`` removes the clock from the
    predicate — nothing here is timed except the give-up.

    That the old assertion *passed* is not evidence it was watching anything: whether the
    worker had reached its ``UPDATE`` by the 2 s mark was luck, and so was whether it had
    even connected. Both are now observed rather than assumed.
    """
    deadline = time.monotonic() + timeout
    while True:
        holder.execute(_CLEAR_STAT_SNAPSHOT)
        if holder.execute(_BLOCKED_BY_US).scalar_one():
            return
        assert thread.is_alive(), (
            "the second session finished without ever waiting on the row lock — it never "
            "contended, so this test is not observing the conditional UPDATE at all"
        )
        assert time.monotonic() < deadline, (
            f"no backend blocked on this session's row lock within {timeout}s"
        )
        time.sleep(0.01)


def _contend[T](db_engine: Engine, holder: Session, work: Callable[[Session], T]) -> T:
    """Run ``work`` in a second session while ``holder`` still holds an uncommitted write.

    **The interleave is forced, not hoped for.** Two threads released from a barrier almost
    always run to completion one after the other, and a sequential pair cannot tell a
    conditional ``UPDATE`` apart from a read-then-write — both refuse the second call. So the
    second session is asserted to be genuinely *blocked* on the row lock, by
    :func:`_await_contention` asking the database, before the first is committed; only then
    does its statement re-evaluate against the winner's committed version, which is the
    moment the compare-and-set either holds or does not.

    Returns what the second session got once it unblocked. An exception raised inside the
    worker is re-raised **here**: left in the thread it would surface as a dead thread, i.e.
    as "it never contended", which is the wrong diagnosis attached to the wrong line.
    """
    result: dict[str, T] = {}
    failure: list[BaseException] = []

    def run() -> None:
        try:
            with Session(db_engine) as other:
                result["value"] = work(other)
                other.commit()
        except Exception as error:  # re-raised in the parent, below
            failure.append(error)

    thread = threading.Thread(target=run)
    thread.start()
    try:
        _await_contention(holder, thread)
    finally:
        # Release the row whatever happened: a failed assertion above must not leave the
        # worker blocked forever, which would hang the suite instead of reporting.
        holder.commit()
        thread.join(timeout=60)
    assert not thread.is_alive()
    if failure:
        raise failure[0]
    return result["value"]


def _status(session: Session, plan_id: UUID) -> str:
    return session.execute(select(UserPlan.status).where(UserPlan.id == plan_id)).scalar_one()


@pytest.mark.integration
def test_a_plan_starts_in_the_pause_and_feasibility_advances_it(
    db_session: Session, area: Area
) -> None:
    """``proposing → proposed``, and the verdict lands with a timestamp of its own."""
    plan = create_plan(db_session, _itinerary(area.id))
    assert (plan.status, plan.revision, plan.feasible) == ("proposing", 1, False)
    assert plan.feasibility_checked_at is None
    assert plan.itinerary_hash == plan.content_hash

    checked = record_feasibility(
        db_session, plan.id, user_id=SUBJECT, feasible=False, violations=["walking_m 4200 > 4000"]
    )
    assert checked is not None
    assert (checked.status, checked.feasible) == ("proposed", False)
    assert checked.violations == ("walking_m 4200 > 4000",)
    assert checked.feasibility_checked_at is not None


@pytest.mark.integration
def test_a_proposed_plan_is_unclaimable(db_session: Session, feasible_plan: PlanRecord) -> None:
    """ADR-0023 Confirmation (a): no compile before approval, and the lifecycle after it.

    **This test alone does not distinguish a claim from a read-then-write** — sequentially
    both refuse the second call. ``test_two_compilers_racing_produce_one_claim`` is the one
    that does, and it is separate because it needs a forced interleave to say anything.
    """
    assert claim_plan_for_compile(db_session, feasible_plan.id, user_id=SUBJECT) is None
    assert _status(db_session, feasible_plan.id) == "proposed"

    approve_plan(db_session, feasible_plan.id, user_id=SUBJECT, approved_by=SUBJECT)
    claimed = claim_plan_for_compile(db_session, feasible_plan.id, user_id=SUBJECT)
    assert claimed is not None and claimed.status == "compiling"
    assert claim_plan_for_compile(db_session, feasible_plan.id, user_id=SUBJECT) is None

    done = finish_compile(db_session, feasible_plan.id, user_id=SUBJECT, outcome="compiled")
    assert done is not None and done.status == "compiled"
    # `failed`/`compiled` are terminal: releasing a claim nobody holds changes nothing.
    assert finish_compile(db_session, feasible_plan.id, user_id=SUBJECT, outcome="failed") is None
    # And a compiled plan re-approved is idempotent, not an error (ADR-0023 table, row 4).
    replay = approve_plan(db_session, feasible_plan.id, user_id=SUBJECT, approved_by=SUBJECT)
    assert isinstance(replay, PlanApproved) and not replay.newly_approved


@pytest.mark.integration
def test_an_infeasible_plan_is_unapprovable_in_the_database(
    db_session: Session, area: Area
) -> None:
    """FR-005 is a `CHECK`, not an ``if``: the refusal survives code that forgets to ask.

    Asserted by going *around* the repository with raw SQL — the only way to show the
    guarantee belongs to the database. Inside a savepoint, because a `CHECK` violation
    aborts the transaction it happens in.
    """
    plan = create_plan(db_session, _itinerary(area.id))
    record_feasibility(
        db_session, plan.id, user_id=SUBJECT, feasible=False, violations=["over budget"]
    )
    db_session.commit()

    refused = approve_plan(db_session, plan.id, user_id=SUBJECT, approved_by=SUBJECT)
    assert refused == PlanRefused("infeasible", load_plan(db_session, plan.id, user_id=SUBJECT))

    savepoint = db_session.begin_nested()
    with pytest.raises(exc.IntegrityError, match="ck_user_plan_feasible"):
        db_session.execute(
            text(
                "UPDATE user_plan SET status='approved', approved_at=now(), "
                "approved_by=:who WHERE id=:id"
            ),
            {"who": SUBJECT, "id": plan.id},
        )
    savepoint.rollback()
    assert _status(db_session, plan.id) == "proposed"


@pytest.mark.integration
def test_an_in_place_mutation_is_stale_not_idempotent(
    db_session: Session, feasible_plan: PlanRecord
) -> None:
    """ADR-0023's third zero-row row: the itinerary changed under a digest that did not.

    A revision counter cannot see this — nothing wrote a new row. Only the hash predicate
    does, which is the whole reason ``itinerary_hash`` is in the ``WHERE``.
    """
    db_session.execute(
        text(
            "UPDATE user_plan SET itinerary = jsonb_set(itinerary, '{lang}', '\"fr\"') "
            "WHERE id = :id"
        ),
        {"id": feasible_plan.id},
    )
    db_session.commit()

    outcome = approve_plan(db_session, feasible_plan.id, user_id=SUBJECT, approved_by=SUBJECT)
    assert isinstance(outcome, PlanRefused) and outcome.reason == "plan_stale"
    assert _status(db_session, feasible_plan.id) == "proposed"
    assert outcome.plan is not None and outcome.plan.itinerary_hash != outcome.plan.content_hash


@pytest.mark.integration
def test_editing_an_approved_plan_supersedes_it_and_keeps_its_history(
    db_session: Session, area: Area, feasible_plan: PlanRecord
) -> None:
    """ADR-0023 Confirmation (d). **An edit clears nothing**, and the successor is unapproved
    until feasibility has run again — which is the spec's "returns to unapproved and re-runs
    feasibility", expressed as rows rather than as a rule someone has to remember."""
    approved = approve_plan(db_session, feasible_plan.id, user_id=SUBJECT, approved_by=SUBJECT)
    assert isinstance(approved, PlanApproved) and approved.newly_approved
    db_session.commit()

    successor = supersede_plan(
        db_session, feasible_plan.id, user_id=SUBJECT, itinerary=_itinerary(area.id, walking_m=9000)
    )
    assert successor is not None
    db_session.commit()

    prior = load_plan(db_session, feasible_plan.id, user_id=SUBJECT)
    assert prior is not None
    assert prior.status == "superseded"
    assert prior.superseded_by == successor.id
    # The history: an edit does not un-approve what was approved, it records what replaced it.
    assert prior.approved_at == approved.plan.approved_at
    assert prior.approved_by == SUBJECT

    assert (successor.status, successor.revision) == ("proposing", 2)
    assert (successor.approved_at, successor.approved_by) == (None, None)
    assert successor.feasibility_checked_at is None, "the successor must be re-checked"

    # Approving the stale id names its successor — the `409 plan_superseded` body.
    stale = approve_plan(db_session, feasible_plan.id, user_id=SUBJECT, approved_by=SUBJECT)
    assert isinstance(stale, PlanRefused) and stale.reason == "plan_superseded"
    assert stale.plan is not None and stale.plan.superseded_by == successor.id

    # The successor cannot be approved until feasibility has actually run over it …
    not_yet = approve_plan(db_session, successor.id, user_id=SUBJECT, approved_by=SUBJECT)
    assert isinstance(not_yet, PlanRefused) and not_yet.reason == "plan_not_approvable"
    record_feasibility(
        db_session, successor.id, user_id=SUBJECT, feasible=False, violations=["9000 m > budget"]
    )
    over = approve_plan(db_session, successor.id, user_id=SUBJECT, approved_by=SUBJECT)
    assert isinstance(over, PlanRefused) and over.reason == "infeasible"
    # … and once it has, on its own id.
    record_feasibility(db_session, successor.id, user_id=SUBJECT, feasible=True, violations=[])
    assert isinstance(
        approve_plan(db_session, successor.id, user_id=SUBJECT, approved_by=SUBJECT), PlanApproved
    )
    # A superseded row is not editable again: its chain already has a successor.
    assert (
        supersede_plan(db_session, feasible_plan.id, user_id=SUBJECT, itinerary=_itinerary(area.id))
        is None
    )


@pytest.mark.integration
def test_a_post_approval_plan_refuses_a_second_feasibility_verdict(
    db_session: Session, feasible_plan: PlanRecord
) -> None:
    """``record_feasibility``'s status fence, which is a guard and not tidiness.

    A re-check over an already-approved plan is ordinary: a retried request, a background
    revalidation after the commons refreshed under the day, a pipeline node replaying. The
    fence must make that a **no-op**, not a write.

    Mutation: widen ``where`` to ``[]`` and this goes red on the first iteration. What
    actually happens then is worse than the ``CHECK`` violation it looks like — the same
    statement writes ``status='proposed'``, which is *outside*
    :data:`~commons.db.POST_APPROVAL_STATUSES`, so ``ck_user_plan_feasible`` never fires and
    nothing raises. The approval is silently reverted instead, with ``approved_at`` still on
    the row. Hence the assertions are on the row's state, not on an exception.
    """
    plan_id = feasible_plan.id
    assert isinstance(
        approve_plan(db_session, plan_id, user_id=SUBJECT, approved_by=SUBJECT), PlanApproved
    )
    before = load_plan(db_session, plan_id, user_id=SUBJECT)
    assert before is not None

    # The whole post-approval set, reached the only way it can legitimately be reached.
    advances: list[tuple[str, Callable[[], PlanRecord | None] | None]] = [
        ("approved", None),
        ("compiling", lambda: claim_plan_for_compile(db_session, plan_id, user_id=SUBJECT)),
        (
            "compiled",
            lambda: finish_compile(db_session, plan_id, user_id=SUBJECT, outcome="compiled"),
        ),
    ]
    for status, advance in advances:
        if advance is not None:
            assert advance() is not None
        assert _status(db_session, plan_id) == status

        declined = record_feasibility(
            db_session, plan_id, user_id=SUBJECT, feasible=False, violations=["a later re-check"]
        )
        assert declined is None, f"a {status} plan accepted a new feasibility verdict"

        after = load_plan(db_session, plan_id, user_id=SUBJECT)
        assert after is not None
        assert (after.status, after.feasible, after.violations) == (status, True, ())
        assert after.approved_at == before.approved_at
        assert after.feasibility_checked_at == before.feasibility_checked_at


def _plan_count(session: Session) -> int:
    return len(session.execute(select(UserPlan.id)).all())


@pytest.mark.integration
def test_a_plan_handed_to_the_compiler_is_no_longer_editable(
    db_session: Session, area: Area, feasible_plan: PlanRecord
) -> None:
    """:data:`~commons.repository.EDITABLE_STATUSES` excludes the three compile states, and
    the ``compiling`` exclusion is the one with teeth.

    The interleave it prevents: the compiler holds a **committed** claim and is doing long
    work; the user hits edit; ``supersede_plan`` flips the row to ``superseded`` and points
    it at a fresh ``proposing`` successor; the compiler finishes and calls
    ``finish_compile``, whose ``WHERE status='compiling'`` now matches nothing and returns
    ``None`` — which reads to the compiler as "I never held a claim". End state: a bundle in
    object storage that no plan row references, and an approved day whose successor is
    unapproved. ``compiled``/``failed`` are excluded for the milder reason that a bundle
    already exists for that exact document.

    Mutation: add any of the three to ``EDITABLE_STATUSES`` and this goes red. Only
    ``superseded`` was covered before, by the last assertion of
    ``test_editing_an_approved_plan_supersedes_it_and_keeps_its_history``.
    """
    approve_plan(db_session, feasible_plan.id, user_id=SUBJECT, approved_by=SUBJECT)
    assert claim_plan_for_compile(db_session, feasible_plan.id, user_id=SUBJECT) is not None
    db_session.commit()

    # A second plan, taken to `failed` — the outcome that keeps its approval and its failure.
    other = create_plan(db_session, _itinerary(area.id))
    record_feasibility(db_session, other.id, user_id=SUBJECT, feasible=True, violations=[])
    approve_plan(db_session, other.id, user_id=SUBJECT, approved_by=SUBJECT)
    claim_plan_for_compile(db_session, other.id, user_id=SUBJECT)
    assert finish_compile(db_session, other.id, user_id=SUBJECT, outcome="failed") is not None
    db_session.commit()

    for plan_id, status in ((feasible_plan.id, "compiling"), (other.id, "failed")):
        assert _status(db_session, plan_id) == status
        rows = _plan_count(db_session)
        assert (
            supersede_plan(db_session, plan_id, user_id=SUBJECT, itinerary=_itinerary(area.id))
            is None
        ), f"a {status} plan was edited out from under its compile"
        # The savepoint unwound: a refused edit leaves no orphan successor behind either.
        assert _plan_count(db_session) == rows
        assert _status(db_session, plan_id) == status

    # And `compiled`, reached from the claim the first plan still holds.
    assert finish_compile(db_session, feasible_plan.id, user_id=SUBJECT, outcome="compiled")
    rows = _plan_count(db_session)
    assert (
        supersede_plan(db_session, feasible_plan.id, user_id=SUBJECT, itinerary=_itinerary(area.id))
        is None
    )
    assert _plan_count(db_session) == rows
    assert _status(db_session, feasible_plan.id) == "compiled"


@pytest.mark.integration
def test_a_replayed_edit_raises_without_poisoning_the_callers_transaction(
    db_session: Session, area: Area, feasible_plan: PlanRecord
) -> None:
    """``supersede_plan``'s *other* savepoint exit: the ``INSERT`` raising, not the ``UPDATE``
    matching zero rows.

    The plan row id **is** the itinerary id, so re-submitting an itinerary that has already
    been written is a primary key violation — reachable on any retried edit. The docstring
    promises the caller's transaction stays usable "either way"; an aborted savepoint that is
    never unwound poisons the enclosing transaction exactly as thoroughly as no savepoint at
    all. The error still propagates, because a duplicate edit is the caller's to answer.

    Mutation: drop the ``except`` clause from ``supersede_plan`` and the reads after the
    ``raises`` block fail with ``PendingRollbackError`` instead of returning rows.
    """
    edit = _itinerary(area.id)
    successor = supersede_plan(db_session, feasible_plan.id, user_id=SUBJECT, itinerary=edit)
    assert successor is not None and successor.id == edit.id
    db_session.commit()

    with pytest.raises(exc.IntegrityError):
        supersede_plan(db_session, successor.id, user_id=SUBJECT, itinerary=edit)

    # Same session, same transaction: still usable, and nothing was half-written.
    survivor = load_plan(db_session, successor.id, user_id=SUBJECT)
    assert survivor is not None
    assert (survivor.status, survivor.superseded_by) == ("proposing", None)
    assert _plan_count(db_session) == 2


@pytest.mark.integration
def test_a_second_user_gets_nothing_not_a_403_shaped_answer(
    db_session: Session, area: Area, feasible_plan: PlanRecord
) -> None:
    """Another subject's plan is **indistinguishable from a missing one** — the same object,
    not merely the same status code — and nothing they call leaves a mark on the row."""
    unknown = uuid4()
    assert load_plan(db_session, feasible_plan.id, user_id=OTHER) is None
    assert load_plan(db_session, unknown, user_id=SUBJECT) is None
    assert approve_plan(
        db_session, feasible_plan.id, user_id=OTHER, approved_by=OTHER
    ) == approve_plan(db_session, unknown, user_id=SUBJECT, approved_by=SUBJECT)

    assert (
        record_feasibility(
            db_session, feasible_plan.id, user_id=OTHER, feasible=True, violations=[]
        )
        is None
    )
    assert claim_plan_for_compile(db_session, feasible_plan.id, user_id=OTHER) is None
    assert (
        supersede_plan(db_session, feasible_plan.id, user_id=OTHER, itinerary=_itinerary(area.id))
        is None
    )
    db_session.commit()

    untouched = load_plan(db_session, feasible_plan.id, user_id=SUBJECT)
    assert untouched is not None
    assert (untouched.status, untouched.approved_at, untouched.superseded_by) == (
        "proposed",
        None,
        None,
    )
    # A foreign supersede must not leave its would-be successor behind either.
    assert db_session.execute(select(UserPlan.id).where(UserPlan.user_id == OTHER)).first() is None


@pytest.mark.integration
def test_concurrent_double_approve_yields_exactly_one_approval(
    db_engine: Engine, db_session: Session, feasible_plan: PlanRecord
) -> None:
    """SC-003's edge case: a double-click, two tabs, a retried request.

    The loser re-evaluates its predicate against the winner's committed version, matches
    zero rows, and *replays* the existing approval rather than erroring: one ``approved_at``,
    one compilable plan, and a ``200`` from both, because nothing failed.
    """
    with Session(db_engine) as first:
        winner = approve_plan(first, feasible_plan.id, user_id=SUBJECT, approved_by=SUBJECT)
        assert isinstance(winner, PlanApproved) and winner.newly_approved
        # `first` has not committed: the row is write-locked and the approval is invisible.
        replay = _contend(
            db_engine,
            first,
            lambda s: approve_plan(s, feasible_plan.id, user_id=SUBJECT, approved_by=SUBJECT),
        )

    assert isinstance(replay, PlanApproved), replay
    assert not replay.newly_approved, "the second call approved the plan a second time"
    # The replay reports the *same* moment, so no caller can observe two different answers.
    assert replay.plan.approved_at == winner.plan.approved_at

    db_session.commit()
    stamped = load_plan(db_session, feasible_plan.id, user_id=SUBJECT)
    assert stamped is not None and stamped.status == "approved"
    assert stamped.approved_at == winner.plan.approved_at


@pytest.mark.integration
def test_two_compilers_racing_produce_one_claim(
    db_engine: Engine, db_session: Session, feasible_plan: PlanRecord
) -> None:
    """ADR-0023's compile guarantee — **and the test that distinguishes the mechanism**.

    A read-then-write compiler passes every sequential assertion in this file: called twice
    in a row it reads ``compiling`` the second time and declines. It fails only here, where
    both compilers read ``approved`` before either writes. Then the claim's ``WHERE
    status='approved'`` is the only thing left standing between one approval and two
    bundles, and an implementation without it hands the plan to both.
    """
    approve_plan(db_session, feasible_plan.id, user_id=SUBJECT, approved_by=SUBJECT)
    db_session.commit()

    with Session(db_engine) as first:
        claimed = claim_plan_for_compile(first, feasible_plan.id, user_id=SUBJECT)
        assert claimed is not None and claimed.status == "compiling"
        loser = _contend(
            db_engine, first, lambda s: claim_plan_for_compile(s, feasible_plan.id, user_id=SUBJECT)
        )

    assert loser is None, "two compilers claimed one approved plan — that is two bundles"
    db_session.commit()
    assert _status(db_session, feasible_plan.id) == "compiling"


# The restart proof runs in a *torn-down interpreter*, so it cannot accidentally observe
# in-process state. It writes the whole lifecycle up to the approval and exits; the parent
# then reads the row through a brand-new engine.
_APPROVE_THEN_EXIT = """
import sys, uuid
from shapely.geometry import box
from geoalchemy2.shape import from_shape
from sqlalchemy.orm import Session

from commons.db import SRID, Area, create_db_engine
from commons.models import ItineraryV1
from commons.repository import approve_plan, create_plan, record_feasibility

engine = create_db_engine()
with Session(engine) as session:
    area = Area(polygon=from_shape(box(28.216, 36.440, 28.232, 36.451), srid=SRID),
                created_by="google-sub-a")
    session.add(area)
    session.flush()
    itinerary = ItineraryV1.model_validate({
        "id": str(uuid.uuid4()), "user_id": "google-sub-a", "area_id": str(area.id),
        "date": "2026-08-14", "lang": "en",
        "budgets": {"walking_m": 4000.0, "hours": 4.0},
    })
    plan = create_plan(session, itinerary)
    record_feasibility(session, plan.id, user_id="google-sub-a", feasible=True, violations=[])
    outcome = approve_plan(session, plan.id, user_id="google-sub-a", approved_by="google-sub-a")
    session.commit()
sys.stdout.write(f"{plan.id} {outcome.plan.approved_at.isoformat()}")
"""


@pytest.mark.integration
def test_an_approval_survives_a_process_restart(db_session: Session, postgis_url: str) -> None:
    """SC-003 = 100%, proved by **actually killing the process** rather than mocking one.

    ADR-0023's claim is that the guarantee is structural: the approval was never held in
    memory, so there is nothing for a restart to lose. A test that re-used this interpreter
    would prove only that a session can re-read its own commit. This one hands the work to a
    fresh Python, waits for it to exit, and reads the row through a new connection.
    """
    result = subprocess.run(
        [sys.executable, "-c", _APPROVE_THEN_EXIT],
        cwd=REPO_ROOT,
        env={**os.environ, DATABASE_URL_ENV: postgis_url},
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr
    raw_id, raw_approved_at = result.stdout.split()

    # The writer is gone. Nothing in this interpreter has ever seen that plan.
    survivor = load_plan(db_session, UUID(raw_id), user_id=SUBJECT)
    assert survivor is not None
    assert survivor.status == "approved"
    assert survivor.approved_by == SUBJECT
    assert survivor.approved_at == datetime.fromisoformat(raw_approved_at).astimezone(UTC)
    # And the surviving approval is what compile consumes — the gate is genuinely open.
    claimed = claim_plan_for_compile(db_session, survivor.id, user_id=SUBJECT)
    assert claimed is not None and claimed.status == "compiling"
