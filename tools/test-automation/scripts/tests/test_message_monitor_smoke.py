#!/usr/bin/env python3
"""Smoke test for the QGC MessageMonitor wire format.

Validates that:
  1. qgc_uxas_bridge.MonitorTap sends a well-formed JSON datagram with
     the keys MessageMonitorPage expects (ts, dir, lmcp_type, summary,
     vehicle_id) to UDP 45680.
  2. A command datagram with shape {"action": "..."} sent to UDP 45679
     would be picked up by EventBroadcaster's command socket.
  3. message_replay.py round-trips a recorded .jsonl: each line in ->
     each datagram out on the expected port.

This is a *receiver* sanity test — it does NOT start the QGC GUI; instead
it binds plain UDP listeners on 45680 and 45679 to verify the wire.
Run on a machine where those ports are free (i.e. QGC not running).

Usage:
    python3 -m pytest tests/test_message_monitor_smoke.py -q
"""

from __future__ import annotations

import json
import os
import select
import socket
import subprocess
import sys
import tempfile
import time
import unittest

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PKG_DIR = os.path.dirname(_SCRIPT_DIR)  # .../scripts
if _PKG_DIR not in sys.path:
    sys.path.insert(0, _PKG_DIR)


def _bind(port: int) -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", port))
    s.settimeout(2.0)
    return s


def _recv_some(sock: socket.socket, n: int = 1, timeout: float = 2.0) -> list[bytes]:
    deadline = time.time() + timeout
    out: list[bytes] = []
    while time.time() < deadline and len(out) < n:
        ready, _, _ = select.select([sock], [], [], deadline - time.time())
        if not ready:
            break
        data, _ = sock.recvfrom(65535)
        out.append(data)
    return out


class TestMonitorTap(unittest.TestCase):
    def test_monitor_tap_emits_expected_shape(self):
        # Import lazily so the test file can stay self-contained.
        try:
            from qgc_uxas_bridge import MonitorTap
        except Exception as exc:
            self.skipTest(f"qgc_uxas_bridge import failed (deps missing): {exc}")
            return

        listener = _bind(45680)
        try:
            tap = MonitorTap(host="127.0.0.1", port=45680, vehicle_id=42)
            tap.emit("uxas_out", "AirVehicleState",
                     "AirVehicleState vid=42 lat=37.6 lon=127.0",
                     extra={"descriptor": "afrl.cmasi.AirVehicleState"})
            data = _recv_some(listener, 1)
            self.assertEqual(len(data), 1, "no datagram observed on 45680")
            obj = json.loads(data[0])
            self.assertEqual(obj["dir"], "uxas_out")
            self.assertEqual(obj["lmcp_type"], "AirVehicleState")
            self.assertEqual(obj["vehicle_id"], 42)
            self.assertIn("ts", obj)
            self.assertIn("summary", obj)
            tap.close()
        finally:
            listener.close()

    def test_replay_round_trip(self):
        # Write a tiny .jsonl, replay it, capture on 45680 + 45679 + 45678.
        rows = [
            {"ts": 1.0,  "dir": "uxas_out", "channel": "bridge",
             "category": "AirVehicleState", "event": "uxas_out",
             "summary": "vid=1", "payload": {"vehicle_id": 1}},
            {"ts": 1.05, "dir": "command_rx", "channel": "command",
             "category": "command", "event": "arm",
             "summary": "arm", "payload": {"action": "arm"}},
            {"ts": 1.10, "dir": "event_tx", "channel": "event",
             "category": "view", "event": "fly_view_opened",
             "summary": "fly_view_opened", "payload": {}},
        ]
        tmpdir = tempfile.mkdtemp(prefix="msgreplay_")
        jsonl = os.path.join(tmpdir, "rec.jsonl")
        with open(jsonl, "w") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")

        l_bridge   = _bind(45680)
        l_command  = _bind(45679)
        l_event    = _bind(45678)
        try:
            proc = subprocess.Popen(
                [sys.executable,
                 os.path.join(_PKG_DIR, "message_replay.py"),
                 jsonl, "--rate", "100.0"],  # fast playback
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                self.fail("replay subprocess hung")

            b = _recv_some(l_bridge, 1, timeout=1.0)
            c = _recv_some(l_command, 1, timeout=1.0)
            e = _recv_some(l_event, 1, timeout=1.0)

            self.assertEqual(len(b), 1, "no bridge datagram")
            self.assertEqual(len(c), 1, "no command datagram")
            self.assertEqual(len(e), 1, "no event datagram")

            self.assertEqual(json.loads(b[0])["dir"], "uxas_out")
            self.assertEqual(json.loads(c[0])["action"], "arm")
            self.assertEqual(json.loads(e[0])["event"], "fly_view_opened")
        finally:
            l_bridge.close()
            l_command.close()
            l_event.close()


if __name__ == "__main__":
    unittest.main()
