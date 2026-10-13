# telemetry_link

`telemetry_link/` 是 MAVLink 通信适配层，负责飞控连接、状态接收、缓存、命令仲裁与编码。
它由 app 组合使用；Web UI 是唯一正式人工操作入口。旧的
`python -m telemetry_link.main` 入口已删除。

## 状态链路

```text
Serial / UDP / TCP / ETH
  -> MavlinkClient
  -> TelemetryReceiver
  -> StateCache
  -> LinkManager.get_latest_*()
```

`DroneState` 提供姿态、速度、高度、GPS、电池、模式和数据新鲜度；`GimbalState` 提供实际
云台反馈；`LinkStatus` 提供连接、重连、最近收发时间及目标 system/component。
单位和详细接口见[接口契约](../docs/ai/architecture/interfaces.md)和
[Telemetry Link 开发说明](../docs/developer/telemetry_link_interfaces.md)。

在 `dual` 模式下，real/sitl 各自维护连接和状态，业务读取与命令发送面向 `active_source`。
source/session 切换时必须清除陈旧命令，不能将旧 source 的目标继续发送到新连接。

## 正式发送链路

```text
Web UI -> ActionRuntimeService
  -> Action 输出 -> ActionDispatcher -> ActionSafetyPipeline
  -> LinkManager -> CommandBroker -> CommandBrokerWorker
  -> MavlinkEnvelopeWriter -> MavlinkCommandEncoder -> MAVLink
```

遥测只有一个部署发送后端，不能通过配置切回旧线程/队列。
`CommandBroker` 管理 typed `VehicleCommandEnvelope`、有效期、替换、停止屏障与 ACK 状态；
`CommandBrokerWorker` 是唯一 wire 调度者，`MavlinkCommandEncoder` 仅同步编码。
旧 `CommandSender` 线程、`CommandQueue` 与 shadow 兼容栈已删除。
`ControlCommand`、`GimbalRateCommand`、`ActionCommand` 是适配器兼容模型，不能取代 Action
的 typed 请求契约。公共坐标契约源位于 `contracts/frames.py`，
`telemetry_link/frames.py` 是兼容导出。

Action 不得直接调用 `LinkManager` 或 pymavlink。读写 transport 的组合由 app 和执行层
负责；Action 通过运行时快照读取状态，输出请求由正式发送链路处理。底层支持某个命令
不等于它已被 Action policy 授权。请求清单见
[Action 契约](../docs/ai/architecture/action_contracts.md)。

## 主要文件

| 文件 | 职责 |
| --- | --- |
| `config.py` | 解析根目录 `config/telemetry.yaml` 及 CLI 覆盖 |
| `link_manager.py` | source 生命周期、状态访问和 transport 入口 |
| `mavlink_client.py` | pymavlink 连接封装 |
| `telemetry_receiver.py`、`telemetry_parser.py` | 接收、解析与单位转换 |
| `state_cache.py` | 线程安全缓存与新鲜度判定 |
| `command_broker.py` | 当前 typed 命令仲裁与 worker |
| `mavlink_encoder.py` | 当前 envelope writer |
| `mavlink_command_encoder.py` | 同步 MAVLink 编码，不创建 sender 线程 |
| `attitude_udp_publisher.py` | 当前 Virtual Nadir 本机姿态 UDP 发布 |
| [COMMAND_AUDIT.md](COMMAND_AUDIT.md) | 低层命令参数与保留诊断语法 |

## 配置与运行

正式服务由 app 启动，从仓库根目录查看参数：

```bash
python -m app.main --help
```

启动和 Web 凭据配置见[部署指南](../docs/ai/guides/DEPLOY_RK3588.md)。
`config/telemetry.yaml` 管理 `data_source`、`active_source`、连接端点、超时、请求消息频率、
本机姿态广播。旧 writer 选择、terminal UI 与 `state_udp_*` 配置已删除，加载旧键会明确拒绝。

旧独立 telemetry/fusion debug、文本命令分发器和 `5010` 状态广播已删除。
正式人工操作和链路状态查询使用 Web UI；配置解析只保留供 app 使用的文件加载接口。

## 安全边界

- 系统 SEND 默认关闭，并保留 Action send-actions 双门禁和 Safety Pipeline。
- 连续 BODY_NED 命令结束时显式发零/停止；断线、超时、重连和 source 切换清除陈旧命令。
- 接收器只解析状态，发送层不计算视觉控制律或任务决策。
- 云台角度来自实际反馈；不得把发送目标当作当前姿态。
- HEARTBEAT 只接受 autopilot 来源，避免误用 GCS heartbeat。
- `relative_altitude` 不由 `VFR_HUD.alt` 覆盖。
- 投放只允许 `payload_release` Action -> `set_servo`；底层 `release_payload()` 已删除。
- 不在此层实现 YOLO、fusion 或旧 mission/stage/control 栈。
- 日志和其他运行产物写入 `runtime/`。
