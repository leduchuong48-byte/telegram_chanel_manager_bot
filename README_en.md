# Channel Manager Bot

![Channel Manager Bot 3.5](docs/ui/web-tags-workbench-3-5.png)

[![Docker Pulls](https://img.shields.io/docker/pulls/leduchuong/telegram_mediachanel_manager_bot?logo=docker&label=Docker%20Pulls&style=flat-square)](https://hub.docker.com/r/leduchuong/telegram_mediachanel_manager_bot)
[![GitHub Stars](https://img.shields.io/github/stars/leduchuong48-byte/telegram_chanel_manager_bot?style=flat-square)](https://github.com/leduchuong48-byte/telegram_chanel_manager_bot/stargazers)
[![GitHub Forks](https://img.shields.io/github/forks/leduchuong48-byte/telegram_chanel_manager_bot?style=flat-square)](https://github.com/leduchuong48-byte/telegram_chanel_manager_bot/network/members)
[![GitHub Issues](https://img.shields.io/github/issues/leduchuong48-byte/telegram_chanel_manager_bot?style=flat-square)](https://github.com/leduchuong48-byte/telegram_chanel_manager_bot/issues)
[![License](https://img.shields.io/github/license/leduchuong48-byte/telegram_chanel_manager_bot?style=flat-square)](https://github.com/leduchuong48-byte/telegram_chanel_manager_bot/blob/main/LICENSE)
[![Platform: ARM64/AMD64](https://img.shields.io/badge/Platform-ARM64%2FAMD64-blue.svg)](#)

[中文](README.md)

> A self-hosted Bot + Web Admin workspace for Telegram group and channel maintenance, focused on faster tag operations, more consistent tools, and better long-term maintainability.

## Why this tool?

Many Telegram maintenance tools can technically finish the job, but they still make the daily workflow awkward: tag management feels like editing raw text, tool pages behave inconsistently, and you still have to guess what the Bot output will look like after changing rules. Channel Manager Bot is built to improve that entire maintenance loop, not just one isolated feature.

## What changed in 3.5

- **A stronger Web Admin UX**: the frontend is now split into two clear modes, with Tag Workspace for continuous editing and preview, and Tool Pages for configuration, maintenance, and execution tasks.
- **A real tag workspace**: tag management moves away from the old plain-text flow into a section-based workbench with managed groups on the left, editable sections in the center, and a fixed Telegram preview on the right.
- **Better tag operations**: rename rules, multi-select mode, merge-to-target, add tags inside sections, reorder sections, move tags across sections, and create-and-move workflows are now part of the main editing experience.
- **Stronger Bot/Admin consistency**: Web Admin and Bot commands continue sharing the same tag files and alias rules, and the Telegram preview is closer to real Bot output.
- **Unified target selection**: `tags`, `cleaner`, `media_filter`, and `tools` consistently show only targets where `bot_can_manage == true`.
- **Usability fixes**: `/account` routing, blocked-word readability, tags-page interaction feedback, and large-section editing flow all received practical improvements.

## Web Admin UI Preview

### Tag Workspace

![Tag Workspace](docs/ui/web-tags-workbench-3-5.png)

The new tag workspace keeps managed groups, editable sections, and Telegram preview in one place, making long editing sessions feel like an actual workspace rather than a scattered collection of panels.

### Tool Pages

![Tool Pages](docs/ui/web-tools-3-5.png)

Tool pages now follow a much more consistent rhythm: choose the target, run the maintenance action, and read feedback in the same interaction pattern.

### Entry And Overview

![Dashboard](docs/ui/web-dashboard-3-5.png)

The dashboard acts as a cleaner entry point for Bot settings, tag management, configuration editing, and logs, with less visual noise and better hierarchy.

## Core Capabilities

- **Tag workflow management**: sections, aliases, ordering, moving, preview, and batch organization.
- **Maintenance tools**: cleaner, media filter, and operational tools under a more unified UI model.
- **Shared Bot/Web rules**: the same tag and alias files drive both editing and execution.
- **Self-hosting friendly**: works well with Docker, Compose, and NAS-style persistent volumes.

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
      - TZ=UTC
      - LOG_LEVEL=INFO
    volumes:
      - ./data:/app/data
      - ./sessions:/app/sessions
```

## Recommended Usage

- Use `/tags` as the primary place for tag organization; raw text should stay a compatibility or advanced path.
- For complex tag cleanup, combine single-tag editing, multi-select mode, and Telegram preview.
- In production, keep tokens, sessions, group data, and databases in mounted storage, not in the repository or image layer.

## Project Layout

- `app/`: FastAPI Web Admin
- `tg_media_dedupe_bot/`: Bot core logic
- `scripts/`: maintenance and self-check scripts
- `WEB_ADMIN_README.md`: more operational, Web Admin-specific usage notes

## Where to Get Help

- Issues: https://github.com/leduchuong48-byte/telegram_chanel_manager_bot/issues
- Discussions: https://github.com/leduchuong48-byte/telegram_chanel_manager_bot/discussions
- Docker Hub: https://hub.docker.com/r/leduchuong/telegram_mediachanel_manager_bot

## Disclaimer

By using this project, you acknowledge and agree to the [Disclaimer](DISCLAIMER.md).

## License

MIT, see [LICENSE](LICENSE)
