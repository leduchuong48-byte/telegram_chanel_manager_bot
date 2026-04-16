from __future__ import annotations

from pathlib import Path


def test_bot_settings_template_contains_llm_controls() -> None:
    tpl = Path('/vol1/1000/services/docker/chanel_manager_bot/app/templates/bot_settings.html')
    html = tpl.read_text(encoding='utf-8')
    assert 'id="llm_provider_key"' in html
    assert 'id="llm_base_url"' in html
    assert 'id="llm_api_key"' in html
    assert 'id="llm_model"' in html
    assert 'id="llm_use_responses_mode"' in html
    assert 'id="llm_enabled"' in html
    assert '/api/settings/llm' in html


def test_bot_settings_template_warns_about_v1_suffix() -> None:
    tpl = Path('/vol1/1000/services/docker/chanel_manager_bot/app/templates/bot_settings.html')
    html = tpl.read_text(encoding='utf-8')
    assert '不要手动追加 /v1' in html
    assert 'id="llm_base_url_warning"' in html
