"""Configuration management with backup and reload capabilities."""

import asyncio
import json
import logging
import shutil
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


class ConfigManager:
    """Manages configuration file operations with backup and validation."""

    _instance: "ConfigManager | None" = None
    _instance_lock = threading.Lock()

    def __init__(self, config_path: str | Path = "config.json"):
        """Initialize the config manager."""
        self.config_path = Path(config_path)
        self.config: dict[str, Any] = {}
        self.backup_dir = self.config_path.parent / "backups"
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self._reload_hooks: list[Callable[[dict[str, Any]], Any]] = []
        self._load_config()
        ConfigManager._instance = self

    @classmethod
    def get_instance(cls, config_path: str | Path = "config.json") -> "ConfigManager":
        """Get or create a global ConfigManager instance."""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls(config_path)
        return cls._instance

    def _load_config(self) -> None:
        """Load configuration from disk."""
        if not self.config_path.exists():
            logger.warning(f"Config file not found: {self.config_path}")
            self.config = self._default_config()
            return
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                self.config = json.load(f)
            logger.info(f"Loaded config from {self.config_path}")
        except Exception as e:
            logger.error(f"Failed to load config: {e}")
            self.config = self._default_config()

    def _default_config(self) -> dict[str, Any]:
        """Return default configuration structure."""
        return {
            "web_admin": {
                "enabled": True,
                "host": "0.0.0.0",
                "port": 8000,
                "secret_key": "change-this-in-production",
            },
            "web_users": [
                {
                    "username": "admin",
                    "password_hash": "",  # User must set this
                }
            ],
            "rss_fetch_interval": 300,
            "web_media_filters": {
                "size_limit_mb": 0,
                "duration_limit_min": 0,
                "media_types": [],
                "filter_mode": "off",
            },
            "forwarding_rules": [],
            "bot": {
                "bot_token": "",
                "api_id": "",
                "api_hash": "",
                "target_chat_id": "",
                "admin_id": "",
                "dry_run": True,
                "delete_duplicates": False,
                "log_level": "INFO",
                "tag_count": 3,
            },
            "database": {
                "path": "./data/bot.db",
            },
        }

    def get_config(self) -> dict[str, Any]:
        """Get current configuration."""
        return self.config

    def update_config(self, new_config: dict[str, Any]) -> tuple[bool, str]:
        """
        Update configuration with validation and backup.

        Returns:
            (success: bool, message: str)
        """
        # Validate JSON structure
        try:
            # Ensure it's a valid dict
            if not isinstance(new_config, dict):
                return False, "Configuration must be a JSON object"
            json.dumps(new_config)  # Validate JSON serializable
        except (TypeError, ValueError) as e:
            return False, f"Invalid JSON: {str(e)}"

        # Create backup
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_name = f"{self.config_path.name}.bak.{timestamp}"
            backup_path = self.config_path.parent / backup_name
            backup_dir_path = self.backup_dir / backup_name
            if self.config_path.exists():
                shutil.copy2(self.config_path, backup_path)
                shutil.copy2(self.config_path, backup_dir_path)
                logger.info(f"Backup created: {backup_path}")
        except Exception as e:
            logger.error(f"Failed to create backup: {e}")
            return False, f"Failed to create backup: {str(e)}"

        # Write new config
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(new_config, f, indent=2, ensure_ascii=False)
            self.config = new_config
            logger.info(f"Config updated and saved to {self.config_path}")
            return True, "Configuration saved successfully"
        except Exception as e:
            logger.error(f"Failed to write config: {e}")
            return False, f"Failed to write config: {str(e)}"

    async def reload_config(self) -> tuple[bool, str]:
        """Reload configuration from disk and run reload hooks."""
        try:
            self._load_config()
            for hook in self._reload_hooks:
                try:
                    result = hook(self.config)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as hook_error:
                    logger.error(f"Reload hook failed: {hook_error}")
                    return False, f"Reload hook failed: {str(hook_error)}"
            logger.info("Configuration reloaded from disk")
            return True, "Configuration reloaded successfully"
        except Exception as e:
            logger.error(f"Failed to reload config: {e}")
            return False, f"Failed to reload config: {str(e)}"

    def register_reload_hook(self, hook: Callable[[dict[str, Any]], Any]) -> None:
        """Register a callback to run after reload."""
        self._reload_hooks.append(hook)

    def get_web_users(self) -> list[dict[str, str]]:
        """Get list of web admin users."""
        return self.config.get("web_users", [])

    def find_user(self, username: str) -> dict[str, str] | None:
        """Find a user by username."""
        users = self.get_web_users()
        for user in users:
            if user.get("username") == username:
                return user
        return None
