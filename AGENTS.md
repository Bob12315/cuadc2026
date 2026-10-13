# AI Development Entry

The generic app (Action Mission, telemetry, and Web UI) supports Linux x86_64
and Linux ARM64. Local YOLO inference remains Linux ARM64 RK3588 only.

## Current Architecture

- Action Mission is the only current mission mainline.
- Web UI is the only formal human-operation entry.
- Actions live in `missions/common/actions/`.
- Mission templates live in `config/action_missions/*.json`.
- The current send path is `Action output -> ActionRuntimeService -> ActionDispatcher -> ActionSafetyPipeline -> VehicleCommandPort adapter -> LinkManager -> telemetry_link`.
- Read `docs/ai/architecture/current_architecture.md` and
  `docs/ai/architecture/deprecated_paths.md` before changing architecture.

Do not restore or add the deprecated mission/stage/control stack, including
`missions/<mission>/mission.py`, `missions/<mission>/stages/<stage>`,
`MissionRunner`, `StageRegistry`, `CommandShaper`, or `FlightCommandExecutor`.

## Platform Rules

- YOLO inference uses `RKNNLite` with an RKNN `.rknn` model on the RK3588 NPU.
- Do not add x86, CUDA, PyTorch, or GPU inference paths.
- The current real-hardware deployment model is `data/models/cuadc2026-fp16.rknn`.
- Configuration drift: `config/yolo.yaml` and `config/profiles/rk3588-real/profile.yaml`
  currently select `data/models/cuadc-fp16.rknn`. This does not change the deployment
  requirement above; verify the intended model and its detection/calibration evidence
  before deployment. Do not silently replace models during unrelated edits.
- RK3588 can support INT8, but this project's current INT8 models are deprecated and must not become the default without new calibration and detection validation.
- Runtime state, logs, SITL files, generated videos, and blackbox data belong under `runtime/`.

## Safety Rules

- Keep `executor.send_commands: false` as the default.
- Preserve the system SEND and Action send-actions double gate.
- Actions must not call pymavlink or `LinkManager` directly; dispatch goes through `ActionRuntimeService` and `ActionDispatcher`.
- Continuous BODY_NED commands must stop with an explicit zero/stop command and stale commands must be cleared.
- The old CommandShaper/FlightCommandExecutor path is not active and must not be restored.
  `ActionSafetyPipeline` is the current Action-compatible safety replacement; it applies
  request validation, safety limits and an independent BODY_NED watchdog. Slew/rate
  limiting remains disabled in `config/safety.yaml` and is a documented gap.
- Payload release is only `payload_release` Action -> `set_servo`; do not use `release_payload` or RC override.
- `yolo_app/` must not connect to MAVLink or generate flight commands.

## Configuration

- App: `config/app.yaml`
- Telemetry: `config/telemetry.yaml`
- YOLO: `config/yolo.yaml`
- Action Mission templates: `config/action_missions/*.json`

## Read Before Editing

1. `README.md`
2. `docs/ai/README.md`
3. `docs/ai/architecture/current_architecture.md`
4. `docs/ai/architecture/action_contracts.md`
5. `docs/ai/architecture/deprecated_paths.md`
6. `docs/developer/coordinate_frames.md`
7. `docs/developer/field_origin_heading.md`
8. `docs/developer/safety.md`

Use `docs/ai/guides/task_checklist.md` for task-specific files.
