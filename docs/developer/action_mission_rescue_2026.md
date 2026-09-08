# 2026 救援比赛 Action Mission

本文描述当前比赛任务主线。正式完整流程以
`config/action_missions/rescue_2026_full_auto.json` 和实际代码为准。

## 比赛任务目标

系统需要在同一次任务中完成：

1. 建立并冻结比赛 FIELD 坐标；
2. 起飞后在投放区多视角定位目标；
3. 按识别数量生成投放计划：零目标在投放区中心投放，一个目标对准后同时投放两枚载荷，
   两个及以上目标选择最高分的两个并依次对准投放；
4. 前往侦察区扫描危险标识并生成排名；
5. 返回起飞点，执行视觉辅助降落和 LAND 兜底。

FIELD 坐标中 `+Y` 为场地前方、`+X` 为场地右方，`altitude_m` 向上为正。
投放区和侦察区几何来自当前 Field Profile，不应在运行时代码中另写一套坐标。

## 当前完整流程

`rescue_2026_full_auto.json` 当前包含以下步骤：

```text
takeoff
→ 4 × (goto_waypoint → gps_capture_view)
→ gps_fuse_views
→ select_drop_targets
→ change_speed (1 m/s)
→ 2 × (goto_waypoint → align_descend(nearest image centre) → payload_release → goto_waypoint climb)
→ change_speed (2 m/s)
→ change_speed (1 m/s)
→ goto_waypoint (recon entry)
→ 5 × goto_waypoint
→ change_speed (2 m/s)
→ goto_waypoint (return home)
→ goto_waypoint (descend home)
→ align_descend
→ land
```

速度切换必须显式使用 `change_speed` Action。`goto_waypoint` 中的到点速度参数是完成
判据，不是飞行限速。

### 展开的 v2 Action 清单与失败策略

下表的序号就是 JSON 内的执行序。三个正式比赛模板的每一步都显式使用
`on_failed: {"action": "continue"}`：失败会被记录为 skipped，然后启动紧邻的下一步，
不会直接返航或终止整个 Mission。

| # | label | Action | 失败策略 |
| ---: | --- | --- | --- |
| 1 | `takeoff_4_5m` | `takeoff` | `continue` |
| 2, 4, 6, 8 | `drop_scan_goto_1..4` | `goto_waypoint` | `continue` |
| 3, 5, 7, 9 | `drop_scan_capture_1..4` | `gps_capture_view` | `continue` |
| 10 | `drop_scan_fuse` | `gps_fuse_views` | `continue` |
| 11 | `select_gps_drop_targets` | `select_drop_targets` | `continue` |
| 12 | `drop_speed_1mps` | `change_speed` | `continue` |
| 13 | `drop_1_approach` | `goto_waypoint` | `continue` |
| 14 | `drop_1_align` | `align_descend` | `continue` |
| 15 | `drop_1_release` | `payload_release`（SERVO9 1800→1600） | `continue` |
| 16 | `drop_1_climb` | `goto_waypoint` | `continue` |
| 17 | `drop_2_approach` | `goto_waypoint` | `continue` |
| 18 | `drop_2_align` | `align_descend` | `continue` |
| 19 | `drop_2_release` | `payload_release`（SERVO10 1800→1600） | `continue` |
| 20 | `drop_2_climb` | `goto_waypoint` | `continue` |
| 21 | `restore_transition_speed_2mps` | `change_speed` | `continue` |
| 22 | `recon_speed_1mps` | `change_speed` | `continue` |
| 23 | `goto_recon_entry_4m` | `goto_waypoint` | `continue` |
| 24–28 | `recon_scan_goto_1..5` | `goto_waypoint` | `continue` |
| 29 | `restore_return_speed_2mps` | `change_speed` | `continue` |
| 30 | `return_home_gps` | `goto_waypoint` | `continue` |
| 31 | `descend_home_2_5m` | `goto_waypoint` | `continue` |
| 32 | `final_land_align` | `align_descend` | `continue` |
| 33 | `land_home` | `land` | `continue` |

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
因此两个投放模板中所有需要地面投影的 `gps_capture_view`、`gps_target_lock` 的 `camera`
参数均为：

```json
{
  "fov_x_deg": 114.591559,
  "fov_y_deg": 98.864783,
  "image_x_sign": 1,
  "image_y_sign": -1
}
```

`align_descend` 直接使用 YOLO 的归一化 `ex/ey` 生成 BODY_NED 控制量，控制目标是画面中心，
速度方向使用 `vx_sign=-1`、`vy_sign=1`。

Gazebo payload bridge 同时监听人工 RC 输入和飞控 SERVO 输出，但自动任务只允许后者：

| 载荷 | bridge 人工 RC 输入 | 自动任务 SERVO 输出 | PWM 时序 | Gazebo detach topic |
| --- | --- | --- | --- | --- |
| bottle1 / `payload_1` | RC13 | SERVO9 | 1600 → 1800 → 1600 | `/cuadc2026/payload/bottle1/detach` |
| bottle2 / `payload_2` | RC14 | SERVO10 | 1600 → 1800 → 1600 | `/cuadc2026/payload/bottle2/detach` |

