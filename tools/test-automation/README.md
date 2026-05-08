# QGC Test Automation Framework

## Overview

Automated testing framework for QGroundControl (QGC) with PX4 SITL (Software In The Loop).
The framework drives end-to-end flight tests by injecting MAVLink commands via pymavlink,
monitoring and controlling the QGC UI through the EventBroadcaster UDP protocol, executing
YAML-based test scenarios with declarative pass/fail criteria, bridging to OpenUxAS over
ZeroMQ for cooperative autonomy tests, and orchestrating multi-vehicle simulations with up
to 10 concurrent SITL instances.

All scripts are standalone Python 3 programs. The test orchestrator reads YAML scenario
files, executes each step sequentially, evaluates assertions, and produces JSON result
files suitable for CI pipelines or human review.

## Architecture

```
 +---------------------+         MAVLink (UDP 14540+N)        +------------------+
 |                     | -------------------------------------> |                  |
 |  Test Orchestrator  |                                       |   PX4 SITL (N)   |
 |  (Python / YAML)   | <------------------------------------- |                  |
 |                     |         MAVLink telemetry              +------------------+
 +---------------------+
    |            ^
    |            |  UDP JSON events (port 45678, QGC -> External)
    |            +-----------------------------------------------+
    |                                                            |
    |   UDP JSON commands (port 45679, External -> QGC)          |
    +-------------------------------------------+                |
                                                |                |
                                          +-----v----------------v-----+
                                          |                            |
                                          |     QGroundControl         |
                                          |     (EventBroadcaster)     |
                                          |                            |
                                          +----------------------------+

 +---------------------+         ZeroMQ (LMCP)          +------------------+
 |  qgc_uxas_bridge.py | <-----------------------------> |    OpenUxAS      |
 |  (MAVLink <-> LMCP) |                                 |  (Autonomy Svc)  |
 +---------------------+                                 +------------------+
```

**Data flows:**

- **Test Orchestrator -> PX4 SITL**: MAVLink COMMAND_LONG messages (arm, takeoff, set_mode,
  etc.) sent via pymavlink over `udpin:127.0.0.1:<14540+N>`.
- **PX4 SITL -> Test Orchestrator**: Heartbeat, GLOBAL_POSITION_INT, COMMAND_ACK, and
  MISSION_ITEM_REACHED telemetry streamed back over the same MAVLink connection.
- **QGC EventBroadcaster -> External (port 45678)**: QGC broadcasts UI state changes
  (arm status, mode changes, alerts, mission progress) as JSON over UDP.
- **External -> QGC EventBroadcaster (port 45679)**: External tools send JSON action
  commands to trigger QGC UI operations (start mission, open views, change settings).
- **qgc_uxas_bridge.py <-> OpenUxAS**: Translates MAVLink waypoints/telemetry to LMCP
  messages over ZeroMQ for cooperative multi-vehicle autonomy services.

## Prerequisites

| Dependency       | Version  | Install                                       |
|------------------|----------|-----------------------------------------------|
| Python           | >= 3.8   | System package manager                        |
| pymavlink        | latest   | `pip install pymavlink`                       |
| pyzmq            | latest   | `pip install pyzmq`                           |
| PyYAML           | latest   | `pip install pyyaml`                          |
| PX4-Autopilot    | v1.14+   | Build for SITL (`make px4_sitl_default`)      |
| QGroundControl   | this repo| Build with EventBroadcaster (see main README) |
| OpenUxAS         | optional | Required only for `qgc_uxas_bridge.py`        |

Install all Python dependencies at once:

```bash
pip install pymavlink pyzmq pyyaml
```

PX4 SITL must be built and the `px4` binary available in `PX4-Autopilot/build/px4_sitl_default/bin/`.
QGroundControl must be compiled from this repository (the EventBroadcaster component is built in).

## Directory Structure

```
tools/test-automation/
├── scripts/
│   ├── test_orchestrator.py    # YAML scenario executor with pass/fail evaluation
│   ├── test_report.py          # HTML and console report generator
│   ├── qgc_event_monitor.py    # Real-time QGC UI event listener (UDP 45678)
│   ├── qgc_event_replay.py     # MAVLink command sequence player
│   ├── mavlink_recorder.py     # Full MAVLink message capture (CSV + binary)
│   ├── standard_mission.py     # Standard flight test (ARM -> Takeoff -> Square -> RTL -> Land)
│   ├── qgc_uxas_bridge.py      # QGC <-> OpenUxAS LMCP/ZeroMQ bridge
│   └── launch_all.sh           # Multi-vehicle SITL launcher (up to 10 vehicles)
├── configs/
│   ├── scenarios/
│   │   ├── TC001_basic_flight.yaml     # Basic arm/takeoff/land cycle
│   │   ├── TC002_mission_flight.yaml   # Waypoint mission upload and execution
│   │   └── TC003_failsafe_test.yaml    # Failsafe trigger and recovery validation
│   └── vehicles.json                   # Vehicle type and port definitions
└── README.md                           # This file
```

