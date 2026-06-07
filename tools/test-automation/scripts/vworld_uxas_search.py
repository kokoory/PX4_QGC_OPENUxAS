#!/usr/bin/env python3
"""Drive an OpenUxAS road / river search over real VWorld (브이월드) geometry.

Fetches a road (LT_L_MOCTLINK, MultiLineString) or river (LT_C_WKMSTRM,
MultiPolygon) from the Korean VWorld Data API, simplifies it into a search
path / area, and hands it to uxas_publish_task.py as a LineSearchTask (road)
or AreaSearchTask (river). UxAS then assigns the eligible vehicles and the
qgc_uxas_bridge flies the resulting MissionCommand — exactly the OpenUxAS
"waterway search" pattern, but sourced from live Korean map data.

The VWorld Data API caps geomFilter at 10 km², so --bbox must stay small
(roughly 0.03° per side at Korean latitudes). Provide a VWorld key via
--key or the VWORLD_KEY environment variable.

Examples:
    # Search a road near the Goheung sim area with vehicle 1
    python3 vworld_uxas_search.py road --vehicles 1 \\
        --bbox 34.605,127.200,34.620,127.215 --altitude 120 \\
        --with-operating-region 34.6125,127.2075,3000

    # Search the Han river surface with a fixed-wing
    python3 vworld_uxas_search.py river --vehicles 4 --name 한강 \\
        --bbox 37.515,126.930,37.535,126.975 --altitude 150 \\
        --with-operating-region 37.525,126.952,4000
"""
from __future__ import annotations

import argparse
import math
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_API = "https://api.vworld.kr/req/data"
ROAD_LAYER = "LT_L_MOCTLINK"   # MultiLineString, attr road_name
RIVER_LAYER = "LT_C_WKMSTRM"   # MultiPolygon, attr riv_nm


def fetch_features(layer: str, bbox, key: str, name_attr=None, name=None):
    """bbox = (min_lat, min_lon, max_lat, max_lon). Returns GeoJSON features."""
    min_lat, min_lon, max_lat, max_lon = bbox
    geom = f"BOX({min_lon},{min_lat},{max_lon},{max_lat})"
    params = {
        "service": "data", "version": "2.0", "request": "GetFeature",
        "format": "json", "size": "1000", "page": "1", "data": layer,
        "geometry": "true", "attribute": "true", "crs": "EPSG:4326",
        "geomFilter": geom, "key": key, "domain": "localhost",
    }
    url = DATA_API + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=30) as r:
        import json
        data = json.load(r)
    resp = data.get("response", {})
    if resp.get("status") != "OK":
        err = resp.get("error", {})
        raise SystemExit(f"VWorld error: {err.get('code')} {err.get('text')}")
    feats = resp["result"]["featureCollection"]["features"]
    if name_attr and name:
        feats = [f for f in feats
                 if name in (f["properties"].get(name_attr) or "")]
    return feats


def _seg_len(seg):
    return sum(math.dist(seg[i], seg[i + 1]) for i in range(len(seg) - 1))


def _clip_ring_to_bbox(ring, bbox):
    """Sutherland-Hodgman clip of a [lon,lat] ring to the bbox. VWorld river
    features span the WHOLE river (e.g. 한강 from Seoul to the eastern
    mountains), so we must clip to the requested box to get the local reach."""
    min_lat, min_lon, max_lat, max_lon = bbox

    def clip(poly, inside, isect):
        out = []
        n = len(poly)
        for i in range(n):
            cur, prv = poly[i], poly[i - 1]
            if inside(cur):
                if not inside(prv):
                    out.append(isect(prv, cur))
                out.append(cur)
            elif inside(prv):
                out.append(isect(prv, cur))
        return out

    def ix(p, q, axis, val):
        # interpolate p->q at coordinate axis==val (0=lon, 1=lat)
        t = (val - p[axis]) / (q[axis] - p[axis]) if q[axis] != p[axis] else 0.0
        return [p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])]

    poly = list(ring)
    poly = clip(poly, lambda c: c[0] >= min_lon, lambda p, q: ix(p, q, 0, min_lon))
    poly = clip(poly, lambda c: c[0] <= max_lon, lambda p, q: ix(p, q, 0, max_lon))
    poly = clip(poly, lambda c: c[1] >= min_lat, lambda p, q: ix(p, q, 1, min_lat))
    poly = clip(poly, lambda c: c[1] <= max_lat, lambda p, q: ix(p, q, 1, max_lat))
    return poly


