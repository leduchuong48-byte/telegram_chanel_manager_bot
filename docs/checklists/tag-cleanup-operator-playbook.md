# Tag Cleanup 操作手册（一次性任务）

## 1. 目标
- 在一次会话中完成标签清洗建议、人工确认、导出与应用。
- 默认优先 `dry_run`，`write` 需二次确认。

## 2. 前置检查
- 后台可访问 `/tag_cleanup`
- Provider/Model 基础状态正常
- 已准备待整理标签列表（建议先导入小样本）

## 3. 标准流程
1. 在 `Tags` 输入区粘贴标签（每行一个）
2. 点击 `Generate Suggestions`
3. 使用过滤器缩小范围（action/decision/confidence）
4. 批量 `Accept Selected` / `Reject Selected`
5. 需要人工改写时，用 `Edit Selected` 批量改目标标签
6. 点击 `Dry Run Apply` 检查摘要
7. 点击 `Export JSON` 保存映射
8. 确认无误后点击 `Apply (Write)`，输入 `APPLY` 完成写入

## 4. 风险控制
- 未输入 `APPLY` 不允许 write
- write 前必须先执行 dry_run
- 每次 write 后立刻导出映射做留档

## 5. 常见问题
- `No current session id`：先执行 preview
- `Write apply blocked`：确认 token 精确为 `APPLY`
- 结果不对：先 `Load Session` 复核 decision/final_target

## 6. 收尾
- 保存导出文件到变更记录
- 记录本次会话 summary（accepted/rejected）
- 如需再次整理，开启新 session 重跑
