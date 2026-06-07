# 새 호스트 셋업 체크리스트 — PX4 × QGC × OpenUxAS 자동 비행

> 2026-06-06 구 호스트(load 과다)에서 이전. 이 문서만 따라 하면 됨.
> 전체 컨텍스트/미해결 이슈/재시작 시퀀스: [`tools/test-automation/docs/SESSION_HANDOFF.md`](tools/test-automation/docs/SESSION_HANDOFF.md)
> 상세 매뉴얼: [`tools/test-automation/docs/QGC_TestAutomation_Manual.md`](tools/test-automation/docs/QGC_TestAutomation_Manual.md)

## 0. 시스템 요구사항

- [ ] Ubuntu 22.04/24.04 (또는 호환 Linux)
- [ ] 빌드 도구: `sudo apt install git cmake ninja-build build-essential`
- [ ] **Qt 6.10+** (WebEngineQuick 모듈 포함 — Qt online installer로 설치, Cesium 3D 빌드에 필요)
- [ ] Gazebo **Harmonic** (PX4 셋업 스크립트가 같이 설치해 줌, 아래 §2)
- [ ] Python ≥ 3.8 + `pip install pymavlink pyzmq pyyaml`
- [ ] (선택) Java JRE 1.8+ — `lmcp_py/`가 repo에 이미 포함돼 있어 LMCP 재생성할 때만 필요
- [ ] (선택) `markdown weasyprint` — 보고서 PDF 재빌드용

## 1. Clone (3개, 나란히)

```bash
cd ~   # PX4는 ~/PX4-Autopilot에 두면 launch_all.sh가 자동으로 찾음

# ① 작업 본체 (QGC fork + test-automation + lmcp_py + 문서)
git clone -b Add3Dmap https://github.com/kokoory/PX4_QGC_OPENUxAS.git
cd PX4_QGC_OPENUxAS && git submodule update --init --recursive && cd ~

# ② PX4 — 라이브 검증에 쓰인 정확한 베이스
git clone -b claude/knowledge-distillation-flight-fGsy1 --recursive \
    https://github.com/kokoory/PX4-Autopilot.git

# ③ OpenUxAS — fork develop (로컬 커밋 14개 포함, eb9d816c)
git clone -b develop https://github.com/kokoory/OpenUxAS.git
```

## 2. PX4 빌드

```bash
cd ~/PX4-Autopilot
bash Tools/setup/ubuntu.sh        # 의존성 + Gazebo Harmonic

# x500 CA 로터 위치 보정 — 라이브 비행이 이 패치 적용 상태로 검증됨
git apply ~/PX4_QGC_OPENUxAS/tools/test-automation/configs/px4_patches/0001-x500-ca-rotor-positions.patch

make px4_sitl_default
```

- [ ] `build/px4_sitl_default/bin/px4` 생성 확인
- [ ] korea.sdf symlink:
  ```bash
  ln -sf ~/PX4_QGC_OPENUxAS/tools/test-automation/configs/worlds/korea.sdf \
      ~/PX4-Autopilot/Tools/simulation/gz/worlds/korea.sdf
  ```
- 참고: 이 브랜치의 NN-control/distillation 코드는 별개 프로젝트 것 (EXTERNAL1 모드에서만 동작, mission 비행과 무간섭)
- PX4를 다른 경로에 둘 경우: `PX4_DIR=<경로> ./scripts/launch_all.sh ...`

## 3. OpenUxAS 빌드

```bash
cd ~/OpenUxAS
# README 절차대로 (anod 기반)
./anod build uxas
```

- [ ] `uxas` 바이너리 확인 — 우리 스크립트/문서는 `<OpenUxAS>/obj/cpp/uxas` 경로를 기대.
      anod가 다른 곳(sbx 안)에 두면 그 경로를 쓰거나 `obj/cpp/`로 복사
- LMCP Python 라이브러리는 **이미 repo에 포함** (`tools/test-automation/lmcp_py/`) — 재생성 불필요.
  MDM이 바뀌었을 때만: 매뉴얼 "Python LMCP 라이브러리 생성" 절 참조

### 3-1. `.vpython` venv 문제 (run-example/anod가 `ModuleNotFoundError: No module named 'uxas'`로 실패할 때)

`.vpython/` venv은 생성 시점의 **절대 경로**가 내부에 박힌다. repo를 다른 경로로 옮기거나
시스템 Python이 업그레이드되면 깨짐 (2026-06-07 구 호스트에서 실제 발생 — repo 이동이 원인).
fresh clone 첫 실행 시 자동 설치는 apt를 써서 sudo를 물을 수 있음. 둘 다 수동 재생성으로 해결:

```bash
cd ~/OpenUxAS
rm -rf .vpython
python3 -m venv .vpython
.vpython/bin/python3 -m pip install -e infrastructure/uxas
./run-example --list    # 동작 확인
```

### 3-2. 예제 실행 + AMASE 시각화 (구 호스트에서 동작 검증 완료)

