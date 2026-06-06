#!/usr/bin/env python3
"""QGC Event Replay - MAVLink command sequence player.

Sends MAVLink command sequences to PX4 SITL vehicles. Supports JSON
sequence files for custom command flows, or runs a default sequence:

    arm -> takeoff 15m -> loiter 30s -> rtl -> land -> disarm

Usage:
    python3 qgc_event_replay.py --instance 0
    python3 qgc_event_replay.py --instance 0 --sequence mission.json
    python3 qgc_event_replay.py --instance 0 --host 192.168.1.100
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from pymavlink import mavutil
except ImportError:
    print("ERROR: pymavlink is required.  Install with: pip install pymavlink",
          file=sys.stderr)
    sys.exit(1)

# ---------------------------------------------------------------------------
# ANSI helpers
# ---------------------------------------------------------------------------

class _C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"

# ---------------------------------------------------------------------------
# PX4 mode constants
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
MAV_CMD_ARM_DISARM = 400
MAV_CMD_TAKEOFF = 22
MAV_CMD_LAND = 21
MAV_CMD_SET_MODE = 176
MAV_CMD_CHANGE_SPEED = 178

# Base port for PX4 SITL instances
MAVLINK_BASE_PORT = 14540

# ---------------------------------------------------------------------------
# Default command sequence
# ---------------------------------------------------------------------------

DEFAULT_SEQUENCE: list[dict[str, Any]] = [
    {
        "name": "Arm",
        "command": "arm",
        "description": "Arm the vehicle",
        "wait_after": 2.0,
    },
    {
        "name": "Takeoff to 15m",
        "command": "takeoff",
        "params": {"altitude": 15.0},
        "description": "Takeoff to 15 metres",
        "wait_after": 10.0,
        "verify": {"type": "altitude", "target": 15.0, "tolerance": 3.0, "timeout": 60.0},
    },
    {
        "name": "Loiter 30s",
        "command": "loiter",
        "params": {"duration": 30.0},
        "description": "Hold position for 30 seconds",
    },
    {
        "name": "RTL",
        "command": "rtl",
        "description": "Return to launch",
        "wait_after": 5.0,
    },
    {
        "name": "Wait for landing",
        "command": "wait_landed",
        "params": {"timeout": 120.0},
        "description": "Wait until vehicle has landed",
    },
    {
        "name": "Disarm",
        "command": "disarm",
        "description": "Disarm the vehicle",
        "wait_after": 2.0,
    },
]


# ---------------------------------------------------------------------------
# Step result
# ---------------------------------------------------------------------------

@dataclass
class StepResult:
    name: str
    command: str
    success: bool
    duration: float
    message: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "command": self.command,
            "success": self.success,
            "duration": round(self.duration, 3),
            "message": self.message,
        }


# ---------------------------------------------------------------------------
# Replay engine
# ---------------------------------------------------------------------------

class EventReplay:
    """Sends MAVLink command sequences to PX4 vehicles."""

    ACK_TIMEOUT = 10.0

    def __init__(self, instance: int = 0, host: str = "127.0.0.1"):
        self.instance = instance
        self.host = host
        self.port = MAVLINK_BASE_PORT + instance
        self.conn: mavutil.mavlink_connection | None = None
        self._abort = False
        self._results: list[StepResult] = []

    def connect(self) -> None:
        connstr = f"udpin:{self.host}:{self.port}"
        print(f"{_C.CYAN}[replay] Connecting to {connstr} ...{_C.RESET}")
        self.conn = mavutil.mavlink_connection(connstr)
        self.conn.wait_heartbeat(timeout=30)
        print(f"{_C.GREEN}[replay] Heartbeat received "
              f"(sys={self.conn.target_system}, "
              f"comp={self.conn.target_component}){_C.RESET}")

    def close(self) -> None:
        if self.conn:
            self.conn.close()

    def run_sequence(self, sequence: list[dict[str, Any]]) -> list[StepResult]:
        """Execute a sequence of commands and return results."""
        self._results = []
        total = len(sequence)

        print(f"\n{_C.BOLD}{'='*60}{_C.RESET}")
        print(f"{_C.BOLD}  Replaying {total} commands{_C.RESET}")
        print(f"{_C.BOLD}{'='*60}{_C.RESET}\n")

        for i, step in enumerate(sequence):
            if self._abort:
                print(f"{_C.RED}[replay] Aborted.{_C.RESET}")
                break

            name = step.get("name", f"step_{i}")
            command = step.get("command", "unknown")
            desc = step.get("description", "")
            params = step.get("params", {})
            wait_after = step.get("wait_after", 0.0)
            verify = step.get("verify")

            print(f"{_C.BLUE}--- [{i+1}/{total}] {name} ({command}) ---{_C.RESET}")
            if desc:
                print(f"    {_C.DIM}{desc}{_C.RESET}")

            t0 = time.time()
            result = self._execute_command(name, command, params)
            result.duration = time.time() - t0

            # Verification step
            if result.success and verify:
                vresult = self._verify(verify)
                if not vresult:
                    result.success = False
                    result.message += " (verification failed)"

            self._results.append(result)
            tag = f"{_C.GREEN}OK{_C.RESET}" if result.success else f"{_C.RED}FAIL{_C.RESET}"
            print(f"    Result: [{tag}] {result.message}  ({result.duration:.1f}s)")

            if wait_after > 0 and result.success:
                print(f"    {_C.DIM}Waiting {wait_after}s ...{_C.RESET}")
                time.sleep(wait_after)

            if not result.success:
                # Check if we should abort on failure
                if step.get("abort_on_fail", True):
                    print(f"{_C.RED}[replay] Aborting due to failure.{_C.RESET}")
                    break

        # Summary
        passed = sum(1 for r in self._results if r.success)
        failed = len(self._results) - passed
        color = _C.GREEN if failed == 0 else _C.RED
        print(f"\n{_C.BOLD}{'='*60}{_C.RESET}")
        print(f"  {color}Results: {passed}/{len(self._results)} passed, "
              f"{failed} failed{_C.RESET}")
        print(f"{_C.BOLD}{'='*60}{_C.RESET}\n")

        return self._results

    def _execute_command(self, name: str, command: str, params: dict) -> StepResult:
        """Execute a single command and return the result."""
        try:
            if command == "arm":
                return self._cmd_arm(name)
            elif command == "disarm":
                force = params.get("force", False)
                return self._cmd_disarm(name, force=force)
            elif command == "takeoff":
                alt = params.get("altitude", 15.0)
                return self._cmd_takeoff(name, altitude=alt)
            elif command == "land":
                return self._cmd_land(name)
            elif command == "rtl":
                return self._cmd_rtl(name)
            elif command == "set_mode":
                mode = params.get("mode", "auto_loiter")
                return self._cmd_set_mode(name, mode)
            elif command == "change_speed":
                speed = params.get("speed", 5.0)
                speed_type = params.get("speed_type", 1)
                return self._cmd_change_speed(name, speed, speed_type)
            elif command == "loiter":
                duration = params.get("duration", 30.0)
                return self._cmd_loiter(name, duration)
            elif command == "wait_landed":
                timeout = params.get("timeout", 120.0)
                return self._cmd_wait_landed(name, timeout)
            elif command == "sleep":
                duration = params.get("duration", 1.0)
                return self._cmd_sleep(name, duration)
            else:
                return StepResult(name=name, command=command, success=False,
                                  duration=0, message=f"Unknown command: {command}")
        except Exception as exc:
            return StepResult(name=name, command=command, success=False,
                              duration=0, message=f"Exception: {exc}")

    # -- Command implementations -----------------------------------------------

    def _send_command_long(self, command_id: int,
                           p1: float = 0, p2: float = 0, p3: float = 0,
                           p4: float = 0, p5: float = 0, p6: float = 0,
                           p7: float = 0) -> int:
        """Send COMMAND_LONG and wait for ACK. Returns MAV_RESULT (-1 on timeout)."""
        self.conn.mav.command_long_send(
            self.conn.target_system,
            self.conn.target_component,
            command_id,
            0,  # confirmation
            p1, p2, p3, p4, p5, p6, p7,
        )
        # Wait for COMMAND_ACK
        deadline = time.time() + self.ACK_TIMEOUT
        while time.time() < deadline:
            msg = self.conn.recv_match(type="COMMAND_ACK", blocking=True, timeout=1)
            if msg and msg.command == command_id:
                return msg.result
        return -1

    def _cmd_arm(self, name: str) -> StepResult:
        result = self._send_command_long(MAV_CMD_ARM_DISARM, p1=1)
        ok = result == 0
        return StepResult(name=name, command="arm", success=ok, duration=0,
                          message=f"ACK={result}" if ok else f"ACK failed: result={result}")

    def _cmd_disarm(self, name: str, force: bool = False) -> StepResult:
        p2 = 21196.0 if force else 0.0
        result = self._send_command_long(MAV_CMD_ARM_DISARM, p1=0, p2=p2)
        ok = result == 0
        return StepResult(name=name, command="disarm", success=ok, duration=0,
                          message=f"ACK={result}" if ok else f"ACK failed: result={result}")

    def _cmd_takeoff(self, name: str, altitude: float = 15.0) -> StepResult:
        result = self._send_command_long(MAV_CMD_TAKEOFF, p7=altitude)
        ok = result == 0
        return StepResult(name=name, command="takeoff", success=ok, duration=0,
                          message=f"alt={altitude}m, ACK={result}")

    def _cmd_land(self, name: str) -> StepResult:
        result = self._send_command_long(MAV_CMD_LAND)
        ok = result == 0
        return StepResult(name=name, command="land", success=ok, duration=0,
                          message=f"ACK={result}")

    def _cmd_rtl(self, name: str) -> StepResult:
        return self._cmd_set_mode(name, "auto_rtl")

    def _cmd_set_mode(self, name: str, mode_name: str) -> StepResult:
        custom = PX4_MODES.get(mode_name)
        if custom is None:
            return StepResult(name=name, command="set_mode", success=False,
                              duration=0, message=f"Unknown mode: {mode_name}")
        result = self._send_command_long(MAV_CMD_SET_MODE, p1=1, p2=float(custom))
        ok = result == 0
        return StepResult(name=name, command="set_mode", success=ok, duration=0,
                          message=f"mode={mode_name}, ACK={result}")

    def _cmd_change_speed(self, name: str, speed: float, speed_type: int) -> StepResult:
        result = self._send_command_long(MAV_CMD_CHANGE_SPEED,
                                         p1=float(speed_type), p2=speed)
        ok = result == 0
        return StepResult(name=name, command="change_speed", success=ok, duration=0,
                          message=f"speed={speed}m/s type={speed_type}, ACK={result}")

    def _cmd_loiter(self, name: str, duration: float) -> StepResult:
        # Set loiter mode first
        mode_result = self._cmd_set_mode(name, "auto_loiter")
        if not mode_result.success:
            return mode_result
        print(f"    {_C.DIM}Loitering for {duration}s ...{_C.RESET}")
        time.sleep(duration)
        return StepResult(name=name, command="loiter", success=True, duration=duration,
                          message=f"Loitered {duration}s")

    def _cmd_wait_landed(self, name: str, timeout: float) -> StepResult:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._abort:
                return StepResult(name=name, command="wait_landed", success=False,
                                  duration=0, message="Aborted")
            msg = self.conn.recv_match(type="GLOBAL_POSITION_INT",
                                       blocking=True, timeout=2)
            if msg:
                rel_alt = msg.relative_alt / 1000.0
                if rel_alt < 0.5:
                    return StepResult(name=name, command="wait_landed", success=True,
                                      duration=0, message=f"Landed (alt={rel_alt:.1f}m)")
        return StepResult(name=name, command="wait_landed", success=False,
                          duration=timeout, message=f"Timeout after {timeout}s")

    def _cmd_sleep(self, name: str, duration: float) -> StepResult:
        time.sleep(duration)
        return StepResult(name=name, command="sleep", success=True,
                          duration=duration, message=f"Slept {duration}s")

    # -- Verification ----------------------------------------------------------

    def _verify(self, verify: dict) -> bool:
        """Run a verification check after a command."""
        vtype = verify.get("type", "")
        timeout = verify.get("timeout", 30.0)

        if vtype == "altitude":
            target = verify.get("target", 10.0)
            tolerance = verify.get("tolerance", 2.0)
            return self._verify_altitude(target, tolerance, timeout)
        elif vtype == "armed":
            expected = verify.get("value", True)
            return self._verify_armed(expected, timeout)
        elif vtype == "on_ground":
            return self._verify_on_ground(timeout)
        return True

    def _verify_altitude(self, target: float, tolerance: float,
                         timeout: float) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = self.conn.recv_match(type="GLOBAL_POSITION_INT",
                                       blocking=True, timeout=2)
            if msg:
                rel_alt = msg.relative_alt / 1000.0
                if abs(rel_alt - target) <= tolerance:
                    print(f"    {_C.GREEN}Altitude verified: "
                          f"{rel_alt:.1f}m (target {target}+/-{tolerance}){_C.RESET}")
                    return True
        print(f"    {_C.RED}Altitude verification failed{_C.RESET}")
        return False

    def _verify_armed(self, expected: bool, timeout: float) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = self.conn.recv_match(type="HEARTBEAT", blocking=True, timeout=1)
            if msg:
                armed = bool(msg.base_mode & 128)
                if armed == expected:
                    return True
        return False

    def _verify_on_ground(self, timeout: float) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = self.conn.recv_match(type="GLOBAL_POSITION_INT",
                                       blocking=True, timeout=2)
            if msg and (msg.relative_alt / 1000.0) < 0.5:
                return True
        return False


# ---------------------------------------------------------------------------
# Sequence loading
# ---------------------------------------------------------------------------

def load_sequence(path: str) -> list[dict[str, Any]]:
    """Load a command sequence from a JSON file."""
    with open(path, "r") as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "sequence" in data:
        return data["sequence"]
    print(f"ERROR: Invalid sequence file format: {path}", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="QGC Event Replay - MAVLink command sequence player",
    )
    parser.add_argument(
        "--instance", type=int, default=0,
        help="PX4 SITL instance number (default: 0, port = 14540 + instance)",
    )
    parser.add_argument(
        "--sequence", default=None,
        help="Path to JSON sequence file (default: built-in arm/takeoff/loiter/rtl/land/disarm)",
    )
    parser.add_argument(
        "--host", default="127.0.0.1",
        help="MAVLink host IP address (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--output", default=None,
        help="Path to write JSON results (optional)",
    )
    args = parser.parse_args()

    # Load sequence
    if args.sequence:
        sequence = load_sequence(args.sequence)
        print(f"Loaded {len(sequence)} commands from {args.sequence}")
    else:
        sequence = DEFAULT_SEQUENCE
        print(f"Using default sequence ({len(sequence)} commands)")

    replay = EventReplay(instance=args.instance, host=args.host)

    def _signal_handler(signum, frame):
        print(f"\n{_C.YELLOW}[replay] Signal {signum} received, aborting ...{_C.RESET}")
        replay._abort = True

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    try:
        replay.connect()
        results = replay.run_sequence(sequence)

        # Optionally save results
        if args.output:
            output = {
                "instance": args.instance,
                "host": args.host,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "sequence_file": args.sequence or "default",
                "results": [r.to_dict() for r in results],
                "passed": all(r.success for r in results),
                "total": len(results),
                "succeeded": sum(1 for r in results if r.success),
                "failed": sum(1 for r in results if not r.success),
            }
            out_path = Path(args.output)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(output, indent=2))
            print(f"Results saved to: {out_path}")

        all_ok = all(r.success for r in results)
        sys.exit(0 if all_ok else 1)

    finally:
        replay.close()


if __name__ == "__main__":
    main()
