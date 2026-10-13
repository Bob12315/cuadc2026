# 2026 救援比赛 Action Mission

本文描述当前比赛任务主线。正式完整流程以
`config/action_missions/rescue_2026_full_auto.json` 和实际代码为准。

## 比赛任务目标

系统需要在同一次任务中完成：

1. 建立并冻结比赛 FIELD 坐标；
2. 起飞后在投放区中心采集一次视野并定位目标；
3. 按识别数量生成投放计划：零目标在投放区中心投放，一个目标对准后依次投放两枚载荷，
   两个及以上目标选择最高分的两个并依次对准投放；
4. 前往侦察区扫描危险标识并生成排名；
5. 返回起飞点，执行视觉辅助降落和 LAND 兜底。

以上是比赛目标。当前完整模板已编排侦察区航点飞行，但没有侦察检测采集、融合或危险标识
排名步骤，不能把飞完侦察航迹视为已经生成排名。起飞前手动按顺序选桶功能已删除，
当前 `select_drop_targets` 按评分和去重规则自动选择。

FIELD 坐标中 `+Y` 为场地前方、`+X` 为场地右方，`altitude_m` 向上为正。
投放区和侦察区边界由当前 Field Profile 定义；正式模板中的具体航点目前显式写在 JSON 中，
不会随 profile 的扫描航点自动更新。调整场地后必须同时核对模板，不应在运行时代码中另写坐标。

## 当前完整流程

`rescue_2026_full_auto.json` 当前包含以下步骤：

```text
takeoff
→ goto_waypoint (drop centre, 5 m) → gps_capture_view
→ gps_fuse_views
→ select_drop_targets
→ change_speed (1 m/s)
→ 2 × (goto_waypoint → align_descend(visual target tracking) → payload_release → goto_waypoint climb)
→ change_speed (2 m/s)
→ goto_waypoint (recon entry, 4 m)
→ change_speed (0.2 m/s)
→ 6 × goto_waypoint (recon, 3 m)
→ change_speed (2 m/s)
→ goto_waypoint (return home)
→ goto_waypoint (descend home)
→ align_descend
→ land
```

速度切换必须显式使用 `change_speed` Action。`goto_waypoint` 中的到点速度参数是完成
判据，不是飞行限速。

完整模板起飞目标为 4.5 m。`takeoff` 先保持原航向垂直爬升，达到
`altitude_m - altitude_tolerance_m` 门槛后才发送一次绝对 `condition_yaw` 转向 FIELD +Y，
达到偏航判据后再稳停 1 秒；它不在整个爬升阶段反复下发偏航指令。

### 侦察区六点航迹

完整 v2 在侦察区的两排各设置左、中、右三个 FIELD 航点，两排分别位于 `Y=56 m` 和 `Y=59 m`，
每排 `X=-3, 0, 3 m`，六点高度均为 3 m。执行顺序为近排左→中→右，再到远排右→中→左：

`(-3,56) → (0,56) → (3,56) → (3,59) → (0,59) → (-3,59)`。

入口仍为 `(-3,56)`、4 m，到达后降为侦察速度 0.2 m/s。六点到点误差为水平 0.4 m、
高度 0.3 m，不要求速度门槛，且没有显式稳停（`goto_waypoint.settle_time_s` 默认为 0）。
只有入口明确设置 1 秒稳停。原中心点 `(0,57.5)` 已移除；结束后
恢复 2 m/s，返回 `(0,0)`、4 m。此修改仅适用于完整 v2，`recon_gps.json` 分项模板保持原航迹。

### 展开的 v2 Action 清单与失败策略

下表的序号就是 JSON 内的执行序。三个正式比赛模板的每一步都显式使用
`on_failed: {"action": "continue"}`：失败会被记录为 skipped，然后启动紧邻的下一步，
不会直接返航或终止整个 Mission。