## Quick Start

**1. Start PX4 SITL (single vehicle):**

```bash
cd /path/to/PX4-Autopilot
make px4_sitl gazebo-classic
```

**2. Launch QGroundControl** (from repo root):

```bash
./build/QGroundControl
```

**3. Run the standard mission test:**

```bash
cd tools/test-automation
python3 scripts/standard_mission.py --instance 0 --altitude 15 --pattern-size 50
```

**4. Run a YAML scenario:**

```bash
python3 scripts/test_orchestrator.py \
    --scenario configs/scenarios/TC001_basic_flight.yaml \
    --instance 0 \
    --output results/
```

**5. Run all scenarios in a directory:**

```bash
python3 scripts/test_orchestrator.py \
    --scenario configs/scenarios/ \
    --instance 0 \
    --output results/
```

**6. Generate an HTML report from results:**

```bash
python3 scripts/test_report.py --input results/ --format html --output report.html
```

## Script Reference

### test_orchestrator.py

**Purpose:** Reads YAML test scenario files, executes each step against a PX4 SITL instance,
validates pass/fail conditions, and writes JSON result files.

**Usage:**

```bash
python3 scripts/test_orchestrator.py --scenario <path> --instance <N> [options]
```

**Options:**

| Flag              | Default       | Description                                      |
|-------------------|---------------|--------------------------------------------------|
| `--scenario`      | (required)    | Path to a `.yaml` file or directory of YAMLs     |
| `--instance`      | `0`           | PX4 SITL instance number (MAVLink port offset)   |
| `--host`          | `127.0.0.1`   | MAVLink / QGC host address                       |
| `--output`        | `results/`    | Directory for JSON result files                  |
| `--event-port`    | `45678`       | UDP port to listen for QGC events                |
| `--send-port`     | `45679`       | UDP port to send commands to QGC                 |

**Output:** One JSON file per scenario in the output directory, named
`<scenario_stem>_<YYYYMMDD_HHMMSS>.json`. Exit code 0 if all scenarios pass, 1 otherwise.

### standard_mission.py

**Purpose:** Executes a complete flight test profile on a single vehicle: preflight checks,
arm, takeoff, fly a square pattern, loiter, RTL, land, disarm. Designed for regression
testing across all vehicle types.

**Usage:**

```bash
python3 scripts/standard_mission.py --instance <N> [options]
```

**Options:**

| Flag              | Default  | Description                                          |
|-------------------|----------|------------------------------------------------------|
| `--instance`      | (required)| PX4 SITL instance (port = 14550 + instance)         |
| `--altitude`      | `15.0`   | Takeoff altitude in metres                           |
| `--pattern-size`  | `50.0`   | Square pattern side length in metres                 |
| `--record`        | `false`  | Enable MAVLink recording for replay                  |
| `--output`        | stdout   | Path to write JSON results (omit for stdout)         |

**Output:** JSON mission result containing per-phase pass/fail, timing, and overall status.
Emergency landing is attempted automatically if any phase fails.

**Phases:**

1. Preflight -- GPS fix (>= 3D), battery (>= 25%), sensor health
2. Arm -- send COMPONENT_ARM_DISARM, confirm via heartbeat
3. Takeoff -- climb to target altitude (+/- 3m tolerance)
4. Pattern -- upload 4-waypoint square mission, fly AUTO, wait for all waypoints reached
5. Loiter -- hold position for 30 seconds
6. RTL -- return to launch, confirm within 5m of home
7. Land -- wait for relative altitude < 0.5m
8. Disarm -- send disarm, confirm via heartbeat

### test_report.py

**Purpose:** Aggregates JSON result files and generates human-readable reports in HTML or
console text format.

**Usage:**

```bash
python3 scripts/test_report.py --input <results_dir> --format <html|text> --output <file>
```

**Options:**

