# QGC에 추가된 기능 정리 (PX4_QGC_OPENUxAS / Add3Dmap)

OpenUxAS 연동을 위해 vanilla QGroundControl에 추가/수정한 기능 전체 목록.

## 1. EventBroadcaster — 통합 허브 (`src/Utilities/EventBroadcaster.{h,cc}`)
QGC ↔ 외부(스크립트/UxAS 파이프라인) 양방향 UDP 브리지. QML 싱글톤.
- **포트**: 45678 = 이벤트 송출(Tx broadcast), 45679 = 명령 수신(Rx), 45680 = bridge LMCP 탭, 45681 = UxAS plan mirror.
- `sendEvent(category, event, data)` → 45678로 JSON 송출 + 내부 로그.
- `commandReceived(action, params)` ← 45679 수신 → GuidedActions가 실행.
- **planOverlay**: 3D에서 그린 area/도로/강 → 2D 지도로 미러.
- **uxasPlannedWaypoints**: UxAS MissionCommand를 PX4 왕복 없이 미러(cyan 경로). `clearUxasPlannedWaypoints()`.
- **MAVLink 운용 탭**: 각 Vehicle의 `mavCommandResult`/`armedChanged`/`flightModeChanged`를 "mavlink" 채널로 기록.
- **VWorld 레이어 가시성 플래그**: `showVWorldBuildings/Roads/Rivers` (툴바↔3D 공유).

## 2. Message Monitor (`src/AnalyzeView/MessageMonitor/` — 신규, Analyze "Messages")
EventBroadcaster + bridge LMCP 트래픽 실시간 뷰.
- 채널: event / command / bridge / plan / mavlink. 필터, Pause, Clear.
- **Save/Load .jsonl**, **Replay**(레이트 0.25~5x), **오토스크롤**(onCountChanged+callLater), **팝아웃 창**.

## 3. 2D UxAS Search Planner 패널 (`src/FlyView/FlyViewMap.qml`)
3D 패널과 동등한 2D 임무계획 패널.
- 탭 **Area / Road / River**. X500·Cessna 수 → Vehicle IDs, Altitude, **Sensor(Wide/Detail)**, **커버리지 readout(기체타입 인지: "need N Cessna")**, **카메라 Overlap %**.
- **공유 Region(사각형 W/H · 폴리곤 draw)** — Area는 커버리지, Road/River는 그 영역으로 scan·클립.
- VWorld 도로/강 후보 클릭선택 + 체크리스트. 발행 → EventBroadcaster.
- **헤더 드래그 이동**, **리사이즈 그립(스케일)**, **초록 풋프린트(카메라 커버리지) on/off**.
- plan-mirror(cyan) / 카메라 풋프린트 누적(초록) / area 미리보기 렌더. 2D 줌(Wayland) 수정. 발행 시 이전 plan 클리어.

## 4. 가이드 액션 양방향 (`src/FlyView/GuidedActionsController.qml`)
- **아웃바운드**: 모든 가이드 액션 버튼(arm/takeoff/land/RTL/goto/orbit/pause/고도·속도/미션/setHome/ROI…)이 `executeAction`에서 `sendEvent("action", 이름, {actionCode, sliderValue,…})` 송출.
- **인바운드**: 45679 명령 → 실행. arm/disarm/takeoff/land/rtl/start_mission/pause/set_mode + **`actionCode` 경로**(녹화된 어떤 버튼이든 그대로 재생).

## 5. 미션 브로드캐스트/자동재싱크 (`src/MissionManager/`)
- `MissionController`: 웨이포인트 추가→`qgc_mission/waypoint_add`, 업로드→`qgc_mission/upload` 송출.
- `MissionManager`: **외부(브리지) 미션변경 자동 재다운로드**(`MISSION_CURRENT.total` 불일치 시 `loadFromVehicle`) → 번호 웨이포인트 항상 현재 미션.

## 6. 3D Cesium 뷰 (`src/Viewer3D/Viewer3DQml/Cesium3DView.{html,qml}`)
- **VWorld 통합**: 위성+Hybrid(한글) 타일, 3D 건물(LT_C_SPBD extrude), 도로/강.
- in-page HTML **UxAS Search Planner**(Area/Road/River, Sensor, 커버리지).
- **3D plan-mirror 웨이포인트** + area/도로/강 오버레이.
- **월드방향 비행체 헤딩 화살표**(polyline + CallbackProperty: 카메라 회전에도 정확, 깜박임 없음, CDN worker 불필요).
- **절대 AMSL 고도 통일**(비행체·경로·라벨, groundAmsl 라이브).
- **VWorld 레이어 show/hide**(`setVWorldLayerVisible`).

## 7. 툴바 / 코어
- `FlyViewToolBarIndicators.qml`: 상단 바에 **VWorld: Bldg/Road/River 토글** 체크박스.
- `QGCCorePlugin.cc`: Message Monitor("Messages") Analyze 등록.
- Settings: `vworldToken`(VWorld), `cesiumToken`(Cesium ion).

## 8. UxAS 패널 완전 양방향 + 이종 SAR (06-27 후반, `FlyViewMap.qml`)
- **양방향(테스트카드)**: 패널 모든 컨트롤이 `uxas_ui` 송출 + `ux_<field>` 명령 수신(`_uxHandleCommand`)으로 패널 원격 구동.
- **River 모드**: Centerline/Riverbank/Area 드롭다운(LineSearch vs AreaSearch).
- **FOV 단일소스**: Wide/Detail/Custom + FOV° → 풋프린트 + UxAS 레인 간격 일치.
- **이종 SAR 섹션**: Rover수 + FW/MC alt·fov + "Publish SAR" → 고정익 광역·고고도 / 멀티콥터 정밀·저고도 / 지상체 도로, 단일 AutomationRequest.
- **클릭 통과 차단**(패널 MouseArea), 멀티비행체 풀(Cessna [4,11], Rover [7]).

## 외부 도구 (QGC 아님, `tools/test-automation/scripts/`)
- **eb_monitor.py**: 45678 시간순 뷰 + `--save` 녹화 (seq dedup).
- **eb_monitor_gui.py**(신규): Tkinter **GUI 창** — 시간순 테이블/필터/Pause/Save.
- **eb_replay.py**: 녹화 .jsonl → 45679 재생(action + `uxas_ui`→`ux_` 매핑).
- **eb_scenario.py**: 손으로 쓴 시나리오/테스트카드 재생. `scenarios/han_river_card.json`.
- **uxas_sar_search.py**(신규): 이종 SAR 태스킹(기종별 티어 + EligibleEntities + OperatingRegion + 단일 AutomationRequest).
- **vworld_multi_search.py**: `_river_centerline`(PCA) + river 모드, 조각 병합.
- **qgc_uxas_bridge.py**: 파일 기반 차량별 카메라 FOV(`_camera_fov_now(vid)`). ⚠️ 45678 바인딩 금지.
