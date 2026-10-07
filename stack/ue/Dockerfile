# UE container = upstream docker_ueransim image + iperf3 (traffic generator) + OPc-type credentials.
FROM docker_ueransim:latest
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends iperf3 iputils-ping \
    && rm -rf /var/lib/apt/lists/*
