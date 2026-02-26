# Telegram 群组/频道媒体去重 Bot（严格幂等）

目标：在群组或频道内识别重复媒体并自动删除重复项；多次运行结果保持一致（幂等）。

## 关键事实与限制（来源：Telegram Bot API 官方文档）

- **Bot API 只能处理“收到的更新”**：通常无法回溯遍历群组/频道的历史消息；若你需要“扫描全量历史”，需使用 MTProto 客户端方案（例如 Telethon）。
- **删除需要权限**：Bot 必须在目标群组/频道具备删除消息的管理员权限，否则无法自动删除。
- **群组隐私模式影响可见性**：若 Bot 开启 Privacy Mode，可能收不到普通消息（仅收命令/被提及等），会影响去重效果。
- **`file_unique_id` 可用于稳定识别同一文件**：用于构建幂等去重键，避免重复下载与哈希。

## 默认实现范围（本仓库当前代码）

- 实时去重：Bot 加入后，对**新产生的媒体消息**进行去重与删除（可先 dry-run）。
- 幂等：同一条消息只处理一次；同一媒体在同一 chat 内只保留一条（默认保留最早消息）。
- 存储：SQLite（`DB_PATH`）。
- 待删队列：若当时未开启删除，会把“应删除的 message_id”记录为待删，可在开启删除后用 `/flush` 批量清理。

## 安装与运行

1. 创建 Bot 并拿到 token（BotFather）。
2. 将 Bot 加入目标群/频道并授予“删除消息”权限（管理员）。
3. （群组）按需关闭 Privacy Mode，确保能收到媒体消息更新。
4. 配置环境变量：复制 `.env.example` 为 `.env` 并填写。
5. 安装依赖并运行：

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python3 -m tg_media_dedupe_bot run-bot
```

提示：程序会自动从当前目录（或其父目录）查找并加载 `.env`（实现见 `tg_media_dedupe_bot/config.py`）。
提示：Telegram 客户端的 “/ 命令列表” 由 Bot API 的 `setMyCommands` 决定；本程序启动时会自动设置。

## Docker 运行

构建镜像（镜像名：`telegram_mediachanel_manager_bot`）：

```bash
docker build -t telegram_mediachanel_manager_bot:3.0 .
```

使用 `docker run`（容器名：`telegram_mediachanel_manager_bot`）：

```bash
docker run -d --name telegram_mediachanel_manager_bot \
  --user "$(id -u):$(id -g)" \
  --env-file ./.env \
  -v "$(pwd)/data:/app/data" \
  -v "$(pwd)/sessions:/app/sessions" \
  --restart unless-stopped \
  telegram_mediachanel_manager_bot:3.0
