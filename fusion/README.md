# Fusion Layer

`fusion/` 是独立业务层，不是 ROS2 节点，不是控制器，也不是 EKF。

它只做一件事：

- 将 `perception` 的当前锁定目标
- 与 `telemetry_link` 的 `DroneState`
- 以及 `telemetry_link` 的 `GimbalState`

合成为 Action 运行时可消费的 `FusedState`。运行时也提供 scene、drone 等快照，
`FusedState` 不是所有 Action 的唯一输入。

## 1. 职责边界

`telemetry_link` 负责：

- 飞控状态反馈
- 云台状态反馈
- 链路状态管理

`fusion` 负责：

- 读取当前目标状态
- 读取最新飞控状态
- 读取最新云台反馈
- 产出控制可直接消费的统一状态

当前阶段 `fusion` 不负责：

- 控制器
- EKF
- 多目标融合
- 复杂时间同步
- 严格三维坐标变换

## 2. 核心原则

### 2.1 云台角度必须来自反馈

`gimbal_yaw / gimbal_pitch` 必须来自 MAVLink 实际反馈，例如：

- `MOUNT_STATUS`
- `GIMBAL_DEVICE_ATTITUDE_STATUS`

不能把发送给云台的命令值直接当成当前姿态。

### 2.2 `ex_cam` 与 `ex_body` 不相同

YOLO 输出的是相机坐标误差：

- `ex_cam`
- `ey_cam`

相机方向与机体方向不一定一致；本模块的 `ex_body/ey_body` 只提供下面的近似。
当前 `align_descend` 从 scene 中读取归一化图像误差并自行计算 BODY_NED 速度，
不依赖这组融合误差；其相机朝向、符号和投放偏移约束见
[坐标系说明](../docs/developer/coordinate_frames.md)。

当前 `fusion/rules.py` 先将反馈中的云台角度从度转换成弧度，再采用工程近似：

```text
ex_body = ex_cam + radians(gimbal_state.yaw)
ey_body = ey_cam + radians(gimbal_state.pitch)
```

YOLO 的 `ex_cam/ey_cam` 是按图像半宽/半高归一化的无量纲误差，输出
`gimbal_yaw/gimbal_pitch` 才是弧度。当前实现把这两种量直接相加，是保留的工程近似；
不能把 `ex_body/ey_body` 声明为严格角误差，也不等同于视觉定位 Action 使用的地面投影
或完整三维变换。

当 `gimbal_valid=false` 时：

- 若 `require_gimbal_feedback=true`，则 `fusion_valid=false`
- 若 `require_gimbal_feedback=false`，则退化为：

```text
ex_body = ex_cam
ey_body = ey_cam
```

## 3. 有效性判定

`control_enabled` 判定：

```text
target_valid
AND tracking_state == "locked"
AND drone_state.control_allowed
AND drone_state.stale == False
AND drone_state.connected == True
```

`fusion_valid` 判定：

- `DroneState` 已连接、不陈旧，且至少一个姿态/速度/高度/GPS/相对高度/LOCAL 位置/电池有效标志为真
- 目标处于 `locked`
- 且云台反馈满足当前配置要求

如果 `drone_state.stale=True`：

- `control_enabled=False`
- `fusion_valid=False`

## 4. 使用方式

```python
from fusion.fusion_manager import FusionManager
from fusion.models import FusionConfig

manager = FusionManager(FusionConfig(require_gimbal_feedback=True))
fused_state = manager.update(perception_target, drone_state, gimbal_state)
```

Action 应通过运行时快照读取所需状态，不自行连接 YOLO 或 telemetry 链路。

当前 `FusedState` 由 Action Mission 运行时作为状态快照消费。它不进入旧
mission/stage/control 适配器，也不直接形成飞行命令；Action 输出必须经过
`ActionDispatcher` 与 P0 Safety Pipeline。

## 5. 正式状态检查

当前感知与融合状态通过 app/Web UI 查看。旧 `tools/fusion_debug.py`、独立 telemetry
文本工具及配套 `5010` 状态广播已删除；不再保留旧协议接收入口。
YOLO 目标会话使用 app 的感知 UDP 接收器；Virtual Nadir 仍使用独立的本机姿态旁路
`attitude_udp_publisher.py`（默认 `5011`），它与已删除的状态广播不同。
