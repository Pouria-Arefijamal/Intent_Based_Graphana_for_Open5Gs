#!/usr/bin/env bash
# ibg.sh — the single entry point. Usage:  scripts/ibg.sh <command>
#   setup     one-time prerequisites check + fetch/build the 5G images
#   up        start everything in the right order and attach the UE
#   down      stop everything (data volumes are kept)
#   destroy   stop everything AND delete volumes (subscriber DB, Prometheus history, Grafana state)
#   status    show containers + whether the UE is attached
#   logs <s>  follow the logs of one service (e.g. amf, upf, intent-engine)
#   urls      print the web addresses
#   setkey    store your Gemini API key (typed silently, saved only in .env) and restart the intent engine
#   test      run the end-to-end acceptance test
source "$(dirname "$0")/lib.sh"

# run `docker compose up -d <args>` with the (long) build output going to runtime/compose-up.log
quiet_up() {
  mkdir -p runtime
  if ! dc up -d "$@" >runtime/compose-up.log 2>&1; then
    tail -25 runtime/compose-up.log >&2; die "docker compose up failed (full log: runtime/compose-up.log)"
  fi
}

urls() {
  local b; b=$(envval BIND_ADDR); b=${b:-127.0.0.1}; [ "$b" = "0.0.0.0" ] && b=localhost
  cat <<EOT

  Grafana (dashboards)    http://$b:$(envval GRAFANA_PORT)     login admin / <GRAFANA_ADMIN_PASSWORD in .env>
     → "Open5GS Observatory"  and  "Intent Console"
  Intent console (direct) http://$b:$(envval INTENT_PORT)
  Prometheus              http://$b:$(envval PROMETHEUS_PORT)
  cAdvisor                http://$b:$(envval CADVISOR_PORT)
  Open5GS WebUI           http://$b:$(envval WEBUI_PORT)     (admin / 1423)
EOT
}

case "${1:-help}" in
  setup)   bash scripts/setup.sh ;;
  up)
    [ -d third_party/docker_open5gs ] || bash scripts/setup.sh
    [ -f .env ] || cp .env.example .env
    for i in docker_open5gs docker_ueransim; do docker image inspect "$i:latest" >/dev/null 2>&1 || die "image $i missing — run scripts/ibg.sh setup"; done
    mkdir -p runtime/log
    say "1/4 database"
    quiet_up --build mongo
    wait_for "MongoDB healthy" 90 bash -c "[ \"\$(docker inspect -f '{{.State.Health.Status}}' ibg-mongo)\" = healthy ]" || die "mongo not healthy"
    bash scripts/provision_subscriber.sh
    if command -v python3 >/dev/null 2>&1; then   # point the embedded chat at the port/host from .env
      h=$(envval PUBLIC_HOST); python3 grafana/build_dashboard.py grafana/dashboards/open5gs_observatory.json \
        --intent-url "http://${h:-localhost}:$(envval INTENT_PORT)" >/dev/null || warn "could not regenerate the dashboard"
    fi
    say "2/4 5G core + RAN + app server + monitoring"
    quiet_up --build --scale ue=0
    say "3/4 waiting for the core network functions to register"
    sleep 8
    say "4/4 attaching the UE"
    bash scripts/attach_ue.sh
    ok "Everything is up."; urls ;;
  down)    dc down --remove-orphans ;;
  destroy) dc down -v --remove-orphans ;;
  status)
    dc ps --format 'table {{.Name}}\t{{.Status}}\t{{.Ports}}'
    docker exec ibg-ue ip -4 -o addr show uesimtun0 2>/dev/null | awk '{print "UE tunnel:", $2, $4}' || true ;;
  logs)    shift; dc logs -f --tail=100 "$@" ;;
  urls)    urls ;;
  setkey)
    [ -f .env ] || cp .env.example .env
    printf 'Paste your Gemini API key (input is hidden; empty = switch to the offline rules engine): '
    read -rs KEYVAL; echo
    # write with python so the key never appears on a command line (ps) or in shell history
    GEMINI_KEY_VALUE="$KEYVAL" python3 - <<'PYEOF'
import os, re
v = os.environ["GEMINI_KEY_VALUE"].strip()
s = open(".env").read()
s = re.sub(r"^GEMINI_API_KEY=.*$", lambda m: "GEMINI_API_KEY=" + v, s, flags=re.M) if re.search(r"^GEMINI_API_KEY=", s, re.M) else s + "\nGEMINI_API_KEY=" + v + "\n"
open(".env", "w").write(s)
os.chmod(".env", 0o600)
PYEOF
    unset KEYVAL
    ok "saved to .env (git-ignored)"
    if docker inspect ibg-intent-engine >/dev/null 2>&1; then
      dc up -d --no-deps --force-recreate intent-engine >runtime/setkey.log 2>&1 && ok "intent engine restarted with the new key" || warn "restart failed, see runtime/setkey.log"
    fi ;;
  test)    bash scripts/e2e_test.sh ;;
  *)       sed -n '2,12p' "$0" ;;
esac
