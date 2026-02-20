# Oak Branch Developer Guide

A hands-on guide to building branches for the Oak framework. Covers everything from scaffolding to production patterns.

For setup and admin commands, see [README.md](README.md).

---

## 1. Quick Start

Scaffold a new branch:

```bash
python create_branch.py polls "Community voting system"
```

This creates:

```
branches/polls/
├── __init__.py       # Empty — framework uses branch.yml for discovery
├── branch.yml        # Manifest — declares id, version, main class
├── branch.py         # OakBranch subclass — your code goes here
└── config.yml        # Runtime settings — operators edit this, not your code
```

Load it without restarting:

```
/load polls
```

After code changes:

```
/reload polls
```

## 2. Branch Manifest (`branch.yml`)

The manifest is how the framework discovers and loads your branch.

### Minimal example

```yaml
id: link
name: Link
version: "1.0.0"
description: Account linking instructions
main: branch.Link
```

### Full example

```yaml
id: suggestions
name: Suggestions
version: "1.0.0"
description: User suggestions with voting and management
main: branch.Suggestions
database: true
dependencies:
  - link
priority: 50
```

### Field reference

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `id` | Yes | — | Unique identifier. Lowercase, alphanumeric + `_` or `-`. |
| `version` | Yes | — | Semver string (e.g. `"1.0.0"`). |
| `main` | Yes | — | `module.ClassName` — the loader imports `branches.<id>.<module>` and instantiates `<ClassName>`. |
| `name` | No | Same as `id` | Display name. |
| `description` | No | `""` | Short description. |
| `database` | No | `false` | Set `true` to get a per-branch SQLite database (`self.db`). |
| `dependencies` | No | `[]` | Branch IDs that must load before this one. |
| `priority` | No | `100` | Load order tiebreaker (lower = earlier). |

## 3. Configuration (`config.yml` + `DEFAULT_CONFIG`)

Every branch has two config layers:

1. **`DEFAULT_CONFIG`** — a Python dict in your branch module. Defines all keys with sensible defaults.
2. **`config.yml`** — a YAML file operators edit. Only needs to override what changes.

The framework deep-merges `config.yml` on top of `DEFAULT_CONFIG`, so missing keys fall back to defaults.

### Defining defaults

```python
DEFAULT_CONFIG = {
    "enabled": True,
    "settings": {
        "channel_id": 0,
        "manager_role_ids": [],
        "validation": {
            "min_length": 10,
            "max_length": 4000,
        },
        "messages": {
            "welcome": "Welcome!",
            "error": "An error occurred.",
        },
    },
}
```

### Corresponding `config.yml`

Operators only override what they need:

```yaml
enabled: true

settings:
  channel_id: 1374374186016964788
  manager_role_ids:
    - 937003755185012756
```

Everything else (`validation`, `messages`, etc.) keeps the defaults from `DEFAULT_CONFIG`.

### Accessing settings

Use `self.setting()` to walk into nested config without `.get().get().get()` chains:

```python
# self.setting() looks inside self.config["settings"] automatically
color = self.setting("ui", "embed_colors", "pending", default=0x2B2D31)
min_len = self.setting("validation", "min_length", default=10)
```

Always provide a `default=` so the branch works even if the key is missing.

### Accessing config from views, modals, and handlers

Views, modals, and handler functions don't have access to the branch instance. Use the **module-level configure pattern** to share the database and config references:

```python
# views.py
_db = None
_config: dict = {}

def configure(db, config: dict) -> None:
    """Called from on_enable() to set module-level refs."""
    global _db, _config
    _db = db
    _config = config
```

Call it in your branch's `on_enable`:

```python
from .views import configure as configure_views

async def on_enable(self) -> None:
    if self.db:
        await self.db.initialize(SCHEMA)
    configure_views(self.db, self.config)
```

Then in views, modals, or handlers, import at function scope to get the current values:

```python
# handlers.py
async def handle_vote(interaction):
    from .views import _db, _config
    row = await _db.fetchone("SELECT ...", (interaction.message.id,))
    manager_roles = _config.get("settings", {}).get("manager_role_ids", [])
```

Function-level imports work because Python re-reads the module attribute each call, so they always see the latest value set by `configure()`.

## 4. Branch Lifecycle

```
__init__(ctx)  →  on_enable()  →  on_ready()  →  on_disable()
   Config          DB, views,      Bot is         Cleanup
   reads           tasks           connected
```

### What goes where

