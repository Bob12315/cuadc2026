# 开源、跨平台与安全主线分阶段改造计划

## 1. 文档状态

| 项目 | 内容 |
| --- | --- |
| 状态 | P0、P1、P2 已完成；P3 阻塞于发布权利与维护者决策 |
| 适用仓库 | `cuadc2026`（早期名称 `uav_system-rk3588`） |
| 当前正式主线 | Action Mission + Web UI |
| 计划目标平台 | Linux x86_64、Linux ARM64 |
| YOLO 目标平台 | Linux ARM64 RK3588 + RKNNLite/NPU |
| 文档性质 | 实施任务书、阶段门禁和验收依据 |

本文记录已经讨论确认的改造方向，并把改造拆成可独立评审、验证和回滚的阶段。
本文创建本身不授权执行其中任何代码、配置、依赖、Git 历史或发布操作。

P1 的代码、文档、CI 和 RK3588 实机门禁已完成：通用 App 支持 Linux x86_64/ARM64，
RK3588 YOLO 保持专用；Action Dry Run 已在 P0-3 删除。P2 已完成 schema v2 Field
Profile、centerline binding 与旧 Mission/Stage 入口的退役。
本文中的目标状态不能被当成已经实现的能力。

实施过程中使用以下约束词：

- **必须**：不满足就不能合并或进入下一阶段；
- **禁止**：任何阶段都不能引入；
- **应当**：默认必须执行，只有书面记录理由后才能例外；
- **可以**：不影响本阶段验收的可选项。

如果本文与当前运行事实冲突，在冲突被专门评审并更新前，以
[`current_architecture.md`](../architecture/current_architecture.md)、
[`action_contracts.md`](../architecture/action_contracts.md)、
[`deprecated_paths.md`](../architecture/deprecated_paths.md)、
[`docs/developer/safety.md`](../../developer/safety.md) 和实际代码为准。

---

## 2. 已确认且不可在实施中自行变更的决策

### 2.1 架构与任务主线

1. Action Mission 是唯一当前任务主线。
2. Web UI 是唯一正式人工操作入口。
3. 目标正式发送链确定为：

   ```text
   Action output
     → ActionRuntimeService
     → ActionDispatcher
     → Action-compatible safety pipeline
     → LinkManager
     → telemetry_link / MAVLink
   ```

4. 禁止恢复或重新增加以下旧栈：

   ```text
   MissionRunner
   StageRegistry
   FlightCommand
   CommandShaper
   FlightCommandExecutor
   missions/<mission>/mission.py
   missions/<mission>/stages/<stage>
   missions.common.control
   ```

5. Action 禁止直接调用 pymavlink 或 `LinkManager`。
6. YOLO 禁止连接 MAVLink、调用 `LinkManager` 或生成飞行命令。

### 2.2 平台范围

1. App 正式支持范围扩展为 Linux x86_64 和 Linux ARM64。
2. 不支持 Windows 和 macOS；不能在文档、CI 或安装器中声称支持。
3. 非视觉部分不得依赖 RK3588、RKNNLite 或特定 NPU。
4. YOLO 保持 Linux ARM64 RK3588 专用：

   - 使用 RKNNLite；
   - 使用 `.rknn` 模型；
   - 项目规范实机模型为 `data/models/cuadc2026-fp16.rknn`；当前根配置和 real profile
     实际选择 `data/models/cuadc-fp16.rknn`，存在配置与规范偏差，部署前需要核对模型
     来源和检测/标定验证，不在本计划以外自动替换模型；
   - 禁止增加 x86 YOLO、CUDA、PyTorch 或通用 GPU 推理路径；
   - 未安装 YOLO 环境时，App 必须能够以显式“无本地视觉进程”模式启动。

### 2.3 环境和依赖

1. 使用 Conda 统一环境入口和 Python 版本约束。
2. App 与 RK3588 YOLO 使用两个独立环境。
3. 依赖分层只是安装清单和导入边界治理，不是框架重写，也不是微服务改造。
4. App 的公共高层环境定义应同时用于 Linux x86_64 和 Linux ARM64；平台锁定结果可以分开。

### 2.4 实发安全

1. `executor.send_commands: false` 必须继续作为仓库默认值。
2. 必须保留系统 SEND 与本次 Action/Mission 实发授权的双门控。
3. 删除用户可选择的 Action Dry Run 功能，但不删除双门控。
4. SITL 测试使用真实 Action 派发链向 SITL 发送命令，不使用“运行但跳过派发”的伪预演模式。
5. 连续 BODY_NED 命令停止时必须显式发送 zero/stop，并清理过期命令。
6. 投放唯一允许路径保持为：

   ```text
   payload_release Action → set_servo → MAV_CMD_DO_SET_SERVO
   ```

### 2.5 Field Profile

1. `config/field_profiles/` 目录不能整体删除。
2. 必须保留当前正式模板：

   ```text
   config/field_profiles/competition_runtime.json
   ```

3. schema v2 中心线/多点方案计划完整退役，但必须先完成 SITL 和所有运行路径迁移。
4. 禁止只删除 JSON 而保留失效的 API、UI、解析、测试或文档。

### 2.6 开源许可证

1. 项目代码计划使用 Apache License 2.0。
2. 代码许可证不能自动覆盖模型、数据集、图片、视频、地图、地形、Logo 或第三方素材。
3. 权属不明确的文件不得随公开版本发布。

---

## 3. 明确不在本计划中的目标

以下项目不属于本轮改造，不能借本计划扩大范围：

- 不把项目改造成通用飞控或替代 ArduPilot；
- 不增加 Windows/macOS 支持；
- 不增加 x86/CUDA/PyTorch YOLO；
- 不更换 RKNN 模型默认精度，不把已废弃 INT8 模型恢复为默认；
- 不恢复 terminal/curses 正式操作入口；
- 不重写 Action Mission 为旧 Mission/Stage 架构；
- 不改变 FIELD、LOCAL_NED、BODY_NED 的坐标定义；
- 不改变载荷释放为 RC override；
- 不在没有校准和 SITL/实飞证据时自行提高速度、下降率、高度或舵机范围；
- 不用删除失败测试、扩大忽略列表或降低断言来代替修复；
- 不在本计划文档阶段执行 Git 历史重写、删除模型或发布仓库。

---

## 4. 目标平台和运行组合

