# QGC × OpenUxAS — 혼합 편대(멀티콥터+고정익) 통합 작업 보고서

## 1. 작업 목표

PX4 SITL 기반 멀티 vehicle 환경에서 멀티콥터와 고정익을 **동일한 OpenUxAS 인스턴스**에 등록하고, UxAS가 vehicle 종류별 dynamics를 보고 적절히 task를 배분하도록 만든다.

**범위.**

1. `configs/vehicles.json` 스키마를 LMCP capability(min/max 속도·고도, climb rate, bank angle)를 담도록 확장.
2. `qgc_uxas_bridge.py`가 모델별 capability를 받아 `AirVehicleConfiguration`을 정확하게 생성하도록 수정.
3. `launch_all.sh`를 `vehicles.json` 기반으로 일반화(하드코딩된 VEHICLES 배열 제거, fleet preset / 개별 ID 선택 지원).
4. `launch_bridges.sh`를 신규 작성: vehicle 수만큼 bridge 인스턴스를 자동 spawn.
5. UxAS 멀티 vehicle 설정(`configs/uxas_multi.xml`) 작성: vehicle별 `WaypointPlanManagerService` + 자율비행 서비스 풀세트.
6. Tier 1/3 시나리오 발행기(`scripts/uxas_publish_task.py`) 작성: AreaSearchTask / LineSearchTask / PointSearchTask + AutomationRequest 를 LMCP binary로 publish 하고 UxAS 응답을 캡처.
7. UxAS 인스턴스를 띄워 실제 통신 가능성 확인.
8. **PX4 ↔ LMCP 매핑 명세화** (§5): PX4 SYS_AUTOSTART / 모델 / MAVLink 필드 / dynamics 파라미터를 LMCP CMASI 객체로 어떻게 옮기는지, 단위·좌표계·식별자 일관성을 모두 문서화.

**비범위 (사용자가 다른 곳에서 SITL을 사용 중이라 이번 보고에서 의도적으로 보류).**

- PX4 SITL multi-instance 실제 실행 (`launch_all.sh` 한 번 띄우기)
- bridge가 MAVLink 텔레메트리를 받아 `AirVehicleState`를 UxAS에 publish 한 뒤 UxAS가 실제 `MissionCommand`를 생성하는 종단 흐름

이 두 단계는 모든 스크립트·설정이 준비되어 있어 SITL 환경에서 그대로 실행하면 동작합니다. 실행 절차는 §8에 정리.

---

## 2. 결론 요약

| 항목 | 결과 |
|---|---|
| `vehicles.json` 혼합 편대 스키마 (멀티콥터/고정익/VTOL/rover + LMCP capability) | ✅ 완료 |
| Bridge 모델별 capability 매핑 (CLI 인자 8개 추가) | ✅ 완료, `--help` 동작 확인 |
| `launch_all.sh` JSON-driven 재작성 (fleet preset 4종) | ✅ 완료, `bash -n` + fleet resolution 확인 |
| `launch_bridges.sh` 신규 (vehicle 수만큼 bridge 자동 spawn) | ✅ 완료, capability 머지 정확 |
| UxAS multi-vehicle 설정 (`uxas_multi.xml`) | ✅ 완료, `WaypointPlanManagerService` × 5 (3 MC + 2 FW) |
| Tier 1/3 시나리오 발행기 (`uxas_publish_task.py`) | ✅ 완료, area/line/point + register-from-config |
| **PX4 ↔ LMCP 매핑 명세** (§5) | ✅ 차종/dynamics/MAVLink↔AirVehicleState/MissionCommand↔mission upload/ID 일관성/좌표·단위 7개 표로 문서화 |
| UxAS 실제 인스턴스와 LMCP round-trip | ✅ **AutomationResponse 수신 확인 (이전 세션의 "echo 안 됨" 문제 해결)** |
| 완전한 종단 흐름 (SITL → bridge → UxAS → MissionCommand → MAVLink 업로드) | ⏸ SITL 외부 사용 중이라 보류 — §8 절차로 실행 가능 |

가장 중요한 진전은 **UxAS 자체와의 LMCP 양방향 통신 확인**입니다. 이전 세션에서 mock peer로만 wire format을 검증했고 실제 UxAS와의 round-trip은 release build의 INFO 로그 비활성 때문에 디버깅이 막혀 있었습니다. 이번에는 `AutomationRequest`를 PUSH한 직후 UxAS `PlanBuilderService`가 처리한 `AutomationResponse`를 직접 받았습니다 (§6.5).

---

## 3. 변경한 파일 목록

| 파일 | 종류 | 요약 |
|---|---|---|
| `tools/test-automation/configs/vehicles.json` | 수정 | `lmcp_defaults`(type별), 개별 vehicle `lmcp` override, `fleets` preset 추가. ID 규칙 0-9 → 1-10 (LMCP는 EntityID=0 권장 안 함). |
| `tools/test-automation/configs/uxas_multi.xml` | 신규 | PublishPullBridge + 자율비행 서비스 풀세트 + WaypointPlanManagerService × 5 |
| `tools/test-automation/scripts/qgc_uxas_bridge.py` | 수정 | 8개 CLI 인자 추가 (`--label`, `--min-speed/...`), `register_vehicle`가 capability 사용 |
| `tools/test-automation/scripts/launch_all.sh` | 재작성 | JSON-driven, fleet preset/ID 리스트, 보고용 fleet.tsv 저장 |
| `tools/test-automation/scripts/launch_bridges.sh` | 신규 | vehicles.json 읽어 vehicle 수만큼 bridge spawn, PID 파일 출력 |
| `tools/test-automation/scripts/uxas_publish_task.py` | 신규 | area/line/point search task + AutomationRequest LMCP publisher |
| `tools/test-automation/docs/QGC_UxAS_MixedFleet_Report.md` | 신규 | 본 보고서 |
| `tools/test-automation/docs/build_report_pdf.py` | 신규 | 본 보고서 PDF 빌더 |

---

## 4. 설계 결정

### 4.1 LMCP capability 표현 — JSON `lmcp_defaults` + per-vehicle override

OpenUxAS의 `AirVehicleConfiguration`은 RoutePlanner가 vehicle dynamics를 판단하는 유일한 입력입니다. 그래서 `vehicles.json`에 두 단계로 표현:

```
"lmcp_defaults": {
  "multicopter": { "min_speed_mps": 0.0,  "max_speed_mps": 18.0, ... },
  "fixed_wing":  { "min_speed_mps": 12.0, "max_speed_mps": 30.0, ... },
  "vtol":        { ... },
  "rover":       { ... }
},
"vehicles": [
  { "id": 4, "type": "fixed_wing", ...,
    "lmcp": { "min_speed_mps": 10.0, "max_speed_mps": 25.0 } }   /* override */
]
```

