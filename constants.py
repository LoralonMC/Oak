"""
Global constants for Oak Discord bot framework.

Contains Discord API limits, embed limits, and other constant values
used throughout the bot and branches.
"""

# ============================================================================
# Discord API Limits
# ============================================================================

# Embed Limits (from Discord API documentation)
EMBED_TITLE_MAX = 256
EMBED_DESCRIPTION_MAX = 4096
EMBED_FIELD_NAME_MAX = 256
EMBED_FIELD_VALUE_MAX = 1024
EMBED_FOOTER_MAX = 2048
EMBED_AUTHOR_MAX = 256
EMBED_TOTAL_MAX = 6000  # Total characters across all embed fields
EMBED_MAX_FIELDS = 25  # Maximum number of fields in an embed

# Message Limits
MESSAGE_CONTENT_MAX = 2000

# Modal Limits
MODAL_TITLE_MAX = 45
MODAL_TEXT_INPUT_LABEL_MAX = 45
MODAL_TEXT_INPUT_PLACEHOLDER_MAX = 100
MODAL_TEXT_INPUT_VALUE_MAX = 4000

# Select Menu Limits
SELECT_OPTION_LABEL_MAX = 100
SELECT_OPTION_DESCRIPTION_MAX = 100
SELECT_OPTION_VALUE_MAX = 100
SELECT_MAX_OPTIONS = 25

# Button Limits
BUTTON_LABEL_MAX = 80

# Channel Name Limits
CHANNEL_NAME_MAX = 100

# Thread Limits
THREAD_NAME_MAX = 100

# User/Member Limits
USERNAME_MIN = 2
USERNAME_MAX = 32
NICKNAME_MAX = 32

# Role Limits
ROLE_NAME_MAX = 100
ROLE_MAX_PER_GUILD = 250

# Guild/Server Limits
GUILD_NAME_MAX = 100
GUILD_DESCRIPTION_MAX = 120

# Emoji Limits
EMOJI_NAME_MAX = 32
EMOJI_MAX_SIZE_KB = 256  # KB

# Sticker Limits
STICKER_NAME_MAX = 30
STICKER_DESCRIPTION_MAX = 100

# Webhook Limits
WEBHOOK_NAME_MAX = 80

# Invite Limits
INVITE_MAX_AGE_SECONDS = 604800  # 7 days in seconds
INVITE_MAX_USES = 100

# Audit Log Limits
AUDIT_LOG_REASON_MAX = 512

# Reaction Limits
REACTION_MAX_PER_MESSAGE = 20

# Application Command Limits (Slash Commands)
COMMAND_NAME_MAX = 32
COMMAND_DESCRIPTION_MAX = 100
COMMAND_OPTION_DESCRIPTION_MAX = 100
COMMAND_MAX_OPTIONS = 25
COMMAND_MAX_CHOICES = 25
COMMAND_CHOICE_NAME_MAX = 100
COMMAND_CHOICE_VALUE_MAX = 100

# File Upload Limits (in MB)
FILE_SIZE_LIMIT_FREE = 8       # MB for free guilds
FILE_SIZE_LIMIT_BOOSTED = 50   # MB for boosted guilds (level 2)
FILE_SIZE_LIMIT_MAX = 100      # MB for max boosted guilds (level 3)

# ============================================================================
# HTTP Status Codes
# ============================================================================
# Common HTTP status codes for error handling
HTTP_OK = 200
HTTP_BAD_REQUEST = 400
HTTP_FORBIDDEN = 403
HTTP_NOT_FOUND = 404
HTTP_RATE_LIMITED = 429
HTTP_INTERNAL_ERROR = 500
HTTP_BAD_GATEWAY = 502
HTTP_SERVICE_UNAVAILABLE = 503

# ============================================================================
# Oak Framework Constants
# ============================================================================

# Branch Configuration
BRANCH_CONFIG_FILE = "config.yml"
BRANCH_DATABASE_FILE = "data.db"

# Logging
LOG_FORMAT = '%(asctime)s [%(levelname)s] %(name)s: %(message)s'
LOG_DATE_FORMAT = '%Y-%m-%d %H:%M:%S'

# Default Timeouts (in seconds)
DEFAULT_VIEW_TIMEOUT = 180  # 3 minutes
DEFAULT_MODAL_TIMEOUT = 300  # 5 minutes
DEFAULT_BUTTON_TIMEOUT = 60  # 1 minute

# Common Validation Ranges
MIN_AGE_DISCORD_TOS = 13  # Discord's minimum age requirement
MAX_AGE_REASONABLE = 100  # Maximum reasonable age for validation

# ============================================================================
# Helper Functions
# ============================================================================

def truncate_for_embed_field(text: str, suffix: str = "...") -> str:
    """
    Truncate text to fit in an embed field value.

    Args:
        text: Text to truncate
        suffix: Suffix to add if truncated (default: "...")

    Returns:
        Truncated text that fits within EMBED_FIELD_VALUE_MAX
    """
    if not text:
        return ""

    if len(text) <= EMBED_FIELD_VALUE_MAX:
        return text

    return text[:EMBED_FIELD_VALUE_MAX - len(suffix)] + suffix


def truncate_for_embed_description(text: str, suffix: str = "...") -> str:
    """
    Truncate text to fit in an embed description.

    Args:
        text: Text to truncate
        suffix: Suffix to add if truncated (default: "...")

    Returns:
        Truncated text that fits within EMBED_DESCRIPTION_MAX
    """
    if not text:
        return ""

    if len(text) <= EMBED_DESCRIPTION_MAX:
        return text

    return text[:EMBED_DESCRIPTION_MAX - len(suffix)] + suffix


def truncate_for_message(text: str, suffix: str = "...") -> str:
    """
    Truncate text to fit in a Discord message.

    Args:
        text: Text to truncate
        suffix: Suffix to add if truncated (default: "...")

    Returns:
        Truncated text that fits within MESSAGE_CONTENT_MAX
    """
    if not text:
        return ""

    if len(text) <= MESSAGE_CONTENT_MAX:
        return text

    return text[:MESSAGE_CONTENT_MAX - len(suffix)] + suffix
