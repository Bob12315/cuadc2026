# 05：实飞测试

本章把无桨台架、比赛场地初始化、第一次实飞、完整任务和比赛当天检查合并为一条分级
验收路线。每一级都要保存日志并复盘，通过后才能进入下一级。

> 实飞可能造成人员伤害和财产损失。必须遵守当地法规、场地规则、飞控厂商说明和队伍
> 安全流程。本章不能替代有经验飞手、硬件负责人和现场安全员。

## 一、进入实飞测试前

必须同时满足：

- 机架、动力、飞控和遥控系统已经由有经验人员完成常规检查；
- 飞控校准、飞行模式和 failsafe 已配置并验证；
- 遥控器或地面站可以随时接管；
- 接线、供电和无桨检查记录完整；
- [RK3588 环境配置](03_rk3588_setup.md) 的只读联调通过；
- [SITL 测试](04_sitl_test.md) 的分项和完整模板验证通过；
- 当前代码 commit、配置、模型和任务模板已记录；
- 测试场地、天气、空域、人员隔离和消防准备符合要求；
- 起飞前保持 `executor.send_commands: false`。

## 二、无桨台架复验

把整机固定在牢固台架，拆下并带走全部螺旋桨，只使用低质量无危险模拟载荷。

### 1. 姿态、GPS 和高度

手动小角度抬起机头、右侧和机尾，确认 roll、pitch、yaw 与飞控安装方向一致。检查 GPS
fix、卫星数、位置、高度和 LOCAL_NED 是否稳定。不要把 GPS Home、EKF Origin 和比赛
FIELD 原点当成同一个概念。

### 2. 相机方向与检测

把目标从画面中心移到右侧和下侧，确认：

- `pixel_x/ex_norm` 向右为正；
- `pixel_y/ey_norm` 向下为正；
- 输入画面没有意外镜像；
- 类别、置信度和跟踪 ID 合理；
- 移走目标或断开相机后状态变为 lost/invalid。

### 3. MAVLink 只读状态

保持 SEND OFF，观察 heartbeat、armed、mode、GPS、姿态、速度和 stale。断开链路后应看到
connected/control_allowed 变化，恢复连接时不得自动恢复旧动作。

### 4. 投放机构空载复验

正式链路只能是：

```text
payload_release → set_servo → MAV_CMD_DO_SET_SERVO
```

先由硬件/飞控负责人确认真实 SERVO 输出和 PWM，一次只连接一路，验证保持、释放、恢复、
电流、压降和机械范围，再使用模拟载荷验证两路不会串扰。模板中的输出 8/9 和 PWM 只是
当前软件值，不能替代实测。

### 5. 故障注入

在无桨、无正式载荷条件下验证：

- 遮挡或断开相机后目标失效；
- 停止 yolo 后 app 不沿用旧感知；
- 断开 telemetry 后状态 stale/control disallowed；
- Stop、Reset 或失败退出后旧连续命令被清理。

涉及实发 zero/stop 的项目必须先在 SITL 验证。新手不要在台架上临时开启完整任务实发。

## 三、人工首飞与只读记录

第一次飞行完全由飞手控制，系统 SEND 保持 OFF，RK3588 只记录 telemetry、相机、YOLO、
录像和 blackbox。

飞手依次完成起飞、定高悬停、小范围平移、转向和降落。复盘时检查：

- RK3588、飞控和相机是否掉电或重启；
- 电压、温度、网络、存储和日志是否稳定；
- 相机振动、延迟、丢帧和检测结果是否可接受；
- telemetry 的方向、高度和速度是否与实际一致；
- 满载布局和模拟释放后的重心是否仍然安全。

出现硬件、电源、姿态或人工操纵问题时，不得进入自动动作测试。

## 四、比赛场地初始化

比赛现场主要使用 schema v3 runtime binding：

- A：飞机在起飞点静止采样得到的动态 GPS 原点，对应 FIELD `(0, 0)`；
- B：位于场地 `+Y` 方向的 forward marker GPS；
- A→B：本次比赛 FIELD `+Y` 的 heading。

schema v3 生成 FIELD → GPS/GLOBAL 任务几何，不建立 LOCAL_NED 场地原点。准备时：

