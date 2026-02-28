"""Internal tool: chart/image generation via matplotlib.

Generates charts (line, bar, pie, scatter, area) from data, saves as PNG,
and auto-uploads to the Slack thread. Standalone from file_generator.py
but follows the same internal tool pattern.

Architecture:
    lucy_generate_chart → matplotlib → PNG file → Slack upload

Chart types:
    - line: time series, trends
    - bar: comparisons, categories
    - pie: distribution, proportions
    - scatter: correlations
    - area: cumulative/stacked trends
"""

from __future__ import annotations

import io
import json
import tempfile
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()


# ═══════════════════════════════════════════════════════════════════════════
# TOOL DEFINITIONS
# ═══════════════════════════════════════════════════════════════════════════

def get_chart_tool_definitions() -> list[dict[str, Any]]:
    """Return OpenAI-format tool definitions for chart generation."""
    return [
        {
            "type": "function",
            "function": {
                "name": "lucy_generate_chart",
                "description": (
                    "Generate a chart/graph image from data. Supports: line, bar, pie, "
                    "scatter, and area charts. The chart is automatically uploaded to the "
                    "current Slack thread. Use this when data is better visualized than "
                    "described in text — trends, comparisons, distributions."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "chart_type": {
                            "type": "string",
                            "enum": ["line", "bar", "pie", "scatter", "area"],
                            "description": "Type of chart to generate.",
                        },
                        "title": {
                            "type": "string",
                            "description": "Chart title.",
                        },
                        "data": {
                            "type": "object",
                            "description": (
                                "Chart data. Structure depends on chart_type:\n"
                                "line/area: {labels: [...], datasets: [{label: str, values: [...]}]}\n"
                                "bar: {labels: [...], datasets: [{label: str, values: [...]}]}\n"
                                "pie: {labels: [...], values: [...]}\n"
                                "scatter: {datasets: [{label: str, points: [[x,y], ...]}]}"
                            ),
                        },
                        "x_label": {
                            "type": "string",
                            "description": "X-axis label.",
                        },
                        "y_label": {
                            "type": "string",
                            "description": "Y-axis label.",
                        },
                        "size": {
                            "type": "string",
                            "enum": ["small", "medium", "large"],
                            "description": "Chart size. Default: medium.",
                        },
                    },
                    "required": ["chart_type", "title", "data"],
                },
            },
        },
    ]


# ═══════════════════════════════════════════════════════════════════════════
# CHART GENERATION
# ═══════════════════════════════════════════════════════════════════════════

# Professional color palette (Tailwind-inspired)
_COLORS = [
    "#3b82f6",  # blue-500
    "#ef4444",  # red-500
    "#10b981",  # emerald-500
    "#f59e0b",  # amber-500
    "#8b5cf6",  # violet-500
    "#06b6d4",  # cyan-500
    "#f97316",  # orange-500
    "#ec4899",  # pink-500
]

_SIZES = {
    "small": (8, 5),
    "medium": (10, 6),
    "large": (14, 8),
}