| 能力 | Linux x86_64 | Linux ARM64 非 RK3588 | Linux ARM64 RK3588 |
| --- | --- | --- | --- |
| core：Action/坐标/fusion/配置 | 必须支持 | 必须支持 | 必须支持 |
| telemetry：pymavlink | 必须支持 | 必须支持 | 必须支持 |
| Web：FastAPI/uvicorn | 必须支持 | 必须支持 | 必须支持 |
| ArduPilot/Gazebo SITL | 主验证平台 | 视上游软件支持情况 | 可以连接本机或外部 SITL |
| App 接收现有 UDP 感知协议 | 必须支持 | 必须支持 | 必须支持 |
| 本地 RKNN YOLO | 明确不支持 | 明确不支持 | 必须支持 |

“App 支持某架构”的最低含义是：环境可安装、模块可导入、配置可解析、Web 可启动、
telemetry 可连接或显式关闭、Action 纯逻辑和协议测试可运行。它不自动代表该机器能运行
RKNN YOLO，也不代表所有第三方 SITL 软件都已为该架构提供二进制包。

---

## 5. 不得在任何阶段破坏的安全不变量

以下条件适用于所有提交和所有阶段：

1. 仓库中的 `executor.send_commands` 默认值严格为 `false`。
2. 连接 telemetry 不等于允许实发。
3. 系统 SEND 在切换 telemetry source、重启服务、载入配置、Action/Mission 结束或异常恢复时，
   不得被隐式打开。
4. Action/Mission 的实发授权必须绑定本次 run，不能作为可能遗留到下一次运行的长期真值。
5. Web 页面、加载模板、Field Reference 采样、状态查询和配置查看不得产生飞行动作。
6. 未确认且未冻结 Field Reference 时，必须拒绝需要 FIELD 派生航点的实发。
7. telemetry stale、断线、目标丢失、Action stop/reset/fail、Action 切换时，连续控制不得沿用旧值。
8. BODY_NED 连续控制结束必须有显式 stop/zero；队列中的 stale 连续命令和 pending navigation
   必须按已评审顺序清理。
9. 舵机只允许使用已配置的 SERVO 输出通道和经过审查的 PWM 范围。
10. 所有拒绝、截断、停止、授权和发送结果必须能够被审计。
11. `yolo_app/` 不得导入或调用飞行发送路径。
12. 所有运行产物必须进入 `runtime/`，不得写入 `config/` 或提交到 Git。

---

## 6. 阶段依赖与合并规则

```text
P0-0 基线固化
  ├─→ P0-1 Web 控制边界收口
  └─→ P0-2 Action Safety Pipeline
          └─→ P0-3 删除 Action Dry Run / 重构本次运行授权
                  └─→ P0-4 P0 SITL 安全验收

P0 完成
  └─→ P1-1 依赖分层
          └─→ P1-2 Conda 与安装器跨架构
                  └─→ P1-3 Linux x86_64/ARM64 CI
                          └─→ P1-4 RK3588 YOLO 隔离验收

P1 完成
  └─→ P2-1 schema v2/SITL 迁移
          └─→ P2-2 schema v2 原子退役
                  └─→ P2-3 旧架构与兼容残留清理

P0～P2 完成
  └─→ P3-1 法律与社区文件
          └─→ P3-2 素材、模型、秘密和历史审计
                  └─→ P3-3 发布候选验收
```

合并规则：

- 每个编号任务应独立 PR；高风险任务不得与格式化或无关重命名混合；
- P0 中影响发送、停止、限幅、队列或授权的 PR 必须单独评审；
- 一个阶段只有在其验收项全部完成并留下验证证据后才能标记完成；
- 不允许为赶进度跳过前置阶段；
- 任何实机验证都必须在 SITL 对应场景通过后进行；
- 文档、配置、测试和实现必须在同一功能 PR 中保持一致。

---

# P0：飞行控制与公开 Web 入口安全

## P0-0：建立不可退化的行为基线

### 目标

在修改发送链前固定当前可观察行为、已知失败和安全不变量，避免后续把行为变化误认为清理。

### 任务

- [x] 记录 Python、Node、配置解析和 Action Mission validator 的当前结果；
- [x] 为当前发送类型建立清单：请求类型、允许的 Action、是否连续、是否需要系统 SEND、
      是否需要本次运行授权、最终调用的 `LinkManager` 接口；
- [x] 建立所有人工和程序化命令入口清单，至少覆盖：
  - `/api/actions/*`；
  - Action Mission 启动/停止接口；
  - `/api/commands/execute`；
  - service restart；
  - telemetry source 切换；
  - 任何直接调用 `web_execute_command()` 或 `ui_commands` 的路径；
- [x] 为连续命令补齐当前 stop、clear、disconnect、stale、丢目标、Action 切换的特征测试；
- [x] 将已知失败分类为代码缺陷、环境缺失、测试漂移或已废弃路径，不删除测试掩盖问题；
- [x] 确认 `runtime/` 目录规则和测试临时目录不会污染仓库。

### 验收

- [x] 能从清单追踪每种飞行请求从 Action 到 `LinkManager` 的完整路径；
- [x] 所有可能改变飞行器或服务状态的 Web 入口均已列出；
- [x] 基线测试结果有可复查记录；
- [x] 本任务不改变运行行为。

### 回滚条件

本任务只允许增加测试、清单和记录。如果出现飞行行为变化，立即拆分并回滚行为修改。

---

## P0-1：收口 Web 控制面和人工权限边界

### 风险

P0-0 历史基线中 Web 默认监听 `0.0.0.0`，且通用 command API 能触发多种状态变化。
P0-1 已收口这些入口；以下说明原始风险和验收要求。公开仓库不能默认假设
局域网内所有访问者都可信，也不能允许通用命令入口绕过 Action 安全主线。

### 目标架构

1. 所有会改变飞行器、任务、发送门、telemetry source 或服务状态的 API 必须鉴权；
2. 浏览器状态修改请求必须具有防跨站请求保护；
3. 通用命令接口不能成为 Action/安全管线旁路；
4. 非回环监听必须是显式部署选择，并且必须配置认证。

### 任务

