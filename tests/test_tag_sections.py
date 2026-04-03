from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config_manager import ConfigManager
from app.main import create_app
from app.routers import tags as tags_router


def _make_client(tmp_path: Path) -> TestClient:
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")
    manager = ConfigManager(config_path)
    app = create_app(manager)
    return TestClient(app)


def test_parse_group_file_into_sections():
    content = """-------欧美厂牌--------
#brazzers
#vixen

-------亚洲/拉丁厂牌--------
#heyzo
#latina
"""
    sections = tags_router.parse_tag_group_sections(content)
    assert sections == [
        {"name": "欧美厂牌", "tags": ["brazzers", "vixen"]},
        {"name": "亚洲/拉丁厂牌", "tags": ["heyzo", "latina"]},
    ]


def test_parse_plain_tag_lines_into_default_section():
    content = "#foo\n#bar\n"
    sections = tags_router.parse_tag_group_sections(content)
    assert sections == [{"name": "未分组", "tags": ["foo", "bar"]}]


def test_dump_sections_round_trip():
    sections = [
        {"name": "欧美厂牌", "tags": ["brazzers", "vixen"]},
        {"name": "其他", "tags": ["misc"]},
    ]
    text = tags_router.dump_tag_group_sections(sections)
    assert "-------欧美厂牌--------" in text
    assert "#brazzers" in text
    reparsed = tags_router.parse_tag_group_sections(text)
    assert reparsed == sections


def test_list_groups_includes_sections():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        tags_router._TAG_GROUPS_DIR = root / "tag_groups"
        tags_router._TAG_ALIASES_DIR = root / "tag_aliases"
        tags_router._ensure_dirs()
        (tags_router._TAG_GROUPS_DIR / "-1001.txt").write_text(
            "-------分类--------\n#foo\n#bar\n", encoding="utf-8"
        )

        groups = __import__('asyncio').run(tags_router.list_groups(_='admin'))
        assert groups[0].sections[0].name == "分类"
        assert groups[0].sections[0].tags == ["foo", "bar"]


def test_preview_group_returns_sections_and_rendered_messages():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        tags_router._TAG_GROUPS_DIR = root / "tag_groups"
        tags_router._TAG_ALIASES_DIR = root / "tag_aliases"
        tags_router._ensure_dirs()
        (tags_router._TAG_GROUPS_DIR / "-1001.txt").write_text(
            "-------分类--------\n#foo\n#bar\n", encoding="utf-8"
        )
        payload = __import__('asyncio').run(tags_router.preview_group(group='-1001', target=None, _='admin'))
        assert payload['sections'][0]['name'] == '分类'
        assert payload['rendered_messages']
