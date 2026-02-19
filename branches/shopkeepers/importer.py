"""
Shopkeepers CSV Importer
Stateless import engine for CSV trade log files.
All config is passed in as arguments from branch.py.
"""

import asyncio
import csv
import hashlib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import aiosqlite

from .nbt_parser import parse_item_metadata, parse_shulker_contents

logger = logging.getLogger(__name__)

# CSV column indices
COL_TIME = 0
COL_PLAYER_UUID = 1
COL_PLAYER_NAME = 2
COL_SHOP_UUID = 3
COL_SHOP_TYPE = 4
COL_SHOP_WORLD = 5
COL_SHOP_X = 6
COL_SHOP_Y = 7
COL_SHOP_Z = 8
COL_SHOP_OWNER_UUID = 9
COL_SHOP_OWNER_NAME = 10
COL_ITEM1_TYPE = 11
COL_ITEM1_AMOUNT = 12
COL_ITEM1_METADATA = 13
COL_ITEM2_TYPE = 14
COL_ITEM2_AMOUNT = 15
COL_ITEM2_METADATA = 16
COL_RESULT_TYPE = 17
COL_RESULT_AMOUNT = 18
COL_RESULT_METADATA = 19
COL_TRADE_COUNT = 20
EXPECTED_COLUMNS = 21

# Filename pattern: trades-YYYY-MM-DD.csv
_FILENAME_PATTERN = re.compile(r"^trades-(\d{4}-\d{2}-\d{2})\.csv$")

# Max warnings logged per file before suppressing
_MAX_WARNINGS_PER_FILE = 50

# Max CSV file size (50 MB) — skip files larger than this
_MAX_CSV_FILE_SIZE = 50 * 1024 * 1024


@dataclass
class ImportResult:
    """Carries counters back to the caller after an import run."""
    files_scanned: int = 0
    files_imported: int = 0
    files_skipped: int = 0
    new_trades: int = 0
    duplicate_trades: int = 0
    new_items: int = 0
    errors: list[str] = field(default_factory=list)


