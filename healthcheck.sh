#!/usr/bin/env sh
set -e

if [ ! -r /proc/1/cmdline ]; then
  echo "missing /proc/1/cmdline" >&2
  exit 1
fi

cmdline="$(tr '\0' ' ' < /proc/1/cmdline)"
case "$cmdline" in
  *web_app.py*|*tg_media_dedupe_bot*) ;;
  *)
    echo "unexpected cmdline: $cmdline" >&2
    exit 1
    ;;
esac

python - <<'PY'
import os
import urllib.request

from tg_media_dedupe_bot.config import load_config

cfg = load_config()
if not cfg.bot_token:
    raise SystemExit("missing TG_BOT_TOKEN")

checks = ["http://127.0.0.1:8000/health"]
port_raw = os.getenv("HEALTH_PORT", "8080").strip()
if port_raw and port_raw != "0":
    checks.append(f"http://127.0.0.1:{port_raw}/")

for url in checks:
    with urllib.request.urlopen(url, timeout=3) as resp:
        if int(getattr(resp, "status", 0)) != 200:
            raise SystemExit(f"health endpoint failed: {url}")
PY
