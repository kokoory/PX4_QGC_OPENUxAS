# 세션 핸드오프 노트

## ★★ 2026-06-06: 고성능 호스트로 이전 — 새 세션 시작 가이드

이 호스트(load 높음, uptime 32일+)에서는 lockstep 센서 starvation 때문에 멀티 vehicle 동시 비행이 불가능했다. **더 강한 컴퓨터의 새 세션에서 이어서 진행**하기로 결정. 아래 순서대로 하면 된다.

### 0. 코드 이전 — 전용 repo `PX4_QGC_OPENUxAS` 생성 + push (결정됨, 2026-06-06)

여기서 작업한 전부(QGC 코어 변경 + Cesium 3D 브랜치 + tools/test-automation)를 **새 GitHub repo `PX4_QGC_OPENUxAS`에 commit/push**해 두었고, 새 호스트에서는 clone만 하면 된다:

```
git clone -b Add3Dmap https://github.com/kokoory/PX4_QGC_OPENUxAS.git
cd PX4_QGC_OPENUxAS && git submodule update --init --recursive   # QGC 빌드에 필요
```

- `lmcp_py/`는 **repo에 포함됨** (gitignore 해제) — 새 호스트에서 Java/LmcpGen 없이 bridge가 바로 동작
- `logs/`(라이브 캡처 324MB)는 repo에 안 들어감 — 보존하려면 이 호스트에서 별도 복사
- PX4-Autopilot / OpenUxAS는 이 repo에 없음 — 새 호스트에서 upstream clone + 빌드 (아래 §1). LmcpGen 재생성도 §1-③ 참조 (lmcp_py가 이미 있으니 보통 불필요)
- 전체 clone이 무거우면 `git clone --depth 1 -b Add3Dmap ...`으로 받아도 작업엔 지장 없음

### 1. 새 호스트 환경 구축 체크리스트

①〜⑤ 상세 명령은 `docs/QGC_TestAutomation_Manual.md` 참조.

1. **PX4-Autopilot** — 라이브 검증에 쓰인 정확한 베이스로 clone:
   ```
   git clone -b claude/knowledge-distillation-flight-fGsy1 --recursive \
       https://github.com/kokoory/PX4-Autopilot.git
   cd PX4-Autopilot
   # x500 CA 로터 위치 보정 (라이브 비행이 이 패치 적용 상태로 검증됨; 구 호스트에선 uncommitted였음)
   git apply <QGC>/tools/test-automation/configs/px4_patches/0001-x500-ca-rotor-positions.patch
   make px4_sitl_default
   ```
   + Gazebo Harmonic 설치. (이 브랜치의 NN-control/distillation 코드는 별개 프로젝트 것 — EXTERNAL1 모드에서만 동작하므로 우리 mission 비행과 무간섭. upstream main으로 새로 받아도 되지만 버전 차이 리스크는 감수)
2. **OpenUxAS** — fork의 develop이 정확한 베이스 (2026-06-06 push 완료, `--no-amase` flag 등 로컬 커밋 14개 포함):
   ```
   git clone -b develop https://github.com/kokoory/OpenUxAS.git
   ```
   release build → `obj/cpp/uxas` 생성 확인 (LmcpGen.jar은 OpenUxAS infrastructure에 같이 빌드됨)
3. **lmcp_py 생성** (Java 1.8+ 필요):
   ```
   java -Xmx2048m -jar <OpenUxAS>/infrastructure/sbx/x86_64-linux/lmcpgen/install/LmcpGen.jar \
       -mdmdir <OpenUxAS>/mdms -py \
       -dir <QGC>/tools/test-automation/lmcp_py
   ```
4. **Python**: `pip install pymavlink pyzmq pyyaml` (보고서 PDF 빌드까지 하려면 + `markdown weasyprint`)
5. **korea.sdf symlink**:
   ```
   ln -sf <QGC>/tools/test-automation/configs/worlds/korea.sdf \
       <PX4>/Tools/simulation/gz/worlds/korea.sdf
   ```
6. **QGC 빌드**: `cmake -B build -G Ninja -DCMAKE_BUILD_TYPE=Release && cmake --build build --parallel` (Qt 6.10+ WebEngineQuick 포함 — Cesium 3D가 빌드 플래그 `QGC_CESIUM3D_ENABLED`에 묶여 있음)
7. **경로 의존성 수정** (2곳뿐):
   - `launch_all.sh` — `PX4_DIR` env로 PX4 경로 지정 (기본값은 `~/distiledUAV/PX4-Autopilot` → `~/PX4-Autopilot` 순서로 탐색)
   - `configs/uxas_multi.xml` 상단 주석의 uxas 실행 경로 — 주석이라 동작에는 무관