| Flag        | Default    | Description                               |
|-------------|------------|-------------------------------------------|
| `--input`   | `results/` | Directory containing JSON result files    |
| `--format`  | `text`     | Output format: `html` or `text`           |
| `--output`  | stdout     | Output file path (omit for stdout)        |

### qgc_event_monitor.py

**Purpose:** Connects to QGC EventBroadcaster on UDP port 45678 and prints every event to
the console in real time. Useful for debugging and understanding what events QGC emits.

**Usage:**

```bash
python3 scripts/qgc_event_monitor.py [--port 45678] [--output events.jsonl]
```

### qgc_event_replay.py

**Purpose:** Reads a sequence of MAVLink commands from a file and replays them against a
PX4 SITL instance with configurable timing. Useful for reproducing specific flight sequences.

**Usage:**

```bash
python3 scripts/qgc_event_replay.py --input recording.jsonl --instance 0
```

### mavlink_recorder.py

**Purpose:** Captures all MAVLink messages on a given connection and writes them to CSV
(human-readable) and/or binary (replay-compatible) files.

**Usage:**

```bash
python3 scripts/mavlink_recorder.py --instance 0 --output capture.csv --binary capture.bin
```

### qgc_uxas_bridge.py

**Purpose:** Bidirectional bridge between QGC (MAVLink) and OpenUxAS (LMCP over ZeroMQ).
Translates MAVLink mission items and vehicle telemetry into LMCP service messages and vice
versa.

**Usage:**

```bash
python3 scripts/qgc_uxas_bridge.py \
    --mav-port 14540 \
    --zmq-pub tcp://127.0.0.1:5556 \
    --zmq-sub tcp://127.0.0.1:5557
```

### launch_all.sh

**Purpose:** Launches multiple PX4 SITL instances for multi-vehicle simulation. Starts up
to 10 vehicles with sequential port assignments.

**Usage:**

```bash
bash scripts/launch_all.sh [vehicle_count] [vehicle_type]
# Example:
bash scripts/launch_all.sh 4 iris
```

## EventBroadcaster Protocol

QGC includes an EventBroadcaster component that exposes internal UI state over UDP. Two
ports are used: one for outbound events (QGC notifies external listeners) and one for
inbound commands (external tools drive QGC).

### Outbound Events (QGC -> External, UDP 45678)

QGC sends JSON datagrams whenever significant state changes occur. Each datagram is a
single JSON object:

```json
{
    "category": "vehicle",
    "event": "armed",
    "timestamp": "2025-01-15T10:30:00Z",
    "data": {
        "vehicle_id": 1,
        "armed": true
    }
}
```

**Event categories and events:**

| Category    | Event                | Description                                    |
|-------------|----------------------|------------------------------------------------|
| `vehicle`   | `armed`              | Vehicle arm state changed                      |
| `vehicle`   | `disarmed`           | Vehicle disarmed                               |
| `vehicle`   | `mode_changed`       | Flight mode changed (data includes mode name)  |
| `vehicle`   | `connected`          | New vehicle connected                          |
| `vehicle`   | `disconnected`       | Vehicle connection lost                        |
| `mission`   | `uploaded`           | Mission upload to vehicle completed            |
| `mission`   | `started`            | Mission execution started                      |
| `mission`   | `item_reached`       | Waypoint reached (data includes seq number)    |
| `mission`   | `completed`          | Mission finished                               |
| `mission`   | `paused`             | Mission paused by user or failsafe             |
| `alert`     | `warning`            | Non-critical warning (low battery, GPS drift)  |
| `alert`     | `error`              | Critical error (sensor failure, geofence)      |
| `alert`     | `failsafe`           | Failsafe triggered (data includes type)        |
| `telemetry` | `position_update`    | Periodic position broadcast                    |
| `telemetry` | `battery_update`     | Battery state changed                          |
| `ui`        | `view_changed`       | Active QGC view changed (Fly, Plan, etc.)      |
| `ui`        | `dialog_opened`      | Modal dialog opened                            |
| `ui`        | `dialog_closed`      | Modal dialog dismissed                         |

### Inbound Commands (External -> QGC, UDP 45679)

External tools send JSON commands to drive QGC actions:

```json
{
    "action": "start_mission",
    "params": {
        "vehicle_id": 1
    }
}
```

**Supported actions:**

