#!/usr/bin/env bash
# provision_subscriber.sh — put the test SIM (IMSI/Ki/OPc from config/open5gs.env) into Open5GS's
# MongoDB so the UE is allowed to register. Idempotent (upsert).
source "$(dirname "$0")/lib.sh"
IMSI=$(envval UE1_IMSI); KI=$(envval UE1_KI); OPC=$(envval UE1_OP)
[ -n "$IMSI" ] && [ -n "$KI" ] && [ -n "$OPC" ] || die "UE1_IMSI/UE1_KI/UE1_OP missing in config/open5gs.env"
say "Provisioning subscriber $IMSI in MongoDB"
docker exec ibg-mongo mongosh --quiet open5gs --eval "
db.subscribers.updateOne({imsi:'$IMSI'},{\$set:{
  imsi:'$IMSI', msisdn:[], imeisv:[],
  security:{k:'$KI', opc:'$OPC', amf:'8000', sqn:NumberLong(0)},
  ambr:{downlink:{value:1,unit:3}, uplink:{value:1,unit:3}},
  slice:[{sst:1, default_indicator:true, session:[{name:'internet', type:3,
    qos:{index:9, arp:{priority_level:8, pre_emption_capability:1, pre_emption_vulnerability:2}},
    ambr:{downlink:{value:1,unit:3}, uplink:{value:1,unit:3}}}]}],
  access_restriction_data:32, network_access_mode:0, subscriber_status:0,
  operator_determined_barring:0, subscribed_rau_tau_timer:12, schema_version:1
}},{upsert:true}).acknowledged" | grep -q true && ok "subscriber $IMSI provisioned" || die "provisioning failed"
