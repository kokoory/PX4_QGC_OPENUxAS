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
LOG_DIR="${TA_DIR}/logs/bridges_$(date +%Y%m%d_%H%M%S)"

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --fleet)         FLEET="$2"; shift 2 ;;
            --ids)           IDS="$2"; shift 2 ;;
            --uxas-host)     UXAS_HOST="$2"; shift 2 ;;
            --no-heartbeat)  NO_HEARTBEAT=1; shift ;;
            --no-register)   AUTO_REGISTER=0; shift ;;
            --log-dir)       LOG_DIR="$2"; shift 2 ;;
            -h|--help)       sed -n '3,15p' "$0"; exit 0 ;;
            *) echo "unknown arg: $1" >&2; exit 1 ;;
        esac
    done
}

log() { echo "[$(date '+%H:%M:%S')] $*"; }

# Emit one line per vehicle, tab-separated:
#   id  label  type  sitl_udp  min_speed max_speed nominal_speed
#   min_alt max_alt nominal_alt max_climb max_bank  spawn_z
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

    local count=0
    while IFS=$'\t' read -r id name type sitl_udp \
                       min_sp max_sp nom_sp min_alt max_alt nom_alt \
                       max_climb max_bank spawn_z; do
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
                per_vehicle_extra+=(--auto-arm-on-start)
            fi
        elif [[ "${type}" == "multicopter" ]]; then
            per_vehicle_extra+=(--auto-takeoff-agl 220)
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
