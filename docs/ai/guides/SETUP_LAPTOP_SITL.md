# 笔记本 SITL 环境安装指南（供 AI Agent 执行）

## 目标

本文是 AI Coding Agent 在笔记本上自主安装本项目仿真环境的执行提示词。目标是完成：

- Conda 与项目 `uav-dev` 开发环境；
- Gazebo Harmonic；
- ArduPilot Copter SITL；
- `ardupilot_gazebo` 插件；
- OpenCV、GStreamer 和插件编译依赖；
- 最小启动验证和可复核的安装报告。

这不是 RK3588 板端部署指南。RKNNLite 和 RK3588 NPU 环境只能按照
[`DEPLOY_RK3588.md`](DEPLOY_RK3588.md) 在 RK3588 上安装，不得在 x86_64 笔记本上增加
CUDA、PyTorch GPU 或其他 YOLO 推理替代路径。

## 给 AI Agent 的完整任务

请在当前笔记本上自主完成本文件规定的 SITL 环境安装。允许执行安装所需的 sudo 命令并触发
终端的安全密码提示；除密码认证、硬件信息或会造成现有数据损失的选择外，不要把普通安装步骤
交还给用户。每个阶段先检查现状，只补齐缺失项，
然后验证；发生错误时先收集证据并定位原因，不要反复重装。

开始前阅读：

1. 仓库根目录 `AGENTS.md` 和 `README.md`；
2. [`docs/beginner/04_sitl_test.md`](../../beginner/04_sitl_test.md)；
3. [`docs/developer/platform_support.md`](../../developer/platform_support.md)；
4. 本文件引用的 Gazebo 和 ArduPilot 官方安装文档。

### 执行边界

- 可以直接运行所需的 sudo 命令，并在需要认证时触发终端密码提示。
- sudo 需要认证时，由用户在终端提示中输入，或使用不会向 Agent 暴露内容的系统安全输入通道；
  不从对话读取，不保存、回显或提交密码，也不把密码拼进命令、环境变量、日志或文件。
- Token、SSH 私钥、代理订阅和其他秘密同样不得输出、保存或提交。
- 不覆盖现有 Conda、Git 仓库、shell 配置或用户文件；先检查并保留本地修改。
- 禁止 `git reset --hard`、`git clean -fd`、强制 checkout 和未经确认的删除。
- 不修改 Clash 配置、代理订阅或系统网络规则。只检查代理进程、环境变量和实际联网结果；
  没有 Clash 时按普通网络继续。
- 首选官方软件源。只有官方源下载失败或持续过慢时，才使用与当前发行版匹配的可信镜像；
  修改前备份相关配置，记录改动，并在报告中给出恢复方法。
- 不为安装方便执行不明来源脚本。必须执行的官方安装脚本先确认 URL 或仓库来源。
- 不启动真实无人机 Action/Mission，也不连接或控制真实飞控。本文只允许本机 SITL 验证。
- 仓库外的安装和构建目录默认放在用户主目录下；项目运行日志、SITL 状态和临时生成物放在
  `runtime/`，不写入源码或配置目录。

## 1. 只读预检

先运行并记录关键信息，不进行安装：

```bash
uname -a
uname -m
cat /etc/os-release
df -h
free -h
command -v git curl cmake gz conda python3
git --version 2>/dev/null || true
cmake --version 2>/dev/null || true
gz --versions 2>/dev/null || true
conda --version 2>/dev/null || true
python3 - <<'PY'
import os
from urllib.parse import urlsplit
for key in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
    value = os.environ.get(key)
    if value:
        try:
            parsed = urlsplit(value)
            print(f"{key}: scheme={parsed.scheme}, host={parsed.hostname}, port={parsed.port}, credentials={'present' if parsed.username or parsed.password else 'absent'}")
        except ValueError:
            print(f"{key}: set (invalid URL; value hidden)")
PY
```

