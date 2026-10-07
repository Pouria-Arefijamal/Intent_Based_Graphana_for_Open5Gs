# Containers: Architecture and Services

A student's guide to what each container does, where it runs, and how it fails.

---

## Network Overview

```
┌─ DATA PATH (User Traffic) ────────────────────┐
│  UE (uesimtun0) → gNB → UPF (N3/ogstun) → UPF (N6) → app-server  │
│  (Simulated device)   (Base station)    (Tunnel)   (Internet)    │
└───────────────────────────────────────────────────────────────────┘

┌─ CONTROL PATH (Management) ────────────────────┐
│  gNB → AMF ↔ SMF → UPF  (Session setup)       │
│         ↓                                       │
│    AUSF, UDM, UDR → mongo  (Authentication)   │
│         ↓                                       │
│    NRF, SCP  (Service lookup & routing)        │
│         ↓                                       │
│    PCF, NSSF, BSF  (Policy & slicing)          │
└───────────────────────────────────────────────────────────────────┘

┌─ MONITORING STACK ────────────────────────────┐
│  Prometheus (scrapes) → Grafana (displays)    │
│         ↑                   ↑                  │
│      cAdvisor, node-exporter, custom exporter │
│      intent-engine (translates natural-language)│
└───────────────────────────────────────────────────────────────────┘
```

---

## 5G Core Network Functions

### mongo (172.30.0.2)

**What it does:** Stores subscriber data: SIM cards, phone numbers, service profiles, authentication keys. The network's customer database.

**Talks to:** AMF, UDM, UDR via network connections; no SBI interface.

**Ports:** `:27017` (MongoDB default).

**Metrics:** None native; monitored via custom exporter (`ibg_container_up`, `ibg_container_cpu_seconds_total`, `ibg_container_memory_bytes`).

**If it stops:** Subscribers cannot authenticate. New PDU sessions fail. Existing sessions continue briefly but cannot renew.

---

### smf (172.30.0.7)

**What it does:** Session manager. When a device wants internet, SMF creates a PDU session, negotiates quality-of-service, and tells the UPF where to send packets. Runs the session lifecycle.

**Talks to:** UPF (N4/PFCP), UDM, PCF (SBI), Prometheus (metrics).

**Ports:** `:7777` (SBI), `:9091` (Prometheus metrics).

**Metrics:** Open5GS native metrics: session count, session creation time, error rates.

**If it stops:** No new PDU sessions. Existing sessions orphan. UE loses connectivity.

---

### upf (172.30.0.8)

**What it does:** User-plane forwarder. Routes actual traffic from the RAN to the internet, applies QoS rules (bandwidth limits, priority), and collects usage statistics. The data highway.

**Talks to:** gNB (N3), SMF (N4), app-server/internet (N6), Prometheus (metrics).

**Ports:** `:9091` (Prometheus metrics). Interfaces: `eth0` (N3+N4+N6 shared), `ogstun` (user-plane TUN device).

**Metrics:** Open5GS native: `fivegs_upffunction_upf_sessionnbr`, `upf_qosflows` (GTP packet counters are stubs at 0). Custom exporter: `eth0` and `ogstun` interface stats (rx/tx bytes, packets, drops).

**If it stops:** All user traffic stops immediately. Sessions survive but no packets flow.

---

### amf (172.30.0.10)

**What it does:** Receptionist and mobility manager. Registers devices on the network, handles handovers between base stations, sends alerts/paging when the network needs to reach a device.

**Talks to:** gNB (N2/NGAP), AUSF, UDM (SBI), SMF, Prometheus.

**Ports:** `:7777` (SBI), `:9091` (Prometheus metrics).

**Metrics:** Open5GS native: registered UEs, attempted connections, authentication failures.

**If it stops:** Devices cannot register or hand over. New connections fail. Existing sessions timeout.

---

### ausf (172.30.0.11)

**What it does:** Security checkpoint. Challenges devices and validates their credentials using cryptographic keys from the SIM. Ensures only legitimate subscribers connect.

**Talks to:** AMF (SBI), UDM (for keys), NRF (lookup).

**Ports:** `:7777` (SBI).

**Metrics:** None native; monitored via custom exporter (`ibg_nf_sbi_up` TCP probe, `ibg_container_cpu_seconds_total`, `ibg_container_memory_bytes`).

**If it stops:** Authentication fails. Devices cannot register.

---

### nrf (172.30.0.12)

**What it does:** Directory service. All network functions register themselves with NRF and query it to find each other (e.g., "Where is the SMF?"). Like DNS for the 5G core.

**Talks to:** All core NFs (SBI).

**Ports:** `:7777` (SBI).

**Metrics:** None native; monitored via custom exporter (`ibg_nf_sbi_up`, `ibg_container_cpu_seconds_total`, `ibg_container_memory_bytes`).

**If it stops:** Service discovery breaks. NFs cannot find each other. New sessions fail; existing ones may survive briefly.

