# Field Reference：schema v3 运行时 GPS 场地参考

FIELD 使用 `+Y` 前方、`+X` 右方、`+Z` 向上。唯一受支持的配置是
`config/field_profiles/competition_runtime.json`。

Web UI 的 Competition Field Setup 流程为：操作者输入 WGS84 forward marker B；无人机在
起飞点静止采样 GPS；系统验证质量、样本数和离散度，以中位 GPS 位置建立原点 A，使用 A→B
计算 heading，并自动 apply/freeze。此过程不启动 Mission，也不发送飞行命令。

schema v3 只支持 FIELD→GPS/GLOBAL。它**不建立 LOCAL_NED 场地原点**，因此
`is_ready_for_field_to_local=false` 是正确且安全的状态；不得把 GPS 原点伪造为 local origin。

当前 API（路由定义见 `web_ui/routers/field.py`）：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/field-reference/status` | 当前参考和采样状态 |
| POST | `/api/field-reference/reset` | 重置参考 |
| POST | `/api/field-reference/freeze` | 冻结已确认参考 |
| POST | `/api/field-reference/runtime-sampling/start` | 输入 `forward_marker_lat/forward_marker_lon`，启动 Competition Field Setup |
| POST | `/api/field-reference/runtime-sampling/finalize` | 显式结束可提交的采样 |
| POST | `/api/field-reference/runtime-sampling/cancel` | 取消采样 |
| POST | `/api/field-profiles/{profile_id}/runtime-sampling/start` | 按支持的 profile 启动采样 |
| GET | `/api/field-profiles` | 列出 profiles |
| GET | `/api/field-profiles/{profile_id}` | 查看 profile |
| GET | `/api/field-profiles/{profile_id}/validate` | 校验 profile |

schema v2、中心线 PCA、`bind-current` 和 FIELD→LOCAL_NED 绑定已在 P2 删除。FIELD 或 GPS
派生航点的 Action Mission 必须等待已确认、同步并冻结的 v3 Field Reference。
