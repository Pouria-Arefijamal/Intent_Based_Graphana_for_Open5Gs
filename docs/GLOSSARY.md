# Glossary: 5G and Monitoring Concepts

A student-friendly reference for every term in this Open5GS monitoring stack. No prior 5G knowledge assumed.

---

## A

**5G SA** (Standalone)
A complete 5G network built from scratch, not dependent on older 4G infrastructure. Think of it as building a whole new city rather than upgrading an existing one.

**5GC** (5G Core)
The brain and nervous system of a 5G network. Handles authentication, routing, policy decisions, and billing—everything that isn't radio communication. See "Core Network."

**AMF** (Access and Mobility Management Function)
Like a receptionist: greets devices when they connect, tracks where they are, and manages handovers when they move between base stations.

**AUSF** (Authentication Server Function)
The security guard: verifies that a device trying to connect is legitimate using cryptographic challenges and responses.

**Backlog** (Queue Backlog)
The number of packets waiting in a queue to be transmitted. High backlog means congestion; packets are piling up faster than they can leave.

**BSF** (Binding Support Function)
Helps coordinate messaging between network functions that need to send data to the same device.

**cAdvisor** (Container Advisor)
A monitoring tool that watches Docker containers and reports their CPU, memory, and network usage—like a fitness tracker for containers.

---

## C

**Cadence**
The rhythm or interval at which something happens repeatedly (e.g., "metrics are scraped every 5 seconds").

**Counter** (Prometheus Metric Type)
A number that only goes up or stays the same; never decreases. Used for things like total bytes transmitted or requests processed. See "Gauge."

**Dashboard** (Grafana)
A visual display showing multiple panels (graphs, numbers, tables) in one place. Think of it as a car's instrument panel showing speed, fuel, engine temperature, etc.

---

## D

**Datasource** (Grafana)
A connection to an external database where Grafana fetches data. In this stack, Prometheus is the datasource that holds all the metrics.

**DNN** (Data Network Name)
A label for a service offered by the 5G network (e.g., "internet," "video streaming," "private enterprise"). Devices can request specific DNNs to get different quality levels.

**Downlink**
Traffic flowing from the app-server → UPF → gNB → UE device. In everyday terms: data coming down to your phone (downloads).

---

## E

**Exporter** (Prometheus)
A service that collects data and formats it as Prometheus metrics. Like a translator converting diverse data sources into a common language Prometheus understands.

---

## F

**fq_codel** (Fair Queuing with CoDel)
A smart queue discipline that prevents slow-moving network flows from hogging the pipe by actively dropping packets from the most aggressive flows before congestion gets too bad.

---

## G

**Gauge** (Prometheus Metric Type)
A number that can go up or down (like temperature or memory usage). Contrasts with counters, which only increase. See "Counter."

**Gemini** (Google Gemini API)
An LLM (large language model) that the intent-engine uses to translate natural language into queries and analysis.

**gNB** (gNodeB, next-generation NodeB)
A 5G base station—the radio tower that transmits and receives signals to/from your phone. "NodeB" comes from 3G terminology.

**Goodput**
The amount of useful data actually delivered (throughput minus protocol overhead and retransmissions). Like measuring actual work done, not wasted motion.

**Grafana**
A visualization platform that shows monitoring data as graphs, gauges, and dashboards. Turns raw numbers from Prometheus into pretty, understandable pictures.

**GTP-U** (GPRS Tunneling Protocol—User Plane)
A tunneling protocol that wraps user traffic inside a packet for transport through the 5G core network. Think of it like putting a letter in an envelope to send it safely.

**Guardrail**
A safety rule that prevents something dangerous or unintended (e.g., "the LLM can only use this list of metrics" or "queries must not exceed 500 characters").

---

## H

**HTB** (Hierarchical Token Bucket)
A queue discipline that creates class hierarchies—some flows get priority or guaranteed bandwidth. Like assigning VIP and economy lanes to traffic.

---

## I

**IMSI** (International Mobile Subscriber Identity)
A permanent identifier for a SIM card: 15 digits encoding country, telecom provider, and subscriber number. Being phased out in favor of SUPI.

**Intent** (Intent-Based Networking)
A user's high-level request in plain English (e.g., "Is the UE sending as much as the server is receiving?") rather than a low-level technical command.

**Intent-Based Networking**
Turning natural-language goals into automated network policies and queries. Like saying "make video calls smooth" instead of manually tuning buffer sizes.

---

## J

**Jitter**
Variation in latency: when some packets arrive in 10ms and others in 50ms, they have high jitter. Jitter breaks real-time apps like voice calls (the audio becomes choppy).

---

## K

**Ki** (Permanent Subscriber Key)
The master secret key stored on a SIM card; the network also knows it. Used to derive session keys for authentication—like a master password that stays private.