```bash
./run-example --list                                  # 예제 목록
./run-example 01_HelloWorld                           # 최소 예제 (Ctrl+C 종료)
./run-example 02_Example_WaterwaySearch --no-amase    # 시뮬레이션 예제, headless
./anod build amase                                    # AMASE 시각화 빌드 (Java 필요, 1회)
./run-example 02_Example_WaterwaySearch               # AMASE GUI와 함께 실행
```

- AMASE 창이 뜨면 **▶ (Play) 버튼**을 눌러야 시뮬레이션 시작 — UAV 2대가 수로 추종 + 센서
  footprint가 지도에 그려지면 정상 (Java 21에서 동작 확인됨)
- Web UI 컨트롤 패널: `python3 uxas_ui_server.py --port 8080` → 사용법 `WebUI_Guide_Korean.md`
- 개념/구조 가이드: `OpenUxAS_Guide_Korean.md`
- 참고: 우리 mixed-fleet 자동 비행은 예제 체계가 아니라 `uxas -cfgPath uxas_multi.xml` 직접
  실행 방식 (SESSION_HANDOFF 4-셸 시퀀스). 예제는 서비스/task 단독 검증용

## 4. QGC 빌드

```bash
cd ~/PX4_QGC_OPENUxAS
cmake -B build -G Ninja -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release --parallel
```

- [ ] `build/Release/QGroundControl` 생성 확인
- [ ] 실행 후 `ss -ulnp | grep 45679` — EventBroadcaster 바인딩 확인

## 5. 헬스 체크

```bash
cd ~/PX4_QGC_OPENUxAS
python3 -m py_compile tools/test-automation/scripts/*.py
bash -n tools/test-automation/scripts/launch_all.sh
python3 tools/test-automation/scripts/qgc_event_monitor.py --help
```

## 6. 스모크 테스트 — 단일 v1 자율 체인 재현 (구 호스트에서 성공한 지점)

SESSION_HANDOFF.md "즉시 재시작 시퀀스"의 4-셸 절차 그대로:

1. 셸 1: UxAS (`uxas_multi.xml`, 포트 5560/5561)
2. 셸 2: `RECORDER=0 ./scripts/launch_all.sh --fleet mixed_small` — **새 호스트는 `SIM_SPEED` 불필요 (기본 1.0)**.
   ⚠️ `RECORDER=0` 필수: mavlink_recorder가 bridge와 같은 UDP 14541에 바인딩해 PX4 트래픽을
   가로채면 GCS heartbeat이 끊겨 ARM이 거부됨 (2026-06-07 발견)
3. 셸 3: `./scripts/launch_bridges.sh --fleet mixed_small` — v1/v2 자동 ARM+TAKEOFF(220 m) 확인
4. 셸 4(선택): QGC — Cesium 3D 버튼 사용 가능 (segfault는 2026-06-07 해결: 재빌드 바이너리 기준)
5. `uxas_publish_task.py area ...` 발행 → 200 m AGL 통과 시 mission 자동 upload → AUTO.MISSION 확인

기대 결과: ARM ack=0 → TAKEOFF ack=0 → 21-wp upload → AUTO.MISSION 전환 (구 호스트 2026-05-29 검증 완료)

## 7. 작업 순서 (스모크 테스트 통과 후)

| # | 항목 | 비고 |
|---|---|---|
| 1 | ~~AUTO.MISSION waypoint 미순회~~ | **2026-06-07 해결** — AUTO.TAKEOFF 진행 중 모드 전환 race. bridge가 takeoff 종료 대기 + 미정착 시 재명령하도록 수정, v1 풀체인 검증 완료 (SESSION_HANDOFF ★★ §3-2) |
| 2 | **Cessna(v4) 자동 ARM 실패** | 공중 spawn z=300에도 GPS lock 전 추락. 후보: gz_standard_vtol / 활주로 모델 / GPS lock 가속 |
| 3 | ~~Cesium 3D 버튼 segfault~~ | **2026-06-07 해결** — 원인은 구 바이너리에 main.cc AppArmor sandbox 우회 미포함. 새 호스트는 §4 빌드만 하면 됨 (SESSION_HANDOFF §3 참조) |
| 4 | **멀티 vehicle 동시 비행** | 구 호스트에선 lockstep starvation으로 불가 — 새 호스트의 본 목적. Tier 1-2/Tier 3 |
| 5 | 보고서 `QGC_UxAS_MixedFleet_Report.md` §6.7 갱신 | 라이브 결과 반영 |

## 8. Claude 세션 시작

새 호스트에서 Claude Code를 띄우고 첫 마디:

> **"tools/test-automation/docs/SESSION_HANDOFF.md 읽고 이어서 진행해줘"**

핵심 디자인 결정(200 m AGL takeover, ID 규칙 5곳 일치, `_capture_types` thread-safety 등)이 모두 그 문서에 있음.
