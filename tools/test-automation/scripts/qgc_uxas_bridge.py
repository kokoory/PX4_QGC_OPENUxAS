#!/usr/bin/env python3
"""
QGC (MAVLink) <-> OpenUxAS (LMCP/ZeroMQ) Bridge.

Bi-directional bridge that:
  - Receives MAVLink vehicle state from QGC/PX4 -> converts to LMCP XML ->
    publishes via ZeroMQ to OpenUxAS.
  - Receives LMCP MissionCommand from OpenUxAS via ZeroMQ -> converts to
    MAVLink mission items -> uploads to QGC/PX4.

Provides an interactive CLI for registering vehicles, requesting area/line
searches, and checking bridge status.

Usage:
    python3 qgc_uxas_bridge.py \\
        --mavlink udp:127.0.0.1:14550 \\
        --uxas-in tcp://127.0.0.1:5560 \\
        --uxas-out tcp://127.0.0.1:5561

    python3 qgc_uxas_bridge.py \\
        --mavlink udp:127.0.0.1:14550 \\
        --uxas-in tcp://127.0.0.1:5560 \\
        --uxas-out tcp://127.0.0.1:5561 \\
        --vehicle-id 1 --auto-register
"""

from __future__ import annotations

import argparse
import cmd
import json
import math
import signal
import sys
import textwrap
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
from xml.dom import minidom

try:
    from pymavlink import mavutil
except ImportError:
    print("ERROR: pymavlink is required.  Install with: pip install pymavlink",
          file=sys.stderr)
    sys.exit(1)

try:
    import zmq
