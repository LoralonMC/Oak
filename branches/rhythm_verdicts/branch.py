"""Chart Verdicts — tells a rhythm chart's author what staff decided.

A chart submitted through the Chart Forge is reviewed by hand, and until this
branch existed the outcome reached the contributor nowhere: the website's own
list only helps someone who thinks to look, and a webhook post lands in a staff
channel the author cannot read.

The website owns delivery state (``rhythm_submissions.notified_at``) rather than
this branch, so a bot restart mid-batch can neither re-send a verdict nor lose
one. This side polls for what it is still owed, DMs it, and reports back only
once the message is actually out.

A rejection is delivered by DM and, if the author has DMs closed, by a mention
in the public charts channel that says only that a decision was made. The
reviewer's reasoning is never posted publicly: it is written for one person.
"""

import asyncio
import logging

import aiohttp
import discord
from discord.ext import tasks

from oak import OakBranch
from oak.context import BranchContext

logger = logging.getLogger(__name__)


DEFAULT_CONFIG = {
    "enabled": True,
    "version": "1.0.0",
    "settings": {
        # Website base URL, reachable from this container. Use the website
        # container's name or id, not the published host address: a published
        # address fails container-to-container on this host (NAT hairpin).
        "api_url": "",
        # Shared secret; must equal BOT_API_KEY in the website's environment.
        "api_key": "",
        # How often to look for undelivered verdicts. Decisions are rare, so
        # this is deliberately slow — the DM is not time-critical.
        "poll_minutes": 5,
        # Where a mention goes when the author has DMs closed. The public charts
        # channel (#rhythm-charts). Leave 0 to skip the fallback entirely.
        "fallback_channel_id": 0,
        # Link the DM points at, so the author can read the reviewer's notes
        # and open the chart again.
        "forge_url": "https://oakheart.net/rhythm",
        "embed": {
            "approved_title": "🎉 Your chart was accepted",
            "approved_description": (
                "**{title}** is going into the game, with your name on it.\n\n"
                "It shows up in `/rhythm` once staff deliver it to the server."
            ),
            "approved_color": 0x8FAA87,  # Garden Green
            "rejected_title": "🎵 Your chart was reviewed",
            "rejected_description": (
                "**{title}** was not accepted this time.\n\n"
                "Charts can be reopened and fixed in the Forge, and resubmitted."
            ),
            # Storybook Brown, not the error red: not being accepted is not a fault.
            "rejected_color": 0x9D8B7A,
            "reason_heading": "What the reviewer said",
            "footer": "Oakheart Chart Forge",
        },
    },
}


