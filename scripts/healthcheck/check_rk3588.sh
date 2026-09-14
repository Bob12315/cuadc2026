#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
APP_ENV_NAME="${APP_ENV_NAME:-uav-app}"
YOLO_ENV_NAME="${YOLO_ENV_NAME:-uav-rk3588-yolo}"
APP_PYTHON="${APP_PYTHON:-}"
YOLO_PYTHON="${YOLO_PYTHON:-}"
FAILURES=0
WARNINGS=0
CONDA_BIN="${CONDA_BIN:-}"
ok() { echo "OK:   $*"; }
warn() { WARNINGS=$((WARNINGS + 1)); echo "WARN: $*"; }
fail() { FAILURES=$((FAILURES + 1)); echo "FAIL: $*"; }
resolve_python() {
  local supplied="$1" env_name="$2"
  [[ -n "${supplied}" ]] && { printf '%s\n' "${supplied}"; return; }
  if [[ -z "${CONDA_BIN}" ]]; then
    CONDA_BIN="$(command -v conda 2>/dev/null || true)"
  fi
  if [[ -z "${CONDA_BIN}" && -x "${HOME}/miniconda3/bin/conda" ]]; then
    CONDA_BIN="${HOME}/miniconda3/bin/conda"
  fi
  if [[ -z "${CONDA_BIN}" && -x "${HOME}/anaconda3/bin/conda" ]]; then
    CONDA_BIN="${HOME}/anaconda3/bin/conda"
  fi
  [[ -n "${CONDA_BIN}" ]] || return 0
  "${CONDA_BIN}" run -n "${env_name}" python -c 'import sys; print(sys.executable)' 2>/dev/null || true
}

echo "RK3588 hardware health check: ${REPO_ROOT}"
[[ "$(uname -s)" == Linux ]] && ok "Linux" || fail "requires Linux"
case "$(uname -m)" in aarch64|arm64) ok "ARM64 architecture";; *) fail "requires ARM64, detected $(uname -m)";; esac
compatible="$(tr -d '\0' </proc/device-tree/compatible 2>/dev/null || true)"
printf '%s' "${compatible}" | grep -qi rk3588 && ok "RK3588 board identity" || fail "RK3588 identity not found"
[[ -e /dev/rknpu || -e /dev/rknpu0 || -d /sys/kernel/debug/rknpu || -d /sys/module/rknpu || -d /sys/bus/platform/drivers/RKNPU ]] && ok "RKNN NPU device/driver" || fail "RKNN NPU device/driver missing"
[[ -f "${REPO_ROOT}/data/models/cuadc-fp16.rknn" ]] && ok "FP16 RKNN deployment model" || fail "deployment model missing"

APP_PYTHON="$(resolve_python "${APP_PYTHON}" "${APP_ENV_NAME}")"
YOLO_PYTHON="$(resolve_python "${YOLO_PYTHON}" "${YOLO_ENV_NAME}")"
[[ -x "${APP_PYTHON}" ]] && "${APP_PYTHON}" -c 'import fastapi, pymavlink, yaml' >/dev/null && ok "app environment" || fail "app environment ${APP_ENV_NAME} unavailable"
[[ -x "${YOLO_PYTHON}" ]] && "${YOLO_PYTHON}" -c 'import cv2; from rknnlite.api import RKNNLite' >/dev/null && ok "YOLO RKNN imports" || fail "YOLO RKNN environment ${YOLO_ENV_NAME} unavailable"
if [[ -x "${YOLO_PYTHON}" ]]; then
  "${YOLO_PYTHON}" - <<'PY' 2>/dev/null || true
from importlib.metadata import version
print(f"INFO: RKNN toolkit-lite2={version('rknn-toolkit-lite2')}")
PY
fi
if command -v modinfo >/dev/null 2>&1; then
  modinfo rknpu 2>/dev/null | awk -F': *' '$1 == "version" {print "INFO: rknpu_driver=" $2; found=1} END {if (!found) print "INFO: rknpu_driver=kernel-built-in-or-version-unavailable"}'
fi

if [[ -x "${YOLO_PYTHON}" ]]; then
  if "${YOLO_PYTHON}" - "${REPO_ROOT}/data/models/cuadc-fp16.rknn" <<'PY' >/dev/null 2>&1; then
from pathlib import Path
import sys
import numpy as np
from rknnlite.api import RKNNLite

rknn = RKNNLite(verbose=False)
try:
    assert rknn.load_rknn(str(Path(sys.argv[1]))) == 0
    assert rknn.init_runtime(core_mask=RKNNLite.NPU_CORE_0_1_2) == 0
    outputs = rknn.inference(inputs=[np.zeros((1, 640, 640, 3), dtype=np.uint8)])
    assert outputs is not None
finally:
    rknn.release()
PY
    ok "RKNN model load, runtime initialization, and authorized blank-frame inference"
  else
    fail "RKNN model/runtime/frame inference failed"
  fi
fi

if [[ -e /dev/video0 ]]; then ok "camera device /dev/video0"; else warn "camera device /dev/video0 absent"; fi
if command -v v4l2-ctl >/dev/null 2>&1; then v4l2-ctl --all -d /dev/video0 >/dev/null 2>&1 && ok "camera query" || warn "camera query failed"; else warn "v4l2-ctl unavailable"; fi

"${APP_PYTHON}" - "${REPO_ROOT}" <<'PY' >/dev/null 2>&1 && ok "default SEND remains off" || fail "configuration safety check failed"
from pathlib import Path
import sys, yaml
assert yaml.safe_load((Path(sys.argv[1]) / 'config/app.yaml').read_text())['executor']['send_commands'] is False
PY
echo "Health check finished: failures=${FAILURES} warnings=${WARNINGS}"
[[ "${FAILURES}" -eq 0 ]]
