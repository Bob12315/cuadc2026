#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
PROFILE_DIR="${REPO_ROOT}/config/profiles/rk3588-real"

source "${SCRIPT_DIR}/profile_common.sh"

profile_require_send_commands_off "${REPO_ROOT}/config/app.yaml"
profile_apply "${REPO_ROOT}" "${PROFILE_DIR}"

echo "Applied RK3588 real profile."
echo "  telemetry: real; verify config/telemetry.yaml against the flight controller Ethernet settings"
echo "  video:     USB camera stable by-id path in config/yolo.yaml"
echo "  scope:     reviewed telemetry/yolo deltas only; Mission templates remain canonical"
echo "Review config/yolo.yaml if the camera device path differs on this board."
