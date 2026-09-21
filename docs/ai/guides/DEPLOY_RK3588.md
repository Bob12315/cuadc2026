# RK3588 Deployment Guide for AI Agents

## 目标

本文是 AI Coding Agent 在真实 RK3588 上部署本项目时的执行规范。目标是在不产生飞行或
舵机动作的前提下，完成仓库、app、YOLO/RKNN、硬件配置、MAVLink 只读链路、systemd
服务和 Web UI 的部署与验证。

开始前必须同时阅读：

1. 根目录 `AGENTS.md`；
2. [`current_architecture.md`](../architecture/current_architecture.md)；
3. [`deprecated_paths.md`](../architecture/deprecated_paths.md)；
4. [`docs/developer/safety.md`](../../developer/safety.md)；
5. 本文。

不熟悉真实无人机硬件的 Agent 只能完成软件检查和部署，不能代替用户确认电压、引脚、
接线、飞控校准或现场安全。

## 支持环境

推荐环境：

```text
Ubuntu 22.04
Linux ARM64
RK3588
Python 3.10
```

其他 Linux ARM64 系统允许尝试，但部署前必须先确认板卡 BSP、内核、NPU 驱动、
RKNNLite 和 Python 环境兼容。不要承诺 Ubuntu 24.04、Debian、Armbian 或其他发行版无需
适配即可运行。

如果 `uname -m` 返回 `x86_64`，立即停止 RK3588 实机部署并报告环境不符。不得为绕过
平台限制增加 CUDA、PyTorch GPU 或 x86 推理后备路径。

## 已验证的 NanoPC-T6 参考基线（2026-08-30）

以下是对已有正常运行板卡 `~/cuadc2026` 的**只读**盘点结果。它是排障和部署时的参考，
不是可以跳过前述检查的硬编码前提；尤其不要把其中的环境名、IP 或视频来源套用到另一块
板卡。

| 项目 | 参考基线 | 部署含义 |
| --- | --- | --- |
| 板卡 / 系统 | FriendlyElec NanoPC-T6；`rockchip,rk3588`；Ubuntu 22.04.4；Linux `aarch64` | `uname -m`、`/proc/device-tree/compatible` 和发行版均符合要求。 |
| NPU | `rknpu` 内核模块和 `RKNPU` platform driver 可见 | 不要求每个 BSP 都暴露 `/dev/rknpu*`；应按多个可用节点检查。 |
| Conda | `~/miniconda3`；Python 3.10.20 | 已运行的环境名是 `app` 与 `yolo`，不是安装脚本的默认名。 |
| YOLO 依赖 | `yolo` 环境的 `rknn-toolkit-lite2==2.3.2` | 版本用于比对，不可据此在其他 BSP 上盲目降级或升级。 |
| 服务 | `uav-app.service`、`uav-yolo.service` 为 enabled/running；app 的 `ExecStart` 含 `--send-commands false` | 服务存活和 SEND OFF 已证实；不得因检查而重启或重写现有 unit。 |
| 视频输入 | `config/yolo.yaml` 为 UDP H.264/RTP 端口 `5600`，由 `udp_gst_bridge_helper.py` 接收 | 此部署不依赖 `/dev/video0`；缺少 USB/V4L2 设备并不构成故障。 |
| Web | app 监听 `0.0.0.0:8080`，`/api/status` 可读 | 访问控制仍由配置和操作者口令决定，不能因监听 LAN 而关闭认证。 |

该快照中没有 MAVLink heartbeat 或新的感知 UDP 数据，所以 Web 状态中的 telemetry 和
perception 为 `disconnected`/`stale`，且没有运行 Action Mission。这只说明检查时上游飞控和
视频发送端未接入或未发送，**不**能由此推断飞控、相机或 NPU 已完成端到端验证。部署报告
必须分别写出“服务存活”和“数据链路已验证”。

## 安全边界

部署全过程保持：

```yaml
executor:
  send_commands: false
```

禁止：

- 解锁或锁定电机；
- 起飞、降落或切换飞行模式；
- 发送速度、航点、位置或姿态控制；
- 控制投放舵机；
- 启动会产生真实飞行动作的 Action 或 Mission；
- 为验证“是否连接成功”而临时打开 SEND。

允许进行以下只读或无动作验证：

