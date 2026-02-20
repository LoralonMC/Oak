"""
Shopkeepers Branch - Main Module
CSV-based trade log importer and price analysis for Minecraft shopkeeper data.
"""

import asyncio
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import tasks

from oak import OakBranch
from oak.context import BranchContext
from oak.constants import EMBED_DESCRIPTION_MAX
from oak.utils import truncate_for_embed_field

from .helpers import (
    get_csv_directory,
    is_admin,
    validate_config,
    format_price,
    format_item_name,
)
from .importer import import_all
from .views import PaginatedEmbedView

# Default configuration for this branch
DEFAULT_CONFIG = {
    "enabled": True,
    "version": "1.0.0",
    "settings": {
        "csv_directory": "",
        "auto_import": {
            "enabled": True,
            "interval_minutes": 30,
        },
        "currencies": [
            {
                "item_key": "minecraft:emerald",
                "emerald_value": 1,
            },
            {
                "item_key": "minecraft:emerald_block",
                "emerald_value": 9,
            },
            {
                "item_key": "executableitems:CompressedEmeraldBlock",
                "emerald_value": 576,
            },
        ],
        "plugin_identity_keys": [
            "executableitems:ei-id",
            "itemsadder",
            "phoenixcrates:key",
            "crazycrates:crazycrates_crate_key",
            "oaktools:tool_type",
            "nekotraps:trap_type",
            "shopkeepers:shop_creation_item",
        ],
        "ui": {
            "embed_color": 0x50C878,
            "trades_per_page": 8,
            "top_entries": 10,
            "price_history_days": 30,
        },
        "admin_role_ids": [],
    },
}

# Database schema for shopkeepers
SHOPKEEPERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS imported_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT UNIQUE NOT NULL,
    file_date DATE NOT NULL,
    last_line_imported INTEGER DEFAULT 0,
    file_size INTEGER,
    file_mtime_ns INTEGER,
    imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_key TEXT UNIQUE NOT NULL,
    material_type TEXT NOT NULL,
    display_name TEXT,
    plugin_id TEXT,
    search_name TEXT NOT NULL,
    is_currency INTEGER DEFAULT 0,
    emerald_value REAL DEFAULT 0.0
);

CREATE INDEX IF NOT EXISTS idx_items_search_name ON items(search_name);
CREATE INDEX IF NOT EXISTS idx_items_material_type ON items(material_type);
CREATE INDEX IF NOT EXISTS idx_items_plugin_id ON items(plugin_id);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date DATE NOT NULL,
    trade_time TEXT NOT NULL,
    player_uuid TEXT NOT NULL,
    shop_uuid TEXT NOT NULL,
    shop_type TEXT NOT NULL,
    shop_world TEXT NOT NULL,
    shop_x INTEGER NOT NULL,
    shop_y INTEGER NOT NULL,
    shop_z INTEGER NOT NULL,
    shop_owner_uuid TEXT,
    item1_id INTEGER,
    item1_amount INTEGER,
    item2_id INTEGER,
    item2_amount INTEGER,
    result_item_id INTEGER,
    result_amount INTEGER,
    trade_count INTEGER DEFAULT 1,
    emerald_cost_total REAL,
    emerald_cost_per_unit REAL,
    source_file TEXT NOT NULL,
    source_line INTEGER NOT NULL,
    row_fingerprint TEXT UNIQUE NOT NULL,
    FOREIGN KEY (item1_id) REFERENCES items(id),
    FOREIGN KEY (item2_id) REFERENCES items(id),
    FOREIGN KEY (result_item_id) REFERENCES items(id)
);

CREATE INDEX IF NOT EXISTS idx_trades_result_date_time ON trades(result_item_id, trade_date, trade_time);
CREATE INDEX IF NOT EXISTS idx_trades_date_time ON trades(trade_date, trade_time);
CREATE INDEX IF NOT EXISTS idx_trades_item1 ON trades(item1_id);
CREATE INDEX IF NOT EXISTS idx_trades_player_uuid ON trades(player_uuid);
CREATE INDEX IF NOT EXISTS idx_trades_shop_owner_uuid ON trades(shop_owner_uuid);
CREATE INDEX IF NOT EXISTS idx_trades_shop_type ON trades(shop_type);
CREATE INDEX IF NOT EXISTS idx_trades_shop_type_date ON trades(shop_type, trade_date);

CREATE TABLE IF NOT EXISTS price_summary (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL,
    trade_date DATE NOT NULL,
    avg_price REAL,
    min_price REAL,
    max_price REAL,
    total_volume REAL,
    trade_count INTEGER,
    UNIQUE(item_id, trade_date),
    FOREIGN KEY (item_id) REFERENCES items(id)
);

CREATE INDEX IF NOT EXISTS idx_price_summary_trade_date ON price_summary(trade_date);

