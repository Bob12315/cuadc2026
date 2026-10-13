# Stable Core 修改前基线（2026-08-16）

本记录冻结本次稳定核心实现开始前的工作区事实。目录中的 `.git` 无法被 Git 识别，因此这是源码快照基线，
不声明 commit、branch、clean worktree 或可用 `git reset` 回滚。

这是 2026-08-16 环境的历史事实，不能据此推断当前目录也没有 Git、没有核心目录
或仍允许 Web tick 推进；当前状态应重新检查，并参考
[当前架构](../architecture/current_architecture.md)。

## 安全与运行边界

- `config/app.yaml` 的 `executor.send_commands` 为 `false`。
- 本次没有启动 app、Web、SITL、YOLO 或 MAVLink 服务，没有连接 RK3588/飞控，也没有发送命令。
- 修改前正式生产链仍为 `ActionRuntimeService → legacy ActionRunner → ActionDispatcher → platform adapters`。
- 修改前 Mission 仍由 `MissionApplicationService/missions.engine` 和 SystemRunner 后台 tick 推进，Web `/tick`
  也可推进；standalone Action 没有独立持续 scheduler。
- 修改前没有 `contracts/core/`、`application/core/`、`missions/core/` 或 `execution/dispatcher_v2.py`。

## 修改前验证

```text
PASS  python -m pytest -q tests -rs
      791 passed, 3 skipped
PASS  python scripts/validate_architecture_boundaries.py
PASS  python scripts/validate_action_missions.py
PASS  python -m compileall -q app application contracts execution field guidance missions
      observability telemetry_link fusion yolo_app web_ui scripts
```

三个 skip 均因本地解释器缺少 `cv2`，对应 RKNN detector、MJPEG stream 和 raw-frame recorder 测试；这不是
RK3588/NPU、SITL 或真实发送证据。

## 修改前主要风险

1. Web、后台循环和兼容方法存在多个潜在 tick owner，请求重试可能推进业务。
2. standalone Action 与 Mission 没有统一 top-level Run/active slot。
3. 采集、Action advance、dispatch、cancel 和 publication 缺少同一 immutable input/cycle correlation。
4. Effect 支持列表、动态 dict 请求、safety 和 handler 分散；accepted/queued 容易被误称为 sent/completed。
5. run/action/lease/SEND/source/session generation 没有统一原子 execution fence。
6. required recording/result projection、terminal cleanup 和 detached I/O 没有 run-level 状态机。
7. 旧 Action/Mission/Dispatcher/SEND state 仍是 production owner；直接合并切换会产生双 writer/双 tick 风险。

平台计划的 PA-07～PA-20 只有离线 readiness，缺少计划要求的完整 SITL/故障矩阵/产品输入验收；因此本记录
只作为实现基线，不把后续 core shadow 代码当成 production acceptance。