def _read_csv_file(filepath: str) -> list[list[str]]:
    """Read all rows from a CSV file synchronously.

    Returns a list of rows (each row is a list of strings).
    This function runs in a thread to avoid blocking the event loop.
    """
    with open(filepath, "r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        return list(reader)


async def import_all(
    db_path: str,
    csv_directory: str,
    plugin_identity_keys: list,
    currencies: list,
) -> ImportResult:
    """
    Main entry point: discover and import all CSV trade files.

    Args:
        db_path: Path to the SQLite database.
        csv_directory: Directory containing trades-YYYY-MM-DD.csv files.
        plugin_identity_keys: Ordered list of plugin identity key strings.
        currencies: List of currency config dicts with item_key and emerald_value.

    Returns:
        ImportResult with counters and any errors.
    """
    result = ImportResult()
    csv_files = _discover_csv_files(csv_directory)
    result.files_scanned = len(csv_files)

    if not csv_files:
        return result

    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA journal_mode = WAL")
        await db.execute("PRAGMA foreign_keys = ON")

        item_cache = await _load_item_cache(db)
        currency_map = await _build_currency_map(db)

        # Build a lookup from item_key -> emerald_value from config,
        # used when upserting items that haven't been flagged yet.
        currency_config = {c["item_key"]: c["emerald_value"] for c in currencies if "item_key" in c}

        affected_dates: set[str] = set()

        for filepath, trade_date, filename in csv_files:
            try:
                # Check file size before importing
                file_stat = await asyncio.to_thread(Path(filepath).stat)
                if file_stat.st_size > _MAX_CSV_FILE_SIZE:
                    logger.warning(
                        f"Skipping {filename}: file size {file_stat.st_size / 1024 / 1024:.1f} MB exceeds "
                        f"{_MAX_CSV_FILE_SIZE / 1024 / 1024:.0f} MB limit"
                    )
                    result.files_skipped += 1
                    continue

                if not await _needs_import(db, filepath, filename):
                    result.files_skipped += 1
                    continue

                # Read all CSV rows in a thread to avoid blocking the event loop
                all_rows = await asyncio.to_thread(_read_csv_file, filepath)

                file_warnings = 0
                file_new = 0
                file_dup = 0
                file_affected_dates: set[str] = set()
                line_num = 0

                for line_num, row in enumerate(all_rows):
                    # Skip header row
                    if line_num == 0:
                        continue

                    # Validate column count
                    if len(row) != EXPECTED_COLUMNS:
                        if file_warnings < _MAX_WARNINGS_PER_FILE:
                            logger.warning(
                                f"{filename}:{line_num}: expected {EXPECTED_COLUMNS} columns, got {len(row)} — skipping"
                            )
                            file_warnings += 1
                        continue

                    # Parse amounts with validation
                    try:
                        item1_amount = int(row[COL_ITEM1_AMOUNT])
                    except (ValueError, IndexError):
                        if file_warnings < _MAX_WARNINGS_PER_FILE:
                            logger.warning(f"{filename}:{line_num}: invalid item1_amount — skipping")
                            file_warnings += 1
                        continue

                    item2_amount = None
                    if row[COL_ITEM2_TYPE]:
                        try:
                            item2_amount = int(row[COL_ITEM2_AMOUNT])
                        except (ValueError, IndexError):
                            if file_warnings < _MAX_WARNINGS_PER_FILE:
                                logger.warning(f"{filename}:{line_num}: invalid item2_amount — skipping")
                                file_warnings += 1
                            continue

                    try:
                        result_amount = int(row[COL_RESULT_AMOUNT])
                    except (ValueError, IndexError):
                        if file_warnings < _MAX_WARNINGS_PER_FILE:
                            logger.warning(f"{filename}:{line_num}: invalid result_amount — skipping")
                            file_warnings += 1
                        continue

                    try:
                        trade_count = int(row[COL_TRADE_COUNT])
                    except (ValueError, IndexError):
                        if file_warnings < _MAX_WARNINGS_PER_FILE:
                            logger.warning(f"{filename}:{line_num}: invalid trade_count — skipping")
                            file_warnings += 1
                        continue

                    # Upsert item1 (always present)
                    item1_id = await _upsert_item(
                        db, row[COL_ITEM1_TYPE], row[COL_ITEM1_METADATA],
                        plugin_identity_keys, item_cache, currency_config, currency_map, result,
                    )

                    # Upsert item2 (only if present)
                    item2_id = None
                    if row[COL_ITEM2_TYPE]:
                        item2_id = await _upsert_item(
                            db, row[COL_ITEM2_TYPE], row[COL_ITEM2_METADATA],
                            plugin_identity_keys, item_cache, currency_config, currency_map, result,
                        )

                    # Upsert result item - check for uniform shulker first
                    actual_result_amount = result_amount
                    result_material = row[COL_RESULT_TYPE]
                    result_metadata = row[COL_RESULT_METADATA]

                    # Check if this is a shulker box with uniform contents
                    if result_material.upper().endswith("SHULKER_BOX") and result_metadata:
                        shulker_contents = parse_shulker_contents(result_metadata)
                        if shulker_contents:
                            # Uniform shulker - expand to bulk trade of contents
                            content_item_id, content_count = shulker_contents
                            # content_item_id is like "minecraft:gunpowder"
                            # Extract material type from it
                            content_material = content_item_id.replace("minecraft:", "").upper()
                            result_material = content_material
                            result_metadata = ""  # No special metadata for bulk items
                            # Total items = items per shulker * number of shulkers * trade count factor
                            actual_result_amount = content_count * result_amount

                    result_item_id = await _upsert_item(
                        db, result_material, result_metadata,
                        plugin_identity_keys, item_cache, currency_config, currency_map, result,
                    )

                    # Compute fingerprint from raw CSV values (not expanded
                    # shulker amounts) so it stays stable across parser changes.
                    row_fingerprint = _compute_fingerprint(
                        trade_date, row[COL_TIME], row[COL_PLAYER_UUID], row[COL_SHOP_UUID],
                        row[COL_ITEM1_TYPE], row[COL_ITEM1_AMOUNT],
                        row[COL_ITEM2_TYPE], row[COL_ITEM2_AMOUNT],
                        row[COL_RESULT_TYPE], row[COL_RESULT_AMOUNT],
                        row[COL_TRADE_COUNT],
                    )

                    # Calculate emerald costs (use actual_result_amount for shulker expansion)
                    emerald_cost_total, emerald_cost_per_unit = _calculate_emerald_cost(
                        item1_id, item1_amount, item2_id, item2_amount,
                        actual_result_amount, trade_count, currency_map,
                    )

                    # Parse coordinate fields
                    try:
                        shop_x = int(row[COL_SHOP_X])
                        shop_y = int(row[COL_SHOP_Y])
                        shop_z = int(row[COL_SHOP_Z])
                    except (ValueError, IndexError):
                        if file_warnings < _MAX_WARNINGS_PER_FILE:
                            logger.warning(f"{filename}:{line_num}: invalid coordinates — skipping")
                            file_warnings += 1
                        continue

                    # Insert trade (OR IGNORE for fingerprint dedup)
                    # Use actual_result_amount for shulker-expanded trades
                    cursor = await db.execute(
                        """INSERT OR IGNORE INTO trades (
                            trade_date, trade_time, player_uuid, shop_uuid,
                            shop_type, shop_world, shop_x, shop_y, shop_z,
                            shop_owner_uuid,
                            item1_id, item1_amount, item2_id, item2_amount,
                            result_item_id, result_amount, trade_count,
                            emerald_cost_total, emerald_cost_per_unit,
                            source_file, source_line, row_fingerprint
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            trade_date, row[COL_TIME], row[COL_PLAYER_UUID], row[COL_SHOP_UUID],
                            row[COL_SHOP_TYPE], row[COL_SHOP_WORLD], shop_x, shop_y, shop_z,
                            row[COL_SHOP_OWNER_UUID] or None,
                            item1_id, item1_amount, item2_id, item2_amount,
                            result_item_id, actual_result_amount, trade_count,
                            emerald_cost_total, emerald_cost_per_unit,
                            filename, line_num, row_fingerprint,
                        ),
                    )

                    if cursor.rowcount > 0:
                        file_new += 1
                        file_affected_dates.add(trade_date)
                    else:
                        file_dup += 1

                    # Upsert player names (update even for duplicate trades to keep names current)
                    player_uuid = row[COL_PLAYER_UUID]
                    player_name = row[COL_PLAYER_NAME]
                    if player_uuid and player_name:
                        await db.execute(
                            """INSERT INTO players (uuid, last_name, last_seen)
                               VALUES (?, ?, ?)
                               ON CONFLICT(uuid) DO UPDATE SET
                                   last_name = excluded.last_name,
                                   last_seen = MAX(last_seen, excluded.last_seen)""",
                            (player_uuid, player_name, trade_date),
                        )

                    # Upsert shop owner names
                    owner_uuid = row[COL_SHOP_OWNER_UUID]
                    owner_name = row[COL_SHOP_OWNER_NAME]
                    if owner_uuid and owner_name:
                        await db.execute(
                            """INSERT INTO players (uuid, last_name, last_seen)
                               VALUES (?, ?, ?)
                               ON CONFLICT(uuid) DO UPDATE SET
                                   last_name = excluded.last_name,
                                   last_seen = MAX(last_seen, excluded.last_seen)""",
                            (owner_uuid, owner_name, trade_date),
                        )

                # Update file record with final state (line_num is last line read)
                stat = await asyncio.to_thread(Path(filepath).stat)
                await _update_file_record(
                    db, filename, trade_date, line_num,
                    stat.st_size, stat.st_mtime_ns,
                )

                await db.commit()

                # Only merge file's affected dates into global set after successful commit
                affected_dates.update(file_affected_dates)

                result.new_trades += file_new
                result.duplicate_trades += file_dup
                result.files_imported += 1

                if file_new > 0:
                    logger.info(f"Imported {filename}: {file_new} new, {file_dup} duplicate trades")

            except Exception as e:
                logger.error(f"Error importing {filename}: {e}", exc_info=True)
                result.errors.append(f"{filename}: {e}")
                try:
                    await db.rollback()
                except Exception:
                    pass

        # Rebuild price summary only for dates that had new trades
        if affected_dates:
            await _rebuild_price_summary(db, affected_dates)

    return result


def _discover_csv_files(csv_directory: str) -> list[tuple[str, str, str]]:
    """
    Discover CSV files matching trades-YYYY-MM-DD.csv in the directory.

    Returns:
        Sorted list of (filepath, trade_date, filename) tuples, ordered by date.
    """
    csv_dir = Path(csv_directory)
    if not csv_dir.exists():
        logger.warning(f"CSV directory does not exist: {csv_directory}")
        return []

    files = []
    for path in csv_dir.glob("trades-*.csv"):
        match = _FILENAME_PATTERN.match(path.name)
        if match:
            files.append((str(path), match.group(1), path.name))

    files.sort(key=lambda x: x[1])  # Sort by date string
    return files


async def _needs_import(
    db: aiosqlite.Connection, filepath: str, filename: str
) -> bool:
    """
    Check if a file needs (re)importing by comparing file stats.

    Returns True if the file is new or has changed since last import.
    Fingerprint-based dedup in the main loop handles any overlap.
    """
    stat = await asyncio.to_thread(Path(filepath).stat)

    row = await db.execute_fetchall(
        "SELECT file_size, file_mtime_ns FROM imported_files WHERE filename = ?",
        (filename,),
    )

    if not row:
        return True

    stored_size, stored_mtime = row[0]
    return not (stored_size == stat.st_size and stored_mtime == stat.st_mtime_ns)


async def _load_item_cache(db: aiosqlite.Connection) -> dict[str, int]:
    """Load all items into a {item_key: id} cache."""
    rows = await db.execute_fetchall("SELECT item_key, id FROM items")
    return {row[0]: row[1] for row in rows}


async def _build_currency_map(db: aiosqlite.Connection) -> dict[int, float]:
    """Load currency items into a {item_id: emerald_value} map."""
    rows = await db.execute_fetchall(
        "SELECT id, emerald_value FROM items WHERE is_currency = 1"
    )
    return {row[0]: row[1] for row in rows}


async def _upsert_item(
    db: aiosqlite.Connection,
    material_type: str,
    metadata: str,
    plugin_keys: list,
    item_cache: dict[str, int],
    currency_config: dict[str, float],
    currency_map: dict[int, float],
    result: ImportResult,
) -> int | None:
    """
    Parse metadata, insert item if new, return item_id.

    Updates item_cache, currency_map, and result.new_items as needed.
    """
    try:
        parsed = parse_item_metadata(material_type, metadata, plugin_keys)
    except Exception:
        # Fall back to vanilla identity on parse failure
        parsed = {
            "display_name": None,
            "plugin_id": None,
            "item_key": f"minecraft:{material_type.lower()}",
            "search_name": material_type.replace("_", " ").lower(),
        }

    item_key = parsed["item_key"]

    # Check cache first
    if item_key in item_cache:
        return item_cache[item_key]

    # Determine currency status from config
    is_currency = 1 if item_key in currency_config else 0
    emerald_value = currency_config.get(item_key, 0.0)

    cursor = await db.execute(
        """INSERT OR IGNORE INTO items (item_key, material_type, display_name, plugin_id, search_name, is_currency, emerald_value)
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            item_key,
            material_type,
            parsed["display_name"],
            parsed["plugin_id"],
            parsed["search_name"],
            is_currency,
            emerald_value,
        ),
    )

    # Track if we actually inserted a new item
    was_inserted = cursor.rowcount > 0

    # Fetch the id (whether we just inserted or it already existed)
    rows = await db.execute_fetchall(
        "SELECT id FROM items WHERE item_key = ?", (item_key,)
    )
    if not rows:
        return None

    item_id = rows[0][0]
    item_cache[item_key] = item_id

    # Only count as new if we actually inserted
    if was_inserted:
        result.new_items += 1

    # Update currency map if this is a currency item
    if is_currency:
        currency_map[item_id] = emerald_value

    return item_id