| Action              | Parameters                              | Description                              |
|---------------------|-----------------------------------------|------------------------------------------|
| `arm`               | `vehicle_id` (int)                      | Arm the vehicle                          |
| `disarm`            | `vehicle_id` (int)                      | Disarm the vehicle                       |
| `takeoff`           | `vehicle_id`, `altitude` (float, m)     | Command takeoff                          |
| `land`              | `vehicle_id`                            | Command landing                          |
| `rtl`               | `vehicle_id`                            | Return to launch                         |
| `start_mission`     | `vehicle_id`                            | Begin uploaded mission                   |
| `pause_mission`     | `vehicle_id`                            | Pause current mission                    |
| `resume_mission`    | `vehicle_id`                            | Resume paused mission                    |
| `set_mode`          | `vehicle_id`, `mode` (string)           | Change flight mode                       |
| `goto`              | `vehicle_id`, `lat`, `lon`, `alt`       | Fly to coordinate                        |
| `change_speed`      | `vehicle_id`, `speed` (float, m/s)      | Change target speed                      |
| `open_view`         | `view` (string: fly/plan/analyze)       | Switch QGC view                          |
| `load_mission`      | `file` (string, path)                   | Load mission file into Plan view         |
| `upload_mission`    | `vehicle_id`                            | Upload current plan to vehicle           |
| `trigger_action`    | `vehicle_id`, `action_name` (string)    | Trigger a custom Fly View action button  |

## Test Scenario YAML Format

Test scenarios are YAML files with a top-level `name` and a list of `steps`. Each step has
a `name`, an `action`, optional `params`, and optional control-flow fields.

### Full schema

```yaml
name: "Human-readable scenario name"

steps:
  - name: "Step display name"
    action: "<action_type>"       # Required. See action types below.
    params:                       # Action-specific parameters (dict).
      key: value
    abort_on_fail: false          # If true, stop the scenario on failure.
                                  # Default: false (continue to next step).
```

### Action types

#### send_command

Send a MAVLink command to PX4 and optionally wait for ACK.

```yaml
- name: "Arm vehicle"
  action: send_command
  params:
    command: arm                  # arm | disarm | takeoff | land | rtl | set_mode | change_speed
    expect_ack: true              # Wait for COMMAND_ACK (default: true)
    # Command-specific params:
    altitude: 10                  # For takeoff
    mode: "auto_mission"          # For set_mode (see PX4_MODES)
    speed: 5                      # For change_speed (m/s)
    speed_type: 1                 # For change_speed (0=airspeed, 1=groundspeed)
    force: false                  # For disarm (force disarm even in-flight)
```

**Available `command` values:** `arm`, `disarm`, `takeoff`, `land`, `rtl`, `set_mode`,
`change_speed`.

**PX4 mode names for `set_mode`:** `manual`, `posctl`, `auto_mission`, `auto_loiter`,
`auto_rtl`, `offboard`, `auto_land`, `auto_takeoff`.

#### send_ui_command

Send a JSON command to QGC via EventBroadcaster (UDP 45679).

```yaml
- name: "Start mission via QGC"
  action: send_ui_command
  params:
    ui_action: "start_mission"    # Action string (see Inbound Commands table)
    ui_params:                    # Optional parameters for the action
      vehicle_id: 1
```

#### wait_event

Wait for a specific QGC EventBroadcaster event (UDP 45678).

```yaml
- name: "Wait for mission complete"
  action: wait_event
  params:
    match:                        # All key-value pairs must match the event
      category: "mission"
      event: "completed"
    timeout: 120                  # Seconds to wait (default: 30)
```

#### wait_state

Poll MAVLink telemetry until a vehicle state condition is met.

```yaml
- name: "Wait until armed"
  action: wait_state
  params:
    check: armed                  # armed | mode | on_ground
    value: true                   # Expected value
    timeout: 30                   # Seconds (default: 30)
    poll_interval: 0.5            # Seconds between polls (default: 0.5)
```

**Check types:**

- `armed` -- value is `true` or `false`
- `mode` -- value is PX4 custom mode integer
- `on_ground` -- value is `true` (relative_alt < 0.5m) or `false` (airborne)

#### check_altitude

Poll GLOBAL_POSITION_INT until altitude is within tolerance of target.

```yaml
- name: "Verify at 10m"
  action: check_altitude
  params:
    target: 10                    # Target altitude in metres (relative)
    tolerance: 2                  # Acceptable deviation in metres (default: 2)
    timeout: 30                   # Seconds (default: 30)
    poll_interval: 0.5            # Seconds (default: 0.5)
```

#### check_position

