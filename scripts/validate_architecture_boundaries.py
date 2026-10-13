#!/usr/bin/env python3
"""Fast static guardrails for the P1 package and safety boundaries."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            result.add(node.module.split(".")[0])
    return result


def py_files(directory: str) -> list[Path]:
    return sorted((ROOT / directory).rglob("*.py"))


def main() -> None:
    errors: list[str] = []
    for path in py_files("contracts"):
        leaked = imports(path) - {"__future__", "abc", "collections", "contracts", "copy", "dataclasses", "datetime", "enum", "hashlib", "json", "math", "time", "typing", "uuid"}
        if leaked:
            errors.append(f"contracts dependency leak {path.relative_to(ROOT)}: {sorted(leaked)}")
    core = py_files("fusion") + py_files("missions/common/actions")
    forbidden_core = {"fastapi", "uvicorn", "pymavlink", "cv2", "rknnlite"}
    for path in core:
        leaked = imports(path) & forbidden_core
        if leaked:
            errors.append(f"core import leak {path.relative_to(ROOT)}: {sorted(leaked)}")

    for path in py_files("missions/common/actions"):
        content = path.read_text(encoding="utf-8")
        imported = imports(path)
        telemetry_import_allowed = (
            "telemetry_link" in imported
            and "from telemetry_link.frames import" in content
            and "telemetry_link." not in content.replace("from telemetry_link.frames import", "")
        )
        if "pymavlink" in imported or ("telemetry_link" in imported and not telemetry_import_allowed) or "LinkManager" in content:
            errors.append(f"Action boundary violation {path.relative_to(ROOT)}")

    for path in py_files("app") + py_files("web_ui") + py_files("fusion"):
        content = path.read_text(encoding="utf-8")
        if "pymavlink" in imports(path):
            errors.append(f"pymavlink ownership violation {path.relative_to(ROOT)}")
        for forbidden in (".local_position(", ".body_velocity(", ".set_servo("):
            if forbidden in content:
                errors.append(f"known direct send call {path.relative_to(ROOT)}: {forbidden}")

    if len(py_files("app")) > 5:
        errors.append("app/ must contain at most five Python assembly/entry/config files")

    for path in py_files("missions"):
        if "app" in imports(path):
            errors.append(f"missions -> app dependency violation {path.relative_to(ROOT)}")

    for path in py_files("field"):
        if imports(path) & {"app", "application", "web_ui", "telemetry_link"}:
            errors.append(f"field dependency violation {path.relative_to(ROOT)}")

    for path in py_files("fusion"):
        if "telemetry_link" in imports(path):
            errors.append(f"fusion concrete telemetry dependency {path.relative_to(ROOT)}")

    for path in py_files("guidance"):
        leaked = imports(path) & {"app", "application", "execution", "fastapi", "missions", "telemetry_link", "web_ui"}
        if leaked:
            errors.append(f"guidance dependency violation {path.relative_to(ROOT)}: {sorted(leaked)}")

    web_source = "\n".join(path.read_text(encoding="utf-8") for path in py_files("web_ui"))
    for marker in ("SystemRunner", "runner.", "getattr(runner"):
        if marker in web_source:
            errors.append(f"Web runner dependency violation: {marker}")

    retired_composites = {
        "drop_sequence.py", "recon_sequence.py", "gps_drop_sequence.py",
        "gps_recon_sequence.py", "gps_multi_view_localize.py",
        "gps_recon_area_scan.py", "multi_view_localize.py", "recon_scan.py",
        "survey_area.py", "recon_inspect_target.py", "recon_descend_observe.py",
        "visual_land.py",
    }
    present = {path.name for path in py_files("missions/common/actions")}
    if present & retired_composites:
        errors.append(f"retired composite Actions present: {sorted(present & retired_composites)}")
    for path in py_files("missions/common/actions"):
        if "Action()" in path.read_text(encoding="utf-8"):
            errors.append(f"nested Action construction {path.relative_to(ROOT)}")

    for directory in ("app", "application", "contracts", "execution", "field", "fusion",
                      "guidance", "missions", "observability", "tools", "web_ui", "yolo_app"):
        for path in py_files(directory):
            if "pymavlink" in imports(path):
                errors.append(f"pymavlink ownership violation {path.relative_to(ROOT)}")

    for path in py_files("yolo_app"):
        content = path.read_text(encoding="utf-8")
        if {"pymavlink", "telemetry_link"} & imports(path) or "LinkManager" in content:
            errors.append(f"YOLO telemetry/control violation {path.relative_to(ROOT)}")

    app_config = (ROOT / "config/app.yaml").read_text(encoding="utf-8")
    if "send_commands: false" not in app_config:
        errors.append("config/app.yaml must default executor.send_commands to false")
    if errors:
        raise SystemExit("\n".join(errors))
    print("architecture boundaries validated")


if __name__ == "__main__":
    main()