except ImportError:
    print("ERROR: pyzmq is required.  Install with: pip install pyzmq",
          file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# LMCP XML namespace and series
# ---------------------------------------------------------------------------
LMCP_NS = "afrl.cmasi"
LMCP_SERIES = "CMASI"
LMCP_VERSION = "3"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class VehicleState:
    """Latest vehicle state extracted from MAVLink."""
    vehicle_id: int = 1
    lat_deg: float = 0.0
    lon_deg: float = 0.0
    alt_m: float = 0.0
    heading_deg: float = 0.0
    airspeed_mps: float = 0.0
    groundspeed_mps: float = 0.0
    climb_rate_mps: float = 0.0
    roll_deg: float = 0.0
    pitch_deg: float = 0.0
    yaw_deg: float = 0.0
    battery_pct: float = 100.0
    fuel_remaining: float = 100.0
    mode: str = "unknown"
    armed: bool = False
    timestamp_ms: int = 0


@dataclass
class WaypointItem:
    """Single waypoint from an LMCP MissionCommand."""
    seq: int = 0
    lat_deg: float = 0.0
    lon_deg: float = 0.0
    alt_m: float = 50.0
    speed_mps: float = 15.0
    turn_type: str = "TurnShort"


@dataclass
class BridgeStats:
    """Counters for bridge activity."""
    mavlink_msgs_in: int = 0
    mavlink_msgs_out: int = 0
    lmcp_msgs_in: int = 0
    lmcp_msgs_out: int = 0
    missions_uploaded: int = 0
    errors: int = 0
    start_time: float = field(default_factory=time.monotonic)


# ---------------------------------------------------------------------------
# LMCP XML builders
# ---------------------------------------------------------------------------

def _pretty_xml(element: ET.Element) -> str:
    """Return pretty-printed XML string."""
    rough = ET.tostring(element, encoding="unicode")
    return minidom.parseString(rough).toprettyxml(indent="  ")


def build_air_vehicle_configuration(
    vehicle_id: int,
    label: str = "UAV",
    min_speed: float = 5.0,
    max_speed: float = 30.0,
    max_climb: float = 5.0,
    min_alt: float = 0.0,
    max_alt: float = 500.0,
    max_bank_deg: float = 25.0,
) -> str:
    """Build LMCP AirVehicleConfiguration XML."""
    root = ET.Element("AirVehicleConfiguration", Series=LMCP_SERIES,
                      Version=LMCP_VERSION)
    ET.SubElement(root, "ID").text = str(vehicle_id)
    ET.SubElement(root, "Label").text = label
    ET.SubElement(root, "MinimumSpeed").text = str(min_speed)
    ET.SubElement(root, "MaximumSpeed").text = str(max_speed)
    ET.SubElement(root, "NominalSpeed").text = str((min_speed + max_speed) / 2)
    ET.SubElement(root, "NominalAltitude").text = str((min_alt + max_alt) / 2)
    ET.SubElement(root, "NominalAltitudeType").text = "MSL"
    ET.SubElement(root, "MaximumClimbRate").text = str(max_climb)

    flight_profile = ET.SubElement(root, "NominalFlightProfile")
    ET.SubElement(flight_profile, "Airspeed").text = str(
        (min_speed + max_speed) / 2)
    ET.SubElement(flight_profile, "MaxBankAngle").text = str(max_bank_deg)

    alt_range = ET.SubElement(root, "AvailableAltitudeRange")
    ET.SubElement(alt_range, "MinAltitude").text = str(min_alt)
    ET.SubElement(alt_range, "MaxAltitude").text = str(max_alt)
    ET.SubElement(alt_range, "AltitudeType").text = "MSL"

    speed_range = ET.SubElement(root, "AvailableSpeedRange")
    ET.SubElement(speed_range, "MinSpeed").text = str(min_speed)
    ET.SubElement(speed_range, "MaxSpeed").text = str(max_speed)

    return _pretty_xml(root)


def build_air_vehicle_state(state: VehicleState) -> str:
    """Build LMCP AirVehicleState XML from current MAVLink state."""
    root = ET.Element("AirVehicleState", Series=LMCP_SERIES,
                      Version=LMCP_VERSION)
    ET.SubElement(root, "ID").text = str(state.vehicle_id)
    ET.SubElement(root, "Time").text = str(state.timestamp_ms)

    location = ET.SubElement(root, "Location")
    ET.SubElement(location, "Latitude").text = f"{state.lat_deg:.7f}"
    ET.SubElement(location, "Longitude").text = f"{state.lon_deg:.7f}"
    ET.SubElement(location, "Altitude").text = f"{state.alt_m:.2f}"
    ET.SubElement(location, "AltitudeType").text = "MSL"

    ET.SubElement(root, "Airspeed").text = f"{state.airspeed_mps:.2f}"
    ET.SubElement(root, "Groundspeed").text = f"{state.groundspeed_mps:.2f}"
    ET.SubElement(root, "Course").text = f"{state.heading_deg:.2f}"
    ET.SubElement(root, "Heading").text = f"{state.heading_deg:.2f}"

    attitude = ET.SubElement(root, "Attitude")
    ET.SubElement(attitude, "Roll").text = f"{state.roll_deg:.2f}"
    ET.SubElement(attitude, "Pitch").text = f"{state.pitch_deg:.2f}"
    ET.SubElement(attitude, "Yaw").text = f"{state.yaw_deg:.2f}"

    ET.SubElement(root, "EnergyAvailable").text = f"{state.fuel_remaining:.1f}"
    ET.SubElement(root, "ActualEnergyRate").text = "0.0"

    ET.SubElement(root, "Mode").text = state.mode.upper()
    ET.SubElement(root, "CurrentWaypoint").text = "0"

    return _pretty_xml(root)


def build_area_search_task(
    task_id: int,
    search_area: list[tuple[float, float]],
    vehicle_ids: list[int],
    altitude_m: float = 50.0,
    speed_mps: float = 15.0,
    label: str = "AreaSearch",
) -> str:
    """Build LMCP AreaSearchTask XML.

    *search_area* is a list of (lat, lon) tuples defining the polygon.
    """
    root = ET.Element("AreaSearchTask", Series=LMCP_SERIES,
                      Version=LMCP_VERSION)
    ET.SubElement(root, "TaskID").text = str(task_id)
    ET.SubElement(root, "Label").text = label
    ET.SubElement(root, "Priority").text = "0"
    ET.SubElement(root, "Required").text = "true"

    eligible = ET.SubElement(root, "EligibleEntities")
    for vid in vehicle_ids:
        ET.SubElement(eligible, "EntityID").text = str(vid)

    search_elem = ET.SubElement(root, "SearchArea")
    boundary = ET.SubElement(search_elem, "Boundary")
    for lat, lon in search_area:
        point = ET.SubElement(boundary, "BoundaryPoint")
        ET.SubElement(point, "Latitude").text = f"{lat:.7f}"
        ET.SubElement(point, "Longitude").text = f"{lon:.7f}"

    desired = ET.SubElement(root, "DesiredAction")
    ET.SubElement(desired, "Altitude").text = str(altitude_m)
    ET.SubElement(desired, "AltitudeType").text = "MSL"
    ET.SubElement(desired, "Speed").text = str(speed_mps)

    return _pretty_xml(root)


def build_line_search_task(
    task_id: int,
    line_points: list[tuple[float, float]],
    vehicle_ids: list[int],
    altitude_m: float = 50.0,
    speed_mps: float = 15.0,
    label: str = "LineSearch",
) -> str:
    """Build LMCP LineSearchTask XML.

    *line_points* is a list of (lat, lon) tuples defining the line.
    """
    root = ET.Element("LineSearchTask", Series=LMCP_SERIES,
                       Version=LMCP_VERSION)
    ET.SubElement(root, "TaskID").text = str(task_id)
    ET.SubElement(root, "Label").text = label
    ET.SubElement(root, "Priority").text = "0"
    ET.SubElement(root, "Required").text = "true"

    eligible = ET.SubElement(root, "EligibleEntities")
    for vid in vehicle_ids:
        ET.SubElement(eligible, "EntityID").text = str(vid)

    point_list = ET.SubElement(root, "PointList")
    for lat, lon in line_points:
        point = ET.SubElement(point_list, "LinePoint")
        ET.SubElement(point, "Latitude").text = f"{lat:.7f}"
        ET.SubElement(point, "Longitude").text = f"{lon:.7f}"

    desired = ET.SubElement(root, "DesiredAction")
    ET.SubElement(desired, "Altitude").text = str(altitude_m)
    ET.SubElement(desired, "AltitudeType").text = "MSL"
    ET.SubElement(desired, "Speed").text = str(speed_mps)

    return _pretty_xml(root)


def build_automation_request(
    request_id: int,
    task_ids: list[int],
    vehicle_ids: list[int],
    operating_region: int = 0,
    redo_all: bool = False,
) -> str:
    """Build LMCP AutomationRequest XML."""
    root = ET.Element("AutomationRequest", Series=LMCP_SERIES,
                       Version=LMCP_VERSION)
    ET.SubElement(root, "RequestID").text = str(request_id)

    task_list = ET.SubElement(root, "TaskList")
    for tid in task_ids:
        ET.SubElement(task_list, "TaskID").text = str(tid)

    entity_list = ET.SubElement(root, "EntityList")
    for vid in vehicle_ids:
        ET.SubElement(entity_list, "EntityID").text = str(vid)

    ET.SubElement(root, "OperatingRegion").text = str(operating_region)
    ET.SubElement(root, "RedoAllTasks").text = str(redo_all).lower()

    return _pretty_xml(root)


# ---------------------------------------------------------------------------
# LMCP XML parser helpers
# ---------------------------------------------------------------------------

def _get_text(parent: ET.Element, tag: str, default: str = "0") -> str:
    el = parent.find(tag)
    return el.text.strip() if el is not None and el.text else default


def parse_mission_command(xml_str: str) -> list[WaypointItem]:
    """Parse an LMCP MissionCommand XML into a list of waypoints."""
    root = ET.fromstring(xml_str)
    waypoints: list[WaypointItem] = []

    wp_list = root.find("WaypointList")
    if wp_list is None:
        wp_list = root.find("WaypointEntityList")
    if wp_list is None:
        # Try direct Waypoint children
        wp_list = root

    seq = 0
    for wp_elem in wp_list:
        tag = wp_elem.tag
        if "Waypoint" not in tag and "Location" not in tag:
            continue

        lat = float(_get_text(wp_elem, "Latitude",
                              _get_text(wp_elem, ".//Latitude", "0")))
        lon = float(_get_text(wp_elem, "Longitude",
                              _get_text(wp_elem, ".//Longitude", "0")))
        alt = float(_get_text(wp_elem, "Altitude",
                              _get_text(wp_elem, ".//Altitude", "50")))
        speed = float(_get_text(wp_elem, "Speed",
                                _get_text(wp_elem, ".//Speed", "15")))
        turn = _get_text(wp_elem, "TurnType", "TurnShort")

        waypoints.append(WaypointItem(
            seq=seq, lat_deg=lat, lon_deg=lon, alt_m=alt,
            speed_mps=speed, turn_type=turn,
        ))
        seq += 1

    return waypoints


def waypoints_to_mavlink_items(waypoints: list[WaypointItem]) -> list[dict]:
    """Convert parsed LMCP waypoints to MAVLink mission item dicts."""
    items = []
    for wp in waypoints:
        items.append({
            "frame": 6,  # MAV_FRAME_GLOBAL_RELATIVE_ALT_INT
            "command": 16,  # MAV_CMD_NAV_WAYPOINT
            "autocontinue": 1,
            "param1": 0,   # hold time
            "param2": 5.0, # acceptance radius
            "param3": 0,   # pass through
            "param4": 0,   # yaw
            "lat": wp.lat_deg,
            "lon": wp.lon_deg,
            "alt": wp.alt_m,
        })
    return items


# ---------------------------------------------------------------------------
# MAVLink reader thread
# ---------------------------------------------------------------------------

class MAVLinkReader:
    """Reads MAVLink messages and updates vehicle state."""

    def __init__(self, connection_str: str, vehicle_id: int = 1):
        self.connection_str = connection_str
        self.vehicle_id = vehicle_id
        self.conn: Optional[Any] = None
        self.state = VehicleState(vehicle_id=vehicle_id)
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self.stats = BridgeStats()

    def start(self) -> None:
        print(f"[MAVLink] Connecting to {self.connection_str} ...")
        self.conn = mavutil.mavlink_connection(self.connection_str)
        self.conn.wait_heartbeat(timeout=30)
        print(f"[MAVLink] Heartbeat received (system {self.conn.target_system},"
              f" component {self.conn.target_component})")
        self._running = True
        self._thread = threading.Thread(target=self._read_loop, daemon=True,
                                        name="mavlink-reader")
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
        if self.conn:
            self.conn.close()

    def get_state(self) -> VehicleState:
        with self._lock:
            # Return a shallow copy
            import copy
            return copy.copy(self.state)

    def upload_mission(self, items: list[dict]) -> bool:
        """Upload mission items to the vehicle via MAVLink protocol."""
        if not self.conn:
            return False
        print(f"[MAVLink] Uploading {len(items)} mission items ...")

        self.conn.waypoint_clear_all_send()
        time.sleep(0.5)
        self.conn.waypoint_count_send(len(items))

        for i in range(len(items)):
            msg = self.conn.recv_match(
                type=["MISSION_REQUEST", "MISSION_REQUEST_INT"],
                blocking=True, timeout=10,
            )
            if msg is None:
                print(f"[MAVLink] Timeout waiting for MISSION_REQUEST (item {i})")
                self.stats.errors += 1
                return False

            item = items[msg.seq] if msg.seq < len(items) else items[i]
            self.conn.mav.mission_item_int_send(
                self.conn.target_system,
                self.conn.target_component,
                msg.seq,
                item.get("frame", 6),
                item.get("command", 16),
                1 if msg.seq == 0 else 0,  # current
                int(item.get("autocontinue", 1)),
                item.get("param1", 0),
                item.get("param2", 5.0),
                item.get("param3", 0),
                item.get("param4", 0),
                int(item["lat"] * 1e7),
                int(item["lon"] * 1e7),
                item.get("alt", 50),
            )

        ack = self.conn.recv_match(type="MISSION_ACK", blocking=True,
                                    timeout=10)
        if ack and ack.type == 0:
            self.stats.missions_uploaded += 1
            self.stats.mavlink_msgs_out += len(items)
            print("[MAVLink] Mission upload complete")
            return True

        print(f"[MAVLink] Mission upload failed (ack={ack})")
        self.stats.errors += 1
        return False

    def _read_loop(self) -> None:
        while self._running:
            try:
                msg = self.conn.recv_match(blocking=True, timeout=0.5)
                if msg is None:
                    continue

                mtype = msg.get_type()
                with self._lock:
                    self.stats.mavlink_msgs_in += 1

                    if mtype == "GLOBAL_POSITION_INT":
                        self.state.lat_deg = msg.lat / 1e7
                        self.state.lon_deg = msg.lon / 1e7
                        self.state.alt_m = msg.alt / 1e3
                        self.state.heading_deg = msg.hdg / 100.0
                        self.state.climb_rate_mps = -msg.vz / 100.0
                        self.state.groundspeed_mps = math.sqrt(
                            (msg.vx / 100.0) ** 2 + (msg.vy / 100.0) ** 2)
                        self.state.timestamp_ms = int(time.time() * 1000)

                    elif mtype == "ATTITUDE":
                        self.state.roll_deg = math.degrees(msg.roll)
                        self.state.pitch_deg = math.degrees(msg.pitch)
                        self.state.yaw_deg = math.degrees(msg.yaw)

                    elif mtype == "VFR_HUD":
                        self.state.airspeed_mps = msg.airspeed
                        self.state.groundspeed_mps = msg.groundspeed

                    elif mtype == "SYS_STATUS":
                        self.state.battery_pct = max(0.0, msg.battery_remaining)
                        self.state.fuel_remaining = self.state.battery_pct

                    elif mtype == "HEARTBEAT":
                        self.state.armed = bool(
                            msg.base_mode
                            & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
                        # Decode mode name from custom_mode
                        custom = msg.custom_mode
                        main_mode = (custom >> 16) & 0xFF
                        sub_mode = (custom >> 24) & 0xFF
                        mode_map = {
                            (1, 0): "manual",
                            (2, 0): "posctl",
                            (4, 1): "auto_ready",
                            (4, 2): "auto_takeoff",
                            (4, 3): "auto_loiter",
                            (4, 4): "auto_mission",
                            (4, 5): "auto_rtl",
                            (4, 6): "auto_land",
                            (6, 0): "offboard",
                        }
                        self.state.mode = mode_map.get(
                            (main_mode, sub_mode), f"custom({main_mode},{sub_mode})")

            except Exception as exc:
                if self._running:
                    print(f"[MAVLink] Read error: {exc}")
                    time.sleep(0.5)


# ---------------------------------------------------------------------------
# ZeroMQ UxAS interface
# ---------------------------------------------------------------------------

class UxASInterface:
    """ZeroMQ pub/sub interface to OpenUxAS."""

    def __init__(self, sub_addr: str, pub_addr: str):
        self.sub_addr = sub_addr
        self.pub_addr = pub_addr
        self.ctx: Optional[zmq.Context] = None
        self.pub_socket: Optional[zmq.Socket] = None
        self.sub_socket: Optional[zmq.Socket] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._received_msgs: list[str] = []
        self._callbacks: list[Any] = []
        self.stats = BridgeStats()

    def start(self) -> None:
        self.ctx = zmq.Context()

        self.pub_socket = self.ctx.socket(zmq.PUB)
        self.pub_socket.connect(self.pub_addr)
        print(f"[UxAS] PUB connected to {self.pub_addr}")

        self.sub_socket = self.ctx.socket(zmq.SUB)
        self.sub_socket.connect(self.sub_addr)
        self.sub_socket.setsockopt_string(zmq.SUBSCRIBE, "")
        print(f"[UxAS] SUB connected to {self.sub_addr}")

        self._running = True
        self._thread = threading.Thread(target=self._receive_loop, daemon=True,
                                        name="uxas-receiver")
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
        if self.pub_socket:
            self.pub_socket.close()
        if self.sub_socket:
            self.sub_socket.close()
        if self.ctx:
            self.ctx.term()

    def publish(self, xml_str: str) -> None:
        """Publish an LMCP XML message to UxAS."""
        if self.pub_socket is None:
            print("[UxAS] Not connected, cannot publish")
            return
        self.pub_socket.send_string(xml_str)
        with self._lock:
            self.stats.lmcp_msgs_out += 1

    def on_message(self, callback) -> None:
        """Register a callback for incoming LMCP messages."""
        self._callbacks.append(callback)

    def get_received(self) -> list[str]:
        with self._lock:
            return list(self._received_msgs)

    def _receive_loop(self) -> None:
        poller = zmq.Poller()
        poller.register(self.sub_socket, zmq.POLLIN)

        while self._running:
            try:
                socks = dict(poller.poll(timeout=500))
                if self.sub_socket in socks:
                    msg_str = self.sub_socket.recv_string()
                    with self._lock:
                        self.stats.lmcp_msgs_in += 1
                        self._received_msgs.append(msg_str)
                        # Keep only last 100 messages
                        if len(self._received_msgs) > 100:
                            self._received_msgs = self._received_msgs[-100:]
                    for cb in self._callbacks:
                        try:
                            cb(msg_str)
                        except Exception as exc:
                            print(f"[UxAS] Callback error: {exc}")
            except zmq.ZMQError:
                if self._running:
                    time.sleep(0.1)
            except Exception as exc:
                if self._running:
                    print(f"[UxAS] Receive error: {exc}")
                    time.sleep(0.5)


# ---------------------------------------------------------------------------
# Bridge core
# ---------------------------------------------------------------------------

class QGCUxASBridge:
    """Main bridge orchestrating MAVLink <-> LMCP conversion."""

    DEFAULT_STATE_RATE_HZ = 2.0  # How often to publish vehicle state

    def __init__(
        self,
        mavlink_str: str,
        uxas_in: str,
        uxas_out: str,
        vehicle_id: int = 1,
        state_rate_hz: float = DEFAULT_STATE_RATE_HZ,
    ):
        self.vehicle_id = vehicle_id
        self.state_rate_hz = state_rate_hz

        self.mavlink = MAVLinkReader(mavlink_str, vehicle_id=vehicle_id)
        self.uxas = UxASInterface(sub_addr=uxas_in, pub_addr=uxas_out)

        self._running = False
        self._state_thread: Optional[threading.Thread] = None
        self._registered = False
        self._task_counter = 100
        self._request_counter = 1000

    def start(self) -> None:
        """Start both MAVLink and UxAS connections + state publisher."""
        self.mavlink.start()
        self.uxas.start()
        self.uxas.on_message(self._handle_uxas_message)

        self._running = True
        self._state_thread = threading.Thread(
            target=self._state_publish_loop, daemon=True,
            name="state-publisher")
        self._state_thread.start()
        print(f"[Bridge] Running (vehicle_id={self.vehicle_id}, "
              f"state rate={self.state_rate_hz} Hz)")

    def stop(self) -> None:
        self._running = False
        if self._state_thread:
            self._state_thread.join(timeout=3)
        self.uxas.stop()
        self.mavlink.stop()
        print("[Bridge] Stopped")

    def register_vehicle(self) -> None:
        """Send AirVehicleConfiguration to UxAS to register this vehicle."""
        xml = build_air_vehicle_configuration(
            vehicle_id=self.vehicle_id,
            label=f"Vehicle_{self.vehicle_id}",
        )
        self.uxas.publish(xml)
        self._registered = True
        print(f"[Bridge] Vehicle {self.vehicle_id} registered with UxAS")

    def send_area_search(
        self,
        area: list[tuple[float, float]],
        altitude: float = 50.0,
        speed: float = 15.0,
    ) -> int:
        """Create and send an AreaSearchTask + AutomationRequest."""
        task_id = self._next_task_id()

        task_xml = build_area_search_task(
            task_id=task_id,
            search_area=area,
            vehicle_ids=[self.vehicle_id],
            altitude_m=altitude,
            speed_mps=speed,
        )
        self.uxas.publish(task_xml)
        print(f"[Bridge] AreaSearchTask {task_id} sent")

        req_id = self._next_request_id()
        req_xml = build_automation_request(
            request_id=req_id,
            task_ids=[task_id],
            vehicle_ids=[self.vehicle_id],
        )
        self.uxas.publish(req_xml)
        print(f"[Bridge] AutomationRequest {req_id} sent for task {task_id}")
        return task_id

    def send_line_search(
        self,
        points: list[tuple[float, float]],
        altitude: float = 50.0,
        speed: float = 15.0,
    ) -> int:
        """Create and send a LineSearchTask + AutomationRequest."""
        task_id = self._next_task_id()

        task_xml = build_line_search_task(
            task_id=task_id,
            line_points=points,
            vehicle_ids=[self.vehicle_id],
            altitude_m=altitude,
            speed_mps=speed,
        )
        self.uxas.publish(task_xml)
        print(f"[Bridge] LineSearchTask {task_id} sent")

        req_id = self._next_request_id()
        req_xml = build_automation_request(
            request_id=req_id,
            task_ids=[task_id],
            vehicle_ids=[self.vehicle_id],
        )
        self.uxas.publish(req_xml)
        print(f"[Bridge] AutomationRequest {req_id} sent for task {task_id}")
        return task_id

    def get_status(self) -> dict:
        """Return bridge status as a dictionary."""
        state = self.mavlink.get_state()
        return {
            "vehicle_id": self.vehicle_id,
            "registered": self._registered,
            "running": self._running,
            "vehicle_state": {
                "lat": state.lat_deg,
                "lon": state.lon_deg,
                "alt": state.alt_m,
                "heading": state.heading_deg,
                "airspeed": state.airspeed_mps,
                "groundspeed": state.groundspeed_mps,
                "mode": state.mode,
                "armed": state.armed,
                "battery": state.battery_pct,
            },
            "stats": {
                "mavlink_in": self.mavlink.stats.mavlink_msgs_in,
                "mavlink_out": self.mavlink.stats.mavlink_msgs_out,
                "lmcp_in": self.uxas.stats.lmcp_msgs_in,
                "lmcp_out": self.uxas.stats.lmcp_msgs_out,
                "missions_uploaded": self.mavlink.stats.missions_uploaded,
                "errors": (self.mavlink.stats.errors
                           + self.uxas.stats.errors),
                "uptime_s": round(
                    time.monotonic() - self.mavlink.stats.start_time, 1),
            },
        }

    # -- Internal ------------------------------------------------------------

    def _next_task_id(self) -> int:
        self._task_counter += 1
        return self._task_counter

    def _next_request_id(self) -> int:
        self._request_counter += 1
        return self._request_counter

    def _state_publish_loop(self) -> None:
        """Periodically convert MAVLink state to LMCP and publish."""
        interval = 1.0 / self.state_rate_hz
        while self._running:
            try:
                state = self.mavlink.get_state()
                xml = build_air_vehicle_state(state)
                self.uxas.publish(xml)
            except Exception as exc:
                print(f"[Bridge] State publish error: {exc}")
            time.sleep(interval)

    def _handle_uxas_message(self, xml_str: str) -> None:
        """Handle incoming LMCP message from UxAS."""
        try:
            root = ET.fromstring(xml_str)
        except ET.ParseError:
            return

        tag = root.tag
        if "MissionCommand" in tag:
            self._handle_mission_command(xml_str)
        elif "AutomationResponse" in tag:
            print(f"[Bridge] Received AutomationResponse from UxAS")
        else:
            print(f"[Bridge] Received LMCP message: {tag}")

    def _handle_mission_command(self, xml_str: str) -> None:
        """Convert LMCP MissionCommand to MAVLink mission and upload."""
        print("[Bridge] Received MissionCommand from UxAS -- converting to MAVLink")
        try:
            waypoints = parse_mission_command(xml_str)
            if not waypoints:
                print("[Bridge] MissionCommand contained no waypoints")
                return
            items = waypoints_to_mavlink_items(waypoints)
            success = self.mavlink.upload_mission(items)
            if success:
                print(f"[Bridge] Mission with {len(items)} waypoints uploaded "
                      "to vehicle")
            else:
                print("[Bridge] Failed to upload mission to vehicle")
        except Exception as exc:
            print(f"[Bridge] Error processing MissionCommand: {exc}")


# ---------------------------------------------------------------------------
# Interactive CLI
# ---------------------------------------------------------------------------

class BridgeCLI(cmd.Cmd):
    """Interactive CLI for the QGC-UxAS bridge."""

    intro = textwrap.dedent("""\

        QGC <-> OpenUxAS Bridge CLI
        Type 'help' for available commands.
    """)
    prompt = "bridge> "

    def __init__(self, bridge: QGCUxASBridge):
        super().__init__()
        self.bridge = bridge

    def do_register(self, arg: str) -> None:
        """Register the vehicle with UxAS (sends AirVehicleConfiguration)."""
        self.bridge.register_vehicle()

    def do_area_search(self, arg: str) -> None:
        """Start an area search task.
        Usage: area_search lat1,lon1 lat2,lon2 lat3,lon3 lat4,lon4 [alt] [speed]
        Example: area_search 47.397,8.545 47.398,8.545 47.398,8.546 47.397,8.546 50 15
        """
        parts = arg.split()
        if len(parts) < 3:
            print("Need at least 3 polygon points. "
                  "Usage: area_search lat1,lon1 lat2,lon2 lat3,lon3 [alt] [speed]")
            return

        area = []
        alt = 50.0
        speed = 15.0

        for p in parts:
            if "," in p:
                try:
                    lat_s, lon_s = p.split(",")
                    area.append((float(lat_s), float(lon_s)))
                except ValueError:
                    # Might be alt or speed
                    pass
            else:
                try:
                    val = float(p)
                    if alt == 50.0 and len(area) >= 3:
                        alt = val
                    else:
                        speed = val
                except ValueError:
                    pass

        if len(area) < 3:
            print("Need at least 3 valid polygon points")
            return

        task_id = self.bridge.send_area_search(area, altitude=alt, speed=speed)
        print(f"Area search task {task_id} submitted")

    def do_line_search(self, arg: str) -> None:
        """Start a line search task.
        Usage: line_search lat1,lon1 lat2,lon2 [lat3,lon3 ...] [alt] [speed]
        Example: line_search 47.397,8.545 47.398,8.546 50 15
        """
        parts = arg.split()
        if len(parts) < 2:
            print("Need at least 2 line points. "
                  "Usage: line_search lat1,lon1 lat2,lon2 [alt] [speed]")
            return

        points = []
        alt = 50.0
        speed = 15.0

        for p in parts:
            if "," in p:
                try:
                    lat_s, lon_s = p.split(",")
                    points.append((float(lat_s), float(lon_s)))
                except ValueError:
                    pass
            else:
                try:
                    val = float(p)
                    if alt == 50.0 and len(points) >= 2:
                        alt = val
                    else:
                        speed = val
                except ValueError:
                    pass

        if len(points) < 2:
            print("Need at least 2 valid line points")
            return

        task_id = self.bridge.send_line_search(points, altitude=alt, speed=speed)
        print(f"Line search task {task_id} submitted")

    def do_status(self, arg: str) -> None:
        """Show bridge status including vehicle state and message counters."""
        status = self.bridge.get_status()
        print(json.dumps(status, indent=2))

    def do_state(self, arg: str) -> None:
        """Show current vehicle state."""
        state = self.bridge.mavlink.get_state()
        info = {
            "vehicle_id": state.vehicle_id,
            "position": f"({state.lat_deg:.7f}, {state.lon_deg:.7f})",
            "altitude_m": f"{state.alt_m:.1f}",
            "heading_deg": f"{state.heading_deg:.1f}",
            "airspeed_mps": f"{state.airspeed_mps:.1f}",
            "groundspeed_mps": f"{state.groundspeed_mps:.1f}",
            "roll_deg": f"{state.roll_deg:.1f}",
            "pitch_deg": f"{state.pitch_deg:.1f}",
            "yaw_deg": f"{state.yaw_deg:.1f}",
            "mode": state.mode,
            "armed": state.armed,
            "battery_pct": f"{state.battery_pct:.0f}",
        }
        for k, v in info.items():
            print(f"  {k:20s}: {v}")

    def do_messages(self, arg: str) -> None:
        """Show recent LMCP messages received from UxAS."""
        msgs = self.bridge.uxas.get_received()
        if not msgs:
            print("No LMCP messages received yet")
            return
        count = int(arg) if arg.strip().isdigit() else 5
        for msg in msgs[-count:]:
            # Print first 200 chars of each
            preview = msg[:200].replace("\n", " ")
            print(f"  {preview}...")

    def do_quit(self, arg: str) -> bool:
        """Exit the bridge CLI."""
        print("Shutting down bridge ...")
        return True

    def do_exit(self, arg: str) -> bool:
        """Exit the bridge CLI."""
        return self.do_quit(arg)

    do_EOF = do_quit


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="QGC (MAVLink) <-> OpenUxAS (LMCP/ZeroMQ) Bridge",
    )
    parser.add_argument(
        "--mavlink", type=str, default="udp:127.0.0.1:14550",
        help="MAVLink connection string (default: udp:127.0.0.1:14550)",
    )
    parser.add_argument(
        "--uxas-in", type=str, default="tcp://127.0.0.1:5560",
        help="ZeroMQ SUB address for receiving from UxAS "
             "(default: tcp://127.0.0.1:5560)",
    )
    parser.add_argument(
        "--uxas-out", type=str, default="tcp://127.0.0.1:5561",
        help="ZeroMQ PUB address for sending to UxAS "
             "(default: tcp://127.0.0.1:5561)",
    )
    parser.add_argument(
        "--vehicle-id", type=int, default=1,
        help="Vehicle ID for LMCP messages (default: 1)",
    )
    parser.add_argument(
        "--state-rate", type=float, default=2.0,
        help="Vehicle state publish rate in Hz (default: 2.0)",
    )
    parser.add_argument(
        "--auto-register", action="store_true", default=False,
        help="Automatically register vehicle on startup",
    )
    parser.add_argument(
        "--non-interactive", action="store_true", default=False,
        help="Run without interactive CLI (bridge only)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    bridge = QGCUxASBridge(
        mavlink_str=args.mavlink,
        uxas_in=args.uxas_in,
        uxas_out=args.uxas_out,
        vehicle_id=args.vehicle_id,
        state_rate_hz=args.state_rate,
    )

    def signal_handler(signum, frame):
        print(f"\n[Bridge] Signal {signum} received -- shutting down")
        bridge.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        bridge.start()

        if args.auto_register:
            time.sleep(1)  # Let connections stabilize
            bridge.register_vehicle()

        if args.non_interactive:
            print("[Bridge] Running in non-interactive mode. Ctrl+C to stop.")
            while True:
                time.sleep(1)
        else:
            cli = BridgeCLI(bridge)
            cli.cmdloop()

    except KeyboardInterrupt:
        pass
    finally:
        bridge.stop()

    return 0


if __name__ == "__main__":
    sys.exit(main())
