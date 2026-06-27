#!/usr/bin/env python3
"""EventBroadcaster scenario player — drive QGC exactly as if a human pressed
the buttons, by sending timed commands to QGC's EventBroadcaster Rx UDP port
(default 45679).

QGC's GuidedActionsController already consumes EventBroadcaster.commandReceived
and runs the matching guided action, so a command sent here behaves the same as
the operator clicking the button. Supported actions (see the QGC switch):
    arm | disarm | takeoff (altitude) | land | rtl | start_mission | pause |
    set_mode (mode: "Hold"/"Mission"/"Return"/...)

A scenario is JSON:
    {
      "name": "demo",
      "steps": [
        {"wait": 0,  "action": "set_mode", "mode": "Hold"},
        {"wait": 2,  "action": "arm"},
        {"wait": 3,  "action": "takeoff", "altitude": 40},
        {"wait": 25, "action": "start_mission"},
        {"wait": 90, "action": "rtl"}
      ]
    }
Each step's "wait" = seconds to pause BEFORE sending it (relative). Use "at" for
seconds from the scenario start instead. Every field except wait/at/comment is
sent as the command JSON (so "action" + its params go on the wire verbatim).

Usage:
    python3 eb_scenario.py scenarios/demo.json
    python3 eb_scenario.py demo.json --host 127.0.0.1 --port 45679
    python3 eb_scenario.py --action takeoff --altitude 40   # one-shot, no file
    python3 eb_scenario.py demo.json --dry-run
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
import time


def _send(sock, addr, cmd: dict, dry: bool) -> None:
    payload = json.dumps(cmd, ensure_ascii=False)
    stamp = time.strftime("%H:%M:%S")
    print(f"[{stamp}] -> {payload}")
    if not dry:
        sock.sendto(payload.encode("utf-8"), addr)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("scenario", nargs="?", help="scenario JSON file")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=45679,
                    help="EventBroadcaster Rx command port (default 45679)")
    ap.add_argument("--action", default="",
                    help="Send a single command instead of a file (e.g. --action arm)")
    ap.add_argument("--altitude", type=float, help="altitude param for --action takeoff")
    ap.add_argument("--mode", help="mode param for --action set_mode")
    ap.add_argument("--rate", type=float, default=1.0,
                    help="time scale (2.0 = twice as fast; default 1.0)")
    ap.add_argument("--dry-run", action="store_true", help="print, do not send")
    args = ap.parse_args(argv)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    addr = (args.host, args.port)

    # One-shot single command (no scenario file).
    if args.action:
        cmd = {"action": args.action}
        if args.altitude is not None:
            cmd["altitude"] = args.altitude
        if args.mode:
            cmd["mode"] = args.mode
        _send(sock, addr, cmd, args.dry_run)
        return 0

    if not args.scenario:
        ap.error("provide a scenario file or use --action")

    with open(args.scenario) as fh:
        scn = json.load(fh)
    steps = scn.get("steps", [])
    name = scn.get("name", args.scenario)
    print(f"[eb_scenario] '{name}': {len(steps)} step(s) -> {args.host}:{args.port}"
          + (f"  (rate {args.rate}x)" if args.rate != 1.0 else "")
          + ("  [DRY-RUN]" if args.dry_run else ""))

    t0 = time.monotonic()
    for i, step in enumerate(steps):
        # Schedule: absolute "at" (s from start) wins over relative "wait".
        if "at" in step:
            target = t0 + float(step["at"]) / args.rate
            delay = max(0.0, target - time.monotonic())
        else:
            delay = float(step.get("wait", 0)) / args.rate
        if delay > 0:
            time.sleep(delay)
        cmd = {k: v for k, v in step.items() if k not in ("wait", "at", "comment")}
        if "action" not in cmd:
            print(f"[eb_scenario] step {i} has no 'action' — skipping")
            continue
        _send(sock, addr, cmd, args.dry_run)

    print("[eb_scenario] done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