```

使用 `docker compose`：

```bash
export UID="$(id -u)"
export GID="$(id -g)"
docker compose up -d
```

健康检查（需提供 `TG_BOT_TOKEN`）：

```bash
docker ps --filter name=telegram_mediachanel_manager_bot --format "{{.Status}}"
```

自检（简单功能自测）：

```bash
docker exec telegram_mediachanel_manager_bot python /usr/local/bin/selfcheck.py
```

说明：自检仅验证配置与 SQLite 去重逻辑，不会调用 Telegram 网络接口。

## 配置项

- `TG_BOT_TOKEN`：Bot token（必填）
- `DB_PATH`：SQLite 路径（默认 `./data/bot.db`）
- `ALLOW_CHAT_IDS`：只在指定 chat 内工作（逗号分隔；留空=全部）
- `DELETE_DUPLICATES`：是否执行删除（默认 0）
- `DRY_RUN`：是否仅打印不删除（默认 1）
- `KEEP_POLICY`：`oldest`（保留最早消息）
- `RETRY_FAILED_DELETES`：是否重试历史删除失败（默认 0）
- `TAG_BUILD_LIMIT`：`/tag_build` 扫描条数限制（默认 0=不限制）
- `TAG_COUNT`：每条消息最多补标签数（默认 3，最大 10；可被 `/tag_count` 覆盖）
- `TG_API_ID`/`TG_API_HASH`：Telethon 登录所需（历史扫描必填）
- `TG_SESSION`：Telethon session 存放路径（默认 `./sessions/user`）

## 管理命令

- `/start`：开始/说明
- `/help`：帮助
- `/ping`：健康检查
- `/stats`：查看当前 chat 的去重统计
- `/mode`：查看当前 chat 模式（dry-run/删除开关）
- `/status`：查看当前 chat 进行中的任务状态
- `/tags_pin [N] [MAX]`：回溯提取历史消息中的 `#标签`，生成“标签目录”并置顶（标签过多会自动分多条置顶；需要 Telethon 用户账号 session；N=扫描条数，0=不限制；MAX=展示的唯一标签数上限，0=不限制）
- `/tags_pin <@username|邀请链接> [N] [MAX]`：同上，显式指定目标（适用于私有群/频道无法解析时）
- `/tag_pin ...`：`/tags_pin ...` 的别名
- `/tag_build`：扫描历史媒体消息，按标签库出现次数优先匹配并补标签（无参数；限制条数由 `TAG_BUILD_LIMIT` 控制；最多补 `TAG_COUNT` 个）
- `/tag_build_status`：查看 `tag_build` 进度
- `/tag_build_stop`：停止 `tag_build` 任务
- `/tag_rebuild [N|all]`：历史媒体消息标签替换 + 屏蔽文本删除（N=条数，从最新开始；all=全部，从最早开始；限制条数默认来自 `TAG_BUILD_LIMIT`）
- `/tag_update`：标签更新（清理黑名单/屏蔽文本/补齐标签并生成目录）
- `/tag_stop`：停止当前 chat 所有任务（scan/tags_pin/tag_build/tag_rebuild）
- `/tag_count [N]`：设置每条消息最多补标签数（1-10；留空查看）
- `/tag_rename [global] #旧=#新`：设置标签别名（`/tag_rename [global] list|del`）
- `/text_block`：管理屏蔽关键词（`/text_block [global] list|add|del 关键词`）
- `/dry_run_on` / `/dry_run_off`：切换是否只记录不删除
- `/enable_delete` / `/disable_delete`：切换是否允许删除重复
- `/flush [N]`：从数据库的“待删队列”中尝试删除最多 N 条（默认 100，最大 1000）
- `/scan [N]`：使用 MTProto 回溯扫描历史（默认 dry-run；N=条数，0=不限制）
- `/scan_delete [N]`：回溯扫描并删除重复（需先 `/dry_run_off` 且 `/enable_delete`）
- `/scan <@username|邀请链接> [N]`：显式指定目标群/频道（适用于私有群/频道无法直接解析时）
- `/scan_delete <@username|邀请链接> [N]`：同上（删除模式）
- `/scan_status`：查看扫描进度
- `/scan_stop`：停止扫描

## 如何确认“正在检查”（实时去重）

本 Bot 不会主动发送“正在扫描”的提示；只有在**收到媒体消息**时才会触发去重逻辑（`/ping` 仅用于健康检查）。

1. 在目标群/频道发送一条媒体（照片/视频/文件）
2. 再发送同一媒体一次（制造重复）
3. 在群里发送 `/stats` 或 `/flush` 查看“判定重复/待删队列”是否增长，或查看运行终端日志

## /tag_build 与 /text_block 使用示例

配置扫描条数上限（可选）：

```bash
export TAG_BUILD_LIMIT=200
export TAG_COUNT=5
```

在群内执行：

```
/text_block add 广告
/text_block add 低价
/text_block list
/tag_count 5
/tag_build
/tag_build_status
/tag_build_stop
```

说明：
- `/tag_build` 会扫描历史媒体消息，按标签库出现次数优先匹配并补标签（最多 `TAG_COUNT` 个，最大 10），然后重发并删除旧消息。
- `/text_block` 中的关键词会在补标签时从文本中删除。
- `/tag_rebuild` 会重写历史消息的标签（按别名替换），并删除屏蔽关键词，然后重发并删除旧消息（all 从最早开始，N 从最新开始）。

## /tag_rename 与标签分组文件

标签库位置：
- `./data/bot.db`（SQLite 表 `tag_library`，按 chat_id 隔离）

