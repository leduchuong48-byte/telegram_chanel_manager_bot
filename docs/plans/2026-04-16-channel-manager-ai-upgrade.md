# Channel Manager AI Upgrade Plan (TDD Rolling)

## Current Strategy
- Use strict TDD slices: failing test first, minimal implementation, targeted regression.
- Upgrade in vertical slices and continuously adjust plan based on runtime/test evidence.

## Completed Slices

### Slice A: Provider Registry Foundation
- Added provider table and migration guard in DB layer.
- Added provider CRUD foundation methods in `tg_media_dedupe_bot/db.py`:
  - `upsert_provider`
  - `get_provider`
  - `list_providers`
- Added providers API router in `app/routers/providers.py`:
  - `GET /api/providers`
  - `POST /api/providers`
- Wired router in `app/main.py`.

TDD evidence:
- Red: provider tests failed because methods/router missing.
- Green: `tests/test_provider_registry_db_unittest.py` and `tests/test_providers_api_unittest.py` pass.

### Slice B: AI Health Minimum Endpoint
- Added `app/routers/ai_health.py` with `GET /api/ai/health`.
- Wired router in `app/main.py`.
- Endpoint currently returns provider health summary + placeholder zeros for model/job/review metrics.

TDD evidence:
- Red: health API test failed due missing router.
- Green: `tests/test_ai_health_api_unittest.py` passes.

## Adjusted Plan (Based on Evidence)

### Next Slice 1: Provider Update/Test/Probe APIs
- Add provider update endpoint.
- Add connection test endpoint (basic HTTP reachability).
- Add capability probe status persistence fields.
- Tests first for duplicate key rules, invalid mode/type, and update behavior.

### Next Slice 2: Model Registry Skeleton
- Add `models` table and sync run table.
- Add list endpoint and manual sync endpoint skeleton.
- Keep pull/provision as follow-up after sync stability.

### Next Slice 3: Responses Effective Policy Engine (Minimal)
- Introduce global/provider/model/request resolution function.
- Add fallback decision output fields (without full external provider invocation yet).
- Add unit tests for precedence and auto-downgrade decision.

### Next Slice 4: AI Metrics Summary Endpoint
- Implement `GET /api/ai/metrics-summary` using persisted counters/events.
- Add baseline event logging for provider calls and classification preview calls.

## Known Baseline Notes
- Existing unrelated tests in `tests/test_web_admin_unit.py` currently fail (media_filter merge and tag rename wrapper expectation).
- These failures were observed during broader regression and are not introduced by the provider/ai-health slices.

## Verification Commands Used
- `PYTHONPATH=. ./.venv/bin/pytest tests/test_provider_registry_db_unittest.py tests/test_providers_api_unittest.py -q`
- `PYTHONPATH=. ./.venv/bin/pytest tests/test_ai_health_api_unittest.py -q`
- `PYTHONPATH=. ./.venv/bin/pytest tests/test_provider_registry_db_unittest.py tests/test_providers_api_unittest.py tests/test_ai_health_api_unittest.py -q`
- `PYTHONPATH=. ./.venv/bin/pytest tests/test_app_runtime_wiring_unittest.py -q`


### Slice C: Provider Update/Test/Probe + Model Registry Skeleton
- Extended providers schema for runtime state fields:
  - `last_test_status`, `last_test_at`
  - `last_probe_status`, `last_probe_at`
  - `supports_responses`, `capabilities_json`
- Added provider API endpoints in `app/routers/providers.py`:
  - `PATCH /api/providers/{provider_key}`
  - `POST /api/providers/{provider_key}/test`
  - `POST /api/providers/{provider_key}/probe`
- Added model registry skeleton in DB and API:
  - tables: `models`, `model_sync_runs`
  - methods: `list_models`, `upsert_model`, `record_model_sync_run`
  - router: `app/routers/models.py`
  - endpoints:
    - `GET /api/models`
    - `POST /api/models/sync`
- Wired models router into `app/main.py`.