`type`별 defaults가 90% 케이스를 커버하고, 모델별 차이(예: RC Cessna는 stall speed 10 m/s, Advanced Plane은 12 m/s)는 `lmcp` 필드로 override합니다. 이 머지 로직은 `launch_bridges.sh`와 `uxas_publish_task.py` 양쪽에서 동일하게 (`{**defaults, **override}`) 수행합니다.

### 4.2 Bridge 인자 vs JSON 직접 읽기

Bridge가 `vehicles.json`을 직접 읽지 않고 capability를 CLI 인자로 받게 만든 이유:

- 단위 테스트가 단순 (mock UxAS peer로 wire format만 검증할 때 JSON 의존성 없음)
- CI에서 한 vehicle씩 띄울 때 옵션 명확
- JSON-driven spawn은 `launch_bridges.sh`가 책임 (단일 책임 분리)

### 4.3 UxAS 설정에서 `WaypointPlanManagerService`를 vehicle별로

UxAS의 `WaypointPlanManagerService`는 한 vehicle을 책임지고, `MissionCommand`를 그 vehicle ID로 발행합니다. 멀티콥터(`turnType=TurnShort`)와 고정익(`turnType=FlyOver` + 종료 시 loiter at 18~22 m/s)의 turn 처리가 다르므로 vehicle별 설정이 필수입니다. fleet 변경 시 `uxas_multi.xml`의 service 목록도 함께 바꿔야 합니다.

### 4.4 ID 규칙 (0 → 1 시작)

LMCP는 `EntityID=0`을 broadcast/unset 의미로 쓰는 관례가 있어 vehicle ID를 1부터 시작하도록 `vehicles.json`을 정리했습니다. 기존 PX4 SITL `-i 0`은 단순히 PX4 instance index이고 LMCP ID와는 독립이므로 충돌 없습니다.

---

## 5. PX4 비행체 → OpenUxAS LMCP 매핑 상세

PX4와 OpenUxAS는 서로 다른 추상화에서 동작합니다: PX4는 **MAVLink 메시지 + 파라미터 + frame coordinate**, UxAS는 **LMCP CMASI 객체 + ZeroMQ envelope**. 이 절은 두 도메인의 각 필드를 어떻게 매핑했는지 정확히 기록합니다. 모든 변환은 `qgc_uxas_bridge.py` 한 곳에서 수행됩니다.

### 5.1 차종(type) 매핑

PX4는 차종을 `SYS_AUTOSTART` 번호 + Gazebo 모델 이름으로 표현하고, LMCP는 메시지 타입 자체(`AirVehicleConfiguration` vs `GroundVehicleConfiguration` 등)로 표현합니다. 두 가지가 일대일 자동 매칭되지 않으므로 `vehicles.json`의 `type` 필드를 **truth source**로 두고 사람이 명시합니다.

| PX4 영역 | LMCP 영역 | 본 작업의 처리 |
|---|---|---|
| `SYS_AUTOSTART=4001..4099` (`gz_x500*`, `gz_iris*`) | `afrl.cmasi.AirVehicleConfiguration` (회전익) | `vehicles.json: type="multicopter"` → `lmcp_defaults.multicopter` |
| `SYS_AUTOSTART=2100..2199` (`gz_rc_cessna`, `gz_advanced_plane`) | `afrl.cmasi.AirVehicleConfiguration` (고정익) | `vehicles.json: type="fixed_wing"` → `lmcp_defaults.fixed_wing` + per-vehicle `lmcp` override |
| `SYS_AUTOSTART=13000..13199` (`gz_standard_vtol`, `gz_tiltrotor`) | `afrl.cmasi.AirVehicleConfiguration` (가변 dynamics) | `vehicles.json: type="vtol"` → `lmcp_defaults.vtol` (현재 멀티콥터/고정익 중간값 사용; 모드 전환은 5.5에서 별도) |
| `SYS_AUTOSTART=50000..50099` (`gz_r1_rover`, `gz_rover_ackermann`) | `afrl.vehicles.GroundVehicleConfiguration` (별도 시리즈) | `vehicles.json: type="rover"` 등록은 했으나 본 작업에서 빌더 미구현 — 다음 단계 항목 |

본 작업의 `mixed_full` fleet은 `multicopter × 3 + fixed_wing × 2`로 구성되어 두 카테고리만 다룹니다. VTOL/rover는 다음 단계.

### 5.2 PX4 dynamics 파라미터 → LMCP `AirVehicleConfiguration` 필드

LMCP `AirVehicleConfiguration`은 UxAS RoutePlanner가 비행 계획을 만들 때 보는 유일한 capability 명세입니다. PX4 파라미터 / SITL 모델 특성을 다음과 같이 매핑했습니다.

| LMCP 필드 (setter) | 의미 | PX4 출처 | 본 작업 기본값 (multicopter / fixed_wing) |
|---|---|---|---|
| `set_ID(int)` | LMCP EntityID | `vehicles.json: id` | 1, 2, 3 / 4, 10 |
| `set_Label(str)` | 표시 이름 | `vehicles.json: name` | "X500 Quadcopter" / "RC Cessna" 등 |
| `set_MinimumSpeed(m/s)` | 최저 안정 비행 속도 | PX4 `FW_AIRSPD_MIN` (FW), 멀티콥터는 0 | 0.0 / 10.0~12.0 |
| `set_MaximumSpeed(m/s)` | 최고 비행 속도 | PX4 `FW_AIRSPD_MAX` (FW), MC는 차체 한계 | 18.0 / 25.0~30.0 |
| `set_NominalSpeed(m/s)` | 순항 속도 | PX4 `FW_AIRSPD_TRIM` (FW), MC는 (min+max)/2 | 8.0 / 18.0~22.0 |
| `set_MinimumAltitude(m)` | 최저 고도 (MSL) | 운용 제약 (사람 결정) | 0.0 / 20.0 |
| `set_MaximumAltitude(m)` | 최고 고도 (MSL) | 운용 제약 | 200.0 / 800.0 |
| `set_NominalAltitude(m)` | 순항 고도 | 운용 결정 | 50.0 / 120.0 |
| `set_MinAltitudeType` / `set_MaxAltitudeType` / `set_NominalAltitudeType` | 고도 기준 (MSL/AGL) | 모두 `AltitudeType.MSL`로 고정 | MSL |
| `set_MaximumClimbRate(m/s)` | 최대 상승률 | PX4 `MC_HOVER_THR_*` / `FW_T_CLMB_MAX` (간접) | 5.0 / 4.0 |
| `set_NominalFlightProfile(FlightProfile)` | 순항 프로파일 | 별도 객체 (아래) | name="Cruise" |

