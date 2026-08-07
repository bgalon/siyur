#!/usr/bin/env bash
#
# Start / stop the local dev stack: PostGIS + the API + the web dev server.
#
#   scripts/dev.sh start     bring everything up and print how to sign in
#   scripts/dev.sh stop      stop the API and web server, leave the database up
#   scripts/dev.sh stop --purge   …and drop the database volume (loses researched data)
#   scripts/dev.sh status    what is actually running right now
#   scripts/dev.sh cookie    print a fresh dev session cookie
#   scripts/dev.sh logs [api|web]   tail a log
#
# This encodes the things that cost real time when done by hand:
#
#   - **Ports are pinned, not negotiated.** Vite silently moves to the next free
#     port when its own is taken, and the *other* server on the original port
#     then answers with its SPA fallback — HTML, status 200. A status-code check
#     passes against that, so the map appears broken while the API is fine.
#     `--strictPort` makes the collision loud instead, and `start` refuses to
#     run at all if either port is already occupied.
#   - **`stop` keeps your data.** `docker compose down -v` drops the volume, and
#     a research pass costs ~40s of live Overture + Overpass to rebuild. Use
#     `--purge` when you actually mean it.
#   - **Signing in needs no Google project.** The API authenticates with a signed
#     session cookie; this mints one with the same mechanism `/auth/callback`
#     uses, against the dev secret you are already running with.
#
# Nothing here is a substitute for docs/TRY-IT.md — that is the source of truth
# for what works and what does not. This is the same commands, in order.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

RUN_DIR="$REPO_ROOT/.dev"
API_PORT="${SIYUR_API_PORT:-8000}"
WEB_PORT="${SIYUR_WEB_PORT:-5173}"
DB_PORT="${SIYUR_DB_PORT:-5432}"
# Dev-only default, matching docker-compose.yml's own `${VAR:-default}` style.
# Real credentials never live in a file (AGENTS.md / Constitution Article V).
export SIYUR_SESSION_SECRET="${SIYUR_SESSION_SECRET:-devsecret}"
export SIYUR_DATABASE_URL="${SIYUR_DATABASE_URL:-postgresql+psycopg://siyur:siyur@localhost:${DB_PORT}/siyur}"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
warn() { printf '\033[33m%s\033[0m\n' "$*" >&2; }
die() { printf '\033[31merror: %s\033[0m\n' "$*" >&2; exit 1; }

# Empty output and success when nothing is listening. `lsof` exits 1 on "no
# match", which under `set -o pipefail` would otherwise abort the script every
# time a port is legitimately free.
port_pid() { lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null | head -1 || true; }

require_free_port() {
  local port="$1" label="$2" busy
  busy="$(port_pid "$port")"
  if [[ -n "$busy" ]]; then
    die "port $port ($label) is in use by pid $busy — stop it, or set $3."
  fi
}

# Kill a pidfile's process, but only if it is still the process we started —
# PIDs are recycled, and killing a stranger's process would be worse than
# leaving ours running.
stop_pidfile() {
  # Declared separately: referring to `name` inside the same `local` statement
  # that declares it is unset at that point under `set -u`.
  local name="$1"
  local marker="$2"
  local pidfile="$RUN_DIR/$name.pid"
  local pid
  [[ -f "$pidfile" ]] || return 0
  pid="$(cat "$pidfile")"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    if ps -p "$pid" -o command= 2>/dev/null | grep -q "$marker"; then
      pkill -TERM -P "$pid" 2>/dev/null || true
      kill -TERM "$pid" 2>/dev/null || true
      echo "  stopped $name (pid $pid)"
    else
      warn "  $name pid $pid is no longer ours — left alone"
    fi
  fi
  rm -f "$pidfile"
}

cmd_cookie() {
  uv run python - <<'PY'
import base64
import json
import os

from itsdangerous import TimestampSigner

from api.security import SESSION_USER_KEY

payload = {SESSION_USER_KEY: {"sub": "dev-local-user", "email": "dev@example.test", "name": "Dev"}}
raw = base64.b64encode(json.dumps(payload).encode())
print(TimestampSigner(os.environ["SIYUR_SESSION_SECRET"]).sign(raw).decode())
PY
}