async def generate_chart(
    chart_type: str,
    title: str,
    data: dict[str, Any],
    x_label: str = "",
    y_label: str = "",
    size: str = "medium",
) -> Path:
    """Generate a chart and return the file path."""
    try:
        import matplotlib
        matplotlib.use("Agg")  # Non-interactive backend
        import matplotlib.pyplot as plt
        import matplotlib.ticker as ticker
    except ImportError:
        raise ImportError(
            "matplotlib is required for chart generation. "
            "Install with: pip install matplotlib"
        )

    fig_size = _SIZES.get(size, _SIZES["medium"])
    fig, ax = plt.subplots(figsize=fig_size, dpi=150)

    # Apply professional styling
    fig.patch.set_facecolor("#ffffff")
    ax.set_facecolor("#fafafa")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#e5e7eb")
    ax.spines["bottom"].set_color("#e5e7eb")
    ax.tick_params(colors="#6b7280", labelsize=9)
    ax.grid(axis="y", alpha=0.3, color="#d1d5db", linestyle="--")

    labels = data.get("labels", [])
    datasets = data.get("datasets", [])
    values = data.get("values", [])
    points = data.get("points", [])

    if chart_type == "line":
        _draw_line(ax, labels, datasets)
    elif chart_type == "bar":
        _draw_bar(ax, labels, datasets)
    elif chart_type == "pie":
        _draw_pie(ax, labels, values, fig)
    elif chart_type == "scatter":
        _draw_scatter(ax, datasets)
    elif chart_type == "area":
        _draw_area(ax, labels, datasets)
    else:
        plt.close(fig)
        raise ValueError(f"Unsupported chart type: {chart_type}")

    # Title
    ax.set_title(title, fontsize=14, fontweight="bold", color="#1f2937", pad=15)

    # Axis labels
    if x_label:
        ax.set_xlabel(x_label, fontsize=10, color="#4b5563", labelpad=8)
    if y_label:
        ax.set_ylabel(y_label, fontsize=10, color="#4b5563", labelpad=8)

    # Legend (if multiple datasets)
    if len(datasets) > 1 and chart_type != "pie":
        ax.legend(
            loc="upper left",
            frameon=True,
            facecolor="white",
            edgecolor="#e5e7eb",
            fontsize=9,
        )

    plt.tight_layout()

    # Save to temp file
    tmp = tempfile.NamedTemporaryFile(
        suffix=".png", prefix="lucy_chart_", delete=False
    )
    fig.savefig(tmp.name, format="png", bbox_inches="tight", facecolor="white")
    plt.close(fig)

    logger.info(
        "chart_generated",
        chart_type=chart_type,
        title=title,
        path=tmp.name,
    )

    return Path(tmp.name)


def _draw_line(ax: Any, labels: list, datasets: list[dict]) -> None:
    """Draw a line chart."""
    for i, ds in enumerate(datasets):
        color = _COLORS[i % len(_COLORS)]
        vals = ds.get("values", [])
        label = ds.get("label", f"Series {i + 1}")
        ax.plot(
            range(len(vals)), vals,
            color=color,
            linewidth=2,
            marker="o",
            markersize=4,
            label=label,
        )
    if labels:
        ax.set_xticks(range(len(labels)))
        ax.set_xticklabels(labels, rotation=45 if len(labels) > 6 else 0, ha="right")


def _draw_bar(ax: Any, labels: list, datasets: list[dict]) -> None:
    """Draw a bar chart (grouped if multiple datasets)."""
    import numpy as np

    n_groups = len(labels)
    n_datasets = len(datasets)

    if n_datasets == 0 or n_groups == 0:
        return

    bar_width = 0.8 / n_datasets
    x = np.arange(n_groups)

    for i, ds in enumerate(datasets):
        color = _COLORS[i % len(_COLORS)]
        vals = ds.get("values", [])
        label = ds.get("label", f"Series {i + 1}")
        offset = (i - n_datasets / 2 + 0.5) * bar_width
        bars = ax.bar(
            x + offset, vals,
            width=bar_width,
            color=color,
            label=label,
            edgecolor="white",
            linewidth=0.5,
        )
        # Value labels on bars
        if n_groups <= 12:
            for bar, val in zip(bars, vals):
                ax.text(
                    bar.get_x() + bar.get_width() / 2,
                    bar.get_height() + max(vals) * 0.01,
                    f"{val:,.0f}" if isinstance(val, (int, float)) else str(val),
                    ha="center", va="bottom",
                    fontsize=7, color="#6b7280",
                )

    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45 if n_groups > 6 else 0, ha="right")