8. **QGC 설정**: 새 호스트의 `QGroundControl.ini`는 건드릴 것 없음 — PX4 SITL이 14550으로 송신하므로 자동 검색됨 (명시적 UDP listener 등록 불필요, 아래 §4 참조)

### 2. 새 호스트에서 달라지는 것 (이 호스트의 제약 해제)

- `SIM_SPEED=0.5` 불필요 → **기본 1.0**으로 실행
- **멀티 vehicle 동시 비행 가능** → `--fleet mixed_small` 3대 동시 시험이 원래 목표였음
- GUI(Gazebo + QGC) 켠 채로 검증 가능

### 3. 작업 순서 (이어서 할 일)

1. **환경 재현 검증**: 아래 "즉시 재시작 시퀀스"로 단일 v1 자동 비행 체인이 새 호스트에서도 도는지 확인 (지난 세션에서 성공한 지점 재현)
2. ~~[1순위] AUTO.MISSION waypoint 미순회~~ → **2026-06-07 해결됨 (구 호스트에서 라이브 재현+수정+재검증 완료)**.
   원인은 mission 데이터가 아니라 **race 2개**:
   - **Race A (주범)**: bridge가 200 m AGL 통과 순간 AUTO.MISSION으로 전환하는데, 그 시점은 아직
     AUTO.TAKEOFF(목표 220 m)가 진행 중. takeoff 미완료 상태에서 모드를 뺏으면 PX4 navigator가
     mission을 즉시 finished 처리(mission_result.finished=True, seq_reached=-1, statustext 없음)하고
     AUTO.LOITER로 복귀. 임계 200 < 목표 220 구조라 **항상** 재현되는 결정적 버그였음.
   - **Race B**: upload 완료 직후 즉시 모드 전환하면 PX4 feasibility 재검사 전이라 LOITER로 bounce 가능.
   **수정 (qgc_uxas_bridge.py)**: ① `_takeover_loop`가 `mode != auto_takeoff`일 때만 활성화,
   ② 전환 후 5 s 내 auto_mission 정착 확인 + 미정착 시 1회 재명령(자동 복구), ③ STATUSTEXT /
   MISSION_CURRENT 로깅 + mission JSON 덤프(`logs/mission_dump_v*.json`) 추가.
   검증: v1 풀체인(ARM→TAKEOFF→cache→takeoff 종료 대기→upload→AUTO.MISSION→seq 0→1→2→3 순회) 무개입 성공.
   **부수 발견 2개**: (a) `launch_all.sh`의 mavlink_recorder가 bridge와 같은 UDP 14541에 바인딩해
   PX4 트래픽을 가로챔 → GCS heartbeat 끊겨 ARM 거부. bridge와 함께 쓸 때는 `RECORDER=0` 필수.
   (b) PX4 SITL dataman이 인스턴스 작업 디렉터리에 영속화돼 이전 세션 mission(21개)이 부팅 직후부터
   보임 — 무해하지만 로그 해석 시 혼동 주의.
3. ~~[2순위] Cessna(v4) 자동 ARM 실패~~ → **2026-06-07 해결됨 (풀체인 라이브 검증 완료)**.
   공중 spawn(z=300)은 애초에 불필요했음 — stock `4003_gz_rc_cessna` airframe에 `RWTO_TKOFF 1`
   (활주 이륙)이 기본이라 **지상 spawn + NAV_TAKEOFF**로 정상 이륙함. 적용한 변경:
   - `vehicles.json` v4 `spawn_pose.z = 300 → 0`
   - `launch_bridges.sh`: fixed_wing 지상 spawn(z<50) → multicopter와 동일한 `--auto-takeoff-agl 220` 경로
   - **FW mission feasibility 거부 해결**: PX4 고정익 기본값 `MIS_TKO_LAND_REQ=2`(착륙 패턴 필수)가
     UxAS waypoint-only mission을 invalid 처리(mission_result.valid=False, state=1 NO_MISSION).
     `vehicles.json` v4에 `MIS_TKO_LAND_REQ: 0` 추가 (우리 운영 모델: 착륙은 운영자가 QGC로)
   - **launcher 파라미터 갭 해결**: `launch_all.sh`는 vehicles.json `parameters`에서 SYS_AUTOSTART만
     소비하고 나머지는 PX4에 전달 안 함(FW_AIRSPD_*도 그동안 미적용이던 잠재 버그). bridge에
     `--px4-param NAME=VALUE` 추가 — PARAM_REQUEST_READ로 선언 타입을 읽고 그 타입으로 PARAM_SET
     (타입 불일치 시 PX4가 조용히 무시함). `launch_bridges.sh`가 vehicles.json에서 자동 주입.
   검증: 지상 spawn → ARM(자동재시도) → 활주 이륙 → 220 m 상승 → UxAS 10-wp mission →
   AUTO.MISSION → seq 0→8 전체 순회 → "Mission finished, loitering". 무개입 성공.
