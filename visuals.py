"""
Inline Visuals — chart generation for hub chat.
Generates chart images (PNG) and returns as base64/data URIs.

Usage: render_chart(type, data, title) -> str (markdown with embedded image)
"""

import base64
import io
import logging
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger("visuals")

# Try matplotlib — if not available, return text-based charts
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False
    logger.warning("matplotlib not installed — using text-based charts")


def _fig_to_b64(fig) -> str:
    """Convert matplotlib figure to base64 PNG."""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100, bbox_inches="tight")
    buf.seek(0)
    img_b64 = base64.b64encode(buf.read()).decode()
    plt.close(fig)
    return img_b64


def render_chart(chart_type: str, data: Dict, title: str = "") -> str:
    """Render a chart and return markdown with embedded image or text fallback.

    chart_type: 'line', 'bar', 'pie', 'area'
    data: {"labels": [...], "values": [...], "values2": [...]} (values2 for comparison)
    title: optional chart title
    """
    if MATPLOTLIB_AVAILABLE:
        return _render_matplotlib(chart_type, data, title)
    return _render_text(chart_type, data, title)


def _render_matplotlib(chart_type: str, data: Dict, title: str = "") -> str:
    """Render chart using matplotlib."""
    labels = data.get("labels", [])
    values = data.get("values", [])
    values2 = data.get("values2", [])

    if not labels or not values:
        return "_No data to chart_"

    fig, ax = plt.subplots(figsize=(6, 3.5))
    fig.patch.set_facecolor("#1e293b")
    ax.set_facecolor("#1e293b")

    colors = ["#6366f1", "#22c55e", "#f59e0b", "#ef4444", "#ec4899", "#06b6d4"]
    title_color = "#e2e8f0"
    label_color = "#94a3b8"

    if chart_type == "pie":
        wedges, texts, autotexts = ax.pie(
            values, labels=labels, autopct="%1.0f%%",
            colors=colors[:len(values)], startangle=90,
            textprops={"color": label_color, "fontsize": 9},
        )
        for t in autotexts:
            t.set_color("white")
        ax.set_title(title, color=title_color, fontsize=11, pad=12)

    elif chart_type == "bar":
        x = range(len(labels))
        bars = ax.bar(x, values, color=colors[0], width=0.6, alpha=0.85)
        if values2:
            bars2 = ax.bar(x, values2, color=colors[1], width=0.4, alpha=0.7, align="edge")
            ax.legend(["Series 1", "Series 2"], loc="upper right",
                       facecolor="#334155", labelcolor=label_color, fontsize=8)
        ax.set_xticks(x)
        ax.set_xticklabels(labels, fontsize=8, color=label_color, rotation=25, ha="right")
        ax.set_title(title, color=title_color, fontsize=11)
        ax.tick_params(colors=label_color, labelsize=8)
        for spine in ax.spines.values():
            spine.set_color("#334155")

    elif chart_type == "area":
        x = range(len(labels))
        ax.fill_between(x, values, alpha=0.3, color=colors[0])
        ax.plot(x, values, color=colors[0], linewidth=2, marker="o", markersize=3)
        if values2:
            ax.fill_between(x, values2, alpha=0.3, color=colors[1])
            ax.plot(x, values2, color=colors[1], linewidth=2, marker="s", markersize=3)
            ax.legend(["Series 1", "Series 2"], loc="upper left",
                       facecolor="#334155", labelcolor=label_color, fontsize=8)
        ax.set_xticks(x)
        ax.set_xticklabels(labels, fontsize=8, color=label_color)
        ax.set_title(title, color=title_color, fontsize=11)
        ax.tick_params(colors=label_color, labelsize=8)
        for spine in ax.spines.values():
            spine.set_color("#334155")

    else:  # line chart (default)
        x = range(len(labels))
        ax.plot(x, values, color=colors[0], linewidth=2, marker="o", markersize=4)
        if values2:
            ax.plot(x, values2, color=colors[1], linewidth=2, marker="s", markersize=4, linestyle="--")
            ax.legend(["Series 1", "Series 2"], loc="upper left",
                       facecolor="#334155", labelcolor=label_color, fontsize=8)
        ax.set_xticks(x)
        ax.set_xticklabels(labels, fontsize=8, color=label_color, rotation=25, ha="right")
        ax.set_title(title, color=title_color, fontsize=11)
        ax.tick_params(colors=label_color, labelsize=8)
        for spine in ax.spines.values():
            spine.set_color("#334155")

    plt.tight_layout()
    img_b64 = _fig_to_b64(fig)
    return f"![Chart: {title}](data:image/png;base64,{img_b64})"


def _render_text(chart_type: str, data: Dict, title: str = "") -> str:
    """Text-based fallback chart using Unicode blocks."""
    labels = data.get("labels", [])
    values = data.get("values", [])

    if not labels or not values:
        return "_No data_"

    max_val = max(v for v in values if v is not None) or 1
    bar_len = 25
    lines = [f"**{title}**\n"] if title else []

    for i, (label, val) in enumerate(zip(labels, values)):
        if val is None:
            continue
        filled = max(1, int((val / max_val) * bar_len))
        bar = "█" * filled + "░" * (bar_len - filled)
        lines.append(f"{label:>12} │{bar}│ {val}")

    return "```\n" + "\n".join(lines) + "\n```"
