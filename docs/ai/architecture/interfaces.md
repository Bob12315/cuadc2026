# 当前接口契约

## Stable Core 内部接口（readiness，尚未 production cutover）

新核心只通过 `contracts/core/` 与 `contracts/platform/` 的 immutable DTO/Protocol 交互。周期固定为一次
snapshot capture、一次 RunCoordinator advance、driver 外部 Port 调用、一次 commit 和一次 CoreCycleSnapshot
publication。Action 只返回 typed Effect；Mission reducer 只返回 typed intent；Coordinator 不调用硬件 Port；
CoreScheduler 是新路径唯一允许持有 CoreCycleDriver 的 cadence owner。

当前 core readiness catalog 仅将 `land`、`change_speed` 注册为原生 feedback-driven
ActionModule。`takeoff` 和其余 Action 仍通过明确的 `LegacyActionModuleAdapter`；
离线目录中已有原生起飞 helper 不代表当前 catalog 已采用该实现。下列 legacy 接口
仍描述当前 production，而不是最终冻结接口。

## ActionModule

Action 位于 `missions/common/actions/`，由 `ActionRunner` 调用：

```text
start(params)
update(context) -> ActionResult
stop()
reset()
```

`contracts.action.ActionResult` 可包含 `done`、`failed`、`reason`、`output`、`detail`
和 typed `effects`。`actions` 是 effects 的请求字典兼容视图和序列化字段；构造函数
只接收 typed effects，不接收 `actions=`。Action 不直接发送 MAVLink。

## ActionRuntimeService

拥有 ActionRunner 和 ActionDispatcher，负责 start/tick/stop/reset。停止或切换导航类
Action 时负责清理连续命令和 pending LOCAL_POSITION，并按路径需要保持或发送 stop。

## MissionOrchestrator

读取 `config/action_missions/*.json` 对应步骤，负责顺序、标签跳转、重试、黑板
`save_as` 和失败策略。它只编排 Action，不构造 MAVLink。

## ActionDispatcher

接收 typed Effect，经请求白名单、不可变 `RunAuthorization` 和系统 SEND 双门控，
通过 `ActionSafetyPipeline` 后派发。当前 `execution/policy.py` 允许的请求类型为：

- `global_goto`（`goto_waypoint` 的 FIELD/已定位 GPS 目标）
- `body_velocity` / `flight_command`
- `set_servo`
- `set_mode` / `arm` / `takeoff` / `land`
- `condition_yaw` / `change_speed`
- `clear_continuous_commands`
- `yolo_lock_target`（只需 run 授权，不要求飞行 SEND，不进入 LinkManager）

当前 dispatcher 注入只写 VehicleCommandPort adapter，最终调用 LinkManager 公开接口。
FIELD 确认由 FieldService/Web 标定流程完成，不是 dispatcher 请求类型。
`ActionSafetyPipeline` 已执行参数校验、包线限制和独立 watchdog；slew/rate limit 仍禁用。
不得声称旧 FlightCommandExecutor 仍在生效。

## Runtime context

context 包含 drone、gimbal、link、perception、scene、health、command，以及当前
schema v3 runtime GPS Field Reference 投影及兼容 heading 字段。它只建立 FIELD↔GPS，
不提供 FIELD→LOCAL_NED 原点。坐标和字段命名见
[坐标系规范](../../developer/coordinate_frames.md) 和
[action_contracts.md](action_contracts.md)。

## LinkManager

是 app/telemetry_link 边界，公开 local position、body velocity、arm/mode/takeoff/
land、gimbal、servo 等语义接口。只有 telemetry_link 构造 MAVLink message。

`release_payload()` 已禁用。投放必须由 `payload_release` Action 生成 `set_servo`
request，再由 dispatcher 调用 servo 接口。

## YOLO UDP

YOLO 发布 `target` 和 `scene.detections`。app 的 UDP receiver 解码为 fusion 数据模型。
字段兼容变更必须同步更新 publisher、receiver、Web UI 和测试。

## 单位

- 距离：m。
- 速度：m/s。
- app local/body yaw：rad；明确命名为 deg 的接口除外。
- PWM：飞控 SERVO 输出 PWM。
- LOCAL_NED：北/东/下。
- BODY_NED：前/右/下。
- FIELD：右/前，Web 地图右/上。
