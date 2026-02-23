#!/usr/bin/env python3
"""Deployment verification for Web Admin API."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import requests


BASE_URL = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")
USERNAME = os.getenv("ADMIN_USER", "admin")
PASSWORD = os.getenv("ADMIN_PASS", "password")


def _print_step(title: str) -> None:
    print(f"\n[CHECK] {title}")


def _fail(message: str) -> None:
    print(f"  ❌ {message}")


def _ok(message: str) -> None:
    print(f"  ✅ {message}")


def _get_backup_files(root: Path) -> list[Path]:
    backups = list(root.glob("config.json.bak.*"))
    backup_dir = root / "backups"
    if backup_dir.exists():
        backups.extend(backup_dir.glob("config.json.bak.*"))
    return backups


def main() -> int:
    failures = 0

    _print_step("Auth Check (GET /api/config without token)")
    try:
        response = requests.get(f"{BASE_URL}/api/config", timeout=5)
    except Exception as exc:
        _fail(f"请求失败: {exc}")
        return 1
    if response.status_code == 401:
        _ok("未授权访问被正确拦截 (401)")
    else:
        _fail(f"预期 401，实际 {response.status_code}")
        failures += 1

    _print_step("Login Flow (POST /api/token)")
    try:
        response = requests.post(
            f"{BASE_URL}/api/token",
            data={"username": USERNAME, "password": PASSWORD},
            timeout=5,
        )
    except Exception as exc:
        _fail(f"登录请求失败: {exc}")
        return 1

    if response.status_code != 200:
        _fail(f"登录失败: {response.status_code} {response.text}")
        return 1

    token = response.json().get("access_token")
    if not token:
        _fail("未返回 access_token")
        return 1
    _ok("登录成功并获取 access_token")

    headers = {"Authorization": f"Bearer {token}"}

    _print_step("Config Access (GET /api/config)")
    try:
        response = requests.get(f"{BASE_URL}/api/config", headers=headers, timeout=5)
    except Exception as exc:
        _fail(f"请求失败: {exc}")
        return 1
    if response.status_code != 200:
        _fail(f"访问失败: {response.status_code} {response.text}")
        return 1
    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        _fail(f"JSON 解析失败: {exc}")
        return 1
    if not isinstance(payload, dict):
        _fail("响应不是 JSON 对象")
        return 1
    _ok("配置读取成功")

    _print_step("Backup Logic (PUT /api/config)")
    config_data = payload.get("data", {})
    if not isinstance(config_data, dict):
        _fail("配置数据格式异常")
        return 1

    config_data["test_timestamp"] = datetime.utcnow().isoformat() + "Z"
    try:
        response = requests.put(
            f"{BASE_URL}/api/config",
            json={"data": config_data},
            headers=headers,
            timeout=5,
        )
    except Exception as exc:
        _fail(f"保存失败: {exc}")
        return 1

    if response.status_code != 200:
        _fail(f"保存失败: {response.status_code} {response.text}")
        return 1
    _ok("配置写入成功")

    root = Path.cwd()
    if (root / "config.json").exists():
        backups = _get_backup_files(root)
        if backups:
            latest = sorted(backups)[-1]
            _ok(f"检测到备份文件: {latest.name}")
        else:
            _fail("未发现备份文件")
            failures += 1
    else:
        _ok("未发现本地 config.json，跳过备份文件检查")

    if failures:
        print(f"\n❌ 失败项: {failures}")
        return 1

    print("\n🎉 所有检查通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
