# Scripts

运维和开发辅助脚本。

## 目录

| 路径 | 用途 |
| --- | --- |
| `scripts/install/` | Python 环境和系统依赖安装 |
| `scripts/deploy/` | systemd 用户服务安装与启用 |
| `scripts/config/` | RK3588 实机/SITL 配置差异渲染与切换（自动保存已停用） |
| `scripts/healthcheck/` | RK3588 板卡环境、设备与 NPU 推理检查 |

## 顶层脚本

| 文件 | 用途 |
| --- | --- |
| `scripts/validate_action_missions.py` | Action Mission JSON 模板格式和参数校验 |
| `scripts/audit_release_readiness.py` | P3 当前工作树与所有 Git refs 的只读发布阻塞审计 |
| `scripts/smoke_test_envs.sh` | app 和 yolo conda 环境冒烟测试 |
| `scripts/run_iris_gimbal_sitl.sh` | 启动 Iris + gimbal SITL 仿真 |
| `scripts/find_ssh_hosts.py` | 在本机私有 `/24` 网段中查找能使用指定账户 SSH 登录的设备 |

## 查找同网段 SSH 设备

仅对自己有授权的私有网络使用。直接编辑 `scripts/find_ssh_hosts.py` 顶部的 `SSH_USERNAME`、`SSH_PASSWORD` 和 `TARGET_NETWORK`，无需另建配置文件。密码会明文保存在脚本中，提交 Git 前应清掉。

```bash
# 编辑脚本顶部配置后直接运行，无需再输入用户名或密码
python3 scripts/find_ssh_hosts.py
```

默认端口是 22、并发数是 16、单次网络超时是 1 秒，也可在脚本顶部改。脚本会列出可确认在线的设备：SSH 端口开放，或 ping 有回应；并单独标明 SSH 登录成功的设备。禁用 ping 且未开放 SSH 的在线设备不会显示。脚本只支持一个私有 IPv4 `/24` 网段，避免误扩大探测范围。运行中密码会在一个仅当前用户可读的临时文件中传给 OpenSSH，并在结束后清除。

## 安装

```bash
# 通用 App：脚本会按需 create/update uav-app，并执行健康检查
bash scripts/install/install_app_env.sh

# 本地开发环境（App + pytest/ruff/mypy + Node.js）
conda env create -f environment-dev.yml
# 已存在时更新：conda env update -f environment-dev.yml --prune

# 只在 RK3588 实机创建或更新视觉环境；脚本会检查板型和 NPU
bash scripts/install/install_yolo_env.sh
```

`environment-*.yml` 通过 `requirements/*.txt` 安装 Python 包。不要在创建环境后再对同一 profile
重复执行 `pip install -r`。根目录旧 `requirements-app.txt`、`requirements-dev.txt` 和
`requirements-yolo.txt` 已删除。

完整说明见 [RK3588 环境配置](../docs/beginner/03_rk3588_setup.md)。

## 部署

立即启动前，按[部署指南](../docs/ai/guides/DEPLOY_RK3588.md)向 systemd 用户管理器提供
Web 操作者凭据；仅在交互终端中 export 不会自动传给用户服务。

```bash
bash scripts/deploy/install_systemd_user_services.sh --dry-run
bash scripts/deploy/install_systemd_user_services.sh
bash scripts/deploy/install_systemd_user_services.sh --enable-now
```

## 配置档案切换

RK3588 实机和 SITL 使用不同的遥测和 YOLO 配置。通过 profile 脚本切换：

```bash
bash scripts/config/apply_rk3588_real.sh   # 实机配置
bash scripts/config/apply_rk3588_sitl.sh    # SITL 配置
```

旧 `save_rk3588_*.sh` 和完整配置复制逻辑已删除。
调参后先将现场配置快照保存到 `runtime/`：

```bash
mkdir -p runtime/config-backups
cp -a config "runtime/config-backups/config-$(date +%Y%m%d-%H%M%S)"
```

两个 apply 脚本都要求 `config/app.yaml` 中 `executor.send_commands` 为 `false` 才允许切换。
审阅现场快照后只把必要差异写入 `profile.yaml`，再用 renderer 检查；自动保存不会完成这一步。

## 健康检查

```bash
bash scripts/healthcheck/check_rk3588.sh
```

检查 ARM64/RK3588 身份、NPU 设备、环境依赖、摄像头设备及默认 SEND。
它还会加载 `cuadc-fp16.rknn`、初始化 NPU 并执行空白帧推理，应避开正在进行的视觉任务。
该脚本不会连接 MAVLink，也不检查磁盘空间。

## 验证

```bash
python scripts/validate_action_missions.py
python scripts/audit_release_readiness.py
```

发布门禁使用 `python scripts/audit_release_readiness.py --strict`。若报告模型、素材、历史或
法律/社区文件阻塞，命令会以非零退出；不要用忽略或软失败绕过它。