检查当前项目和可能已经存在的外部仓库：

```bash
git status --short --branch
git log -1 --oneline
git -C "$HOME/ardupilot" status --short --branch 2>/dev/null || true
git -C "$HOME/gz_ws/src/ardupilot_gazebo" status --short --branch 2>/dev/null || true
```

联网测试必须验证 DNS、HTTPS 和软件源，而不是只看 Clash 图标。不得打印代理订阅或认证信息。
如果系统不是受 Gazebo Harmonic 官方二进制包支持的 Ubuntu 版本，停止 Gazebo 安装并报告；
不要硬改发行版代号。当前官方文档列出的二进制支持版本是 Ubuntu 22.04 Jammy 和 24.04 Noble，
执行时仍以官方页面为准。

预检后先用简短清单报告“已满足 / 需要安装 / 需要用户确认”，然后继续所有安全且明确的项目。

## 2. Conda 和项目开发环境

如果已有可用 Conda，沿用现有安装，不要再安装第二份。如果没有，按 CPU 架构从 Conda 官方或
Miniforge 官方发布页选择匹配的 Linux 安装器，校验下载完整性后安装到用户目录；不得覆盖系统
Python。安装后让当前非交互 shell 正确加载 Conda。

项目 Python/Node 开发依赖使用仓库定义的独立环境，不手工复制依赖清单，也不再执行第二次
`pip install -r`：

```bash
cd "<本项目仓库根目录>"
conda env create -f environment-dev.yml
```

如果 `uav-dev` 已存在，先核对环境来源，再用以下方式更新：

```bash
conda env update -n uav-dev -f environment-dev.yml --prune
```

验证：

```bash
conda run -n uav-dev python --version
conda run -n uav-dev node --version
conda run -n uav-dev python -m pytest --version
```

Gazebo 的系统库和 ArduPilot 官方前置依赖仍按官方方式安装。不要为了“全部放进 Conda”而把
这些系统组件换成未经项目验证的 Conda 包，也不要在 Conda 环境中污染 ArduPilot 安装脚本的
Python 选择。

## 3. 安装 Gazebo Harmonic

