from __future__ import annotations

from pathlib import Path
import tempfile

from fastapi.testclient import TestClient

from app.core.config_manager import ConfigManager
from app.main import create_app
from app.routers import cleaner as cleaner_router
from app.routers import media_filter as media_filter_router
from app.routers import tags as tags_router


def make_client(tmp_path: Path) -> TestClient:
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")
    manager = ConfigManager(config_path)
    app = create_app(manager)
    app.dependency_overrides.clear()
    return TestClient(app)


def test_cleaner_request_models_accept_target_field():
    payload = cleaner_router.DeleteByTypeRequest(types=["text"], limit=10, target="-100123")
    assert payload.target == "-100123"


def test_media_filter_normalizes_legacy_shape_as_default():
    normalized = media_filter_router._normalize_storage({
        "size_limit_mb": 10,
        "duration_limit_min": 5,
        "media_types": ["video"],
        "filter_mode": "blacklist",
    })
    assert normalized["default"]["size_limit_mb"] == 10
    assert normalized["default"]["filter_mode"] == "blacklist"
    assert normalized["overrides"] == {}


def test_media_filter_merges_default_and_override():
    storage = media_filter_router._normalize_storage({
        "default": {"filter_mode": "blacklist", "media_types": ["video"]},
        "overrides": {"-1001": {"size_limit_mb": 50}},
    })
    merged, has_override = media_filter_router._resolve_settings_for_target(storage, "-1001")
    assert merged.filter_mode == "blacklist"
    assert merged.media_types == ["video"]
    assert merged.size_limit_mb == 50
    assert has_override is True


def test_tag_group_rename_endpoint_moves_group_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        tags_router._TAG_GROUPS_DIR = root / "tag_groups"
        tags_router._TAG_ALIASES_DIR = root / "tag_aliases"
        tags_router._ensure_dirs()
        (tags_router._TAG_GROUPS_DIR / "old.txt").write_text("foo\nbar\n", encoding="utf-8")

        response = tags_router.rename_group.__wrapped__(  # type: ignore[attr-defined]
            tags_router.TagGroupRenameRequest(old_name="old", new_name="new"),
            _="admin",
        )

        assert response.success is True
        assert not (tags_router._TAG_GROUPS_DIR / "old.txt").exists()
        assert (tags_router._TAG_GROUPS_DIR / "new.txt").read_text(encoding="utf-8") == "foo\nbar\n"