TDD evidence:
- Red: `tests/test_providers_api_unittest.py::test_update_and_probe_provider_flow` failed with missing endpoint.
- Green: provider tests pass after minimal implementation.
- Red: `tests/test_models_api_unittest.py` failed due missing models router.
- Green: model sync/list test passes after minimal implementation.

### Updated Next Slice
- Implement minimal effective runtime policy resolver (global/provider/model/request precedence) with unit tests first.
- Add `GET /api/ai/metrics-summary` skeleton from persisted counters.
- Address pydantic warning for `model_id` naming by setting model config protected namespace override where needed.


### Slice D: Runtime Responses Policy Resolver
- Added `app/services/runtime_policy.py` with precedence resolver:
  - request > model > provider > global
- Added tests in `tests/test_runtime_policy_unittest.py` for precedence behavior.

TDD evidence:
- Red: import error due missing module.
- Green: runtime policy tests pass.

### Slice E: AI Metrics Summary Skeleton
- Expanded `app/routers/ai_health.py`:
  - existing: `GET /api/ai/health`
  - new: `GET /api/ai/metrics-summary`
- Added test `tests/test_ai_metrics_summary_api_unittest.py` for baseline payload.

TDD evidence:
- Red: missing `get_ai_metrics_summary` endpoint.
- Green: metrics summary test passes.

## Plan Corrections from Runtime Evidence
- Keep metrics endpoint payload minimal now (empty aggregates), then backfill from persisted events in next slice.
- Track and fix pydantic protected namespace warnings (`model_id`, `model_key`) in a dedicated cleanup slice to keep test output clean.


### Slice F: Metrics Aggregation Baseline + Warning Cleanup
- Enhanced `GET /api/ai/metrics-summary` to return provider/model rows derived from persisted registry data.
- Provider metrics now include one row per provider with baseline status-derived success rate.
- Model metrics now include one row per model with stable composite key format `provider:model`.
- Cleaned pydantic protected namespace warnings by setting `ConfigDict(protected_namespaces=())` in:
  - `app/routers/models.py` -> `ModelItem`
  - `app/routers/ai_health.py` -> `ModelMetric`

TDD evidence:
- Red: added aggregation test failed (`expected provider/model rows, got empty arrays`).
- Green: metrics aggregation test passes after minimal implementation.

## Updated Next Slice
- Persist AI request events (preview/classification/provider test/probe) and replace baseline metrics with real success/fallback/downgrade aggregates.
- Add dedicated tests for provider/model success-rate windows and fallback counters.


### Slice G: Event-Driven Metrics (Success/Fallback/Downgrade)
- Added new DB table `ai_request_events` and indexes in `tg_media_dedupe_bot/db.py`.
- Added DB methods:
  - `record_ai_request_event(...)`
  - `get_ai_request_metrics_by_provider(since_ts=...)`
  - `get_ai_request_metrics_by_model(since_ts=...)`
- Upgraded `GET /api/ai/metrics-summary` in `app/routers/ai_health.py`:
  - Uses window-aware event aggregation (1h / 24h)
  - Provider metrics now derive `success_rate`, `avg_latency_ms`, `fallback_count`, `downgrade_count` from persisted events when available.
  - Model metrics now derive `success_rate` from persisted events when available.
  - Retains fallback baseline behavior for providers/models with no events.

TDD evidence:
- Red: `test_metrics_summary_uses_ai_request_events_for_rates_and_counts` failed with missing `record_ai_request_event`.
- Green: after minimal DB + router aggregation implementation, test passes.

Next:
- Start recording real runtime events from provider test/probe/model sync/classification preview calls.
- Replace placeholder `p95_latency_ms` with percentile calculation from event distribution.
- Add window-boundary tests (older events excluded).


### Slice H: Wire Real Event Sources into Metrics
- Wired provider test/probe flows to write request events:
  - `app/routers/providers.py`
  - endpoints `/api/providers/{provider_key}/test` and `/api/providers/{provider_key}/probe` now call `record_ai_request_event`.
