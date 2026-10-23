# Upstream v3 upgrade implementation plan

**Goal:** Upgrade to OpenBMB/StaffDeck ccf25abe while preserving all committed and uncommitted local customizations. User selected the complete v3 migration.

**Architecture:** Merge upstream into a snapshot of local/custom in the isolated clone. Retain the new Harness v3 engine and port local data-query capabilities, artifact delivery, deterministic schedules and Feishu routing into its compatibility seams. Keep the original checkout and service intact until validation is complete.

**Constraints:** Python >=3.11; Node 22.x >=22.19 or >=24; SQLite migration; secrets and business state excluded from commits. Do not create issues or send test notifications. Preserve the complete source backup at ../StaffDeck-backup-20261023-090622.

## Task 1: Protect local customizations
- [x] Clone upstream and pin ccf25abe.
- [x] Back up Git history, binary patches, untracked sources, private environment and consistent SQLite database.
- [x] Commit the source-only working copy snapshot on codex/upgrade-v3-preserve-custom in the clone.

## Task 2: Merge and resolve conflicts
- [x] Merge main with --no-commit, keeping both parents.
- [x] Runtime: preserve upstream v3 orchestration, port data query fast path, capability discovery and artifact/Feishu behavior from local commits. Files: backend/app/api/chat.py and backend/app/core/{capability_manifest,harness_capability_invoker,harness_v2_engine}.py.
- [x] Scheduling/channels: preserve multiple daily times, intervals, deterministic pipeline, recipient isolation, recall binding and worker timeout fixes. Files: backend/app/api/channels.py, backend/app/channels/adapters/feishu.py, backend/app/scheduled_tasks/{service,worker}.py.
- [x] Frontend: retain local data query/share management routes and menus alongside upstream navigation and streaming protocol. Files: frontend-enterprise/src/{components/AppSidebar.tsx,enums/routes.ts,i18n/en.json,pages/chat/chatHelpers.test.ts}.
- [x] Configuration: combine .gitignore and backend/app/config.py, preserving local reporting and deployment settings. Verify auto-merged model/schema, dependency and startup files against both sides.

## Task 3: Validate preservation and v3 runtime
- [x] Check no conflict markers remain; git diff --check.
- [x] Use a separate virtualenv and isolated database; install backend[dev], then python prepare.py --no-cli-link. Do not reuse production secrets or schedulers in smoke tests.
- [x] Run frontend npm ci, npm test -- --run, npm run build, npm run i18n:check and npm run config:check.
- [x] Run backend tests and tests_harness, comparing Windows POSIX/symlink baseline. Specifically exercise data_query, scheduled_task_pipeline, sales_snapshot, artifact_share_link and channel routing tests.
- [x] Validate sales same-snapshot ranking, exact influencer names, and cumulative-total row visibility remain intact.
- [x] Smoke test v3 health, console routes, native data-query tool capability and readonly persistence migration using the backed-up state only.

## Task 4: Integrate
- [x] Record test outcomes and finish the merge commit.
- [x] Preserve the original branch; transfer the validated upgrade branch and switch the live checkout only after compatible runtime artifacts are prepared.
- [x] Restart service, confirm health and inspect existing scheduled task configuration without manual notifications.

## Review focus
- v3 capability transport must retain tenant/employee authorization for native data queries.
- SQLite initialization must not reset archived employees, templates, task settings or artifact links.
- Feishu recall and outbound target resolution must preserve explicit binding selection.
- Business failures must not become successful pipeline records.
- Live sales table and ranking must share a snapshot; zero increment must not hide nonzero daily totals.

## Validation record (2026-10-23)

- Upstream pinned to ccf25abe; local source snapshot 51603dc9 retains all 20 working-copy files, on top of the 26 original customization commits.
- All 14 textual merge conflicts resolved; v3 native capability adapters preserve data query, HTML reports, task-specific Feishu scope and returned artifact delivery.
- Frontend: 386 tests / 68 files passed, TypeScript and Vite production build passed, i18n 4747 translations and config checks passed.
- Custom data-query/sales/report checks: 167 passed, one previously failing local first-push chart expectation deselected (pre-existing before this upgrade).
- Artifact management: 79 passed with hermetic test storage.
- Focused scheduling: 43 passed; additional worker/provider checks 24 passed.
- Runtime: 59 module/host checks, 18 native/report/scope checks and 97 async/channel/engine checks passed (overlapping selections, not summed).
- New framework broad run reached 417 passed, 2 skipped, 2 xfailed; exact 10 remaining failures independently reproduced against pristine upstream ccf25abe. Windows POSIX-mode permission assertion also remains upstream-only.
- Node runtime host and client builds passed; native CLI --help passed. V3 assembly initialized and health/console/data-query/share/chat routes returned 200 against an isolated database copy.
- Database migration on backup copy preserved all selected existing business fields: 11 scheduled tasks, 5 templates, 14 employees, 1 share link, 2 model configs and 1 channel binding; SQLite quick_check passed. Original APP_SECRET decrypted existing model credentials successfully.
- Windows Python 3.11 non-ASCII editable SDK paths normalized in dependency installer; regression passed.
- Deployment Docker recipe now builds Node v3 runtime and installs the local SDK, preserving existing compose volumes/port/user/sandbox. Docker image build not executed on this host.
- No manual business notifications or live model calls were sent during validation.

## Rulings

- User explicitly selected complete v3 migration, superseding the original v2-only runtime guideline.
- Keep legacy chart behavior and record its pre-existing test mismatch rather than change unrelated sales presentation.
- Do not rewrite the ten upstream failing contracts; preserve and document their baseline.

## Live cutover completed

- Original workspace is now on codex/upgrade-v3-preserve-custom; upstream ccf25abe is an ancestor. The original feature branch remains intact, and codex/pre-v3-20261023 (d3d3f188) preserves the complete original uncommitted source snapshot.
- Original backend environment saved as ../StaffDeck-backup-20261023-090622/backend-venv-v2; new Python dependencies and local Harness SDK installed in backend/.venv.
- Bundled Node was 22.14.0 (below v3 requirements); backed it up and installed the existing vendor Node 24.11.1 binary. Native CLI version command passed under the selected runtime.
- Service restarted from staffdeck_harness.distribution.asgi; port 5173 and health check passed, applied assembly reports engine=harness_v3.
- Read-only post-cutover checks confirmed original task definitions, templates, share-link expiry, employee state, encrypted model credentials and channel settings are unchanged; backend/.env bytes match the backup.
- Existing administrator browser session remained valid. Verified live pages: connected Feishu channel, 3 data sources, 5 query templates, one active task-bound HTML share link, sales employee scheduled task and historical successful runs.
- Real Node engine + real MCP round trip + local scripted model: 2 sealed successful-turn variants passed in 35.32s, with no external model credentials.
- Additional sealed unknown-tool retry-count failure also reproduced on pristine upstream; not an upgrade-specific regression. Docker image and external live-model/notification tests remain unrun.
- Temporary W: drive alias removed after artifact transfer. Source working tree is clean after committing this validation record.
