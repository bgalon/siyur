# AGENTS.md — `scripts/`

Nested override for work in this package; extends the root `AGENTS.md` (read that first).

**Scope:** operator entry points, not application code. `dev.sh` (start/stop the local stack),
`fetch-basemap.sh` (regenerate the dev tiles + glyphs), `try_it.py`, `permission_report.py`.

**Invariants enforced here:**
- **A script encodes what cost real time by hand**, and says so in its header. `dev.sh`'s
  comments exist because each one is a bug someone already hit — pinned ports (Vite silently
  relocates and the *other* server answers with a `200` SPA fallback, so a status-code check
  passes against a broken map), `stop` keeping the volume, migrations reaching head.
- **Output is generated, never committed.** `fetch-basemap.sh` writes to `web/dev-assets/`,
  which is gitignored. Fix the script, never hand-edit its output.
- **Degrade loudly, fail only when it matters.** A missing basemap warns and the stack still
  starts; a migration that does not reach head is fatal. Match that judgement — never print a
  success line over a traceback.
- **No place is baked in.** `fetch-basemap.sh --bbox=…` takes any area; Rhodes is a default.
- **`set -euo pipefail`, and mind the traps it sets.** `lsof` exits 1 on "no match" and
  `gh pr checks` exits non-zero whenever a check is not passing — both abort a script under
  `pipefail` unless handled (`|| true`, or `--watch`).
- **Never read or write `.env*` or `secrets/`.** Dev defaults are inline `${VAR:-default}`,
  matching `docker-compose.yml`; real credentials come from the process environment.
