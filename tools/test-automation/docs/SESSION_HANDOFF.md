# 세션 핸드오프 노트

## ★★★★★★★ 2026-06-27: QGC 양방향 스크립트화(EventBroadcaster 녹화/재생) — 자체검증 완료

### 한 일
QGC를 EventBroadcaster로 **완전 양방향 스크립트화**. 인프라는 이미 있었고(아래) 재생 경로 + 외부도구 추가.
- **아웃바운드(이미 있음)**: `GuidedActionsController.executeAction`이 모든 가이드 버튼을 `sendEvent("action", 이름, {actionCode, sliderValue,…})`로 45678 송출. + 미션(`qgc_mission`)/MAVLink(`mavlink`).
- **인바운드(강화)**: 45679 명령 → `onCommandReceived` 실행. **`actionCode` 경로 추가** → 녹화된 어떤 버튼이든 그대로 재생(8개 명령 제한 없음).
- **외부 도구 3개 신규**(`tools/test-automation/scripts/`): `eb_monitor.py`(45678 시간순 뷰 + `--save` 녹화, seq dedup), `eb_replay.py`(녹화 .jsonl → 45679 재생, 원타이밍, actionCode), `eb_scenario.py`(손작성 시나리오, `scenarios/demo.json`).

### 자체검증 ✅ (사람 없이 클로즈드 루프)
`eb_scenario --action rtl` → 45679 → QGC 실행(bridge "RTL: start return at 139m") → 45678로 `action/rtl` echo 캡처 → `eb_replay`로 캡처 재생 → bridge "RTL: start return→completed, loitering" = 차량 재반응. **명령→실행→리턴값→녹화→재생→재실행 전부 동작.**

### QGC 추가 기능 전체 정리 → `docs/QGC_FEATURES_ADDED.md` (신규)

### ⚠️ GitHub 미반영 (중요)
**이 세션들(06-11/06-17~18/06-27) 작업 전부 미커밋.** HEAD는 origin과 0/0(마지막 커밋 `aff133926`은 이 작업들 이전). **아직 GitHub에 아무것도 안 올라감** → 커밋/push 필요.

### 06-27 후반: 패널 양방향(테스트카드) + River LineSearch + FOV + 멀티비행체 + 이종 SAR

**A. 패널 완전 양방향 (테스트카드 시나리오)** — 아웃: 패널 모든 컨트롤이 `sendEvent("uxas_ui", field, {value})`. 인: `FlyViewMap._uxHandleCommand` + `Connections{EventBroadcaster onCommandReceived}` → **`ux_<field>` 명령으로 패널 구동**(`_uxReplaying` 가드). 도구: **`eb_monitor_gui.py`(신규 GUI 창)**, `eb_replay.py` uxas_ui→`ux_` 매핑, `scenarios/han_river_card.json`(예시).

**B. River = LineSearch** — `vworld_multi_search.py`: `_river_centerline()`(PCA) + 모드 center/bank/area, **박스 안 조각 전부 합쳐 연속 중심선**. 패널 River 드롭다운(`_uxRiverMode`), listener `--river-mode` 전달.

**C. FOV 자동+수동** — 패널 `_uxFovDeg`(Wide45/Detail20/Custom). **bridge가 파일에서 읽음**: `_camera_fov_now(vid)` → `/tmp/uxas_camera_fov_<vid>.txt`(기종별) → 공용 → 기본. listener가 발행 fov를 파일에 씀. ⚠️ **버그 교훈: bridge가 45678 직접 바인딩하면 SO_REUSEPORT 경합으로 listener가 publish 못 받음(멀티 bridge일 때 강검색 깨짐) → 파일 기반 전환. bridge는 45678 바인딩 금지.**

**D. 멀티 비행체** — 풀: X500=[1,2,3,8,9], Cessna=**[4,11]**, Rover=[7]. `launch_all.sh --ids 1,2,4,11` + `launch_bridges.sh --ids 1,2,4,11`. spawn 0~300m(EKF 안전).

