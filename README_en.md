# Channel Manager Bot 3.5

![Web Admin 3.5](https://img.shields.io/badge/Web%20Admin-3.5-1f6feb?style=for-the-badge)

[![Docker Pulls](https://img.shields.io/docker/pulls/leduchuong/telegram_mediachanel_manager_bot?logo=docker&label=Docker%20Pulls&style=flat-square)](https://hub.docker.com/r/leduchuong/telegram_mediachanel_manager_bot)
[![GitHub Stars](https://img.shields.io/github/stars/leduchuong48-byte/telegram_chanel_manager_bot?style=flat-square)](https://github.com/leduchuong48-byte/telegram_chanel_manager_bot/stargazers)
[![GitHub Forks](https://img.shields.io/github/forks/leduchuong48-byte/telegram_chanel_manager_bot?style=flat-square)](https://github.com/leduchuong48-byte/telegram_chanel_manager_bot/network/members)
[![GitHub Issues](https://img.shields.io/github/issues/leduchuong48-byte/telegram_chanel_manager_bot?style=flat-square)](https://github.com/leduchuong48-byte/telegram_chanel_manager_bot/issues)
[![License](https://img.shields.io/github/license/leduchuong48-byte/telegram_chanel_manager_bot?style=flat-square)](https://github.com/leduchuong48-byte/telegram_chanel_manager_bot/blob/main/LICENSE)
[![Build: Passing](https://img.shields.io/badge/Build-Passing-brightgreen.svg)](#)
[![Platform: ARM64/AMD64](https://img.shields.io/badge/Platform-ARM64%2FAMD64-blue.svg)](#)

[中文](README.md)

> Better alternative to manual Telegram admin workflows for Telegram operations.

A self-hosted Telegram management tool that combines Bot workflows with a Web Admin panel for tag operations, cleaning tools, media filters, and more consistent operator UX.

## Why this tool?

If your Telegram maintenance flow depends on commands, scattered scripts, and inconsistent admin pages, simple tasks like editing tags, previewing results, and switching between tools become slower than they should be. Version 3.5 focuses on a more stable, more unified, and more practical Web Admin workflow.

## Why This Project Is Useful (Pain Points)

- Tag management used to feel like raw text maintenance instead of a proper workspace for editing and previewing.
- Different admin pages behaved differently, which increased friction and the chance of mistakes.
- Web Admin changes and actual Bot output were not aligned enough, so operators still had to guess the final result.

## What the Project Does (Features)

- A dual-mode admin design with a Tag Workspace for continuous editing and preview, plus Tool Pages for configuration and maintenance actions.
- Better tag workflows with sections, multi-select, merge, rename rules, section ordering, tag moving, and fixed Telegram preview.
- Shared tag files and alias rules across Web Admin and Bot commands for more predictable behavior.

## ⚡️ Quick Start (Run in 3 seconds)

```bash
docker run -d --name telegram_mediachanel_manager_bot --restart unless-stopped -p 1009:8000 -v /path/to/data:/app/data -v /path/to/sessions:/app/sessions leduchuong/telegram_mediachanel_manager_bot:latest
```

> In production, inject secrets through external config and mounted files. Do not publish real tokens, session data, group data, or personal information.

## Docker Compose (Portainer / NAS ready)

```yaml
services:
  app:
    image: leduchuong/telegram_mediachanel_manager_bot:latest
    container_name: telegram_mediachanel_manager_bot
    restart: unless-stopped
    environment:
      - TZ=UTC
      - LOG_LEVEL=INFO
    ports:
      - "1009:8000"
    volumes:
      - ./data:/app/data
      - ./sessions:/app/sessions
```

## GitHub Topics (pick at least 5)

`#telegram` `#selfhosted` `#homelab` `#nas` `#bot` `#automation` `#webadmin`

## 📈 Visual Add-ons (Profile Style)

<p align="left"> <img src="https://komarev.com/ghpvc/?username=leduchuong48-byte&label=Repo%20views&color=0e75b6&style=flat" alt="leduchuong48-byte" /> </p>

<p>
  <img align="left" src="https://github-readme-stats-sigma-five.vercel.app/api/top-langs?username=leduchuong48-byte&show_icons=true&locale=en&layout=compact" alt="top-langs" />
  <img align="center" src="https://github-readme-stats-sigma-five.vercel.app/api?username=leduchuong48-byte&show_icons=true&locale=en" alt="stats" />
</p>

<p><img align="center" src="https://github-readme-streak-stats.herokuapp.com/?user=leduchuong48-byte" alt="streak" /></p>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=leduchuong48-byte/telegram_chanel_manager_bot&type=Date&theme=dark" />
  <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=leduchuong48-byte/telegram_chanel_manager_bot&type=Date" />
  <img alt="Star History" src="https://api.star-history.com/svg?repos=leduchuong48-byte/telegram_chanel_manager_bot&type=Date" />
</picture>

## 🧰 Languages and Tools

<p align="left"><img src="https://skillicons.dev/icons?i=python,docker" alt="tech stack"/></p>

## Getting Started

### Prerequisites

- Python 3.11, or Docker / Docker Compose.
- Telegram Bot configuration, optional Telethon configuration, and persistent data directories.

### Installation

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

### Run

```bash
python3 web_app.py
```

## Usage Example

```bash
docker compose up -d --build
```

## What's New in 3.5

- Better Web Admin UX with a split between Tag Workspace and Tool Pages, plus unified structure, buttons, feedback, scrolling rules, and visual hierarchy.
- Stronger tag workflow with managed groups on the left, section editing in the center, fixed Telegram preview on the right, and less reliance on unstable overlays.
- Better tag operations including rename rules, multi-select mode, merge to a target tag, create tags inside sections, section reordering, cross-section moves, and new-section moves.
- Stronger Bot/Admin consistency through shared tag files and alias rules, with previews closer to actual Bot output.
- Unified target-group behavior so `tags`, `cleaner`, `media_filter`, and `tools` only show `bot_can_manage == true` groups.
- Practical fixes including `/account` 404 repair, better blocked-word readability, tags-page interaction fixes, and less scrolling friction in large sections.

## Where to Get Help

- Issues: https://github.com/leduchuong48-byte/telegram_chanel_manager_bot/issues
- Discussions: https://github.com/leduchuong48-byte/telegram_chanel_manager_bot/discussions
- For bug reports, include anonymized repro steps, screenshots, and config excerpts when possible.

## Maintainers and Contributors

- Maintainer: @leduchuong48-byte
- Contributing: [CONTRIBUTING.md](CONTRIBUTING.md)

## 🤝 Connect

- GitHub: https://github.com/leduchuong48-byte
- Repository: https://github.com/leduchuong48-byte/telegram_chanel_manager_bot

## Disclaimer

By using this project, you acknowledge and agree to the [Disclaimer](DISCLAIMER.md).

## License

MIT, see [LICENSE](LICENSE)
