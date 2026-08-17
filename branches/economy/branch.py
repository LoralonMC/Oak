"""
Economy Branch - Main Module
Thin API client for OakheartWeb economy data. All data comes from the plugin's
REST API — no local database.
"""

import asyncio
import re
import time
import urllib.parse
from collections import OrderedDict

import aiohttp

import discord
from discord import app_commands
from discord.ext import commands

from oak import OakBranch
from oak.context import BranchContext
from oak.constants import EMBED_DESCRIPTION_MAX, EMBED_TITLE_MAX

from .views import PaginatedEmbedView

# Bound on the in-memory response cache. Autocomplete generates a unique key
# per keystroke (q=d, q=di, q=dia, ...) so without a cap the dict grows
# forever during normal use.
_API_CACHE_MAX_ENTRIES = 500

# Default configuration for this branch
DEFAULT_CONFIG = {
    "enabled": True,
    "version": "1.0.0",
    "settings": {
        "api_url": "",
        "api_key": "",
        "ui": {
            "embed_color": 0x545D4D,
            "entries_per_page": 10,
            "default_period": 7,
        },
        "cache": {
            "default_ttl": 30,    # Hot endpoints (/price, /search, /top per category)
            "overview_ttl": 60,   # Stable aggregates (/economy, /health, /trending, /anomalies)
        },
        "admin_role_ids": [],
    },
}

# Legacy section-sign colour codes, stripped from display names defensively.
_LEGACY_COLOR_RE = re.compile("§.")

# Emeralds in one emerald block. The API stores every price as a raw emerald
# count (blocks already multiplied out), so this is only ever used to convert
# back for display.
EMERALDS_PER_BLOCK = 9

# Currency display config
CURRENCIES = {
    "emerald": {"name": "Emerald", "emoji": "🟢"},
    "vote_token": {"name": "Vote Token", "emoji": "🪙"},
    "crate_crystal": {"name": "Crate Crystal", "emoji": "💎"},
}


