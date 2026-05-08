#!/usr/bin/env bash
#
# Master launcher for 10 PX4 SITL vehicles with Gazebo + MAVLink recorders.
#
# Starts 10 vehicles with different configurations, each with its own
# MAVLink recorder. The first vehicle creates the Gazebo simulation world;
# subsequent vehicles join with PX4_GZ_STANDALONE=1.
#
# Usage:
#     bash launch_all.sh [duration_seconds]
#
# Example:
#     bash launch_all.sh 300   # Run for 5 minutes then cleanup
#     bash launch_all.sh       # Run until Ctrl+C
#
# Prerequisites:
#     - PX4-Autopilot source tree (PX4_DIR env var or ~/PX4-Autopilot)
#     - Gazebo (gz-sim) installed
#     - Python 3 with pymavlink installed (for mavlink_recorder.py)

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PX4_DIR="${PX4_DIR:-${HOME}/PX4-Autopilot}"
PX4_BUILD_DIR="${PX4_DIR}/build/px4_sitl_default"
LOG_DIR="${SCRIPT_DIR}/../logs/$(date +%Y%m%d_%H%M%S)"
RECORDER_SCRIPT="${SCRIPT_DIR}/mavlink_recorder.py"

DURATION="${1:-0}"  # 0 = run until Ctrl+C

# Vehicle configurations: model, spawn offset (x, y, z, yaw_deg)
# Format: "instance_id:model:spawn_x:spawn_y:spawn_z:spawn_yaw"
VEHICLES=(
    "0:gz_x500:0:0:0:0"
    "1:gz_x500_depth:3:0:0:0"
    "2:gz_x500_vision:6:0:0:0"
    "3:gz_rc_cessna:10:0:0:0"
    "4:gz_standard_vtol:14:0:0:0"
    "5:gz_tiltrotor:18:0:0:0"
    "6:gz_r1_rover:22:0:0:0"
    "7:gz_x500_lidar:0:5:0:0"
    "8:gz_x500_gimbal:3:5:0:0"
    "9:gz_advanced_plane:10:5:0:0"
)

# MAVLink ports: instance N uses UDP ports 14540+N (SITL) and 14550+N (QGC)
MAVLINK_BASE_PORT=14550

# Tracking arrays
declare -a PX4_PIDS=()
declare -a RECORDER_PIDS=()
GZ_SERVER_PID=""

# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

log_info() {
    echo "[$(date '+%H:%M:%S')] INFO: $*"
}

log_warn() {
    echo "[$(date '+%H:%M:%S')] WARN: $*" >&2
}

log_error() {
    echo "[$(date '+%H:%M:%S')] ERROR: $*" >&2
}

# Create log directory
setup_logs() {
    mkdir -p "${LOG_DIR}"
    log_info "Logs directory: ${LOG_DIR}"
}

# Parse vehicle config string into variables
# Sets: V_INSTANCE, V_MODEL, V_X, V_Y, V_Z, V_YAW
parse_vehicle_config() {
    local config="$1"
    IFS=':' read -r V_INSTANCE V_MODEL V_X V_Y V_Z V_YAW <<< "${config}"
}

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

cleanup() {
    log_info "Cleaning up all processes ..."

    # Stop recorders
    for pid in "${RECORDER_PIDS[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
            log_info "Stopping recorder (PID ${pid})"
            kill -SIGTERM "$pid" 2>/dev/null || true
        fi
    done

    # Stop PX4 instances
    for pid in "${PX4_PIDS[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
            log_info "Stopping PX4 (PID ${pid})"
            kill -SIGTERM "$pid" 2>/dev/null || true
        fi
    done

    # Wait briefly for graceful shutdown
    sleep 2

    # Force-kill anything still running
    for pid in "${RECORDER_PIDS[@]}" "${PX4_PIDS[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
            log_warn "Force-killing PID ${pid}"
            kill -SIGKILL "$pid" 2>/dev/null || true
        fi
    done

    # Stop Gazebo if we know its PID
    if [[ -n "${GZ_SERVER_PID}" ]] && kill -0 "${GZ_SERVER_PID}" 2>/dev/null; then
        log_info "Stopping Gazebo server (PID ${GZ_SERVER_PID})"
        kill -SIGTERM "${GZ_SERVER_PID}" 2>/dev/null || true
        sleep 1
        kill -SIGKILL "${GZ_SERVER_PID}" 2>/dev/null || true
    fi

    # Kill any lingering gz/px4 processes from this session
    pkill -f "px4.*-i [0-9]" 2>/dev/null || true

    log_info "Cleanup complete"
    log_info "Logs saved in: ${LOG_DIR}"
}

