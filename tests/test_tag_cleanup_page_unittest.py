from __future__ import annotations

from pathlib import Path


def test_tag_cleanup_template_has_core_controls() -> None:
    tpl = Path('/vol1/1000/services/docker/chanel_manager_bot/app/templates/tag_cleanup.html')
    html = tpl.read_text(encoding='utf-8')
    assert 'Tag Cleanup' in html
    assert 'id="tagsInput"' in html
    assert 'id="previewBtn"' in html
    assert 'id="loadSessionBtn"' in html
    assert 'id="suggestionsTable"' in html
    assert 'id="acceptSelectedBtn"' in html
    assert 'id="rejectSelectedBtn"' in html
    assert 'id="applyDryRunBtn"' in html
    assert 'id="applyWriteBtn"' in html
    assert 'id="exportJsonBtn"' in html
    assert 'id="actionFilter"' in html
    assert 'id="decisionFilter"' in html
    assert 'id="confidenceFilter"' in html
    assert 'id="editSelectedBtn"' in html
