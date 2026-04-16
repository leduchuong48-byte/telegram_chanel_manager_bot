# Web Admin Panel 使用手册

本手册说明 Web 管理面板的安装、登录、配置编辑与热重载行为。

---

## 1. 安装依赖

安装 Web 面板所需依赖：

```bash
python3 -m pip install -r requirements.txt
```

关键依赖包括：
- fastapi
- uvicorn[standard]
- python-multipart（OAuth2 表单登录）
- python-jose[cryptography]
- passlib[bcrypt]

---

## 2. 快速启动

### 2.1 Docker 启动

```bash
export UID=$(id -u)
export GID=$(id -g)
docker compose up -d
```

### 2.2 本地启动

```bash
export TG_BOT_TOKEN="your_bot_token"
python3 web_app.py
```

服务默认监听 `0.0.0.0:8000`。登录入口：`http://localhost:8000/login`。

---

## 3. 登录与账号配置

配置文件位置：项目根目录 `config.json`。

在 `config.json` 中配置 `web_users`：

```json
{
  "web_users": [
    {
      "username": "admin",
      "password_hash": "$2b$12$..."
    }
  ]
}
```

### 3.1 生成 bcrypt 密码哈希

推荐使用工具脚本生成哈希：

```bash
python3 utils/password_gen.py "mypassword"
```

输出的字符串直接填入 `config.json` 的 `password_hash` 字段。

---

## 4. 功能说明

### 配置优先级

### Cleaner 新增接口与行为

- GET /api/cleaner/jobs 支持 limit、status、task_type、chat_id 查询参数。
- GET /api/cleaner/monitoring 返回运行中任务数、pause 中 chat 数、最近失败任务数。
- Cleaner runtime 对可恢复错误执行一次保守重试；权限错误、配置错误等不可恢复问题直接失败。
- 当 bot.dry_run=true 时，Cleaner 仅扫描与统计，不执行真实删除；当 bot.dry_run=false 时，Cleaner 才执行真实删除。

- Web UI 保存的是 config.json，它是业务配置的权威源。
- .env 中旧字段（如 DRY_RUN、DELETE_DUPLICATES、TG_API_ID、TG_API_HASH、TG_BOT_TOKEN）仅保留兼容用途，不再作为 Cleaner/Web 业务行为的最终来源。
- 若 .env 与 config.json 冲突，系统会优先采用 config.json 并在日志中输出 deprecated_env_override_ignored。

### 4.1 配置编辑器

- **保存 (Save)**：写入 `config.json`，并生成备份文件。
- **热重载 (Hot Reload)**：触发运行时配置刷新。

当前热重载仅会更新 `bot` 配置中的运行时字段并刷新日志级别：
- `dry_run`
- `delete_duplicates`
- `log_level`
- `tag_count`
- `tag_build_limit`

建议新增并使用 `bot.web_tg_session`（例如 `./sessions/webui`），与运行中 Bot 的 `TG_SESSION` 分离，避免 Web 维护工具触发 `database is locked`。
`bot.target_chat_ids` 支持多目标群组（数组或在页面中按换行/逗号输入）；`target_chat_id` 保留为兼容字段。若 `target_chat_ids` 为空，维护工具会优先使用 Bot 注册表目标（`managed_chats`），为空时再回退到 Web 会话自动发现。
维护工具页面支持“单群组执行”：可在页面下拉框选择某个群组后只对该群组发送维护指令。单群组执行前会校验 Bot 在该群组内的成员状态，仅允许 Bot 为管理员/群主的目标执行。
系统新增 `managed_chats` 注册表（`data/bot.db`）：由 Bot 进程在 `my_chat_member` 更新和群消息事件中持续写入，用于 Web 维护工具优先读取“Bot 实际所在群组”。
`managed_chats` 现在包含校验字段 `verified_at/verified_by`：用于标记“是否已通过权限校验、由哪个来源校验”（如 `my_chat_member`、`get_chat_member`）。
维护工具接口支持 `GET /api/tools/targets?refresh=1` 强制同步：使用 Web Telethon 会话批量补全群组标题与用户名并回写 `managed_chats`。
维护工具在“单群组执行”和“全量执行”前都会调用 Bot API `getChatMember` 校验 Bot 在目标群中的成员状态，仅向管理员/群主状态目标发送维护指令。
标签页面新增“标签重命名规则（A=B）”管理：支持群组规则、全局规则、生效规则（只读）；底层与 Bot 命令 `/tag_rename` 共用 `data/tag_aliases/*.txt` 文件格式。

当前代码未包含调度器重启逻辑，因此如果涉及定时任务间隔或调度器配置变更，需要重启服务才能生效。


### 4.3 管理员身份模型（重要）

系统中存在三类“管理员”，请务必区分：

- **Web Admin 管理员账号 (`web_users`)**：用于登录 Web 面板。对应页面：`/users`（或 `/account`）。
- **Telegram 控制用户 (`telegram_controllers`)**：用于通过 Telegram 指令控制 Bot。对应页面：`/telegram_controllers`。
- **Telegram 群/频道管理员**：是 Telegram 群内权限角色，不等于系统控制用户。

> 说明：`/users` 仅管理 Web 登录账号，**不等同于 Telegram 控制用户**。

#### Telegram 控制用户优先级

