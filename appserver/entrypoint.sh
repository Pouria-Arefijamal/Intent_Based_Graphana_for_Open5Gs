#!/bin/sh
# Route the UE address pool back through the UPF (needed for server-initiated flows such as ping),
# then run four iperf3 servers (one test per server at a time) on 5201-5204.
set -e
if [ -n "${UPF_IP:-}" ] && [ -n "${UE_POOL:-}" ]; then
  ip route replace "$UE_POOL" via "$UPF_IP" || echo "WARN: could not add route to UE pool"
fi
for p in 5202 5203 5204; do iperf3 -s -p "$p" >/dev/null 2>&1 & done
exec iperf3 -s -p 5201