---

### udm (172.30.0.13)

**What it does:** Unified data manager. Stores subscriber profiles and handles authentication. Works with AUSF to verify credentials.

**Talks to:** AMF, AUSF (SBI), UDR (internal), mongo (database).

**Ports:** `:7777` (SBI).

**Metrics:** None native; monitored via custom exporter (`ibg_nf_sbi_up`, `ibg_container_cpu_seconds_total`, `ibg_container_memory_bytes`).

**If it stops:** Authentication and subscriber profile lookup fail. Devices cannot register.

---

### udr (172.30.0.14)

**What it does:** Data repository for network-level policies and operator-level settings (not subscriber-specific). Stores access rules, charging parameters, etc.

**Talks to:** PCF, NSSF, NRF (SBI), mongo (database).

**Ports:** `:7777` (SBI).

**Metrics:** None native; monitored via custom exporter (`ibg_nf_sbi_up`, `ibg_container_cpu_seconds_total`, `ibg_container_memory_bytes`).

**If it stops:** Policy and slicing lookups fail. Some services may default to permissive behavior; others may block.

---

### pcf (172.30.0.27)

**What it does:** Policy control officer. Sets bandwidth limits, QoS rules, charging rates, and service-level agreements. Tells the UPF how to treat different flows.

**Talks to:** SMF, UDR, NSSF (SBI), Prometheus.

**Ports:** `:7777` (SBI), `:9091` (Prometheus metrics).

**Metrics:** Open5GS native: policy decisions, charging events.

**If it stops:** Default policies apply. Some QoS policies may not update; existing sessions continue.

---

### nssf (172.30.0.28)

**What it does:** Network slicing scheduler. Assigns devices to slices (logical sub-networks) based on their needs and subscriptions. One slice for video, another for IoT.

**Talks to:** AMF, UDR, NRF (SBI).

**Ports:** `:7777` (SBI).

**Metrics:** None native; monitored via custom exporter (`ibg_nf_sbi_up`, `ibg_container_cpu_seconds_total`, `ibg_container_memory_bytes`).

**If it stops:** Devices cannot join slices. Sessions may fail or join default slices.

---

### bsf (172.30.0.29)

**What it does:** Binding coordinator. Helps route messages to the same device across multiple sessions and services.

**Talks to:** Other NFs via SBI.

**Ports:** `:7777` (SBI).

**Metrics:** None native; monitored via custom exporter (`ibg_nf_sbi_up`, `ibg_container_cpu_seconds_total`, `ibg_container_memory_bytes`).

**If it stops:** Multi-session binding fails; some services may not work correctly.

---

### scp (172.30.0.35)

**What it does:** Service communication proxy. Routes SBI messages between NFs; improves resilience and load balancing. Acts as a message middleman.

**Talks to:** All core NFs (SBI).

**Ports:** `:7777` (SBI).

**Metrics:** None native; monitored via custom exporter (`ibg_nf_sbi_up`, `ibg_container_cpu_seconds_total`, `ibg_container_memory_bytes`).

**If it stops:** SBI routing breaks. Depending on configuration, NFs may fall back to direct connections or fail.

---

### webui (172.30.0.26)

**What it does:** A web dashboard for manual administration: add/remove SIMs, view subscriber info, check network status. Bypasses the API for human operators.

**Talks to:** mongo, all core NFs.

**Ports:** `:9999` (HTTP).

**Metrics:** None.

**If it stops:** Dashboard becomes unavailable; network continues working.

---

## RAN (Radio Access Network)

### gnb (172.30.0.23, UERANSIM)

**What it does:** Simulated 5G base station. Transmits/receives radio signals (simulated), handles initial connection with devices, forwards control messages to AMF and user traffic to UPF.

**Talks to:** UE (simulated radio), AMF (N2/NGAP), UPF (N3/GTP-U).

**Ports:** None exposed; uses N2 and N3 interfaces over `eth0`.

**Metrics:** None native; custom exporter collects `eth0` interface stats (rx/tx bytes, packets).

**If it stops:** UE loses radio connection. Sessions survive; no packets flow. Re-connection fails.

---

### ue (172.30.0.24, UERANSIM)

**What it does:** Simulated 5G mobile device. Sends traffic (HTTP, iperf3, ICMP), responds to network commands. Represents your phone in this test network.

**Talks to:** gNB (simulated radio), app-server (via UPF data path).

**Ports:** None exposed; uses tunnel `uesimtun0` (assigns itself 192.168.100.x).

**Metrics:** None native; custom exporter collects `eth0` and `uesimtun0` interface stats.

**If it stops:** Test traffic stops. Sessions orphan; no data flows.

---

## Data Network

### app-server (172.30.0.99)

**What it does:** Iperf3 server simulating the internet. Receives traffic from UE (uplink), sends traffic to UE (downlink).

**Talks to:** UPF (N6), Prometheus (metrics).