- 读取 telemetry、heartbeat、GPS、姿态和日志；
- 启动 YOLO 和 Web UI；
- 检查 RKNN 模型加载、视频和目标检测；
- 查看服务、端口和系统状态。

首次启动前应提醒用户拆除螺旋桨和正式载荷。允许 Agent 通过交互式 SSH 终端直接执行部署所需的
sudo 命令并触发密码提示。需要认证时，由用户在终端提示中输入，
或使用不会向 Agent 暴露内容的系统安全输入通道；不得从对话读取、保存、回显或提交密码，
也不得把密码拼进命令、环境变量、日志或文件。SSH 私钥、Token、Wi-Fi 密码等秘密信息遵循
相同规则。

## 部署前检查

先只读收集证据，不安装、不修改、不启动服务。至少检查：

```bash
uname -a
uname -m
cat /etc/os-release
df -h
free -h
ip addr
ip route
hostname -I
git --version
python3 --version
conda --version
conda info --envs
```

按板卡厂商可用的方法检查 NPU driver/runtime，不要假定所有 BSP 都提供相同命令。检查
相机时可读取：

```bash
v4l2-ctl --list-devices
ls -l /dev/v4l/by-id/ 2>/dev/null || true
```

还应在不改动服务的情况下检查实际运行配置和已有服务。这样可以先发现“文档默认值”与
现有板卡的差异：

```bash
tr '\0' ' ' </proc/device-tree/compatible; echo
for path in /dev/rknpu /dev/rknpu0 /sys/module/rknpu /sys/bus/platform/drivers/RKNPU; do
  test -e "$path" && ls -ld "$path"
done

cd ~/cuadc2026
git status --short --branch
systemctl --user --no-pager --full status uav-app.service uav-yolo.service || true
systemctl --user cat uav-app.service uav-yolo.service || true
ss -lntup
```

先读取 `config/yolo.yaml` 的 `source` 再选择相机检查方式：本地 V4L2 source 才检查对应的
`/dev/video*` 或 `/dev/v4l/by-id/*`；纯数字的 UDP source（例如 `5600`）应检查该 UDP
端口、`udp_gst_bridge_helper.py` 子进程和视频发送端。不能把 `/dev/video0` 缺失当作 UDP
视频链路的故障。

部署前报告以下三类信息：

- 已满足；
- 缺失，可以安全安装；
- 需要用户或硬件负责人确认。

不得根据模糊信息猜板卡型号、相机、MAVLink 连接方式、UART、电源或引脚。

## 仓库获取与状态检查

先确定用户指定的仓库路径。如果仓库不存在，可以在用户确认目标目录后克隆：

```bash
git clone https://github.com/Bob12315/uav_system-rk3588.git
cd uav_system-rk3588
```

如果仓库已经存在，不得重新克隆覆盖。必须先运行：

```bash
git status --short --branch
git branch --show-current
git remote -v
git log -1 --oneline
```

保留所有本地改动。禁止自动执行 `git reset --hard`、`git clean -fd`、强制 checkout 或
其他可能丢失用户工作的命令。更新仓库前先说明将改变什么，并处理本地修改与目标分支的
关系。

确认这些文件存在：

```text
config/app.yaml
config/telemetry.yaml
config/yolo.yaml
data/models/cuadc2026-fp16.rknn
scripts/install/install_app_env.sh
scripts/install/install_yolo_env.sh
scripts/deploy/install_systemd_user_services.sh
scripts/healthcheck/check_rk3588.sh
```

还要解析，而不是只检查，`config/yolo.yaml` 的 `model_path`。项目的规范 FP16 部署模型是
`data/models/cuadc2026-fp16.rknn`；不得把历史 INT8 模型设为默认。当前仓库配置和 NanoPC-T6
参考基线却选择了另一个 FP16 文件 `gazebo_dataset-fp16.rknn`，且两者哈希不同。这是必须在
新实机部署前由硬件/视觉负责人裁决的配置差异，不能由部署 Agent 擅自切换。无论保留还是
迁移，都要记录实际路径、哈希、检测类别和实测验证证据；不得因为文件名不同就在运行中的
板卡上自动改写 `model_path`。

## app 环境

项目使用独立的 app Conda 环境和 Python 3.10。安装脚本默认环境名为 `uav-app`，但环境名
是可覆盖的部署参数，**不是**对已有板卡的事实断言。若 Conda 不存在，应选择适用于
Linux ARM64 的可靠发行版并参考其官方安装方式；不要覆盖已有 Conda 安装或修改用户全局
Python。

