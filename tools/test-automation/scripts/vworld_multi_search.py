#!/usr/bin/env python3
"""Publish a multi-feature VWorld road/river search to OpenUxAS.

Unlike vworld_uxas_search.py (one chained sweep path), this groups the
VWorld features in a bbox by NAME (road_name / riv_nm), so each road or
river becomes its OWN LMCP task:

  - road  -> one LineSearchTask per road name (segments chained)
  - river -> one AreaSearchTask per river name (largest clipped polygon)

All tasks go out under a SINGLE AutomationRequest, so UxAS's
AssignmentTreeBranchBound spreads them across the mixed fleet — long
highways naturally fall to the fast fixed-wing (Cessna), tight short
streets to the multicopters (X500), purely from the cost optimisation
over each vehicle's registered speed / turn capability.

Selection: --names "강남대로,테헤란로" picks specific names; --names ALL
takes every named feature in the bbox. This is what the QGC 3D planning
panel's checklist / click-select / select-all all resolve to.

Examples:
    # All roads in a Gangnam box, mixed X500 + Cessna, UxAS auto-assigns
    VWORLD_KEY=... python3 vworld_multi_search.py road \\
        --vehicles 1,2,4 --names ALL \\
        --bbox 37.490,127.020,37.510,127.045 \\
        --register-from-config ../configs/vehicles.json \\
        --with-operating-region 37.500,127.032,3000

    # Just two named roads
    python3 vworld_multi_search.py road --vehicles 1,4 \\
        --names "강남대로,테헤란로" --bbox 37.49,127.02,37.51,127.045 --dry-run
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parent / "lmcp_py"))

import vworld_uxas_search as vu   # geometry helpers (fetch, clip, chain, decimate)


def roads_by_name(features, bbox, max_pts):
    """Group road segments by road_name, chain each into one [(lat,lon)] path."""
    groups = {}
    for f in features:
        nm = (f["properties"].get("road_name") or "").strip()
        if not nm:
            continue
        g = f["geometry"]
        lines = (g["coordinates"] if g["type"] == "MultiLineString"
                 else [g["coordinates"]] if g["type"] == "LineString" else [])
        for ln in lines:
            clipped = vu._clip_line_to_bbox(ln, bbox)
            if len(clipped) >= 2:
                groups.setdefault(nm, []).append(clipped)
    out = {}
    for nm, segs in groups.items():
        chained = vu._decimate(vu._chain_segments(segs), max_pts)
        if len(chained) >= 2:
            out[nm] = [(c[1], c[0]) for c in chained]
    return out


def rivers_by_name(features, bbox, max_pts):
    """Group river polygons by riv_nm, largest clipped ring each."""
    best = {}
    for f in features:
        nm = (f["properties"].get("riv_nm") or "").strip()
        if not nm:
            continue
        g = f["geometry"]
        polys = (g["coordinates"] if g["type"] == "MultiPolygon"
                 else [g["coordinates"]] if g["type"] == "Polygon" else [])
        for p in polys:
            clipped = vu._clip_ring_to_bbox(p[0], bbox)
            if len(clipped) >= 3:
                ln = vu._seg_len(clipped)
                if nm not in best or ln > best[nm][0]:
                    best[nm] = (ln, clipped)
    out = {}
    for nm, (_, ring) in best.items():
        ring = vu._decimate(ring, max_pts)
        out[nm] = [(c[1], c[0]) for c in ring]
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter, description=__doc__)
    ap.add_argument("kind", choices=["road", "river"])
    ap.add_argument("--vehicles", required=True)
    ap.add_argument("--bbox", required=True, help="min_lat,min_lon,max_lat,max_lon")
    ap.add_argument("--names", default="ALL",
                    help='Comma-separated names, or "ALL" for every named feature')
    ap.add_argument("--max-pts", type=int, default=30)
    ap.add_argument("--altitude", type=float, default=120.0)
    ap.add_argument("--key", default=os.environ.get("VWORLD_KEY", ""))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--with-operating-region", default=None,
                    help="center_lat,center_lon[,half_size_m]")
    ap.add_argument("--task-id-base", type=int, default=6200)
    ap.add_argument("--request-id", type=int, default=62000)
    ap.add_argument("--zone-id", type=int, default=6200)
    ap.add_argument("--region-id", type=int, default=62000)
    ap.add_argument("--wait-secs", type=float, default=305.0)
    ap.add_argument("--register-from-config", default=None)
    ap.add_argument("--uxas-pub", default="tcp://127.0.0.1:5560")
    ap.add_argument("--uxas-pull", default="tcp://127.0.0.1:5561")
    args = ap.parse_args(argv)

    if not args.key:
        raise SystemExit("VWorld key required (--key or VWORLD_KEY)")
    parts = [float(x) for x in args.bbox.split(",")]
    bbox = (min(parts[0], parts[2]), min(parts[1], parts[3]),
            max(parts[0], parts[2]), max(parts[1], parts[3]))
    vehicle_ids = [int(x) for x in args.vehicles.split(",") if x.strip()]
    want = None if args.names.strip().upper() == "ALL" else \
        {n.strip() for n in args.names.split(",") if n.strip()}

    layer = vu.ROAD_LAYER if args.kind == "road" else vu.RIVER_LAYER
    feats = vu.fetch_features(layer, bbox, args.key)
    if args.kind == "road":
        named = roads_by_name(feats, bbox, args.max_pts)
    else:
        named = rivers_by_name(feats, bbox, args.max_pts)
    if want is not None:
        named = {k: v for k, v in named.items() if k in want}
    if not named:
        raise SystemExit(f"No {args.kind} features matched in bbox "
                         f"(found {len(feats)}, names filter {args.names})")

    print(f"[multi] {args.kind}: {len(named)} feature(s) -> "
          f"{len(named)} task(s): {', '.join(list(named)[:8])}"
          + (" ..." if len(named) > 8 else ""))
    if args.dry_run:
        for nm, pts in named.items():
            print(f"  {nm}: {len(pts)} pts")
        return 0

    # --- Publish to UxAS ---------------------------------------------------
    from qgc_uxas_bridge import (
        UxASInterface, build_line_search_task, build_area_search_task,
        build_automation_request, build_keep_in_zone, build_operating_region)
    import uxas_publish_task as up

    ux = UxASInterface(sub_addr=args.uxas_pub, push_addr=args.uxas_pull,
                       entity_id=999, service_id=0, source_group="MultiPublisher")
    ux.start()
    time.sleep(0.5)

    if args.register_from_config:
        up.register_vehicles_from_config(ux, Path(args.register_from_config),
                                         vehicle_ids)
        time.sleep(1.0)

    operating_region = 0
    if args.with_operating_region:
        rp = args.with_operating_region.split(",")
        c_lat, c_lon = float(rp[0]), float(rp[1])
        half = float(rp[2]) if len(rp) > 2 else 3000.0
        kiz = build_keep_in_zone(zone_id=args.zone_id, center_lat=c_lat,
                                 center_lon=c_lon, half_size_m=half)
        ux.publish(kiz)
        ux.publish(build_operating_region(region_id=args.region_id,
                                          keep_in_ids=[args.zone_id]))
        operating_region = args.region_id
        time.sleep(1.0)

    task_ids = []
    for i, (nm, pts) in enumerate(named.items()):
        tid = args.task_id_base + i
        if args.kind == "road":
            task = build_line_search_task(tid, pts, vehicle_ids,
                                          altitude_m=args.altitude, label=nm)
        else:
            task = build_area_search_task(tid, pts, vehicle_ids,
                                          altitude_m=args.altitude, label=nm)
        ux.publish(task)
        task_ids.append(tid)
        print(f"  published task {tid} ({nm}, {len(pts)} pts)")
    time.sleep(0.5)

    req = build_automation_request(request_id=args.request_id, task_ids=task_ids,
                                   vehicle_ids=vehicle_ids,
                                   operating_region=operating_region)
    ux.publish(req)
    print(f"[multi] AutomationRequest {args.request_id}: "
          f"{len(task_ids)} tasks -> vehicles {vehicle_ids} (UxAS auto-assigns)")

    # Listen for UxAS's response via the callback interface (no blocking recv).
    import threading as _th
    done = _th.Event()

    def _on_msg(env, obj):
        cls = type(obj).__name__ if obj is not None else "raw"
        if cls in ("AutomationResponse", "MissionCommand"):
            print(f"  <- {cls}")
            if cls == "AutomationResponse":
                done.set()
    ux.on_message(_on_msg)

    done.wait(timeout=args.wait_secs)
    if not done.is_set():
        print("[multi] no AutomationResponse within "
              f"{args.wait_secs:.0f}s (is UxAS running on "
              f"{args.uxas_pub} / {args.uxas_pull}?)")
    ux.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
