#!/usr/bin/env bash

profile_require_send_commands_off() {
  local app_config="$1"
  python - "${app_config}" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
in_executor = False
executor_indent = None
for raw_line in path.read_text(encoding="utf-8").splitlines():
    line_without_comment = raw_line.split("#", 1)[0].rstrip()
    if not line_without_comment.strip():
        continue
    indent = len(line_without_comment) - len(line_without_comment.lstrip(" "))
    stripped = line_without_comment.strip()
    if stripped == "executor:":
        in_executor = True
        executor_indent = indent
        continue
    if in_executor and indent <= (executor_indent or 0):
        in_executor = False
    if in_executor and stripped.startswith("send_commands:"):
        value = stripped.split(":", 1)[1].strip()
        if value == "false":
            raise SystemExit(0)
        raise SystemExit("Refusing profile operation: config/app.yaml executor.send_commands must be false")

raise SystemExit("Refusing profile operation: config/app.yaml executor.send_commands was not found")
PY
}

profile_apply() {
  local repo_root="$1"
  local profile_dir="$2"

  python "${repo_root}/scripts/config/render_profile.py" \
    --repo-root "${repo_root}" --profile "${profile_dir}/profile.yaml" --write
}
