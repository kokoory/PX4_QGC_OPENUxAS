#!/usr/bin/env bash
#
# Spawn one qgc_uxas_bridge.py per vehicle, reading capability fields
# (min/max speed, min/max alt, etc.) from configs/vehicles.json.
#
# Usage:
#   launch_bridges.sh                       # all vehicles in vehicles.json
#   launch_bridges.sh --fleet mixed_full    # use named fleet
#   launch_bridges.sh --ids 1,2,4,10        # explicit IDs
#   launch_bridges.sh --uxas-host 127.0.0.1 # UxAS host (default 127.0.0.1)
#   launch_bridges.sh --no-heartbeat        # skip MAVLink heartbeat wait
#
# Logs to ${TA_DIR}/logs/bridges_<timestamp>/bridge_<id>.log
# Writes PID file: ${TA_DIR}/logs/bridges_<timestamp>/bridges.pids
# Kill all bridges:  kill $(cat <pidfile>)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TA_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
VEHICLES_JSON="${VEHICLES_JSON:-${TA_DIR}/configs/vehicles.json}"
BRIDGE="${SCRIPT_DIR}/qgc_uxas_bridge.py"

FLEET=""
IDS=""
UXAS_HOST="127.0.0.1"
NO_HEARTBEAT=0
AUTO_REGISTER=1
# QGC MessageMonitorPage listens on 45680. Override with --monitor-port 0 to
# disable, or --monitor-port N to forward to a different port.
MONITOR_PORT="${MONITOR_PORT:-45680}"
MONITOR_HOST="${MONITOR_HOST:-127.0.0.1}"
LOG_DIR="${TA_DIR}/logs/bridges_$(date +%Y%m%d_%H%M%S)"

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --fleet)         FLEET="$2"; shift 2 ;;
            --ids)           IDS="$2"; shift 2 ;;
            --uxas-host)     UXAS_HOST="$2"; shift 2 ;;
            --no-heartbeat)  NO_HEARTBEAT=1; shift ;;
            --no-register)   AUTO_REGISTER=0; shift ;;
            --monitor-port)  MONITOR_PORT="$2"; shift 2 ;;
            --monitor-host)  MONITOR_HOST="$2"; shift 2 ;;
            --log-dir)       LOG_DIR="$2"; shift 2 ;;
            -h|--help)       sed -n '3,15p' "$0"; exit 0 ;;
            *) echo "unknown arg: $1" >&2; exit 1 ;;
        esac
    done
}

log() { echo "[$(date '+%H:%M:%S')] $*"; }

# Emit one line per vehicle, tab-separated:
#   id  label  type  sitl_udp  min_speed max_speed nominal_speed
#   min_alt max_alt nominal_alt max_climb max_bank  spawn_z  px4_params
# px4_params: comma-separated NAME=VALUE from vehicles.json "parameters"
# (minus SYS_AUTOSTART, which launch_all.sh consumes). The bridge applies
# them via MAVLink PARAM_SET — launch_all.sh does NOT push them into PX4.
resolve_caps() {
    python3 - "$VEHICLES_JSON" "$FLEET" "$IDS" <<'PY'
import json, sys
path, fleet_name, id_list = sys.argv[1], sys.argv[2], sys.argv[3]
data = json.load(open(path))
vehicles = {v["id"]: v for v in data["vehicles"]}
defaults = data.get("lmcp_defaults", {})

if id_list:
    ids = [int(x) for x in id_list.split(",") if x.strip()]
elif fleet_name:
    ids = data["fleets"][fleet_name]["vehicle_ids"]
else:
    ids = sorted(vehicles.keys())

for i in ids:
    v = vehicles[i]
    vtype = v["type"]
    base = dict(defaults.get(vtype, {}))
    over = v.get("lmcp", {})
    cap = {**base, **over}
    nominal_speed = cap.get("nominal_speed_mps",
                            (cap.get("min_speed_mps", 5) + cap.get("max_speed_mps", 30)) / 2)
    spawn_z = v.get("spawn_pose", {}).get("z", 0)
    px4_params = ",".join(
        f"{k}={val}" for k, val in v.get("parameters", {}).items()
        if k != "SYS_AUTOSTART")
    print("\t".join([
        str(v["id"]), v["name"], vtype,
        str(v["ports"]["sitl_udp"]),
        str(cap.get("min_speed_mps", 5)),
        str(cap.get("max_speed_mps", 30)),
        str(nominal_speed),
        str(cap.get("min_alt_m", 0)),
        str(cap.get("max_alt_m", 500)),
        str(cap.get("nominal_alt_m", 100)),
        str(cap.get("max_climb_mps", 5)),
        str(cap.get("max_bank_deg", 25)),
        str(spawn_z),
        px4_params or "-",
    ]))
PY
}

