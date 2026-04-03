# Channel Manager Bot 3.5

![Web Admin 3.5](https://img.shields.io/badge/Web%20Admin-3.5-1f6feb?style=for-the-badge)

[![Docker Pulls](https://img.shields.io/docker/pulls/leduchuong/telegram_mediachanel_manager_bot?logo=docker&label=Docker%20Pulls&style=flat-square)](https://hub.docker.com/r/leduchuong/telegram_mediachanel_manager_bot)
[![GitHub Stars](https://img.shields.io/github/stars/leduchuong48-byte/telegram_chanel_manager_bot?style=flat-square)](https://github.com/leduchuong48-byte/telegram_chanel_manager_bot/stargazers)
[![GitHub Forks](https://img.shields.io/github/forks/leduchuong48-byte/telegram_chanel_manager_bot?style=flat-square)](https://github.com/leduchuong48-byte/telegram_chanel_manager_bot/network/members)
[![GitHub Issues](https://img.shields.io/github/issues/leduchuong48-byte/telegram_chanel_manager_bot?style=flat-square)](https://github.com/leduchuong48-byte/telegram_chanel_manager_bot/issues)
[![License](https://img.shields.io/github/license/leduchuong48-byte/telegram_chanel_manager_bot?style=flat-square)](https://github.com/leduchuong48-byte/telegram_chanel_manager_bot/blob/main/LICENSE)
[![Build: Passing](https://img.shields.io/badge/Build-Passing-brightgreen.svg)](#)
[![Platform: ARM64/AMD64](https://img.shields.io/badge/Platform-ARM64%2FAMD64-blue.svg)](#)

[English](README_en.md)

> Better alternative to manual Telegram admin workflows for Telegram operations.

一个面向 Telegram 群组与频道管理场景的自托管 Bot + Web Admin 工具，聚焦标签工作流、清洗工具、媒体筛选与后台操作一致性。

## Why this tool?（为什么要做它）

如果日常 Telegram 运维长期依赖命令、零散脚本和风格不统一的管理页面，标签编辑、效果预览、工具切换和权限目标确认都会越来越低效。3.5 版本的目标就是把这些高频维护动作收拢到更稳定、更统一、更适合长期使用的 Web Admin 工作流中。

## 为什么有用（痛点）

- 标签管理过去更像文本维护而不是工作台操作，重命名、合并、移动和预览都不够顺手。
- 多个后台页面的结构、反馈和目标群组选择逻辑不一致，增加了维护成本与误操作概率。
- Web Admin 与 Bot 最终输出之间缺少足够直观的一致性，改完以后仍要反复确认实际效果。

## 项目做什么（功能概览）

- 提供“标签工作台 + 工具页”双模式后台，分别面向持续编辑预览和维护执行操作。
- 标签工作台支持分区管理、多选、批量合并、重命名规则、分区排序、标签移动与固定 TG 预览。
- Web Admin 与 Bot 命令共用同一套标签文件和 alias 规则，尽量减少前后台行为偏差。

## ⚡️ Quick Start (Run in 3 seconds)

```bash
docker run -d --name telegram_mediachanel_manager_bot --restart unless-stopped -p 1009:8000 -v /path/to/data:/app/data -v /path/to/sessions:/app/sessions leduchuong/telegram_mediachanel_manager_bot:latest
```

> 生产环境请通过外部挂载和环境文件注入配置，不要把真实 token、session、群组数据或个人信息写进镜像或仓库。

## Docker Compose（Portainer / NAS 可直接粘贴）

```yaml
services:
  app:
    image: leduchuong/telegram_mediachanel_manager_bot:latest
    container_name: telegram_mediachanel_manager_bot
    restart: unless-stopped
    environment:
      - TZ=Asia/Shanghai
      - LOG_LEVEL=INFO
    ports:
      - "1009:8000"
    volumes:
      - ./data:/app/data
      - ./sessions:/app/sessions
```

## GitHub Topics（建议至少 5 个）

`#telegram` `#selfhosted` `#homelab` `#nas` `#bot` `#automation` `#webadmin`

## 📈 可视化指标（Profile 风格）

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

## 如何快速开始（Getting Started）

### 环境要求

- Python 3.11，或 Docker / Docker Compose。
- Telegram Bot 配置、可选 Telethon 配置，以及持久化数据目录。

### 安装

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

### 运行

```bash
python3 web_app.py
```

## 使用示例

```bash
docker compose up -d --build
```

## 3.5 版本亮点

- Web Admin 前端体验升级：后台页面按“标签工作台 / 工具页”重构，统一页面结构、按钮样式、提示反馈、滚动规则与视觉层级。
- 标签工作台升级：左侧被管理群组、中间标签分区、右侧固定 TG 预览，减少旧浮层编辑的不稳定体验。
- 标签操作增强：支持单标签重命名规则、多选模式、多标签合并到同一目标、分区内新增标签、分区排序、标签移动到其他分区，以及新建分区并移动。
- Bot 联动一致性增强：Web Admin 与 Bot 命令继续共用同一套标签文件和 alias 规则，预览更贴近实际输出。
- 目标群组选择统一：`tags`、`cleaner`、`media_filter`、`tools` 页统一只显示 `bot_can_manage == true` 的群组。
- 可用性修复：修复 `/account` 404、提升屏蔽词列表可读性、修复 tags 页多处脚本与交互反馈问题，并减少大分区场景下的来回滚动。

## 在哪里获得帮助

- Issue: https://github.com/leduchuong48-byte/telegram_chanel_manager_bot/issues
- Discussion: https://github.com/leduchuong48-byte/telegram_chanel_manager_bot/discussions
- 如果要反馈问题，建议附带匿名化后的复现步骤、页面截图和配置片段。

## 维护者与贡献者

- Maintainer: @leduchuong48-byte
- Contributing: [CONTRIBUTING.md](CONTRIBUTING.md)

## 🤝 Connect

- GitHub: https://github.com/leduchuong48-byte
- Repository: https://github.com/leduchuong48-byte/telegram_chanel_manager_bot

## 免责声明

使用本项目即表示你已阅读并同意 [免责声明](DISCLAIMER.md)。

## 许可证

MIT，详见 [LICENSE](LICENSE)
