from __future__ import annotations

import argparse
import asyncio
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tg_media_dedupe_bot")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("run-bot", help="运行 Bot API 实时去重机器人")

    scan = sub.add_parser("scan", help="使用 Telethon 回溯扫描（可选）")
    scan.add_argument("--chat", required=True, help="Telethon 可识别的 chat（id/username/invite 等）")
    scan.add_argument("--limit", type=int, default=0, help="扫描条数限制；0 表示不限制")
    scan.add_argument("--delete", action="store_true", help="实际删除（默认仅 dry-run）")
    scan.add_argument("--reverse", action="store_true", help="从旧到新扫描（推荐，幂等更直观）")
    scan.add_argument("--as-bot", action="store_true", help="使用 TG_BOT_TOKEN 以 bot 身份登录（否则使用用户账号登录）")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "run-bot":
        from tg_media_dedupe_bot.telegram_bot import run_bot

        run_bot()
        return 0

    if args.command == "scan":
        from tg_media_dedupe_bot.telethon_scan import run_scan

        asyncio.run(
            run_scan(
                chat=args.chat,
                limit=args.limit,
                delete=args.delete,
                reverse=args.reverse,
                as_bot=bool(args.as_bot),
            )
        )
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
