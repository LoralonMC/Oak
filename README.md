# Oak - Modular Discord Bot Framework

A feature-rich, modular Discord bot framework providing suggestion management, staff applications, server status tracking, and more. Built with a flexible branch-based architecture inspired by Minecraft plugins and VSCode extensions.

**GitHub:** https://github.com/LoralonMC/oak

## Features

- **Suggestion System**: Community suggestion voting with like/dislike buttons, discussion threads, and staff approval/denial workflow
- **Staff Application System**: Comprehensive multi-page application forms with inactivity tracking, background checks (optional MySQL/Plan integration), and automated denial/acceptance workflow
- **Support Ticket System**: Thread-based tickets with multiple categories (gameplay, billing, reports, appeals, bugs), staff management, and automatic anti-archive protection
- **Server Status Tracking**: Auto-updating voice channels showing real-time Minecraft server player count and Discord member count
- **Account Linking Guide**: Simple command displaying instructions for linking Minecraft accounts via Discord
- **Bot Management**: Admin slash commands for hot-reloading branches, viewing stats, and managing the bot
- **Modular Architecture**: Hot-reload any branch and its config without restarting the bot
- **Auto-Discovery**: New branches are automatically detected and loaded on startup

## Architecture

Oak uses a **modular, folder-based branch system** inspired by Minecraft Paper plugins and VSCode extensions:

- Each branch is self-contained in its own folder
- Each branch has its own `config.yml` file with settings
- Hot-reload support - change configs and reload without restarting
- Auto-discovery - just create a branch folder and restart the bot
- Clean separation between global settings (`.env`) and branch-specific settings (`config.yml`)
- Per-branch databases - each branch manages its own SQLite database

### Terminology Note

Oak uses **"branch"** terminology for its modular extensions (inspired by Git branches and tree branches). However, because Oak is built on Discord.py, you'll see references to Discord.py's native `commands.Cog` class and `bot.add_cog()` method in the code. These are Discord.py's implementation details - from a user and developer perspective, everything is a **"branch"**.