先从 `systemctl --user cat uav-app.service` 或 `conda info --envs` 确定已有服务实际使用的
Python。NanoPC-T6 参考板卡使用环境 `app`。新环境可保留默认名；更新已有 `app` 环境时
显式传入其名字：

```bash
# 新建/标准部署（默认环境名 uav-app）
bash scripts/install/install_app_env.sh

# 已有 NanoPC-T6 风格环境（只在确认需要更新环境时）
APP_ENV_NAME=app bash scripts/install/install_app_env.sh
```

脚本会检查 Linux ARM64、环境名和 Python 版本，通过 `environment-app.yml` 单次安装
`requirements/app.txt`，然后执行健康检查。不要自行重新设计 requirements 或把 app 与 yolo
合并为一个环境。

根据实际环境名验证，而不是把 `uav-app` 写死：

```bash
APP_ENV_NAME=app  # 新部署默认改为 uav-app
APP_PYTHON="$(conda run -n "$APP_ENV_NAME" python -c 'import sys; print(sys.executable)')"
"$APP_PYTHON" -m app.main --help
"$APP_PYTHON" -m tools.telemetry_debug --help  # development diagnostic only
```

验证后再次确认 `config/app.yaml` 中 `executor.send_commands` 为 `false`。

## YOLO / RKNN 环境

项目使用独立的 YOLO Conda 环境、Python 3.10、RKNNLite 和 RK3588 NPU。安装脚本默认环境
名为 `uav-rk3588-yolo`；NanoPC-T6 的已运行服务使用的是历史环境名 `yolo`。和 app 环境
一样，先读取 unit 的 `ExecStart`，再决定是否需要更新环境：

```bash
# 新建/标准部署（默认环境名 uav-rk3588-yolo）
bash scripts/install/install_yolo_env.sh

# 已有 NanoPC-T6 风格环境（只在确认需要更新环境时）
YOLO_ENV_NAME=yolo bash scripts/install/install_yolo_env.sh
```

安装后根据实际环境名验证依赖与当前配置的模型路径：

```bash
YOLO_ENV_NAME=yolo  # 新部署默认改为 uav-rk3588-yolo
YOLO_PYTHON="$(conda run -n "$YOLO_ENV_NAME" python -c 'import sys; print(sys.executable)')"
"$YOLO_PYTHON" -c "import cv2, numpy, yaml; from rknnlite.api import RKNNLite; print(RKNNLite)"
test -f data/models/cuadc2026-fp16.rknn
"$YOLO_PYTHON" - <<'PY'
from pathlib import Path
import yaml

config_path = Path("config/yolo.yaml")
model_path = (config_path.parent / yaml.safe_load(config_path.read_text())["model_path"]).resolve()
assert model_path.suffix == ".rknn" and model_path.is_file(), model_path
print(f"configured RKNN model: {model_path}")
PY
```

不得把历史 INT8 模型切回默认部署模型。只有取得检测验证证据和负责人确认后，才可将实际
`model_path` 迁移到规范的 `data/models/cuadc2026-fp16.rknn`；部署 Agent 不能擅自改写当前
可工作的不同 FP16 配置。

`check_rk3588.sh` 的完整 NPU 空白帧推理会占用 NPU。它适合首次、服务未运行时的无动作
验收；若 `uav-yolo.service` 已在运行，不要为了“再确认一次”并行启动第二个 RKNN runtime。
此时应检查 unit、日志、端口和只读状态；需要停止服务做离线 NPU 验证时，必须先获得用户
明确授权。

如果 RKNNLite、板卡 NPU runtime 与 BSP 明显不匹配，停止并报告已检测版本、错误日志和
所需板卡资料。不得尝试随机安装多个不明来源版本，也不得添加 CUDA、PyTorch GPU 或 x86
fallback 作为实机推理路径。

## 硬件配置

Agent 可以自动检测：

- 网络地址、路由和监听端口；
- Linux 视频设备与稳定设备路径；
- 已有配置和服务状态；
- 当前能够观察到的 MAVLink 数据包或 heartbeat。

以下内容不能猜：