**Ports:** `:5201-5204` (iperf3 servers).

**Metrics:** None native; custom exporter collects `eth0` interface stats and qdisc queueing stats (backlog, drops, overlimits).

**If it stops:** No app traffic. Sessions survive; user-plane traffic halts.

---

## Monitoring Stack

### prometheus (172.30.0.240)

**What it does:** Time-series metrics database. Scrapes metrics from Open5GS NFs, cAdvisor, exporters every 5 seconds. Stores them with timestamps for historical queries.

**Talks to:** AMF, SMF, UPF, PCF (metrics), cAdvisor, node-exporter, custom exporter, itself.

**Ports:** `:9090` (HTTP, exposed as PROMETHEUS_PORT).

**Metrics:** None of its own; collects from others.

**If it stops:** Metrics cannot be collected or queried. Grafana has no data; past data remains on disk.

---

### grafana (172.30.0.241)

**What it does:** Visualization platform. Queries Prometheus and renders metrics as dashboards, graphs, and gauges. Shows the network's health in real time.

**Talks to:** Prometheus (datasource), intent-engine (for intent dashboards).

**Ports:** `:3000` (HTTP, exposed as GRAFANA_PORT).

**Metrics:** None of its own.

**If it stops:** Dashboard becomes invisible. Prometheus still collects data; you just cannot see it.

---

### cadvisor (172.30.0.242)

**What it does:** Host-level cgroup monitor. Reports system CPU, memory, and network from cgroups. Per-container metrics (with names) are not available in this setup; use the custom exporter instead.

**Talks to:** Docker daemon (via socket), Prometheus (metrics).

**Ports:** `:8080` (HTTP, exposed as CADVISOR_PORT).

**Metrics:** Host-level cgroup data: `container_cpu_usage_seconds_total`, `container_memory_working_set_bytes` (not per-container).

**If it stops:** Host-level cgroup metrics disappear. Per-NF metrics from custom exporter remain.

---

### node-exporter (172.30.0.243)

**What it does:** Host monitor. Reports system-level metrics: CPU, disk, network, processes. Prometheus scrapes these.

**Talks to:** Host OS, Prometheus.

**Ports:** `:9100` (HTTP, exposed as NODE_EXPORTER_PORT).

**Metrics:** `node_cpu_seconds_total`, `node_memory_*`, `node_network_*`, `node_filesystem_*`, etc.

**If it stops:** Host-level metrics disappear. Per-container CPU/memory from the custom exporter remain.

---

### exporter (172.30.0.244)

**What it does:** Custom metrics collector. Aggregates network interface stats from UE/gNB/UPF/app-server, qdisc queueing stats, path connectivity (ping tests), and container health checks. Formats them for Prometheus.

**Talks to:** All containers (via `docker exec` and socket), Prometheus.

**Ports:** `:9200` (HTTP, internal only; not exposed to host).

**Metrics:** `ibg_container_up`, `ibg_nf_sbi_up`, `ibg_iface_*` (bytes, packets, drops), `ibg_qdisc_*` (backlog, drops), `ibg_path_up`, `ibg_path_rtt_ms`, `ibg_ue_session_up`.

**If it stops:** Custom metrics for interfaces, qdisc, and NF health disappear. Native Open5GS metrics (from `:9091` ports) still flow.

---

### intent-engine (172.30.0.245)

**What it does:** Natural-language query translator. Converts intents like "Is the UPF forwarding all traffic?" into PromQL queries, executes them through Grafana's datasource proxy, analyzes results, and creates dashboards automatically in Grafana's 'Intent-Based' folder.

**Talks to:** Grafana (proxy queries and dashboard creation), Gemini API (if configured).

**Ports:** `:8088` (HTTP, exposed as INTENT_PORT).

**Metrics:** `ibg_intent_requests_total`, `ibg_intent_duration_seconds` histogram, `ibg_intent_llm_errors_total`.

**If it stops:** Intent API becomes unavailable. Prometheus still collects data; dashboards remain readable but cannot be created by intent.

---

## Summary: Failure Scenarios

| Container | Stops → | Impact |
|-----------|---------|--------|
| **mongo** | Subscribers cannot authenticate | |
| **amf** | Devices cannot register or hand over | |
| **smf** | New PDU sessions fail | |
| **upf** | All user-plane traffic stops | |
| **ausf** | Authentication fails | |
| **udm** | Subscriber profile lookup fails | |
| **pcf** | QoS policies don't update | |
| **nrf** | Service discovery breaks | |
| **gNB** | No radio connection; re-connect fails | |
| **UE** | Test traffic stops | |
| **app-server** | No downstream traffic | |
| **prometheus** | Metrics cannot be stored or queried | |
| **grafana** | Dashboards invisible; data still collected | |
| **cadvisor** | Container metrics disappear | |
| **exporter** | Custom network/qdisc metrics disappear | |
| **intent-engine** | Intent API unavailable | |

