# Telegram Channel Manager Bot

![Overview Dashboard](docs/ui/dashboard-v4.png)

[![Docker Pulls](https://img.shields.io/docker/pulls/leduchuong/telegram_mediachanel_manager_bot?logo=docker&label=Docker%20Pulls&style=flat-square)](https://hub.docker.com/r/leduchuong/telegram_mediachanel_manager_bot)
[![GitHub Stars](https://img.shields.io/github/stars/leduchuong48-byte/telegram_chanel_manager_bot?style=flat-square)](https://github.com/leduchuong48-byte/telegram_chanel_manager_bot/stargazers)
[![GitHub Forks](https://img.shields.io/github/forks/leduchuong48-byte/telegram_chanel_manager_bot?style=flat-square)](https://github.com/leduchuong48-byte/telegram_chanel_manager_bot/network/members)
[![GitHub Issues](https://img.shields.io/github/issues/leduchuong48-byte/telegram_chanel_manager_bot?style=flat-square)](https://github.com/leduchuong48-byte/telegram_chanel_manager_bot/issues)
[![License](https://img.shields.io/github/license/leduchuong48-byte/telegram_chanel_manager_bot?style=flat-square)](https://github.com/leduchuong48-byte/telegram_chanel_manager_bot/blob/main/LICENSE)
[![Build: Passing](https://img.shields.io/badge/Build-Passing-brightgreen.svg)](#)
[![Platform: ARM64/AMD64](https://img.shields.io/badge/Platform-ARM64%2FAMD64-blue.svg)](#)

[中文](README.md)

> Better alternative to legacy admin bot panel for E-ink devices.

Telegram Channel Manager Bot is a self-hosted operations backend for Telegram channels/groups, combining media dedupe, cleaner workflows, tag maintenance, controller management, and Web Admin tooling.

## Why this tool?

Version `3.5` already delivered core admin features, but operational workflows became fragmented as cleaner jobs, task tracking, and permission boundaries grew more complex. `4.0` is a major console redesign focused on clarity, operator speed, and diagnostics.

## 4.0 vs 3.5 (Major Upgrade)

- New operations overview dashboard with clearer state/risk/action hierarchy.
- New task result center to read outcome, cause, and evidence in one place.
- Cleaner/Tools/Tags workflows now follow a more consistent operations path.
- Identity boundaries are clearer: Web users, Telegram controllers, execution session.
- Reworked tag workbench better suited for long-term maintenance.

## UI Preview (New 4.0 UI)

![Dashboard](docs/ui/dashboard-v4.png)
![Task Center](docs/ui/task-center-v4.png)
![Cleaner](docs/ui/cleaner-v4.png)
![Tags](docs/ui/tags-v4.png)
![Controllers](docs/ui/controllers-v4.png)

## ⚡️ Quick Start (Run in 3 seconds)

```bash
docker run -d --name telegram_mediachanel_manager_bot --restart unless-stopped -p 1009:8000 --env-file .env -v $(pwd)/config.json:/app/config.json -v $(pwd)/data:/app/data -v $(pwd)/sessions:/app/sessions -v $(pwd)/backups:/app/backups leduchuong/telegram_mediachanel_manager_bot:latest
```

## Docker Compose (Portainer / NAS ready)

Copy this into Portainer stacks and hit Deploy. Done.

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

## Why upgrade to 4.0

Upgrade from `3.5` to `4.0` is strongly recommended. The value is not just visual polish: the new release significantly improves operational clarity, task diagnostics, and permission boundary management.

## Where to get help

- Issues: https://github.com/leduchuong48-byte/telegram_chanel_manager_bot/issues

## Disclaimer

By using this project, you agree to [DISCLAIMER.md](DISCLAIMER.md).
