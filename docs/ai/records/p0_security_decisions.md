# P0 安全决策与参数来源

日期：2026-08-12

本文记录当日安全决策和参数来源，不是当前参数副本。当前安全边界见
[安全文档](../../developer/safety.md)，实际包线和 SERVO/PWM 白名单以
[`config/safety.yaml`](../../../config/safety.yaml) 及正式任务模板为准；
下列 SERVO 数值属于历史记录，不应用于现场配置。

## Web 控制面

- 使用局域网单操作者模型，固定角色为 `operator`；P0 不引入 observer/admin 多角色系统。
- 口令仅从 `UAV_WEB_OPERATOR_PASSWORD` 或 `UAV_WEB_OPERATOR_PASSWORD_FILE` 加载。
  权限文件不得对 group/world 开放。
- 默认监听 `127.0.0.1`。非回环监听仍强制认证；缺少凭据时启动失败。
- 浏览器使用 HttpOnly、SameSite=Strict 会话 cookie 和 CSRF token；Bearer 口令仅用于非浏览器自动化。
- 安全事件写入 `runtime/logs/web_ui/security.jsonl`，操作审计写入
  `runtime/logs/web_ui/audit.jsonl`。P0 不配置外部接收器。

## Safety Pipeline 参数

参数集中在 `config/safety.yaml`。P0 采用当前已运行任务模板和控制器中实际使用值的保守上界，
没有提高任何飞行包线：

- BODY_NED 前后/左右最大 `0.4 m/s`；上升/下降最大 `0.35 m/s`；偏航速率最大
  `0.6 rad/s`；TTL/deadman `0.5 s`。
- 航点高度 `0.3–6.0 m`，单次航点距离最多 `100 m`，change-speed `0.1–2.0 m/s`。
- 起飞仅允许 `GUIDED`，高度 `0.5–6.0 m`，并要求已解锁。
- 投放只允许原子 `payload_release` 使用模板中明确配置的 SERVO 8（`1200–1750`）和
  SERVO 9（`1185–1815`）；复合 Action 不再拥有投放权限。
- SITL 和 real 共用同一保守包线，因此 real 不会比 SITL 更宽松。
- slew/rate limit 暂不启用；独立 watchdog 和显式 zero/stop 已启用。

任何放宽以上值的变更都需要新的 SITL、空载舵机和检测/飞行验证证据。
