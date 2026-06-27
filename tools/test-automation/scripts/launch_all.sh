#!/usr/bin/env bash
#
# Launch PX4 SITL fleet (mixed multicopter / fixed-wing / VTOL / rover)
# driven by configs/vehicles.json. Each instance reads its model + parameters
# from the JSON entry, so heterogeneous fleets are a config edit, not a
# script edit.
#
# Usage:
#   launch_all.sh                       # launch all 10 vehicles, run until Ctrl+C
#   launch_all.sh --fleet mixed_full    # use a named fleet from vehicles.json
#   launch_all.sh --ids 1,2,4,10        # explicit vehicle IDs
#   launch_all.sh --duration 300        # auto-shutdown after 300s
#
# Environment overrides:
#   PX4_DIR       (default: ~/distiledUAV/PX4-Autopilot then ~/PX4-Autopilot)
#   VEHICLES_JSON (default: ../configs/vehicles.json)
#   RECORDER      (default: 1; set 0 to skip mavlink_recorder.py)

set -euo pipefail

# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TA_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
VEHICLES_JSON="${VEHICLES_JSON:-${TA_DIR}/configs/vehicles.json}"
EXTRA_WORLDS_DIR="${TA_DIR}/configs/worlds"

# Find PX4 source tree
if [[ -z "${PX4_DIR:-}" ]]; then
    if   [[ -d "${HOME}/distiledUAV/PX4-Autopilot" ]]; then PX4_DIR="${HOME}/distiledUAV/PX4-Autopilot"
    elif [[ -d "${HOME}/PX4-Autopilot"           ]]; then PX4_DIR="${HOME}/PX4-Autopilot"
    else echo "ERROR: PX4_DIR not set and no default found" >&2; exit 1
    fi
fi
PX4_BUILD_DIR="${PX4_DIR}/build/px4_sitl_default"
PX4_GZ_ENV="${PX4_BUILD_DIR}/rootfs/gz_env.sh"

# Source PX4's generated Gazebo environment (sets PX4_GZ_MODELS, PX4_GZ_WORLDS,
# GZ_SIM_RESOURCE_PATH, GZ_SIM_SYSTEM_PLUGIN_PATH, GZ_SIM_SERVER_CONFIG_PATH).
# Initialize the variables it expands against (gz_env.sh uses `$VAR:...` without
# `${VAR:-}`, so they must exist when we have `set -u`).
: "${GZ_SIM_RESOURCE_PATH:=}"
: "${GZ_SIM_SYSTEM_PLUGIN_PATH:=}"
: "${GZ_SIM_SERVER_CONFIG_PATH:=}"
export GZ_SIM_RESOURCE_PATH GZ_SIM_SYSTEM_PLUGIN_PATH GZ_SIM_SERVER_CONFIG_PATH
if [[ -f "${PX4_GZ_ENV}" ]]; then
    # shellcheck disable=SC1090
    source "${PX4_GZ_ENV}"
fi

LOG_DIR="${TA_DIR}/logs/$(date +%Y%m%d_%H%M%S)"
RECORDER="${RECORDER:-1}"
RECORDER_SCRIPT="${SCRIPT_DIR}/mavlink_recorder.py"

# Defaults
FLEET=""
IDS=""
DURATION=0
WORLD=""            # PX4_GZ_WORLD; default: read from vehicles.json or "default"
HOME_LAT=""         # PX4_HOME_LAT; default: read from vehicles.json defaults
HOME_LON=""         # PX4_HOME_LON
HOME_ALT=""         # PX4_HOME_ALT

# ---------------------------------------------------------------------------
parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --fleet)    FLEET="$2"; shift 2 ;;
            --ids)      IDS="$2"; shift 2 ;;
            --duration) DURATION="$2"; shift 2 ;;
            --world)    WORLD="$2"; shift 2 ;;
            --home-lat) HOME_LAT="$2"; shift 2 ;;
            --home-lon) HOME_LON="$2"; shift 2 ;;
            --home-alt) HOME_ALT="$2"; shift 2 ;;
            -h|--help)
                sed -n '3,20p' "$0"; exit 0 ;;
            *) echo "unknown arg: $1" >&2; exit 1 ;;
        esac
    done
}

