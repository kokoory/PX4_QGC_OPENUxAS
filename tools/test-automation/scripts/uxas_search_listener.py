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

In ADDITION the listener subscribes to the UxAS external PUB
(tcp://127.0.0.1:5560) for MissionCommand messages and mirrors them in
two directions, without waiting for the bridge / PX4:

  * QGC: fire-and-forget UDP packet on the plan-mirror port (default 45681)
    of shape {"category":"uxas_plan","event":"mission",
              "data":{"vehicle_id":N,"waypoints":[[lat,lon,alt],...],"ts":...}}
    EventBroadcaster picks it up and exposes uxasPlannedWaypoints to QML.
  * AMASE: forward the same LMCP MissionCommand (and any parent
    AutomationResponse) to AMASE's TCP server (default 127.0.0.1:5555) so
    AMASE renders the same waypoints on its own map.

Run alongside UxAS + SITL + bridge:
  VWORLD_KEY=<key> python3 uxas_search_listener.py
"""
from __future__ import annotations

import argparse
import contextlib
import json
import math
import os
import socket
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent

# Make sibling lmcp_py + qgc_uxas_bridge importable regardless of CWD --------
_LMCP_PY_DIR = (SCRIPT_DIR.parent / "lmcp_py").resolve()
if str(_LMCP_PY_DIR) not in sys.path:
    sys.path.insert(0, str(_LMCP_PY_DIR))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

# Optional: pyzmq + LMCP. They are only needed for the UxAS-plan mirror; the
# original road/river/area dispatcher works without them, so import lazily.
try:
    import zmq  # type: ignore
    from lmcp import LMCPFactory  # type: ignore
    from qgc_uxas_bridge import (
        decode_envelope as _decode_envelope,
        waypoints_from_mission_command as _waypoints_from_mc,
    )
    _LMCP_AVAILABLE = True
except Exception as _exc:  # pragma: no cover - graceful degradation
    zmq = None  # type: ignore
    LMCPFactory = None  # type: ignore
    _decode_envelope = None  # type: ignore
    _waypoints_from_mc = None  # type: ignore
    _LMCP_AVAILABLE = False
    _LMCP_IMPORT_ERROR = _exc


def _meters_to_deg(center_lat, dm):
    dlat = dm / 111320.0
    dlon = dm / (111320.0 * max(0.1, math.cos(math.radians(center_lat))))
    return dlat, dlon


def _rect_polygon(lat, lon, half_w_m, half_h_m):
    dlat, _ = _meters_to_deg(lat, half_h_m)
    _, dlon = _meters_to_deg(lat, half_w_m)
    # CCW rectangle: SW, SE, NE, NW
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
        # Share the panel's camera FOV with the bridges (they read this file when
        # building the AirVehicleState, so UxAS lane spacing follows the panel).
        _fov = data.get("fov")
        if _fov is None and "sensor" in data:
            _fov = 20.0 if data.get("sensor") == "detail" else 45.0
        if _fov is not None:
            try:
                with open("/tmp/uxas_camera_fov.txt", "w") as _fh:
                    _fh.write(str(float(_fov)))
            except (OSError, ValueError):
                pass
        radius = float(data.get("region_radius", 3000))
        task_id, req_id, zone_id, region_id = self._next_ids()
        region = f"{lat:.7f},{lon:.7f},{radius:.0f}"
        reg_cfg = data.get("register_from_config") or self.args.register_from_config

        if kind == "area":
            # Explicit polygon if the panel drew one, else width×height rect.
            poly_in = data.get("polygon")   # [[lat,lon], ...]
            if poly_in and len(poly_in) >= 3:
                poly = [(float(p[0]), float(p[1])) for p in poly_in]
            else:
                half_w = float(data.get("width_m", data.get("half_size_m", 300))) / 2.0
                half_h = float(data.get("height_m", data.get("half_size_m", 300))) / 2.0
                poly = _rect_polygon(lat, lon, half_w, half_h)
            pts = [f"{a:.7f},{b:.7f}" for a, b in poly]
            cmd = [sys.executable, str(SCRIPT_DIR / "uxas_publish_task.py"),
                   "area", "--polygon", *pts,
                   "--vehicles", vehicles, "--altitude", str(altitude),
                   "--task-id", str(task_id), "--request-id", str(req_id),
                   "--zone-id", str(zone_id), "--region-id", str(region_id),
                   "--with-operating-region", region,
                   "--wait-secs", str(self.args.wait_secs),
                   "--uxas-pub", self.args.uxas_pub, "--uxas-pull", self.args.uxas_pull]
            if reg_cfg:
                cmd += ["--register-from-config", reg_cfg]

        elif kind in ("road", "river"):
            # Multi-feature: each selected road/river name -> its own task,
            # all under one AutomationRequest so UxAS spreads them across the
            # mixed fleet. names = "ALL" or a comma list (from the panel's
            # checklist / click-select / select-all — they all resolve here).
            # Prefer the exact scan bbox [w,s,e,n] the panel measured; fall
            # back to a square around the centre.
            bb = data.get("bbox")
            if bb and len(bb) == 4:
                w, s, e, n = (float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3]))
                bbox = f"{s:.7f},{w:.7f},{n:.7f},{e:.7f}"
            else:
                half_m = float(data.get("half_size_m", 1000))
                dlat, dlon = _meters_to_deg(lat, half_m)
                bbox = (f"{lat - dlat:.7f},{lon - dlon:.7f},"
                        f"{lat + dlat:.7f},{lon + dlon:.7f}")
            names = data.get("names", "ALL")
            if isinstance(names, (list, tuple)):
                names = ",".join(str(n) for n in names) or "ALL"
            cmd = [sys.executable, str(SCRIPT_DIR / "vworld_multi_search.py"),
                   kind, "--bbox", bbox, "--names", names,
                   "--vehicles", vehicles, "--altitude", str(altitude),
                   "--request-id", str(req_id), "--task-id-base", str(task_id),
                   "--zone-id", str(zone_id), "--region-id", str(region_id),
                   "--with-operating-region", region,
                   "--wait-secs", str(self.args.wait_secs),
                   "--uxas-pub", self.args.uxas_pub, "--uxas-pull", self.args.uxas_pull]
            if kind == "river":
                cmd += ["--river-mode", str(data.get("river_mode", "center"))]
            if reg_cfg:
                cmd += ["--register-from-config", reg_cfg]
            if not os.environ.get("VWORLD_KEY") and self.args.vworld_key:
                cmd += ["--key", self.args.vworld_key]

        elif kind == "sar":
            # Heterogeneous SAR: one region split across vehicle types (fixed-wing
            # wide+high, multicopter detailed+low, rover on roads) in one request.
            bb = data.get("bbox")
            if bb and len(bb) == 4:
                w, s, e, n = (float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3]))
                bbox = f"{s:.7f},{w:.7f},{n:.7f},{e:.7f}"
            else:
                half_m = float(data.get("half_size_m", 2000))
                dlat, dlon = _meters_to_deg(lat, half_m)
                bbox = (f"{lat - dlat:.7f},{lon - dlon:.7f},"
                        f"{lat + dlat:.7f},{lon + dlon:.7f}")

            def _csv(key):
                v = data.get(key, "")
                return ",".join(str(x) for x in v) if isinstance(v, (list, tuple)) else str(v)

            cmd = [sys.executable, str(SCRIPT_DIR / "uxas_sar_search.py"),
                   "--bbox", bbox,
                   "--fw-ids", _csv("fw_ids"), "--mc-ids", _csv("mc_ids"),
                   "--ugv-ids", _csv("ugv_ids"),
                   "--fw-alt", str(data.get("fw_alt", 250)),
                   "--mc-alt", str(data.get("mc_alt", 60)),
                   "--fw-fov", str(data.get("fw_fov", 45)),
                   "--mc-fov", str(data.get("mc_fov", 20)),
                   "--mc-inner", str(data.get("mc_inner", 0.45)),
                   "--request-id", str(req_id), "--task-id-base", str(task_id),
                   "--uxas-pub", self.args.uxas_pub, "--uxas-pull", self.args.uxas_pull]
            if reg_cfg:
                cmd += ["--register-from-config", reg_cfg]
            if not os.environ.get("VWORLD_KEY") and self.args.vworld_key:
                cmd += ["--key", self.args.vworld_key]

        else:
            print(f"[listener] unknown search kind: {kind}")
            return

        nm = data.get("names", "")
        print(f"[listener] {kind} search @ ({lat:.5f},{lon:.5f}) "
              f"vehicles={vehicles} alt={altitude:.0f} names={nm} -> publishing")
        # Run in a thread so a long --wait-secs doesn't block new events.
        threading.Thread(target=self._run, args=(cmd,), daemon=True).start()

    def _run(self, cmd):
        try:
            subprocess.call(cmd)
        except Exception as exc:
            print(f"[listener] publish failed: {exc}")


# ---------------------------------------------------------------------------
# AMASE TCP forwarder + QGC plan-mirror UDP
# ---------------------------------------------------------------------------

class AmaseTcpForwarder:
    """Forwards LMCP messages to AMASE's TCP server (default 5555).

    AMASE's avtas.amase.network.TcpServer accepts the standard LMCP wire
    framing (LMCPFactory.packMessage(obj, True)), so we just open a single
    TCP socket and write raw bytes. If AMASE isn't running yet we retry on
    every send with light backoff; failed sends never block the listener.
    """

    def __init__(self, host: str, port: int, enabled: bool = True):
        self.host = host
        self.port = port
        self.enabled = enabled and _LMCP_AVAILABLE
        self._sock: socket.socket | None = None
        self._lock = threading.Lock()
        self._last_retry = 0.0
        self._retry_period = 5.0

    def _ensure_connected(self) -> bool:
        if self._sock is not None:
            return True
        now = time.monotonic()
        if now - self._last_retry < self._retry_period:
            return False
        self._last_retry = now
        try:
            s = socket.create_connection((self.host, self.port), timeout=1.0)
            s.settimeout(None)
            self._sock = s
            print(f"[amase] connected to {self.host}:{self.port}")
            return True
        except OSError:
            self._sock = None
            return False

    def send_lmcp(self, lmcp_obj) -> None:
        if not self.enabled or LMCPFactory is None:
            return
        try:
            raw = bytes(LMCPFactory.packMessage(lmcp_obj, True))
        except Exception as exc:
            print(f"[amase] pack failed: {exc}")
            return
        with self._lock:
            if not self._ensure_connected():
                return
            try:
                self._sock.sendall(raw)
            except OSError as exc:
                print(f"[amase] send failed ({exc}); will retry")
                try:
                    self._sock.close()
                except Exception:
                    pass
                self._sock = None

    def close(self) -> None:
        with self._lock:
            if self._sock is not None:
                with contextlib.suppress(Exception):
                    self._sock.close()
                self._sock = None


class PlanMirror:
    """Sends 'uxas_plan' / 'mission' events into QGC's EventBroadcaster.

    Targets the plan-mirror UDP port (default 45681). UDP is one-shot and
    free of bind-conflicts even if QGC isn't running yet — datagrams just
    drop on the floor.
    """

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def send_mission(self, vehicle_id: int,
                     waypoints: list[tuple[float, float, float]]) -> None:
        payload = {
            "category": "uxas_plan",
            "event": "mission",
            "timestamp": time.time(),
            "data": {
                "vehicle_id": int(vehicle_id),
                "waypoints": [[float(a), float(b), float(c)]
                              for a, b, c in waypoints],
                "ts": int(time.time() * 1000),
            },
        }
        try:
            self._sock.sendto(
                (json.dumps(payload) + "\n").encode("utf-8"),
                (self.host, self.port),
            )
        except OSError as exc:
            print(f"[plan-mirror] UDP send failed: {exc}")


class UxasMissionMirror:
    """Subscribes to UxAS PUB and mirrors MissionCommand into QGC + AMASE.

    The subscription is purely additive: it does not interfere with
    qgc_uxas_bridge.py, which subscribes to the same PUB on its own ZMQ
    context. (UxAS's PUB fans out to every subscriber.)
    """

    def __init__(self, sub_addr: str, plan_mirror: PlanMirror,
                 amase: AmaseTcpForwarder | None):
        self.sub_addr = sub_addr
        self.plan_mirror = plan_mirror
        self.amase = amase
        self._ctx: "zmq.Context" | None = None
        self._sock: "zmq.Socket" | None = None
        self._factory = LMCPFactory.LMCPFactory() if _LMCP_AVAILABLE else None
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not _LMCP_AVAILABLE:
            print(f"[mirror] disabled (lmcp_py/pyzmq unavailable: "
                  f"{_LMCP_IMPORT_ERROR})")
            return
        self._ctx = zmq.Context.instance()
        self._sock = self._ctx.socket(zmq.SUB)
        self._sock.connect(self.sub_addr)
        self._sock.setsockopt_string(zmq.SUBSCRIBE, "")
        print(f"[mirror] SUB connected to {self.sub_addr}")
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="uxas-mirror")
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._sock is not None:
            with contextlib.suppress(Exception):
                self._sock.close(linger=0)
            self._sock = None
        if self.amase is not None:
            self.amase.close()

    def _loop(self) -> None:
        poller = zmq.Poller()
        poller.register(self._sock, zmq.POLLIN)
        while self._running:
            try:
                socks = dict(poller.poll(timeout=500))
                if self._sock not in socks:
                    continue
                raw = self._sock.recv()
                env = _decode_envelope(raw)
                if env is None or not env.payload:
                    continue
                try:
                    obj = self._factory.getObject(bytearray(env.payload))
                except Exception as exc:
                    print(f"[mirror] decode error for {env.descriptor}: {exc}")
                    continue
                if obj is None:
                    continue
                cls = type(obj).__name__
                if cls == "MissionCommand":
                    self._handle_mission_command(obj)
                elif cls == "AutomationResponse":
                    self._handle_automation_response(obj)
                elif cls in ("AirVehicleState", "AirVehicleConfiguration"):
                    # Forward the LIVE vehicle (config + moving state) to AMASE so
                    # it draws and animates the aircraft icon, not just the planned
                    # route. UxAS echoes these on its PUB after the bridge pushes
                    # them in. No QGC mirror — QGC already has the MAVLink vehicle.
                    if self.amase is not None:
                        self.amase.send_lmcp(obj)
            except zmq.ZMQError:
                if self._running:
                    time.sleep(0.1)
            except Exception as exc:
                if self._running:
                    print(f"[mirror] loop error: {exc}")
                    time.sleep(0.5)

    def _handle_mission_command(self, mc) -> None:
        # Vehicle id is inherited from VehicleActionCommand
        try:
            vid = int(mc.get_VehicleID())
        except Exception:
            vid = 0
        if vid <= 0:
            return
        try:
            wps = _waypoints_from_mc(mc)
        except Exception as exc:
            print(f"[mirror] waypoint extract failed: {exc}")
            return
        triplets = [(w.lat_deg, w.lon_deg, w.alt_m) for w in wps]
        if not triplets:
            print(f"[mirror] MissionCommand vid={vid} had no waypoints")
            return
        print(f"[mirror] MissionCommand vid={vid} wps={len(triplets)} "
              f"-> QGC(UDP {self.plan_mirror.port}) + AMASE")
        self.plan_mirror.send_mission(vid, triplets)
        if self.amase is not None:
            self.amase.send_lmcp(mc)

    def _handle_automation_response(self, ar) -> None:
        if self.amase is None:
            return
        # AMASE happily renders the same AutomationResponse so the tasks /
        # mission view is consistent with what UxAS computed.
        self.amase.send_lmcp(ar)


def _probe_tcp(host: str, port: int, timeout: float = 0.25) -> bool:
    """Return True iff (host, port) currently accepts a TCP connection."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, default=45678,
                    help="EventBroadcaster broadcast port (default 45678)")
    ap.add_argument("--vehicles", default="1",
                    help="Default vehicle IDs if the event omits them")
    ap.add_argument("--task-id-base", type=int, default=7000)
    ap.add_argument("--register-from-config",
                    default=str(SCRIPT_DIR.parent / "configs" / "vehicles.json"),
                    help="vehicles.json for AirVehicleConfiguration registration "
                         "(mixed-fleet capabilities drive UxAS auto-assignment)")
    ap.add_argument("--wait-secs", default="305")
    ap.add_argument("--uxas-pub", default="tcp://127.0.0.1:5560")
    ap.add_argument("--uxas-pull", default="tcp://127.0.0.1:5561")
    ap.add_argument("--vworld-key", default=os.environ.get("VWORLD_KEY", ""))
    # --- UxAS-plan mirror (QGC + AMASE) -------------------------------------
    ap.add_argument("--qgc-plan-host", default="127.0.0.1",
                    help="QGC EventBroadcaster host for the plan mirror")
    ap.add_argument("--qgc-plan-port", type=int, default=45681,
                    help="QGC EventBroadcaster plan-mirror UDP port "
                         "(must match EventBroadcaster::_planMirrorPort)")
    ap.add_argument("--amase-host", default="127.0.0.1",
                    help="AMASE TCP server host (default 127.0.0.1)")
    ap.add_argument("--amase-port", type=int, default=5555,
                    help="AMASE TCP server port (default 5555)")
    ap.add_argument("--amase", choices=("auto", "on", "off"), default="auto",
                    help="Forward MissionCommand to AMASE TCP. 'auto' = ON "
                         "iff the AMASE port is reachable at startup; 'off' "
                         "disables; 'on' tries unconditionally")
    ap.add_argument("--no-uxas-mirror", action="store_true",
                    help="Disable the UxAS PUB subscription (mirror) entirely")
    args = ap.parse_args(argv)

    if args.vworld_key:
        os.environ.setdefault("VWORLD_KEY", args.vworld_key)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", args.port))
    dispatcher = SearchDispatcher(args)
    print(f"[listener] listening for QGC uxas_search events on UDP {args.port}")
    print(f"[listener] UxAS endpoint {args.uxas_pub} / {args.uxas_pull}")

    # --- UxAS-plan mirror (additive, never required) -----------------------
    plan_mirror = PlanMirror(args.qgc_plan_host, args.qgc_plan_port)
    amase: AmaseTcpForwarder | None = None
    if args.amase == "off":
        amase_enabled = False
    elif args.amase == "on":
        amase_enabled = True
    else:
        amase_enabled = _probe_tcp(args.amase_host, args.amase_port)
        if not amase_enabled:
            print(f"[amase] auto-disabled (no listener on "
                  f"{args.amase_host}:{args.amase_port})")
    if amase_enabled:
        amase = AmaseTcpForwarder(args.amase_host, args.amase_port,
                                  enabled=True)
        print(f"[amase] forwarder targeting {args.amase_host}:"
              f"{args.amase_port}")
    mirror: UxasMissionMirror | None = None
    if not args.no_uxas_mirror:
        mirror = UxasMissionMirror(args.uxas_pub, plan_mirror, amase)
        mirror.start()
    print(f"[listener] plan-mirror UDP → {args.qgc_plan_host}:"
          f"{args.qgc_plan_port}")

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
    if mirror is not None:
        mirror.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
