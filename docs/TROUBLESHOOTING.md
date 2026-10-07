# Troubleshooting

Every problem below was actually hit while building this project.

| Symptom | Cause | Fix |
|---|---|---|
| `cannot talk to Docker` / `permission denied ... docker.sock` | your user is not in the `docker` group | `sudo usermod -aG docker $USER`, then **log out and back in** |
| `ibg-gnb` keeps showing `Restarting (0)` | the upstream start script ends in an interactive `bash`; without a TTY it exits and Docker restarts it | already handled (`tty: true`, `stdin_open: true` in `docker-compose.yml`). If you edited the compose file, keep those two lines |
| `attach_ue.sh`: *gNB never connected to the AMF* | the AMF was not ready yet, or an NF crashed | `docker logs ibg-amf`, `docker logs ibg-nrf`; then `docker compose ... restart gnb` (or `scripts/ibg.sh down && scripts/ibg.sh up`) |
| `attach_ue.sh`: *UE did not get a PDU session* | subscriber missing or wrong key | `scripts/provision_subscriber.sh`; check `docker logs ibg-ue \| tail -30` and `docker logs ibg-amf \| grep -i auth` |
| ping over the tunnel fails | missing route in the UE / UPF NAT not set | `docker exec ibg-ue ip route` must contain `172.30.0.99 dev uesimtun0`; `docker exec ibg-upf iptables -t nat -S` |
| You restarted/recreated the `ibg-ue` container and now traffic bypasses 5G (UE tx = 0 in Grafana) or the UPF counts 2 sessions | the route through the tunnel is added by `attach_ue.sh` (not stored in the container) and the old session lingers in the UPF | `scripts/ibg.sh down && scripts/ibg.sh up` (clean), or at least `scripts/attach_ue.sh` |
| Iperf3 runs but Grafana shows **0** | the UE talked to the server through `eth0`, bypassing 5G | same route check as above — the `/32` route through `uesimtun0` is what forces traffic into the tunnel |
| Grafana panels *No data* right after start | Prometheus needs a few scrapes (5 s each) and `rate()` needs ≥ 2 samples | wait ~30 s |
| Per-NF CPU is empty in **cAdvisor** | on hosts using the containerd/overlayfs image store, cAdvisor exposes no container `name` label | this project does not depend on it: CPU/memory per NF come from the custom exporter (`ibg_container_cpu_seconds_total`) |
| `fivegs_ep_n3_gtp_indatapktn3upf` is always 0 | those Open5GS v2.8 counters are stubs | use `ibg_iface_*` (interface counters); the dashboard already does |
| Port already in use on `up` | another service uses 3000 / 9090 / 8081 / 9100 | change `GRAFANA_PORT`, `PROMETHEUS_PORT`, … in `.env` |
| Intent console says `engine: rules` | no key, quota exhausted, or every model failed | run `scripts/ibg.sh setkey`, or put a key in `.env` (`GEMINI_API_KEY=`), then `docker compose --env-file config/open5gs.env --env-file .env up -d intent-engine`; `docker logs ibg-intent-engine` shows which model failed (never the key) |
| Gemini `404 ... no longer available` | model names are retired over time | edit `GEMINI_MODELS` in `.env` (list with `curl -H "x-goog-api-key: $KEY" https://generativelanguage.googleapis.com/v1beta/models`) |
| `docker build` of Open5GS takes forever | it compiles from source | normal: 15–40 min the first time; it is cached afterwards |
| Everything is slow / CPU at 100 % | UERANSIM simulates the radio in software; the UE process crosses one full CPU core between ~250 and ~500 Mbit/s (measured on an ARM64 Jetson) | keep tests ≤ 100 Mbit/s |
| Want a clean slate | | `scripts/ibg.sh destroy && scripts/ibg.sh up` (deletes the subscriber DB, Prometheus history and Grafana state) |
| The *Ask the network* row at the top of the dashboard is empty | the intent engine is not running, or its port differs from the one the dashboard was generated with | `scripts/ibg.sh up` regenerates the dashboard with the port from `.env`; check `docker logs ibg-intent-engine` and <http://localhost:8088> |
