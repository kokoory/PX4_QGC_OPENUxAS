#!/usr/bin/env python3
"""EventBroadcaster monitor — a standalone, time-ordered view of everything QGC
broadcasts on the EventBroadcaster Tx UDP port (default 45678).

QGC's EventBroadcaster emits one JSON datagram per event:
    {"seq":N, "timestamp":<unix sec>, "category":"action"|"command"|
     "qgc_mission"|"uxas_search"|..., "event":"<name>", "data":{...}}
Every guided-action button press (arm / takeoff / land / RTL / goto / orbit /
pause / change_altitude / change_speed / start_mission / ...) shows up here, so
this is the external "what did the operator press, in order" log the in-app
Message Monitor shows internally.

Usage:
    python3 eb_monitor.py                      # listen on 0.0.0.0:45678
    python3 eb_monitor.py --port 45678 \\
        --only action,command                  # filter to categories
    python3 eb_monitor.py --save session.jsonl # also append raw JSON lines
    python3 eb_monitor.py --no-color
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import socket
import sys

_COLORS = {
    "action":     "\033[96m",   # cyan  — operator button presses
    "command":    "\033[93m",   # yellow — inbound commands (scenario/replay)
    "qgc_mission":"\033[95m",   # magenta — mission edits/uploads
    "uxas_search":"\033[92m",   # green — UxAS search publishes
    "mavlink":    "\033[94m",   # blue  — vehicle command results
    "_reset":     "\033[0m",
}


def _fmt_time(ts: float) -> str:
    try:
        return _dt.datetime.fromtimestamp(ts).strftime("%H:%M:%S.%f")[:-3]
    except Exception:
        return "--:--:--.---"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=45678,
                    help="EventBroadcaster Tx broadcast port (default 45678)")
    ap.add_argument("--only", default="",
                    help="Comma-separated categories to show (default: all)")
    ap.add_argument("--save", default="",
                    help="Append every received datagram as a JSONL line to this file")
    ap.add_argument("--no-color", action="store_true")
    args = ap.parse_args(argv)

    only = {c.strip() for c in args.only.split(",") if c.strip()}
    color = (not args.no_color) and sys.stdout.isatty()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    except (AttributeError, OSError):
        pass
    sock.bind((args.host, args.port))

    print(f"[eb_monitor] listening on {args.host}:{args.port}"
          + (f"  (only: {', '.join(sorted(only))})" if only else "")
          + (f"  → {args.save}" if args.save else ""))
    print(f"{'Time':<13} {'Category':<12} {'Event':<22} Data")
    print("-" * 90)

    save_fh = open(args.save, "a") if args.save else None
    _last_key = None   # de-dup: the same datagram can arrive twice on localhost
    try:
        while True:
            raw, _ = sock.recvfrom(65535)
            line = raw.decode("utf-8", "replace").strip()
            try:
                msg = json.loads(line)
            except Exception:
                continue
            key = (msg.get("seq"), msg.get("category"), msg.get("event"))
            if key == _last_key and key[0] is not None:
                continue
            _last_key = key
            cat = msg.get("category", "?")
            if only and cat not in only:
                continue
            if save_fh:
                save_fh.write(line + "\n")
                save_fh.flush()
            ts = msg.get("timestamp", 0)
            ev = msg.get("event", "")
            data = msg.get("data", {})
            data_str = json.dumps(data, ensure_ascii=False) if data else ""
            c0 = _COLORS.get(cat, "") if color else ""
            c1 = _COLORS["_reset"] if color else ""
            print(f"{_fmt_time(ts):<13} {c0}{cat:<12}{c1} {ev:<22} {data_str}", flush=True)
    except KeyboardInterrupt:
        print("\n[eb_monitor] stopped")
    finally:
        if save_fh:
            save_fh.close()
        sock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
