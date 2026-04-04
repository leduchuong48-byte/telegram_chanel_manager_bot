# Channel Manager Bot

![Channel Manager Bot 3.5](docs/ui/web-tags-workbench-3-5.png)

[![Docker Pulls](https://img.shields.io/docker/pulls/leduchuong/telegram_mediachanel_manager_bot?logo=docker&label=Docker%20Pulls&style=flat-square)](https://hub.docker.com/r/leduchuong/telegram_mediachanel_manager_bot)
[![GitHub Stars](https://img.shields.io/github/stars/leduchuong48-byte/telegram_chanel_manager_bot?style=flat-square)](https://github.com/leduchuong48-byte/telegram_chanel_manager_bot/stargazers)
[![GitHub Forks](https://img.shields.io/github/forks/leduchuong48-byte/telegram_chanel_manager_bot?style=flat-square)](https://github.com/leduchuong48-byte/telegram_chanel_manager_bot/network/members)
[![GitHub Issues](https://img.shields.io/github/issues/leduchuong48-byte/telegram_chanel_manager_bot?style=flat-square)](https://github.com/leduchuong48-byte/telegram_chanel_manager_bot/issues)
[![License](https://img.shields.io/github/license/leduchuong48-byte/telegram_chanel_manager_bot?style=flat-square)](https://github.com/leduchuong48-byte/telegram_chanel_manager_bot/blob/main/LICENSE)
[![Platform: ARM64/AMD64](https://img.shields.io/badge/Platform-ARM64%2FAMD64-blue.svg)](#)

[English](README_en.md)

> 面向 Telegram 群组与频道维护场景的自托管 Bot + Web Admin 工作台，适合长期整理标签、执行清洗任务、统一后台操作流程。

## Why this tool?

如果你平时要维护多个 Telegram 群组或频道，最烦的往往不是“没有功能”，而是每次做事都得切换脑回路：标签像在修文本文件，清洗和筛选工具各有各的交互，改完规则以后还要靠猜测确认 Bot 最终会怎么输出。Channel Manager Bot 3.5 更关注这些真实维护场景，把高频操作整理成一个更顺手、更稳定、更适合持续使用的工作台。

## 适合什么场景

- **长期维护标签体系**：当你的群组标签越来越多，需要频繁重命名、合并、分区整理和预览效果时，标签工作台比原始文本方式更稳定。
- **多工具日常巡检**：当你经常切换消息清洗、媒体筛选、维护工具、账号管理这些页面时，统一工具页结构能明显降低操作负担。
- **Bot 与后台协同维护**：当你希望 Web Admin 改完的规则，能尽量和 Bot 最终输出一致，避免“前台看起来对、实际发出来不一样”时，这套共享标签与 alias 规则更可靠。
- **NAS / Docker 自托管**：当你更关心部署稳定、目录清晰、方便备份和长期运行时，这个项目更适合放进长期维护环境，而不是一次性脚本方案。

## 3.5 版本重点

- **Web Admin 前端体验升级**：后台页面按两种模式重构，标签工作台用于持续编辑与预览，工具页用于配置、维护和执行类操作，整体页面结构、按钮样式、提示反馈、滚动规则与视觉层级都更统一。
- **标签工作台真正可用**：标签管理从旧文本编辑方式升级为分区工作台，左侧显示被管理群组，中间编辑标签目录，右侧固定 TG 预览；点击标签后可直接在当前分区中编辑，不再依赖不稳定浮层。
- **标签操作工作流增强**：支持单标签重命名规则、多选模式、多标签合并到同一目标标签、分区内新增标签、分区排序、标签移动到其他分区，以及新建分区并移动。
- **Bot 联动一致性增强**：Web Admin 与 Bot 命令继续共用同一套标签文件和 alias 规则，TG 预览尽量贴近 Bot 实际输出，减少“改完靠猜”的情况。
- **目标群组选择统一**：`tags`、`cleaner`、`media_filter`、`tools` 页面统一只显示 `bot_can_manage == true` 的目标，降低误操作概率。
- **可用性修复**：修复 `/account` 404，提升屏蔽词列表可读性，修复 tags 页面多处脚本与交互反馈问题，并减少大分区场景下的来回滚动。

## Web Admin UI Preview

### 标签工作台

![标签工作台](docs/ui/web-tags-workbench-3-5.png)

新的标签工作台把“目标群组、标签分区、TG 预览”放进同一个编辑视图里，适合持续整理标签目录、重命名规则和分区结构，而不是像过去那样在多个零碎状态之间跳来跳去。

### 工具页

![工具页](docs/ui/web-tools-3-5.png)

工具页强调统一的节奏：先选目标群组，再执行维护动作，再看反馈。消息清洗、媒体筛选、维护工具等页面不再各说各话。

### 入口与总览

![后台总览](docs/ui/web-dashboard-3-5.png)

主页负责把高频入口和维护方向清楚地收拢起来，让配置编辑、Bot 设置、标签管理和日志查看都有稳定的落点。

## 核心能力

- **标签工作流**：标签分区、别名规则、预览、排序、移动、批量整理。
- **维护工具**：清洗、筛选、维护动作统一成一致的工具页交互。
- **Bot / Web 共用规则**：前台编辑和 Bot 执行使用同一套标签与 alias 文件，减少前后台不一致。
- **自托管友好**：适合 Docker / Compose / NAS 场景，运行依赖清晰，数据目录独立。

## 常见使用方式

### 1. 把它当作标签整理后台

适合已经有稳定内容流、但标签结构越来越复杂的群组或频道。你可以在 `/tags` 里持续整理分区、规则和预览，而不是靠原始文本和临时记忆反复修改。

### 2. 把它当作日常维护工具箱

适合管理员需要经常执行清洗、筛选、维护动作的场景。工具页统一之后，操作路径更固定，不容易在不同页面之间来回适应。

### 3. 把它当作 Bot 运维配置台

适合希望把 Bot 规则、Web 管理和数据目录放在同一套自托管环境里统一维护的用户，尤其是 Docker / NAS / 小型服务器场景。

## 完整 Bot 命令列表

下面这部分是完整命令参考。首页保留完整命令，是因为这个项目并不只是 Web Admin，很多真实维护动作仍然直接发生在 Bot 对话里。

### 基础与状态

- `/start`：显示欢迎信息、当前模式，并给出 Web 面板入口
- `/help`：显示完整指令列表
- `/menu`：打开主控按钮面板
- `/ping`：健康检查
- `/stats`：查看当前 chat 统计
- `/mode`：查看或设置当前 chat 的删除模式
- `/status`：查看当前 chat 正在执行的任务状态

### 运行模式与删除开关

- `/enable_delete`：开启删除能力
- `/disable_delete`：关闭删除能力
- `/dry_run_on`：开启 dry-run
- `/dry_run_off`：关闭 dry-run

### 历史扫描与重复处理

- `/scan [N]`：回溯扫描当前群/频道历史，`N=条数`，`0=不限制`
- `/scan <chat> [N]`：显式指定扫描目标，`chat` 支持 `-100...`、`@username`、邀请链接
- `/scan_delete [N]`：回溯扫描并删除重复，要求先关闭 dry-run 并开启 delete
- `/scan_delete <chat> [N]`：同上，但显式指定目标
- `/scan_status`：查看扫描任务进度
- `/scan_stop`：停止当前扫描任务
- `/flush [N]`：删除待删队列，默认 `100`，最大 `1000`

### 标签目录与标签生成

- `/tags_pin [N] [MAX]`：回溯提取 `#标签`，生成标签目录并置顶
- `/tags_pin <chat> [N] [MAX]`：对指定目标执行标签目录生成
- `/tag_pin`：`/tags_pin` 的别名
- `/tag_build`：扫描历史媒体消息，按标签库匹配并自动补标签
- `/tag_build_status`：查看 `tag_build` 任务进度
- `/tag_build_stop`：停止 `tag_build`
- `/tag_rebuild [N|all]`：对历史消息执行标签替换与屏蔽文本删除
- `/tag_update`：执行标签更新，包括清理黑名单、屏蔽文本、补齐标签并生成目录
- `/tag_stop`：停止当前 chat 的标签相关任务（`scan` / `tags_pin` / `tag_build` / `tag_rebuild`）
- `/tag_count [N]`：设置每条消息最多补标签数，范围 `1-10`

### 标签别名与文本规则

- `/tag_rename [global] #旧=#新`：设置标签别名规则
- `/tag_rename [global] list`：查看标签别名规则
- `/tag_rename [global] del #旧标签`：删除标签别名规则
- `/text_block [global] list`：查看屏蔽关键词
- `/text_block [global] add 关键词`：新增屏蔽关键词
- `/text_block [global] del 关键词`：删除屏蔽关键词

### Session / Telethon 维护

以下命令通常建议在私聊中使用：

- `/session_status`：查看当前 Telethon session 状态
- `/session_login`：开始手机号登录流程
- `/session_qr`：使用二维码登录
- `/session_code`：提交短信或 Telegram 登录验证码
- `/session_password`：提交二步验证密码
- `/session_logout`：登出当前 session
- `/session_reset`：重置当前 session

### 命令使用说明

- 很多历史扫描与标签命令依赖 Telethon 用户账号 session，不是纯 Bot Token 就能完成。
- 涉及 `<chat>` 参数的命令，一般支持 `-100...`、`@username`、邀请链接三类目标表示方式。
- 涉及真实删除的命令，建议先在 dry-run 下观察输出，再切换到正式执行。

### 推荐的最小使用顺序

如果你刚开始接管一个新群组或频道，推荐顺序是：

1. `/ping`
2. `/status`
3. `/session_status`
4. `/tags_pin` 或进入 `/tags`
5. `/tag_update`
6. 确认后再决定是否执行 `/scan_delete`

## ⚡ Quick Start

```bash
docker run -d \
  --name telegram_mediachanel_manager_bot \
  --restart unless-stopped \
  -p 1009:8000 \
  -v /path/to/data:/app/data \
  -v /path/to/sessions:/app/sessions \
  leduchuong/telegram_mediachanel_manager_bot:latest
```

## Docker Compose

```yaml
services:
  app:
    image: leduchuong/telegram_mediachanel_manager_bot:latest
    container_name: telegram_mediachanel_manager_bot
    restart: unless-stopped
    ports:
      - "1009:8000"
    environment:
      - TZ=Asia/Shanghai
      - LOG_LEVEL=INFO
    volumes:
      - ./data:/app/data
      - ./sessions:/app/sessions
```

## 使用建议

- 标签整理优先在 `/tags` 中完成，原始文本模式更适合作为兼容或高级入口。
- 对复杂标签整理任务，优先使用单标签编辑、多选模式和 TG 预览配合工作。
- 生产环境请始终把 token、session、群组数据和运行数据库留在外部挂载目录，不要进入仓库或镜像层。

## 项目结构

- `app/`：FastAPI Web Admin
- `tg_media_dedupe_bot/`：Bot 核心逻辑
- `scripts/`：维护与自检脚本
- `WEB_ADMIN_README.md`：偏操作手册的 Web Admin 说明

## 在哪里获得帮助

- Issues: https://github.com/leduchuong48-byte/telegram_chanel_manager_bot/issues
- Discussions: https://github.com/leduchuong48-byte/telegram_chanel_manager_bot/discussions
- Docker Hub: https://hub.docker.com/r/leduchuong/telegram_mediachanel_manager_bot

## 免责声明

使用本项目即表示你已阅读并同意 [免责声明](DISCLAIMER.md)。

## 许可证

MIT，详见 [LICENSE](LICENSE)
