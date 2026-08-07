# AGENTS.md — `tests/`

Nested override for work in this package; extends the root `AGENTS.md` (read that first).

**Scope:** the Python test tiers (`docs/design/test-strategy.md`). **Tier 1** — unit, pure,
fast, no I/O: `uv run pytest tests/ -q`, CI job 2. **Tier 2** — integration/component against
real PostGIS, marked `@integration`: `uv run pytest -q -m integration`, CI job 3. Exit code 5
("no tests collected") is a pass, not a failure.

**Invariants enforced here:**
- **Tests never hit the live world.** No Overture cloud release, no Overpass, no Anthropic.
  Fixtures are captured under `tests/fixtures/` and reviewed by their provenance notes, not
  line by line (which is why `diff-guard` excludes them).
- **Every behaviour change ships a Tier-1 test in the same change.** A fix with no test is
  incomplete — and every entry in `docs/failures/` owes a regression test or guardrail before
  it closes.
- **Guardrails must be proved to bite.** A test that would pass with the bug reintroduced is
  not a regression test. FAIL-005 is the standard: the behavioural suite passed **35/35** with
  the defect back in place, and only the AST tripwire caught it — so the tripwire was kept.
- **Assert the mechanism, not just the outcome.** The agentevals superset matcher is
  order-blind: moving `resolve_area` after the research loop left the trajectory eval green.
  Order needed its own assertion.
- **`tests/test_geo_api_pins.py` and `tests/test_llm_seam.py` are tripwires, not unit tests.**
  They fail CI on stale geo APIs and on any provider SDK imported above `commons/llm.py`.
  Do not relax them to make a change pass — the change is what is wrong.
- **Fixture-derived success is "rehearsed", not proven.** SC-005's genericity claim needs an
  *unrehearsed* area; say which one a test actually demonstrates.
