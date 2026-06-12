#!/usr/bin/env python3
"""
FastMCP server exposing INXOTIVE YouTube tools as MCP tools.

Provides:
  - search_youtube_videos
  - get_video_info
  - get_video_transcript
  - get_video_comments
  - analyze_video
  - process_youtube_url

Run: python mcp_youtube_server.py
Transport: stdio (compatible with any MCP host / INXOTIVE HUB).
"""

import asyncio
import logging
import os
import re
import sys
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Logging — stderr so it doesn't contaminate stdio transport
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="[yt-mcp] %(levelname)s %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Load NINE_ROUTER_API_KEY from ~/.env_secrets
# ---------------------------------------------------------------------------

ENV_SECRETS_PATH = Path.home() / ".env_secrets"


def _load_secrets():
    """Load key=value lines from ~/.env_secrets into os.environ if not already set."""
    if not ENV_SECRETS_PATH.exists():
        logger.warning(".env_secrets not found at %s", ENV_SECRETS_PATH)
        return
    with open(ENV_SECRETS_PATH) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key not in os.environ:
                os.environ[key] = value


_load_secrets()

# ---------------------------------------------------------------------------
# Import youtube_service module
# ---------------------------------------------------------------------------

sys.path.insert(0, str(Path.home() / "market-api"))

import youtube_service as yt  # noqa: E402

# Call init at import time
yt.init_youtube()

# Re-export the key names (cosmetic — the module reference is used directly)
search_youtube = yt.search_youtube
fetch_video_info = yt.fetch_video_info
extract_transcript_async = yt.extract_transcript_async
fetch_youtube_comments = yt.fetch_youtube_comments
analyze_youtube_video = yt.analyze_youtube_video
is_youtube_url = yt.is_youtube_url
extract_youtube_id = yt.extract_youtube_id

# ---------------------------------------------------------------------------
# FastMCP server
# ---------------------------------------------------------------------------

