"""Unit tests for the exporter's pure parsers (no Docker, no network)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "exporter"))

from ibg_parsers import parse_ping_rtt_ms, parse_proc_net_dev, parse_tc_qdisc  # noqa: E402

PROC_NET_DEV = """\
Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo:    1000      10    0    0    0     0          0         0     1000      10    0    0    0     0       0          0
  eth0: 123456789  98765    1    2    0     0          0         0 987654321  54321    3    4    0     0       0          0
ogstun:  5000000   4000    0    7    0     0          0         0   600000    500    0    8    0     0       0          0
"""


def test_proc_net_dev_fields():
    parsed = parse_proc_net_dev(PROC_NET_DEV)
    assert set(parsed) == {"lo", "eth0", "ogstun"}
    assert parsed["eth0"] == {
        "rx_bytes": 123456789, "rx_packets": 98765, "rx_drop": 2,
        "tx_bytes": 987654321, "tx_packets": 54321, "tx_drop": 4,
    }
    assert parsed["ogstun"]["rx_drop"] == 7 and parsed["ogstun"]["tx_drop"] == 8


def test_proc_net_dev_no_space_after_colon_and_garbage():
    text = "eth0:100 2 0 0 0 0 0 0 200 3 0 0 0 0 0 0\nbroken: 1 2 3\nnot a line\n"
    parsed = parse_proc_net_dev(text)
    assert list(parsed) == ["eth0"]
    assert parsed["eth0"]["rx_bytes"] == 100 and parsed["eth0"]["tx_bytes"] == 200


def test_proc_net_dev_empty():
    assert parse_proc_net_dev("") == {}


NOQUEUE = """\
qdisc noqueue 0: root refcnt 2
 Sent 0 bytes 0 pkt (dropped 0, overlimits 0 requeues 0)
 backlog 0b 0p requeues 0
"""

PFIFO_FAST = """\
qdisc pfifo_fast 0: root refcnt 2 bands 3 priomap 1 2 2 2 1 2 0 0 1 1 1 1 1 1 1 1
 Sent 8765432 bytes 6543 pkt (dropped 12, overlimits 0 requeues 3)
 backlog 3028b 2p requeues 3
"""

FQ_CODEL = """\
qdisc fq_codel 0: root refcnt 2 limit 10240p flows 1024 quantum 1514 target 5ms interval 100ms memory_limit 32Mb ecn drop_batch 64
 Sent 1000000 bytes 800 pkt (dropped 5, overlimits 0 requeues 1)
 backlog 1514b 1p requeues 1
  maxpacket 1514 drop_overlimit 0 new_flow_count 4 ecn_mark 0
  new_flows_len 0 old_flows_len 1
"""

TBF = """\
qdisc tbf 1: root refcnt 2 rate 10Mbit burst 32Kb lat 400ms
 Sent 5000000 bytes 3500 pkt (dropped 42, overlimits 1234 requeues 0)
 backlog 12Kb 8p requeues 0
"""

HTB = """\
qdisc htb 1: root refcnt 2 r2q 10 default 0x10 direct_packets_stat 0 direct_qlen 1000
 Sent 700 bytes 7 pkt (dropped 1, overlimits 2 requeues 0)
 backlog 0b 0p requeues 0
qdisc netem 10: parent 1:10 limit 1000 delay 20ms
 Sent 600 bytes 6 pkt (dropped 0, overlimits 0 requeues 0)
 backlog 100b 1p requeues 0
"""


def _one(text):
    q = parse_tc_qdisc(text)
    assert len(q) == 1
    return q[0]


def test_qdisc_noqueue():
    q = _one(NOQUEUE)
    assert q.kind == "noqueue"
    assert (q.sent_bytes, q.sent_packets, q.dropped, q.overlimits) == (0, 0, 0, 0)
    assert (q.backlog_bytes, q.backlog_packets) == (0, 0)


def test_qdisc_pfifo_fast():
    q = _one(PFIFO_FAST)
    assert q.kind == "pfifo_fast"
    assert (q.sent_bytes, q.sent_packets, q.dropped, q.overlimits, q.requeues) == (8765432, 6543, 12, 0, 3)
    assert (q.backlog_bytes, q.backlog_packets) == (3028, 2)


def test_qdisc_fq_codel():
    q = _one(FQ_CODEL)
    assert q.kind == "fq_codel"
    assert (q.sent_bytes, q.sent_packets, q.dropped) == (1000000, 800, 5)
    assert (q.backlog_bytes, q.backlog_packets) == (1514, 1)


def test_qdisc_tbf_kilobyte_backlog():
    q = _one(TBF)
    assert q.kind == "tbf" and q.handle == "1:"
    assert (q.dropped, q.overlimits) == (42, 1234)
    assert q.backlog_bytes == 12 * 1024 and q.backlog_packets == 8


def test_qdisc_htb_with_child():
    q = parse_tc_qdisc(HTB)
    assert [x.kind for x in q] == ["htb", "netem"]
    assert q[0].sent_bytes == 700 and q[0].dropped == 1 and q[0].overlimits == 2
    assert q[1].backlog_bytes == 100 and q[1].backlog_packets == 1


def test_qdisc_without_stats_has_none_not_zero():
    q = _one("qdisc noqueue 0: root refcnt 2\n")
    assert q.sent_bytes is None and q.backlog_bytes is None and q.dropped is None


def test_qdisc_empty():
    assert parse_tc_qdisc("") == []


def test_ping_rtt():
    out = "64 bytes from 172.30.0.99: icmp_seq=1 ttl=63 time=1.37 ms"
    assert parse_ping_rtt_ms(out) == 1.37
    assert parse_ping_rtt_ms("time<1 ms") == 1.0
    assert parse_ping_rtt_ms("100% packet loss") is None
