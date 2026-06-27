#!/usr/bin/env python3
"""Heterogeneous SAR (Search-And-Rescue) tasking for OpenUxAS.

Splits ONE region across vehicle types by capability, in a SINGLE
AutomationRequest, so UxAS assigns each tier to the right airframe and jointly
optimises routes:

  - fixed-wing : broad AreaSearch over the WHOLE region, HIGH altitude, WIDE FOV
                 (fast, long endurance — the first wide sweep)
  - multicopter: detailed AreaSearch over the inner priority CORE, LOW altitude,
                 NARROW FOV (close inspection where a person is most likely)
  - ground rover: LineSearch along the VWorld roads in the region (follows roads)

Each task is restricted to its tier via EligibleEntities, so UxAS never asks a
multicopter to fly the wide sweep or a fixed-wing to crawl the core. Per-tier
altitude is applied by re-registering each tier's vehicles at its altitude, and
per-tier camera FOV is written to /tmp/uxas_camera_fov_<id>.txt (the bridge reads
it → UxAS lane spacing matches each tier's footprint).

Spawned by uxas_search_listener on a {"action":"sar_search", ...} publish.
"""
from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

import vworld_uxas_search as vu
import vworld_multi_search as vm
from qgc_uxas_bridge import (
    UxASInterface, build_area_search_task, build_line_search_task,
    build_automation_request, build_keep_in_zone, build_operating_region)
import uxas_publish_task as up


def _rect_ring(bbox, shrink=1.0):
    """bbox=(s,w,n,e) → rectangle ring [(lat,lon)...] shrunk about its centre."""
    s, w, n, e = bbox
    clat, clon = (s + n) / 2, (w + e) / 2
    hs, hw = (n - s) / 2 * shrink, (e - w) / 2 * shrink
    return [(clat - hs, clon - hw), (clat - hs, clon + hw),
            (clat + hs, clon + hw), (clat + hs, clon - hw)]


def _write_fov(vid, fov):
    try:
        with open(f"/tmp/uxas_camera_fov_{vid}.txt", "w") as fh:
            fh.write(str(float(fov)))
    except OSError:
        pass


