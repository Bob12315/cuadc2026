# PA-00 平台适配层接口基线

本文冻结 PA-00 执行时的只读事实，供后续 `PA-01` 至 `PA-31` 做 characterization、迁移和回滚对照。
它描述的是 2026-08-16 当时源码快照，不是
[平台适配层目标接口](../plans/platform_adapter_interface_refactor_plan.md)。后续任务若发现实现已经变化，必须先
重新核对源码，不能把本文当成新的生产契约。

## 1. 快照身份与验证基线

盘点日期：2026-08-16。

| 项目 | 实际结果 |
| --- | --- |
| 工作区 | `.`（本仓库根目录；当时使用 Windows 工作区，具体绝对位置不作为配置） |
| 源码身份 | 目录没有 `.git`；按源码快照处理，没有可信 revision、branch、`git status` 或 `git diff` 基线 |
| 平台 | Windows 11 `10.0.26200`，AMD64 |
| 可用 Python | Codex bundled CPython 3.12.13，MSC v.1944 64 bit |
| 默认 `python` | WindowsApps 别名，不能运行本项目验证 |
| pytest | bundled Python 没有安装 pytest；`python -m pytest ...` 以 `No module named pytest` 退出 |
| SEND 基线 | `config/app.yaml:112` 为 `executor.send_commands: false` |
| 外部系统 | 未连接真机、远程 RK3588、MAVLink endpoint；未启动 app、YOLO、SITL 或 Web 服务 |

本次实际运行：

```text
PASS  python -m compileall -q app application contracts execution field guidance
      missions observability telemetry_link fusion yolo_app web_ui scripts
PASS  python scripts/validate_architecture_boundaries.py
PASS  python scripts/validate_action_missions.py
      drop_two_targets.json: 23 steps
      recon_gps.json: 11 steps
      rescue_2026_full_auto.json: 36 steps
BLOCKED  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q
         tests/unit tests/contracts tests/integration tests/sitl
         原因：当前可用解释器没有 pytest；PA-00 不安装依赖或新增测试设施
```

最近一次全量 pass 数只能从历史完成记录读取，不能视为本快照本次已验证结果。

## 2. 当前组装与 owner

当前 production composition root 仍由 `application.runner.ApplicationRunner` 直接创建服务；
`app/bootstrap.py` 创建 telemetry/YOLO transport 对象：

```text
ApplicationRunner
  -> ServiceManager
       -> YoloUdpReceiver
       -> LinkManager
       -> VehicleStateAdapter / VehicleCommandAdapter
  -> FieldService / CalibrationSession / _ReferenceStore
  -> BlackboxRecorder
  -> SystemControlService / MissionApplicationService
  -> WebServices.from_runner()
  -> WebUiServer / FastAPI routers
```

证据：`application/runner.py:90-179`、`app/bootstrap.py:171-226`。`WebServices.from_runner()` 在
`application/web_services.py:57-83` 读取 Runner 和 `runtime.dispatcher.authorization`，当前还没有目标
`PlatformPorts` aggregate，也没有 `contracts/platform/` 或 `application/ports/`。

## 3. Web inbound 基线

### 3.1 路由清单

下表记录 43 个显式 `/api` 路由。FastAPI 自动生成的 OpenAPI/docs 路由不属于项目自定义接口。

