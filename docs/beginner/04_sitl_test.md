# 04：SITL 测试

## 本章目标

在 ArduPilot SITL 中验证 telemetry、坐标方向、Action、停止路径和比赛任务模板。SITL
通过后仍不能跳过无载荷实飞。

预计时间：半天以上。需要准备：运行 ArduPilot SITL 的电脑、RK3588 和局域网。

本章把操作分成两类：

- **AI 完成**：备份实机配置、应用 SITL profile、重启板端服务、检查各阶段状态。把"AI
  提示词"小节整段发给 AI Coding Agent 即可，无需自己敲命令。
- **用户完成**：只有一条，在 RK3588 仓库根目录运行启动脚本，其余步骤通过 Web UI
  操作。

## 1. 理解测试拓扑

电脑运行 Gazebo 和 ArduPilot SITL，RK3588 继续运行与实机相同的 app、RKNNLite 和 Web UI：

```text
电脑：Gazebo 相机 + ArduPilot SITL
  ├─ H264/UDP 5600 → RK3588 yolo_app
  └─ MAVLink/UDP 14550 → RK3588 app

RK3588：YOLO + Action Mission + Web UI
```

先记录实际地址，不要照抄示例：

| 角色 | 实际 IP |
| --- | --- |
| Gazebo/SITL 电脑 | 待填写 |
| RK3588 | 待填写 |

电脑需要安装并验证 ArduPilot SITL、Gazebo、`ardupilot_gazebo`、GStreamer 和对应世界。
这些依赖因电脑系统不同而变化，不属于 RK3588 板端安装脚本的职责。

## 2. AI 提示词：切换 SITL 配置

> 在本仓库根目录，把当前实机配置备份到 `runtime/config-backups/before-sitl-<时间戳>/`
> （先运行 `git status --short` 并展示给我，再备份 `config/app.yaml`、
> `config/telemetry.yaml`、`config/yolo.yaml`），确认 `config/app.yaml` 中
> `executor.send_commands: false` 后应用 SITL profile。完成后报告备份路径、diff 摘要
> 和 `send_commands` 的值。不要动 Mission 模板和其他配置。

SITL profile 默认接收 MAVLink UDP 14550，YOLO 视频源为 UDP 5600，模型为
`gazebo_dataset-fp16.rknn`。切换 profile 只改 telemetry/YOLO 差异；需要长期保留的实机
差异应人工核对后写入 `config/profiles/rk3588-real/profile.yaml`。

## 3. 启动仿真（用户唯一操作）

确认电脑到 RK3588 网络可达后，在已安装仿真依赖的电脑上，从本项目仓库根目录运行：

```bash
GCS_HOST="<RK3588-IP>" VIDEO_HOST="<RK3588-IP>" bash scripts/run_iris_gimbal_sitl.sh
```

脚本在同一个终端窗口（或四个独立终端）中自动启动：

- Gazebo（`cuadc2026_rescue` world，固定下视相机）；
- ArduCopter SITL（MAVLink 发到 `udp:<RK3588-IP>:14550`，投放 bridge 发到本机 14551）;
- 载荷投放 bridge（`payload_release_bridge.py`）；
- 相机 RTP relay（延迟 10 s 后打开相机流，把 H264/RTP 从电脑本机 5600 转发到
  `udp:<RK3588-IP>:5600`）。

默认值是队伍现场地址 `10.101.31.108`，必须用环境变量替换。路径类变量
（`ARDUPILOT_DIR`、`GZ_REPO_DIR`）默认是相对本项目仓库根目录的 `../ardupilot` 和
`../gz_ws/src/ardupilot_gazebo`。参数文件、world、投放 bridge 和配置默认从选定的
`GZ_REPO_DIR` 派生。所有相对路径覆盖也按本项目仓库根目录解析，与启动时的工作目录无关；
安装位置不同的电脑可用同名环境变量覆盖。SITL 生成物写入 `runtime/sitl/iris-gimbal/`。
脚本会自动启用相机流并转发视频，不要再手动运行
`gz topic ... enable_streaming` 或 GStreamer 转发，以免争用 UDP 5600。