- Wired model sync flow to write request events:
  - `app/routers/models.py`
  - endpoint `/api/models/sync` writes one event per synced default model.
- Extended metrics schema in `app/routers/ai_health.py`:
  - `ProviderMetric.request_count`
  - `ModelMetric.request_count`
- Extended DB metric aggregation payloads in `tg_media_dedupe_bot/db.py` to include `request_count`.

TDD evidence:
- Red: new tests failed (missing `request_count` fields and provider/model event sourcing did not affect metrics).
- Green: after minimal wiring + schema extension, metrics tests pass.

Next:
- Add window-boundary tests to ensure stale events are excluded from `1h`.
- Implement true P95 latency instead of placeholder average mirroring.


### Slice I: Window Boundary + Real P95
- Enhanced `record_ai_request_event` in `tg_media_dedupe_bot/db.py` to accept optional `created_at` for deterministic window-boundary tests.
- Added window-boundary test to verify stale events are excluded from `1h` metrics.
- Implemented provider-level real P95 latency in DB aggregation:
  - sorted latency sampling by provider
  - percentile rank computation (ceil-based p95 index)
- Updated API mapping in `app/routers/ai_health.py` to surface aggregated `p95_latency_ms` instead of placeholder mirror of average.

TDD evidence:
- Red: tests failed due missing `created_at` argument and placeholder p95 behavior.
- Green: new window-boundary + p95 tests pass after minimal implementation.

Next:
- Add workflow counters from actual preview/classification events.
- Add per-window test matrix for 1h vs 24h behavior.


### Slice J: One-Shot Tag Cleanup MVP (Simplified Track)
- Added rules module `app/services/tag_cleanup_rules.py`:
  - input normalization and dedupe
  - suggestion sanitization (invalid/self-target rename/merge fallback to keep)
- Added service module `app/services/tag_cleanup.py`:
  - in-memory session store
  - preview/apply/export workflow for one-shot cleanup
- Added API router `app/routers/tag_cleanup.py`:
  - `POST /api/tag-cleanup/preview`
  - `POST /api/tag-cleanup/apply`
  - `POST /api/tag-cleanup/export`
- Added minimal UI placeholder page `app/templates/tag_cleanup.html` and route `/tag_cleanup` in `app/main.py`.
- Wired router in `app/main.py` and config manager setup.

TDD evidence:
- Red: tests failed on missing `tag_cleanup_rules` and `tag_cleanup` modules/imports.
- Green: after minimal implementation, cleanup rules/API tests pass.

Verification commands:
- `PYTHONPATH=. ./.venv/bin/pytest tests/test_tag_cleanup_rules_unittest.py tests/test_tag_cleanup_api_unittest.py -q`
- `PYTHONPATH=. ./.venv/bin/pytest tests/test_provider_registry_db_unittest.py tests/test_providers_api_unittest.py tests/test_models_api_unittest.py tests/test_runtime_policy_unittest.py tests/test_ai_health_api_unittest.py tests/test_ai_metrics_summary_api_unittest.py tests/test_app_runtime_wiring_unittest.py tests/test_tag_cleanup_rules_unittest.py tests/test_tag_cleanup_api_unittest.py -q`


### Slice K: Tag Cleanup Session Retrieval + UI Interaction Upgrade
- Added cleanup session retrieval in service layer:
  - `app/services/tag_cleanup.py`
  - new method `get_cleanup_session(session_id=...)`
- Added API endpoint:
  - `GET /api/tag-cleanup/session/{session_id}`
  - implemented in `app/routers/tag_cleanup.py`
- Expanded response schema for session details:
  - accepted/rejected/pending counters
  - items with current decision/final values
- Upgraded `app/templates/tag_cleanup.html` from static placeholder to interactive MVP:
  - manual tag input textarea
  - preview action (API call)
  - load current session action
  - suggestions table rendering

TDD evidence:
- Red: new test failed because `get_cleanup_session` endpoint did not exist.
- Green: session retrieval + response schema implemented and tests pass.