4. ~~[3순위] Cesium 3D 버튼 segfault~~ → **2026-06-07 해결됨**. 원인은 main.cc의 AppArmor sandbox
   자동 우회 코드(이미 작성돼 있었음)가 **구 바이너리(5/8 빌드)에 미포함**이었던 것. 재빌드 후
   env 변수 없이 Cesium 진입 + 토글 4회 검증 완료. 새 호스트에서는 §1의 QGC 빌드만 하면 끝
5. Tier 1-2/Tier 3 멀티 vehicle 확장 + 보고서 §6.7 갱신

---

## ★ 2026-05-29(2차) 돌파: 실제 비행 + 자율 체인 end-to-end 성공

단일 멀티콥터(v1)로 **전체 자율 비행 체인이 라이브로 동작 확인됨**:

```
Bridge auto-takeoff (ARM + NAV_TAKEOFF, 현재 좌표) → 0→220m AGL 실제 상승 (gz Z로 확인)
  → UxAS 자율 task 할당 → 21-waypoint MissionCommand
  → Bridge가 200m AGL 미만에서 cache → 200.1m 통과 시 자동 활성
  → Mission upload (21개 전부, thread-safe) → AUTO.MISSION 전환 (ack=0)
```

**이번 세션에서 고친 4개 결정적 버그 (모두 적용됨):**

1. **GCS heartbeat 누락** → `MAVLinkReader._heartbeat_loop` 추가 (1Hz MAV_TYPE_GCS). 없으면 PX4가 "Resolve system health failures first"로 arm 거부.
2. **takeoff 고도 AGL/AMSL 혼동** → `takeoff()`가 param7 = home_amsl + target_agl 로 변환. AMSL 절대고도라 home(30m) 안 더하면 "Already higher than takeoff altitude".
3. **NAV_TAKEOFF lat/lon=0** (가장 결정적) → 현재 좌표를 넣어야 함. 0,0이면 PX4가 적도로 해석해 모터를 안 돌리고 auto-disarm. `takeoff()`가 `s.lat_deg/s.lon_deg` 사용.
4. **lockstep Accel TIMEOUT** → `PX4_SIM_SPEED_FACTOR`(launch_all.sh의 `SIM_SPEED` env, 기본 1.0). 느린 호스트는 `SIM_SPEED=0.5`로 실행해야 센서 타임아웃/EKF "vertical velocity unstable" 회피. **단일 vehicle + headless(`gz sim -g` kill) + SIM_SPEED=0.5**가 이 호스트의 안정 조합.

**~~남은 refinement~~ → 2026-06-07 해결**: LOITER 복귀의 원인은 후보 (a)(b)(c) 모두 아니었고,
**AUTO.TAKEOFF 진행 중 모드 전환 race**였음 (상세는 최상단 ★★ §3-2). waypoint 좌표/구조는 덤프
검증 결과 정상(폴리곤 lawn-mower 패턴, 번호 1→21 선형 체인, 전 항목 relative 50 m).

**이 호스트 운영 핵심:** load가 평소 높음(uptime 32일). 비행 검증은 **단일 vehicle + GUI off + SIM_SPEED=0.5**로. 멀티 vehicle 동시 비행은 더 강한 호스트 필요(lockstep 센서 starvation).

---


다음 세션에서 이 문서를 가리키면 바로 컨텍스트가 복구되고 어디서부터 시작할지 명확해집니다.

## 상태 (2026-05-29 종료 시점)

- 모든 시뮬레이션 프로세스 정리 완료 (PX4 SITL, Gazebo, bridges, UxAS, QGC 모두 종료)
- 포트 14541/14542/14544/5560/5561/45679 모두 비어있음
- 코드/설정 변경은 **working tree에만** 존재 — git commit은 사용자 결정 대기

## 즉시 재시작 시퀀스 (멀티콥터 자동 비행 검증)

이 순서대로 4개 셸을 띄우면 새 세션에서 바로 다음 시험을 이어갈 수 있습니다.