比赛 world、固定相机参数和投放 bridge 来自
[本项目场景 fork](https://github.com/Bob12315/ardupilot_gazebo)。

## 4. AI 提示词：启动 RK3588 板端

> 应用 SITL profile 后，在无人机不连接真实飞控、SEND OFF、没有运行 Action/Mission 的
> 状态下重启用户服务加载配置：
> `systemctl --user restart uav-yolo.service uav-app.service`，然后用
> `systemctl --user --no-pager --full status uav-yolo.service uav-app.service` 报告
> 状态。如果服务已配置操作者口令（systemd drop-in），不要改动凭据文件；没有 systemd
> 服务时，提示我在两个终端分别用 conda 环境手动启动 `python -m yolo_app.main --source
> 5600` 和 `python -m app.main --connect-telemetry --send-commands false`（手动启动需要
> 先安全提供 `UAV_WEB_OPERATOR_PASSWORD`，不要让我在聊天里贴口令）。
>
> Web UI 地址：`http://<RK3588-IP>:8080/`，视频流：
> `http://<RK3588-IP>:8081/video/yolo.mjpeg`。

## 5. AI 提示词：观察连接（只读）

保持 SEND=OFF，启动 app/yolo 后：

> 保持 SEND=OFF 只观察，不要开 SEND、不要启动任何 Action/Mission。用
> `curl http://<RK3588-IP>:8080/api/status` 和 journalctl 日志逐项确认并报告：
> telemetry data_source/active_source 是 sitl；heartbeat、GPS、姿态、LOCAL_NED 在更新；
> 仿真视频和 YOLO scene 更新（perception 不 stale）；Web UI 显示 SEND OFF；没有任务自动
> 启动。

## 6. AI 提示词：建立 SITL Field Reference

> 在 Web UI 的 Competition Field Setup 输入 SITL 场地的 forward marker B，在起飞点完成
> schema v3 GPS runtime sampling。确认 `FIELD→GPS` 已就绪、`FIELD→LOCAL_NED` 保持不可用，
> 并核对 Web 地图 `+Y` 前方、`+X` 右方与仿真运动方向一致。方向相反立即停止任务，不能靠
> 临时交换 x/y 或改符号掩盖。

## 7. AI 提示词：从低风险 Action 开始

> 按风险从低到高逐项验证，一次只改一个变量，每步报告结果后才继续下一步：只读/识别
> Action → 场地坐标预览 → 单个低速航点 → stop/reset/skip → 起飞与降落分项 → 投放请求
> （无真实机构）→ 分项 Mission → 完整 `rescue_2026_full_auto`。只有在确认仿真场地、
> 速度、高度和遥控接管后，才在 SITL 中开启系统 SEND，并为本次 SITL Action/Mission 建立
> run 授权。

## 8. AI 提示词：必测失败路径

> 逐一注入以下失败，确认停止或失败后不会恢复旧速度、旧位置目标或重复投放请求，逐项
> 报告结果：Action 运行中点击 Stop；连续 BODY_NED Action 丢失视觉目标；Mission 中断、
> Reset 和 Skip Current；telemetry 断线和恢复；yolo 停止和恢复；Field Reference 未确认
> 或未冻结时启动 Mission；投放步骤失败后的恢复路径；限速阶段结束后速度恢复；视觉降落
> 失败后 LAND 兜底。

## 9. 常见问题

### 收不到 MAVLink

- 确认 SITL 的 `--out` 指向 RK3588，而不是电脑本机；
- 确认防火墙允许 UDP 14550；
- 确认板端 `config/telemetry.yaml` 使用 SITL/UDP-in；
- 使用 `ss -lunp` 和获准的抓包工具确认数据方向；
- 检查 heartbeat 时不需要打开 SEND。

### 没有仿真视频

- 确认 Gazebo 相机传感器已经启用；
- 确认电脑本地 UDP 5600 有 RTP 数据；
- 确认 GStreamer 的目标是 RK3588 实际 IP；
- 确认板端 UDP 5600 没有被其他进程占用；
- 检查 `uav-yolo.service` 日志中的解码和模型加载错误。

### 仿真目标没有检测结果

SITL profile 使用 `data/models/gazebo_dataset-fp16.rknn`，不能误用实机场景模型。确认
模型、类别表、光照、相机方向和 Gazebo 目标素材与当前数据集一致。

### 飞行方向与地图相反

立即停止任务，核对 FIELD `+Y`、`+X`、SITL 起始朝向和 profile。不能通过临时交换
x/y 或修改符号来掩盖问题。

## 10. 保存结果

> 记录本次 SITL 使用的 commit、profile、Mission 模板、参数、SITL 起点、每个失败注入结果
> 和日志路径。运行产物放在 `runtime/`，不要把临时 SITL 文件写进配置目录。

## SITL 验收清单

- [ ] SITL telemetry、视频和 YOLO 状态稳定。
- [ ] FIELD 方向和航点方向经过实际仿真运动验证。
- [ ] stop/reset/skip 和丢目标路径均停止旧命令。
- [ ] 投放步骤不会重复或绕过 dispatcher。
- [ ] 完整 `rescue_2026_full_auto.json` 模板在保守参数下完成一次。
- [ ] SITL 的 SEND 开启没有被带回实机配置。

## AI 提示词：恢复实机配置

> SITL 结束后，先在 Web UI 关闭系统 SEND、停止 Mission，确认配置中的
> `executor.send_commands: false`。然后应用实机 profile：`bash
> scripts/config/apply_rk3588_real.sh`。应用后展示 diff，人工核对实机差异；profile 是
> 增量合并，不保证恢复未列入差异文件的每一项，必要时对照 `runtime/config-backups/` 的
> SITL 前备份恢复。确认模型、视频输入和飞控链路后，在无任务、SEND OFF 的状态下重新
> 加载服务。

下一章：[实飞测试](05_flight_test.md)。
