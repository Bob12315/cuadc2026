# Phase 2 legacy test retirement

This is the Phase 2 retirement record, not a statement that its future-coverage
items are still absent. The table below preserves that phase's dispositions.
The current Action safety implementation and tests are under `execution/` and
`tests/unit/execution/`; FIELD/GPS contracts and transforms are covered by the
current Field Reference and frame contract tests. The removed CommandShaper,
FlightCommandExecutor and CoordinateTransform must not be recreated from this
historical table.

These tests were removed from the active suite because they imported deleted
mission/stage/control packages during collection. The production stack was not restored.

| Retired test | Missing legacy dependency | Remaining value and disposition | Current/future coverage |
| --- | --- | --- | --- |
| `test_app_config.py` | StageRegistry and visual/rescue configs | Current Web UI, telemetry parser and removed CLI assertions migrated; old mission config assertions retired | `test_action_lab_only_startup.py` |
| `test_approach_track.py` | `missions.visual_tracking.stages` and `missions.common.control` | Bound entirely to removed controller | No current replacement; do not restore old stage |
| `test_command_shaper.py` | `missions.common.control` | Limit/slew/NaN safety remains valuable but the old shaper is not the Action path | Future Action-compatible safety pipeline tests after architecture decision |
| `test_debug_runtime.py` | `missions.base` and old debug config | Tested legacy forced stage mode | Action Lab/Mission lifecycle tests replace the active use case |
| `test_downward_align_descend.py` | rescue stage and old control types | Camera alignment/descent behavior remains valuable | `test_align_descend_action.py` and dispatcher tests |
| `test_executor.py` | old FlightCommandExecutor/types | Gimbal conversion assertion was tied to inactive executor | Future dispatcher/LinkManager contract tests if required |
| `test_input_adapter.py` | old StageInputAdapter | Stage filtering/stability behavior is not in current Action path | Current runtime context/action tests; add an Action-specific adapter only if designed |
| `test_mission_navigation.py` | removed `missions.common.navigation` | Transform and tolerance math remains useful, but its frame convention is legacy | `test_runtime_context.py`; future CoordinateTransform/FieldReference tests |
| `test_mission_registry.py` | `missions.registry`, visual/rescue missions | Registry and switching behavior replaced by Action registry/templates | `test_action_lab_registry.py`, `test_action_mission_templates.py`, orchestrator tests |
| `test_mission_runner.py` | MissionRunner, MissionAction and old control context | Dispatch/once/dry-run behavior remains important | `test_action_lab_dispatch.py`, `test_action_runtime.py`, orchestrator tests |
| `test_overhead_hold.py` | visual tracking stage and old control types | Bound entirely to removed controller | No current replacement; do not restore old stage |
| `test_visual_tracking_mission.py` | old mission/control packages | Bound entirely to removed mission state machine | Action Mission template/orchestrator scenario tests |

The active suite must not add import ignores or recreate the deleted packages merely to
make these tests collect. Known mainline assertion failures are tracked separately from
these collection failures.