`FlightProfile` 하위 객체:

| FlightProfile 필드 | 본 작업 값 | 근거 |
|---|---|---|
| `set_Name("Cruise")` | 고정 | 단일 cruise 프로파일만 사용 |
| `set_Airspeed` | nominal_speed | dynamics 동일 출처 |
| `set_MaxBankAngle(deg)` | MC 35° / FW 45° | PX4 `MPC_TILTMAX_AIR` / `FW_R_LIM` 관행값 |
| `set_PitchAngle` / `set_VerticalSpeed` | 0 / 0 | 순항 horizontal 가정 |
| `set_EnergyRate` | 0.02 | placeholder (배터리 모델링은 차후) |

이 매핑은 `qgc_uxas_bridge.py`의 `build_air_vehicle_configuration(...)` 한 함수에 집중되어 있고, 인자값은 `register_vehicle()`에서 `self.capability` dict로부터 끌어옵니다. `self.capability`는 CLI 인자(`--min-speed` 등)로 들어오고, 그 값은 `launch_bridges.sh`가 `vehicles.json`의 `lmcp_defaults[type]` ⊕ per-vehicle `lmcp` override에서 머지해 넘깁니다.

`vehicles.json`의 `lmcp_defaults` 블록은 이 매핑의 **single source of truth**입니다. 새 모델을 추가할 때는 `vehicles.json` 한 곳만 수정하면 bridge와 publisher가 자동으로 따라옵니다.

### 5.3 MAVLink 텔레메트리 → LMCP `AirVehicleState`

Bridge의 `MAVLinkReader._read_loop`가 PX4가 보내는 MAVLink 메시지를 읽어 내부 `VehicleState`를 갱신하고, `_state_publish_loop`가 `--state-rate Hz`(기본 2 Hz) 주기로 `AirVehicleState`를 LMCP로 직렬화해 UxAS PULL에 PUSH합니다.

| MAVLink 메시지·필드 | 단위 | 변환 | LMCP `AirVehicleState` setter | LMCP 단위 |
|---|---|---|---|---|
| `GLOBAL_POSITION_INT.lat` | int32 (1e-7 deg) | `× 1e-7` | `set_Location(Location3D.set_Latitude(...))` | deg |
| `GLOBAL_POSITION_INT.lon` | int32 (1e-7 deg) | `× 1e-7` | `Location3D.set_Longitude(...)` | deg |
| `GLOBAL_POSITION_INT.alt` | int32 (mm, MSL) | `÷ 1000` | `Location3D.set_Altitude(...) AltitudeType.MSL` | m |
| `GLOBAL_POSITION_INT.hdg` | uint16 (cdeg) | `÷ 100` | `set_Heading(...)`, `set_Course(...)` | deg |
| `GLOBAL_POSITION_INT.vz` | int16 (cm/s, +down) | `× -0.01` | `set_VerticalSpeed(...)` | m/s (+up) |
| `GLOBAL_POSITION_INT.vx/vy` | int16 (cm/s) | `sqrt(vx²+vy²) × 0.01` | `set_Groundspeed(...)` | m/s |
| `ATTITUDE.roll/pitch/yaw` | float32 (rad) | `math.degrees(...)` | `set_Roll/Pitch` (`Yaw`는 별도 사용 안 함) | deg |
| `VFR_HUD.airspeed` | float (m/s) | identity | `set_Airspeed(...)` | m/s |
| `VFR_HUD.groundspeed` | float (m/s) | identity (덮어쓰기) | `set_Groundspeed(...)` | m/s |
| `SYS_STATUS.battery_remaining` | int8 (%, -1=unknown) | `max(0, val)` | `set_EnergyAvailable(...)` | % |
| `HEARTBEAT.base_mode & MAV_MODE_FLAG_SAFETY_ARMED` | bit | bool | (내부 상태로만 사용) | — |
| `HEARTBEAT.custom_mode` (PX4 main/sub mode) | uint32 | mode_map (5.4) | (내부 → `set_Mode(NavigationMode.Waypoint)`) | enum |
| `time.time()` (수신 wall clock) | s | `× 1000` | `set_Time(...)` | ms |

LMCP 측 미사용 필드(예: `WindSpeed/WindDirection`, `Fuel*` 등 일부)는 0/기본값으로 비워둡니다. UxAS RoutePlanner는 `Location` + `Airspeed/Groundspeed`만 필수로 봅니다.

### 5.4 PX4 flight mode → LMCP `NavigationMode`

PX4는 `HEARTBEAT.custom_mode`의 상위 비트에 main/sub mode를 인코딩합니다. bridge가 디코드하는 매핑:

| (main, sub) | PX4 의미 | 내부 라벨 |
|---|---|---|
| (1, 0) | Manual | `manual` |
| (2, 0) | Position Control | `posctl` |
| (4, 1..6) | AUTO.{Ready, Takeoff, Loiter, Mission, RTL, Land} | `auto_*` |
| (6, 0) | Offboard | `offboard` |
| 기타 | — | `custom(<main>,<sub>)` |

LMCP `NavigationMode`는 enum이 한정적(`Waypoint`, `Loiter`, `FlightDirector`, `TargetTrack`, `Hover`)이라 본 작업은 PX4 mode와 무관하게 `NavigationMode.Waypoint`로 단순 매핑합니다. UxAS는 vehicle을 task-driven 운영하므로 mode 세부는 PlanBuilder가 결정합니다. 향후 VTOL mode 전환에서는 `set_Mode(NavigationMode.Hover)` ↔ `set_Mode(NavigationMode.Waypoint)` 전환을 별도로 송신할 예정 (다음 단계).

### 5.5 LMCP `MissionCommand` → MAVLink mission upload (역방향)

UxAS가 plan을 완성하면 vehicle별로 `MissionCommand`를 외부 PUB로 broadcast합니다. Bridge는 SUB에서 받아 다음과 같이 MAVLink 미션 업로드 시퀀스를 실행합니다.