- [x] 默认 Web 监听调整为回环地址；如部署使用 `0.0.0.0`，启动时必须验证认证已配置；
- [x] 选择并实现适合局域网单操作者的认证模型；
- [x] 密钥、口令、会话密钥禁止写入受 Git 跟踪的 YAML，必须从环境变量或部署密钥文件加载；
- [x] 对所有修改状态的 API 强制认证、授权、CSRF 防护和结构化审计；
- [x] 设置明确的 allowed origins/hosts；不能以通配符替代部署配置；
- [x] 为登录、实发确认和高风险接口增加速率限制或等价防滥用控制；
- [x] 审计 `/api/commands/execute`：
  - 只读命令可以保留；
  - 飞行、舵机、解锁、起飞、降落和连续速度命令必须删除，或转换为正式 Action 请求；
  - 控制 SEND、切换 source、重启服务等管理命令必须使用独立、强类型、鉴权 API；
  - 禁止把自由文本直接映射为飞行命令；
- [x] 所有审计事件至少记录时间、操作者、来源地址、run ID、目标 source、操作类型、结果和原因；
- [x] 日志不得记录口令、token 或完整秘密。

### 实施前必须由维护者确定

- [x] 认证凭据由环境变量、独立权限文件还是外部反向代理提供；
- [x] 安全事件接收地址；
- [x] 是否允许多操作者，以及角色是否区分 observer/operator/admin。

在以上三项没有书面决策前，不得由实现者自行选择复杂身份系统并宣称完成。

### 测试

- [x] 未认证访问所有修改状态接口均失败；
- [x] 认证但无对应权限的访问失败；
- [x] CSRF/Origin 不合法的浏览器修改请求失败；
- [x] 通用 command API 无法触发 MAVLink、舵机、SEND/source 或 service restart；
- [x] 回环模式正常；非回环且无认证配置时启动失败；
- [x] WebSocket 的认证、断开和重连不会绕过权限；
- [x] 审计日志包含结果但不泄漏秘密。

### 验收

- [x] Web UI 仍是唯一正式人工入口；
- [x] 不存在绕过 ActionDispatcher/安全管线的 Web 飞行入口；
- [x] 非回环部署默认失败关闭，而不是无认证继续启动。

---

## P0-2：新增 Action-compatible Safety Pipeline

### 目标

在当前 Action 主线中建立统一、可测试、可审计的飞行请求安全裁决层，替代旧
`CommandShaper/FlightCommandExecutor` 曾经承担但当前已经缺失的安全职责。

该管线不是 Mission、Action、控制器或 MAVLink 发送器。它不得选择目标、规划任务、计算
视觉控制策略，也不得直接调用 pymavlink/`LinkManager`。

### 推荐模块边界

```text
ActionDispatcher
  ├─ DispatchPolicy / Action allowlist
  ├─ 双门控检查
  ├─ ActionRequestValidator
  ├─ SafetyEnvelope
  ├─ ContinuousCommandGuard
  ├─ TransitionGuard
  └─ SafetyDecision
        ↓ 仅允许结构化、安全化后的请求
      LinkManager
```

### 输入模型

每个待裁决请求必须能够携带或关联：

- `run_id`；
- Action/Mission 名称；
- request type；
- 目标 telemetry source（`sitl` 或 `real`）；
- monotonic 生成时间；
- frame 和单位；
- 原始 payload；
- 是否连续；
- 去重/优先级 key；
- 本次运行授权上下文。

禁止依赖裸 `x/y/z` 猜测坐标语义。兼容参数在归一化后必须转成明确 frame 和语义字段。

### SafetyDecision 输出

每次裁决必须返回可序列化结果：

```text
allowed | rejected | clamped | stop_emitted
reason_code
original_request
effective_request
run_id / action / source
evaluated_at_monotonic
```

任何 clamp 都必须显式记录；禁止静默修改参数。对无法安全推断的输入必须拒绝，而不是使用
隐式默认值。

### 必须覆盖的校验

#### 通用校验

- [x] 拒绝缺失、错误类型、bool 冒充数字、NaN、正负 Inf；
- [x] 拒绝未知 request type、未知 frame、未知单位和不允许的 Action/request 组合；
- [x] 检查 run 授权、目标 source 和当前 active source 一致；
- [x] 检查 telemetry connected、heartbeat/RX 新鲜度和 control allowed；
- [x] 检查请求时间戳和 TTL，拒绝过期请求；
- [x] 结构化 reason code 稳定且有测试。

#### BODY_NED/连续速度

- [x] 校验前后、左右、上下速度和偏航速率的有限值及安全包线；
- [x] 必须使用 monotonic time 实现 TTL/deadman；
- [x] watchdog 必须独立于 Action 是否继续产生 tick，不能因 Action 卡住而停止检查；
- [x] 超时后只发送受控的显式 zero/stop，并清除 stale 连续命令；
- [x] stop/reset/fail、目标丢失、telemetry stale、source 切换和 Action 切换必须触发停止路径；
- [x] 如果启用 slew/rate limit，必须基于真实 `dt`，不能假设固定循环周期；
- [x] 新的位置、land 或其他控制接管前，必须按已评审顺序终止旧连续控制。

#### LOCAL/GLOBAL/FIELD 航点

- [x] 校验坐标、经纬度、高度、距离和 frame；
- [x] FIELD 请求必须要求 Field Reference confirmed、synced、frozen 和相应转换能力 ready；
- [x] schema v3 只能提供其实际具备的 FIELD→GLOBAL 能力，禁止伪造 LOCAL origin；
- [x] 航点安全包线和地理围栏策略必须配置化并有来源说明；
- [x] 转换只使用 `field/coordinates.py` 或已裁决的唯一实现源。

#### mode/arm/takeoff/land/yaw/speed

- [x] 每种命令定义允许的 Action、前置状态、参数范围和重复发送策略；
- [x] 起飞高度、偏航速度和 change-speed 值必须受安全包线限制；
- [x] 不允许未知模式字符串直接传入飞控；
- [x] land 和紧急停止路径不得被普通优先级命令覆盖。

#### payload/set_servo

- [x] 只允许 `payload_release` 相关白名单 Action；
- [x] 只允许配置明确列出的 SERVO 输出通道；
- [x] PWM 必须在经过硬件空载验证的范围内；
- [x] 必须保持 once/幂等约束，避免重复投放；
- [x] 禁止 RC override、`release_payload` 和 Action 直连 MAVLink。

### 配置

建议新增独立 `config/safety.yaml`，集中保存经过审查的安全包线和 TTL。该文件必须：

- 带单位；
- 带适用 request type；
- 有保守默认值；
- 对缺失的关键限制 fail closed；
- 不包含现场秘密；
- 由 parser 和 schema 验证；
- 变更时必须运行安全测试。

### 实施前必须由维护者提供或确认