| 方法和路径 | 当前输入 | 当前成功响应/调用者 | 当前失败行为 |
| --- | --- | --- | --- |
| `POST /api/auth/login` | JSON `{password}` | `{ok,operator,role,csrf_token}`，设置 `uav_session` cookie；调用 `WebSecurity.login` | 401/429 FastAPI `{"detail":...}`；其他异常未统一映射 |
| `POST /api/auth/logout` | cookie；修改请求受 auth/CSRF middleware 保护 | `{ok:true}`，删除 cookie | middleware 401/403 |
| `GET /api/status` | 无 | 原样返回 `WebServices.status_snapshot()` | 未捕获异常进入 FastAPI 500 |
| `GET /api/audit?limit=` | `limit` clamp 到 1..500 | `AuditLog.read_latest()` 的裸数组 | 受认证保护；无 cursor；读文件错误未映射 |
| `GET /api/events` | 无 | `status_snapshot()["events"]` 裸数组 | 未捕获异常进入 500 |
| `GET /api/actions/list` | 无 | `{ok:true,actions:[...]}` | 无显式业务错误 |
| `GET /api/actions/status` | 无 | `{ok:true,action_lab:{...}}` | 捕获所有异常并返回 HTTP 200 `{ok:false,error}` |
| `POST /api/actions/start` | `ActionStartRequest{name,params,authorize,target_source}` | 启动后在 HTTP handler 内调用一次 `action_lab_tick()`；返回 result/status/dispatch | source 错误为 400/409；其他失败多为 HTTP 200 `{ok:false,...}` |
| `POST /api/actions/stop` | 无 body | result/status/dispatch envelope | 所有异常变为 HTTP 200 `{ok:false,error}` |
| `POST /api/actions/reset` | 无 body | 同上 | 同上 |
| `POST /api/manual-step-move` | `{direction,step_m,authorize,target_source}` | `{ok,result,action_lab}`；要求 `authorize=true` | 未授权为 400；source mismatch 为 409；其他异常未统一映射 |
| `GET /api/action-mission/status` | 无 | `{ok:true,action_mission}` | HTTP 200 `{ok:false,error}` |
| `GET /api/action-mission/templates` | 无 | `{ok:true,templates}`，包含配置相对 `path` | 模板装载错误可为 404/500 `detail` |
| `GET /api/action-mission/template/{name}` | path name | `{ok:true,template:<原 JSON>}` | 404/500 `detail` |
| `POST /api/action-mission/configure` | `{steps:[{name,params,save_as,label,on_failed}]}` | Web router 直接构造 `MissionActionStep`，返回 mission status | HTTP 200 `{ok:false,error}` |
| `POST /api/action-mission/start` | `{authorize,target_source}` | `{ok,action_mission}` | source 400/409；其他 HTTP 200 `{ok:false,error}` |
| `POST /api/action-mission/stop` | 无 | `{ok:true,action_mission}` | HTTP 200 `{ok:false,error}` |
| `POST /api/action-mission/reset` | 无 | 同上 | 同上 |
| `POST /api/action-mission/tick` | 无 | HTTP 请求直接推进一次 Mission | HTTP 200 `{ok:false,error}` |
| `POST /api/action-mission/skip-current` | 无 | `{ok:true,action_mission}` 并直接写 audit | HTTP 200 `{ok:false,error}` |
| `POST /api/control/send` | `{enabled:bool}` | `{ok,message}`，调用 `SystemControlService.set_send` | operation 的 `ok:false` 仍是 HTTP 200 |
| `POST /api/telemetry/source` | `{source:"sitl"|"real"}` | `{ok,message}`，调用 `switch_source` | DTO/route 400；operation 拒绝仍是 HTTP 200 |
| `GET /api/field-reference/status` | 无 | `FieldService.status()` dict | 所有异常变为 HTTP 200 `{ok:false,error}` |
| `POST /api/field-reference/reset` | 无 | `_ReferenceStore.reset()` dict | 同上 |
| `POST /api/field-reference/freeze` | 无 | `_ReferenceStore.freeze()` dict | 同上 |
| `POST /api/field-profiles/{id}/runtime-sampling/start` | profile ID | Calibration start dict | HTTP 200 `{ok:false,...}`，并追加 Field audit |
| `POST /api/field-reference/runtime-sampling/finalize` | 无 | Calibration finalize/apply dict | 同上 |
| `POST /api/field-reference/runtime-sampling/cancel` | 无 | Calibration cancel/reset dict | 同上 |
| `POST /api/field-reference/runtime-sampling/start` | strict float lat/lon | Competition sampling start dict | 非有限/越界为 400；按错误字符串猜 400/409；其他失败可 HTTP 200 |
| `GET /api/field-profiles` | 无 | `{ok:true,profiles:[...]}` | 异常被包装在条目或 `{ok:false}` 中 |
| `GET /api/field-profiles/{id}` | ID | profile projection dict | not found/parse error 为 HTTP 200 `{ok:false,...}` |
| `GET /api/field-profiles/{id}/validate` | ID | `{ok,profile_id,errors,warnings}` | HTTP 200 error dict |
| `POST /api/localization/clear` | 无 | `{ok,message}` | operation 的 `ok:false` 仍是 HTTP 200 |
| `GET /api/yolo/stream` | 无 | `{port,path}`；router 直接读取 `config/yolo.yaml` | YAML/IO 错误被吞掉并回退 port 8081 |
| `GET /api/camera-recording/status` | 无 | `{ok:true,recording:<app 本地 dict>}` | 未捕获异常进入 500 |
| `POST /api/camera-recording/toggle` | 无 | `{ok,message,recording}` | UDP send 失败通常仍通过 200 body 表达 |
| `POST /api/yolo/target/{action}?track_id=` | action + query track ID | `{ok,message}`；router 先拼自由字符串命令 | 非法 action 为 400；command 失败为 HTTP 200 |
| `POST /api/services/telemetry/reconnect` | 无 | `{ok,message}` | unavailable 仍是 HTTP 200 body |
| `POST /api/services/{service}/restart` | service path | `{ok,message}` | 未配置 service 仍是 HTTP 200 body |
| `GET /api/config/files` | 无 | approved 相对路径的裸数组 | 未捕获异常进入 500 |
| `GET /api/config/file?path=` | path query | `{path,content,has_backup}` | `ValueError` 映射 404 `detail` |
| `PUT /api/config/file?path=` | `{content,action}` | `{diff,ok,message}`；router 直接写文件并选择 apply side effect | 校验/路径错误为 400；apply 失败仍是 HTTP 200 |
| `POST /api/config/restore?path=&action=` | query | `{diff,ok,message}`；直接交换 `.bak` | `ValueError` 为 400；apply 失败仍是 HTTP 200 |

