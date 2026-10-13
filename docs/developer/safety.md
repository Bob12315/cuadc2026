# 安全边界

本文面向修改发送、限幅、停止、队列或门控逻辑的开发者。

本文描述当前真实发送链路和仍未解决的安全架构缺口。任何影响发送、限幅、停止、
队列或门控的修改都必须单独评审并先在 SITL 验证。

## Stable Core 迁移期约束

新 stable-core readiness 保持系统 SEND 与 ExecutionLease 双门控：`SystemSendState` 默认关闭，EffectDispatcher
同时校验 source/session/run/action/lease/auth/SEND generation，continuous BODY_NED 旧帧不做 pre-admission
retry，任何 child/run 退出都经过 lease revoke/cancel，必要时请求 stop barrier。当前 production 尚未原子切换
到该链，旧安全链仍必须保持唯一；不得同时启用两个 dispatcher、scheduler 或 cycle writer。

## 当前实际 Action 链路

```text
Action output
  → ActionRuntimeService
  → ActionDispatcher
  → ActionSafetyPipeline
  → VehicleCommandPort adapter
  → LinkManager
  → CommandBroker / CommandBrokerWorker
  → MavlinkEnvelopeWriter → MavlinkCommandEncoder
  → MAVLink
```

旧 `MissionStage → FlightCommand → CommandShaper → FlightCommandExecutor` 不再是当前
可运行 Action 链路，不能继续把 FlightCommandExecutor 写成当前唯一出口。

## Action-compatible Safety Pipeline

`ActionSafetyPipeline` 是所有当前 Action request 到达 `LinkManager` 前的统一裁决入口，
覆盖 run/source/telemetry/TTL、参数有限值与包线、Field Reference、payload 白名单以及
独立连续命令 watchdog。裁决明确记录 original/effective/rejected request。不得恢复已删除的
旧 control 栈，也不得绕过该入口。

## SEND 双门控

系统默认：

```yaml
executor:
  send_commands: false
```

Action 实发至少同时要求：

1. 系统 SEND/`send_commands` 开启；
2. 当前 Action/Mission 已建立绑定 run ID 和目标 source 的不可变授权。

加载模板、配置任务、查看状态和记录/确认 Field Reference 本身不得产生飞行运动
命令。连接 telemetry 不代表允许发送。

## 连续命令停止

- 停止、切换或失败退出连续 BODY_NED Action 时必须发送明确 zero/stop。
- 必须清理旧 continuous command 和 pending LOCAL_POSITION，避免恢复连接后重放。
- telemetry 断线、状态 stale 或控制不允许时必须停止连续发送。
- 丢失视觉目标不得沿用旧水平速度继续飞行。投放对准生成零速等待；完整模板的最终返航
  对准显式允许首次获取目标后丢失视觉时以零水平速度继续下降，到最终高度结束。
  这属于 `continue_descending_on_target_loss` 的明确策略，并非重放旧命令。

## Field Reference

- 未确认 Field Reference 时必须拒绝实发 FIELD 航点。
- mission 执行中 FIELD origin/heading 冻结。
- yaw、GPS Home 或 EKF Origin 变化不能自动改写已确认 reference。
- GPS A/B 用于确定场地原点和方向，粗 GPS 目标用于导航；投放对准使用视觉误差与飞控高度。
  当前 `align_descend` 优先读取 relative altitude，缺失时回退 `local_z`，没有直接使用激光测距输入。
  模板还允许 30 秒对准超时后继续投放，故不能把 `payload_released` 视为视觉对准或实物投放成功证明。

## 投放

唯一允许主线：

```text
payload_release Action → set_servo → MAV_CMD_DO_SET_SERVO
```

SERVO 输出通道不是遥控器 RC 输入通道。禁止 `release_payload()`、RC override 和
直接 pymavlink。本次 run 已授权但系统 SEND 关闭时仍不得调用 servo 实发。

## YOLO 与 telemetry 失效

- YOLO 超时：目标无效，不保留旧水平速度；投放对准零速等待，最终返航对准按上文明确的
  丢目标下降策略处理。不能把停止水平跟踪误写为所有场景都停止下降。
- telemetry heartbeat/RX 超时：connected=false、stale=true、control_allowed=false，
  链路运行时停止连续控制并清理陈旧命令。
- gimbal feedback 丢失时，依赖云台反馈的 Action 不得误放行。

## 实机前检查

- 已完成纯逻辑测试和正式 Action 安全链的 SITL 低速实发。
- `send_commands` 默认 false，双门控状态清楚。
- 坐标 frame、单位、正负号和 Field Reference 已确认。
- 速度、下降率和偏航参数保守。
- stop/zero、断线、丢目标和 stale 路径均已验证。
- payload SERVO 输出通道/PWM 已空载验证。
- 遥控器/地面站接管、场地和人员安全准备完成。

紧急情况优先使用遥控器或地面站接管；停止 app 只能作为辅助路径，不能成为唯一
安全手段。