**E. 이종 SAR (핵심 신규)** — `uxas_sar_search.py`(신규): 한 region을 기종별 티어로, **단일 AutomationRequest**. 고정익=전체 AreaSearch 고고도(250)+넓은FOV, 멀티콥터=core(45%) 저고도(60)+좁은FOV, 지상체=도로 LineSearch. 각 작업 **EligibleEntities** 제한. 기종별 NominalAltitude 재등록 + 차량별 FOV파일. **OperatingRegion 필수**(없으면 "Not Ready"→0 command). listener `kind=="sar"` 브랜치. 패널 "── Heterogeneous SAR ──" 섹션 + `_uxPublishSar()` + 인바운드 `ux_publish_sar`/`ux_rover`/`ux_fw_alt`… ✅ 4대(FW 4,11/MC 1,2) 전부 미션 생성 검증.

**F. 기타** — 패널 클릭 통과 차단(uxasPanel MouseArea). 헤딩=선회 크랩(정상). 패널 폭: SAR FW/MC 두 줄 분리.

**셸 주의**: 이 환경 Bash는 `pkill`/`pgrep` nonzero시 set -e로 **복합명령 중단** → launch는 **단독 명령**으로.

---

## ★★★★★★ 2026-06-17~18: Cessna 강검색 + 3D 비행체 헤딩(최종 해결) + VWorld 토글 + Road/River 영역지정

### 이번 세션 요지
06-11 작업(아래 ★★★★★) 이어서, **고정익 Cessna로 강남 도로/강 검색** + QGC 3D/툴바/패널 다수 개선. **미커밋 변경 매우 많음(아직 commit 안 함).**

### 새/수정 기능 (전부 빌드·동작, 일부 미검증)
1. **3D 비행체 헤딩 — 최종 해결**(여러 시행착오 끝): `Cesium3DView.html` 비행체를 **polyline 화살표 + Cesium `CallbackProperty`** 로. → ① 월드방향(카메라 돌려도 헤딩 정확) ② **CallbackProperty라 매 프레임 재생성 없음→깜박임 없음** ③ polyline이라 **CDN 폴리곤 worker 불필요**(폴리곤은 `createPolygonGeometry.js` CDN fetch 실패로 3D 뷰가 깨졌었음). 교훈: Cesium에서 매 틱 갱신되는 지오메트리는 CallbackProperty로.
2. **3D 고도 프레임 통일**: 비행체·plan점·plan선·라벨을 전부 **절대 AMSL**로. `_groundAmsl`을 매 틱 **차량 AMSL−AGL**로 라이브 계산(home 신호는 뷰 로드 시 안 와서 0이었음). QML이 `altitudeAMSL`도 HTML로 전달.
3. **VWorld 레이어 토글**: 상단 툴바(`FlyViewToolBarIndicators.qml`)에 **`VWorld: Bldg/Road/River`** 체크박스. 상태는 `EventBroadcaster`의 bool 3개(`showVWorldBuildings/Roads/Rivers`). Cesium3DView가 변화를 HTML `setVWorldLayerVisible()`로 전달 → 3D 건물/도로/강 show/hide.
4. **Road/River 영역지정**(NEW, 이번 세션 마지막, **미검증**): 영역 도형(Region: Rectangle W/H · Polygon draw)을 **전 탭 공유**로. Road/River scan이 **뷰포트→그린 영역 bbox** 사용(`_uxRegionBbox`), 발행에 `region` 폴리곤 포함. **사각형은 기존 bbox 클립으로 정확**, **폴리곤은 아직 그 bbox로만 클립**(정밀 폴리곤 클립 = Python 후속 TODO).
5. **2D 패널 추가**(06-11에 이어): 헤더 **드래그 이동**(`⠿ UxAS Plan`, margin式·앵커유지), **리사이즈 그립**(`_uxScale`), **초록 풋프린트 토글**, **기체타입 인지 커버리지**("need N Cessna"), **카메라 Overlap %**.
6. **미션 브로드캐스트**: `MissionController` 웨이포인트 추가→`qgc_mission/waypoint_add`, 업로드→`qgc_mission/upload`를 EventBroadcaster(UDP 45678 + Monitor)로.
7. **(A) 외부 미션변경 자동 재다운로드**: `MissionManager::_handleMissionCurrent`가 `MISSION_CURRENT.total != 로컬 count`면 `loadFromVehicle()`(distinct total당 1회). → bridge가 미션 올리면 QGC 번호 웨이포인트 자동 갱신.
8. **Message Monitor 오토스크롤**: `onCountChanged: Qt.callLater(positionViewAtEnd)`.
9. **새 publish 시 이전 plan-mirror 클리어**: `EventBroadcaster.clearUxasPlannedWaypoints(0)`.

