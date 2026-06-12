"""
Autodream — Memory Consolidation Engine for INXOTIVE.
Background sub-agent that prunes, merges, and maintains memory files.

Functions:
- consolidate_memory() — full sweep: fix dates, merge duplicates, compress old entries
- daily_consolidate() — lightweight daily run
- generate_insights() — usage patterns report (/insights equivalent)

Runs on schedule via systemd timer or triggered from hub.
"""

import json
import logging
import os
import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("autodream")

MEMORY_DIR = Path.home() / ".claude" / "projects" / "-home-bisma" / "memory"
MEMORY_INDEX = MEMORY_DIR / "MEMORY.md"

# ── Date patterns ──

RELATIVE_DATE_PATTERNS = [
    (r"(?i)\byesterday\b", lambda: (datetime.now() - timedelta(1)).strftime("%Y-%m-%d")),
    (r"(?i)\btoday\b", lambda: datetime.now().strftime("%Y-%m-%d")),
    (r"(?i)\blast\s+week\b", lambda: (datetime.now() - timedelta(7)).strftime("%Y-%m-%d")),
    (r"(?i)\blast\s+month\b", lambda: (datetime.now() - timedelta(30)).strftime("%Y-%m-%d")),
    (r"(?i)\bnext\s+week\b", lambda: (datetime.now() + timedelta(7)).strftime("%Y-%m-%d")),
    (r"(?i)\bnext\s+month\b", lambda: (datetime.now() + timedelta(30)).strftime("%Y-%m-%d")),
    (r"(?i)\btomorrow\b", lambda: (datetime.now() + timedelta(1)).strftime("%Y-%m-%d")),
    (r"(?i)\btwo\s+days\s+ago\b", lambda: (datetime.now() - timedelta(2)).strftime("%Y-%m-%d")),
    (r"(?i)\bthree\s+days\s+ago\b", lambda: (datetime.now() - timedelta(3)).strftime("%Y-%m-%d")),
    (r"(?i)\blast\s+night\b", lambda: (datetime.now() - timedelta(1)).strftime("%Y-%m-%d")),
    (r"(?i)\bthis\s+morning\b", lambda: datetime.now().strftime("%Y-%m-%d")),
    (r"(?i)\b(?:on\s+)?(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b",
     lambda m: _last_weekday(m.group(0))),
]

MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 7, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _last_weekday(name: str) -> str:
    """Convert weekday name to last occurrence date."""
    days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    today = datetime.now()
    target = days.index(name.strip().lower())
    days_ago = (today.weekday() - target) % 7
    if days_ago == 0:
        days_ago = 7
    return (today - timedelta(days=days_ago)).strftime("%Y-%m-%d")


def _fix_relative_dates(text: str) -> Tuple[str, int]:
    """Replace relative dates with absolute dates. Returns (text, count)."""
    count = 0
    for pattern, repl in RELATIVE_DATE_PATTERNS:
        if callable(repl) and not hasattr(repl, "__code__"):
            # Simple callable with no args
            try:
                new_text, n = re.subn(pattern, repl() if callable(repl) else repl, text)
                count += n
                text = new_text
            except:
                pass
        elif callable(repl):
            try:
                new_text, n = re.subn(pattern, lambda m, r=repl: r(m) if hasattr(r, '__call__') else r, text)
                count += n
                text = new_text
            except:
                pass
        else:
            new_text, n = re.subn(pattern, repl, text)
            count += n
            text = new_text
    return text, count


# ── Main engine ──


async def consolidate_memory() -> Dict:
    """Full memory consolidation sweep.

    Returns dict with stats: files_checked, dates_fixed, duplicates_merged,
    entries_compressed, old_archived.
    """
    stats = {"files_checked": 0, "dates_fixed": 0, "duplicates_removed": 0,
             "lines_compressed": 0, "errors": []}

    if not MEMORY_DIR.exists():
        return {**stats, "error": "Memory dir not found"}

    for fpath in sorted(MEMORY_DIR.glob("*.md")):
        if fpath.name == "MEMORY.md":
            continue
        stats["files_checked"] += 1
        try:
            result = await _consolidate_file(fpath)
            for k, v in result.items():
                if k in stats:
                    stats[k] += v
        except Exception as e:
            stats["errors"].append(f"{fpath.name}: {e}")
            logger.warning("Autodream error on %s: %s", fpath.name, e)

    # Touch timestamp file
    timestamp_file = MEMORY_DIR / ".autodream_last_run"
    timestamp_file.write_text(datetime.now().isoformat())

    logger.info("Autodream complete: %s", stats)
    return stats