- [x] BODY_NED 各轴速度上限；
- [x] 下降率和偏航速率上限；
- [x] 连续命令 TTL/deadman 时间；
- [x] slew/rate limit；
- [x] 起飞高度、航点高度和单次航点距离范围；
- [x] change-speed 范围；
- [x] SERVO 通道、释放/保持 PWM 和允许范围；
- [x] real 与 SITL 是否共用限制，若不同则如何确保 real 更保守。

这些数值没有确认前，可以实现数据结构和拒绝逻辑，但不得把猜测值作为实机默认值。

### 测试

- [x] 所有 validator/envelope 使用纯单元测试覆盖边界值和非法值；
- [x] 使用可控 monotonic clock 测试 TTL，不使用真实 sleep；
- [x] 测试 stop 只按设计次数产生且 stale 命令不会重放；
- [x] 测试 source 切换、断线、重连、Action 切换、异常和目标丢失；
- [x] 测试 set_servo 非法通道/PWM、重复 key 和未授权行为；
- [x] 测试所有 Action policy 均有明确映射，不允许默认放行；
- [x] 黑匣子/审计能够区分 original、effective 和 rejected request。

### 验收

- [x] 当前所有会发送到 `LinkManager` 的 Action request 都经过同一个安全裁决入口；
- [x] 不存在旧 CommandShaper/FlightCommandExecutor 运行依赖；
- [x] 连续命令在 watchdog、异常和切换场景中均能显式停止并清理；
- [x] 任何拒绝或截断都有稳定、可查询原因；
- [x] SITL 通过前不进行实机验证。

### 回滚条件

出现下列任一情况必须停止实机推进并回滚到 `send_commands=false`：

- stop/zero 不能确定发送；
- stale 命令可能在重连后重放；
- 新管线存在可绕过入口；
- 参数单位/frame 不可追踪；
- 实际输出与 SafetyDecision 不一致；
- 测试需要依赖旧控制栈才能通过。

---

## P0-3：删除 Action Dry Run，改为本次运行显式授权

### 删除范围

本任务删除的是 Action Lab 中“Action 运行但派发被跳过”的 Dry Run 模式：

- [x] 删除 Web `Dry Run` 按钮；
- [x] 删除 `Dry-run only` 状态卡和相关文案；
- [x] 删除 `send_actions=false` 作为 Action 启动模式的 API 语义；
- [x] 删除 `dry_run_only` reason code，替换为明确的未授权/系统 SEND 关闭原因；
- [x] 删除或重写依赖 Dry Run 行为的测试；
- [x] 修改 `target_lock`、`align_descend` 等把算法行为错误描述为 Dry Run 的说明；
- [x] 删除“Action 正在运行但所有飞行请求都被静默跳过”的状态。

### 必须保留的机制

- [x] `executor.send_commands=false` 默认值；
- [x] 系统 SEND 门；
- [x] 本次 Action/Mission 实发授权门；
- [x] Dispatcher allowlist 和 Safety Pipeline；
- [x] Stop/Reset、显式 zero/stop 和 stale clear；
- [x] telemetry source 切换自动关闭系统 SEND；
- [x] SITL 和 real 的明确区分。

### 新授权语义

1. Web 启动 Action/Mission 时必须建立不可变的本次运行授权上下文；
2. 授权至少绑定 `run_id`、操作者、Action/Mission 名称、目标 source 和确认时间；
3. 授权不能自动继承到下一次 run；
4. 每次派发仍同时检查系统 SEND；系统 SEND 中途关闭后必须立即停止后续实发；
5. UI 确认框必须明确显示 `SITL` 或 `REAL`，不能只写 `vehicle/simulator`；
6. 对可能生成飞行/舵机命令的 Action，缺少本次授权时服务端必须拒绝启动；
7. 纯计算 Action 可以执行，但仍按普通 Action 命名和记录，不得重新包装成 Dry Run；
8. Action registry/policy 必须明确标记某 Action 是否可能产生飞行或舵机请求，禁止靠运行时猜测。

### 正式验证流程

```text
配置 active_source=sitl
  → 明确打开系统 SEND
  → 确认本次 SITL Action/Mission
  → 通过正式 Dispatcher + Safety Pipeline 向 SITL 发送
  → 验证完成并关闭 SEND
  → 切换 real（切换动作再次强制 SEND=OFF）
  → 完成实机前检查
  → 明确打开系统 SEND
  → 确认本次 REAL Action/Mission
```

### 同名但不属于 Action Dry Run 的功能

部署脚本的 `--dry-run` 只负责预览 systemd 文件，不产生飞行动作。保留该能力，并建议改名为
`--render-only`，同步更新脚本帮助、文档和测试。其他脚本中如存在同名参数，必须逐个按副作用
分类，禁止批量字符串删除。

### 测试和验收

- [x] Web 只提供一个明确的启动/派发入口，不再提供 Dry Run；
- [x] API 缺少本次授权时无法启动可能发送命令的 Action；
- [x] 旧 run 授权不能用于新 run；
- [x] `SITL` 和 `REAL` 的确认信息可区分；
- [x] 系统 SEND 关闭后，即使 run 已授权也不能发送；
- [x] source 切换后授权和系统 SEND 均不能误继承；
- [x] 纯计算 Action 仍可正常运行，不制造飞行副作用；
- [x] 不存在 `dry_run_only` 状态和文档残留，部署脚本的 render-only 语义除外。

---

## P0-4：P0 SITL 和失效场景验收

P0 完成前必须在正式 Action 发送链上验证：

- [x] 未认证、未授权、SEND OFF 三种情况分别被拒绝并有不同 reason；
- [x] 正常 takeoff、goto、change-speed、yaw、land；
- [x] BODY_NED 正常控制和超时 stop；
- [x] Action stop/reset/fail；
- [x] Action 切换和 Mission 阶段切换；
- [x] YOLO 超时/目标丢失；
- [x] telemetry heartbeat/RX stale；
- [x] SITL 断开和重连；
- [x] source 切换；
- [x] 非法 NaN/Inf、超限速度、错误 frame、错误 Field Reference；
- [x] set_servo 非法通道/PWM 和重复投放；
- [x] blackbox/审计能够还原授权、裁决、发送、停止和失败顺序。

P0 的完成定义：所有控制入口已收口，Safety Pipeline 已成为正式必经路径，Action Dry Run 已删除，
SITL 失效测试全部通过，仓库默认 SEND 仍为 OFF。

---

