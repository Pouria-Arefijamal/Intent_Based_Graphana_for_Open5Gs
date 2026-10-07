# Intent-Based Grafana for Open5GS

**Ask a 5G network questions in plain English and get answers from real measurements.**

This project builds a complete, working 5G network on one computer — a 5G core ([Open5GS](https://open5gs.org)),
a simulated phone and base station ([UERANSIM](https://github.com/aligungr/UERANSIM)), a web server on the
"internet" side — and wraps it with a monitoring system ([Prometheus](https://prometheus.io) +
[Grafana](https://grafana.com)). On top sits an **intent engine**: you type something like

> *"Is the UPF forwarding everything the UE sends? Compare in vs out traffic."*

and the engine (using Google's **free Gemini** model, or an offline rule-based fallback) turns that sentence into
safe database queries, runs them **through Grafana**, computes the statistics itself, asks the model to explain
them, and creates a Grafana dashboard for your question.

> **Written for students.** You do not need to know 5G, Docker or Grafana beforehand. Read §2 first
> ("5G in five minutes"); every unfamiliar word is in [docs/GLOSSARY.md](docs/GLOSSARY.md).

---

## Contents
1. [What you get](#1-what-you-get)
2. [5G in five minutes](#2-5g-in-five-minutes)
3. [What you need](#3-what-you-need)
4. [Quick start](#4-quick-start)
5. [The containers — what each one does](#5-the-containers--what-each-one-does)
6. [Tour of Grafana and how the metrics are measured](#6-tour-of-grafana-and-how-the-metrics-are-measured)
7. [The Intent Console (natural-language questions)](#7-the-intent-console-natural-language-questions)
8. [Experiments to try](#8-experiments-to-try)
9. [Measuring things yourself in Grafana](#9-measuring-things-yourself-in-grafana)
10. [Testing — how we know it works](#10-testing--how-we-know-it-works)
11. [How this repo was built: master/worker agents](#11-how-this-repo-was-built-masterworker-agents)
12. [Security notes](#12-security-notes)
13. [Repository layout](#13-repository-layout)
14. [Troubleshooting, glossary, licences](#14-troubleshooting-glossary-licences)

---

## 1. What you get

| Piece | What it is |
|---|---|
| **A real 5G Standalone network** | 11 Open5GS network functions (+ the admin WebUI) and MongoDB, one UERANSIM gNB (base station) and one UE (phone). The phone really registers, authenticates, gets an IP address and sends data through GTP-U tunnels to the UPF. |
| **A data server** | `app-server` runs iperf3 so you can push measurable traffic through the 5G path. |
| **Metrics from every container** | Native Open5GS metrics (AMF, SMF, UPF, PCF), per-container CPU/memory, per-interface traffic counters at four points on the path, queue (qdisc) backlog/drops on the N6 link, container and service health. |
| **Grafana dashboards** | *Open5GS Observatory* (35 panels in 6 rows, ready-made) and *Intent Console* (ask questions). |
| **Intent engine** | Natural language → validated PromQL → executed through Grafana → statistics → explanation → new dashboard. Works with free Gemini, and **also with no AI at all** (rules engine). |
| **Tests** | Unit tests + an end-to-end test that compares Grafana's numbers with iperf3's own measurement. |

---

## 2. 5G in five minutes

A mobile network has three parts:

```
 phone ──radio──▶ base station ──▶ 5G CORE ──▶ the internet
 (UE)             (gNB)            (many small programs)    (here: app-server)
```

* **UE** (User Equipment) – the phone. Here a *simulated* one (`ue` container).
* **gNB** – the base station (`gnb` container). The radio is simulated in software, so no hardware is needed.
* **5G Core (5GC)** – the brain. It is split into small programs called **Network Functions (NFs)**:
  * **AMF** – front door: registers the phone, keeps track of where it is.
  * **AUSF / UDM / UDR + MongoDB** – "is this SIM real?": check the secret key stored for the SIM.
  * **SMF** – sets up the phone's data session (**PDU session**) and tells the UPF what to do.
  * **UPF** – the **data pipe**. Every packet the phone sends or receives passes through it. This is the part we measure most.
  * **PCF** – rules/policies. **NSSF** – network slices. **BSF** – session binding.
  * **NRF** – the phone book: NFs register there and look each other up. **SCP** – a router for NF-to-NF messages.
* **Control plane vs user plane** – control messages (signalling) set things up; *user plane* is the actual data. The UPF carries the user plane inside **GTP-U** tunnels.

Numbered interfaces you will see: **N2** (gNB↔AMF signalling), **N3** (gNB↔UPF data tunnel), **N4** (SMF↔UPF control, protocol PFCP), **N6** (UPF↔internet).

**Uplink** = phone → internet. **Downlink** = internet → phone.

**What "intent-based" means:** instead of learning a query language you state *what you want to know* (your *intent*); the system works out *how* to find out.

---

## 3. What you need

* A Linux machine (Ubuntu recommended), **x86-64 or ARM64**, ≥ 8 GB RAM, ≥ 20 GB free disk, internet access. Everything is built from source on your own machine, so both CPU types should work — but it has so far been **tested only on an NVIDIA Jetson (ARM64, Ubuntu 22.04-based)**.
* **Docker Engine + Docker Compose v2** (`docker compose version` must work; v2.24 or newer).
  ```bash
  # Ubuntu quick install
  curl -fsSL https://get.docker.com | sh
  sudo usermod -aG docker $USER      # then LOG OUT and back in
  ```
* `git`, `python3` (only used by the test script — standard library only).
* Optional: a **free Gemini API key** from <https://aistudio.google.com/apikey>. Without it everything still works; the intent engine falls back to its offline rules engine.

---

## 4. Quick start

```bash
git clone https://github.com/Pouria-Arefijamal/Intent_Based_Graphana_for_Open5Gs.git
cd Intent_Based_Graphana_for_Open5Gs

scripts/ibg.sh setup     # checks Docker, fetches Open5GS packaging (pinned commit), builds the 5G images
                         # FIRST TIME ONLY: compiling Open5GS takes 15–40 minutes. Go get coffee.

nano .env                # (optional) paste your key:  GEMINI_API_KEY=...   — .env is git-ignored
scripts/ibg.sh up        # starts everything and attaches the UE (~2 minutes)
```

When it finishes you will see the web addresses:

| What | URL (defaults) |
|---|---|
| **Grafana** (login `admin` / `admin`, or just browse as viewer) | <http://localhost:3000> → dashboards *Open5GS Observatory*, *Intent Console* |
| Intent console (stand-alone page) | <http://localhost:8088> |
| Prometheus | <http://localhost:9090> |
| cAdvisor | <http://localhost:8081> |
| Open5GS WebUI (subscriber admin) | <http://localhost:9999> (`admin` / `1423`) |

> Ports taken already? Change `GRAFANA_PORT`, `PROMETHEUS_PORT`, … in `.env`, then `scripts/ibg.sh up` again.

Generate some traffic so the dashboard has something to show:

```bash
scripts/traffic.sh udp-up 40 30      # 40 Mbit/s uplink for 30 s through the 5G tunnel
scripts/traffic.sh tcp-down 20       # TCP downlink for 20 s
```

Other commands: `scripts/ibg.sh status | logs <service> | urls | test | down | destroy`.

---

## 5. The containers — what each one does

Full per-container explanation (IP, ports, metrics, "what breaks if it stops"): **[docs/CONTAINERS.md](docs/CONTAINERS.md)**. Summary:

| Group | Containers | Role |
|---|---|---|
| Database | `mongo` | Stores the SIM (IMSI, key, OPc, allowed slice/DNN). Provisioned by `scripts/provision_subscriber.sh`. |
| Core – control plane | `nrf` `scp` `ausf` `udm` `udr` `pcf` `nssf` `bsf` `amf` `smf` | Registration, authentication, policy, slicing, session management. |
| Core – user plane | `upf` | Forwards user packets between the gNB tunnel (N3) and the internet (N6) via the `ogstun` TUN interface; NAT. |
| Admin | `webui` | Open5GS web UI to add/edit subscribers. |
| RAN (simulated) | `gnb`, `ue` | UERANSIM base station and phone. The UE has the tunnel interface `uesimtun0` and iperf3. |
| "Internet" | `app-server` | iperf3 servers (the "internet"). |
| Monitoring | `prometheus` `grafana` `cadvisor` `node-exporter` `exporter` | Collect, store and show metrics. `exporter` is our own: it measures what nothing else does. |
| Intelligence | `intent-engine` | The natural-language layer. |

Network: `172.30.0.0/24` (name `ibg_net`). The UE gets `192.168.100.x` inside the 5G network.

---

## 6. Tour of Grafana and how the metrics are measured

### 6.1 Where does each number come from?

```
 Open5GS AMF/SMF/UPF/PCF ──:9091/metrics──┐
 exporter (ours)  ───────────:9200/metrics─┼─▶ Prometheus (stores, every 5 s) ─▶ Grafana (draws) ─▶ you
 cAdvisor / node-exporter ───────────────┘                                          ▲
                                                                                    └─ intent-engine asks Grafana
```

* **Prometheus** *scrapes* (HTTP GET) each target every 5 seconds and stores the numbers as time series.
* Two kinds of metric: a **gauge** is a value now (e.g. registered UEs = 1); a **counter** only ever grows (e.g. bytes transferred). For counters you ask for the **rate**: `rate(x[30s])` = increase per second over the last 30 seconds.
* **PromQL** is Prometheus's query language. Example — uplink bit rate at the UE:
  `rate(ibg_iface_tx_bytes_total{service="ue",iface="uesimtun0"}[30s]) * 8`   (bytes → bits).

### 6.2 What is exposed, and by whom

| Source | Examples | Notes |
|---|---|---|
| **Open5GS native** (AMF, SMF, UPF, PCF only — the only NFs that have a metrics endpoint in v2.8.0) | `fivegs_amffunction_rm_registeredsubnbr` (registered UEs), `amf_session`, `gnb`, `ran_ue`, `pfcp_sessions_active` (SMF), `ues_active`, `fivegs_upffunction_upf_sessionnbr` | **Caveat found while building this:** the UPF packet counters `fivegs_ep_n3_gtp_*datapkt` stay at 0 even under 40 Mbit/s of traffic (they are stubs), so we never use them. |
| **Our exporter** (`ibg_…`) | `ibg_container_up`, `ibg_nf_sbi_up` (can we connect to the NF's port 7777?), `ibg_container_cpu_seconds_total`, `ibg_container_memory_bytes`, `ibg_iface_{rx,tx}_{bytes,packets,drop}_total` for UE, gNB, UPF and server, `ibg_qdisc_*` (queue backlog/drops on N6), `ibg_path_up`, `ibg_path_rtt_ms` | It reads `/proc/net/dev` and `tc -s qdisc` *inside* the containers through the Docker API. A value it cannot read is **left out**, never reported as 0. |
| **cAdvisor, node-exporter** | host CPU/memory/network | Container *names* are not available from cAdvisor on every host, so per-NF numbers come from our exporter instead. |
| **Intent engine** | `ibg_intent_requests_total`, … | The engine monitors itself too. |

The other Open5GS NFs (NRF, UDM, …) have no metrics endpoint; they are covered by container CPU/memory and by the `ibg_container_up` / `ibg_nf_sbi_up` health checks.

### 6.3 Measuring traffic *in* and *out* — the key idea

We watch the same traffic at **four points**, so a difference between two points tells you where packets were lost:

```
 uplink   UE tx (uesimtun0) ─▶ gNB eth0 ─▶ UPF rx (ogstun) ─▶ app-server rx (eth0)
 downlink app-server tx    ─▶ UPF tx (ogstun) ─▶ gNB eth0 ─▶ UE rx (uesimtun0)
```

If the three uplink numbers are equal, nothing is lost. If `app-server rx` ≪ `UE tx`, packets are dropped
somewhere in between (for example by a congested N6 queue — see §8). The tiny excess you see (~2–4 %) is real:
interface counters include IP/UDP headers while iperf3 reports payload only.

### 6.4 The *Open5GS Observatory* dashboard

Rows from top to bottom (every panel has an "i" description in Grafana):

1. **Overview** – green/red tile per container, registered UEs, PDU sessions (AMF = SMF = UPF should all be 1), connected gNBs, data path up/RTT.
2. **User plane — in vs out** – uplink and downlink at each measurement point, delivery ratio (%), UPF packet rate, drops.
3. **Core native metrics** – AMF registrations, SMF sessions/PFCP, Open5GS endpoints up.
4. **Containers** – CPU and memory per container, UPF CPU, network per container.
5. **N6 qdisc** – queue backlog and drops on the link to the "internet".
6. **Host** – CPU/memory/network of your computer.

Time range (top right) and the auto-refresh (5 s) are yours to change.

---

## 7. The Intent Console (natural-language questions)

Open Grafana → dashboard **Intent Console** (or <http://localhost:8088>). Click an example or type your own question and press *Analyse*.

### 7.1 What happens when you press the button

```
 your sentence
     │ 1  PLAN      Gemini picks metrics + writes PromQL (or the rules engine maps keywords → vetted recipes)
     │ 2  GUARD     every query is checked: known metrics only, read-only, length/range limits, no wildcards
     │ 3  EXECUTE   queries run THROUGH Grafana's datasource proxy (so it uses exactly what a dashboard panel would)
     │ 4  MEASURE   the engine itself computes min/mean/p95/max/trend and in-vs-out ratios (the AI never does arithmetic)
     │ 5  EXPLAIN   Gemini gets only those numbers and writes verdict + findings + recommendations
     ▼ 6  DASHBOARD a new Grafana dashboard "intent-…" with one panel per query + the explanation, link returned
```

Safety by design:
* **Your sentence is data, not instructions.** It cannot change the allow-list, the target, or the system prompt.
* **Read-only queries.** Data is read only with `query_range` through Grafana's datasource proxy (plus listing metric names from Prometheus). The only thing the engine *writes* is its own dashboards in Grafana's `Intent-Based` folder. No metric outside the allow-list (live Prometheus names ∪ catalog) is accepted; `{__name__=~".+"}` wildcards, `@`/comments, runaway sub-queries and huge `offset`s are rejected. User/AI text placed in dashboards is escaped (HTML *and* Markdown) because Grafana's sanitiser is switched off for the console iframe.
* **No AI? No problem.** With no key, with an exhausted quota, or when Gemini errors, the engine answers with the **rules engine** and says so (`engine: rules`). If you send nonsense it answers with a clear error (HTTP 400/413/422), and if Grafana itself is down with 502 — but a broken AI never breaks it.
* **The AI cannot invent numbers.** All figures come from Prometheus; the AI only words them.
* Your key lives only in `.env` (git-ignored) and is sent only to Google as a request header. It is never logged or returned.

### 7.2 Things to ask

* *Is the UPF forwarding everything the UE sends? Compare in vs out traffic for the last 10 minutes.*
* *Is any network function down or overloaded?*
* *How many UEs are registered and how many PDU sessions are active?*
* *Is there congestion on the N6 link?*
* *Which container uses the most CPU?*
* *What is the round-trip time through the 5G tunnel?*

The same function is available as an API:

```bash
curl -s localhost:8088/api/intent -H 'Content-Type: application/json' \
  -d '{"intent":"Is there congestion on the N6 link?","range_minutes":10}' | python3 -m json.tool
```

Details: [docs/INTENT_ENGINE.md](docs/INTENT_ENGINE.md). Contract between all components: [docs/SPEC.md](docs/SPEC.md).

---

## 8. Experiments to try

**A. Normal traffic.** `scripts/traffic.sh udp-up 40 60`, then open *User plane — in vs out*. The three uplink lines overlap at ≈ 41 Mbit/s. Ask the console: *"Is the UPF forwarding everything the UE sends?"* → balanced, OK.

**B. Create congestion.**
```bash
scripts/congestion.sh on 10            # token-bucket limit: 10 Mbit/s on the UPF's egress towards the internet (N6)
scripts/traffic.sh udp-up 40 30        # the UE tries to send 40 Mbit/s
```
iperf3 reports ~75 % loss. In Grafana the *UE tx* and *UPF rx* lines stay at ≈ 41 Mbit/s but *app-server rx* drops to ≈ 10:
the packets are lost **between the UPF and the server**, and *N6 qdisc → drops/s* shows exactly where. Ask the console:
*"Is the UPF forwarding everything the UE sends?"* → loss; *"Is there congestion on the N6 link?"* → WARN/CRIT.
Then `scripts/congestion.sh off`.

**C. Many flows.** `scripts/traffic.sh tcp-up 30 8` (8 parallel TCP streams) and compare CPU and RTT with 1 stream.

**D. Break something.** `docker stop ibg-pcf` → the tile for `pcf` turns red, ask *"Is any network function down?"*; `docker start ibg-pcf` to fix.

**E. Add a subscriber.** Open the WebUI, add an IMSI, and extend `stack/ue/ueransim-ue.yaml` for a second UE (advanced).

---

## 9. Measuring things yourself in Grafana

1. Grafana → **Explore** → datasource *Prometheus*.
2. Type a metric name; autocomplete lists every `ibg_…` and Open5GS metric.
3. Try: `rate(ibg_iface_rx_bytes_total{service="upf",iface="ogstun"}[30s])*8`
4. Click **Add to dashboard** to keep it, set *Unit → bits/sec* for readability.
5. Useful building blocks:
   * rate of a counter → `rate(metric[30s])`
   * per-NF CPU % of one core → `rate(ibg_container_cpu_seconds_total[30s])*100`
   * who is busiest → `topk(3, rate(ibg_container_cpu_seconds_total[30s]))`
   * packet loss between two points → `1 - (server_rx / ue_tx)` using the expressions above.

---

## 10. Testing — how we know it works

```bash
# Unit tests (no Docker stack, no network, no API key needed) – run inside a clean Python container:
docker run --rm -v "$PWD":/w -w /w python:3.12-slim sh -c \
  "pip install -q -r intent_engine/requirements.txt -r exporter/requirements.txt pytest && python -m pytest tests/unit -q"

# End-to-end (needs the stack up):
scripts/ibg.sh test
```

The end-to-end test is designed so it **can fail**. It:

1. checks every Prometheus target, every container, every SBI port, 1 registered UE, 1 session at AMF = SMF = UPF;
2. pushes **real iperf3 traffic** (40 Mbit/s UDP uplink, 30 s) and compares what Grafana reports at the UE, the UPF and the server **against iperf3's own measurement** (±10 %), and the three points against each other (±5 %);
3. asks the intent engine (live Gemini when a key is configured) whether the UPF forwards everything, and checks the engine's deterministic in-vs-out ratio, verdict, and the dashboard it created;
4. feeds it hostile input (empty, oversized, prompt-injection with `{__name__=~".+"}` and a request for the API key);
5. creates a **10 Mbit/s bottleneck** at the UPF's N6 egress, offers 40 Mbit/s uplink, and checks that iperf3 itself sees ≈ 10 Mbit/s and ~75 % loss, that Prometheus sees the drops and the server receives only ≈ 25 % of what the UE sent, that the engine's deterministic comparison says **loss** and its analysis is WARN/CRIT — then removes the bottleneck and checks traffic flows freely again;
6. scans the whole repository tree and the logs of the engine, Grafana, Prometheus and exporter containers to prove the API key leaked nowhere (only the git-ignored `.env` holds it).

Results of the last run are in `test_output/e2e_report.json` (git-ignored). The verified results from the author's machine are in [docs/TEST_REPORT.md](docs/TEST_REPORT.md).

---

## 11. How this repo was built: master/worker agents

The repo was built with a **cost-aware multi-agent workflow** that is itself packaged as a reusable
Claude Code skill: [`.claude/skills/master-worker-agents/SKILL.md`](.claude/skills/master-worker-agents/SKILL.md).

* An **Opus "master"** plans, writes the contract ([docs/SPEC.md](docs/SPEC.md)), verifies and reviews.
* **Sonnet "workers"** implement independent components in parallel (intent engine; exporter + dashboards).
* A **Haiku "scout"** does mechanical work (glossary, container docs).
* Nobody's word is trusted: the master re-runs every acceptance test; a fresh Opus reviewer audits the result.

Want to share the dashboard with the world? See [grafana/community/README.md](grafana/community/README.md) (ready-made export + upload steps for grafana.com).

Agent definitions: [`.claude/agents/`](.claude/agents). Copy the `.claude/` folder into any project to reuse the pattern.

---

## 12. Security notes

* All web ports are bound to **127.0.0.1** by default (`BIND_ADDR` in `.env`). Do not set `0.0.0.0` on an untrusted network.
* Change `GRAFANA_ADMIN_PASSWORD` in `.env` **before the first `scripts/ibg.sh up`** if the machine is shared (Grafana stores it in its volume on first start; to change it later run `scripts/ibg.sh destroy` first, or change it in Grafana's UI **and** in `.env`, otherwise the intent engine can no longer log in and answers 502). Anonymous *Viewer* access is on so the console iframe works without login.
* The `exporter` container mounts `/var/run/docker.sock` read-only. Read-only mount ≠ read-only API: anything that can talk to the exporter's container could control Docker. It is on a private network and runs fixed code, but treat this as a **lab** setup, not production.
* Several containers (`upf`, `gnb`, `ue`, `cadvisor`) run `privileged` because 5G tunnelling and cgroup access need it.
* The UE's SIM key/OPc in `config/open5gs.env` are the public Open5GS *test* values — not secrets.
* Your Gemini key: only in `.env`. If you ever paste a key into a chat, an issue or a screenshot, **revoke it** at <https://aistudio.google.com/apikey> and create a new one.

---

## 13. Repository layout

```
docker-compose.yml        the whole testbed (22 services)
.env.example              copy to .env (git-ignored): key, passwords, ports
config/open5gs.env        network/PLMN/SIM settings shared by the 5G containers (non-secret)
scripts/                  ibg.sh (entry point), setup.sh, provision_subscriber.sh, attach_ue.sh,
                          traffic.sh, congestion.sh, e2e_test.sh
stack/ue/                 UE image (adds iperf3) + UERANSIM UE config (OPc credentials)
appserver/                iperf3 "internet" server image
exporter/                 custom Prometheus exporter
prometheus/               scrape configuration
grafana/                  provisioning + dashboard generator + generated dashboard JSON
  community/              share-ready export for grafana.com + publishing guide
intent_engine/            natural-language → PromQL → analysis service (FastAPI)
tests/unit, tests/e2e     tests
docs/                     SPEC, CONTAINERS, GLOSSARY, INTENT_ENGINE, TROUBLESHOOTING, TEST_REPORT
.claude/                  master/worker agent skill + agent definitions
third_party/              (created by setup.sh, git-ignored) pinned Open5GS docker packaging
```

---

## 14. Troubleshooting, glossary, licences

* **[docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)** – every problem hit while building this and its fix.
* **[docs/GLOSSARY.md](docs/GLOSSARY.md)** – 5G, networking, Prometheus/Grafana terms.
* This repository's code: MIT ([LICENSE](LICENSE)). Open5GS (AGPL-3.0), UERANSIM (GPL-3.0), docker_open5gs (BSD-2) and the monitoring images keep their own licences; they are fetched/built on your machine and **not** redistributed here.
* Pinned versions: Open5GS v2.8.0 (`f87da61`), UERANSIM v3.2.6, docker_open5gs `1f3bcf7`, Prometheus 2.54.1, Grafana 11.2.0, cAdvisor 0.49.1, node_exporter 1.8.2, MongoDB 6.0.
