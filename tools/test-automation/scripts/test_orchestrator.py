#!/usr/bin/env python3
"""QGC Test Automation Orchestrator.

Reads YAML test scenarios, sends MAVLink commands via pymavlink,
listens for QGC EventBroadcaster events, validates pass/fail per step,
and saves JSON results.

Usage:
    python3 test_orchestrator.py --scenario scenario.yaml --instance 0 --output results/
    python3 test_orchestrator.py --scenario configs/scenarios/ --instance 0
"""

import argparse
import json
import math
import os
import pathlib
import socket
import struct
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import yaml
from pymavlink import mavutil

# ---------------------------------------------------------------------------
# PX4 custom mode mapping (main_mode << 16 | sub_mode << 24 ... simplified)
# For SET_MODE (MAV_CMD 176): param1=base_mode, param2=custom_mode
# ---------------------------------------------------------------------------
PX4_MODES = {
    "manual": 1,
    "posctl": 2,
    "auto_mission": 3,
    "auto_loiter": 4,
    "auto_rtl": 5,
    "offboard": 6,
    "auto_land": 9,
    "auto_takeoff": 10,
}

# MAVLink command IDs
MAVLINK_CMDS = {
    "arm": 400,
    "disarm": 400,
    "takeoff": 22,
    "land": 21,
    "set_mode": 176,
    "change_speed": 178,
    "rtl": 176,  # set_mode to RTL
}

# Default ports
EVENT_LISTEN_PORT = 45678
EVENT_SEND_PORT = 45679
MAVLINK_BASE_PORT = 14540


