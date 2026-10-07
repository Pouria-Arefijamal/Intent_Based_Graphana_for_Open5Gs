# Test report

Generated from the real result files of the last two consecutive runs of `scripts/e2e_test.sh`
(`test_output/e2e_report.json`). Nothing here is hand-written except the commentary.

| | |
|---|---|
| Host | NVIDIA Jetson, ARM64, 14 cores, 122 GB RAM, Ubuntu-based, Docker Compose v5.5.1 |
| Stack | Open5GS v2.8.0 (`f87da61`), UERANSIM v3.2.6, Prometheus 2.54.1, Grafana 11.2.0, 1 gNB + 1 UE |
| LLM | Google Gemini free tier via the Developer API (`gemini-3.5-flash-lite` answered; the engine falls back to other models, then to the rules engine) |
| Unit tests | **268 passed** (`pytest tests/unit`, no network, no Docker, no key) |
| End-to-end, run 1 | **37/37 passed** (2026-10-07 13:01:55) |
| End-to-end, run 2 (back to back) | **37/37 passed** (2026-10-07 13:05:17) |
| Clean-slate start | `scripts/ibg.sh destroy` then `scripts/ibg.sh up` (images cached): stack up and UE attached in **34 s** |

## What the end-to-end test proves

1. Grafana's numbers match an independent measurement: iperf3 sends 40 Mbit/s of UDP; the UE interface, the UPF `ogstun` interface and the
   app-server interface — read **through Grafana's datasource proxy** — all show 37–41 Mbit/s (header overhead included), and agree with each other within 5 %.
2. The intent engine, asked *"Is the UPF forwarding everything the UE sends? Compare in vs out traffic"*, answers from those numbers: its deterministic
   in/out ratio is ≈ 1.01 (*balanced*) and the verdict is OK.
3. With a deliberate 10 Mbit/s bottleneck on the UPF's N6 egress and 40 Mbit/s offered: iperf3 itself reports 9.75 Mbit/s delivered / 75.6 % loss;
   Prometheus shows ≈ 2 600 qdisc drops/s; the engine's ratio matches the test's own recomputation over the engine-reported window (±10 %),
   its comparison says **loss**, and the analysis is WARN/CRIT and cites queue/drop evidence. After the bottleneck is removed, traffic flows with 0 % loss.
4. Hostile input (empty, oversized, prompt injection asking for `{__name__=~".+"}` and the API key) is handled; the offline rules engine works with no LLM.
5. The API key appears in no file of the tree except the git-ignored `.env`, and in none of the engine/Grafana/Prometheus/exporter logs.

## All checks of the last run

