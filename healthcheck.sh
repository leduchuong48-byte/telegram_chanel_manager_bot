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
import json
import urllib.request

with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=3) as resp:
    if int(getattr(resp, "status", 0)) != 200:
        raise SystemExit("health endpoint returned non-200")
    payload = json.loads(resp.read().decode("utf-8"))
    if payload.get("status") != "ok":
        raise SystemExit(f"health endpoint unhealthy: {payload}")
PY