class RhythmVerdicts(OakBranch):
    """Delivers Chart Forge decisions to the people who made the charts."""

    def __init__(self, ctx: BranchContext) -> None:
        super().__init__(ctx)
        self.api_url = str(self.setting("api_url", default="")).rstrip("/")
        self.api_key = self.setting("api_key", default="")
        self.forge_url = self.setting("forge_url", default="https://oakheart.net/rhythm")
        self.fallback_channel_id = int(self.setting("fallback_channel_id", default=0) or 0)
        try:
            self._poll_minutes = max(1, int(self.setting("poll_minutes", default=5)))
        except (TypeError, ValueError):
            self.log.error("Invalid poll_minutes; falling back to 5 minutes.")
            self._poll_minutes = 5
        self._session: aiohttp.ClientSession | None = None

    async def on_enable(self) -> None:
        self._session = aiohttp.ClientSession()
        if not self.api_url or not self.api_key:
            self.log.warning(
                "api_url/api_key not configured — chart verdicts will not be delivered."
            )
            return
        self.deliver_verdicts.change_interval(minutes=self._poll_minutes)
        self.deliver_verdicts.start()
        self.register_task("deliver_verdicts", self.deliver_verdicts)
        self.log.info(f"Chart verdict delivery polling every {self._poll_minutes}m")

    async def on_disable(self) -> None:
        self.deliver_verdicts.cancel()
        if self._session:
            await self._session.close()
            self._session = None

    # ------------------------------------------------------------------
    #  Website API
    # ------------------------------------------------------------------

    async def _fetch_pending(self) -> list[dict]:
        """Verdicts the website says are still owed to their authors."""
        if not self._session:
            return []
        try:
            async with self._session.get(
                f"{self.api_url}/api/bot/rhythm/decisions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    self.log.warning(f"Verdict fetch returned {resp.status}")
                    return []
                data = await resp.json()
                return data.get("decisions", []) or []
        except asyncio.TimeoutError:
            self.log.warning("Verdict fetch timed out after 10s")
            return []
        except Exception as e:
            self.log.error(f"Verdict fetch failed: {e}")
            return []

    async def _report_delivered(self, submission_id: int) -> None:
        """Tell the website a verdict is out, so it is never sent twice."""
        if not self._session:
            return
        try:
            async with self._session.post(
                f"{self.api_url}/api/bot/rhythm/decisions/{int(submission_id)}/delivered",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    self.log.warning(
                        f"Marking verdict {submission_id} delivered returned {resp.status}; "
                        "it will be retried next poll."
                    )
        except Exception as e:
            # Left unmarked on purpose: a duplicate DM is a smaller harm than a
            # verdict the author never receives.
            self.log.error(f"Could not mark verdict {submission_id} delivered: {e}")

    # ------------------------------------------------------------------
    #  Delivery
    # ------------------------------------------------------------------

    def _build_embed(self, decision: dict) -> discord.Embed:
        approved = decision.get("status") == "approved"
        prefix = "approved" if approved else "rejected"
        title = self.setting("embed", f"{prefix}_title", default="Your chart was reviewed")
        template = self.setting("embed", f"{prefix}_description", default="**{title}** was reviewed.")
        color = int(self.setting("embed", f"{prefix}_color", default=0x9D8B7A))
        chart_title = decision.get("title") or "your chart"
        try:
            description = template.format(title=chart_title)
        except (KeyError, IndexError) as e:
            self.log.error(f"Bad description template: {e}; using the raw template")
            description = template

        embed = discord.Embed(title=title, description=description, color=color)
        reason = (decision.get("review_note") or "").strip()
        if reason:
            heading = self.setting("embed", "reason_heading", default="What the reviewer said")
            embed.add_field(name=heading, value=reason[:1024], inline=False)
        if self.forge_url:
            embed.add_field(name="Chart Forge", value=self.forge_url, inline=False)
        footer = self.setting("embed", "footer", default="")
        if footer:
            embed.set_footer(text=footer)
        return embed

    async def _deliver(self, decision: dict) -> bool:
        """DM one verdict, falling back to a channel mention. True if it landed."""
        author_id = decision.get("author_discord_id")
        if not author_id:
            return False
        embed = self._build_embed(decision)

        try:
            user = self.bot.get_user(int(author_id)) or await self.bot.fetch_user(int(author_id))
        except (discord.NotFound, discord.HTTPException, ValueError) as e:
            self.log.warning(f"Could not resolve chart author {author_id}: {e}")
            user = None

        if user:
            try:
                await user.send(embed=embed)
                self.log.info(f"DMed chart verdict for submission {decision.get('id')} to {author_id}")
                return True
            except discord.Forbidden:
                self.log.info(f"Chart author {author_id} has DMs closed; using the channel fallback")
            except discord.HTTPException as e:
                self.log.error(f"Failed to DM chart author {author_id}: {e}")

        # DMs closed or the user is unreachable. Say that a decision was made and
        # where to read it, never what the decision was: a rejection is not
        # something to announce on someone's behalf.
        if not self.fallback_channel_id:
            return False
        channel = self.bot.get_channel(self.fallback_channel_id)
        if not channel:
            self.log.warning(f"Fallback channel {self.fallback_channel_id} not found")
            return False
        try:
            await channel.send(
                content=(
                    f"<@{author_id}> your chart **{decision.get('title') or 'submission'}** has been"
                    f" reviewed. I could not DM you, so the result and any notes are waiting in the"
                    f" Chart Forge: {self.forge_url}"
                ),
                allowed_mentions=discord.AllowedMentions(
                    users=True, roles=False, everyone=False, replied_user=False
                ),
            )
            return True
        except discord.HTTPException as e:
            self.log.error(f"Fallback post failed for submission {decision.get('id')}: {e}")
            return False

    @tasks.loop(minutes=5)
    async def deliver_verdicts(self) -> None:
        for decision in await self._fetch_pending():
            if await self._deliver(decision):
                await self._report_delivered(decision.get("id"))

    @deliver_verdicts.before_loop
    async def before_deliver_verdicts(self) -> None:
        await self.bot.wait_until_ready()
