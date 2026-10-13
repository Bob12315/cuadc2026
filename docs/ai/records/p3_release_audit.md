# P3 发布准备审计

审计日期：2026-08-12。此记录只描述仓库审计结果；它不授予公开发布、删除文件或重写
Git 历史的权限。

资产清单、文件缺失项和下面的结论属于该次审计；后续新增模型、媒体或发布文件
必须重新审查，不能把本页当成当前完整 inventory。当前架构与部署模型偏差见
[当前架构](../architecture/current_architecture.md) 和 [`AGENTS.md`](../../../AGENTS.md)。

## 结论

**当前禁止公开发布。** P3 尚未完成。以下项目必须由维护者提供可核验的权利或联系信息，
不能由实现者猜测：版权主体和年份、全体现有权利人对 Apache-2.0 的同意、DCO/CLA、私密
安全报告渠道、Code of Conduct 执行联系人，以及模型/数据/图片/Logo 的再分发权。

运行可重复的基础审计：

```bash
python scripts/audit_release_readiness.py
python scripts/audit_release_readiness.py --strict
```

第二条命令在任一发布阻塞存在时必须失败；这正是预期行为，不能用忽略或软失败代替处置。

## 已审计范围与发现

| 范围 | 发现 | 发布处置 |
| --- | --- | --- |
| 当前模型 | `data/models/cuadc2026-fp16.rknn`、`gazebo_dataset-fp16.rknn` | 未找到训练数据、模型作者或再分发许可；公开发布前移除或补齐 `MODEL_CARD.md`/许可。 |
| 当前地形数据 | `data/terrain/S36E149.DAT` | 未找到来源和许可证；公开发布前移除或记录来源、版本、许可与 attribution。 |
| 当前硬件图片 | `docs/beginner/images/hardware/*.png` | 文件说明为购买记录截图，但没有作者、来源、截图中个人信息审查或再分发许可；公开发布前逐项审查或移除。 |
| Git 历史 | 发现多个旧 `.rknn`、`mav.tlog`、`eeprom.bin`、地形数据和媒体路径 | 必须检查对象内容、来源、个人/现场信息；未通过前禁止公开。 |
| 当前明文秘密 | 基础模式扫描未发现私钥、GitHub/AWS/Slack/Google 已知 token 格式 | 这不是高熵秘密扫描的替代；发布前仍须在完整历史使用经批准的秘密扫描器，并轮换任何已泄漏凭据。 |
| 依赖许可证 | `scripts/license_scan.py` 已修复缺少包名元数据时的崩溃；本机共享 Python 环境扫描到 2,754 个 distribution，其中 124 个许可证元数据为 `UNKNOWN` | 此结果不是锁定 App/YOLO 环境的许可证清单；发布候选必须在每个锁定环境运行，所有 `UNKNOWN` 项需要查阅上游许可证并记录处置。 |
| 发布文件 | 根目录未找到 `LICENSE`、`NOTICE`、`CONTRIBUTING.md`、`SECURITY.md`、`CODE_OF_CONDUCT.md`、第三方/素材/模型清单 | 维护者作出 D-09～D-12 决策后再创建正式文件；不得以占位符发布。 |

`.gitignore` 已补充 telemetry、EEPROM 和常见飞行日志产物规则；它不影响历史中已有对象，
也不能证明素材可公开。

## 历史重写

历史中存在需审查对象，但本次**未执行**历史重写。若需移除，必须先满足计划中的全部条件：
精确对象清单、可恢复备份、协作者冻结 push、独立克隆演练、迁移记录、凭据轮换和维护者再次
明确授权。仅从当前工作树删除文件不足以清除历史。

## P3-3 可复用验证证据

P0～P2 已提供以下可重复验证命令；发布候选必须在目标环境再次执行并保存结果：

```bash
python -m pytest -q tests/unit tests/contracts tests/integration tests/sitl
python scripts/validate_architecture_boundaries.py
python scripts/validate_action_missions.py
python scripts/validate_p0_sitl.py
python scripts/validate_p2_field_reference.py
```

2026-08-13 的架构收尾已在 Linux x86_64 和隔离的 RK3588 Linux aarch64 副本上
各通过 708 项测试，RKNNLite 2.3.2 也成功加载当前 FP16 模型。这不等于完整的
发布候选安装、摄像头/NPU 推理冒烟或全部 SITL 失效场景记录，这些仍需在正式发布前补齐。

## 维护者决策表

| 决策 | 发布前所需证据 |
| --- | --- |
| 版权主体、年份、Apache-2.0 同意 | 全部现有主要贡献者/所属组织的书面确认。 |
| DCO 或 CLA | 选择一种机制并在 `CONTRIBUTING.md` 写出可执行规则。 |
| 安全报告渠道 | 可管理的私密地址或平台配置，并由维护者实际接收测试。 |
| CoC 执行联系人 | 可管理的联系地址及执行责任人确认。 |
| 模型/数据/图片/Logo 权利 | 每项来源、作者/权利人、许可证、修改、个人信息审查、是否可再分发。 |
