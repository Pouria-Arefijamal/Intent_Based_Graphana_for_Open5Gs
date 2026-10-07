# "Internet" side of the testbed: iperf3 servers (TCP+UDP), tc (to inject congestion), tcpdump.
FROM ubuntu:jammy
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      iperf3 iproute2 iputils-ping tcpdump netcat-openbsd procps ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