### Cessna 고정익 강남 검색 (동작)
- `RECORDER=0 bash scripts/launch_all.sh --ids 4` (recorder 경합 영구 회피) → Cessna 자동이륙(60m).
- **고정익 미션 착륙 필수**: bridge `_activate_mission`이 끝에 **NAV_LAND 자동 추가**, 단 **진행방향으로 D=alt/tan(6°) 밀어 완만 활공**(마지막wp 바로 위면 활공각>8°로 PX4 거부).

### 알아둘 것 (함정/설정)
- **`guidedMaximumAltitude` 기본 121.92m(=400ft)** 가 QGC "Change Altitude" 슬라이더 상한. 더 올리려면 Application Settings→Fly View에서 값 변경(또는 `FlyView.SettingsGroup.json` 기본값).
- **QGC를 빌드 끝나기 전 띄우면 옛 바이너리** → `ps -eo pid,lstart`의 시작시각 > 바이너리 mtime 확인.
- 빌드 실패 흔한 원인: QML "Property value set multiple times"(동일 시그널 핸들러 중복, 예 `onCesiumReadyChanged` 두 번).

### 미검증 ❌ (다음 세션)
- **Road/River 영역지정** 실제 발행→비행 검증(한강 중간 사각형). ← 가장 최근, 안 해봄.
- 폴리곤 영역 **정밀 클립**(Python `_clip_line/ring_to_polygon` 추가).
- VWorld 토글이 3D에서 실제 show/hide 되는지(빌드는 됨).
- 미커밋 변경 **커밋 정리**.

---

## ★★★★★ 2026-06-11: 강남 이전 + 2D패널 3D동등화 + Cessna 도로검색 + 미션 브로드캐스트/자동재싱크 + OpenUxAS 예제02 착수

### 위치/환경
- **home을 고흥→강남역(37.4979, 127.0276, alt 38)로 변경** (`vehicles.json` defaults + `korea.sdf` spherical + `fetch_amase_overlay.py` DEFAULT_CENTER). 한강이 바로 옆이라 강 검색 테스트에 좋음.
- **recorder/bridge 14541·14544 포트경합 영구해결책: `RECORDER=0 bash scripts/launch_all.sh ...`** (launch_all의 RECORDER env=0이면 recorder 안 띄움). 더는 recorder kill 안 해도 됨.

### 어제 미검증 항목 검증 완료
- **강(한강) AreaSearch ✅** — 단, **스캔 박스가 강 중심에 와야** 함(남쪽 강변에 걸치면 수면 폴리곤이 얇게 잘려 13wp 엉성; 강 위면 44~89wp 정상 잔디깎기). 반복 inject로 **UxAS에 stale task 쌓이면 미션 thrash** → UxAS 재시작. [[uxas-bridge-pipeline-gotchas]]
- **3D Cesium 뷰 ✅**, **Message Monitor Bridge-tap(45680) 행 ✅** (UxAS↔Bridge/Plan/MAVLink 다 실시간). **Replay는 사용자가 설계 변경 예정 → 보류**.
- **AMASE**: listener가 AirVehicleState+MissionCommand를 5555로 forward하도록 추가(`_loop`에 AirVehicleState/Config 케이스). AMASE GUI는 떴으나 **사용자가 AMASE 자체를 드롭**(맵 검정=배경타일 없음, 차량은 forward로 표시 가능).

### Part A — 패널 Alt가 실제 비행고도 제어 (검증 완료 ✅)
- UxAS 경로계획기는 **차량 NominalAltitude**로 웨이포인트 고도를 잡음(task의 search alt 무시). → `uxas_publish_task.register_vehicles_from_config(..., altitude=)` override 추가, `vworld_multi_search`/`uxas_publish_task`가 발행 altitude 전달. 검증: alt=120 주면 wp 120m(이전 50 고정 해결). **listener는 발행마다 subprocess라 재시작 불필요**.