# ---------------------------------------------------------------------------
# EventChannel -- listens UDP for QGC EventBroadcaster JSON events
# ---------------------------------------------------------------------------
class EventChannel:
    """Receives QGC EventBroadcaster events via UDP."""

    def __init__(self, port: int = EVENT_LISTEN_PORT):
        self.port = port
        self._sock: Optional[socket.socket] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._events: list[dict] = []
        self._lock = threading.Lock()
        self._waiters: list[dict] = []

    def start(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("0.0.0.0", self.port))
        self._sock.settimeout(0.5)
        self._running = True
        self._thread = threading.Thread(target=self._listen, daemon=True)
        self._thread.start()
        print(f"[EventChannel] Listening on UDP :{self.port}")

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        if self._sock:
            self._sock.close()

    def _listen(self) -> None:
        while self._running:
            try:
                data, addr = self._sock.recvfrom(65535)
                try:
                    event = json.loads(data.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                with self._lock:
                    self._events.append(event)
                    for w in self._waiters:
                        if self._event_matches(event, w["match"]):
                            w["found"] = event
                            w["event_obj"].set()
            except socket.timeout:
                continue
            except OSError:
                break

    @staticmethod
    def _event_matches(event: dict, match: dict) -> bool:
        for key, val in match.items():
            if event.get(key) != val:
                return False
        return True

    def wait_event(self, match: dict, timeout: float = 30.0) -> Optional[dict]:
        """Block until an event matching *match* arrives or timeout."""
        # Check already-received events first
        with self._lock:
            for ev in self._events:
                if self._event_matches(ev, match):
                    return ev
            waiter = {"match": match, "found": None, "event_obj": threading.Event()}
            self._waiters.append(waiter)
        waiter["event_obj"].wait(timeout=timeout)
        with self._lock:
            if waiter in self._waiters:
                self._waiters.remove(waiter)
        return waiter["found"]

    def get_events(self) -> list[dict]:
        with self._lock:
            return list(self._events)

    def clear(self) -> None:
        with self._lock:
            self._events.clear()


# ---------------------------------------------------------------------------
# EventSender -- sends UI commands to QGC via UDP
# ---------------------------------------------------------------------------
class EventSender:
    """Sends UI command JSON messages to QGC via UDP."""

    def __init__(self, host: str = "127.0.0.1", port: int = EVENT_SEND_PORT):
        self.host = host
        self.port = port
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def send(self, action: str, params: Optional[dict] = None) -> None:
        msg: dict[str, Any] = {"action": action}
        if params:
            msg["params"] = params
        data = json.dumps(msg).encode("utf-8")
        self._sock.sendto(data, (self.host, self.port))
        print(f"[EventSender] Sent: {msg}")

    def close(self) -> None:
        self._sock.close()


# ---------------------------------------------------------------------------
# MAVLinkChannel -- pymavlink connection
# ---------------------------------------------------------------------------
class MAVLinkChannel:
    """Wraps pymavlink connection to a PX4 SITL instance."""

    def __init__(self, instance: int = 0, host: str = "127.0.0.1"):
        self.instance = instance
        self.host = host
        self.port = MAVLINK_BASE_PORT + instance
        self.conn: Optional[mavutil.mavlink_connection] = None
        self._last_heartbeat: Optional[Any] = None
        self._last_position: Optional[Any] = None
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._ack_events: dict[int, threading.Event] = {}
        self._ack_results: dict[int, int] = {}

    def connect(self) -> None:
        connstr = f"udpin:{self.host}:{self.port}"
        print(f"[MAVLink] Connecting to {connstr}")
        self.conn = mavutil.mavlink_connection(connstr)
        self.conn.wait_heartbeat(timeout=30)
        print(f"[MAVLink] Heartbeat received (sys={self.conn.target_system}, "
              f"comp={self.conn.target_component})")
        self._running = True
        self._thread = threading.Thread(target=self._reader, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        if self.conn:
            self.conn.close()

    def _reader(self) -> None:
        while self._running:
            try:
                msg = self.conn.recv_match(blocking=True, timeout=0.5)
                if msg is None:
                    continue
                mtype = msg.get_type()
                with self._lock:
                    if mtype == "HEARTBEAT":
                        self._last_heartbeat = msg
                    elif mtype == "GLOBAL_POSITION_INT":
                        self._last_position = msg
                    elif mtype == "COMMAND_ACK":
                        cmd_id = msg.command
                        if cmd_id in self._ack_events:
                            self._ack_results[cmd_id] = msg.result
                            self._ack_events[cmd_id].set()
            except Exception:
                if not self._running:
                    break

    # -- Command helpers ---------------------------------------------------

    def _send_command_long(self, command: int, param1: float = 0,
                           param2: float = 0, param3: float = 0,
                           param4: float = 0, param5: float = 0,
                           param6: float = 0, param7: float = 0,
                           timeout: float = 10.0) -> int:
        """Send MAV_CMD via COMMAND_LONG, wait for ACK. Returns MAV_RESULT."""
        ev = threading.Event()
        with self._lock:
            self._ack_events[command] = ev
            self._ack_results.pop(command, None)
        self.conn.mav.command_long_send(
            self.conn.target_system,
            self.conn.target_component,
            command,
            0,  # confirmation
            param1, param2, param3, param4, param5, param6, param7,
        )
        ev.wait(timeout=timeout)
        with self._lock:
            self._ack_events.pop(command, None)
            result = self._ack_results.pop(command, -1)
        return result

    def arm(self) -> int:
        print("[MAVLink] Sending ARM")
        return self._send_command_long(400, param1=1)

    def disarm(self, force: bool = False) -> int:
        print("[MAVLink] Sending DISARM")
        return self._send_command_long(400, param1=0, param2=21196 if force else 0)

    def takeoff(self, altitude: float = 10.0) -> int:
        print(f"[MAVLink] Sending TAKEOFF alt={altitude}")
        return self._send_command_long(22, param7=altitude)

    def land(self) -> int:
        print("[MAVLink] Sending LAND")
        return self._send_command_long(21)

    def rtl(self) -> int:
        print("[MAVLink] Sending RTL (set_mode)")
        return self.set_mode("auto_rtl")

    def set_mode(self, mode_name: str) -> int:
        custom = PX4_MODES.get(mode_name)
        if custom is None:
            print(f"[MAVLink] Unknown mode: {mode_name}")
            return -1
        print(f"[MAVLink] Setting mode to {mode_name} (custom={custom})")
        # PX4 uses base_mode=MAV_MODE_FLAG_CUSTOM_MODE_ENABLED (1)
        # and custom_mode encodes main+sub mode
        # Simplified: param1=1 (custom), param2=custom_mode
        return self._send_command_long(176, param1=1, param2=float(custom))

    def change_speed(self, speed: float, speed_type: int = 1) -> int:
        """speed_type: 0=airspeed, 1=groundspeed."""
        print(f"[MAVLink] Changing speed to {speed} m/s (type={speed_type})")
        return self._send_command_long(178, param1=float(speed_type), param2=speed)

    def upload_mission(self, items: list[dict]) -> bool:
        """Upload a list of mission items via the mission protocol."""
        print(f"[MAVLink] Uploading mission with {len(items)} items")
        wp = mavutil.mavwp.MAVWPLoader()
        for i, item in enumerate(items):
            wp.add(mavutil.mavlink.MAVLink_mission_item_int_message(
                self.conn.target_system,
                self.conn.target_component,
                i,
                item.get("frame", 6),  # MAV_FRAME_GLOBAL_RELATIVE_ALT_INT
                item.get("command", 16),  # MAV_CMD_NAV_WAYPOINT
                0 if i > 0 else 1,  # current
                int(item.get("autocontinue", 1)),
                item.get("param1", 0),
                item.get("param2", 0),
                item.get("param3", 0),
                item.get("param4", 0),
                int(item.get("lat", 0) * 1e7),
                int(item.get("lon", 0) * 1e7),
                item.get("alt", 10),
            ))
        self.conn.waypoint_count_send(wp.count())
        # Simple blocking mission upload
        for i in range(wp.count()):
            msg = self.conn.recv_match(type="MISSION_REQUEST_INT",
                                       blocking=True, timeout=10)
            if msg is None:
                msg = self.conn.recv_match(type="MISSION_REQUEST",
                                           blocking=True, timeout=5)
            if msg is None:
                print(f"[MAVLink] Mission upload timeout at item {i}")
                return False
            self.conn.mav.send(wp.wp(msg.seq))
        ack = self.conn.recv_match(type="MISSION_ACK", blocking=True, timeout=10)
        if ack and ack.type == 0:
            print("[MAVLink] Mission upload complete")
            return True
        print(f"[MAVLink] Mission upload failed: {ack}")
        return False

    # -- State queries -----------------------------------------------------

    def get_position(self) -> Optional[dict]:
        with self._lock:
            p = self._last_position
        if p is None:
            return None
        return {
            "lat": p.lat / 1e7,
            "lon": p.lon / 1e7,
            "alt": p.alt / 1000.0,
            "relative_alt": p.relative_alt / 1000.0,
            "vx": p.vx / 100.0,
            "vy": p.vy / 100.0,
            "vz": p.vz / 100.0,
            "hdg": p.hdg / 100.0,
        }

    def get_heartbeat(self) -> Optional[dict]:
        with self._lock:
            h = self._last_heartbeat
        if h is None:
            return None
        return {
            "base_mode": h.base_mode,
            "custom_mode": h.custom_mode,
            "system_status": h.system_status,
            "armed": bool(h.base_mode & 128),
        }


# ---------------------------------------------------------------------------
# Step result
# ---------------------------------------------------------------------------
@dataclass
class StepResult:
    name: str
    action: str
    passed: bool
    duration: float = 0.0
    details: str = ""
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "action": self.action,
            "passed": self.passed,
            "duration": round(self.duration, 3),
            "details": self.details,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# TestOrchestrator
# ---------------------------------------------------------------------------
class TestOrchestrator:
    """Main test runner: reads YAML scenarios, executes steps, validates."""

    def __init__(self, instance: int = 0, host: str = "127.0.0.1",
                 event_port: int = EVENT_LISTEN_PORT,
                 send_port: int = EVENT_SEND_PORT):
        self.instance = instance
        self.host = host
        self.event_channel = EventChannel(port=event_port)
        self.event_sender = EventSender(host=host, port=send_port)
        self.mavlink = MAVLinkChannel(instance=instance, host=host)
        self.results: list[StepResult] = []

    def setup(self) -> None:
        self.event_channel.start()
        self.mavlink.connect()

    def teardown(self) -> None:
        self.event_channel.stop()
        self.event_sender.close()
        self.mavlink.close()

    # -- Scenario loading --------------------------------------------------

    @staticmethod
    def load_scenario(path: str) -> dict:
        with open(path, "r") as f:
            return yaml.safe_load(f)

    # -- Step execution ----------------------------------------------------

    def run_scenario(self, scenario: dict) -> dict:
        """Execute all steps in a scenario, return full result dict."""
        name = scenario.get("name", "unnamed")
        print(f"\n{'='*60}")
        print(f"  Scenario: {name}")
        print(f"{'='*60}\n")

        self.results = []
        self.event_channel.clear()
        start_time = time.time()

        steps = scenario.get("steps", [])
        all_passed = True
        for i, step in enumerate(steps):
            step_name = step.get("name", f"step_{i}")
            action = step.get("action", "unknown")
            print(f"\n--- Step {i+1}/{len(steps)}: {step_name} ({action}) ---")

            t0 = time.time()
            result = self._execute_step(step)
            result.duration = time.time() - t0

            self.results.append(result)
            status = "PASS" if result.passed else "FAIL"
            print(f"    Result: {status} ({result.duration:.2f}s)"
                  f"{' -- ' + result.details if result.details else ''}"
                  f"{' !! ' + result.error if result.error else ''}")

            if not result.passed:
                all_passed = False
                if step.get("abort_on_fail", False):
                    print("    Aborting scenario due to abort_on_fail")
                    break

        elapsed = time.time() - start_time
        result_doc = {
            "scenario": name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "instance": self.instance,
            "passed": all_passed,
            "total_steps": len(steps),
            "passed_steps": sum(1 for r in self.results if r.passed),
            "failed_steps": sum(1 for r in self.results if not r.passed),
            "duration": round(elapsed, 3),
            "steps": [r.to_dict() for r in self.results],
        }

        tag = "PASS" if all_passed else "FAIL"
        print(f"\n{'='*60}")
        print(f"  Result: {tag}  ({result_doc['passed_steps']}/"
              f"{result_doc['total_steps']} steps passed in {elapsed:.1f}s)")
        print(f"{'='*60}\n")
        return result_doc

    def _execute_step(self, step: dict) -> StepResult:
        action = step.get("action", "unknown")
        name = step.get("name", action)
        params = step.get("params", {})

        try:
            handler = {
                "send_command": self._step_send_command,
                "send_ui_command": self._step_send_ui_command,
                "wait_event": self._step_wait_event,
                "wait_state": self._step_wait_state,
                "check_altitude": self._step_check_altitude,
                "check_position": self._step_check_position,
                "upload_mission": self._step_upload_mission,
                "sleep": self._step_sleep,
            }.get(action)

            if handler is None:
                return StepResult(name=name, action=action, passed=False,
                                  error=f"Unknown action: {action}")
            return handler(name, params)

        except Exception as exc:
            return StepResult(name=name, action=action, passed=False,
                              error=str(exc))

    # -- Step handlers -----------------------------------------------------

    def _step_send_command(self, name: str, params: dict) -> StepResult:
        cmd = params.get("command", "")
        expect_ack = params.get("expect_ack", True)

        if cmd == "arm":
            result_code = self.mavlink.arm()
        elif cmd == "disarm":
            result_code = self.mavlink.disarm(force=params.get("force", False))
        elif cmd == "takeoff":
            result_code = self.mavlink.takeoff(altitude=params.get("altitude", 10))
        elif cmd == "land":
            result_code = self.mavlink.land()
        elif cmd == "rtl":
            result_code = self.mavlink.rtl()
        elif cmd == "set_mode":
            result_code = self.mavlink.set_mode(params.get("mode", "manual"))
        elif cmd == "change_speed":
            result_code = self.mavlink.change_speed(
                speed=params.get("speed", 5),
                speed_type=params.get("speed_type", 1),
            )
        else:
            return StepResult(name=name, action="send_command", passed=False,
                              error=f"Unknown command: {cmd}")

        if not expect_ack:
            return StepResult(name=name, action="send_command", passed=True,
                              details=f"Sent {cmd}, no ACK expected")

        passed = result_code == 0
        details = f"cmd={cmd}, ack_result={result_code}"
        error = "" if passed else f"ACK result {result_code} (expected 0)"
        return StepResult(name=name, action="send_command", passed=passed,
                          details=details, error=error)

    def _step_send_ui_command(self, name: str, params: dict) -> StepResult:
        action = params.get("ui_action", "")
        extra = params.get("ui_params", {})
        self.event_sender.send(action, extra if extra else None)
        return StepResult(name=name, action="send_ui_command", passed=True,
                          details=f"Sent UI action: {action}")

    def _step_wait_event(self, name: str, params: dict) -> StepResult:
        match = params.get("match", {})
        timeout = params.get("timeout", 30)
        ev = self.event_channel.wait_event(match, timeout=timeout)
        if ev:
            return StepResult(name=name, action="wait_event", passed=True,
                              details=json.dumps(ev, default=str))
        return StepResult(name=name, action="wait_event", passed=False,
                          error=f"Timeout ({timeout}s) waiting for {match}")

    def _step_wait_state(self, name: str, params: dict) -> StepResult:
        check = params.get("check", "armed")
        value = params.get("value", True)
        timeout = params.get("timeout", 30)
        poll = params.get("poll_interval", 0.5)

        deadline = time.time() + timeout
        while time.time() < deadline:
            if check == "armed":
                hb = self.mavlink.get_heartbeat()
                if hb and hb["armed"] == value:
                    return StepResult(name=name, action="wait_state", passed=True,
                                      details=f"armed={value}")
            elif check == "mode":
                hb = self.mavlink.get_heartbeat()
                if hb and hb["custom_mode"] == value:
                    return StepResult(name=name, action="wait_state", passed=True,
                                      details=f"mode={value}")
            elif check == "on_ground":
                pos = self.mavlink.get_position()
                if pos and ((value and pos["relative_alt"] < 0.5)
                            or (not value and pos["relative_alt"] >= 0.5)):
                    return StepResult(name=name, action="wait_state", passed=True,
                                      details=f"on_ground={value}, alt={pos['relative_alt']:.1f}")
            time.sleep(poll)

        return StepResult(name=name, action="wait_state", passed=False,
                          error=f"Timeout ({timeout}s) waiting for {check}={value}")

    def _step_check_altitude(self, name: str, params: dict) -> StepResult:
        target = params.get("target", 10)
        tolerance = params.get("tolerance", 2)
        timeout = params.get("timeout", 30)
        poll = params.get("poll_interval", 0.5)

        deadline = time.time() + timeout
        last_alt = None
        while time.time() < deadline:
            pos = self.mavlink.get_position()
            if pos:
                last_alt = pos["relative_alt"]
                if abs(last_alt - target) <= tolerance:
                    return StepResult(
                        name=name, action="check_altitude", passed=True,
                        details=f"alt={last_alt:.1f}, target={target}+/-{tolerance}")
            time.sleep(poll)

        return StepResult(
            name=name, action="check_altitude", passed=False,
            error=f"Alt {last_alt} not within {tolerance} of {target} "
                  f"after {timeout}s")

    def _step_check_position(self, name: str, params: dict) -> StepResult:
        target_lat = params.get("lat", 0)
        target_lon = params.get("lon", 0)
        radius = params.get("radius", 5)  # metres
        timeout = params.get("timeout", 30)
        poll = params.get("poll_interval", 0.5)

        def _haversine(lat1, lon1, lat2, lon2):
            R = 6371000
            dlat = math.radians(lat2 - lat1)
            dlon = math.radians(lon2 - lon1)
            a = (math.sin(dlat / 2) ** 2
                 + math.cos(math.radians(lat1))
                 * math.cos(math.radians(lat2))
                 * math.sin(dlon / 2) ** 2)
            return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

        deadline = time.time() + timeout
        last_dist = None
        while time.time() < deadline:
            pos = self.mavlink.get_position()
            if pos:
                last_dist = _haversine(pos["lat"], pos["lon"],
                                       target_lat, target_lon)
                if last_dist <= radius:
                    return StepResult(
                        name=name, action="check_position", passed=True,
                        details=f"dist={last_dist:.1f}m (radius={radius}m)")
            time.sleep(poll)

        return StepResult(
            name=name, action="check_position", passed=False,
            error=f"Position not within {radius}m (last dist={last_dist})")

    def _step_upload_mission(self, name: str, params: dict) -> StepResult:
        items = params.get("items", [])
        file_path = params.get("file")
        if file_path and not items:
            with open(file_path, "r") as f:
                items = json.load(f) if file_path.endswith(".json") else yaml.safe_load(f)
        if not items:
            return StepResult(name=name, action="upload_mission", passed=False,
                              error="No mission items provided")
        ok = self.mavlink.upload_mission(items)
        return StepResult(name=name, action="upload_mission", passed=ok,
                          details=f"{len(items)} items",
                          error="" if ok else "Upload failed")

    def _step_sleep(self, name: str, params: dict) -> StepResult:
        duration = params.get("duration", 1)
        print(f"    Sleeping {duration}s ...")
        time.sleep(duration)
        return StepResult(name=name, action="sleep", passed=True,
                          details=f"Slept {duration}s")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def collect_yaml_files(path: str) -> list[str]:
    p = pathlib.Path(path)
    if p.is_file():
        return [str(p)]
    if p.is_dir():
        files = sorted(p.glob("*.yaml")) + sorted(p.glob("*.yml"))
        return [str(f) for f in files]
    return []


def main() -> None:
    parser = argparse.ArgumentParser(description="QGC Test Orchestrator")
    parser.add_argument("--scenario", required=True,
                        help="Path to YAML scenario file or directory of YAMLs")
    parser.add_argument("--instance", type=int, default=0,
                        help="PX4 SITL instance number (default 0)")
    parser.add_argument("--host", default="127.0.0.1",
                        help="MAVLink / QGC host (default 127.0.0.1)")
    parser.add_argument("--output", default="results/",
                        help="Output directory for JSON results")
    parser.add_argument("--event-port", type=int, default=EVENT_LISTEN_PORT,
                        help=f"QGC event listen port (default {EVENT_LISTEN_PORT})")
    parser.add_argument("--send-port", type=int, default=EVENT_SEND_PORT,
                        help=f"QGC UI command port (default {EVENT_SEND_PORT})")
    args = parser.parse_args()

    scenario_files = collect_yaml_files(args.scenario)
    if not scenario_files:
        print(f"ERROR: No YAML files found at {args.scenario}", file=sys.stderr)
        sys.exit(1)

    os.makedirs(args.output, exist_ok=True)

    orchestrator = TestOrchestrator(
        instance=args.instance,
        host=args.host,
        event_port=args.event_port,
        send_port=args.send_port,
    )

    try:
        orchestrator.setup()

        all_results = []
        for sf in scenario_files:
            print(f"\nLoading scenario: {sf}")
            scenario = orchestrator.load_scenario(sf)
            result = orchestrator.run_scenario(scenario)
            all_results.append(result)

            # Save individual result
            base = pathlib.Path(sf).stem
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            out_file = os.path.join(args.output, f"{base}_{ts}.json")
            with open(out_file, "w") as f:
                json.dump(result, f, indent=2, default=str)
            print(f"Result saved: {out_file}")

        # Summary
        total = len(all_results)
        passed = sum(1 for r in all_results if r["passed"])
        print(f"\n{'='*60}")
        print(f"  BATCH SUMMARY: {passed}/{total} scenarios passed")
        print(f"{'='*60}")

        sys.exit(0 if passed == total else 1)

    finally:
        orchestrator.teardown()


if __name__ == "__main__":
    main()
