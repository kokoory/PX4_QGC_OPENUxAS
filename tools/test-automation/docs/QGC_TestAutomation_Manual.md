# QGC 테스트 자동화 프레임워크

## 개요

`tools/test-automation/`은 QGroundControl(QGC) + PX4 SITL + OpenUxAS 환경에서 종단 비행 시험을 자동화하는 도구 모음입니다. 핵심 구성은 다음과 같습니다.

- **QGC EventBroadcaster**: QGC가 UI 이벤트를 UDP 45678로 브로드캐스트하고, UDP 45679로 외부 명령을 받습니다 (in-tree, `src/Utilities/EventBroadcaster.{cc,h}`).
- **MAVLink 자동화 스크립트**: pymavlink로 PX4 SITL에 직접 명령(arm/takeoff/land/RTL/mission)을 보내고 텔레메트리를 캡처합니다.
- **YAML 시나리오 오케스트레이터**: 단계별 액션·assertion을 선언적으로 기술하고 자동 실행 후 Pass/Fail 판정과 JSON 결과를 생성합니다.
- **OpenUxAS 브릿지**: PX4 MAVLink ↔ LMCP/ZeroMQ 양방향 변환으로 협업 자율비행 서비스를 통합합니다.
- **멀티 SITL 런처**: 최대 10대 동시 SITL 실행 + QGC 자동 연결.

전 구성요소는 표준 Python 3 프로그램이며 외부 종속성은 `pymavlink`, `pyzmq`, `pyyaml`, `weasyprint`(PDF만)뿐입니다.

---

## 시스템 토폴로지

```
                    ┌──────────────────────────┐
                    │   test_orchestrator.py   │
                    │   (YAML 시나리오 실행)    │
                    └────┬───────────────┬─────┘
        MAVLink (UDP)    │               │   UDP 45678 / 45679
   ┌─────────────────────┘               └─────────────┐
   │                                                    │
┌──▼─────────────┐    UDP 45678 (이벤트)    ┌──────────▼─────────┐
│  PX4 SITL #N   │◀──────────────────────▶│  QGroundControl    │
│  (포트         │   QGC EventBroadcaster  │  (Cesium 3D 지도   │
│   14540+N,     │   UDP 45679 (명령)      │   + EventBroadcaster)│
│   14550+N)     │                          └──────────┬─────────┘
└──┬─────────────┘                                     │
   │ MAVLink 미션 / 명령                                │
   │                                                    │
┌──▼──────────────────────────────────────────────────▼─┐
│            qgc_uxas_bridge.py (vehicle 1..N)           │
│   MAVLink ↔ LMCP binary (AddressedAttributedMessage)   │
└────────────────────────┬───────────────────────────────┘
                         │ ZeroMQ
              PUSH ────────────► PULL :5561
              SUB  ◀──────────── PUB  :5560
                         │
                  ┌──────▼──────┐
                  │  OpenUxAS   │
                  │  (LMCP bus) │
                  └─────────────┘
```

데이터 흐름 요약:

1. PX4 SITL이 MAVLink(UDP 14540+N) 채널로 텔레메트리·heartbeat를 송출하고, 외부에서 보낸 COMMAND_LONG/MISSION_ITEM_INT를 수신·실행합니다.
2. QGroundControl은 SITL의 14550+N 채널에 연결되어 UI에 띄우고, 사용자 액션과 뷰 전환을 EventBroadcaster로 UDP 45678에 JSON 한 줄씩 송출합니다. UDP 45679로 들어온 JSON 명령은 마치 사용자가 버튼을 누른 것처럼 `GuidedActionsController`에 forward됩니다.
3. `test_orchestrator.py`는 YAML 시나리오의 step을 순회하며, 각 step이 MAVLink 명령(`arm`, `takeoff`, …)이면 pymavlink로 직접 보내고, QGC UI 명령(`qgc_action`)이면 UDP 45679로 send합니다. assertion은 텔레메트리 또는 EventBroadcaster 이벤트를 모니터링해 Pass/Fail을 산출합니다.
4. `qgc_uxas_bridge.py`는 MAVLink(QGC/PX4)를 LMCP `AirVehicleConfiguration`/`AirVehicleState`로 변환해 UxAS PULL 5561로 PUSH하고, UxAS PUB 5560을 SUB해서 받은 `MissionCommand`를 MAVLink 미션으로 풀어 SITL에 업로드합니다.

---

## 사전 준비

### 시스템 의존성

