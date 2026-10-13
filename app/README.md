# app

`app/` 是薄启动层，不写业务用例、控制算法，也不直接构造 MAVLink 消息。

## 职责

- 加载总配置。
- 创建严格配置。
- 在 `bootstrap.py` 中组合并启动 `application/` 服务。
- 提供 `python -m app.main` 入口。

## 主要文件

- `main.py`：模块入口。
- `bootstrap.py`：composition root。
- `config.py`：严格 app/telemetry/Web 配置。
- `__init__.py`：包声明。

主循环和用例服务位于 `application/`；执行、安全派发位于 `execution/`。

## 禁止事项

- 不写控制公式。
- 不直接 import pymavlink。
- 不绕过 `ActionDispatcher` 与 Safety Pipeline。

## 配置边界

- `config/app.yaml`：app 服务、UI、黑匣子和控制出口。
- `config/telemetry.yaml`：由 `telemetry_link.config` 统一解析，app 不重复定义解析规则。
- `config/yolo.yaml`：YOLO 进程及目标切换 UDP 配置。
- `config/action_missions/*.json`：Action Mission 模板。

旧 `control/` 配置入口已经移除。部署和调试统一使用根目录 `config/`。
