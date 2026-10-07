#!/usr/bin/env bash
# congestion.sh — inject (or remove) a bottleneck on the N6 link so you can watch congestion in Grafana.
#   scripts/congestion.sh on [rate_mbit=10]   limit the UPF's egress towards the internet (token-bucket filter)
#   scripts/congestion.sh off                 remove it
#   scripts/congestion.sh status              show the qdisc
# Then run:  scripts/traffic.sh udp-up 40 30   and watch the "N6 qdisc" row / ask the intent console.
#
# Why the UPF and not the server? In this docker topology N3 and N6 share the UPF's eth0. Uplink packets
# the UPF *forwards* to the app-server leave through that eth0, exactly like a router's egress queue: when
# it is slower than the arrival rate the queue fills and packets are dropped. (Shaping the app-server's
# OWN egress instead only back-pressures the local iperf3 sender — nothing is ever dropped.)
source "$(dirname "$0")/lib.sh"
case "${1:-status}" in
  on)  r=${2:-10}
       docker exec ibg-upf tc qdisc replace dev eth0 root tbf rate "${r}mbit" burst 32kbit latency 200ms \
         && ok "N6 bottleneck ON: ${r} Mbit/s (tbf) on the UPF's eth0 egress" ;;
  off) docker exec ibg-upf tc qdisc del dev eth0 root 2>/dev/null || true; ok "N6 bottleneck OFF" ;;
  status) docker exec ibg-upf tc -s qdisc show dev eth0 ;;
  *) sed -n '2,7p' "$0"; exit 1 ;;
esac