额外路由：`GET /` 返回 `static/index.html`；`WS /ws/status` 每 250 ms 在 worker thread 中调用
`status_snapshot()`，发送无版本的完整裸 dict。WebSocket 只校验 session cookie 和 Origin；没有 Host
检查、subscribe、cursor、stream session/sequence、heartbeat、bounded per-client queue 或 gap/resync。
证据：`web_ui/api_routers.py:34-56`。

### 3.2 当前请求 DTO 和响应形状

Pydantic 输入位于 `web_ui/dto.py:9-60`。Action/Mission params、`on_failed` 仍是裸 dict；大多数 mutation
没有 request ID、idempotency key 或 revision。成功响应没有统一 envelope，常见形状为：

```text
{"ok": true, ...}
{"ok": false, "error": "<exception string>"}
{"ok": false, "message": "<operation message>"}
<裸 status dict>
<裸 list>
```

FastAPI/Pydantic 的标准错误、middleware rejection 和显式 `HTTPException` 使用
`{"detail":...}`，业务异常则经 route 的宽捕获转成 HTTP 200。没有稳定 `ApplicationError`、Problem
Details、request/correlation ID 或 ETag。

证据：`web_ui/routers/actions.py:28-90`、`missions.py:15-88`、`field.py:15-76`、
`web_ui/security.py:150-211`。

### 3.3 WebServices 当前调用面

`application/web_services.py:8-55` 暴露：

- `SystemControlPort`: `set_send`、`switch_source`、`reconnect`、`restart_service`、自由字符串
  `target_command`、`recording_status`、`recording_toggle`。
- `MissionControlPort`: source getter；Action start/tick/stop/reset/status；Mission
  configure/start/tick/stop/reset/skip/status；manual step。
- 15 个 `Callable`/值字段：status、Field reference/profile/sampling、localization clear、Action specs、
  Action Lab enabled、authorization snapshot。

`WebServices.from_runner()` 直接镜像 Runner 方法，并通过
`runner.action_runtime.dispatcher.authorization` 读取 Execution 内部状态。Router 仍导入
`MissionActionStep`、直接读取 YAML、直接拥有 `ConfigStore`/`AuditLog`。当前边界的主要 production
caller 是各 `web_ui/routers/*.py` 和 status WebSocket。

## 4. telemetry / MAVLink 基线

### 4.1 Port 与 LinkManager 表面

`contracts/ports.py:6-21` 的 `VehicleStatePort` 同时包含读取和切源，返回 `Any`；
`VehicleCommandPort` 只声明六个 motion/clear 方法。`telemetry_link/ports.py:35-47` 的实际
`VehicleCommandAdapter` 用 `__getattr__` 把除读/生命周期黑名单外的任意名称转发到 LinkManager。

`LinkManager` 当前公开表面：