trap cleanup EXIT INT TERM

# ---------------------------------------------------------------------------
# Check prerequisites
# ---------------------------------------------------------------------------

check_prerequisites() {
    if [[ ! -d "${PX4_DIR}" ]]; then
        log_error "PX4 directory not found: ${PX4_DIR}"
        log_error "Set PX4_DIR environment variable to your PX4-Autopilot path"
        exit 1
    fi

    if [[ ! -f "${PX4_BUILD_DIR}/bin/px4" ]]; then
        log_error "PX4 binary not found at ${PX4_BUILD_DIR}/bin/px4"
        log_error "Build PX4 SITL first: cd ${PX4_DIR} && make px4_sitl_default"
        exit 1
    fi

    if ! command -v gz &>/dev/null; then
        log_warn "Gazebo (gz) command not found in PATH"
        log_warn "Some vehicle models may not work without Gazebo"
    fi

    if ! python3 -c "import pymavlink" 2>/dev/null; then
        log_warn "pymavlink not found. MAVLink recorders will not start."
        log_warn "Install with: pip install pymavlink"
    fi
}

# ---------------------------------------------------------------------------
# Start a PX4 SITL instance
# ---------------------------------------------------------------------------

start_px4_instance() {
    local instance="$1"
    local model="$2"
    local spawn_x="$3"
    local spawn_y="$4"
    local spawn_z="$5"
    local spawn_yaw="$6"
    local is_first="$7"

    local instance_dir="${LOG_DIR}/instance_${instance}"
    mkdir -p "${instance_dir}"

    local log_file="${instance_dir}/px4.log"
    local env_vars=""

    # Model/world settings
    env_vars="PX4_SYS_AUTOSTART=4001"
    env_vars="${env_vars} PX4_GZ_MODEL=${model}"

    # Spawn position
    env_vars="${env_vars} PX4_GZ_MODEL_POSE=${spawn_x},${spawn_y},${spawn_z},0,0,${spawn_yaw}"

    if [[ "${is_first}" == "false" ]]; then
        # Subsequent vehicles join existing Gazebo world
        env_vars="${env_vars} PX4_GZ_STANDALONE=1"
    fi

    log_info "Starting PX4 instance ${instance} (model=${model}, " \
             "pos=[${spawn_x},${spawn_y},${spawn_z}])"

    # Launch PX4
    (
        cd "${PX4_BUILD_DIR}"
        env ${env_vars} ./bin/px4 \
            -i "${instance}" \
            -d \
            "${PX4_DIR}/ROMFS/px4fmu_common" \
            -s "etc/init.d-posix/rcS" \
            > "${log_file}" 2>&1
    ) &

    local pid=$!
    PX4_PIDS+=("${pid}")
    log_info "  PX4 instance ${instance} started (PID ${pid}, log: ${log_file})"

    # If first instance, wait for Gazebo to initialize
    if [[ "${is_first}" == "true" ]]; then
        log_info "  Waiting for Gazebo to initialize ..."
        sleep 10
        # Try to find Gazebo server PID
        GZ_SERVER_PID=$(pgrep -f "gz sim" 2>/dev/null | head -1) || true
        if [[ -n "${GZ_SERVER_PID}" ]]; then
            log_info "  Gazebo server detected (PID ${GZ_SERVER_PID})"
        fi
    else
        # Shorter delay for subsequent vehicles
        sleep 3
    fi
}

# ---------------------------------------------------------------------------
# Start MAVLink recorder for an instance
# ---------------------------------------------------------------------------

start_recorder() {
    local instance="$1"
    local port=$((MAVLINK_BASE_PORT + instance))
    local instance_dir="${LOG_DIR}/instance_${instance}"
    local log_file="${instance_dir}/recorder.log"

    if [[ ! -f "${RECORDER_SCRIPT}" ]]; then
        log_warn "Recorder script not found: ${RECORDER_SCRIPT}"
        log_warn "Skipping recorder for instance ${instance}"
        return
    fi

    if ! python3 -c "import pymavlink" 2>/dev/null; then
        return
    fi

    log_info "Starting recorder for instance ${instance} (port ${port})"

    python3 "${RECORDER_SCRIPT}" \
        --instance "${instance}" \
        --output "${instance_dir}" \
        > "${log_file}" 2>&1 &

    local pid=$!
    RECORDER_PIDS+=("${pid}")
    log_info "  Recorder started (PID ${pid})"
}