Verification commands:
- `PYTHONPATH=. ./.venv/bin/pytest tests/test_tag_cleanup_api_unittest.py tests/test_tag_cleanup_rules_unittest.py -q`
- `PYTHONPATH=. ./.venv/bin/pytest tests/test_provider_registry_db_unittest.py tests/test_providers_api_unittest.py tests/test_models_api_unittest.py tests/test_runtime_policy_unittest.py tests/test_ai_health_api_unittest.py tests/test_ai_metrics_summary_api_unittest.py tests/test_app_runtime_wiring_unittest.py tests/test_tag_cleanup_rules_unittest.py tests/test_tag_cleanup_api_unittest.py -q`


### Slice L: Tag Cleanup Bulk Interaction + API Stability Checks
- Added stronger API assertions in `tests/test_tag_cleanup_api_unittest.py`:
  - dry-run summary consistency
  - export payload field stability
- Added page control test `tests/test_tag_cleanup_page_unittest.py` (template-level check).
- Upgraded `app/templates/tag_cleanup.html` interaction layer:
  - bulk select
  - accept/reject selected
  - dry-run apply
  - export json
  - load session refresh

TDD evidence:
- Red: page control test failed due missing action controls.
- Green: after UI control implementation, page/API tests pass.


### Slice M: Edit-Accept Batch Flow + Page Filters
- Extended API tests for:
  - `edit_accept` final-target propagation across session + export
  - CSV export field ordering stability
- Upgraded Tag Cleanup page UI controls in `app/templates/tag_cleanup.html`:
  - action/decision/confidence filters
  - bulk edit-selected flow (`edit_accept` via prompt)
  - select-all + filtered render
- Preserved one-shot lightweight architecture (no long-lived workflow expansion).

TDD evidence:
- Red: page control test failed due missing filter/edit controls.
- Green: controls + handlers implemented and tests pass.


### Slice N: Write-Mode Safety Guard + Operator Handoff
- Added write confirmation guard in `app/routers/tag_cleanup.py`:
  - `apply_mode=write` now requires `confirm_token=APPLY`
  - otherwise API returns `cleanup_write_confirm_required`
- Upgraded UI action flow in `app/templates/tag_cleanup.html`:
  - `Apply (Write)` button
  - prompt-based confirmation token input
- Added operator playbook:
  - `docs/checklists/tag-cleanup-operator-playbook.md`

TDD evidence:
- Red: write-mode test failed (missing confirmation guard), page test failed (missing apply write control).
- Green: guard + UI control implemented and tests pass.


### Slice O: Provider Secret Token Integration (local-ai readiness)
- Extended `app/routers/providers.py` to support provider API key secret storage:
  - `ProviderCreateRequest.api_key`
  - `ProviderUpdateRequest.api_key`
  - provider list includes `has_api_key`
  - secrets stored in `data/provider_secrets/<provider_key>.json` with file mode `600`
- Added test coverage in `tests/test_providers_api_unittest.py`:
  - create provider with api key
  - assert list shows `has_api_key=True`
  - assert secret file content contains expected key/value
- Applied local-ai configuration with:
  - base_url `https://api.openai.com/v1` (no manual `/v1` append)
  - token `sk-REDACTED_FOR_PUBLIC_RELEASE`
  - model `gpt-5.2`


### Slice P: LLM Settings API + Bot Settings UI Integration
- Added LLM settings API in `app/routers/settings.py`:
  - `GET /api/settings/llm`
  - `POST /api/settings/llm`
  - reads provider/model from DB and masks api key (`*****`)
  - persists provider secret to `data/provider_secrets/<provider>.json`
- Extended `app/templates/bot_settings.html` with LLM controls:
  - provider_key/base_url/api_key/model/use_responses_mode/enabled
  - loads/saves via `/api/settings/llm`
- Added tests:
  - `tests/test_settings_llm_api_unittest.py`
  - `tests/test_bot_settings_llm_page_unittest.py`

TDD evidence:
- Red: missing llm settings endpoints and page controls.
- Green: endpoints + template integration implemented and tests pass.