| 类别 | 方法 | 返回/含义 |
| --- | --- | --- |
| 生命周期 | `start`、`start_background`、`stop` | `None` / background `Thread`；每个 source 各有 receiver、sender、queue |
| source/state | `get_active_source`、`switch_active_source`、`get_latest_*`、`get_source_*`、`get_link_status`、`is_connected` | switch 返回 bool；state 是多个 getter，不是单一原子 snapshot |
| queue 原语 | `submit_control_command`、`submit_action_command`、`submit_latest_action_command`、`clear_continuous_commands`、`clear_pending_local_position_actions` | 全部 `None`；没有 command ID 或 receipt |
| 飞行 one-shot | `set_mode`、`arm`、`disarm`、`takeoff`、`land`、`condition_yaw`、`change_speed`、`set_home_*`、`global_goto`、`local_position`、`reposition` | 构造 `ActionCommand` 并入优先队列；方法返回 `None` |
| 云台/payload | ROI、gimbal configure/angle/rate、`set_servo`、`set_relay` | one-shot 或 latest gimbal slot；返回 `None` |
| 连续 motion | `send_velocity_command`、`send_yaw_rate_command`、`stop_control`、`stop_body_velocity`、`stop_body_velocity_and_clear` | 更新 latest control 或入 stop barrier；返回 `None` |
| 语义 wrapper | `goto_local_ned`、`send_body_velocity`、`set_servo_output_pwm` | 委托旧方法；返回 `None` |
| recovery | `hold_current_local_position` | 有有效 LOCAL_NED 时入队 hold 并返回 bool |
| 禁用路径 | `release_payload` | 总是抛 `NotImplementedError`；sender 对 `RELEASE_PAYLOAD` 也不发 wire |

方法证据：`telemetry_link/link_manager.py:197-815`。正式 payload 路径仍是
`SetServo -> set_servo_output_pwm/set_servo -> MAV_CMD_DO_SET_SERVO`。

### 4.2 Queue、source switch、retry 和 stop

`CommandQueue` 当前有三个独立容器：

- `ControlCommand` latest-only slot；新值覆盖旧值。
- `GimbalRateCommand` latest-only slot。
- `ActionCommand` heap：priority 数字越小越优先，同 priority 按 sequence FIFO；
  `put_latest_action()` 只替换相同 action type。

证据：`telemetry_link/command_queue.py:13-107`。

source switch 的当前顺序是：`SystemControlService.disable_send()` 撤销 authorization、停止已登记 continuous
并尝试清导航，然后 `VehicleStateAdapter.switch_active_source()`；`LinkManager.switch_active_source()` 更新
active source 后只清两个 source 的 continuous/gimbal latest slot。它不直接清两个 source 的全部 one-shot
heap，也没有 source/session revision 或 cancellation receipt。证据：`application/system_control.py:35-60`、
`telemetry_link/link_manager.py:218-236`。

sender 在断线时清 continuous/gimbal，并每轮从 heap 取出一个 action 丢弃。stale monitor 停/join
receiver/sender、清三类 queue、close client 后重连。没有 link session/generation；state cache 和 ACK 不隔离
旧 receiver generation。证据：`telemetry_link/command_sender.py:56-94`、
`telemetry_link/link_manager.py:113-142`。

action wire write 抛异常时，sender 在自己的线程内 sleep，减少 `retries_left` 后 requeue；
`requeue_action()` 把 `created_at` 改为当前 wall time。没有稳定 deadline、idempotency、status/event 或 ACK
correlation。证据：`command_sender.py:368-373`、`command_queue.py:105-107`。

当前 stop 有两种不同语义：

- `stop_body_velocity()` 把 zero `STOP` 写到 latest control slot，之后可被新 continuous 覆盖。
- `stop_body_velocity_and_clear()` 先清 latest control，再把 priority 0 的 `STOP_BODY_VELOCITY` 放进 one-shot
  heap；sender wire write zero 后再次清 latest control，以覆盖 transition race。

后者是当前旧安全 barrier，但没有 generation final-check、单一 write gate、session binding、deadline 或
`TRANSMITTED|STOP_UNDELIVERABLE` receipt。证据：`link_manager.py:705-741`、
`command_sender.py:116-131`。

### 4.3 当前 `sent` 的准确含义