# P1：Linux x86_64/ARM64 App 与 RK3588 YOLO 隔离

## P1-1：依赖分层，不改框架

### 目标目录

```text
requirements/
├── core.txt
├── telemetry.txt
├── web.txt
├── app.txt
├── dev.txt
└── rk3588-yolo.txt
```

### 依赖职责

| 文件 | 内容边界 |
| --- | --- |
| `core.txt` | Action 纯逻辑、坐标、fusion、配置所需的 PyYAML、NumPy 等 |
| `telemetry.txt` | `core` + pymavlink |
| `web.txt` | `core` + FastAPI、uvicorn、websockets；运行时确有需要时才保留 httpx |
| `app.txt` | 聚合 core + telemetry + web，不包含 RKNNLite/OpenCV YOLO |
| `dev.txt` | app + pytest + lint/format/type 工具 |
| `rk3588-yolo.txt` | RKNNLite、OpenCV、NumPy、PyYAML，仅 RK3588 |

Node.js 不是 pip 依赖。Node 测试工具必须由开发 Conda 环境或独立 `package.json` 管理，不能写进
Python requirements 冒充可安装包。

### 任务

- [x] 建立上述分层文件；
- [x] 根目录旧 requirements 在迁移期只作为兼容聚合入口；仓库内 caller 全部切到
      `requirements/*.txt` 后已删除，不保留第二套入口或版本约束；
- [x] 检查每个依赖是否真正在运行时使用；测试专用依赖移入 dev；
- [x] 为直接依赖设置经过验证的兼容范围，避免无依据地锁死所有传递依赖；
- [x] 分别生成或维护 linux-64 和 linux-aarch64 的可复现锁定结果；
- [x] 许可证扫描覆盖所有直接和传递依赖。

### 导入边界

- [x] `core` 模块不得导入 FastAPI、uvicorn、pymavlink、cv2、rknnlite；
- [x] `web_ui` 可以依赖 App 的只读服务接口，不能成为 core 的反向依赖；
- [x] pymavlink 常量和 frame 应收敛在 `telemetry_link` 边界；
- [x] RKNNLite 和 cv2 导入保持在 `yolo_app/`；
- [x] App 启动和导入不能因未安装 RKNNLite/OpenCV 失败。

### 验收

- [x] 只安装 core 后，纯 Action/坐标/fusion/config 测试通过；
- [x] 安装 app 后，App、telemetry、Web 可用且没有 RKNNLite；
- [x] RK3588 YOLO 环境可以独立验证 RKNN runtime；
- [x] 没有目录重写或框架替换被混入依赖 PR。

---

## P1-2：Conda 环境和安装器

### 目标环境

```text
environment-app.yml          # Linux x86_64 / ARM64 公共高层定义
environment-dev.yml          # App + Python 测试 + Node/lint 工具
environment-rk3588-yolo.yml  # Linux ARM64 RK3588 专用
```

### 规则

- [x] 固定支持的 Python 主/次版本范围；当前安装脚本使用 3.10，升级必须单独验证；
- [x] 公共 environment 文件只声明显式高层依赖，不提交某台机器完整导出的传递包快照；
- [x] environment 通过 canonical `requirements/*.txt` 单次安装 Python 依赖；安装器不得在
      `conda env create/update` 后重复执行同 profile 的 `pip install -r`；
- [x] App 安装器允许 `x86_64/amd64` 和 `aarch64/arm64`；
- [x] YOLO 安装器必须同时检查 ARM64 和 RK3588/RKNN runtime 条件，不能只检查 `aarch64`；
- [x] App 安装失败时必须给出缺失组件和架构信息；
- [x] 所有路径从仓库根目录、当前 Conda prefix 或配置解析，禁止硬编码用户名和
      开发者家目录；
- [x] systemd 模板使用解析到的 Conda Python，不默认假设 `~/anaconda3`；
- [x] 提供通用 App healthcheck 和独立 RK3588 hardware healthcheck；
- [x] 通用 App healthcheck 不检查 NPU、摄像头或 RKNNLite；
- [x] RK3588 healthcheck 继续检查模型、NPU runtime、摄像头和默认 SEND OFF。

### App 无本地 YOLO 模式

- [x] 把现有关闭 YOLO UDP/使用空感知的能力整理为正式、文档化启动方式；
- [x] 状态接口明确报告 `perception_source=disabled|udp` 和数据是否 stale；
- [x] 不得在非 RK3588 App 环境自动下载或回退到 PyTorch/CUDA 模型；
- [x] 需要视觉的 Action 在没有有效感知时必须 fail closed；
- [x] 不需要视觉的配置、Web、telemetry、Action 和 SITL 流程仍可使用。

### 验收

- [x] 同一个 `environment-app.yml` 能在 linux-64 与 linux-aarch64 解析；
- [x] 两架构的 App 安装脚本不含 RK3588 强制检查；
- [x] x86_64 App 启动不导入 `rknnlite`；
- [x] RK3588 YOLO 环境缺少 NPU runtime 时给出明确错误，不伪装成功；
- [x] 安装/部署路径不依赖特定用户名或 Conda 安装目录。

---

## P1-3：跨架构 CI 与测试矩阵

### PR 必跑

- [x] Linux x86_64：compile/import；
- [x] Linux x86_64：core 测试；
- [x] Linux x86_64：App/telemetry/Web 测试；
- [x] Action Mission JSON validator；
- [x] JavaScript/Node 测试；
- [x] lint/format/type 检查（工具在实施时确定）；
- [x] requirements/environment 解析和重复依赖检查；
- [x] 安全静态检查：禁止 Action 直连 pymavlink/LinkManager、禁止 yolo_app 导入发送路径。

### ARM64 必跑

- [x] Linux ARM64：App environment 安装；
- [x] Linux ARM64：compile/import；
- [x] Linux ARM64：core 与非硬件 App 测试；
- [x] pymavlink 和 Web 启动 smoke test。

ARM64 可以使用受支持的原生 runner；若只能模拟运行，必须在结果中标注。没有原生或等价安装
证据时，不能把 ARM64 发布状态标为 fully verified。

### RK3588 硬件门禁

- [x] RKNNLite import 和 runtime 初始化；
- [x] 当前 `.rknn` 模型加载；
- [x] 一帧已授权测试素材推理；
- [x] YOLO UDP 协议与 App 接收兼容；
- [x] YOLO 进程没有 MAVLink/飞行控制能力；
- [x] 默认 `send_commands=false`；
- [x] 硬件结果记录 RKNN runtime/driver 版本（Toolkit Lite2 2.3.2、rknpu 0.9.8）。