```
# 셸 1 — UxAS 시작
mkdir -p /tmp/uxas_run && cd /tmp/uxas_run
cp /home/donghoon/myclaude/qgroundcontrol/tools/test-automation/configs/uxas_multi.xml ./cfg.xml
/home/donghoon/myclaude/OpenUxAS/obj/cpp/uxas -cfgPath cfg.xml -runUntil 7200
# 확인: ss -tlnp | grep -E '5560|5561'

# 셸 2 — PX4 SITL (Korea, mixed_small)
cd /home/donghoon/myclaude/qgroundcontrol/tools/test-automation
./scripts/launch_all.sh --fleet mixed_small
# "All PX4 instances launched" 메시지 뜰 때까지 대기 (~25s)

# 셸 3 — Bridges (auto-takeoff 활성)
cd /home/donghoon/myclaude/qgroundcontrol/tools/test-automation
./scripts/launch_bridges.sh --fleet mixed_small
# 콘솔에 multicopter는 "(auto-takeoff 220m)", fixed_wing은 "(auto-arm)"
# bridge_1/2 로그에 "ARM=True ack result=0 (OK)" + "TAKEOFF agl=220m ack=0 (OK)" 확인

# 셸 4 — QGC (선택, 시각화용)
DISPLAY=:0 QTWEBENGINE_DISABLE_SANDBOX=1 \
    /home/donghoon/myclaude/qgroundcontrol/build/Release/QGroundControl &
# (2026-06-07) 3D 버튼 segfault 해결됨 — 재빌드 바이너리에서 Cesium 3D 사용 가능

# Tier 1-1 발행 (멀티콥터 v1 area search)
cd /home/donghoon/myclaude/qgroundcontrol/tools/test-automation/scripts
python3 -u uxas_publish_task.py area \
    --vehicles 1 \
    --polygon 34.6105,127.2050 34.6130,127.2050 34.6130,127.2075 34.6105,127.2075 \
    --altitude 80 \
    --task-id 6001 --request-id 61001 \
    --with-operating-region 34.611670,127.206028,3000 \
    --zone-id 6001 --region-id 61001 \
    --wait-secs 305 \
    --out ../logs/scenarios/tier1_v1_NEXT.json
```

기대 시퀀스:

1. SITL → 3대 spawn (v1, v2 지상, v4 공중 300 m)
2. Bridge 시작 직후 v1/v2 → ARM → NAV_TAKEOFF(220 m) → 수직 상승
3. v4 bridge가 auto-arm 시도하지만 실패 가능성 높음 (Cessna는 다음 단계 별도 해결)
4. v1 alt_agl ≥ 200 m 통과 → bridge가 cached UxAS MissionCommand 자동 upload
5. AUTO.MISSION 모드 전환 → PX4가 21~25개 waypoint 추종 → QGC 지도에서 비행 확인

## 마지막으로 한 작업 (Task #20 / #26 — in_progress)

