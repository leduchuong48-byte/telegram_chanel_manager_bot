# 上线前人工验收清单

> 目标：用最小成本验证 `Provider / Model / Responses / Tag Cleanup` 可用性。
> 
> 策略：优先核心链路，避免重型回归。

## A. 环境与基础

- [ ] 服务可正常启动，无启动报错
- [ ] Web 管理后台可访问
- [ ] 数据库连接正常（SQLite 文件可读写）
- [ ] 配置文件加载正常（含 database path）

## B. Provider 管理

- [ ] 可新增 Provider（至少 1 条）
- [ ] Provider 字段校验正常（非法 key/type/mode 会被拒绝）
- [ ] Provider 列表可显示新增项
- [ ] `test` 操作可成功返回
- [ ] `probe` 操作可成功返回
- [ ] probe 后 supports_responses 字段可见且合理
- [ ] Provider 更新可生效（display_name / enabled / mode / default_model）

## C. Model 管理

- [ ] `sync` 可执行且返回成功
- [ ] 模型列表可显示同步结果
- [ ] 模型可按 provider 维度展示
- [ ] enabled_only 过滤行为正确
- [ ] default model 展示与 provider 配置一致

## D. Responses 策略（解释可见）

- [ ] request > model > provider > global 优先级符合预期
- [ ] 无覆盖时回落到 global
- [ ] 不支持 mode 时有明确错误提示
- [ ] WebUI/API 能解释“最终生效值”来源层级

## E. AI 健康与短窗口指标

- [ ] `/api/ai/health` 可返回基础状态
- [ ] `/api/ai/metrics-summary?window=1h` 可返回摘要
- [ ] provider test/probe 后 request_count 有增量
- [ ] model sync 后 model request_count 有增量
- [ ] 旧事件不会进入 1h 窗口统计
- [ ] p95 latency 字段有合理值（非简单镜像 avg）

## F. Tag Cleanup（一次性任务）

- [ ] 可进入 Tag Cleanup 页面
- [ ] 选择数据源后可生成建议（preview）
- [ ] 建议可按 action/decision/confidence 筛选
- [ ] 可逐条 Accept/Reject/Edit
- [ ] 可批量 Accept/Reject（按筛选或勾选）
- [ ] 可导出 JSON 结果
- [ ] 可导出 CSV 结果
- [ ] Dry Run Apply 可执行
- [ ] Apply 前有确认提示
- [ ] Apply 后摘要计数正确（merge/rename/deprecate 等）

## G. 稳定性与边界

- [ ] 空输入标签集时有友好提示
- [ ] 非法 action/decision 被拒绝
- [ ] target_tag 为空时不会直接应用 merge/rename
- [ ] 大量标签输入时页面可操作（无明显卡死）

## H. 回退与应急

- [ ] 不执行 Apply 时不会改动现有标签数据
- [ ] Export 结果可作为手工回退依据
- [ ] 出现异常时可重新开始新 session

## I. 上线结论

- [ ] P0 项（B/C/D/F）全部通过
- [ ] P1 项（E/G/H）无阻断风险
- [ ] 允许上线（是/否）：____
- [ ] 验收人：____
- [ ] 验收时间：____