def _ids(s):
    return [int(x) for x in str(s).split(",") if str(x).strip()]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--bbox", required=True, help="min_lat,min_lon,max_lat,max_lon")
    ap.add_argument("--fw-ids", default="")
    ap.add_argument("--mc-ids", default="")
    ap.add_argument("--ugv-ids", default="")
    ap.add_argument("--fw-alt", type=float, default=250.0)
    ap.add_argument("--mc-alt", type=float, default=60.0)
    ap.add_argument("--fw-fov", type=float, default=45.0)
    ap.add_argument("--mc-fov", type=float, default=20.0)
    ap.add_argument("--mc-inner", type=float, default=0.45,
                    help="inner fraction of the region the multicopters cover in detail")
    ap.add_argument("--names", default="ALL")
    ap.add_argument("--max-pts", type=int, default=30)
    ap.add_argument("--key", default=os.environ.get("VWORLD_KEY", ""))
    ap.add_argument("--task-id-base", type=int, default=8000)
    ap.add_argument("--request-id", type=int, default=108000)
    ap.add_argument("--register-from-config", default=None)
    ap.add_argument("--uxas-pub", default="tcp://127.0.0.1:5560")
    ap.add_argument("--uxas-pull", default="tcp://127.0.0.1:5561")
    args = ap.parse_args(argv)

    parts = [float(x) for x in args.bbox.split(",")]
    bbox = (min(parts[0], parts[2]), min(parts[1], parts[3]),
            max(parts[0], parts[2]), max(parts[1], parts[3]))
    fw, mc, ugv = _ids(args.fw_ids), _ids(args.mc_ids), _ids(args.ugv_ids)
    all_ids = fw + mc + ugv
    if not all_ids:
        raise SystemExit("SAR needs at least one of --fw-ids/--mc-ids/--ugv-ids")

    print(f"[SAR] region {bbox}  fixed-wing={fw}@{args.fw_alt}m/{args.fw_fov}° "
          f"multicopter={mc}@{args.mc_alt}m/{args.mc_fov}° rover={ugv}")

    ux = UxASInterface(sub_addr=args.uxas_pub, push_addr=args.uxas_pull,
                       entity_id=998, service_id=0, source_group="SARPublisher")
    ux.start()
    time.sleep(0.5)

    # Per-tier altitude (re-register each tier at its own NominalAltitude) + FOV.
    if args.register_from_config:
        cfg = Path(args.register_from_config)
        if fw:
            up.register_vehicles_from_config(ux, cfg, fw, altitude=args.fw_alt)
        if mc:
            up.register_vehicles_from_config(ux, cfg, mc, altitude=args.mc_alt)
        if ugv:
            up.register_vehicles_from_config(ux, cfg, ugv, altitude=0.0)
        time.sleep(1.0)
    for v in fw:
        _write_fov(v, args.fw_fov)
    for v in mc:
        _write_fov(v, args.mc_fov)

    # OperatingRegion (KeepInZone over the whole search area). UxAS rejects an
    # AutomationRequest as "Not Ready" without a valid region, so build one big
    # enough to contain every tier's coverage.
    import math
    clat, clon = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    span_m = max((bbox[2] - bbox[0]) * 111320.0,
                 (bbox[3] - bbox[1]) * 111320.0 * math.cos(math.radians(clat)))
    half_m = max(3000.0, span_m)
    zone_id = args.task_id_base + 900
    region_id = args.request_id + 900
    ux.publish(build_keep_in_zone(zone_id=zone_id, center_lat=clat,
                                  center_lon=clon, half_size_m=half_m))
    ux.publish(build_operating_region(region_id=region_id, keep_in_ids=[zone_id]))
    time.sleep(1.0)

    task_ids = []
    tid = args.task_id_base

    # Tier 1 — fixed-wing: broad sweep over the whole region (high, wide).
    if fw:
        t = build_area_search_task(tid, _rect_ring(bbox, 1.0), fw,
                                   altitude_m=args.fw_alt, label="SAR-wide(FW)")
        ux.publish(t)
        task_ids.append(tid)
        print(f"  task {tid}: fixed-wing AreaSearch (whole region) alt={args.fw_alt} -> {fw}")
        tid += 1

    # Tier 2 — multicopter: detailed core (low, narrow).
    if mc:
        t = build_area_search_task(tid, _rect_ring(bbox, max(0.1, min(1.0, args.mc_inner))),
                                   mc, altitude_m=args.mc_alt, label="SAR-core(MC)")
        ux.publish(t)
        task_ids.append(tid)
        print(f"  task {tid}: multicopter AreaSearch (inner {args.mc_inner:.0%}) alt={args.mc_alt} -> {mc}")
        tid += 1

    # Tier 3 — ground rover: follow VWorld roads in the region.
    if ugv:
        try:
            feats = vu.fetch_features(vu.ROAD_LAYER, bbox, args.key)
            roads = vm.roads_by_name(feats, bbox, args.max_pts)
        except Exception as exc:
            roads = {}
            print(f"  [warn] road fetch failed for rover tier: {exc}")
        for nm, pts in list(roads.items())[:3]:
            t = build_line_search_task(tid, pts, ugv, altitude_m=0.0,
                                       label=f"SAR-road(UGV):{nm}")
            ux.publish(t)
            task_ids.append(tid)
            print(f"  task {tid}: rover LineSearch '{nm}' -> {ugv}")
            tid += 1

    if not task_ids:
        raise SystemExit("SAR produced no tasks (no vehicles / no roads)")

    time.sleep(0.5)
    req = build_automation_request(request_id=args.request_id, task_ids=task_ids,
                                   vehicle_ids=all_ids, operating_region=region_id)
    ux.publish(req)
    print(f"[SAR] AutomationRequest {args.request_id}: {len(task_ids)} task(s) "
          f"across {len(all_ids)} vehicle(s) {all_ids} — UxAS assigns by capability")
    time.sleep(3.0)   # let ZMQ flush; the bridges handle the AutomationResponse
    ux.stop()
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
