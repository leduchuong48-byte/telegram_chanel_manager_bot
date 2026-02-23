# Telegram Chanel Manager Bot

[中文](README.md)

Telegram Chanel Manager Bot is a `Telethon + FastAPI` project for Telegram channel/group operations, including media de-duplication, historical scanning, tag workflows, and web-based administration.

## Why This Project Is Useful (Pain Points)

In multi-channel/group environments, duplicate media and noisy content accumulate quickly. Manual cleanup is costly and often inconsistent. Historical backfill and tag normalization are also hard to maintain with ad-hoc scripts. This project unifies monitoring, de-duplication, tagging, and configuration management into one workflow.

## What the Project Does (Features)

- Real-time media de-duplication with delete/dry-run modes
- Historical scan and batch processing via Telethon
- Tag extraction, pin index generation, rebuild, and aliasing
- Web admin panel with auth, config editing, and logs
- SQLite-backed persistence for rules and runtime state

## Getting Started

### Prerequisites

- Python 3.11+
- Telegram Bot Token
- Optional: Telegram API ID / API HASH (required for historical scan)
- Docker and Docker Compose (recommended)

### Run with Docker

```bash
cp .env.example .env
# edit .env and config.json

docker compose up -d --build
```

Open: `http://localhost:1009`

### Run Locally

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python web_app.py
```

## Where to Get Help

- Issues: `https://github.com/leduchuong48-byte/telegram_chanel_manager_bot/issues`
- Include sanitized logs and reproducible steps

## Maintainers and Contributors

- Maintainer: `@leduchuong48-byte`

## Disclaimer

By using this project, you acknowledge and agree to the [Disclaimer](DISCLAIMER.md).
