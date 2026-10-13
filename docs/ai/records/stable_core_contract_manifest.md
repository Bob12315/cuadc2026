# Stable Core v1 readiness manifest（2026-08-16）

本 manifest 描述 2026-08-16 已落盘的内部 stable-core readiness，不代表 CF-25 production cutover 或 CF-28 freeze
已经验收。严格冻结状态以 `python scripts/validate_stable_core.py --strict` 为准。

## 契约与 schema

- `contracts/core/common.py`：typed core IDs、`FrozenJson/FrozenObject`、稳定 reason。
- `contracts/core/time.py`：`CoreTime/CoreClock/SystemCoreClock/ManualCoreClock`。
- `contracts/core/input_state.py`：`RuntimeInputSnapshot`、`InputSnapshotRef`、freshness、fusion、SEND snapshot、
  `CycleCorrelation`；schema `1.0`。
- `contracts/core/effects.py`：11 个封闭 `EffectKind`，与 `execution/effect_registry.py` 一一对应。
- `contracts/core/action.py`：definition/codec/runner DTO、route-neutral lifecycle feedback、terminal-no-effect。
- `contracts/core/mission.py`：Mission v3、failure policy、step token、reducer intents；schema `3.0`。
- `contracts/core/execution.py`：grant、lease、effect envelope/attempt、safety/dispatch receipt。
- `contracts/core/run.py`、`run_io.py`、`system.py`、`cycle.py`：Run/System/I/O/cycle 状态与 Port。
- 复用 `contracts/platform/common.py` 的 canonical run/action/lease/session/generation/resource-version 类型；core
  不重新定义平台 identity。

## 唯一 owner 目标与当前实现

| 资源 | readiness owner | 当前 production 状态 |
| --- | --- | --- |
| immutable input | `SnapshotCollector → RuntimeInputStore` | 未切换，shadow/additive |
| Run/child/lease/cancel policy | `RunCoordinator` | 未切换，shadow/additive |
| typed effect gate/translation | `execution.dispatcher_v2.EffectDispatcher` | 未切换；旧 dispatcher 仍 production |
| SEND mutable state | `application.core.SystemSendState` | 未切换；旧 SEND state 仍 production |
| execution fence | `CoreExecutionFenceAuthority` | 未接 PA broker production wiring |
| cadence | `CoreScheduler → CoreCycleDriver` | 未切换；旧 SystemRunner 仍运行主循环 |
| cycle query | `CoreCycleStore` | additive；PA-20 recorder 未接 |

## Effect registry

`set_flight_mode`、`arm`、`takeoff`、`land`、`condition_yaw`、`change_speed`、
`local_position_target`、`global_position_target`、`body_velocity_target`、`set_servo`、
`set_vision_target` 均有唯一 capability/route/safety profile/translator 记录。payload 只允许 canonical
`payload_release` registration + `payload_release_v1` profile + `SetServo`，没有新增 disarm、RC override 或
`release_payload`。

## 已原生化与兼容范围

- 原生 feedback-driven one-shot：`takeoff`、`land`、`yaw_align`、`change_speed`。
- 其余 17 个正式 Action 仍通过显式 `LegacyActionModuleAdapter` 做 core shadow/readiness；因此 CF-07～CF-11、
  CF-25、CF-27 和 CF-28 不能标记完成。
- 三个正式 v2 Mission 可确定性编译为 v3；compiled blackboard expression 的解析位于 Mission/blackboard 层，
  Coordinator 不解析 `$` 字符串。

## 变更规则

普通扩展应新增 ActionDefinition/codec/ActionModule 或 Mission template，不修改 Runner/Coordinator/Dispatcher。
新增 EffectKind、状态、schema major、owner 或 Port 是架构变更，必须同步 registry、translator、safety、contract
tests、本文 manifest 与 ADR/计划记录。默认 `executor.send_commands: false` 不得改变。

## 验证入口

```text
python -m pytest -q tests
python scripts/validate_architecture_boundaries.py
python scripts/validate_action_missions.py
python scripts/validate_stable_core.py
python scripts/validate_stable_core.py --strict   # 当前预期失败并列出未切换 legacy blockers
python -m compileall -q app application contracts execution field guidance missions
    observability telemetry_link fusion yolo_app web_ui scripts tests
```
