# Platform Adapter 批量执行状态（2026-08-16）

本文记录用户将执行范围从 PA-00 扩展到“全部任务”后的实际执行状态。任务是否完成仍只以
[`platform_adapter_interface_refactor_plan.md`](../plans/platform_adapter_interface_refactor_plan.md) 第 14 节的复选框和
完成记录为准；本文不替代验收，也不把本地单元测试结果冒充 SITL 或目标硬件证据。

本文的“本轮/当前”均指 2026-08-16 执行会话；Git 环境、测试数字、设备可用性和
产品输入阻塞应在新会话重新核对。当前 production 路径见
[当前架构](../architecture/current_architecture.md)。

## 1. 修改前基线与风险

修改前事实已冻结在
[`platform_adapter_interface_baseline.md`](platform_adapter_interface_baseline.md)：目录没有 Git 元数据，
`config/app.yaml` 的 `executor.send_commands` 为 `false`，未启动 app、YOLO、SITL 或 Web，未连接 MAVLink
端点或 RK3588。主要风险是 production `source=test` fail-open、一次性命令的“sent”语义不实、legacy/v2
writer ownership 不明确、continuous stop 缺少可信 write gate/barrier、ACK 关联不足、YOLO session/录像
actual state 缺失、Field live reference/多写入源，以及 event/audit/blackbox 对具体实现的耦合。

## 2. 本轮已实现的本地切片

- PA-01～PA-06：确定性 fake、显式 Vehicle state/command 分离、标准库共同契约、原子 Vehicle snapshot、
  typed command lifecycle、shadow CommandBroker；对应任务已按计划完成记录勾选。
- PA-07～PA-10 readiness：legacy/v2 XOR、typed MAVLink encoder/adapter、单 writer owner、canonical
  submission/cancel receipt、SafetyStopBarrier/write gate、ACK correlation/router、completion observer 与独立
  transport/ACK/completion 状态轴。所有 production cancel caller 使用 versioned canonical request；legacy
  裸 shape 在 Broker fail closed。
- PA-11～PA-14 readiness：YOLO v2 HELLO/session/tombstone/TTL/size gate、原子 perception frame、capability
  negotiation、typed target/recording command、target-session ACK/dedupe、recorder actual-state/硬超时；v1 fallback
  不伪报 ACK 或 RECORDING，YOLO 仍不接触 MAVLink。正式 `PerceptionPort`/`VisionCommandPort` 已按计划签名，
  loopback receiver 的原子 wait/session-change 以及 recorder write/flush failure 均有执行测试。
- PA-15～PA-17 readiness：单一只读 profile repository、immutable FieldReferenceSnapshot/ReferenceVersion、
  stale command admission/pre-write guard、typed GpsObservation 与 calibration transaction、单次原子 commit；
  FieldService 不再主动拉 telemetry，RuntimeContextBuilder 只投影已提交 snapshot。`_active_profile_id`、builder
  独立 calibration metadata 和 legacy apply/restore writer 已删除；profile 与全部标定 diagnostics 由同一个
  committed snapshot 提供。
- PA-18～PA-20 readiness：typed OperationalEvent/Audit/CycleRecordEnvelope、sink 隔离、有界异步 JSONL audit、
  有界 cycle recorder/store 和 v1 projector。补充了慢 sink/overflow、稳定 cursor、inline secret redaction、
  receipt 上限、cycle golden、session reset、rotation/retention、start barrier timeout 与 runtime 目录边界测试。
  新 cycle recorder 未接 production；旧 blackbox writer 仍是唯一 production cycle writer。

以上 readiness 表示代码与离线测试已落地，不表示任务验收完成。PA-07～PA-10 的强制 SITL 证据缺失，
PA-15～PA-17 又依赖未验收的 PA-08；PA-18～PA-20 仍需按各任务完整故障矩阵补齐证据。

## 3. 实际验证

使用 Codex bundled CPython 3.12.13，且未启动任何生产服务：

```text
PASS  python -m pytest -q tests
      789 passed, 3 skipped in 4.55s
SKIP  test_yolo_rknn_detector.py、test_mjpeg_stream.py、test_raw_frame_recorder.py：
      当前解释器无目标依赖 cv2；recorder 状态机另有不依赖 cv2 的执行测试
PASS  python scripts/validate_architecture_boundaries.py
PASS  python scripts/validate_action_missions.py
PASS  python -m ruff check scripts/validate_architecture_boundaries.py scripts/license_scan.py
PASS  python -m mypy --follow-imports=skip scripts/validate_architecture_boundaries.py scripts/license_scan.py
PASS  python scripts/license_scan.py
PASS  python -m compileall -q app application contracts execution field fusion missions
      observability telemetry_link web_ui yolo_app tests
BASELINE  python -m ruff check app application contracts execution field observability
          telemetry_link yolo_app tests
          313 errors（repo-wide 既存格式/静态债务；未作为本轮安全切片自动大改）
BASELINE  python -m mypy app application contracts execution field observability telemetry_link yolo_app
          --ignore-missing-imports --follow-imports=silent
          314 errors in 52 files（既存 typing/import fallback 债务）
```

此前裸 `pytest` 会误收集已删除的历史测试，其中引用了已删除且禁止恢复的
`FieldReferenceService`、旧 GPS sequence/Stage 路径，并在 collection 阶段失败。正式测试根仍由
`pytest.ini: testpaths = tests` 固化；不得恢复 deprecated stack。

安全边界复核：

```text
config/app.yaml: executor.send_commands: false
未启动 app/SITL/YOLO/Web
未连接真机、SITL MAVLink 或 RK3588
未执行真实或仿真飞行命令
未生成本任务 runtime 飞行/录像/blackbox 数据
```

## 4. 不能越过的验收门禁

1. PA-07～PA-10 明确要求 test-only SITL gate，其中 PA-08 要验证 BODY velocity revoke 后的 wire/barrier
   ordering，PA-10 要验证 arm/takeoff/mode/position/land lifecycle。这需要在隔离 SITL 配置中允许发送；与本轮
   “不打开 SEND”约束冲突，因此这些任务必须保持未完成。
2. PA-21 的硬前置是 PA-00～PA-20 已验收且 `AR-26 / CF-00～CF-28` 全部完成。当前 PA-07～PA-10 未验收，
   所以不得启动 CF-00，也不得推进 PA-21～PA-31；跳过此前置会制造第二套核心 owner，违反计划本身。
3. PA-15 的验收写明“三份正式 profile 内容逐字段一致”，当前 `config/field_profiles/` 只有一份正式
   `competition_runtime.json`，`runtime/field_profiles/` 没有另外两份正式 profile。
   未经产品输入不能凭空制造另外两份正式场地 profile，因此该验收项保持阻塞。
4. PA-29 明确需要获准的 RK3588 目标硬件门禁；本轮禁止连接真机，无法产生该证据。
5. PA-26 的 soak/hit-counter、PA-27～PA-30 的兼容删除以及 PA-31 最终审计都依赖上述门禁，不能通过离线
   单测替代或提前删除 rollback path。

结论：已完成所有在“不连接真机、不启用 SEND、不越过任务前置”条件下可安全执行的本地实现与验证；
剩余工作是明确的外部证据/前置依赖阻塞，不得标记为完成。
