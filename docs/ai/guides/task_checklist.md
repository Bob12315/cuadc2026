# 任务阅读清单

所有任务先读：

```text
README.md
AGENTS.md
docs/ai/architecture/current_architecture.md
docs/ai/architecture/action_contracts.md
docs/ai/architecture/deprecated_paths.md
docs/developer/coordinate_frames.md
docs/developer/field_origin_heading.md
docs/developer/safety.md
```

## Action 行为

追加：目标 `missions/common/actions/*.py`、`base.py`、`result.py`、`runner.py`、
`application/action_runtime.py`、`guidance/`、相关 `tests/unit/mission/` 与 `tests/unit/domain/`。

## Action Mission

追加：`missions/engine.py`、`config/action_missions/*.json`、
`scripts/validate_action_missions.py` 和 orchestrator/template 测试。

## 派发和连续命令

追加：`execution/dispatcher.py`、`execution/policy.py`、`execution/authorization.py`、
`execution/safety_pipeline.py`、`execution/safety_config.py`、`config/safety.yaml`、
`application/action_runtime.py`、
`telemetry_link/link_manager.py`、`command_broker.py`、`mavlink_encoder.py`、
`mavlink_command_encoder.py`、`mavlink_command_adapter.py`、相关测试。
这是高风险边界；确认 stop/zero、双门控和断线清理。

## Field Reference/坐标

追加：`field/coordinates.py`、`field/context.py`、`field/service.py`、
`field/calibration_session.py`、`field/calibration_transaction.py`、`field/_reference_store.py`、
`config/field_profiles/competition_runtime.json`、`missions/common/actions/goto_waypoint.py`、
涉及 FIELD 的 Action、Web UI runtime sampling 代码和坐标测试。
schema v3 只建立 GPS 场地参考，不伪造 LOCAL_NED origin；不得复制转换公式。

## Web UI

追加：`application/web_services.py`、`web_ui/server.py`、`web_ui/api_routers.py`、
`web_ui/routers/`、`web_ui/static/index.html`、`web_ui/static/app.js`、`web_ui/static/js/`
和 Web contract/integration tests。
不要把 terminal UI 当正式入口，也不要把 SystemRunner 注入 Web。

## YOLO

追加：`yolo_app/README.md`、`config/yolo.yaml`、`yolo_app/config.py`、
`rknn_detector.py`、publisher/receiver 协议和 YOLO 测试。

## 平台适配层/API 契约

追加：`docs/ai/plans/architecture_refactor_tasks.md`、
`docs/ai/plans/platform_adapter_interface_refactor_plan.md`、
`docs/ai/plans/stable_core_refactor_plan.md`、`docs/ai/architecture/interfaces.md`、
`contracts/ports.py`、`contracts/platform/`、`application/ports/`（尚未落地的计划目录）、
`application/web_services.py`、`application/system_control.py`、`application/mission_service.py`、
`application/action_runtime.py`、`telemetry_link/ports.py`、`web_ui/dto.py`、`web_ui/security.py`、
`web_ui/context.py`、`web_ui/api_routers.py`、`web_ui/routers/`、相关 adapter/contract/integration/SITL
测试。

执行 `AR-25` 时，每个会话只执行一个 `PA-xx`。迁移前先保持 characterization，不在无关 PA 中顺手
改变现有协议或行为；`PA-21` 至 `PA-24` 完成后，v1 routes 必须达到：Web 不导入 Mission engine 具体
模型、不直接 tick Action/Mission、不使用 HTTP 200 + `{ok:false}` 表示应用失败。旧 route 可在明确
兼容发布期保持 PA-00 响应形状，但只能委托同一 Application Port，并在 PA-27 满足门禁后删除。任何
发送 adapter 切换中，新旧 writer 都不得双写，并必须保持 SEND=false、空队列和新 session 启动。

## 稳定核心层

追加：`docs/ai/plans/architecture_refactor_tasks.md`、`docs/ai/plans/stable_core_refactor_plan.md`、
`docs/ai/plans/platform_adapter_interface_refactor_plan.md`、
`contracts/core/`、`missions/core/`、`missions/common/actions/`、`execution/`、
`application/core/`、`application/action_runtime.py`、`application/mission_service.py`、
`application/state_store.py`、`application/runner.py`、`app/bootstrap.py` 及对应 contracts/unit/property/
differential/integration/SITL 测试。

执行 `AR-26` 时，每个会话只执行一个 `CF-xx`，跨计划顺序固定为
`PA-00～PA-20 → CF-00～CF-28 → PA-21～PA-31`。`CF` 任务不得重复实现 Vehicle/Perception/Field、
CommandBroker、cancel/STOP barrier、event/blackbox 或 Web adapter；直接复用已验收 PA Port/DTO。
Action/Mission/Run 迁移必须保持一个 active top-level run、一个 advance owner、一个 snapshot publication、
一个核心 EffectDispatcher submit call site、一个核心 cancel policy/request producer、一个 CoreCycleDriver
ExecutionCancelPort call site 和一个 PA broker/wire + barrier 执行 owner。Web/兼容入口只排队命令或查询，
不能直接 tick；新旧 scheduler、
Dispatcher 或 writer 不得并存。`CF-28` 完成后，`PA-21` 只做冻结核心/PA Port conformance 检查，核心
文件只读且不得新建第二套 RunCoordinator；正式外部 Application DTO/Port 从 `PA-22` 开始。

## 禁止按旧清单工作

以下路径 missing/deprecated，不得新增以满足旧测试：

```text
missions/<mission>/mission.py
missions/<mission>/stages/<stage>
missions.visual_tracking
missions.rescue_competition
missions.common.control
```
