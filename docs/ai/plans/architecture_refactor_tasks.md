# 全项目架构重构任务书

本文是后续 AI/Coding Agent 分会话执行架构重构的唯一任务清单。每个会话只执行一个明确的
任务编号，不得顺手推进后续任务。任务完成且验收通过后，才把对应复选框改为 `[x]`，并在
任务下追加简短完成记录。

本文规划的是当前 Action Mission 主线的整理，不是恢复旧 mission/stage/control 栈。若本文
与 `AGENTS.md`、`docs/ai/architecture/current_architecture.md` 或安全文档冲突，以更严格的安全限制为准。

## 0. 接管规则

新会话开始时必须：

1. 阅读根目录 `README.md`、`AGENTS.md`；
2. 阅读 `docs/ai/README.md`、`docs/ai/architecture/current_architecture.md`、
   `docs/ai/architecture/action_contracts.md`、`docs/ai/architecture/deprecated_paths.md`、
   `docs/ai/guides/task_checklist.md`；
3. 阅读 `docs/developer/coordinate_frames.md`、`field_origin_heading.md`、`safety.md`；
4. 阅读本文完整内容，并确认用户指定的任务编号；
5. 若存在 `.git`，运行 `git status --short` 和 `git diff --stat`；若不存在，明确记录“源码快照”，不得
   伪造 revision、diff 或 clean 状态；
6. 保留已有工作区修改，不得使用 `git reset --hard`、`git checkout --` 或类似操作；
7. 未经用户明确要求，不提交、不推送、不启动真实硬件服务；
8. 每次只修改任务范围内的代码、测试和必要文档。

本文创建时的工作区已经完成第一轮 `app/` 死代码清理但尚未提交，包括删除
`app/health_monitor.py`、`app/ui_commands.py` 和若干无调用包装器。后续会话不得还原这些
文件。该清理已通过 `1525 passed, 1 skipped`、架构校验、Action Mission 校验和 P2 Field
Reference 校验。以上为历史基线；后续任务的已有修改和测试结果必须重新检查，
不能把当时的未提交状态、测试数或已经退役的中间接口视为当前事实。

## 1. 永久架构和安全约束

- Action Mission 是唯一任务主线。
- Web UI 是唯一正式人工操作入口。
- 不恢复 `MissionRunner`、`StageRegistry`、`CommandShaper`、
  `FlightCommandExecutor`、`missions/<mission>/mission.py` 或 stages 目录。
- `executor.send_commands` 默认必须严格保持 `false`。
- 飞行和 payload 实发必须同时通过系统 SEND 和本次 run authorization。
- 所有飞行和 payload 请求必须经过统一 Execution Dispatcher 和 Safety Pipeline。
- Action 不得导入 pymavlink，不得直接访问 `LinkManager`。
- `app/`、`web_ui/`、`fusion/`、`field/`、`guidance/` 不得导入 pymavlink。
- pymavlink 只能由 `telemetry_link/` 拥有。
- 连续 BODY_NED 命令必须有 deadman、明确 stop/zero 和队列清理。
- 投放只能走 `payload_release Action -> set_servo -> MAV_CMD_DO_SET_SERVO`。
- `yolo_app/` 不得连接 MAVLink，也不得产生飞行命令。
- YOLO 正式部署只使用 RKNNLite、RK3588 NPU 和当前 FP16 RKNN 模型。
- 不新增 x86、CUDA、PyTorch 或 GPU 推理路径。
- `runtime/` 只存放日志、录像、SITL、blackbox 等运行产物，不创建同名 Python 包。

## 2. 目标运行形态

保持两个正式进程，不引入 ROS、消息代理或微服务：

```text
uav-app 控制进程
  Web UI
    -> Application Services
    -> Action Mission Engine
    -> Actions
    -> Execution Dispatcher
    -> Authorization + SEND + Safety Pipeline
    -> VehicleCommandPort
    -> telemetry_link / MAVLink

  Vision UDP + Vehicle State + Gimbal State + Field Reference
    -> Fusion
    -> immutable RuntimeSnapshot
    -> Action/Mission

uav-yolo 视觉进程
  Camera/Video
    -> RKNNLite
    -> detection/tracking/target selection
    -> versioned UDP perception protocol
    -> optional MJPEG and runtime/videos recording
```

生产部署继续使用 `uav-app.service` 和 `uav-yolo.service`。独立 telemetry 文本入口只能作为
开发诊断工具，不属于正式操作主线。

## 3. 目标代码结构

```text
app/
  __init__.py
  main.py
  config.py
  bootstrap.py

contracts/
  action.py
  effects.py
  perception.py
  state.py
  field.py
  ports.py

application/
  runner.py
  state_store.py
  mission_service.py
  result_service.py
  system_control.py

missions/
  engine.py
  blackboard.py
  template.py
  definitions.py
  common/actions/
    flight/
    perception/
    localization/
    payload/
    reporting/

guidance/
  align_descend.py
  visual_landing.py
  waypoint_math.py
  target_projection.py
  target_fusion.py

execution/
  authorization.py
  policy.py
  safety_config.py
  safety_pipeline.py
  dispatcher.py
  handlers/

field/
  models.py
  profile.py
  coordinates.py
  calibration.py
  geometry.py
  service.py

telemetry_link/
fusion/
web_ui/
yolo_app/
observability/
tools/
```

目录结构是目标边界，不要求一次性创建全部空目录。只有实际迁入代码时才创建模块。

## 4. 目标依赖方向

允许：