def _clip_line_to_bbox(line, bbox):
    """Keep only the line vertices inside the bbox (cheap clip for road links,
    which are short so vertex filtering is adequate)."""
    min_lat, min_lon, max_lat, max_lon = bbox
    return [c for c in line
            if min_lon <= c[0] <= max_lon and min_lat <= c[1] <= max_lat]


def _decimate(points, max_pts):
    """Evenly thin a list of [lon,lat] (or [lat,lon]) to <= max_pts, keeping ends."""
    if len(points) <= max_pts:
        return points
    step = (len(points) - 1) / (max_pts - 1)
    out = [points[round(i * step)] for i in range(max_pts)]
    return out


def _chain_segments(segs):
    """Greedily join MultiLineString segments into one ordered polyline by
    repeatedly appending the segment whose nearest endpoint continues the path.
    Falls back gracefully; good enough to give a road a single sweep path."""
    if not segs:
        return []
    segs = [list(s) for s in segs]
    path = segs.pop(max(range(len(segs)), key=lambda i: _seg_len(segs[i])))
    changed = True
    while segs and changed:
        changed = False
        tail = path[-1]
        head = path[0]
        best_i, best_d, best_mode = None, float("inf"), None
        for i, s in enumerate(segs):
            for mode, pt in (("tail_fwd", s[0]), ("tail_rev", s[-1]),
                             ("head_fwd", s[-1]), ("head_rev", s[0])):
                anchor = tail if mode.startswith("tail") else head
                d = math.dist(anchor, pt)
                if d < best_d:
                    best_d, best_i, best_mode = d, i, mode
        if best_i is None or best_d > 0.01:   # ~1 km gap → stop joining
            break
        s = segs.pop(best_i)
        if best_mode == "tail_fwd":
            path += s
        elif best_mode == "tail_rev":
            path += s[::-1]
        elif best_mode == "head_fwd":
            path = s + path
        else:
            path = s[::-1] + path
        changed = True
    return path


def road_polyline(features, max_pts, bbox):
    """Chain road segments (clipped to bbox) into one [(lat,lon), ...] path."""
    segs = []
    for f in features:
        g = f["geometry"]
        lines = (g["coordinates"] if g["type"] == "MultiLineString"
                 else [g["coordinates"]] if g["type"] == "LineString" else [])
        for ln in lines:
            clipped = _clip_line_to_bbox(ln, bbox)
            if len(clipped) >= 2:
                segs.append(clipped)
    chained = _chain_segments(segs)              # [lon,lat]
    chained = _decimate(chained, max_pts)
    return [(c[1], c[0]) for c in chained]       # -> (lat,lon)


