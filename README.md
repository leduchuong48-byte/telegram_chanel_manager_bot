# Telegram 群组/频道媒体管理器

## 项目介绍

面向 Telegram 群组和频道运营者的媒体去重、标签维护与内容治理工具，提供 Bot 运行能力和 FastAPI Web 管理台。

## 解决什么问题

帮助运营者减少重复媒体、历史内容整理和风险内容复核中的重复劳动，并把任务状态、失败原因和人工确认集中到一个管理台。

## 目标用户

适合维护 Telegram 群组或频道内容目录、需要先预览再执行清理的个人运营者和小型团队。

## 功能与边界

### 已实现功能

- 通过 Bot 处理新媒体消息的重复检测，并支持幂等记录与待删除队列。
- 通过 Telethon 用户会话进行历史消息扫描、标签提取、标签重建和标签目录维护。
- Web 管理台提供任务中心、失败任务、日志、会话状态、可见性诊断和运行配置页面。
- 内容治理工作台支持标签治理、广告风险分析、黑名单和人工确认流程。
- 支持转发规则、文本屏蔽、媒体筛选、Bot 控制用户和 AI provider 配置管理。
- 使用 SQLite 保存任务、去重和运行状态，并提供运行时健康检查。

### 边界与风险

- Telegram Bot API 通常只能处理收到的更新；历史扫描需要配置 Telethon 用户会话。
- 删除操作需要目标群组或频道的相应管理员权限，建议先保持 dry-run 并人工复核。
- 公开仓库不包含真实 token、API 凭据、session、数据库、备份或生产媒体；示例配置仅用于初始化。
- 截图使用空数据库和演示登录状态，不能代表已连接 Telegram 账户或真实业务数据。

## 部署方式

- 推荐使用 Docker Compose：复制 .env.example 为 .env，准备 config.json 后执行 docker compose up -d。
- 可直接使用已发布镜像 leduchuong/telegram_mediachanel_manager_bot:4.1。
- Web 管理台默认监听容器 8000 端口；compose 示例映射到主机 1009 端口，可按需调整。
- 也可以按 Dockerfile 构建本地镜像：docker build -t telegram_mediachanel_manager_bot:4.1 .。

## 配置说明

- 复制 config.json.example 为 config.json，并设置 web_admin.secret_key；示例中的 change-me 只能作为占位符。
- 在 config.json 的 bot 中配置 bot_token、api_id、api_hash、target_chat_ids 和 web_tg_session。
- 首次部署建议保持 bot.dry_run=true、bot.delete_duplicates=false，完成权限和可见性检查后再启用写操作。
- .env 仅保留容器日志、兼容路径和 SQLite 路径等基础设置，业务配置以 config.json 为准。

## 使用方式

启动后访问 http://localhost:1009/login，使用 config.json 中的 Web 用户登录。先在执行会话中心确认 Telethon 会话，再从任务中心或内容治理页创建预览任务；确认结果后才执行删除或批量变更。

## 截图

![Web 管理台总览](screenshots/dashboard-desktop.png)

![内容治理工作台](screenshots/content-ops-desktop.png)

![移动端登录页](screenshots/login-mobile.png)

## 安全说明

发布前已从 staging 排除环境文件、真实配置、Telegram session、数据库、备份、缓存和运行日志，并通过内置扫描、Gitleaks 与 TruffleHog。请使用部署环境注入真实凭据，设置强 Web 密钥，限制管理台网络暴露范围，并在启用删除前进行 dry-run 验证。
