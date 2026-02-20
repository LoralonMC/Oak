# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
- **Tickets**: Transcript web server (`branches/tickets/web.py`) — serves transcripts as hosted links instead of file attachments
  - Configurable via `transcript.web` settings (port, base_url); fully backwards compatible
  - New dependency: `aiohttp`

### Fixed
- **Framework**: Audit log `details` field truncated to embed field limit (1024 chars)
- **Framework**: Dependency cycle branches now recorded in load failures (visible in `/health`)
- **Tickets**: Transcript "Closed:" header shows real timestamp instead of literal "now"
- **Tickets**: Transcript embed renderer now displays embed fields (e.g. close reason)
- **Application**: Status update is now atomic — prevents stale overwrites by concurrent staff
- **Application**: Corrupted JSON in application answers no longer crashes the bot
- **Shopkeepers**: Player name display no longer crashes when UUID is null
- **Suggestions**: INSERT no longer writes to legacy `likes`/`dislikes` JSON columns
- **Suggestions**: Rejection messages sent as temporary channel replies instead of DMs

## [0.3.0] - 2026-02-20

Major framework rewrite: all branches now run on the Oak framework with centralized database access, config management, and lifecycle hooks.

### Added
- **Oak framework** (`oak/` package) replacing the old `core/` module
  - `OakBranch` base class with `on_enable`/`on_ready`/`on_disable` lifecycle
  - `BranchDatabase` with persistent connection, WAL mode, write lock, transaction context manager, and migration tracking
  - `EventBus` for inter-branch communication
  - `InteractionRouter` for `oak:{branch}:{action}` custom ID routing
  - `BranchLoader` with dependency resolution and hot-reload support
  - Branch manifests (`branch.yml`) for metadata and dependency declaration
  - `TaskRegistry` for tracking `discord.ext.tasks` loops across branches
  - `BackupManager` for scheduled database backups with WAL checkpoint
  - `PaginatedEmbedView` reusable paginated embed component
  - In-memory metrics tracking (commands, events, DB ops, errors)
  - Inter-branch service registry (`self.services` / `self.require_branch()`)
  - Audit logging to a Discord channel for admin actions
- **Admin commands**: `/reload`, `/load`, `/status`, `/health`, `/metrics`, `/backup`, `/enable`, `/disable`
- **Branch developer guide** (`GUIDE.md`) covering config, database, lifecycle, commands, views, and migration

### Changed
- All branches migrated from raw `aiosqlite.connect()` to `BranchDatabase` methods
- All disk-reading config helpers replaced with `self.config` / `self.setting()`
- Views, modals, and handlers use module-level `configure(db, config)` pattern
- Slash command sync moved from `on_ready` to `setup_hook` (runs once instead of on every reconnect)
- Config loading uses deep merge so missing keys fall back to defaults
- `.env` removed from tracking, replaced with `.env.example`

### Fixed
- **Suggestions**: Vote race condition fixed with atomic `suggestion_votes` table; null-safe JSON parsing; reason text truncated to embed field limit
- **Applications**: Decline race condition fixed with status check before UPDATE; Read button permission check; modal title truncation
- **Tickets**: Close race condition fixed with `AND status = 'open'` guard; ticket number race fixed with transaction; owner check on close confirmation; reminder messages now replaced instead of accumulating; anti-archive memory leak (unbounded `_thread_update_times`)
- **Shopkeepers**: `_calculate_emerald_cost` None crash; TAG_Int unsigned handling; SQL injection via f-strings replaced with parameterized queries; import lock TOCTOU race
- **Status Channels**: Players None check; task skipped when unconfigured
- **Framework**: Duplicate branch ID detection; malformed custom ID rejection; stale manifest on reload; circular dependency handling; event listener timeout; persistent view cleanup on branch disable; config parse failure logging; exception details no longer leaked to Discord users
- Error handlers added to all modals across all branches

## [0.2.0] - 2026-02-06

### Added
- **Shopkeepers branch** (new) — Minecraft trade data analysis system
  - CSV-based trade log importer with SHA-256 deduplication and incremental import
  - NBT metadata parser supporting legacy Bukkit and modern 1.20.5+ component formats
  - 13 slash commands: `/price`, `/top`, `/search`, `/player`, `/shop`, `/trending`, `/shops`, `/players`, `/shopkeepers_import`, `/shopkeepers_stats`, `/economy`, `/sinks`, `/faucets`
  - Configurable multi-tier currency, plugin identity key support, auto-import task
- **Tickets**: Reminder messages now tracked and replaced to avoid clutter in threads

### Fixed
- Slash command sync no longer re-runs on every Discord reconnect
- Global slash command error handler added
- Config deep merge for missing keys
- SQLite WAL journal mode enabled
- Bug fixes across all branches (admin command display, snowflake validation, application status emojis, and more)
- Removed 14 unused functions and ~300 lines of dead code

## [0.1.1] - 2025-11-15

### Added
- **Tickets**: Reminder system with `/remindme` and `/stopreminder` commands
  - Custom intervals, DM notifications, snooze (1h/6h/1d), auto-cancel on ticket close
- **Tickets**: Staff commands `/closeticket`, `/addticket`, `/ticketstats`
- **Tickets**: Category-based permission system for granular staff access control
  - `staff_roles` per category (renamed from `ping_roles`, backwards compatible)
  - Global staff override via `staff_role_ids`
- **Tickets**: Auto-reopen detection when closed tickets are manually unarchived

### Changed
- Ticket close/manage operations now respect category-based permissions
- Thread archived and locked before database update for consistency
- Transaction safety with IMMEDIATE locking for ticket number generation

### Fixed
- Staff could previously access tickets outside their assigned categories
- Closed tickets that were manually unarchived did not reopen properly

## [0.1.0] - 2025-11-08

### Added
- **Oak framework** — modular Discord bot with hot-reload branch system
- **Application branch** — staff application system with configurable questions, review workflow, background checks, and inactivity tracking
- **Suggestions branch** — community suggestion voting with status management (approve/deny/consider) and discussion threads
- **Tickets branch** — support ticket system with configurable categories, panels, transcript logging, and auto-archive
- **Status Channels branch** — voice channels displaying live server member/bot/role counts
- **Admin branch** — bot management commands (`/reload`, `/load`, `/branches`, `/botinfo`)
- **Link branch** — Minecraft account linking
- Constants module with Discord API limits
- Branch scaffolding tool (`create_branch.py`)

## [0.0.1] - 2025-11-06

Initial commit with core bot structure, branch loader, and five branches (Application, Suggestions, Status Channels, Link).