RK3588 硬件检查可以是发布前人工门禁，不要求公共 CI 拥有 NPU，但公开文档必须准确说明这一点。

---

## P1-4：P1 完成定义

- [x] Linux x86_64 和 Linux ARM64 都能安装并运行非 YOLO App；
- [x] RKNNLite/OpenCV YOLO 不会进入通用 App 环境；
- [x] RK3588 YOLO 按独立环境运行；
- [x] 无视觉模式行为清楚且 fail closed；
- [x] 文档不再声称整个仓库只能在 RK3588 上导入或运行；
- [x] 文档同时明确本地 YOLO 仍只支持 RK3588；
- [x] 不存在 x86/CUDA/PyTorch 推理回退。

---

# P2：Field Profile v2 和旧架构完整退役

状态：已完成（2026-08-12）。schema v3 runtime GPS Field Reference 是唯一支持的
场地流程；schema v2/centerline binding、旧 mission/stage/control 入口已删除。

## P2-1：迁移 SITL 和 LOCAL_NED 依赖

### 当前事实

`competition_runtime.json` 是正式比赛现场模板。schema v3 使用运行时 GPS 原点和 forward
marker 生成 FIELD→GLOBAL 几何，不建立 LOCAL_NED origin。schema v2 的 anchor/centerline
方案及其 SITL、API、Web、服务和测试引用已在 P2 退役。

### 硬前置条件

删除 schema v2 前必须证明：

- [x] 当前正式 Action Mission 不再需要 schema v2 提供的 FIELD→LOCAL_NED 能力；
- [x] SITL 已迁移到 schema v3 runtime sampling；
- [x] 所有需要 LOCAL_NED 的 Action 都有明确来源，不能把 schema v3 GPS origin 假装成 local origin；
- [x] Web 正式流程不再展示 v2 bind-current；
- [x] 文档和自动化测试不再依赖 v2 profile；
- [x] 已通过 `scripts/validate_p2_field_reference.py` 完成 v3 Field Reference→Action Mission 预检流程。

### 任务

- [x] 设计并验证 SITL 的 schema v3 profile/forward marker/采样流程；
- [x] 更新 SITL 配置和教程；
- [x] 将 v2 测试所覆盖的通用数学/校验价值迁移到 v3 或更小的纯函数测试；
- [x] 统计所有 `centerline_points`、`bind-current`、`profile_centerline` 和 v2 schema 引用；
- [x] 增加“正式配置不得再引用 v2”的验证器。

### 验收

- [x] SITL 不使用 `sitl_centerline_lane.json`；
- [x] 正式任务和 Web 不要求 v2；
- [x] schema v3 的 GLOBAL 能力和 LOCAL_NED 不可用状态保持真实、可测试。

---

## P2-2：原子删除 schema v2

满足 P2-1 后，在同一退役阶段完整处理：

### 删除配置

```text
config/field_profiles/XSYU.json
config/field_profiles/sitl_centerline_lane.json
config/field_profiles/example_centerline_lane.json
config/field_profiles/example_competition_lane.json
```

### 保留配置

```text
config/field_profiles/competition_runtime.json
```

运行时生成的 profile 继续写入：

```text
runtime/field_profiles/
```

### 同步清理

- [x] schema v2 parser/validator/data model；
- [x] centerline PCA fitting 和 binding service；
- [x] `/api/field-profiles/{profile_id}/bind-current`；
- [x] Web v2 profile 选择和绑定入口；
- [x] controller/service/runtime context 中只为 v2 存在的字段；
- [x] v2 测试、fixtures 和集成测试；
- [x] beginner/developer/config/AI 文档中的 v2 指引；
- [x] 已返回 410 且只把用户引向 v2 bind-current 的旧提示，改成当前 v3 流程或删除。

### 禁止做法

- 禁止删除整个 `config/field_profiles/`；
- 禁止删除 `competition_runtime.json`；
- 禁止保留一个永远失败的 v2 API 假装兼容；
- 禁止把 v2 的 local origin 自动拼到 v3；
- 禁止通过跳过 Field Reference 前置检查让旧测试通过。

### 验收

- [x] 全仓库不再存在运行时 schema v2/centerline binding 路径；
- [x] v3 比赛现场初始化与 SITL 全流程通过；
- [x] FIELD→GLOBAL 和 FIELD→LOCAL 能力状态准确；
- [x] 配置目录只保留仍受支持的模板。

### 回滚条件

如果任何正式 Action 仍只能依靠 v2 建立 LOCAL_NED 参考，立即停止删除；先完成架构裁决，不能用
虚假 local origin 绕过。

---

## P2-3：删除旧 Mission/Stage/兼容残留

### 候选范围

- `app/mission_runner.py`；
- `app/stage_registry.py`；
- 旧 mission/stage/control 状态字段和 Web 控件；
- 只为旧路径存在的 completion、fallback、测试和文档；
- `release_payload`、RC override 和已废弃 INT8 默认描述；
- 对不存在的 CommandShaper/FlightCommandExecutor 的误导性“当前路径”表述。

### 删除前检查

- [x] 使用 `rg` 和静态导入检查证明候选文件不在当前启动、Action Mission、测试工具或迁移脚本中；
- [x] 将仍有价值的安全测试迁移到 P0 Safety Pipeline；
- [x] 将仍有价值的纯算法测试迁移到当前 Action/core；
- [x] 确认配置 schema 和状态 API 不再输出旧字段；
- [x] 更新 `docs/ai/architecture/deprecated_paths.md`，区分“已删除”与“永久禁止恢复”。

### 验收

- [x] 当前架构图与实际 import/call graph 一致；
- [x] 不再需要 legacy fallback 才能启动；
- [x] 删除旧文件不会减少当前主线的行为或安全覆盖；
- [x] 不存在为了兼容旧测试重新创建 deprecated 模块的行为。

---

# P3：开源发布准备

状态：阻塞（2026-08-12）。基础审计已完成并记录于
[`p3_release_audit.md`](../records/p3_release_audit.md)，但权利、私密报告渠道和历史对象处置尚未获得
维护者可核验证据；禁止公开发布。

## P3-1：法律和社区文件

状态：阻塞。D-09～D-11 未确认，不能以占位符或推测的主体/联系人创建正式发布文件。

### 必须新增

#### `LICENSE`

