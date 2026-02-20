"""Price history chart generation using matplotlib."""

import asyncio
import io
from datetime import datetime


async def generate_price_chart(
    rows: list[tuple],
    item_name: str,
    embed_color: int,
) -> io.BytesIO:
    """Generate a price history line chart.

    Args:
        rows: List of (trade_date_str, avg_price, min_price, max_price) tuples,
              ordered by date ascending.
        item_name: Item display name for the chart title.
        embed_color: Integer color to use for the line.

    Returns:
        BytesIO buffer containing the PNG image.
    """
    return await asyncio.to_thread(_render_chart, rows, item_name, embed_color)


def _render_chart(
    rows: list[tuple],
    item_name: str,
    embed_color: int,
) -> io.BytesIO:
    """Synchronous chart rendering (CPU-bound)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    # Parse data
    dates = []
    avgs = []
    mins = []
    maxs = []
    for trade_date, avg_price, min_price, max_price in rows:
        dates.append(datetime.strptime(trade_date, "%Y-%m-%d"))
        avgs.append(avg_price)
        mins.append(min_price)
        maxs.append(max_price)

    # Convert embed_color int to RGB tuple (0-1 range)
    r = ((embed_color >> 16) & 0xFF) / 255
    g = ((embed_color >> 8) & 0xFF) / 255
    b = (embed_color & 0xFF) / 255
    line_color = (r, g, b)

    # Dark theme matching Discord
    bg_color = "#2B2D31"
    text_color = "#FFFFFF"
    grid_color = "#3F4147"
    fill_alpha = 0.15

    fig, ax = plt.subplots(figsize=(7, 3), dpi=100)
    fig.patch.set_facecolor(bg_color)
    ax.set_facecolor(bg_color)

    # Plot avg line and min/max range
    ax.plot(dates, avgs, color=line_color, linewidth=2, label="Avg")
    ax.fill_between(dates, mins, maxs, color=line_color, alpha=fill_alpha, label="Min/Max")

    # Formatting
    ax.set_title(item_name, color=text_color, fontsize=12, pad=8)
    ax.set_ylabel("Emeralds", color=text_color, fontsize=10)
    ax.tick_params(colors=text_color, labelsize=8)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=3, maxticks=10))
    ax.grid(True, color=grid_color, linewidth=0.5, alpha=0.5)

    for spine in ax.spines.values():
        spine.set_color(grid_color)

    fig.autofmt_xdate(rotation=30, ha="right")
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=bg_color, bbox_inches="tight")
    buf.seek(0)
    plt.close(fig)
    return buf
