# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- **Tickets**: First-response tracking. The first reply from someone who can
  manage the ticket's category (and isn't the person who opened it) is
  recorded, and open tickets with no such reply after
  `settings.sla.first_response_hours` (default 6) are listed in
  `settings.sla.alert_channel_id` (falling back to the log channel), pinging
  that category's staff roles. Repeats every
  `realert_after_hours` (default 12) until answered. Set
  `first_response_hours: 0` to disable. Tickets that predate the feature are
  backfilled from thread history on startup, so an already-answered ticket
  isn't flagged forever; the same pass doubles as a safety net for replies
  posted while the bot was offline.
- Metrics now survive restarts. Counters are persisted to
  `oak/admin/metrics.json` on shutdown and every ten minutes, and reloaded at
  startup, so a deploy no longer zeroes `/metrics`. `Metrics.reset()` clears
  them deliberately, and the tracked `since` timestamp shows the real window.

- Error logs are now forwarded to Discord. Set `ERROR_LOG_CHANNEL` (and
  optionally `ERROR_LOG_LEVEL`, default `ERROR`) and failures that previously
  only reached the container log get posted to a channel. Reports are deduped
  over a five-minute window and capped per flush, so an outage produces a
  handful of messages rather than hundreds, and `discord.*` records are never
  forwarded so a failed send can't feed itself.
- **Application**: Submitted applications that nobody has reviewed now nudge
  admin chat, listing each application's number and how long it has been
  waiting. Configurable via `settings.review.stale_after_days` (default 7)
  and `renudge_after_days` (default 7). The inactivity sweep deliberately
  ignores `pending`, since that delay belongs to the reviewers, so nothing
  had been closing this loop.
- **Application**: `/appstats` now explains gaps in application numbers,
  reporting how many were started but never submitted. Numbers are assigned
  at channel creation, so abandoned attempts leave gaps that otherwise read
  as missing applications.
- `tests/`: a pytest suite (184 tests) over the framework and branch logic
  that runs without a Discord connection, plus a GitHub Actions workflow
  running it on Python 3.10 alongside a `compileall` pass over every module.

- **Application**: Open applications are now closed out when the applicant
  leaves the Discord server. Previously the application sat pending forever
  and the channel lingered with a permission overwrite for someone who was
  gone, until a human happened to notice. In-progress and pending
  applications get status `left_server`, a notice is posted in admin chat
  with the application number, its prior status and the applicant's
  Minecraft name, and the channel is deleted. The record itself is kept, so
  the answers stay available in `/apphistory`. Accepted applications are
  left alone. Handled both by an `on_member_remove` listener and by a sweep
  on the 12-hour inactivity task, so departures during a restart or deploy
  aren't missed.

### Removed

- **Suggestions**: The legacy `likes`/`dislikes` JSON columns are dropped once
  their contents are in `suggestion_votes`. The drop is guarded on the columns
  existing and on the votes table being populated, so a fresh database and an
  unmigrated one are both safe, and it degrades to leaving them in place on
  SQLite older than 3.35.

### Fixed

- **Tickets**: The panel is no longer deleted and reposted on unrelated config
  changes. `hash_config()` hashed the entire config, so editing a transcript
  URL, a staff role or a database credential churned the panel and lost its
  message id and any pin. It now hashes only what the panel renders: the
  panel title, description, colour and field name, plus each enabled
  category's label, emoji and description.
- **Application**: The Background Check button now defers before running its
  archived-thread scan and Plan playtime lookup. Both can exceed Discord's
  three-second interaction window, which showed up to staff as "This
  interaction failed" instead of a result.
- **Application**: Accepting an application whose applicant has left the
  server now falls back to a user fetch, and reports the DM as failed when
  the user can't be resolved at all. Previously the DM step was skipped
  silently and the channel still announced the applicant had been notified.
- **Application**: Denial reasons are now escaped wherever they're displayed.
  The application-history views rendered stored reasons raw while the
  decline message escaped them.
- **Tickets**: The thread-update debounce cache is now pruned by the listener
  rather than only by the anti-archive loop, so it no longer grows unbounded
  when `anti_archive.enabled` is false.
- Reads (`fetchone`/`fetchall`) now take the branch write lock. They share the
  single connection with `transaction()`, so a read landing mid-transaction
  ran inside it and could observe rows that were later rolled back.

### Changed

- **Vote Reminders**: `/votereminder` now declares
  `default_permissions(administrator=True)` so it's hidden from non-admins
  rather than only rejected at runtime.
- **Application**: Modal page size is now the shared `QUESTIONS_PER_PAGE`
  constant instead of a literal `5` repeated across the question split and
  the post-restart resume step.
- `BranchLoader.branch_path()` exposes a branch's directory so callers no
  longer reach into `_paths` (the admin branch was doing this).

### Security

- **Application**: Start/Continue application buttons now verify that the
  clicking user is the applicant for the channel, so a stray reviewer or
  anyone else who can see the channel can't submit answers on someone else's
  behalf.
- **Application**: The Apply button now enforces `required_link_role_id` as a
  real gate, rejecting users without the linked-account role before a channel
  is created (previously the setting only sent a reminder after submission).
  The apply panel lists the requirement and a configurable `link_required`
  message explains how to link.
- **Tickets**: Hosted transcript URLs now embed an unguessable 22-character
  token in the filename, so transcripts can no longer be enumerated by ticket
  number. The transcript web server also defaults to binding `127.0.0.1`
  (set `transcript.web.bind_host` to override) instead of all interfaces.

### Changed

- **Application**: The `button_label` setting is now honored on the Apply
  button (it was previously hardcoded), the default welcome message no longer
  states a fixed "5-10 minutes" completion time, and the unused `cleanup`
  config block (which referenced a non-existent `/appcleanup` command) has
  been removed.
- **Tickets**: Ticket creation no longer holds the SQLite write lock during
  the Discord thread-creation API call. Numbered tickets now use a three-step
  reserve → create thread → finalize flow, and orphaned reservations from
  crashed runs are cleared on branch startup.
- **Application**: Decline modal now claims the denial in the database before
  sending the DM, so two reviewers can't both DM conflicting decisions for the
  same application.