- [ ] 使用未经改写的 Apache License 2.0 正文；
- [ ] 确定并填写适当的 copyright holder/年份；
- [ ] 不自行添加可能改变许可证含义的限制条款。

#### `NOTICE`

- [ ] 记录项目归属和必须保留的 attribution；
- [ ] 只放 attribution/notice，不把第三方许可证全文全部堆入其中；
- [ ] 与 Apache-2.0 的 NOTICE 传播要求一致。

#### `CONTRIBUTING.md`

至少包含：

- [ ] Linux x86_64/ARM64 App 和 RK3588-only YOLO 平台边界；
- [ ] 当前 Action Mission 架构和禁止恢复的旧栈；
- [ ] 环境安装、分层依赖和测试命令；
- [ ] PR 粒度、提交规范和安全关键改动要求；
- [ ] P0 修改必须先 SITL、单独评审；
- [ ] 新 Action、坐标、Field Reference、MAVLink、Web API 的检查清单；
- [ ] 贡献者声明其拥有贡献权利且贡献按项目许可证提供；
- [ ] DCO 或 CLA 政策。

#### `SECURITY.md`

至少包含：

- [ ] 支持版本；
- [ ] 私下报告渠道；
- [ ] 不应公开提交的漏洞类型；
- [ ] Web 未授权控制、SEND 绕过、MAVLink 直发、命令重放、凭据泄漏、舵机误动作等范围；
- [ ] 确认、响应和披露流程；
- [ ] 维护者不能保证工业/航空认证的免责声明；
- [ ] 不把紧急飞行事件依赖为 GitHub Issue 响应。

#### `CODE_OF_CONDUCT.md`

- [ ] 采用 Contributor Covenant 2.1；
- [ ] 填写真实、可管理的举报渠道；
- [ ] 明确适用范围和执行责任人；
- [ ] 不保留模板占位符。

#### 第三方和素材清单

建议拆分为：

```text
THIRD_PARTY_NOTICES.md
ASSETS_LICENSES.md
MODEL_CARD.md 或 MODEL_LICENSE.md
```

必须逐项标记来源、作者/权利人、许可证、修改情况和是否允许再分发。

### 发布前必须由维护者确定

- [ ] copyright holder：个人、团队、学校或其他主体；
- [ ] 所有现有主要贡献者/权利人同意 Apache-2.0；
- [ ] DCO 或 CLA 的选择；
- [ ] Security 私密报告渠道；
- [ ] Code of Conduct 执行联系人；
- [ ] 学校、社团、比赛名称和 Logo 的使用授权边界。

以上信息没有确定前，可以准备模板，但不能发布带占位符的正式文件。

---

## P3-2：仓库内容、秘密和 Git 历史审计

状态：阻塞。当前与历史的模型、地形、硬件截图及 telemetry/EEPROM 产物仍需权利与内容审查；
本次未执行历史重写。

### 工作树审计

- [ ] 模型、数据集、图片、视频、地图、地形、字体、Logo 逐项确认权利；
- [ ] 删除或排除无法确认再分发权利的资产；
- [ ] 检查真实 GPS、现场坐标、个人信息、用户名、绝对路径和设备序列号；
- [ ] 检查 token、密码、私钥、Wi-Fi、SSH、云服务和 Web 密钥；
- [ ] 检查 `.gitignore` 覆盖 `runtime/` 日志、blackbox、SITL、生成视频和临时文件；
- [ ] 检查大文件是否需要 Git LFS，以及 LFS 本身是否适合公开发布；
- [ ] 依赖许可证和 NOTICE/attribution 完整；
- [ ] README 不声称尚未通过的跨平台或飞行能力。

### Git 历史审计

公开发布前不仅检查当前文件，还必须检查全部历史中的：

- 已删除 `.tlog`、blackbox 和视频；
- 旧模型和训练产物；
- 秘密和个人信息；
- 无权再分发的第三方文件；
- 大型二进制和真实坐标。

### 历史重写安全规则

Git 历史重写属于破坏性操作，只有在以下条件全部满足后才能执行：

- [ ] 精确列出需要清理的对象和原因；
- [ ] 创建可恢复备份；
- [ ] 通知所有协作者冻结 push；
- [ ] 在独立克隆中演练并验证；
- [ ] 记录重写后的迁移说明；
- [ ] 所有泄漏秘密即使从历史删除也必须轮换；
- [ ] 维护者再次明确授权执行。

本任务文档不构成历史重写授权。

### 验收

- [ ] 自动秘密扫描无未处置高风险结果；
- [ ] 大文件和资产均有处置记录；
- [ ] 工作树和完整历史均已检查；
- [ ] 发布包不包含 runtime 产物、私有坐标或无权资产。

---

## P3-3：文档和发布候选验收

状态：阻塞。P0～P2 的验证证据可复用，但发布候选环境矩阵和 P3-1/P3-2 门禁尚未完成。

### 文档必须一致说明

- [ ] App 支持 Linux x86_64/ARM64；
- [ ] YOLO 只支持 RK3588 + RKNNLite；
- [ ] 无 x86/CUDA/PyTorch YOLO；
- [ ] 两个 Conda 环境的安装和启动方式；
- [ ] 无本地视觉时的能力边界；
- [ ] Web 认证和非回环部署方法；
- [ ] SITL 使用正式发送链，不存在 Action Dry Run；
- [ ] 系统 SEND 默认 OFF 和本次运行双门控；
- [ ] Field Profile 只记录当前受支持方案；
- [ ] 项目不是经航空安全认证的产品。

### 发布候选测试矩阵

| 场景 | 必须结果 |
| --- | --- |
| Linux x86_64 core install/test | 通过 |
| Linux x86_64 App + Web + telemetry smoke | 通过 |
| Linux ARM64 core/App install/test | 通过 |
| RK3588 YOLO hardware smoke | 通过并记录 runtime/driver |
| Node/Web 测试 | 通过 |
| Action Mission validator | 通过 |
| P0 Safety Pipeline 单元/集成测试 | 通过 |
| SITL 正常任务 | 通过 |
| SITL 断线、stale、丢目标、stop、切换 | 通过 |
| Web 未认证/越权/CSRF 测试 | 通过 |
| schema v3 Field Reference 流程 | 通过 |
| 秘密/依赖许可证/资产扫描 | 无未处置阻塞项 |

### 发布门禁

以下任一项成立时禁止公开发布：

