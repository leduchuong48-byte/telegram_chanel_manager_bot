from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from app.core.config_manager import ConfigManager
from app.routers import tag_cleanup as tag_cleanup_router


class TagCleanupApiTest(unittest.TestCase):
    def test_preview_apply_export_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "config.json"
            db_path = root / "bot.db"
            config_path.write_text(json.dumps({"database": {"path": str(db_path)}}), encoding="utf-8")
            manager = ConfigManager(config_path)
            tag_cleanup_router.set_config_manager(manager)

            preview = asyncio.run(
                tag_cleanup_router.preview_cleanup(
                    tag_cleanup_router.TagCleanupPreviewRequest(
                        source_type="manual_input",
                        tags=[
                            {"tag": "AI绘图", "count": 3, "samples": ["mj"], "aliases": ["AI作图"]},
                            {"tag": "AI作图", "count": 5, "samples": ["sd"], "aliases": []},
                        ],
                    ),
                    _="admin",
                )
            )
            self.assertEqual(preview.status, "preview_ready")
            self.assertGreater(preview.summary.total_suggestions, 0)

            first = preview.items[0]
            apply_resp = asyncio.run(
                tag_cleanup_router.apply_cleanup(
                    tag_cleanup_router.TagCleanupApplyRequest(
                        session_id=preview.session_id,
                        decisions=[
                            tag_cleanup_router.TagCleanupDecision(
                                item_id=first.item_id,
                                decision="accept",
                                final_action=first.suggested_action,
                                final_target_tag=first.suggested_target_tag,
                            )
                        ],
                        apply_mode="dry_run",
                    ),
                    _="admin",
                )
            )
            self.assertTrue(apply_resp.success)

            export_resp = asyncio.run(
                tag_cleanup_router.export_cleanup(
                    tag_cleanup_router.TagCleanupExportRequest(
                        session_id=preview.session_id,
                        format="json",
                        export_type="final_mapping",
                    ),
                    _="admin",
                )
            )
            self.assertTrue(export_resp.success)
            self.assertEqual(export_resp.format, "json")


    def test_get_cleanup_session_after_decisions(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "config.json"
            db_path = root / "bot.db"
            config_path.write_text(json.dumps({"database": {"path": str(db_path)}}), encoding="utf-8")
            manager = ConfigManager(config_path)
            tag_cleanup_router.set_config_manager(manager)

            preview = asyncio.run(
                tag_cleanup_router.preview_cleanup(
                    tag_cleanup_router.TagCleanupPreviewRequest(
                        source_type="manual_input",
                        tags=[
                            {"tag": "A", "aliases": ["B"]},
                            {"tag": "C", "aliases": []},
                        ],
                    ),
                    _="admin",
                )
            )
            first = preview.items[0]
            asyncio.run(
                tag_cleanup_router.apply_cleanup(
                    tag_cleanup_router.TagCleanupApplyRequest(
                        session_id=preview.session_id,
                        decisions=[
                            tag_cleanup_router.TagCleanupDecision(
                                item_id=first.item_id,
                                decision="accept",
                                final_action=first.suggested_action,
                                final_target_tag=first.suggested_target_tag,
                            )
                        ],
                        apply_mode="dry_run",
                    ),
                    _="admin",
                )
            )

            session = asyncio.run(
                tag_cleanup_router.get_cleanup_session(
                    session_id=preview.session_id,
                    _="admin",
                )
            )
            self.assertEqual(session.session_id, preview.session_id)
            self.assertEqual(session.status, "dry_run_ready")
            self.assertEqual(session.summary.accepted, 1)
            self.assertGreaterEqual(session.summary.pending, 0)


    def test_dry_run_summary_and_export_fields_stable(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "config.json"
            db_path = root / "bot.db"
            config_path.write_text(json.dumps({"database": {"path": str(db_path)}}), encoding="utf-8")
            manager = ConfigManager(config_path)
            tag_cleanup_router.set_config_manager(manager)

            preview = asyncio.run(
                tag_cleanup_router.preview_cleanup(
                    tag_cleanup_router.TagCleanupPreviewRequest(
                        source_type="manual_input",
                        tags=[
                            {"tag": "AI绘图", "aliases": ["AI作图"]},
                            {"tag": "old_tag", "aliases": []},
                        ],
                    ),
                    _="admin",
                )
            )
            decisions = []
            for item in preview.items:
                if item.suggested_action in {"rename", "deprecate"}:
                    decisions.append(
                        tag_cleanup_router.TagCleanupDecision(
                            item_id=item.item_id,
                            decision="accept",
                            final_action=item.suggested_action,
                            final_target_tag=item.suggested_target_tag,
                        )
                    )

            apply_resp = asyncio.run(
                tag_cleanup_router.apply_cleanup(
                    tag_cleanup_router.TagCleanupApplyRequest(
                        session_id=preview.session_id,
                        decisions=decisions,
                        apply_mode="dry_run",
                    ),
                    _="admin",
                )
            )

            self.assertEqual(apply_resp.status, "dry_run_ready")
            self.assertGreaterEqual(apply_resp.summary.accepted, 1)
            self.assertEqual(
                apply_resp.summary.accepted + apply_resp.summary.rejected,
                len(decisions),
            )

            export_resp = asyncio.run(
                tag_cleanup_router.export_cleanup(
                    tag_cleanup_router.TagCleanupExportRequest(
                        session_id=preview.session_id,
                        format="json",
                        export_type="final_mapping",
                    ),
                    _="admin",
                )
            )

            self.assertTrue(export_resp.success)
            self.assertTrue(export_resp.filename.endswith('.json'))
            self.assertGreaterEqual(len(export_resp.content), 1)
            row = export_resp.content[0]
            self.assertIn('source_tag', row)
            self.assertIn('final_action', row)
            self.assertIn('final_target_tag', row)


    def test_edit_accept_updates_final_target_in_session_and_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "config.json"
            db_path = root / "bot.db"
            config_path.write_text(json.dumps({"database": {"path": str(db_path)}}), encoding="utf-8")
            manager = ConfigManager(config_path)
            tag_cleanup_router.set_config_manager(manager)

            preview = asyncio.run(
                tag_cleanup_router.preview_cleanup(
                    tag_cleanup_router.TagCleanupPreviewRequest(
                        source_type="manual_input",
                        tags=[{"tag": "AI绘图", "aliases": ["AI作图"]}],
                    ),
                    _="admin",
                )
            )
            first = preview.items[0]

            asyncio.run(
                tag_cleanup_router.apply_cleanup(
                    tag_cleanup_router.TagCleanupApplyRequest(
                        session_id=preview.session_id,
                        decisions=[
                            tag_cleanup_router.TagCleanupDecision(
                                item_id=first.item_id,
                                decision="edit_accept",
                                final_action="rename",
                                final_target_tag="ai图片",
                            )
                        ],
                        apply_mode="dry_run",
                    ),
                    _="admin",
                )
            )

            session = asyncio.run(tag_cleanup_router.get_cleanup_session(session_id=preview.session_id, _="admin"))
            self.assertEqual(session.summary.accepted, 1)
            self.assertEqual(session.items[0].final_target_tag, "ai图片")

            exported = asyncio.run(
                tag_cleanup_router.export_cleanup(
                    tag_cleanup_router.TagCleanupExportRequest(
                        session_id=preview.session_id,
                        format="json",
                        export_type="final_mapping",
                    ),
                    _="admin",
                )
            )
            self.assertEqual(exported.content[0]["final_target_tag"], "ai图片")

    def test_csv_export_stable_field_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "config.json"
            db_path = root / "bot.db"
            config_path.write_text(json.dumps({"database": {"path": str(db_path)}}), encoding="utf-8")
            manager = ConfigManager(config_path)
            tag_cleanup_router.set_config_manager(manager)

            preview = asyncio.run(
                tag_cleanup_router.preview_cleanup(
                    tag_cleanup_router.TagCleanupPreviewRequest(
                        source_type="manual_input",
                        tags=[{"tag": "AI绘图", "aliases": ["AI作图"]}],
                    ),
                    _="admin",
                )
            )
            first = preview.items[0]
            asyncio.run(
                tag_cleanup_router.apply_cleanup(
                    tag_cleanup_router.TagCleanupApplyRequest(
                        session_id=preview.session_id,
                        decisions=[
                            tag_cleanup_router.TagCleanupDecision(
                                item_id=first.item_id,
                                decision="accept",
                                final_action=first.suggested_action,
                                final_target_tag=first.suggested_target_tag,
                            )
                        ],
                        apply_mode="dry_run",
                    ),
                    _="admin",
                )
            )

            exported = asyncio.run(
                tag_cleanup_router.export_cleanup(
                    tag_cleanup_router.TagCleanupExportRequest(
                        session_id=preview.session_id,
                        format="csv",
                        export_type="final_mapping",
                    ),
                    _="admin",
                )
            )
            self.assertTrue(exported.filename.endswith('.csv'))
            self.assertGreaterEqual(len(exported.content), 1)
            self.assertEqual(
                list(exported.content[0].keys()),
                ["source_tag", "final_action", "final_target_tag", "final_category"],
            )


    def test_write_mode_requires_confirm_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "config.json"
            db_path = root / "bot.db"
            config_path.write_text(json.dumps({"database": {"path": str(db_path)}}), encoding="utf-8")
            manager = ConfigManager(config_path)
            tag_cleanup_router.set_config_manager(manager)

            preview = asyncio.run(
                tag_cleanup_router.preview_cleanup(
                    tag_cleanup_router.TagCleanupPreviewRequest(
                        source_type="manual_input",
                        tags=[{"tag": "AI绘图", "aliases": ["AI作图"]}],
                    ),
                    _="admin",
                )
            )
            first = preview.items[0]

            with self.assertRaises(Exception):
                asyncio.run(
                    tag_cleanup_router.apply_cleanup(
                        tag_cleanup_router.TagCleanupApplyRequest(
                            session_id=preview.session_id,
                            decisions=[
                                tag_cleanup_router.TagCleanupDecision(
                                    item_id=first.item_id,
                                    decision="accept",
                                    final_action=first.suggested_action,
                                    final_target_tag=first.suggested_target_tag,
                                )
                            ],
                            apply_mode="write",
                        ),
                        _="admin",
                    )
                )

            write_ok = asyncio.run(
                tag_cleanup_router.apply_cleanup(
                    tag_cleanup_router.TagCleanupApplyRequest(
                        session_id=preview.session_id,
                        decisions=[
                            tag_cleanup_router.TagCleanupDecision(
                                item_id=first.item_id,
                                decision="accept",
                                final_action=first.suggested_action,
                                final_target_tag=first.suggested_target_tag,
                            )
                        ],
                        apply_mode="write",
                        confirm_token="APPLY",
                    ),
                    _="admin",
                )
            )
            self.assertTrue(write_ok.success)
            self.assertEqual(write_ok.status, "applied")


if __name__ == "__main__":
    unittest.main()