**PromQL** and **Kubernetes** concepts (if needed in your context):
(Kubernetes is outside the scope of this stack, but PromQL is core.)

---

## L

**Latency** (Round-Trip Time / RTT)
The time it takes for data to travel from sender to receiver and back. Measured in milliseconds. Low latency is critical for real-time apps (gaming, calls).

**LLM** (Large Language Model)
An AI system (like ChatGPT or Gemini) trained on massive text, capable of understanding and generating human language. The intent-engine uses one to translate intents to queries.

---

## M

**MCC/MNC** (Mobile Country Code / Mobile Network Code)
Part of a phone number's identity:
- **MCC**: 3 digits identifying the country (e.g., 310 = USA)
- **MNC**: 2–3 digits identifying the telecom operator (e.g., 410 = AT&T)

**Mongo** (MongoDB)
A database that stores subscriber information (SIM cards, service profiles, etc.) for the 5GC.

**Monitoring** / **Observability**
Collecting and visualizing system behavior: metrics (numbers), logs (events), traces (request paths). The intent-engine lets you query this data by intent.

---

## N

**N2** (Interface)
Control-plane link between RAN (gNB) and core (AMF). Like a phone line for management calls.

**N3** (Interface)
User-plane tunnel between RAN (gNB) and core (UPF). Carries encrypted user traffic using GTP-U.

**N4** (Interface)
Control-plane link between SMF and UPF. SMF tells UPF what forwarding rules to apply.

**N6** (Interface)
User-plane link between core (UPF) and external data networks (e.g., the internet or app-server). The "exit gate" from the 5G core.

**NF** (Network Function)
A service or software component in the 5GC (e.g., AMF, UPF, SMF). Think of it as a microservice in the network.

**NGAP** (Next-Generation Application Protocol)
The control-plane protocol between RAN and AMF (over N2). Handles connection setup, paging, etc.

**Node-Exporter**
A Prometheus exporter that runs on the host and reports system metrics (CPU, disk, network).

**Noqueue** (Queue Discipline)
The simplest queue: no buffering or scheduling, just drop packets if the link is busy. Rarely used in practice.

**NRF** (Network Repository Function)
A registry and lookup service: network functions register themselves with the NRF so others can find them. Like a phone book for 5G.

**NSSF** (Network Slice Selection Function)
Picks which network slice a device should use based on its requirements and subscription. A slice is a logically independent network inside one physical network.

---

## O

**OP** / **OPc** (Operator Variant Key / Variant)
Cryptographic keys derived from Ki, known to both the SIM and the network. Used in authentication algorithms (like Milenage). OPc is a variant that improves security.

**Open5GS**
An open-source implementation of the 5G core network. This project uses Open5GS as its core platform.

**Operator** (in Prometheus)
A symbol (+, -, *, /) or function (rate(), sum()) in PromQL that combines or transforms metrics.

---

## P

**Packet Loss**
Percentage of packets that don't reach their destination. Caused by congestion, errors, or drops. High loss breaks applications; acceptable thresholds depend on the app (video can tolerate ~5%, real-time voice cannot).

**Panel** (Grafana)
A single visualization in a dashboard: a graph, gauge, table, or text box.

**PCF** (Policy Control Function)
Sets and enforces network policies: bandwidth limits, QoS rules, charging rates. Like a traffic police officer setting speed limits.

**PDU Session** (Protocol Data Unit)
A logical connection between a UE device and a data network (like the internet). Each PDU session has its own routing, quality-of-service, and billing rules.

**PFCP** (Packet Forwarding Control Protocol)
The protocol SMF uses to tell the UPF which packets to forward where. Runs over N4.

**PLMN** (Public Land Mobile Network)
A telecom operator's network identified by MCC+MNC. Every carrier has one.

**PromQL** (Prometheus Query Language)
The language for querying Prometheus: select metrics, filter by labels, aggregate, compute rates, etc. Example: `rate(ibg_iface_tx_bytes_total[30s])*8` calculates throughput in bits.

**Prometheus**
A time-series database and monitoring system. Stores metrics (points in time) and lets you query them with PromQL.

---

## Q

**QDisc** (Queueing Discipline)
The Linux kernel's scheduler for network traffic. Decides how to queue, prioritize, and drop packets. Examples: noqueue, tbf, htb, fq_codel.

**QoS** (Quality of Service)
Network settings that guarantee a minimum level of performance: bandwidth, latency, packet loss. A video call might request "100 Mbps, <50ms latency."

**QoS Flow**
A logical flow within a PDU session, each with its own QoS parameters. One PDU session might have multiple flows (e.g., video and voice).

---

## R

**RAN** (Radio Access Network)
The "front end" of a mobile network: base stations (gNBs) and the radio protocols connecting devices to the network. Contrast with "core network" (5GC).