- **Application**: `appstats` and `apphistory` now accept Discord
  administrators as well as configured reviewer roles, matching the
  `default_permissions(administrator=True)` already on those commands.
- **Economy**: API calls now build query strings via aiohttp's `params=` and
  URL-encode path segments with `urllib.parse.quote`, so user-supplied values
  (item, query, player, currency) can't break the URL or override parameters.

### Fixed

- Framework: `/enable` and successful `load_branch()` now clear stale entries
  from `_skipped` and `_load_failures`, so `/branches` and `/health` no longer
  report a freshly-loaded branch as disabled or failed.
- **Application**: `on_disable()` now stops every persistent view it
  registered, so `/reload` no longer leaves stale view callbacks pointing at
  the old module globals or a closed DB.
- **Suggestions**: Added `on_disable()` to stop registered `SuggestionVoteView`
  instances on unload/reload, matching the tickets/application pattern.
- **Tickets**: `/ticketstats` now filters out `status='reserved'` rows so
  transient in-flight reservations don't briefly inflate totals or appear as
  a phantom status bucket.
- **Application**: Removed `default_permissions(administrator=True)` from
  `/appstats` and `/apphistory` — the decorator hid the commands from
  non-admin reviewers in Discord's UI even though the runtime `is_staff()`
  check would have let them through. Permissions are now enforced solely at
  runtime.
- **Application**: Bumped the paginated-application view timeout from 3
  minutes to 15 minutes so reviewers reading long applications don't lose
  the Next button while they're still reading.

### Added

- **Application**: Applicant-facing messages (welcome, apply panel, submission,
  acceptance, denial) are now configurable via a `messages:` config section,
  falling back to the existing copy as defaults. The `denied` message supports
  a `{reason}` placeholder for the reviewer's reason.
- **Vote Reminders**: New branch that posts a daily vote reminder embed in
  a configured channel at a configured UTC time, pinging a subscriber role.
  Includes an admin-only `/votereminder` command to post on demand for
  testing or ad-hoc pushes.
- **Tickets**: New `transcript.web.bind_host` config option (defaults to
  `"127.0.0.1"`) so the transcript server's listen address is discoverable
  in `config.yml`.
- **Tickets**: Periodic cleanup task prunes orphan transcript HTML files —
  ones whose Discord delivery failed before the filename was recorded, or
  whose ticket row was later deleted. Files newer than
  `transcript.web.retention_hours` (default 24) are left alone.
- **Application**: New `cleanup_completed_at` column on `applications`
  tracks denied-channel auto-deletion so the inactivity loop doesn't keep
  rediscovering the same row every 12h forever.

### Security

- **Tickets**: Transcript web server adds strict security headers
  (`Content-Security-Policy: default-src 'none'`, `X-Content-Type-Options`,
  `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`) and a
  `Path.resolve()` containment check, plus NUL-byte rejection on
  filenames. Defense in depth on top of the existing token-in-filename.
- **Suggestions**: `on_message` now rejects DMs and threads, and validation
  notice messages use `allowed_mentions` scoped to the author so a forged
  `@everyone` in suggestion content can't ride through the reply.
- **Application**: Startup warning if `accepted_category_id` grants
  `@everyone view_channel` — moving an application channel into that
  category inherits its overwrites and would expose answers.

### Changed

- Framework: Migrations now run inside `BEGIN IMMEDIATE`/`COMMIT` so a
  multi-statement migration that fails partway through rolls back cleanly
  instead of silently leaving the schema half-applied with the migration
  row marked complete. "Already applied" is only swallowed for a single
  `ALTER TABLE ADD COLUMN` against legacy databases.
- Framework: `BackupManager.stop()` and `BranchWatcher.stop()` are now
  async and await task cancellation, so a mid-flight backup or reload
  can't race with branch unload during shutdown.
- Framework: `OakBot.on_message()` prefix handling now slices the prefix
  off as a substring (was `lstrip(str(prefix))`, which strips any
  combination of the prefix's characters — a prefix like `"oak "` would
  chew o/a/k/space off the front of the command name).
- Framework: `write_branch_enabled()` writes to a temp file in the same
  directory then `os.replace()`s, so a crash mid-write can't truncate
  the YAML file (branch configs may hold secrets).
- Framework: `OakConfig` clamps `DB_BACKUP_INTERVAL` to >= 0 and
  `DB_BACKUP_MAX_COUNT` to >= 1 so misconfigured env vars can't burn CPU
  in a tight asyncio sleep loop or delete every backup immediately.
- **Tickets**: `/closeticket` and the close button now claim the close
  atomically (`UPDATE ... WHERE status = 'open'`) and bail without
  touching reminders or thread state if another writer already won the
  race. The thread-edit-failure revert is scoped by `closed_by` so it
  can't unconditionally reopen a ticket someone else legitimately closed.
- **Tickets**: Reminder send failures now bump `last_reminded_at` so a
  Discord outage doesn't make the same reminder refire every minute.
- **Tickets**: Anti-archive unauthorized-unarchive check now scans up to
  25 recent audit log entries within the last 2 minutes (was 5 entries
  with no time bound), so a burst of unrelated thread updates can't push
  the relevant entry out of the search window.
- **Tickets**: Transcripts are capped at 5000 messages; the remainder is
  noted with a truncation footer rather than swallowing memory on a
  multi-thousand-message ticket.
- **Economy**: Response cache is now an LRU bounded at 500 entries —
  autocomplete keystrokes (`q=d`, `q=di`, …) no longer grow the dict
  forever. `/recent` skips the cache entirely (`ttl=0`). API error
  logs now show `type(e).__name__` to avoid leaking response bodies.
- **Economy**: `/player` treats `tradeCount=0` as a valid response
  instead of "no data found"; embed titles for `/search` and
  `/playershops` are clamped to 256 chars so a long input can't bust
  Discord's title limit.
- **Suggestions**: `topsuggestions` ORDER BY now uses the explicit
  aggregate expressions instead of bare identifiers, so the legacy
  `likes`/`dislikes` text columns can't accidentally shadow the
  aggregates and yield lexicographic ordering on JSON.
- **Suggestions**: `_migrate_votes` is now per-row tolerant — a single
  malformed legacy JSON payload no longer aborts the whole migration
  and loses votes for every other suggestion.
