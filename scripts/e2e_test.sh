#!/usr/bin/env bash
# e2e_test.sh — end-to-end acceptance test (needs the stack up: scripts/ibg.sh up).
# Pushes real iperf3 traffic through the 5G path and checks that Grafana's data matches iperf3's own
# numbers, then checks the intent engine's analysis against that ground truth.
# Options are passed to the python script, e.g.  scripts/e2e_test.sh --skip-congestion --engine rules
source "$(dirname "$0")/lib.sh"
docker inspect -f '{{.State.Running}}' ibg-ue >/dev/null 2>&1 || die "stack is not running — start it with: scripts/ibg.sh up"
exec python3 tests/e2e/e2e_check.py "$@"