- 相机真实设备、接口、格式和分辨率；
- MAVLink 使用 UDP、UART、TCP 还是 Ethernet；
- 飞控 IP、目标地址和 UDP 方向；
- 串口路径、波特率和 UART 电平；
- SERVO 输出、舵机 PWM；
- 电源电压、极性和硬件接线。

缺少信息时停止相应配置并询问用户。修改前展示计划，修改后展示 diff。硬件配置通常只
涉及：

```text
config/yolo.yaml
config/telemetry.yaml
```

不得顺便修改 Action Mission、飞行高度、速度、投放参数或业务代码。

视频 source 是输入协议选择，不是“总是接 USB 相机”的开关。参考板卡的数字 source `5600`
会启动仓库内的 GStreamer bridge，并监听 UDP `5600`；它没有可用 `/dev/video0` 是正常的。
换成 V4L2、RTSP 或完整 pipeline 前，必须由硬件负责人确认实际输入格式、端口和上游发送端。

## MAVLink

根据用户确认的真实链路配置 `config/telemetry.yaml`。部署阶段的目标仅是稳定只读获取
飞控状态：

- heartbeat；
- armed；
- mode；
- GPS；
- attitude；
- local position（飞控提供时）。

连接前后均确认 `config/app.yaml`：

```yaml
executor:
  send_commands: false
```

连接成功不代表允许飞行，不得继续测试模式、起飞、航点、速度、降落或舵机命令。

还必须报告 `data_source` 与 `active_source`，不能只因 UDP `14550` 正在监听就宣称连上真机。
NanoPC-T6 参考快照中二者均为 `sitl`，且状态接口显示 heartbeat 缺失；这与“仅服务进程在
运行”是两件事。只有在已确认的上游飞控正在发送时，才把 `connected=true`、新鲜 heartbeat、
GPS 和 attitude 记录为通过。

## systemd

先检测 app 和 YOLO 环境中 Python 的真实绝对路径，不要假定 Conda 位于 `~/anaconda3`。
如果 unit 已存在且运行中，先比对它的 `WorkingDirectory`、`ExecStart` 和 SEND 参数；仅为
检查而执行安装脚本会覆盖 unit 文件，因此不要这样做。

```bash
systemctl --user cat uav-app.service uav-yolo.service
systemctl --user show uav-app.service uav-yolo.service \
  --property=ActiveState --property=ExecStart --property=WorkingDirectory --no-pager
```

新建服务或已获准重建服务时，优先使用仓库脚本。环境名和 Python 路径都必须从实际环境
解析：

```bash
# NanoPC-T6 现有环境使用 app/yolo；新部署显式使用 uav-app/uav-rk3588-yolo。
APP_ENV_NAME=app             # 新部署改为 uav-app
YOLO_ENV_NAME=yolo           # 新部署改为 uav-rk3588-yolo
APP_PYTHON="$(conda run -n "$APP_ENV_NAME" python -c 'import sys; print(sys.executable)')"
YOLO_PYTHON="$(conda run -n "$YOLO_ENV_NAME" python -c 'import sys; print(sys.executable)')"
APP_ENV_NAME="$APP_ENV_NAME" YOLO_ENV_NAME="$YOLO_ENV_NAME" \
APP_PYTHON="$APP_PYTHON" YOLO_PYTHON="$YOLO_PYTHON" \
bash scripts/deploy/install_systemd_user_services.sh --dry-run
```

检查 dry-run 输出中的 `WorkingDirectory`、`ExecStart` 和 Python 路径。正确后安装，但先
不启动：

```bash
APP_ENV_NAME="$APP_ENV_NAME" YOLO_ENV_NAME="$YOLO_ENV_NAME" \
APP_PYTHON="$APP_PYTHON" YOLO_PYTHON="$YOLO_PYTHON" \
bash scripts/deploy/install_systemd_user_services.sh
```

检查：

```bash
systemctl --user status uav-app.service
systemctl --user status uav-yolo.service
```

只有确认 SEND OFF、螺旋桨和载荷已移除后，才可以启动服务做只读验证。是否启用开机
启动和 linger 应告知用户，并根据实际运行需求决定。

脚本不带 `--enable-now` 时不会启动服务；已有服务保持现状。需要无人登录后继续运行时，
在获得用户同意后执行 `sudo loginctl enable-linger "$USER"`，并用
`loginctl show-user "$USER" -p Linger` 记录结果。