def _draw_pie(ax: Any, labels: list, values: list, fig: Any) -> None:
    """Draw a pie chart."""
    colors = _COLORS[:len(values)]

    # Hide axes for pie
    ax.axis("equal")
    ax.spines["left"].set_visible(False)
    ax.spines["bottom"].set_visible(False)
    ax.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)
    ax.grid(False)

    wedges, texts, autotexts = ax.pie(
        values,
        labels=labels,
        colors=colors,
        autopct="%1.1f%%",
        startangle=90,
        pctdistance=0.8,
        wedgeprops={"edgecolor": "white", "linewidth": 2},
    )

    for text in texts:
        text.set_fontsize(9)
        text.set_color("#4b5563")
    for autotext in autotexts:
        autotext.set_fontsize(8)
        autotext.set_color("white")
        autotext.set_fontweight("bold")


def _draw_scatter(ax: Any, datasets: list[dict]) -> None:
    """Draw a scatter plot."""
    for i, ds in enumerate(datasets):
        color = _COLORS[i % len(_COLORS)]
        pts = ds.get("points", [])
        label = ds.get("label", f"Series {i + 1}")
        if pts:
            x_vals = [p[0] for p in pts]
            y_vals = [p[1] for p in pts]
            ax.scatter(
                x_vals, y_vals,
                color=color,
                s=40,
                alpha=0.7,
                edgecolors="white",
                linewidth=0.5,
                label=label,
            )


def _draw_area(ax: Any, labels: list, datasets: list[dict]) -> None:
    """Draw an area chart."""
    for i, ds in enumerate(datasets):
        color = _COLORS[i % len(_COLORS)]
        vals = ds.get("values", [])
        label = ds.get("label", f"Series {i + 1}")
        ax.fill_between(
            range(len(vals)), vals,
            alpha=0.3, color=color, label=label,
        )
        ax.plot(
            range(len(vals)), vals,
            color=color, linewidth=1.5,
        )
    if labels:
        ax.set_xticks(range(len(labels)))
        ax.set_xticklabels(labels, rotation=45 if len(labels) > 6 else 0, ha="right")


# ═══════════════════════════════════════════════════════════════════════════
# TOOL DISPATCH
# ═══════════════════════════════════════════════════════════════════════════

async def execute_chart_tool(
    tool_name: str,
    parameters: dict[str, Any],
    slack_client: Any = None,
    channel_id: str = "",
    thread_ts: str = "",
) -> dict[str, Any]:
    """Execute a chart tool and optionally upload to Slack."""

    if tool_name != "lucy_generate_chart":
        return {"error": f"Unknown chart tool: {tool_name}"}

    chart_type = parameters.get("chart_type", "")
    title = parameters.get("title", "Chart")
    data = parameters.get("data", {})
    x_label = parameters.get("x_label", "")
    y_label = parameters.get("y_label", "")
    size = parameters.get("size", "medium")

    if not chart_type:
        return {"error": "chart_type is required."}
    if not data:
        return {"error": "data is required."}

    try:
        path = await generate_chart(
            chart_type=chart_type,
            title=title,
            data=data,
            x_label=x_label,
            y_label=y_label,
            size=size,
        )
    except ImportError as e:
        return {"error": str(e)}
    except Exception as e:
        logger.error("chart_generation_failed", error=str(e))
        return {"error": f"Chart generation failed: {str(e)[:200]}"}

    result: dict[str, Any] = {
        "result": f"Generated {chart_type} chart: {title}",
        "file_path": str(path),
    }

    # Auto-upload to Slack if context available
    if slack_client and channel_id:
        try:
            upload_result = await slack_client.files_upload_v2(
                file=str(path),
                channel=channel_id,
                thread_ts=thread_ts or None,
                title=title,
                initial_comment=f"📊 {title}",
            )
            result["uploaded"] = True
            logger.info("chart_uploaded_to_slack", title=title)
        except Exception as e:
            # Try v1 fallback
            try:
                upload_result = await slack_client.files_upload(
                    file=str(path),
                    channels=channel_id,
                    thread_ts=thread_ts or None,
                    title=title,
                    initial_comment=f"📊 {title}",
                )
                result["uploaded"] = True
            except Exception as e2:
                logger.warning("chart_upload_failed", error=str(e2))
                result["uploaded"] = False
                result["upload_error"] = str(e2)[:100]

    return result