Execution handler 调用 LinkManager/command adapter 后立即返回 `{"status":"sent"}`；Dispatcher 随即把
项目加入 `dispatch["sent"]`。这时 one-shot 通常只进入 heap，continuous 只更新 latest slot；writer 可能尚未
运行、链路可能随后断开、write 可能失败，也没有 ACK/完成观察。证据：
`execution/dispatcher.py:273-300`、`execution/handlers/*.py`、`link_manager.py:277-285`。

因此当前 `sent` 仅表示“安全裁决后调用了旧 command facade，且调用未同步抛错”，不能解释为 queued、
wire transmitted、flight-controller ACK 或 observed completion。

### 4.4 已确认的 fail-open 风险

production `ActionDispatcher` 注入的是只写 `VehicleCommandAdapter`（`application/runner.py:147-153`）。该
adapter 故意没有 `get_active_source()`/`get_latest_drone_state()`；Dispatcher 因此在
`execution/dispatcher.py:835-839` 回退 `source="test"`，Safety Pipeline 对 test source 跳过若干 telemetry
连接/stale/control checks。此风险分派给 PA-02；PA-00 不修改 production wiring。

## 5. Perception、Vision command 与录像基线

### 5.1 Perception v1 wire

`contracts/perception_protocol.py:7-33` 定义 `PERCEPTION_SCHEMA_VERSION=1`：

```text
schema_version
sequence                    # publisher 当前使用 target.frame_id
captured_at_monotonic
published_at_monotonic
target: Mapping[str, Any]
scene: Mapping[str, Any]
```

`target` 字段来自 `CurrentTarget.to_dict()`；app 解码字段为 timestamp/frame_id/valid/tracking/track/class/
confidence/bbox/image size/target size/ex/ey/lost count。`scene` 包含 timestamp、frame_id、image width/height
和 detections；每个 detection 含 track/class/confidence/bbox/center/size/ex/ey/target_size。证据：
`yolo_app/udp_publisher.py:19-28`、`app/bootstrap.py:103-168`、`yolo_app/models.py`。

app receiver 绑定 `config/app.yaml` 的 UDP endpoint，`recvfrom(65535)`，只接受 schema 1；全局
`_last_sequence` 要求严格递增，并用本机 `time.monotonic() - published_at_monotonic <= 2s` 判断 stale。
target 与 scene 在同一个锁内更新，但对外由两个 getter 分别复制，Runner 又分别调用。没有 producer ID、
process session、clock domain、HELLO、tombstone、datagram/detection 上限或显式 truncation。YOLO 重启后
frame ID 从低值开始时，旧 receiver 的 `_last_sequence` 可持续拒绝新包。证据：
`app/bootstrap.py:21-93`。

### 5.2 Vision command v1 wire

`VisionCommand` 当前字段：

```text
schema_version = 1
sequence                    # app 进程全局 itertools counter
sent_at_monotonic
action                     # lock_target/unlock_target/switch_next/switch_prev/recording_start/recording_stop
track_id?                  # 仅 lock 使用
```

`YoloCommandClient.send()` 每次创建临时 UDP socket，只验证本地 enabled，然后 `sendto()` 并返回 `None`。
YOLO receiver `recvfrom(4096)`，用单一 `_last_sequence` 和跨进程 monotonic 差小于 2 秒判断，之后返回
`CommandMessage` 给主循环。没有 client/process session、command ID、TTL、ACK/status、dedupe cache 或
actual state reply。证据：`application/yolo_command_client.py:20-43`、
`yolo_app/command_receiver.py:27-83`。

Web 先把 path/query 转成 `"target ..."` 字符串，`SystemControlService` 再 split 并转成 wire action；
`next/prev` 仍以不可重放 edge command 发送。当前 app client 实际读取 `yolo.yaml` 中不存在的
`command_receiver` mapping，因此退回 `127.0.0.1:5006/enabled=true` 默认；YOLO 自身读取平铺的
`command_ip=0.0.0.0`、`command_port=5006`。证据：`web_ui/routers/vision.py:43-51`、
`application/system_control.py:74-87`、`app/config.py:138-147`、`config/yolo.yaml:60-63`。

### 5.3 录像 actual state

