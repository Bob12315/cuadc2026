# Stable Core 批量执行状态（2026-08-16）

本文记录用户在原始硬门禁报告后明确要求“直接全部改、最后由用户验证”时，本轮实际落盘内容、离线证据和
仍未完成项。任务完成仍以
[`stable_core_refactor_plan.md`](../plans/stable_core_refactor_plan.md) 的逐项验收为准；本记录不把 readiness、
离线单测或用户后续验证承诺冒充 SITL/目标硬件/production cutover 证据。

本文的“本轮/当前”均指 2026-08-16 执行会话；其取消项、设备状态和测试数字
不构成后续会话的事实或授权。当前 production 路径见
[当前架构](../architecture/current_architecture.md)。

## 1. 修改前基线和安全约束

修改前基线见 [`stable_core_baseline.md`](stable_core_baseline.md)。开始修改时正式测试为 `791 passed,
3 skipped`，没有 stable-core 目录，旧 Action/Mission/Dispatcher/SystemRunner 是 production owner。

本轮始终保持：

- `config/app.yaml: executor.send_commands: false`；
- 未启动 app、Web、YOLO、SITL 或 MAVLink；
- 未连接 RK3588、飞控或其他真实硬件；
- 未发送 arm/takeoff/land/position/BODY_NED/servo/payload 命令；
- 未恢复 MissionRunner、StageRegistry、CommandShaper 或 FlightCommandExecutor。

## 2. 已落盘的离线 stable-core 实现

### 契约、快照与 Action/Effect

- 新增 `contracts/core/`：typed IDs/time/version/FrozenJson、immutable input/cycle、11 个 typed Effect、
  ActionDefinition/codec/feedback/Runner、Mission v3、grant/lease/safety/dispatch、Run/System/I/O contracts。
- 新增 `application/core/SnapshotCollector` 和 immutable stores；组件读取失败 fail closed，同周期使用一个
  `RuntimeInputSnapshot/InputSnapshotRef`。
- 新增封闭 Effect registry、capability policy、pure SafetyPolicy、vehicle translator 和 `dispatcher_v2`；
  dispatcher 可核对完整 execution fence，accepted 与 completed 分离。
- 新增 `EffectDeliveryTracker` 和 platform status projection：接纳前失败才做有界 retry，accepted 后不重提交，
  continuous BODY_NED 不重试旧帧，retry 保留 Effect identity 并使用当前 evaluation input ref。
- 新增 feedback-driven 原生 `takeoff/land/yaw_align/change_speed` Action；terminal result 不携带 Effect。
  其余正式 Action 暂由显式 legacy adapter 支撑 core shadow，不宣称原生迁移完成。

### Mission、Run、系统与 scheduler

- 三个正式 v2 Mission 可确定性编译为 immutable v3 definition；支持 bounded retry/jump/continue/skip、
  per-cycle/total budget、每 child 新 token/lease、每 step lease/cancel 边界和 exit barrier。
- blackboard 深度 immutable，保存 step/action/schema/time provenance；compiled expression 在 Mission 层解析，
  Coordinator 不解析 `$` 或 JSON。
- `RunAggregate` 有显式 transition table、terminal 不可逆、terminalize-once、last input/tick projection。
- `RunIoAggregate` 区分 PENDING/ACCEPTED/ACTIVE/PERSISTED/RELEASED/FAILED/TIMED_OUT；required recording/result
  不把 submission acceptance 当完成，并有 bounded detached tracker。
- `SystemControlAggregate` 有 full-version SEND CAS、idempotency payload conflict、maintenance/exclusive operation
  reservation；`SystemSendState` 默认 false。
- `CoreExecutionFenceAuthority` 原子维护 run/auth/lease/SEND/cancel/source/session，generation 使用 authority-
  lifetime counter，revoke 后不会复用 generation。
- `RunCoordinator` 是新路径唯一 top-level Run/lease/cancel policy owner；request thread 只入队，Action/Mission
  共用 active slot，source/session/SEND generation 变化触发 stop/revoke/cancel。
