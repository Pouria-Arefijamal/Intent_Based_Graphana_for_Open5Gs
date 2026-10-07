"""Pure parsing helpers for the IBG exporter.

Nothing in this module touches Docker, the network or the clock, so every
function can be unit-tested with plain fixture strings.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional

# Column order of /proc/net/dev after the "iface:" prefix.
_RX_FIELDS = ("bytes", "packets", "errs", "drop", "fifo", "frame", "compressed", "multicast")
_TX_FIELDS = ("bytes", "packets", "errs", "drop", "fifo", "colls", "carrier", "compressed")


def parse_proc_net_dev(text: str) -> Dict[str, Dict[str, int]]:
    """Parse the text of ``/proc/net/dev``.

    Returns ``{iface: {"rx_bytes":..,"rx_packets":..,"rx_drop":..,"tx_bytes":..,
    "tx_packets":..,"tx_drop":..}}``. Header lines and malformed lines are
    skipped (a malformed line never produces a fake zero entry).
    """
    result: Dict[str, Dict[str, int]] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        name, _, rest = line.partition(":")
        name = name.strip()
        fields = rest.split()
        if not name or len(fields) < 16:
            continue
        try:
            nums = [int(f) for f in fields[:16]]
        except ValueError:
            continue
        rx = dict(zip(_RX_FIELDS, nums[:8]))
        tx = dict(zip(_TX_FIELDS, nums[8:16]))
        result[name] = {
            "rx_bytes": rx["bytes"],
            "rx_packets": rx["packets"],
            "rx_drop": rx["drop"],
            "tx_bytes": tx["bytes"],
            "tx_packets": tx["packets"],
            "tx_drop": tx["drop"],
        }
    return result


@dataclass
class QdiscStats:
    """One qdisc as reported by ``tc -s qdisc show``. ``None`` = not reported."""

    kind: str
    handle: str = ""
    sent_bytes: Optional[int] = None
    sent_packets: Optional[int] = None
    dropped: Optional[int] = None
    overlimits: Optional[int] = None
    requeues: Optional[int] = None
    backlog_bytes: Optional[int] = None
    backlog_packets: Optional[int] = None


_SENT_RE = re.compile(
    r"Sent\s+(\d+)\s+bytes\s+(\d+)\s+pkt\s*"
    r"\(\s*dropped\s+(\d+)\s*,\s*overlimits\s+(\d+)\s+requeues\s+(\d+)\s*\)"
)
_BACKLOG_RE = re.compile(r"backlog\s+([\d.]+)([KMG]?)b\s+(\d+)p")
_SIZE_MULT = {"": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3}


def parse_tc_qdisc(text: str) -> List[QdiscStats]:
    """Parse the output of ``tc -s qdisc show dev <dev>``.

    Handles noqueue, pfifo_fast, fq_codel, tbf, htb (and any other kind: the
    statistics lines have the same shape for all of them). A qdisc block whose
    statistics lines are absent keeps ``None`` fields instead of zeros.
    """
    qdiscs: List[QdiscStats] = []
    current: Optional[QdiscStats] = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("qdisc "):
            parts = line.split()
            kind = parts[1] if len(parts) > 1 else "unknown"
            handle = parts[2] if len(parts) > 2 else ""
            current = QdiscStats(kind=kind, handle=handle)
            qdiscs.append(current)
            continue
        if current is None:
            continue
        m = _SENT_RE.search(line)
        if m:
            current.sent_bytes = int(m.group(1))
            current.sent_packets = int(m.group(2))
            current.dropped = int(m.group(3))
            current.overlimits = int(m.group(4))
            current.requeues = int(m.group(5))
            continue
        m = _BACKLOG_RE.search(line)
        if m:
            current.backlog_bytes = int(float(m.group(1)) * _SIZE_MULT[m.group(2)])
            current.backlog_packets = int(m.group(3))
    return qdiscs


_RTT_RE = re.compile(r"time[=<]\s*([\d.]+)\s*ms")


def parse_ping_rtt_ms(text: str) -> Optional[float]:
    """Return the RTT in ms from ``ping -c1`` output, or None if absent."""
    m = _RTT_RE.search(text)
    return float(m.group(1)) if m else None
