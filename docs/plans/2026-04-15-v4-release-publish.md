# Channel Manager Bot 4.0 Release Publish Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Publish `v4.0` of Channel Manager Bot to GitHub and Docker Hub with new Web Admin UI messaging, updated release notes, and no legacy screenshots.

**Architecture:** Use sanitized release copies under the remote upload workspace, derive release messaging from the current Web Admin redesign and runtime/task-center capabilities, and publish GitHub + Docker Hub artifacts from the same 4.0 product narrative. Use real current UI surfaces only; do not reuse 3.5 or older screenshots.

**Tech Stack:** Git, GitHub Releases API, Docker Hub publish script, Docker buildx/source-image fallback, FastAPI/Jinja Web Admin templates, existing sanitize/publish scripts.

---

### Task 1: Derive 3.5 -> 4.0 release narrative

**Files:**
- Read: `README.md`
- Read: `WEB_ADMIN_README.md`
- Read: `docs/plans/2026-04-15-web-admin-holistic-redesign.md`
- Read: `app/templates/dashboard.html`
- Read: `app/templates/cleaner.html`
- Read: `app/templates/task_center.html`
- Read: `app/templates/telegram_controllers.html`
- Read: `app/templates/users.html`

**Step 1: Identify release themes**
- Extract the major 4.0 themes: redesigned overview/dashboard, task center, cleaner workflow, role/identity boundary clarity, tool-page/editor-page split.

**Step 2: Draft 4.0 upgrade summary**
- Write release notes that explicitly compare 3.5 and 4.0.
- Emphasize that 4.0 is a major UI/operations redesign, not a patch release.
- Include an explicit upgrade recommendation.

**Step 3: Capture screenshot policy**
- Limit release screenshots to current UI surfaces only.
- Exclude any legacy/3.5 screenshots or screenshots with outdated information architecture.

### Task 2: Create sanitized GitHub release copy

**Files:**
- Create/modify in sanitized copy: `README.md`
- Create/modify in sanitized copy: `README_en.md`
- Create/modify in sanitized copy: `DISCLAIMER.md`
- Create: release notes markdown in upload workspace

**Step 1: Run sanitize copy**
Run the GitHub sanitize script against the source directory into the remote upload workspace.

**Step 2: Replace generated template text with real 4.0 product copy**
- Describe the new admin console and operational workflows.
- Update docker image references to `leduchuong/telegram_mediachanel_manager_bot`.
- Use current UI screenshots only.

**Step 3: Verify release copy**
- Confirm README references 4.0.
- Confirm README does not mention old screenshot assets.
- Confirm release notes mention 3.5 -> 4.0 comparison and recommend upgrade.

### Task 3: Publish GitHub and create v4.0 release

**Files:**
- Publish sanitized GitHub copy to `https://github.com/leduchuong48-byte/telegram_chanel_manager_bot`
- Create release `v4.0`

**Step 1: Push sanitized GitHub copy**
- Use token-based publish script.

**Step 2: Create GitHub release**
- Publish `v4.0` release with final release notes.

**Step 3: Verify**
- Check README raw URL.
- Check release URL exists and includes the expected body.

### Task 4: Create sanitized Docker Hub release copy

**Files:**
- Create/modify in sanitized copy: `README.md`
- Create/modify in sanitized copy: `README_en.md`
- Create/modify in sanitized copy: `DOCKERHUB_DESCRIPTION.txt`
- Create/modify in sanitized copy: `DISCLAIMER.md`

**Step 1: Run Docker sanitize copy**
- Target repo: `leduchuong/telegram_mediachanel_manager_bot`
- Use current UI screenshot URLs only.

**Step 2: Replace template overview with real 4.0 content**
- Make 4.0 UI redesign the headline.
- Summarize task center / cleaner / role boundary changes.
- Recommend upgrade from 3.5.

**Step 3: Verify overview assets**
- Check README uses current screenshot URLs only.
- Check short description <= 100 chars and aligned with 4.0 positioning.

### Task 5: Publish Docker Hub image and verify tags

**Files:**
- Publish image: `leduchuong/telegram_mediachanel_manager_bot:4.0`
- Publish image: `leduchuong/telegram_mediachanel_manager_bot:latest`

**Step 1: Attempt buildx publish**
- If multi-arch builder is unavailable, use the existing fallback approach and publish from a local source image.

**Step 2: Publish metadata**
- Sync short description and README overview to Docker Hub.

**Step 3: Verify**
- Confirm `latest` exists.
- Confirm `4.0` exists.
- Report whether the final artifact is multi-arch or single-arch.
