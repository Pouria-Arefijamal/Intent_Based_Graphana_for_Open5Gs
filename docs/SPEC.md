# SPEC — contract between components

This file is the single source of truth that every component is written against.
If code and SPEC disagree, fix the code (or ask the master to amend the SPEC) — never silently diverge.

## 1. Topology

Docker compose project name: `ibg`. Network `ibg_net`, subnet `172.30.0.0/24`, bridge.
Container names are `ibg-<service>`. Compose service names (also DNS names on the network):

| service | IP | role label | notes |
|---|---|---|---|
| mongo | .2 | core | subscriber DB |
| smf | .7 | core | native metrics `:9091` |
| upf | .8 | core | native metrics `:9091`; interfaces `eth0` (N3+N4+N6 share it in docker), `ogstun` (user-plane TUN) |
| amf | .10 | core | native metrics `:9091` |
| ausf .11, nrf .12, udm .13, udr .14, pcf .27 (metrics `:9091`), nssf .28, bsf .29, scp .35, webui .26 | core | SBI on `:7777` (webui: `:9999`) |
| gnb | .23 | ran | UERANSIM gNB; iface `eth0` |
| ue | .24 | ran | UERANSIM UE; iface `eth0`, tunnel `uesimtun0` (UE IP from 192.168.100.0/24) |
| app-server | .99 | data | iperf3 server + sink; iface `eth0` (this is the **N6** side); has `tc` |
| prometheus | .240 | monitoring | `:9090` |
| grafana | .241 | monitoring | `:3000` |
| cadvisor | .242 | monitoring | `:8080` |
| node-exporter | .243 | monitoring | `:9100` |
| exporter | .244 | monitoring | `:9200` custom exporter (§3) |
| intent-engine | .245 | monitoring | `:8088` (§4) |

Host-published ports (all bound to `${BIND_ADDR:-127.0.0.1}`), configurable in `.env`:
`GRAFANA_PORT` (3000), `PROMETHEUS_PORT` (9090), `INTENT_PORT` (8088), `CADVISOR_PORT` (8081),
`NODE_EXPORTER_PORT` (9100), `WEBUI_PORT` (9999).

Data path: `ue(uesimtun0) → gnb → upf(eth0 N3) → upf(ogstun) → upf(eth0 N6) → app-server(eth0)`.
Direction convention: **uplink** = UE → app-server; **downlink** = app-server → UE.
For the TUN device `ogstun` in the UPF: kernel `rx_bytes` = uplink (user space wrote into it),
kernel `tx_bytes` = downlink.

## 2. Prometheus

`prometheus/prometheus.yml`, global `scrape_interval: 5s`, `evaluation_interval: 5s`. Jobs:
`open5gs` (targets `amf:9091 smf:9091 upf:9091 pcf:9091`), `cadvisor` (`cadvisor:8080`),
`node` (`node-exporter:9100`), `ibg_exporter` (`exporter:9200`), `intent_engine` (`intent-engine:8088`),
`prometheus` (`localhost:9090`). **Verified live (Open5GS v2.8.0): the UPF GTP-U packet counters `fivegs_ep_n3_gtp_indatapktn3upf/outdatapktn3upf` stay 0 under real traffic — they are stubs; never use them for throughput. User-plane in/out comes from interface counters (§3).** Only AMF/SMF/UPF/PCF expose native Open5GS metrics in v2.8.0; the other
NFs are covered by the exporter's per-container CPU/memory (`ibg_container_cpu_seconds_total`, `ibg_container_memory_bytes`, label `service`) and by
`ibg_container_up` / `ibg_nf_sbi_up`. **cAdvisor is kept for host-level cgroup data but must NOT be used for per-NF queries: verified on the dev host (Docker overlayfs/containerd snapshotter) that it exposes no `name` label for containers.**

Grafana datasource: name `Prometheus`, **uid `ibg-prometheus`**, url `http://prometheus:9090`, default.

## 3. Custom exporter (`exporter/`, port 9200, path `/metrics`)

Python, `prometheus_client` text format. Uses the Docker SDK over `/var/run/docker.sock` (mounted read-only).
Finds containers by label `com.docker.compose.project=ibg` and `com.docker.compose.service=<service>`.
A metric whose source is unreadable is **omitted**, never emitted as 0. Collection cached ≥2 s so scrapes are cheap.

