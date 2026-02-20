"""HTML transcript generation for closed tickets."""

import asyncio
import io
import logging
import re
from datetime import datetime, timezone

import discord

logger = logging.getLogger(__name__)

# Markdown patterns
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
_UNDERLINE = re.compile(r"__(.+?)__")
_STRIKE = re.compile(r"~~(.+?)~~")
_CODE_BLOCK = re.compile(r"```(?:\w+\n)?(.*?)```", re.DOTALL)
_INLINE_CODE = re.compile(r"`(.+?)`")
_USER_MENTION = re.compile(r"<@!?(\d+)>")
_ROLE_MENTION = re.compile(r"<@&(\d+)>")
_CHANNEL_MENTION = re.compile(r"<#(\d+)>")

# Group messages from same author within this window
_GROUP_SECONDS = 420  # 7 minutes

_CSS = """
* { margin: 0; padding: 0; box-sizing: border-box; }
body { background: #36393f; color: #dcddde; font-family: 'Segoe UI', 'Helvetica Neue', Helvetica, Arial, sans-serif; font-size: 15px; line-height: 1.375; }
.header { background: #202225; padding: 20px 24px; border-bottom: 1px solid #2f3136; }
.header h1 { color: #fff; font-size: 20px; margin-bottom: 8px; }
.header .meta { color: #a3a6aa; font-size: 13px; line-height: 1.6; }
.header .meta span { color: #dcddde; }
.messages { padding: 16px 0; }
.msg-group { padding: 2px 24px; }
.msg-group:hover { background: #32353b; }
.msg-group.first { margin-top: 12px; padding-top: 4px; }
.msg-header { display: flex; align-items: baseline; gap: 8px; margin-bottom: 2px; }
.avatar { width: 40px; height: 40px; border-radius: 50%; float: left; margin-right: 12px; margin-top: 2px; }
.author { font-weight: 600; font-size: 15px; cursor: default; }
.timestamp { color: #72767d; font-size: 12px; }
.content { margin-left: 52px; word-wrap: break-word; white-space: pre-wrap; }
.content p { margin: 2px 0; }
.system-msg { color: #72767d; font-style: italic; padding: 4px 24px; font-size: 14px; }
.attachment { margin: 4px 0; }
.attachment a { color: #00aff4; text-decoration: none; }
.attachment a:hover { text-decoration: underline; }
.attachment img { max-width: 400px; max-height: 300px; border-radius: 4px; margin: 4px 0; display: block; }
.embed-block { border-left: 4px solid #5865f2; background: #2f3136; border-radius: 4px; padding: 8px 12px; margin: 4px 0; max-width: 520px; }
.embed-block .embed-title { font-weight: 600; color: #fff; margin-bottom: 4px; }
.embed-block .embed-desc { font-size: 14px; color: #dcddde; }
.embed-fields { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
.embed-field { min-width: 0; flex: 0 0 100%; }
.embed-field.inline { flex: 1 1 30%; min-width: 120px; }
.embed-field-name { font-size: 13px; font-weight: 600; color: #fff; margin-bottom: 2px; }
.embed-field-value { font-size: 14px; color: #dcddde; }
code { background: #2f3136; padding: 2px 4px; border-radius: 3px; font-size: 13px; font-family: 'Consolas', 'Courier New', monospace; }
pre { background: #2f3136; padding: 8px; border-radius: 4px; margin: 4px 0; overflow-x: auto; }
pre code { background: none; padding: 0; }
.footer { background: #202225; padding: 16px 24px; border-top: 1px solid #2f3136; color: #72767d; font-size: 12px; text-align: center; }
"""