### Part B — 2D UxAS 패널을 3D와 완전 동등화 (`FlyViewMap.qml`)
- 탭 **Area/Road/River**, X500/Cessna 카운트→Vehicle IDs, Altitude, **Sensor(Wide45/Detail20)**, 커버리지 readout, Area Shape(Rect W/H + Polygon 지도클릭 draw), Set vehicles, Publish. 문구도 3D와 동일.
- **"안 보이던 글씨" = 패널이 좁아 행 우측이 화면밖 잘림** → 폭 26→32, Altitude/Sensor 행 분리.
- 추가: **초록 풋프린트 on/off 토글**(`showCameraCoverage` 체크박스), **리사이즈 그립**(좌하단, `_uxScale`), **헤더 드래그 이동**(`⠿ UxAS Plan`, margin 조정式이라 앵커 유지·더블클릭 리셋), **기체타입 인지 커버리지**(X500/Cessna → "need N Cessna", 속도 다름), **카메라 Overlap % 설정**(`_uxOverlapPct`, 커버리지 계산+발행 페이로드 반영).

### Cessna(고정익 id4) 강남 도로/Area 검색 ✅
- **고정익 자동이륙 검증됨**(bridge `--auto-takeoff-agl 60`, 60m까지 활주이륙 후 Hold).
- **고정익 미션은 착륙 지점 필수** → bridge `_activate_mission`이 끝에 **NAV_LAND 자동 추가**. 단 마지막wp 바로 위면 활공각>8°로 거부 → **진행방향으로 D=alt/tan(6°) 밀어 완만 활공**으로 배치. AUTO.MISSION ack=0 OK 확인.
- bridge `--vehicle-id`는 UxAS ID이고 PX4 MAV_SYS_ID와 무관(매핑). MAV_SYS_ID는 1~255 한계.

### QGC 코어 변경 (전부 빌드·검증됨)
- **미션 브로드캐스트**: `MissionController` waypoint 추가→`qgc_mission/waypoint_add`, MISSION 업로드(sendToVehicle)→`qgc_mission/upload`를 EventBroadcaster로 송출(UDP 45678 + Message Monitor). 외부 프로그램이 듣고 나중에 시퀀스 구동 가능.
- **(A) 외부 미션변경 자동 재다운로드**: `MissionManager::_handleMissionCurrent`가 `MISSION_CURRENT.total != 로컬 count`면 `loadFromVehicle()` (distinct total당 1회, 루프방지 `_lastExternalReloadTotal`). → bridge가 미션 올리면 QGC 번호 웨이포인트가 현재 미션으로 자동 갱신(이전 것 안 남음).
- **3D 비행체 헤딩**: `Cesium3DView.html` billboard(화면향)→**월드 방향 화살표 폴리곤**(`_vehicleArrowPositions`, perPositionHeight) → 카메라 돌려도 실제 yaw 정확.
- **Message Monitor 오토스크롤**: `onCountChanged: Qt.callLater(positionViewAtEnd)`.
- **새 publish 시 이전 plan-mirror 클리어**: `_uxPublish`/`_uxPublishArea`가 `EventBroadcaster.clearUxasPlannedWaypoints(0)` 호출.

### OpenUxAS 예제02 WaterwaySearch on PX4+QGC (착수, 보류)
- 예제 cfg가 이미 **PUB 5560/PULL 5561**(=우리 bridge 포트) 노출 → AMASE 자리를 PX4 bridge가 대체. `cfg_WaterwaySearch_PX4.xml` 만듦(AMASE 5555 브리지 제거). 좌표=Deschutes 강(미국 45.32,-120.96), UAV 400/500.
- `configs/worlds/deschutes.sdf`(오리건 원점) + PX4 worlds 심링크 + `vehicles.json` id11 두번째 Cessna 추가.
- **월드 SDF 복사 시 `<world name>`을 PX4_GZ_WORLD와 일치시켜야** 함(안 그러면 "Timed out waiting for Gazebo world").
- **막힌 점: 2번째 기체(V500)를 4km 떨어뜨려 스폰하면 PX4 gz EKF 발산**(heading invalid/GPS drift). 가까이(수백m) 스폰해야 둘 다 정상. → 사용자가 예제 중단하고 강남 Cessna로 전환. (재개하려면 id11 spawn을 가깝게 + bridge 2개 400/500 + UxAS는 `cfg_WaterwaySearch_PX4.xml`)