Poll GLOBAL_POSITION_INT until the vehicle is within a radius of a target coordinate.

```yaml
- name: "Verify at waypoint 1"
  action: check_position
  params:
    lat: 47.397742                # Target latitude (WGS-84, decimal degrees)
    lon: 8.545594                 # Target longitude
    radius: 5                    # Acceptance radius in metres (default: 5)
    timeout: 30                   # Seconds (default: 30)
    poll_interval: 0.5            # Seconds (default: 0.5)
```

#### upload_mission

Upload mission items to the vehicle via the MAVLink mission protocol.

```yaml
- name: "Upload waypoint mission"
  action: upload_mission
  params:
    file: "configs/missions/square.json"   # Load items from file (JSON or YAML)
    # OR inline items:
    items:
      - lat: 47.397742
        lon: 8.545594
        alt: 10
        command: 16               # MAV_CMD_NAV_WAYPOINT (default: 16)
        frame: 6                  # MAV_FRAME_GLOBAL_RELATIVE_ALT_INT (default: 6)
        autocontinue: 1
```

#### sleep

Pause execution for a fixed duration.

```yaml
- name: "Wait for stabilization"
  action: sleep
  params:
    duration: 5                   # Seconds
```

### Complete scenario example

```yaml
name: "TC001 Basic Flight"

steps:
  - name: "Arm vehicle"
    action: send_command
    params:
      command: arm
    abort_on_fail: true

  - name: "Verify armed"
    action: wait_state
    params:
      check: armed
      value: true
      timeout: 10

  - name: "Takeoff to 10m"
    action: send_command
    params:
      command: takeoff
      altitude: 10
    abort_on_fail: true

  - name: "Check altitude reached"
    action: check_altitude
    params:
      target: 10
      tolerance: 2
      timeout: 30

  - name: "Hover for 5 seconds"
    action: sleep
    params:
      duration: 5

  - name: "RTL"
    action: send_command
    params:
      command: rtl

  - name: "Wait for landing"
    action: wait_state
    params:
      check: on_ground
      value: true
      timeout: 120

  - name: "Disarm"
    action: send_command
    params:
      command: disarm
```

## QGC <-> OpenUxAS Integration

The `qgc_uxas_bridge.py` script provides a bidirectional translation layer between QGC
(MAVLink) and OpenUxAS (LMCP over ZeroMQ). This enables cooperative autonomy services
running in OpenUxAS to control vehicles managed by QGC.

### How the bridge works

1. The bridge connects to PX4 via pymavlink (`udpin`) and to OpenUxAS via two ZeroMQ
   sockets: a SUB socket for receiving LMCP messages and a PUB socket for publishing them.
2. Incoming MAVLink telemetry (position, heartbeat) is translated to LMCP `AirVehicleState`
   messages and published to OpenUxAS.
3. Incoming LMCP `MissionCommand` and `VehicleActionCommand` messages from OpenUxAS are
   translated to MAVLink `COMMAND_LONG` or mission upload sequences and sent to PX4.
4. The bridge maintains a vehicle ID mapping table to correlate MAVLink system IDs with
   OpenUxAS entity IDs.

### Message mapping table

| Direction         | MAVLink Message / Command        | LMCP Message                    |
|-------------------|----------------------------------|---------------------------------|
| MAV -> LMCP       | `GLOBAL_POSITION_INT`            | `AirVehicleState`               |
| MAV -> LMCP       | `HEARTBEAT`                      | `EntityState`                   |
| MAV -> LMCP       | `MISSION_ITEM_REACHED`           | `MissionStatus`                 |
| MAV -> LMCP       | `BATTERY_STATUS`                 | `EntityState.EnergyAvailable`   |
| LMCP -> MAV       | `MissionCommand`                 | Mission upload (waypoints)      |
| LMCP -> MAV       | `VehicleActionCommand (Loiter)`  | `MAV_CMD_NAV_LOITER_UNLIM`      |
| LMCP -> MAV       | `VehicleActionCommand (RTL)`     | `MAV_CMD_NAV_RETURN_TO_LAUNCH`  |
| LMCP -> MAV       | `VehicleActionCommand (Land)`    | `MAV_CMD_NAV_LAND`              |
| LMCP -> MAV       | `AutomationRequest`              | Mission upload + AUTO mode      |

### Running with OpenUxAS

