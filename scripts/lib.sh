#!/usr/bin/env bash
# lib.sh — helpers shared by all scripts. Source it; do not run it.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# `dc` = docker compose with both env files (non-secret network config + local .env)
dc() { docker compose --env-file config/open5gs.env --env-file .env "$@"; }

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m ✔\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m ! \033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m ✘ %s\033[0m\n' "$*" >&2; exit 1; }

# read one variable from .env / config/open5gs.env (no shell eval, no secrets echoed)
# (a missing key returns an empty string and success — with `set -e -o pipefail` a failing grep would
#  otherwise kill the calling script silently)
envval() { grep -hE "^$1=" .env config/open5gs.env 2>/dev/null | tail -1 | cut -d= -f2- || true; }

# wait_for "<description>" <timeout-seconds> <command...>  — retries every 2 s
wait_for() {
  local what="$1" timeout="$2"; shift 2
  local t=0
  until "$@" >/dev/null 2>&1; do
    t=$((t+2)); [ "$t" -ge "$timeout" ] && { warn "timeout waiting for: $what"; return 1; }
    sleep 2
  done
  ok "$what"
}