### 미커밋 변경 다수 (이번 세션, 아직 commit 안 함)
- C++: `MissionController.{cc}`, `MissionManager.{h,cc}`, `Cesium3DView.html`, `FlyViewMap.qml`, `MessageMonitorPage.qml`
- Python: `uxas_publish_task.py`, `vworld_multi_search.py`, `uxas_search_listener.py`, `qgc_uxas_bridge.py`(NAV_LAND), `fetch_amase_overlay.py`
- 신규: `configs/worlds/deschutes.sdf`, `configs/amase/Scenario_Gangnam.xml`, `examples/.../cfg_WaterwaySearch_PX4.xml`(OpenUxAS 트리), `vehicles.json`(id11 + 강남 home)

### 빌드/실행 함정 (오늘 시간 많이 씀)
- **QGC를 빌드 끝나기 전에 띄우면 옛 바이너리로 실행됨** → `ps -eo pid,lstart`의 QGC 시작시각이 바이너리 mtime보다 **이후**인지 확인하고 테스트.
- set -e 쉘에서 `pkill`/`pgrep`이 매칭 없으면 1 반환 → 뒤 명령(런치) 중단. **kill과 launch는 분리된 Bash 호출로**.

---

## ★★★★ 2026-06-10: vanilla PX4 재구축 + publish-road 파이프라인 수정 + 검증 4건 + MAVLink 모니터

### 환경 (이번 세션에서 바뀐 것)
- **PX4 새로 받음**: `/home/swerc/PX4-Autopilot` = **upstream PX4/PX4-Autopilot main** (kokoory 포크 아님). tag `v1.18.0-alpha1-305-gd5f5c50330`. 빌드는 `PATH=/usr/bin:$PATH make px4_sitl_default` (시스템 python3.10; `/usr/local/bin/python3.8`은 SSL 깨져서 kconfiglib 설치 불가 → PATH로 우회). `korea.sdf` symlink 재생성함.
- **EKF runaway 원인 = kokoory 포크의 `mc_nn_control` 모듈로 확정**. vanilla PX4는 220m 자동이륙·AUTO.MISSION 모두 **통제된 비행, runaway 없음**. → `vehicles.json` v1에서 `MC_NN_EN` 제거함(vanilla엔 없는 파라미터).
- **launch_all.sh**: HEADLESS 기본 + dataman 청소 유지. 단 GUI 원하면 `GZ_GUI=1`로 실행 후 `DISPLAY=:1 gz sim -g` 별도 기동.

### publish-road가 "안 되던" 원인 3개 (모두 수정)
1. **listener 미기동** — `uxas_search_listener.py`가 안 떠 있으면 45678 수신처 없음. (운영: 띄우면 됨)
2. **bridge가 AutomationResponse 안의 MissionCommand를 안 꺼냄** — UxAS는 standalone MissionCommand를 안 보내고 `AutomationResponse.MissionCommandList`에 담아 보냄. `qgc_uxas_bridge.py _handle_uxas_message`가 리스트 순회하도록 수정함.
3. **recorder vs bridge 포트 경합(14541)** — `mavlink_recorder.py`(127.0.0.1:14541)가 PX4 위치 패킷을 가로채 bridge(0.0.0.0:14541)가 굶음 → AirVehicleState 미발행 → UxAS "AutomationRequest Not Ready" → 빈 응답. **임시로 recorder kill**. **TODO: launch 스크립트가 recorder/bridge에 포트를 분리해 주도록 고쳐야 함(harness 버그).**
- 수정 후: 도로검색 → UxAS 4 task → 67~70wp MissionCommand → bridge 자동이륙 → AUTO.MISSION 비행 + plan mirror(45681)로 QGC 지도에 cyan 웨이포인트.

### 검증 이슈 4건 (모두 수정)
1. **Message Monitor가 Tx만 보임** → live `onMessageReceived`가 "plan" 채널(45681 plan mirror)을 bridge로 오인해 빈 행. **"plan" 채널 케이스 추가**(카운터/필터/라벨). 팝아웃 창은 `allowPopout:true`로 이미 됨(헤더 창 아이콘).
2. **카메라 커버리지 1번만 찍힘** → `_lastStampCoord[v.id] = v.coordinate`가 **참조 저장**이라 `last.distanceTo(현재)`가 항상 0. **스냅샷 `QtPositioning.coordinate(lat,lon)` 저장**으로 수정. green α 0.18→0.32+테두리, z 상향, `trajectoryPoints.onPointsCleared` 자동삭제 제거(비행 중 버퍼리셋이 커버리지 지웠음)→재arm 시에만 클리어.
3. **기수≠진행방향** → 웨이포인트 param4=0(북). bridge가 **param4 = 들어오는 leg 방위각**(wp[N]=bearing(N-1→N), PX4는 향하는 wp의 yaw 사용)으로 설정. `_bearing_deg` 헬퍼 추가. (처음엔 outgoing으로 줘서 off-by-one → 수정함)
4. **도로 2회 주행** = **UxAS 정상 동작**(4 LineSearchTask를 1대에 chain + ViewAngleList 미설정 + 장애물 라우팅). 코드 버그 아님. 줄이려면 도로↔차량 1:1 분배 / 세그먼트 병합 / ViewAngle 명시.