app 的权威表面目前是 `SystemControlService._recording` 本地 dict：
`{recording,path,message,trigger?}`。UDP `sendto()` 不抛错后，start 立即设置 `recording=true` 和 wildcard
`runtime/videos/camera_*.mp4`；stop 立即设置 false。它没有读取 YOLO actual state。

YOLO `RawFrameRecorder` 才实际创建 `camera_<timestamp>.mp4`、持有 writer/path/frames/error，并拥有默认
10 分钟 monotonic hard timeout。command 在 YOLO frame loop poll 后应用；actual status 不回传 app。
证据：`application/system_control.py:89-119`、`yolo_app/main.py:56-117`、
`yolo_app/raw_frame_recorder.py:12-115`。

## 6. Field、Profile 与 Calibration 基线

### 6.1 当前入口与 owner

`FieldService` 同时拥有 `_ReferenceStore`、`CalibrationSession`、profile 目录查找、drone snapshot getter
和 Web status projection。它把 `_ReferenceStore.reference` 这个 live mutable `FieldReference` 绑定给
`RuntimeContextBuilder`，并通过 `reference` property 对外返回同一对象。证据：
`field/service.py:18-38`。

当前入口：

| 域 | 入口 | 当前返回 |
| --- | --- | --- |
| reference query | `FieldService.status()` | `{ok,field_reference,telemetry}` 裸 dict |
| lifecycle | `reset()`、`freeze()` | 至少 `{ok}`，失败用 `{ok:false,error}` |
| registered sampling | `start_runtime_profile_sampling(profile_id)` | session/sampling dict；template-only 拒绝 |
| competition sampling | `start_competition_runtime_sampling(lat,lon)` | 用 `competition_runtime` template 建 runtime profile |
| observation | `observe_runtime_profile_sampling(snapshot, observed_at_s)` | Runner 每周期主动推送 drone dict；成功自动 finalize/apply/freeze |
| finalize/cancel | `finalize_runtime_profile_binding()`、`cancel_runtime_profile_sampling()` | Calibration session dict |
| profile list/get/validate | Runner 内 `field_profile_*` 方法 | Web-compatible dict，而不是 repository DTO |

### 6.2 当前 status/schema

`FieldService.status()` 的 `field_reference` 包含：confirmed/frozen/readiness、origin/forward marker、heading
rad/deg、active source、synced flag、profile ID、runtime binding 和 warnings；`telemetry` 同时包含 GPS
valid/lat/lon/time/fix/satellites/eph/epv。证据：`field/service.py:38-67`。

`CalibrationSession.status()` 包含 state、template/runtime/profile identity、input/forward marker、last/
preview error、candidate_ready、sampling、candidate summary、geometry、last result。状态字符串当前包括
`idle`、`sampling`、`candidate_ready`、`applied`、`sampling_failed`、`apply_failed`。成功 observation 会自动
finalize，随后跨 `_ReferenceStore` 和 `RuntimeContextBuilder` 做 snapshot、apply、sync、freeze；失败再跨
两个对象 rollback。证据：`field/calibration_session.py:50-217,257-414,539-600`。

profile schema 只接受 v3；正式 config 目录与 runtime 目录都被遍历，config 在前。相同 profile ID 没有
统一 repository 去重：Runner list 可同时列出两份，而 load/get 遇到 config 成功后返回。目录列表和
profile projection 在 Runner 中重复实现，FieldService 又有自己的固定 `_PROFILE_DIRS`。证据：
`field/profile_service.py:9-37`、`field/service.py:18-23,116-125`、
`application/runner.py:433-533`。

坐标算法仍是 schema-v3 FIELD ↔ GPS/GLOBAL；没有 FIELD → LOCAL_NED。PA-00 不改变任何数值、阈值、
自动 freeze 或 profile 文件。

## 7. Observability、Audit 与 Blackbox 基线

### 7.1 内存 operational events

Runner 持有 `deque(maxlen=160)`，记录裸 dict：

```text
{"timestamp": time.time(), "level": <str>, "message": <str>}
```

`GET /api/events` 从完整 status 中取该 deque 的最近项目；没有 event/schema/correlation/run/source ID、
cursor、fan-out receipt 或 sink 隔离。证据：`application/runner.py:107,393-397`、
`web_ui/status_adapter.py:92-107`。

### 7.2 Audit JSONL

`web_ui.AuditLog` 是具体同步 JSONL adapter，字段为：