- 默认 SEND 不是严格 `false`；
- Web 非回环监听可以无认证启动；
- 存在绕过 Safety Pipeline 的飞行入口；
- 连续命令 stop/stale clear 未通过；
- Apache-2.0 权属未确认；
- 模型或素材再分发权不明确；
- 工作树或历史存在未轮换秘密；
- x86_64/ARM64 支持只写在文档里、没有安装测试；
- RK3588 YOLO 依赖被塞入通用 App 环境；
- 仍存在 Action Dry Run 或 v2 Field Profile 正式入口；
- README/教程与真实启动、配置或安全门控不一致。

---

## 7. 全局测试与证据要求

每个实施 PR 必须在描述中包含：

1. 任务编号；
2. 修改范围和明确非目标；
3. 对发送链、安全不变量和平台矩阵的影响；
4. 运行的精确测试命令和结果；
5. 未运行测试及原因；
6. 配置/schema/API 兼容性变化；
7. 需要的部署迁移；
8. 回滚方法；
9. 若涉及实机，先行 SITL 证据和现场安全条件。

禁止只写“测试通过”而不提供范围。硬件测试证据必须记录硬件型号、架构、OS、Python、
RKNN runtime/driver、配置 profile 和 git commit。

---

## 8. 待维护者补齐的实施参数

以下列出本计划最初的决策清单。D-01～D-07 的历史确认见
[P0 安全决策](../records/p0_security_decisions.md)，现行限制以 `config/safety.yaml` 为准；
D-08 的支持矩阵见[平台支持](../../developer/platform_support.md)。
D-09～D-12 尚缺维护者证据，仍是 P3 硬门禁，不交给实现者猜测。

| 编号 | 决策内容 | 对应阶段（历史或尚未完成） |
| --- | --- | --- |
| D-01 | BODY_NED 各轴最大速度 | P0-2 实机默认值 |
| D-02 | 最大下降率、偏航速率、slew | P0-2 实机默认值 |
| D-03 | 连续命令 TTL/deadman | P0-2 watchdog |
| D-04 | 航点高度、距离和 change-speed 包线 | P0-2 实机默认值 |
| D-05 | Servo 通道、保持/释放 PWM 和范围 | P0-2 payload 验收 |
| D-06 | Web 认证/密钥部署模型 | P0-1 |
| D-07 | observer/operator/admin 是否分级 | P0-1 |
| D-08 | 正式支持的 Linux 发行版和 Python 次版本 | P1-2/P1-3 |
| D-09 | copyright holder 和年份 | P3-1 |
| D-10 | DCO 或 CLA | P3-1 |
| D-11 | Security 私密渠道和 CoC 联系人 | P3-1 |
| D-12 | 模型、数据、图片、Logo 的再分发权 | P3-2 |

待定项必须通过更新本文或专门架构/安全决策文档确认，不能只存在于聊天或口头沟通中。

---

## 9. 阶段状态跟踪

| 阶段 | 状态 | 完成证据 |
| --- | --- | --- |
| P0-0 基线固化 | 已完成 | [`p0_0_baseline.md`](../records/p0_0_baseline.md) |
| P0-1 Web 控制边界 | 已完成 | [`p0_security_decisions.md`](../records/p0_security_decisions.md)、[`p0_acceptance.md`](../records/p0_acceptance.md) |
| P0-2 Safety Pipeline | 已完成 | [`p0_security_decisions.md`](../records/p0_security_decisions.md)、[`p0_acceptance.md`](../records/p0_acceptance.md) |
| P0-3 删除 Action Dry Run | 已完成 | [`p0_acceptance.md`](../records/p0_acceptance.md) |
| P0-4 P0 SITL 验收 | 已完成 | [`p0_acceptance.md`](../records/p0_acceptance.md)、`scripts/validate_p0_sitl.py` |
| P1-1 依赖分层 | 已完成 | [`requirements/README.md`](../../../requirements/README.md)、`scripts/validate_architecture_boundaries.py` |
| P1-2 Conda/安装器 | 已完成 | [`platform_support.md`](../../developer/platform_support.md)、`environment-*.yml`、通用/RK3588 healthcheck |
| P1-3 跨架构 CI | 已完成 | [`.github/workflows/ci.yml`](../../../.github/workflows/ci.yml)、`1920 passed, 4 skipped` 本地测试结果 |
| P1-4 P1 验收 | 已完成 | NanoPC-T6 RK3588：RKNN Toolkit Lite2 2.3.2、rknpu 0.9.8、模型加载/NPU 初始化/空帧推理通过；YOLO 相关测试 `18 passed` |
| P2-1 SITL/v2 前置迁移 | 已完成 | `scripts/validate_p2_field_reference.py`、schema v3 runtime sampling 测试 |
| P2-2 schema v2 原子退役 | 已完成 | 仅保留 `competition_runtime.json`；v2 parser/API/Web/测试已删除 |
| P2-3 旧架构清理 | 已完成 | `app/mission_runner.py`、`app/stage_registry.py` 已删除；静态边界检查通过 |
| P3-1 法律/社区文件 | 阻塞 | D-09～D-11 未决；[`p3_release_decisions.md`](../records/p3_release_decisions.md) |
| P3-2 仓库和历史审计 | 阻塞 | 模型/地形/截图及历史产物待处置；[`p3_release_audit.md`](../records/p3_release_audit.md) |
| P3-3 发布候选验收 | 阻塞 | 依赖 P3-1/P3-2 和发布候选环境验证 |

状态只允许使用：`未开始`、`进行中`、`阻塞`、`已完成`。标记为 `已完成` 时必须在“完成证据”
中链接 PR、测试报告或对应文档，不能只修改状态文字。

---

## 10. 推荐的首次实施顺序

以下记录本计划开始时的顺序；P0～P2 的历史完成证据见第 9 节，
不得重复执行已经退役的中间接口。最初的实施批次为：

1. P0-0 建立发送入口清单和失效行为测试；
2. 确认 D-06/D-07，实施 P0-1 Web 控制面收口；
3. 确认 D-01～D-05，实施 P0-2 Safety Pipeline；
4. 在安全管线成为必经路径后实施 P0-3，删除 Action Dry Run；
5. 完成 P0-4 全套 SITL 失效场景；
6. 再开始 P1 跨平台依赖工作。

这样可以保证后续为开源和跨平台而扩大部署范围时，公开的 Web 控制面和真实飞行发送链已经先具备
明确的鉴权、安全裁决、停止与审计边界。
