<!-- Siyur PR template (ADR-0005). One branch per unit of work: agent/<ticket>-<slug>. -->

## Summary
<!-- What changed and why, in 2–4 lines. -->

## Links
- Unit / ticket: <!-- DU-NN · issue # · spec -->
- ADR(s): <!-- e.g. ADR-0004; write /adr if this PR made a decision -->
- Devlog: <!-- docs/devlog/YYYY-MM-DD-*.md if decision-bearing -->

## Type
<!-- one: docs · spike (throwaway, spike/) · feature · chore · fix -->

## Tests / evals
<!-- Which tiers ran (T1 unit · T2 integration+component · T3 airplane-mode e2e) and evals (structural / trajectory). For a DU, confirm its DoD. -->

## Course-feed
- Exhibit-tag candidate(s): <!-- exhibit/<U#>-<slug>, proposed for Ben -->

## Checklist
- [ ] Conventional-commit title; `Co-Authored-By:` model trailer on commits
- [ ] No secrets / `.env` touched; nothing `bundleable=false` enters a bundle
- [ ] Decision made → ADR (`/adr`); failure hit → FAIL-NNN + regression eval (`/failure`)
- [ ] Geo APIs use the pinned majors (no stale idioms) — if code touches geo
- [ ] (DU only) EARS criteria verified · named test tiers green · devlog entry