| Hook | Use for |
|------|---------|
| `__init__(ctx)` | Read config values into instance attributes. Call `super().__init__(ctx)`. |
| `on_enable()` | Initialize database schema, register persistent views, start background tasks. |
| `on_ready()` | Anything that needs the bot to be connected to Discord (fetch channels, etc.). |
| `on_disable()` | Cancel background tasks, release resources. |

### Available properties

All set by `super().__init__(ctx)` — use them anywhere in your branch:

| Property | Type | Description |
|----------|------|-------------|
| `self.bot` | `OakBot` | The bot instance. |
| `self.db` | `BranchDatabase \| None` | Per-branch SQLite database. `None` if `database: false`. |
| `self.log` | `logging.Logger` | Namespaced logger (`oak.branch.<id>`). |
| `self.config` | `dict` | Merged config (deep merge of `DEFAULT_CONFIG` + `config.yml`). |
| `self.data_dir` | `Path` | Path to the branch's directory. |
| `self.events` | `BranchEventHandle` | Scoped event bus handle. |
| `self.interactions` | `BranchInteractionHandle` | Scoped custom\_id builder/parser. |
| `self.services` | `dict[str, Any]` | Public API dict — populate in `on_enable()` for inter-branch access. |

### Example

```python
from oak import OakBranch
from oak.context import BranchContext

class Polls(OakBranch):
    def __init__(self, ctx: BranchContext):
        super().__init__(ctx)
        self.channel_id = self.setting("channel_id", default=0)

    async def on_enable(self) -> None:
        if self.db:
            await self.db.initialize(SCHEMA)
        self.bot.add_view(PollView())

    async def on_ready(self) -> None:
        self.log.info("Polls branch ready")

    async def on_disable(self) -> None:
        self.log.info("Polls branch unloaded")
```

## 5. Database

Set `database: true` in `branch.yml` to get a per-branch SQLite database at `branches/<id>/data.db`.

### Schema definition

Define your schema as a module-level string. Always use `IF NOT EXISTS` so it's safe to re-run:

```python
SCHEMA = """
CREATE TABLE IF NOT EXISTS polls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER UNIQUE,
    user_id INTEGER,
    question TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS poll_votes (
    poll_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    choice TEXT NOT NULL,
    PRIMARY KEY (poll_id, user_id)
);
"""
```

### Initializing

Call `initialize()` in `on_enable`:

```python
async def on_enable(self) -> None:
    if self.db:
        await self.db.initialize(SCHEMA)
```

This opens the connection, sets WAL mode + foreign keys, creates the migration tracking table, and runs your schema.

### Read/write operations

```python
# Write (auto-locked, auto-committed)
await self.db.execute(
    "INSERT INTO polls (message_id, user_id, question) VALUES (?, ?, ?)",
    (message.id, user.id, question),
)

# Read one row
row = await self.db.fetchone(
    "SELECT question FROM polls WHERE message_id = ?",
    (message_id,),
)
if row:
    question = row[0]

# Read all rows
rows = await self.db.fetchall("SELECT * FROM polls WHERE user_id = ?", (user_id,))
```

- **Writes** (`execute`) acquire the write lock and auto-commit.
- **Reads** (`fetchone`, `fetchall`) don't lock — safe under WAL mode.

### Migrations

For schema changes after the initial release, use named migrations:

```python
from oak import Migration

MIGRATIONS = [
    Migration(
        name="add_polls_closed_at",
        sql="ALTER TABLE polls ADD COLUMN closed_at TIMESTAMP;"
    ),
    Migration(
        name="add_poll_votes_index",
        sql="CREATE INDEX IF NOT EXISTS idx_poll_votes_poll ON poll_votes(poll_id);"
    ),
]
```

Run them after `initialize()`:

```python
async def on_enable(self) -> None:
    if self.db:
        await self.db.initialize(SCHEMA)
        await self.db.migrate(MIGRATIONS)
```

Each migration runs once — the framework tracks applied migrations in an `_oak_migrations` table. Migration names must be unique.

### Transactions

For multiple writes that must succeed or fail together, use `transaction()`:

```python
async with self.db.transaction() as conn:
    await conn.execute("UPDATE polls SET closed_at = ? WHERE id = ?", (now, poll_id))
    await conn.execute("DELETE FROM poll_votes WHERE poll_id = ?", (poll_id,))
```

The context manager acquires the write lock, issues `BEGIN IMMEDIATE`, and commits on success or rolls back on error. Keep Discord API calls **outside** the transaction to avoid holding the lock during slow network operations.

