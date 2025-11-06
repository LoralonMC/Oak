# Oak - Modular Discord Bot Framework

A feature-rich, modular Discord bot framework providing suggestion management, staff applications, server status tracking, and more. Built with a flexible branch-based architecture inspired by Minecraft plugins and VSCode extensions.

**GitHub:** https://github.com/LoralonMC/oak

## Features

- **Suggestion System**: Users can submit suggestions with voting (likes/dislikes) and staff management
- **Staff Application System**: Automated trainee application process with multi-step forms and background checks
- **Server Status Tracking**: Real-time Minecraft server statistics and member count in voice channels
- **Account Linking Guide**: Instructions for linking Minecraft accounts to Discord
- **Modular Architecture**: Hot-reload branches without restarting the bot
- **Auto-Discovery**: New branches are automatically detected and loaded

## Architecture

Oak uses a **modular, folder-based branch system** inspired by Minecraft Paper plugins and VSCode extensions:

- Each branch is self-contained in its own folder
- Each branch has its own `config.yml` file with settings
- Hot-reload support - change configs and reload without restarting
- Auto-discovery - just create a branch folder and restart the bot
- Clean separation between global settings (`.env`) and branch-specific settings (`config.yml`)
- Per-branch databases - each branch manages its own SQLite database

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

   # Admin Role IDs (Bot Management - can use !reload, !load, etc.)
   # Comma-separated list of role IDs
   ADMIN_ROLE_IDS=role_id_1,role_id_2
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
Discord Bot/
├── bot.py                     # Main bot entry point
├── admin_commands.py          # Bot management commands (reload, load, etc.)
├── config.py                  # Global configuration loader
├── database.py                # Database utility functions
├── utils.py                   # Utility functions (validation, sanitization)
├── requirements.txt           # Python dependencies
├── create_branch.py           # Utility to create new branches
├── .env                       # Environment variables (with placeholders)
├── .gitignore                 # Git ignore file
├── logs/                      # Log files directory
├── core/
│   ├── __init__.py
│   └── branch_loader.py       # Auto-discovery and hot-reload system
└── branches/
    ├── __init__.py
    ├── application/           # Staff application system
    │   ├── __init__.py
    │   ├── branch.py
    │   ├── config.yml
    │   └── data.db            # Application database (auto-created)
    ├── suggestions/           # Suggestion voting system
    │   ├── __init__.py
    │   ├── branch.py
    │   ├── config.yml
    │   └── data.db            # Suggestions database (auto-created)
    ├── status_channels/       # Server status voice channels
    │   ├── __init__.py
    │   ├── branch.py
    │   └── config.yml
    └── link/                  # Account linking guide
        ├── __init__.py
        ├── branch.py
        └── config.yml
```

## Bot Admin Commands

These commands require roles specified in `ADMIN_ROLE_IDS` in `.env`:

- `!reload <branch>` - Reload a branch and its config (hot-reload without restart)
- `!load <branch>` - Load a previously unloaded branch
- `!unload <branch>` - Unload a branch (disable without deleting)
- `!branches` - List all currently loaded branches
- `!reloadall` - Reload all loaded branches at once
- `!botinfo` - Display bot statistics and information

**Example:**
```
!reload suggestions
!load application
!branches
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

The branch will be automatically discovered on next bot restart, or use `!load polls`.

**Note:** The generated template includes database functionality. If your branch doesn't need a database, simply comment out the database initialization in the `cog_load()` method and database-related code.

## Branch Features

### Suggestions System
**Location:** `branches/suggestions/`

Users post messages in the configured suggestion channel. The bot:
1. Creates an embed with the suggestion
2. Adds like/dislike voting buttons
3. Creates a discussion thread
4. Saves to database

Staff with `manager_role_ids` can manage suggestions:
- Approve with reason
- Deny with reason
- Delete suggestion
- View vote statistics

### Application System
**Location:** `branches/application/`

Comprehensive staff application system with configurable questions and workflow:
1. Users click configurable application button (e.g., "Apply for Staff")
2. Creates a private application channel
3. Multi-page application forms with customizable questions
4. Answer quality validation
5. Staff review tools:
   - Read full application across multiple pages
   - Accept/Deny with feedback to applicant
   - Optional: Background check (playtime via Plan plugin for Minecraft servers)
   - Optional: Punishment history lookup
6. Auto-cleanup of abandoned applications

**Configuration:**
- Fully customizable application questions in `config.yml`
- Configurable position name (Staff, Moderator, Helper, etc.)
- Optional MySQL/Plan integration for Minecraft servers
- Configurable button labels and channel names

**Staff Commands:**
- `!appstats` - View application statistics
- `!appcleanup [days]` - Clean up abandoned applications
- `!applist [status]` - List applications by status

### Status Channels
**Location:** `branches/status_channels/`

Automatically updates voice channels every 6 minutes:
- **Player Count Channel**: Shows online players (e.g., "Online: 15/100")
- **Member Count Channel**: Shows total Discord members (e.g., "Total Members: 1,234")

Uses `mcstatus` to query the Minecraft server.

### Link Command
**Location:** `branches/link/`

Provides instructions for linking Minecraft accounts to Discord using the Discord bot integration.

## Configuration System

### Global Settings (`.env`)
- `DISCORD_TOKEN` - Bot authentication token
- `GUILD_ID` - Discord server ID
- `ADMIN_ROLE_IDS` - Roles that can manage the bot

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
!reload suggestions
```
No bot restart needed!

## Permission System

The bot has **two separate permission levels**:

### Bot Admin Permissions
Defined in `.env` as `ADMIN_ROLE_IDS`. Controls:
- Bot management commands (!reload, !load, !unload, etc.)
- Access to bot information

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
3. Reload the branch: `!reload <name>`

### Creating New Branches

1. Use the creation script:
   ```bash
   python create_branch.py my_feature "Description"
   ```
2. Edit `branches/my_feature/config.yml` - Set your channel IDs, role IDs, etc.
3. Edit `branches/my_feature/branch.py` - Add your commands and logic
4. If you don't need a database, comment out the `cog_load()` method
5. Load the branch: `!load my_feature` (or restart bot)

### Best Practices

1. **Always use config files** - Never hardcode IDs or settings
2. **Test in development** - Use a test server before production
3. **Check logs** - Monitor logs in the `logs/` directory for errors
4. **Use hot-reload** - Use `!reload <branch>` instead of restarting
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
- Use `!branches` to see if branch is loaded

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
