# Oak

A modular Discord bot framework with a branch-based plugin architecture. Each feature lives in its own self-contained branch with independent config, database, and lifecycle — hot-reloadable without restarts.

Built for a Minecraft community server. Includes suggestion voting, staff applications, support tickets, server status tracking, market analysis, and account linking out of the box.

## Setup

### Requirements

- Python 3.10+
- Discord bot token
- MySQL database (optional, for Plan integration in the application branch)

### Installation

1. Clone the repository
   ```bash
   git clone https://github.com/LoralonMC/Oak.git
   cd Oak
   ```

2. Install dependencies
   ```bash
   pip install -r requirements.txt
   ```

3. Create `.env` from the example
   ```bash
   cp .env.example .env
   ```
   Edit `.env` and fill in your bot token and guild ID.

4. Configure branches — edit `config.yml` in each branch folder to set channel IDs, role IDs, etc.

5. Run the bot
   ```bash
   python bot.py
   ```

## Architecture

Oak uses a **branch-based plugin system** inspired by Minecraft Paper plugins:

- Every branch extends `OakBranch` and lives in its own `branches/<name>/` folder
- A `branch.yml` manifest declares metadata (id, version, main class, database flag)
- A `config.yml` holds runtime settings (channel IDs, role IDs, feature flags)
- Branches are auto-discovered on startup and can be hot-reloaded via `/reload`
- Each branch gets its own SQLite database (WAL mode, write-locked), event bus handle, and logger

### Branch lifecycle

| Hook | When |
|------|------|
| `on_enable()` | Branch loaded — initialize DB schema, register views, start tasks |
| `on_ready()` | Bot fully connected to Discord (fires once) |
| `on_disable()` | Branch unloaded — cancel tasks, release resources |

### Project structure

```
Oak/
├── bot.py                        # Entry point (logging, OakBot, run)
├── .env.example                  # Environment variable template
├── requirements.txt
├── create_branch.py              # Scaffold a new branch
├── oak/                          # Framework core
│   ├── __init__.py               # Public API (OakBot, OakBranch, etc.)
│   ├── bot.py                    # OakBot — setup, shutdown, error handling
│   ├── branch.py                 # OakBranch — base class for all branches
│   ├── loader.py                 # BranchLoader — discover, load, reload, unload
│   ├── manifest.py               # branch.yml parser
│   ├── config.py                 # OakConfig (.env) + branch config loader (deep merge)
│   ├── context.py                # BranchContext — dependency injection container
│   ├── database.py               # BranchDatabase — per-branch SQLite (WAL + write lock)
│   ├── events.py                 # EventBus + BranchEventHandle
│   ├── interactions.py           # InteractionRouter + BranchInteractionHandle
│   ├── views.py                  # Reusable views (PaginatedEmbedView)
│   ├── tasks.py                  # TaskRegistry for scheduled task loops
│   ├── backup.py                 # Database backup utilities
│   ├── metrics.py                # In-memory counters (commands, events, DB, errors)
│   ├── constants.py              # Discord API limits
│   ├── errors.py                 # Framework exceptions
│   ├── utils.py                  # Shared utilities (sanitization, truncation)
│   └── admin/                    # Built-in admin branch (always loaded)
│       ├── branch.py             # /reload, /load, /unload, /enable, /disable, /branches,
│       │                         #   /sync, /botinfo, /health, /metrics, /backup
│       └── branch.yml
└── branches/                     # User branches (auto-discovered)
    ├── suggestions/
    │   ├── branch.yml            # Manifest
    │   ├── branch.py             # OakBranch subclass
    │   ├── config.yml            # Runtime settings
    │   ├── handlers.py
    │   ├── helpers.py
    │   ├── modals.py
    │   └── views.py
    ├── application/
    ├── tickets/
    ├── shopkeepers/
    ├── status_channels/
    └── link/
```

Branch databases (`data.db`) are auto-created on first load and git-ignored.

### Branch manifest (`branch.yml`)

```yaml
id: suggestions
name: Suggestions
version: "1.0.0"
description: User suggestions with voting and management
main: branch.Suggestions
database: true
```

The `main` field is `module.ClassName` — the loader imports `branches.<folder>.<module>` and instantiates `<ClassName>`.

### Configuration

**Global** (`.env`): bot token, guild ID, audit log channel, backup settings.

**Per-branch** (`config.yml`): each branch defines a `DEFAULT_CONFIG` dict in its module. The framework deep-merges `config.yml` on top, so you only need to override what you change.

```yaml
# branches/suggestions/config.yml
enabled: true
settings:
  channel_id: 1374374186016964788
  manager_role_ids:
    - 937003755185012756
```

Hot-reload any branch and its config:
```
/reload suggestions
```

## Admin Commands

