# Channel Manager Bot

![Channel Manager Bot 3.5](docs/ui/web-tags-workbench-3-5.png)

[![Docker Pulls](https://img.shields.io/docker/pulls/leduchuong/telegram_mediachanel_manager_bot?logo=docker&label=Docker%20Pulls&style=flat-square)](https://hub.docker.com/r/leduchuong/telegram_mediachanel_manager_bot)
[![GitHub Stars](https://img.shields.io/github/stars/leduchuong48-byte/telegram_chanel_manager_bot?style=flat-square)](https://github.com/leduchuong48-byte/telegram_chanel_manager_bot/stargazers)
[![GitHub Forks](https://img.shields.io/github/forks/leduchuong48-byte/telegram_chanel_manager_bot?style=flat-square)](https://github.com/leduchuong48-byte/telegram_chanel_manager_bot/network/members)
[![GitHub Issues](https://img.shields.io/github/issues/leduchuong48-byte/telegram_chanel_manager_bot?style=flat-square)](https://github.com/leduchuong48-byte/telegram_chanel_manager_bot/issues)
[![License](https://img.shields.io/github/license/leduchuong48-byte/telegram_chanel_manager_bot?style=flat-square)](https://github.com/leduchuong48-byte/telegram_chanel_manager_bot/blob/main/LICENSE)
[![Platform: ARM64/AMD64](https://img.shields.io/badge/Platform-ARM64%2FAMD64-blue.svg)](#)

[中文](README.md)

> A self-hosted Bot + Web Admin workspace for Telegram group and channel maintenance, designed for long-term tag organization, tool execution, and cleaner operational flow.

## Why this tool?

Many Telegram maintenance tools can technically do the job, but the real pain appears in repeated daily use: tag management feels like raw text maintenance, each tool page behaves differently, and after editing rules you still have to guess how the Bot will actually render the result. Channel Manager Bot 3.5 focuses on those real maintenance scenarios and turns them into a smoother, more stable workspace.

## Best-Fit Scenarios

- **Ongoing tag maintenance**: when your groups or channels need frequent tag cleanup, renaming, merging, section organization, and previewing, the tag workspace is much easier to live with than raw text editing.
- **Daily moderation and maintenance work**: when you keep switching between cleaner, media filter, maintenance tools, and account pages, a unified tool-page structure reduces friction.
- **Bot and admin panel working together**: when you want Web Admin changes to stay close to actual Bot output, shared tag and alias rules help reduce surprises.
- **NAS / Docker self-hosting**: when you care about stable deployment, persistent directories, and long-running operations, this fits better than a throwaway script stack.

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

## Common Ways To Use It

### 1. As a tag-operations console

Useful when your content flow is stable but the tag system keeps growing in complexity. `/tags` becomes the main place to maintain structure, aliases, and preview behavior instead of repeatedly editing raw text.

### 2. As a daily maintenance toolbox

Useful when admins repeatedly run cleaner, filtering, and maintenance actions. The unified tool-page rhythm makes these operations easier to repeat without context switching.

### 3. As a self-hosted Bot operations panel

Useful when you want Bot configuration, Web Admin, and data directories to live in one self-hosted environment, especially on Docker, NAS, or a small server.

## Full Bot Command Reference

This project is not only a Web Admin panel. A large part of the real maintenance workflow still happens directly through Bot commands, so the full command list is kept in the main README as well.

### Basics And Status

- `/start`: show welcome info, current mode, and Web Admin entry
- `/help`: show the full command list
- `/menu`: open the main control menu
- `/ping`: health check
- `/stats`: show current chat statistics
- `/mode`: inspect or switch the current chat mode
- `/status`: show active task status for the current chat

### Mode And Delete Switches

- `/enable_delete`: enable delete mode
- `/disable_delete`: disable delete mode
- `/dry_run_on`: enable dry-run
- `/dry_run_off`: disable dry-run

### History Scan And Duplicate Processing

- `/scan [N]`: scan history of the current group/channel, `N=message count`, `0=no limit`
- `/scan <chat> [N]`: scan an explicit target, where `<chat>` can be `-100...`, `@username`, or an invite link
- `/scan_delete [N]`: scan history and delete duplicates, requires dry-run off and delete enabled
- `/scan_delete <chat> [N]`: same as above, with an explicit target
- `/scan_status`: show scan progress
- `/scan_stop`: stop the current scan task
- `/flush [N]`: clear the pending deletion queue, default `100`, max `1000`

### Tag Directory And Tag Generation

- `/tags_pin [N] [MAX]`: scan hashtags from history, generate a pinned tag directory
- `/tags_pin <chat> [N] [MAX]`: generate a pinned tag directory for an explicit target
- `/tag_pin`: alias of `/tags_pin`
- `/tag_build`: scan historical media messages and auto-append tags based on the tag library
- `/tag_build_status`: show `tag_build` progress
- `/tag_build_stop`: stop `tag_build`
- `/tag_rebuild [N|all]`: rebuild historical message tags and remove blocked text
- `/tag_update`: update tags by cleaning blacklist text, blocked text, and rebuilding the directory
- `/tag_stop`: stop tag-related tasks in the current chat (`scan`, `tags_pin`, `tag_build`, `tag_rebuild`)
- `/tag_count [N]`: set the maximum number of tags to append per message, range `1-10`

### Tag Alias And Text Rules

- `/tag_rename [global] #old=#new`: set a tag alias rule
- `/tag_rename [global] list`: list alias rules
- `/tag_rename [global] del #old_tag`: delete an alias rule
- `/text_block [global] list`: list blocked keywords
- `/text_block [global] add keyword`: add a blocked keyword
- `/text_block [global] del keyword`: delete a blocked keyword

### Session / Telethon Maintenance

These commands are usually better used in private chat:

- `/session_status`: inspect current Telethon session state
- `/session_login`: start phone login flow
- `/session_qr`: start QR login flow
- `/session_code`: submit the login code
- `/session_password`: submit the 2FA password
- `/session_logout`: log out the current session
- `/session_reset`: reset the current session

### Usage Notes

- Many history-scan and tag commands depend on a Telethon user session, not only the Bot token.
- Commands that accept `<chat>` usually support `-100...`, `@username`, and invite-link forms.
- For destructive operations, it is safer to observe output under dry-run before switching to real deletion.

### Recommended Minimal Flow

When taking over a new group or channel, a practical starting order is:

1. `/ping`
2. `/status`
3. `/session_status`
4. `/tags_pin` or the `/tags` page
5. `/tag_update`
6. only then decide whether `/scan_delete` is appropriate

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
