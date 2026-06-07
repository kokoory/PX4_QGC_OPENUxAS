#!/usr/bin/env python3
"""Download satellite imagery for the OpenAMASE map (same area as QGC).

AMASE renders maps fully offline: its WorldImageLayer scans a directory
(config/amase Plugins.xml -> <Directory>./data/overlay</Directory>) for
georeferenced images. Without local imagery only the tiny whole-Earth
world_image_small.jpg is shown, which is effectively black at mission zoom.

This script downloads Esri World Imagery XYZ tiles (the same imagery family
QGC's satellite map uses) for a bounding box, stitches them, reprojects from
Web Mercator to the equirectangular (linear lat/lon) mapping AMASE expects,
and writes:

    <overlay_dir>/<name>.jpg   the image
    <overlay_dir>/<name>       ESRI world file (AMASE expects the world file
                               named as the image minus its 4-char suffix,
                               i.e. literally no extension — see
                               WorldImageLayer.setImageDir())

Defaults target the Korea mixed-fleet sim area (configs/vehicles.json
defaults.home_*) and auto-detect the OpenUxAS AMASE install.

Usage:
    python3 fetch_amase_overlay.py                        # Korea, 4 km, z16
    python3 fetch_amase_overlay.py --radius-km 8 --zoom 15
    python3 fetch_amase_overlay.py --center 45.323,-120.9645 --name waterway
"""

from __future__ import annotations

import argparse
import io
import math
import os
import sys
import time
import urllib.request

from PIL import Image
import numpy as np

TILE_URL = ("https://server.arcgisonline.com/ArcGIS/rest/services/"
            "World_Imagery/MapServer/tile/{z}/{y}/{x}")
TILE_SIZE = 256
USER_AGENT = "qgc-uxas-test-automation/1.0 (AMASE offline overlay fetch)"

DEFAULT_CENTER = (34.61167, 127.206028)  # vehicles.json defaults.home_*
DEFAULT_AMASE_GLOB = os.path.expanduser(
    "~/myclaude/OpenUxAS/infrastructure/sbx/x86_64-linux/amase/src/OpenAMASE")


def lonlat_to_tile(lat: float, lon: float, z: int) -> tuple[float, float]:
    """WGS84 -> fractional XYZ tile coordinates."""
    n = 2 ** z
    x = (lon + 180.0) / 360.0 * n
    lat_r = math.radians(lat)
    y = (1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi) \
        / 2.0 * n
    return x, y


def tiley_to_lat(y: float, z: int) -> float:
    n = 2 ** z
    return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))


def fetch_tile(z: int, x: int, y: int, retries: int = 3) -> Image.Image:
    req = urllib.request.Request(
        TILE_URL.format(z=z, x=x, y=y), headers={"User-Agent": USER_AGENT})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return Image.open(io.BytesIO(resp.read())).convert("RGB")
        except Exception as exc:
            if attempt == retries - 1:
                raise
            print(f"  retry {z}/{x}/{y}: {exc}")
            time.sleep(1.0 + attempt)
    raise RuntimeError("unreachable")


