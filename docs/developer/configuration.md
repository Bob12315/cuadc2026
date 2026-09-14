# 配置说明

本文面向需要修改项目配置和任务模板的开发者。

## 当前配置分层

| 路径 | 用途 |
| --- | --- |
| `config/app.yaml` | app 生命周期、服务、Web UI、blackbox 和系统 SEND 默认值 |
| `config/telemetry.yaml` | MAVLink 数据源、端点、频率、超时和发送参数 |
| `config/yolo.yaml` | RKNN 模型、视频源、UDP、显示和录像 |
| `config/action_missions/*.json` | 当前 Action Mission 模板 |
| `config/profiles/rk3588-real/` | 实机 profile |
| `config/profiles/rk3588-sitl/` | SITL profile |

旧 `missions/<mission_name>/config.yaml` 属于 deprecated mission/stage 架构，不再作为
新任务配置位置。

## app.yaml

关键安全项：

```yaml
ui:
  web_enabled: true

executor:
  send_commands: false
```

- Web UI 是正式操作入口。
- terminal/curses UI deprecated；`config/app.yaml` 不再提供正式 terminal 开关。
- `send_commands` 默认必须为 false；连接 telemetry 不等于允许实发。
- `mission.name: action_lab_only` 是旧 mission 配置表面的兼容标识；当前比赛任务不从
  这里选择，而是由 Web UI 加载 `config/action_missions/*.json`。

## Action Mission JSON

模板由 Web UI 加载并交给 MissionOrchestrator。步骤引用
`missions/common/actions/` 注册的 Action，可包含 params、label、save_as、失败策略和
重试。修改后运行：

```bash
python scripts/validate_action_missions.py
```

新坐标参数方向见 [../ai/architecture/action_contracts.md](../ai/architecture/action_contracts.md)。

## telemetry.yaml

根配置选择 `real` 或 `sitl` 数据源；具体端点必须按硬件/仿真环境确认。
`control_send_rate_hz` 限制连续命令发送频率。断线时必须清空连续控制和云台速率
命令。切换数据源或重连后系统 SEND 保持关闭。

## yolo.yaml

当前部署模型是：

```text
data/models/cuadc-fp16.rknn
```

配置相对路径应为：

```yaml
model_path: "../data/models/cuadc-fp16.rknn"
```

根 `config/yolo.yaml` 和实机 profile 使用 `cuadc-fp16.rknn`；SITL profile 使用
`gazebo_dataset-fp16.rknn`。

RK3588/RKNN 可以支持 INT8，但本项目当前 INT8 模型已废弃。除非重新量化、校准并
验证检测正常，否则不得切回 INT8 默认部署。

## profile 切换

```bash
bash scripts/config/apply_rk3588_real.sh
bash scripts/config/apply_rk3588_sitl.sh
```

profile 只保存相对根配置的差异，不复制整套配置或 Mission。`save_rk3588_*.sh`
已禁用自动快照；调整时直接审核 `profile.yaml`。切换脚本只写配置，不重启服务。
当前 SITL profile 保留 Gazebo FP16 模型、UDP 5600 视频和 Virtual Nadir 开关；
real profile 显式恢复真实数据源、USB by-id 路径、实机 FP16 模型及实机近似视场角；
按用户要求启用 Virtual Nadir。两套 profile 分别恢复自己的相机和输出视场角。

脚本执行前必须确认 `executor.send_commands: false`。不要把运行日志、录像或 SITL
产物写入 config/data；它们属于 `runtime/`。

### 2026-09-07 板卡配置盘点

目标：`pi@10.101.31.108:/home/pi/cuadc2026`。本地根配置与板卡同步；
app/YOLO 服务未重启，运行进程仍使用此前配置，不能据此宣称真实链路已就绪。

- USB 摄像头：HD 720P Webcam，uvcvideo，稳定采集路径为
  `/dev/v4l/by-id/usb-HD_720P_Webcam_HD_720P_Webcam_20130901-video-index0`
  （本次对应 `/dev/video41`）。MJPG 640×480、30 FPS 已连续读取 5 帧；
  解码有少量 JPEG 尾部冗余数据告警，长期稳定性仍待验证。
- `eth0` 已有 NetworkManager 静态地址 `192.168.10.15/24`，无网关；
  `eth1` 使用 DHCP。两口均 DOWN，无活动 IPv4。未修改系统网络配置。
- 根 telemetry 选择 `real`/`eth`。`udpin 0.0.0.0:15001` 仍是待确认的原模板端点，
  不是已探测到的飞控设置。需确认实际插口、飞控 IP/掩码、UDP/TCP 及方向、端口；
  若采用当前 udpin 模板，飞控需要向板卡实际网口 IP 的 UDP 15001 发送 MAVLink。
- 按实机配置说明选择 `cuadc-fp16.rknn`，板卡文件 SHA-256：
  `347bc25fac71f06d2a6e81dfce155b82980e315b8a337b34ad7bc97913fdaadf`。
  未并行启动第二个 NPU runtime；实际类别顺序及检测效果仍需实物验证。
- Virtual Nadir 关闭。保留的近似标定参数仅供 SITL 使用，不能视为 USB 相机标定。
  Action Mission 中 `114.591559`/`98.864783` 视场角也尚未替换；需真实内参、安装朝向、
  高度来源，以及云台是否存在/能否提供反馈。`require_gimbal_feedback: true` 保持不变。