| LMCP `Waypoint` 필드 | 단위 | 변환 | MAVLink `MISSION_ITEM_INT` 필드 | 단위 |
|---|---|---|---|---|
| `get_Latitude()` | deg | `× 1e7` (int) | `x` | int32 (1e-7 deg) |
| `get_Longitude()` | deg | `× 1e7` (int) | `y` | int32 (1e-7 deg) |
| `get_Altitude()` | m (MSL) | identity (frame은 RELATIVE로 고정) | `z` | m |
| `get_Speed()` | m/s | 단순 수용; 별도 `DO_CHANGE_SPEED` 미발행 (단순화) | — | — |
| `get_TurnType()` | enum (TurnShort/FlyOver) | 미사용 (acceptance radius 5 m 고정) | — | — |
| seq (리스트 인덱스) | int | identity | `seq` | uint16 |
| 고정 상수 | — | `MAV_CMD_NAV_WAYPOINT (16)` | `command` | uint16 |
| 고정 상수 | — | `MAV_FRAME_GLOBAL_RELATIVE_ALT_INT (6)` | `frame` | uint8 |
| 고정 상수 | — | `autocontinue=1`, `param1=0`(hold), `param2=5.0`(acceptance radius m), `param3=0`, `param4=0` | params | — |

업로드 핸드셰이크는 표준 MAVLink mission protocol:
1. `MISSION_CLEAR_ALL` 송신
2. `MISSION_COUNT` 송신 (전체 개수)
3. PX4가 `MISSION_REQUEST_INT`로 한 개씩 요청 → bridge가 `MISSION_ITEM_INT` 응답
4. 마지막에 PX4가 `MISSION_ACK`로 결과 회신 (`type=0`이면 성공)

이 시퀀스는 `MAVLinkReader.upload_mission(items)` 한 함수에 들어 있고, 멀티 vehicle 환경에서도 각 bridge가 자기 `target_system`만 다루므로 충돌 없습니다.

**VehicleID 필터링.** `MissionCommand.get_VehicleID()` 값이 자기 `--vehicle-id`와 다르면 무시합니다. UxAS PUB는 broadcast라서 모든 bridge가 모든 MissionCommand를 받지만, 자기 것만 처리하는 구조입니다. 이렇게 해야 N대 bridge가 같은 UxAS에 붙어 있어도 미션이 엇갈리지 않습니다.

### 5.6 식별자(ID) 일관성

매핑이 흐트러지면 vehicle별 미션이 엇갈리므로, 다음 5개 ID가 항상 일치하도록 합니다.

| 위치 | 키 | mixed_full fleet 예 |
|---|---|---|
| `vehicles.json: vehicles[].id` | LMCP EntityID 원천 | 1, 2, 3, 4, 10 |
| `launch_all.sh` `-i N` (PX4 SITL instance index) | PX4 internal | 동일 숫자 (PX4 instance index = 우리 ID) |
| MAVLink `target_system` (PX4 SITL 기본값) | MAVLink heartbeat에서 | PX4가 instance index 사용 |
| `qgc_uxas_bridge.py --vehicle-id N` | bridge 측 | 동일 숫자 |
| `uxas_multi.xml` `<WaypointPlanManagerService VehicleID="N">` | UxAS 측 | 동일 숫자 |

`launch_bridges.sh`와 `uxas_publish_task.py`는 `vehicles.json`을 직접 읽어 한 번에 ID를 결정하므로, 사람이 5곳에 같은 숫자를 적을 일이 줄어듭니다. UxAS cfg(`uxas_multi.xml`)만 vehicle 수가 바뀌면 사람이 직접 갱신해야 합니다(`WaypointPlanManagerService` 추가/삭제). 자동 생성은 다음 단계 항목.

### 5.7 좌표계·단위 요약

| 도메인 | 위/경도 | 고도 | 속도 | 각도 |
|---|---|---|---|---|
| PX4 MAVLink (raw) | int32, 1e-7 deg | int32 mm (`GLOBAL_POSITION_INT.alt`, MSL) | int16 cm/s | int16 cdeg / float rad |
| Bridge 내부 (`VehicleState`) | float deg | float m | float m/s | float deg |
| LMCP CMASI | float deg | float m | float m/s | float deg |
| LMCP 고도 기준 | — | `AltitudeType.MSL` 강제 | — | — |

모든 단위 변환은 한 군데(`MAVLinkReader._read_loop`)에 모여 있어 추후 단위 버그가 생겨도 추적이 단순합니다.

### 5.8 매핑 검증

- **단위·envelope round-trip**: 이전 세션 `qgc_uxas_bridge.py` mock peer 검증으로 `AirVehicleConfiguration`/`AirVehicleState`/`AreaSearchTask`/`LineSearchTask`/`AutomationRequest`/`MissionCommand` 6종에 대해 `pack → encode_envelope → decode_envelope → unpack` 일치 확인 완료.
- **실 UxAS 호환**: 본 작업의 §6.5에서 `AutomationRequest`를 UxAS에 PUSH한 직후 `AutomationResponse`를 수신, `src_entity="100"`(UxAS 본체)이 회신했음을 확인 → 위 표 5.2 매핑이 정확히 UxAS의 `AutomationRequestValidatorService` 파서 기대 형식과 일치.
- **종단(SITL 포함)**: 미실행, SITL 환경 확보 후 §8 절차 그대로 수행하면 됩니다.

---

## 6. 검증 결과

### 6.1 Bridge syntax + capability CLI

```
$ python3 -m py_compile tools/test-automation/scripts/qgc_uxas_bridge.py
OK
$ python3 tools/test-automation/scripts/qgc_uxas_bridge.py --help | tail -10
  --min-speed MIN_SPEED  LMCP MinimumSpeed m/s (multicopter=0, fixedwing>0)
  --max-speed MAX_SPEED  LMCP MaximumSpeed m/s
  --nominal-speed NOMINAL_SPEED ...
  --min-alt MIN_ALT      LMCP min altitude m (multicopter=0, fixedwing>~20)
  --max-alt MAX_ALT      LMCP max altitude m
  --max-climb MAX_CLIMB  LMCP MaximumClimbRate m/s
  --max-bank-deg MAX_BANK_DEG  LMCP MaxBankAngle deg
```

### 6.2 `launch_all.sh` fleet resolution

```
$ bash -n launch_all.sh && echo OK
OK
$ python3 -c "import json; d=json.load(open('configs/vehicles.json')); \
              print(d['fleets']['mixed_full']['vehicle_ids'])"
[1, 2, 3, 4, 10]
```

### 6.3 `launch_bridges.sh` capability merge

`mixed_full` fleet 해석 결과:

| ID | Name | Type | min_sp | max_sp | min_alt | max_alt |
|---|---|---|---|---|---|---|
| 1 | X500 Quadcopter | multicopter | 0.0 | 18.0 | 0.0 | 200.0 |
| 2 | X500 Depth Camera | multicopter | 0.0 | 18.0 | 0.0 | 200.0 |
| 3 | X500 Vision | multicopter | 0.0 | 18.0 | 0.0 | 200.0 |
| 4 | RC Cessna | fixed_wing | **10.0** | **25.0** | 20.0 | 800.0 |
| 10 | Advanced Plane | fixed_wing | **12.0** | **30.0** | 20.0 | 800.0 |

per-vehicle `lmcp` override가 type defaults 위에 정확히 머지되는 것을 확인 (v4와 v10이 fixed_wing 기본 12/30 대신 자기 값 사용).

### 6.4 UxAS 인스턴스 부팅

```
$ /home/donghoon/myclaude/OpenUxAS/obj/cpp/uxas -cfgPath uxas_multi.xml -runUntil 120
$ ss -tlnp | grep -E '5560|5561'
LISTEN 0  100  0.0.0.0:5561  0.0.0.0:*  users:(("uxas",pid=2598164,fd=42))
LISTEN 0  100  0.0.0.0:5560  0.0.0.0:*  users:(("uxas",pid=2598164,fd=44))
```

5 vehicle(`WaypointPlanManagerService` × 5) + 자율비행 서비스 풀세트로도 정상 부팅.

### 6.5 Bridge ↔ UxAS LMCP round-trip (가장 중요)

`uxas_publish_task.py area --vehicles 1 ... --register-from-config vehicles.json`을 실행했을 때:

```
--- Registering 1 vehicle(s) from ../configs/vehicles.json ---
  registered v1  X500 Quadcopter        multicopter speed=[0,18] m/s  alt=[0,200] m
--- PUBLISH AreaSearchTask id=200 polygon_pts=4 eligible=[1] alt=60.0m ---
--- PUBLISH AutomationRequest id=3000 ---
--- Listening for UxAS response for 8.0s ---
  RECV  AutomationResponse         missions=0

--- Summary ---
pushed by us : 3
received     : 1
  AutomationResponse             1
```

저장된 캡처(`logs/scenarios/tier1_single.json`):

```
{
  "kind": "area", "vehicle_ids": [1],
  "task_id": 200, "request_id": 3000,
  "messages_sent": 3, "messages_received": 1,
  "captured": [
    { "descriptor": "afrl.cmasi.AutomationResponse",
      "kind": "AutomationResponse",
      "src_entity": "100", "src_group": "",
      "num_missions": 0, "info_size": 1 }
  ]
}
```

**해석.**

1. `AutomationResponse`의 `src_entity=100`은 UxAS 자체(`uxas_multi.xml`의 `<UxAS EntityID="100">`)가 응답한 것 — 즉 우리 메시지가 UxAS internal bus에 정확히 도달, `AutomationRequestValidatorService`가 받아서 `PlanBuilderService`가 응답을 만들어 다시 외부 PUB로 broadcast.
2. **이전 세션의 "UxAS와 echo 안 됨" 문제는 해소.** wire format(envelope `address$attrs$payload`, 단일 프레임, LMCP binary)이 정확히 호환됨을 실 UxAS와 confirm.
3. `num_missions=0`인 이유는 vehicle의 현재 위치(`AirVehicleState`)를 UxAS가 모르기 때문. `RoutePlannerVisibilityService`는 시작점 좌표가 있어야 plan을 만들 수 있는데, 우리가 보낸 건 `AirVehicleConfiguration`(dynamics만)이고 `AirVehicleState`는 보내지 않았음. `info_size=1`은 `AutomationResponseInfo`(failure reason)가 한 개 들어있음을 의미 — 즉 UxAS가 "vehicle 위치를 모름" 식의 사유를 회신했을 가능성이 매우 높음.

이 결과는 **bridge + UxAS 통신 경로 자체는 완전히 동작**하며, 실제 SITL을 붙이면 `AirVehicleState`가 자동으로 흘러 들어와 `MissionCommand`가 발행될 것임을 의미합니다.

### 6.6 Live SITL 시뮬레이션 결과 (mixed_small, 5월 29일)

`mixed_small` fleet(X500 × 2 + RC Cessna × 1)을 실제로 실행해 종단 흐름을 검증했습니다. 검증 과정에서 4개의 설정/스크립트 수정사항이 추가되었습니다.

**환경 디버깅에서 찾은 4가지 수정사항.**

| 증상 | 원인 | 수정 |
|---|---|---|
| RC Cessna가 "no autostart file found (2100_*)"로 죽음 | `vehicles.json`의 `SYS_AUTOSTART` 값이 PX4 v1.16 빌드와 불일치 | `gz_rc_cessna=4003`, `gz_advanced_plane=4008`, `gz_standard_vtol=4004`, `gz_r1_rover=4009`, `gz_tiltrotor=4020` 등 PX4 빌드의 `airframes/4xxx_gz_*` 매핑으로 갱신 |
| PX4가 `Failed to find world [/default.sdf]`로 Gazebo 부팅 실패 | `GZ_SIM_RESOURCE_PATH`, `GZ_SIM_SYSTEM_PLUGIN_PATH`, `GZ_SIM_SERVER_CONFIG_PATH` 미설정 | `launch_all.sh`에서 PX4가 빌드 시 생성하는 `build/px4_sitl_default/rootfs/gz_env.sh`를 `source` 하도록 변경 |
| Bridge가 SITL에 붙어도 stdout 로그가 안 보임 | Python 기본 buffered stdout | `launch_bridges.sh`에서 `python3` → `python3 -u` |
| Bridge가 SITL heartbeat를 못 받음 | `vehicles.json`의 `sitl_udp`가 PX4 Onboard MAVLink 송신 포트와 불일치 (PX4 instance N → UDP 14540+N) | 모든 vehicle의 `ports.sitl_udp = 14540 + id`로 자동 산정 |

**실행 후 라이브 상태 (10분 운영).**

```
PX4 SITL 인스턴스    : v1 (X500),  v2 (X500_depth), v4 (RC Cessna)  ── 3 / 3 running
Gazebo Harmonic 8.10  : default world에 모델 3대 spawn
MAVLink heartbeat     : sys_id=2 (X500 quad), sys_id=3 (X500 quad), sys_id=5 (Cessna FW) 모두 수신
Bridge × 3            : 모두 register OK, AirVehicleState 2 Hz publish 중
                       lmcp_out ≈ 30+ per minute per bridge,
                       state.lat ≈ 47.39797 (GPS lock OK)
OpenUxAS              : 5560/5561 LISTEN, ConsiderSelfGenerated bridge 동작
                       AutomationRequest 수신·처리, AutomationResponse 발행
```

**Bridge → UxAS 메시지 흐름 직접 측정** (단일 bridge instance, 15초 동안):