| 항목 | 버전 | 설치 방법 |
|---|---|---|
| Python | ≥ 3.8 | 시스템 패키지 |
| pymavlink | latest | `pip install pymavlink` |
| pyzmq | latest | `pip install pyzmq` |
| PyYAML | latest | `pip install pyyaml` |
| PX4-Autopilot | v1.14+ | `make px4_sitl_default` 빌드 |
| QGroundControl | this repo | EventBroadcaster 포함 빌드 (CMake) |
| OpenUxAS | optional | `qgc_uxas_bridge.py`만 사용 |
| Java JRE | 1.8+ | `LmcpGen.jar` 실행 (Python LMCP 라이브러리 생성) |

```
pip install pymavlink pyzmq pyyaml
```

### Python LMCP 라이브러리 생성

`qgc_uxas_bridge.py`는 OpenUxAS의 MDM(Message Data Model)에서 생성한 Python LMCP 클래스를 사용합니다. `tools/test-automation/lmcp_py/`에 한 번만 생성하면 됩니다.

```
java -Xmx2048m -jar /home/donghoon/myclaude/OpenUxAS/infrastructure/sbx/x86_64-linux/lmcpgen/install/LmcpGen.jar \
    -mdmdir /home/donghoon/myclaude/OpenUxAS/mdms \
    -py \
    -dir /home/donghoon/myclaude/qgroundcontrol/tools/test-automation/lmcp_py
```

생성 후 디렉토리:
```
lmcp_py/
├── lmcp/             ← LMCPFactory, LMCPObject
├── afrl/             ← afrl.cmasi, afrl.impact, afrl.vehicles
└── uxas/             ← uxas.messages.{uxnative, task, route}
```

### QGC 빌드 (EventBroadcaster + Cesium 3D)

```
cmake -B build -G Ninja -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release --parallel
```

빌드 산출물 `build/Release/QGroundControl`. EventBroadcaster는 자동 등록되어 시작 시 UDP 45679에 바인딩됩니다 (`ss -ulnp | grep 45679`로 확인).

### 한 줄 헬스 체크

```
python3 -m py_compile tools/test-automation/scripts/*.py
bash -n tools/test-automation/scripts/launch_all.sh
python3 tools/test-automation/scripts/qgc_event_monitor.py --help
```

---

## 디렉토리 구조

```
tools/test-automation/
├── README.md
├── docs/
│   └── QGC_TestAutomation_Manual.{md,pdf}    ← 이 문서
├── lmcp_py/                                   ← LmcpGen 생성물 (gitignored)
├── configs/
│   ├── vehicles.json                          ← 10대 기체 설정
│   └── scenarios/
│       ├── TC001_basic_flight.yaml
│       ├── TC002_mission_flight.yaml
│       └── TC003_failsafe_test.yaml
└── scripts/
    ├── test_orchestrator.py
    ├── test_report.py
    ├── qgc_event_monitor.py
    ├── qgc_event_replay.py
    ├── mavlink_recorder.py
    ├── standard_mission.py
    ├── qgc_uxas_bridge.py
    └── launch_all.sh
```

---

## 스크립트 레퍼런스

### `qgc_event_monitor.py` — UI 이벤트 실시간 모니터

**기능.** QGC가 UDP 45678로 broadcast하는 JSON 이벤트를 수신·예쁘게 출력하고, 카테고리별 카운트를 누적합니다. 시나리오 실행 중 어떤 UI 액션이 트리거됐는지 한눈에 보여줍니다.

**옵션.**
- `--port PORT` 수신 포트 (기본 45678)
- `--log FILE` 받은 이벤트를 JSONL 로 기록

**사용 예.**
```
python3 scripts/qgc_event_monitor.py --port 45678 --log /tmp/qgc-events.jsonl
```

QGC를 띄우고 모니터를 켠 다음 Fly View에서 Arm 버튼을 누르면 다음과 같이 출력됩니다:
```
13:42:01.234   1  action     arm_clicked          vehicle=1
13:42:03.014   2  action     takeoff_clicked      altitude=10
13:42:14.987   3  view       fly_view             {}
```

**입력 페이로드.** 한 datagram = 하나의 JSON 객체:
```
{"seq": 12, "timestamp": 1714967600.123, "category": "action",
 "event": "arm_clicked", "data": {"vehicle": 1}}
```

---

### `qgc_event_replay.py` — MAVLink 명령 시퀀스 재생

**기능.** 미리 정의한 MAVLink 명령 시퀀스(arm → takeoff → waypoint → land 같은)를 PX4 SITL에 직접 시간 기반으로 송출합니다. 회귀 테스트의 골든 시퀀스로 활용합니다.

