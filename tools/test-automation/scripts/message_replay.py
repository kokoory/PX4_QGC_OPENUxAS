#!/usr/bin/env python3
"""Replay a recorded QGC MessageMonitor .jsonl file.

The .jsonl is written by QGC's MessageMonitorPage (one JSON object per line),
with the same shape as the qgc_uxas_bridge.py monitor tap PLUS rows from the
EventBroadcaster tx/rx channels:

    { "ts": 1781018000.123,
      "dir": "uxas_out" | "uxas_in" | "event_tx" | "command_rx",
      "channel": "event" | "command" | "bridge",
      "category": "...", "event": "...",
      "summary": "...", "payload": {...} }

For each line we re-emit a fresh UDP datagram onto the same port QGC listens to:

    channel == "event"    -> UDP 45678   (EventBroadcaster tx port,
                                          shape compatible with sendEvent JSON)
    channel == "command"  -> UDP 45679   (EventBroadcaster command port)
    channel == "bridge"   -> UDP 45680   (MessageMonitorPage bridge tap)

Timing is reproduced from `ts` deltas, scaled by `--rate` (e.g. --rate 5.0
plays back 5x faster, --rate 0.25 plays back 4x slower).

Usage:
    python3 message_replay.py messages.jsonl
    python3 message_replay.py messages.jsonl --rate 5.0
    python3 message_replay.py messages.jsonl --only bridge --rate 1.0
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import time
from typing import Any


PORT_FOR_CHANNEL = {
    "event":   45678,
    "command": 45679,
    "bridge":  45680,
}


def _build_event_datagram(row: dict) -> bytes:
    """Reconstruct an EventBroadcaster-tx JSON datagram from a row."""
    out = {
        "seq":       row.get("seq", 0),
        "timestamp": row.get("ts", time.time()),
        "category":  row.get("category", "replay"),
        "event":     row.get("event", ""),
    }
    payload = row.get("payload") or {}
    if payload:
        out["data"] = payload
    return (json.dumps(out, separators=(",", ":")) + "\n").encode("utf-8")


def _build_command_datagram(row: dict) -> bytes:
    """Reconstruct a command JSON datagram (UDP 45679)."""
    payload = dict(row.get("payload") or {})
    payload.setdefault("action", row.get("event", "noop"))
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def _build_bridge_datagram(row: dict) -> bytes:
    """Reconstruct a bridge tap JSON datagram (UDP 45680)."""
    payload = row.get("payload") or {}
    if isinstance(payload, dict) and "dir" in payload:
        # already in tap shape — re-stamp ts so the receiver clock matches now
        out = dict(payload)
    else:
        out = {
            "ts":        time.time(),
            "dir":       row.get("dir", "uxas_in"),
            "lmcp_type": row.get("category", ""),
            "summary":   row.get("summary", ""),
            "vehicle_id": 0,
        }
    out["ts"] = time.time()
    return json.dumps(out, separators=(",", ":")).encode("utf-8")


_BUILDERS = {
    "event":   _build_event_datagram,
    "command": _build_command_datagram,
    "bridge":  _build_bridge_datagram,
}


def replay(path: str, rate: float, host: str, only: str | None) -> int:
    rows: list[dict] = []
    with open(path, "r") as fh:
        for ln_no, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                print(f"[replay] {path}:{ln_no} bad JSON: {exc}", file=sys.stderr)
    if not rows:
        print("[replay] nothing to replay", file=sys.stderr)
        return 1

    rows.sort(key=lambda r: r.get("ts", 0.0))
    base_ts = rows[0].get("ts", 0.0)
    wall0 = time.time()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

    n = 0
    for row in rows:
        channel = row.get("channel")
        if not channel:
            # Infer from "dir"
            d = row.get("dir", "")
            if d.startswith("event"):
                channel = "event"
            elif d.startswith("command"):
                channel = "command"
            else:
                channel = "bridge"
        if only and channel != only:
            continue
        builder = _BUILDERS.get(channel)
        if builder is None:
            continue

        target_offset = (row.get("ts", base_ts) - base_ts) / max(rate, 0.001)
        delay = (wall0 + target_offset) - time.time()
        if delay > 0:
            time.sleep(delay)
        port = PORT_FOR_CHANNEL[channel]
        try:
            sock.sendto(builder(row), (host, port))
            n += 1
        except OSError as exc:
            print(f"[replay] send failed ({channel}): {exc}", file=sys.stderr)

    print(f"[replay] replayed {n} message(s) at rate {rate}x")
    sock.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("file", help="Path to a .jsonl recorded by QGC MessageMonitor")
    p.add_argument("--rate", type=float, default=1.0,
                   help="Playback rate multiplier (default 1.0). 5.0 = 5x faster.")
    p.add_argument("--host", default="127.0.0.1",
                   help="Destination host (default 127.0.0.1)")
    p.add_argument("--only", choices=("event", "command", "bridge"),
                   help="Replay only one channel.")
    args = p.parse_args(argv)
    return replay(args.file, args.rate, args.host, args.only)


if __name__ == "__main__":
    sys.exit(main())
