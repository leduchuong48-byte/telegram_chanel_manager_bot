<h1 align="center">Telegram Channel Manager Bot</h1>

<p align="center">
<a href="https://github.com/leduchuong48-byte/telegram_chanel_manager_bot/releases"><img alt="Release" src="https://img.shields.io/github/v/release/leduchuong48-byte/telegram_chanel_manager_bot?display_name=tag"></a>
<a href="https://hub.docker.com/r/leduchuong/telegram_mediachanel_manager_bot"><img alt="Docker Pulls" src="https://img.shields.io/docker/pulls/leduchuong/telegram_mediachanel_manager_bot?logo=docker"></a>
<a href="https://github.com/leduchuong48-byte/telegram_chanel_manager_bot/stargazers"><img alt="Stars" src="https://img.shields.io/github/stars/leduchuong48-byte/telegram_chanel_manager_bot"></a>
<a href="https://github.com/leduchuong48-byte/telegram_chanel_manager_bot/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/github/license/leduchuong48-byte/telegram_chanel_manager_bot"></a>
</p>

<h3 align="center">
  <a href="README_en.md">English</a><span> · </span>
  <a href="https://github.com/leduchuong48-byte/telegram_chanel_manager_bot/issues">报告问题</a>
  <span> · </span>
  <a href="https://github.com/leduchuong48-byte/telegram_chanel_manager_bot/discussions">讨论</a>
</h3>

## 概述

Telegram Channel Manager Bot 是一个面向 Telegram 群组/频道运营的自托管后台，提供消息清洗、任务结果中心、标签工作台、控制用户管理与系统诊断能力。

## 界面

### Web 管理台（4.0）

> 4.0 为 UI 大版本升级，以下为新版后台页面。

![Dashboard](https://raw.githubusercontent.com/leduchuong48-byte/telegram_chanel_manager_bot/main/docs/ui/dashboard-v4.png)
![Task Center](https://raw.githubusercontent.com/leduchuong48-byte/telegram_chanel_manager_bot/main/docs/ui/task-center-v4.png)
![Cleaner](https://raw.githubusercontent.com/leduchuong48-byte/telegram_chanel_manager_bot/main/docs/ui/cleaner-v4.png)
![Tags](https://raw.githubusercontent.com/leduchuong48-byte/telegram_chanel_manager_bot/main/docs/ui/tags-v4.png)
![Controllers](https://raw.githubusercontent.com/leduchuong48-byte/telegram_chanel_manager_bot/main/docs/ui/controllers-v4.png)

## 支持

| 类别 | 支持 |
|---|---|
| 运行方式 | Web Admin + Telegram 控制用户 |
| 主要场景 | 消息清洗、任务跟踪、标签维护、权限边界管理 |
| 部署 | Docker / Docker Compose / NAS / Portainer |

## 安装

```bash
git clone https://github.com/leduchuong48-byte/telegram_chanel_manager_bot.git
cd telegram_chanel_manager_bot
cp .env.example .env
cp config.json.example config.json
```

## Docker 容器

```bash
docker pull leduchuong/telegram_mediachanel_manager_bot:latest
```

```bash
docker run -d --name telegram_mediachanel_manager_bot --restart unless-stopped -p 1009:8000 --env-file .env -v $(pwd)/config.json:/app/config.json -v $(pwd)/data:/app/data -v $(pwd)/sessions:/app/sessions -v $(pwd)/backups:/app/backups leduchuong/telegram_mediachanel_manager_bot:latest
```

```yaml
services:
  telegram_mediachanel_manager_bot:
    image: leduchuong/telegram_mediachanel_manager_bot:latest
    container_name: telegram_mediachanel_manager_bot
    restart: unless-stopped
    ports:
      - "1009:8000"
    env_file:
      - .env
    volumes:
      - ./config.json:/app/config.json
      - ./data:/app/data
      - ./sessions:/app/sessions
      - ./backups:/app/backups
```

## 配置

- 核心运行配置：`config.json`
- 环境变量：`.env`
- 建议先完成 Web 登录账号与执行会话配置，再进行批量运营任务。

## 4.0 升级说明（对比 3.5）

- 控制台信息架构重做，状态与风险更可见。
- 新任务结果中心，强调“结论 -> 原因 -> 证据”。
- Cleaner / Tags / 控制用户等页面工作流统一。
- 建议现有 3.5 用户升级到 4.0。

## 支持与贡献

- Issues: https://github.com/leduchuong48-byte/telegram_chanel_manager_bot/issues
- Discussions: https://github.com/leduchuong48-byte/telegram_chanel_manager_bot/discussions

## 免责声明

使用本项目即表示你已阅读并同意 [DISCLAIMER.md](DISCLAIMER.md)。