**옵션.**
- `--instance N` PX4 SITL 인스턴스 번호 (포트 14540+N)
- `--sequence FILE` 시퀀스 JSON 파일 (없으면 내장 기본 시퀀스 사용)
- `--host HOST` 기본 127.0.0.1
- `--output FILE` 실행 결과 로그

**사용 예.**
```
python3 scripts/qgc_event_replay.py --instance 0 --sequence configs/golden_seq.json
```

**시퀀스 JSON 형식.**
```
[
  {"t": 0.0,  "action": "arm"},
  {"t": 2.0,  "action": "takeoff", "altitude": 10},
  {"t": 30.0, "action": "land"}
]
```

---

### `mavlink_recorder.py` — 전체 MAVLink 캡처

**기능.** PX4 SITL이 송신하는 모든 MAVLink 메시지를 (1) raw 바이너리(`.mavlink`)와 (2) 메시지 타입별 CSV로 동시에 저장합니다. 비행 후 분석/디버깅의 1차 근거 자료입니다.

**옵션.**
- `--instance N` SITL 인스턴스 번호
- `--host HOST`, `--output-dir DIR`
- `--duration SEC` 0이면 Ctrl+C까지 무한 캡처
- `--rate HZ` 메시지 stream rate 요청
- `--status-interval SEC` 진행 상황 출력 주기

**사용 예.**
```
python3 scripts/mavlink_recorder.py --instance 0 --duration 120 \
    --output-dir /tmp/flight_001
```

산출물:
```
/tmp/flight_001/raw_20260508_143012.mavlink
/tmp/flight_001/GLOBAL_POSITION_INT_20260508_143012.csv
/tmp/flight_001/ATTITUDE_20260508_143012.csv
...
```

---

### `standard_mission.py` — 표준 비행 시험 미션

**기능.** "사각 패턴 + 호버 + 자동 RTL" 같은 표준 미션을 생성·업로드·실행합니다. 회귀 테스트의 baseline.

**옵션.**
- `--instance N` 필수
- `--altitude M` 비행 고도 (기본 20)
- `--pattern-size M` 사각 한 변 길이 (기본 50)
- `--record` 동시에 mavlink_recorder도 함께 실행
- `--output FILE` 결과 JSON

**사용 예.**
```
python3 scripts/standard_mission.py --instance 0 --altitude 30 --pattern-size 80 --record
```

---

### `test_orchestrator.py` — YAML 시나리오 자동 실행

**기능.** 시나리오 YAML을 읽어 step을 순서대로 실행, 각 step의 assertion을 평가, JSON 결과를 출력합니다. CI에서 그대로 사용할 수 있도록 종료 코드(0=PASS, 1=FAIL)를 반환합니다.

**옵션.**
- `--scenario FILE` 필수, YAML 시나리오 경로
- `--instance N` 대상 SITL 인스턴스 (기본 0)
- `--host HOST`, `--event-port PORT`, `--send-port PORT`
- `--output FILE` 결과 JSON 저장 경로

**사용 예.**
```
python3 scripts/test_orchestrator.py \
    --scenario configs/scenarios/TC001_basic_flight.yaml \
    --instance 0 --output /tmp/TC001.json
```

**시나리오 YAML 스키마.**

```
name: TC001 Basic Flight
description: 기본 arm → takeoff → land 시퀀스
timeout: 120
target:
  instance: 0           # 14540+N MAVLink 포트
  qgc_event_port: 45678 # QGC가 broadcast하는 이벤트 포트
  qgc_send_port: 45679  # QGC에 명령을 보내는 포트
steps:
  - name: arm vehicle
    action: arm
    via: mavlink         # mavlink | qgc_action
    timeout: 5
    assert:
      - kind: telemetry
        message: HEARTBEAT
        field: armed
        equals: true
      - kind: event       # qgc_event_monitor가 받는 이벤트
        category: action
        event: arm_acknowledged
        within: 3

  - name: takeoff
    action: takeoff
    via: mavlink
    params:
      altitude: 10
    assert:
      - kind: telemetry
        message: GLOBAL_POSITION_INT
        field: relative_alt_m
        gte: 9.5
        within: 30

  - name: land
    action: land
    via: mavlink
    assert:
      - kind: telemetry
        message: HEARTBEAT
        field: armed
        equals: false
        within: 60
```

