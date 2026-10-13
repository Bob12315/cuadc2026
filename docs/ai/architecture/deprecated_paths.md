# Deprecated 路径清单

本清单用于阻止后续开发误恢复旧架构。`deprecated` 不等于都能立即删除。

## Stable Core 迁移期 legacy（尚不可删除）

`application/action_runtime.py`、`application/send_state.py`、`execution/dispatcher.py`、`missions/engine.py` 和
`missions/common/actions/runner.py` 是 stable-core 计划要求最终删除/退役的旧核心 owner，但当前尚未完成
CF-21/CF-25/CF-26 静止切换与零命中证明，仍属于 production rollback/运行路径。禁止新增对它们的依赖；也
禁止在 compatibility 命中归零前提前删除。用 `python scripts/validate_stable_core.py --strict` 查看当前 blocker。

| 项目 | 状态与原因 | 当前替代 | 现在可删除？/前置迁移 |
| --- | --- | --- | --- |
| terminal/curses UI | 已删除；Web UI 是正式入口 | `web_ui/` | 不得恢复独立 terminal 人工操作入口 |
| `MissionRunner` | 已在 P2 删除 | `MissionOrchestrator` | 永久禁止恢复 |
| `StageRegistry` | 已在 P2 删除 | Action registry | 永久禁止恢复 |
| `CommandShaper` | 已在 P2 删除 | P0 Action-compatible Safety Pipeline | 永久禁止恢复 |
| `FlightCommandExecutor` | 已在 P2 删除 | `ActionDispatcher → Safety Pipeline → LinkManager` | 永久禁止恢复 |
| `missions/<mission>/mission.py` | deprecated 旧式任务插件 | `config/action_missions/*.json` | 不新增；旧引用清理后可删残留 |
| `missions/<mission>/stages/<stage>` | deprecated 旧式控制阶段 | `missions/common/actions/` | 不新增；旧引用清理后可删残留 |
| `missions.visual_tracking` | 已在 P2 删除 | Action/Action Mission | 永久禁止恢复 |
| `missions.rescue_competition` | 已在 P2 删除 | Action Mission 模板 | 永久禁止恢复 |
| `missions.common.control` | 已在 P2 删除 | Action runtime + P0 Safety Pipeline | 永久禁止恢复 |
| `release_payload` 接口 | 已删除 | `payload_release` Action → `set_servo` | 不得恢复旧映射/RC override |
| RC override 投放 | deprecated，通道语义和安全边界不合适 | `set_servo` | 可禁止；不得新增兼容路径 |
| INT8 默认部署路径 | 当前模型已废弃/未验证 | 项目规范 FP16 模型 `data/models/cuadc2026-fp16.rknn`；配置偏差见 `AGENTS.md` | 可清理默认描述；硬件仍可支持未来经验证的 INT8 |
| 复合 Action 状态机（`*_sequence`、`gps_multi_view_localize`、`gps_recon_area_scan`、`visual_land`） | 已从生产 registry 删除并归档 | 原子 Action + `config/action_missions/*.json` | 永久禁止恢复生产双路径 |
| profile 内完整 Mission/config 副本 | 已删除 | 根配置 + `profile.yaml` 差异 | 不得恢复整份复制 |
| Web 接收 `SystemRunner` / 单文件 routes | 已删除 | `WebServices` + `web_ui/routers/` | 不得以兼容 fallback 恢复 |

2026-10-13 清理：旧 telemetry `CommandSender` 线程、`CommandQueue`、one-shot shadow、
文本 dispatcher、独立 telemetry/fusion debug 和 `5010` 状态广播已删除；当前保留唯一
`CommandBrokerWorker → MavlinkEnvelopeWriter → MavlinkCommandEncoder` 发送后端。
旧 writer/UI/state-UDP 配置键会被拒绝。profile 保存入口及复制旧 Mission 配置的逻辑也已删除。

退役实现与历史行为锁不进入生产导入、正式 validator 或 pytest 主线。