mcp = FastMCP("inxotive-youtube")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_count(n: int) -> str:
    """Format large numbers with K/M suffixes."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _fmt_duration(seconds: int) -> str:
    """Format duration seconds to h:mm:ss or mm:ss."""
    if not seconds:
        return "Unknown"
    seconds = int(seconds)
    h, r = divmod(seconds, 3600)
    m, s = divmod(r, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def search_youtube_videos(query: str, max_results: int = 5) -> str:
    """Search YouTube videos. Returns title, channel, duration, views for each result."""
    try:
        result = await yt.search_youtube(query, max_results=max_results)
        if not result.get("success"):
            return f"Search failed: {result.get('error', 'Unknown error')}"

        items = result.get("results", [])
        if not items:
            return f"No results found for \"{query}\"."

        lines = [
            f"Search results for \"{query}\" ({len(items)} videos):",
            "─" * 60,
        ]
        for i, v in enumerate(items, 1):
            title = v.get("title", "Untitled")
            channel = v.get("channel", "Unknown channel")
            dur = v.get("formatted_duration", "Unknown")
            views = _fmt_count(v.get("view_count", 0))
            url = v.get("url", f"https://www.youtube.com/watch?v={v.get('video_id', '')}")
            lines.append(f"{i}. {title}")
            lines.append(f"   Channel: {channel}  |  Duration: {dur}  |  Views: {views}")
            lines.append(f"   {url}")
            lines.append("")

        return "\n".join(lines).strip()

    except Exception as e:
        logger.exception("search_youtube_videos error")
        return f"Error searching YouTube: {e}"


@mcp.tool()
async def get_video_info(video_id: str) -> str:
    """Get detailed info about a YouTube video: title, channel, views, likes, duration, description."""
    try:
        info = await yt.fetch_video_info(video_id)
        if not info.get("success"):
            return f"Failed to fetch video info: {info.get('error', 'Unknown error')}"

        lines = [
            f"Title: {info.get('title', 'Unknown')}",
            f"Channel: {info.get('channel', 'Unknown')}",
            f"Duration: {info.get('formatted_duration', 'Unknown')}",
            f"Views: {_fmt_count(info.get('view_count', 0))}",
            f"Likes: {_fmt_count(info.get('like_count', 0))}",
            f"Comments: {_fmt_count(info.get('comment_count', 0))}",
            f"Uploaded: {info.get('upload_date_formatted', 'Unknown')}",
            f"URL: https://www.youtube.com/watch?v={video_id}",
            "",
            "Description:",
            info.get("description", "(no description)")[:1500],
        ]

        tags = info.get("tags", [])
        if tags:
            lines.extend(["", f"Tags: {', '.join(tags[:10])}"])

        categories = info.get("categories", [])
        if categories:
            lines.extend(["", f"Categories: {', '.join(categories)}"])

        return "\n".join(lines)

    except Exception as e:
        logger.exception("get_video_info error")
        return f"Error fetching video info: {e}"


@mcp.tool()
async def get_video_transcript(video_id: str) -> str:
    """Get the transcript of a YouTube video with timestamps."""
    try:
        url = f"https://www.youtube.com/watch?v={video_id}"
        transcript_data = await yt.extract_transcript_async(url, video_id)
        if not transcript_data.get("success"):
            return f"Transcript unavailable: {transcript_data.get('error', 'No transcript available for this video.')}"

        language = transcript_data.get("language", "unknown")
        segments = transcript_data.get("segments", [])
        full_text = transcript_data.get("transcript", "")

        lines = [
            f"Video ID: {video_id}",
            f"Language: {language}",
            f"Segments: {len(segments)}",
            "─" * 60,
        ]

        if segments:
            for seg in segments[:100]:  # cap at 100 segments
                ts = seg.get("timestamp", "00:00")
                text = seg.get("text", "")
                lines.append(f"[{ts}] {text}")
            if len(segments) > 100:
                lines.append(f"\n... and {len(segments) - 100} more segments.")
        else:
            lines.append(full_text[:5000] if full_text else "(empty transcript)")

        return "\n".join(lines)

    except Exception as e:
        logger.exception("get_video_transcript error")
        return f"Error fetching transcript: {e}"


@mcp.tool()
async def get_video_comments(video_id: str, max_comments: int = 10) -> str:
    """Get top comments from a YouTube video."""
    try:
        comments_data = await yt.fetch_youtube_comments(video_id, max_comments=max_comments)
        if not comments_data.get("success"):
            return f"Comments unavailable: {comments_data.get('error', 'Could not fetch comments.')}"

        comments = comments_data.get("comments", [])
        if not comments:
            return f"No comments found for video {video_id} (or comments are disabled)."

        lines = [
            f"Top {len(comments)} comments for {comments_data.get('title', video_id)}",
            f"Channel: {comments_data.get('channel', 'Unknown')}",
            "─" * 60,
        ]
        for i, c in enumerate(comments, 1):
            author = c.get("author", "Anonymous")
            text = c.get("text", "")
            likes = c.get("likes", 0)
            lines.append(f"{i}. @{author}  [{likes} like{'s' if likes != 1 else ''}]")
            lines.append(f"   {text[:300]}")
            lines.append("")

        return "\n".join(lines).strip()

    except Exception as e:
        logger.exception("get_video_comments error")
        return f"Error fetching comments: {e}"


@mcp.tool()
async def analyze_video(video_id_or_url: str) -> str:
    """Full AI analysis of a YouTube video: summary, key points, analysis, and viewer reception.
    Uses 9Router deepseek-v4-flash."""
    # Parse input: accept either a raw video_id or a full YouTube URL
    video_id: Optional[str] = None
    if yt.is_youtube_url(video_id_or_url):
        video_id = yt.extract_youtube_id(video_id_or_url)
    elif re.match(r"^[a-zA-Z0-9_-]{11}$", video_id_or_url):
        video_id = video_id_or_url
    else:
        return (
            f"Invalid input: \"{video_id_or_url}\". "
            "Please provide a YouTube URL or an 11-character video ID."
        )

    if not video_id:
        return "Could not extract video ID from the provided input."

    try:
        analysis_result = await yt.analyze_youtube_video(
            video_id,
            include_comments=True,
            max_comments=10,
            model="max-free",
        )

        if not analysis_result.get("success"):
            return f"Analysis failed: {analysis_result.get('error', 'Unknown error')}"

        analysis_text = analysis_result.get("analysis", "")
        if not analysis_text or analysis_text.startswith("Analysis unavailable"):
            return (
                f"Analysis could not be completed. {analysis_text}\n\n"
                f"Video info: https://www.youtube.com/watch?v={video_id}"
            )

        # Prepend a header with basic info
        info = analysis_result.get("info") or {}
        header = (
            f"Video: {info.get('title', 'Unknown')}\n"
            f"Channel: {info.get('channel', 'Unknown')}\n"
            f"Duration: {info.get('formatted_duration', 'Unknown')}\n"
            f"Views: {_fmt_count(info.get('view_count', 0))} | "
            f"Likes: {_fmt_count(info.get('like_count', 0))}\n"
            f"Transcript: {'Available' if analysis_result.get('transcript', {}).get('available') else 'Unavailable'}\n"
            f"Comments analyzed: {analysis_result.get('comments', {}).get('count', 0)}\n"
            f"{'─' * 60}\n"
        )

        return header + (analysis_text or "(no analysis returned)")

    except Exception as e:
        logger.exception("analyze_video error")
        return f"Error analyzing video: {e}"


@mcp.tool()
async def process_youtube_url(url: str) -> str:
    """Auto-detect if a URL is a YouTube video. Returns video info and transcript availability."""
    try:
        if not yt.is_youtube_url(url):
            return f"\"{url}\" is not a recognised YouTube URL."

        video_id = yt.extract_youtube_id(url)
        if not video_id:
            playlist_id = yt.extract_playlist_id(url)
            if playlist_id:
                return (
                    f"URL is a YouTube playlist (ID: {playlist_id}).\n"
                    "Use this tool with an individual video URL for video info."
                )
            return f"URL looks like YouTube but could not extract video ID: {url}"

        # Fetch info + transcript in parallel
        info_task = yt.fetch_video_info(video_id)
        transcript_task = yt.extract_transcript_async(url, video_id)
        info, transcript_data = await asyncio.gather(info_task, transcript_task)

        lines = [
            "YouTube Video detected!",
            "─" * 60,
        ]

        if info.get("success"):
            lines.extend([
                f"Title: {info.get('title', 'Unknown')}",
                f"Channel: {info.get('channel', 'Unknown')}",
                f"Duration: {info.get('formatted_duration', 'Unknown')}",
                f"Views: {_fmt_count(info.get('view_count', 0))}",
                f"Likes: {_fmt_count(info.get('like_count', 0))}",
                f"Uploaded: {info.get('upload_date_formatted', 'Unknown')}",
            ])
        else:
            lines.append(f"Info fetch failed: {info.get('error', 'Unknown error')}")

        lines.append("")
        if transcript_data.get("success"):
            lang = transcript_data.get("language", "unknown")
            segs = transcript_data.get("segment_count", 0)
            lines.append(
                f"Transcript: Available  |  Language: {lang}  |  {segs} segments"
            )
        else:
            lines.append(f"Transcript: {transcript_data.get('error', 'Unavailable')}")

        lines.extend([
            "",
            f"Video ID: {video_id}",
            f"URL: https://www.youtube.com/watch?v={video_id}",
            "",
            "Available tools:",
            "  - get_video_info        (detailed metadata)",
            "  - get_video_transcript  (timestamped transcript)",
            "  - get_video_comments    (top comments)",
            "  - analyze_video         (full AI analysis)",
        ])

        return "\n".join(lines)

    except Exception as e:
        logger.exception("process_youtube_url error")
        return f"Error processing YouTube URL: {e}"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logger.info("Starting INXOTIVE YouTube MCP server (stdio transport)")
    mcp.run(transport="stdio")
