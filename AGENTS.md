# Repository Guidelines

## Project Structure & Module Organization

StaffDeck combines a Python 3.11+ FastAPI service with a React/TypeScript console. Backend application code lives in `backend/app/`; entry points such as `backend/single_port_app.py` support the desktop and single-port runtime. Backend tests are in `backend/tests/`, and the supported conversation runtime is Harness v2. Frontend code is in `frontend-enterprise/src/`, with static assets in `frontend-enterprise/public/` and colocated `*.test.ts` or `*.test.tsx` files. Use `scripts/` for development lifecycle tooling and `packaging/` for platform release assets.

## Build, Test, and Development Commands

- `python3 -m venv backend/.venv && backend/.venv/bin/python -m pip install -e "backend[dev]"` installs backend and test dependencies.
- `npm --prefix frontend-enterprise ci` installs the locked frontend dependencies.
- `scripts/dev_up.sh --detach` builds the frontend and starts the single-port app; use `scripts/dev_status.sh` and `scripts/dev_down.sh` to inspect or stop it.
- `backend/.venv/bin/python -m pytest backend/tests` runs the backend suite.
- `backend/.venv/bin/ruff check backend` checks Python style.
- `npm --prefix frontend-enterprise test` runs Vitest; `npm --prefix frontend-enterprise run build` performs TypeScript checking and the production Vite build.
- Run `i18n:check` and `config:check` from `frontend-enterprise` when changing UI text or Vite environment usage.

## Coding Style & Naming Conventions

Python uses four-space indentation, type hints, `snake_case` functions/modules, and `PascalCase` classes; Ruff targets Python 3.11 with a 100-character line limit. TypeScript is strict and follows the existing two-space, single-quote, semicolon style. Name React components in `PascalCase`, hooks with `use...`, and tests after the unit under test. Prefer the `@/` alias for frontend imports.

## Testing Guidelines

Name Python tests `test_*.py` and frontend tests `*.test.ts(x)`. Add focused regression tests for behavior changes, especially permissions, persistence, streaming, and channel routing. No numeric coverage threshold is configured; changed paths should still be exercised. For UI changes, also verify the affected route and user role in a browser.

### Known environment-specific test failures (Windows)

Running the full backend suite on Windows has a pre-existing baseline of environment-related failures (verified 2026-09-16: 48 failed / 2198 passed). These are not regressions — do not "fix" them as part of unrelated changes:

- `test_lark_cli.py` (~23 cases) — `OSError` from subprocess/environment differences.
- `test_harness_command.py`, `test_harness_v2.py`, `test_runner_bash_guard.py`, and the bash-package cases in `test_general_skills.py` — the POSIX sandbox (`bwrap`)/bash is unavailable or behaves differently on Windows.
- `test_harness_artifact_download.py`, `test_attachment_store.py`, `test_harness_session_cleanup.py` — symlink permissions are restricted on Windows.
- `test_feishu_process_spike.py`, `test_feishu_durable_inbox.py`, `test_channel_wecom.py`, `test_runtime_lock.py`, `test_tools_api.py` (stdio MCP probe), `test_markdown_render.py`, `test_memory_service.py` — sporadic process/environment failures.

When triaging, compare against the pre-change baseline first: failures concentrated in the files above and tied to the POSIX sandbox, symlinks, or lark-cli binaries can be treated as known environment issues. Anything outside that pattern still needs investigation before being attributed to the environment.

## Commit & Pull Request Guidelines

Follow the history’s concise Conventional Commit pattern, such as `feat(channels): add binding status` or `fix: reject unsafe avatar URLs`. Keep commits focused. Pull requests should explain intent and risk, link relevant issues, list tests run, and identify routes and roles used for UI validation. Include screenshots for visible changes and preserve unrelated worktree changes.

## Security & Configuration

Copy `backend/.env.example` to `backend/.env`; never commit secrets or channel credentials. Use strong `APP_SECRET` values and least-privilege external credentials. The supported production migration path currently assumes SQLite.

## Agent skills

### Issue tracker

Do not create or manage issues for this repository. See `docs/agents/issue-tracker.md`.

### Triage labels

Issue triage labels are not used. See `docs/agents/triage-labels.md`.

### Domain docs

Use the single-context documentation layout. See `docs/agents/domain.md`.

### Fixed-flow scheduled tasks

For scheduled tasks following the "Fetch -> Process -> Deduplicate -> Feishu Push" fixed workflow, follow the standardized deterministic pattern. See code-level specification in `backend/app/scheduled_tasks/fixed_process_workflow.md` and `skills/fixed-etl-scheduled-task/SKILL.md`.