# Read defaults.home_lat / home_lon / home_alt from vehicles.json if not given.
resolve_home() {
    local field="$1"
    python3 - "$VEHICLES_JSON" "$field" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
print(d.get("defaults", {}).get(sys.argv[2], ""))
PY
}

log()  { echo "[$(date '+%H:%M:%S')] $*"; }
warn() { echo "[$(date '+%H:%M:%S')] WARN: $*" >&2; }
err()  { echo "[$(date '+%H:%M:%S')] ERROR: $*" >&2; }

# Resolve list of vehicle IDs into a JSON array of selected vehicle dicts.
# Output (stdout): one line per vehicle, fields tab-separated:
#   id  name  model  type  spawn_x spawn_y spawn_z yaw_deg  sitl_udp  qgc_udp  autostart
resolve_fleet() {
    python3 - "$VEHICLES_JSON" "$FLEET" "$IDS" <<'PY'
import json, sys
path, fleet_name, id_list = sys.argv[1], sys.argv[2], sys.argv[3]
data = json.load(open(path))
vehicles = {v["id"]: v for v in data["vehicles"]}
if id_list:
    ids = [int(x) for x in id_list.split(",") if x.strip()]
elif fleet_name:
    fleets = data.get("fleets", {})
    if fleet_name not in fleets:
        sys.exit(f"fleet '{fleet_name}' not found")
    ids = fleets[fleet_name]["vehicle_ids"]
else:
    ids = sorted(vehicles.keys())
for i in ids:
    v = vehicles[i]
    sp = v["spawn_pose"]
    pt = v["ports"]
    autostart = v.get("parameters", {}).get("SYS_AUTOSTART", 4001)
    print("\t".join([str(v["id"]), v["name"], v["model"], v["type"],
                     str(sp["x"]), str(sp["y"]), str(sp["z"]),
                     str(sp.get("yaw_deg", 0)),
                     str(pt["sitl_udp"]), str(pt["qgc_udp"]),
                     str(autostart)]))
PY
}

declare -a PX4_PIDS=()
declare -a RECORDER_PIDS=()
GZ_SERVER_PID=""

cleanup() {
    log "Cleanup ..."
    for pid in "${RECORDER_PIDS[@]:-}" "${PX4_PIDS[@]:-}"; do
        [[ -z "${pid:-}" ]] && continue
        kill -TERM "$pid" 2>/dev/null || true
    done
    sleep 2
    for pid in "${RECORDER_PIDS[@]:-}" "${PX4_PIDS[@]:-}"; do
        [[ -z "${pid:-}" ]] && continue
        kill -KILL "$pid" 2>/dev/null || true
    done
    if [[ -n "${GZ_SERVER_PID}" ]]; then
        kill -TERM "${GZ_SERVER_PID}" 2>/dev/null || true
        sleep 1
        kill -KILL "${GZ_SERVER_PID}" 2>/dev/null || true
    fi
    pkill -f "px4.*-i [0-9]" 2>/dev/null || true
    log "Cleanup done. Logs in: ${LOG_DIR}"
}
trap cleanup EXIT INT TERM

check_prereq() {
    [[ -f "${PX4_BUILD_DIR}/bin/px4" ]] || { err "PX4 binary missing: ${PX4_BUILD_DIR}/bin/px4 — build with 'make px4_sitl_default' in ${PX4_DIR}"; exit 1; }
    [[ -f "${VEHICLES_JSON}" ]]         || { err "vehicles.json missing: ${VEHICLES_JSON}"; exit 1; }
    command -v gz >/dev/null || warn "Gazebo 'gz' not in PATH — some models may not start"

    # PX4 SITL persists missions/geofence/safe-points in dataman across reboots
    # (PX4_BUILD_DIR/dataman). If a previous flight crashed with a corrupted
    # waypoint (e.g. 21 km altitude from an EKF blowup), PX4 will replay it on
    # the next AUTO.* transition and the takeoff appears to "fly the leftover
    # mission" instead of climbing to the requested altitude. Reset it.
    if [[ -f "${PX4_BUILD_DIR}/dataman" ]]; then
        log "Clearing PX4 dataman (removes leftover mission/geofence/safe-points)"
        rm -f "${PX4_BUILD_DIR}/dataman"
    fi
}