### 신규 기능: 일반 QGC MAVLink 트래픽 모니터 (완성, 빌드됨)
- **C++**: `EventBroadcaster`가 `MultiVehicleManager`→각 Vehicle의 `mavCommandResult`(QGC가 보낸 명령+ack), `armedChanged`, `flightModeChanged`를 탭 → 중앙 로그에 **"mavlink" 채널**로 기록 + `messageReceived("mavlink",...)` emit(팝아웃 창들도 공유). `_connectMavlinkTap`은 생성자에서 queued로 연결.
- **QML**(`MessageMonitorPage.qml`): "mavlink" 채널 — 카운터(`MAVLink: N`), 필터, 라벨(QGC→Vehicle / Vehicle→QGC), live 핸들러 재구성.
- Utilities가 단일 타겟이라 `#include "MultiVehicleManager.h"`/`"Vehicle.h"` CMake 수정 불필요.

### 검증 완료 ✅
2D 도로선택→publish→비행 / Message Monitor Tx+Rx(plan)+MAVLink / 카메라 커버리지 누적 / 기수=진행방향 / vanilla PX4 무발산.

### 우선순위: 내일 = 아래 "미검증 ❌" 먼저, Cessna는 그 다음 세션
(강→3D→Bridge tap·Replay→AMASE 끝낸 뒤 멀티콥터 검증 완료되면, 그 다음에 Cessna 전환)

### (다음 세션) Cessna(고정익)로 도로검색 시도
- **차량 id 4** `gz_rc_cessna` fixed_wing, autostart 4003, `--ids 4`. 포트: sitl_udp 14544, qgc_udp 14553, mavlink_tcp 4563. params: FW_AIRSPD_MIN/MAX 10/25, `MIS_TKO_LAND_REQ=0`. lmcp 10~25 m/s.
- **고정익 주의점**:
  - bridge launch 시 `--mavlink udpin:0.0.0.0:14544 --vehicle-id 4 --qgc... ` 등 **id4 포트로** 바꿔야 함(현재 명령은 id1=14541). launch_bridges.sh `--ids 4` 쓰면 자동.
  - **기수 yaw 수정(param4)은 고정익엔 무의미** — 고정익은 항상 진행방향을 향함(독립 yaw 불가). 카메라=헤딩 장착이면 자연히 맞음. (param4 설정해도 무해)
  - **자동이륙**: 고정익 NAV_TAKEOFF는 활주/캐터펄트식. `--auto-takeoff-agl`/`--alt-takeover-agl` 동작이 멀티콥터와 다를 수 있음 → 이륙 안 되면 bridge takeoff 로직 점검 필요.
  - **LineSearchTask(도로)는 고정익에 더 적합** — 라인 따라 비행. 강(AreaSearch)은 선회 반경(turnRadius) 커서 작은 폴리곤 어려울 수 있음.
  - vanilla PX4에 rc_cessna airframe(4003) 있는지 확인됨(빌드에 포함). 없으면 빌드 옵션 점검.
- recorder/bridge 포트 경합(14541 류)은 id4면 14544라 동일 패턴 — recorder kill 또는 포트분리 필요.