```bash
# Terminal 1: Start PX4 SITL
cd PX4-Autopilot && make px4_sitl gazebo-classic

# Terminal 2: Start OpenUxAS
cd OpenUxAS && ./build/uxas -cfgPath configs/example.xml

# Terminal 3: Start bridge
python3 scripts/qgc_uxas_bridge.py \
    --mav-port 14540 \
    --zmq-pub tcp://127.0.0.1:5556 \
    --zmq-sub tcp://127.0.0.1:5557

# Terminal 4: Start QGC
./build/QGroundControl
```

## Multi-Vehicle Simulation

The `launch_all.sh` script starts multiple PX4 SITL instances, each on a unique set of
ports. QGC auto-detects all vehicles via MAVLink heartbeats.

### Port assignments

Each vehicle instance N is assigned the following ports:

| Port              | Formula       | Instance 0 | Instance 1 | Instance 9 |
|-------------------|---------------|------------|------------|------------|
| MAVLink (SITL)    | 14540 + N     | 14540      | 14541      | 14549      |
| MAVLink (GCS)     | 14550 + N     | 14550      | 14551      | 14559      |
| Simulator         | 14560 + N     | 14560      | 14561      | 14569      |

### Vehicle types

The `vehicles.json` configuration file maps instance numbers to vehicle types and spawn
positions:

```json
[
    {"instance": 0, "type": "iris",      "x": 0,   "y": 0,   "z": 0},
    {"instance": 1, "type": "iris",      "x": 2,   "y": 0,   "z": 0},
    {"instance": 2, "type": "typhoon",   "x": 4,   "y": 0,   "z": 0},
    {"instance": 3, "type": "plane",     "x": 0,   "y": 10,  "z": 0},
    {"instance": 4, "type": "rover",     "x": 0,   "y": 20,  "z": 0}
]
```

### Usage

```bash
# Launch 4 iris quadcopters
bash scripts/launch_all.sh 4 iris

# Launch vehicles from vehicles.json config
bash scripts/launch_all.sh --config configs/vehicles.json

# Run the standard mission on vehicle instance 2
python3 scripts/standard_mission.py --instance 2 --altitude 20

# Run a test scenario targeting vehicle instance 0
python3 scripts/test_orchestrator.py --scenario configs/scenarios/ --instance 0
```

All SITL processes are launched in the background. The script prints PIDs and writes them
to `/tmp/px4_sitl_pids.txt` for cleanup. To stop all instances:

```bash
bash scripts/launch_all.sh --stop
```

## Custom MAVLink Actions (QGC UI Buttons)

QGC Fly View supports custom action buttons defined in a JSON file. These buttons appear
in the Actions menu and send MAVLink COMMAND_LONG messages when pressed. This is useful
for OpenUxAS integration, where custom commands trigger autonomy services.

### Creating uxas_actions.json

Place a `uxas_actions.json` file in the QGC application settings directory or pass it via
a command-line option. Format:

```json
{
    "version": 1,
    "actions": [
        {
            "label": "Start Search",
            "description": "Begin cooperative area search via OpenUxAS",
            "command": 31010,
            "params": {
                "param1": 1.0,
                "param2": 0.0,
                "param3": 0.0,
                "param4": 0.0,
                "param5": 0.0,
                "param6": 0.0,
                "param7": 0.0
            }
        },
        {
            "label": "Cancel Task",
            "description": "Cancel current OpenUxAS task assignment",
            "command": 31011,
            "params": {
                "param1": 0.0
            }
        },
        {
            "label": "Request Replan",
            "description": "Request OpenUxAS to recompute assignment",
            "command": 31012,
            "params": {
                "param1": 1.0,
                "param2": 0.0
            }
        }
    ]
}
```

Each action entry supports:

| Field         | Type   | Description                                           |
|---------------|--------|-------------------------------------------------------|
| `label`       | string | Button text shown in Fly View Actions menu            |
| `description` | string | Tooltip text for the button                           |
| `command`     | int    | MAVLink COMMAND_LONG command ID                       |
| `params`      | object | `param1` through `param7` float values (default 0.0)  |

When a button is pressed, QGC sends a `COMMAND_LONG` with the specified command ID and
parameters to the active vehicle. The `qgc_uxas_bridge.py` intercepts these custom
command IDs and translates them into the appropriate LMCP service requests for OpenUxAS.

### Registering with QGC

Copy the file to the QGC config directory:

```bash
cp uxas_actions.json ~/.config/QGroundControl.org/
```

Or specify at launch:

```bash
./QGroundControl --custom-actions /path/to/uxas_actions.json
```