- **Status Channels**: Member and player updates are independent helpers
  so a failure in one no longer early-returns the whole tick and skips
  the other. The Minecraft `JavaServer` lookup result is cached and
  re-resolved only on failure (was a fresh DNS+SRV lookup every tick).

### Fixed

- **Tickets**: Transcript on-disk filename is now recorded in the DB
  only after at least one Discord delivery succeeds; if every delivery
  path fails, the saved HTML is deleted immediately rather than left
  on disk for retention pruning. Previous fix recorded the filename
  too eagerly and would have kept truly-orphan files referenced
  forever.
- Framework: `BranchWatcher` now sets a `_stopping` flag and shields
  in-flight reloads with `asyncio.shield`, so shutdown cancellation
  can't interrupt the loader while it's mutating branch state. The
  prior change only awaited the cancelled task, which doesn't prevent
  cancellation from cutting through the reload mid-flight.
- Framework: `reload_branch()` failures now record the error in
  `_load_failures`, so `/branches` and `/health` show the broken state
  instead of treating the branch as cleanly absent.
- Framework: When `add_cog`/`on_enable` fails, the loader now also
  unregisters any tasks the branch had time to schedule and attempts a
  `remove_cog` cleanup, so partial state from a half-loaded branch
  doesn't survive the failed load.
- Framework: `paginate()` raises on `page_size <= 0` instead of looping
  forever.
- **Application**: New partial unique index on `applications(user_id)
  WHERE status IN ('in_progress', 'pending')` makes the in-memory
  `_creating_users` dedupe authoritative — a user whose previous
  application is `cancelled`/`abandoned`/`denied` can still re-apply,
  but can't spam the button to create multiple channels in flight.
- **Application**: Accept button now catches `discord.HTTPException`
  (not just `Forbidden`), so a transient outage during the DM doesn't
  leave the application in `accepted` with no public message and no
  notification.
- **Application**: `ApplicationHistoryView` selection handles
  malformed `answers_json` without raising — the user gets an
  ephemeral error rather than a silent "interaction failed".

## [2.0.0] - 2026-02-20

Complete architectural rewrite to the Oak framework. All branches now use centralized
database access, framework-managed config, and a standardized lifecycle. This is a
breaking change for all branch code.

### Added

#### Oak Framework (`oak/` package)

- `OakBot` class replacing the root `Oak` class, with config injection, metrics, audit
  logging, and graceful shutdown
- `OakBranch` base class with `on_enable()`/`on_ready()`/`on_disable()` lifecycle hooks,
  `self.setting()` helper for nested config access, `self.services` dict for inter-branch
  service registry, `require_branch()` for cross-branch dependencies, and `register_task()`
  for background task tracking
- `BranchDatabase` class (`oak/database.py`) with persistent `aiosqlite` connection, WAL
  mode, `asyncio.Lock` write serialization, and built-in methods: `execute()`, `fetchone()`,
  `fetchall()`, `executemany()`, `transaction()` context manager, `initialize()` for schema,
  `migrate()` with named migration tracking in `_oak_migrations` table, `backup()` for
  WAL-safe file copies, and `raw_connection()` for advanced use
- `BranchContext` frozen dataclass providing `bot`, `id`, `name`, `config`, `db`, `logger`,
  `data_dir`, `events`, and `interactions` to each branch
- `BranchLoader` (`oak/loader.py`) with manifest-based discovery, dependency resolution via
  topological sort, `load_branch()`/`reload_branch()`/`unload_branch()` lifecycle,
  `require_branch()`, and tracking of skipped/failed branches
- `BranchManifest` (`oak/manifest.py`) — `branch.yml` files declaring `id`, `name`,
  `version`, `description`, `author`, `main`, `database`, `priority`, `dependencies`, and
  `oak_version` compatibility gate
- `EventBus` (`oak/events.py`) for inter-branch pub/sub with `OakEvent` dataclass, wildcard
  subscriptions (`*` and `foo.*` patterns), 30-second listener timeout via
  `asyncio.wait_for`, parallel dispatch via `asyncio.gather`, and scoped
  `BranchEventHandle` with auto-tagging
- `InteractionRouter` (`oak/interactions.py`) for `oak:{branch}:{action}[:value]` custom ID
  routing with strict validation (3-4 colon-delimited parts, non-empty segments, value
  pattern `[A-Za-z0-9._-]+`), plus scoped `BranchInteractionHandle` for building and
  parsing namespaced custom IDs
- `OakConfig` (`oak/config.py`) with `.env` loading, validation (raises `ConfigError`
  instead of `sys.exit`), and properties: `command_prefix`, `dev_mode`,
  `audit_log_channel`, `db_backup_interval`, `db_backup_max_count`
- `deep_merge()` function using `copy.deepcopy` to prevent shared mutable references
- `write_branch_enabled()` function for toggling `enabled` flag in branch config files
- `TaskRegistry` (`oak/tasks.py`) tracking `discord.ext.tasks.Loop` instances per branch
  with `is_running`, `failed`, `next_iteration`, `current_loop` status, used by `/health`
- `BackupManager` (`oak/backup.py`) with periodic backup scheduling via
  `DB_BACKUP_INTERVAL` env var, WAL checkpoint under write lock, `shutil.copy2` in executor
  thread, old backup pruning via `DB_BACKUP_MAX_COUNT`
- `PaginatedEmbedView` (`oak/views.py`) — reusable paginated embed with Previous/Next
  buttons, author-locked interaction, auto-disable on timeout, configurable timeout
- `Metrics` (`oak/metrics.py`) — in-memory counters for commands, events, db_writes,
  db_reads, and errors, with `inc()` and `summary()` methods
- `BranchWatcher` (`oak/watcher.py`) — file system polling for `.py` changes with
  auto-reload in `DEV_MODE`
- Custom exception hierarchy (`oak/errors.py`): `OakError`, `BranchLoadError`,
  `BranchNotFoundError`, `ManifestError`, `ConfigError`
- `oak/constants.py`: added `BRANCH_MANIFEST_FILE`, `CUSTOM_ID_PREFIX`,
  `CUSTOM_ID_MAX_LENGTH`, `EVENT_LISTENER_TIMEOUT`; `truncate_for_embed_field()` now
  accepts optional `max_length` parameter