For read-only access to the raw `aiosqlite.Connection` (e.g. for type annotations or helpers that receive it), use `self.db.raw_connection()`.

## 6. Slash Commands

Branches are discord.py Cogs, so all `app_commands` features work directly.

### Basic command

```python
from discord import app_commands
import discord

@app_commands.command(name="poll", description="Create a poll")
async def create_poll(self, interaction: discord.Interaction) -> None:
    await interaction.response.send_message("Poll created!")
```

### Parameters with descriptions

```python
@app_commands.command(name="closeticket", description="Close a ticket")
@app_commands.describe(reason="Reason for closing the ticket (optional)")
async def close_ticket(self, interaction: discord.Interaction, reason: str = None):
    await interaction.response.defer(ephemeral=True)
    # ... close logic ...
    await interaction.followup.send("Ticket closed.", ephemeral=True)
```

### Autocomplete

Define an autocomplete method and attach it with `@app_commands.autocomplete()`:

```python
async def category_autocomplete(
    self,
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Autocomplete categories from config."""
    categories = self.setting("categories", default={})
    choices = [
        app_commands.Choice(name=label, value=key)
        for key, label in categories.items()
    ]
    if current:
        choices = [c for c in choices if current.lower() in c.name.lower()]
    return choices[:25]  # Discord limits to 25 choices

@app_commands.command(name="addticket", description="Add a ticket")
@app_commands.describe(category="Category for this ticket")
@app_commands.autocomplete(category=category_autocomplete)
async def add_ticket(self, interaction: discord.Interaction, category: str):
    ...
```

After adding or changing commands, run `/sync` to push them to Discord.

## 7. Views & Buttons (Persistent UI)

Persistent views survive bot restarts. Two requirements: `timeout=None` and every button needs a `custom_id`.

### Creating a persistent view

```python
import discord
from discord import ui

class PollVoteView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)  # Persistent — no timeout

    @discord.ui.button(label="Vote A", style=discord.ButtonStyle.green, custom_id="oak:polls:vote_a")
    async def vote_a(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Voted for A!", ephemeral=True)

    @discord.ui.button(label="Vote B", style=discord.ButtonStyle.red, custom_id="oak:polls:vote_b")
    async def vote_b(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Voted for B!", ephemeral=True)
```

### Registering in `on_enable`

```python
async def on_enable(self) -> None:
    self.bot.add_view(PollVoteView())
```

The bot re-attaches the view to any existing messages with matching `custom_id`s.

### Namespaced custom\_ids

Use `self.interactions.custom_id()` to build properly namespaced IDs:

```python
cid = self.interactions.custom_id("vote", "option_a")
# → "oak:polls:vote:option_a"

cid = self.interactions.custom_id("manage")
# → "oak:polls:manage"
```

Format: `oak:{branch_id}:{action}` or `oak:{branch_id}:{action}:{value}`

Rules:
- No colons in `action` or `value`
- `value` must match `[A-Za-z0-9._-]`
- Total length max 100 characters

Parse incoming custom\_ids:

```python
parsed = self.interactions.parse(custom_id)
if parsed:
    print(parsed.branch)   # "polls"
    print(parsed.action)   # "vote"
    print(parsed.value)    # "option_a"
```

Returns `None` if the custom\_id doesn't belong to this branch.

## 8. Modals

Modals are popup forms with text inputs.

### Basic modal

```python
import discord
from discord import ui

class ReasonModal(ui.Modal, title="Provide a Reason"):
    def __init__(self, target_id: int):
        super().__init__()
        self.target_id = target_id
        self.reason = ui.TextInput(
            label="Reason",
            style=discord.TextStyle.paragraph,
            required=True,
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        # ... process self.reason.value ...
        await interaction.followup.send("Done!", ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logger.error(f"Error in ReasonModal: {error}", exc_info=True)
        try:
            if interaction.response.is_done():
                await interaction.followup.send("An error occurred.", ephemeral=True)
            else:
                await interaction.response.send_message("An error occurred.", ephemeral=True)
        except Exception:
            pass
```

### Showing a modal from a button

```python
@discord.ui.button(label="Deny", style=discord.ButtonStyle.red)
async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
    await interaction.response.send_modal(ReasonModal(self.message_id))
```

### `on_error` pattern

Always implement `on_error` on modals. Check `interaction.response.is_done()` to decide whether to use `followup.send` or `response.send_message` — this handles the case where the error occurs after `defer()`.