`via: qgc_action`을 사용하면 step이 UDP 45679로 `{"action":"…"}` JSON을 송신해 QGC UI를 통해 명령이 발행되고, EventBroadcaster의 응답 이벤트가 다시 `kind: event`로 검증 가능합니다. 이 경로는 QGC ↔ PX4 통합 동작을 한 번에 시험합니다.

**출력 JSON.**
```
{
  "scenario": "TC001 Basic Flight",
  "timestamp": "2026-05-08T14:30:00",
  "instance": 0,
  "passed": true,
  "total_steps": 3, "passed_steps": 3, "failed_steps": 0,
  "duration": 73.4,
  "steps": [{"name":"arm vehicle","passed":true,"duration":1.0,...}, ...]
}
```

---

### `test_report.py` — HTML/콘솔 리포트

**기능.** orchestrator가 생성한 JSON(여러 개도 가능)을 받아 콘솔 표 + HTML 리포트를 생성합니다. CI artifact로 적합합니다.

**옵션.**
- `--input FILE` (반복 가능) JSON 결과 파일
- `--html FILE` HTML 출력 경로

**사용 예.**
```
python3 scripts/test_report.py \
    --input /tmp/TC001.json --input /tmp/TC002.json \
    --html /tmp/report.html
```

---

### `qgc_uxas_bridge.py` — QGC ↔ OpenUxAS LMCP 브릿지

**기능.**
1. MAVLink로부터 받은 vehicle state를 LMCP `AirVehicleState`로 변환해 UxAS의 PULL 소켓(5561)에 PUSH합니다 (`--state-rate Hz`).
2. UxAS PUB 소켓(5560)을 SUB해서 받은 `MissionCommand`를 MAVLink mission으로 변환·업로드합니다.
3. CLI로 `register_vehicle`, `area_search`, `line_search`, `status`, `state`, `messages`를 실행할 수 있습니다.

**옵션.**
- `--mavlink CONN` MAVLink connection string (`udp:127.0.0.1:14550`)
- `--uxas-pub ADDR` UxAS 외부 PUB 주소 (`tcp://127.0.0.1:5560`)
- `--uxas-pull ADDR` UxAS 외부 PULL 주소 (`tcp://127.0.0.1:5561`)
- `--vehicle-id N` LMCP에서 사용할 vehicle ID
- `--state-rate HZ` AirVehicleState publish 주기 (기본 2)
- `--auto-register` 시작 시 자동으로 AirVehicleConfiguration 송신
- `--non-interactive` CLI 없이 백그라운드 모드
- `--no-heartbeat-wait` MAVLink heartbeat 대기 생략 (UxAS 단독 점검용)

**기본 사용 예 (단일 vehicle).**
```
python3 scripts/qgc_uxas_bridge.py \
    --mavlink udp:127.0.0.1:14550 \
    --uxas-pub  tcp://127.0.0.1:5560 \
    --uxas-pull tcp://127.0.0.1:5561 \
    --vehicle-id 1 --auto-register
```

CLI 안에서:
```
bridge> register
[Bridge] Vehicle 1 registered with UxAS

bridge> area_search 47.397,8.545 47.398,8.545 47.398,8.546 47.397,8.546 80
[Bridge] AreaSearchTask 102 sent
[Bridge] AutomationRequest sent for task 102

bridge> status
{ "vehicle_id": 1, "registered": true, "running": true,
  "vehicle_state": {...},
  "stats": {"mavlink_in": 412, "lmcp_in": 18, "lmcp_out": 6, ...}
}
```

**LMCP/ZeroMQ 와이어 형식.** 상세는 부록 A.

---

### `launch_all.sh` — 10대 멀티 SITL 런처

**기능.** PX4-Autopilot 빌드 산출물을 사용해 N(기본 10)대의 SITL을 동시에 띄우고, 각 인스턴스를 QGC로 자동 라우팅합니다.

**옵션 (환경변수).**
- `PX4_DIR` PX4-Autopilot 루트 (기본 `~/distiledUAV/PX4-Autopilot`)
- `INSTANCES` 동시 인스턴스 수 (기본 10)
- `MODEL` PX4 SITL 모델 (`gz_x500`, `iris` 등)
- `BASE_LAT`, `BASE_LON` 출발 좌표
- `SPACING_M` 인스턴스 간 거리 (m)

**사용 예.**
```
INSTANCES=4 MODEL=gz_x500 ./scripts/launch_all.sh
```

각 인스턴스의 MAVLink 포트는 `14540 + N`(GCS) / `14550 + N`(QGC). QGC는 자동 검색으로 모든 인스턴스를 vehicle 목록에 추가합니다.