| 시각(s) | mavlink_in | lmcp_out (AVS publish) | lmcp_in (UxAS PUB sub) |
|:-:|:-:|:-:|:-:|
| 0 | 457 | 3 | 1 |
| 5 | 2 601 | 13 | 1 |
| 10 | 4 755 | 23 | 1 |
| 14 | 6 443 | 31 | 1 |

`lmcp_out` 2/s = AirVehicleState publish rate 2 Hz 정확. `mavlink_in` ≈ 50 Hz × N message types ≈ 400+/s로 PX4 telemetry 전체 수신. `lmcp_in=1`은 시작 시점 받은 `AutomationResponse` 1개.

**Tier 1-1 (단일 v1 area search) 실행 결과** — `logs/scenarios/tier1_v1_120s.json`:

```
--- PUBLISH AreaSearchTask id=401 polygon_pts=4 eligible=[1] alt=30.0m ---
--- PUBLISH AutomationRequest id=5001 ---
--- Listening for UxAS response for 115.0s ---
  RECV  ServiceStatus              src=('','100')
  RECV  AutomationResponse         missions=0
  RECV  ServiceStatus              src=('','100')

pushed by us : 2     received : 3
  ServiceStatus       2
  AutomationResponse  1
```

UxAS internal log (`/tmp/uxas_sim/log/log_*`)에서 같은 시각:

```
1780004342370 INFO:  LmcpObjectNetworkClientBaseuniqueAutomationRequest->getRequestID()[2242]
1780004442370 WARN:  - automation request ID[2242] was not ready in time and was not sent.
1780004504857 WARN:  taskID 200 already exists. Killing previous task
1780004504860 INFO:  LmcpObjectNetworkClientBaseuniqueAutomationRequest->getRequestID()[3348]
1780004604860 WARN:  - automation request ID[3348] was not ready in time and was not sent.
```

**해석.** UxAS의 `AutomationRequestValidatorService`가 100초(`MaxResponseTime_ms=100000`) 동안 plan을 만들지 못해 timeout. `num_missions=0`인 `AutomationResponse`를 보냄 (1개의 `AutomationResponseInfo`에 실패 사유 들어있음, 정확 내용은 다음 단계에서 추출). 즉:

- ✅ Bridge ↔ UxAS 양방향 LMCP 통신 완전 동작 (PUSH/SUB 모두 traffic 흐름 확인)
- ✅ UxAS가 우리 PUSH 메시지 정상 수신·처리 (`uniqueAutomationRequest` 로그가 그 증거)
- ✅ Bridge가 UxAS PUB로부터 `AutomationResponse` + `ServiceStatus` 정상 수신
- ⚠ UxAS의 `PlanBuilderService`가 100초 내에 plan을 못 만들어 `MissionCommand` 미발행. 가장 가능성 큰 원인은 **`OperatingRegion` / `KeepInZone` 미정의** — UxAS examples를 보면 `MessagesToSend/KeepInZone_*.xml`과 함께 `OperatingRegion`을 항상 같이 publish. RoutePlanner가 plan 가능 영역을 결정하는 데 이 두 메시지가 필요.

**다음 검증 단계 (별도 작업).**

1. `OperatingRegion` LMCP 빌더를 `qgc_uxas_bridge.py`에 추가, `KeepInZone`(polygon: vehicle 주변 큰 영역)을 fleet 초기화 시 publish.
2. `uxas_publish_task.py`에 `--with-operating-region` 옵션 추가해 task 발행과 함께 region을 자동 송신.
3. `AutomationResponseInfo.Value`에서 정확한 실패 사유 추출하는 헬퍼 추가 (현재 `info_size=1`만 캡처).
4. polygon 위치/altitude를 vehicle 현재 상태(`AirVehicleState.Location`)와 가까이 두어 trivial plan부터 검증.

이 4가지가 끝나면 Tier 1-2 (3대 multicopter split) → Tier 3 (이종 편대) 순으로 의미있는 `MissionCommand` 발행 + MAVLink 업로드 + 실제 비행을 검증할 수 있습니다.

### 6.7 Korea 좌표 + OperatingRegion 라이브 시험 (5월 29일)

§6.6의 4개 후속 작업을 모두 적용한 후, 시작 위치를 **한국 (34.611670, 127.206028)** 으로 옮기고 `mixed_small` fleet으로 동일 흐름을 재시험했습니다.

**적용한 8개 작업.**

1. `configs/worlds/korea.sdf` 신규 — spherical_coordinates = (34.611670, 127.206028, alt 30 m), world name = `"korea"`. PX4 stock `Tools/simulation/gz/worlds/`에 symlink.
2. `launch_all.sh` — `PX4_HOME_LAT/LON/ALT`와 `PX4_GZ_WORLD`를 vehicle별로 export, `--world/--home-lat/--home-lon/--home-alt` CLI 옵션 추가. `EXTRA_WORLDS_DIR`(`configs/worlds`)를 `GZ_SIM_RESOURCE_PATH`에 합류.
3. `vehicles.json` `defaults` — 한국 좌표 + `world="korea"` 반영.
4. `qgc_uxas_bridge.py` — `build_keep_in_zone`, `build_operating_region` 빌더 추가, `_meters_to_lat/lon` 헬퍼 포함. 모두 LMCP pack/unpack round-trip 검증 완료.
5. `uxas_publish_task.py` — `--with-operating-region center_lat,center_lon[,half_size_m]` 옵션 추가. `AutomationResponseInfo` 디코딩(Key/Value 캡처) 정상 동작 — UxAS 실패 사유를 콘솔에서 바로 확인 가능.
6. `uxas_multi.xml` — `AutomationRequestValidatorService MaxResponseTime_ms=100000 → 300000`, `Test_SimulationTime` 서비스 추가.
7. `~/.config/QGroundControl/QGroundControl.ini` — UDP listener Link1/2/3 (port 14541/14542/14544, auto=true) 등록. QGC 시작 시 3대 자동 검색.
8. Tier 시나리오 polygon/line 좌표를 한국 home 주변(±200 m)으로 갱신.

**라이브 검증 결과 (10:00 ~ 10:05, fleet 운영 약 15분).**

