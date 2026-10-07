#!/usr/bin/env bash
# traffic.sh — generate REAL traffic through the 5G path (UE → gNB → UPF → app-server) with iperf3.
#   scripts/traffic.sh tcp-up   [secs=20] [streams=1]    TCP, uplink  (UE sends)
#   scripts/traffic.sh tcp-down [secs=20] [streams=1]    TCP, downlink (server sends)
#   scripts/traffic.sh udp-up   <Mbit/s> [secs=20]        UDP at a fixed rate, uplink
#   scripts/traffic.sh udp-down <Mbit/s> [secs=20]        UDP at a fixed rate, downlink
# Extra iperf3 flags can be appended via IPERF_EXTRA (e.g. IPERF_EXTRA=-J for JSON).
source "$(dirname "$0")/lib.sh"
APP=$(envval APP_SERVER_IP); PORT=${IPERF_PORT:-5201}
mode=${1:-}; shift || true
case "$mode" in
  tcp-up)   secs=${1:-20}; n=${2:-1};  args=(-t "$secs" -P "$n") ;;
  tcp-down) secs=${1:-20}; n=${2:-1};  args=(-t "$secs" -P "$n" -R) ;;
  udp-up)   rate=${1:?need Mbit/s}; secs=${2:-20}; args=(-u -b "${rate}M" -t "$secs") ;;
  udp-down) rate=${1:?need Mbit/s}; secs=${2:-20}; args=(-u -b "${rate}M" -t "$secs" -R) ;;
  *) sed -n '2,8p' "$0"; exit 1 ;;
esac
say "iperf3 $mode  (UE → $APP:$PORT through the 5G tunnel)"
# shellcheck disable=SC2086
docker exec ibg-ue iperf3 -c "$APP" -p "$PORT" "${args[@]}" -f m ${IPERF_EXTRA:-}
