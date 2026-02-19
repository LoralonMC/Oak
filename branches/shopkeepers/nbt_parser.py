"""
NBT Metadata Parser
Regex-based parser for extracting item identity from Minecraft NBT metadata strings.
"""

import base64
import gzip
import re
import struct

# Roman numeral lookup for enchantment levels
# NOTE: ENCHANTMENT_NAMES and the max-level-1 list in _extract_stored_enchantments
# may need updating when new Minecraft versions add enchantments.
ROMAN_NUMERALS = {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V", 6: "VI", 7: "VII", 8: "VIII", 9: "IX", 10: "X"}

# Enchantment display names (minecraft:enchant_id -> "Display Name")
ENCHANTMENT_NAMES = {
    "aqua_affinity": "Aqua Affinity",
    "bane_of_arthropods": "Bane of Arthropods",
    "binding_curse": "Curse of Binding",
    "blast_protection": "Blast Protection",
    "breach": "Breach",
    "channeling": "Channeling",
    "density": "Density",
    "depth_strider": "Depth Strider",
    "efficiency": "Efficiency",
    "feather_falling": "Feather Falling",
    "fire_aspect": "Fire Aspect",
    "fire_protection": "Fire Protection",
    "flame": "Flame",
    "fortune": "Fortune",
    "frost_walker": "Frost Walker",
    "impaling": "Impaling",
    "infinity": "Infinity",
    "knockback": "Knockback",
    "looting": "Looting",
    "loyalty": "Loyalty",
    "luck_of_the_sea": "Luck of the Sea",
    "lure": "Lure",
    "mending": "Mending",
    "multishot": "Multishot",
    "piercing": "Piercing",
    "power": "Power",
    "projectile_protection": "Projectile Protection",
    "protection": "Protection",
    "punch": "Punch",
    "quick_charge": "Quick Charge",
    "respiration": "Respiration",
    "riptide": "Riptide",
    "sharpness": "Sharpness",
    "silk_touch": "Silk Touch",
    "smite": "Smite",
    "soul_speed": "Soul Speed",
    "sweeping_edge": "Sweeping Edge",
    "swift_sneak": "Swift Sneak",
    "thorns": "Thorns",
    "unbreaking": "Unbreaking",
    "vanishing_curse": "Curse of Vanishing",
    "wind_burst": "Wind Burst",
}

# Potion effect display names
POTION_NAMES = {
    "water": "Water Bottle",
    "mundane": "Mundane Potion",
    "thick": "Thick Potion",
    "awkward": "Awkward Potion",
    "night_vision": "Night Vision",
    "long_night_vision": "Night Vision (Extended)",
    "invisibility": "Invisibility",
    "long_invisibility": "Invisibility (Extended)",
    "leaping": "Leaping",
    "long_leaping": "Leaping (Extended)",
    "strong_leaping": "Leaping II",
    "fire_resistance": "Fire Resistance",
    "long_fire_resistance": "Fire Resistance (Extended)",
    "swiftness": "Swiftness",
    "long_swiftness": "Swiftness (Extended)",
    "strong_swiftness": "Swiftness II",
    "slowness": "Slowness",
    "long_slowness": "Slowness (Extended)",
    "strong_slowness": "Slowness IV",
    "turtle_master": "Turtle Master",
    "long_turtle_master": "Turtle Master (Extended)",
    "strong_turtle_master": "Turtle Master II",
    "water_breathing": "Water Breathing",
    "long_water_breathing": "Water Breathing (Extended)",
    "healing": "Healing",
    "strong_healing": "Healing II",
    "harming": "Harming",
    "strong_harming": "Harming II",
    "poison": "Poison",
    "long_poison": "Poison (Extended)",
    "strong_poison": "Poison II",
    "regeneration": "Regeneration",
    "long_regeneration": "Regeneration (Extended)",
    "strong_regeneration": "Regeneration II",
    "strength": "Strength",
    "long_strength": "Strength (Extended)",
    "strong_strength": "Strength II",
    "weakness": "Weakness",
    "long_weakness": "Weakness (Extended)",
    "luck": "Luck",
    "slow_falling": "Slow Falling",
    "long_slow_falling": "Slow Falling (Extended)",
    "wind_charged": "Wind Charged",
    "weaving": "Weaving",
    "oozing": "Oozing",
    "infested": "Infested",
}


def parse_item_metadata(material_type: str, metadata: str, plugin_identity_keys: list = None) -> dict:
    """
    Parse NBT metadata to extract item identity information.

    Args:
        material_type: The Minecraft material type (e.g. "EMERALD_BLOCK", "DIAMOND_SWORD")
        metadata: Raw NBT metadata string from CSV export
        plugin_identity_keys: Ordered list of "namespace:key" strings to scan for
            in PublicBukkitValues. First match wins. Use "itemsadder" for ItemsAdder's
            special format. If None, uses DEFAULT_PLUGIN_IDENTITY_KEYS.

    Returns:
        dict with keys:
            display_name: str | None - original casing for UI
            plugin_id: str | None - e.g. "executableitems:VoteToken"
            item_key: str - stable identity key
            search_name: str - lowercase for autocomplete
    """
    if plugin_identity_keys is None:
        plugin_identity_keys = DEFAULT_PLUGIN_IDENTITY_KEYS

    display_name = _extract_custom_name(metadata) if metadata else None
    plugin_id = _extract_plugin_id(metadata, plugin_identity_keys) if metadata else None

    # Special handling for enchanted books
    if material_type.upper() == "ENCHANTED_BOOK" and metadata:
        enchant_info = _extract_stored_enchantments(metadata)
        if enchant_info:
            enchant_key, enchant_display = enchant_info
            item_key = f"minecraft:enchanted_book:{enchant_key}"
            display_name = f"Enchanted Book ({enchant_display})"
            search_name = f"enchanted book {enchant_display.lower()}"
            return {
                "display_name": display_name,
                "plugin_id": plugin_id,
                "item_key": item_key,
                "search_name": search_name,
            }

    # Special handling for potions, splash potions, lingering potions, tipped arrows
    material_upper = material_type.upper()
    if material_upper in ("POTION", "SPLASH_POTION", "LINGERING_POTION", "TIPPED_ARROW") and metadata:
        potion_info = _extract_potion_type(metadata)
        if potion_info:
            potion_key, potion_display = potion_info
            item_key = f"minecraft:{material_type.lower()}:{potion_key}"
            if material_upper == "TIPPED_ARROW":
                display_name = f"Arrow of {potion_display}"
            elif material_upper == "SPLASH_POTION":
                display_name = f"Splash Potion of {potion_display}"
            elif material_upper == "LINGERING_POTION":
                display_name = f"Lingering Potion of {potion_display}"
            else:
                display_name = f"Potion of {potion_display}"
            search_name = display_name.lower()
            return {
                "display_name": display_name,
                "plugin_id": plugin_id,
                "item_key": item_key,
                "search_name": search_name,
            }

    item_key = _build_item_key(material_type, plugin_id, display_name)
    search_name = _build_search_name(material_type, display_name)

    return {
        "display_name": display_name,
        "plugin_id": plugin_id,
        "item_key": item_key,
        "search_name": search_name,
    }


# Default identity keys, checked in priority order. Each entry is a
# "namespace:key" string matching PublicBukkitValues entries. The special
# value "itemsadder" handles ItemsAdder's non-standard format.
DEFAULT_PLUGIN_IDENTITY_KEYS = [
    "executableitems:ei-id",
    "itemsadder",
    "phoenixcrates:key",
    "crazycrates:crazycrates_crate_key",
    "oaktools:tool_type",
    "nekotraps:trap_type",
    "shopkeepers:shop_creation_item",
]


def _extract_stored_enchantments(metadata: str) -> tuple[str, str] | None:
    """
    Extract stored enchantments from enchanted book NBT metadata.

    Handles two formats:
    1. Bukkit format: stored-enchants: {'minecraft:riptide': 3}
    2. 1.20.5+ components: 'minecraft:stored_enchantments': '{"minecraft:silk_touch":1}'

    For books with multiple enchantments, creates a combined identity.

    Args:
        metadata: Raw NBT metadata string

    Returns:
        Tuple of (key_suffix, display_name) or None if no enchantments found.
        Example: ("sharpness_5", "Sharpness V")
    """
    matches = []

    # Try Bukkit format first: stored-enchants: {'minecraft:riptide': 3}
    bukkit_match = re.search(r"stored-enchants:\s*\{([^}]+)\}", metadata)
    if bukkit_match:
        enchants_content = bukkit_match.group(1)
        enchant_pattern = r"'minecraft:([^']+)':\s*(\d+)"
        matches = re.findall(enchant_pattern, enchants_content)

    # Try 1.20.5+ components format: 'minecraft:stored_enchantments': '{"minecraft:silk_touch":1}'
    if not matches:
        components_match = re.search(
            r"'minecraft:stored_enchantments':\s*'([^']+)'", metadata
        )
        if components_match:
            enchants_json = components_match.group(1)
            # Parse the JSON-like string: "minecraft:enchant":level
            enchant_pattern = r'"minecraft:([^"]+)":(\d+)'
            matches = re.findall(enchant_pattern, enchants_json)

    if not matches:
        return None

    # Sort enchantments alphabetically for consistent keys
    matches.sort(key=lambda x: x[0])

    # Build key and display parts
    key_parts = []
    display_parts = []

    for enchant_id, level_str in matches:
        level = int(level_str)
        roman = ROMAN_NUMERALS.get(level, str(level))
        enchant_name = ENCHANTMENT_NAMES.get(enchant_id, enchant_id.replace("_", " ").title())

        key_parts.append(f"{enchant_id}_{level}")
        # Don't show level for max-1 enchants like Mending, Silk Touch, etc.
        if level == 1 and enchant_id in ("mending", "silk_touch", "infinity", "flame", "channeling", "multishot", "aqua_affinity"):
            display_parts.append(enchant_name)
        else:
            display_parts.append(f"{enchant_name} {roman}")

    return ("_".join(key_parts), ", ".join(display_parts))


def _extract_potion_type(metadata: str) -> tuple[str, str] | None:
    """
    Extract potion type from potion/tipped arrow NBT metadata.

    Handles two formats:
    1. Bukkit format: potion-type: 'minecraft:long_fire_resistance'
    2. 1.20.5+ components: 'minecraft:potion_contents': '{potion:"minecraft:long_invisibility"}'

    Args:
        metadata: Raw NBT metadata string

    Returns:
        Tuple of (key_suffix, display_name) or None if no potion type found.
        Example: ("strong_healing", "Healing II")
    """
    potion_id = None

    # Try Bukkit format: potion-type: 'minecraft:long_fire_resistance'
    bukkit_match = re.search(r"potion-type:\s*'minecraft:([^']+)'", metadata)
    if bukkit_match:
        potion_id = bukkit_match.group(1)

    # Try 1.20.5+ components format: 'minecraft:potion_contents': '{potion:"minecraft:..."}'
    # Handles both single quotes and double-escaped quotes
    if not potion_id:
        components_match = re.search(
            r"""'minecraft:potion_contents':\s*'\{potion:"+minecraft:([^"]+)""", metadata
        )
        if components_match:
            potion_id = components_match.group(1)

    if not potion_id:
        return None

    potion_display = POTION_NAMES.get(potion_id, potion_id.replace("_", " ").title())

    return (potion_id, potion_display)


def _extract_custom_name(metadata: str) -> str | None:
    """
    Extract custom display name from NBT metadata.

    Handles three formats:
    1. 1.20.5+ components: 'minecraft:custom_name': '{"text":"Name"}'
    2. Bukkit format: display-name: '{"text":"Name","italic":false}'
    3. Old Bukkit base64: custom: <base64> containing display.Name NBT tag

    Ignores 'minecraft:item_name' entirely.

    Handles:
    - text:"Vote Boots" (simple name)
    - Per-character gradient: text:"P", text:"a", text:"l" -> "Pal"
    - Plain string fallback: '"Invisible Item Frame"'

    Args:
        metadata: Raw NBT metadata string (after CSV parsing)

    Returns:
        Display name with original casing, or None
    """
    custom_name_value = None

    # Try 1.20.5+ components format first: 'minecraft:custom_name': '...'
    custom_name_match = re.search(
        r"'minecraft:custom_name':\s*'(.*?)'(?:\s*[,}])", metadata
    )
    if custom_name_match:
        custom_name_value = custom_name_match.group(1)

    # Try Bukkit format: display-name: '{"text":"Name",...}'
    if not custom_name_value:
        display_name_match = re.search(
            r"display-name:\s*'(\{.+?\})'", metadata
        )
        if display_name_match:
            custom_name_value = display_name_match.group(1)

    # Try old Bukkit base64 format: custom: <base64> containing display.Name
    if not custom_name_value:
        custom_match = re.search(r"custom:\s*([A-Za-z0-9+/=]+)", metadata)
        if custom_match:
            custom_name_value = _extract_display_name_from_nbt(custom_match.group(1))

    if not custom_name_value:
        return None

    # Find all text:"..." values within the custom_name field
    # Handle multiple formats:
    # - JSON format with double-double quotes: ""text"":""Name"" (Bukkit display-name)
    # - JSON format with single quotes: "text":"Name"
    # - NBT format: text:"Name" or text:""+Name""+
    text_matches = re.findall(
        r'""text"":""([^"]+)""|"text":"([^"]+)"|text:""+([^"]+)""+|text:"([^"]*)"',
        custom_name_value
    )

    if text_matches:
        # Flatten the tuple matches and filter out empty strings
        parts = [t[0] or t[1] or t[2] or t[3] for t in text_matches if any(t)]
        # Filter out item number tags like [#81], [#123] - these are identification tags
        parts = [p for p in parts if p and not re.match(r'^\[#\d+\]$', p)]
        if parts:
            return "".join(parts).strip()

    # Plain string fallback: check for bare '"SomeName"' pattern
    # (e.g. '"Invisible Item Frame"')
    plain_match = re.search(r'^"(.+)"$', custom_name_value)
    if plain_match:
        return plain_match.group(1)

    return None


def _extract_display_name_from_nbt(encoded: str) -> str | None:
    """
    Extract display name from a base64-encoded gzipped NBT blob.

    In Minecraft NBT, display names are stored in: tag.display.Name
    The Name value is a JSON text component string.

    Args:
        encoded: Base64-encoded string from the custom: field

    Returns:
        The JSON text component string (to be parsed by caller), or None
    """
    try:
        decoded = base64.b64decode(encoded)
        decompressed = gzip.decompress(decoded)

        # Look for the 'display' compound tag followed by 'Name' string tag
        # The display compound contains: Name (string with JSON text component)
        #
        # We look for the pattern: display compound -> Name string tag
        # Name tag format: 0x08 + 0x00 0x04 + "Name" + string length + string value

        display_idx = decompressed.find(b'display')
        if display_idx == -1:
            return None

        # Search for Name string tag after display marker
        search_region = decompressed[display_idx:]

        # Find 'Name' string tag: \x08\x00\x04Name + string
        name_marker = b'\x08\x00\x04Name'
        name_idx = search_region.find(name_marker)
        if name_idx == -1:
            return None

        str_len_pos = name_idx + len(name_marker)
        if str_len_pos + 2 > len(search_region):
            return None

        str_len = struct.unpack('>H', search_region[str_len_pos:str_len_pos+2])[0]
        str_start = str_len_pos + 2

        if str_start + str_len > len(search_region):
            return None

        # Return the JSON text component string
        return search_region[str_start:str_start+str_len].decode('utf-8', errors='ignore')

    except Exception:
        return None


def _extract_plugin_id(metadata: str, identity_keys: list) -> str | None:
    """
    Extract plugin identifier from NBT metadata.

    Scans for each configured identity key in priority order (first match wins).
    Most plugins store identity as "namespace:key":"value" inside PublicBukkitValues.
    ItemsAdder uses a special format: itemsadder:{id:"<id>",namespace:"<ns>"}.

    Also handles old Bukkit format where plugin data is stored in a base64-encoded
    gzipped NBT blob in the custom: field.

    Args:
        metadata: Raw NBT metadata string
        identity_keys: Ordered list of "namespace:key" strings to scan for

    Returns:
        Plugin ID string (e.g. "executableitems:VoteToken"), or None
    """
    # Pre-decode base64 blob if present (old Bukkit format)
    nbt_blob = None
    custom_match = re.search(r"custom:\s*([A-Za-z0-9+/=]+)", metadata)
    if custom_match:
        nbt_blob = _decode_custom_nbt(custom_match.group(1))

    for key in identity_keys:
        # ItemsAdder special case — uses its own serialization format
        if key == "itemsadder":
            # Try 1.20.5+ components format - handles both quote styles:
            # - itemsadder:{id:"emeraldkey",namespace:"emeraldset"} (single quotes)
            # - itemsadder:{id:""emeraldkey"",namespace:""emeraldset""} (double-double quotes)
            ia_match = re.search(
                r'itemsadder:\{id:"+([^"]+)"+,namespace:"+([^"]+)"+\}', metadata
            )
            if ia_match:
                return f"itemsadder:{ia_match.group(2)}:{ia_match.group(1)}"

            # Try old Bukkit format: base64 NBT containing itemsadder compound
            if nbt_blob:
                ia_data = _parse_itemsadder_from_nbt(nbt_blob)
                if ia_data:
                    return ia_data
            continue

        # Generic: "namespace:key_name":"value" → namespace:value
        namespace = key.split(":")[0]
        key_name = key.split(":", 1)[1] if ":" in key else key

        # Try multiple quote formats:
        # 1. ""key"": ""value"" (1.20.5+ NBT double-double quotes)
        # 2. "key": "value" (PublicBukkitValues JSON style)
        # 3. \"key\": \"value\" (escaped quotes in strings)
        for pattern in [
            rf'""+{re.escape(key)}""+:\s*""+([^"]+)"+"',  # ""key"":""value""
            rf'"{re.escape(key)}":\s*"([^"]+)"',          # "key": "value"
            rf'\\?"{re.escape(key)}\\?":\s*\\?"([^"\\]+)\\?"',  # \"key\": \"value\"
        ]:
            match = re.search(pattern, metadata)
            if match:
                return f"{namespace}:{match.group(1)}"

        # Try boolean/numeric values (e.g., "key":1b, "key": true)
        # For presence-only keys, use the key name as the identifier
        # Handle both escaped \" and regular " quotes, with optional whitespace
        bool_pattern = rf'\\?"{re.escape(key)}\\?":\s*(\d+b?|true|false)'
        match = re.search(bool_pattern, metadata)
        if match:
            # Use the key name part as identifier (e.g., "shop_creation_item")
            return f"{namespace}:{key_name}"

        # Try old Bukkit format: base64 NBT containing plugin compound
        if nbt_blob:
            value = _parse_plugin_value_from_nbt(nbt_blob, namespace, key_name)
            if value:
                return f"{namespace}:{value}"

    return None


def _decode_custom_nbt(encoded: str) -> bytes | None:
    """
    Decode and decompress a base64-encoded gzipped NBT blob.

    Args:
        encoded: Base64-encoded string from the custom: field

    Returns:
        Decompressed NBT bytes, or None if decoding fails
    """
    try:
        decoded = base64.b64decode(encoded)
        return gzip.decompress(decoded)
    except Exception:
        return None


def _parse_itemsadder_from_nbt(nbt_data: bytes) -> str | None:
    """
    Parse ItemsAdder plugin ID from decompressed NBT data.

    ItemsAdder stores data as a compound tag with 'id' and 'namespace' string fields.

    Args:
        nbt_data: Decompressed NBT bytes

    Returns:
        Plugin ID string (e.g. "itemsadder:nogs_menagerie:nm_pack_starter_pack"), or None
    """
    # Look for the itemsadder compound tag
    itemsadder_idx = nbt_data.find(b'itemsadder')
    if itemsadder_idx == -1:
        return None

    # Search for 'id' and 'namespace' string tags after the itemsadder marker
    search_region = nbt_data[itemsadder_idx:]

    item_id = _extract_nbt_string(search_region, b'id')
    namespace = _extract_nbt_string(search_region, b'namespace')

    if item_id and namespace:
        return f"itemsadder:{namespace}:{item_id}"

    return None


def _parse_plugin_value_from_nbt(nbt_data: bytes, namespace: str, key_name: str) -> str | None:
    """
    Parse a generic plugin value from decompressed NBT data.

    Looks for a compound tag matching the namespace, then extracts the key value.

    Args:
        nbt_data: Decompressed NBT bytes
        namespace: Plugin namespace (e.g. "executableitems")
        key_name: Key name to look for (e.g. "ei-id")

    Returns:
        The value string, or None if not found
    """
    # Look for the plugin compound tag
    namespace_bytes = namespace.encode('utf-8')
    namespace_idx = nbt_data.find(namespace_bytes)
    if namespace_idx == -1:
        return None

    # Search for the key string tag after the namespace marker
    search_region = nbt_data[namespace_idx:]

    return _extract_nbt_string(search_region, key_name.encode('utf-8'))


def _extract_nbt_string(data: bytes, tag_name: bytes) -> str | None:
    """
    Extract a string value from NBT data by tag name.

    NBT string tag format: 0x08 + 2-byte name length (big-endian) + name + 2-byte value length + value

    Args:
        data: NBT bytes to search
        tag_name: Name of the string tag to find

    Returns:
        The string value, or None if not found
    """
    # Build the marker: string tag (0x08) + name length + name
    name_len = len(tag_name)
    marker = b'\x08' + struct.pack('>H', name_len) + tag_name

    idx = data.find(marker)
    if idx == -1:
        return None

    # Read the string value length (2 bytes after marker)
    str_len_pos = idx + len(marker)
    if str_len_pos + 2 > len(data):
        return None

    str_len = struct.unpack('>H', data[str_len_pos:str_len_pos+2])[0]
    str_start = str_len_pos + 2

    if str_start + str_len > len(data):
        return None

    return data[str_start:str_start+str_len].decode('utf-8', errors='ignore')


def _normalize_display_name(name: str) -> str:
    """
    Normalize a display name for use in item keys.

    Strips color codes, trims whitespace, collapses spaces, lowercases.

    Args:
        name: Raw display name

    Returns:
        Normalized lowercase name
    """
    # Strip section sign color codes (e.g. §a, §l, §r)
    name = re.sub(r"§.", "", name)
    # Trim whitespace
    name = name.strip()
    # Collapse multiple spaces to single
    name = re.sub(r"\s+", " ", name)
    # Lowercase
    return name.lower()


def _build_item_key(material_type: str, plugin_id: str | None, display_name: str | None) -> str:
    """
    Build a stable identity key for an item.

    Args:
        material_type: Minecraft material type
        plugin_id: Plugin identifier if any
        display_name: Custom display name if any

    Returns:
        Stable item key string
    """
    if plugin_id:
        return plugin_id
    if display_name:
        return f"minecraft:{material_type.lower()}:{_normalize_display_name(display_name)}"
    return f"minecraft:{material_type.lower()}"


def _build_search_name(material_type: str, display_name: str | None) -> str:
    """
    Build a lowercase search name for autocomplete.

    Args:
        material_type: Minecraft material type
        display_name: Custom display name if any

    Returns:
        Lowercase search-friendly name
    """
    if display_name:
        return _normalize_display_name(display_name)
    # Humanize material type: "EMERALD_BLOCK" -> "emerald block"
    return material_type.replace("_", " ").lower()


def parse_shulker_contents(metadata: str) -> tuple[str, int] | None:
    """
    Parse shulker box contents from NBT metadata.

    If the shulker contains only one type of item (uniform contents),
    returns the item ID and total count. Otherwise returns None.

    Handles two formats:
    1. Bukkit format: internal: <base64 gzipped NBT>
    2. 1.20.5+ components: 'minecraft:container': '[{item:{count:64,id:"minecraft:gunpowder"},slot:0},...]'

    Args:
        metadata: Raw NBT metadata string from CSV

    Returns:
        Tuple of (item_id, total_count) if uniform contents, None otherwise.
        item_id is like "minecraft:gunpowder" or "minecraft:diamond"
    """
    items = {}

    # Try 1.20.5+ components format first (more common in newer data)
    container_match = re.search(r"'minecraft:container':\s*'\[([^\]]+)\]'", metadata)
    if container_match:
        container_content = container_match.group(1)
        # Parse items: {item:{count:64,id:"minecraft:gunpowder"},slot:0}
        # or with escaped quotes: {item:{count:64,id:""minecraft:gunpowder""},slot:0}
        # Find all item entries
        entries = re.findall(r'\{item:\{([^}]+)\},slot:\d+\}', container_content)
        for entry in entries:
            # Extract id and count from each entry
            id_match = re.search(r'id:\s*"*minecraft:([^",]+)"*', entry)
            count_match = re.search(r'count:\s*(\d+)', entry)
            if id_match and count_match:
                item_id = f"minecraft:{id_match.group(1)}"
                count = int(count_match.group(1))
                items[item_id] = items.get(item_id, 0) + count

    # Try Bukkit format with base64 internal data
    if not items:
        internal_match = re.search(r"internal:\s*([A-Za-z0-9+/=]+)", metadata)
        if internal_match:
            try:
                encoded = internal_match.group(1)
                decoded = base64.b64decode(encoded)
                decompressed = gzip.decompress(decoded)
                items = _parse_shulker_nbt(decompressed)
            except Exception:
                pass

    if not items:
        return None

    # Check if all items are the same type (uniform shulker)
    item_ids = set(items.keys())
    if len(item_ids) != 1:
        # Mixed contents - not a uniform shulker
        return None

    item_id = list(item_ids)[0]
    total_count = items[item_id]

    return (item_id, total_count)


def _parse_shulker_nbt(data: bytes) -> dict[str, int]:
    """
    Parse binary NBT data to extract item IDs and counts from shulker contents.

    This is a simplified NBT parser that looks for the specific patterns
    we need: item IDs (strings starting with "minecraft:") and counts.

    Args:
        data: Decompressed NBT binary data

    Returns:
        Dict mapping item_id -> total_count across all slots
    """
    items = {}

    # Find all minecraft:item_id patterns followed by count values
    # The NBT structure has: id -> string "minecraft:xxx", count -> int
    #
    # In binary NBT:
    # - String tag (type 8): 08 00 02 "id" 00 13 "minecraft:gunpowder"
    # - Int tag (type 3): 03 00 05 "count" 00 00 00 40 (64 in big-endian)
    #
    # We'll use a simple regex-like scan for the pattern

    i = 0
    while i < len(data) - 20:
        # Look for string tag with name "id"
        # Format: 08 (string tag) + 00 02 (name length 2) + "id" + XX XX (string length) + string value
        if data[i:i+5] == b'\x08\x00\x02id':
            # Read the string length (2 bytes, big-endian)
            str_len = struct.unpack('>H', data[i+5:i+7])[0]
            # Read the string value
            item_id = data[i+7:i+7+str_len].decode('utf-8', errors='ignore')

            # Now look for the count tag nearby (within next ~20 bytes)
            # Format: 03 (int tag) + 00 05 (name length 5) + "count" + XX XX XX XX (int value)
            count_start = i + 7 + str_len
            count_end = min(count_start + 30, len(data))
            count_region = data[count_start:count_end]

            count_idx = count_region.find(b'\x03\x00\x05count')
            if count_idx != -1:
                # Read the 4-byte big-endian int
                count_pos = count_start + count_idx + 8  # 1 + 2 + 5 = 8 bytes for tag header
                if count_pos + 4 <= len(data):
                    count = struct.unpack('>I', data[count_pos:count_pos+4])[0]

                    # Accumulate counts by item ID
                    if item_id.startswith("minecraft:"):
                        items[item_id] = items.get(item_id, 0) + count

            i = count_start
        else:
            i += 1

    return items
