# 当前架构裁决

本文记录当前可运行主线。与历史文档冲突时，以本文和实际代码为准。

## Stable Core readiness 与 production 边界（2026-08-16）

仓库已新增 `contracts/core/`、`missions/core/`、`application/core/` 和 typed `execution/dispatcher_v2.py`，
实现 immutable snapshot、Action/Mission v3、Run/lease/fence、typed Effect、安全/派发、Coordinator、cycle driver
与 scheduler 的离线 readiness。它们目前仍是 additive/shadow 代码；没有完成 CF-21/CF-25/CF-26 的 production
owner 切换。所以下文旧链仍是当前 production 事实，不得把 readiness 图误读为当前真实发送链。

Web `/api/action-mission/tick` 已降为只读兼容路由，不再从 HTTP request thread 推进业务。完整状态、缺口和
严格冻结入口见 [`../records/stable_core_execution_status.md`](../records/stable_core_execution_status.md) 与
[`../records/stable_core_contract_manifest.md`](../records/stable_core_contract_manifest.md)。

## 唯一任务主线

```text
Web UI
  → WebServices facade / typed routers
  → Action Lab / Action Mission
  → ActionRuntimeService
  → ActionRunner
  → missions/common/actions/*（需要时调用 guidance/* 纯算法）
  → ActionDispatcher
  → ActionSafetyPipeline
  → LinkManager
  → telemetry_link / MAVLink
```

- Action Mission 是当前唯一任务主线。
- Web UI 是当前唯一正式人工操作入口。
- `missions/common/actions/` 是 Action 实现位置。
- `config/action_missions/*.json` 是任务模板位置。
- `ActionDispatcher → ActionSafetyPipeline → LinkManager` 是当前实际发送链路。

## 模块边界

| 模块 | 当前职责 |
| --- | --- |
| `app/` | 4 个薄文件：入口、严格配置和 composition root |
| `application/` | 用例服务、运行状态、Mission 编排、WebServices 门面 |
| `missions/common/actions/` | 原子 Action 的生命周期、参数/完成判断与 typed effect；`align_descend` 当前也在此实现目标锁定和下降控制 |
| `guidance/` | 无全局状态、Web、telemetry 或发送依赖的纯算法 |
| `web_ui/` | FastAPI router、静态模块和唯一正式人工入口；只依赖 WebServices |
| `telemetry_link/` | VehicleStatePort/VehicleCommandPort、队列、MAVLink 封装和发送 |
| `fusion/` | 感知与飞控/云台状态融合，不发送命令 |
| `yolo_app/` | RKNNLite/NPU 感知和 UDP 输出，不连接 MAVLink |

旧 terminal/curses UI 已从仓库移除；当前共用用例和运行逻辑位于 `application/`，不得重新建立
独立 terminal 人工操作入口。

历史 `missions/common/lifecycle/` 已删除，不能把该路径当作现有 owner 或认为
`align_descend` 仍只是 thin wrapper；共享定位/融合数学按需从 `guidance/` 调用。

## 已废弃旧主线

`MissionRunner → StageRegistry → FlightCommand → CommandShaper →
FlightCommandExecutor` 不是当前可运行主线，其依赖的大量 mission/stage/control
模块已经缺失。不得恢复或新增旧式 `missions/<mission>/mission.py`、
`missions/<mission>/stages/<stage>`。详见 [deprecated_paths.md](deprecated_paths.md)。

## Action-compatible safety pipeline

当前所有允许到达 `LinkManager` 的 Action request 都先经过 `ActionSafetyPipeline`。
它负责 run/source/telemetry/TTL 校验、安全包线、Field Reference 前置条件、payload
白名单和独立 BODY_NED deadman；裁决保留 original/effective/rejected request。
参数来源见 [`p0_security_decisions.md`](../records/p0_security_decisions.md)。未经明确裁决不得恢复
已删除的旧控制栈。

## 当前任务编排边界

正式 catalog 只有 `drop_two_targets`、`recon_gps`、
`rescue_2026_full_auto`。多视角飞行、双目标投放、侦察航迹和视觉降落均由模板中的
原子步骤表达；Action registry 不再发布复合 sequence/scan/visual-land Action。
Mission engine 是受限顺序编排器，提供保存结果、失败重试、条件跳转和 finally 等价的
明确返航/降落步骤，不是通用脚本语言。