### 내일 할 것 (미검증) ❌
1. **강(river) 선택** — 도로만 함. 강은 줌아웃해야 `고읍천` 잡힘.
2. **3D Cesium 뷰** — 도로/강 가시성(#5), 3D plan mirror 웨이포인트(#6 3D쪽).
3. **Bridge tap 행**(Monitor "Bridge↔UxAS", 45680) 표시 확인 + **Replay 실제 실행**.
4. **AMASE 통합 (가장 큰 미검증 덩어리)** — 이번 세션 한 번도 안 띄움(amase auto-disabled). 비행 시각화(#7), 지도수정 반영(#8), UxAS좌표 AMASE 표시(#6 AMASE쪽). `configs/amase/run_amase_goheung.sh`, `fetch_amase_overlay.py`(CARTO/OSM/Esri 멀티 프로바이더).
- 미커밋 변경 다수(아직 commit 안 함): EventBroadcaster.{h,cc}, MessageMonitorPage.qml(신규), FlyViewMap.qml, qgc_uxas_bridge.py, Cesium3DView.* 등.

### 풀스택 기동 순서 (이번 세션에서 동작 확인된 명령)
```bash
# 1) UxAS
cd tools/test-automation/configs && \
  ~/OpenUxAS/infrastructure/sbx/x86_64-linux/uxas-release/install/bin/uxas -cfgPath ./uxas_multi.xml &
# 2) PX4 + Gazebo GUI (1대)
GZ_GUI=1 PX4_DIR=/home/swerc/PX4-Autopilot bash scripts/launch_all.sh --ids 1 &
DISPLAY=:1 gz sim -g &            # GUI 클라이언트 별도
# 3) recorder 죽이기 (bridge와 14541 경합 — TODO 영구수정 전까지)
kill $(pgrep -f mavlink_recorder.py)
# 4) bridge (takeover 낮게! 50m 도로검색엔 10m)
python3 -u scripts/qgc_uxas_bridge.py --mavlink udpin:0.0.0.0:14541 \
  --uxas-pub tcp://127.0.0.1:5560 --uxas-pull tcp://127.0.0.1:5561 --vehicle-id 1 \
  --auto-register --non-interactive --monitor-port 45680 \
  --auto-takeoff-agl 30 --alt-takeover-agl 10 &
# 5) listener (절대경로 주의, register-from-config는 생략—bridge가 등록)
python3 -u scripts/uxas_search_listener.py --vworld-key 4AB82F93-D134-3AFB-AEA8-59CB23854556 \
  --vehicles 1 --uxas-pub tcp://127.0.0.1:5560 --uxas-pull tcp://127.0.0.1:5561 --amase auto &
# 6) QGC
./build/Release/QGroundControl &
# 검색 주입 테스트(또는 QGC에서 Publish road): UDP 45678로 {"category":"uxas_search","event":"road","data":{...,"bbox":[127.19,34.60,127.22,34.62],"names":"ALL"}}
```

---

## ★★★ 2026-06-08~09: QGC 3D 임무계획 패널 + VWorld + 올림픽대로 라이브 비행

이 기간에 추가/검증된 것(모두 `kokoory/PX4_QGC_OPENUxAS` Add3Dmap에 push, HEAD `9edda44e5` 이후):

**1) Cesium 3D 안 HTML 임무계획 패널** (`src/Viewer3D/Viewer3DQml/Cesium3DView.html` `#uxasPanel`)
- QML이 아니라 **HTML div**인 이유: WebEngineView가 자체 레이어라 형제 QML(Popup 포함)을 가림. in-page HTML(z-index)만 지구본 위에 그려짐. (수차례 재현 확인)
- **탭 Area/Road/River**. 공유 파라미터(모든 탭): X500/Cessna 개수 → vehicle IDs, Altitude, **Sensor(Wide45°/Detail20°)**, 커버리지 readout(Area=면적·need N X500 / Road·River=GSD·선택수·길이km).
- **Area**: Rectangle(W×H) 또는 **Polygon 직접 그리기**(지도 클릭, 청록 점/선). 미리보기 노란 폴리곤.
- **Road/River**: "Scan area"(=선택 영역 bbox, VWorld 10km² 클램프) → 이름 체크리스트 + **지도에서 선 클릭 선택** + Select all. 선택 시 초록 하이라이트.
- 발행: `qgcBridge.publishSearch` → EventBroadcaster(UDP 45678) → `uxas_search_listener.py`.
- **헤더 드래그로 이동** 가능. 패널은 화면 중앙 look-at 지점 기준.

**2) VWorld(브이월드) 통합** (키 `4AB82F93-D134-3AFB-AEA8-59CB23854556`, Settings→General→VWorld 또는 .ini `vworldToken`)
- 3D 위성+Hybrid(한글 라벨) 타일, 3D 건물(`LT_C_SPBD` 층수 extrude), 도로(`LT_L_MOCTLINK`)/하천(`LT_C_WKMSTRM`) 표시.
- **CORS 제약**: 타일(WMTS)은 ACAO 있음, **Data API(벡터)는 없음** → HTML fetch 불가 → QML XMLHttpRequest로 우회(qgcBridge.requestVWorldLayer).
- **geomFilter ≤ 10km²** 한계 → 스캔 박스 클램프.

