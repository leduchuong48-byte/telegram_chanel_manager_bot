# Tag Cleanup 手工验收（一次性任务版）

## 1. 预览链路

- [ ] 选择 `Database` 数据源能生成建议
- [ ] 上传 `JSON` 能生成建议
- [ ] 上传 `CSV` 能生成建议
- [ ] 空输入会被拦截并提示
- [ ] max_items 生效

## 2. 建议质量（最小要求）

- [ ] merge 建议有目标标签
- [ ] rename 建议有目标标签
- [ ] deprecate 建议无目标标签也可处理
- [ ] confidence 字段有值且范围合理（0~1）
- [ ] reason 字段可读

## 3. 审核交互

- [ ] 行级 Accept 生效
- [ ] 行级 Reject 生效
- [ ] Edit 后 Accept 生效（final_action/final_target 生效）
- [ ] 批量 Accept 生效
- [ ] 批量 Reject 生效
- [ ] decision 筛选正常

## 4. 导出

- [ ] 导出 JSON 成功
- [ ] JSON 可读且字段完整
- [ ] 导出 CSV 成功
- [ ] CSV 列头正确

## 5. 应用

- [ ] Dry Run Apply 返回摘要，不落库
- [ ] Apply 前有确认弹窗
- [ ] Apply 后摘要与已决策项一致
- [ ] rejected 项不会被应用
- [ ] edited 项按 final_* 应用

## 6. 收尾

- [ ] 页面可开始新 session
- [ ] 旧 session 不会影响新 session
- [ ] 本次输出可作为归档文件保存