- 飞机位于正式起飞点并保持静止；
- GPS fix、卫星数、EPH 和 EPV 满足 profile；
- forward marker 经纬度已由两人确认，没有写反；
- Web UI 可读取 telemetry，SEND 保持 OFF。

在 Web UI 打开 `Competition Field Setup / 比赛场地初始化`：

1. 输入 forward marker 纬度和经度；
2. 启动 runtime sampling；
3. 保持飞机静止并观察 accepted/rejected/duplicate samples；
4. 检查水平离散度、A→B baseline、heading 和 warning；
5. 核对投放区、侦察区和扫描航点的地图预览；
6. 条件满足后等待系统自动 finalize、apply 和 freeze；
7. 确认状态为 confirmed、synced、frozen。

当前模板要求至少 6 个合格样本、6 秒采样窗口和不超过 1 m 的水平离散度，实际
阈值以 `config/field_profiles/competition_runtime.json` 为准。

以下情况必须 Reset 并排除原因，不能绕过 preflight：

- forward marker 不确定或经纬度疑似写反；
- GPS 质量不满足阈值；
- 采样期间飞机被移动；
- 水平离散度过大或 A→B baseline 太短；
- 地图方向或场地几何与真实场地不一致；
- 未达到 confirmed、synced、frozen；
- 出现无法解释的 warning。

## 五、分级自动飞行

### A. 人工飞行验证 FIELD 方向

继续保持 SEND OFF，由飞手在安全高度低速运动，比较 Web 地图、GLOBAL/LOCAL 状态和真实
方向。方向不一致时立即停止自动化测试并重新建立 Field Reference。

### B. 单个低风险 Action

使用已经在 SITL 验证的保守参数，先测试可立即停止的低速航点或模式。启用 SEND 前明确
测试动作、停止条件、飞手接管方式和操作员职责，一次只改变一个变量。

### C. 起飞和降落分项

单独验证自动起飞和降落，不串联识别、投放或完整 Mission。每项完成后关闭 SEND、保存
日志并复盘。

### D. 无载荷视觉分项

保持正式载荷不安装、投放机构断开或使用安全模拟件，验证扫描、目标锁定、视觉对准、
下降、丢目标停止和 LAND 兜底。

### E. 投放分项

按以下顺序逐级增加风险：

```text
实飞但投放输出断开
→ 落地后的空载 SERVO 验证
→ 安全区域内携带模拟载荷
→ 单路正式机构
→ 两路顺序投放
```

确认 Mission 的失败、重试和 `jump_to` 不会重复释放已经投放的载荷。

### F. 完整比赛任务

只有所有分项通过后，才使用经批准的
`config/action_missions/rescue_2026_full_auto.json`。先以 SEND OFF 完整干跑，再在
保守参数下实飞。任务模板、速度、高度、场地 profile、两个 SERVO 输出和失败恢复路径
必须由第二人复核。

## 六、每次自动动作前口令

```text
场地净空？
飞手已接管待命？
测试动作和停止条件明确？
Field Reference 正确？
速度、高度和目标已复核？
系统 SEND 与本次 REAL run 授权状态明确？
日志正在记录？
```

以下任一情况立即终止自动任务，由飞手或地面站优先接管：

- 姿态、方向、高度或运动方向异常；
- telemetry stale、GPS/EKF 异常或网络反复断开；
- 相机/YOLO 丢失后仍持续运动；
- RK3588、飞控或相机重启；
- 人员进入安全区；
- 飞手、观察员或操作员任一人喊停。

停止 app 只能作为辅助路径，不能作为唯一急停手段。

## 七、比赛当天检查表

### 人员分工

- 飞手：遥控器和最终接管；
- Web 操作员：场地初始化和 Mission；
- 硬件负责人：电池、接线和投放机构；
- 观察员/安全员：场地净空和喊停；
- 复核员：检查配置、参数和清单。

飞手不应在关键飞行阶段同时低头操作网页。

### 到场前

- [ ] 代码 commit、模型、实机 profile 和 Mission 模板已冻结并记录。
- [ ] `python scripts/validate_action_missions.py` 通过。
- [ ] SITL、无载荷实飞和失败注入记录完整。
- [ ] 飞控参数、RK3588 配置和日志已备份。
- [ ] 电池、备用桨、线材、工具、模拟载荷和网络设备齐全。
- [ ] 没有来源不明的板端改动。