def _escape_html(text: str) -> str:
    """Escape HTML special characters."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _md_to_html(text: str, guild: discord.Guild | None = None) -> str:
    """Convert basic Discord markdown to HTML."""
    text = _escape_html(text)

    # Code blocks first (protect from other replacements)
    def _code_block(m):
        return f"<pre><code>{m.group(1)}</code></pre>"
    text = _CODE_BLOCK.sub(_code_block, text)

    # Inline code
    text = _INLINE_CODE.sub(r"<code>\1</code>", text)

    # Bold, italic, underline, strikethrough
    text = _BOLD.sub(r"<strong>\1</strong>", text)
    text = _ITALIC.sub(r"<em>\1</em>", text)
    text = _UNDERLINE.sub(r"<u>\1</u>", text)
    text = _STRIKE.sub(r"<s>\1</s>", text)

    # Mentions
    def _resolve_user(m):
        uid = int(m.group(1))
        if guild:
            member = guild.get_member(uid)
            if member:
                return f'<span style="color:#5865f2;font-weight:600">@{_escape_html(member.display_name)}</span>'
        return f'<span style="color:#5865f2;font-weight:600">@Unknown User</span>'

    def _resolve_role(m):
        rid = int(m.group(1))
        if guild:
            role = guild.get_role(rid)
            if role:
                return f'<span style="color:#5865f2;font-weight:600">@{_escape_html(role.name)}</span>'
        return f'<span style="color:#5865f2;font-weight:600">@Unknown Role</span>'

    def _resolve_channel(m):
        cid = int(m.group(1))
        if guild:
            ch = guild.get_channel(cid)
            if ch:
                return f'<span style="color:#5865f2">#{_escape_html(ch.name)}</span>'
        return f'<span style="color:#5865f2">#unknown-channel</span>'

    text = _USER_MENTION.sub(_resolve_user, text)
    text = _ROLE_MENTION.sub(_resolve_role, text)
    text = _CHANNEL_MENTION.sub(_resolve_channel, text)

    # Newlines to <br> (outside of pre blocks)
    parts = text.split("<pre>")
    result = []
    for i, part in enumerate(parts):
        if i == 0:
            result.append(part.replace("\n", "<br>"))
        else:
            pre_end = part.find("</pre>")
            if pre_end != -1:
                result.append("<pre>" + part[:pre_end + 6] + part[pre_end + 6:].replace("\n", "<br>"))
            else:
                result.append("<pre>" + part)
    return "".join(result)


def _author_color(member: discord.Member | discord.User) -> str:
    """Get the display color for a member."""
    if isinstance(member, discord.Member) and member.color and member.color.value != 0:
        return f"#{member.color.value:06x}"
    return "#ffffff"


def _format_timestamp(dt: datetime) -> str:
    """Format a datetime for display."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%m/%d/%Y %I:%M %p")


