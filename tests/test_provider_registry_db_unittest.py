from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tg_media_dedupe_bot.db import Database


class ProviderRegistryDbTest(unittest.TestCase):
    def test_upsert_and_list_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "bot.db"
            db = Database(db_path)
            try:
                db.upsert_provider(
                    provider_key="openai_main",
                    display_name="OpenAI Main",
                    provider_type="openai_compatible",
                    base_url="https://api.openai.com/v1",
                    enabled=True,
                    use_responses_mode="auto",
                    default_model="gpt-4.1",
                )
                providers = db.list_providers(enabled_only=False)
            finally:
                db.close()

            self.assertEqual(len(providers), 1)
            self.assertEqual(providers[0]["provider_key"], "openai_main")
            self.assertEqual(providers[0]["use_responses_mode"], "auto")
            self.assertEqual(providers[0]["default_model"], "gpt-4.1")


if __name__ == "__main__":
    unittest.main()