标签别名文件（全局 + 按群/频道分开）：
- `./data/tag_aliases/global.txt`（全局）
- `./data/tag_aliases/<chat_id>.txt`（群/频道）
- 格式：每行 `#旧=#新`，示例：`#高中生=#男高`
- 说明：`/tag_rename` 会同步对应文件；删除别名用 `/tag_rename del #旧`
- 冲突规则：同一旧标签在全局与群组都存在时，以全局为准
- 手动修改文件后，执行 `/tag_rename list` 或运行 `/tag_build` `/tags_pin` 会自动同步

标签分组文件（仅用于 `/tags_pin` 展示）：
- `./data/tag_groups/<chat_id>.txt`
- 格式 A（分区标题 + 标签列表）：
```
-------电影--------
#动作
#爱情

-------游戏--------
#主机
#手游
```
- 未被分组的标签会自动加入“其他”分区并写回文件。

## 历史扫描（可选）

如果你确实需要“扫描群组/频道历史媒体”，推荐用 MTProto（Telethon）。本仓库预留了 `scan` 子命令接口（需要额外依赖与账号登录信息），但是否启用取决于你能接受的账号/合规成本。

### 使用步骤（在 Telegram 内操作）

1. 安装依赖：`pip install -r requirements-scan.txt`
2. 申请并填写 `TG_API_ID`/`TG_API_HASH`（Telegram 官方提供的 API 配置）
3. 先私聊 bot 完成用户账号授权（会生成/更新 `TG_SESSION` 对应的 session 文件）：
   - `/session_status` 查看是否已授权
   - （推荐）`/session_qr` 二维码登录（避免验证码被 Telegram 判定“已分享”）
   - `/session_login +8613xxxx` 发送验证码
   - `/session_code 12345` 提交验证码（如提示两步验证再 `/session_password 你的密码`）
   - 如果提示 session 是 bot 或无法发送验证码：先 `/session_reset` 再重新 `/session_login`
4. 在目标群/频道内发送 `/scan`（不删，只记录待删队列/统计；可加 N 限制扫描条数）
5. 如需执行删除：先在群里 `/dry_run_off` 且 `/enable_delete`，再发送 `/scan_delete`

如果遇到 “The API access for bot users is restricted ... GetHistoryRequest”：
- 说明你当前使用的是 bot 账号（或 bot session），无法回溯历史；请按上面步骤用**用户账号**完成 `/session_login` 授权后重试。

如果遇到 “无法解析该群组/频道，请使用 @username 或邀请链接”：
- 说明目标是私有群/频道且无法仅靠 chat_id 解析；请改用 `/scan @username` 或 `/scan 邀请链接`（例如 `https://t.me/+xxxx`）。

如果遇到 “database is locked”：
- 说明 SQLite 正在被另一个进程/连接写入；请确认没有同时运行多个 bot/scan 进程，并重启 bot 后重试。

（可选）仍可在宿主机使用 CLI：`python3 -m tg_media_dedupe_bot scan --chat <chat> --reverse`

权限说明：无论是用户账号还是 bot，必须在目标群/频道具备删除消息权限，否则删除会失败（可在 `/stats` 查看失败计数）。

## 你需要确认的 4 个问题

1. “扫描群组内媒体”是指**仅处理新消息**，还是需要**回溯历史**？
2. 去重范围是**单个群/频道内**，还是跨多个群/频道做全局去重？
3. “重复”定义是否只按 Telegram 的 `file_unique_id`（同文件复发），还是要按**文件内容哈希**（重传/改名仍判重）？
4. 重复后保留策略：保留最早/最新/管理员消息/带特定标签的消息？

## UI 界面预览

![登录页](docs/ui/login.png)
![控制台概览](docs/ui/dashboard.png)

## 特色功能

- 严格幂等去重：同媒体重复投递只保留一份，避免群内刷屏。
- Web 管控台：可视化维护 Bot 配置、标签、筛选规则与日志。
- 历史任务能力：支持标签重建、批量清理与任务进度追踪。
- NAS 友好部署：支持 Docker / Compose，适配常见家用服务器场景。
