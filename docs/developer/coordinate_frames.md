# 坐标系规范

本文面向需要处理飞控、场地或图像坐标的开发者。

本文是项目坐标系的唯一规范源。不要再新增平行的 `coordinate_systems.md`。
MAVLink frame 常量统一从 `telemetry_link/frames.py` 导入，不得在 Action 中硬编码整数。

## LOCAL_NED

| 语义名 | 兼容名 | 正方向 | 单位 |
| --- | --- | --- | --- |
| `north_m` | `x` | 北 | m |
| `east_m` | `y` | 东 | m |
| `down_m` / `z_down_m` | `z` | 下 | m |

`LOCAL_NED` 由飞控 EKF 提供。ArduPilot EKF Origin、GPS Home、上电点都不等于
比赛 FIELD 原点。

## BODY_NED

| 语义名 | 正方向 | 单位 |
| --- | --- | --- |
| `vx_mps` / `vx_forward_mps` | 机头前方 | m/s |
| `vy_mps` / `vy_right_mps` | 机体右方 | m/s |
| `vz_mps` / `vz_down_mps` | 下 | m/s |

BODY_NED 速度不经过 FIELD 坐标转换。

## 下视相机图像误差

目标定位使用未镜像的检测输入，约定如下：

- `pixel_x` 向右为正，`pixel_y` 向下为正。
- `ex_norm`：目标在画面右侧为正。
- `ey_norm`：目标在画面下方为正。
- 当前下视相机默认 `image_x_sign = 1.0`、`image_y_sign = -1.0`。

因此在机体 yaw 为 0 时，正 `ex_norm` 映射到机体右方/LOCAL_NED 东向偏移；正
`ey_norm` 经 `image_y_sign=-1` 映射到机体后方/LOCAL_NED 南向偏移。不要恢复只影响
显示的 mirror、输入层 mirror 或历史 `vy_sign=-1.0` 修正。

对准下降每帧选择归一化距离画面中心最近的检测，直接以 `ex_norm/ey_norm` 控制机体速度。
默认 `vx_sign=-1.0`，即目标偏上时向前飞；横向为 `vy_sign=1.0`。

到达初识别桶航点并进入 `align_descend` 后，立即使用 `release_target_ex/ey`
作为图像对准目标，整个下降过程以 `ex/ey - release_target_ex/ey` 控制；
Web UI 同时显示该偏差目标红点。下降阶段仍使用普通增益和限速，
到最终高度后切换最终增益和限速，并进行稳定等待、对准投票和速度判定。
投放速度门槛 `release_max_speed_mps` 只比较水平速度 `sqrt(vx² + vy²)`，
不包含垂直速度 `vz`；水平速度分量缺失或非有限值时不放行。

## FIELD

FIELD 是比赛场地坐标系：

- `+Y` 是场地前方。
- `+X` 是场地右方。
- Web UI 地图上方是 `+Y`。
- Web UI 地图右方是 `+X`。

FIELD 原点和 heading 必须由用户显式确认。mission 执行期间二者冻结；yaw
跳变、罗盘突变、GPS Home 更新或 EKF Origin 更新都不得自动改变它们。只有用户
重新确认后才能改变。完整来源和确认规则见
[field_origin_heading.md](field_origin_heading.md)。

## 高度符号

用户输入 `altitude_m` 向上为正；MAVLink LOCAL_NED 的 `z_down_m` 向下为正：

```text
z_down_m = -altitude_m
```

禁止用裸 `z` 表示高度而不说明 frame 和符号。

## FIELD 到 LOCAL_NED

设 `field_heading_yaw_rad` 是 FIELD `+Y` 在 LOCAL_NED 中的 yaw；`0` 表示北，
`pi/2` 表示东：

```text
forward_N = cos(field_heading_yaw_rad)
forward_E = sin(field_heading_yaw_rad)

right_N = -sin(field_heading_yaw_rad)
right_E =  cos(field_heading_yaw_rad)

local_N = origin_N + field_y_m * forward_N + field_x_m * right_N
local_E = origin_E + field_y_m * forward_E + field_x_m * right_E
z_down = -altitude_m
```

`field/coordinates.py` 是 FIELD↔LOCAL_NED 转换的 **唯一实现源**。
`field/context.py` 和 `missions/common/actions/goto_waypoint.py` 中的
历史公式在迁移完成前仍需保持行为一致；后续各 Action 必须通过
`CoordinateTransform` 进行转换，不得继续复制公式。

## 接口约定

- LOCAL_NED 新接口优先使用 `north_m/east_m/z_down_m`。
- FIELD 新接口优先使用 `field_x_m/field_y_m/altitude_m`。
- BODY_NED 新接口优先使用 `vx_forward_mps/vy_right_mps/vz_down_mps`。
- `x/y/z/frame/waypoint_mode` 仅作为迁移期兼容参数。
- 投放只能走 `payload_release` Action → `set_servo`，不能使用
  `release_payload()`、RC override 或直接 pymavlink。
