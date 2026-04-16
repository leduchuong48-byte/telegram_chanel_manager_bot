#!/usr/bin/env bash
set -euo pipefail

WORKDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${COMPOSE_FILE:-$WORKDIR/docker-compose.yml}"
ENV_FILE="${ENV_FILE:-$WORKDIR/.env}"
SERVICE_NAME="${SERVICE_NAME:-telegram_mediachanel_manager_bot}"
NEW_CONTAINER_NAME="${NEW_CONTAINER_NAME:-${SERVICE_NAME}_rolling}"
IMAGE="${IMAGE:-telegram_mediachanel_manager_bot:4.0}"
HEALTHCHECK_URL="${HEALTHCHECK_URL:-}"
HEALTHCHECK_PORT="${HEALTHCHECK_PORT:-${HEALTH_PORT:-8080}}"
HEALTHCHECK_PATH="${HEALTHCHECK_PATH:-/health}"
HEALTHCHECK_TIMEOUT="${HEALTHCHECK_TIMEOUT:-60}"
DRY_RUN="${DRY_RUN:-0}"

log() { printf "%s\n" "$*"; }
run() {
  if [ "$DRY_RUN" = "1" ]; then
    log "DRY_RUN: $*"
  else
    eval "$@"
  fi
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    log "缺少依赖命令: $1"
    exit 1
  fi
}

require_cmd docker
require_cmd python3
if [ -n "$HEALTHCHECK_URL" ]; then
  require_cmd curl
fi

if [ ! -f "$COMPOSE_FILE" ]; then
  log "缺少 compose 文件: $COMPOSE_FILE"
  exit 1
fi

if [ ! -f "$ENV_FILE" ]; then
  log "缺少 env 文件: $ENV_FILE"
  exit 1
fi

if [[ "$IMAGE" == *":latest" ]] || [[ "$IMAGE" == *":LATEST" ]]; then
  log "不允许使用 latest 镜像标签: $IMAGE"
  exit 1
fi

old_id="$(docker inspect -f '{{.Id}}' "$SERVICE_NAME" 2>/dev/null || true)"
if [ -z "$old_id" ]; then
  log "未找到运行容器: $SERVICE_NAME"
  exit 1
fi

if [ ! -f "${COMPOSE_FILE}.bak" ]; then
  run "cp \"$COMPOSE_FILE\" \"${COMPOSE_FILE}.bak\""
fi

python3 - "$SERVICE_NAME" "$WORKDIR" "$ENV_FILE" <<'PY'
import json
import subprocess
import sys
from pathlib import Path

service = sys.argv[1]
workdir = Path(sys.argv[2])
env_path = Path(sys.argv[3])

env_json = subprocess.check_output(
    ["docker", "inspect", "-f", "{{json .Config.Env}}", service], text=True
).strip()
env_list = json.loads(env_json)
actual_env = {}
for item in env_list:
    if "=" in item:
        key, value = item.split("=", 1)
        actual_env[key] = value

expected_env = {}
for raw in env_path.read_text().splitlines():
    line = raw.strip()
    if not line or line.startswith("#"):
        continue
    if line.startswith("export "):
        line = line[len("export ") :].strip()
    if "=" not in line:
        continue
    key, value = line.split("=", 1)
    key = key.strip()
    value = value.strip().strip('"').strip("'")
    if key:
        expected_env[key] = value

missing = []
mismatch = []
for key, value in expected_env.items():
    if key not in actual_env:
        missing.append(key)
    elif actual_env[key] != value:
        mismatch.append((key, value, actual_env[key]))

mount_json = subprocess.check_output(
    ["docker", "inspect", "-f", "{{json .Mounts}}", service], text=True
).strip()
mounts = json.loads(mount_json)
expected_mounts = [
    (str(workdir / "data"), "/app/data"),
    (str(workdir / "sessions"), "/app/sessions"),
]
missing_mounts = []
for src, dst in expected_mounts:
    if not any(m.get("Source") == src and m.get("Destination") == dst for m in mounts):
        missing_mounts.append(f"{src}:{dst}")

errors = []
if missing:
    errors.append("环境变量缺失: " + ", ".join(sorted(missing)))
if mismatch:
    errors.append(
        "环境变量不一致: "
        + ", ".join([f"{k} expected={v} actual={a}" for k, v, a in mismatch])
    )