---

## `configs/vehicles.json` — 10대 기체 설정

```
{
  "vehicles": [
    { "id": 1, "instance": 0, "model": "gz_x500",
      "spawn": {"lat": 47.39775, "lon": 8.54562, "alt": 488.0},
      "params": {"COM_RC_LOSS": 0.5, "BAT_LOW_THR": 0.20} },
    ...
  ]
}
```

`launch_all.sh`와 `test_orchestrator.py`가 모두 이 파일을 입력으로 받습니다. `id`는 LMCP `EntityID`이자 `qgc_uxas_bridge.py --vehicle-id`이고, `instance`는 PX4 SITL 슬롯 번호입니다.

---

## 멀티 vehicle × OpenUxAS 통합 운영

이 절은 핵심 사용 흐름이라 단계별로 자세히 다룹니다. 가정: 4대 SITL + 4개 bridge + 1개 UxAS + 1개 QGC.

### 1단계 — UxAS 설정 파일 작성

UxAS는 `LmcpObjectNetworkPublishPullBridge`로 외부 LMCP 트래픽을 받습니다. multi-vehicle을 받으려면 다음 cfg가 필요합니다.

```
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<UxAS EntityID="100" FormatVersion="1.0" EntityType="Aircraft">

  <!-- 외부 (Python bridge) 통신용 -->
  <Bridge Type="LmcpObjectNetworkPublishPullBridge"
          AddressPUB="tcp://*:5560"
          AddressPULL="tcp://*:5561"
          ConsiderSelfGenerated="TRUE">
    <SubscribeToMessage MessageType="afrl.cmasi.AirVehicleConfiguration"/>
    <SubscribeToMessage MessageType="afrl.cmasi.AirVehicleState"/>
    <SubscribeToMessage MessageType="afrl.cmasi.AreaSearchTask"/>
    <SubscribeToMessage MessageType="afrl.cmasi.LineSearchTask"/>
    <SubscribeToMessage MessageType="afrl.cmasi.AutomationRequest"/>
    <SubscribeToMessage MessageType="afrl.cmasi.MissionCommand"/>
  </Bridge>

  <!-- 자율 비행 서비스 -->
  <Service Type="AutomationRequestValidatorService" MaxResponseTime_ms="100000"/>
  <Service Type="TaskManagerService"/>
  <Service Type="SensorManagerService"/>
  <Service Type="RouteAggregatorService"/>
  <Service Type="RoutePlannerVisibilityService" MinimumWaypointSeparation_m="50.0"/>
  <Service Type="PlanBuilderService"/>
  <Service Type="AutomationDiagramDataService"/>

  <!-- vehicle 별 WaypointPlanManagerService (LMCP MissionCommand 발행) -->
  <Service Type="WaypointPlanManagerService" VehicleID="1"
           NumberWaypointsToServe="512" NumberWaypointsOverlap="5"
           param.turnType="FlyOver"/>
  <Service Type="WaypointPlanManagerService" VehicleID="2"
           NumberWaypointsToServe="512" NumberWaypointsOverlap="5"
           param.turnType="FlyOver"/>
  <Service Type="WaypointPlanManagerService" VehicleID="3"
           NumberWaypointsToServe="512" NumberWaypointsOverlap="5"
           param.turnType="FlyOver"/>
  <Service Type="WaypointPlanManagerService" VehicleID="4"
           NumberWaypointsToServe="512" NumberWaypointsOverlap="5"
           param.turnType="FlyOver"/>

</UxAS>
```

`WaypointPlanManagerService`는 vehicle마다 하나씩 필요합니다. `VehicleID`가 LMCP `EntityID`이므로, bridge의 `--vehicle-id`와 일치시켜야 합니다.

### 2단계 — SITL × N + QGC × 1

```
# 터미널 1: SITL 4대
INSTANCES=4 MODEL=gz_x500 ./tools/test-automation/scripts/launch_all.sh

# 터미널 2: QGC
./build/Release/QGroundControl
```

QGC는 자동 검색으로 `udp://14550..14553` 4개 vehicle을 동시에 띄웁니다.

### 3단계 — UxAS 실행

```
mkdir -p /tmp/uxas_run && cd /tmp/uxas_run
cp /home/donghoon/myclaude/qgroundcontrol/tools/test-automation/configs/uxas_multi.xml ./cfg.xml
/home/donghoon/myclaude/OpenUxAS/obj/cpp/uxas -cfgPath cfg.xml -runUntil 600
```