```text
app -> application, web_ui, adapters
web_ui -> application interfaces, contracts
application -> missions, execution, field, fusion, contracts
missions -> guidance, contracts, field read contracts
execution -> contracts, VehicleCommandPort
field -> contracts
fusion -> contracts
telemetry_link -> contracts
yolo_app -> perception protocol
```

禁止：

```text
missions -> app
web_ui -> app.SystemRunner
fusion -> telemetry_link concrete models
Action -> LinkManager
app -> pymavlink
field -> SystemRunner
```

不引入通用 DI 框架或全局事件总线。优先使用构造函数注入、明确 service 和不可变 snapshot。

## 5. 通用验证

每个任务至少运行与修改范围对应的定向测试，并运行：

```bash
git diff --check
python -m compileall app missions telemetry_link fusion yolo_app web_ui scripts
python scripts/validate_architecture_boundaries.py
python scripts/validate_action_missions.py
```

每个里程碑末尾必须运行：

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q tests/unit tests/contracts tests/integration tests/sitl
python scripts/validate_p2_field_reference.py
```

安全发送相关任务还必须运行 `python scripts/validate_p0_sitl.py` 的 dry-run/测试安全路径；不得
为了通过测试打开真实 SEND。

## 5.1 RK3588 远程测试机

用户提供了一台持续开机的 RK3588 测试机：

```text
SSH target: pi@192.168.5.33
```

认证口令属于敏感信息，不写入 Git 跟踪文档、脚本、配置、命令行、测试输出或日志。新会话
需要连接时，应使用交互式密码、用户提供的进程环境变量或后续配置的 SSH key；不得把口令
拼入 `sshpass`、shell history、URL 或工具调用参数。如果新会话无法取得认证信息，应向用户
索取或请用户配置 SSH key，不能猜测或在仓库中搜索明文口令。

远程测试默认规则：

1. 先完成本地 compileall、定向测试和架构校验，再连接测试机；
2. 第一次连接只做只读盘点：`hostname`、`uname -a`、Python/Conda/RKNNLite 版本、仓库路径、
   当前分支、`git status --short` 和 systemd 状态；
3. 测试机上的已有修改、配置、日志和模型都视为用户数据，不得 reset、覆盖或删除；
4. 未经用户明确授权，不执行 pull、checkout、安装依赖、复制仓库、覆盖配置、重启/停止服务；
5. 不得在远程测试中把 `executor.send_commands` 改为 true；
6. 不发送 arm、takeoff、land、位置、速度、yaw、servo 或 payload 命令；
7. 优先使用 `--send-commands false`、`--no-ui`、临时端口和 `runtime/` 下的临时输出；
8. 测试 YOLO 时不得新增 x86/CUDA/PyTorch 路径，也不得替换正式 FP16 RKNN 模型；
9. 若测试需要占用相机、NPU、UDP 端口或影响正在运行的 app/yolo 服务，必须先向用户说明影响
   并获得明确授权；
10. 远程验证结果要记录主机、代码 revision、配置 profile、命令、退出码和关键输出，但不得
    记录认证口令。

适合使用 RK3588 测试机的任务主要是 AR-18、AR-21、AR-22 和 AR-24。其他任务只有在本地
无法验证 ARM64/RKNN/真实系统服务行为时才使用远程测试机。远程机器可用不等于允许实飞或
允许修改其运行状态。

## 6. 任务清单

### 已完成基线

- [x] **BASE-01：第一轮 app 死代码清理**

  已删除退役自由文本 Web 命令链、未接入 HealthMonitor、旧 SystemRunner 包装器、缓存和
  少量无调用符号。源码净减少约 494 行。该状态可能仍在未提交工作区中，后续任务必须保留。

---

### Milestone A：先封闭安全边界

- [x] **AR-01：修复 `manual_step_move` 发送旁路并建立静态守卫**

  前置：BASE-01。

  目标：删除 `SystemRunner.manual_step_move()` 中的控制公式、pymavlink import 和
  `manager.local_position()` 直调。

  实现要求：

  - 在 `missions/common/actions/` 增加正式 `manual_step` Action；
  - Action 从 RuntimeSnapshot/context 读取 LOCAL_NED 位置和有效 yaw；
  - BODY 前后左右偏移转换使用纯函数，输出 `local_position` request；
  - Web 请求必须包含明确 `authorize=true`，授权绑定操作者、source、run ID 和 action；
  - 开始手动步进前停止当前 Action/Mission、撤销旧授权并清理连续/位置队列；
  - SEND 关闭、未授权、source 不匹配、telemetry stale/断线时不得发送；
  - 使用 `telemetry_link.frames.LOCAL_NED`，不得导入 pymavlink；
  - 更新 Action registry/spec、dispatch policy、Web DTO/API、前端确认和测试；
  - 扩展 `validate_architecture_boundaries.py`：禁止 `app/` 导入 pymavlink，禁止已知直接发送。

  验收：六个方向符号正确；双门控和 stale/source 失败测试齐全；全量测试通过；
  `rg -n 'pymavlink' app missions web_ui fusion` 只允许文档字符串或零结果。

  禁止：用新的 Web 特判直接调用 dispatcher/LinkManager；绕过 run authorization。

  完成记录（2026-08-13）：
  - 主要变更：新增正式 `manual_step` Action、Web 显式授权 DTO、六方向 BODY→LOCAL_NED 纯函数。
  - 删除/迁移：删除 SystemRunner 内 pymavlink 与 `local_position()` 直调旁路。
  - 安全边界：统一经过 run/source/SEND/telemetry/TTL/Safety Pipeline；开始前撤销旧授权并清导航。
  - 定向测试：manual step、dispatch policy、P0 safety 共 43 项通过。
  - 全量测试：1536 passed, 1 skipped。
  - 后续任务前置状态：ready。

- [x] **AR-02：删除剩余 app 空壳和无效状态开关**

  前置：AR-01。

  范围：`debug_runtime.py`、`ActionRuntimeDebugConfig`、`ControlRuntimeSwitches`、
  `web_status_service.py` 和 dispatch 薄辅助模块。

  实现要求：

  - 删除无实际行为的 DebugRuntime/force_mode；
  - 将 controller switches 缩减为真正使用的系统 SEND 状态；
  - 删除 gimbal/body/approach 历史开关和固定 `NO_MISSION`/空列表方法；
  - 合并 `dispatch/types.py`、`dispatch/normalizer.py` 等过细文件，但不改变安全策略；
  - 更新配置、Web status、测试和 `app/README.md`。

  验收：不存在退役字段引用；SEND 关闭仍会停止连续命令并清理导航队列；全量测试通过。

  完成记录（2026-08-13）：
  - 主要变更：运行时开关缩减为唯一 SystemSendState。
  - 删除/迁移：删除 DebugRuntime、ActionRuntimeDebugConfig、ControlRuntimeSwitches 和固定 stage 状态表面；状态适配器迁出 app。
  - 安全边界：SEND 关闭继续触发 continuous stop、授权撤销和导航队列清理。
  - 定向测试：Web status、startup 和 SEND safety 回归通过。
  - 全量测试：1536 passed, 1 skipped。
  - 后续任务前置状态：ready。

---

### Milestone B：建立稳定契约和单一状态源

- [x] **AR-03：建立纯 `contracts/` 包和依赖守卫**

  前置：AR-02。

  目标：建立不依赖 FastAPI、pymavlink、OpenCV、RKNNLite、app 的共享类型层。

  首批契约：

  - `VehicleSnapshot`、`GimbalSnapshot`；
  - `PerceptionSnapshot`、`SceneSnapshot`；
  - `FusedSnapshot`、`RuntimeSnapshot`；
  - `ActionResult` 基础协议；
  - `VehicleStatePort`、`VehicleCommandPort` Protocol；
  - effect 基类/联合类型的最小骨架。

  实现要求：只建立后续任务立即需要的类型，不创建大量空抽象；加入导入方向测试。

  验收：`contracts/` 只有标准库依赖；类型可 JSON 序列化；现有行为不变；全量测试通过。

  完成记录（2026-08-13）：
  - 主要变更：建立 state/action/effect/perception/field/ports 纯契约与 JSON 序列化测试。
  - 删除/迁移：ActionResult 契约迁入 contracts。
  - 安全边界：加入标准库-only 静态导入守卫。
  - 定向测试：contracts tests 通过。
  - 全量测试：1536 passed, 1 skipped。
  - 后续任务前置状态：ready。

- [x] **AR-04：引入 `ApplicationStateStore`，迁移实时状态快照**

  前置：AR-03。

  目标：替代 `SystemRunner.latest_snapshot` 和零散锁，建立单一线程安全实时状态源。

  状态范围：vehicle、gimbal、perception、scene、fused、link、field summary、更新时间和序号。

  实现要求：

  - 状态写入只发生在应用主循环/明确服务；
  - 读取返回不可变或防御性复制 snapshot；
  - Web status、Action context、Field 采样都从 store 读取；
  - 不在 store 内计算控制或业务逻辑；
  - 加入并发读写、旧快照不被后续修改污染的测试。

  验收：`latest_snapshot` 从 SystemRunner 移除；原 API payload 保持兼容；全量测试通过。

  完成记录（2026-08-13）：
  - 主要变更：引入带序号/更新时间的线程安全 ApplicationStateStore。
  - 删除/迁移：移除 runner.latest_snapshot，Web、Action context 和 Field 采样统一读 store。
  - 安全边界：读取使用深复制，旧快照不受后续写入污染。
  - 定向测试：并发读写和防御性复制测试通过。
  - 全量测试：1536 passed, 1 skipped。
  - 后续任务前置状态：ready。

- [x] **AR-05：引入 `ResultService`，迁移任务结果状态**

  前置：AR-04。

  目标：迁移 SystemRunner 中 localization/drop/recon/workflow 的 `latest_*` 字段和约
  500 行结果整理逻辑。

  实现要求：

  - 明确定义定位结果、投放目标、投放工作流、侦察目标、报告和排名模型；
  - ResultService 接收 Action/Mission 结果并更新结果 snapshot；
  - Web 只读 ResultService；
  - 不包含飞行命令或 Mission 生命周期；
  - 保持现有 Web JSON 字段，必要时在 Web adapter 做一次兼容序列化。

  验收：SystemRunner 不再持有上述 `latest_*` 字段；现有结果页面和测试通过。

  完成记录（2026-08-13）：
  - 主要变更：引入线程安全 ResultService/ResultSnapshot，集中整理定位、投放工作流和侦察结果。
  - 删除/迁移：约 480 行结果处理从 Runner 迁出，Runner 不再存放 latest_* 结果字段。
  - 安全边界：ResultService 只接收结果与生成防御性副本，不包含飞行发送或 Mission 生命周期。
  - 定向测试：结果、定位、投放工作流及 Mission 编排 74 项通过。
  - 全量测试：1536 passed, 1 skipped。
  - 后续任务前置状态：ready。

---

### Milestone C：拆解 SystemRunner，使 app 回归组装层

- [x] **AR-06：提取 `SystemControlService`**

  前置：AR-05。

  迁移职责：系统 SEND、telemetry source 切换、重连、YOLO target 命令、录像控制、外部服务
  重启和相关审计事件。

  要求：

  - SEND/source/reconnect/restart 必须撤销授权并清理连续命令；
  - 录像路径统一进入 `runtime/videos/`；
  - app restart 不能自动重新打开 SEND；
  - 服务不依赖 FastAPI，也不实现 Action/Mission。

  验收：Web 管理 API 通过该 service；SystemRunner 不再实现这些方法；安全回归测试通过。

  完成记录（2026-08-13）：
  - 主要变更：SEND、切源、重连、YOLO 指令、录像和进程重启集中到 SystemControlService。
  - 删除/迁移：Web 管理端点直接依赖 service，Runner 的系统控制门面已移除。
  - 安全边界：SEND 关闭、切源、重连和重启均撤销授权、停止连续命令并清队列；录像位于 runtime/videos。
  - 定向测试：Web 安全/状态/模板 25 项通过。
  - 全量测试：1536 passed, 1 skipped。
  - 后续任务前置状态：ready。

- [x] **AR-07：提取 `MissionApplicationService`**

  前置：AR-06。

  迁移职责：Action Lab start/tick/stop/reset、Action Mission configure/start/tick/stop/reset/skip、
  run authorization、Field preflight、任务录像生命周期。

  要求：

  - 保持 ActionRuntimeService 和 MissionOrchestrator 当前语义；
  - Web 不再直接调用 SystemRunner 的 Mission 方法；
  - Mission 结束/失败/停止都撤销授权、停止连续命令并清理队列；
  - 不移动或改写 Action 算法。

  验收：SystemRunner 不再持有 Mission API 门面；所有 Mission/Action 当前测试通过。

  完成记录（2026-08-13）：
  - 主要变更：Action Lab、Action Mission、run authorization、Field preflight 和任务录像生命周期迁入 MissionApplicationService。
  - 删除/迁移：Web Mission/Action 端点直接依赖 service；Runner 仅保留旧测试所需的动态兼容转发。
  - 安全边界：停止、重置、失败和完成继续撤销授权，并由原 runtime/dispatcher 清理连续命令。
  - 定向测试：Mission、manual-step、Web 安全及投放工作流 57 项通过。
  - 全量测试：1536 passed, 1 skipped。
  - 后续任务前置状态：ready。

- [x] **AR-08：建立 `application/runner.py` 和薄 `app/bootstrap.py`**

  前置：AR-07。

  目标：`app/` 只保留配置、组装、入口；主循环迁入 application runner。

  Runner 只负责：读取服务状态、更新 fusion、写 StateStore、驱动 Mission tick、记录 blackbox。

  验收：

  - 旧 `app/system_runner.py` 删除，主循环由 `application/runner.py` 持有；
  - `app/` 目标不超过约 5 个 Python 文件；
  - 任一 app 文件不包含控制公式、Mission 状态机或 pymavlink；
  - `python -m app.main --help` 和 1 秒 no-UI/no-telemetry smoke test 通过。

  完成记录（2026-08-13）：
  - 主要变更：主循环和运行协调迁入 application/runner.py，ServiceManager/UDP adapter 迁入 app/bootstrap.py。
  - 删除/迁移：删除 app/system_runner.py 与 app/service_manager.py；app/ 仅保留入口、配置、bootstrap 和包文件。
  - 安全边界：Runner 主循环只采集状态、融合、写 store、驱动 Mission tick 与记录 blackbox，无 pymavlink/发送公式。
  - 环境验证：`app.main --help` 和 1 秒 no-UI/no-telemetry/SEND-false smoke 通过。
  - 全量测试：1536 passed, 1 skipped。
  - 后续任务前置状态：ready。

---

### Milestone D：统一执行层

- [x] **AR-09：机械迁移 execution 模块，消除 `app -> execution` 职责混杂**

  前置：AR-08。

  迁移当前 dispatcher、authorization、safety config/pipeline 和 handlers 到 `execution/`。

  要求：先做机械移动，不同时改 effect schema；直接更新正式 imports，不保留长期 import shim；
  更新文档、脚本和测试。

  验收：`app/` 不再拥有 dispatch/safety/authorization；现有派发与安全测试逐项通过。

  完成记录（2026-08-13）：
  - 主要变更：dispatcher、authorization、policy、safety 和 handlers 机械迁入 execution/。
  - 删除/迁移：删除 app/dispatch 与 app 内 execution 实现，不保留 import shim。
  - 安全边界：策略和 SafetyDecision 行为保持不变。
  - 定向测试：execution/dispatch/safety 60 项通过。
  - 全量测试：1536 passed, 1 skipped；P0 validator 通过。
  - 后续任务前置状态：ready。

- [x] **AR-10：将字典 action request 迁移为 typed effects**

  前置：AR-09。

  目标 effect：SetMode、Arm、Takeoff、Land、ConditionYaw、ChangeSpeed、LocalGoto、GlobalGoto、
  BodyVelocity、SetServo、ClearMotion、VisionTargetCommand。

  实现顺序：先一次性命令，再位置命令，再连续命令和 payload。每类 effect 必须有独立小提交
  级 diff/测试，即使在同一任务会话也不可混成一次无边界重写。

  要求：

  - ActionResult.effects 使用明确联合类型；
  - Dispatcher 不再从任意 dict 猜测 action_type/params；
  - SafetyDecision 仍记录 original/effective/rejected；
  - 安全策略保持独立 allowlist，不能信任 Action 自报 capability；
  - 移除迁移完成后的旧字段 fallback。

  验收：不存在生产路径 `dict[str, object]` action request；所有派发、安全、payload、连续命令
  测试和 P0 SITL 校验通过。

  完成记录（2026-08-13）：
  - 主要变更：ActionResult.effects 限定为 typed Effect 联合类型，Action 端通过显式 typed 边界构造。
  - 删除/迁移：删除 ActionResult actions 构造 fallback 和 Dispatcher.dispatch_actions 字典兼容入口。
  - 安全边界：SafetyDecision 继续记录 original/effective/rejected，独立 policy allowlist 未改变。
  - 定向测试：execution、payload、manual-step 和 typed contracts 98 项通过。
  - SITL 验证：本机 ArduCopter P0 低速正式链通过，完成 arm/takeoff/yaw/speed/goto/continuous stop/land。
  - 全量测试：1536 passed, 1 skipped。
  - 后续任务前置状态：ready。

---

### Milestone E：Field Reference 单一化

- [x] **AR-11：机械迁移 Field 模块到 `field/`**

  前置：AR-08；可与 AR-09 顺序执行，但一次会话只能做一个。

  迁移 profile、reference、coordinates、sampling、geometry、service 代码；不先改变算法。
  Actions 不得再通过 `app.*` 导入坐标或 Field 类型。

  验收：不存在 `missions -> app` Field import；坐标、采样、geometry、controller 测试全部通过。

  完成记录（2026-08-13）：
  - 主要变更：坐标、profile、reference、calibration、geometry、service 全部迁入 field/。
  - 删除/迁移：Actions 和测试改用 field 正式 imports，无 missions→app Field 依赖。
  - 安全边界：算法未调参，schema-v3 行为保持。
  - 定向测试：Field/P2 测试和 validator 通过。
  - 全量测试：1536 passed, 1 skipped。
  - 后续任务前置状态：ready。

- [x] **AR-12：确认并归档非正式 v1 Mission 模板**

  前置：AR-11。

  这是删除检查点。执行前先向用户报告：各 v1/无后缀模板是否被 Web catalog、部署文档、SITL、
  脚本或比赛流程使用。未经用户确认，不删除模板。

  用户确认后：

  - 正式 catalog 只保留 `rescue_2026_full_auto.json`、`drop_two_targets.json`、
    `recon_gps.json`；
  - 历史参考模板已删除，不参与正式 validator；
  - 更新 README、Web catalog 和测试；
  - 不把归档模板继续作为 runtime compatibility 的理由。

  验收：正式模板清单单一明确；validator 只验证正式模板；归档状态有文档说明。

  完成记录（2026-08-13）：
  - 主要变更：正式 catalog/validator 只保留三个 v2 模板。
  - 删除：五个历史模板及 SITL 副本已从仓库移除。
  - 安全边界：不保留 runtime compatibility。
  - 定向测试：模板 catalog、归档和 validator 测试通过。
  - 全量测试：1536 passed, 1 skipped。
  - 后续任务前置状态：ready。

- [x] **AR-13：合并 Field Controller/Service/Binding Orchestrator**

  前置：AR-12。

  目标：建立一个 `FieldService` 和一个内部 `CalibrationSession`，删除多份 Field 状态同步。

  保留：GPS 质量门槛、采样状态机、preview/finalize、原子 apply、失败 rollback、确认、自动 freeze、
  geometry 生成、状态快照。

  删除：RuntimeContextBuilder 中 Field 状态副本、`confirm_field_reference` 桥、service/builder 双写、
  重复 synced 比较。

  验收：FieldReference 只有一个状态源；事务失败不产生半应用状态；P2 和全部 Field 测试通过。

  完成记录（2026-08-13）：
  - 主要变更：公开入口合并为 `FieldService`，内部采样生命周期改为 `CalibrationSession`。
  - 删除/迁移：删除 Controller/Service/Binding Orchestrator 公共类、builder 坐标副本和双写同步比较。
  - 安全边界：FieldService 与 RuntimeContextBuilder 持有同一个 FieldReference；metadata 失败整笔 rollback。
  - 定向测试：单一对象身份、成功 freeze、失败 rollback 与 schema-v3 往返通过。
  - 全量测试：1096 passed, 1 skipped；P2 validator 通过。
  - 后续任务前置状态：ready。

- [x] **AR-14：退役 LOCAL_NED Field Reference 兼容路径**

  前置：AR-13。

  正式 schema v3 只支持 FIELD -> GPS/GLOBAL。删除 legacy centerline、LOCAL_NED origin、
  `field_transform_ready()` 兼容别名和相关 fallback；BODY_NED 不受影响。

  验收：正式模板全部使用 GPS/GLOBAL Field 目标；无 legacy Local Field 分支；坐标符号和任务
  preflight 测试通过。

  完成记录（2026-08-13）：
  - 主要变更：FieldReference 与坐标模块只保留 FIELD↔GPS/GLOBAL schema-v3 转换。
  - 删除/迁移：删除 local origin 字段、FIELD↔LOCAL_NED 函数、local target fallback 与 UI 状态字段。
  - 安全边界：Safety Pipeline 明确拒绝 FIELD local target；普通 LOCAL_NED 与 BODY_NED 能力保持不变。
  - 定向测试：全球坐标符号/往返、preflight 和 local-target fail-closed 覆盖通过。
  - 全量测试：1096 passed, 1 skipped；P2、架构和模板 validator 通过。
  - 后续任务前置状态：ready。

---

### Milestone F：简化 Action 和 Mission

- [x] **AR-15：统一 ActionDefinition，删除手工 Action Lab 重复清单**

  前置：AR-10。

  单一 ActionDefinition 同时提供 name、factory、参数 schema、默认值、label、description。
  Registry、Action Lab、Mission validator、API schema 和文档生成共用该定义。

  安全权限不放入 ActionDefinition，继续由 execution policy 独立审计。

  验收：删除 700+ 行手工 `action_lab_specs()`；注册表和默认参数不存在双份维护；模板校验通过。

  完成记录（2026-08-13）：
  - 主要变更：一个不可变 ActionDefinition catalog 同时生成 registry、Web specs 与参数 schema。
  - 删除/迁移：删除 700+ 行独立 Action Lab metadata 清单及手工顺序断言。
  - 安全边界：Action 权限仍只由 execution policy 管理，未混入定义 catalog。
  - 定向测试：definition/registry/Web 一致性和 JSON schema contract 通过。
  - 全量测试：1096 passed, 1 skipped；Action Mission validator 通过。
  - 后续任务前置状态：ready。

- [x] **AR-16：提取大型 Action 中的纯 guidance/localization 算法**

  前置：AR-15。

  顺序：`align_descend`、target projection/fusion、visual landing、waypoint math、target selection。

  要求：

  - 纯算法不读全局状态、不发命令、不导入 Web/telemetry/app；
  - Action 只管理 start/update/stop/reset 和 effect 生成；
  - 每迁移一个算法先补 characterization tests，再移动；
  - 不在一个任务内重写算法参数或调参。

  验收：主要 Action 适配器原则上不超过约 200 行；算法测试与 Action 行为测试分离；全量通过。

  完成记录（2026-08-13）：
  - 主要变更：align/descend、capture projection、GPS/ENU fusion、waypoint math、target projection/localization/fusion 进入纯 `guidance/`。
  - 删除/迁移：`align_descend.py` 对外 Action 缩为 14 行；状态生命周期与算法测试分层。
  - 安全边界：guidance 不导入 Web、telemetry、application，不产生 effect 或发送命令。
  - 定向测试：align characterization 206 项与新 atomic capture/ranking tests 通过。
  - 全量测试：708 passed, 1 skipped（AR-24 审计前）。
  - 后续任务前置状态：ready。

- [x] **AR-17：用 Action Mission/subflow 替代复合 Action 内嵌状态机**

  前置：AR-16。

  首先为 Mission engine 设计最小控制节点：sequence、retry、branch、foreach、finally 或等价的
  受限机制。不得重新引入 stage 类或通用脚本语言。

  按顺序迁移：drop sequence、recon sequence、GPS drop/recon sequence、multi-view workflow。
  每次只迁移一个流程，并保留旧流程做短期行为对照；对照完成立即删除旧复合 Action，不保留
  永久双路径。

  验收：Mission 模板表达流程，Action 不再启动子 Action；失败恢复、stop/zero、速度恢复、
  payload 顺序和 SITL 流程测试通过。

  完成记录（2026-08-13）：
  - 主要变更：三个正式模板展开多视角、双投放、侦察航迹和视觉降落原子 subflow。
  - 删除：12 个复合 Action/共享状态机及其旧行为锁已从仓库移除，并从 registry、policy、UI 删除。
  - 安全边界：align→payload 仍触发 zero/queue clear；两个 payload 的 SERVO/PWM/顺序和恢复标签保持显式。
  - 定向测试：模板 validator、atomic subflow、payload、dispatch policy contract 通过。
  - 全量测试：708 passed, 1 skipped（AR-24 审计前）。
  - 后续任务前置状态：ready。

---

### Milestone G：适配器和 UI 边界整理

- [x] **AR-18：拆分 telemetry 读写端口并解除 fusion 具体依赖**

  前置：AR-03、AR-10。

  目标：应用只持有 VehicleStatePort；只有 execution 持有 VehicleCommandPort。缩小 LinkManager
  公开发送表面。fusion 改为只依赖 contracts snapshot。

  将 `fusion/debug_main.py` 和 telemetry 文本/curses 控制入口移到 `tools/`，标记为开发诊断，
  不得成为正式人工入口。

  验收：fusion 不导入 telemetry_link；非 execution 生产模块无法取得命令端口；telemetry 接收、
  stale、切源、队列和 sender 测试通过。

  完成记录（2026-08-13）：
  - 主要变更：增加 VehicleStateAdapter/VehicleCommandAdapter，应用只注入读端口，写端口由 Dispatcher 持有。
  - 删除/迁移：MAVLink frame ID 移入纯 contracts；诊断入口位于 tools/。
  - 安全边界：Command adapter 不暴露读取、切源或生命周期方法，queue cleanup 仍由 execution 负责。
  - 定向测试：telemetry receive/stale/source/queue/sender 与 stop-and-clear contract 通过。
  - 全量测试：1096 passed, 1 skipped；architecture validator 通过。
  - 后续任务前置状态：ready。

- [x] **AR-19：拆分 Web 后端 routers，移除对 God Object 的依赖**

  前置：AR-08。

  routers：auth、status、actions、missions、field、vision、config、services。Web 通过明确的
  application services/protocol 注入，不接收 SystemRunner。

  删除已退役的 commands execute/completions 和无效 mission catalog 表面；保持认证、CSRF、
  Origin、Host、审计和 typed API。

  验收：`web_ui/server.py` 只负责创建 app、middleware、static 和 router 挂载；Web 安全与 API
  集成测试通过。

  完成记录（2026-08-13）：
  - 主要变更：auth/status/actions/missions/field/vision/config/services routers 与 typed DTO 分离。
  - 删除/迁移：Web 只接收显式 `WebServices`，不再接收或 fallback 到 SystemRunner。
  - 安全边界：认证、CSRF、Host、Origin、rate limit 和审计中间件保持。
  - 定向测试：Web security/API integration 29 passed。
  - 全量测试：708 passed, 1 skipped（AR-24 审计前）。
  - 后续任务前置状态：ready。

- [x] **AR-20：拆分 Web 前端主文件并统一 API client**

  前置：AR-19。

  目标：`app.js` 只做启动；拆分 mission、status、control；将 `field_map.js` 拆为 model、render、
  interaction；所有请求只经过 `api_client.js`；删除注释掉的旧 endpoint 和重复 Action/Mission API。

  验收：JS 测试通过；浏览器功能清单逐项 smoke；无重复 fetch 包装器；缓存版本统一管理。

  完成记录（2026-08-13）：
  - 主要变更：app.js 仅启动；mission/status/control 独立；Field 拆为 model/render/interaction。
  - 删除/迁移：退役复合 Action 的 Action Lab 项和旧 field_map 单文件入口删除。
  - 安全边界：所有 HTTP 请求仅由 `api_client.js` 持有 fetch；静态模块缓存版本统一。
  - 定向测试：模块加载顺序、唯一 fetch owner、Field model/render contract 通过。
  - 全量测试：708 passed, 1 skipped（AR-24 审计前）。
  - 后续任务前置状态：ready。

- [x] **AR-21：版本化 YOLO/app UDP 协议**

  前置：AR-03。

  Envelope 至少包含 schema_version、sequence、captured_at_monotonic、published_at_monotonic、
  target、scene。app receiver 检查版本、乱序和 stale；命令协议也使用 typed message。

  录像统一写入 `runtime/videos/`。保持 RKNNLite、RK3588 NPU 和 FP16 模型路径不变。

  验收：协议 contract tests 同时覆盖 producer/consumer；旧无版本数据的迁移策略明确并最终删除；
  YOLO 不导入 telemetry/MAVLink。

  完成记录（2026-08-13）：
  - 主要变更：感知与命令 envelope 加入 schema_version、sequence 和 monotonic 时间戳。
  - 删除/迁移：无版本 UDP 数据不再接受；录像统一到 runtime/videos/。
  - 安全边界：consumer 拒绝未知版本、乱序和 stale；YOLO 仍不接触 telemetry/MAVLink。
  - 定向测试：producer/consumer、命令乱序和 scene envelope 测试通过。
  - 全量测试：1536 passed, 1 skipped。
  - 后续任务前置状态：ready。

---

### Milestone H：配置、部署、测试和最终删除

- [x] **AR-22：配置 schema 和 real/SITL profile 去重**

  前置：AR-08、AR-21。

  保留正式路径 `config/app.yaml`、`telemetry.yaml`、`yolo.yaml`、`safety.yaml`、
  `action_missions/`、`field_profiles/`。为每个配置建立严格 schema：未知字段报错、bool 不接受字符串。

  real/SITL profile 只保存差异或通过安全脚本生成，任何 profile 都不得把 SEND 改为 true。

  验收：配置加载/覆盖测试齐全；apply/save 脚本保持 SEND false；重复完整配置显著减少。

  完成记录（2026-08-13）：
  - 主要变更：app/telemetry/YOLO loader 严格拒绝未知字段与字符串 bool；profile 改为受审 delta renderer。
  - 删除/迁移：删除 real/SITL 四份 242 行重复完整 YAML；real delta 为空，SITL 只保留四项差异。
  - 安全边界：每个 profile 强制声明 executor.send_commands=false，renderer 拒绝其他值。
  - 定向测试：strict schema、CLI override、delta render 和 SEND guard 通过。
  - 全量测试：1096 passed, 1 skipped。
  - 后续任务前置状态：ready。

- [x] **AR-23：重组测试目录和架构 contract tests**

  前置：AR-22。

  目标目录：unit/domain、unit/mission、unit/execution、unit/adapters、contracts、integration、sitl。
  移动测试时不通过删除失败测试获得全绿；实现耦合断言改为公开契约断言。

  必须保留安全不变量测试：双门控、stale/source、continuous deadman、stop/zero、队列清理、Field
  freeze、payload whitelist、YOLO timeout。

  验收：无 legacy 测试目录；主线和 SITL 标记清晰；CI 同时覆盖 linux-64 和 linux-aarch64 可用部分。

  完成记录（2026-08-13）：
  - 主要变更：测试重组为 unit/domain、unit/mission、unit/execution、unit/adapters、contracts、integration、sitl。
  - 删除/迁移：删除 `tests/current` 分类；退役实现测试归档，等价安全/schema-v3 公共契约测试补齐。
  - 安全边界：双门控、stale/source、deadman、stop/zero、queue cleanup、freeze、payload whitelist、YOLO timeout 全保留。
  - 定向测试：CI linux-64/aarch64 路径与独立 SITL 目录更新。
  - 全量测试：1098 passed, 1 skipped。
  - 后续任务前置状态：ready。

- [x] **AR-24：部署、文档和最终架构审计**

  前置：AR-01 至 AR-23 全部完成。

  更新 systemd、安装脚本、healthcheck、README、开发者文档、AI 文档和命令审计。删除迁移期 import
  shim、deprecated fallback、旧配置键、旧模板引用和空目录。

  最终验收：

  - `app/` 约 4 至 5 个 Python 文件，只负责入口、配置和组装；
  - 无 app/missions、app/web_ui 循环依赖；
  - pymavlink 只在 telemetry_link；
  - 所有飞行/payload 请求经过 Execution + Authorization + SEND + Safety；
  - FieldReference 只有一个状态源；
  - Action 不包含嵌套 Mission 状态机；
  - 正式 Mission catalog 只有明确支持的模板；
  - YOLO 协议有版本、序号和时间戳；
  - `executor.send_commands: false`；
  - compileall、全量 pytest、所有 validator、环境 smoke、SITL 低速正式安全链验证全部通过；
  - 真机前检查仍需人工完成，重构任务不得自动进行实飞。

  完成记录（2026-08-13）：
  - 主要变更：systemd dry-run、healthcheck、profile renderer、README/开发者/AI 架构文档与最终 contract 审计统一。
  - 删除/迁移：删除 Runner/Web DTO 迁移转发、旧 `/api/missions` stage catalog、复合 Action 结果 fallback、旧配置/模板引用和空目录。
  - 安全边界：`app/` 仅 4 个入口/组装文件；pymavlink 仅属于 telemetry_link；三个正式 profile/template 均保持 SEND=false 和统一 Execution 链。
  - 定向测试：architecture/action-mission/Field validators、compileall、app healthcheck、systemd dry-run、real/SITL profile 渲染均通过；ArduCopter SITL 低速正式安全链通过。
  - 全量测试：Linux x86_64 与 RK3588 Linux aarch64 均为 708 passed；RKNNLite 2.3.2 成功加载 `cuadc2026-fp16.rknn`。
  - 后续任务前置状态：ready；真机前检查与实飞仍必须由人工单独授权。

---

### Milestone I：平台适配层接口稳定化

- [ ] **AR-25：平台适配层 Port/DTO、生命周期和版本化接口重构**

  前置：AR-01 至 AR-24 全部完成。

  目标：稳定 Web API、telemetry/MAVLink、YOLO UDP、Field 和 observability 五类平台边界，使普通
  任务变化只修改 Action、ActionDefinition 与 Mission；新增真正的平台/Effect capability 时另需更新
  Execution capability policy 及安全测试，但不修改无关 adapter。硬件、wire 或存储变化只修改对应
  adapter。

  详细子任务、目标接口、迁移顺序、回滚门禁和验收标准见
  [平台适配层接口重构执行计划](platform_adapter_interface_refactor_plan.md)。每个会话只执行一个
  `PA-xx`；只有 `PA-00` 至 `PA-31` 全部验收完成后才可勾选本项。

  `PA-21` 的 RunCoordinator/scheduler 实现已拆为 `AR-26` 的唯一 canonical 核心任务。实际跨计划顺序
  固定为 `PA-00～PA-20 → CF-00～CF-28 → PA-21～PA-31`；`PA-21` 只做冻结核心与 PA Port 的
  conformance 检查，不得修改核心或另建第二套 Coordinator；正式 Application DTO/Port 从 PA-22 开始。
  AR 编号只用于归档，具体前后关系以子任务依赖为准。

  永久限制：不恢复旧 mission/stage/control 栈；不扩大 SEND、run authorization、payload 或连续
  控制能力；新旧 MAVLink sender、Field writer、blackbox writer 和 Web use case 不得双写/双实现。

---

### Milestone J：稳定核心层冻结

- [ ] **AR-26：Action/Mission/Effect/Run 稳定核心层重构与 v1 冻结**

  前置：`AR-01～AR-24` 已完成，且 `AR-25` 的 `PA-00～PA-20` 已逐项验收；不要求整个 AR-25 先完成。

  目标：冻结 Action 契约与运行器、Mission 编排器、统一调度与状态快照、Effect 派发与安全系统、Run
  生命周期五块核心。完成后，普通任务迭代原则上只修改 Action、ActionDefinition、Mission 模板和测试；
  核心只在新增 Effect/平台能力、破坏性 schema 或安全边界变化时按 ADR/版本化流程修改。

  详细接口、职责边界、工作流、分会话任务、回滚门禁和验收标准见
  [稳定核心层冻结与改造执行计划](stable_core_refactor_plan.md)。每个会话只执行一个 `CF-xx`；只有
  `CF-00` 至 `CF-28` 全部验收完成后才可勾选本项。

  与 AR-25 的边界：直接复用 PA 已冻结的 Vehicle/Perception/Field snapshots、Command/Cancel/Event Ports；
  不复制 MAVLink、YOLO、Field、日志或 Web adapter。`CF-28` 完成后回到 `PA-21` 做冻结核心/PA Port
  conformance 检查，再由 `PA-22` 建立正式 Application facade 并继续 `PA-23～PA-31`。

  永久限制：一个 active top-level run、一个 scheduler、一个 typed snapshot publication、一个核心
  EffectDispatcher submit call site、一个核心 cancel policy/request producer、一个 CoreCycleDriver
  ExecutionCancelPort call site，以及一个 PA broker/wire 与 STOP barrier 执行 owner；不恢复旧栈，不扩大
  SEND/payload/continuous 权限，不自动实飞。

## 7. 单任务完成记录格式

执行某个任务后，在该任务下追加：

```text
完成记录（YYYY-MM-DD）：
- 主要变更：...
- 删除/迁移：...
- 安全边界：...
- 定向测试：...
- 全量测试：...
- 后续任务前置状态：ready / blocked（原因）
```

若任务未完整实现，不得勾选完成。应报告剩余项，并保持测试可运行、SEND 默认关闭，不得用兼容
空壳或跳过测试掩盖未完成状态。
