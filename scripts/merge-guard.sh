#!/usr/bin/env bash
# merge-guard.sh — the guardrail for FAIL-009.
#
# `main` went red twice in two days because a PR was merged with failing checks. Neither
# operator was ignorant of the rule; both had checked. The check lied:
#
#     gh pr checks 92 --watch | tail -4
#
# `gh pr checks` sorts failures ABOVE passes, so `tail -4` showed four green rows and hid
# two red ones. A human-readable status command was used as a machine gate.
#
# This script is the machine gate. It reads JSON, counts, and exits non-zero. There is
# nothing to truncate and nothing to eyeball.
#
# Two independent conditions, because FAIL-009 and PR #93 showed two different ways to be
# wrong:
#   1. no check may be non-passing   — the failure that merged #92
#   2. every required job must be PRESENT — a job that never ran is not a job that passed,
#      and a missing signal reads as innocent (#93's stale-base run)
#
# Usage:  scripts/merge-guard.sh <pr-number>
#         scripts/merge-guard.sh --json <file>   # offline, for tests/test_merge_guard.py
set -euo pipefail

# The seven gates AGENTS.md requires green before a merge. Matched as a prefix ("1 · "),
# because the display names carry descriptions that change.
REQUIRED_PREFIXES=("1 ·" "2 ·" "3 ·" "4 ·" "5 ·" "6 ·" "7 ·")

usage () { echo "usage: $0 <pr-number> | $0 --json <file>" >&2; exit 2; }
[ $# -ge 1 ] || usage

if [ "$1" = "--json" ]; then
  [ $# -eq 2 ] || usage
  [ -r "$2" ] || { echo "merge-guard: cannot read $2" >&2; exit 2; }
  PAYLOAD=$(cat "$2")
  LABEL="payload $2"
else
  PR="$1"
  # NOTE: `gh pr checks` exits non-zero whenever any check is not passing. That is exactly
  # what we are measuring, so the exit status must not abort us here (22 recorded tool
  # failures in this repo came from that under `set -e`).
  PAYLOAD=$(gh pr checks "$PR" --json name,state 2>/dev/null || true)
  LABEL="PR #$PR"
fi

if [ -z "${PAYLOAD//[[:space:]]/}" ] || [ "$PAYLOAD" = "[]" ]; then
  echo "BLOCKED: $LABEL reported no checks at all. No checks is not the same as passing." >&2
  exit 1
fi

failing=$(printf '%s' "$PAYLOAD" | jq '[.[] | select(.state != "SUCCESS")] | length')
passing=$(printf '%s' "$PAYLOAD" | jq '[.[] | select(.state == "SUCCESS")] | length')

status=0

if [ "$failing" -ne 0 ]; then
  echo "BLOCKED: $LABEL has $failing non-passing check(s):" >&2
  printf '%s' "$PAYLOAD" | jq -r '.[] | select(.state != "SUCCESS") | "  \(.state)\t\(.name)"' >&2
  status=1
fi

# Presence, independently of state.
missing=()
for prefix in "${REQUIRED_PREFIXES[@]}"; do
  if ! printf '%s' "$PAYLOAD" | jq -e --arg p "$prefix" 'any(.[]; .name | startswith($p))' >/dev/null; then
    missing+=("$prefix")
  fi
done

if [ ${#missing[@]} -ne 0 ]; then
  echo "BLOCKED: $LABEL is missing required job(s): ${missing[*]}" >&2
  echo "  A job that never ran is not a job that passed." >&2
  status=1
fi

if [ "$status" -eq 0 ]; then
  echo "OK: $LABEL — $passing passing, 0 failing, all seven required jobs present."
fi
exit "$status"