`ss -tlnp | grep -E '5560|5561'` 출력으로 UxAS가 두 포트에 LISTEN 중인지 확인합니다.

### 4단계 — Bridge × N 실행 (vehicle 당 1개)

각 vehicle마다 별도 bridge 프로세스를 띄웁니다. MAVLink 포트는 SITL과, vehicle ID는 UxAS cfg와 일치해야 합니다.

```
# 터미널 3: vehicle 1
python3 tools/test-automation/scripts/qgc_uxas_bridge.py \
    --mavlink udp:127.0.0.1:14540 \
    --uxas-pub  tcp://127.0.0.1:5560 \
    --uxas-pull tcp://127.0.0.1:5561 \
    --vehicle-id 1 --auto-register --non-interactive

# 터미널 4: vehicle 2
python3 tools/test-automation/scripts/qgc_uxas_bridge.py \
    --mavlink udp:127.0.0.1:14541 \
    --uxas-pub  tcp://127.0.0.1:5560 --uxas-pull tcp://127.0.0.1:5561 \
    --vehicle-id 2 --auto-register --non-interactive

# 터미널 5,6: vehicle 3, 4 (포트 14542, 14543)
```

여러 bridge가 같은 UxAS PUB를 SUB하는 것은 ZMQ 패턴상 정상입니다 — UxAS PUB가 한 번 broadcast하면 모든 SUB가 사본을 받습니다. 단, 각 bridge는 자기 vehicle이 대상이 아닌 `MissionCommand`를 무시해야 하므로, bridge의 `_handle_mission_command`는 `mc.get_VehicleID() == self.vehicle_id`인 경우에만 MAVLink로 풀어냅니다 (현재 구현됨).

### 5단계 — 미션 발행

Bridge 한 대를 인터랙티브 모드로 띄워 task를 발행하거나, 별도 스크립트로 LMCP `AutomationRequest`를 만들어 PUSH합니다.

```
# 인터랙티브 bridge (--non-interactive 빼고 실행한 vehicle 1 콘솔)
bridge> area_search 47.397,8.545 47.398,8.545 47.398,8.546 47.397,8.546 80
[Bridge] AreaSearchTask 102 sent
[Bridge] AutomationRequest sent for task 102
```

또는 4대를 한 번에 묶어서 영역 분할 검색을 발행하려면 작은 헬퍼를 작성합니다.

```
# ad-hoc 다중 vehicle 영역 분배
import sys; sys.path.insert(0, 'tools/test-automation/scripts')
sys.path.insert(0, 'tools/test-automation/lmcp_py')
from qgc_uxas_bridge import (UxASInterface, build_area_search_task,
                             build_automation_request)

ux = UxASInterface(sub_addr='tcp://127.0.0.1:5560',
                   push_addr='tcp://127.0.0.1:5561',
                   entity_id=999, service_id=0)
ux.start()

vehicle_ids = [1, 2, 3, 4]
polygon = [(47.397,8.545),(47.398,8.545),(47.398,8.546),(47.397,8.546)]

task = build_area_search_task(task_id=200, polygon_lat_lon=polygon,
                              eligible_vehicles=vehicle_ids, altitude_m=80.0)
ux.publish(task)

req  = build_automation_request(request_id=3000, task_ids=[200],
                                vehicle_ids=vehicle_ids)
ux.publish(req)
```

UxAS의 `PlanBuilderService`가 `AutomationResponse`/`MissionCommand`를 vehicle별로 분배하면, 각 bridge의 `_handle_mission_command`가 자기 vehicle에 해당하는 `MissionCommand`만 받아 SITL에 미션 업로드합니다.

### 6단계 — 검증

| 도구 | 확인 항목 |
|---|---|
| `bridge> status` | `lmcp_in/out`, `mavlink_in/out`, `missions_uploaded` 카운터가 증가하는지 |
| `qgc_event_monitor.py` | QGC에서 mission start 이벤트 수신 |
| `mavlink_recorder.py --instance N` | MISSION_ITEM_REACHED, GLOBAL_POSITION_INT 추적 |
| `test_orchestrator.py` | 시나리오 단위 자동 Pass/Fail |

### 7단계 — CI 자동화

`test_orchestrator.py`의 종료 코드를 사용해 GitHub Actions / Jenkins에 endpoint:

```
launch_all.sh &
sleep 15  # SITL/QGC 안정화
qgc_uxas_bridge.py --vehicle-id 1 --auto-register --non-interactive &
qgc_uxas_bridge.py --vehicle-id 2 --auto-register --non-interactive &
test_orchestrator.py --scenario configs/scenarios/TC001_basic_flight.yaml \
                     --output /tmp/TC001.json
test_orchestrator.py --scenario configs/scenarios/TC002_mission_flight.yaml \
                     --output /tmp/TC002.json
test_report.py --input /tmp/TC001.json --input /tmp/TC002.json \
               --html /tmp/report.html
```

---

## 트러블슈팅

### Bridge가 UxAS와 connect는 되지만 메시지 echo가 없습니다.

UxAS는 release build에서 `UXAS_LOG_INFORM`/`UXAS_LOG_DEBUGGING` 매크로가 컴파일타임에 비활성(`src/cpp/Utilities/UxAS_Log.h:23-24`)이라 internal forward 경로가 로그에 남지 않습니다. 가능한 원인:

- `ConsiderSelfGenerated="TRUE"` 누락 → bridge에서 push한 메시지가 internal bus에 forwarding되지 않음.
- `WaypointPlanManagerService` 같은 vehicle별 service가 없으면 `AutomationRequest`가 처리되지 않아 `MissionCommand` 응답이 없음.
- LMCP `EntityID` 불일치 → `WaypointPlanManagerService VehicleID="N"`과 bridge `--vehicle-id N`이 같아야 함.

검증 체크리스트:
1. `ss -tlnp | grep -E '5560|5561'` → UxAS LISTEN 중인지
2. `bridge> status`에서 `lmcp_out` 증가 → bridge가 push 성공
3. mock UxAS peer로 wire format만 분리 검증 (부록 B의 스니펫)

### QGC 실행 시 segfault.

Ubuntu 24.04 + 사용자 경로($HOME/Qt) WebEngine 조합. `kernel.apparmor_restrict_unprivileged_userns=1`이 default이고 user-installed Qt의 `QtWebEngineProcess`에 AppArmor 프로필이 없어 sandboxing 실패. Bridge와는 무관하지만 같은 환경에서 일어나기 쉽습니다. 회피:

```
QTWEBENGINE_DISABLE_SANDBOX=1 ./build/Release/QGroundControl
```

(`src/main.cc`에서 자동으로 set하도록 패치되어 있음.)

### EventBroadcaster 명령이 무시됩니다.

- 차량이 연결되지 않은 상태에서는 `GuidedActionsController`가 warning 후 무시 (`!_activeVehicle`).
- 시뮬에서 GPS lock 전에는 `arm`이 거부됨 → SITL 시작 후 5–10초 대기.
- `nc -u 127.0.0.1 45679` 같은 단순 도구로 명령을 손으로 보내 동작 확인 가능.

---

## 부록 A — LMCP/ZeroMQ 와이어 형식

`qgc_uxas_bridge.py`는 OpenUxAS `LmcpObjectNetworkPublishPullBridge`와 직접 호환되도록 다음 형식을 사용합니다.

### 소켓 패턴

| UxAS 측 | 외부 (bridge) 측 |
|---|---|
| `ZMQ_PUB` `tcp://*:5560` (server bind) | `ZMQ_SUB` connect, subscribe="" |
| `ZMQ_PULL` `tcp://*:5561` (server bind) | `ZMQ_PUSH` connect |

### 한 datagram = 단일 프레임 AddressedAttributedMessage

```
<address> "$" <contentType> "|" <descriptor> "|" <sourceGroup>
"|" <sourceEntityId> "|" <sourceServiceId> "$" <payload>
```

- `address` — 보통 LMCP type FQN (`afrl.cmasi.AirVehicleState`). UxAS는 broadcast 패턴 매칭에 사용.
- `contentType` — 항상 `lmcp`.
- `descriptor` — LMCP type FQN (address와 동일하게 사용 가능).
- `sourceGroup` — 자유 식별자(빈 문자열 가능).
- `sourceEntityId` — bridge의 `--vehicle-id`(문자열).
- `sourceServiceId` — 일반적으로 `"0"`.
- `payload` — `LMCPFactory.packMessage(obj, calcChecksum=True)` 바이너리. 헤더(4B 매직 `LMCP`)–사이즈(4B)–valid(1B)–series_id(8B)–type(4B)–version(2B)–payload–checksum(4B) 구조.

`$`/`|` 구분자는 wire 형식의 처음 두 `$`만 분리에 사용되므로, `payload` 바이너리에 `$`가 들어 있어도 안전합니다 (검증됨).