# ---------------------------------------------------------------------------
# Wait for vehicle heartbeat
# ---------------------------------------------------------------------------

wait_for_heartbeat() {
    local instance="$1"
    local port=$((MAVLINK_BASE_PORT + instance))
    local timeout=30
    local elapsed=0

    log_info "Waiting for heartbeat on port ${port} (timeout ${timeout}s) ..."

    while [[ ${elapsed} -lt ${timeout} ]]; do
        # Quick check: try to connect and receive a heartbeat
        if python3 -c "
import sys
from pymavlink import mavutil
try:
    conn = mavutil.mavlink_connection('udpin:0.0.0.0:${port}', timeout=2)
    hb = conn.wait_heartbeat(timeout=3)
    conn.close()
    sys.exit(0 if hb else 1)
except:
    sys.exit(1)
" 2>/dev/null; then
            log_info "  Heartbeat received for instance ${instance}"
            return 0
        fi
        sleep 2
        elapsed=$((elapsed + 2))
    done

    log_warn "  No heartbeat for instance ${instance} after ${timeout}s"
    return 1
}

# ---------------------------------------------------------------------------
# Status display
# ---------------------------------------------------------------------------

print_status() {
    echo ""
    echo "================================================================"
    echo "  PX4 SITL Fleet Status"
    echo "================================================================"
    echo ""
    printf "  %-4s  %-22s  %-8s  %-8s\n" "ID" "Model" "PX4 PID" "Status"
    printf "  %-4s  %-22s  %-8s  %-8s\n" "----" "----------------------" "--------" "--------"

    for i in "${!VEHICLES[@]}"; do
        parse_vehicle_config "${VEHICLES[$i]}"
        local pid="${PX4_PIDS[$i]:-N/A}"
        local status="unknown"
        if [[ "${pid}" != "N/A" ]] && kill -0 "${pid}" 2>/dev/null; then
            status="running"
        else
            status="stopped"
        fi
        printf "  %-4s  %-22s  %-8s  %-8s\n" "${V_INSTANCE}" "${V_MODEL}" "${pid}" "${status}"
    done

    echo ""
    echo "  Logs: ${LOG_DIR}"
    echo "  Recorders: ${#RECORDER_PIDS[@]} active"
    echo "================================================================"
    echo ""
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

main() {
    log_info "QGC Test Automation - Fleet Launcher"
    log_info "===================================="
    log_info "Vehicles: ${#VEHICLES[@]}"
    log_info "Duration: $(( DURATION > 0 ? DURATION : 0 ))s (0 = until Ctrl+C)"
    echo ""

    check_prerequisites
    setup_logs

    # Start vehicles sequentially
    local is_first="true"
    for config in "${VEHICLES[@]}"; do
        parse_vehicle_config "${config}"
        start_px4_instance "${V_INSTANCE}" "${V_MODEL}" \
                           "${V_X}" "${V_Y}" "${V_Z}" "${V_YAW}" \
                           "${is_first}"
        is_first="false"
    done

    log_info "All ${#VEHICLES[@]} PX4 instances started"
    echo ""

    # Start recorders for each vehicle
    for config in "${VEHICLES[@]}"; do
        parse_vehicle_config "${config}"
        start_recorder "${V_INSTANCE}"
    done

    log_info "All recorders started"

    # Display status
    print_status

    # Wait for specified duration or until Ctrl+C
    if [[ ${DURATION} -gt 0 ]]; then
        log_info "Running for ${DURATION} seconds ..."
        local remaining=${DURATION}
        while [[ ${remaining} -gt 0 ]]; do
            local interval=10
            if [[ ${remaining} -lt ${interval} ]]; then
                interval=${remaining}
            fi
            sleep "${interval}"
            remaining=$((remaining - interval))
            if [[ ${remaining} -gt 0 ]]; then
                log_info "${remaining}s remaining ..."
            fi
        done
        log_info "Duration elapsed. Shutting down ..."
    else
        log_info "Press Ctrl+C to stop all vehicles and exit"
        # Wait indefinitely
        while true; do
            sleep 60
            # Periodic health check
            local alive=0
            for pid in "${PX4_PIDS[@]}"; do
                if kill -0 "$pid" 2>/dev/null; then
                    alive=$((alive + 1))
                fi
            done
            log_info "Health check: ${alive}/${#PX4_PIDS[@]} PX4 instances running"
        done
    fi
}

main "$@"