- 投放舵机通道/PWM、场地参考和任务飞行参数尚未实机确认；未修改或运行 Mission。
  `executor.send_commands: false` 以及服务的 `--send-commands false` 保持不变。

后续确认（同日）：用户指定 eth0，网口已 UP，地址 `192.168.10.15/24`。
只读 UDP 接收已解码来自 `192.168.10.14:62510`、发往板卡 `15001` 的 MAVLink
HEARTBEAT：sysid=1、compid=1、autopilot=3、type=2。当前 `eth/udpin/15001`
端点已有依据；这不代表姿态频率或完整数据链已验证。未发送 MAVLink 请求或控制命令。
用户要求虚拟云台并确认方向正常；“28mm、85°”的焦距含义及视场角轴向仍待确认，
暂不将 85° 任意写入水平/垂直字段，Virtual Nadir 保持关闭直到参数确认。舵机按用户要求暂不处理。

## Web 配置编辑

Web UI 可以预览、保存和恢复允许的配置文件。应用/重连/重启前必须关闭 SEND，完成
后也不得自动重新开启。配置中的 bool 必须使用 YAML 原生 `true/false`，不能使用
字符串。


用户补充参数后（同日）：镜头为 2.8 mm，85° 为对角视场角，要求虚拟云台。
根配置及 real profile 已启用 Virtual Nadir，camera/output 水平视场角 `72.487673°`、
垂直视场角 `57.603876°`。换算为 `2*atan(tan(85°/2)*轴向像素数/800)`，
其中 800 是 640×480 对角像素数。假设方形像素、针孔投影、85° 对应当前采集区域；
`approximate_calibration: true`，畸变暂为零，安装旋转沿用用户确认的正常下视方向。
SITL profile 显式保留原 `114.591559°/98.864783°`，切换不会继承实机视场角。

飞控 IP `192.168.10.14`、板卡 eth0 `192.168.10.15/24`、UDP 15001 与已观察到的
心跳一致。`udpin` 中 `eth_host: 0.0.0.0` 是本机监听地址，不应填飞控 IP。
用户提供的“端口协议 2”属于设备侧枚举；本程序只使用 `eth_mode: udpin`，没有该数值字段，
未擅自写飞控参数。网关文字 `192.10.3` 不是完整 IPv4，未设置网关；同网段直连无需网关。

本次只更新文件，未重启服务。Virtual Nadir 的 ATTITUDE 实测频率/时序及相机畸变仍待验证。
任务模板中独立的相机视场角仍为仿真值，尚不适合直接执行实机 GPS 目标定位；本次未改 Mission。
物理云台反馈要求仍保持原值，虚拟图像增稳本身不会产生物理云台反馈。舵机暂不处理。


### 2026-09-07 室内无 GPS 测试

用户授权测试后已重启 app/YOLO，真实配置已加载。已通过：real/eth 连接、未解锁
STABILIZE 状态、有效 ATTITUDE、USB 640×480 采集、实机 FP16 RKNN 双 worker
推理、Virtual Nadir `rectify_valid=True/reason=ok`、新鲜 YOLO UDP scene，以及
HTTP 200 MJPEG 解码（网页输出 480×360）。姿态约 83 Hz，端到端处理约 12 FPS，
日志图像年龄约 100–140 ms。画面中未检出目标，模型实物识别率/类别及运动增稳效果未验收。
启动短暂 `attitude_history_empty` 后恢复；RKNN 静态模型动态范围查询警告未阻止推理。

补测 `request_message_intervals: true` 触发 `LinkManager reconnect failed:
ack_key_quarantined`，反复重连；已恢复 false，仅保留原 ATTITUDE 请求。
此外，app 单独重启后接收 YOLO 报 `session_mismatch`，需一并重启 YOLO 恢复。
这两项属于待修复的软件问题，测试期间未修改 ACK/发送安全链。
高度/电池遥测尚未验证，不能只归因于室内无 GPS。

GPS、场地参考和飞行任务未测试；未更改模式、解锁、运动或舵机命令，SEND 全程关闭。
物理云台反馈为无效，`require_gimbal_feedback: true` 的后续融合要求仍待处理；
这不妨碍本次虚拟图像增稳链工作。任务模板的独立视场角仍待同步到实机参数。


### 2026-09-07 电池遥测请求修复

已修复 SourceRuntime 启动时多个 `MAV_CMD_SET_MESSAGE_INTERVAL` 共用 ACK key 的
连续请求时序：收到 ACK 或超时关闭 slot 后，继续等待配置的 ACK quarantine，
再发送下一条请求；等待可由 stop_event 中断，停止后不再请求后续消息。
保留 AckRouter 的隔离保护，未修改飞行命令授权、双门控或重试策略。
新增 ACK 正常/缺失及停止中断回归测试，相关 telemetry/broker 测试 107 项通过。

根/real profile 已开启 `request_message_intervals: true`，SITL profile 保留 false。
部署并同时重启 app/YOLO 后：连接稳定，battery_valid=true，采样电压 24.745 V，
飞控上报电量 95%；altitude_valid/relative_alt_valid=true，GPS fix_type=1（无定位）。
姿态约 83 Hz，Virtual Nadir rectify_valid=true，感知新鲜；SEND=false、未解锁。
百分比为飞控报告值，本次未校准电源模块。此前批量遥测请求故障已解决；
app 单独重启的 YOLO session_mismatch 问题未在本次修改。