def find_overlay_dir() -> str:
    candidates = [
        os.environ.get("AMASE_OPENAMASE_DIR", ""),
        DEFAULT_AMASE_GLOB,
        os.path.expanduser("~/OpenUxAS/infrastructure/sbx/x86_64-linux/"
                           "amase/src/OpenAMASE"),
    ]
    for c in candidates:
        if c and os.path.isdir(os.path.join(c, "data")):
            return os.path.join(c, "data", "overlay")
    sys.exit("Could not find the OpenAMASE install (data/ dir). "
             "Set AMASE_OPENAMASE_DIR or pass --out explicitly.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--center", default=None,
                    help="lat,lon (default: Korea sim home "
                         f"{DEFAULT_CENTER[0]},{DEFAULT_CENTER[1]})")
    ap.add_argument("--radius-km", type=float, default=4.0,
                    help="half-size of the square coverage (default 4 km)")
    ap.add_argument("--zoom", type=int, default=16,
                    help="XYZ zoom level (16 ~ 2.4 m/px, default)")
    ap.add_argument("--name", default="korea",
                    help="output base name (default: korea)")
    ap.add_argument("--out", default=None,
                    help="overlay dir (default: auto-detect OpenAMASE "
                         "data/overlay)")
    args = ap.parse_args()

    if args.center:
        lat_c, lon_c = (float(v) for v in args.center.split(","))
    else:
        lat_c, lon_c = DEFAULT_CENTER

    dlat = args.radius_km / 111.32
    dlon = args.radius_km / (111.32 * math.cos(math.radians(lat_c)))
    north, south = lat_c + dlat, lat_c - dlat
    west, east = lon_c - dlon, lon_c + dlon

    z = args.zoom
    x0f, y0f = lonlat_to_tile(north, west, z)   # top-left
    x1f, y1f = lonlat_to_tile(south, east, z)   # bottom-right
    x0, y0 = int(math.floor(x0f)), int(math.floor(y0f))
    x1, y1 = int(math.floor(x1f)), int(math.floor(y1f))
    nx, ny = x1 - x0 + 1, y1 - y0 + 1
    total = nx * ny
    print(f"Coverage: lat [{south:.5f},{north:.5f}] lon [{west:.5f},{east:.5f}]")
    print(f"Tiles: {nx} x {ny} = {total} @ z{z}")
    if total > 1500:
        sys.exit("Refusing to fetch >1500 tiles; lower --zoom or --radius-km.")

    mosaic = Image.new("RGB", (nx * TILE_SIZE, ny * TILE_SIZE))
    done = 0
    for ty in range(y0, y1 + 1):
        for tx in range(x0, x1 + 1):
            tile = fetch_tile(z, tx, ty)
            mosaic.paste(tile, ((tx - x0) * TILE_SIZE, (ty - y0) * TILE_SIZE))
            done += 1
            if done % 25 == 0 or done == total:
                print(f"  {done}/{total} tiles")

    # --- Reproject Web Mercator rows -> equirectangular (linear latitude) ---
    # AMASE's MapScaledImage maps the image linearly in lat/lon, so each
    # output row must correspond to a constant latitude step. We resample
    # rows of the mercator mosaic accordingly (column mapping is already
    # linear in longitude).
    src = np.asarray(mosaic)
    h_src = src.shape[0]
    lat_top = tiley_to_lat(y0, z)            # mosaic top edge
    lat_bot = tiley_to_lat(y1 + 1, z)        # mosaic bottom edge
    h_out = h_src                            # keep comparable vertical res
    out_lats = np.linspace(lat_top, lat_bot, h_out)
    # fractional mercator y for each output latitude -> source row index
    n = 2 ** z
    lat_r = np.radians(out_lats)
    merc_y = (1.0 - np.log(np.tan(lat_r) + 1.0 / np.cos(lat_r)) / math.pi) \
        / 2.0 * n
    src_rows = np.clip(((merc_y - y0) * TILE_SIZE).astype(int), 0, h_src - 1)
    equirect = src[src_rows, :, :]

    # --- Crop to the requested bbox ---
    lon_left = x0 / n * 360.0 - 180.0
    lon_right = (x1 + 1) / n * 360.0 - 180.0
    px_per_lon = equirect.shape[1] / (lon_right - lon_left)
    px_per_lat = h_out / (lat_top - lat_bot)
    cx0 = int((west - lon_left) * px_per_lon)
    cx1 = int((east - lon_left) * px_per_lon)
    cy0 = int((lat_top - north) * px_per_lat)
    cy1 = int((lat_top - south) * px_per_lat)
    cropped = equirect[max(cy0, 0):cy1, max(cx0, 0):cx1, :]
    img = Image.fromarray(cropped)

    overlay_dir = args.out or find_overlay_dir()
    os.makedirs(overlay_dir, exist_ok=True)
    jpg_path = os.path.join(overlay_dir, f"{args.name}.jpg")
    img.save(jpg_path, quality=88)

    # ESRI world file: A, D, B, E, C(top-left lon), F(top-left lat).
    # AMASE pairs "<name>.jpg" with a world file literally named "<name>"
    # (image filename minus 4 chars) — see WorldImageLayer.setImageDir().
    a = (east - west) / img.width
    e = -(north - south) / img.height
    world_path = os.path.join(overlay_dir, args.name)
    with open(world_path, "w") as fh:
        fh.write(f"{a:.10f}\n0.0\n0.0\n{e:.10f}\n"
                 f"{west + a / 2:.10f}\n{north + e / 2:.10f}\n")

    print(f"Wrote {jpg_path} ({img.width}x{img.height})")
    print(f"Wrote {world_path} (ESRI world file, degrees)")
    print("AMASE will pick it up on next launch "
          "(WorldImageLayer Directory=./data/overlay).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
