# AI / 开发者文档入口

`docs/ai/` 只保留这一份总入口。其余文档按用途分目录，避免把“当前程序事实”“未来改造计划”
和“历史证据”混在一起。

```text
docs/ai/
├─ README.md       # 唯一总入口
├─ architecture/   # 当前程序架构、接口与禁止边界
├─ plans/          # 目标架构、改造任务与执行顺序
├─ records/        # 基线、验收、状态和决策证据
└─ guides/         # AI 任务清单与部署操作指南
```

## 文档权威级别

| 类别 | 回答的问题 | 使用原则 |
| --- | --- | --- |
| [`architecture/`](architecture/current_architecture.md) | 程序现在怎样运行、接口和边界是什么 | 当前事实入口；与源码不一致时先核对源码 |
| [`plans/`](plans/architecture_refactor_tasks.md) | 程序准备怎样改、任务按什么顺序执行 | 目标和迁移步骤；未完成任务中的接口不是当前接口 |
| `records/` | 当时的基线、验收结果和决策依据是什么 | 用于回归和审计；不能单独代替完整当前架构 |
| [`guides/`](guides/task_checklist.md) | 接手任务或部署时具体读什么、做什么 | 执行入口；仍受当前架构和安全边界约束 |

当前代码与 `architecture/` 的优先级高于尚未落地的计划；`records/` 中的安全决策可能仍是
参数来源，但记录本身不自动代表完整现状。

## 固定必读顺序

1. 根目录 `README.md` 和 `AGENTS.md`；
2. [当前架构](architecture/current_architecture.md)；
3. [当前接口](architecture/interfaces.md)；
4. [Action 契约](architecture/action_contracts.md)；
5. [废弃路径](architecture/deprecated_paths.md)；
6. [坐标系](../developer/coordinate_frames.md)；
7. [Field Reference](../developer/field_origin_heading.md)；
8. [安全边界](../developer/safety.md)；
9. [任务阅读清单](guides/task_checklist.md)。

## 按任务选择文档

| 任务 | 追加阅读 |
| --- | --- |
| 普通 Action / Mission 修改 | [Action 契约](architecture/action_contracts.md)和[任务阅读清单](guides/task_checklist.md) |
| 全项目架构整理 | [全项目架构重构任务书](plans/architecture_refactor_tasks.md) |
| Web、MAVLink、YOLO UDP、Field、日志等平台边界 | [平台适配层计划](plans/platform_adapter_interface_refactor_plan.md) |
| Action、Mission、Effect、Run、scheduler 稳定核心 | [稳定核心层计划](plans/stable_core_refactor_plan.md) |
| 开源、跨平台、安全和发布工作 | [开源与安全改造计划](plans/open_source_refactor_plan.md)及对应 `records/` 证据 |
| RK3588 实机部署 | [RK3588 部署规范](guides/DEPLOY_RK3588.md) |
| 笔记本 SITL 环境安装 | [笔记本 SITL 安装规范](guides/SETUP_LAPTOP_SITL.md) |

证据记录按主题读取：

| 主题 | 记录 |
| --- | --- |
| P0 安全 | [重构前基线](records/p0_0_baseline.md)、[安全决策](records/p0_security_decisions.md)、[验收记录](records/p0_acceptance.md) |
| 平台适配层 | [PA-00 基线](records/platform_adapter_interface_baseline.md)、[执行状态](records/platform_adapter_interface_execution_status.md) |
| P3 发布 | [发布审计](records/p3_release_audit.md)、[维护者决策](records/p3_release_decisions.md) |

平台与稳定核心的唯一执行顺序是：

```text
PA-00～PA-20 → CF-00～CF-28 → PA-21～PA-31
```

每个会话只执行一个明确的 `AR-xx`、`PA-xx` 或 `CF-xx`。具体完成状态以相应计划中的复选框和
完成记录为准。

## 当前主线与安全红线

```text
Web UI → WebServices facade / typed routers → Action Mission → ActionRuntimeService
→ ActionRunner → Actions（按需调用 guidance）→ ActionDispatcher
→ ActionSafetyPipeline → LinkManager → telemetry_link / MAVLink
```

- 不恢复旧 mission/stage/control 栈；
- Action 不直接调用 pymavlink 或 `LinkManager`；
- Web UI 是唯一正式人工操作入口；
- 本地 YOLO 只走 RKNNLite + RK3588 NPU，不新增 x86/CUDA/PyTorch 推理路径；
- `executor.send_commands` 默认保持 `false`；
- 投放只走 `payload_release → set_servo`；
- 运行产物只进入 `runtime/`。

主线验证命令和测试范围见 [tests/README.md](../../tests/README.md)。历史 Git 计划或未完成目标接口不能替代当前源码、
`architecture/` 和已验收契约。