- `oak/utils.py`: `sanitize_text()`, `truncate_for_embed_field()`, `truncate()`,
  `format_discord_timestamp()`, `safe_send()`, `paginate()`

#### Admin Commands

- `/enable <branch>` — enables a disabled branch (writes config, loads, syncs commands,
  audit logs)
- `/disable <branch>` — unloads and disables a branch (writes config, syncs commands,
  audit logs)
- `/health` — bot uptime, latency, branch count, per-branch database health check
  (`SELECT 1`), scheduled task status (running/stopped/failed with loop count), load
  failures, disabled branches
- `/metrics` — top 10 events, DB reads/writes per branch, top 10 commands, error counts
- `/backup [branch]` — backs up a single branch or all branches with databases
- `/sync` — force-syncs slash commands to guild
- `/branches` redesigned: shows loaded (green), disabled (grey), and failed (red) branches
  with version numbers
- `/botinfo` expanded: uptime, latency (ms), memory RSS, branch ratio, Python version,
  discord.py version, Oak version
- `/reload`, `/load`, `/unload` now defer interaction, re-sync commands, and send audit
  logs; error messages no longer expose exception details to users
- All admin commands enforce `@app_commands.checks.has_permissions(administrator=True)` in
  addition to `@app_commands.default_permissions(administrator=True)`

#### Tickets Branch

- Transcript generation system (`branches/tickets/transcript.py`): full HTML transcripts
  with Discord dark theme CSS, message grouping within 7-minute windows, system message
  rendering, inline image display, file attachment links, embed rendering with colored
  borders, and Discord markdown to HTML conversion (bold, italic, underline, strikethrough,
  code blocks, inline code, mentions)
- Transcript web server (`branches/tickets/web.py`): `aiohttp`-based server serving HTML
  transcripts with path traversal protection, configurable via `transcript.web` settings
- `/closeticket` now generates transcript before archiving and sends it to log channel
  and/or DMs the ticket creator
- `transcript_message_id` column added to `tickets` table via migration

#### Suggestions Branch

- `suggestion_votes` table with composite primary key `(suggestion_id, user_id)` and CHECK
  constraint on `vote_type`; backfilled from legacy JSON columns via `_migrate_votes()`
- `/topsuggestions` command: highest-voted suggestions with status filter (All/Pending/
  Approved/Denied), sort options (net votes/most likes/most dislikes), paginated embed with
  jump links to original messages
- Config validation warnings on `on_enable()` for placeholder `channel_id` and empty
  `manager_role_ids`

#### Application Branch

- `DEFAULT_CONFIG` dict with all settings defined in code (previously only in config.yml)
- Staff permission checks on all manage buttons (Accept, Move, Decline, Background Check,
  View History) and `StatusChangeView._update_status()` via `_check_staff()` method
- Application status validation before state changes: accept checks `status = 'pending'`
  with `WHERE` clause and `rowcount` check; decline checks `status = 'pending'`; move
  checks `status = 'accepted'`
- Post-restart answer recovery: `ContinueView` recovers partial answers from database if
  `self.answers` is empty after bot restart, calculates correct resume step
- Partial answer saving on each modal page submission (previously only saved on completion)
- Cancel button verifies the clicking user is the actual applicant and updates DB status to
  'cancelled' before deleting channel
- Archived thread search in background check (searches both cached and
  `archived_threads(limit=100)` in punishment forum)
- Pagination safety counter with `max_iterations` limit to prevent infinite loops, with
  guaranteed forward progress (at least one field per page)
- Null-safe `paginate_application_embed()` when applicant has left the server
- 3-phase creation pattern: Phase 1 (check existing, close DB), Phase 2 (create Discord
  channel, no DB), Phase 3 (atomic INSERT with subquery for app_index)
- Atomic `app_index` assignment via `INSERT ... (SELECT COALESCE(MAX(app_index), 0) + 1)`
- Placeholder config warnings for `reviewer_role_ids == [0]` and
  `application_channel_id == 0`
- Inactivity check excludes abandoned-eligible apps from warning queries
- `DeclineReasonModal` defers interaction immediately to avoid timeout; reason sanitized
  with `escape_markdown(escape_mentions())`; fire-and-forget delayed channel deletion via
  `asyncio.create_task()`
- `ApplicationModal.on_error()` and `DeclineReasonModal.on_error()` handlers
- Config validation on `on_enable()`: warns for placeholder IDs, errors if
  `abandon_after_days <= warning_after_days`
- Application history search increased from `limit=10` to `limit=50`

#### Shopkeepers Branch

- `_escape_like()` method for SQL LIKE wildcard escaping in all user input queries
- `public` parameter on all public commands to control ephemeral/public response
- Admin shop exclusion in `/price` (filters `shop_type != 'admin'`)
- Price history chart generation (`chart.py`) using matplotlib with Discord dark theme,
  average price line, and min/max fill band
- Fuzzy item search prefers shortest match via `ORDER BY LENGTH(search_name) ASC`
- Compound index `idx_trades_shop_type_date` for shop-type-filtered date queries
- `auto_import_task.error` handler for unhandled import errors
- Query truncation safety (user input shown in error messages truncated to 100 chars)
- Embed description and field truncation using framework utilities
- CSV reader moved to `asyncio.to_thread()` to avoid blocking the event loop
- Max CSV file size check (50 MB limit)
- File stat calls via `asyncio.to_thread()`

#### Framework Infrastructure

- Rotating log handler (`TimedRotatingFileHandler`) replacing date-stamped log files, with
  midnight rotation and 30-day retention
- Logs directory relative to script (`Path(__file__).parent / "logs"`) instead of CWD
- `branches_dir` path made absolute relative to `oak/` package
- `setup_logging()` function wrapping logging configuration
- `CommandOnCooldown` error handling with user-friendly retry message
- `_ready_fired` flag preventing duplicate `on_ready()` dispatches on reconnect
- `on_message()` logs only the command name (not arguments)
- `on_command_error()` messages auto-delete after 10 seconds
- Command sync error handling with logging
- Duplicate branch ID detection in `BranchLoader.discover()`
- DFS-based topological sort replacing broken Kahn's algorithm for dependency resolution
- Module cache cleanup on reload (deletes all `branches.<name>.*` submodules)
- Resource cleanup on `add_cog` failure
- `_is_branch_enabled()` helper for config.yml enabled-flag check
- `_cleanup_branch_resources()` helper for event/database cleanup
- Reload failure logging
- `BranchDatabase.initialize()` double-init guard
- `BranchDatabase.migrate()` duplicate migration name check and error re-raise
- `BranchDatabase.write_lock` public property
- `EventBus` dispatch timeout for listeners (30 seconds with `asyncio.shield`)
- Manifest validation: semver format warning, branch ID digit prefix rejection, wrong-type
  field warnings, `oak_version` compatibility check