직전 추가한 6가지가 모두 working tree에 반영된 상태입니다 (Task #21~#25는 완료, #26 라이브 시험은 시작 직전 중지).

- `vehicles.json` v4 `spawn_pose.z = 300` (Cessna 공중 spawn)
- `qgc_uxas_bridge.py`:
  - `VehicleState.alt_agl_m` + `gps_fix` 필드
  - `_capture_types` / `_capture_queue` (thread-safe mission/command 응답 라우팅)
  - `MAVLinkReader.upload_mission` 재작성 (capture 큐 사용)
  - `MAVLinkReader.arm()`, `MAVLinkReader.takeoff(alt)`, `MAVLinkReader.set_mode_auto_mission()`
  - MissionCommand cache + 200 m AGL 자동 활성 로직
  - `_takeover_loop` (1 Hz) — auto-arm(고정익) + auto-takeoff(멀티콥터) + cached mission 활성
  - CLI: `--alt-takeover-agl`, `--auto-arm-on-start`, `--auto-arm-min-alt`, `--auto-takeoff-agl`
  - Camera + Gimbal payload를 AirVehicleConfiguration / AirVehicleState에 자동 첨부
- `launch_bridges.sh`: vehicle type+spawn_z로 자동 분기
  - `fixed_wing && z≥50` → `--auto-arm-on-start`
  - `multicopter` → `--auto-takeoff-agl 220`
- `uxas_publish_task.py`: `--with-operating-region`, `AutomationResponseInfo` Key/Value 디코딩
- `configs/uxas_multi.xml`: `Test_SimulationTime` + `MaxResponseTime_ms=300000`
- `configs/worlds/korea.sdf` + PX4 stock worlds에 symlink

## 미해결 이슈 (다음 세션 우선순위)

| 우선순위 | 항목 | 비고 |
|---|---|---|
| 1 | ~~멀티콥터 자동 비행 라이브 검증~~ | **2026-06-07 완료** — v1 풀체인 무개입 성공 (waypoint 순회 포함, ★★ §3-2 참조) |
| 2 | ~~Cessna 자동 ARM 실패~~ | **2026-06-07 해결** — 후보 (a)(b)(c) 전부 불필요. 지상 spawn + RWTO 활주 이륙 + `MIS_TKO_LAND_REQ=0` + bridge `--px4-param` (★★ §3-3 참조) |
| 3 | ~~QGC Cesium 3D 버튼 segfault~~ | **2026-06-07 해결**. 원인 = 구 바이너리에 main.cc sandbox 우회 미포함. 재빌드로 해소, 토글 검증 완료 |
| 4 | Tier 1-2 / Tier 3 자동 비행 확장 | 1번 끝나면 자연스럽게 |
| 5 | 보고서 `QGC_UxAS_MixedFleet_Report.md` §6.7 갱신 | 라이브 결과 추가 |

## 핵심 디자인 결정 (잊지 말 것)

- **200 m AGL takeover**: UxAS plan은 200 m 이상에서만 활성. 멀티콥터 takeoff는 220 m까지 (마진 20 m). 운영자가 QGC에서 LAND 트리거하면 alt < 200 m로 떨어져 자동으로 cache 모드로 복귀.
- **공중 spawn (Cessna)**: 활주로 없이 fixed-wing 시연하려는 의도. 현재 미해결.
- **Camera + Gimbal LMCP**: `AreaSearchTask` plan에는 sensor footprint 필수. 모든 vehicle에 PayloadID = 100·id+1 (gimbal), 100·id+2 (camera), HFoV discrete [45, 22, 7.6, 3.7, 0.63, 0.11]°, 1024×768.
- **MAVLink connection thread-safety**: `_capture_types` flag로 `_read_loop`가 protocol 응답을 큐로 우회. control 메서드 `arm/takeoff/upload_mission/set_mode_auto_mission` 모두 동일 패턴.
- **ID 규칙 5곳 일치**: `vehicles.json id` = PX4 `-i N` = MAVLink target_system (= id+1) = bridge `--vehicle-id` = UxAS `WaypointPlanManagerService VehicleID`.

## QGC (Add3Dmap 브랜치) 측 코드 변경

이번/이전 세션에서 QGC 코어에도 5개 영역의 변경이 들어가 있다 — 빌드 산출물 `build/Release/QGroundControl`에 포함됨.

### 1. EventBroadcaster (UI 자동화 백도어)

| 파일 | 역할 |
|---|---|
| `src/Utilities/EventBroadcaster.{cc,h}` 신규 | `QML_SINGLETON` UDP broadcaster + receiver. **UDP 45678** 로 QGC UI 이벤트 JSON broadcast, **UDP 45679** 로 외부 명령 receive |
| `src/Utilities/CMakeLists.txt` | EventBroadcaster.cc/h 추가 |
| `src/FlyView/GuidedActionsController.qml` | `EventBroadcaster.sendEvent("action", ...)` 호출, `Connections { target: EventBroadcaster }` 에서 `onCommandReceived(action, params)` 받아 `executeAction(...)` dispatch (arm/disarm/takeoff/land/rtl/start_mission/pause/set_mode) |
| `src/UI/toolbar/SelectViewDropdown.qml` | 뷰 전환 시 `EventBroadcaster.sendEvent("view", "fly_view"\|"plan_view"\|...)` |

**송신 JSON** (UDP 45678):
```
{"seq":12,"timestamp":1714967600.123,"category":"action","event":"arm_clicked","data":{"vehicle":1}}
```

**수신 JSON** (UDP 45679):
```
{"action":"arm"}                           {"action":"land"}
{"action":"takeoff","altitude":15}         {"action":"rtl"}
{"action":"set_mode","mode":"posctl"}      {"action":"start_mission"}
```

**주의**: receive 측은 QGC active vehicle 1대에만 적용 (`_activeVehicle` 체크) — multi-vehicle은 vehicle 전환 메커니즘 추가 필요. 우리 자동 비행에서는 EventBroadcaster를 *우회*하고 bridge가 MAVLink로 직접 control 명령(arm/takeoff/set_mode) 송신하는 방식을 채택.

### 2. WebEngine sandbox 우회 (Linux 전용)

`src/main.cc:25-37` — `QtWebEngineQuick::initialize()` 직전에 `QTWEBENGINE_DISABLE_SANDBOX=1`을 자동 set.

**이유**: Ubuntu 24.04+의 `kernel.apparmor_restrict_unprivileged_userns=1` 정책이 사용자 경로(`~/Qt/`)에서 빌드된 `QtWebEngineProcess`에 AppArmor 프로필이 없어 sandbox user namespace 생성 실패 → Cesium 3D 띄울 때 GPU/렌더러 프로세스가 segfault.

```cpp
#ifdef QGC_CESIUM3D_ENABLED
#ifdef Q_OS_LINUX
    if (!qEnvironmentVariableIsSet("QTWEBENGINE_DISABLE_SANDBOX")
        && !qEnvironmentVariableIsSet("QTWEBENGINE_CHROMIUM_FLAGS")) {
        qputenv("QTWEBENGINE_DISABLE_SANDBOX", "1");
    }
#endif
    QtWebEngineQuick::initialize();
#endif
```

### 3. Cesium 3D 통합 (Add3Dmap 브랜치 베이스)

이번 세션에서 만든 게 아니라 브랜치에 이미 있던 작업이지만, 자동 비행 시연과 같이 평가해야 하는 부분:

| 파일 | 내용 |
|---|---|
| `src/Viewer3D/Viewer3DQml/Cesium3DView.qml` | `WebEngineView` + `WebChannel` 기반. vehicle position/heading/altitude를 2 Hz로 JS에 push, 미션 waypoint/홈/궤적 시각화, 우클릭 컨텍스트 메뉴(goto/orbit/ROI/sethome) |
| `src/Viewer3D/Viewer3DQml/Cesium3DView.html` | Cesium.js CDN, 라우팅 핸들러 (`initCesium`, `updateVehiclePosition`, `setMissionItems`, `flyToLocation`, ...) |
| `src/Viewer3D/Viewer3DManager.{cc,h}` | `DisplayMode` enum에 `Cesium3D=2` 추가, `cesiumToken` 설정 연동 |
| `src/Viewer3D/Viewer3DQml/Viewer3DShowAction.qml` | Toolstrip 액션: token 있으면 "Cesium 3D" / 없으면 "3D View" |
| `src/FlyView/FlyView.qml` | `cesium3DLoader` (active when `_isCesium3DMode`) — `Cesium3DView.qml`을 동적 로드 |
| `src/Viewer3D/CMakeLists.txt`, `src/CMakeLists.txt` | `Qt6::WebEngineQuick` 링크, `QGC_CESIUM3D_ENABLED` 컴파일 플래그 |
| `CMakeLists.txt` | Qt6 components에 `WebEngineQuick` 추가 |

**~~미해결 이슈~~ → 2026-06-07 해결됨**: 3D 버튼 클릭 시 `exit 139 (SIGSEGV)`의 원인은 QML/JS가 아니라
**§2의 AppArmor sandbox 문제 그 자체**였다. main.cc의 자동 우회 코드는 5/29 세션에 working tree에
추가됐지만 당시 바이너리(5/8 빌드)에는 컴파일돼 있지 않았던 것. gdb로 확인한 사실:
- env 없이 구 바이너리 실행 + 3D 클릭 → `libQt6WebEngineCore` 내부 SIGSEGV 재현
- `QTWEBENGINE_DISABLE_SANDBOX=1` 설정 시 → Cesium 지구본 정상 렌더링
- 재빌드 후 env 없이 → 정상 (자동 우회 작동), Cesium↔Map 토글 반복에도 안정 (Loader 생명주기 문제 없음)
- 한때 의심했던 빈 토큰 `initCesium("")`은 무관 (토큰 없으면 버튼이 Cesium3D 모드로 진입 자체를 안 함)

**2026-06-07 Cesium 3D 개선 (현재 위치 + VWorld 한국 지도)**:
- **현재 위치로 열림**: 기존엔 카메라가 한국 상공 5000 km 고정이라 "3D가 안 되는 것처럼" 보였음.
  `Cesium3DView.qml`이 `QGroundControl.flightMapPosition`/`flightMapZoom`(2D 지도가 보던 중심/줌)을
  읽어 `initCesium(token, vworldToken, lon, lat, height)`로 전달, -55° 피치로 같은 곳을 비춤
  (zoom→height 변환 `_zoomToHeight`). 차량 연결 시엔 기존 fly-to-vehicle 타이머가 그 위로 덮어씀.
- **VWorld 한국 지도 (브이월드)**: `addVWorldLayers(token)`이 VWorld WMTS를 Cesium imagery 레이어로
  추가 — Satellite(고해상 위성 jpeg) + Hybrid(도로/한글 지명 png). Web Mercator·XYZ라
  `WebMercatorTilingScheme` 그대로 매칭, `rectangle`을 한국(123~132.5E, 32~39.5N)으로 제한해
  영역 밖 404 방지(밖은 Cesium World Imagery로 폴백). VWorld는 `Access-Control-Allow-Origin: *`라
  WebGL 텍스처 CORS 통과 확인. 검증: 고흥 시뮬 지역에 한글 라벨("고흥호")까지 정상 렌더.
- **VWorld 토큰 설정** (코드는 `appSettings.vworldToken`에서 읽음 — 키 자체는 repo에 커밋 안 함):
  QGC `Settings → General → VWorld`에 입력하거나 `QGroundControl(.Daily).ini [General]`에
  `vworldToken=<키>` 추가. 이 토큰은 2D 지도의 VWorld 공급자에도 그대로 쓰임. 키 발급: vworld.kr
  무료 가입 → 인증키 발급(도메인 `localhost` 등록). 우리 키는 별도 보관처 참조.

**2026-06-07 VWorld 3D 건물 (extrude)**:
- 3D 버튼 → 카메라가 머무는 지점의 **VWorld 건물 footprint를 가져와 층수만큼 입체로 세움**.
  `Cesium3DView.html`의 `loadVWorldBuildings`(camera `moveEnd`마다, debounce 400 ms)가 화면
  중앙 ray가 지면에 닿는 지점 주변 박스를 만들고, `renderVWorldBuildings`가 `LT_C_SPBD`
  feature를 polygon `extrudedHeight = gro_flo_co × 3.3 m`로 추출. 지형에 묻히지 않게
  `heightReference: CLAMP_TO_GROUND` + `extrudedHeightReference: RELATIVE_TO_GROUND`.
  검증: 강남 1735 m 뷰에서 1000동 입체 렌더(아파트 단지·블록), QGC와 같은 위치.
- **결정적 제약 2개 (디버깅으로 확정)**:
  1. **CORS**: VWorld 타일(WMTS)은 `Access-Control-Allow-Origin: *`를 보내지만 **Data API(벡터)는
     CORS 헤더가 없음** → qrc: 페이지의 `fetch()`가 차단됨. 그래서 건물 요청은 HTML이
     `qgcBridge.requestBuildings(w,s,e,n)`로 위임하고 **QML의 `XMLHttpRequest`**(CORS 비적용)가
     받아 `webView.runJavaScript('renderVWorldBuildings(...)')`로 되돌려줌.
  2. **geomFilter 면적 ≤ 10 km²**: 박스가 넘으면 `INVALID_RANGE`로 0건 반환. 피치 카메라의
     `computeViewRectangle`는 지평선까지 부풀어 27 km²가 나오므로 사용 금지. 대신 화면 중앙
     look-at 지점 ± margin(`camHeight×6e-6`, 0.004~0.014°, lat 37.5에서 ~7.6 km²) 박스를 씀.
- **벡터 데이터 가용 레이어** (같은 Data API, 이 키로 확인): 건물 `LT_C_SPBD`(층수 `gro_flo_co`),
  도로 `LT_L_MOCTLINK`(MultiLineString + `road_name`), 하천 `LT_C_WKMSTRM`(MultiPolygon + `riv_nm`),
  지적 `LP_PA_CBND_BUBUN`.

**2026-06-07 VWorld 도로/하천 3D 표시 + OpenUxAS 탐색 연동**:
- **3D 표시**: `requestBuildings`를 범용 `requestVWorldLayer(layer, w,s,e,n, jsCallback)`로
  일반화(QML). HTML이 view 박스마다 건물/도로/하천을 함께 요청 — 도로는
  `renderVWorldRoads`(노란 `clampToGround` polyline), 하천은 `renderVWorldRivers`(파란 반투명
  `ClassificationType.TERRAIN` polygon). 강남에서 도로망 노란선 + 건물 동시 렌더 확인.
- **OpenUxAS 연동**: `scripts/vworld_uxas_search.py` — VWorld 도로/하천 geometry를 가져와
  단순화한 뒤 `uxas_publish_task.py`에 `line`(도로 → LineSearchTask) / `area`(하천 →
  AreaSearchTask)로 넘김. WaterwaySearch와 동일 구조를 **실제 한국 지도 데이터**로.
  - `road`: 도로 세그먼트를 bbox 클립 → greedy 체이닝으로 1개 sweep path → LineSearchTask
  - `river`: 하천 폴리곤을 bbox 클립(**Sutherland-Hodgman** 필수 — VWorld `한강`은 전국 단위
    폴리곤이라 클립 안 하면 점이 강원/충북까지 퍼짐) → AreaSearchTask
  - bbox는 VWorld 10 km² 제한 내(≈0.03°/변). `--dry-run`으로 점만 출력. `VWORLD_KEY` env 사용.
  - 예: `python3 vworld_uxas_search.py river --vehicles 4 --name 한강 --bbox 37.515,126.945,37.530,126.965 --with-operating-region 37.52,126.955,4000`
  - dry-run으로 도로(40pt sweep)·하천(30pt 영역, 클립 정상) 검증 완료. 라이브 비행은 풀스택 필요.

**2026-06-07 QGC에서 임무 계획 (3D 우클릭 → UxAS 탐색)**:
- **흐름**: QGC 3D 뷰 우클릭 → 컨텍스트 메뉴 `UxAS: area/road/river search here` →
  `EventBroadcaster.sendEvent("uxas_search", kind, {center_lat, center_lon, vehicles,
  altitude, half_size_m, region_radius})` (UDP 45678 방송) → `uxas_search_listener.py`가
  받아 클릭 지점 중심의 폴리곤(area)/bbox(road·river)를 만들어 `uxas_publish_task.py` /
  `vworld_uxas_search.py`로 발행 → UxAS가 차량 배정 → bridge가 비행.
- **파일**: `Cesium3DView.qml`(메뉴 3항목 + `_sendUxasSearch` + 임무 기본값 프로퍼티
  `uxasVehicles`/`uxasAreaAltitude`/...), `scripts/uxas_search_listener.py`(신규).
- **실행**: UxAS+SITL+bridge 띄운 뒤 `VWORLD_KEY=<키> python3 uxas_search_listener.py`.
  그러면 운영자가 QGC 3D에서 우클릭만으로 임무를 발행. (road/river는 VWORLD_KEY 필요.)
- **검증 상태**: 리스너는 UDP 이벤트 → 올바른 발행 명령 생성까지 end-to-end 검증됨(수동 UDP
  송신). 3D 우클릭→방송 링크는 기존 검증된 EventBroadcaster 경로(GuidedActionsController와
  동일 패턴)를 사용 — 단, **합성 입력(XTEST)이 내장 Chromium 우클릭으로 등록되지 않아
  헤드리스 자동화로는 메뉴 클릭을 못 띄움**. 실제 마우스로는 동작 예상이나 라이브 확인 권장.
- **확장 여지**: 현재는 클릭 지점 중심 고정 박스. 폴리곤 직접 그리기/도로명 선택/파라미터
  다이얼로그는 추후. river는 박스 내 최대 하천 폴리곤 사용(이름 무관).

### 4. QGC 사용자 설정 (`~/.config/QGroundControl/QGroundControl.ini`)

`[LinkConfigurations]`에서 명시적 UDP listener들(Link1/2/3 = 14541/14542/14544)을 **제거**했음. PX4 SITL이 normal MAVLink를 14550으로 모두 송신해 QGC가 자동 검색하므로 명시 등록 불필요. sys_id 2/3/5로 vehicle 구분.

```
[LinkConfigurations]
Link0\auto=false ... (Serial ttyACM0, 사용 안 함)
count=1
```

### 5. QGC 빌드

기존 빌드 산출물 `build/Release/QGroundControl` (Mar 30 빌드) 그대로 사용. 위 모든 변경이 컴파일된 상태. 만약 main.cc / EventBroadcaster를 더 손보면:

```
cd /home/donghoon/myclaude/qgroundcontrol
cmake --build build --config Release --parallel
```

---

## Git 상태 (working tree 변경)

수정/추가된 파일 (commit 안 됨):
```
# QGC 코어
modified:  src/main.cc                              # §2 WebEngine sandbox bypass
modified:  src/FlyView/GuidedActionsController.qml  # §1 EventBroadcaster receive/send
modified:  src/UI/toolbar/SelectViewDropdown.qml    # §1 view 전환 broadcast
modified:  src/Utilities/CMakeLists.txt             # §1 EventBroadcaster 빌드
new:       src/Utilities/EventBroadcaster.{cc,h}    # §1 UDP 45678/45679 brokers
# (그 외 src/Viewer3D/, src/FlyView/FlyView.qml 등 Cesium 3D 통합은 이미 commit된 브랜치 베이스)

# test-automation
modified:  tools/test-automation/configs/vehicles.json  # v1.1 + Korea + Cessna spawn z=300
modified:  tools/test-automation/scripts/qgc_uxas_bridge.py  # 모든 자동화
modified:  tools/test-automation/scripts/launch_all.sh       # JSON-driven
new:       tools/test-automation/scripts/launch_bridges.sh
new:       tools/test-automation/scripts/uxas_publish_task.py
new:       tools/test-automation/configs/uxas_multi.xml
new:       tools/test-automation/configs/worlds/korea.sdf
new:       tools/test-automation/docs/{QGC_TestAutomation_Manual,QGC_UxAS_MixedFleet_Report}.{md,pdf}
new:       tools/test-automation/docs/build_pdf.py, build_report_pdf.py
new:       tools/test-automation/docs/SESSION_HANDOFF.md  ← 본 문서
new (gitignored): tools/test-automation/lmcp_py/   # LmcpGen 생성물
new (gitignored): tools/test-automation/logs/      # 라이브 캡처
```

## 환경 의존성

- PX4-Autopilot: `/home/donghoon/distiledUAV/PX4-Autopilot` (build/px4_sitl_default 빌드 완료)
- OpenUxAS: `/home/donghoon/myclaude/OpenUxAS/obj/cpp/uxas` (release build)
- LmcpGen: `/home/donghoon/myclaude/OpenUxAS/infrastructure/sbx/x86_64-linux/lmcpgen/install/LmcpGen.jar`
- QGC: `/home/donghoon/myclaude/qgroundcontrol/build/Release/QGroundControl` (Add3Dmap 브랜치)
- Gazebo: Harmonic 8.10
- Python: pymavlink, pyzmq, pyyaml, markdown, weasyprint 모두 설치됨
- korea.sdf symlink: `/home/donghoon/distiledUAV/PX4-Autopilot/Tools/simulation/gz/worlds/korea.sdf` → 우리 `configs/worlds/korea.sdf`