- `CoreCycleDriver` 固定 capture → status query → advance once → cancel/dispatch → commit → publish；
  `CoreScheduler` 不 catch-up replay，非 daemon 且有界 join，并记录 driver exception。
- Web `/api/action-mission/tick` 已改成 deprecated/read-only，不再从 HTTP request thread 推进 Mission。

### 守卫与文档

- `scripts/validate_stable_core.py`：默认验证 stable-core import/SEND/Web-tick 边界；`--strict` 额外要求生产
  cutover 和 legacy 删除，当前应失败并精确列出 blockers。
- 新增 [`stable_core_contract_manifest.md`](stable_core_contract_manifest.md)。
- architecture boundary validator 允许 core 复用 canonical `contracts.platform` DTO；没有引入具体硬件依赖。

## 3. 当前未完成、取消或必须由外部环境完成的事项

| 范围 | 当前状态 | 原因/下一证据 |
| --- | --- | --- |
| CF-00 formal acceptance | 未勾选 | baseline 已生成，但 PA-07～PA-20 没有按平台计划正式验收。 |
| CF-01～CF-06 | substantial offline readiness | contract/model/tests 已落盘；缺计划要求的完整 characterization/differential shadow 证据。 |
| CF-07～CF-11 | 部分完成 | 4 个 one-shot 已原生；其余 17 个 Action 仍兼容包装，复杂算法 golden/differential 未完成。 |
| CF-12～CF-16 | substantial offline readiness | compiler/blackboard/reducer 已落盘；三个正式 Mission 的完整 fake lifecycle/differential trace 未完成。 |
| CF-17～CF-24 | substantial shadow readiness | fence/safety/dispatcher/tracker/Run/Coordinator 已落盘；Run I/O 尚未接入 driver/coordinator 全生命周期，PA broker production writer 未切。 |
| CF-21 production writer cut | 未执行 | 旧 dispatcher 仍 production；无 PA-07～PA-10 SITL/writer/barrier 验收和静止切换证据。 |
| CF-25 production lifecycle cut | 未执行 | 旧 ActionRuntime/MissionService/SystemRunner 仍 production；全部 Action 未原生，不能安全删除 rollback path。 |
| CF-26 scheduler/recorder cut | 未执行 | CoreScheduler readiness 已实现，但未接 production；旧 blackbox 仍唯一 writer，PA-20 S2 未切。 |
| CF-27 legacy deletion | 未执行 | compatibility hit 未证明为零；删除会破坏当前 production。 |
| CF-28 strict freeze | 未通过 | `validate_stable_core.py --strict` 应失败；manifest 明确为 readiness。 |
| RK3588/ARM64/RKNN | 本轮取消 | 设备不在线；未产生 NPU/model/camera/target Linux 证据。 |
| SITL/SEND-on | 本轮取消 | 用户要求保持 SEND 关闭；未产生 wire ordering、ACK/completion 或飞行行为证据。 |

因此本轮没有修改稳定核心计划的 `[ ]` 复选框，也没有把 AR-26/CF-28 标记完成。能离线安全实现的核心
骨架、纯模型和测试已经落盘；production owner 切换、legacy 删除和最终 freeze 仍需先完成表中门禁。

## 4. 本轮验证

使用 Codex bundled CPython 3.12.13，所有命令均未启动生产服务。最终数字以本文件最后一次更新后的实际
验证输出为准：

```text
PASS  python -m pytest -q tests -rs
      817 passed, 3 skipped in 5.03s
PASS  python scripts/validate_architecture_boundaries.py
PASS  python scripts/validate_action_missions.py
PASS  python scripts/validate_stable_core.py
EXPECTED FAIL  python scripts/validate_stable_core.py --strict
      7 个 blocker：5 个旧 production owner 文件、兼容 Action registrations、legacy background tick owner
PASS  python -m compileall -q app application contracts execution field guidance missions
      observability telemetry_link fusion yolo_app web_ui scripts tests
```

三个 `cv2` 测试继续 skip；没有用 skip 替代 RK3588 或目标平台验收。
