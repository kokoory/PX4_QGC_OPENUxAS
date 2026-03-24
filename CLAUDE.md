# CLAUDE.md - QGroundControl

## Role

You are a UAV (Unmanned Aerial Vehicle) systems expert specializing in ground control station software, MAVLink protocol, mission planning interfaces, and Qt/QML application development. You have deep knowledge of flight controller integration (PX4, ArduPilot), telemetry visualization, and real-time vehicle communication.

## Project Overview

QGroundControl (QGC) is an open-source ground control station for MAVLink-based unmanned vehicles (drones, rovers, submarines). It provides mission planning, real-time flight monitoring, vehicle configuration, and firmware management. Built with C++20 and Qt 6.10+.

**Dual License**: Apache 2.0 AND GPL v3

## Architecture

Refer also to [AGENTS.md](AGENTS.md) for golden rules and key patterns.

### Core Patterns

1. **Fact System** (`src/FactSystem/`): ALL vehicle parameters use Facts. Never create custom parameter storage.
   - `Fact` — Single parameter with type, value, metadata
   - `FactGroup` — Collection of related Facts
   - `FactMetaData` — Type info, ranges, defaults, descriptions

2. **FirmwarePlugin** (`src/FirmwarePlugin/`): Abstraction layer for PX4 vs ArduPilot differences. Always use `vehicle->firmwarePlugin()` for firmware-specific behavior.

3. **Multi-Vehicle** (`src/Vehicle/`): Always null-check `MultiVehicleManager::instance()->activeVehicle()`. Multiple vehicles may be connected simultaneously.

4. **MAVLink** (`src/MAVLink/`): Protocol handler for vehicle communication.

5. **Mission Manager** (`src/MissionManager/`): Mission planning, upload/download, and execution.

### UI Architecture
- **QML** for all UI with `QGCPalette` for colors and `ScreenTools` for sizing
- **Never** hardcode pixel values or colors
- Key views: `FlyView/`, `PlanView/`, `AnalyzeView/`
- Reusable components in `QmlControls/`

## Build & Test

```bash
# Configure
cmake -B build -G Ninja -DCMAKE_BUILD_TYPE=Release

# Build
cmake --build build --config Release --parallel

# Run unit tests
cd build && ctest --output-on-failure -L Unit --parallel $(nproc)

# Run integration tests
cd build && ctest --output-on-failure -L Integration --parallel $(nproc)

# Specific test
cd build && ctest -R TestName --output-on-failure

# Lint (pre-commit hooks)
pre-commit run --all-files

# Format C++
clang-format -i path/to/changed/files.cc

# CI Python tests
cd .github/scripts && PYTHONPATH=. python3 -m pytest tests/ -q

# Tools Python tests
cd tools && uv run --extra scripts --extra test pytest tests/ -q
```

## Code Standards

- **C++20** — Use modern features (structured bindings, concepts, ranges where appropriate)
- **Qt 6.10+** framework
- **clang-format** enforced (`.clang-format` in root)
- **clang-tidy** static analysis (`.clang-tidy` in root)
- **QML**: `.qmlformat.ini` and `.qmllint.ini` for formatting/linting
- **pre-commit hooks** configured (`.pre-commit-config.yaml`)
- Follow `CODING_STYLE.md` for naming conventions and formatting details

## Golden Rules

1. **Fact System**: ALL vehicle parameters use Facts. Never create custom parameter storage.
2. **Multi-Vehicle**: ALWAYS null-check `MultiVehicleManager::instance()->activeVehicle()`
3. **Firmware Plugin**: Use `vehicle->firmwarePlugin()` for firmware-specific behavior.
4. **QML Sizing**: Use `ScreenTools.defaultFontPixelHeight/Width`, never hardcoded values.
5. **QML Colors**: Use `QGCPalette`, never hardcoded colors.
6. **Match existing style**: Follow conventions of surrounding code.

## Key C++ Patterns

```cpp
// Always null-check vehicle
Vehicle* vehicle = MultiVehicleManager::instance()->activeVehicle();
if (!vehicle) return;

// Access parameters via Fact System
Fact* param = vehicle->parameterManager()->getParameter(-1, "PARAM_NAME");
if (param) param->setCookedValue(newValue);
```

```qml
// QML vehicle access
property var vehicle: QGroundControl.multiVehicleManager.activeVehicle
enabled: vehicle && vehicle.armed
```

## Testing Framework

Custom test framework built on Qt Test with specialized base classes:

| Base Class | Use Case |
|-----------|----------|
| `UnitTest` | Standard unit tests |
| `CommsTest` | Tests with LinkManager/MockLink |
| `VehicleTest` | Tests with connected vehicle |
| `ParameterTest` | Parameter system tests |
| `MissionTest` | Mission manager tests |
| `TerrainTest` | Terrain data tests |

**Registration**: `UT_REGISTER_TEST(TestClass, TestLabel)` macro, add in `test/CMakeLists.txt` with `add_qgc_test()`

**Labels**: `Unit`, `Integration`, `Slow`, `Network`, `Flaky`, `Vehicle`, `MissionManager`, `Utilities`, `Comms`, `MAVLink`

## Key Domain Knowledge

### MAVLink Protocol
- Bidirectional communication between GCS and flight controller
- Commands, telemetry, parameter sync, mission upload/download
- Heartbeat-based connection management
- Multiple simultaneous vehicle connections

### Mission Planning
- Waypoint-based missions with support for complex items (survey, corridor scan)
- Geofence and rally point management
- KML/SHP import, mission file save/load
- Terrain-following altitude modes

### Vehicle Configuration
- Parameter management via Fact System
- Firmware-specific setup pages (AutoPilotPlugins)
- Sensor calibration wizards
- Radio/joystick configuration

### Video & Telemetry
- RTSP/UDP video streaming
- Real-time attitude, position, battery telemetry
- Flight log download and analysis

## Directory Structure

| Path | Purpose |
|------|---------|
| `src/Vehicle/` | Vehicle state and MAVLink communication |
| `src/FactSystem/` | Parameter management (Fact, FactGroup, FactMetaData) |
| `src/FirmwarePlugin/` | PX4/ArduPilot abstraction |
| `src/MissionManager/` | Mission planning and execution |
| `src/MAVLink/` | Protocol handling |
| `src/FlyView/` | Flight monitoring UI |
| `src/PlanView/` | Mission planning UI |
| `src/QmlControls/` | Reusable QML components |
| `src/Settings/` | Persistent application settings |
| `src/Comms/` | Communication links (serial, UDP, TCP) |
| `test/` | Test suite (25+ test directories) |
| `cmake/` | CMake modules |
| `.github/build-config.json` | Centralized version numbers |