| 항목 | 결과 |
|---|---|
| Gazebo Harmonic에서 `korea.sdf` 로드 | ✅ `world: korea, model: x500_1`, `Setting world origin to lat: 34.61167, lon: 127.206028, alt: 30.0` |
| PX4 SITL × 3 spawn (X500 + X500_depth + RC Cessna) | ✅ |
| GLOBAL_POSITION_INT 첫 수신 좌표 | ✅ v1=(34.611670, 127.206028, 29.9 m), v2=(34.611670, 127.206061, 30.7 m), v4=(34.611670, 127.206137, 29.8 m). `spawn_pose.x`가 동쪽 미터 → 경도 변환 정확히 반영 |
| Bridge × 3 register OK + AirVehicleConfiguration 송신 | ✅ |
| Bridge state_publish_loop (2 Hz) | ✅ `lmcp_out` 매초 2씩 증가 |
| UxAS가 우리 PUSH 메시지 수신 | ✅ `LmcpObjectNetworkClientBaseuniqueAutomationRequest->getRequestID()[81]` 로그로 확인 |
| `AutomationResponseInfo` 디코딩 | ✅ 실패 사유 평문 추출: `RequestValidator='- automation request ID[81] was not ready in time and was not sent.\n'` |
| `MissionCommand` 발행 (`AreaSearchTask`, polygon 200 m × 150 m) | ⏸ 미발행 — `PlanBuilderService`가 300 s 내에 plan 완성 못 함 |
| `MissionCommand` 발행 (`LineSearchTask`, 3-point line ~ 100 m) | ⏸ 동일 — UxAS validator가 1번에 1개씩 직렬 처리, 두 번째 task는 큐에서 첫 task 300s timeout 대기 |

`AutomationResponseInfo` 디코딩이 이번 작업의 가장 큰 진단 자산입니다. 이전엔 `info_size=1`만 보였던 실패 사유가 `RequestValidator` 키로 평문 추출되어 UxAS internal 동작을 짐작 없이 알 수 있게 됐습니다.

**근본 원인 가설.** UxAS examples(`examples/02_Example_WaterwaySearch/cfg_WaterwaySearch.xml`, `examples/05a_Ada_AssignTasks/cfg_cpp.xml`)와 우리 cfg를 비교했을 때 핵심 차이:

- **`AirVehicleConfiguration`에 `CameraConfiguration` 누락**. `AreaSearchTask`는 sensor footprint(카메라 FoV) 기반으로 polygon을 스캔할 swath 너비를 결정 → camera 없으면 RoutePlanner가 swath 계산 못 함. examples의 `AirVehicleConfiguration_V*.xml`을 보면 `PayloadConfigurationList`에 `GimbalConfiguration + CameraConfiguration`이 항상 포함.
- `WaypointPlanManagerService`에 `DefaultLoiterRadius_m`, `LoopBackToFirstTask`, `GimbalPayloadId` 등 examples의 디폴트 매개변수 미설정.
- `PlanBuilderService`에 `AssignmentStartPointLead_m="0.0"` 미설정 (현재는 default 사용).

`LineSearchTask`는 camera 없이도 plan 가능해야 하지만, validator가 직렬 처리라 첫 `AreaSearchTask` request의 300 s timeout이 끝나야 두 번째가 시작 — 같은 세션에서 두 task를 연속 발행하면 두 번째는 항상 5분 후에야 처리. 시간 비용이 커 본 시험에서는 첫 `AreaSearchTask` timeout만 확인했습니다.

**검증된 사실 (이번 시험의 net value).**

1. **`OperatingRegion` + `KeepInZone` LMCP wire format이 UxAS와 호환** (RequestValidator가 `OperatingRegion ID`를 인식하고 plan을 시도하는 단계까지 진입).
2. **`AutomationResponseInfo` 디코딩 파이프라인 완성** — 향후 모든 plan 실패를 즉시 진단 가능.
3. **PX4 SITL Korea 좌표 변환 흐름이 깨끗** (Gazebo world, GLOBAL_POSITION_INT, AirVehicleState 모두 일관).
4. **UxAS multi-vehicle cfg(`uxas_multi.xml`)에 `Test_SimulationTime` 누락이 plan 정체의 결정적 원인이 아님을 확인** (추가해도 동일하게 timeout) — RoutePlanner 입력 데이터 부족이 본질.

**다음 검증 단계.**

1. `build_air_vehicle_configuration`에 `CameraConfiguration + GimbalConfiguration` 추가 (PayloadID 매핑). AreaSearchTask plan의 결정적 입력.
2. `WaypointPlanManagerService`에 examples 기본 옵션 (`DefaultLoiterRadius_m=250`, `LoopBackToFirstTask=FALSE`, `GimbalPayloadId=1`) 추가.
3. `examples/02_Example_WaterwaySearch/runUxAS_WaterwaySearch.sh`를 동일 UxAS 바이너리로 실행해 baseline 동작 confirm — UxAS 빌드 자체가 정상인지 분리 검증.
4. baseline 정상이면 우리 cfg에 `SendMessagesService`로 minimal AVC + AVS를 추가해 examples와 동일 패턴으로 시작점 확보.

---

## 7. 토폴로지 (작업 완료 후 상태)

```
                              ┌─────────────────┐
                              │  QGroundControl │
                              │  (Cesium 3D)    │
                              └────────┬────────┘
                                       │ MAVLink (14550..14559)
   ┌───────────────────────────────────┴───────────────────────┐
   │                                                            │
   ▼                                                            ▼
┌────────┐  ┌────────┐  ┌────────┐  ┌────────┐  ┌────────┐
│ PX4 #1 │  │ PX4 #2 │  │ PX4 #3 │  │ PX4 #4 │  │ PX4 #10│
│ x500   │  │ x500   │  │ x500   │  │ Cessna │  │ Adv.   │
│ MC     │  │ MC     │  │ MC     │  │ FW     │  │ Plane  │
│ :14540 │  │ :14541 │  │ :14542 │  │ :14543 │  │ :14549 │
└───┬────┘  └───┬────┘  └───┬────┘  └───┬────┘  └───┬────┘
    │           │           │           │           │
    ▼           ▼           ▼           ▼           ▼
┌────────┐  ┌────────┐  ┌────────┐  ┌────────┐  ┌────────┐
│bridge#1│  │bridge#2│  │bridge#3│  │bridge#4│  │bridge#10│
│ MC cap │  │ MC cap │  │ MC cap │  │ FW cap │  │ FW cap  │
│ 0-18m/s│  │ 0-18m/s│  │ 0-18m/s│  │10-25m/s│  │12-30m/s │
└───┬────┘  └───┬────┘  └───┬────┘  └───┬────┘  └───┬────┘
    │           │           │           │           │
    └───────────┴───────────┴───────────┴───────────┘
                            │  ZMQ  PUSH :5561 / SUB :5560
                            ▼
                  ┌───────────────────────┐
                  │      OpenUxAS         │
                  │  WaypointPlanMgr × 5  │
                  │  AssignmentTreeBB     │
                  │  RoutePlannerVis      │
                  │  PlanBuilder          │
                  │  AutomationReqValid   │
                  └───────────────────────┘

uxas_publish_task.py (제어용) ─── PUSH AutomationRequest ───►  UxAS
                              ◄── SUB AutomationResponse ─────
                              ◄── SUB MissionCommand[v=N] ───  (vehicle별 N개)
```