def _compute_fingerprint(
    trade_date: str,
    time: str,
    player_uuid: str,
    shop_uuid: str,
    item1_type: str,
    item1_amount: str,
    item2_type: str,
    item2_amount: str,
    result_type: str,
    result_amount: str,
    trade_count: str,
) -> str:
    """
    Compute SHA-256 fingerprint of trade fields for deduplication.

    Uses pipe-delimited fields. Excludes source_file/line so identical
    trades deduplicate across files.
    """
    raw = "|".join([
        trade_date, time, player_uuid, shop_uuid,
        item1_type, item1_amount,
        item2_type, item2_amount,
        result_type, result_amount, trade_count,
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _calculate_emerald_cost(
    item1_id: int | None,
    item1_amount: int,
    item2_id: int | None,
    item2_amount: int | None,
    result_amount: int,
    trade_count: int,
    currency_map: dict[int, float],
) -> tuple[float | None, float | None]:
    """
    Calculate emerald cost from currency inputs.

    Returns:
        (emerald_cost_total, emerald_cost_per_unit) or (None, None) if no currency inputs.
    """
    cost_per_trade = 0.0
    has_currency = False

    if item1_id and item1_id in currency_map:
        cost_per_trade += item1_amount * currency_map[item1_id]
        has_currency = True

    if item2_id is not None and item2_id in currency_map and item2_amount is not None:
        cost_per_trade += item2_amount * currency_map[item2_id]
        has_currency = True

    if not has_currency:
        return (None, None)

    emerald_cost_per_unit = cost_per_trade / result_amount if result_amount else None
    emerald_cost_total = cost_per_trade * trade_count

    return (emerald_cost_total, emerald_cost_per_unit)


async def _update_file_record(
    db: aiosqlite.Connection,
    filename: str,
    file_date: str,
    last_line: int,
    file_size: int,
    file_mtime_ns: int,
) -> None:
    """Insert or update the imported_files record for a file."""
    await db.execute(
        """INSERT INTO imported_files (filename, file_date, last_line_imported, file_size, file_mtime_ns)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(filename) DO UPDATE SET
            last_line_imported = excluded.last_line_imported,
            file_size = excluded.file_size,
            file_mtime_ns = excluded.file_mtime_ns,
            imported_at = CURRENT_TIMESTAMP""",
        (filename, file_date, last_line, file_size, file_mtime_ns),
    )


async def _rebuild_price_summary(db: aiosqlite.Connection, affected_dates: set[str]) -> None:
    """Incrementally rebuild price_summary for dates that had new trades."""
    if not affected_dates:
        return

    placeholders = ",".join("?" for _ in affected_dates)
    date_params = tuple(sorted(affected_dates))

    await db.execute(
        f"DELETE FROM price_summary WHERE trade_date IN ({placeholders})",
        date_params,
    )
    await db.execute(
        f"""INSERT INTO price_summary (item_id, trade_date, avg_price, min_price, max_price, total_volume, trade_count)
        SELECT result_item_id, trade_date,
            AVG(emerald_cost_per_unit), MIN(emerald_cost_per_unit), MAX(emerald_cost_per_unit),
            SUM(result_amount * trade_count), COUNT(*)
        FROM trades
        WHERE shop_type != 'admin' AND emerald_cost_per_unit IS NOT NULL
            AND result_item_id IS NOT NULL AND trade_date IN ({placeholders})
        GROUP BY result_item_id, trade_date""",
        date_params,
    )
    await db.commit()
    logger.info(f"Price summary updated for {len(affected_dates)} date(s)")
