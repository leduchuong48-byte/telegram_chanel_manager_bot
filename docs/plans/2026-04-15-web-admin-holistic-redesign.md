# Web Admin Holistic Redesign Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace patch-by-patch UI iteration with a single coherent admin-console architecture that makes identity, execution, task status, and failure reasons explicit.

**Architecture:** Reorganize the Web Admin around five domains (Overview, Operations, Telegram Runtime, System Settings, Observability) with one shared status model (`controller`, `session`, `task`, `chat`, `failure`). Keep current backend capabilities but expose them through consistent page-level aggregates and a first-class task center.

**Tech Stack:** FastAPI, Jinja2 templates, app/static JS/CSS, SQLite job tables, existing cleaner/task APIs, websocket logs.

---

## Product Principles

1. **Single mental model**
   - Web login identity != Telegram controller identity != Telethon execution identity.
2. **State-first UI**
   - Every critical action must show preconditions before execution.
3. **Task-first operations**
   - Any asynchronous or risky action is visible in Task Center.
4. **Diagnosis-first errors**
   - Failures are categorized with action guidance, not generic toasts.
5. **Role clarity**
   - Only `owner` and `admin`, with globally consistent capability labels.

---

## Information Architecture (Target)

### 1) Overview
- System health, mode status, execution identity, running tasks, recent failures.

### 2) Operations
- Task Center, cleaner operations, history maintenance operations, task details/log stream.

### 3) Telegram Runtime
- Telegram controllers, session identity/health, target chat visibility/effective state.

### 4) System Settings
- Bot settings, filters/media rules, config editor, web admin users, maintenance tools.

### 5) Observability
- System logs, task logs, deletion events, failure diagnostics.

---

## Unified State Model

### Controller
- `role`: `owner | admin`
- `status`: `active | unknown | disabled`
- `capability_summary`

### Session
- `identity`, `health`, `last_verified_at`, `visible_targets_count`, `dependent_task_types`

### Task
- `task_id`, `task_type`, `status`, `stage`, `progress`, `triggered_by`, `executor_identity`, `target`, `cancellable`

### Chat
- `resolution_status`, `session_visibility`, `bot_presence`, `effective_mode`, `capabilities`

### Failure
- `category`: `permission_denied | role_denied | session_unavailable | chat_unresolved | chat_not_visible | runtime_mode_blocked | task_runtime_error | telegram_external_error`
- `message`, `recommended_action`, `related_links`

---

## Roles and Capabilities

### owner
- Manage telegram controllers
- Manage session login/switch/reset
- Modify core bot settings and runtime critical modes
- Execute all maintenance/business tasks

### admin
- Execute maintenance/business commands/tasks (`tag_build`, `tag_rebuild`, `tag_update`, `tag_stop`, cleaner jobs)
- View task center/logs/diagnostics
- Cannot manage controllers/session/core settings

---

## Page Responsibilities

### `/` Overview
- Show global readiness and quick diagnosis cards.
- Include explicit "Execution Identity" card.

### `/telegram_controllers`
- Show only owner/admin model.
- Explain what admin can/cannot do.

### `/session`
- Treat as execution identity center.
- Show impact: which task types depend on this session.

### `/task_center`
- Unified task list with status filters, details, cancellation, progress, failure category.
- Link to related objects (session/chat/controller).

### `/cleaner`, `/tags`, `/tools`
- Task launcher pages only.
- Submit -> immediately link to task detail.

### `/logs`
- Keep real-time logs, add task/object filters and links.

### `/users`
- Explicitly web-login-only identity page.

---

## API/Backend Consolidation Plan

### Keep and standardize existing APIs
- `/api/cleaner/jobs`
- `/api/cleaner/jobs/{job_id}`
- `/api/cleaner/jobs/{job_id}/cancel`
- `/api/cleaner/monitoring`
- `/api/chat_effective_state/chats`
- `/api/chat_effective_state/events`
- `/ws/logs`

### Add aggregate endpoints (phase 2)
- `GET /api/dashboard/summary`
- `GET /api/task_center/jobs` (cross-domain normalized view)
- `GET /api/task_center/jobs/{task_id}`
- `GET /api/diagnostics/failures`
- `GET /api/runtime/identity`

---

## Implementation Phases

### Phase 1 (Foundation)
- Navigation/domain regrouping in base layout.
- Overview page cards for identity/mode/tasks/failures.
- Owner/admin role copy unification across pages.
- Cleaner/tags/tools -> explicit "view in Task Center" behavior.

### Phase 2 (Task and Diagnosis Core)
- Task Center detail view + stage timeline + failure category rendering.
- Normalized task payloads from existing cleaner/job tables.
- Failure categorization in API response layer.
- Links between task detail and session/chat/effective-state pages.

### Phase 3 (Telegram Runtime Clarity)
- Session page as execution identity dashboard.
- Chat visibility/effective-state deep page.
- Unified diagnostics workflow: from failure -> root object -> fix action.

---

## Module Impact Checklist

### Frontend templates
- `app/templates/base.html`
- `app/templates/dashboard.html`
- `app/templates/task_center.html`
- `app/templates/cleaner.html`
- `app/templates/telegram_controllers.html`
- `app/templates/login_telegram.html`
- `app/templates/logs.html`
- `app/templates/users.html`

### Frontend static
- `app/static/*` (task center components, status badges, diagnostics widgets)

### Routers
- `app/main.py`
- `app/routers/cleaner.py`
- `app/routers/chat_effective_state.py`
- `app/routers/logs.py`
- `app/routers/telegram_controllers.py`
- new aggregate routers for dashboard/task center diagnostics

### Core models/helpers
- `app/core/models.py`
- new enums/types for role/status/failure categories

### Runtime/backend
- `tg_media_dedupe_bot/pipeline_runtime.py`
- `tg_media_dedupe_bot/db.py`
- task progress and failure metadata normalization

---

## Verification Strategy

1. Unit tests for role capability and response model consistency.
2. API tests for task list/detail/monitoring/failure category fields.
3. Template tests for navigation, role copy, task center visibility.
4. Manual E2E checks:
   - run cleaner task -> appears in Task Center immediately
   - running task shows progress and can cancel
   - failed task shows categorized reason and actionable link
   - admin cannot access owner-only actions but can execute maintenance tasks

---

## Definition of Done

- A user can answer in under 10 seconds:
  1. Who can control bot now?
  2. Which Telegram identity executes history tasks?
  3. What tasks are running and where are they stuck?
  4. Why did the last task fail and what to do next?
- No critical operation remains black-box.
- Role boundaries are consistent across all pages and APIs.