```text
timestamp, source, action, ok, message,
operator, source_address, run_id, target_source,
operation_type, result, reason
```

每次 `append()` 在 caller thread 创建目录、打开、写一行；没有 schema/audit/request/correlation ID、
durability receipt 或脱敏 DTO。`read_latest()` 用 `read_text().splitlines()` 全文件载入后截尾。普通 operation
audit 与 security audit 写不同配置路径，但共用相同无版本 shape。证据：`web_ui/audit.py:9-60`、
`web_ui/security.py:76-82,97-122,196-210`。

除 Field helper 捕获 append 异常外，多数 route/middleware audit failure 可传播；middleware 在业务 response
生成后同步 append，因此当前没有“副作用已完成但 audit 失败仍保持原 receipt”的统一保证。

### 7.3 Blackbox v1 JSONL

`BlackboxRecorder` 由 Runner 直接持有。Runner 每周期在 vehicle armed 时自动 start、disarmed 时 stop，
再同步调用 `record()`；主循环当前传入 `send_commands=False` 字面值。证据：
`application/runner.py:96,270-286,299-342`。

文件名是 `<local timestamp>_runNNN.jsonl`，同 basename meta 为 `.meta.json`。meta：

```text
created_at
format = "uav_project.blackbox.v1"
data_file
sample_hz
trigger {reason, started_at}
fields {perception,drone,gimbal,fused,commands,events}
```

每条 data record：

```text
t, dt, seq
runtime {mode,mission,health,hold_reason,send_commands,
         enable_gimbal,enable_body,enable_approach,control_allowed,target_valid}
perception?, scene?, drone?, link?, gimbal?, fused?, inputs?
command_raw?, command_shaped?
events?                       # mode/target-valid/SEND transition string list
debug?                        # Runner 增加 commands、action_lab、align_descend projection
```

`record()` 直接 JSON encode/write/flush/stat/rotate/prune；I/O 与控制 loop 同线程。queue、worker、drop policy、
session/barrier、partial flush 或 structured recorder status 均不存在。write OSError 会把 recorder disabled；
rotation/retention 同步操作 data/meta。证据：`observability/blackbox.py:16-264`。

## 8. 当前 caller 总结

| 边界 | production caller | concrete owner |
| --- | --- | --- |
| Web inbound | Browser/static JS、status WebSocket | `web_ui/api_routers.py` + routers + `WebSecurity` |
| Web use case facade | 各 Web routers | `WebServices.from_runner()` 镜像 Runner/services |
| Vehicle state | Runner、Field status、Web status、Execution 旧 getattr 路径 | `LinkManager`/`StateCache` 经 `VehicleStateAdapter` |
| Vehicle command | `ActionDispatcher`/handlers；System/mission stop cleanup | `LinkManager` 经 dynamic `VehicleCommandAdapter` |
| Perception | Runner/Fusion/Actions | `YoloUdpReceiver` |
| Vision command | Web/SystemControl/Dispatcher | 每次调用新建 `YoloCommandClient` UDP socket |
| Field | Runner、RuntimeContextBuilder、Mission preflight、Web | `FieldService` + `_ReferenceStore` + `CalibrationSession` |
| Events | Runner/SystemControl/ResultService | Runner 内 deque |
| Audit | Web middleware 和部分 routers | Web 层 `AuditLog` JSONL |
| Blackbox | Runner cycle | 同步 `BlackboxRecorder` |
| Camera recording | Web 与 Mission lease | app local optimistic dict；YOLO `RawFrameRecorder` 是未回传的 actual owner |

## 9. 已有测试与 characterization 缺口分派

本节只登记，不在 PA-00 新增 fake 或测试。

