#!/usr/bin/env python3
"""Send dummy AirVehicleState (and AirVehicleConfiguration) for one or more
vehicles to UxAS, at 2 Hz, so the planner sees an EntityState and will
respond to AutomationRequests. For headless UxAS-only smoke tests.

Usage:
    dummy_vehicle_state.py --vehicles 1,2,3 \
        --lat 34.61167 --lon 127.206028 --alt 80

Reads capabilities from configs/vehicles.json (so per-vehicle min/max
speed/altitude are correct for UxAS feasibility checks).
"""

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
TA_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(TA_DIR / "lmcp_py"))

from qgc_uxas_bridge import (  # type: ignore[reportMissingImports]
    UxASInterface,
    VehicleState,
    build_air_vehicle_configuration,
    build_air_vehicle_state,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vehicles", required=True,
                    help="Comma-separated vehicle IDs (e.g. 1 or 1,2,4)")
    ap.add_argument("--lat", type=float, default=34.61167)
    ap.add_argument("--lon", type=float, default=127.206028)
    ap.add_argument("--alt", type=float, default=80.0,
                    help="Altitude AMSL m for dummy state")
    ap.add_argument("--config", default=str(TA_DIR / "configs/vehicles.json"))
    ap.add_argument("--uxas-pub", default="tcp://127.0.0.1:5560")
    ap.add_argument("--uxas-pull", default="tcp://127.0.0.1:5561")
    ap.add_argument("--rate", type=float, default=2.0)
    args = ap.parse_args()

    vehicle_ids = [int(x) for x in args.vehicles.split(",") if x.strip()]
    cfg = json.load(open(args.config))
    vehicles = {v["id"]: v for v in cfg["vehicles"]}
    defaults = cfg.get("lmcp_defaults", {})

    ux = UxASInterface(args.uxas_pub, args.uxas_pull, entity_id=vehicle_ids[0])
    ux.start()
    time.sleep(0.5)

    # send AirVehicleConfiguration once per vehicle
    for vid in vehicle_ids:
        v = vehicles.get(vid)
        if not v:
            print(f"  WARN vehicle {vid} not in config")
            continue
        cap = v.get("capabilities", {})
        avc = build_air_vehicle_configuration(
            vehicle_id=vid, label=v["name"],
            min_speed=cap.get("min_speed_mps", defaults.get("min_speed_mps", 5)),
            max_speed=cap.get("max_speed_mps", defaults.get("max_speed_mps", 30)),
            nominal_speed=cap.get("nominal_speed_mps",
                                  (cap.get("min_speed_mps", 5)
                                   + cap.get("max_speed_mps", 30)) / 2),
            nominal_altitude=cap.get("nominal_alt_m", 100),
            max_climb=cap.get("max_climb_mps", 5),
            min_alt=cap.get("min_alt_m", 0),
            max_alt=cap.get("max_alt_m", 500),
            max_bank_deg=cap.get("max_bank_deg", 25),
        )
        ux.publish(avc)
        print(f"  AVC v{vid} {v['name']}")

    # build a VehicleState for each
    states = {}
    for vid in vehicle_ids:
        s = VehicleState(vehicle_id=vid)
        s.lat_deg = args.lat
        s.lon_deg = args.lon + 0.0001 * (vid - 1)  # slight offset per vehicle
        s.alt_m = args.alt
        s.heading_deg = 0.0
        s.airspeed_mps = 10.0
        s.groundspeed_mps = 10.0
        s.climb_rate_mps = 0.0
        s.pitch_deg = 0.0
        s.roll_deg = 0.0
        s.battery_pct = 100.0
        s.timestamp_ms = int(time.time() * 1000)
        states[vid] = s

    print(f"  Streaming AirVehicleState @ {args.rate} Hz "
          f"for v{','.join(map(str, vehicle_ids))} ... (Ctrl+C to stop)")

    period = 1.0 / args.rate
    try:
        while True:
            for vid, s in states.items():
                s.timestamp_ms = int(time.time() * 1000)
                avs = build_air_vehicle_state(s)
                ux.publish(avs)
            time.sleep(period)
    except KeyboardInterrupt:
        print("Bye.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
