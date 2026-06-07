#!/usr/bin/env python3
"""Bridge QGC mission planning to OpenUxAS.

QGC's Cesium 3D view broadcasts a "uxas_search" event over EventBroadcaster
(UDP 45678) when the operator right-clicks a point and picks an area / road /
river search. This listener receives that event, turns the clicked centre into
a concrete search geometry, and publishes the matching LMCP task to UxAS:

  - area  -> a square polygon around the centre      -> uxas_publish_task.py area
  - road  -> VWorld road network in a box round it   -> vworld_uxas_search.py road
  - river -> VWorld river surface in a box round it   -> vworld_uxas_search.py river

So the operator plans the mission entirely inside QGC; UxAS assigns the
vehicle and the qgc_uxas_bridge flies the resulting MissionCommand.

Broadcast payload (data fields), e.g.:
  {"category":"uxas_search","event":"area",
   "data":{"center_lat":34.612,"center_lon":127.207,"vehicles":"1",
           "altitude":80,"half_size_m":150,"region_radius":3000}}

Run alongside UxAS + SITL + bridge:
  VWORLD_KEY=<key> python3 uxas_search_listener.py
"""
from __future__ import annotations

import argparse
import json
import math
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent


def _meters_to_deg(center_lat, dm):
    dlat = dm / 111320.0
    dlon = dm / (111320.0 * max(0.1, math.cos(math.radians(center_lat))))
    return dlat, dlon


def _square_polygon(lat, lon, half_m):
    dlat, dlon = _meters_to_deg(lat, half_m)
    # CCW square: SW, SE, NE, NW
    return [(lat - dlat, lon - dlon), (lat - dlat, lon + dlon),
            (lat + dlat, lon + dlon), (lat + dlat, lon - dlon)]


class SearchDispatcher:
    def __init__(self, args):
        self.args = args
        self._task_seq = 0

    def _next_ids(self):
        self._task_seq += 1
        base = self.args.task_id_base + self._task_seq
        return base, base + 100000, base, base + 100000  # task, req, zone, region

    def dispatch(self, kind, data):
        lat = float(data["center_lat"])
        lon = float(data["center_lon"])
        vehicles = str(data.get("vehicles", self.args.vehicles))
        altitude = float(data.get("altitude", 80))
        half_m = float(data.get("half_size_m", 150))
        radius = float(data.get("region_radius", 3000))
        task_id, req_id, zone_id, region_id = self._next_ids()
        region = f"{lat:.7f},{lon:.7f},{radius:.0f}"

        common = [
            "--vehicles", vehicles, "--altitude", str(altitude),
            "--task-id", str(task_id), "--request-id", str(req_id),
            "--zone-id", str(zone_id), "--region-id", str(region_id),
            "--with-operating-region", region,
            "--wait-secs", str(self.args.wait_secs),
            "--uxas-pub", self.args.uxas_pub, "--uxas-pull", self.args.uxas_pull,
        ]

        if kind == "area":
            poly = _square_polygon(lat, lon, half_m)
            pts = [f"{a:.7f},{b:.7f}" for a, b in poly]
            cmd = [sys.executable, str(SCRIPT_DIR / "uxas_publish_task.py"),
                   "area", "--polygon", *pts] + common
        elif kind in ("road", "river"):
            dlat, dlon = _meters_to_deg(lat, half_m)
            bbox = f"{lat - dlat:.7f},{lon - dlon:.7f},{lat + dlat:.7f},{lon + dlon:.7f}"
            cmd = [sys.executable, str(SCRIPT_DIR / "vworld_uxas_search.py"),
                   kind, "--bbox", bbox] + common
            if not os.environ.get("VWORLD_KEY") and self.args.vworld_key:
                cmd += ["--key", self.args.vworld_key]
        else:
            print(f"[listener] unknown search kind: {kind}")
            return

        print(f"[listener] {kind} search @ ({lat:.5f},{lon:.5f}) "
              f"vehicles={vehicles} alt={altitude:.0f} -> publishing task {task_id}")
        # Run in a thread so a long --wait-secs doesn't block new events.
        threading.Thread(target=self._run, args=(cmd,), daemon=True).start()

    def _run(self, cmd):
        try:
            subprocess.call(cmd)
        except Exception as exc:
            print(f"[listener] publish failed: {exc}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, default=45678,
                    help="EventBroadcaster broadcast port (default 45678)")
    ap.add_argument("--vehicles", default="1",
                    help="Default vehicle IDs if the event omits them")
    ap.add_argument("--task-id-base", type=int, default=7000)
    ap.add_argument("--wait-secs", default="305")
    ap.add_argument("--uxas-pub", default="tcp://127.0.0.1:5560")
    ap.add_argument("--uxas-pull", default="tcp://127.0.0.1:5561")
    ap.add_argument("--vworld-key", default=os.environ.get("VWORLD_KEY", ""))
    args = ap.parse_args(argv)

    if args.vworld_key:
        os.environ.setdefault("VWORLD_KEY", args.vworld_key)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", args.port))
    dispatcher = SearchDispatcher(args)
    print(f"[listener] listening for QGC uxas_search events on UDP {args.port}")
    print(f"[listener] UxAS endpoint {args.uxas_pub} / {args.uxas_pull}")

    while True:
        try:
            raw, _addr = sock.recvfrom(65535)
        except KeyboardInterrupt:
            break
        try:
            msg = json.loads(raw.decode("utf-8"))
        except Exception:
            continue
        if msg.get("category") != "uxas_search":
            continue
        kind = msg.get("event")
        data = msg.get("data", {})
        try:
            dispatcher.dispatch(kind, data)
        except KeyError as exc:
            print(f"[listener] event missing field {exc}")
        except Exception as exc:
            print(f"[listener] dispatch error: {exc}")

    sock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