## 局域网浏览器访问、白名单与“连接不安全”

`web_host: "0.0.0.0"` 只表示 app 在所有本地接口监听，**不**表示任何浏览器都应被允许
登录。浏览器使用的站点地址必须经过 `config/app.yaml` 的 `ui` 白名单校验：

| 字段 | 匹配对象 | 正确形式 |
| --- | --- | --- |
| `allowed_hosts` | 浏览器地址栏中 Web UI 的主机/IP | 不带协议和端口，例如 `10.101.31.109`。 |
| `allowed_origins` | 浏览器请求的 Origin | 带 `http://` 和端口，例如 `http://10.101.31.109:8080`。 |
| `allowed_networks` | 可接受的一组私有 IP 主机/IP Origin | 仅受信任的 CIDR，例如 `10.101.31.0/24`。 |
| `auto_allow_current_private_network` | 当前默认路由的私有网段 | DHCP 会变更时可设为 `true`；它不是公网访问开关。 |

若需要从 Tailscale 或任意字面量 IPv4/IPv6 地址访问，可显式配置
`allowed_networks: ["0.0.0.0/0", "::/0"]`。这仍保留登录认证、CSRF 与 SEND 门，但会让
任意网络地址的浏览器进入登录界面；DNS 主机名仍须另行加入 `allowed_hosts`。

例如，RK3588 位于受信任的 `10.101.31.0/24` 操作网、浏览器访问
`http://10.101.31.109:8080` 时，最小配置可为：

```yaml
ui:
  web_host: "0.0.0.0"
  web_port: 8080
  auth_required: true
  allowed_hosts: ["127.0.0.1", "localhost", "10.101.31.109"]
  allowed_origins: ["http://127.0.0.1:8080", "http://localhost:8080", "http://10.101.31.109:8080"]
  allowed_networks: ["10.101.31.0/24"]
  auto_allow_current_private_network: true
```

实际 IP、DNS 名称、端口和网段必须按现场确认；不要复制示例，也不要使用 `*`。实现会拒绝
通配符 Host/Origin，且非回环监听必须保持 `auth_required: true` 并从
`UAV_WEB_OPERATOR_PASSWORD` 或权限受限的 `UAV_WEB_OPERATOR_PASSWORD_FILE` 获取口令。口令
不得写入 YAML、命令历史、日志或版本库。

地址栏提示“连接不安全”与白名单错误不同：当前 Web UI 的浏览器 Origin 校验只接受
`http://<host>:<port>`，所以从局域网用 HTTP 访问时，浏览器标记为不安全是预期行为。它不应
通过关闭认证、扩大为公网网段、忽略证书警告或开启 SEND 来处理。仅可在受信任私有操作网中
使用；如需要 HTTPS、跨网段或公网访问，必须先做单独的反向代理/TLS 与 Origin 支持的架构
评审，不能把 HTTPS URL 直接加入当前 `allowed_origins`。

按浏览器或 API 返回结果区分问题：

| 现象 | 含义 | 最小处理 |
| --- | --- | --- |
| 页面可打开，地址栏显示“连接不安全” | HTTP 没有 TLS，不是白名单拒绝 | 确认仅在可信私有网使用；不改白名单。 |
| HTTP 400，`host not allowed` | 地址栏主机/IP 不在 Host 或私有网段白名单 | 仅加入实际 Web UI 主机/IP，或确认所需的私有 CIDR。 |
| HTTP 403，`origin not allowed` | 完整 `http://host:port` Origin 未获准 | 加入与地址栏完全一致的 HTTP Origin。 |
| HTTP 401 或登录失败 | 认证/会话问题 | 确认操作者口令由受限环境变量或文件提供；不要把口令写进配置。 |

白名单配置只在 app 启动时加载。修改前先展示 diff；修改后，只有在 SEND OFF、螺旋桨和载荷
已移除且用户明确同意时，才可以重启 `uav-app.service` 使其生效。重启并不能解决浏览器的
HTTP “连接不安全”提示。

## 首次只读启动

首次启动目标只有：

```text
YOLO 正常
Web UI 正常
telemetry 正常
SEND OFF
```

仅对尚未启动、且已满足安全前提的服务，可以启动用户服务：

```bash
systemctl --user start uav-yolo.service uav-app.service
systemctl --user --no-pager --full status uav-yolo.service uav-app.service
```