def _build_html(
    messages: list[discord.Message],
    ticket_data: dict,
    guild: discord.Guild | None,
) -> str:
    """Build the full HTML transcript string."""
    # Header metadata
    category = ticket_data.get("category", "Unknown")
    ticket_number = ticket_data.get("ticket_number")
    creator_id = ticket_data.get("creator_id")
    created_at = ticket_data.get("created_at", "")
    closed_at = ticket_data.get("closed_at", "")
    closed_by = ticket_data.get("closed_by")
    close_reason = ticket_data.get("close_reason", "")

    ticket_label = f"#{ticket_number}" if ticket_number else f"ID:{creator_id}"

    # Resolve names
    creator_name = f"Unknown ({creator_id})"
    if guild and creator_id:
        member = guild.get_member(creator_id)
        if member:
            creator_name = f"{member.display_name} ({member.name})"

    closer_name = ""
    if closed_by:
        closer_name = f"Unknown ({closed_by})"
        if guild:
            member = guild.get_member(closed_by)
            if member:
                closer_name = f"{member.display_name} ({member.name})"

    meta_lines = [
        f"Category: <span>{_escape_html(category.replace('_', ' ').title())}</span>",
        f"Ticket: <span>{_escape_html(ticket_label)}</span>",
        f"Creator: <span>{_escape_html(creator_name)}</span>",
        f"Opened: <span>{_escape_html(str(created_at))}</span>",
    ]
    if closed_at:
        meta_lines.append(f"Closed: <span>{_escape_html(str(closed_at))}</span>")
    if closer_name:
        meta_lines.append(f"Closed by: <span>{_escape_html(closer_name)}</span>")
    if close_reason:
        meta_lines.append(f"Reason: <span>{_escape_html(close_reason)}</span>")

    meta_html = "<br>".join(meta_lines)

    # Build message HTML
    msg_html_parts = []
    prev_author_id = None
    prev_time = None

    for msg in messages:
        is_system = msg.type not in (
            discord.MessageType.default,
            discord.MessageType.reply,
        )

        if is_system:
            sys_text = _escape_html(msg.system_content or str(msg.type.name).replace("_", " "))
            msg_html_parts.append(f'<div class="system-msg">{sys_text}</div>')
            prev_author_id = None
            prev_time = None
            continue

        # Check if this message groups with previous
        created = msg.created_at
        same_author = msg.author.id == prev_author_id
        within_window = (
            prev_time is not None
            and (created - prev_time).total_seconds() <= _GROUP_SECONDS
        )
        is_grouped = same_author and within_window

        ts_str = _format_timestamp(created)
        color = _author_color(msg.author)
        avatar_url = msg.author.display_avatar.url if msg.author.display_avatar else ""

        if not is_grouped:
            msg_html_parts.append(
                f'<div class="msg-group first">'
                f'<img class="avatar" src="{_escape_html(avatar_url)}" alt="">'
                f'<div class="msg-header">'
                f'<span class="author" style="color:{color}">{_escape_html(msg.author.display_name)}</span>'
                f'<span class="timestamp">{ts_str}</span>'
                f'</div>'
            )
        else:
            msg_html_parts.append('<div class="msg-group">')

        # Content
        if msg.content:
            content_html = _md_to_html(msg.content, guild)
            msg_html_parts.append(f'<div class="content"><p>{content_html}</p></div>')

        # Attachments
        for att in msg.attachments:
            att_name = _escape_html(att.filename)
            att_url = _escape_html(att.url)
            if att.content_type and att.content_type.startswith("image/"):
                msg_html_parts.append(
                    f'<div class="content attachment">'
                    f'<a href="{att_url}" target="_blank"><img src="{att_url}" alt="{att_name}"></a>'
                    f'</div>'
                )
            else:
                msg_html_parts.append(
                    f'<div class="content attachment">'
                    f'<a href="{att_url}" target="_blank">{att_name}</a>'
                    f'</div>'
                )

        # Embeds
        for emb in msg.embeds:
            emb_color = f"#{emb.color.value:06x}" if emb.color else "#5865f2"
            title_html = f'<div class="embed-title">{_escape_html(emb.title or "")}</div>' if emb.title else ""
            desc_html = f'<div class="embed-desc">{_md_to_html(emb.description or "", guild)}</div>' if emb.description else ""
            fields_html = ""
            for field in emb.fields:
                fname = _escape_html(field.name) if field.name else ""
                fval = _md_to_html(field.value or "", guild)
                inline_class = " inline" if field.inline else ""
                fields_html += (
                    f'<div class="embed-field{inline_class}">'
                    f'<div class="embed-field-name">{fname}</div>'
                    f'<div class="embed-field-value">{fval}</div>'
                    f'</div>'
                )
            if fields_html:
                fields_html = f'<div class="embed-fields">{fields_html}</div>'
            msg_html_parts.append(
                f'<div class="content"><div class="embed-block" style="border-left-color:{emb_color}">'
                f'{title_html}{desc_html}{fields_html}'
                f'</div></div>'
            )

        msg_html_parts.append("</div>")

        prev_author_id = msg.author.id
        prev_time = created

    messages_html = "\n".join(msg_html_parts)
    msg_count = len(messages)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Transcript — {_escape_html(category)} {_escape_html(ticket_label)}</title>
