#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
PROFILE_DIR="${REPO_ROOT}/config/profiles/rk3588-sitl"

source "${SCRIPT_DIR}/profile_common.sh"

profile_require_send_commands_off "${REPO_ROOT}/config/app.yaml"
profile_apply "${REPO_ROOT}" "${PROFILE_DIR}"

echo "Applied RK3588 SITL profile."
echo "  telemetry: sitl udp udpin 0.0.0.0:14550"
echo "  video:     UDP H264/RTP port 5600"
echo "  scope:     reviewed telemetry/yolo deltas only; Mission templates remain canonical"