对已在运行的服务只观察状态和日志，不要为验证而 restart。任何情况下都不打开 Action Lab
发送、不启动 Mission。若出现异常，先收集日志；停止对应服务也需要用户明确授权。

## 验证

首次启动且 YOLO 服务尚未运行时，可执行完整硬件健康检查。对运行中的 YOLO 服务，跳过会
创建第二个 NPU runtime 的完整检查，执行其余只读验证即可：

```bash
# 仅在 uav-yolo.service 未运行时；按实际环境名传参。
APP_ENV_NAME=app YOLO_ENV_NAME=yolo bash scripts/healthcheck/check_rk3588.sh

ss -lntu
APP_ENV_NAME=app  # 新部署默认改为 uav-app
APP_PYTHON="$(conda run -n "$APP_ENV_NAME" python -c 'import sys; print(sys.executable)')"
curl -fsS http://127.0.0.1:8080/api/status | "$APP_PYTHON" -c '
import json, sys
status = json.load(sys.stdin)
assert status["controllers"]["send_commands"] is False
assert status["action_mission"]["running"] is False
for key in ("link", "drone", "perception_status"):
    print(f"{key}: {status[key]}")
print("SEND OFF and no Action Mission")
'
journalctl --user -u uav-app.service -n 150 --no-pager
journalctl --user -u uav-yolo.service -n 150 --no-pager
```

验证清单：

- app 与 yolo service 正常且没有持续重启；
- Web UI 可访问，状态接口明确显示 SEND OFF 和未运行 Mission；
- 对首次离线验收：RKNN 模型可加载、runtime 可初始化，且健康检查 `failures=0`；
- 对接入视频发送端后：YOLO 视频可访问，`perception_status.stale=false`，并有新鲜检测结果；
- 对接入飞控后：heartbeat、GPS、姿态可只读观察，`link.connected=true` 且状态不 stale；
- `executor.send_commands=false`；
- 没有发送动作，也没有运行 Mission；
- 日志中没有持续异常。

`systemd` 的 `active (running)` 只证明进程存活，不能代替后三项数据面验证。没有接入视频或
飞控时，`perception_status.stale=true` 或 `link.connected=false` 是应报告的未验证状态，不应通过
改端口、开启 SEND 或启动 Mission 来“修复”。端口应根据当前配置解释，不要只因为某个常见
端口没有监听就改写配置。

## 故障处理原则

遇到问题必须遵循：

```text
收集证据
→ 判断故障层级
→ 提出最小修复
→ 修复
→ 再验证
```

建议排查顺序：

```text
系统 / 电源
→ 文件 / Git
→ Python / Conda
→ NPU / RKNN
→ 相机
→ 网络
→ MAVLink
→ systemd
→ app / YOLO
→ Web UI
```

每次修复前说明证据、将修改的文件和回滚方法。不要用重新安装掩盖明确的配置、权限、
路径、端口或版本问题。

## 禁止事项

- 不得重装系统作为默认排错方式；
- 不得随意删除 Conda 环境；
- 不得运行 `git reset --hard` 或 `git clean -fd`；
- 不得覆盖用户已有配置和 Git 改动；
- 不得恢复旧 mission/stage/control 架构；
- 不得在 Action 或感知代码中绕过正式发送链；
- 不得根据 `/dev/videoX` 编号、线色或模糊照片猜硬件；
- 不得把秘密信息写入命令输出、日志、配置或提交；
- 不得开启 SEND 或执行任何真实飞行动作。

## 完成报告格式

部署结束后按以下格式报告。无法确认的项目写明原因，不得伪造成功：

```text
系统：
- OS：
- Kernel：
- Architecture：
- RK3588/NPU：

仓库：
- Path：
- Branch：
- Commit：
- Git status：

app：
- Environment：
- systemd / Web status：
- SEND / Mission status：

YOLO：
- Environment：
- RKNNLite：
- Model：
- Video source：
- systemd status：
- Fresh perception verified：

MAVLink：
- Connection：
- Heartbeat：
- GPS：
- Attitude：
- SEND：

数据链路结论：
- 服务存活：
- 感知上游已验证 / 未接入：
- 飞控上游已验证 / 未接入：

Web UI：
- URL：
- Status：

systemd：
- uav-app：
- uav-yolo：

需要人工处理：
- ...

未解决问题：
- ...
```