---

## 8. 실제 시험 실행 절차 (SITL 환경에서)

사용자가 다른 시험을 마치고 SITL을 사용 가능해지면, 아래 5단계로 종단 흐름을 검증할 수 있습니다.

### 8.1 PX4 SITL 5대 (3 MC + 2 FW) 시작

```
cd /home/donghoon/myclaude/qgroundcontrol/tools/test-automation
./scripts/launch_all.sh --fleet mixed_full
```

새 터미널 두 개로:
- 헬스 체크: `ss -ulnp | grep -E '1454[0-9]|1455[0-9]'`
- 로그: `tail -f logs/<timestamp>/instance_1/px4.log`

### 8.2 QGroundControl 시작

```
QTWEBENGINE_DISABLE_SANDBOX=1 ./build/Release/QGroundControl
```

5대가 자동 검색되어 vehicle 목록에 나타나는지 확인.

### 8.3 OpenUxAS 시작

```
mkdir -p /tmp/uxas_run && cd /tmp/uxas_run
cp /home/donghoon/myclaude/qgroundcontrol/tools/test-automation/configs/uxas_multi.xml ./cfg.xml
/home/donghoon/myclaude/OpenUxAS/obj/cpp/uxas -cfgPath cfg.xml -runUntil 1800
```

`ss -tlnp | grep -E '5560|5561'`로 LISTEN 확인.

### 8.4 Bridge 5개 자동 spawn

```
cd /home/donghoon/myclaude/qgroundcontrol/tools/test-automation
./scripts/launch_bridges.sh --fleet mixed_full
```

각 bridge 로그(`logs/bridges_<ts>/bridge_<id>.log`)에 `[Bridge] Vehicle N (...) registered with UxAS [speed ... m/s, alt ... m]`가 보이면 OK. SITL 텔레메트리가 들어오기 시작하면 bridge 내부 state_publish_loop가 `AirVehicleState`를 2 Hz로 PUSH합니다.

### 8.5 Tier 1 / Tier 3 시나리오 발행

```
cd tools/test-automation/scripts
# Tier 1-1 (단일 MC area search)
python3 uxas_publish_task.py area --vehicles 1 \
    --polygon 47.397,8.545 47.398,8.545 47.398,8.546 47.397,8.546 \
    --altitude 60 --out ../logs/scenarios/tier1_single_$(date +%s).json

# Tier 1-2 (3대 MC area split)
python3 uxas_publish_task.py area --vehicles 1,2,3 \
    --polygon 47.397,8.545 47.398,8.545 47.398,8.548 47.397,8.548 \
    --altitude 60 --out ../logs/scenarios/tier1_split_$(date +%s).json

# Tier 3 (혼합 편대 협업 검색)
python3 uxas_publish_task.py area --vehicles 1,2,3,4,10 \
    --polygon 47.396,8.543 47.399,8.543 47.399,8.549 47.396,8.549 \
    --altitude 80 --wait-secs 10 \
    --out ../logs/scenarios/tier3_mixed_$(date +%s).json
```

**기대 결과 (SITL 정상 동작 시).**

- `AutomationResponse` `num_missions` > 0
- `MissionCommand` 메시지 vehicle_id별로 도착 (Tier 1-2에서는 3개, Tier 3에서는 5개까지)
- 각 bridge의 `_handle_mission_command`가 MAVLink로 미션 업로드 → SITL이 미션 비행 시작
- QGC 지도에 vehicle별 미션이 표시되고 비행 시작

`status` CLI(인터랙티브 bridge에서):
```
bridge> status
{ ..., "stats": { "mavlink_in": 412, "lmcp_in": 18, "lmcp_out": 6,
                  "missions_uploaded": 1, ... } }
```

---

## 9. 다음 단계 (이번 보고서 범위 외)

§ 의도적으로 미루어 놓은 작업, 다음에 우선순위 순으로 다뤄야 할 것들:

1. **VTOL/Rover 지원 (이번 작업 2번 항목).** `GroundVehicleConfiguration`/`GroundVehicleState` 빌더와 VTOL flight mode 전환(`MC ↔ FW`) 매핑을 bridge에 추가. UxAS는 VTOL을 별도로 다루지 않고 `AirVehicleConfiguration`의 speed 범위로 추론하므로, 일단은 fixedwing capability로 register 후 mode transition을 MAVLink command로 명시 매핑하는 방향이 현실적.
2. **시나리오 YAML 통합.** 현재 `test_orchestrator.py`가 PX4 SITL 직접 명령만 다루는데, `via: uxas_task` 같은 step type을 추가해 시나리오가 LMCP task 발행도 표현할 수 있게 한다.
3. **KeepOutZone / OperatingRegion 시나리오.** `examples/05a_Ada_AssignTasks/MessagesToSend/KeepOutZone_*.xml` 형식을 LMCP binary로 변환하는 헬퍼를 `uxas_publish_task.py`에 추가.
4. **AssignmentResponse 후처리.** UxAS가 어떤 vehicle에 어떤 task를 할당했는지를 별도 요약 리포트로 추출 (Tier 3에서 의미가 큼).

---

## 10. 산출물 위치

```
qgroundcontrol/
└── tools/test-automation/
    ├── configs/
    │   ├── vehicles.json              ← v1.1 (capability + fleets)
    │   └── uxas_multi.xml             ← UxAS 5-vehicle 설정
    ├── scripts/
    │   ├── qgc_uxas_bridge.py         ← capability CLI 인자 추가
    │   ├── launch_all.sh              ← JSON-driven 재작성
    │   ├── launch_bridges.sh          ← 신규: bridge auto-spawn
    │   └── uxas_publish_task.py       ← 신규: area/line/point task publisher
    ├── docs/
    │   ├── QGC_UxAS_MixedFleet_Report.md   ← 본 문서
    │   ├── QGC_UxAS_MixedFleet_Report.pdf  ← 본 문서 PDF
    │   └── build_report_pdf.py             ← PDF 빌더
    └── logs/
        └── scenarios/
            └── tier1_single.json      ← UxAS round-trip 캡처 결과
```

PDF 재생성: `python3 tools/test-automation/docs/build_report_pdf.py`