### 호환성 검증 스니펫

```
# mock UxAS peer로 bridge wire format 단독 검증
python3 - <<'PY'
import sys, time, threading, zmq
sys.path.insert(0, 'tools/test-automation/scripts')
sys.path.insert(0, 'tools/test-automation/lmcp_py')
from qgc_uxas_bridge import (UxASInterface, encode_envelope, decode_envelope,
                             build_air_vehicle_configuration)
from lmcp import LMCPFactory

ctx = zmq.Context.instance()
pull = ctx.socket(zmq.PULL); pull.bind('tcp://127.0.0.1:5571')
pub  = ctx.socket(zmq.PUB);  pub.bind('tcp://127.0.0.1:5570')

stop = threading.Event(); factory = LMCPFactory.LMCPFactory()
def relay():
    p = zmq.Poller(); p.register(pull, zmq.POLLIN)
    while not stop.is_set():
        if dict(p.poll(200)).get(pull) == zmq.POLLIN:
            raw = pull.recv()
            env = decode_envelope(raw)
            obj = factory.getObject(bytearray(env.payload)) if env else None
            print(f'[mock] {env.descriptor} -> {type(obj).__name__}')
            pub.send(encode_envelope(env.address, env.descriptor,
                                     'PublishPullBridge', '100', '51', env.payload))
threading.Thread(target=relay, daemon=True).start(); time.sleep(0.2)

ux = UxASInterface(sub_addr='tcp://127.0.0.1:5570',
                   push_addr='tcp://127.0.0.1:5571', entity_id=42)
ux.on_message(lambda env, obj: print(f'[bridge SUB] {env.descriptor} -> {type(obj).__name__}'))
ux.start(); time.sleep(0.5)
ux.publish(build_air_vehicle_configuration(vehicle_id=42, label='UAV42'))
time.sleep(1.0); ux.stop(); stop.set()
PY
```

기대 출력:
```
[mock] afrl.cmasi.AirVehicleConfiguration -> AirVehicleConfiguration
[bridge SUB] afrl.cmasi.AirVehicleConfiguration -> AirVehicleConfiguration
```

---

## 부록 B — EventBroadcaster JSON 스키마

### QGC → 외부 (UDP 45678 broadcast)

```
{
  "seq": 12,                    // 단조 증가 시퀀스
  "timestamp": 1714967600.123,  // epoch seconds (float)
  "category": "action",         // action|view|setting|map|mission
  "event": "arm_clicked",       // free-form event name
  "data": { ... }               // optional: 컨텍스트
}
```

발행 위치(현재 구현):
- `src/FlyView/GuidedActionsController.qml` — guided action 실행 시 `category=action`
- `src/UI/toolbar/SelectViewDropdown.qml` — view 전환 시 `category=view`

### 외부 → QGC (UDP 45679)

```
{ "action": "arm" }
{ "action": "disarm" }
{ "action": "takeoff",  "altitude": 15 }
{ "action": "land" }
{ "action": "rtl" }
{ "action": "start_mission" }
{ "action": "pause" }
{ "action": "set_mode", "mode": "posctl" }
```

수신 처리: `src/FlyView/GuidedActionsController.qml`이 `EventBroadcaster.onCommandReceived`를 구독해 active vehicle에 대해 `executeAction(...)`을 호출.

---

## 부록 C — 빠른 시작 체크리스트

```
[ ] PX4 SITL 빌드 완료 (PX4-Autopilot/build/px4_sitl_default/bin/px4 존재)
[ ] QGC 빌드 완료 (build/Release/QGroundControl 존재, EventBroadcaster 포함)
[ ] OpenUxAS 빌드 완료 (obj/cpp/uxas 존재)
[ ] LmcpGen으로 lmcp_py/ 생성 완료
[ ] pip install pymavlink pyzmq pyyaml
[ ] launch_all.sh 한 번 실행해 SITL × N 정상 부팅 확인
[ ] QGC 실행 후 EventBroadcaster 포트 listen 확인 (ss -ulnp | grep 45679)
[ ] qgc_event_monitor.py로 단일 액션(view 전환) 이벤트 수신 확인
[ ] qgc_uxas_bridge.py 실행 후 status에서 lmcp_out 증가 확인
[ ] test_orchestrator.py로 TC001 PASS 확인
[ ] test_report.py로 HTML 리포트 생성 확인
```

각 단계가 모두 통과하면 multi-vehicle × OpenUxAS 자동화 환경이 완성됩니다.
