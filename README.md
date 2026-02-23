# Telegram Chanel Manager Bot

[English](README_en.md)

Telegram Chanel Manager Bot 是一个基于 `Telethon + FastAPI` 的 Telegram 群组/频道管理与媒体去重工具，提供实时去重、历史扫描、标签处理与 Web 管理面板。

## 为什么有用（痛点）

在多群组/频道场景里，重复媒体与噪音消息会快速堆积，人工清理成本高且容易漏删；当需要回溯历史内容、统一标签和规则时，纯手工操作几乎不可持续。本项目把“监听、去重、标签整理、可视化配置”整合到一个流程里，降低长期维护成本。

## 项目做什么（功能概览）

- 实时去重：按媒体标识检测重复内容并执行删除或 dry-run
- 历史处理：支持基于 Telethon 的回溯扫描与批处理
- 标签能力：标签提取、目录置顶、标签重建与别名替换
- Web 管理：登录鉴权、配置编辑、规则管理与日志查看
- 数据持久化：SQLite 存储统计、规则和运行状态

## 如何快速开始（Getting Started）

### 环境要求

- Python 3.11+
- Telegram Bot Token
- （可选）Telegram API ID / API HASH（历史扫描功能需要）
- Docker 与 Docker Compose（推荐）

### Docker 运行

```bash
cp .env.example .env
# 编辑 .env 与 config.json

docker compose up -d --build
```

访问：`http://localhost:1009`

### 本地运行

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python web_app.py
```

### 目录说明

- `tg_media_dedupe_bot/`：Bot 核心逻辑与命令处理
- `app/`：Web 管理面板（FastAPI）
- `config.json`：Web 与 Bot 运行配置
- `data/`、`sessions/`、`backups/`、`logs/`：运行时数据目录（默认忽略）

## 在哪里获得帮助

- Issue：`https://github.com/leduchuong48-byte/telegram_chanel_manager_bot/issues`
- 提问时请附：复现步骤、日志片段、脱敏后的配置信息

## 维护者与贡献者

- Maintainer: `@leduchuong48-byte`

## 免责声明

使用本项目即表示你已阅读并同意 [免责声明](DISCLAIMER.md)。