def river_polygon(features, max_pts, bbox):
    """Largest river polygon, clipped to bbox, as [(lat,lon), ...] vertices."""
    best = None
    best_len = 0.0
    for f in features:
        g = f["geometry"]
        polys = (g["coordinates"] if g["type"] == "MultiPolygon"
                 else [g["coordinates"]] if g["type"] == "Polygon" else [])
        for p in polys:
            clipped = _clip_ring_to_bbox(p[0], bbox)
            if len(clipped) >= 3:
                ln = _seg_len(clipped)
                if ln > best_len:
                    best, best_len = clipped, ln
    if not best:
        return []
    ring = _decimate(best, max_pts)
    return [(c[1], c[0]) for c in ring]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=__doc__)
    ap.add_argument("kind", choices=["road", "river"])
    ap.add_argument("--vehicles", required=True,
                    help="Comma-separated vehicle IDs (e.g. 1 or 1,2,4)")
    ap.add_argument("--bbox", required=True,
                    help="min_lat,min_lon,max_lat,max_lon (<= ~10 km²)")
    ap.add_argument("--name", default=None,
                    help="Filter by road_name / riv_nm substring (e.g. 한강)")
    ap.add_argument("--max-pts", type=int, default=40,
                    help="Max waypoints/vertices after simplification")
    ap.add_argument("--altitude", type=float, default=120.0)
    ap.add_argument("--key", default=os.environ.get("VWORLD_KEY", ""),
                    help="VWorld API key (or set VWORLD_KEY)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Fetch + simplify only; print points, don't publish")
    # Pass-through to uxas_publish_task.py
    ap.add_argument("--with-operating-region", default=None)
    ap.add_argument("--task-id", default="6100")
    ap.add_argument("--request-id", default="61100")
    ap.add_argument("--zone-id", default="6100")
    ap.add_argument("--region-id", default="61100")
    ap.add_argument("--wait-secs", default="305")
    ap.add_argument("--register-from-config", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--uxas-pub", default="tcp://127.0.0.1:5560")
    ap.add_argument("--uxas-pull", default="tcp://127.0.0.1:5561")
    args = ap.parse_args(argv)

    if not args.key:
        raise SystemExit("VWorld key required (--key or VWORLD_KEY env)")
    parts = [float(x) for x in args.bbox.split(",")]
    if len(parts) != 4:
        raise SystemExit("--bbox must be min_lat,min_lon,max_lat,max_lon")
    bbox = (min(parts[0], parts[2]), min(parts[1], parts[3]),
            max(parts[0], parts[2]), max(parts[1], parts[3]))

    if args.kind == "road":
        feats = fetch_features(ROAD_LAYER, bbox, args.key,
                               name_attr="road_name", name=args.name)
        pts = road_polyline(feats, args.max_pts, bbox)
        if len(pts) < 2:
            raise SystemExit("No road geometry found in bbox "
                             f"({len(feats)} features)")
        kind, flag = "line", "--line"
        print(f"[VWorld] road: {len(feats)} features -> {len(pts)}-pt sweep path")
    else:
        feats = fetch_features(RIVER_LAYER, bbox, args.key,
                               name_attr="riv_nm", name=args.name)
        pts = river_polygon(feats, min(args.max_pts, 30), bbox)
        if len(pts) < 3:
            raise SystemExit("No river polygon found in bbox "
                             f"({len(feats)} features)")
        kind, flag = "area", "--polygon"
        print(f"[VWorld] river: {len(feats)} features -> {len(pts)}-vertex area")

    pt_args = [f"{lat:.7f},{lon:.7f}" for lat, lon in pts]
    if args.dry_run:
        print(f"[VWorld] {kind} points ({len(pt_args)}):")
        for p in pt_args:
            print("   ", p)
        return 0

    cmd = [sys.executable, str(SCRIPT_DIR / "uxas_publish_task.py"), kind,
           "--vehicles", args.vehicles, flag, *pt_args,
           "--altitude", str(args.altitude),
           "--task-id", str(args.task_id), "--request-id", str(args.request_id),
           "--zone-id", str(args.zone_id), "--region-id", str(args.region_id),
           "--wait-secs", str(args.wait_secs),
           "--uxas-pub", args.uxas_pub, "--uxas-pull", args.uxas_pull]
    if args.with_operating_region:
        cmd += ["--with-operating-region", args.with_operating_region]
    if args.register_from_config:
        cmd += ["--register-from-config", args.register_from_config]
    if args.out:
        cmd += ["--out", args.out]
    print("[VWorld] -> " + " ".join(cmd[:6]) + f" ... ({len(pt_args)} pts)")
    return subprocess.call(cmd)


if __name__ == "__main__":
    sys.exit(main())
