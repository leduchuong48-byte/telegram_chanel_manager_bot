<h1 align="center">Telegram Channel Manager Bot</h1>

<p align="center">
<a href="https://github.com/leduchuong48-byte/telegram_chanel_manager_bot/releases"><img alt="Release" src="https://img.shields.io/github/v/release/leduchuong48-byte/telegram_chanel_manager_bot?display_name=tag"></a>
<a href="https://hub.docker.com/r/leduchuong/telegram_mediachanel_manager_bot"><img alt="Docker Pulls" src="https://img.shields.io/docker/pulls/leduchuong/telegram_mediachanel_manager_bot?logo=docker"></a>
<a href="https://github.com/leduchuong48-byte/telegram_chanel_manager_bot/stargazers"><img alt="Stars" src="https://img.shields.io/github/stars/leduchuong48-byte/telegram_chanel_manager_bot"></a>
<a href="https://github.com/leduchuong48-byte/telegram_chanel_manager_bot/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/github/license/leduchuong48-byte/telegram_chanel_manager_bot"></a>
</p>

<h3 align="center">
  <a href="README.md">中文</a><span> · </span>
  <a href="https://github.com/leduchuong48-byte/telegram_chanel_manager_bot/issues">Report Bug</a>
  <span> · </span>
  <a href="https://github.com/leduchuong48-byte/telegram_chanel_manager_bot/discussions">Discussions</a>
</h3>

## Overview

Telegram Channel Manager Bot is a self-hosted Telegram operations backend for cleanup workflows, task tracking, tag maintenance, controller management, and diagnostics.

## Interface

### Web Admin (4.0)

> Version 4.0 ships a major UI redesign.

![Dashboard](https://raw.githubusercontent.com/leduchuong48-byte/telegram_chanel_manager_bot/main/docs/ui/dashboard-v4.png)
![Task Center](https://raw.githubusercontent.com/leduchuong48-byte/telegram_chanel_manager_bot/main/docs/ui/task-center-v4.png)
![Cleaner](https://raw.githubusercontent.com/leduchuong48-byte/telegram_chanel_manager_bot/main/docs/ui/cleaner-v4.png)
![Tags](https://raw.githubusercontent.com/leduchuong48-byte/telegram_chanel_manager_bot/main/docs/ui/tags-v4.png)
![Controllers](https://raw.githubusercontent.com/leduchuong48-byte/telegram_chanel_manager_bot/main/docs/ui/controllers-v4.png)

## Support Matrix

| Category | Support |
|---|---|
| Runtime | Web Admin + Telegram controller workflow |
| Core Use Cases | Cleanup, task diagnostics, tag maintenance, permission boundaries |
| Deployment | Docker / Docker Compose / NAS / Portainer |

## Installation

```bash
git clone https://github.com/leduchuong48-byte/telegram_chanel_manager_bot.git
cd telegram_chanel_manager_bot
cp .env.example .env
cp config.json.example config.json
```

## Docker

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

## Configuration

- Runtime config: `config.json`
- Environment: `.env`
- Configure Web user + execution session before production operations.

## Upgrade Notes (4.0 vs 3.5)

- Console information architecture redesigned.
- New task result center (`outcome -> cause -> evidence`).
- Unified workflows across Cleaner / Tags / Controllers.
- Upgrade from 3.5 to 4.0 is recommended.

## Support & Contribution

- Issues: https://github.com/leduchuong48-byte/telegram_chanel_manager_bot/issues
- Discussions: https://github.com/leduchuong48-byte/telegram_chanel_manager_bot/discussions

## Disclaimer

By using this project, you agree to [DISCLAIMER.md](DISCLAIMER.md).