- `.env.example` with 5 new env vars: `COMMAND_PREFIX`, `DEV_MODE`, `AUDIT_LOG_CHANNEL`,
  `DB_BACKUP_INTERVAL`, `DB_BACKUP_MAX_COUNT`

#### Documentation

- Branch developer guide (`GUIDE.md`) covering: quick start, manifest reference, config
  system with `configure(db, config)` pattern, lifecycle hooks, database operations with
  `BranchDatabase`, slash commands, persistent views with namespaced custom IDs, modals with
  `on_error`, background tasks with `register_task()`, event bus, admin commands, paginated
  embeds, inter-branch services, database backups, and migration from pre-framework branches

### Changed

- All branch classes inherit from `OakBranch` instead of `commands.Cog`
- All branch constructors accept `BranchContext` instead of `bot`
- All `cog_load`/`cog_unload` lifecycle hooks renamed to `on_enable`/`on_disable`
- All `@commands.Cog.listener() async def on_ready` changed to plain `async def on_ready`
  (framework dispatches directly)
- All module-level `logger = logging.getLogger(__name__)` replaced with `self.log` from
  branch context
- All `__init__.py` files emptied (framework uses `branch.yml` for discovery, no more
  `setup()` functions)
- All raw `aiosqlite.connect()` calls replaced with `BranchDatabase` methods (`execute`,
  `fetchone`, `fetchall`, `transaction`)
- All disk-reading config helpers (`get_*_config()`, `get_db_path()`) replaced with
  `self.config` / `self.setting()`
- Views, modals, and handlers that can't access the branch instance use module-level
  `_db`/`_config` set by `configure(db, config)` in `on_enable()`
- Import paths changed: `from constants import ...` to `from oak.constants import ...`,
  `from utils import ...` to `from oak.utils import ...`
- `GUILD_ID` global replaced with `self.bot.guild_id`
- All persistent views register both legacy custom IDs and new `oak:{branch}:{action}`
  namespaced IDs for backward compatibility
- `DummyView` renamed to `SuggestionVoteView`
- Suggestions: votes exclusively use `suggestion_votes` table; legacy JSON columns still
  synced for backward compatibility but no longer written to on INSERT
- Suggestions: empty/too-short rejection messages sent as temporary channel replies
  (`delete_after=10`) instead of DMs
- Suggestions: `StatusModal` fetches correct channel via stored `channel_id` instead of
  using ephemeral interaction channel; re-checks manager permissions on submit
- Suggestions: vote handler uses single `interaction.response.edit_message()` instead of
  separate `message.edit()` + `defer()`
