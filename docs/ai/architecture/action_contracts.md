# Action 契约与坐标参数迁移方向

本文描述当前 Action 主线的边界和未来参数方向。Phase 1 不修改运行时 schema。

## Action 边界

- Action 位于 `missions/common/actions/`，由 `ActionRunner` 驱动。
- Action 可以读取 runtime context，并返回结构化 result 和 action request。
- Action 不直接调用 pymavlink，不直接连接 `telemetry_link.LinkManager`。
- `ActionRuntimeService` 把 result 交给 `ActionDispatcher`。
- `ActionDispatcher` 执行系统 SEND + 本次 run 授权双门控；通过 `ActionSafetyPipeline`
  裁决后才调用 `LinkManager` 的公开接口。
- `config/action_missions/*.json` 只编排 Action，不包含 MAVLink message。

## 推荐坐标参数

FIELD 航点建议迁移为：

```json
{
  "field_x_m": 0.0,
  "field_y_m": 10.0,
  "altitude_m": 3.0,
  "field_yaw_deg": 0.0
}
```

`goto_waypoint` 只接受 FIELD 输入并只发送 GLOBAL GPS 目标。默认
`yaw_mode=field_heading`，以 `field_yaw_deg=0.0` 始终朝向 FIELD +Y（已定义为
场地中央方向），并转换为飞控北基准实际偏航。`yaw_mode=hold` 仅保留为明确
指定时的兼容选项：它锁定启动后的首个有效飞行器 yaw，并在每个 GLOBAL GPS
目标中显式下发该角度。
不再生成 LOCAL_NED 航点。BODY_NED 速度建议使用
`vx_forward_mps/vy_right_mps/vz_down_mps`。完整符号约定见
[坐标系规范](../../developer/coordinate_frames.md)。

## 兼容现状

`goto_waypoint` 的旧 `x/y` 在迁移期仅表示 FIELD `field_x_m/field_y_m`；
不再代表 LOCAL_NED。新模板必须使用语义化 FIELD 字段。任何迁移必须同步更新
Action Lab 说明、Action Mission JSON、validator 和回归测试。

FIELD 航点必须经过唯一的 `field/coordinates.py` 进行转换；未确认
Field Reference 时必须拒绝实发。BODY_NED 速度不参与 FIELD 转换。

## 起飞后的固定场地朝向

`takeoff` 默认在高度门槛达到后，发送绝对 `condition_yaw` 并等待稳定的
偏航遥测，直到机头朝向 `FIELD +Y`（`yaw_mode=field_heading`、
`field_yaw_deg=0.0`）。默认容差为 5°、连续 2 个遥测更新、偏航阶段超时为
12 秒。字段朝向尚未确认时，Action 会在切换模式或解锁前以
`field_heading_not_ready` 失败；`yaw_mode=hold` 是唯一保留原行为的显式兼容选项。

## 速度切换契约

航点阶段的地速切换只能走正式发送链：

```text
change_speed Action → ActionDispatcher → LinkManager.change_speed
→ MAV_CMD_DO_CHANGE_SPEED
```

`goto_waypoint.max_horizontal_speed_mps` 是到点速度门槛，不是飞行限速。需要限定
任务阶段地速时，必须显式编排 `change_speed` Action，并在阶段结束或失败恢复路径中
恢复后续阶段速度。`LinkManager` 会把当前速度目标附加到后续位置航点；发送线程检测到
Guided 由速度控制切回位置控制时，必须先发送位置目标，再立即重发
`MAV_CMD_DO_CHANGE_SPEED`，防止飞控位置控制初始化覆盖阶段限速。Action 不得直接调用
`LinkManager` 或 pymavlink。

## 投放契约

投放主线只能是：

```text
payload_release Action → set_servo → MAV_CMD_DO_SET_SERVO
```

禁止 `release_payload()`、RC override 和 Action 内直接发送 MAVLink。舵机输出通道
不是遥控器 RC 输入通道。
