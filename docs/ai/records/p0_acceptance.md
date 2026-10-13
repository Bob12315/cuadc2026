# P0 验收记录

最后复验：2026-08-13

本文为该日期的验收记录；测试数、SITL 动作清单（含当时 LOCAL_NED goto）
和产物路径不代表当前任务模板或本次复验结果。当前 production 路径见
[当前架构](../architecture/current_architecture.md)，正式航点输入见
[Action 契约](../architecture/action_contracts.md)。

## 实现结果

- Web 默认回环监听；所有修改状态的 API 经过认证、Origin/CSRF、防滥用和结构化审计。
- 自由文本飞行入口和 manual-step 旁路返回 HTTP 410；SEND、source、YOLO 和服务管理使用强类型 API。
- 所有当前 Action request 在 `ActionDispatcher` 内统一经过 `ActionSafetyPipeline`，形成
  original/effective/rejected `SafetyDecision`。
- BODY_NED watchdog 独立于 Action tick；失效后使用优先级 0 的一次性零速屏障，再清理连续/导航命令。
- Web 的 Action Dry Run 已删除；飞行动作启动必须建立不可变 `RunAuthorization`，绑定 run ID、
  操作者、scope、目标 source 和确认时间。系统 SEND 继续作为第二道门，默认保持 OFF。

## 自动化验证

执行：

```bash
python -m compileall -q app application contracts execution field fusion guidance missions observability telemetry_link web_ui yolo_app scripts tools
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q tests/unit tests/contracts tests/integration tests/sitl
```

最终结果：708 passed，无失败。同一套件在 Linux x86_64 和隔离的 RK3588
Linux aarch64 目录上均通过。

P0 定向测试覆盖 Web 鉴权/CSRF/Origin、run 授权、TTL/stale/source、NaN/Inf/包线、Field
Reference、SERVO 白名单/幂等、连续 deadman 和 transition stop。

## 真实 ArduPilot SITL

本机使用 ArduCopter SITL，通过 UDP 14550 和正式链路：

```text
ActionDispatcher → ActionSafetyPipeline → LinkManager → telemetry_link → MAVLink → SITL
```

完成并通过：GUIDED、arm、takeoff 2 m、change-speed、relative yaw、LOCAL_NED goto、BODY_NED
连续速度、0.5 s deadman 显式 stop、land/disarm。SITL 仅在 `runtime/sitl/p0/` 产生运行文件，
结束后进程已停止。可复跑脚本为 `scripts/validate_p0_sitl.py`，运行报告为
`runtime/sitl/p0/acceptance.json`（runtime 产物不提交 Git）。

没有连接或发送到 real source，没有进行真机验证。