**Rate** (PromQL Function)
Calculates the rate of change of a counter over a time window: `rate(metric[5m])` means "how much did this metric increase per second over the last 5 minutes?" Used to compute throughput from byte counters.

**RTT** (Round-Trip Time)
Same as latency: time for a packet to reach a destination and return. Measured in milliseconds.

---

## S

**S-NSSAI** (Single Network Slice Selection Assistance Information)
An identifier for a network slice. Includes a Slice/Service Type (SST) and optional Slice Differentiator (SD). Think of it as a "flavor" of network service.

**SBI** (Service-Based Interface)
The REST/HTTP API used by 5GC network functions to talk to each other. Instead of custom protocols, NFs use standardized HTTP calls.

**Scrape** (Prometheus)
Prometheus's act of contacting an exporter (e.g., at `:9091`) and pulling metrics. Happens at regular intervals (every 5 seconds in this stack).

**SCP** (Service Communication Proxy)
Routes control-plane messages between network functions that may not directly know each other's addresses. Improves resilience and load balancing.

**Slice** (Network Slice)
A logically independent network inside one physical network. Different slices can have different performance rules (one slice: strict latency, another: best effort).

**SMF** (Session Management Function)
Manages PDU sessions: creates/deletes them, negotiates QoS, coordinates forwarding rules with the UPF. Like a session-layer referee.

**SUPI** (Subscription Permanent Identifier)
The modern replacement for IMSI: contains the same information but is encrypted during transmission for privacy.

---

## T

**TBF** (Token Bucket Filter)
A simple rate-limiting queue discipline. Tokens accumulate at a fixed rate; sending a packet costs tokens. Once tokens run out, packet transmission stops. Like a toll booth.

**TEID** (Tunnel Endpoint Identifier)
A label that identifies a tunnel in GTP-U packets. The UPF and gNB use TEIDs to route encapsulated user traffic.

**Throughput**
The amount of data successfully transmitted per unit time (bits per second). Throughput ≤ bandwidth and includes protocol overhead; goodput excludes overhead.

**TUN Device** (Tunnel Device)
A virtual network interface created by the kernel for user-space applications. In this stack, the UE and UPF use TUN devices to send/receive traffic.

---

## U

**UDM** (Unified Data Management)
Stores subscriber profiles and handles authentication requests. Like a customer database and security vault combined.

**UDR** (Unified Data Repository)
A database for network-level policies (not subscriber-specific): call forwarding rules, access control lists, etc.

**UE** (User Equipment)
Any mobile device: a phone, laptop, IoT sensor. The "client" in a mobile network. In this stack, simulated by UERANSIM.

**UERANSIM**
A software simulator that emulates a 5G radio device (UE) and base station (gNB). Used for testing without real hardware.

**Uplink**
Traffic flowing from the UE device → gNB → UPF → app-server. In everyday terms: data going up from your phone (uploads).

**UPF** (User Plane Function)
The traffic cop of the user plane: forwards packets between the RAN, the core, and external networks. Implements QoS policies and charging rules.

---

## V

**Verdict** (Comparison Analysis)
The intent-engine's conclusion about traffic flow:
- **Balanced**: in and out match (ratio 0.9–1.1)
- **Loss**: out is less than in (ratio < 0.9)
- **Amplified**: out exceeds in (ratio > 1.1), usually due to GTP-U encapsulation overhead
- **Idle**: traffic too low to analyze

---

## W

**Webui**
The Open5GS web dashboard for managing subscribers, provisioning SIMs, and monitoring the core network manually.

---

## X–Z

*(Reserved for future additions)*

---

## Acronym Quick Reference

| Term | Meaning |
|------|---------|
| 5G | Fifth-generation mobile network |
| 5GC | 5G Core network |
| AMF | Access & Mobility Management Function |
| AUSF | Authentication Server Function |
| BSF | Binding Support Function |
| DNN | Data Network Name |
| gNB | gNodeB (5G base station) |
| GTP-U | GPRS Tunneling Protocol—User Plane |
| IMSI | International Mobile Subscriber Identity |
| Ki | Permanent Subscriber Key |
| MCC | Mobile Country Code |
| MNC | Mobile Network Code |
| PFCP | Packet Forwarding Control Protocol |
| PLMN | Public Land Mobile Network |
| QoS | Quality of Service |
| RAN | Radio Access Network |
| SBI | Service-Based Interface |
| SCP | Service Communication Proxy |
| SMF | Session Management Function |
| SUPI | Subscription Permanent Identifier |
| TEID | Tunnel Endpoint Identifier |
| UDM | Unified Data Management |
| UDR | Unified Data Repository |
| UE | User Equipment |
| UPF | User Plane Function |