| | Check | Detail |
|---|---|---|
| ✅ | all Prometheus scrape targets are up | 9 targets |
| ✅ | all 16 core/RAN/data containers report up |  |
| ✅ | SBI port 7777 answers on every NF that has one | {'nrf': 1.0, 'scp': 1.0, 'ausf': 1.0, 'udr': 1.0, 'udm': 1.0, 'pcf': 1.0, 'nssf': 1.0, 'bsf': 1.0, 'amf': 1.0, 'smf': 1.0} |
| ✅ | AMF reports 1 registered UE | [({'__name__': 'fivegs_amffunction_rm_registeredsubnbr', 'instance': 'amf:9091', 'job': 'open5gs', 'plmnid': '00101', 'snssai': '1'}, 1.0)] |
| ✅ | AMF, SMF(PFCP) and UPF all count exactly 1 PDU session | {'amf_session': 1.0, 'pfcp_sessions_active': 1.0, 'fivegs_upffunction_upf_sessionnbr': 1.0} |
| ✅ | UE routes the app-server through the 5G tunnel (uesimtun0), not the docker bridge | 172.30.0.99 dev uesimtun0 scope link |
| ✅ | data path UE->UPF->app-server is up |  |
| ✅ | iperf3 delivered ~40 Mbit/s with 0% loss | 40.00 Mbit/s, loss 0.00% |
| ✅ | UE uplink (uesimtun0 tx) ≈ iperf3 rate (±10%) | 37.06 Mbit/s vs iperf3 40.00 |
| ✅ | UPF uplink (ogstun rx) ≈ iperf3 rate (±10%) | 36.97 Mbit/s vs iperf3 40.00 |
| ✅ | app-server rx (eth0) ≈ iperf3 rate (±10%) | 37.49 Mbit/s vs iperf3 40.00 |
| ✅ | the three measurement points agree with each other (±5%) | 37.06, 36.97, 37.49 |
| ✅ | (documented) UPF native GTP counter is a stub = 0 despite traffic | confirms why interface counters are used |
| ✅ | intent engine healthy and reaches Grafana + Prometheus | {"status": "ok", "gemini_configured": true, "grafana_reachable": true, "prometheus_reachable": true} |
| ✅ | Gemini was used (not the offline fallback) | gemini:gemini-3.5-flash-lite |
| ✅ | deterministic in-vs-out comparison finds the uplink balanced | [{"a": "ue_uplink_bps", "b": "server_rx_bps", "direction": "uplink", "ratio_b_over_a": 1.0107, "verdict": "balanced", "mean_a_bps": 27286885.63, "mean |
| ✅ | analysis has a verdict, a summary and findings | verdict=OK |
| ✅ | analysis does not call healthy traffic critical | Uplink traffic is active, balanced, and flowing correctly from UE through UPF to the App Server with a mean throughput around 16.7 Mbps. Downlink traf |
| ✅ | the engine created a Grafana dashboard for the intent (>=3 panels) | http://localhost:3001/d/intent-i-20261007-110415-0b59 |
| ✅ | 'Intent Console' dashboard exists in Grafana |  |
| ✅ | empty intent rejected with 4xx | HTTP 400 |
| ✅ | oversized intent rejected with 4xx | HTTP 400 |
| ✅ | prompt-injection intent: no __name__ selector was executed (direct rejection is covered by the guardrail unit tests) |  |
| ✅ | prompt-injection intent: API key not present in response |  |
| ✅ | offline rules engine works without any LLM | rules |
| ✅ | iperf3 itself: ~10 Mbit/s delivered, 60-85% loss | delivered 9.75 Mbit/s, loss 75.6% |
| ✅ | Prometheus shows qdisc drops on the N6 link | 2701.1549400000004 drops/s |
| ✅ | Grafana data: server receives ~25% of what the UE sent (loss between UPF and server) | UE 40.8 -> server 10.0 Mbit/s |
| ✅ | deterministic comparison reports uplink LOSS (ratio < 0.9) | [{"a": "ue_uplink_bps", "b": "server_rx_bps", "direction": "uplink", "ratio_b_over_a": 0.7007, "verdict": "loss", "mean_a_bps": 23490261.51, "mean_b_b |
| ✅ | engine's ratio matches the test's own recomputation from raw Prometheus series over the reported window (±10%) | engine 0.7007 vs independent 0.7342 |
| ✅ | analysis verdict is WARN or CRIT for the lossy path | WARN: Analysis of the 3-minute test bed data shows that the UPF forwards uplink traffic from the UE in a balanced manner with a ratio of 1.0. However, |
| ✅ | intent engine flags the N6 congestion (WARN or CRIT) | CRIT: N6 link congestion analysis shows severe queue backlog and packet drops at the UPF qdisc. Mean qdisc backlog reached 29.46 pkts (peaking at 183. |
| ✅ | analysis mentions queue/drop/congestion evidence |  |
| ✅ | after removing the bottleneck traffic flows freely again | loss 0.00% |
| ✅ | API key appears in no file of the repository tree except the git-ignored .env |  |
| ✅ | .env is git-ignored |  |
| ✅ | API key is in none of the intent-engine / grafana / prometheus / exporter container logs |  |

## Defects found by testing and review while building (all fixed)

| Found by | Defect | Fix |
|---|---|---|
| live metrics | Open5GS UPF packet counters `fivegs_ep_n3_gtp_*` stay at 0 under 40 Mbit/s (stubs); SMF has no `fivegs_smffunction_sm_sessionnbr` | in/out traffic taken from interface counters; recipes use `pfcp_sessions_active` |
| live metrics | cAdvisor exposes no container `name` label on this host | per-NF CPU/memory moved into the custom exporter (Docker stats API) |
| first e2e run | engine reported a dashboard link that did not exist: same-title dashboards in one folder overwrote each other in Grafana | unique titles + the engine only advertises the uid Grafana confirms; regression test |
| e2e hypothesis was wrong | shaping the app-server's *own* egress never drops packets (kernel back-pressures the local sender) | bottleneck moved to the UPF's N6 egress (forwarded traffic); exporter reads qdisc stats of both |
| live repeat of one question | Gemini's plan was non-deterministic: 3 of 4 runs omitted the queries needed for the in/out comparison | deterministic *plan completion* adds the missing chain members |
| first live Gemini call | first model in the list timed out every call (+45 s) | model order changed to put the fast lite model first |
| Opus review | Markdown-link injection (`[x](javascript:…)`) into Grafana text panels, whose HTML sanitiser is off | Markdown + HTML escaping everywhere user/LLM text is rendered; scheme neutralising; tests |
| Opus review | DNS-rebinding exposure, unbounded body size, sub-query/offset abuse of Prometheus | Host allow-list, 16 KB body cap, sub-query/offset limits; tests |
| Opus review | doc errors (panel counts, cAdvisor claims, ports, read-only wording), tautological tests, key on a shell command line in the test | corrected; tests rewritten so they can fail |
| e2e run | recreating the UE container drops the tunnel route → traffic silently bypasses 5G | e2e now checks the route explicitly; documented in TROUBLESHOOTING |
| e2e runs back to back | earlier congestion leaks into the next run's "healthy" window | test waits until the queue has been drop-free for 2 minutes |

## Known limits (honest)

* Tested on one machine (ARM64 Jetson). x86-64 should work (everything is built from source) but is untested.
* One UE, one gNB. UERANSIM simulates the radio in software; one process crosses a full CPU core around 250–500 Mbit/s, so keep tests ≲ 100 Mbit/s.
* Only AMF/SMF/UPF/PCF expose native metrics in Open5GS v2.8.0; the other NFs are covered by health checks and container CPU/memory.
* The LLM can still word things imperfectly; numbers come from Prometheus and the engine never lets the LLM *lower* a deterministic WARN/CRIT verdict.
* Free-tier Gemini has rate limits; the engine falls back to the rules engine when they are hit.