| # | label | Action | 失败策略 |
| ---: | --- | --- | --- |
| 1 | `takeoff_4_5m` | `takeoff` | `continue` |
| 2 | `goto_drop_center_5m` | `goto_waypoint` | `continue` |
| 3 | `drop_scan_capture` | `gps_capture_view` | `continue` |
| 4 | `drop_scan_fuse` | `gps_fuse_views` | `continue` |
| 5 | `select_gps_drop_targets` | `select_drop_targets` | `continue` |
| 6 | `drop_speed_1mps` | `change_speed` | `continue` |
| 7 | `drop_1_approach` | `goto_waypoint` | `continue` |
| 8 | `drop_1_align` | `align_descend` | `continue` |
| 9 | `drop_1_release` | `payload_release`（后方 SERVO8 1800→1370） | `continue` |
| 10 | `drop_1_climb` | `goto_waypoint` | `continue` |
| 11 | `drop_2_approach` | `goto_waypoint` | `continue` |
| 12 | `drop_2_align` | `align_descend` | `continue` |
| 13 | `drop_2_release` | `payload_release`（前方 SERVO9 1835→1335） | `continue` |
| 14 | `drop_2_climb` | `goto_waypoint` | `continue` |
| 15 | `restore_transition_speed_2mps` | `change_speed` | `continue` |
| 16 | `goto_recon_entry_4m` | `goto_waypoint` | `continue` |
| 17 | `recon_speed_1mps`（实际 0.2 m/s） | `change_speed` | `continue` |
| 18–23 | `recon_scan_goto_1..6` | `goto_waypoint` | `continue` |
| 24 | `restore_return_speed_2mps` | `change_speed` | `continue` |
| 25 | `return_home_gps` | `goto_waypoint` | `continue` |
| 26 | `descend_home_2_5m` | `goto_waypoint` | `continue` |
| 27 | `final_land_align` | `align_descend` | `continue` |
| 28 | `land_home` | `land` | `continue` |

## 模板定位

| 模板 | 用途 |
| --- | --- |
| `rescue_2026_full_auto.json` | 当前完整 GPS-first 比赛流程 |
| `drop_two_targets.json` | 双目标投放分项流程 |
| `recon_gps.json` | 侦察航点飞行分项流程 |

历史模板已删除，不参与正式运行 catalog 或默认 validator。

模板存在不代表已经通过实飞验收。正式比赛前必须在 Web UI 核对当前模板内容、场地
profile、速度、高度、SERVO 输出和失败继续策略。

## SITL 固定下视相机与投放映射

`scripts/run_iris_gimbal_sitl.sh` 启动的世界使用
`iris_cuadc2026_fixed_down_camera`。其相机是固定下视、未镜像的 640 × 480 图像，10 Hz，
水平 FOV 为 2.0 rad（114.591559°）；按 4:3 图像比例推导的垂直 FOV 为 98.864783°。
这是 SITL profile 的相机/output 视场角。`drop_two_targets.json` 的地面投影 `camera`
参数采用这组值：

```json
{
  "fov_x_deg": 114.591559,
  "fov_y_deg": 98.864783,
  "image_x_sign": 1,
  "image_y_sign": -1
}
```

`rescue_2026_full_auto.json` 的单视角 `gps_capture_view` 当前使用另一组独立投影参数：
`fov_x_deg=142.964844`、`fov_y_deg=131.870643`、`ground_distance_scale=0.21`，
图像方向仍为 `image_x_sign=1`、`image_y_sign=-1`。这些参数不从 YOLO profile 自动继承，
也不能视为实机相机内参；实飞前须按实际图像、安装和高度来源验证。
两个正式投放模板均未编排 `gps_target_lock`，而是由 `align_descend` 在到达目标附近后自行锁定视觉目标。

`align_descend` 使用 YOLO 的归一化 `ex/ey` 相对 `release_target_ex/ey` 的误差生成
BODY_NED 控制量。未设置偏移时控制目标为画面中心；完整投放流程的两路 `ey` 偏移分别为
0.3 和 -0.05。速度方向使用 `vx_sign=-1`、`vy_sign=1`。

Gazebo payload bridge 同时监听人工 RC 输入和飞控 SERVO 输出，但自动任务只允许后者：

| 载荷 | 位置 | 自动任务 SERVO 输出 | PWM 时序（锁→投→锁） | Gazebo detach topic |
| --- | --- | --- | --- | --- |
| bottle1 / `payload_1` | 后方；遥控器 -26（锁）、60（投） | SERVO8 | 1370 → 1800 → 1370 | `/cuadc2026/payload/bottle1/detach` |
| bottle2 / `payload_2` | 前方；遥控器 -35（锁）、49（投） | SERVO9 | 1335 → 1835 → 1335 | `/cuadc2026/payload/bottle2/detach` |

表中的遥控器位置是历史校准记录，绝不是 Mission 参数。按 `-100 = 1000`、`100 = 2000`
线性换算，SERVO9 的旧记录 -35/49 对应 1325/1745，不能据此推导当前模板的 1335/1835。
当前自动任务以模板和安全白名单为准；bridge 的匹配阈值仍须在实际 Gazebo 配置中核对。
`payload_release` 只经 `set_servo` 发送 `MAV_CMD_DO_SET_SERVO`；
`config/safety.yaml` 只白名单化 SERVO8 的 1370--1800 与 SERVO9 的 1335--1835 PWM，禁止
RC override。

## 侦察、对准、投放原子 Action

每个 Action 的统一返回形状为 `done`、`failed`、`reason`、`output`、`detail` 和 typed
`effects`；序列化状态中的 `actions` 是兼容 request 视图。Mission 只将有 `save_as` 的
`output` 放入 blackboard。

