# Telemetry Command Audit

本文核对当前 `MavlinkEnvelopeWriter` 与同步 `MavlinkCommandEncoder` 的编码行为。
旧文本命令解析器、独立 telemetry 终端入口、CommandSender 线程和旧队列已删除。
正式人工入口是 Web UI，Action 请求经过运行时、Dispatcher、SEND/授权与 Safety Pipeline；
低层支持某个命令不代表 Action policy 允许它。

协议参考：[MAVLink common 命令定义](https://mavlink.io/en/messages/common.html)、
[ArduPilot Copter Guided 命令说明](https://ardupilot.org/dev/docs/copter-commands-in-guided-mode.html)。

## 机体速度与停止

当前 BodyVelocity 使用 `SET_POSITION_TARGET_LOCAL_NED`，frame 为 BODY_NED：

- 提供完整的前/右/下三轴速度，单位 m/s。
- 未提供 yaw/yaw rate 时 mask 忽略两者。
- 提供 yaw 时使用速度 + yaw；提供 yaw rate 时使用速度 + yaw rate。
- yaw 与 yaw rate 不能同时指定；编码器拒绝该组合。
- yaw 单位 rad，yaw rate 单位 rad/s。

ArduPilot Guided 的偏航目标不能脱离位置/速度/加速度三轴单独作为 setpoint。
停止屏障使用 StopMotion，显式发送三轴零速及零 yaw rate，并由 broker 取消陈旧连续命令。
不能以删除队列或等待遥测超时替代零/停止指令。

## 位置目标与阶段速度

GlobalPositionTarget 使用 `SET_POSITION_TARGET_GLOBAL_INT`，当前 typed writer 固定为
GLOBAL_RELATIVE_ALT_INT（frame 6），高度相对 Home；经纬度编码为度乘 1e7。
LocalPositionTarget 使用 LOCAL_NED，单位米，z 正向向下；当前 FIELD 航点只生成 GPS/GLOBAL。
两类位置目标的可选 yaw 均为 rad。

ChangeSpeed 使用 `MAV_CMD_DO_CHANGE_SPEED`，typed writer 当前固定地速类型 1。
底层编码器虽保留位置切换后的速度重发能力，v2 adapter/typed envelope 未携带速度覆盖，
writer 传入 `_speed_overrides=[]`；不能声称当前模式切换后一定会重发阶段速度。
此缺口须单独验证和修复，不在旧链路删除任务中顺带更改发送行为。

## 一次性命令

| Typed 请求 | MAVLink 编码 | 当前参数 |
| --- | --- | --- |
| Arm | MAV_CMD_COMPONENT_ARM_DISARM | param1 为 1 或 0 |
| Takeoff | MAV_CMD_NAV_TAKEOFF | param7 为目标高度米 |
| Land | MAV_CMD_NAV_LAND | 当前参数为 0，当前位置降落 |
| SetMode | pymavlink mode_mapping + set_mode | 按飞控模式映射校验名称 |
| ConditionYaw | MAV_CMD_CONDITION_YAW | yaw 度，速度固定 20°/s，direction 固定 0；保留 relative 标志 |
| SetServo | MAV_CMD_DO_SET_SERVO | param1 为 SERVO 输出通道，param2 为 PWM 微秒 |
| GimbalAngle | MAV_CMD_DO_MOUNT_CONTROL | typed yaw/pitch rad 转成度；roll 固定 0，mount mode 来自配置 |
| GimbalRate | MAV_CMD_DO_GIMBAL_MANAGER_PITCHYAW | pitch/yaw rate rad/s 转成 deg/s，角度目标为 NaN |

发出 Takeoff/Land/位置命令不等于 Action 已完成；完成判断仍依赖新鲜状态反馈。
云台角度以真实反馈为准，不得用发送目标替代姿态。

## 消息频率请求

连接建立时，运行时可直接调用同步编码器请求遥测频率；这是消息订阅，不是飞行控制。
`request_message_interval` 使用 `MAV_CMD_SET_MESSAGE_INTERVAL`：

- param1 为 message ID；
- 正频率按 `int(1e6 / rate_hz)` 编码 interval 微秒；
- 当前编码器对非正频率发送 interval -1，表示禁用该消息流；
- 协议 interval 0 才是请求默认频率，与 -1 不同。

语义已对照 [MAV_CMD_SET_MESSAGE_INTERVAL](https://mavlink.io/en/messages/common.html#MAV_CMD_SET_MESSAGE_INTERVAL)。
旧诊断文本 `default` 别名随解析器删除，不再作为可用操作命令。

## 投放与发送归属

唯一投放路径为 `payload_release` Action -> 授权与 payload 白名单 -> `set_servo`。
旧 `release_payload` 方法、枚举和编码分支已删除，不存在载荷映射 fallback，也不得使用 RC override。
SERVO 输出通道与 RC 输入通道不同，PWM 必须经过实物空载验证。

CommandBrokerWorker 是当前 wire 调度者，同步编码器不启动线程、不读取旧队列、不做命令重试。
ACK 相关状态由 broker/ACK router 管理，不能把 ACK 或服务存活当作动作到位证明。