cmd_start() {
  mkdir -p "$RUN_DIR"

  # Refuse rather than negotiate. A "helpfully" relocated port is the failure
  # mode this whole script exists to prevent.
  require_free_port "$API_PORT" api SIYUR_API_PORT
  require_free_port "$WEB_PORT" web SIYUR_WEB_PORT
  # 5432 is a popular port — another project's Postgres (or a Supabase stack)
  # will claim it. Docker's own message for this is an opaque "failed to set up
  # container networking … Bind for 0.0.0.0:5432 failed", after it has already
  # created a network and a volume, so catch it here instead.
  if ! docker compose ps --status running --quiet postgis 2>/dev/null | grep -q .; then
    require_free_port "$DB_PORT" postgis SIYUR_DB_PORT
  fi

  bold "1/5  PostGIS"
  docker compose up -d >/dev/null
  # Wait on compose's own healthcheck, not a bare `pg_isready`. The postgres
  # entrypoint runs a temporary bootstrap server while it creates the database,
  # so `pg_isready` (which defaults to the `postgres` database) answers "ready"
  # in a window when `siyur` does not exist yet — migrations then fail against a
  # database that is up but empty. The compose healthcheck names `-d siyur`.
  local waited=0 cid
  cid="$(docker compose ps -q postgis)"
  [[ -n "$cid" ]] || die "postgis container did not start — try: docker compose logs postgis"
  until [[ "$(docker inspect -f '{{.State.Health.Status}}' "$cid" 2>/dev/null)" == "healthy" ]]; do
    ((waited += 1))
    [[ $waited -gt 60 ]] && die "PostGIS unhealthy after 60s — try: docker compose logs postgis"
    sleep 1
  done
  echo "  healthy on :$DB_PORT after ${waited}s"

  bold "2/5  Migrations"
  # No pipe, no `|| true`: a failed migration must stop the script, not print
  # "schema at head" over the top of a traceback. `set -e` does the rest.
  uv run alembic upgrade head 2>&1 | grep -E "Running upgrade|Context impl" || true
  uv run alembic current 2>/dev/null | grep -q "(head)" ||
    die "migrations did not reach head — run: uv run alembic upgrade head"
  echo "  schema at head"

  bold "3/5  Basemap"
  # Dev-only vector tiles. Gitignored and machine-produced, so a fresh clone has
  # none — fetch on demand rather than making every checkout carry ~3 MB of
  # binaries. Missing tiles are not fatal but they are visible: the style still
  # loads and paints its own background, so you get flat grey with no streets and
  # no features, while markers, delimit, research and the sheet all work normally.
  # That is a degraded map, not a broken stack, so a failed fetch warns and
  # carries on rather than refusing to start.
  if [[ -f web/dev-assets/tiles/rhodes.pmtiles ]]; then
    echo "  present ($(du -h web/dev-assets/tiles/rhodes.pmtiles | cut -f1))"
  elif ! command -v pmtiles >/dev/null; then
    warn "  pmtiles CLI not installed — map renders flat grey, no streets. brew install pmtiles, then scripts/fetch-basemap.sh"
  elif scripts/fetch-basemap.sh >/dev/null 2>&1; then
    echo "  fetched ($(du -h web/dev-assets/tiles/rhodes.pmtiles | cut -f1))"
  else
    warn "  basemap fetch failed — map renders flat grey, no streets. Retry: scripts/fetch-basemap.sh"
  fi

  bold "4/5  API"
  # shellcheck disable=SC2086
  nohup uv run uvicorn api.app:app --port "$API_PORT" >"$RUN_DIR/api.log" 2>&1 &
  echo $! >"$RUN_DIR/api.pid"
  waited=0
  until curl -fsS "http://localhost:$API_PORT/healthz" >/dev/null 2>&1; do
    ((waited += 1))
    [[ $waited -gt 45 ]] && die "API did not answer /healthz in 45s — see $RUN_DIR/api.log"
    sleep 1
  done
  echo "  http://localhost:$API_PORT — /healthz ok after ${waited}s"

  bold "5/5  Web"
  # --strictPort: fail loudly on a collision instead of moving to the next port
  # and letting whatever holds this one answer with its SPA fallback.
  nohup pnpm -C web dev --port "$WEB_PORT" --strictPort >"$RUN_DIR/web.log" 2>&1 &
  echo $! >"$RUN_DIR/web.pid"
  waited=0
  until curl -fsS "http://localhost:$WEB_PORT/" >/dev/null 2>&1; do
    ((waited += 1))
    [[ $waited -gt 45 ]] && die "web dev server did not start in 45s — see $RUN_DIR/web.log"
    sleep 1
  done
  echo "  http://localhost:$WEB_PORT — proxying /areas /sites /me to the API"

  local cookie
  cookie="$(cmd_cookie)"
  echo
  bold "Sign in"
  echo "  In the browser console at http://localhost:$WEB_PORT, then reload:"
  echo
  echo "    document.cookie = \"session=$cookie; path=/\""
  echo
  echo "  For curl:  export COOKIE='$cookie'"
  echo
  bold "Research an area (any area — nothing is hardcoded per place)"
  cat <<EOF
    curl -s --cookie "session=\$COOKIE" -H 'content-type: application/json' \\
      -d '{"bbox":[28.222,36.4405,28.229,36.4465]}' localhost:$API_PORT/areas

    curl -sN --cookie "session=\$COOKIE" -H 'content-type: application/json' \\
      -d '{"force_refresh":false}' localhost:$API_PORT/areas/<area_id>/research

EOF
  warn "  Use bbox, not name: the divisions lookup behind \`name\` takes ~73s (see docs/TRY-IT.md)."
  echo
  echo "  Stop with: scripts/dev.sh stop"
}