start_px4() {
    local id="$1" name="$2" model="$3" type="$4"
    local sx="$5" sy="$6" sz="$7" yaw="$8"
    local sitl_port="$9" qgc_port="${10}" autostart="${11}"
    local is_first="${12}"
    local dir="${LOG_DIR}/instance_${id}"; mkdir -p "$dir"

    local env_vars="PX4_SYS_AUTOSTART=${autostart}"
    env_vars="${env_vars} PX4_GZ_MODEL=${model}"
    env_vars="${env_vars} PX4_GZ_MODEL_POSE=${sx},${sy},${sz},0,0,${yaw}"
    env_vars="${env_vars} PX4_GZ_WORLD=${WORLD}"
    # On hosts that can't sustain real-time gz lockstep (Accel TIMEOUT →
    # EKF "vertical velocity unstable" → won't take off), run the sim slower
    # than wall-clock so each lockstep step has more CPU budget.
    # SIM_SPEED env (default 1.0). Set e.g. 0.5 to halve sim speed.
    env_vars="${env_vars} PX4_SIM_SPEED_FACTOR=${SIM_SPEED:-1.0}"
    # PX4-RC starts `gz sim -g` (the GUI client) by default; HEADLESS=1 skips
    # it. On hosts whose Xorg has no nvidia_drv (Mesa falls back to llvmpipe
    # CPU rendering), the GUI eats 8+ cores and starves the gz lockstep
    # scheduler → EKF blows up → vehicle climbs uncontrollably. Default on,
    # set GZ_GUI=1 to force the GUI back.
    if [[ "${GZ_GUI:-0}" != "1" ]]; then
        env_vars="${env_vars} HEADLESS=1"
    fi
    if [[ -n "${HOME_LAT}" ]]; then env_vars="${env_vars} PX4_HOME_LAT=${HOME_LAT}"; fi
    if [[ -n "${HOME_LON}" ]]; then env_vars="${env_vars} PX4_HOME_LON=${HOME_LON}"; fi
    if [[ -n "${HOME_ALT}" ]]; then env_vars="${env_vars} PX4_HOME_ALT=${HOME_ALT}"; fi
    # Re-export inherited Gazebo paths so they survive `env <env_vars>`.
    env_vars="${env_vars} GZ_SIM_RESOURCE_PATH=${GZ_SIM_RESOURCE_PATH:-}"
    env_vars="${env_vars} GZ_SIM_SYSTEM_PLUGIN_PATH=${GZ_SIM_SYSTEM_PLUGIN_PATH:-}"
    env_vars="${env_vars} GZ_SIM_SERVER_CONFIG_PATH=${GZ_SIM_SERVER_CONFIG_PATH:-}"
    if [[ "${is_first}" == "false" ]]; then
        env_vars="${env_vars} PX4_GZ_STANDALONE=1"
    fi

    log "PX4 #${id} ${name} [${model}, ${type}] pos=(${sx},${sy},${sz}) sitl_udp=${sitl_port}"
    (
        cd "${PX4_BUILD_DIR}"
        env ${env_vars} ./bin/px4 \
            -i "${id}" \
            -d \
            "${PX4_DIR}/ROMFS/px4fmu_common" \
            -s "etc/init.d-posix/rcS" \
            > "${dir}/px4.log" 2>&1
    ) &
    local pid=$!
    PX4_PIDS+=("${pid}")
    log "  PX4 #${id} pid=${pid}, log=${dir}/px4.log"

    if [[ "${is_first}" == "true" ]]; then
        log "  Waiting 10s for Gazebo bring-up ..."; sleep 10
        GZ_SERVER_PID="$(pgrep -f 'gz sim' 2>/dev/null | head -1 || true)"
        [[ -n "${GZ_SERVER_PID}" ]] && log "  gz sim pid=${GZ_SERVER_PID}"
    else
        sleep 3
    fi
}

start_recorder() {
    local id="$1"; local dir="${LOG_DIR}/instance_${id}"
    [[ "${RECORDER}" == "1" ]] || return 0
    [[ -f "${RECORDER_SCRIPT}" ]] || { warn "recorder script missing"; return 0; }
    python3 -c "import pymavlink" 2>/dev/null || { warn "pymavlink missing — skipping recorder"; return 0; }
    python3 "${RECORDER_SCRIPT}" --instance "${id}" --output-dir "${dir}" \
        > "${dir}/recorder.log" 2>&1 &
    local pid=$!
    RECORDER_PIDS+=("${pid}")
    log "  recorder #${id} pid=${pid}"
}

