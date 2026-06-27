#!/usr/bin/env python3
"""EventBroadcaster record/replay — play back a recorded QGC session so the
vehicle does exactly what the operator did, in the original order and timing.

RECORD (capture every button press + event as it happens):
    python3 eb_monitor.py --save session.jsonl        # operate QGC normally

REPLAY (re-drive QGC from the recording):
    python3 eb_replay.py session.jsonl                 # original timing
    python3 eb_replay.py session.jsonl --rate 4        # 4x faster
    python3 eb_replay.py session.jsonl --only action   # only button presses

How it works: each recorded "action" event carries the numeric actionCode (+
sliderValue / optionChecked) that QGC's GuidedActionsController used. We send
that straight back to QGC's EventBroadcaster Rx port (45679); QGC's
onCommandReceived replays the exact guided action — identical to a human press.
Recorded "command" events (already in command form) are re-sent verbatim.

Timing comes from each event's recorded "timestamp"; the gap between events is
preserved (scaled by --rate).
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
import time


def _load(path: str) -> list[dict]:
    out = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def _to_command(evt: dict):
    """Map a recorded broadcast event → a command dict for the Rx port, or None
    if it is not a replayable action/command."""
    cat = evt.get("category")
    data = evt.get("data") or {}
    if cat == "command":
        # already a command; data holds the action + params
        cmd = dict(data)
        cmd.setdefault("action", evt.get("event", ""))
        return cmd if cmd.get("action") else None
    if cat == "action":
        cmd = {"action": evt.get("event", "")}
        # actionCode drives QGC's faithful-replay path; pass slider/option too.
        if "actionCode" in data:
            cmd["actionCode"] = data["actionCode"]
        if "sliderValue" in data:
            cmd["sliderValue"] = data["sliderValue"]
        if "optionChecked" in data:
            cmd["optionChecked"] = data["optionChecked"]
        return cmd if (cmd.get("action") or "actionCode" in cmd) else None
    if cat == "uxas_ui":
        # Panel control → QGC's _uxHandleCommand applies it (ux_<field>).
        ev = evt.get("event", "")
        if not ev:
            return None
        cmd = {"action": "ux_" + ev}
        for k in ("value", "kind", "shape", "mode", "name", "selected"):
            if k in data:
                cmd[k] = data[k]
        return cmd
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("recording", help="JSONL file captured by eb_monitor --save")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=45679,
                    help="EventBroadcaster Rx command port (default 45679)")
    ap.add_argument("--only", default="action,command,uxas_ui",
                    help="Categories to replay (default: action,command,uxas_ui)")
    ap.add_argument("--rate", type=float, default=1.0, help="time scale (default 1.0)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    only = {c.strip() for c in args.only.split(",") if c.strip()}
    events = [e for e in _load(args.recording) if e.get("category") in only]
    if not events:
        print(f"[eb_replay] no replayable events ({', '.join(sorted(only))}) in {args.recording}")
        return 1

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    addr = (args.host, args.port)
    print(f"[eb_replay] {len(events)} event(s) from {args.recording} -> {args.host}:{args.port}"
          + (f"  (rate {args.rate}x)" if args.rate != 1.0 else "")
          + ("  [DRY-RUN]" if args.dry_run else ""))

    prev_ts = None
    for evt in events:
        ts = float(evt.get("timestamp", 0) or 0)
        if prev_ts is not None and ts >= prev_ts:
            gap = (ts - prev_ts) / args.rate
            if gap > 0:
                time.sleep(min(gap, 3600))
        prev_ts = ts
        cmd = _to_command(evt)
        if not cmd:
            continue
        payload = json.dumps(cmd, ensure_ascii=False)
        print(f"[{time.strftime('%H:%M:%S')}] -> {payload}")
        if not args.dry_run:
            sock.sendto(payload.encode("utf-8"), addr)

    print("[eb_replay] done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