RC13/14 是仿真 bridge 的人工触发输入，绝不是 Mission 的参数。`payload_release` 只经
`set_servo` 发送 `MAV_CMD_DO_SET_SERVO`；`config/safety.yaml` 只白名单化 SERVO9/10 的
1600--1800 PWM，禁止 RC override。

## 侦察、对准、投放原子 Action

每个 Action 的统一返回形状为 `done`、`failed`、`reason`、`output`、`detail` 和 typed
`actions`；Mission 只将有 `save_as` 的 `output` 放入 blackboard。

| 阶段 | Action 与运行方式 | 成功结果 | 失败与模板策略 |
| --- | --- | --- | --- |
| 四视角投放区侦察 | 依次运行 4 × `goto_waypoint` → `gps_capture_view`，再运行 `gps_fuse_views` → `select_drop_targets`。每个 capture 从当前 YOLO `scene.detections` 与捕获时 GPS/yaw/高度投影。 | capture: `gps_view_captured`，`output.raw_estimates`；fuse: `gps_views_fused`（也可能是空成功 `gps_views_fused_empty`），`localized_objects`；select: `selected_targets` / `target_slots`。 | 任一步失败均记录后继续下一步。依赖缺失的 blackboard 数据可能使后续 Action 启动失败，该步骤同样会被跳过。 |
| 最近目标对准下降 | 飞机先到融合 GPS 点上方 2.5 m，融合 GPS 只用于导航。`align_descend` 每帧直接从 `scene.detections` 中选择归一化距离画面中心最近的目标，同时修正水平位置并下降。零目标中心投放会跳过该步骤。 | 到目标高度后，连续 5 个不同 `frame_id` 的画面中至少 3 帧位于对准范围，返回 `alignment_confirmed`；投放对准达到 30 s 时发零速并以 `alignment_timeout_accepted` 完成。 | 投放对准超时按成功继续投放；最终返航视觉对准仍保持超时失败。 |
| 投放 | `payload_release` 先生成一次 release PWM，在等待窗口维持零速，随后生成 hold PWM。零目标或一个目标时第一次投放同时控制 SERVO9/10；两个目标时分别控制 SERVO9、SERVO10，第二投放步骤在目标不足时安全跳过。 | 首 tick 为 `release_sent`，最终为 `payload_released`；跳过时为 `payload_release_skipped`。`detail` 记录 payload/target ID、SERVO 输出、PWM、等待状态与零速命令。 | 失败后继续下一步。当前 ActionResult 没有 dispatch/bridge 回执：SEND、安全或传输拒绝记录在 `last_dispatch.skipped/errors`，仍可能得到 `payload_released`，因此仍需核对 dispatch 和 bridge 日志。 |

连续的 `align_descend` 在停止、超时或丢失视觉时都会发送显式零速，
并清除旧连续命令；这不能替代飞手或地面站接管。

投放对准段的有效参数是：目标高度 1.2 m、下降率 0.30 m/s、对准范围
`|ex|/|ey| ≤ 0.02`、水平 P 增益 0.3 且限幅 0.25 m/s。返航视觉下降段高度为 0.3 m，
水平限幅及对准范围为 0.3。超时固定为 30 s，5 帧窗口和 3 帧命中数固定在 Action 中。
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
  → LinkManager
  → Flight Controller / YOLO / Servo
```

Mission 和 Action 不得直接调用 pymavlink 或 `LinkManager`。飞控请求只允许经由
`ActionDispatcher` 派发；投放只允许走：

```text
payload_release → set_servo → MAV_CMD_DO_SET_SERVO
```

## Blackboard 数据流

Mission 步骤可用 `save_as` 保存 `ActionResult.output`，后续参数使用完整字符串 `$path`
读取。当前 v2 投放主线的主要数据流是：

```text
gps_capture_view save_as drop_scan_view_1..4
  → gps_fuse_views save_as drop_scan
  → drop_scan.localized_objects

select_drop_targets save_as drop_targets
  → drop_targets.target_slots
  → first/second alignment enabled
  → first release SERVO outputs and second release enabled

goto_waypoint（融合目标 GPS 上方）
  → align_descend
  → 每个新画面选择中心最近检测
  → MissionOrchestrator 清理连续命令并保持位置
  → payload_release save_as drop_release_1..2
```

投放区融合除要求每个聚类至少 3 个有效观测外，还要求这些观测至少来自 3 个不同扫描
航点，避免单一画面内的重复框满足融合门槛。融合输出携带的总权重会参与同类别目标的
稳定排序。零目标时 `target_slots[0]` 是 FIELD `(0, 32.5)` 的中心回退点，飞机在 2.5 m
高度直接同时投放两枚载荷。一个目标时第一槽使用融合 GPS，并在一次对准后同时投放两枚
载荷；两个目标时两个槽分别执行。`align_descend` 不消费固定 track，只使用飞机到达位置
后的当前画面；投放计划只控制该步骤是否执行。

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

1. `config/app.yaml` 中系统 `executor.send_commands` 已人工开启；
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
- 危险标识排名依赖当前模型、阈值和观察点覆盖，不能只凭模板校验判定有效。
- Action 连续控制由 P0 Safety Pipeline 统一裁决。
- 报告结果可供 Web UI 和 blackboard 使用，但最终比赛交付形式仍需按规则确认。
