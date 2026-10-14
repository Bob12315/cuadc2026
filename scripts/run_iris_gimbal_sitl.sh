#!/usr/bin/env bash
set -euo pipefail

SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
REPO_ROOT="$(dirname "$(dirname "${SCRIPT_PATH}")")"

# Relative overrides are always relative to this repository, not the caller's cwd.
# Resolve before passing paths to terminals, whose working directory may differ.
resolve_repo_path() {
  local path="$1"
  if [[ "${path}" != /* ]]; then
    path="${REPO_ROOT}/${path}"
  fi
  realpath -m -- "${path}"
}

# The team SITL workstation forwards MAVLink telemetry and video to the RK3588
# at this address by default. Override GCS_HOST for another ground station.
GCS_HOST="${GCS_HOST:-10.101.31.108}"
VIDEO_HOST="${VIDEO_HOST:-${GCS_HOST}}"
MAVLINK_PORT="${MAVLINK_PORT:-14550}"
MAVPROXY_STREAMRATE="${MAVPROXY_STREAMRATE:-50}"
VIDEO_PORT="${VIDEO_PORT:-5600}"
PAYLOAD_MAVLINK_PORT="${PAYLOAD_MAVLINK_PORT:-14551}"
ARDUPILOT_DIR="$(resolve_repo_path "${ARDUPILOT_DIR:-../ardupilot}")"
GZ_REPO_DIR="$(resolve_repo_path "${GZ_REPO_DIR:-../gz_ws/src/ardupilot_gazebo}")"
GZ_PARAM_FILE="$(resolve_repo_path "${GZ_PARAM_FILE:-${GZ_REPO_DIR}/config/gazebo-cuadc2026-fixed-camera.parm}")"
GZ_WORLD="$(resolve_repo_path "${GZ_WORLD:-${GZ_REPO_DIR}/worlds/cuadc2026_rescue.sdf}")"
CAMERA_STREAM_TOPIC="${CAMERA_STREAM_TOPIC:-/world/cuadc2026_rescue/model/iris_cuadc2026_fixed_down_camera/model/gimbal/link/pitch_link/sensor/camera/image/enable_streaming}"
PAYLOAD_BRIDGE="$(resolve_repo_path "${PAYLOAD_BRIDGE:-${GZ_REPO_DIR}/tools/cuadc2026/payload_release_bridge.py}")"
PAYLOAD_CONFIG="$(resolve_repo_path "${PAYLOAD_CONFIG:-${GZ_REPO_DIR}/config/cuadc2026-payload-release.yaml}")"
CAMERA_STREAM_DELAY="${CAMERA_STREAM_DELAY:-10}"
TERMINAL_HOLD="${TERMINAL_HOLD:-1}"
export TERMINAL_HOLD

if [[ "${1:-}" == "--tab-runner" ]]; then
  shift
  title="$1"
  shift
  printf "\033]0;%s\007" "${title}"
  echo "== ${title} =="
  echo "$*"
  echo

  if "$@"; then
    exit_code=0
  else
    exit_code=$?
    echo
    echo "Process exited with status ${exit_code}."
  fi

  if [ "${TERMINAL_HOLD}" = "1" ]; then
    echo "Press Enter to close this terminal."
    read -r _
  fi
  exit "${exit_code}"
fi

open_terminal() {
  local title="$1"
  shift

  if command -v gnome-terminal >/dev/null 2>&1; then
    gnome-terminal --title="${title}" -- bash "${SCRIPT_PATH}" --tab-runner "${title}" "$@"
  elif command -v konsole >/dev/null 2>&1; then
    konsole --new-tab --title "${title}" -e bash "${SCRIPT_PATH}" --tab-runner "${title}" "$@"
  elif command -v xfce4-terminal >/dev/null 2>&1; then
    xfce4-terminal --title="${title}" --command="$(printf '%q ' bash "${SCRIPT_PATH}" --tab-runner "${title}" "$@")"
  elif command -v xterm >/dev/null 2>&1; then
    xterm -T "${title}" -e bash "${SCRIPT_PATH}" --tab-runner "${title}" "$@" &
  else
    echo "No supported terminal emulator found." >&2
    echo "Install gnome-terminal, konsole, xfce4-terminal, or xterm." >&2
    exit 1
  fi
}

gz_command=(gz sim -v4 -r "${GZ_WORLD}")
SITL_RUNTIME_DIR="${REPO_ROOT}/runtime/sitl/iris-gimbal"
mkdir -p -- "${SITL_RUNTIME_DIR}"
sitl_args=(
  ./Tools/autotest/sim_vehicle.py -D -v ArduCopter -f JSON
  --use-dir "${SITL_RUNTIME_DIR}"
  "--add-param-file=${GZ_PARAM_FILE}" --console
  "--mavproxy-args=--streamrate=${MAVPROXY_STREAMRATE}"
  "--out=udp:${GCS_HOST}:${MAVLINK_PORT}"
  "--out=udp:127.0.0.1:${PAYLOAD_MAVLINK_PORT}"
)
printf -v sitl_shell '%q ' "${sitl_args[@]}"
printf -v ardupilot_shell_dir '%q' "${ARDUPILOT_DIR}"
arducopter_command=(
  bash -lc
  "cd ${ardupilot_shell_dir} && ${sitl_shell}"
)
printf -v payload_shell '%q ' python3 "${PAYLOAD_BRIDGE}" --config "${PAYLOAD_CONFIG}"
payload_bridge_command=(
  bash -lc
  "${payload_shell}"
)
printf -v relay_wait_shell '%q ' echo "Waiting ${CAMERA_STREAM_DELAY}s for Gazebo to start..."
printf -v relay_sleep_shell '%q ' sleep "${CAMERA_STREAM_DELAY}"
printf -v relay_topic_shell '%q ' gz topic -t "${CAMERA_STREAM_TOPIC}" -m gz.msgs.Boolean -p 'data: 1'
relay_args=(
  gst-launch-1.0 -v udpsrc "port=${VIDEO_PORT}"
  caps=application/x-rtp,media=video,encoding-name=H264,payload=96
  '!' rtpjitterbuffer latency=0 drop-on-latency=true '!' rtph264depay
  '!' h264parse '!' rtph264pay config-interval=1 pt=96
  '!' udpsink "host=${VIDEO_HOST}" "port=${VIDEO_PORT}"
)
printf -v relay_stream_shell '%q ' "${relay_args[@]}"
relay_command=(
  bash -lc
  "${relay_wait_shell} && ${relay_sleep_shell} && ${relay_topic_shell} && ${relay_stream_shell}"
)

if command -v gnome-terminal >/dev/null 2>&1; then
  # GNOME Terminal 3.52 only supports per-tab commands in one invocation via
  # --command.  One invocation is necessary to reliably keep all four tabs in
  # the same window.  Its deprecation notice is not part of simulation output.
  terminal_command() {
    local title="$1"
    shift
    printf '%q ' bash "${SCRIPT_PATH}" --tab-runner "${title}" "$@"
  }
  gnome-terminal \
    --window --title="gz sim CUADC 2026 rescue" --command "$(terminal_command "gz sim CUADC 2026 rescue" "${gz_command[@]}")" \
    --tab --title="ArduCopter SITL" --command "$(terminal_command "ArduCopter SITL" "${arducopter_command[@]}")" \
    --tab --title="Payload release bridge" --command "$(terminal_command "Payload release bridge" "${payload_bridge_command[@]}")" \
    --tab --title="Gimbal camera RTP relay" --command "$(terminal_command "Gimbal camera RTP relay" "${relay_command[@]}")" \
    2>/dev/null
  terminal_layout="one GNOME Terminal window with four tabs"
else
  open_terminal "gz sim CUADC 2026 rescue" "${gz_command[@]}"
  open_terminal "ArduCopter SITL" "${arducopter_command[@]}"
  open_terminal "Payload release bridge" "${payload_bridge_command[@]}"
  open_terminal "Gimbal camera RTP relay" "${relay_command[@]}"
  terminal_layout="separate terminal windows"
fi

echo "Started Gazebo, ArduCopter SITL, payload release bridge, and camera RTP relay in ${terminal_layout}."
echo "  MAVLink out: udp:${GCS_HOST}:${MAVLINK_PORT}"
echo "  MAVProxy stream rate: ${MAVPROXY_STREAMRATE} Hz"
echo "  Payload UDP: udp:127.0.0.1:${PAYLOAD_MAVLINK_PORT}"
echo "  Video out:   udp:${VIDEO_HOST}:${VIDEO_PORT}"