| metric | type | labels | meaning |
|---|---|---|---|
| `ibg_container_up` | gauge | `service`,`role` | 1 if container running else 0 (emit 0 if the container exists but is stopped) |
| `ibg_container_cpu_seconds_total` | counter | `service` | cumulative CPU time of the container, from the Docker stats API (`cpu_stats.cpu_usage.total_usage`/1e9; use `stats(stream=False, one_shot=True)`, collected in the background thread every ~5 s, containers in parallel). `rate(...)*100` = % of one core |
| `ibg_container_memory_bytes` | gauge | `service` | working-set memory (usage minus inactive_file/cache when present) from the same call |
| `ibg_nf_sbi_up` | gauge | `service` | 1 if TCP connect to `<service>:7777` succeeds (nrf scp ausf udr udm pcf nssf bsf amf smf) |
| `ibg_iface_rx_bytes_total` / `_tx_bytes_total` / `_rx_packets_total` / `_tx_packets_total` / `_rx_drop_total` / `_tx_drop_total` | counter | `service`,`iface` | from `/proc/net/dev` inside the container (`docker exec cat /proc/net/dev`) for: upf{eth0,ogstun}, gnb{eth0}, ue{eth0,uesimtun0}, app-server{eth0} |
| `ibg_qdisc_backlog_packets`, `ibg_qdisc_backlog_bytes` | gauge | `service`,`dev`,`kind` | `tc -s qdisc show dev eth0` in **both `upf` and `app-server`** (the UPF's eth0 egress is the real N6 egress for forwarded uplink traffic — verified: a bottleneck on the *app-server's own* egress only back-pressures the local iperf3 sender and never produces drops, whereas a bottleneck on forwarded traffic does) |
| `ibg_qdisc_sent_bytes_total`, `ibg_qdisc_drops_total`, `ibg_qdisc_overlimits_total` | counter | `service`,`dev`,`kind` | same source |
| `ibg_path_up` | gauge | – | 1 if `ping -I uesimtun0 -c1 -W1 <app-server ip>` inside `ue` succeeds (refreshed every 10 s, cached) |
| `ibg_path_rtt_ms` | gauge | – | RTT of that ping (omit when path down) |
| `ibg_ue_session_up` | gauge | – | 1 if `uesimtun0` exists in `ue` |
| `ibg_exporter_scrape_seconds` | gauge | – | duration of the last collection |
| `ibg_exporter_errors_total` | counter | `collector` | collector failures |

Env: `EXPORTER_PORT=9200`, `COMPOSE_PROJECT=ibg`, `APP_SERVER_IP=172.30.0.99`.

## 4. Intent engine (`intent_engine/`, FastAPI, port 8088)