## 9. Background Tasks

Use `discord.ext.tasks` for periodic work.

### Basic task loop

```python
from discord.ext import tasks

class StatusChannels(OakBranch):
    def __init__(self, ctx: BranchContext):
        super().__init__(ctx)

    async def on_enable(self) -> None:
        self.update_channels.start()

    async def on_disable(self) -> None:
        self.update_channels.cancel()

    @tasks.loop(minutes=11)
    async def update_channels(self):
        guild = self.bot.get_guild(self.bot.guild_id)
        if not guild:
            return
        # ... update channel names ...

    @update_channels.before_loop
    async def before_update(self) -> None:
        await self.bot.wait_until_ready()

    @update_channels.error
    async def update_error(self, error: Exception) -> None:
        self.log.error(f"Unhandled error in update task: {error}", exc_info=True)
```

### Registering tasks for /health

Call `self.register_task()` in `on_enable` so your task loop appears in the `/health` dashboard:

```python
async def on_enable(self) -> None:
    self.update_channels.start()
    self.register_task("update_channels", self.update_channels)
```

Registration is optional — tasks work fine without it, they just won't show up in `/health`.

### Key points

- **Start in `on_enable`**, **cancel in `on_disable`** — this ensures tasks stop on `/unload` or `/reload`.
- **`before_loop`**: Always `await self.bot.wait_until_ready()` so the task doesn't run before the bot is connected.
- **`error` handler**: Log unhandled errors. Without this, task exceptions are silently swallowed.

## 10. Events

The event bus enables inter-branch communication.

### Subscribing to events

```python
async def on_enable(self) -> None:
    self.events.on("ticket.created", self.handle_ticket_created)

async def handle_ticket_created(self, event):
    # event.source  — branch id that emitted the event
    # event.name    — "ticket.created"
    # event.data    — {"ticket_id": 123, "user_id": 456}
    ticket_id = event.data.get("ticket_id")
    self.log.info(f"New ticket: {ticket_id}")
```

### Emitting events

```python
await self.events.emit("ticket.created", {
    "ticket_id": ticket_id,
    "user_id": user.id,
})
```

The event is automatically tagged with your branch's `id` as the `source`.

### Event object

| Field | Type | Description |
|-------|------|-------------|
| `source` | `str` | Branch id that emitted the event |
| `name` | `str` | Event name (e.g. `"ticket.created"`) |
| `data` | `dict` | Arbitrary payload (default `{}`) |

Listeners get a 30-second timeout. Exceptions in listeners are logged, not propagated — one broken listener won't take down others.

Cleanup is automatic: when a branch is unloaded, all its event subscriptions are removed.

## 11. Admin Commands

These are built-in commands (require Discord Administrator permission):

| Command | Description |
|---------|-------------|
| `/load <branch>` | Load a discovered but unloaded branch |
| `/unload <branch>` | Unload a running branch |
| `/reload <branch>` | Hot-reload a branch (re-reads manifest + config + code) |
| `/enable <branch>` | Enable a disabled branch and load it |
| `/disable <branch>` | Disable and unload a branch |
| `/branches` | List all loaded and discovered branches |
| `/sync` | Force-sync slash commands to the guild |
| `/botinfo` | Show bot stats |
| `/health` | Check bot and branch health (DB, scheduled tasks) |
| `/metrics` | Show command usage, event counts, DB activity, errors |
| `/backup [branch]` | Back up branch database(s) to timestamped files |

All responses are ephemeral. Branch names autocomplete.

### Typical development workflow

1. Edit your branch code
2. `/reload my_branch` — reloads code, config, and manifest
3. `/sync` — if you added or changed slash commands
4. Test your changes

No bot restart needed for branch changes.

## 12. Paginated Embeds

The framework provides `PaginatedEmbedView` for paginating through a list of embeds with Previous/Next buttons:

```python
from oak.views import PaginatedEmbedView

# Build your pages
pages = [discord.Embed(title=f"Page {i+1}") for i in range(5)]

# Send with the view
view = PaginatedEmbedView(pages, author_id=interaction.user.id)
await interaction.response.send_message(embed=pages[0], view=view)
view.message = await interaction.original_response()
```

Features:
- Previous/Next buttons with disabled states at boundaries
- Only the original author can interact (others get an ephemeral "not your query" message)
- Buttons are automatically disabled on timeout (default 180s)
- Set `timeout=None` for persistent pagination (not recommended — register a persistent view instead)

## 13. Inter-Branch Services