<style>{_CSS}</style>
</head>
<body>
<div class="header">
<h1>Ticket Transcript</h1>
<div class="meta">{meta_html}</div>
</div>
<div class="messages">
{messages_html}
</div>
<div class="footer">
{msg_count} messages &mdash; Generated {_format_timestamp(datetime.now(timezone.utc))} UTC
</div>
</body>
</html>"""


async def generate_transcript(
    thread: discord.Thread,
    ticket_data: dict,
) -> io.BytesIO:
    """Generate an HTML transcript from a ticket thread.

    Must be called BEFORE the thread is archived.

    Args:
        thread: The ticket thread to transcribe.
        ticket_data: Dict with category, ticket_number, creator_id,
                     created_at, closed_at, closed_by, close_reason.

    Returns:
        BytesIO buffer containing the HTML file.
    """
    messages = []
    async for msg in thread.history(limit=None, oldest_first=True):
        messages.append(msg)

    guild = thread.guild

    # Use thread for large transcripts
    if len(messages) > 100:
        html = await asyncio.to_thread(_build_html, messages, ticket_data, guild)
    else:
        html = _build_html(messages, ticket_data, guild)

    buf = io.BytesIO(html.encode("utf-8"))
    buf.seek(0)
    return buf


async def send_transcript(
    buffer: io.BytesIO,
    thread: discord.Thread,
    ticket_data: dict,
    config: dict,
    db,
    bot,
    transcripts_dir=None,
) -> None:
    """Send the transcript to log channel and/or DM creator.

    If the web server is configured (``base_url`` is set), the transcript is
    saved to disk and an embed with a clickable link is sent instead of a
    file attachment.

    Args:
        buffer: The HTML transcript BytesIO buffer.
        thread: The ticket thread.
        ticket_data: Dict with ticket metadata.
        config: Branch config dict.
        db: BranchDatabase instance.
        bot: The bot instance.
        transcripts_dir: Path to transcripts directory (required when web is enabled).
    """
    settings = config.get("settings", {})
    transcript_config = settings.get("transcript", {})
    log_to_channel = transcript_config.get("log_channel", True)
    dm_creator = transcript_config.get("dm_creator", False)

    web_config = transcript_config.get("web", {})
    web_enabled = web_config.get("enabled", False)
    base_url = web_config.get("base_url", "").rstrip("/") if web_enabled else ""

    category = ticket_data.get("category", "unknown")
    ticket_number = ticket_data.get("ticket_number")
    ticket_label = str(ticket_number) if ticket_number else str(ticket_data.get("creator_id", "unknown"))
    filename = f"ticket-{category}-{ticket_label}.html"

    # If web server is enabled, save to disk and build a URL
    transcript_url = None
    if base_url and transcripts_dir:
        try:
            from .web import save_transcript
            await save_transcript(transcripts_dir, filename, buffer)
            transcript_url = f"{base_url}/transcripts/{filename}"
        except Exception as e:
            logger.error(f"Failed to save transcript to disk: {e}", exc_info=True)

    # Post to log channel
    transcript_message_id = None
    if log_to_channel:
        log_channel_id = settings.get("log_channel_id", 0)
        if log_channel_id:
            log_channel = bot.get_channel(log_channel_id)
            if log_channel:
                try:
                    if transcript_url:
                        embed = discord.Embed(
                            title="Ticket Transcript",
                            description=f"[View Transcript]({transcript_url})",
                            color=0x5865F2,
                        )
                        embed.add_field(name="Ticket", value=thread.name, inline=True)
                        msg = await log_channel.send(embed=embed)
                    else:
                        buffer.seek(0)
                        file = discord.File(buffer, filename=filename)
                        msg = await log_channel.send(
                            content=f"Transcript for {thread.name}:",
                            file=file,
                        )
                    transcript_message_id = msg.id
                except discord.HTTPException as e:
                    logger.error(f"Failed to send transcript to log channel: {e}")

    # Save transcript message ID
    if transcript_message_id:
        try:
            await db.execute(
                "UPDATE tickets SET transcript_message_id = ? WHERE thread_id = ?",
                (transcript_message_id, thread.id),
            )
        except Exception as e:
            logger.error(f"Failed to save transcript_message_id: {e}")

    # DM creator
    if dm_creator:
        creator_id = ticket_data.get("creator_id")
        if creator_id:
            try:
                user = bot.get_user(creator_id)
                if not user:
                    user = await bot.fetch_user(creator_id)
                if transcript_url:
                    embed = discord.Embed(
                        title="Ticket Transcript",
                        description=(
                            f"Here is the transcript for your ticket ({thread.name}):\n"
                            f"[View Transcript]({transcript_url})"
                        ),
                        color=0x5865F2,
                    )
                    await user.send(embed=embed)
                else:
                    buffer.seek(0)
                    file = discord.File(buffer, filename=filename)
                    await user.send(
                        content=f"Here is the transcript for your ticket ({thread.name}):",
                        file=file,
                    )
            except discord.Forbidden:
                logger.warning(f"Could not DM transcript to user {creator_id} — DMs disabled")
            except discord.HTTPException as e:
                logger.error(f"Failed to DM transcript to user {creator_id}: {e}")
            except Exception as e:
                logger.error(f"Error sending transcript DM: {e}")