Purpose: turn a natural-language **intent** ("Is the UPF forwarding everything the UE sends? Show in vs out
traffic for the last 10 minutes") into (1) a validated, read-only set of PromQL queries, (2) executed
**through Grafana's datasource proxy**, (3) deterministic statistics, (4) an LLM-written analysis that may
only use those numbers, (5) a Grafana dashboard for the intent.

Env:
`GEMINI_API_KEY` (optional), `GEMINI_MODELS` (default `gemini-3.5-flash-lite,gemini-flash-lite-latest,gemini-flash-latest`),
`GRAFANA_URL` (default `http://grafana:3000`), `GRAFANA_USER`/`GRAFANA_PASSWORD` (default admin/admin) **or** `GRAFANA_TOKEN`,
`GRAFANA_DS_UID` (default `ibg-prometheus`), `PUBLIC_GRAFANA_URL` (browser-visible, default `http://localhost:3000`),
`PUBLIC_INTENT_URL` (default `http://localhost:8088`), `PROM_URL` (only used to list metric names; default `http://prometheus:9090`),
`INTENT_PORT=8088`.

Without `GEMINI_API_KEY` (or when every Gemini model fails) the engine falls back to the **rules engine**
(keyword → recipe mapping + template analysis) and says so (`engine:"rules"`). It must never crash because the LLM is down.

### Routes
- `GET /` → single-file HTML console (textarea, example intents, result view, link to dashboard). No external CDN.
- `GET /healthz` → `{"status":"ok","gemini_configured":bool,"grafana_reachable":bool,"prometheus_reachable":bool}`
- `GET /api/catalog` → `{"recipes":[{id,title,unit,description,promql}],"metrics":[{name,description}]}`
- `POST /api/intent` body `{"intent":str,"range_minutes":int=15,"create_dashboard":bool=true,"engine":"auto|gemini|rules"="auto"}`
  → 200 JSON:
```json
{
 "intent_id":"i-20261007-123456-ab12",
 "engine":"gemini:gemini-flash-latest | rules",
 "intent":"...", "range_minutes":15, "window":{"start":1791000000,"end":1791000900},
 "plan":{"goal":"...","queries":[{"id":"ue_uplink_bps","title":"UE uplink","promql":"...","unit":"bps","kind":"timeseries"}]},
 "results":[{"id":"...","title":"...","promql":"...","unit":"bps","ok":true,"error":null,
             "stats":{"n":180,"min":0,"mean":1.2e7,"p95":2e7,"max":2.4e7,"last":1e7,"slope_per_min":0.0,"nonzero_fraction":0.9}}],
 "comparisons":[{"a":"ue_uplink_bps","b":"server_rx_bps","ratio_b_over_a":0.97,"verdict":"balanced"}],
 "analysis":{"verdict":"OK|WARN|CRIT|INFO","summary":"...","findings":["..."],"recommendations":["..."]},
 "dashboard":{"uid":"intent-i-...","url":"http://localhost:3000/d/intent-i-..."} ,
 "warnings":["..."]
}
```
  Errors: 400 empty/oversized intent (>1000 chars), 413 body > 16 KB, 422 schema or no valid query could be built, 502 if Grafana is unreachable or rejects the engine's credentials (message says so). Host header must be allow-listed (TrustedHost).
- `GET /metrics` → Prometheus metrics: `ibg_intent_requests_total{engine,outcome}`, `ibg_intent_duration_seconds` histogram,
  `ibg_intent_llm_errors_total{model}`.

### Guardrails (must be implemented and unit-tested)
1. LLM output must parse as JSON matching the plan schema; ≤ 6 queries; titles ≤ 80 chars.
2. Every PromQL expression is checked **before execution**: max 500 chars; only identifiers that are in the metric
   allow-list (live `/api/v1/label/__name__/values` ∪ catalog) or PromQL functions/keywords/label names; reject `{__name__=~...}`
   selectors, reject empty-matcher selectors that would select everything, reject unbalanced brackets. Read-only by construction
   (only `query_range` is ever called).
3. Range clamp: 1 ≤ `range_minutes` ≤ 360; step chosen so ≤ 240 points per series; rate windows ≥ 4×5s=20s.
4. One repair round: if Prometheus rejects a query, send the error back to the LLM once; otherwise record `ok:false` and continue.
5. Analysis prompt contains only computed stats, never raw intent text as instructions; the LLM is told "use only numbers provided".
   Post-check: numbers in `summary` that are not derivable from the stats are not rejected but the response carries `warnings`
   if the LLM output could not be parsed (then fall back to the deterministic template analysis).
6. Intent text is data, not instructions: it can never change the system prompt, allow-list, or Grafana target.

### Recipes (the catalog; also used by the rules engine). `rate()` window 30s.
| id | promql | unit |
|---|---|---|
| `ue_uplink_bps` | `rate(ibg_iface_tx_bytes_total{service="ue",iface="uesimtun0"}[30s])*8` | bps |
| `ue_downlink_bps` | `rate(ibg_iface_rx_bytes_total{service="ue",iface="uesimtun0"}[30s])*8` | bps |
| `upf_uplink_bps` | `rate(ibg_iface_rx_bytes_total{service="upf",iface="ogstun"}[30s])*8` | bps |
| `upf_downlink_bps` | `rate(ibg_iface_tx_bytes_total{service="upf",iface="ogstun"}[30s])*8` | bps |
| `server_rx_bps` | `rate(ibg_iface_rx_bytes_total{service="app-server",iface="eth0"}[30s])*8` | bps |
| `server_tx_bps` | `rate(ibg_iface_tx_bytes_total{service="app-server",iface="eth0"}[30s])*8` | bps |
| `gnb_rx_bps` / `gnb_tx_bps` | gnb eth0 rx/tx | bps |
| `upf_cpu_pct` | `rate(ibg_container_cpu_seconds_total{service="upf"}[30s])*100` | percent |
| `nf_cpu_pct` | `rate(ibg_container_cpu_seconds_total[30s])*100` | percent |
| `nf_mem_bytes` | `ibg_container_memory_bytes` | bytes |
| `nf_up` | `ibg_container_up` | bool |
| `sbi_up` | `ibg_nf_sbi_up` | bool |
| `registered_ues` | `fivegs_amffunction_rm_registeredsubnbr` | count |
| `pdu_sessions_amf` | `amf_session` | count |
| `pdu_sessions_smf` | `pfcp_sessions_active` | count |
| `smf_ues_active` | `ues_active` | count |
| `pdu_sessions_upf` | `fivegs_upffunction_upf_sessionnbr` | count |
| `connected_gnbs` | `gnb` | count |
| `qdisc_backlog_pkts` | `ibg_qdisc_backlog_packets` | pkts |
| `qdisc_drops_per_s` | `rate(ibg_qdisc_drops_total[30s])` | pps |
| `path_up` | `ibg_path_up` | bool |
| `path_rtt_ms` | `ibg_path_rtt_ms` | ms |
| `upf_ogstun_drops` | `rate(ibg_iface_rx_drop_total{service="upf",iface="ogstun"}[30s])+rate(ibg_iface_tx_drop_total{service="upf",iface="ogstun"}[30s])` | pps |

Comparison pairs (in-vs-out analysis, computed deterministically): `(ue_uplink_bps → upf_uplink_bps → server_rx_bps)` and
`(server_tx_bps → upf_downlink_bps → ue_downlink_bps)`. `ratio = mean(b)/mean(a)` over the window where `mean(a) > 1 kbps`;
verdict `balanced` if 0.9 ≤ ratio ≤ 1.1, `loss` if < 0.9, `amplified` if > 1.1 (GTP-U encapsulation/ACK overhead — flagged), `idle` if no traffic.
Note: baseline protocol chatter (ICMP probes ~ a few kbps) is below the `idle` threshold of 10 kbps.

### Dashboards created by the engine (via Grafana HTTP API `POST /api/dashboards/db`, `overwrite:true`)
- Per intent: uid `intent-<intent_id>`, folder "Intent-Based", tags `["ibg","intent"]`, time range = intent range, one timeseries/stat
  panel per query (datasource uid `ibg-prometheus`, correct unit mapping `bps→bps`, `percent→percent`, `bytes→bytes`, `bool→none` ...),
  plus a text panel (markdown) with the analysis.
- At startup (retry up to 120 s until Grafana is reachable): upsert dashboard uid `ibg-intent-console` titled "Intent Console":
  one full-width text panel containing an `<iframe src="${PUBLIC_INTENT_URL}/" ...>` (needs Grafana `disable_sanitize_html` + embedding; set in compose).

## 5. Grafana (`grafana/`)

Provisioned: datasource (above), dashboard provider reading `/var/lib/grafana/dashboards`, dashboard
`grafana/dashboards/open5gs_observatory.json` (uid `ibg-observatory`, title "Open5GS Observatory") generated by
`grafana/build_dashboard.py` (pure python, no deps; re-runnable, deterministic). Rows: **Overview** (NF up grid,
registered UEs, PDU sessions, gNBs, path up/RTT), **User plane in vs out** (UE/UPF/app-server uplink & downlink, loss ratio),
**Core native metrics** (AMF/SMF/UPF/PCF), **Containers** (CPU/mem/net per NF from the custom exporter — NOT cAdvisor), **N6 qdisc**, **Host**.
Compose sets `GF_SECURITY_ALLOW_EMBEDDING=true`, `GF_PANELS_DISABLE_SANITIZE_HTML=true`, anonymous Viewer enabled.

## 6. File ownership (one owner per file)

| owner | files |
|---|---|
| intent engine | `intent_engine/**`, `tests/unit/test_engine_*.py`, `docs/INTENT_ENGINE.md` |
| observability (exporter, Prometheus, Grafana) | `exporter/**`, `prometheus/prometheus.yml`, `grafana/**`, `tests/unit/test_exporter_*.py` |
| documentation (glossary, containers) | `docs/GLOSSARY.md`, `docs/CONTAINERS.md` |
| integration (compose, scripts, e2e, README) | `docker-compose.yml`, `config/**`, `scripts/**`, `appserver/**`, `tests/e2e/**`, `README.md`, `.gitignore`, `docs/SPEC.md` |

## 7. Acceptance

- Unit: `python3 -m pytest tests/unit -q` passes with no network and no Docker.
- Integration (master): `scripts/e2e_test.sh` (stack must already be up: `scripts/ibg.sh up`) — pushes real iperf3 traffic through the 5G path, checks the dashboard
  numbers against iperf3's own figures, runs a real Gemini intent, checks the created dashboard.