| 阶段 | Action 与运行方式 | 成功结果 | 失败与模板策略 |
| --- | --- | --- | --- |
| 投放区中心单视角粗定位 | 飞至 FIELD `(0, 32.5)`、5 m 后运行一次 `gps_capture_view`，从当前广角 YOLO `scene.detections` 与捕获时 GPS/yaw/高度计算每个筒的粗 GPS；随后 `gps_fuse_views` 与 `select_drop_targets` 按筒类别得分和空间去重选择最多两个目标。 | capture: `gps_view_captured`，`output.raw_estimates`；fuse: `gps_views_fused`（也可能是空成功 `gps_views_fused_empty`），`localized_objects`；select: `selected_targets` / `target_slots`。 | 任一步失败均记录后继续下一步。依赖缺失的 blackboard 数据可能使后续 Action 启动失败，该步骤同样会被跳过。 |
| 最近目标对准下降 | 飞机先到融合 GPS 点上方 3 m，融合 GPS 只用于导航。`align_descend` 首先从 `scene.detections` 获取画面中心附近的目标，按 track ID 和图像位置连续性跟踪，同时根据投放偏置修正水平位置并下降。零目标中心投放会跳过该步骤。 | 到目标高度并完成稳停后，最近 6 个计票画面至少 3 帧位于对准范围，且水平速度低于模板门槛，返回 `alignment_confirmed`；提供 `frame_id` 时相邻重复帧不重复计票。投放对准达到 30 s 时发零速并以 `alignment_timeout_accepted` 完成。 | 投放对准超时按成功继续投放；最终返航视觉对准仍保持 30 s 总超时失败。 |
| 投放 | `payload_release` 先生成一次 release PWM，在等待窗口维持零速，随后生成 hold PWM。零目标或一个目标时第一次投放依次控制后方 SERVO8、前方 SERVO9，两路之间间隔 1 s；两个目标时分别控制 SERVO8、SERVO9，第二投放步骤在目标不足时安全跳过。 | 首 tick 为 `release_sent`，最终为 `payload_released`；跳过时为 `payload_release_skipped`。`detail` 记录 payload/target ID、SERVO 输出、PWM、等待状态与零速命令。 | 失败后继续下一步。当前 ActionResult 没有 dispatch/bridge 回执：SEND、安全或传输拒绝记录在 `last_dispatch.skipped/errors`，仍可能得到 `payload_released`，因此仍需核对 dispatch 和 bridge 日志。 |

投放 `align_descend` 丢失视觉时生成零速等待；停止和超时退出时生成显式零速并清除旧连续命令。
最终返航段显式启用 `continue_descending_on_target_loss`：首次获取目标后再丢失视觉，会以零水平速度
继续下降，到最终高度后结束该 Action。若启动后 2 秒仍未首次获取目标，则以
`target_acquisition_timeout_accepted` 完成并进入 LAND。这些行为不能替代飞手或地面站接管。

完整流程投放对准段的有效参数是：目标高度 1.1 m、下降率 0.2 m/s、相对投放偏置的
对准范围为 `|ex - release_target_ex| ≤ 0.04` 且 `|ey - release_target_ey| ≤ 0.04`；两路投放的 `ey` 偏置
分别为 0.3 和 -0.05。下降阶段水平 P 增益为 0.42，限幅 0.3 m/s；最终高度 P 增益为 0.2，
限幅 0.15 m/s，稳停 0.3 s，并要求水平速度低于 0.08 m/s。积分校正关闭。
返航视觉下降段目标高度为 0.3 m，对准范围为 0.3，稳停 0.5 s。
超时固定为 30 s，6 帧窗口和 3 帧命中数固定在 Action 中。
只有投放对准把超时视作完成；最终返航视觉对准显式设置为超时失败。

## 运行架构

```text
Action Mission JSON
  → MissionOrchestrator
  → MissionBlackboard
  → ActionRuntimeService
  → ActionRunner
  → Action.update()
  → ActionResult
  → ActionDispatcher
  → ActionSafetyPipeline
  → LinkManager
  → telemetry_link / MAVLink → Flight Controller / Servo
```

YOLO 感知通过独立 UDP 链路进入 runtime context；视觉锁定请求由 dispatcher 的 YOLO client
处理，不经 `LinkManager`。新增 Stable Core dispatcher/lease/cycle 链目前只完成离线 readiness，
不替代上图的实际运行链，边界见 [当前架构](../ai/architecture/current_architecture.md)。

Mission 和 Action 不得直接调用 pymavlink 或 `LinkManager`。飞控请求只允许经由
`ActionDispatcher` 派发；投放只允许走：

```text
payload_release → set_servo → MAV_CMD_DO_SET_SERVO
```

## Blackboard 数据流