Bot 运行时控制权限采用以下优先级：

1. `telegram_controllers` 表中 `enabled=1` 的用户（主控制员 + 辅助控制员）
2. 兼容字段 `settings.controller_user_id`
3. 若以上都不存在，首次私聊指令触发时会自动绑定当前用户（并回写兼容字段）

`bot.admin_id` 仅保留兼容/通知语义（如启动通知），不再作为主要控制权限来源。

#### Telegram 控制用户 API

- `GET /api/telegram-controllers`：获取控制用户列表
- `POST /api/telegram-controllers`：新增/保存控制用户
- `PATCH /api/telegram-controllers/{user_id}`：更新备注或启停状态
- `POST /api/telegram-controllers/{user_id}/make-primary`：设为主控制员
- `DELETE /api/telegram-controllers/{user_id}`：删除控制用户（禁止删除唯一启用主控制员）

### 4.2 备份机制

每次保存配置后会生成备份文件：
- `./config.json.bak.<时间戳>`
- `./backups/config.json.bak.<时间戳>`

---

## 5. API 文档摘要

### 5.1 获取 Token

```bash
curl -X POST http://localhost:8000/api/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=admin&password=your_password"
```

### 5.2 获取配置

```bash
curl -X GET http://localhost:8000/api/config \
  -H "Authorization: Bearer <token>"
```

### 5.3 更新配置

```bash
curl -X PUT http://localhost:8000/api/config \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"data": {...}}'
```

---

## 6. 页面入口

- 登录页：`/login`
- 仪表盘：`/`
- Bot 设置：`/bot_settings`
- Web 管理员账号：`/users`（或 `/account`）
- Telegram 控制用户：`/telegram_controllers`
- 配置编辑器：`/config_editor`


## Frontend UX Modes

The Web Admin frontend now follows two page modes:

- `editor page`: used by `/tags`, focused on sustained editing work. Layout is `resource list -> workspace -> preview`.
- `tool page`: used by `/cleaner`, `/media_filter`, `/tools`, focused on parameter selection and immediate execution.

### Shared Interaction Rules

- Only one primary vertical scroll region should dominate each page.
- Target selectors must only show groups where `bot_can_manage == true`.
- Toast feedback should be lightweight and fast; confirmations are reserved for destructive actions.
- Keep motion subtle and short; prioritize stability and responsiveness over flourish.
- Apple-style visual direction is preferred: restrained surfaces, clear hierarchy, calm spacing, low-noise controls.

### Tags Workspace Rules

- The tags page is a workbench, not a dashboard.
- Clicking a tag opens the editor inside the current section card, not in a floating unstable position.
- TG preview remains fixed and should not disappear during editing.
- Raw text mode is considered advanced/compatibility mode and should not be the primary path.

### Tool Page Rules

- Cleaner, media filter, and tools pages should use the same page rhythm: target/scope area -> main task area -> feedback/help text.
- Reuse copy tone and target-group behaviors consistently across these pages.


## 7. 4.0 升级总结

本次 `4.0` 版本主要聚焦于 Web Admin 前端交互重构与维护工作流整合，重点变化如下：

- **整体页面范式重构**
  - 前端分为两种模式：
    - `editor page`：用于 `/tags`，聚焦持续编辑与预览
    - `tool page`：用于 `/cleaner`、`/media_filter`、`/tools`、`/filters`、`/account`
  - 统一了基础壳层、间距、按钮、提示、滚动规则与视觉层级。

- **标签工作台升级**
  - 标签管理页从旧文本编辑模式升级为“分区工作台”。
  - 左侧显示被管理群组，中间编辑标签目录，右侧固定 TG 预览。
  - 点击标签后，在当前分区标题下方直接编辑，不再依赖不稳定的浮层。
  - 支持：
    - 单标签重命名规则
    - 多选模式
    - 多标签合并到同一目标标签
    - 分区内新增标签
    - 分区排序（上移/下移）
    - 标签移动到其他分区
    - 新建分区并移动（后续可继续扩展）

- **Bot 联动一致性增强**
  - Web Admin 与 Bot 命令继续共用同一套标签文件和 alias 规则。
  - Web 侧的 TG 预览尽量贴近 Bot 实际输出，减少“改完靠猜”的情况。

- **目标群组选择统一**
  - 多个页面统一只显示 `bot_can_manage == true` 的群组。
  - `tags`、`cleaner`、`media_filter`、`tools` 的目标群组行为已统一。

- **工具页体验优化**
  - 消息清洗、媒体筛选、维护工具、屏蔽词管理、账号管理均已收口到统一工具页风格。
  - 提升了可读性、层级感、对比度与执行反馈的一致性。

- **可用性修复**
  - 修复 `/account` 404，兼容到账号管理页。
  - 提升屏蔽词列表可读性。
  - 修复多处 tags 页面脚本与交互反馈问题。

### 4.0 使用建议

- 标签整理优先在 `/tags` 中进行，不建议再以原始文本为主。
- 原始文本模式保留为兼容/高级入口，仅在批量导入或故障排查时使用。
- 对复杂标签整理任务，优先使用：
  - 单标签编辑条
  - 多选模式
  - TG 预览

### 版本标识

- FastAPI 应用版本：`4.0`
- Docker 镜像标签建议：`telegram_mediachanel_manager_bot:4.0`