CREATE TABLE IF NOT EXISTS players (
    uuid TEXT PRIMARY KEY NOT NULL,
    last_name TEXT NOT NULL,
    last_seen DATE NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_players_last_name ON players(last_name COLLATE NOCASE);
"""


class Shopkeepers(OakBranch):
    """CSV-based trade log importer and price analysis system."""

    def __init__(self, ctx: BranchContext):
        super().__init__(ctx)

        # Validate config
        is_valid, errors = validate_config(self.config)
        if not is_valid:
            self.log.error(f"Shopkeepers config validation failed: {errors}")
            for error in errors:
                self.log.error(f"  - {error}")

        # Cache settings
        self.csv_directory = self.setting("csv_directory", default="") or get_csv_directory(self.config)
        self.currencies = self.setting("currencies", default=[])
        self.plugin_identity_keys = self.setting("plugin_identity_keys", default=[])

        # UI settings
        self.embed_color = self.setting("ui", "embed_color", default=0x50C878)
        self.trades_per_page = self.setting("ui", "trades_per_page", default=8)
        self.top_entries = self.setting("ui", "top_entries", default=10)
        self.price_history_days = self.setting("ui", "price_history_days", default=30)

        # Admin settings
        self.admin_role_ids = self.setting("admin_role_ids", default=[])

        # Auto-import settings
        self.auto_import_enabled = self.setting("auto_import", "enabled", default=True)
        self.auto_import_interval = self.setting("auto_import", "interval_minutes", default=30)

        self._auto_import_task = None
        self._import_lock = asyncio.Lock()

        self.log.info("Shopkeepers branch initialized")

    async def on_enable(self):
        """Initialize database, run migrations, sync currencies."""
        if self.db:
            await self.db.initialize(SHOPKEEPERS_SCHEMA)

        # Run migrations for existing databases
        await self._run_migrations()

        # Sync currency flags in items table
        await self._sync_currencies()

        # Start auto-import task if enabled
        if self.auto_import_enabled:
            self.auto_import_task.change_interval(minutes=self.auto_import_interval)
            self.auto_import_task.start()
            self._auto_import_task = self.auto_import_task
            self.log.info(f"Auto-import task started (interval: {self.auto_import_interval} minutes)")

        self.log.info("Shopkeepers branch loaded")

    async def _run_migrations(self):
        """Run database migrations for existing databases."""
        pass

    async def _sync_currencies(self):
        """
        Sync currency configuration to the items table.

        Sets is_currency=1 and emerald_value for configured currencies.
        Resets any items no longer in the currency list to is_currency=0.
        """
        try:
            async with self.db.transaction() as conn:
                # Collect configured currency item_keys
                currency_keys = set()
                for currency in self.currencies:
                    item_key = currency.get("item_key")
                    emerald_value = currency.get("emerald_value", 0)
                    if not item_key:
                        continue
                    currency_keys.add(item_key)

                    # Upsert: update if exists
                    await conn.execute(
                        """UPDATE items SET is_currency = 1, emerald_value = ?
                        WHERE item_key = ?""",
                        (emerald_value, item_key),
                    )

                # Reset items no longer in the currency list
                if currency_keys:
                    placeholders = ",".join("?" for _ in currency_keys)
                    await conn.execute(
                        f"""UPDATE items SET is_currency = 0, emerald_value = 0.0
                        WHERE is_currency = 1 AND item_key NOT IN ({placeholders})""",
                        tuple(currency_keys),
                    )
                else:
                    # No currencies configured, reset all
                    await conn.execute(
                        "UPDATE items SET is_currency = 0, emerald_value = 0.0 WHERE is_currency = 1"
                    )

            self.log.info(f"Currency sync complete ({len(currency_keys)} currencies configured)")
        except Exception as e:
            self.log.error(f"Error syncing currencies: {e}", exc_info=True)

    async def on_disable(self):
        """Cancel auto-import task if running."""
        if self._auto_import_task and self._auto_import_task.is_running():
            self._auto_import_task.cancel()
        self.log.info("Shopkeepers branch unloaded")

    # ------------------------------------------------------------------
    # Pagination helpers
    # ------------------------------------------------------------------

    def _build_pages(
        self,
        rows: list,
        per_page: int,
        title: str,
        format_row,
        footer_extra: str = "",
        separator: str = "\n",
    ) -> list[discord.Embed]:
        """Build paginated embeds from query rows.

        Args:
            rows: Query result rows.
            per_page: Items per page.
            title: Embed title.
            format_row: Callable(rank: int, row) -> str for each row.
            footer_extra: Extra text appended to page footer.
            separator: Join separator between lines (default newline).
        """
        pages = []
        for page_start in range(0, len(rows), per_page):
            page_rows = rows[page_start:page_start + per_page]
            lines = [format_row(i, row) for i, row in enumerate(page_rows, start=page_start + 1)]
            page_num = (page_start // per_page) + 1
            total_pages = -(-len(rows) // per_page)
            description = separator.join(lines)
            if len(description) > EMBED_DESCRIPTION_MAX:
                description = description[:EMBED_DESCRIPTION_MAX - 3] + "..."
            embed = discord.Embed(
                title=title,
                description=description,
                color=self.embed_color,
            )
            footer = f"Page {page_num}/{total_pages}"
            if footer_extra:
                footer += f" | {footer_extra}"
            embed.set_footer(text=footer)
            pages.append(embed)
        return pages

    @staticmethod
    def _escape_like(text: str) -> str:
        """Escape SQL LIKE wildcard characters in user input."""
        return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    async def _send_paginated(self, interaction: discord.Interaction, pages: list[discord.Embed], *, ephemeral: bool = True):
        """Send a single embed or a paginated view."""
        if not pages:
            await interaction.followup.send("No data available.", ephemeral=True)
            return
        if len(pages) == 1:
            await interaction.followup.send(embed=pages[0], ephemeral=ephemeral)
        else:
            view = PaginatedEmbedView(pages, interaction.user.id)
            message = await interaction.followup.send(embed=pages[0], view=view, ephemeral=ephemeral)
            view.message = message

    @tasks.loop(minutes=30)
    async def auto_import_task(self):
        """Periodically import new CSV trade data."""
        try:
            async with self._import_lock:
                result = await import_all(
                    self.db, self.csv_directory,
                    self.plugin_identity_keys, self.currencies,
                )

            if result.new_trades > 0 or result.errors:
                self.log.info(
                    f"Auto-import complete: {result.files_imported} files, "
                    f"{result.new_trades} new trades, {result.new_items} new items"
                )
            if result.errors:
                for error in result.errors:
                    self.log.error(f"Auto-import error: {error}")
        except Exception as e:
            self.log.error(f"Error in auto-import task: {e}", exc_info=True)

    @auto_import_task.before_loop
    async def before_auto_import_task(self):
        """Wait until bot is ready before starting task."""
        await self.bot.wait_until_ready()

    @auto_import_task.error
    async def auto_import_task_error(self, error):
        """Log errors from the auto-import task."""
        self.log.error(f"Unhandled error in auto_import_task: {error}", exc_info=True)

    @app_commands.command(
        name="shopkeepers_import",
        description="Manually import CSV trade data (Admin only)",
    )
    async def shopkeepers_import(self, interaction: discord.Interaction):
        """Manually trigger a CSV import."""
        if not is_admin(interaction, self.admin_role_ids):
            await interaction.response.send_message(
                "You don't have permission to use this command.", ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        async with self._import_lock:
            result = await import_all(
                self.db, self.csv_directory,
                self.plugin_identity_keys, self.currencies,
            )

        embed = discord.Embed(
            title="CSV Import Complete",
            color=self.embed_color,
        )
        embed.add_field(name="Files Scanned", value=str(result.files_scanned), inline=True)
        embed.add_field(name="Files Imported", value=str(result.files_imported), inline=True)
        embed.add_field(name="Files Skipped", value=str(result.files_skipped), inline=True)
        embed.add_field(name="New Trades", value=str(result.new_trades), inline=True)
        embed.add_field(name="Duplicate Trades", value=str(result.duplicate_trades), inline=True)
        embed.add_field(name="New Items", value=str(result.new_items), inline=True)

        if result.errors:
            error_text = "\n".join(result.errors[:10])
            if len(result.errors) > 10:
                error_text += f"\n... and {len(result.errors) - 10} more"
            embed.add_field(name="Errors", value=truncate_for_embed_field(error_text), inline=False)

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # Autocomplete
    # ------------------------------------------------------------------

    async def item_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Shared autocomplete for item searches. Returns up to 25 items."""
        try:
            if current.strip():
                escaped = self._escape_like(current.lower())
                query = """
                    SELECT i.item_key, i.display_name, i.search_name,
                           COALESCE(SUM(ps.total_volume), 0) AS vol
                    FROM items i
                    LEFT JOIN price_summary ps ON ps.item_id = i.id
                    WHERE i.is_currency = 0 AND i.search_name LIKE ? ESCAPE '\\'
                    GROUP BY i.id
                    ORDER BY vol DESC
                    LIMIT 25
                """
                rows = await self.db.fetchall(query, (f"%{escaped}%",))
            else:
                query = """
                    SELECT i.item_key, i.display_name, i.search_name,
                           COALESCE(SUM(ps.total_volume), 0) AS vol
                    FROM items i
                    LEFT JOIN price_summary ps ON ps.item_id = i.id
                    WHERE i.is_currency = 0
                    GROUP BY i.id
                    ORDER BY vol DESC
                    LIMIT 25
                """
                rows = await self.db.fetchall(query)

            return [
                app_commands.Choice(
                    name=format_item_name(row[1], row[2])[:100],
                    value=row[0],
                )
                for row in rows
            ]
        except Exception as e:
            self.log.error(f"Item autocomplete error: {e}", exc_info=True)
            return []

    async def player_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Shared autocomplete for player searches. Returns up to 25 players."""
        try:
            if current.strip():
                escaped = self._escape_like(current)
                query = """
                    SELECT last_name, uuid FROM players
                    WHERE last_name LIKE ? ESCAPE '\\' COLLATE NOCASE
                    ORDER BY last_seen DESC
                    LIMIT 25
                """
                rows = await self.db.fetchall(query, (f"%{escaped}%",))
            else:
                # Show most recently active players when empty
                query = """
                    SELECT last_name, uuid FROM players
                    ORDER BY last_seen DESC
                    LIMIT 25
                """
                rows = await self.db.fetchall(query)

            return [
                app_commands.Choice(name=row[0][:100], value=row[0])
                for row in rows
            ]
        except Exception as e:
            self.log.error(f"Player autocomplete error: {e}", exc_info=True)
            return []

    # ------------------------------------------------------------------
    # /price
    # ------------------------------------------------------------------

    @app_commands.command(name="price", description="Check the price of an item")
    @app_commands.describe(item="Item to look up", days="Number of days to analyze (default: 30)", public="Show result publicly")
    async def price(self, interaction: discord.Interaction, item: str, days: app_commands.Range[int, 1, 365] = None, public: bool = False):
        """Show price statistics for an item."""
        await interaction.response.defer(ephemeral=not public)

        period_days = days if days is not None else self.price_history_days

        try:
            # Exact match on item_key
            row = await self.db.fetchall(
                "SELECT id, item_key, display_name, search_name FROM items WHERE item_key = ?",
                (item,),
            )
            # Fallback: fuzzy match on search_name
            if not row:
                escaped_item = self._escape_like(item.lower())
                row = await self.db.fetchall(
                    "SELECT id, item_key, display_name, search_name FROM items WHERE search_name LIKE ? ESCAPE '\\' ORDER BY LENGTH(search_name) ASC LIMIT 1",
                    (f"%{escaped_item}%",),
                )
            if not row:
                await interaction.followup.send(f"No item found matching **{item[:100]}**.", ephemeral=True)
                return

            item_id, item_key, display_name, search_name = row[0]
            name = format_item_name(display_name, search_name)

            # Fetch all individual trade prices for true median calculation
            all_prices = await self.db.fetchall(
                """SELECT emerald_cost_per_unit FROM trades
                   WHERE result_item_id = ? AND trade_date >= date('now', ?)
                     AND emerald_cost_per_unit IS NOT NULL
                     AND shop_type != 'admin'
                   ORDER BY emerald_cost_per_unit""",
                (item_id, f"-{period_days} days"),
            )

            # Calculate true median from individual trades
            median_p = None
            if all_prices:
                prices = [p[0] for p in all_prices]
                n = len(prices)
                if n % 2 == 1:
                    median_p = prices[n // 2]
                else:
                    median_p = (prices[n // 2 - 1] + prices[n // 2]) / 2

            # Aggregate price data over configured period
            stats = await self.db.fetchall(
                """SELECT AVG(avg_price), MIN(min_price), MAX(max_price),
                          SUM(total_volume), SUM(trade_count)
                   FROM price_summary
                   WHERE item_id = ? AND trade_date >= date('now', ?)""",
                (item_id, f"-{period_days} days"),
            )

            avg_p, min_p, max_p, volume, trades = stats[0] if stats else (None, None, None, None, None)

            # Build embed with per-unit and per-stack (x64) prices
            embed = discord.Embed(title=f"Price: {name}", color=self.embed_color)

            # Per-unit prices
            embed.add_field(name="Avg", value=format_price(avg_p), inline=True)
            embed.add_field(name="Median", value=format_price(median_p), inline=True)
            embed.add_field(name="Min / Max", value=f"{format_price(min_p)} / {format_price(max_p)}", inline=True)

            # Per-stack prices (x64)
            stack_avg = avg_p * 64 if avg_p else None
            stack_median = median_p * 64 if median_p else None
            stack_min = min_p * 64 if min_p else None
            stack_max = max_p * 64 if max_p else None

            embed.add_field(name="Stack Avg (x64)", value=format_price(stack_avg), inline=True)
            embed.add_field(name="Stack Median", value=format_price(stack_median), inline=True)
            embed.add_field(name="Stack Min / Max", value=f"{format_price(stack_min)} / {format_price(stack_max)}", inline=True)

            # Stats row
            embed.add_field(name="Volume", value=f"{int(volume):,}" if volume else "0", inline=True)
            embed.add_field(name="Trades", value=f"{int(trades):,}" if trades else "0", inline=True)
            embed.add_field(name="Period", value=f"Last {period_days} days", inline=True)

            # Recent history (last 10 daily entries)
            history = await self.db.fetchall(
                """SELECT trade_date, avg_price, total_volume
                   FROM price_summary
                   WHERE item_id = ?
                   ORDER BY trade_date DESC
                   LIMIT 10""",
                (item_id,),
            )
            if history:
                lines = []
                for h_date, h_price, h_vol in history:
                    date_short = h_date[5:]  # MM-DD
                    lines.append(f"`{date_short}` {format_price(h_price)} ({int(h_vol):,} vol)")
                embed.add_field(name="Recent History", value=truncate_for_embed_field("\n".join(lines)), inline=False)

            embed.set_footer(text=f"Item key: {item_key} | Stack prices assume x64")
            await interaction.followup.send(embed=embed, ephemeral=not public)

        except Exception as e:
            self.log.error(f"Error in /price command: {e}", exc_info=True)
            await interaction.followup.send("An error occurred while fetching price data.", ephemeral=True)

    @price.autocomplete("item")
    async def price_autocomplete(self, interaction: discord.Interaction, current: str):
        return await self.item_autocomplete(interaction, current)

    # ------------------------------------------------------------------
    # /top
    # ------------------------------------------------------------------

    @app_commands.command(name="top", description="View top traded items")
    @app_commands.describe(sort_by="Sort order", public="Show result publicly")
    @app_commands.choices(sort_by=[
        app_commands.Choice(name="Most Trades", value="trades"),
        app_commands.Choice(name="Most Expensive", value="expensive"),
        app_commands.Choice(name="Highest Volume", value="volume"),
    ])
    async def top(self, interaction: discord.Interaction, sort_by: str = "trades", public: bool = False):
        """Show top items by trades, price, or volume."""
        await interaction.response.defer(ephemeral=not public)

        try:
            max_rows = self.top_entries * 3
            order_map = {"expensive": "price DESC", "volume": "vol DESC", "trades": "tc DESC"}
            having = "HAVING tc >= 5" if sort_by == "expensive" else ""

            query = f"""
                SELECT i.display_name, i.search_name, i.item_key,
                       AVG(ps.avg_price) AS price,
                       SUM(ps.total_volume) AS vol,
                       SUM(ps.trade_count) AS tc
                FROM price_summary ps
                JOIN items i ON i.id = ps.item_id
                WHERE i.is_currency = 0
                GROUP BY ps.item_id
                {having}
                ORDER BY {order_map.get(sort_by, "tc DESC")}
                LIMIT ?
            """
            rows = await self.db.fetchall(query, (max_rows,))

            if not rows:
                await interaction.followup.send("No trade data available yet.", ephemeral=True)
                return

            sort_labels = {"trades": "Most Trades", "expensive": "Most Expensive", "volume": "Highest Volume"}
            pages = self._build_pages(
                rows, self.top_entries,
                f"Top Items -- {sort_labels.get(sort_by, sort_by)}",
                lambda i, row: (
                    f"**{i}.** {format_item_name(row[0], row[1])} -- "
                    f"{format_price(row[3])} | {int(row[4]):,} vol | {int(row[5]):,} trades"
                ),
            )
            await self._send_paginated(interaction, pages, ephemeral=not public)

        except Exception as e:
            self.log.error(f"Error in /top command: {e}", exc_info=True)
            await interaction.followup.send("An error occurred while fetching top items.", ephemeral=True)

    # ------------------------------------------------------------------
    # /search
    # ------------------------------------------------------------------

    @app_commands.command(name="search", description="Search for items by name")
    @app_commands.describe(query="Item name to search for", public="Show result publicly")
    async def search(self, interaction: discord.Interaction, query: str, public: bool = False):
        """Search for items and show price info."""
        await interaction.response.defer(ephemeral=not public)

        try:
            escaped_query = self._escape_like(query.lower())
            rows = await self.db.fetchall(
                """SELECT i.item_key, i.display_name, i.search_name,
                          i.plugin_id, i.material_type,
                          AVG(ps.avg_price) AS price,
                          COALESCE(SUM(ps.total_volume), 0) AS vol,
                          COALESCE(SUM(ps.trade_count), 0) AS tc
                   FROM items i
                   LEFT JOIN price_summary ps ON ps.item_id = i.id
                   WHERE i.item_key = ? OR i.search_name LIKE ? ESCAPE '\\'
                   GROUP BY i.id
                   ORDER BY tc DESC
                   LIMIT 25""",
                (query, f"%{escaped_query}%"),
            )

            if not rows:
                await interaction.followup.send(f"No items found matching **{query[:100]}**.", ephemeral=True)
                return

            def _fmt_search(i, row):
                ikey, dname, sname, plugin_id, mat_type, price, vol, tc = row
                name = format_item_name(dname, sname)
                source = plugin_id or mat_type
                return (
                    f"**{name}**\n"
                    f"{format_price(price)} | {int(vol):,} vol | {int(tc):,} trades | `{source}`"
                )

            pages = self._build_pages(
                rows, self.trades_per_page,
                f"Search: {query[:100]}", _fmt_search,
                footer_extra=f"{len(rows)} results",
                separator="\n\n",
            )
            await self._send_paginated(interaction, pages, ephemeral=not public)

        except Exception as e:
            self.log.error(f"Error in /search command: {e}", exc_info=True)
            await interaction.followup.send("An error occurred while searching.", ephemeral=True)

    @search.autocomplete("query")
    async def search_autocomplete(self, interaction: discord.Interaction, current: str):
        return await self.item_autocomplete(interaction, current)

    # ------------------------------------------------------------------
    # /player
    # ------------------------------------------------------------------

    @app_commands.command(name="player", description="View trade stats for a player")
    @app_commands.describe(player="Player name or UUID", public="Show result publicly")
    async def player(self, interaction: discord.Interaction, player: str, public: bool = False):
        """Show trade statistics for a player by name or UUID."""
        await interaction.response.defer(ephemeral=not public)

        try:
            # Resolve player identifier to UUID
            # If it looks like a UUID (contains dashes and is long), use directly
            # Otherwise, look up by name
            if "-" in player and len(player) > 30:
                player_uuid = player
                # Try to get the name from players table
                name_row = await self.db.fetchall(
                    "SELECT last_name FROM players WHERE uuid = ?", (player_uuid,)
                )
                player_name = name_row[0][0] if name_row else None
            else:
                # Look up by name (case-insensitive)
                uuid_row = await self.db.fetchall(
                    "SELECT uuid, last_name FROM players WHERE last_name = ? COLLATE NOCASE",
                    (player,),
                )
                if not uuid_row:
                    await interaction.followup.send(
                        f"No player found with name **{player[:100]}**. Try using their UUID instead.",
                        ephemeral=True,
                    )
                    return
                player_uuid = uuid_row[0][0]
                player_name = uuid_row[0][1]

            # Basic stats
            stats = await self.db.fetchall(
                """SELECT COUNT(*) AS trade_count,
                          SUM(trade_count) AS total_tx,
                          MIN(trade_date) AS first_trade,
                          MAX(trade_date) AS last_trade,
                          COUNT(DISTINCT result_item_id) AS unique_items
                   FROM trades WHERE player_uuid = ?""",
                (player_uuid,),
            )
            trade_count, total_tx, first_trade, last_trade, unique_items = stats[0]

            if not trade_count:
                await interaction.followup.send(f"No trades found for player `{player_uuid}`.", ephemeral=True)
                return

            # Total spent
            spent_row = await self.db.fetchall(
                "SELECT SUM(emerald_cost_total) FROM trades WHERE player_uuid = ? AND emerald_cost_total IS NOT NULL",
                (player_uuid,),
            )
            total_spent = spent_row[0][0] if spent_row and spent_row[0][0] else 0

            # Build title with name if available
            title = f"Player Stats: {player_name}" if player_name else "Player Stats"
            embed = discord.Embed(title=title, color=self.embed_color)
            embed.add_field(name="UUID", value=f"`{player_uuid}`", inline=False)
            embed.add_field(name="Trades", value=f"{trade_count:,}", inline=True)
            embed.add_field(name="Transactions", value=f"{int(total_tx):,}", inline=True)
            embed.add_field(name="Unique Items", value=f"{unique_items:,}", inline=True)
            embed.add_field(name="Total Spent", value=format_price(total_spent), inline=True)
            embed.add_field(name="First Trade", value=first_trade, inline=True)
            embed.add_field(name="Last Trade", value=last_trade, inline=True)

            # Top 10 purchased items by volume (excluding currencies)
            top_items = await self.db.fetchall(
                """SELECT i.display_name, i.search_name,
                          SUM(t.result_amount * t.trade_count) AS vol
                   FROM trades t
                   JOIN items i ON i.id = t.result_item_id
                   WHERE t.player_uuid = ? AND i.is_currency = 0
                   GROUP BY t.result_item_id
                   ORDER BY vol DESC
                   LIMIT 10""",
                (player_uuid,),
            )
            if top_items:
                lines = []
                for dname, sname, vol in top_items:
                    name = format_item_name(dname, sname)
                    lines.append(f"* {name} -- {int(vol):,}")
                embed.add_field(name="Top Purchased Items", value=truncate_for_embed_field("\n".join(lines)), inline=False)

            await interaction.followup.send(embed=embed, ephemeral=not public)

        except Exception as e:
            self.log.error(f"Error in /player command: {e}", exc_info=True)
            await interaction.followup.send("An error occurred while fetching player data.", ephemeral=True)

    @player.autocomplete("player")
    async def player_command_autocomplete(self, interaction: discord.Interaction, current: str):
        return await self.player_autocomplete(interaction, current)

    # ------------------------------------------------------------------
    # /shop
    # ------------------------------------------------------------------

    @app_commands.command(name="shop", description="View trade stats for a shop owner")
    @app_commands.describe(owner="Shop owner name or UUID", public="Show result publicly")
    async def shop(self, interaction: discord.Interaction, owner: str, public: bool = False):
        """Show trade statistics for a shop owner by name or UUID."""
        await interaction.response.defer(ephemeral=not public)

        try:
            # Resolve owner identifier to UUID
            if "-" in owner and len(owner) > 30:
                owner_uuid = owner
                # Try to get the name from players table
                name_row = await self.db.fetchall(
                    "SELECT last_name FROM players WHERE uuid = ?", (owner_uuid,)
                )
                owner_name = name_row[0][0] if name_row else None
            else:
                # Look up by name (case-insensitive)
                uuid_row = await self.db.fetchall(
                    "SELECT uuid, last_name FROM players WHERE last_name = ? COLLATE NOCASE",
                    (owner,),
                )
                if not uuid_row:
                    await interaction.followup.send(
                        f"No player found with name **{owner[:100]}**. Try using their UUID instead.",
                        ephemeral=True,
                    )
                    return
                owner_uuid = uuid_row[0][0]
                owner_name = uuid_row[0][1]

            # Overview stats
            stats = await self.db.fetchall(
                """SELECT COUNT(*) AS trade_count,
                          SUM(trade_count) AS total_tx,
                          COUNT(DISTINCT player_uuid) AS unique_customers,
                          COUNT(DISTINCT shop_uuid) AS shop_count,
                          MIN(trade_date) AS first_trade,
                          MAX(trade_date) AS last_trade
                   FROM trades WHERE shop_owner_uuid = ?""",
                (owner_uuid,),
            )
            trade_count, total_tx, customers, shops, first_trade, last_trade = stats[0]

            if not trade_count:
                await interaction.followup.send(f"No trades found for shop owner `{owner_uuid}`.", ephemeral=True)
                return

            # Total revenue
            rev_row = await self.db.fetchall(
                "SELECT SUM(emerald_cost_total) FROM trades WHERE shop_owner_uuid = ? AND emerald_cost_total IS NOT NULL",
                (owner_uuid,),
            )
            total_revenue = rev_row[0][0] if rev_row and rev_row[0][0] else 0

            # Build title with name if available
            title = f"Shop Owner Stats: {owner_name}" if owner_name else "Shop Owner Stats"
            embed = discord.Embed(title=title, color=self.embed_color)
            embed.add_field(name="Owner UUID", value=f"`{owner_uuid}`", inline=False)
            embed.add_field(name="Trades", value=f"{trade_count:,}", inline=True)
            embed.add_field(name="Transactions", value=f"{int(total_tx):,}", inline=True)
            embed.add_field(name="Unique Customers", value=f"{customers:,}", inline=True)
            embed.add_field(name="Shops", value=f"{shops:,}", inline=True)
            embed.add_field(name="Total Revenue", value=format_price(total_revenue), inline=True)
            embed.add_field(name="First Trade", value=first_trade, inline=True)
            embed.add_field(name="Last Trade", value=last_trade, inline=True)

            # Top 10 sold items
            top_items = await self.db.fetchall(
                """SELECT i.display_name, i.search_name,
                          SUM(t.result_amount * t.trade_count) AS vol
                   FROM trades t
                   JOIN items i ON i.id = t.result_item_id
                   WHERE t.shop_owner_uuid = ? AND i.is_currency = 0
                   GROUP BY t.result_item_id
                   ORDER BY vol DESC
                   LIMIT 10""",
                (owner_uuid,),
            )
            if top_items:
                lines = []
                for dname, sname, vol in top_items:
                    name = format_item_name(dname, sname)
                    lines.append(f"* {name} -- {int(vol):,}")
                embed.add_field(name="Top Sold Items", value=truncate_for_embed_field("\n".join(lines)), inline=False)

            # Shop locations (top 5 by trade count)
            locations = await self.db.fetchall(
                """SELECT shop_world, shop_x, shop_y, shop_z, COUNT(*) AS tc
                   FROM trades
                   WHERE shop_owner_uuid = ?
                   GROUP BY shop_uuid, shop_world, shop_x, shop_y, shop_z
                   ORDER BY tc DESC
                   LIMIT 5""",
                (owner_uuid,),
            )
            if locations:
                loc_lines = []
                for world, x, y, z, tc in locations:
                    loc_lines.append(f"* {world} ({x}, {y}, {z}) -- {tc:,} trades")
                embed.add_field(name="Shop Locations", value=truncate_for_embed_field("\n".join(loc_lines)), inline=False)

            await interaction.followup.send(embed=embed, ephemeral=not public)

        except Exception as e:
            self.log.error(f"Error in /shop command: {e}", exc_info=True)
            await interaction.followup.send("An error occurred while fetching shop data.", ephemeral=True)

    @shop.autocomplete("owner")
    async def shop_command_autocomplete(self, interaction: discord.Interaction, current: str):
        return await self.player_autocomplete(interaction, current)

    # ------------------------------------------------------------------
    # /shopkeepers_stats (Admin only)
    # ------------------------------------------------------------------

    @app_commands.command(name="shopkeepers_stats", description="View database statistics (Admin only)")
    async def shopkeepers_stats(self, interaction: discord.Interaction):
        """Show overall database statistics. Admin only."""
        if not is_admin(interaction, self.admin_role_ids):
            await interaction.response.send_message(
                "You don't have permission to use this command.", ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            # Total trades
            total_trades = (await self.db.fetchall("SELECT COUNT(*) FROM trades"))[0][0]
            # Total items
            total_items = (await self.db.fetchall("SELECT COUNT(*) FROM items"))[0][0]
            # Total unique players
            total_players = (await self.db.fetchall("SELECT COUNT(DISTINCT player_uuid) FROM trades"))[0][0]
            # Total unique owners
            total_owners = (await self.db.fetchall(
                "SELECT COUNT(DISTINCT shop_owner_uuid) FROM trades WHERE shop_owner_uuid IS NOT NULL"
            ))[0][0]
            # Date range
            date_range = (await self.db.fetchall(
                "SELECT MIN(trade_date), MAX(trade_date) FROM trades"
            ))[0]
            # Shop type breakdown
            shop_types = await self.db.fetchall(
                "SELECT shop_type, COUNT(*) FROM trades GROUP BY shop_type ORDER BY COUNT(*) DESC"
            )
            # World breakdown
            worlds = await self.db.fetchall(
                "SELECT shop_world, COUNT(*) FROM trades GROUP BY shop_world ORDER BY COUNT(*) DESC"
            )
            # Imported files
            file_count = (await self.db.fetchall("SELECT COUNT(*) FROM imported_files"))[0][0]
            # Price summary entries
            ps_count = (await self.db.fetchall("SELECT COUNT(*) FROM price_summary"))[0][0]

            # DB file size
            db_size_bytes = self.db.path.stat().st_size
            if db_size_bytes >= 1_048_576:
                db_size = f"{db_size_bytes / 1_048_576:.1f} MB"
            else:
                db_size = f"{db_size_bytes / 1024:.1f} KB"

            embed = discord.Embed(title="Shopkeepers Database Stats", color=self.embed_color)
            embed.add_field(name="Total Trades", value=f"{total_trades:,}", inline=True)
            embed.add_field(name="Total Items", value=f"{total_items:,}", inline=True)
            embed.add_field(name="Unique Players", value=f"{total_players:,}", inline=True)
            embed.add_field(name="Unique Owners", value=f"{total_owners:,}", inline=True)
            embed.add_field(name="Imported Files", value=f"{file_count:,}", inline=True)
            embed.add_field(name="Price Summaries", value=f"{ps_count:,}", inline=True)

            if date_range[0]:
                embed.add_field(name="Date Range", value=f"{date_range[0]} -- {date_range[1]}", inline=False)

            if shop_types:
                type_lines = [f"* {stype}: {count:,}" for stype, count in shop_types]
                embed.add_field(name="Shop Types", value=truncate_for_embed_field("\n".join(type_lines)), inline=True)

            if worlds:
                world_lines = [f"* {world}: {count:,}" for world, count in worlds]
                embed.add_field(name="Worlds", value=truncate_for_embed_field("\n".join(world_lines)), inline=True)

            embed.set_footer(text=f"Database size: {db_size}")
            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            self.log.error(f"Error in /shopkeepers_stats command: {e}", exc_info=True)
            await interaction.followup.send("An error occurred while fetching stats.", ephemeral=True)

    # ------------------------------------------------------------------
    # /economy (Admin only)
    # ------------------------------------------------------------------

    @app_commands.command(name="economy", description="View economy sink/faucet analysis (Admin only)")
    @app_commands.describe(period="Time period to analyze")
    @app_commands.choices(period=[
        app_commands.Choice(name="Last 7 days", value=7),
        app_commands.Choice(name="Last 14 days", value=14),
        app_commands.Choice(name="Last 30 days", value=30),
        app_commands.Choice(name="Last 90 days", value=90),
        app_commands.Choice(name="All time", value=0),
    ])
    async def economy(self, interaction: discord.Interaction, period: int = 30):
        """
        Show economy sink/faucet analysis for admin shops.

        Sinks: Currency spent at admin shops (leaves player economy)
        Faucets: Currency earned from admin shops (enters player economy)
        """
        if not is_admin(interaction, self.admin_role_ids):
            await interaction.response.send_message(
                "You don't have permission to use this command.", ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            # Parameterized date filter
            if period > 0:
                date_clause = "AND t.trade_date >= date('now', ?)"
                date_params = (f"-{period} days",)
                period_label = f"Last {period} days"
            else:
                date_clause = ""
                date_params = ()
                period_label = "All time"

            # Total currency SPENT at admin shops (sinks)
            sink_query = f"""
                SELECT COALESCE(SUM(t.emerald_cost_total), 0), COUNT(*)
                FROM trades t
                JOIN items i ON i.id = t.result_item_id
                WHERE t.shop_type = 'admin'
                  AND t.emerald_cost_total IS NOT NULL
                  AND i.is_currency = 0
                  {date_clause}
            """
            sink_result = await self.db.fetchall(sink_query, date_params)
            total_sunk, sink_trades = sink_result[0] if sink_result else (0, 0)

            # Total currency EARNED from admin shops (faucets)
            # Excludes currency conversion shops (e.g., EMB <-> CEMB)
            faucet_query = f"""
                SELECT COALESCE(SUM(t.result_amount * t.trade_count * ri.emerald_value), 0), COUNT(*)
                FROM trades t
                JOIN items ri ON ri.id = t.result_item_id
                JOIN items i1 ON i1.id = t.item1_id
                WHERE t.shop_type = 'admin'
                  AND ri.is_currency = 1
                  AND i1.is_currency = 0
                  {date_clause}
            """
            faucet_result = await self.db.fetchall(faucet_query, date_params)
            total_fauceted, faucet_trades = faucet_result[0] if faucet_result else (0, 0)

            # Net flow (negative = healthy sink, positive = inflation)
            net_flow = total_fauceted - total_sunk

            # Top 10 sink items (what players BUY most from admin shops)
            top_sinks_query = f"""
                SELECT i.display_name, i.search_name,
                       SUM(t.emerald_cost_total) AS total_cost,
                       SUM(t.result_amount * t.trade_count) AS total_volume,
                       COUNT(*) AS trade_count
                FROM trades t
                JOIN items i ON i.id = t.result_item_id
                WHERE t.shop_type = 'admin'
                  AND t.emerald_cost_total IS NOT NULL
                  AND i.is_currency = 0
                  {date_clause}
                GROUP BY t.result_item_id
                ORDER BY total_cost DESC
                LIMIT 10
            """
            top_sinks = await self.db.fetchall(top_sinks_query, date_params)

            # Top 10 faucet items (what players SELL most to admin shops)
            top_faucets_query = f"""
                SELECT i.display_name, i.search_name,
                       SUM(t.result_amount * t.trade_count * ri.emerald_value) AS total_earned,
                       SUM(t.item1_amount * t.trade_count) AS total_volume,
                       COUNT(*) AS trade_count
                FROM trades t
                JOIN items i ON i.id = t.item1_id
                JOIN items ri ON ri.id = t.result_item_id
                WHERE t.shop_type = 'admin'
                  AND ri.is_currency = 1
                  AND i.is_currency = 0
                  {date_clause}
                GROUP BY t.item1_id
                ORDER BY total_earned DESC
                LIMIT 10
            """
            top_faucets = await self.db.fetchall(top_faucets_query, date_params)

            # Daily breakdown (last 14 days for display)
            daily_query = """
                SELECT
                    t.trade_date,
                    COALESCE(SUM(CASE WHEN ri.is_currency = 0 THEN t.emerald_cost_total ELSE 0 END), 0) AS daily_sink,
                    COALESCE(SUM(CASE WHEN ri.is_currency = 1 AND i1.is_currency = 0 THEN t.result_amount * t.trade_count * ri.emerald_value ELSE 0 END), 0) AS daily_faucet
                FROM trades t
                JOIN items ri ON ri.id = t.result_item_id
                LEFT JOIN items i1 ON i1.id = t.item1_id
                WHERE t.shop_type = 'admin'
                  AND t.trade_date >= date('now', ?)
                GROUP BY t.trade_date
                ORDER BY t.trade_date DESC
            """
            daily_data = await self.db.fetchall(daily_query, ("-14 days",))

            # Build embed
            embed = discord.Embed(
                title="Economy Analysis -- Admin Shops",
                color=self.embed_color,
            )

            # Flow indicators
            if net_flow < 0:
                flow_indicator = "Net Sink (Healthy)"
            elif net_flow > 0:
                flow_indicator = "Net Faucet (Inflation)"
            else:
                flow_indicator = "Balanced"

            embed.add_field(
                name="Currency Sunk",
                value=f"{format_price(total_sunk)}\n({sink_trades:,} trades)",
                inline=True,
            )
            embed.add_field(
                name="Currency Fauceted",
                value=f"{format_price(total_fauceted)}\n({faucet_trades:,} trades)",
                inline=True,
            )
            embed.add_field(
                name="Net Flow",
                value=f"{format_price(abs(net_flow))}\n{flow_indicator}",
                inline=True,
            )

            # Top sinks
            if top_sinks:
                sink_lines = []
                for dname, sname, cost, vol, tc in top_sinks[:5]:
                    name = format_item_name(dname, sname)
                    sink_lines.append(f"* {name}: {format_price(cost)}")
                embed.add_field(
                    name="Top Sinks (Players Buy)",
                    value=truncate_for_embed_field("\n".join(sink_lines)),
                    inline=True,
                )

            # Top faucets
            if top_faucets:
                faucet_lines = []
                for dname, sname, earned, vol, tc in top_faucets[:5]:
                    name = format_item_name(dname, sname)
                    faucet_lines.append(f"* {name}: {format_price(earned)}")
                embed.add_field(
                    name="Top Faucets (Players Sell)",
                    value=truncate_for_embed_field("\n".join(faucet_lines)),
                    inline=True,
                )

            # Daily trend
            if daily_data:
                trend_lines = []
                for date, daily_sink, daily_faucet in daily_data[:7]:
                    date_short = date[5:]  # MM-DD
                    net = daily_faucet - daily_sink
                    arrow = "v" if net < 0 else "^" if net > 0 else "="
                    trend_lines.append(
                        f"`{date_short}` {arrow} S:{format_price(daily_sink)} F:{format_price(daily_faucet)}"
                    )
                embed.add_field(
                    name="Daily Trend (Last 7 Days)",
                    value=truncate_for_embed_field("\n".join(trend_lines)),
                    inline=False,
                )

            embed.set_footer(text=f"Period: {period_label} | S=Sink, F=Faucet")
            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            self.log.error(f"Error in /economy command: {e}", exc_info=True)
            await interaction.followup.send("An error occurred while fetching economy data.", ephemeral=True)

    # ------------------------------------------------------------------
    # /sinks (Admin only)
    # ------------------------------------------------------------------

    @app_commands.command(name="sinks", description="View detailed sink items from admin shops (Admin only)")
    @app_commands.describe(period="Time period to analyze")
    @app_commands.choices(period=[
        app_commands.Choice(name="Last 7 days", value=7),
        app_commands.Choice(name="Last 14 days", value=14),
        app_commands.Choice(name="Last 30 days", value=30),
        app_commands.Choice(name="All time", value=0),
    ])
    async def sinks(self, interaction: discord.Interaction, period: int = 30):
        """Show detailed breakdown of currency sinks (items players buy from admin shops)."""
        if not is_admin(interaction, self.admin_role_ids):
            await interaction.response.send_message(
                "You don't have permission to use this command.", ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            if period > 0:
                date_clause = "AND t.trade_date >= date('now', ?)"
                date_params = (f"-{period} days",)
                period_label = f"Last {period} days"
            else:
                date_clause = ""
                date_params = ()
                period_label = "All time"

            query = f"""
                SELECT i.display_name, i.search_name, i.item_key,
                       SUM(t.emerald_cost_total) AS total_cost,
                       SUM(t.result_amount * t.trade_count) AS total_volume,
                       COUNT(*) AS trade_count,
                       AVG(t.emerald_cost_per_unit) AS avg_price
                FROM trades t
                JOIN items i ON i.id = t.result_item_id
                WHERE t.shop_type = 'admin'
                  AND t.emerald_cost_total IS NOT NULL
                  AND i.is_currency = 0
                  {date_clause}
                GROUP BY t.result_item_id
                ORDER BY total_cost DESC
                LIMIT 30
            """
            rows = await self.db.fetchall(query, date_params)

            if not rows:
                await interaction.followup.send(
                    f"No sink data found for {period_label.lower()}.", ephemeral=True
                )
                return

            total_sunk = sum(row[3] for row in rows)

            def _fmt_sink(i, row):
                dname, sname, ikey, cost, vol, tc, avg = row
                name = format_item_name(dname, sname)
                pct = (cost / total_sunk * 100) if total_sunk > 0 else 0
                source = f" `{ikey}`" if not ikey.startswith("minecraft:") or ikey.count(":") > 1 else ""
                return (
                    f"**{i}.** {name}{source}\n"
                    f"   {format_price(cost)} ({pct:.1f}%) | "
                    f"{int(vol):,} items | {tc:,} trades"
                )

            pages = self._build_pages(
                rows, self.top_entries,
                "Economy Sinks -- Admin Shops", _fmt_sink,
                footer_extra=f"{period_label} | Total: {format_price(total_sunk)}",
            )
            await self._send_paginated(interaction, pages)

        except Exception as e:
            self.log.error(f"Error in /sinks command: {e}", exc_info=True)
            await interaction.followup.send("An error occurred while fetching sink data.", ephemeral=True)

    # ------------------------------------------------------------------
    # /faucets (Admin only)
    # ------------------------------------------------------------------

    @app_commands.command(name="faucets", description="View detailed faucet items from admin shops (Admin only)")
    @app_commands.describe(period="Time period to analyze")
    @app_commands.choices(period=[
        app_commands.Choice(name="Last 7 days", value=7),
        app_commands.Choice(name="Last 14 days", value=14),
        app_commands.Choice(name="Last 30 days", value=30),
        app_commands.Choice(name="All time", value=0),
    ])
    async def faucets(self, interaction: discord.Interaction, period: int = 30):
        """Show detailed breakdown of currency faucets (items players sell to admin shops)."""
        if not is_admin(interaction, self.admin_role_ids):
            await interaction.response.send_message(
                "You don't have permission to use this command.", ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            if period > 0:
                date_clause = "AND t.trade_date >= date('now', ?)"
                date_params = (f"-{period} days",)
                period_label = f"Last {period} days"
            else:
                date_clause = ""
                date_params = ()
                period_label = "All time"

            query = f"""
                SELECT i.display_name, i.search_name, i.item_key,
                       SUM(t.result_amount * t.trade_count * ri.emerald_value) AS total_earned,
                       SUM(t.item1_amount * t.trade_count) AS total_volume,
                       COUNT(*) AS trade_count,
                       AVG(t.result_amount * ri.emerald_value / NULLIF(t.item1_amount, 0)) AS avg_price_per_item
                FROM trades t
                JOIN items i ON i.id = t.item1_id
                JOIN items ri ON ri.id = t.result_item_id
                WHERE t.shop_type = 'admin'
                  AND ri.is_currency = 1
                  AND i.is_currency = 0
                  {date_clause}
                GROUP BY t.item1_id
                ORDER BY total_earned DESC
                LIMIT 30
            """
            rows = await self.db.fetchall(query, date_params)

            if not rows:
                await interaction.followup.send(
                    f"No faucet data found for {period_label.lower()}.", ephemeral=True
                )
                return

            total_fauceted = sum(row[3] for row in rows if row[3])

            def _fmt_faucet(i, row):
                dname, sname, ikey, earned, vol, tc, avg = row
                name = format_item_name(dname, sname)
                earned = earned or 0
                pct = (earned / total_fauceted * 100) if total_fauceted > 0 else 0
                source = f" `{ikey}`" if not ikey.startswith("minecraft:") or ikey.count(":") > 1 else ""
                return (
                    f"**{i}.** {name}{source}\n"
                    f"   {format_price(earned)} ({pct:.1f}%) | "
                    f"{int(vol):,} items | {tc:,} trades"
                )

            pages = self._build_pages(
                rows, self.top_entries,
                "Economy Faucets -- Admin Shops", _fmt_faucet,
                footer_extra=f"{period_label} | Total: {format_price(total_fauceted)}",
            )
            await self._send_paginated(interaction, pages)

        except Exception as e:
            self.log.error(f"Error in /faucets command: {e}", exc_info=True)
            await interaction.followup.send("An error occurred while fetching faucet data.", ephemeral=True)

    # ------------------------------------------------------------------
    # /trending
    # ------------------------------------------------------------------

    @app_commands.command(name="trending", description="View items with biggest price changes (compares recent half vs prior half)")
    @app_commands.describe(
        period="Time window split in half for comparison (e.g. 14d = last 7d vs prior 7d)",
        direction="Show price increases, decreases, or both",
        public="Show result publicly",
    )
    @app_commands.choices(period=[
        app_commands.Choice(name="Last 7 days", value=7),
        app_commands.Choice(name="Last 14 days", value=14),
        app_commands.Choice(name="Last 30 days", value=30),
    ])
    @app_commands.choices(direction=[
        app_commands.Choice(name="Both (biggest changes)", value="both"),
        app_commands.Choice(name="Rising (price increases)", value="up"),
        app_commands.Choice(name="Falling (price decreases)", value="down"),
    ])
    async def trending(
        self,
        interaction: discord.Interaction,
        period: int = 7,
        direction: str = "both",
        public: bool = False,
    ):
        """Show items with the biggest price changes."""
        await interaction.response.defer(ephemeral=not public)

        try:
            # Compare average price in recent period vs previous period
            # Recent: last N days, Previous: N days before that
            half_period = period // 2

            query = """
                WITH recent AS (
                    SELECT item_id, AVG(avg_price) AS recent_avg, SUM(trade_count) AS recent_trades
                    FROM price_summary
                    WHERE trade_date >= date('now', ?)
                    GROUP BY item_id
                    HAVING recent_trades >= 3
                ),
                previous AS (
                    SELECT item_id, AVG(avg_price) AS prev_avg
                    FROM price_summary
                    WHERE trade_date >= date('now', ?) AND trade_date < date('now', ?)
                    GROUP BY item_id
                )
                SELECT i.display_name, i.search_name, i.item_key,
                       r.recent_avg, p.prev_avg,
                       r.recent_trades,
                       ((r.recent_avg - p.prev_avg) / p.prev_avg) * 100 AS pct_change
                FROM recent r
                JOIN previous p ON r.item_id = p.item_id
                JOIN items i ON i.id = r.item_id
                WHERE i.is_currency = 0 AND p.prev_avg > 0
            """

            if direction == "up":
                query += " AND pct_change > 0 ORDER BY pct_change DESC LIMIT 20"
            elif direction == "down":
                query += " AND pct_change < 0 ORDER BY pct_change ASC LIMIT 20"
            else:
                query += " ORDER BY ABS(pct_change) DESC LIMIT 20"

            rows = await self.db.fetchall(
                query,
                (f"-{half_period} days", f"-{period} days", f"-{half_period} days"),
            )

            if not rows:
                await interaction.followup.send(
                    f"Not enough price data to calculate trends for the last {period} days.",
                    ephemeral=True,
                )
                return

            direction_labels = {
                "both": "Biggest Changes",
                "up": "Rising Prices",
                "down": "Falling Prices",
            }

            def _fmt_trend(i, row):
                dname, sname, ikey, recent, prev, trades, pct = row
                name = format_item_name(dname, sname)
                arrow = "^" if pct > 0 else "v"
                sign = "+" if pct > 0 else ""
                return (
                    f"**{i}.** {arrow} {name}\n"
                    f"   {format_price(prev)} -> {format_price(recent)} ({sign}{pct:.1f}%)"
                )

            pages = self._build_pages(
                rows, self.top_entries,
                f"Trending Items -- {direction_labels.get(direction)}",
                _fmt_trend,
                footer_extra=f"Last {period} days",
            )
            await self._send_paginated(interaction, pages, ephemeral=not public)

        except Exception as e:
            self.log.error(f"Error in /trending command: {e}", exc_info=True)
            await interaction.followup.send("An error occurred while fetching trending data.", ephemeral=True)

    # ------------------------------------------------------------------
    # /shops (Leaderboard)
    # ------------------------------------------------------------------

    @app_commands.command(name="shops", description="View top shop owner leaderboard")
    @app_commands.describe(sort_by="Sort order", public="Show result publicly")
    @app_commands.choices(sort_by=[
        app_commands.Choice(name="Revenue (emeralds earned)", value="revenue"),
        app_commands.Choice(name="Trade Count", value="trades"),
        app_commands.Choice(name="Unique Customers", value="customers"),
    ])
    async def shops(self, interaction: discord.Interaction, sort_by: str = "revenue", public: bool = False):
        """Show leaderboard of top shop owners."""
        await interaction.response.defer(ephemeral=not public)

        try:
            max_rows = self.top_entries * 3
            order_map = {"revenue": "revenue DESC", "customers": "customers DESC", "trades": "trade_count DESC"}
            extra_where = "AND t.emerald_cost_total IS NOT NULL" if sort_by == "revenue" else ""

            query = f"""
                SELECT t.shop_owner_uuid,
                       p.last_name,
                       SUM(t.emerald_cost_total) AS revenue,
                       COUNT(*) AS trade_count,
                       COUNT(DISTINCT t.player_uuid) AS customers
                FROM trades t
                LEFT JOIN players p ON p.uuid = t.shop_owner_uuid
                WHERE t.shop_owner_uuid IS NOT NULL {extra_where}
                GROUP BY t.shop_owner_uuid
                ORDER BY {order_map.get(sort_by, "trade_count DESC")}
                LIMIT ?
            """
            rows = await self.db.fetchall(query, (max_rows,))

            if not rows:
                await interaction.followup.send("No shop owner data available yet.", ephemeral=True)
                return

            sort_labels = {"revenue": "Top Revenue", "trades": "Most Trades", "customers": "Most Customers"}
            pages = self._build_pages(
                rows, self.top_entries,
                f"Shop Leaderboard -- {sort_labels.get(sort_by)}",
                lambda i, row: (
                    f"**{i}.** {row[1] if row[1] else row[0][:8] + '...'}\n"
                    f"   {format_price(row[2]) if row[2] else 'N/A'} | "
                    f"{row[3]:,} trades | {row[4]:,} customers"
                ),
            )
            await self._send_paginated(interaction, pages, ephemeral=not public)

        except Exception as e:
            self.log.error(f"Error in /shops command: {e}", exc_info=True)
            await interaction.followup.send("An error occurred while fetching shop leaderboard.", ephemeral=True)

    # ------------------------------------------------------------------
    # /players (Leaderboard)
    # ------------------------------------------------------------------

    @app_commands.command(name="players", description="View top player leaderboard")
    @app_commands.describe(sort_by="Sort order", public="Show result publicly")
    @app_commands.choices(sort_by=[
        app_commands.Choice(name="Spending (emeralds spent)", value="spending"),
        app_commands.Choice(name="Trade Count", value="trades"),
        app_commands.Choice(name="Unique Items", value="items"),
    ])
    async def players_leaderboard(self, interaction: discord.Interaction, sort_by: str = "spending", public: bool = False):
        """Show leaderboard of top players/buyers."""
        await interaction.response.defer(ephemeral=not public)

        try:
            max_rows = self.top_entries * 3
            order_map = {"spending": "spending DESC", "items": "unique_items DESC", "trades": "trade_count DESC"}
            extra_where = "WHERE t.emerald_cost_total IS NOT NULL" if sort_by == "spending" else ""

            query = f"""
                SELECT t.player_uuid,
                       p.last_name,
                       SUM(t.emerald_cost_total) AS spending,
                       COUNT(*) AS trade_count,
                       COUNT(DISTINCT t.result_item_id) AS unique_items
                FROM trades t
                LEFT JOIN players p ON p.uuid = t.player_uuid
                {extra_where}
                GROUP BY t.player_uuid
                ORDER BY {order_map.get(sort_by, "trade_count DESC")}
                LIMIT ?
            """
            rows = await self.db.fetchall(query, (max_rows,))

            if not rows:
                await interaction.followup.send("No player data available yet.", ephemeral=True)
                return

            sort_labels = {"spending": "Top Spenders", "trades": "Most Trades", "items": "Most Unique Items"}
            pages = self._build_pages(
                rows, self.top_entries,
                f"Player Leaderboard -- {sort_labels.get(sort_by)}",
                lambda i, row: (
                    f"**{i}.** {row[1] if row[1] else row[0][:8] + '...'}\n"
                    f"   {format_price(row[2]) if row[2] else 'N/A'} | "
                    f"{row[3]:,} trades | {row[4]:,} items"
                ),
            )
            await self._send_paginated(interaction, pages, ephemeral=not public)

        except Exception as e:
            self.log.error(f"Error in /players command: {e}", exc_info=True)
            await interaction.followup.send("An error occurred while fetching player leaderboard.", ephemeral=True)