if missing_mounts:
    errors.append("挂载不一致: " + ", ".join(missing_mounts))

if errors:
    for err in errors:
        print(err, file=sys.stderr)
    raise SystemExit(2)
PY

current_image="$(docker inspect -f '{{.Config.Image}}' "$SERVICE_NAME")"
if [ "$current_image" = "$IMAGE" ]; then
  log "当前容器已是目标镜像: $IMAGE"
  log "old_id=$old_id new_id=$old_id"
  log "health_log=$(docker inspect -f '{{json .State.Health.Log}}' "$SERVICE_NAME")"
  exit 0
fi

existing_new="$(docker inspect -f '{{.Id}}' "$NEW_CONTAINER_NAME" 2>/dev/null || true)"
if [ -n "$existing_new" ]; then
  new_image="$(docker inspect -f '{{.Config.Image}}' "$NEW_CONTAINER_NAME")"
  if [ "$new_image" != "$IMAGE" ]; then
    run "docker rm -f \"$NEW_CONTAINER_NAME\""
  fi
fi

run "docker build -t \"$IMAGE\" \"$WORKDIR\""

if docker inspect "$NEW_CONTAINER_NAME" >/dev/null 2>&1; then
  new_status="$(docker inspect -f '{{.State.Status}}' "$NEW_CONTAINER_NAME")"
  if [ "$new_status" != "running" ]; then
    run "docker start \"$NEW_CONTAINER_NAME\""
  fi
else
  run "docker run -d --name \"$NEW_CONTAINER_NAME\" --restart unless-stopped --user \"${UID:-1000}:${GID:-1000}\" --env-file \"$ENV_FILE\" -v \"$WORKDIR/data:/app/data\" -v \"$WORKDIR/sessions:/app/sessions\" \"$IMAGE\""
fi

rollback() {
  log "触发回滚：移除新容器 $NEW_CONTAINER_NAME"
  run "docker rm -f \"$NEW_CONTAINER_NAME\""
  exit 1
}

start_ts="$(date +%s)"
while true; do
  status="$(docker inspect -f '{{.State.Health.Status}}' "$NEW_CONTAINER_NAME" 2>/dev/null || true)"
  if [ "$status" = "healthy" ]; then
    break
  fi
  if [ "$status" = "unhealthy" ]; then
    log "健康检查失败: $NEW_CONTAINER_NAME"
    rollback
  fi
  now_ts="$(date +%s)"
  if [ $((now_ts - start_ts)) -ge "$HEALTHCHECK_TIMEOUT" ]; then
    log "健康检查超时: $NEW_CONTAINER_NAME"
    rollback
  fi
  sleep 2
done

if [ -n "$HEALTHCHECK_URL" ]; then
  if ! curl -fsS "$HEALTHCHECK_URL" >/dev/null; then
    log "HTTP 200 校验失败: $HEALTHCHECK_URL"
    rollback
  fi
else
  if [ "$DRY_RUN" = "1" ]; then
    log "DRY_RUN: docker exec -e HEALTHCHECK_PORT=$HEALTHCHECK_PORT -e HEALTHCHECK_PATH=$HEALTHCHECK_PATH \"$NEW_CONTAINER_NAME\" python - <<'PY' ..."
  else
    docker exec -e HEALTHCHECK_PORT="$HEALTHCHECK_PORT" -e HEALTHCHECK_PATH="$HEALTHCHECK_PATH" "$NEW_CONTAINER_NAME" python - <<'PY'
import os
import urllib.request

port = int(os.environ.get("HEALTHCHECK_PORT", "8080"))
path = os.environ.get("HEALTHCHECK_PATH", "/health")
url = f"http://127.0.0.1:{port}{path}"
with urllib.request.urlopen(url, timeout=5) as resp:
    if resp.status != 200:
        raise SystemExit(f"unexpected status: {resp.status}")
PY
  fi
fi

run "docker stop \"$SERVICE_NAME\""
run "docker rm \"$SERVICE_NAME\""
run "docker rename \"$NEW_CONTAINER_NAME\" \"$SERVICE_NAME\""

new_id="$(docker inspect -f '{{.Id}}' "$SERVICE_NAME")"
log "old_id=$old_id new_id=$new_id"
log "health_log=$(docker inspect -f '{{json .State.Health.Log}}' "$SERVICE_NAME")"
