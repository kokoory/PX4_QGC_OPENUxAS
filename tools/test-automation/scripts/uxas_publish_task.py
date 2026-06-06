#!/usr/bin/env python3
"""
Publish LMCP search tasks to OpenUxAS for Tier 1/3 scenarios.

The script connects to UxAS's PUSH endpoint (default tcp://127.0.0.1:5561),
optionally registers one or more vehicles (--register-from-config), sends an
AreaSearchTask / LineSearchTask / PointSearchTask, then an AutomationRequest
that hands those task IDs to the requested vehicles. UxAS responds with an
AutomationResponse and per-vehicle MissionCommand messages, which this
script also subscribes to (default tcp://127.0.0.1:5560) and prints.

Examples:

    # Tier 1 — single multicopter area search
    python3 uxas_publish_task.py area \\
        --vehicles 1 \\
        --polygon 47.397,8.545 47.398,8.545 47.398,8.546 47.397,8.546 \\
        --altitude 60

    # Tier 1 — three multicopters split a polygon
    python3 uxas_publish_task.py area --vehicles 1,2,3 \\
        --polygon 47.397,8.545 47.398,8.545 47.398,8.547 47.397,8.547 \\
        --altitude 60

    # Tier 3 — mixed fleet (fixed-wing fast + multicopter slow) sharing a polygon
    python3 uxas_publish_task.py area --vehicles 1,2,3,4,10 \\
        --polygon 47.396,8.543 47.399,8.543 47.399,8.549 47.396,8.549 \\
        --register-from-config ../configs/vehicles.json \\
        --altitude 80 --wait-secs 8

    # Line search along a road for a single fixed-wing
    python3 uxas_publish_task.py line --vehicles 4 \\
        --line 47.397,8.545 47.398,8.547 47.399,8.549 \\
        --altitude 120
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import threading
from pathlib import Path
from typing import Optional

SCRIPT_DIR = Path(__file__).resolve().parent
LMCP_PY_DIR = SCRIPT_DIR.parent / "lmcp_py"
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(LMCP_PY_DIR))

try:
    import zmq
except ImportError:
    print("ERROR: pyzmq required (pip install pyzmq)", file=sys.stderr)
    sys.exit(1)

from qgc_uxas_bridge import (
    UxASInterface, encode_envelope, decode_envelope,
    build_air_vehicle_configuration,
    build_area_search_task, build_line_search_task,
    build_automation_request,
    build_keep_in_zone, build_operating_region,
)
from lmcp import LMCPFactory
from afrl.cmasi.PointSearchTask import PointSearchTask
from afrl.cmasi.Location3D import Location3D
from afrl.cmasi.AltitudeType import AltitudeType


def _parse_points(s_list: list[str]) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    for s in s_list:
        try:
            a, b = s.split(",")
            pts.append((float(a), float(b)))
        except ValueError:
            raise SystemExit(f"bad lat,lon: {s!r}")
    return pts


def _capabilities_for(vehicle: dict, lmcp_defaults: dict) -> dict:
    t = vehicle["type"]
    cap = {**lmcp_defaults.get(t, {}), **vehicle.get("lmcp", {})}
    return {
        "min_speed": cap.get("min_speed_mps", 5.0),
        "max_speed": cap.get("max_speed_mps", 30.0),
        "nominal_speed": cap.get("nominal_speed_mps",
                                 (cap.get("min_speed_mps", 5)
                                  + cap.get("max_speed_mps", 30)) / 2),
        "min_alt": cap.get("min_alt_m", 0.0),
        "max_alt": cap.get("max_alt_m", 500.0),
        "nominal_alt": cap.get("nominal_alt_m", 100.0),
        "max_climb": cap.get("max_climb_mps", 5.0),
        "max_bank_deg": cap.get("max_bank_deg", 25.0),
    }


def register_vehicles_from_config(ux: UxASInterface, cfg_path: Path,
                                  vehicle_ids: list[int]) -> dict[int, str]:
    data = json.load(open(cfg_path))
    vehicles = {v["id"]: v for v in data["vehicles"]}
    defaults = data.get("lmcp_defaults", {})
    labels: dict[int, str] = {}
    for vid in vehicle_ids:
        if vid not in vehicles:
            print(f"  WARN: vehicle id {vid} not in config; skipping")
            continue
        v = vehicles[vid]
        cap = _capabilities_for(v, defaults)
        avc = build_air_vehicle_configuration(
            vehicle_id=vid, label=v["name"],
            min_speed=cap["min_speed"], max_speed=cap["max_speed"],
            nominal_speed=cap["nominal_speed"],
            nominal_altitude=cap["nominal_alt"],
            max_climb=cap["max_climb"],
            min_alt=cap["min_alt"], max_alt=cap["max_alt"],
            max_bank_deg=cap["max_bank_deg"],
        )
        ux.publish(avc)
        labels[vid] = f"{v['name']} ({v['type']})"
        print(f"  registered v{vid:<2d} {v['name']:22s} {v['type']:11s} "
              f"speed=[{cap['min_speed']:.0f},{cap['max_speed']:.0f}] m/s  "
              f"alt=[{cap['min_alt']:.0f},{cap['max_alt']:.0f}] m")
    return labels


def build_point_search_task(task_id: int, lat: float, lon: float,
                            alt: float, eligible: list[int]) -> PointSearchTask:
    t = PointSearchTask()
    t.set_TaskID(task_id)
    t.set_Label(f"PointSearch_{task_id}")
    loc = Location3D()
    loc.set_Latitude(lat); loc.set_Longitude(lon)
    loc.set_Altitude(alt); loc.set_AltitudeType(AltitudeType.MSL)
    t.set_SearchLocation(loc)
    for v in eligible:
        t.EligibleEntities.append(v)
    return t


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter,
                                 description=__doc__)
    ap.add_argument("kind", choices=["area", "line", "point"],
                    help="Task kind")
    ap.add_argument("--vehicles", required=True,
                    help="Comma-separated vehicle IDs (e.g. 1,2,3 or 1,2,4,10)")
    ap.add_argument("--polygon", nargs="+", default=None,
                    help="(area) polygon vertices as lat,lon pairs (>=3)")
    ap.add_argument("--line", nargs="+", default=None,
                    help="(line) line points as lat,lon pairs (>=2)")
    ap.add_argument("--point", default=None,
                    help="(point) lat,lon")
    ap.add_argument("--altitude", type=float, default=80.0,
                    help="Search altitude m (default 80)")
    ap.add_argument("--task-id", type=int, default=200,
                    help="LMCP TaskID base")
    ap.add_argument("--request-id", type=int, default=3000,
                    help="LMCP RequestID base")
    ap.add_argument("--uxas-pub", default="tcp://127.0.0.1:5560",
                    help="UxAS PUB endpoint (we SUB)")
    ap.add_argument("--uxas-pull", default="tcp://127.0.0.1:5561",
                    help="UxAS PULL endpoint (we PUSH)")
    ap.add_argument("--register-from-config", default=None,
                    help="Path to vehicles.json: register AirVehicleConfigurations "
                         "for the requested vehicle IDs first")
    ap.add_argument("--with-operating-region", default=None,
                    help="Publish a KeepInZone + OperatingRegion before the task. "
                         "Value: 'center_lat,center_lon[,half_size_m]' "
                         "(default half_size_m=2000)")
    ap.add_argument("--region-id", type=int, default=801,
                    help="LMCP OperatingRegion.ID (default 801)")
    ap.add_argument("--zone-id", type=int, default=701,
                    help="LMCP KeepInZone.ZoneID (default 701)")
    ap.add_argument("--wait-secs", type=float, default=6.0,
                    help="Seconds to listen for UxAS response after publish")
    ap.add_argument("--out", default=None,
                    help="Write captured LMCP responses summary to JSON")
    args = ap.parse_args(argv)

    vehicle_ids = [int(x) for x in args.vehicles.split(",") if x.strip()]

    # Connect to UxAS (with a dummy entity_id; this CLI is a controller,
    # not a vehicle — we only publish tasks/requests and observe).
    ux = UxASInterface(sub_addr=args.uxas_pub, push_addr=args.uxas_pull,
                       entity_id=999, service_id=0,
                       source_group="TaskPublisher")

    captured: list[dict] = []
    by_kind: dict[str, int] = {}

    def on_msg(env, obj):
        kind = type(obj).__name__ if obj is not None else "raw"
        by_kind[kind] = by_kind.get(kind, 0) + 1
        rec = {"descriptor": env.descriptor, "kind": kind,
               "src_entity": env.entity_id, "src_group": env.source_group}
        if obj is not None:
            try:
                if kind == "MissionCommand":
                    rec["vehicle_id"] = obj.get_VehicleID()
                    rec["num_waypoints"] = len(obj.get_WaypointList())
                    rec["first_wp"] = obj.get_FirstWaypoint()
                elif kind == "AutomationResponse":
                    rec["num_missions"] = len(obj.get_MissionCommandList())
                    rec["info_size"] = len(obj.get_Info())
                    rec["info"] = [
                        {"key": kvp.get_Key(), "value": kvp.get_Value()}
                        for kvp in obj.get_Info()
                    ]
                elif kind == "AirVehicleConfiguration":
                    rec["vehicle_id"] = obj.get_ID()
                elif kind == "ServiceStatus":
                    pass
            except Exception as exc:
                rec["decode_error"] = str(exc)
        captured.append(rec)
        prefix = f"  RECV  {kind:25s}"
        if kind == "MissionCommand":
            print(f"{prefix}  -> v{rec.get('vehicle_id','?'):<3} "
                  f"wp_count={rec.get('num_waypoints','?')}")
        elif kind == "AutomationResponse":
            info_str = ""
            if rec.get("info"):
                info_str = "  info=" + "; ".join(
                    f"{i.get('key')}={i.get('value')!r}"
                    for i in rec["info"])
            print(f"{prefix}  missions={rec.get('num_missions','?')}{info_str}")
        else:
            print(f"{prefix}  src=({env.source_group!r},{env.entity_id!r})")

    ux.on_message(on_msg)
    ux.start()
    time.sleep(0.5)  # ZMQ slow joiner

    if args.register_from_config:
        cfg_path = Path(args.register_from_config)
        if not cfg_path.is_absolute():
            cfg_path = SCRIPT_DIR / cfg_path
        print(f"--- Registering {len(vehicle_ids)} vehicle(s) from {cfg_path} ---")
        register_vehicles_from_config(ux, cfg_path, vehicle_ids)
        time.sleep(1.0)

    operating_region_id = 0
    if args.with_operating_region:
        parts = [p.strip() for p in args.with_operating_region.split(",")]
        if len(parts) < 2:
            raise SystemExit("--with-operating-region needs 'lat,lon[,half_size_m]'")
        c_lat = float(parts[0]); c_lon = float(parts[1])
        half_size = float(parts[2]) if len(parts) >= 3 else 2000.0
        kiz = build_keep_in_zone(zone_id=args.zone_id,
                                  center_lat=c_lat, center_lon=c_lon,
                                  half_size_m=half_size,
                                  min_alt_m=0.0, max_alt_m=500.0)
        region = build_operating_region(region_id=args.region_id,
                                         keep_in_ids=[args.zone_id])
        print(f"--- PUBLISH KeepInZone id={args.zone_id} center=({c_lat:.5f},{c_lon:.5f}) "
              f"half_size={half_size:.0f}m ---")
        ux.publish(kiz)
        print(f"--- PUBLISH OperatingRegion id={args.region_id} KeepIn=[{args.zone_id}] ---")
        ux.publish(region)
        operating_region_id = args.region_id
        time.sleep(1.0)

    if args.kind == "area":
        if not args.polygon or len(args.polygon) < 3:
            raise SystemExit("--polygon needs at least 3 lat,lon points")
        polygon = _parse_points(args.polygon)
        task = build_area_search_task(
            task_id=args.task_id, polygon_lat_lon=polygon,
            eligible_vehicles=vehicle_ids, altitude_m=args.altitude)
        print(f"--- PUBLISH AreaSearchTask id={args.task_id} polygon_pts={len(polygon)} "
              f"eligible={vehicle_ids} alt={args.altitude}m ---")
        ux.publish(task)
    elif args.kind == "line":
        if not args.line or len(args.line) < 2:
            raise SystemExit("--line needs at least 2 lat,lon points")
        line_pts = _parse_points(args.line)
        task = build_line_search_task(
            task_id=args.task_id, line_lat_lon=line_pts,
            eligible_vehicles=vehicle_ids, altitude_m=args.altitude)
        print(f"--- PUBLISH LineSearchTask id={args.task_id} line_pts={len(line_pts)} "
              f"eligible={vehicle_ids} alt={args.altitude}m ---")
        ux.publish(task)
    elif args.kind == "point":
        if not args.point:
            raise SystemExit("--point lat,lon required")
        lat, lon = _parse_points([args.point])[0]
        task = build_point_search_task(
            task_id=args.task_id, lat=lat, lon=lon,
            alt=args.altitude, eligible=vehicle_ids)
        print(f"--- PUBLISH PointSearchTask id={args.task_id} "
              f"loc=({lat:.5f},{lon:.5f}) eligible={vehicle_ids} ---")
        ux.publish(task)

    req = build_automation_request(
        request_id=args.request_id, task_ids=[args.task_id],
        vehicle_ids=vehicle_ids,
        operating_region=operating_region_id)
    print(f"--- PUBLISH AutomationRequest id={args.request_id} "
          f"operating_region={operating_region_id} ---")
    ux.publish(req)

    print(f"--- Listening for UxAS response for {args.wait_secs}s ---")
    t_end = time.time() + args.wait_secs
    while time.time() < t_end:
        time.sleep(0.1)

    print("\n--- Summary ---")
    print(f"pushed by us : {ux.stats.lmcp_msgs_out}")
    print(f"received     : {ux.stats.lmcp_msgs_in}")
    for k, c in sorted(by_kind.items(), key=lambda kv: -kv[1]):
        print(f"  {k:30s} {c}")

    ux.stop()

    if args.out:
        out = {
            "kind": args.kind,
            "vehicle_ids": vehicle_ids,
            "task_id": args.task_id,
            "request_id": args.request_id,
            "altitude_m": args.altitude,
            "messages_sent": ux.stats.lmcp_msgs_out,
            "messages_received": ux.stats.lmcp_msgs_in,
            "by_kind": by_kind,
            "captured": captured,
        }
        Path(args.out).write_text(json.dumps(out, indent=2, default=str))
        print(f"summary written to {args.out}")

    # Exit code reflects whether UxAS produced any MissionCommand for our request
    missions = by_kind.get("MissionCommand", 0)
    if missions == 0:
        print("\nNOTE: no MissionCommand received. Possible reasons:")
        print("  - UxAS process not running on the given endpoints")
        print("  - vehicles not registered (use --register-from-config)")
        print("  - WaypointPlanManagerService entries missing for these IDs")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
