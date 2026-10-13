# 03：RK3588 环境配置

## 本章目标

把 RK3588 准备成一台可以联网、可以通过 SSH 登录的 Linux 主机。完成这些基础步骤后，
项目代码、Python、RKNN、YOLO、MAVLink 和 systemd 服务交给 AI Coding Agent 部署。

> [!TIP]
> 推荐使用 **Ubuntu 22.04 ARM64**。当前项目主要在 Ubuntu 22.04 / Linux ARM64 RK3588
> 环境下使用和验证，软件依赖与 RKNN 环境相对容易对齐。其他 Linux ARM64 系统也可以
> 尝试，但如果板卡 BSP、内核、NPU 驱动或 RKNNLite 版本不同，可能需要额外适配。

## 1. 给 RK3588 刷写系统

不同 RK3588 板卡的镜像和刷写工具不同，请以板卡厂商说明为准：

```text
下载板卡对应的 Ubuntu 22.04 ARM64 镜像
→ 使用厂商推荐工具刷写
→ 首次启动
→ 创建用户名和密码
→ 进入桌面或终端
```

不要把其他板卡的镜像直接刷入当前设备，也不要根据本教程猜分区、引脚或恢复模式。

<!-- TODO: 后续补充具体 RK3588 板卡刷机视频 / 截图 -->

## 2. 连接网络

首次配置时，可以给 RK3588 接上显示器和键盘，然后连接 Wi-Fi；也可以直接使用网线。
这一阶段只需要确认两件事：

- RK3588 可以访问互联网；
- 笔记本和 RK3588 之间网络可达。

## 3. 安装并开启 SSH

在 RK3588 上运行：

```bash
sudo apt update
sudo apt install openssh-server net-tools -y
sudo systemctl enable ssh
sudo systemctl start ssh
```

检查 SSH 服务并查看 RK3588 的 IP 地址：

```bash
systemctl status ssh
hostname -I
ifconfig
```

记录连接信息，不要把密码写进文档或仓库：

```text
RK3588 用户名：
RK3588 IP：
```

然后在笔记本终端测试：

```bash
ssh "<用户名>@<RK3588-IP>"
```

看到 RK3588 的命令行就表示成功。到这里以后，绝大多数板端软件配置都可以交给 AI Agent
完成。

## 4. 在笔记本安装 AI Coding Agent

选择一个能够完成以下工作的 AI Coding Agent：

- 阅读 Git 仓库；
- 执行本地终端命令；
- 使用 SSH 操作远程主机；
- 阅读和修改文件；
- 查看执行结果和日志。

例如 Codex、Claude Code，或其他具备终端和 SSH 能力的 Coding Agent。

> AI Agent 的具体安装方法请参考其官方文档。

## 5. 让 AI 通过 SSH 部署项目

```text
笔记本
  │
  │ AI Coding Agent
  │
  │ SSH
  ▼
RK3588
  ├── 检查系统
  ├── 获取仓库
  ├── 安装依赖
  ├── 配置 Python
  ├── 配置 RKNN 和 YOLO
  ├── 配置 MAVLink
  ├── 配置 systemd
  └── 验证运行
```

AI 必须先阅读 [`docs/ai/guides/DEPLOY_RK3588.md`](../ai/guides/DEPLOY_RK3588.md)。把下面这段提示词
复制给 AI，并替换 SSH 地址：

```text
请通过 SSH 帮我部署这台 RK3588 上的 cuadc2026 项目。
仓库：https://github.com/Bob12315/cuadc2026.git

RK3588 SSH：
<用户名>@<RK3588-IP>

请先阅读仓库中的：

docs/ai/guides/DEPLOY_RK3588.md

严格按照该文件检查、部署和验证。

要求：
1. 先检查环境，再进行安装。
2. 不确定硬件信息时询问我，不要猜。
3. 不发送任何真实解锁、起飞、航点、速度、降落、舵机或 Mission 控制命令。
4. 每完成一个阶段都验证结果。
5. 遇到错误时先定位原因，不要直接重装系统或覆盖配置。
6. 保留我已有的 Git 改动。
7. 可以执行部署所需的 sudo 命令并触发终端密码提示；密码由我在安全提示中输入，
   不要从对话读取、保存或回显密码，也不要把密码拼进命令。
8. 最后告诉我：
   - 哪些环境已经配置完成；
   - app 是否正常；
   - YOLO / RKNN 是否正常；
   - Web UI 是否正常；
   - MAVLink 是否能只读连接；
   - 哪些地方还需要人工处理。
9. 如果软件下载较慢，可以在确认镜像来源可信后换用国内镜像源。
```