print_summary() {
    echo
    echo "============================================================"
    echo "  PX4 SITL Fleet — ${#PX4_PIDS[@]} vehicles up"
    echo "============================================================"
    printf "  %-4s  %-22s  %-18s  %-10s\n" "ID" "Name" "Model" "Status"
    printf "  %-4s  %-22s  %-18s  %-10s\n" "----" "----------------------" "------------------" "----------"
    local i=0
    while IFS=$'\t' read -r id name model type sx sy sz yaw sitl qgc autostart; do
        local pid="${PX4_PIDS[$i]:-?}"
        local status="?"
        [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null && status="running" || status="stopped"
        printf "  %-4s  %-22s  %-18s  %-10s\n" "${id}" "${name}" "${model}" "${status}"
        i=$((i+1))
    done <<< "$FLEET_LINES"
    echo
    echo "  Logs:       ${LOG_DIR}"
    echo "  Recorders:  ${#RECORDER_PIDS[@]} active"
    echo "============================================================"
    echo
}

# ---------------------------------------------------------------------------
main() {
    parse_args "$@"
    check_prereq

    # Resolve home + world from vehicles.json defaults if not provided.
    [[ -z "${WORLD}" ]] && WORLD="$(resolve_home world)"
    [[ -z "${WORLD}" ]] && WORLD="default"
    [[ -z "${HOME_LAT}" ]] && HOME_LAT="$(resolve_home home_lat)"
    [[ -z "${HOME_LON}" ]] && HOME_LON="$(resolve_home home_lon)"
    [[ -z "${HOME_ALT}" ]] && HOME_ALT="$(resolve_home home_alt)"

    # Make our local worlds dir visible to Gazebo so PX4_GZ_WORLD=<name> resolves
    # against configs/worlds/<name>.sdf in addition to the PX4 stock worlds.
    if [[ -d "${EXTRA_WORLDS_DIR}" ]]; then
        export GZ_SIM_RESOURCE_PATH="${GZ_SIM_RESOURCE_PATH:-}:${EXTRA_WORLDS_DIR}"
    fi

    FLEET_LINES="$(resolve_fleet)"
    [[ -n "${FLEET_LINES}" ]] || { err "no vehicles selected"; exit 1; }
    local count
    count=$(echo "${FLEET_LINES}" | wc -l)

    log "Launcher starting (${count} vehicle(s), fleet='${FLEET}', ids='${IDS}')"
    log "World=${WORLD}  home=(${HOME_LAT:-PX4-default}, ${HOME_LON:-PX4-default}, ${HOME_ALT:-PX4-default})"
    mkdir -p "${LOG_DIR}"
    log "Logs: ${LOG_DIR}"

    # Save resolved fleet to the log dir for the report
    echo "${FLEET_LINES}" > "${LOG_DIR}/fleet.tsv"

    local is_first="true"
    while IFS=$'\t' read -r id name model type sx sy sz yaw sitl qgc autostart; do
        start_px4 "${id}" "${name}" "${model}" "${type}" \
                  "${sx}" "${sy}" "${sz}" "${yaw}" \
                  "${sitl}" "${qgc}" "${autostart}" "${is_first}"
        is_first="false"
    done <<< "${FLEET_LINES}"

    log "All PX4 instances launched"

    while IFS=$'\t' read -r id rest; do
        start_recorder "${id}"
    done <<< "${FLEET_LINES}"

    print_summary

    if [[ "${DURATION}" -gt 0 ]]; then
        log "Auto-shutdown in ${DURATION}s"
        sleep "${DURATION}"
    else
        log "Press Ctrl+C to stop"
        while true; do
            sleep 60
            local alive=0
            for pid in "${PX4_PIDS[@]}"; do
                kill -0 "$pid" 2>/dev/null && alive=$((alive+1))
            done
            log "Health: ${alive}/${#PX4_PIDS[@]} PX4 instances running"
        done
    fi
}

main "$@"
