#!/usr/bin/env sh
set -e

if [ ! -r /proc/1/cmdline ]; then
  echo "missing /proc/1/cmdline" >&2
  exit 1
fi

cmdline="$(tr '\0' ' ' < /proc/1/cmdline)"
case "$cmdline" in
  *tg_media_dedupe_bot*) ;;
  *)
    echo "unexpected cmdline: $cmdline" >&2
    exit 1
    ;;
esac

python - <<'PY'
from tg_media_dedupe_bot.config import load_config

cfg = load_config()
if not cfg.bot_token:
    raise SystemExit("missing TG_BOT_TOKEN")
PY
