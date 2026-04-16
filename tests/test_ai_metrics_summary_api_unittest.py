from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from app.core.config_manager import ConfigManager
from app.routers import ai_health as ai_health_router
from app.routers import providers as providers_router
from app.routers import models as models_router


class AiMetricsSummaryApiTest(unittest.TestCase):
    def test_metrics_summary_returns_baseline_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "config.json"
            db_path = root / "bot.db"
            config_path.write_text(json.dumps({"database": {"path": str(db_path)}}), encoding="utf-8")
            manager = ConfigManager(config_path)
            ai_health_router.set_config_manager(manager)

            summary = asyncio.run(ai_health_router.get_ai_metrics_summary(_="admin", window="1h"))
            self.assertEqual(summary.window, "1h")
            self.assertEqual(len(summary.providers), 0)
            self.assertEqual(len(summary.models), 0)
            self.assertEqual(summary.workflow.review_pending, 0)


    def test_metrics_summary_includes_provider_and_model_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "config.json"
            db_path = root / "bot.db"
            config_path.write_text(json.dumps({"database": {"path": str(db_path)}}), encoding="utf-8")
            manager = ConfigManager(config_path)
            ai_health_router.set_config_manager(manager)

            from tg_media_dedupe_bot.db import Database
            db = Database(db_path)
            try:
                db.upsert_provider(
                    provider_key="openai_main",
                    display_name="OpenAI Main",
                    provider_type="openai_compatible",
                    base_url="https://api.openai.com/v1",
                    enabled=True,
                    use_responses_mode="auto",
                    default_model="gpt-4.1-mini",
                )
                db.upsert_model(
                    provider_key="openai_main",
                    model_id="gpt-4.1-mini",
                    enabled=True,
                    source="provider_default",
                )
            finally:
                db.close()

            summary = asyncio.run(ai_health_router.get_ai_metrics_summary(_="admin", window="1h"))
            self.assertEqual(len(summary.providers), 1)
            self.assertEqual(summary.providers[0].provider_key, "openai_main")
            self.assertEqual(len(summary.models), 1)
            self.assertEqual(summary.models[0].model_key, "openai_main:gpt-4.1-mini")


    def test_metrics_summary_uses_ai_request_events_for_rates_and_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "config.json"
            db_path = root / "bot.db"
            config_path.write_text(json.dumps({"database": {"path": str(db_path)}}), encoding="utf-8")
            manager = ConfigManager(config_path)
            ai_health_router.set_config_manager(manager)

            from tg_media_dedupe_bot.db import Database
            db = Database(db_path)
            try:
                db.upsert_provider(
                    provider_key="openai_main",
                    display_name="OpenAI Main",
                    provider_type="openai_compatible",
                    base_url="https://api.openai.com/v1",
                    enabled=True,
                    use_responses_mode="auto",
                    default_model="gpt-4.1-mini",
                )
                db.upsert_model(
                    provider_key="openai_main",
                    model_id="gpt-4.1-mini",
                    enabled=True,
                    source="provider_default",
                )
                db.record_ai_request_event(
                    provider_key="openai_main",
                    model_key="openai_main:gpt-4.1-mini",
                    success=True,
                    fallback_used=False,
                    downgrade_used=False,
                    latency_ms=100,
                )
                db.record_ai_request_event(
                    provider_key="openai_main",
                    model_key="openai_main:gpt-4.1-mini",
                    success=False,
                    fallback_used=True,
                    downgrade_used=True,
                    latency_ms=300,
                )
            finally:
                db.close()

            summary = asyncio.run(ai_health_router.get_ai_metrics_summary(_="admin", window="1h"))
            self.assertEqual(len(summary.providers), 1)
            self.assertAlmostEqual(summary.providers[0].success_rate, 0.5)
            self.assertEqual(summary.providers[0].avg_latency_ms, 200)
            self.assertEqual(summary.providers[0].fallback_count, 1)
            self.assertEqual(summary.providers[0].downgrade_count, 1)
            self.assertEqual(len(summary.models), 1)
            self.assertAlmostEqual(summary.models[0].success_rate, 0.5)


    def test_metrics_summary_reflects_provider_test_probe_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "config.json"
            db_path = root / "bot.db"
            config_path.write_text(json.dumps({"database": {"path": str(db_path)}}), encoding="utf-8")
            manager = ConfigManager(config_path)
            ai_health_router.set_config_manager(manager)
            providers_router.set_config_manager(manager)

            from tg_media_dedupe_bot.db import Database
            db = Database(db_path)
            try:
                db.upsert_provider(
                    provider_key="openai_main",
                    display_name="OpenAI Main",
                    provider_type="openai_compatible",
                    base_url="https://api.openai.com/v1",
                    enabled=False,
                    use_responses_mode="auto",
                    default_model="gpt-4.1-mini",
                )
            finally:
                db.close()

            asyncio.run(providers_router.test_provider_connection(provider_key="openai_main", _="admin"))
            asyncio.run(providers_router.probe_provider_capabilities(provider_key="openai_main", _="admin"))

            summary = asyncio.run(ai_health_router.get_ai_metrics_summary(_="admin", window="1h"))
            self.assertEqual(len(summary.providers), 1)
            self.assertEqual(summary.providers[0].provider_key, "openai_main")
            self.assertAlmostEqual(summary.providers[0].success_rate, 1.0)
            self.assertEqual(summary.providers[0].request_count, 2)

    def test_metrics_summary_reflects_model_sync_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "config.json"
            db_path = root / "bot.db"
            config_path.write_text(json.dumps({"database": {"path": str(db_path)}}), encoding="utf-8")
            manager = ConfigManager(config_path)
            ai_health_router.set_config_manager(manager)
            models_router.set_config_manager(manager)

            from tg_media_dedupe_bot.db import Database
            db = Database(db_path)
            try:
                db.upsert_provider(
                    provider_key="openai_main",
                    display_name="OpenAI Main",
                    provider_type="openai_compatible",
                    base_url="https://api.openai.com/v1",
                    enabled=True,
                    use_responses_mode="auto",
                    default_model="gpt-4.1-mini",
                )
            finally:
                db.close()

            asyncio.run(models_router.sync_models(_="admin"))

            summary = asyncio.run(ai_health_router.get_ai_metrics_summary(_="admin", window="1h"))
            self.assertEqual(len(summary.models), 1)
            self.assertEqual(summary.models[0].model_key, "openai_main:gpt-4.1-mini")
            self.assertEqual(summary.models[0].request_count, 1)


    def test_metrics_summary_excludes_events_older_than_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "config.json"
            db_path = root / "bot.db"
            config_path.write_text(json.dumps({"database": {"path": str(db_path)}}), encoding="utf-8")
            manager = ConfigManager(config_path)
            ai_health_router.set_config_manager(manager)

            from tg_media_dedupe_bot.db import Database
            db = Database(db_path)
            try:
                db.upsert_provider(
                    provider_key="openai_main",
                    display_name="OpenAI Main",
                    provider_type="openai_compatible",
                    base_url="https://api.openai.com/v1",
                    enabled=True,
                    use_responses_mode="auto",
                    default_model="gpt-4.1-mini",
                )
                db.upsert_model(
                    provider_key="openai_main",
                    model_id="gpt-4.1-mini",
                    enabled=True,
                    source="provider_default",
                )
                db.record_ai_request_event(
                    provider_key="openai_main",
                    model_key="openai_main:gpt-4.1-mini",
                    success=True,
                    fallback_used=False,
                    downgrade_used=False,
                    latency_ms=100,
                    created_at=1,
                )
            finally:
                db.close()

            summary = asyncio.run(ai_health_router.get_ai_metrics_summary(_="admin", window="1h"))
            self.assertEqual(len(summary.providers), 1)
            self.assertEqual(summary.providers[0].request_count, 0)

    def test_metrics_summary_reports_real_p95_latency(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "config.json"
            db_path = root / "bot.db"
            config_path.write_text(json.dumps({"database": {"path": str(db_path)}}), encoding="utf-8")
            manager = ConfigManager(config_path)
            ai_health_router.set_config_manager(manager)

            from tg_media_dedupe_bot.db import Database
            db = Database(db_path)
            try:
                db.upsert_provider(
                    provider_key="openai_main",
                    display_name="OpenAI Main",
                    provider_type="openai_compatible",
                    base_url="https://api.openai.com/v1",
                    enabled=True,
                    use_responses_mode="auto",
                    default_model="gpt-4.1-mini",
                )
                db.upsert_model(
                    provider_key="openai_main",
                    model_id="gpt-4.1-mini",
                    enabled=True,
                    source="provider_default",
                )
                for latency in [100, 110, 120, 130, 1000]:
                    db.record_ai_request_event(
                        provider_key="openai_main",
                        model_key="openai_main:gpt-4.1-mini",
                        success=True,
                        fallback_used=False,
                        downgrade_used=False,
                        latency_ms=latency,
                    )
            finally:
                db.close()

            summary = asyncio.run(ai_health_router.get_ai_metrics_summary(_="admin", window="1h"))
            self.assertEqual(len(summary.providers), 1)
            self.assertEqual(summary.providers[0].p95_latency_ms, 1000)


if __name__ == "__main__":
    unittest.main()