Built-in commands (require Discord Administrator permission):

| Command | Description |
|---------|-------------|
| `/reload <branch>` | Hot-reload a branch and its config |
| `/load <branch>` | Load a branch |
| `/unload <branch>` | Unload a branch |
| `/enable <branch>` | Enable a disabled branch and load it |
| `/disable <branch>` | Disable and unload a branch |
| `/branches` | List loaded and discovered branches |
| `/sync` | Force-sync slash commands to the guild |
| `/botinfo` | Show bot stats |
| `/health` | Check bot and branch health (DB, scheduled tasks) |
| `/metrics` | Show command usage, event counts, DB activity, errors |
| `/backup [branch]` | Back up branch database(s) to timestamped files |

All responses are ephemeral. Branch names autocomplete.

## Branches

### Suggestions

Community suggestion system with voting and staff moderation.

- Users post in the suggestions channel → bot creates an embed with vote buttons and a discussion thread
- Like/dislike voting with duplicate prevention and vote toggling
- Staff with manager roles can approve, deny, or delete suggestions
- All actions update the embed and notify the original author
- Persistent buttons survive bot restarts

### Application

Multi-page staff application workflow.

- "Apply" button creates a private channel visible only to the applicant and reviewers
- Modal forms collect answers across multiple pages (5 questions per page), with progress saved between pages
- Answer quality validation prevents low-effort submissions
- Staff tools: read full application (paginated), accept, deny with reason, background check (optional MySQL/Plan integration), view application history
- Inactivity tracking: warns after 3 days, auto-abandons after 7 days
- `/appstats` and `/apphistory <user>` slash commands

### Tickets

Thread-based support ticket system with categories.

- Ticket panel with per-category buttons (gameplay, billing, reports, appeals, bugs — fully configurable)
- Creates private threads with staff pings and welcome messages
- Close with optional reason, reopen via command or thread unarchive (checks authorization via audit log)
- Anti-archive background task keeps open tickets unarchived
- Reminder system: `/remindme` sets initial + daily reminders with snooze/stop buttons that persist across restarts
- Sequential ticket numbering per category
- `/tickets`, `/ticketstats`, `/closeticket`, `/reopenticket`, `/addticket`

### Shopkeepers

CSV trade log importer and market analysis for the Minecraft Shopkeepers plugin.

- Auto-imports CSV trade logs on a configurable interval (blocking I/O runs in a thread pool)
- Incremental processing with SHA-256 deduplication
- NBT parser identifies enchanted books, potions, tipped arrows, shulker boxes, and plugin items (ExecutableItems, ItemsAdder, etc.)
- Dual format support: legacy Bukkit NBT and modern 1.20.5+ components
- Admin shop prices excluded from market averages

**Public commands:** `/price`, `/top`, `/search`, `/player`, `/shop`, `/trending`, `/shops`, `/players`
**Admin commands:** `/shopkeepers_import`, `/shopkeepers_stats`, `/economy`, `/sinks`, `/faucets`

### Status Channels

Auto-updating voice channels with server statistics.

- Updates every 11 minutes with jitter to avoid API rate limits
- Minecraft player count via `mcstatus` (async DNS lookup + status query)
- Discord member count
- Configurable format strings with injection protection

### Link

Simple `!link` prefix command that displays instructions for linking Minecraft accounts to Discord. Includes a per-user cooldown (10 seconds).

## Creating a New Branch

```bash
python create_branch.py my_feature "Description of my feature"
```

Creates:
```
branches/my_feature/
├── __init__.py
├── branch.yml        # Manifest
├── branch.py         # OakBranch subclass with example commands
└── config.yml        # Runtime config
```

The generated branch includes a database schema, `DEFAULT_CONFIG`, lifecycle hooks, and example commands. Load it with `/load my_feature` or restart the bot.

Set `database: false` in `branch.yml` if you don't need a database.

For the full developer guide — config, database, views, modals, events, background tasks, and more — see **[GUIDE.md](GUIDE.md)**.

## Logging

Logs go to both console and `logs/oak.log` with daily rotation (30-day retention). Discord.py's internal logging is suppressed to WARNING level.

## Troubleshooting

**Bot won't start** — check `DISCORD_TOKEN` and `GUILD_ID` in `.env`.

**Branch won't load** — check syntax with `python -m py_compile branches/<name>/branch.py`, verify `branch.yml` and `config.yml` are valid YAML, and check logs.

**Commands not appearing** — run `/sync` to force-sync slash commands. Verify the branch is loaded with `/branches`.

**Database errors** — check that the branch folder is writable. Delete `branches/<name>/data.db` to rebuild (loses data).

## License

MIT License — see LICENSE file for details.

---

**Oak** — by [LoralonMC](https://github.com/LoralonMC)