async def _consolidate_file(fpath: Path) -> Dict:
    """Consolidate a single memory file."""
    result = {"dates_fixed": 0, "duplicates_removed": 0, "lines_compressed": 0}
    original = fpath.read_text(encoding="utf-8")
    text = original

    # 1. Fix relative dates
    text, n = _fix_relative_dates(text)
    result["dates_fixed"] = n

    # 2. Remove duplicate lines (exact consecutive duplicates)
    lines = text.split("\n")
    deduped = []
    prev = ""
    for line in lines:
        stripped = line.strip()
        if stripped and stripped == prev:
            result["duplicates_removed"] += 1
            continue
        deduped.append(line)
        prev = stripped

    # 3. Compress multiple empty lines to max 2
    cleaned = []
    empty_count = 0
    for line in deduped:
        if not line.strip():
            empty_count += 1
            if empty_count <= 2:
                cleaned.append(line)
            else:
                result["lines_compressed"] += 1
        else:
            empty_count = 0
            cleaned.append(line)

    text = "\n".join(cleaned)

    # 4. Merge duplicate bullet points (same text, different date)
    # Simple approach: for bullet lists, deduplicate by content
    lines = text.split("\n")
    seen_bullets: Dict[str, int] = {}
    merged = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("- ") or stripped.startswith("* "):
            # Extract content after prefix (ignore date prefix)
            content = re.sub(r"^[-*]\s+", "", stripped)
            # If content already seen, skip
            content_key = content.lower().strip()
            if content_key in seen_bullets:
                result["duplicates_removed"] += 1
                continue
            seen_bullets[content_key] = 1
        merged.append(line)
    text = "\n".join(merged)

    # Write back if changed
    if text != original:
        fpath.write_text(text, encoding="utf-8")
        logger.info("  %s: %s", fpath.name, result)

    return result


async def daily_consolidate() -> Dict:
    """Lightweight daily consolidation — faster, less aggressive."""
    stats = {"dates_fixed": 0, "lines_cleaned": 0}

    # Focus on business_daily.md and sales_pipeline.md
    targets = ["business_daily.md", "sales_pipeline.md"]
    for name in targets:
        fpath = MEMORY_DIR / name
        if not fpath.exists():
            continue
        try:
            text = fpath.read_text(encoding="utf-8")
            original = text

            # Fix dates only
            text, n = _fix_relative_dates(text)
            stats["dates_fixed"] += n

            # Compress whitespace
            lines = text.split("\n")
            cleaned = []
            empty_count = 0
            for line in lines:
                if not line.strip():
                    empty_count += 1
                    if empty_count <= 2:
                        cleaned.append(line)
                    else:
                        stats["lines_cleaned"] += 1
                else:
                    empty_count = 0
                    cleaned.append(line)
            text = "\n".join(cleaned)

            if text != original:
                fpath.write_text(text, encoding="utf-8")

        except Exception as e:
            logger.warning("Daily consolidate error on %s: %s", name, e)

    return stats


# ── /insights equivalent ──


async def generate_usage_insights() -> str:
    """Generate usage insights report from memory files and logs.

    Equivalent to Claude Code's /insights command.
    """
    lines = ["# 🧠 INXOTIVE Usage Insights", f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"]

    # 1. Memory stats
    if MEMORY_DIR.exists():
        mem_files = [f for f in MEMORY_DIR.glob("*.md") if f.name != "MEMORY.md"]
        total_lines = 0
        for f in mem_files:
            try:
                total_lines += len(f.read_text().split("\n"))
            except:
                pass
        lines.append(f"## 📊 Memory Stats")
        lines.append(f"- **{len(mem_files)}** memory files")
        lines.append(f"- **{total_lines}** total lines of context")
        lines.append(f"- Last modified: {max((f.stat().st_mtime for f in mem_files), default=0)}")
        lines.append("")

    # 2. Check autodream last run
    ts_file = MEMORY_DIR / ".autodream_last_run"
    if ts_file.exists():
        last_run = ts_file.read_text().strip()[:19]
        lines.append(f"## 🔄 Autodream Status")
        lines.append(f"- Last consolidation: {last_run}")
        lines.append(f"- Next scheduled: {datetime.now().strftime('%Y-%m-%d 00:00')}")
        lines.append("")

    # 3. Identify most-edited files
    if mem_files:
        lines.append("## 📝 Most Active Files")
        sorted_files = sorted(mem_files, key=lambda f: f.stat().st_mtime, reverse=True)[:5]
        for f in sorted_files:
            age_hours = (datetime.now() - datetime.fromtimestamp(f.stat().st_mtime)).total_seconds() / 3600
            if age_hours < 1:
                lines.append(f"- 🔴 **{f.name}** — just now")
            elif age_hours < 24:
                lines.append(f"- 🟡 **{f.name}** — {int(age_hours)}h ago")
            else:
                lines.append(f"- ⚪ **{f.name}** — {int(age_hours / 24)}d ago")
        lines.append("")

    # 4. Check for maintenance
    lines.append("## ⚡ Recommendations")
    lines.append("- Run `/biz-scan` to start a session with full context")
    lines.append("- Run consolidate if memory files are >6 months old")
    lines.append("- Check sales_pipeline.md for stale leads")

    return "\n".join(lines)


# ── Report consolidate stats ──


def format_consolidate_report(stats: Dict) -> str:
    """Format consolidation results as readable report."""
    errors = stats.get("errors", [])
    if not stats.get("files_checked"):
        return "No memory files found."

    report = [
        "## 🔄 Autodream — Memory Consolidation Report",
        f"Files scanned: {stats.get('files_checked', 0)}",
        f"Relative dates → absolute: {stats.get('dates_fixed', 0)}",
        f"Duplicate entries removed: {stats.get('duplicates_removed', 0)}",
        f"Blank lines compressed: {stats.get('lines_compressed', 0)}",
    ]

    if errors:
        report.append(f"\n⚠️ Errors ({len(errors)}):")
        for e in errors[:5]:
            report.append(f"- {e}")

    if stats.get("dates_fixed") + stats.get("duplicates_removed") + stats.get("lines_compressed") == 0:
        report.append("\n✅ No changes needed — all memory files are clean.")

    return "\n".join(report)