**3) 멀티 도로/강 발행** (`scripts/vworld_multi_search.py`)
- bbox 안 feature를 **이름별 그룹핑** → 도로마다 LineSearchTask / 강마다 AreaSearchTask → 한 AutomationRequest로 UxAS 자동분배. `--names ALL` 또는 목록.
- **고속도로→Cessna, 골목→X500** 자동: 별도 규칙 없이 `--register-from-config`(능력치) + 도로단위 task면 UxAS 비용최적화가 매칭.

**4) 2D 지도 연동** (`src/FlyView/FlyViewMap.qml`, `EventBroadcaster` C++)
- **2D 줌 수정**: Wayland+XWayland(xcb)에서 휠이 TouchPad로 보고돼 무시됨 → WheelHandler가 Mouse|TouchPad 모두 수용.
- **3D 계획 → 2D 표시**: `EventBroadcaster.planOverlay`(공유 QVariant) → 3D에서 그린 area 폴리곤·선택 도로(주황)·강(파랑)이 2D 비행지도에도 렌더.

**5) 올림픽대로 라이브 비행 검증** (헤드리스 성공)
- 3D 패널 올림픽대로 선택 → UxAS 30-waypoint 경로 → bridge 자동 ARM→220m 이륙→200m서 활성 → AUTO.MISSION → 도로 고도 50m 하강 → 남쪽 7.2m/s 순회(wp0 도달, seq 진행) 확인.
- **단, QGC(특히 3D WebEngine) 동시 실행 시 이 호스트에선 lockstep starvation→EKF 발산→flight termination.** 부하 규칙은 `[[feedback_host_load_lockstep]]` 메모리 참조. **고성능 호스트에선 QGC+멀티vehicle 동시 가능.**

**6) Cesium 3D segfault / waypoint 미순회 / Cessna ARM** — 모두 이전(2026-06-07) 해결됨(아래 섹션).

**다음 할 일**: 고성능 호스트에서 멀티 vehicle(X500+Cessna) 동시 비행으로 도로망 자동분배 시연 + (작업중) **카메라 풋프린트 커버리지 표시**.

---

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

**2026-06-08 영역 크기 UI 패널 (3D 뷰 안 HTML 패널)**:
- 3D 뷰 우상단에 **영역 크기 설정 패널**: Width/Height/Altitude(m), Sensor(Wide 45°/Detail 20°),
  Vehicles. 값을 바꾸면 **필요 X500 대수를 실시간 계산** ("Area 4.00 km² · GSD 12 cm /
  1 X500 ≈ 1.18 km² → need 4 X500"). [Set vehicles to N] 버튼이 차량 IDs를 1..N으로 채움.
  [Publish Area/Road/River] 버튼 → 화면 중앙 지점을 중심으로 `qgcBridge.publishSearch`로 발행.
- **핵심 결정 — 패널은 QML이 아니라 Cesium HTML 안의 div**: `WebEngineView`가 자체 컴포지터
  레이어라 형제 QML(Popup 포함)을 **가린다**(여러 번 재현 확인). 따라서 패널을 페이지 안
  HTML(`#uxasPanel`, z-index)로 넣어야 지구본 위에 확실히 그려짐. 커버리지 계산도 JS(`_uxCoverage`).
  QML 쪽은 `qgcBridge.publishSearch(kind, lat, lon, paramsJson)` 하나만 추가(EventBroadcaster 방송).
- **검증 상태**: 패널 렌더 + 실시간 계산("4 km² → need 4 X500")은 **시각적으로 확정**. 리스너는
  width/height 사각형 발행 명령 생성까지 검증(수동 UDP). **Publish 버튼→발행 링크는 합성 입력
  (XTEST)이 내장 Chromium에 클릭으로 등록되지 않아 헤드리스로 못 띄움** — 실제 마우스 클릭은 정상.
- **좌표계 주의**: 발행은 **화면 중앙 ray가 지면에 닿는 지점**을 중심으로 함(우클릭 불필요).
  영역을 화면에 프레이밍한 뒤 Publish.

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
