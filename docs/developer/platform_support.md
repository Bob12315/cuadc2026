# 平台与环境支持

通用 app（Action Mission、遥测、Web UI）支持 Linux x86_64 与 Linux ARM64；它不安装
RKNNLite 或 OpenCV。app 可以用 `--no-yolo-udp` 运行，此时 Web 状态显示
`perception_source=disabled`，视觉 Action 会因无效感知而安全失败，非视觉 Action 不受影响。

板端 YOLO 是可选的第二个进程，只支持 Linux ARM64 的 RK3588，使用
`rknn-toolkit-lite2` 和 `data/models/cuadc-fp16.rknn`。它不提供 x86、CUDA、PyTorch
或 GPU 回退路径。

```bash
# 通用 App，支持 Linux x86_64 / ARM64
bash scripts/install/install_app_env.sh
conda run -n uav-app bash scripts/healthcheck/check_app.sh

# 本地开发（App + Python 测试工具 + Node.js）
conda env create -f environment-dev.yml

# 仅 RK3588 实机：安装脚本会验证板型、NPU 与模型
bash scripts/install/install_yolo_env.sh
bash scripts/healthcheck/check_rk3588.sh
```

三个 `environment-*.yml` 只负责 Conda/Python/Node 环境并引用 `requirements/*.txt` 中的唯一
Python 依赖清单。不要在环境创建完成后再次安装同一个 requirements profile。

Python 3.10 是当前固定的受支持小版本；升级 Python 需要单独验证 app、MAVLink、RKNN
Runtime 和实机推理，不能借由通用环境升级隐式完成。
