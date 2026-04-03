#!/usr/bin/env python3
"""Test script to verify Web Admin Panel functionality."""

import asyncio
import json
import sys
import time
from pathlib import Path

import requests

BASE_URL = "http://localhost:8000"
TEST_USERNAME = "admin"
TEST_PASSWORD = "password"


class TestClient:
    """Simple HTTP test client."""

    def __init__(self, base_url: str):
        self.base_url = base_url
        self.token = None

    def login(self, username: str, password: str) -> bool:
        """Test login endpoint."""
        print(f"\n[TEST] Login as {username}...")
        try:
            response = requests.post(
                f"{self.base_url}/api/auth/login",
                json={"username": username, "password": password},
                timeout=5,
            )
            if response.status_code != 200:
                print(f"  ❌ Login failed: {response.status_code}")
                print(f"     {response.text}")
                return False

            data = response.json()
            self.token = data.get("access_token")
            if not self.token:
                print("  ❌ No token in response")
                return False

            print(f"  ✅ Login successful, token: {self.token[:20]}...")
            return True
        except Exception as e:
            print(f"  ❌ Login error: {e}")
            return False

    def get_current_user(self) -> bool:
        """Test get current user endpoint."""
        print("\n[TEST] Get current user...")
        try:
            response = requests.get(
                f"{self.base_url}/api/auth/me",
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=5,
            )
            if response.status_code != 200:
                print(f"  ❌ Failed: {response.status_code}")
                return False

            data = response.json()
            username = data.get("username")
            print(f"  ✅ Current user: {username}")
            return True
        except Exception as e:
            print(f"  ❌ Error: {e}")
            return False

    def get_config(self) -> bool:
        """Test get config endpoint."""
        print("\n[TEST] Get configuration...")
        try:
            response = requests.get(
                f"{self.base_url}/api/config",
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=5,
            )
            if response.status_code != 200:
                print(f"  ❌ Failed: {response.status_code}")
                return False

            data = response.json()
            config = data.get("data", {})
            print(f"  ✅ Config loaded with {len(config)} keys")
            return True
        except Exception as e:
            print(f"  ❌ Error: {e}")
            return False

    def test_unauthorized_access(self) -> bool:
        """Test that endpoints require authentication."""
        print("\n[TEST] Verify authentication is required...")
        try:
            # Try to access without token
            response = requests.get(
                f"{self.base_url}/api/config",
                timeout=5,
            )
            if response.status_code == 401:
                print("  ✅ Correctly rejected unauthorized request (401)")
                return True
            else:
                print(f"  ❌ Expected 401, got {response.status_code}")
                return False
        except Exception as e:
            print(f"  ❌ Error: {e}")
            return False

    def update_config(self, new_config: dict) -> bool:
        """Test update config endpoint."""
        print("\n[TEST] Update configuration...")
        try:
            response = requests.put(
                f"{self.base_url}/api/config",
                json={"data": new_config},
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=5,
            )
            if response.status_code != 200:
                print(f"  ❌ Failed: {response.status_code}")
                print(f"     {response.text}")
                return False

            data = response.json()
            message = data.get("message", "")
            print(f"  ✅ Config updated: {message}")

            # Check if backup was created
            backup_dir = Path("backups")
            if backup_dir.exists():
                backup_files = list(backup_dir.glob("config.json.bak.*"))
                if backup_files:
                    latest_backup = sorted(backup_files)[-1]
                    print(f"  ✅ Backup created: {latest_backup.name}")
                    return True
            print("  ⚠️  No backup found")
            return True
        except Exception as e:
            print(f"  ❌ Error: {e}")
            return False

    def reload_config(self) -> bool:
        """Test reload config endpoint."""
        print("\n[TEST] Hot reload configuration...")
        try:
            response = requests.post(
                f"{self.base_url}/api/config/reload",
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=5,
            )
            if response.status_code != 200:
                print(f"  ❌ Failed: {response.status_code}")
                return False

            data = response.json()
            message = data.get("message", "")
            print(f"  ✅ Config reloaded: {message}")
            return True
        except Exception as e:
            print(f"  ❌ Error: {e}")
            return False

    def health_check(self) -> bool:
        """Test health check endpoint."""
        print("\n[TEST] Health check...")
        try:
            response = requests.get(
                f"{self.base_url}/health",
                timeout=5,
            )
            if response.status_code != 200:
                print(f"  ❌ Failed: {response.status_code}")
                return False

            data = response.json()
            status = data.get("status")
            print(f"  ✅ Service status: {status}")
            return True
        except Exception as e:
            print(f"  ❌ Error: {e}")
            return False


def main():
    """Run all tests."""
    print("=" * 60)
    print("Web Admin Panel Test Suite")
    print("=" * 60)

    # Check if service is running
    print(f"\nChecking if service is running on {BASE_URL}...")
    try:
        response = requests.get(f"{BASE_URL}/health", timeout=5)
        print("✅ Service is running")
    except Exception as e:
        print(f"❌ Service is not running: {e}")
        print("\nPlease start the service first:")
        print("  python web_app.py")
        print("  or")
        print("  docker compose up -d")
        return 1

    # Run tests
    client = TestClient(BASE_URL)
    results = []

    # Test authentication
    results.append(("Unauthorized access blocked", client.test_unauthorized_access()))

    # Test login
    results.append(("User login", client.login(TEST_USERNAME, TEST_PASSWORD)))

    if not client.token:
        print("\n❌ Cannot continue without valid token")
        return 1

    # Test authenticated endpoints
    results.append(("Get current user", client.get_current_user()))
    results.append(("Get configuration", client.get_config()))

    # Test config update
    try:
        with open("config.json", "r") as f:
            config = json.load(f)
        results.append(("Update configuration", client.update_config(config)))
    except Exception as e:
        print(f"\n[TEST] Update configuration...")
        print(f"  ⚠️  Skipped: {e}")

    # Test hot reload
    results.append(("Hot reload configuration", client.reload_config()))

    # Summary
    print("\n" + "=" * 60)
    print("Test Summary")
    print("=" * 60)

    passed = sum(1 for _, result in results if result)
    total = len(results)

    for test_name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"  {status}: {test_name}")

    print(f"\nTotal: {passed}/{total} tests passed")

    if passed == total:
        print("\n🎉 All tests passed!")
        return 0
    else:
        print(f"\n❌ {total - passed} test(s) failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