## 6. 验证部署结果

AI 完成后，至少确认：

- app 和 YOLO 服务能够稳定运行；
- RKNNLite 能加载当前 RKNN 模型；
- 已通过环境变量或权限受限文件向 app 提供操作者口令；使用 systemd 时也已给用户服务配置该环境变量；
- 若需要局域网访问，已核对 `web_host`、实际 IP 的 Host/Origin 白名单和可信网段，Web UI 可以通过 `http://<RK3588-IP>:8080/` 访问；
- YOLO 视频和检测结果可见；
- MAVLink 能只读显示 heartbeat、GPS 和姿态；
- Web UI 明确显示 SEND OFF；
- 没有运行任何真实飞行动作或完整 Mission。

如果某项失败，把 AI 的完成报告和对应日志保留下来，不要立即重装系统。

## 7. 在笔记本配置仿真环境

这一部分用于安装后续 SITL 测试需要的：

- ArduPilot SITL
- Gazebo
- `ardupilot_gazebo`
- 相关仿真依赖

笔记本安装由 AI Agent 按
[`SETUP_LAPTOP_SITL.md`](../ai/guides/SETUP_LAPTOP_SITL.md) 自主完成。
将下面的提示词复制给能够执行本地终端命令的 AI Agent。执行过程中出现 sudo 密码提示时，
直接在终端提示中输入密码，不要把密码写入提示词：

```text
请帮我在当前笔记本上自主安装本项目的 SITL 仿真环境。

请先阅读仓库中的：

docs/ai/guides/SETUP_LAPTOP_SITL.md

严格按该文件完成环境预检、Conda 项目环境、Gazebo Harmonic、ArduPilot Copter SITL、
ardupilot_gazebo、GStreamer、联调验证和最终报告。已有环境只检查并补齐，不覆盖现有
Conda、Git 改动、代理配置或 shell 配置。
除必须由我决定的冲突或风险外，请自行执行安装并逐阶段验证，不要只给我命令清单。
允许直接执行所需的 sudo 命令；需要认证时触发终端密码提示让我输入，
不要读取、保存、回显密码，也不要把密码放进命令、日志或文件。
```

也可以参考下面的视频了解安装过程：

> 📹 [仿真环境配置视频（Bilibili）](https://www.bilibili.com/video/BV18fzaB6E6q/)

安装提示词以官方 Gazebo、ArduPilot 文档和实际系统检查结果为准；视频中的版本或命令可能已经
变化。

环境安装完成后，下一章会介绍怎样真正启动和测试 SITL。

## 完成检查

- [ ] RK3588 已安装 Linux ARM64 系统
- [ ] 推荐使用 Ubuntu 22.04
- [ ] RK3588 可以正常联网
- [ ] 笔记本可以通过 SSH 登录 RK3588
- [ ] 笔记本已经准备好 AI Coding Agent
- [ ] 笔记本已按 `docs/ai/guides/SETUP_LAPTOP_SITL.md` 配置 SITL 环境
- [ ] AI 已按照 `docs/ai/guides/DEPLOY_RK3588.md` 完成部署
- [ ] app 可以正常运行
- [ ] YOLO / RKNN 可以正常运行
- [ ] Web UI 可以访问
- [ ] MAVLink 可以只读获取飞控状态
- [ ] `executor.send_commands` 仍然为 `false`

**下一章：[SITL 测试](04_sitl_test.md)**