cmd_stop() {
  local purge=0
  [[ "${1:-}" == "--purge" ]] && purge=1
  bold "Stopping"
  # Markers match the *recorded* pid's own command line, which is the wrapper we
  # launched (`pnpm`, `uv`), not the child it execs (`vite`, `uvicorn`).
  stop_pidfile web "pnpm"
  stop_pidfile api "uvicorn"
  # A pnpm/vite tree can outlive its parent; clear anything still holding the port.
  local leftover
  leftover="$(port_pid "$WEB_PORT")"
  if [[ -n "$leftover" ]]; then
    kill -TERM "$leftover" 2>/dev/null || true
    echo "  cleared :$WEB_PORT (pid $leftover)"
  fi
  if [[ $purge -eq 1 ]]; then
    docker compose down -v >/dev/null 2>&1 || true
    echo "  database stopped and volume dropped — the next start re-researches from scratch"
  else
    docker compose stop >/dev/null 2>&1 || true
    echo "  database stopped, data kept (use --purge to drop it)"
  fi
}

port_state() {
  local pid
  pid="$(port_pid "$1")"
  if [[ -n "$pid" ]]; then echo "up (pid $pid)"; else echo "down"; fi
}

cmd_status() {
  printf 'api    :%-5s %s\n' "$API_PORT" "$(port_state "$API_PORT")"
  printf 'web    :%-5s %s\n' "$WEB_PORT" "$(port_state "$WEB_PORT")"
  if docker compose ps --status running --quiet postgis 2>/dev/null | grep -q .; then
    printf 'db     :%-5s up\n' "$DB_PORT"
  else
    printf 'db     :%-5s down\n' "$DB_PORT"
  fi
}

cmd_logs() {
  local which="${1:-api}"
  [[ -f "$RUN_DIR/$which.log" ]] || die "no log at $RUN_DIR/$which.log — is it running?"
  tail -f "$RUN_DIR/$which.log"
}

case "${1:-}" in
  start) shift; cmd_start "$@" ;;
  stop) shift; cmd_stop "$@" ;;
  status) cmd_status ;;
  cookie) cmd_cookie ;;
  logs) shift; cmd_logs "$@" ;;
  *)
    echo "usage: scripts/dev.sh {start|stop [--purge]|status|cookie|logs [api|web]}" >&2
    exit 2
    ;;
esac