main() {
    parse_args "$@"
    [[ -f "${BRIDGE}" ]]        || { echo "bridge missing: ${BRIDGE}"; exit 1; }
    [[ -f "${VEHICLES_JSON}" ]] || { echo "vehicles.json missing"; exit 1; }
    mkdir -p "${LOG_DIR}"

    local pid_file="${LOG_DIR}/bridges.pids"
    : > "${pid_file}"

    log "UxAS endpoint: tcp://${UXAS_HOST}:5560 (PUB) / :5561 (PULL)"
    log "Bridges log dir: ${LOG_DIR}"

    local extra=()
    [[ "${NO_HEARTBEAT}" == "1" ]] && extra+=(--no-heartbeat-wait)
    [[ "${AUTO_REGISTER}" == "1" ]] && extra+=(--auto-register)
    extra+=(--non-interactive)
    # Stream LMCP message summaries to QGC's MessageMonitor panel.
    if [[ "${MONITOR_PORT}" -gt 0 ]] 2>/dev/null; then
        extra+=(--monitor-port "${MONITOR_PORT}" --monitor-host "${MONITOR_HOST}")
    fi

    local count=0
    while IFS=$'\t' read -r id name type sitl_udp \
                       min_sp max_sp nom_sp min_alt max_alt nom_alt \
                       max_climb max_bank spawn_z px4_params; do
        local mavlink_conn="udpin:0.0.0.0:${sitl_udp}"
        local logfile="${LOG_DIR}/bridge_${id}.log"
        # Air-spawn fixed-wing (z >= 50) needs bridge-side auto-arm so the
        # vehicle doesn't free-fall while waiting for QGC operator input.
        # Ground-spawn multicopter gets auto-takeoff to 220 m AGL so the UxAS
        # plan auto-activates at the 200 m takeover threshold.
        local per_vehicle_extra=()
        if [[ "${type}" == "fixed_wing" ]]; then
            local z_int=${spawn_z%.*}
            if [[ ${z_int} -ge 50 ]]; then
                # Legacy air-spawn path (kept for reference; unreliable —
                # the vehicle falls before GPS/EKF is ready to arm).
                per_vehicle_extra+=(--auto-arm-on-start)
            else
                # Ground spawn: PX4 rc_cessna has RWTO_TKOFF=1 by default,
                # so NAV_TAKEOFF performs a runway takeoff from flat ground.
                # Same ARM + NAV_TAKEOFF path as the multicopters.
                per_vehicle_extra+=(--auto-takeoff-agl 220)
            fi
        elif [[ "${type}" == "multicopter" ]]; then
            per_vehicle_extra+=(--auto-takeoff-agl 220)
        fi
        # Apply vehicles.json "parameters" over MAVLink (launch_all.sh only
        # consumes SYS_AUTOSTART; FW_AIRSPD_*, MIS_TKO_LAND_REQ etc. would
        # otherwise silently never reach PX4).
        if [[ -n "${px4_params}" && "${px4_params}" != "-" ]]; then
            local param_spec
            for param_spec in ${px4_params//,/ }; do
                per_vehicle_extra+=(--px4-param "${param_spec}")
            done
        fi
        # Friendly tag for the log line summarizing per-vehicle behavior
        local extra_tag=""
        if [[ "${per_vehicle_extra[*]}" == *"--auto-arm-on-start"* ]]; then
            extra_tag=" (auto-arm)"
        elif [[ "${per_vehicle_extra[*]}" == *"--auto-takeoff-agl"* ]]; then
            extra_tag=" (auto-takeoff 220m)"
        fi
        log "Bridge #${id} ${name} [${type}] sitl_udp=${sitl_udp} speed=${min_sp}-${max_sp} alt=${min_alt}-${max_alt} spawn_z=${spawn_z}${extra_tag}"
        python3 -u "${BRIDGE}" \
            --mavlink "${mavlink_conn}" \
            --uxas-pub  "tcp://${UXAS_HOST}:5560" \
            --uxas-pull "tcp://${UXAS_HOST}:5561" \
            --vehicle-id "${id}" \
            --label "${name}" \
            --min-speed "${min_sp}" --max-speed "${max_sp}" --nominal-speed "${nom_sp}" \
            --min-alt "${min_alt}" --max-alt "${max_alt}" --nominal-alt "${nom_alt}" \
            --max-climb "${max_climb}" --max-bank-deg "${max_bank}" \
            "${extra[@]}" "${per_vehicle_extra[@]}" \
            > "${logfile}" 2>&1 &
        local pid=$!
        echo "${pid}" >> "${pid_file}"
        log "  bridge #${id} pid=${pid} log=${logfile}"
        count=$((count + 1))
        sleep 0.3
    done < <(resolve_caps)

    log "Launched ${count} bridge(s). PID file: ${pid_file}"
    log "Stop all:  kill \$(cat ${pid_file})"
    log "Tail logs: tail -f ${LOG_DIR}/bridge_*.log"
}

main "$@"