class Economy(OakBranch):
    """Economy dashboard powered by OakheartWeb API."""

    def __init__(self, ctx: BranchContext):
        super().__init__(ctx)
        self.api_url = self.setting("api_url", default="")
        self.api_key = self.setting("api_key", default="")
        self.embed_color = self.setting("ui", "embed_color", default=0x545D4D)
        self.per_page = self.setting("ui", "entries_per_page", default=10)
        self.default_period = self.setting("ui", "default_period", default=7)
        self.admin_role_ids = self.setting("admin_role_ids", default=[])
        self._cache_default_ttl = self.setting("cache", "default_ttl", default=30)
        self._cache_overview_ttl = self.setting("cache", "overview_ttl", default=60)
        self._session: aiohttp.ClientSession | None = None
        # OrderedDict gives us LRU eviction via move_to_end / popitem(last=False).
        self._api_cache: OrderedDict[tuple, tuple[float, dict]] = OrderedDict()

    async def on_enable(self):
        self._session = aiohttp.ClientSession()
        if not self.api_url:
            self.log.warning("api_url not configured — economy commands will fail")

    async def on_disable(self):
        if self._session:
            await self._session.close()
            self._session = None
        # Drop cached responses so a hot reload starts with a clean slate
        # instead of serving stale data from the previous instance.
        self._api_cache.clear()

    # ------------------------------------------------------------------
    # API Client
    # ------------------------------------------------------------------

    async def api_get(
        self,
        path: str,
        params: dict | None = None,
        ttl: int | None = None,
        timeout: float = 10.0,
        namespace: str = "economy",
    ) -> dict | None:
        """GET request to OakheartWeb API with in-memory caching.

        ``namespace`` selects the API family — ``"economy"`` for
        ``/api/economy/...`` (the default), or ``"player"`` for the core
        ``/api/player/<name>`` lookup used to turn a name into a UUID.

        ``path`` is the endpoint path (e.g. ``"item/diamond"``); user-supplied
        path segments must be URL-encoded by the caller via
        ``urllib.parse.quote(value, safe="")``. ``params`` is sent as the query
        string so aiohttp handles encoding — never interpolate raw user input
        into the path or query.

        ``timeout`` is the total request budget in seconds. Autocomplete must
        pass a short value (≤ 2s) — Discord invalidates the interaction after
        ~3s, so anything longer means the autocomplete reply gets a 404
        ``Unknown interaction`` even when the API does eventually answer.

        Returns parsed JSON or None on error. The ``ttl`` arg overrides the
        default cache TTL — pass ``self._cache_overview_ttl`` for slow-changing
        endpoints like overview / trending / anomalies.
        """
        if not self._session or not self.api_url:
            return None
        cache_ttl = ttl if ttl is not None else self._cache_default_ttl
        # Cache key includes namespace + params so different queries don't collide
        cache_key = (namespace, path, tuple(sorted(params.items())) if params else ())
        now = time.monotonic()
        if cache_ttl > 0:
            cached = self._api_cache.get(cache_key)
            if cached and (now - cached[0]) < cache_ttl:
                self._api_cache.move_to_end(cache_key)
                return cached[1]
        try:
            url = f"{self.api_url}/api/{namespace}/{path}"
            headers = {"Authorization": f"Bearer {self.api_key}"}
            async with self._session.get(
                url,
                headers=headers,
                params=params,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if cache_ttl > 0:
                        self._api_cache[cache_key] = (now, data)
                        self._api_cache.move_to_end(cache_key)
                        # LRU eviction — keep memory bounded under autocomplete pressure
                        while len(self._api_cache) > _API_CACHE_MAX_ENTRIES:
                            self._api_cache.popitem(last=False)
                    return data
                self.log.warning(f"API {resp.status}: {path}")
                return None
        except asyncio.TimeoutError:
            # aiohttp's TimeoutError has an empty str(); make the log useful.
            self.log.warning(f"API timeout ({path}) after {timeout:.1f}s")
            return None
        except Exception as e:
            # Log type + message separately so a future ContentTypeError with
            # an echoed Authorization header in the body doesn't leak via the
            # default __str__.
            self.log.error(f"API error ({path}): {type(e).__name__}: {e}")
            return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_pages(
        self, rows: list, per_page: int, title: str, format_row, footer_extra: str = "",
    ) -> list[discord.Embed]:
        """Build paginated embeds from a list of data rows."""
        pages = []
        for page_start in range(0, len(rows), per_page):
            page_rows = rows[page_start:page_start + per_page]
            lines = [format_row(i, row) for i, row in enumerate(page_rows, start=page_start + 1)]
            page_num = (page_start // per_page) + 1
            total_pages = -(-len(rows) // per_page)
            description = "\n".join(lines)
            if len(description) > EMBED_DESCRIPTION_MAX:
                description = description[:EMBED_DESCRIPTION_MAX - 3] + "..."
            embed = discord.Embed(title=title, description=description, color=self.embed_color)
            footer = f"Page {page_num}/{total_pages}"
            if footer_extra:
                footer += f" • {footer_extra}"
            embed.set_footer(text=footer)
            pages.append(embed)
        return pages

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

    def _fmt(self, n) -> str:
        """Format a number with commas, or '--' if None."""
        if n is None:
            return "--"
        return f"{int(n):,}"

    def _period_label(self, period: int) -> str:
        return "All time" if period == 0 else f"Last {period} days"

    def _emeralds(self, value) -> str:
        """Render a raw emerald amount in the abbreviations players use in game.

        The API normalises every emerald block to 9 emeralds before it does any
        maths, so prices arrive as a single raw emerald figure — but shop signs
        are written in blocks, so 28.17 has to be read back as "3 blocks and a
        bit". EM/EMS is one/many emeralds, EMB/EMBS one/many blocks.
        """
        try:
            value = float(value)
        except (TypeError, ValueError):
            return "--"

        blocks, remainder = divmod(abs(value), EMERALDS_PER_BLOCK)
        blocks = int(blocks)
        # Round first, then carry: 8.999 must read as one block, not "8.999 EMS".
        remainder = round(remainder, 2)
        if remainder >= EMERALDS_PER_BLOCK:
            blocks += 1
            remainder = 0.0

        parts = []
        if blocks:
            parts.append(f"{blocks:,} {'EMB' if blocks == 1 else 'EMBS'}")
        if remainder or not parts:
            # %g drops trailing zeros: 1.17 -> "1.17", 1.0 -> "1"
            shown = f"{remainder:g}"
            parts.append(f"{shown} {'EM' if remainder == 1 else 'EMS'}")

        sign = "-" if value < 0 else ""
        return sign + " ".join(parts)

    def _plain(self, text) -> str:
        """Strip legacy section-sign colour codes from an API-supplied string."""
        if not text:
            return ""
        return _LEGACY_COLOR_RE.sub("", str(text)).strip()

    def _item_name(self, row: dict | None) -> str:
        """Human-readable item name.

        The API only sends ``displayName`` for items that carry a custom name —
        vanilla rows omit the key entirely, so falling straight through to the
        raw key printed things like ``minecraft:emerald``. Prettify the key
        instead. Colours live in a separate ``nameColor`` field, so display
        names arrive clean; the legacy-code strip is belt-and-braces.
        """
        if not isinstance(row, dict):
            return "?"
        name = row.get("displayName")
        if name:
            return _LEGACY_COLOR_RE.sub("", name).strip() or row.get("itemKey", "?")
        key = row.get("itemKey")
        if not key:
            return "?"
        tail = key.split(":", 1)[-1]
        return tail.replace("_", " ").title() if tail else key

    def _item_stack(self, row: dict | None) -> str:
        """``6x Emerald`` — an item with its trade amount."""
        if not isinstance(row, dict):
            return "?"
        amount = int(row.get("amount", 1) or 1)
        return f"{amount:,}x {self._item_name(row)}"

    def _rel_time(self, ms) -> str:
        """Discord relative timestamp from an epoch-millisecond value."""
        try:
            return f"<t:{int(ms) // 1000}:R>"
        except (TypeError, ValueError):
            return ""

    async def _resolve_player(self, name: str) -> tuple[str, str] | None:
        """Resolve a player name to ``(uuid, name)`` via the core player API.

        The economy ``player/<id>`` endpoint keys strictly on UUID: handed a
        name it matches no rows and returns a well-formed all-zero body, which
        rendered as a real embed full of zeros. Resolve first so an unknown
        name is reported as unknown.
        """
        data = await self.api_get(
            urllib.parse.quote(name, safe=""),
            namespace="player",
            ttl=self._cache_overview_ttl,
        )
        if not data or not data.get("uuid"):
            return None
        return data["uuid"], data.get("name", name)

    # ------------------------------------------------------------------
    # Staff gate
    # ------------------------------------------------------------------

    def _is_staff(self, interaction: discord.Interaction) -> bool:
        """True if the invoker has Discord Administrator perm OR a configured admin role."""
        perms = getattr(interaction.user, "guild_permissions", None)
        if perms and perms.administrator:
            return True
        if not self.admin_role_ids:
            return False
        user_role_ids = {r.id for r in getattr(interaction.user, "roles", [])}
        return any(rid in user_role_ids for rid in self.admin_role_ids)

    async def _require_staff(self, interaction: discord.Interaction) -> bool:
        """Refuse non-staff with an ephemeral message. Returns True if user passes."""
        if self._is_staff(interaction):
            return True
        if interaction.response.is_done():
            await interaction.followup.send("This command is staff-only.", ephemeral=True)
        else:
            await interaction.response.send_message("This command is staff-only.", ephemeral=True)
        return False

    # ------------------------------------------------------------------
    # Autocomplete (uses /api/economy/search)
    # ------------------------------------------------------------------

    async def item_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete for item searches via OakheartWeb API.

        Discord invalidates an autocomplete interaction after ~3 seconds.
        We use a short API timeout and skip caching (ttl=0) so a stale slow
        response never blocks fresh keystrokes — if the API is unhealthy,
        autocomplete silently returns no suggestions rather than spamming
        "Unknown interaction" 404s.
        """
        try:
            if not current.strip():
                return []
            data = await self.api_get(
                "search",
                params={"q": current, "limit": 25},
                ttl=0,
                timeout=2.0,
            )
            if not data or not data.get("results"):
                return []
            choices = []
            for item in data["results"]:
                name = self._item_name(item)
                key = item["itemKey"]
                # Discord limits choice name to 100 chars
                choices.append(app_commands.Choice(name=name[:100], value=key[:100]))
            return choices
        except Exception as e:
            self.log.error(f"Item autocomplete error: {e}")
            return []

    # ------------------------------------------------------------------
    # /economy
    # ------------------------------------------------------------------

    @app_commands.command(name="economy", description="[Staff] View overall economy stats")
    @app_commands.describe(period="Time period in days (0 = all time)", public="Show result publicly")
    async def economy_cmd(self, interaction: discord.Interaction, period: int = None, public: bool = False):
        """[Staff] Show overall economy overview."""
        if not await self._require_staff(interaction):
            return
        await interaction.response.defer(ephemeral=not public)
        p = period if period is not None else self.default_period

        data = await self.api_get("overview", params={"period": p}, ttl=self._cache_overview_ttl)
        if not data:
            await interaction.followup.send("Could not fetch economy data.", ephemeral=True)
            return

        embed = discord.Embed(title="Economy Overview", color=self.embed_color)
        embed.add_field(name="Total Trades", value=self._fmt(data.get("totalTrades")), inline=True)
        embed.add_field(name="Unique Traders", value=self._fmt(data.get("uniqueTraders")), inline=True)
        embed.add_field(name="Unique Items", value=self._fmt(data.get("uniqueItems")), inline=True)
        embed.add_field(name="Admin Trades", value=self._fmt(data.get("adminTrades")), inline=True)
        embed.add_field(name="Player Trades", value=self._fmt(data.get("playerTrades")), inline=True)
        embed.add_field(name="Crate Opens", value=self._fmt(data.get("totalCrateOpens")), inline=True)

        # Currency flow. The API nests one object per currency
        # ({"emerald": {"totalIn": ..., "totalOut": ..., "net": ...}}); the old
        # flat "<currency>_in" lookup missed every key and reported 0/0/0.
        flow = data.get("currencyFlow", {})
        for key, info in CURRENCIES.items():
            cur = flow.get(key)
            if not isinstance(cur, dict):
                continue
            inflow = int(cur.get("totalIn", 0))
            outflow = int(cur.get("totalOut", 0))
            net = int(cur.get("net", inflow - outflow))
            traded = int(cur.get("traded", 0))
            sign = "+" if net >= 0 else ""
            embed.add_field(
                name=f"{info['emoji']} {info['name']}",
                value=(
                    f"In: {inflow:,}\nOut: {outflow:,}\n"
                    f"Net: {sign}{net:,}\nTraded: {traded:,}"
                ),
                inline=True,
            )

        velocity = data.get("emeraldVelocity")
        if velocity is not None:
            embed.add_field(name="Emerald Velocity", value=f"{float(velocity):.2f}", inline=True)

        embed.set_footer(text=self._period_label(p))
        await interaction.followup.send(embed=embed, ephemeral=not public)

    # ------------------------------------------------------------------
    # /price
    # ------------------------------------------------------------------

    @app_commands.command(name="price", description="Check trade stats for an item")
    @app_commands.describe(item="Item to look up", period="Time period in days (0 = all time)", public="Show result publicly")
    async def price(self, interaction: discord.Interaction, item: str, period: int = None, public: bool = False):
        """Show trade statistics for an item."""
        await interaction.response.defer(ephemeral=not public)
        p = period if period is not None else self.default_period

        # shopType=PLAYER keeps the admin shops at spawn out of the price, the
        # counts and the shop list — they're fixed-price sinks, not market signal.
        data = await self.api_get(
            f"item/{urllib.parse.quote(item, safe='')}",
            params={"period": p, "shopType": "PLAYER"},
        )
        if not data:
            await interaction.followup.send(f"No data found for **{item[:100]}**.", ephemeral=True)
            return

        embed = discord.Embed(title=f"Item: {self._item_name(data)}", color=self.embed_color)

        # Price, in emeralds per unit, from trades where emeralds were the cost.
        priced = int(data.get("pricedTrades", 0) or 0)
        if priced:
            avg = float(data.get("avgPrice", 0))
            low = float(data.get("minPrice", 0))
            high = float(data.get("maxPrice", 0))
            embed.add_field(name="Avg Price", value=f"🟢 {self._emeralds(avg)} each", inline=True)
            range_value = (
                self._emeralds(low) if low == high
                else f"{self._emeralds(low)} – {self._emeralds(high)}"
            )
            embed.add_field(name="Range", value=range_value, inline=True)
            embed.add_field(name="Priced Trades", value=f"{priced:,}", inline=True)
        else:
            embed.add_field(
                name="Avg Price",
                value="No emerald-priced trades in this period",
                inline=False,
            )

        # The API splits trade stats by which side of the trade the item sat on.
        # Shopkeepers models a buy-shop as "player hands over the item, receives
        # emeralds", so the item-as-payment side is players *selling* it — not a
        # separate "used as currency" concept.
        embed.add_field(
            name="Players Bought",
            value=(
                f"{self._fmt(data.get('receivedVolume'))} units\n"
                f"in {self._fmt(data.get('receivedCount'))} trades"
            ),
            inline=True,
        )
        embed.add_field(
            name="Players Sold",
            value=(
                f"{self._fmt(data.get('givenVolume'))} units\n"
                f"in {self._fmt(data.get('givenCount'))} trades"
            ),
            inline=True,
        )
        embed.add_field(name="Material", value=data.get("material") or "--", inline=True)

        plugin = data.get("pluginId", "vanilla")
        if plugin != "vanilla":
            embed.add_field(name="Source", value=plugin, inline=True)

        top_shops = data.get("topShops", [])
        if top_shops:
            lines = [
                f"**{s.get('shopName') or '?'}** ({s.get('ownerName') or s.get('shopType', '?')})"
                f" — {int(s.get('trades', 0)):,} trades, {int(s.get('volume', 0)):,} units"
                for s in top_shops[:5]
            ]
            embed.add_field(name="Top Shops", value="\n".join(lines), inline=False)

        # Only claim the filter applied if the API echoed it back. An older
        # plugin build ignores the unknown param and returns everything, and a
        # footer asserting otherwise would be a lie about the numbers above it.
        footer = self._period_label(p)
        if data.get("shopType") == "PLAYER":
            footer += " • Player shops only"
        embed.set_footer(text=footer)
        await interaction.followup.send(embed=embed, ephemeral=not public)

    @price.autocomplete("item")
    async def price_autocomplete(self, interaction: discord.Interaction, current: str):
        return await self.item_autocomplete(interaction, current)

    # ------------------------------------------------------------------
    # /top
    # ------------------------------------------------------------------

    @app_commands.command(name="top", description="View leaderboards")
    @app_commands.describe(category="What to rank", period="Time period in days (0 = all time)", public="Show result publicly")
    @app_commands.choices(category=[
        app_commands.Choice(name="Items (by volume)", value="items"),
        app_commands.Choice(name="Traders (by trades)", value="traders"),
        app_commands.Choice(name="Shops (by trades)", value="shops"),
    ])
    async def top(self, interaction: discord.Interaction, category: str = "items", period: int = None, public: bool = False):
        """Show top items, traders, or shops."""
        await interaction.response.defer(ephemeral=not public)
        p = period if period is not None else self.default_period

        data = await self.api_get(
            f"top-{urllib.parse.quote(category, safe='')}",
            params={"period": p, "limit": 50},
        )
        if not data:
            await interaction.followup.send("Could not fetch leaderboard data.", ephemeral=True)
            return

        if category == "items":
            rows = data.get("items", [])
            def fmt(rank, row):
                name = self._item_name(row)
                vol = int(row.get("volume", 0))
                trades = int(row.get("tradeCount", 0))
                price = row.get("avgPrice")
                price_part = f", 🟢 {self._emeralds(price)} ea" if price is not None else ""
                return f"**{rank}.** {name} — {vol:,} vol, {trades:,} trades{price_part}"
            title = "Top Traded Items"

        elif category == "traders":
            rows = data.get("traders", [])
            def fmt(rank, row):
                name = row.get("name", "Unknown")
                trades = int(row.get("tradeCount", 0))
                items = int(row.get("uniqueItems", 0))
                return f"**{rank}.** {name} — {trades:,} trades, {items} items"
            title = "Top Traders"

        else:  # shops
            rows = data.get("shops", [])
            def fmt(rank, row):
                name = row.get("ownerName", "Unknown")
                trades = int(row.get("tradeCount", 0))
                customers = int(row.get("uniqueCustomers", 0))
                return f"**{rank}.** {name} — {trades:,} trades, {customers} customers"
            title = "Top Shops"

        pages = self._build_pages(rows, self.per_page, title, fmt, footer_extra=self._period_label(p))
        await self._send_paginated(interaction, pages, ephemeral=not public)

    # ------------------------------------------------------------------
    # /search
    # ------------------------------------------------------------------

    @app_commands.command(name="search", description="Search for items by name")
    @app_commands.describe(query="Item name to search for", public="Show result publicly")
    async def search(self, interaction: discord.Interaction, query: str, public: bool = False):
        """Search for items by name or key."""
        await interaction.response.defer(ephemeral=not public)

        data = await self.api_get("search", params={"q": query, "limit": 25})
        if not data or not data.get("results"):
            await interaction.followup.send(f"No items found matching **{query[:100]}**.", ephemeral=True)
            return

        rows = data["results"]
        def fmt(rank, row):
            name = self._item_name(row)
            key = row["itemKey"]
            plugin = row.get("pluginId", "vanilla")
            source = f" `{plugin}`" if plugin != "vanilla" else ""
            return f"**{rank}.** {name}{source}\n　`{key}`"

        pages = self._build_pages(rows, self.per_page, f"Search: {query}"[:EMBED_TITLE_MAX], fmt)
        await self._send_paginated(interaction, pages, ephemeral=not public)

    @search.autocomplete("query")
    async def search_autocomplete(self, interaction: discord.Interaction, current: str):
        return await self.item_autocomplete(interaction, current)

    # ------------------------------------------------------------------
    # /player
    # ------------------------------------------------------------------

    @app_commands.command(name="player", description="View trade stats for a player")
    @app_commands.describe(name="Player name", period="Time period in days (0 = all time)", public="Show result publicly")
    async def player_cmd(self, interaction: discord.Interaction, name: str, period: int = None, public: bool = False):
        """Show trade stats for a player."""
        await interaction.response.defer(ephemeral=not public)
        p = period if period is not None else self.default_period

        # The economy endpoint keys on UUID — a name matches nothing and comes
        # back as a well-formed all-zero body, so resolve the name first.
        resolved = await self._resolve_player(name)
        if not resolved:
            await interaction.followup.send(f"Unknown player **{name[:100]}**.", ephemeral=True)
            return
        uuid, player_name = resolved

        data = await self.api_get(f"player/{urllib.parse.quote(uuid, safe='')}", params={"period": p})
        # `tradeCount=0` is a valid response (known player with no trades in
        # period), so only treat None/missing as "not found".
        if not data or data.get("tradeCount") is None:
            await interaction.followup.send(f"No trade data found for **{name[:100]}**.", ephemeral=True)
            return

        embed = discord.Embed(title=f"Player: {data.get('name') or player_name}", color=self.embed_color)
        embed.add_field(name="Total Trades", value=self._fmt(data.get("tradeCount")), inline=True)
        embed.add_field(name="Unique Items", value=self._fmt(data.get("uniqueItems")), inline=True)

        balance = data.get("emeraldBalance")
        if balance is not None:
            sign = "+" if int(balance) >= 0 else ""
            embed.add_field(name="🟢 Emerald Balance", value=f"{sign}{int(balance):,}", inline=True)
            embed.add_field(name="Spent", value=self._fmt(data.get("emeraldsSpent")), inline=True)
            embed.add_field(name="Received", value=self._fmt(data.get("emeraldsReceived")), inline=True)

        ranks = []
        if data.get("tradeRank"):
            ranks.append(f"Trades: #{int(data['tradeRank']):,}")
        if data.get("emeraldRank"):
            ranks.append(f"Emeralds: #{int(data['emeraldRank']):,}")
        if ranks:
            embed.add_field(name="Ranks", value="\n".join(ranks), inline=True)

        top_items = data.get("topItems", [])
        if top_items:
            lines = []
            for i, item in enumerate(top_items[:5], 1):
                vol = int(item.get("volume", 0))
                lines.append(f"**{i}.** {self._item_name(item)} — {vol:,}")
            embed.add_field(name="Top Items", value="\n".join(lines), inline=False)

        shops = data.get("shopBreakdown", [])
        if shops:
            lines = [
                f"**{s.get('shopName') or '?'}** ({s.get('shopType', '?')}) — {int(s.get('trades', 0)):,} trades"
                for s in shops[:5]
            ]
            embed.add_field(name="Top Shops Used", value="\n".join(lines), inline=False)

        embed.set_footer(text=self._period_label(p))
        await interaction.followup.send(embed=embed, ephemeral=not public)

    # ------------------------------------------------------------------
    # /health
    # ------------------------------------------------------------------

    @app_commands.command(name="cashflow", description="[Staff] View currency flow analysis")
    @app_commands.describe(currency="Currency to analyze", period="Time period in days (0 = all time)", public="Show result publicly")
    @app_commands.choices(currency=[
        app_commands.Choice(name="Emerald", value="emerald"),
        app_commands.Choice(name="Vote Token", value="vote_token"),
        app_commands.Choice(name="Crate Crystal", value="crate_crystal"),
    ])
    async def cashflow(self, interaction: discord.Interaction, currency: str = "emerald", period: int = None, public: bool = False):
        """[Staff] Show currency flow (inflow vs outflow) breakdown."""
        if not await self._require_staff(interaction):
            return
        await interaction.response.defer(ephemeral=not public)
        p = period if period is not None else self.default_period

        data = await self.api_get(
            f"currency/{urllib.parse.quote(currency, safe='')}/flow",
            params={"period": p},
            ttl=self._cache_overview_ttl,
        )
        if not data:
            await interaction.followup.send("Could not fetch currency flow data.", ephemeral=True)
            return

        info = CURRENCIES.get(currency, {"name": currency, "emoji": "💰"})
        embed = discord.Embed(title=f"{info['emoji']} {info['name']} Flow", color=self.embed_color)

        # Event-based flow
        event_in = data.get("eventTotalIn", 0)
        event_out = data.get("eventTotalOut", 0)
        trade_in = data.get("tradeInflow", 0)
        trade_out = data.get("tradeOutflow", 0)

        total_in = event_in + trade_in
        total_out = event_out + trade_out
        net = total_in - total_out
        sign = "+" if net >= 0 else ""

        embed.add_field(name="Total Inflow", value=f"{total_in:,}", inline=True)
        embed.add_field(name="Total Outflow", value=f"{total_out:,}", inline=True)
        embed.add_field(name="Net Flow", value=f"{sign}{net:,}", inline=True)

        # Breakdown by source
        breakdown = data.get("eventBreakdown", [])
        if breakdown:
            in_lines = []
            out_lines = []
            for entry in breakdown:
                etype = entry["eventType"].replace("_", " ").title()
                total = int(entry["total"])
                if entry["direction"] == "in":
                    in_lines.append(f"{etype}: {total:,}")
                else:
                    out_lines.append(f"{etype}: {total:,}")

            if trade_in:
                in_lines.append(f"Shop Trades: {trade_in:,}")
            if trade_out:
                out_lines.append(f"Shop Trades: {trade_out:,}")

            if in_lines:
                embed.add_field(name="Inflow Sources", value="\n".join(in_lines), inline=True)
            if out_lines:
                embed.add_field(name="Outflow Sinks", value="\n".join(out_lines), inline=True)

        embed.set_footer(text=self._period_label(p))
        await interaction.followup.send(embed=embed, ephemeral=not public)

    # ------------------------------------------------------------------
    # /crate
    # ------------------------------------------------------------------

    @app_commands.command(name="crate", description="[Staff] View crate open statistics")
    @app_commands.describe(crate_type="Specific crate type (leave empty for overview)", period="Time period in days (0 = all time)", public="Show result publicly")
    async def crate(self, interaction: discord.Interaction, crate_type: str = None, period: int = None, public: bool = False):
        """[Staff] Show crate open statistics — overview or specific crate type."""
        if not await self._require_staff(interaction):
            return
        await interaction.response.defer(ephemeral=not public)
        p = period if period is not None else self.default_period

        if crate_type:
            data = await self.api_get(
                f"crates/item/{urllib.parse.quote(crate_type, safe='')}",
                params={"period": p},
            )
            if not data:
                await interaction.followup.send(f"No data for crate type **{crate_type[:100]}**.", ephemeral=True)
                return

            embed = discord.Embed(title=f"Crate: {crate_type}", color=self.embed_color)
            embed.add_field(name="Total Opens", value=self._fmt(data.get("totalOpens")), inline=True)

            rewards = data.get("rewards", [])
            if rewards:
                lines = []
                for r in rewards[:10]:
                    name = self._plain(r.get("rewardName")) or "Unknown"
                    won = int(r.get("timesWon", 0))
                    rarity = r.get("rarity", "")
                    rarity_tag = f" `{rarity}`" if rarity else ""
                    lines.append(f"{name}{rarity_tag} — {won:,}x")
                embed.add_field(name="Top Rewards", value="\n".join(lines), inline=False)

            embed.set_footer(text=self._period_label(p))
            await interaction.followup.send(embed=embed, ephemeral=not public)
        else:
            data = await self.api_get("crates/overview", params={"period": p})
            if not data:
                await interaction.followup.send("Could not fetch crate data.", ephemeral=True)
                return

            embed = discord.Embed(title="Crate Overview", color=self.embed_color)

            types = data.get("crateTypes", [])
            if types:
                lines = []
                for t in types:
                    name = t["crateType"]
                    opens = int(t.get("opens", 0))
                    players = int(t.get("uniquePlayers", 0))
                    lines.append(f"**{name}** — {opens:,} opens, {players} players")
                embed.add_field(name="Crate Types", value="\n".join(lines), inline=False)

            top_rewards = data.get("topRewards", [])
            if top_rewards:
                lines = []
                for r in top_rewards[:10]:
                    name = self._plain(r.get("rewardName")) or "Unknown"
                    won = int(r.get("timesWon", 0))
                    lines.append(f"{name} — {won:,}x")
                embed.add_field(name="Top Rewards", value="\n".join(lines), inline=False)

            embed.set_footer(text=self._period_label(p))
            await interaction.followup.send(embed=embed, ephemeral=not public)

    # ------------------------------------------------------------------
    # /recent
    # ------------------------------------------------------------------

    @app_commands.command(name="recent", description="View the most recent trades")
    @app_commands.describe(limit="How many trades to show (1-100, default 25)", public="Show result publicly")
    async def recent(self, interaction: discord.Interaction, limit: int = 25, public: bool = False):
        """Show the most recent trades server-wide."""
        await interaction.response.defer(ephemeral=not public)
        limit = max(1, min(limit, 100))

        # ttl=0 disables caching for /recent — the command name promises freshness
        data = await self.api_get("recent", params={"limit": limit}, ttl=0)
        if not data:
            await interaction.followup.send("Could not fetch recent trades.", ephemeral=True)
            return

        trades = data.get("trades", []) if isinstance(data, dict) else data
        if not trades:
            await interaction.followup.send("No recent trades.", ephemeral=True)
            return

        def fmt(rank, row):
            # A trade row is {playerName, shopName, shopType, item1[, item2],
            # result, timestamp} — the player received `result` and paid with
            # item1 (+ item2 when Shopkeepers split the price over two slots).
            buyer = row.get("playerName", "?")
            got = self._item_stack(row.get("result"))
            cost = [self._item_stack(row["item1"])] if row.get("item1") else []
            if row.get("item2"):
                cost.append(self._item_stack(row["item2"]))
            paid = " + ".join(cost) if cost else "?"
            shop = row.get("shopName") or "?"
            shop_type = row.get("shopType", "")
            when = self._rel_time(row.get("timestamp"))
            return (
                f"**{rank}.** {buyer} — {got} for {paid}\n"
                f"　{shop} · {shop_type} · {when}"
            )

        pages = self._build_pages(trades, self.per_page, "Recent Trades", fmt)
        await self._send_paginated(interaction, pages, ephemeral=not public)

    # ------------------------------------------------------------------
    # /trending
    # ------------------------------------------------------------------

    @app_commands.command(name="trending", description="View hot items right now")
    @app_commands.describe(direction="Rising or falling in volume", public="Show result publicly")
    @app_commands.choices(direction=[
        app_commands.Choice(name="Rising", value="rising"),
        app_commands.Choice(name="Falling", value="falling"),
    ])
    async def trending(self, interaction: discord.Interaction, direction: str = "rising", public: bool = False):
        """Show currently trending items."""
        await interaction.response.defer(ephemeral=not public)

        data = await self.api_get("trending", ttl=self._cache_overview_ttl)
        if not data:
            await interaction.followup.send("Could not fetch trending data.", ephemeral=True)
            return

        # The API splits the payload into "rising" and "falling" — there is no
        # flat "items" list, so the old lookup always reported nothing trending.
        items = data.get(direction, []) if isinstance(data, dict) else []
        if not items:
            await interaction.followup.send(f"No {direction} items right now.", ephemeral=True)
            return

        def fmt(rank, row):
            name = self._item_name(row)
            vol = int(row.get("currentVolume", 0))
            change = int(row.get("change", 0))
            pct = int(row.get("changePercent", 0))
            trades = int(row.get("currentTrades", 0))
            sign = "+" if change >= 0 else ""
            return (
                f"**{rank}.** {name} — {vol:,} vol "
                f"({sign}{change:,}, {sign}{pct}%) · {trades:,} trades"
            )

        icon = "🔥" if direction == "rising" else "📉"
        title = f"{icon} {direction.title()} Items"
        pages = self._build_pages(items, self.per_page, title, fmt, footer_extra=self._period_label(int(data.get("period", 0))))
        await self._send_paginated(interaction, pages, ephemeral=not public)

    # ------------------------------------------------------------------
    # /anomalies [Staff]
    # ------------------------------------------------------------------

    @app_commands.command(name="anomalies", description="[Staff] View flagged suspicious trades")
    @app_commands.describe(public="Show result publicly (default: false)")
    async def anomalies(self, interaction: discord.Interaction, public: bool = False):
        """[Staff] List trades flagged as anomalous."""
        if not await self._require_staff(interaction):
            return
        await interaction.response.defer(ephemeral=not public)

        data = await self.api_get("anomalies", ttl=self._cache_overview_ttl)
        if not data:
            await interaction.followup.send("Could not fetch anomalies.", ephemeral=True)
            return

        anomalies_list = data.get("anomalies", []) if isinstance(data, dict) else data
        if not anomalies_list:
            await interaction.followup.send("No anomalies flagged.", ephemeral=True)
            return

        def fmt(rank, row):
            flag = row.get("type", "unknown")
            player = row.get("playerName", "?")
            # The API calls the human-readable detail "reason"; there is no
            # "details" key, so that line always rendered blank. Rebuild it from
            # the structured fields when they're present so the item reads as a
            # name rather than the raw key the reason string embeds.
            amount, avg = row.get("amount"), row.get("avgAmount")
            if amount is not None and avg is not None:
                reason = f"{int(amount):,}x {self._item_name(row)} (avg {int(avg):,})"
            else:
                reason = self._plain(row.get("reason")) or self._item_name(row)
            shop = row.get("shopName") or "?"
            shop_type = row.get("shopType", "")
            when = self._rel_time(row.get("timestamp"))
            return (
                f"**{rank}.** `{flag}` — {player}: {reason}\n"
                f"　{shop} · {shop_type} · {when}"
            )

        pages = self._build_pages(anomalies_list, self.per_page, "🚨 Flagged Anomalies", fmt)
        await self._send_paginated(interaction, pages, ephemeral=not public)

    # ------------------------------------------------------------------
    # /playershops
    # ------------------------------------------------------------------

    @app_commands.command(name="playershops", description="View player shop owners and their best sellers")
    @app_commands.describe(
        name="Filter to one owner (leave empty for all)",
        period="Time period in days (0 = all time)",
        public="Show result publicly",
    )
    async def playershops(self, interaction: discord.Interaction, name: str = None, period: int = None, public: bool = False):
        """Show player-shop owners ranked by trades, optionally filtered to one owner."""
        await interaction.response.defer(ephemeral=not public)
        p = period if period is not None else self.default_period

        # The endpoint takes no owner filter — it returns the top player-shop
        # owners by trade count (server-capped at 100). Ask for the full 100 and
        # filter here rather than passing a `player` param the API ignores,
        # which used to return everyone's shops under one player's name.
        data = await self.api_get("player-shops", params={"period": p, "limit": 100})
        if not data:
            await interaction.followup.send("Could not fetch player shop data.", ephemeral=True)
            return

        shops = data.get("shops", []) if isinstance(data, dict) else data
        if name:
            wanted = name.casefold()
            shops = [s for s in shops if (s.get("ownerName") or "").casefold() == wanted]
            if not shops:
                await interaction.followup.send(
                    f"No shop trades recorded for **{name[:100]}** in this period "
                    "(only the top 100 owners by trade count are ranked).",
                    ephemeral=True,
                )
                return
        if not shops:
            await interaction.followup.send("No player shop trades in this period.", ephemeral=True)
            return

        def fmt(rank, row):
            owner = row.get("ownerName") or "Unknown"
            trades = int(row.get("tradeCount", 0))
            customers = int(row.get("uniqueCustomers", 0))
            shop_count = int(row.get("shopCount", 0))
            top = ", ".join(self._item_name(i) for i in (row.get("topItems") or [])[:3])
            line = (
                f"**{rank}.** {owner} — {trades:,} trades, "
                f"{customers} customers, {shop_count} shop{'' if shop_count == 1 else 's'}"
            )
            return f"{line}\n　{top}" if top else line

        title = f"{shops[0].get('ownerName')}'s Shops" if name else "Player Shops"
        pages = self._build_pages(
            shops, self.per_page, title[:EMBED_TITLE_MAX], fmt,
            footer_extra=self._period_label(p),
        )
        await self._send_paginated(interaction, pages, ephemeral=not public)