Branches can expose a public API via `self.services` and consume other branches with `self.require_branch()`:

### Exposing services

```python
async def on_enable(self) -> None:
    await self.db.initialize(SCHEMA)
    self.services["get_player"] = self.get_player
    self.services["lookup_price"] = self.lookup_price
```

### Consuming services

```python
async def on_enable(self) -> None:
    shopkeepers = self.require_branch("shopkeepers")
    price_fn = shopkeepers.services["lookup_price"]
    price = await price_fn("diamond")
```

`require_branch()` raises `BranchNotFoundError` if the target branch isn't loaded. Use `dependencies` in `branch.yml` to ensure load order.

## 14. Database Backups

Branch databases can be backed up manually or on a schedule.

### Manual backup

```python
# From within a branch
path = await self.db.backup()
```

Or use the `/backup` admin command (backs up one branch or all).

### Periodic backups

Set environment variables in `.env`:

```env
DB_BACKUP_INTERVAL=6    # Back up every 6 hours (0 = disabled)
DB_BACKUP_MAX_COUNT=3   # Keep 3 most recent backups per branch
```

Backups are saved to `branches/<name>/backups/<stem>_YYYYMMDD_HHMMSS.db` and old backups are pruned automatically.

## 15. Migrating from Pre-Framework Branches

If you have branches written for the old codebase (before the Oak framework refactor), this section covers everything you need to update.

### Migration checklist

1. Add `branch.yml` manifest
2. Delete `__init__.py` (or empty it)
3. Change base class from `commands.Cog` to `OakBranch`
4. Change constructor from `__init__(self, bot)` to `__init__(self, ctx: BranchContext)`
5. Update imports (`config`, `constants`, `database`, `utils` → `oak.*`)
6. Replace manual config loading with `self.setting()`
7. Replace `aiosqlite.connect()` calls with `self.db` methods
8. Replace `cog_load`/`cog_unload` with `on_enable`/`on_disable`
9. Update `custom_id` strings to `oak:` namespace (with legacy compat if needed)

### Step 1: Add `branch.yml`

The framework now discovers branches by looking for `branch.yml`, not `__init__.py`. Create one in your branch folder:

```yaml
id: my_branch
name: MyBranch
version: "1.0.0"
description: What this branch does
main: branch.MyBranch
database: true          # if your branch uses a database
```

The `main` field is `module.ClassName` — typically `branch.MyBranch` if your class lives in `branch.py`.

### Step 2: Delete `__init__.py`

The old `__init__.py` contained a `setup(bot)` function for discord.py extension loading:

```python
# OLD — delete this entire file
async def setup(bot):
    await bot.add_cog(MyBranch(bot))
```

The framework handles instantiation now. Delete `__init__.py` or leave it empty.

### Step 3: Change base class and constructor

**Old:**
```python
from discord.ext import commands

class MyBranch(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.config = self.load_config()
        channel_id = self.config.get("settings", {}).get("channel_id", 0)
```

**New:**
```python
from oak import OakBranch
from oak.context import BranchContext

class MyBranch(OakBranch):
    def __init__(self, ctx: BranchContext):
        super().__init__(ctx)
        channel_id = self.setting("channel_id", default=0)
```

`super().__init__(ctx)` sets up `self.bot`, `self.config`, `self.db`, `self.log`, `self.data_dir`, `self.events`, and `self.interactions` automatically. Remove any manual setup for these.

### Step 4: Update imports

| Old import | New import |
|------------|------------|
| `from config import DISCORD_TOKEN, GUILD_ID` | `self.bot.guild_id` (token is internal) |
| `from constants import ...` | `from oak.constants import ...` |
| `from utils import load_branch_config` | Removed — config is auto-loaded |
| `from utils import deep_merge` | `from oak.config import deep_merge` (rarely needed) |
| `from utils import sanitize_text` | `from oak.utils import sanitize_text` |
| `from database import init_branch_database` | Removed — use `self.db.initialize()` |
| `import aiosqlite` (for queries) | Removed — use `self.db` methods |

### Step 5: Replace config loading

**Old** — every branch had a `load_config()` method:
```python
def load_config(self) -> dict:
    from utils import load_branch_config
    config_path = Path(__file__).parent / "config.yml"
    return load_branch_config(config_path, DEFAULT_CONFIG, "MyBranch")
```

**New** — delete `load_config()`. The framework reads `DEFAULT_CONFIG` from your module and deep-merges `config.yml` automatically. Access values with `self.setting()`:

```python
# Old
self.config.get("settings", {}).get("validation", {}).get("min_length", 10)

# New
self.setting("validation", "min_length", default=10)
```

### Step 6: Replace database access

**Old** — branches managed their own connections:
```python
from database import init_branch_database
import aiosqlite

# In cog_load:
self.db_path = str(Path(__file__).parent / "data.db")
await init_branch_database(self.db_path, SCHEMA, "MyBranch")

# For every query:
async with aiosqlite.connect(self.db_path) as db:
    cursor = await db.execute("SELECT ...", params)
    row = await cursor.fetchone()
    await db.commit()
```

**New** — the framework manages a persistent connection with write locking:
```python
# In on_enable:
await self.db.initialize(SCHEMA)

# For queries:
row = await self.db.fetchone("SELECT ...", params)
rows = await self.db.fetchall("SELECT ...", params)
await self.db.execute("INSERT ...", params)  # auto-locked, auto-committed
```

Make sure `database: true` is set in `branch.yml`, otherwise `self.db` will be `None`.

### Step 7: Replace lifecycle hooks

**Old** — used discord.py's `cog_load`/`cog_unload` directly:
```python
async def cog_load(self):
    await init_branch_database(...)

async def cog_unload(self):
    self.my_task.cancel()
```

**New** — use Oak lifecycle hooks:
```python
async def on_enable(self):       # replaces cog_load
    await self.db.initialize(SCHEMA)

async def on_disable(self):      # replaces cog_unload
    self.my_task.cancel()

async def on_ready(self):        # new — fires once when bot is connected
    self.log.info("Ready")
```

### Step 8: Update custom\_ids

Old branches used bare strings like `"suggestion_like"`. The new convention namespaces them with `oak:`:

```
Old: "suggestion_like"
New: "oak:suggestions:like"
```

If you have **existing messages in Discord** with old custom\_ids (persistent buttons), you need a legacy compatibility layer to handle both formats during the transition:

```python
_ID_MAP = {
    "suggestion_like": "oak:suggestions:like",
    "suggestion_dislike": "oak:suggestions:dislike",
}

class MyView(ui.View):
    def __init__(self, *, legacy: bool = False):
        super().__init__(timeout=None)
        if not legacy:
            for child in self.children:
                if hasattr(child, 'custom_id') and child.custom_id in _ID_MAP:
                    child.custom_id = _ID_MAP[child.custom_id]
```

Register both versions in `on_enable`:
```python
async def on_enable(self):
    self.bot.add_view(MyView(legacy=True))   # catches old button clicks
    self.bot.add_view(MyView())              # catches new button clicks
```

New messages will use the `oak:` namespaced IDs. Once all old messages have aged out, remove the legacy view.

### Step 9: Replace module-level loggers

**Old** — module-level logger in every file:
```python
logger = logging.getLogger(__name__)
```

**New** — use `self.log` in the branch class (already namespaced as `oak.branch.<id>`):
```python
self.log.info("Something happened")
self.log.error(f"Failed: {e}", exc_info=True)
```

Helper modules outside the branch class can still use `logging.getLogger(__name__)`.

### What's new (not a migration issue, but worth knowing)

These systems didn't exist before — you don't need to migrate anything, but they're available:

- **Event bus** (`self.events`) — inter-branch communication. See [Section 10](#10-events).
- **Interaction router** (`self.interactions`) — build/parse namespaced `custom_id` strings programmatically. See [Section 7](#7-views--buttons-persistent-ui).
- **Migration system** (`self.db.migrate()`) — tracked schema migrations. See [Section 5](#5-database).
- **Dependency resolution** — declare `dependencies` in `branch.yml` to control load order.
- **Graceful shutdown** — the framework calls `on_disable` and closes databases automatically on bot shutdown.
- **Paginated embeds** (`PaginatedEmbedView`) — framework-level Previous/Next pagination. See [Section 12](#12-paginated-embeds).
- **Task registry** (`self.register_task()`) — register background tasks so they appear in `/health`. See [Section 9](#9-background-tasks).
- **Service registry** (`self.services` + `self.require_branch()`) — inter-branch public APIs. See [Section 13](#13-inter-branch-services).
- **Database backups** (`self.db.backup()`, `/backup` command, periodic scheduling). See [Section 14](#14-database-backups).
- **Audit logging** — admin actions are logged to a configurable Discord channel.
- **Command metrics** — slash and text command usage tracked in `/metrics`.
