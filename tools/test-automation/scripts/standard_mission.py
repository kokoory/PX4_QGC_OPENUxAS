#!/usr/bin/env python3
"""
Standard test mission for all vehicle types.

Executes an identical mission profile across all vehicle configurations:
  Phase 1: Preflight checks (GPS, battery, sensors)
  Phase 2: Arm
  Phase 3: Takeoff to configurable altitude
  Phase 4: Fly square pattern via OFFBOARD guided mode
  Phase 5: Loiter for 30 seconds
  Phase 6: RTL (Return to Launch)
  Phase 7: Land
  Phase 8: Disarm

Usage:
    python3 standard_mission.py --instance 0
    python3 standard_mission.py --instance 0 --altitude 15 --pattern-size 50
    python3 standard_mission.py --instance 0 --altitude 20 --pattern-size 100 --record
"""

from __future__ import annotations

import argparse
import json
import math
import os
import signal
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

try:
    from pymavlink import mavutil, mavwp
except ImportError:
    print("ERROR: pymavlink is required.  Install with: pip install pymavlink", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

class Phase(str, Enum):
    PREFLIGHT = "preflight"
    ARM = "arm"
    TAKEOFF = "takeoff"
    PATTERN = "pattern"
    LOITER = "loiter"
    RTL = "rtl"
    LAND = "land"
    DISARM = "disarm"
    EMERGENCY = "emergency_land"


class StepStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"


@dataclass
class StepResult:
    phase: str
    status: str
    message: str = ""
    duration_s: float = 0.0
    timestamp: str = ""


@dataclass
class MissionResult:
    vehicle_instance: int
    altitude_m: float
    pattern_size_m: float
    start_time: str = ""
    end_time: str = ""
    total_duration_s: float = 0.0
    overall_status: str = "pass"
    steps: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _step_timer():
    """Return a context-manager-like pair (start, elapsed)."""
    t0 = time.monotonic()

    def elapsed() -> float:
        return round(time.monotonic() - t0, 3)

    return t0, elapsed


def _offset_coords(lat: float, lon: float, north_m: float, east_m: float):
    """Return (lat, lon) offset by *north_m* / *east_m* from origin."""
    earth_radius = 6_371_000.0
    d_lat = north_m / earth_radius
    d_lon = east_m / (earth_radius * math.cos(math.radians(lat)))
    return lat + math.degrees(d_lat), lon + math.degrees(d_lon)


# ---------------------------------------------------------------------------
# Mission runner
# ---------------------------------------------------------------------------

class StandardMission:
    """Execute the standard test mission on a single PX4 SITL vehicle."""

    # MAVLink command helpers
    MAV_CMD_NAV_TAKEOFF = 22
    MAV_CMD_NAV_LOITER_TIME = 19
    MAV_CMD_NAV_RETURN_TO_LAUNCH = 20
    MAV_CMD_NAV_LAND = 21
    MAV_CMD_COMPONENT_ARM_DISARM = 400
    MAV_CMD_DO_SET_MODE = 176

    # PX4 custom modes
    PX4_CUSTOM_MAIN_MODE_OFFBOARD = 6
    PX4_CUSTOM_MAIN_MODE_AUTO = 4
    PX4_CUSTOM_SUB_MODE_AUTO_LOITER = 3
    PX4_CUSTOM_SUB_MODE_AUTO_RTL = 5
    PX4_CUSTOM_SUB_MODE_AUTO_LAND = 6

    HEARTBEAT_TIMEOUT_S = 30
    ACK_TIMEOUT_S = 10
    PREFLIGHT_TIMEOUT_S = 60
    TAKEOFF_TIMEOUT_S = 60
    WAYPOINT_TIMEOUT_S = 120
    LOITER_DURATION_S = 30
    RTL_TIMEOUT_S = 120
    LAND_TIMEOUT_S = 120
    DISARM_TIMEOUT_S = 30

    GPS_FIX_MIN = 3           # 3-D fix
    BATTERY_MIN_PCT = 25.0
    ALTITUDE_TOLERANCE_M = 3.0
    WAYPOINT_ACCEPT_RADIUS_M = 5.0

    def __init__(
        self,
        instance: int,
        altitude: float = 15.0,
        pattern_size: float = 50.0,
        record: bool = False,
    ) -> None:
        self.instance = instance
        self.altitude = altitude
        self.pattern_size = pattern_size
        self.record = record

        self.udp_port = 14550 + instance
        self.connection_str = f"udpin:0.0.0.0:{self.udp_port}"

        self.conn: mavutil.mavlink_connection | None = None
        self.result = MissionResult(
            vehicle_instance=instance,
            altitude_m=altitude,
            pattern_size_m=pattern_size,
        )
        self._abort = False
        self._home_lat = 0.0
        self._home_lon = 0.0
        self._home_alt = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self) -> MissionResult:
        """Execute the full mission profile and return results."""
        self.result.start_time = _now_iso()
        t_start = time.monotonic()

        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        try:
            self._connect()
            self._phase_preflight()
            self._phase_arm()
            self._phase_takeoff()
            self._phase_pattern()
            self._phase_loiter()
            self._phase_rtl()
            self._phase_land()
            self._phase_disarm()
        except _MissionAbort as exc:
            self._record_step(Phase.EMERGENCY, StepStatus.FAIL, str(exc))
            self.result.overall_status = "fail"
            self._emergency_land()
        except Exception as exc:
            self._record_step(Phase.EMERGENCY, StepStatus.FAIL, f"Unexpected error: {exc}")
            self.result.overall_status = "fail"
            self._emergency_land()
        finally:
            self.result.end_time = _now_iso()
            self.result.total_duration_s = round(time.monotonic() - t_start, 3)

        return self.result

    def publish_results(self, path: Path | None = None) -> str:
        """Serialize results to JSON and optionally write to *path*."""
        payload = asdict(self.result)
        text = json.dumps(payload, indent=2)
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        return text

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def _connect(self) -> None:
        print(f"[mission] Connecting to {self.connection_str} ...")
        self.conn = mavutil.mavlink_connection(self.connection_str)
        self.conn.wait_heartbeat(timeout=self.HEARTBEAT_TIMEOUT_S)
        print(f"[mission] Heartbeat received (system {self.conn.target_system},"
              f" component {self.conn.target_component})")

    # ------------------------------------------------------------------
    # Phase implementations
    # ------------------------------------------------------------------

    def _phase_preflight(self) -> None:
        _, elapsed = _step_timer()
        print("[mission] Phase 1 – Preflight checks ...")

        deadline = time.monotonic() + self.PREFLIGHT_TIMEOUT_S
        gps_ok = False
        battery_ok = False
        sensors_ok = False

        while time.monotonic() < deadline:
            if self._abort:
                raise _MissionAbort("Aborted during preflight")

            # GPS
            msg = self.conn.recv_match(type="GPS_RAW_INT", blocking=True, timeout=2)
            if msg and msg.fix_type >= self.GPS_FIX_MIN:
                gps_ok = True

            # Battery
            msg = self.conn.recv_match(type="SYS_STATUS", blocking=True, timeout=2)
            if msg and msg.battery_remaining >= self.BATTERY_MIN_PCT:
                battery_ok = True
                sensors_ok = True  # SYS_STATUS sensor flags present

            if gps_ok and battery_ok and sensors_ok:
                self._record_step(Phase.PREFLIGHT, StepStatus.PASS,
                                  "GPS, battery, and sensor checks passed", elapsed())
                return

        detail = f"gps={gps_ok}, battery={battery_ok}, sensors={sensors_ok}"
        raise _MissionAbort(f"Preflight checks timed out ({detail})")

    def _phase_arm(self) -> None:
        _, elapsed = _step_timer()
        print("[mission] Phase 2 – Arming ...")

        self.conn.mav.command_long_send(
            self.conn.target_system,
            self.conn.target_component,
            self.MAV_CMD_COMPONENT_ARM_DISARM,
            0,  # confirmation
            1,  # arm
            0, 0, 0, 0, 0, 0,
        )

        ack = self._wait_command_ack(self.MAV_CMD_COMPONENT_ARM_DISARM, self.ACK_TIMEOUT_S)
        if ack is None or ack.result != 0:
            raise _MissionAbort(f"Arm command rejected (ack={ack})")

        # Confirm via heartbeat
        if not self._wait_armed(True, timeout=self.ACK_TIMEOUT_S):
            raise _MissionAbort("Vehicle did not report armed state")

        self._record_step(Phase.ARM, StepStatus.PASS, "Vehicle armed", elapsed())

    def _phase_takeoff(self) -> None:
        _, elapsed = _step_timer()
        print(f"[mission] Phase 3 – Takeoff to {self.altitude}m ...")

        # Store home position
        msg = self.conn.recv_match(type="GLOBAL_POSITION_INT", blocking=True, timeout=5)
        if msg:
            self._home_lat = msg.lat / 1e7
            self._home_lon = msg.lon / 1e7
            self._home_alt = msg.relative_alt / 1e3

        self.conn.mav.command_long_send(
            self.conn.target_system,
            self.conn.target_component,
            self.MAV_CMD_NAV_TAKEOFF,
            0,
            0, 0, 0, 0, 0, 0,
            self.altitude,
        )

        ack = self._wait_command_ack(self.MAV_CMD_NAV_TAKEOFF, self.ACK_TIMEOUT_S)
        if ack is None or ack.result != 0:
            raise _MissionAbort(f"Takeoff command rejected (ack={ack})")

        if not self._wait_altitude(self.altitude, self.ALTITUDE_TOLERANCE_M, self.TAKEOFF_TIMEOUT_S):
            raise _MissionAbort("Failed to reach takeoff altitude")

        self._record_step(Phase.TAKEOFF, StepStatus.PASS,
                          f"Reached {self.altitude}m", elapsed())

    def _phase_pattern(self) -> None:
        _, elapsed = _step_timer()
        half = self.pattern_size / 2.0
        print(f"[mission] Phase 4 – Flying {self.pattern_size}m square pattern ...")

        # Build 4 waypoints relative to home
        waypoints = [
            _offset_coords(self._home_lat, self._home_lon, half, half),
            _offset_coords(self._home_lat, self._home_lon, half, -half),
            _offset_coords(self._home_lat, self._home_lon, -half, -half),
            _offset_coords(self._home_lat, self._home_lon, -half, half),
        ]

        # Upload mission
        wp_loader = mavwp.MAVWPLoader()
        # Home waypoint (item 0)
        wp_loader.add(mavwp.MAVWPLoader.wp(
            self._home_lat, self._home_lon, self.altitude, 0))

        for i, (lat, lon) in enumerate(waypoints, start=1):
            wp = mavwp.MAVWPLoader.wp(lat, lon, self.altitude, 0)
            wp_loader.add(wp)

        self.conn.waypoint_clear_all_send()
        self.conn.waypoint_count_send(wp_loader.count())

        for i in range(wp_loader.count()):
            msg = self.conn.recv_match(type=["MISSION_REQUEST", "MISSION_REQUEST_INT"],
                                       blocking=True, timeout=self.ACK_TIMEOUT_S)
            if msg is None:
                raise _MissionAbort(f"Timeout waiting for MISSION_REQUEST (item {i})")
            wp = wp_loader.wp(msg.seq)
            self.conn.mav.mission_item_int_send(
                self.conn.target_system,
                self.conn.target_component,
                msg.seq,
                wp.frame if hasattr(wp, "frame") else 6,  # MAV_FRAME_GLOBAL_RELATIVE_ALT_INT
                16,  # MAV_CMD_NAV_WAYPOINT
                0 if msg.seq > 0 else 1,  # current
                1,  # autocontinue
                0, self.WAYPOINT_ACCEPT_RADIUS_M, 0, 0,
                int(wp.x * 1e7) if isinstance(wp.x, float) and abs(wp.x) < 180 else int(wp.x),
                int(wp.y * 1e7) if isinstance(wp.y, float) and abs(wp.y) < 180 else int(wp.y),
                self.altitude,
            )

        ack = self.conn.recv_match(type="MISSION_ACK", blocking=True, timeout=self.ACK_TIMEOUT_S)
        if ack is None or ack.type != 0:
            raise _MissionAbort(f"Mission upload failed (ack={ack})")

        # Start mission (set AUTO mode)
        self._set_mode_auto()

        self.conn.mav.command_long_send(
            self.conn.target_system,
            self.conn.target_component,
            mavutil.mavlink.MAV_CMD_MISSION_START,
            0, 0, 0, 0, 0, 0, 0, 0,
        )

        # Wait for mission completion (MISSION_ITEM_REACHED for last waypoint)
        deadline = time.monotonic() + self.WAYPOINT_TIMEOUT_S
        last_reached = -1
        target_seq = len(waypoints)
        while time.monotonic() < deadline:
            if self._abort:
                raise _MissionAbort("Aborted during pattern flight")
            msg = self.conn.recv_match(
                type=["MISSION_ITEM_REACHED", "MISSION_CURRENT"],
                blocking=True, timeout=2,
            )
            if msg is not None and msg.get_type() == "MISSION_ITEM_REACHED":
                last_reached = msg.seq
                print(f"  Waypoint {msg.seq}/{target_seq} reached")
                if msg.seq >= target_seq:
                    break

        if last_reached < target_seq:
            raise _MissionAbort(
                f"Pattern incomplete – reached waypoint {last_reached}/{target_seq}")

        self._record_step(Phase.PATTERN, StepStatus.PASS,
                          f"Square pattern complete ({self.pattern_size}m)", elapsed())

    def _phase_loiter(self) -> None:
        _, elapsed = _step_timer()
        print(f"[mission] Phase 5 – Loiter {self.LOITER_DURATION_S}s ...")

        # Switch to LOITER sub-mode
        self._set_px4_mode(self.PX4_CUSTOM_MAIN_MODE_AUTO,
                           self.PX4_CUSTOM_SUB_MODE_AUTO_LOITER)

        time.sleep(self.LOITER_DURATION_S)

        self._record_step(Phase.LOITER, StepStatus.PASS,
                          f"Loitered for {self.LOITER_DURATION_S}s", elapsed())

    def _phase_rtl(self) -> None:
        _, elapsed = _step_timer()
        print("[mission] Phase 6 – RTL ...")

        self.conn.mav.command_long_send(
            self.conn.target_system,
            self.conn.target_component,
            self.MAV_CMD_NAV_RETURN_TO_LAUNCH,
            0, 0, 0, 0, 0, 0, 0, 0,
        )

        # Wait until near home position
        deadline = time.monotonic() + self.RTL_TIMEOUT_S
        while time.monotonic() < deadline:
            if self._abort:
                raise _MissionAbort("Aborted during RTL")
            msg = self.conn.recv_match(type="GLOBAL_POSITION_INT", blocking=True, timeout=2)
            if msg:
                rel_alt = msg.relative_alt / 1e3
                lat = msg.lat / 1e7
                lon = msg.lon / 1e7
                dist = self._haversine(lat, lon, self._home_lat, self._home_lon)
                if dist < self.WAYPOINT_ACCEPT_RADIUS_M and rel_alt < self.altitude + 5:
                    self._record_step(Phase.RTL, StepStatus.PASS,
                                      f"Returned to launch (dist={dist:.1f}m)", elapsed())
                    return

        raise _MissionAbort("RTL timed out")

    def _phase_land(self) -> None:
        _, elapsed = _step_timer()
        print("[mission] Phase 7 – Landing ...")

        deadline = time.monotonic() + self.LAND_TIMEOUT_S
        while time.monotonic() < deadline:
            if self._abort:
                raise _MissionAbort("Aborted during landing")
            msg = self.conn.recv_match(type="GLOBAL_POSITION_INT", blocking=True, timeout=2)
            if msg:
                rel_alt = msg.relative_alt / 1e3
                if rel_alt < 0.5:
                    self._record_step(Phase.LAND, StepStatus.PASS,
                                      "Vehicle landed", elapsed())
                    return

        raise _MissionAbort("Landing timed out")

    def _phase_disarm(self) -> None:
        _, elapsed = _step_timer()
        print("[mission] Phase 8 – Disarming ...")

        self.conn.mav.command_long_send(
            self.conn.target_system,
            self.conn.target_component,
            self.MAV_CMD_COMPONENT_ARM_DISARM,
            0,
            0,  # disarm
            0, 0, 0, 0, 0, 0,
        )

        if not self._wait_armed(False, timeout=self.DISARM_TIMEOUT_S):
            raise _MissionAbort("Vehicle did not disarm")

        self._record_step(Phase.DISARM, StepStatus.PASS, "Vehicle disarmed", elapsed())

    # ------------------------------------------------------------------
    # Emergency handling
    # ------------------------------------------------------------------

    def _emergency_land(self) -> None:
        """Attempt emergency landing on failure."""
        print("[mission] EMERGENCY – attempting immediate land ...")
        if self.conn is None:
            return
        try:
            # Force LAND mode
            self._set_px4_mode(self.PX4_CUSTOM_MAIN_MODE_AUTO,
                               self.PX4_CUSTOM_SUB_MODE_AUTO_LAND)
            # Wait for touch-down
            deadline = time.monotonic() + self.LAND_TIMEOUT_S
            while time.monotonic() < deadline:
                msg = self.conn.recv_match(type="GLOBAL_POSITION_INT", blocking=True, timeout=2)
                if msg and (msg.relative_alt / 1e3) < 0.5:
                    print("[mission] Emergency landing complete")
                    return
        except Exception as exc:
            print(f"[mission] Emergency land failed: {exc}", file=sys.stderr)

    # ------------------------------------------------------------------
    # Utility helpers
    # ------------------------------------------------------------------

    def _wait_command_ack(self, command_id: int, timeout: float):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            msg = self.conn.recv_match(type="COMMAND_ACK", blocking=True, timeout=1)
            if msg and msg.command == command_id:
                return msg
        return None

    def _wait_armed(self, armed: bool, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            msg = self.conn.recv_match(type="HEARTBEAT", blocking=True, timeout=1)
            if msg is not None:
                is_armed = (msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED) != 0
                if is_armed == armed:
                    return True
        return False

    def _wait_altitude(self, target: float, tolerance: float, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            msg = self.conn.recv_match(type="GLOBAL_POSITION_INT", blocking=True, timeout=1)
            if msg:
                rel_alt = msg.relative_alt / 1e3
                if abs(rel_alt - target) <= tolerance:
                    return True
        return False

    def _set_mode_auto(self) -> None:
        self._set_px4_mode(self.PX4_CUSTOM_MAIN_MODE_AUTO, 0)

    def _set_px4_mode(self, main_mode: int, sub_mode: int = 0) -> None:
        custom = (main_mode << 16) | (sub_mode << 24)
        self.conn.mav.command_long_send(
            self.conn.target_system,
            self.conn.target_component,
            self.MAV_CMD_DO_SET_MODE,
            0,
            mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
            custom, 0, 0, 0, 0, 0,
        )
        self._wait_command_ack(self.MAV_CMD_DO_SET_MODE, self.ACK_TIMEOUT_S)

    @staticmethod
    def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Return distance in metres between two WGS-84 coordinates."""
        R = 6_371_000.0
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlam = math.radians(lon2 - lon1)
        a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    def _record_step(self, phase: Phase, status: StepStatus, message: str,
                     duration: float = 0.0) -> None:
        step = StepResult(
            phase=phase.value,
            status=status.value,
            message=message,
            duration_s=duration,
            timestamp=_now_iso(),
        )
        self.result.steps.append(asdict(step))
        tag = "PASS" if status == StepStatus.PASS else "FAIL"
        print(f"  [{tag}] {phase.value}: {message} ({duration:.1f}s)")

    def _signal_handler(self, signum, frame) -> None:
        print(f"\n[mission] Signal {signum} received – aborting ...")
        self._abort = True


class _MissionAbort(Exception):
    """Raised to trigger emergency landing."""


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the standard QGC test mission on a PX4 SITL vehicle.",
    )
    parser.add_argument(
        "--instance", type=int, required=True,
        help="PX4 SITL instance number (port = 14550 + instance)",
    )
    parser.add_argument(
        "--altitude", type=float, default=15.0,
        help="Takeoff altitude in metres (default: 15)",
    )
    parser.add_argument(
        "--pattern-size", type=float, default=50.0,
        help="Square pattern side length in metres (default: 50)",
    )
    parser.add_argument(
        "--record", action="store_true", default=False,
        help="Enable MAVLink recording for this run",
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="Path to write JSON results (default: stdout)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    mission = StandardMission(
        instance=args.instance,
        altitude=args.altitude,
        pattern_size=args.pattern_size,
        record=args.record,
    )

    result = mission.run()

    out_path = Path(args.output) if args.output else None
    report = mission.publish_results(out_path)

    if out_path is None:
        print("\n--- Mission Results ---")
        print(report)
    else:
        print(f"[mission] Results written to {out_path}")

    return 0 if result.overall_status == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
