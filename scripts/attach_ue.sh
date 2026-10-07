#!/usr/bin/env bash
# attach_ue.sh — wait for the gNB to connect to the AMF (NG Setup), start the UE, wait for its
# PDU session (the uesimtun0 interface) and route traffic for the app-server through that tunnel.
source "$(dirname "$0")/lib.sh"
APP=$(envval APP_SERVER_IP)
say "Waiting for the gNB to complete NG Setup with the AMF"
wait_for "gNB connected to AMF (NG Setup successful)" 120 bash -c "docker logs ibg-gnb 2>&1 | grep -q 'NG Setup procedure is successful'" \
  || die "gNB never connected to the AMF. Check: docker logs ibg-gnb ; docker logs ibg-amf"
say "Starting the UE"
dc up -d ue >runtime/ue-up.log 2>&1 || { tail -20 runtime/ue-up.log >&2; die "could not start the UE container"; }
wait_for "UE got a PDU session (uesimtun0 has an IP)" 120 bash -c "docker exec ibg-ue ip -4 addr show uesimtun0 | grep -q inet" \
  || die "UE did not get a PDU session. Check: docker logs ibg-ue ; docker logs ibg-smf"
# Without this route the UE would reach the app-server directly over the docker bridge (eth0) and
# bypass the 5G tunnel entirely. The /32 forces that one destination through the tunnel.
docker exec ibg-ue ip route replace "$APP/32" dev uesimtun0
UEIP=$(docker exec ibg-ue ip -4 -o addr show uesimtun0 | awk '{print $4}' | cut -d/ -f1)
ok "UE IP inside the 5G network: $UEIP ; route $APP/32 → uesimtun0"
if docker exec ibg-ue ping -I uesimtun0 -c 2 -W 2 "$APP" >/dev/null 2>&1; then
  ok "data path UE → gNB → UPF → app-server works (ping over the tunnel)"
else
  die "ping over the 5G tunnel failed — see docs/TROUBLESHOOTING.md"
fi