Mission 步骤可用 `save_as` 保存 `ActionResult.output`，后续参数使用完整字符串 `$path`
读取。当前 v2 投放主线的主要数据流是：

```text
gps_capture_view save_as drop_scan_view
  → gps_fuse_views save_as drop_scan
  → drop_scan.localized_objects

select_drop_targets save_as drop_targets
  → drop_targets.target_slots
  → first/second alignment enabled
  → first release SERVO outputs and second release enabled

goto_waypoint（融合目标 GPS 上方）
  → align_descend
  → 按 track ID 和图像位置连续性跟踪视觉目标
  → MissionOrchestrator 清理连续命令并保持位置
  → payload_release save_as drop_release_1..2
```

投放区以中心单张广角画面进行粗定位：融合兼容单视角，聚类和来源航点门槛均为 1，
并放宽画面边缘过滤。粗 GPS 只用于区分场上筒、以 1.0 m 空间去重避免同一筒占用两个
槽位，以及导航至目标附近；广角边缘误差由后续近距离视觉对准投放处理。融合输出携带的
总权重会参与同类别目标的稳定排序。零目标时 `target_slots[0]` 是 FIELD `(0, 32.5)` 的
中心回退点，飞机在 3 m 高度直接依次投放两枚载荷。一个目标时第一槽使用融合 GPS，
并在一次对准后依次投放两枚载荷；两个目标时两个槽分别执行。`align_descend` 不消费
Mission 指定的固定 track，只使用飞机到达位置后的当前画面建立并维护本 Action 的 track/图像位置锁定；
投放计划只控制该步骤是否执行。

参数引用支持字典键和列表索引，例如：

```json
{
  "target": "$drop_targets.selected_targets.0"
}
```

当前只支持整个字符串为 `$path`，不支持把路径插入到其他字符串中。

## 步骤和失败策略

步骤基本结构：

```json
{
  "name": "action_name",
  "label": "optional_label",
  "save_as": "optional_blackboard_key",
  "params": {},
  "on_failed": {"action": "continue"}
}
```

当前 orchestrator 仍支持 `fail`、`retry_current`、`retry_current_then_jump_to`、`jump_to`、
`continue`，但三个正式比赛模板统一使用 `continue`。循环展开为明确的有限步骤，不提供
通用脚本或旧 stage 类。调整流程时必须确认：

- 失败 Action 的连续速度和 pending position 已停止、清除；
- 阶段限速在失败后仍能由后续步骤恢复；
- 失败步骤及原因能够在 `skipped_steps` 中追踪；
- 最后的返航和降落步骤仍保留在顺序任务末尾。

## 场地初始化和起飞前检查

当前完整 v2 模板使用 FIELD/GPS 派生坐标。任务启动前要求 Field Reference 已确认、
同步并冻结。Web UI 的 `Competition Field Setup` 使用 schema v3 流程：输入场地前向
标记点 GPS，在起飞点采集当前 GPS 样本，生成运行时原点和 heading，再冻结到本次任务。

schema v3 是比赛与 SITL 唯一受支持的场地流程。具体契约见
[场地原点与方向](field_origin_heading.md)。

## 安全门控

配置或加载 Mission 不发送飞行命令。实发至少需要：

1. 系统 SEND 已由操作者人工开启（配置默认仍为 `executor.send_commands: false`）；
2. 当前 Action Mission 已建立绑定目标 source 的本次 run 授权；
3. telemetry、Field Reference 和相关 Action preflight 条件有效。

`SEND=OFF` 是默认状态。停止、跳过、失败或切换连续控制 Action 时必须明确发送
zero/stop 并清理旧命令。实机急停和最终接管依赖遥控器或地面站，不能只依赖停止 app。

## 验证顺序

```text
1. 当前主线单元/集成测试
2. Action Mission 模板离线校验
3. Web UI Configure 检查
4. SEND=OFF 完整干跑
5. SITL 低速 SEND=ON
6. 实机无载荷飞行
7. 挂载载荷但断开投放输出
8. 空载验证 SERVO 通道和 PWM
9. 正式载荷投放
```

离线校验：

```bash
python scripts/validate_action_missions.py
python scripts/validate_action_missions.py \
  config/action_missions/rescue_2026_full_auto.json
```

validator 不连接飞控，不验证识别精度、坐标标定、飞行安全或实机投放结果。

## 当前限制

- Mission 刻意保持受限顺序/失败分支模型，不提供通用脚本 DSL。
- 比赛参数仍需按正式场地、机体、相机和载荷进行现场标定。
- 侦察检测采集、融合和危险标识排名尚未编排进正式模板；后续实现仍需验证模型、阈值和覆盖。
- Action 连续控制由 P0 Safety Pipeline 统一裁决。
- 报告结果可供 Web UI 和 blackboard 使用，但最终比赛交付形式仍需按规则确认。
