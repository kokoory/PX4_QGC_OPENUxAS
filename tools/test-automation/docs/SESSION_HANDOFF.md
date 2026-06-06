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
2. **[1순위] AUTO.MISSION waypoint 미순회 디버그**: 업로드는 정상(21개)인데 즉시 LOITER 복귀(MISSION_CURRENT state=5 COMPLETE). → `waypoints_from_mission_command` 출력 좌표를 덤프해 실제 폴리곤 순회 경로인지 검증, 첫 item/고도(80m vs 현재 220m) 정리. 상세는 아래 "남은 refinement"
3. **[2순위] Cessna(v4) 자동 ARM 실패** — 해결책 후보는 "미해결 이슈" 표 참조
4. **[3순위] Cesium 3D 버튼 segfault** (exit 139) — §3 디버그 후보 참조
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

**남은 refinement (다음 세션 1순위):**
- AUTO.MISSION 진입 후 vehicle이 LOITER로 복귀(MISSION_CURRENT state=5 COMPLETE)하며 waypoint를 실제로 순회하지 않음. MISSION_COUNT=21은 정상 업로드됨. 원인 후보: (a) 업로드한 21개 waypoint 고도(80m)가 현재(220m)와 큰 차이 + 첫 item이 NAV_TAKEOFF가 아니라 바로 NAV_WAYPOINT, (b) seq=0를 current로 올렸는데 PX4가 즉시 reached 처리, (c) UxAS waypoint 좌표/구조 확인 필요. → `waypoints_from_mission_command` 출력 좌표를 덤프해서 실제 폴리곤을 순회하는 경로인지 검증하고, 필요하면 mission 첫 항목/고도 정리.

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
# 주의: 3D 버튼 누르지 말 것 — 현재 segfault (별도 디버그 항목)

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
| 1 | **멀티콥터 자동 비행 라이브 검증** | 코드 작성 완료, 실 시험 직전 종료. 위 시퀀스로 즉시 가능 |
| 2 | **Cessna 자동 ARM 실패** | spawn z=300으로도 PX4 GPS lock 전에 추락 → `ARM ack result=1 (FAIL)`. 해결책 후보: (a) gz_standard_vtol로 변경, (b) korea.sdf에 활주로 모델 추가, (c) PX4 GPS lock 가속 파라미터 |
| 3 | **QGC Cesium 3D 버튼 segfault** | `exit code 139`. WebEngineView 또는 Cesium ion 토큰 관련. `/src/Viewer3D/Viewer3DQml/Cesium3DView.qml`에서 디버그 필요 |
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

**미해결 이슈**: 3D 버튼 클릭 시 QGC `exit 139 (SIGSEGV)`. WebEngineView 생성 시점 또는 Cesium ion 토큰 미설정 상태의 JS 에러로 추정. 다음 세션 디버그 후보:
- 토큰이 빈 문자열일 때 `initCesium("")` 호출하는 부분 (`Cesium3DView.qml:39`) — 빈 토큰 가드 추가
- WebEngineView 부모 destruct 타이밍 — `cesium3DLoader.onActiveChanged`에서 setSource 호출 패턴이 안전한지

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