**In practice:**
- 📁 **User-facing**: "branches" (folders, commands, documentation)
- 🔧 **Code-level**: `commands.Cog` (Discord.py's required base class)
- 💬 **When talking about Oak**: Use "branch"
- 💻 **When writing code**: Inherit from `commands.Cog` (required by Discord.py)

## Setup

### Requirements

- Python 3.8 or higher
- Discord Bot Token
- MySQL database (optional, for Plan integration)

### Installation

1. **Clone or download the repository**
   ```bash
   cd "Discord Bot"
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment variables**

   Create a `.env` file in the root directory:
   ```env
   # Discord Bot Token (REQUIRED - Get from https://discord.com/developers/applications)
   DISCORD_TOKEN=your_bot_token_here

   # Guild ID (Your Discord server ID)
   GUILD_ID=your_guild_id

   # Note: Bot admin commands (/reload, /load, etc.) use Discord's built-in
   # Administrator permission. No custom role configuration needed.
   ```

4. **Configure branches**

   Each branch has its own `config.yml` file. Edit these to set channel IDs, role IDs, etc:

   - `branches/application/config.yml` - Application system settings
   - `branches/suggestions/config.yml` - Suggestion system settings
   - `branches/status_channels/config.yml` - Status channel settings
   - `branches/link/config.yml` - Link command settings

5. **Run the bot**
   ```bash
   python bot.py
   ```

## Project Structure

```
Oak/
├── bot.py                     # Main bot entry point
├── config.py                  # Global configuration loader
├── database.py                # Database utility functions
├── utils.py                   # Utility functions (validation, sanitization)
├── constants.py               # Discord API limits and framework constants
├── requirements.txt           # Python dependencies
├── create_branch.py           # Utility to create new branches
├── .env                       # Environment variables (with placeholders)
├── .gitignore                 # Git ignore file
├── logs/                      # Log files directory (auto-created)
├── core/
│   ├── __init__.py
│   └── branch_loader.py       # Auto-discovery and hot-reload system
└── branches/
    ├── __init__.py
    ├── admin/                 # Bot management commands
    │   ├── __init__.py
    │   └── branch.py
    ├── application/           # Staff application system
    │   ├── __init__.py
    │   ├── branch.py
    │   └── config.yml
    ├── suggestions/           # Suggestion voting system
    │   ├── __init__.py
    │   ├── branch.py
    │   └── config.yml
    ├── status_channels/       # Server status voice channels
    │   ├── __init__.py
    │   ├── branch.py
    │   └── config.yml
    ├── link/                  # Account linking guide
    │   ├── __init__.py
    │   ├── branch.py
    │   └── config.yml
    └── tickets/               # Support ticket system
        ├── __init__.py
        ├── branch.py
        └── config.yml

Note: Branch databases (data.db files) are automatically created when branches first load.
They are git-ignored and not included in the repository.
```

## Bot Admin Commands

These commands require Administrator permission in Discord:

- `/reload <branch>` - Reload a branch and its config (hot-reload without restart)
- `/load <branch>` - Load a previously unloaded branch
- `/unload <branch>` - Unload a branch (disable without deleting)
- `/branches` - List all currently loaded branches
- `/reloadall` - Reload all loaded branches at once
- `/botinfo` - Display bot statistics and information

**Features:**
- Branch name autocomplete for easy selection
- Ephemeral responses (only you see them)
- Built-in parameter validation

**Example:**
```
/reload suggestions
/load application
/branches
```

## Creating New Branches

Use the included `create_branch.py` script to generate a new branch:

```bash
python create_branch.py polls "Community voting system"
```

This creates:
```
branches/polls/
├── __init__.py       # Package setup
├── branch.py         # Main branch code (with template)
├── config.yml        # Configuration file
└── data.db           # Database (auto-created on first run)
```

The branch will be automatically discovered on next bot restart, or use `/load polls`.

**Note:** The generated template includes database functionality. If your branch doesn't need a database, simply comment out the database initialization in the `cog_load()` method and database-related code.

> **About `cog_load()`**: This is a Discord.py Cog lifecycle method that's automatically called when your branch loads. It's the perfect place to initialize databases, register views, or set up background tasks.

## Branch Features

### Suggestions System
**Location:** `branches/suggestions/`

**What it does:**
When users post a message in the designated suggestions channel, the bot automatically:
1. Deletes the original message
2. Creates a professional embed with the suggestion content
3. Adds interactive voting buttons (👍 Like, 👎 Dislike, ⚙️ Manage)
4. Creates a discussion thread for community feedback
5. Tracks all votes in the database

**Staff Management Features:**
Staff members with configured manager roles can:
- **Approve** suggestions with a custom reason (embed turns green)
- **Deny** suggestions with a custom reason (embed turns red)
- **Delete** suggestions entirely
- View real-time vote statistics
- All actions update the embed and notify the original author

**Features:**
- Input validation (min/max length)
- Duplicate vote prevention
- Vote toggling (click again to remove vote)
- Persistent buttons (work after bot restart)
- Configurable embed colors and messages

### Application System
**Location:** `branches/application/`

**What it does:**
A complete staff application workflow system. Users click an "Apply for Staff" button which:
1. Creates a private application channel (only visible to applicant + reviewers)
2. Presents a multi-page form system (Discord modals with 5 questions per page)
3. Saves progress after each page (can continue later)
4. Validates answers for quality
5. Notifies staff when application is submitted

**Staff Review Tools:**
- **Read Applications**: View full application with pagination (handles Discord's field limits)
- **Accept**: Moves channel to "accepted" category, notifies applicant
- **Deny**: Sends reason to applicant, optionally deletes channel after delay
- **Background Checks** (optional): Query Minecraft playtime via MySQL/Plan plugin
- **Punishment History** (optional): Check forum for prior warnings/bans
- **Application History**: View user's past applications with `/apphistory`

**Automated Management:**
- **Inactivity Warnings**: Warns applicants after 3 days of inactivity
- **Auto-Abandon**: Closes applications after 7 days of inactivity
- **Duplicate Prevention**: Users can only have one active application
- **Statistics Tracking**: Track processing times, acceptance rates, etc.

**Slash Commands:**
- `/appstats` - View detailed application statistics
- `/apphistory <user>` - View a user's complete application history

**Configuration:**
- 17 customizable application questions (edit in `config.yml`)
- Configurable position name and channel naming
- Optional MySQL/Plan integration for Minecraft servers
- Customizable inactivity thresholds and messages
- Optional account linking requirement

### Tickets System
**Location:** `branches/tickets/`

**What it does:**
A thread-based support ticket system with multiple categories. Users click a button to select their issue type, which:
1. Creates a private thread in a configured channel
2. Adds the user and pings relevant staff roles
3. Sends a customized welcome message for that category
4. Provides ticket control buttons (Close, Reopen)

**Ticket Categories (fully configurable):**
- 🎮 **Ingame Support**: Gameplay help and questions
- 💳 **Billing Support**: Purchase/donation issues
- ⚠️ **Player Reports**: Report rule-breaking players
- 🔓 **Punishment Appeals**: Appeal bans or warnings
- 🐛 **Bug Reports**: Report technical issues

**Features:**
- **Unique Numbering**: Each category has sequential ticket numbers (e.g., `ingame-01`, `billing-02`)
- **Flexible Naming**: Use `{number}` for sequential, `{nickname}` for user-specific names
- **Staff Management**: Close tickets with custom reasons, reopen if needed
- **Anti-Archive**: Automatically unarchives closed tickets that get reopened
- **Activity Tracking**: View user's ticket history with `/tickets` command
- **Statistics**: Detailed ticket stats with `/ticketstats` command
- **Logging**: Optional ticket event logging to a dedicated channel
- **Privacy Controls**: Configure which categories allow adding other users

**Slash Commands:**
- `/ticketstats` - View ticket statistics (total, by category, resolution times)
- `/tickets` - View your open tickets

**Configuration:**
- Define unlimited custom categories with unique settings
- Per-category staff role pings
- Custom welcome messages per category
- Configurable panel embed and button layout

### Status Channels
**Location:** `branches/status_channels/`

**What it does:**
Automatically updates voice channel names every 6 minutes with live statistics:
- **Player Count**: Minecraft server online players (e.g., "Online: 15/100")
- **Member Count**: Total Discord server members (e.g., "Total Members: 1,234")

**Features:**
- Uses `mcstatus` library to query Minecraft Java servers
- Graceful error handling (continues on server offline)
- Rate limit protection with retry logic
- Anti-spike jitter (±10% randomization to avoid synchronized API calls)
- Customizable format strings for both counters

**Configuration:**
- Minecraft server host and port
- Channel IDs for player count and member count
- Custom format strings (supports number formatting)

### Link Command
**Location:** `branches/link/`

**What it does:**
Displays instructions for linking Minecraft accounts to Discord.

**Features:**
- Simple `!link` command
- Customizable embed (title, description, color)
- Explains the linking process step-by-step

**Use Case:**
For Minecraft servers using Discord linking plugins (e.g., DiscordSRV, Plan) where players need to link their accounts to get roles synchronized.

### Admin Commands
**Location:** `branches/admin/`

**What it does:**
Provides bot management slash commands (requires Discord Administrator permission).

**Available Commands:**
- `/reload <branch>` - Hot-reload a branch and its config without restarting
- `/load <branch>` - Load a previously unloaded branch
- `/unload <branch>` - Unload a branch temporarily
- `/branches` - List all currently loaded branches
- `/reloadall` - Reload all branches at once
- `/botinfo` - Display bot statistics and information

**Features:**
- Branch name autocomplete for easy selection
- Ephemeral responses (only you see them)
- Protection against unloading critical branches
- Detailed error messages

## Configuration System

### Global Settings (`.env`)
- `DISCORD_TOKEN` - Bot authentication token
- `GUILD_ID` - Discord server ID

### Per-Branch Settings (`branches/<name>/config.yml`)
Each branch has its own configuration file with:
- `enabled` - Toggle branch on/off
- `version` - Config version
- `settings` - Branch-specific settings (channel IDs, role IDs, etc.)

**Example** (`branches/suggestions/config.yml`):
```yaml
enabled: true
version: "1.0.0"
settings:
  channel_id: 1374374186016964788
  manager_role_ids:
    - 937003755185012756
    - 937003936156647514
  validation:
    min_length: 10
    max_length: 4000
```

### Hot-Reload
Change a config file and reload the branch:
```
/reload suggestions
```
No bot restart needed!

## Permission System

The bot has **two separate permission levels**:

### Bot Admin Permissions
Uses Discord's built-in **Administrator permission**. Controls:
- Bot management commands (/reload, /load, /unload, etc.)
- Access to bot information
- Anyone with Administrator permission in Discord can use these commands

### Branch-Specific Permissions
Defined in each branch's `config.yml`. Examples:
- **Suggestions**: `manager_role_ids` - Who can approve/deny suggestions
- **Applications**: `reviewer_role_ids` - Who can review applications

This separation allows you to give different people access to bot management vs. feature management.

## Database

Oak uses a **per-branch database architecture** where each branch manages its own SQLite database:
- **branches/suggestions/data.db** - Stores suggestions, votes, status, and reasons
- **branches/application/data.db** - Stores application data, answers, and status

Each database is automatically created and initialized when the branch loads for the first time. This makes each branch truly self-contained and portable.

## Logging

Logs are stored in the `logs/` directory with files named `oak_YYYYMMDD.log` and include:
- Bot startup and shutdown events
- Branch loading/reloading
- User actions (suggestions, applications)
- Errors and warnings
- Discord API issues

Logs are also output to console for real-time monitoring.

## Security Features

- All secrets stored in `.env` file (`.env` includes placeholders for public release, use `.env.local` for actual secrets)
- Input validation and sanitization
- SQL injection prevention (parameterized queries)
- Discord token never exposed in code
- MySQL credentials in application config only (not global)

## Development

### Adding Features to Existing Branches

1. Edit the branch file: `branches/<name>/branch.py`
2. Add new settings to: `branches/<name>/config.yml`
3. Reload the branch: `/reload <name>`

### Creating New Branches

1. Use the creation script:
   ```bash
   python create_branch.py my_feature "Description"
   ```
2. Edit `branches/my_feature/config.yml` - Set your channel IDs, role IDs, etc.
3. Edit `branches/my_feature/branch.py` - Add your commands and logic
4. If you don't need a database, comment out the database init in `cog_load()`
5. Load the branch: `/load my_feature` (or restart bot)

> **Tip**: `cog_load()` and `cog_unload()` are Discord.py Cog lifecycle methods. Keep them for resource initialization/cleanup even if you don't use a database.

### Best Practices

1. **Always use config files** - Never hardcode IDs or settings
2. **Test in development** - Use a test server before production
3. **Check logs** - Monitor logs in the `logs/` directory for errors
4. **Use hot-reload** - Use `/reload <branch>` instead of restarting
5. **Validate input** - Use utility functions from `utils.py`
6. **Handle errors** - Use try-except blocks with logging

## Troubleshooting

### Bot won't start
- Check that `DISCORD_TOKEN` is set in `.env`
- Verify all required environment variables are present
- Check console output for specific errors

### Commands not working
- Verify bot has proper Discord permissions in server settings
- Check that role IDs in configs are correct
- Use `/branches` to see if branch is loaded

### Branch won't load
- Check syntax: `python -m py_compile branches/<name>/branch.py`
- Review logs in the `logs/` directory
- Verify `config.yml` is valid YAML

### Database errors
- Check that the branch folder is writable
- Review database initialization in logs
- Each branch has its own `data.db` in its folder
- Delete `branches/<name>/data.db` to rebuild that branch's database (loses data!)

### MySQL/Plan integration not working
- Verify credentials in `branches/application/config.yml`
- Set `mysql.enabled: false` to disable if not using Plan
- Check network connectivity to MySQL server

## Support

For issues or questions:
1. Check the logs first (in the `logs/` directory)
2. Review this README
3. Check branch-specific config files
4. Verify Discord bot permissions
5. Open an issue on GitHub: https://github.com/LoralonMC/oak/issues

## License

MIT License - See LICENSE file for details

---

**Oak** - A modular Discord bot framework by [LoralonMC](https://github.com/LoralonMC)