以 [Gazebo 官方文档](https://gazebosim.org/docs/harmonic/install_ubuntu/) 为准。

在确认 Ubuntu 版本受支持、现有 Gazebo 安装不冲突后执行：

```bash
sudo apt-get update
sudo apt-get install -y curl lsb-release gnupg
sudo curl https://packages.osrfoundation.org/gazebo.gpg \
  --output /usr/share/keyrings/pkgs-osrf-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/pkgs-osrf-archive-keyring.gpg] https://packages.osrfoundation.org/gazebo/ubuntu-stable $(lsb_release -cs) main" \
  | sudo tee /etc/apt/sources.list.d/gazebo-stable.list >/dev/null
sudo apt-get update
sudo apt-get install -y gz-harmonic
```

安装完成后可用 headless server 验证，避免把 GUI/显卡问题误判为基础安装失败：

```bash
gz --versions
timeout 20 gz sim -s -v4 shapes.sdf
```

日志应包含 `World [shapes] initialized`；`timeout` 发送 SIGTERM 后的正常关闭日志不代表失败。

Gazebo Harmonic 默认不能与 Gazebo Classic 11 并存。检测到 `gazebo11`、ROS/Gazebo 版本绑定
或已有自定义 OSRF 源时，不要直接卸载或替换；先报告冲突及官方兼容方案，需要改变现有环境时
再询问用户。

验证版本，并用官方 `shapes.sdf` 做一次有限时长启动检查。图形会话可验证 GUI；无显示会话时
使用 server/headless 方式并说明未验证 GUI。测试进程超时退出不等于安装失败，需检查启动日志中
是否存在资源、渲染或动态库错误。

## 4. 安装并构建 ArduPilot SITL

以 [ArduPilot 官方文档](https://ardupilot.org/dev/docs/building-setup-linux.html) 为准。

默认源码位置为 `$HOME/ardupilot`。目录不存在时：

```bash
sudo apt-get update
sudo apt-get install -y git gitk git-gui
git clone --recurse-submodules https://github.com/ArduPilot/ardupilot.git "$HOME/ardupilot"
```

目录已存在时先检查 remote、分支、commit、submodule 和工作树。保留所有改动；只有工作树干净且
快进更新安全时才更新，否则基于现状继续或报告，不重新克隆覆盖。

在 ArduPilot 仓库内执行官方前置脚本：

```bash
cd "$HOME/ardupilot"
Tools/environment_install/install-prereqs-ubuntu.sh -y
```

该脚本可能修改用户 profile。执行后检查实际 diff，不再重复手工添加官方脚本已经配置的 PATH。
按官方方式重新加载路径（重新登录后同样生效），再显式构建 SITL Copter：

```bash
. ~/.profile
```

```bash
cd "$HOME/ardupilot"
./waf configure --board sitl
./waf copter
```

验证生成物，并运行一个不解锁、不起飞的短时 Copter SITL 启动检查。JSON 模型必须显式指定：

```bash
test -x "$HOME/ardupilot/build/sitl/bin/arducopter"
cd "<本项目仓库根目录>"
mkdir -p runtime/sitl/install-check
cd runtime/sitl/install-check
timeout 15 "$HOME/ardupilot/build/sitl/bin/arducopter" -S --model JSON
```

应看到 `Starting SITL: JSON`、`JSON control interface set to 127.0.0.1:9002` 与
`SERIAL0 on TCP port 5760`；超时结束是预期行为。记录使用的 ArduPilot commit。

## 5. 安装 `ardupilot_gazebo`

插件编译以 [SITL with Gazebo 官方文档](https://ardupilot.org/dev/docs/sitl-with-gazebo.html)和
[上游 `ardupilot_gazebo` README](https://github.com/ArduPilot/ardupilot_gazebo) 为准。
本项目比赛场景使用 [Bob12315 的场景 fork](https://github.com/Bob12315/ardupilot_gazebo)，
包含 `cuadc2026_rescue.sdf`、固定下视相机参数和 payload bridge；这些不是上游标准 Iris 示例。
记录该 fork 的实际 commit，不要以只克隆上游仓库代替比赛场景安装。

安装 Harmonic 对应依赖：

```bash
sudo apt-get update
sudo apt-get install -y libgz-sim8-dev rapidjson-dev \
  libopencv-dev libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev \
  gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly gstreamer1.0-libav gstreamer1.0-gl \
  build-essential
```

默认工作区为 `$HOME/gz_ws`。仓库不存在时克隆；已存在时按上一节相同规则保护工作树：

```bash
mkdir -p "$HOME/gz_ws/src"
git clone https://github.com/Bob12315/ardupilot_gazebo.git \
  "$HOME/gz_ws/src/ardupilot_gazebo"
```

使用可重复执行的 out-of-tree 构建：

```bash
export GZ_VERSION=harmonic
cmake -S "$HOME/gz_ws/src/ardupilot_gazebo" \
  -B "$HOME/gz_ws/src/ardupilot_gazebo/build" \
  -DCMAKE_BUILD_TYPE=RelWithDebInfo
cmake --build "$HOME/gz_ws/src/ardupilot_gazebo/build" -j"$(nproc)"
```

把以下环境变量写入 `~/.bashrc`。添加前先搜索，保证每项只出现一次；不要反复追加重复行，
也不要覆盖已有同名路径：

```bash
export GZ_SIM_SYSTEM_PLUGIN_PATH="$HOME/gz_ws/src/ardupilot_gazebo/build:${GZ_SIM_SYSTEM_PLUGIN_PATH:-}"
export GZ_SIM_RESOURCE_PATH="$HOME/gz_ws/src/ardupilot_gazebo/models:$HOME/gz_ws/src/ardupilot_gazebo/worlds:${GZ_SIM_RESOURCE_PATH:-}"
```

项目启动脚本默认按仓库根目录解析相对路径，Gazebo 加载插件和资源仍需要上述两个环境变量。
新终端会自动读取 `~/.bashrc`；当前 shell 在启动测试前执行 `. ~/.bashrc`。

GStreamer 验证：

```bash
gst-inspect-1.0 x264enc rtph264depay h264parse rtph264pay avdec_h264
```

## 6. 联调验证

使用 `scripts/run_iris_gimbal_sitl.sh` 验证 Gazebo 与本机 Copter SITL。通过环境变量指定
实际安装路径，无需修改脚本。脚本当前 `GCS_HOST` 默认是队伍现场地址 `10.101.31.108`，
`VIDEO_HOST` 默认跟随它；本节安装检查必须显式覆盖为 `127.0.0.1`，避免把数据发往现场设备。
脚本默认使用仓库旁的 `../ardupilot` 和 `../gz_ws/src/ardupilot_gazebo`；参数文件和 world
从所选 Gazebo 仓库派生。所有相对路径覆盖按本项目仓库根目录解析。以下示例沿用前文
安装到用户主目录的布局，显式覆盖外部仓库位置；确认对应文件存在后运行：

```bash
cd "<本项目仓库根目录>"
. ~/.bashrc
mkdir -p runtime/sitl/install-check
PROJECT_REPO_ROOT="$PWD"
cd runtime/sitl/install-check
GCS_HOST=127.0.0.1 VIDEO_HOST=127.0.0.1 \
  ARDUPILOT_DIR="$HOME/ardupilot" \
  GZ_REPO_DIR="$HOME/gz_ws/src/ardupilot_gazebo" \
  bash "$PROJECT_REPO_ROOT/scripts/run_iris_gimbal_sitl.sh"
```

脚本会分别启动 Gazebo、JSON Copter、payload bridge 和 RTP relay。此次只判定 Gazebo 世界成功
加载、Copter 显示 `Starting SITL: JSON` 且 JSON 控制接口连接到 `127.0.0.1:9002` 为通过；不执行
arm、takeoff、Mission，也不向 RK3588 或任何真实飞控发送数据。验证结束后关闭本次启动的终端。
payload bridge 或 RTP relay 的单独报错不应掩盖 Gazebo/SITL 的核心启动结果，但应记录在最终报告。

辅助脚本内部会切换到 ArduPilot 源码目录启动 `sim_vehicle.py`，并显式使用
`--use-dir <本项目仓库>/runtime/sitl/iris-gimbal`。外层 `cd` 不改变该输出位置；
不会在 ArduPilot 源码目录生成 EEPROM、地形缓存和飞行日志。检查证据也保存到本项目 `runtime/`。

如果插件仓库现有世界、模型或参数文件名与项目文档不同，以实际 commit 为证据报告差异，不要
伪造缺失文件或随意下载第三方替代物。

## 7. 完成报告

最终报告必须包含：

- 操作系统、CPU 架构和图形会话情况；
- Conda 安装位置、`uav-dev` 状态；
- Gazebo 版本与 `shapes.sdf` 验证结果；
- ArduPilot 路径、分支、commit、Copter SITL 构建/启动结果；
- `ardupilot_gazebo` 路径、commit、构建/世界加载结果；
- GStreamer 验证结果；
- 新增或修改过的 apt 源、shell 配置和代理/镜像临时措施，以及恢复方法；
- 未完成事项、原始错误摘要和需要人工处理的内容；
- 下一步如何按照 [`docs/beginner/04_sitl_test.md`](../../beginner/04_sitl_test.md)
  连接 RK3588，且明确提醒实际 IP 不能照抄示例。

不要只报告“安装成功”。每一项都给出实际执行的验证命令、关键结果或失败证据。