### 整机和只读状态

- [ ] 机架、电机、桨、紧固件和重心检查完成。
- [ ] RK3588、飞控、GPS、相机和投放机构固定可靠。
- [ ] 电池、DC-DC、连接器无损伤或过热痕迹。
- [ ] 遥控器、地面站、飞行模式和 failsafe 已检查。
- [ ] 上电后 `executor.send_commands: false`。
- [ ] app/yolo、Web UI、视频、检测和 telemetry 稳定。
- [ ] 当前 Mission 是经批准的完整或分项模板。

### Field Reference 和 Mission

- [ ] 飞机位于正式起飞点并保持静止。
- [ ] forward marker GPS 由两人复核。
- [ ] 样本数量、质量、离散度和 baseline 合格。
- [ ] 地图前向、投放区和侦察区与真实场地一致。
- [ ] confirmed、synced、frozen 均为 true。
- [ ] 起飞高度、阶段速度、返航高度和视觉降落目标正确。
- [ ] 两个 payload、SERVO 输出和 PWM 与空载记录一致。
- [ ] 失败恢复不会重复投放，侦察/视觉失败仍有安全返航和 LAND 路径。
- [ ] blackbox、飞控日志和必要录像已准备。

### 开启自动动作前与任务后

- [ ] 飞手、操作员、观察员和复核员全部口头确认。
- [ ] 场地净空，天气、空域和 GPS 条件允许。
- [ ] 系统 SEND 和本次 Action/Mission run 授权由指定操作员控制。
- [ ] 任务后先关闭 SEND，再停止或重置 Mission。
- [ ] 飞机断电后再处理载荷和接线。
- [ ] 保存 app、yolo、blackbox、飞控和录像数据。
- [ ] 记录结果、异常、人工接管和配置版本。
- [ ] 未完成复盘前不同时修改多个参数再次起飞。

## 八、现场故障快速排查

先执行固定动作：关闭 SEND、停止 Mission、实机拆桨和移除载荷，并记录故障时间、最近
改动及 `git status --short`。不要删除日志或覆盖配置。

```bash
bash scripts/healthcheck/check_rk3588.sh
systemctl --user --no-pager --full status uav-app.service uav-yolo.service
journalctl --user -u uav-app.service -n 150 --no-pager
journalctl --user -u uav-yolo.service -n 150 --no-pager
ss -lntu | grep -E ':(5005|5006|8080|8081|14550|15001)\b'
python scripts/validate_action_missions.py
```

### Field Reference 无法完成

检查 forward marker、经纬度顺序、GPS fix、卫星数、EPH/EPV、样本数量、水平离散度和
A→B baseline，确认采样期间飞机静止。

### Action 或 Mission 不启动

查看 Web UI 和 app 日志中的 reason，常见原因包括 Field Reference 未确认/同步/冻结、
telemetry stale、Action 参数错误、上一个 Action 未停止、模板校验失败或 SEND 状态不符。

### 舵机通道或方向错误

立即断开正式载荷和机械负载，核对飞控 SERVO 输出而不是 RC 输入通道，重新进行单路
空载标定。不得使用 RC override 或直接 pymavlink 绕过当前链路。

### 必须停止自行排查的情况

- 供电电压、极性、共地或引脚不确定；
- 出现过热、异味、冒烟或连接器变色；
- 飞控姿态、电机输出或自动运动方向异常；
- 需要关闭安全检查才能继续；
- 无法确认某个操作是否会产生真实动作。

保持断电或 SEND OFF，保存日志和配置，由硬件负责人、飞手和项目维护者共同处理。

## 最终验收

- [ ] 无桨台架、故障注入和两路投放空载验证完成。
- [ ] 人工首飞期间计算板、相机、供电和日志稳定。
- [ ] FIELD 与真实方向一致，并经过第二人复核。
- [ ] 每个 Action 都在保守参数下单独验证且可停止。
- [ ] 丢目标、断链、Stop 和失败退出不会保留旧运动。
- [ ] 起飞、降落、识别、投放、侦察和返航均有分项日志。
- [ ] 完整任务至少完成一次受控验证并完成复盘。
- [ ] 比赛清单已打印或转为可签字的队伍版本。

完成本章后，开发者可继续阅读 [比赛任务设计](../developer/action_mission_rescue_2026.md)。