| 域 | 当前已有覆盖 | 缺失 characterization/fake | 后续任务 |
| --- | --- | --- | --- |
| Web/security | `test_p0_web_security.py` 覆盖 modifying auth/CSRF/origin；Web module/fetch owner 静态测试 | 43 路由逐项 request/response/error golden；HTTP 200 error inventory；WebSocket auth/Host/payload/慢客户端；request ID/revision/idempotency | PA-21～PA-25，删除证据 PA-27 |
| WebServices | 显式 services 非 Runner 的静态断言 | `from_runner` 全调用面 contract、router 不直接 tick/YAML/I/O 的行为锁、旧/新 route 单 use-case | PA-21、PA-22、PA-24 |
| telemetry state | stale/source/config/state parse 与部分 interface tests | 原子 snapshot、receiver generation、link session、旧包晚到、wait_next、read/write Port 故障 | PA-01、PA-02、PA-04 |
| telemetry queue/sender | latest-only、priority clear、disconnected drop、retry/wire encoding、stop-and-clear 基本测试 | pausable writer 的 dequeue/check/write race；command ID/lifecycle/event；writer failure receipt；source/session/deadline/idempotency；XOR backend | PA-01、PA-05～PA-08 |
| ACK/completion | 无 ACK router；现有测试只检查 wire method/参数 | ACK early/late/source/correlation/discard；独立 completion observer；session lost | PA-09、PA-10 |
| YOLO perception | publisher 同 envelope target+scene；receiver schema/order/stale 有部分间接覆盖 | HELLO/session/tombstone/restart low sequence；atomic Port getter；cross-clock TTL；datagram/detection upper bound；malformed allocation bound | PA-01、PA-11、PA-12 |
| Vision command | v1 recording action 接收；拒 legacy/out-of-order | UDP loss/duplicate/reorder/restart fake；client/process session；command ID/dedupe/ACK/actual lock；next/prev → explicit track ID | PA-01、PA-13 |
| Camera recording | RawFrameRecorder MP4、10 min timeout、重复 start refresh | app/YOLO actual-state round trip；ACK loss/retry exactly-once；start/open、stop/flush failure；process restart；Mission lease | PA-14 |
| Field/profile | schema-v3 round trip、single shared reference、apply rollback、success frozen、retired v2 | profile content/priority/duplicate golden；injected dirs；immutable snapshot/version/generation；concurrency/stale command; repository unavailable | PA-15、PA-16 |
| Calibration | success/rollback 由 schema-v3 tests 部分覆盖 | operation/observation ID replay、session revision、base ReferenceVersion conflict、atomic commit point、restart invalidation、failure injection | PA-17 |
| events | 仅 Web status/deque 间接覆盖 | typed schema/ID/cursor、fan-out、每 sink bounded queue、slow/failure isolation、control-loop latency | PA-01、PA-18 |
| audit | Web security 测试会产生 audit；没有独立 sink contract | schema/correlation/actor/source/secret redaction、ACCEPTED/PERSISTED、cursor、慢盘/只读/权限、业务 receipt 不被 audit failure 改写 | PA-01、PA-19 |
| blackbox | `test_blackbox_recorder.py` 覆盖 v1 JSONL 和 armed session | v1 golden 全字段、rotation/retention/meta 配对、slow/full/readonly、overflow/drop range、worker crash、barrier/partial flush/有界 shutdown | PA-01、PA-20 |

## 10. 迁移决定与风险登记

以下不是 PA-00 修复项：

1. PA-02 必须优先删除 production `source="test"` fallback，显式注入只读 StatePort，状态缺失 fail closed。
2. PA-04～PA-10 必须把当前 `sent` 拆成 submission/queue/transport/ACK/completion，并先建立 session、
   generation 和可信 stop barrier；不得用命名变更掩盖旧语义。
3. PA-11～PA-14 按 receiver-first 顺序升级 YOLO；在 actual ACK/status 前不能把 UDP send 显示为
   target/recording 已应用。
4. PA-15～PA-17 必须先锁 profile golden，再引入 immutable ReferenceVersion 和单事务 calibration；不能
   改 schema-v3 数值或自动 freeze 默认行为。
5. PA-18～PA-20 必须隔离 event/audit/blackbox sink；安全收口和控制 loop 不等待文件 I/O。
6. PA-21～PA-25 才能删除手工 tick、建立明确 Application Ports 和 Web v1；PA-00 不改任何 route shape。
7. 所有 writer 切换保持 exactly one；兼容删除只在 PA-26～PA-30 的证据门禁后进行。

## 11. PA-00 安全不变量结论

- 本切片仅新增本文并更新计划完成记录；没有修改 production Python、测试、YAML、Mission 或 wire/file
  writer。
- `executor.send_commands` 仍为 false。
- 没有连接真机、远程测试机或任何 telemetry endpoint，没有启动真实服务或发送任何命令。
- PA-01 仍未开始；所有缺口只登记到对应后续 PA。
