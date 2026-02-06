# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased] - 2026-02-06

### Added - Shopkeepers Branch (New)
A complete CSV-based trade log importer and market analysis system for Minecraft Shopkeepers plugin data.

#### Core System
- **CSV Importer**: Automatic and manual import of Shopkeepers trade log CSV files
- **NBT Parser**: Identifies vanilla items, enchanted books, potions, tipped arrows, shulker boxes, and custom plugin items
- **Dual NBT Format Support**: Handles both legacy Bukkit (`v:4189`) and modern 1.20.5+ component (`DataVersion:4440`) formats
- **SHA-256 Deduplication**: Fingerprint-based trade dedup prevents double-counting on re-import
- **Incremental Import**: Only processes new lines from each CSV, skips unchanged files entirely
- **Price Summary Table**: Maintains daily price averages for fast lookups, rebuilt incrementally for affected dates only

#### Public Commands
- **`/price <item> [days]`**: Check average, min, max price with daily price chart (1-365 days)
- **`/top [sort_by]`**: View top traded items by trade count, volume, or total value
- **`/search <query>`**: Search for items by name with price info
- **`/player <name>`**: View trade stats for a player (spending, favorites, recent trades)
- **`/shop <owner>`**: View trade stats for a shop owner (revenue, top items, recent trades)
- **`/trending [period] [direction]`**: Items with biggest price changes (compares recent half vs prior half)
- **`/shops [sort_by]`**: Top shop owner leaderboard (by revenue, trades, or unique items)
- **`/players [sort_by]`**: Top player leaderboard (by spending, trades, or unique items)

#### Admin Commands
- **`/shopkeepers_import`**: Manually trigger CSV import
- **`/shopkeepers_stats`**: View database statistics (total trades, items, files, etc.)
- **`/economy [period]`**: Economy sink/faucet analysis (emerald flow through admin shops)
- **`/sinks [period]`**: Detailed breakdown of items players buy from admin shops
- **`/faucets [period]`**: Detailed breakdown of items players sell to admin shops

#### Configuration
- Configurable multi-tier currency system (Emeralds, Emerald Blocks, Compressed Emerald Blocks)
- Plugin identity key support (ExecutableItems, ItemsAdder, PhoenixCrates, etc.)
- Auto-import interval, UI settings, admin role IDs
- Admin shop prices excluded from market averages

#### Architecture
- 5 database tables: `imported_files`, `items`, `trades`, `players`, `price_summary`
- 6 source files: `branch.py`, `importer.py`, `nbt_parser.py`, `helpers.py`, `views.py`, `__init__.py`
- Paginated embeds with interactive Previous/Next buttons for all list commands
- Background auto-import task with configurable interval

---

## [Unreleased] - 2025-01-15

### Added - Tickets Branch

#### Ticket Reminder System
- **`/remindme` command**: Set custom reminders for tickets with optional initial delay and DM notifications
  - Supports time formats like `30m`, `1h`, `2h`, `1d`
  - Optional DM notifications when reminders fire
  - Daily recurring reminders after initial reminder
  - Snooze functionality (1h, 6h, 1d)
  - Stop reminder functionality
- **`/stopreminder` command**: Stop active reminders for the current ticket
- **Reminder Control View**: Interactive buttons on reminder messages to stop or snooze
- **Reminder background task**: Automatically sends reminders at scheduled times (checks every minute)
- **Database schema**: Added `ticket_reminders` table with proper indexes and constraints
- **Auto-cleanup**: Reminders are automatically cancelled when tickets are closed

#### Staff Commands
- **`/closeticket` command**: Staff command to close tickets with optional reason (works in any ticket thread)
- **`/addticket` command**: Manually add existing threads to the tickets database with category autocomplete
- **`/ticketstats` command**: View comprehensive ticket statistics including resolution times and category breakdowns

#### Category-Based Permission System
- **New permission model**: Staff can only manage tickets in categories they're assigned to
- **`staff_roles` config setting**: Renamed from `ping_roles` for clarity (backwards compatible)
  - Roles listed here can both manage tickets AND are pinged when tickets are created
  - Provides granular access control per category (e.g., only admins can manage billing tickets)
- **Global staff override**: Roles in `staff_role_ids` can manage ALL categories
- **`can_manage_ticket_category()` helper**: Checks if user can manage tickets in specific categories
- **Enhanced security**: Sensitive categories (billing, appeals) can be restricted to specific roles

#### Ticket Reopening
- **Auto-reopen detection**: Tickets automatically reopen if manually unarchived
- **`on_raw_thread_update` listener**: Detects when closed tickets are unarchived and updates status
- **Reopen logging**: Logs reopen events to the log channel

#### Configuration Improvements
- **`categories_field_name` panel setting**: Customize or hide the categories field name in ticket panel
- **Hot-reload support**: Config values are reloaded in `cog_load()` for better development experience
- **Backwards compatibility**: Old configs using `ping_roles` still work seamlessly

### Changed - Tickets Branch

#### Permission System Overhaul
- **Close button permissions**: Now respect category-based permissions (uses `can_manage_ticket_category`)
- **Command permissions**: All staff commands now use category-based permission checks
- **Removed global staff checks**: Commands like `/closeticket` and `/reopenticket` now check category permissions instead of global staff only

#### Database Operations
- **Thread closure order**: Thread is now archived+locked BEFORE database update for better consistency
- **Race condition protection**: Added retry logic with exponential backoff in `get_next_ticket_number()`
- **Transaction safety**: Uses IMMEDIATE transactions to prevent concurrent ticket number collisions

#### Code Quality
- **Improved error handling**: Better error messages for permission failures
- **Enhanced logging**: More detailed logs for reminder operations and permission checks
- **Type hints**: Added proper type annotations throughout

### Fixed - Tickets Branch

- **Privacy issue**: Staff members can no longer access/manage tickets outside their assigned categories
- **Thread unarchive bug**: Closed tickets that are manually unarchived now properly reopen with correct status
- **Permission checks**: Ticket creators can close their own tickets regardless of staff permissions
- **Panel validation**: Panel now properly validates and recreates when config changes

### Security - Tickets Branch

- **Category isolation**: Sensitive categories (billing, appeals) can now be restricted to specific staff roles
- **Dual-layer security**:
  - Discord permission layer: Control thread visibility via "Manage Threads" permission
  - Bot command layer: Control ticket management via `staff_roles` config
- **Permission validation**: All management commands now validate category access before executing

---

## Notes

### Migration Guide: `ping_roles` → `staff_roles`

The `ping_roles` setting has been renamed to `staff_roles` to better reflect its dual purpose:
1. Roles that get pinged when tickets are created
2. Roles that can manage tickets in that category

**Action Required**: Update your `config.yml` to use `staff_roles` instead of `ping_roles`

**Backwards Compatibility**: Old configs using `ping_roles` will continue to work, but updating is recommended.

### Recommended Permission Setup

For proper security with sensitive ticket categories:

1. **Global staff roles** (`staff_role_ids`): Only admin/owner roles with full access
2. **Category staff roles** (`staff_roles`): Specific roles per category
3. **Discord permissions**: Remove "Manage Threads" from regular staff in ticket channel
4. **Bot permissions**: Ensure bot has "Manage Threads" to create/close tickets

Example:
```yaml
staff_role_ids:
  - 123456  # Admin - full access
  - 789012  # Owner - full access

categories:
  billing_support:
    staff_roles:
      - 123456  # Admin only
      - 789012  # Owner only

  ingame_support:
    staff_roles:
      - 345678  # Staff role
```