- Application: embed title uses `applicant.display_name` instead of `applicant.mention`
  (mentions don't render in embed titles)
- Application: modal title truncated to 45 characters (Discord limit)
- Application: database update moved before Discord API calls in `_complete_application()`
  and `DeclineReasonModal`
- Application: `StatusChangeView` buttons no longer have persistent custom_ids (ephemeral
  view)
- Application: `PostSubmissionView.read_application()` and `ApplicationHistoryView` use
  `PaginatedEmbedView` instead of multi-embed followup loops
- Application: background check MySQL operations run via `asyncio.to_thread()` to avoid
  blocking; `connect_timeout: 10` added
- Shopkeepers: branch-local `PaginatedEmbedView` replaced with framework
  `oak.views.PaginatedEmbedView`
- Shopkeepers: `_send_paginated()` accepts `ephemeral` kwarg (previously always ephemeral)
- Shopkeepers: `/price` command shows matplotlib price chart instead of text-based history
  (with text fallback for < 2 data points or missing matplotlib)
- Shopkeepers: `format_price()` returns `"Free"` for zero values (previously returned
  `"N/A"`)
- Shopkeepers: import affected dates tracked per-file and only merged after successful
  commit
- Status channels: loop interval changed from 6 minutes to 11 minutes
- Status channels: jitter changed from `random.uniform(-36, 36)` to
  `random.uniform(0, 36)` (always non-negative)
- Status channels: uses `async_lookup()` and `async_status()` instead of synchronous
  mcstatus methods
- Status channels: skips update when neither channel ID is configured
- Tickets: authorized reopen check via audit logs — unauthorized users have their reopen
  reversed (thread re-archived and re-locked)
- Tickets: thread update debounce with 5-second window to prevent duplicate processing
- Tickets: close cancels reminders atomically in the same DB transaction
- Tickets: orphan thread cleanup on DB insert failure
- Tickets: `sanitize_name()` uses Unicode-aware regex `[^\w\-]` instead of ASCII-only
- Tickets: `parse_time_string()` rejects zero values
- Tickets: `ConfirmCloseView` restricted to the initiating user
- Tickets: `TicketQuestionsModal` respects `required: false` with `min_length=0`
- Tickets: rate limit dict hard cap at 1000 entries
- Tickets: stale `_thread_update_times` entries cleaned every anti-archive cycle
- Tickets: persistent view cleanup (`.stop()`) in `on_disable()`
- All emojis removed from user-facing embed text (titles, descriptions, field names)
- Em dashes replaced with ASCII dashes, bullets changed from `•` to `-`/`*`
- Config file reads use explicit `encoding="utf-8"`
- `create_branch.py` updated: generates `OakBranch` subclass, `branch.yml` manifest,
  `self.setting()`, `self.log`, `self.db.initialize()`, `on_enable`/`on_disable`/`on_ready`;
  rejects reserved names and names starting with `_`; defaults to `database: false`
- `requirements.txt` updated with `aiohttp~=3.11.0` and `matplotlib~=3.10.0`
- README rewritten as concise reference-style documentation with tables
- CONTRIBUTING.md updated for Oak framework terminology and patterns

### Removed

- Root-level `config.py`, `constants.py`, `database.py`, `utils.py` — replaced by `oak/`
  package equivalents
- `core/branch_loader.py` — replaced by `oak/loader.py` and `oak/manifest.py`
- `branches/admin/` directory — admin branch moved to `oak/admin/` as built-in framework
  component
- All `setup()` functions and imports from branch `__init__.py` files
- All `load_config()` methods from branches
- All `get_*_config()`, `get_db_path()`, `get_embed_colors()` (no-arg), and
  `get_reviewer_role_ids()` disk-reading helpers
- All direct `import aiosqlite` from branch files
- `is_application_reviewer()` decorator
- `/reloadall` command (use `/reload` per branch or full bot restart)
- Deprecated `truncate_for_embed_field()` shim in `oak/constants.py`
- Deprecated `connect()` method on `BranchDatabase` (use `raw_connection()`)
- Unused functions removed across all branches: dead status_emoji dicts, f-strings without
  placeholders, redundant global declarations, unused variables and imports

### Fixed

- Application `app_index` race condition: atomic assignment via subquery INSERT instead of
  separate SELECT + INSERT
- Application database held during slow Discord API calls: split into 3-phase pattern
  releasing DB before channel creation
- Application answers lost after bot restart: recovered from database on `ContinueView`
  resume
- Application decline modal interaction timeout: defers immediately
- Application infinite loop in pagination: safety counter with guaranteed forward progress
- Application crash when applicant left server: null-safe `paginate_application_embed()`
- Application status double-update: atomic `AND status = 'pending'` WHERE clause in decline
  and `AND status = ?` in `StatusChangeView`
- Application corrupted JSON answers: caught with `try/except (json.JSONDecodeError, TypeError)`
- Suggestions manage fetching wrong channel: passes `channel_id` through view chain instead
  of using ephemeral interaction channel
- Suggestions double-status change: checks `status = 'Pending'` before allowing
  approve/deny
- Suggestions vote race condition: atomic operations on `suggestion_votes` table instead of
  JSON column read-modify-write
- Suggestions null-safe JSON parsing for `likes`/`dislikes` columns (handles NULL values)
- Suggestions reason field truncated to embed field limit (prevents HTTPException)
- Shopkeepers SQL injection via LIKE wildcards: escapes `%`, `_`, `\` in user input
- Shopkeepers blocking event loop during CSV import and file stat: moved to
  `asyncio.to_thread()`
- Shopkeepers null-safe currency calculation (handles `item2_id = 0` and
  `item2_amount = None`)
- Shopkeepers player/shop name crash on null UUID
- Status channels blocking event loop: async mcstatus lookup and status
- Status channels format string injection: rejects attribute access (`.`) and index
  access (`[`)
- Status channels crash when server returns no player information
- Tickets unauthorized reopen: audit log check identifies unarchiver and re-locks if
  unauthorized
- Tickets thread update spam: 5-second debounce window
- Tickets reminders not cancelled on close: atomic in same DB transaction
- Tickets rate limit memory leak: hard cap at 1000 entries with expired entry cleanup
- Tickets close race condition: `AND status = 'open'` guard
- Tickets null unarchiver: re-archives and logs when audit log lookup fails
- Framework `on_ready` firing on every reconnect: `_ready_fired` flag
- Framework stale manifest on reload: re-discovers manifest before reloading
- Framework circular dependency handling: DFS-based topological sort, cycle members excluded
  and recorded in `_load_failures`
- Framework stale submodule references on reload: cleans all `branches.<name>.*` from
  `sys.modules`
- Framework resource cleanup on `add_cog` failure: closes database and event handle
- Framework config parse failures now logged instead of silently ignored
- Framework event listener timeout: 30-second limit with `asyncio.shield`

### Security

- `.env` file no longer tracked in git; renamed to `.env.example` with `.env` in
  `.gitignore`
- Branch config files (`branches/*/config.yml`) added to `.gitignore` (may contain secrets)
- Decline reason and suggestion content sanitized with `escape_markdown(escape_mentions())`
  before display
- Format string injection prevention in status channels (rejects `.` and `[` in format
  strings)
- SQL LIKE wildcard escaping in all shopkeepers user input queries
- Staff permission checks enforced on all application management buttons
- Ticket reopen authorization via audit log verification
- Admin command error messages no longer expose exception details to Discord users
- Transcript web server validates filenames against path traversal (`..`, `/`, `\`)

## [1.2.0] - 2026-02-06

### Added

- **Shopkeepers branch** — Minecraft trade data analysis system for the Shopkeepers plugin
  - CSV-based trade log importer (`importer.py`) with SHA-256 fingerprint deduplication,
    incremental import, and configurable auto-import interval (default 30 minutes)
  - NBT metadata parser (`nbt_parser.py`) supporting legacy Bukkit format and modern 1.20.5+
    component format; extracts custom names (including gradient text), enchantments, potion
    types, shulker box contents, and plugin identity (ExecutableItems, ItemsAdder,
    PhoenixCrates, CrazyCrates, OakTools, NekoTraps, Shopkeepers)
  - Database schema: 5 tables (`imported_files`, `items`, `trades`, `price_summary`,
    `players`) with indexes on trade dates, item IDs, player UUIDs, and shop owners
  - Uniform shulker box expansion (single-item shulkers auto-expanded to bulk trades)
  - Incremental price summary rebuild (only affected dates recomputed)
  - Multi-tier currency system (emerald = 1, emerald_block = 9,
    CompressedEmeraldBlock = 576) with smart mixed-unit display (CEMB/EMB/EM)
  - Public commands: `/price` (price stats with history), `/top` (leaderboard by trades/
    price/volume), `/search` (fuzzy item lookup), `/player` (trade stats), `/shop` (shop
    owner stats), `/trending` (price change analysis), `/shops` (owner leaderboard),
    `/players` (player leaderboard)
  - Admin commands: `/shopkeepers_import` (manual CSV import), `/shopkeepers_stats`
    (database dashboard), `/economy` (sink/faucet analysis for admin shops), `/sinks`
    (detailed currency sink breakdown), `/faucets` (detailed currency faucet breakdown)
  - Item and player autocomplete handlers
  - `PaginatedEmbedView` with Previous/Next buttons and author-only interaction check
  - Configurable plugin identity keys, embed color, trades per page, and UI settings
- Ticket reminder message tracking and replacement: stores `last_reminder_message_id`,
  deletes old reminder before sending new one (prevents reminder clutter in ticket threads)
- `last_reminder_message_id` column added to `ticket_reminders` table via migration
- Global slash command error handler (`_on_app_command_error`) on `OakBot`
- `deep_merge()` utility function for recursive dictionary merging
- `load_branch_config()` now deep-merges loaded config against defaults
- `check_application_answer_quality()` moved from root `utils.py` to application helpers
- WAL journal mode enabled in `init_branch_database()`

### Changed

- Slash command sync moved from `on_ready()` to `setup_hook()` (prevents re-sync on every
  Discord reconnect, avoiding rate limits)
- `/branches` uses `bot.extensions` instead of `bot.cogs` (correctly lists branch modules)
- `/botinfo` counts `bot.tree.get_commands()` (slash commands) instead of `bot.commands`
  (prefix commands)
- `DummyView` no longer accepts unused `status` parameter
- Ticket panel validation moved from `cog_load` to `on_ready` (ensures bot cache is
  populated)
- Log channel ID validation range corrected to `2^64 - 1` (unsigned 64-bit snowflakes)
- Logging messages stripped of emojis throughout bot.py and branch_loader.py
- Error messages stripped of emojis in `on_command_error()`
- `on_message()` log simplified to `Command from {author}: {content}`
- Type hints modernized from `Optional[X]` / `Dict[str, Any]` to Python 3.10+ `X | None` /
  `dict[str, Any]`
- `create_branch.py` generates slash command templates instead of prefix commands
- Branch autocomplete in admin commands wrapped in try/except (returns empty on error)
- `STATUS_EMOJI` extracted to module-level dict in application views (shared across views)
- Ticket rate limit dict properly typed as `dict[int, float]` with expired entry cleanup
- `.gitignore`: added `branches/shopkeepers/csv_data/`, `temp/`

### Removed

- `get_env_int_list()` from config.py
- `validate_channel_id()`, `validate_role_ids()`, `validate_config_dict()` from config.py
- `get_db_connection()` from database.py
- `truncate_for_embed_description()`, `truncate_for_message()` from constants.py
- `validate_minecraft_username()`, `validate_age()`, `truncate_text()`, `format_duration()`,
  `is_valid_url()`, `validate_yes_no()`, `validate_rating()`, `validate_time_commitment()`
  from utils.py
- Intent validation block from bot.py
- `BranchLoader.loaded_branches` unused dict
- Unused `yaml` import from status_channels branch
- Comment block removed from `.env` about admin permission system

### Fixed

- Slash commands re-synced on every Discord reconnect, causing unnecessary API calls and
  potential rate limiting
- Application stats null-safety: checks row is not None before accessing index
- Ticket rate limit dict could grow unbounded: `_cleanup_rate_limits()` removes expired
  entries before each check
- f-strings without placeholders corrected to plain strings

## [1.1.0] - 2025-11-15

### Added

- **Ticket reminder system**: `/remindme` command with custom intervals (`30m`, `1h`, `2h`,
  `1d`), optional DM notifications, `ReminderControlView` with Stop/Snooze 1h/Snooze 6h/
  Snooze 1d buttons, 1-minute check loop, automatic cancellation on ticket close, orphaned
  reminder deactivation
- **Staff commands**: `/closeticket` (close with optional reason, sends closure embed, logs
  to log channel), `/addticket` (manually register existing threads with category
  autocomplete and status detection)
- **Category-based permission system**: `can_manage_ticket_category()` checking Discord
  administrator, global `staff_role_ids`, or per-category `staff_roles`;
  `can_bypass_duplicate_check()` for configurable bypass roles
- **Initial questions modal**: `TicketQuestionsModal` generating up to 5 `TextInput` fields
  from config with per-field `label`, `placeholder`, `required`, `max_length`, `min_length`;
  `{answers}` placeholder in welcome messages for formatted Q&A injection
- **Ticket creation rate limiting**: configurable cooldown via
  `rate_limit.ticket_creation_cooldown_seconds` (default 60), checked before and after modal
  submission
- **Configurable button styles**: per-category `button_style` supporting `primary`,
  `secondary`, `success`, `danger` and aliases (`blurple`, `grey`, `green`, `red`)
- **Ticket reopening detection**: `on_raw_thread_update` listener detects manual unarchive
  of closed tickets and updates status to "open" in database with logging
- `ticket_reminders` database table with foreign key cascade, indexes on active status,
  thread, and user, and unique partial index preventing duplicate active reminders
- `parse_time_string()` helper supporting minutes/hours/days with 30-day maximum
- `categories_field_name` panel setting (set to `""` to hide categories in panel embed)
- `bypass_duplicate_check_role_ids` config setting
- Rate limit protection (1-second sleep) between processing due reminders

### Changed

- Close and reopen permissions now use `can_manage_ticket_category()` instead of global
  `is_staff()` — category-specific access control
- Thread closure order: archive and lock BEFORE database update for consistent state on
  failure
- Duplicate ticket check respects `can_bypass_duplicate_check()` bypass roles
- Auto-archive duration adapts to guild boost level (7 days for tier 2+, 1 day for tier 0-1)
- `ping_roles` renamed to `staff_roles` in category config (backward compatible fallback)
- Category labels no longer include emoji prefix (emoji in separate `emoji` field)
- Welcome messages updated to use `{answers}` placeholder
- Config reloaded on every ticket panel button click
- Interaction response handling checks `is_done()` before deferring

### Fixed

- Staff members could manage tickets outside their assigned categories
- Closed tickets manually unarchived did not properly reopen in database
- Ticket creators can close their own tickets regardless of staff role membership
- Race condition in reminder creation handled via `IntegrityError` catch (unique partial
  index enforcement)
- Auto-archive duration failure on unboosted guilds (previously hardcoded 7-day request)

### Security

- Category isolation: sensitive categories (billing, appeals) restricted to specific staff
  roles via per-category `staff_roles` config
- All management commands (`/closeticket`, `/reopenticket`, `/addticket`, close button)
  validate category access before executing

## [1.0.0] - 2025-11-08

Initial release of the Oak Discord Bot framework.

### Added

#### Core Framework

- `Oak` bot class (`bot.py`) with Discord.py `commands.Bot`, `!` prefix, intents for
  message_content, messages, guilds, and members
- Branch auto-discovery and loading via `BranchLoader` (`core/branch_loader.py`) with
  filesystem scanning, config loading, and hot-reload support
- Configuration system (`config.py`) loading `.env` for `DISCORD_TOKEN` and `GUILD_ID`,
  with validation
- Database utilities (`database.py`): `init_branch_database()` for schema creation with WAL
  mode and foreign keys
- Utility functions (`utils.py`): `sanitize_text()`, `check_application_answer_quality()`
- Discord API constants (`constants.py`): embed limits, message limits, modal limits, select
  menu limits, button limits, channel/thread limits, HTTP status codes, Oak framework
  constants, and `truncate_for_embed_field()` helper
- Logging with file handler (`logs/oak_YYYYMMDD.log`) and console handler
- Guild-scoped slash command syncing on startup
- Global command error handler for prefix commands
- Branch creation scaffold (`create_branch.py`) generating branch template with
  `branch.py`, `__init__.py`, `config.yml`, and `branch.yml`
- `requirements.txt`: `discord.py>=2.3.0`, `python-dotenv>=1.0.0`, `aiosqlite>=0.19.0`,
  `PyYAML>=6.0`, `mysql-connector-python>=8.0.0`, `mcstatus>=11.0.0`
- MIT License

#### Admin Branch (`branches/admin/`)

- `/reload <branch>` — hot-reload a branch and its config
- `/load <branch>` — load a branch
- `/unload <branch>` — unload a branch (with self-protection)
- `/branches` — list all loaded branches
- `/reloadall` — reload all branches (with deferred response)
- `/botinfo` — server count, user count, branch count, Python version, discord.py version,
  latency
- Branch name autocomplete on all commands
- All responses ephemeral, all commands require Administrator permission

#### Application Branch (`branches/application/`)

- Multi-page modal application system (5 questions per page) with configurable questions
  (up to 17), answer quality validation (minimum length, repeated character detection,
  single-word detection), and progress saving between pages
- Private application channels with permission overwrites
- Staff review tools: `ManageView` with Accept (moves to accepted category), Decline
  (reason modal with configurable delay and DM), Background Check (MySQL/Plan playtime
  integration), View History (previous applications with paginated dropdown)
- `StatusChangeView` for manual status changes (Pending/Accepted/Denied/Cancelled/Abandoned)
- `ApplicationHistoryView` with dropdown select for viewing previous applications
- Automated inactivity management: configurable warning after N days, auto-abandon after N
  days, denied app cleanup after N hours if DM failed
- Duplicate application prevention with race condition handling (`IntegrityError` catch)
- Optional required link role check (Minecraft account linking)
- Database: `applications` table with `id`, `user_id`, `channel_id`, `app_index`, `answers`,
  `status`, timestamps, and denial tracking columns; `idx_applications_status` and
  `idx_applications_last_activity` indexes
- `/appstats` — application statistics (total, per-status counts, average review time)
- `/apphistory <user>` — user's application history with status emojis and detail dropdown
- Configurable embed colors, messages, position name, channel IDs, and MySQL settings

#### Suggestions Branch (`branches/suggestions/`)

- Community suggestion system: intercepts messages in configured channel, replaces with
  formatted embed, adds Like/Dislike/Manage voting buttons, creates discussion thread
- Vote toggling (re-click to remove), opposite vote removal, live statistics update
- Staff management: `ManageSuggestionView` with Approve (green embed), Deny (red embed),
  Delete (removes message and thread) buttons
- `StatusModal` for approval/denial reason with moderator mention, timestamp, and author DM
  notification
- Database: `suggestions` table with `id`, `message_id`, `thread_id`, `user_id`, `content`,
  `likes` (JSON), `dislikes` (JSON), `status`, `reason`, `created_at`
- Persistent `SuggestionVoteView` (formerly `DummyView`) surviving bot restarts
- Configurable embed colors, validation (min/max length), and messages

#### Tickets Branch (`branches/tickets/`)

- Thread-based ticket system with configurable categories (default 5: ingame, billing,
  reports, appeals, bugs)
- `TicketPanelView` with dynamically generated buttons per category (configurable label,
  emoji, style)
- `TicketControlView` with Close and Reopen buttons
- `CloseReasonModal` for close reason entry
- Per-category sequential ticket numbering with `{number}` and `{nickname}` thread name
  formatting
- Anti-archive background task (30-minute loop) preventing open ticket archival
- Thread update listener handling archive/unarchive events
- Config hash tracking for automatic panel refresh on config change
- Config validation (`validate_config()`) for required fields, channel IDs, and category
  structure
- Standardized log embeds (`format_log_embed()`) for open/close/reopen events
- `/ticketstats` — total tickets, open/closed counts, per-category breakdown, average
  resolution time
- `/tickets` — user's open tickets (max 10)
- Database: `tickets` and `ticket_counters` tables
- Configurable: `panel_channel_id`, `log_channel_id`, per-category `channel_id`,
  `staff_roles`, `welcome_message`, `thread_name`, `allow_adding_users`

#### Status Channels Branch (`branches/status_channels/`)

- Auto-updating voice channels displaying real-time server statistics
- Member count channel with configurable format string (e.g., `"Total Members: {count:,}"`)
- Minecraft player count channel via `mcstatus.JavaServer` with configurable format
  (e.g., `"Online: {online}/{max}"`)
- 6-minute update loop with 10% jitter for rate limit protection
- Rate limit retry handling (`retry_after`)

#### Link Branch (`branches/link/`)

- `/link` command displaying configurable embed with account linking instructions
- Embed title, description, and color configurable via `config.yml`

#### Documentation

- README with architecture overview, setup instructions, project structure, all branch
  feature descriptions, configuration system, permission system, database architecture,
  development workflow, and troubleshooting
- CONTRIBUTING.md with coding standards, branch development guide, PR format, and testing
  checklist
