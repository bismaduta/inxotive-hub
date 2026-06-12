"""
YouTube Power Module — comprehensive YouTube tooling for INXOTIVE HUB.
Search, transcript (multi-language), comments, video info, audio download,
channel lookup, LLM analysis, and auto-indexing to Qdrant knowledge base.

Dependencies: yt-dlp, youtube_transcript_api, openai-whisper, httpx, qdrant-client
"""

import asyncio
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 9Router Config
# ---------------------------------------------------------------------------

NINE_ROUTER_BASE = "http://localhost:20128/v1"
NINE_ROUTER_API_KEY = os.environ.get("NINE_ROUTER_API_KEY") or os.environ.get("ROUTER_API_KEY", "")
NINE_ROUTER_HEADERS = {"Authorization": f"Bearer {NINE_ROUTER_API_KEY}"} if NINE_ROUTER_API_KEY else {}
DEFAULT_YT_MODEL = "max-free"  # 9Router combo model — default for YouTube analysis

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

YOUTUBE_INSTRUCTION_PROMPT = """When the user shares a YouTube video, respond with a structured breakdown:

1. **Summary** — Concise overview of the video's content and main thesis (2-4 sentences)
2. **Key Points** — Bullet list of the most important topics, arguments, or moments
3. **Notable Timestamps** — If timestamps are available from the transcript, highlight 3-5 interesting moments with their approximate timestamps (e.g. "03:45 — discusses X")
4. **Audience Reception** — If comments are available, summarize what viewers think: general sentiment, top reactions, any debate or controversy

Keep it conversational and concise. Do NOT web search for this video — use only the transcript and comments provided."""

# Will be set at startup
YouTubeTranscriptApi = None
YOUTUBE_AVAILABLE = False

# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------


def _find_ytdlp() -> str:
    """Find yt-dlp binary: venv bin first, then system PATH, then ~/.local/bin."""
    venv_bin = Path(sys.executable).parent / "yt-dlp"
    if venv_bin.exists():
        return str(venv_bin)
    found = shutil.which("yt-dlp")
    if found:
        return found
    for p in [
        Path.home() / ".local/bin/yt-dlp",
        Path("/usr/local/bin/yt-dlp"),
        Path("/usr/bin/yt-dlp"),
    ]:
        if p.exists():
            return str(p)
    return "yt-dlp"  # hope it's on PATH


YTDLP_PATH = _find_ytdlp()


def init_youtube():
    """Import and cache the YouTube transcript API."""
    global YouTubeTranscriptApi, YOUTUBE_AVAILABLE
    try:
        from youtube_transcript_api import YouTubeTranscriptApi as _Api
        YouTubeTranscriptApi = _Api
        YOUTUBE_AVAILABLE = True
        logger.info("YouTube transcript API available (%s)", YTDLP_PATH)
    except ImportError as e:
        logger.warning("youtube-transcript-api not installed: %s", e)
        YOUTUBE_AVAILABLE = False


# ---------------------------------------------------------------------------
# URL utilities
# ---------------------------------------------------------------------------

YOUTUBE_REGEX = re.compile(
    r"(?:https?://)?(?:www\.)?(?:youtube\.com/(?:watch\?v=|embed/|v/|shorts/|playlist\?list=)"
    r"|youtu\.be/)([a-zA-Z0-9_-]{11})"
)

YOUTUBE_PLAYLIST_REGEX = re.compile(
    r"(?:https?://)?(?:www\.)?youtube\.com/playlist\?list=([a-zA-Z0-9_-]+)"
)


def is_youtube_url(url: str) -> bool:
    if not isinstance(url, str):
        return False
    return bool(YOUTUBE_REGEX.search(url)) or bool(YOUTUBE_PLAYLIST_REGEX.search(url))


def extract_youtube_id(url: str) -> Optional[str]:
    """Extract YouTube video ID from various URL formats."""
    parsed = urllib.parse.urlparse(url)
    if parsed.hostname in ("www.youtube.com", "youtube.com", "m.youtube.com", "music.youtube.com"):
        if parsed.path in ("/watch", ""):
            params = urllib.parse.parse_qs(parsed.query)
            if "v" in params:
                return params["v"][0]
        elif parsed.path.startswith("/embed/"):
            return parsed.path.split("/")[-1]
        elif parsed.path.startswith("/shorts/"):
            return parsed.path.split("/")[-1].split("?")[0]
        elif parsed.path.startswith("/v/"):
            return parsed.path.split("/")[2]
    elif parsed.hostname == "youtu.be":
        return parsed.path[1:].split("?")[0]
    m = YOUTUBE_REGEX.search(url)
    if m:
        return m.group(1)
    return None


def extract_playlist_id(url: str) -> Optional[str]:
    m = YOUTUBE_PLAYLIST_REGEX.search(url)
    if m:
        return m.group(1)
    parsed = urllib.parse.urlparse(url)
    if "list" in parsed.query:
        params = urllib.parse.parse_qs(parsed.query)
        return params.get("list", [None])[0]
    return None


# ---------------------------------------------------------------------------
# Video info via yt-dlp (fast, no download)
# ---------------------------------------------------------------------------


async def fetch_video_info(video_id: str, timeout: int = 30) -> Dict[str, Any]:
    """Fetch video metadata using yt-dlp (no download)."""
    try:
        cmd = [
            YTDLP_PATH,
            "--skip-download",
            "--no-write-comments",
            "--dump-json",
            "--no-check-certificates",
            "--extractor-args",
            "youtube:skip=webpage;player_client=android",
            f"https://www.youtube.com/watch?v={video_id}",
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise

        if proc.returncode != 0:
            return {"success": False, "error": f"yt-dlp failed: {stderr.decode()[:300]}"}

        data = json.loads(stdout.decode())
        return {
            "success": True,
            "video_id": video_id,
            "title": data.get("title", ""),
            "channel": data.get("channel", "") or data.get("uploader", ""),
            "channel_url": data.get("channel_url", ""),
            "channel_id": data.get("channel_id", ""),
            "duration": data.get("duration", 0),
            "view_count": data.get("view_count", 0),
            "like_count": data.get("like_count", 0),
            "comment_count": data.get("comment_count", 0),
            "upload_date": data.get("upload_date", ""),
            "description": (data.get("description", "") or "")[:5000],
            "tags": data.get("tags", [])[:20],
            "categories": data.get("categories", []),
            "thumbnail": data.get("thumbnail", f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"),
            "webpage_url": data.get("webpage_url", f"https://www.youtube.com/watch?v={video_id}"),
            "formatted_duration": _format_duration(data.get("duration", 0)),
            "upload_date_formatted": _format_upload_date(data.get("upload_date", "")),
        }

    except asyncio.TimeoutError:
        logger.warning("Video info timed out for %s", video_id)
        return {"success": False, "error": "Request timed out"}
    except FileNotFoundError:
        logger.warning("yt-dlp not installed")
        return {"success": False, "error": "yt-dlp not installed"}
    except Exception as e:
        logger.warning("Failed to fetch info for %s: %s", video_id, e)
        return {"success": False, "error": str(e)}


def _format_duration(seconds) -> str:
    if not seconds:
        return "00:00"
    seconds = int(seconds)
    h, r = divmod(seconds, 3600)
    m, s = divmod(r, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _format_upload_date(d: str) -> str:
    if len(d) == 8:
        try:
            dt = datetime.strptime(d, "%Y%m%d")
            return dt.strftime("%d %b %Y")
        except ValueError:
            pass
    return d or ""


# ---------------------------------------------------------------------------
# Transcript extraction
# ---------------------------------------------------------------------------


async def extract_transcript_async(
    url: str,
    video_id: str,
    max_retries: int = 3,
) -> Dict[str, Any]:
    """Async YouTube transcript extraction with retries."""
    if not YOUTUBE_AVAILABLE or YouTubeTranscriptApi is None:
        return {"success": False, "error": "YouTube transcript API not available", "transcript": None}

    for attempt in range(max_retries):
        try:
            api = YouTubeTranscriptApi()
            transcript = api.fetch(video_id)
            transcript_list = list(transcript)

            formatted = []
            for snippet in transcript_list:
                text = snippet.text.strip()
                if not text:
                    continue
                formatted.append({
                    "text": text,
                    "start": snippet.start,
                    "duration": snippet.duration,
                    "timestamp": f"{int(snippet.start // 60):02d}:{int(snippet.start % 60):02d}",
                })

            full_text = " ".join(e["text"] for e in formatted)
            max_len = 8000
            if len(full_text) > max_len:
                full_text = full_text[:max_len] + "... [transcript truncated]"

            # Detect language from snippet metadata
            detected_lang = "en"
            try:
                detected_lang = transcript_list[0].language or "en"
            except (AttributeError, IndexError):
                pass

            return {
                "success": True,
                "transcript": full_text,
                "video_id": video_id,
                "language": detected_lang,
                "is_generated": False,
                "segments": formatted,
                "segment_count": len(formatted),
            }

        except Exception as e:
            logger.warning("Transcript attempt %d failed: %s", attempt + 1, e)
            if attempt < max_retries - 1:
                await asyncio.sleep(1 * (attempt + 1))

    return {"success": False, "error": f"Failed after {max_retries} attempts", "transcript": None}


# ---------------------------------------------------------------------------
# Comment fetching
# ---------------------------------------------------------------------------


async def fetch_youtube_comments(
    video_id: str, max_comments: int = 25, timeout: int = 30
) -> Dict[str, Any]:
    """Fetch top comments for a YouTube video using yt-dlp."""
    try:
        cmd = [
            YTDLP_PATH,
            "--skip-download",
            "--write-comments",
            "--extractor-args",
            f"youtube:max_comments={max_comments},all,100,0",
            "--dump-json",
            "--js-runtimes",
            "node",
            "--no-check-certificates",
            f"https://www.youtube.com/watch?v={video_id}",
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise

        if proc.returncode != 0:
            return {"success": False, "error": f"yt-dlp failed: {stderr.decode()[:200]}", "comments": []}

        data = json.loads(stdout.decode())
        title = data.get("title", "")
        channel = data.get("channel", "") or data.get("uploader", "")
        raw_comments = data.get("comments", [])

        comments = []
        for c in raw_comments[:max_comments]:
            text = (c.get("text") or "").strip()
            if not text:
                continue
            comments.append({
                "author": c.get("author", "Unknown"),
                "text": text,
                "likes": c.get("like_count", 0),
                "time": c.get("timestamp", ""),
            })

        # Sort by likes descending
        comments.sort(key=lambda x: x.get("likes", 0), reverse=True)

        return {
            "success": True,
            "comments": comments,
            "count": len(comments),
            "title": title,
            "channel": channel,
        }

    except asyncio.TimeoutError:
        logger.warning("Comment fetch timed out for %s", video_id)
        return {"success": False, "error": "Comment fetch timed out", "comments": []}
    except FileNotFoundError:
        logger.warning("yt-dlp not found")
        return {"success": False, "error": "yt-dlp not installed", "comments": []}
    except Exception as e:
        logger.warning("Failed to fetch comments for %s: %s", video_id, e)
        return {"success": False, "error": str(e), "comments": []}


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


async def search_youtube(
    query: str, max_results: int = 10, timeout: int = 30
) -> Dict[str, Any]:
    """Search YouTube using yt-dlp (no API key needed)."""
    try:
        cmd = [
            YTDLP_PATH,
            "--skip-download",
            "--dump-json",
            "--no-check-certificates",
            "--flat-playlist",
            "--extractor-args",
            "youtube:skip=webpage;player_client=android",
            f"ytsearch{max_results}:{query}",
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise

        if proc.returncode != 0:
            return {
                "success": False,
                "error": f"yt-dlp search failed: {stderr.decode()[:200]}",
                "results": [],
            }

        results = []
        for line in stdout.decode().strip().split("\n"):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
                vid = item.get("id", "")
                results.append({
                    "video_id": vid,
                    "title": item.get("title", ""),
                    "channel": item.get("channel", "") or item.get("uploader", ""),
                    "duration": item.get("duration", 0) or 0,
                    "formatted_duration": _format_duration(item.get("duration", 0) or 0),
                    "view_count": int(item.get("view_count", 0) or 0),
                    "thumbnail": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                    "url": f"https://www.youtube.com/watch?v={vid}",
                    "upload_date": item.get("upload_date", ""),
                })
            except json.JSONDecodeError:
                continue

        return {"success": True, "results": results, "count": len(results), "query": query}

    except asyncio.TimeoutError:
        return {"success": False, "error": "Search timed out", "results": []}
    except FileNotFoundError:
        return {"success": False, "error": "yt-dlp not installed", "results": []}
    except Exception as e:
        logger.warning("Search failed: %s", e)
        return {"success": False, "error": str(e), "results": []}


# ---------------------------------------------------------------------------
# Channel videos
# ---------------------------------------------------------------------------


async def fetch_channel_videos(
    channel_id: str, max_results: int = 20, timeout: int = 30
) -> Dict[str, Any]:
    """Fetch videos from a YouTube channel."""
    try:
        if channel_id.startswith("http"):
            url = channel_id
        else:
            url = f"https://www.youtube.com/channel/{channel_id}/videos"

        cmd = [
            YTDLP_PATH,
            "--skip-download",
            "--dump-json",
            "--no-check-certificates",
            "--flat-playlist",
            "--extractor-args",
            "youtube:skip=webpage;player_client=android",
            url,
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise

        if proc.returncode != 0:
            return {"success": False, "error": f"yt-dlp failed: {stderr.decode()[:200]}", "videos": []}

        videos = []
        for line in stdout.decode().strip().split("\n")[:max_results]:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
                vid = item.get("id", "")
                videos.append({
                    "video_id": vid,
                    "title": item.get("title", ""),
                    "url": f"https://www.youtube.com/watch?v={vid}",
                    "duration": item.get("duration", 0),
                    "formatted_duration": _format_duration(item.get("duration", 0)),
                    "view_count": item.get("view_count", 0),
                    "thumbnail": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                })
            except json.JSONDecodeError:
                continue

        return {"success": True, "videos": videos, "count": len(videos)}

    except asyncio.TimeoutError:
        return {"success": False, "error": "Request timed out", "videos": []}
    except Exception as e:
        return {"success": False, "error": str(e), "videos": []}


# ---------------------------------------------------------------------------
# Audio download & transcription
# ---------------------------------------------------------------------------


async def download_audio(
    video_id: str, output_dir: Optional[str] = None, timeout: int = 120
) -> Dict[str, Any]:
    """Download audio from YouTube video for transcription."""
    try:
        if output_dir is None:
            output_dir = tempfile.mkdtemp(prefix="yt_audio_")

        output_template = os.path.join(output_dir, "%(id)s.%(ext)s")
        url = f"https://www.youtube.com/watch?v={video_id}"

        cmd = [
            YTDLP_PATH,
            "-x",
            "--audio-format",
            "mp3",
            "--audio-quality",
            "0",
            "--no-check-certificates",
            "-o",
            output_template,
            url,
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise

        if proc.returncode != 0:
            return {"success": False, "error": f"Download failed: {stderr.decode()[:300]}"}

        # Find the downloaded file
        audio_path = os.path.join(output_dir, f"{video_id}.mp3")
        if not os.path.exists(audio_path):
            for f in os.listdir(output_dir):
                if f.startswith(video_id):
                    audio_path = os.path.join(output_dir, f)
                    break
            else:
                return {"success": False, "error": "Audio file not found after download"}

        file_size = os.path.getsize(audio_path)
        return {
            "success": True,
            "audio_path": audio_path,
            "file_size": file_size,
            "file_size_formatted": _format_size(file_size),
            "video_id": video_id,
        }

    except asyncio.TimeoutError:
        return {"success": False, "error": "Download timed out"}
    except FileNotFoundError:
        return {"success": False, "error": "yt-dlp not installed"}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def transcribe_youtube(
    video_id: str,
    model: str = "small",
    language: str = "id",
    timeout: int = 300,
) -> Dict[str, Any]:
    """Download audio from YouTube and transcribe with Whisper."""
    try:
        # Step 1: Download audio
        audio_result = await download_audio(video_id, timeout=120)
        if not audio_result.get("success"):
            return audio_result

        audio_path = audio_result["audio_path"]

        # Step 2: Transcribe with Whisper CLI
        if not shutil.which("whisper"):
            return {"success": False, "error": "Whisper CLI not installed"}

        cmd = [
            "whisper",
            audio_path,
            "--model", model,
            "--language", language,
            "--output_format", "json",
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise

        # Cleanup audio
        cleanup_dir = os.path.dirname(audio_path)
        try:
            os.unlink(audio_path)
            for f in os.listdir(cleanup_dir):
                if video_id in f:
                    os.unlink(os.path.join(cleanup_dir, f))
        except (FileNotFoundError, OSError):
            pass

        if proc.returncode != 0:
            return {"success": False, "error": f"Whisper failed: {stderr.decode()[:300]}"}

        result = json.loads(stdout.decode())

        return {
            "success": True,
            "text": result.get("text", ""),
            "language": language,
            "model": model,
            "video_id": video_id,
            "segments": result.get("segments", []),
        }

    except asyncio.TimeoutError:
        return {"success": False, "error": "Transcription timed out"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# 9Router API helper (OpenAI-compatible)
# ---------------------------------------------------------------------------


async def _call_9router_chat(
    prompt: str,
    system: str = "",
    model: str = "",
    temperature: float = 0.3,
    max_tokens: int = 1024,
    timeout: int = 60,
) -> str:
    """Call 9Router chat completions (OpenAI-compatible)."""
    use_model = model or DEFAULT_YT_MODEL
    if not NINE_ROUTER_API_KEY:
        return "Analysis unavailable: 9Router API key not configured"

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(
                f"{NINE_ROUTER_BASE}/chat/completions",
                headers=NINE_ROUTER_HEADERS,
                json={
                    "model": use_model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
            )
            if r.status_code == 200:
                # Some 9Router models append SSE trailer (data: [DONE]) to non-streaming responses
                body = r.text
                json_part = body.split("data:")[0].strip()
                if not json_part:
                    json_part = body
                data = json.loads(json_part)
                return data.get("choices", [{}])[0].get("message", {}).get("content", "") or "(empty response)"
            else:
                return f"Analysis unavailable: 9Router returned HTTP {r.status_code}"
    except Exception as e:
        return f"Analysis unavailable: {e}"


async def _call_9router_embeddings(text: str, model: str = "") -> list:
    """Get embeddings from 9Router (OpenAI-compatible). Falls back to Ollama."""
    use_model = model or "max-free"

    # Try 9Router embeddings first
    if NINE_ROUTER_API_KEY:
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(
                    f"{NINE_ROUTER_BASE}/embeddings",
                    headers=NINE_ROUTER_HEADERS,
                    json={"model": use_model, "input": text[:3000]},
                )
                if r.status_code == 200:
                    data = r.json()
                    emb = data.get("data", [{}])[0].get("embedding", [])
                    if emb:
                        return emb
        except Exception:
            pass

    # Fallback: try Ollama nomic-embed-text
    try:
        r = httpx.post(
            "http://localhost:11434/api/embeddings",
            json={"model": "nomic-embed-text", "prompt": text[:5000]},
            timeout=30,
        )
        if r.status_code == 200:
            return r.json().get("embedding", [])
    except Exception:
        pass

    raise RuntimeError("No embedding model available (9Router + Ollama both failed)")


# ---------------------------------------------------------------------------
# LLM Analysis
# ---------------------------------------------------------------------------


async def analyze_youtube_video(
    video_id: str,
    include_comments: bool = True,
    max_comments: int = 10,
    model: str = "",
) -> Dict[str, Any]:
    """Full YouTube analysis: info + transcript + comments + AI summary in Indonesian.

    Uses 9Router for AI analysis (default: max-free combo model).
    """
    try:
        # Fetch everything in parallel
        info_task = fetch_video_info(video_id)
        transcript_task = extract_transcript_async(
            f"https://www.youtube.com/watch?v={video_id}", video_id
        )

        tasks = [info_task, transcript_task]
        if include_comments:
            tasks.append(fetch_youtube_comments(video_id, max_comments))

        done = await asyncio.gather(*tasks)
        info = done[0]
        transcript_data = done[1]
        comments_data = done[2] if include_comments and len(done) > 2 else {
            "success": False, "comments": []
        }

        # Build analysis context
        context_parts = []
        if transcript_data.get("success"):
            context_parts.append(f"TRANSCRIPT:\n{transcript_data['transcript']}")

        if comments_data.get("success") and comments_data.get("comments"):
            context_parts.append("TOP COMMENTS:")
            for c in comments_data["comments"][:5]:
                context_parts.append(f"- @{c['author']}: {c['text'][:200]}")

        context = "\n\n".join(context_parts) if context_parts else "No transcript or comments available."

        title = info.get("title", "Unknown") if info.get("success") else "Unknown"
        channel = info.get("channel", "Unknown") if info.get("success") else "Unknown"
        duration_fmt = info.get("formatted_duration", "Unknown") if info.get("success") else "Unknown"
        views = info.get("view_count", 0) if info.get("success") else 0
        likes = info.get("like_count", 0) if info.get("success") else 0
        upload_date = info.get("upload_date_formatted", "Unknown") if info.get("success") else "Unknown"

        prompt = f"""Analisis video YouTube berikut dalam Bahasa Indonesia:

JUDUL: {title}
CHANNEL: {channel}
DURASI: {duration_fmt}
VIEWS: {views:,}
LIKES: {likes:,}
TANGGAL: {upload_date}

{context}

Beri analisis terstruktur:
1. **Ringkasan** — Apa isi utama video ini? (2-3 kalimat)
2. **Poin-poin Kunci** — 3-5 poin terpenting
3. **Analisis** — Konteks, kualitas, atau implikasi dari konten video
4. **Komentar Viewer** — Apa reaksi audiens dari komentar yang ada?
5. **Skor** — Rating 1-10 untuk kualitas konten video ini"""

        analysis = await _call_9router_chat(
            prompt=prompt,
            system="Kamu adalah analis konten YouTube yang ahli. Gunakan Bahasa Indonesia yang baik dan profesional. Jawab dengan struktur markdown yang rapi.",
            model=model or DEFAULT_YT_MODEL,
            temperature=0.3,
            max_tokens=1200,
        )

        return {
            "success": True,
            "video_id": video_id,
            "info": info if info.get("success") else None,
            "transcript": {
                "available": transcript_data.get("success", False),
                "text": (transcript_data.get("transcript", "") or "")[:3000]
                if transcript_data.get("success")
                else None,
                "language": transcript_data.get("language", "unknown"),
            },
            "comments": {
                "available": comments_data.get("success", False),
                "count": comments_data.get("count", 0),
                "top": comments_data.get("comments", [])[:5]
                if comments_data.get("success")
                else [],
            },
            "analysis": analysis,
        }

    except Exception as e:
        return {"success": False, "error": str(e), "video_id": video_id}


# ---------------------------------------------------------------------------
# Playlist extraction
# ---------------------------------------------------------------------------


async def fetch_playlist(
    playlist_id: str, max_results: int = 50, timeout: int = 30
) -> Dict[str, Any]:
    """Fetch videos from a YouTube playlist."""
    try:
        cmd = [
            YTDLP_PATH,
            "--skip-download",
            "--dump-json",
            "--no-check-certificates",
            "--flat-playlist",
            f"https://www.youtube.com/playlist?list={playlist_id}",
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise

        if proc.returncode != 0:
            return {"success": False, "error": f"yt-dlp failed: {stderr.decode()[:200]}", "videos": []}

        videos = []
        playlist_title = ""
        for line in stdout.decode().strip().split("\n")[:max_results]:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
                if not playlist_title:
                    playlist_title = item.get("playlist_title", "") or item.get("playlist", "")
                vid = item.get("id", "")
                if not vid:
                    continue
                videos.append({
                    "video_id": vid,
                    "title": item.get("title", ""),
                    "url": f"https://www.youtube.com/watch?v={vid}",
                    "duration": item.get("duration", 0),
                    "formatted_duration": _format_duration(item.get("duration", 0)),
                    "thumbnail": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                    "index": item.get("playlist_index", 0),
                })
            except json.JSONDecodeError:
                continue

        return {
            "success": True,
            "videos": videos,
            "count": len(videos),
            "playlist_id": playlist_id,
            "playlist_title": playlist_title,
        }

    except asyncio.TimeoutError:
        return {"success": False, "error": "Request timed out", "videos": []}
    except Exception as e:
        return {"success": False, "error": str(e), "videos": []}


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def format_transcript_for_context(
    transcript_data: Dict[str, Any],
    url: str,
    title: str = "",
    channel: str = "",
) -> str:
    """Format transcript for LLM context injection."""
    if not transcript_data.get("success"):
        header = f' "{title}"' if title else ""
        if channel and title:
            header += f" by {channel}"
        return (
            f"\n[YouTube{header}: Transcript unavailable"
            f" ({transcript_data.get('error', 'Unknown error')})."
            " Do NOT web search for this video.]"
        )

    transcript = transcript_data.get("transcript", "")
    video_id = transcript_data.get("video_id", "")
    language = transcript_data.get("language", "unknown")
    segments = transcript_data.get("segments", [])

    ctx = "\n[YOUTUBE VIDEO TRANSCRIPT]\n"
    if title:
        ctx += f"Title: {title}\n"
    if channel:
        ctx += f"Channel: {channel}\n"
    ctx += f"Video ID: {video_id}\n"
    ctx += f"Language: {language}\n"
    ctx += f"URL: {url}\n\n"

    if segments:
        ctx += "Timestamped Transcript:\n"
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            ctx += f"[{seg['timestamp']}] {seg['text']}\n"
        if len(ctx) > 12000:
            ctx = ctx[: ctx.index("Timestamped Transcript:\n")]
            ctx += "Transcript:\n" + transcript
    else:
        ctx += "Transcript:\n" + transcript
    ctx += "\n[END TRANSCRIPT]\n"
    return ctx


def format_comments_for_context(comments_data: Dict[str, Any], url: str) -> str:
    """Format YouTube comments for LLM context injection."""
    if not comments_data.get("success") or not comments_data.get("comments"):
        return ""

    comments = comments_data["comments"]
    ctx = f"\n[YOUTUBE VIDEO COMMENTS — Top {len(comments)} by popularity]\n"
    ctx += f"URL: {url}\n\n"

    for i, c in enumerate(comments, 1):
        likes = c.get("likes", 0)
        likes_str = f" [{likes} likes]" if likes else ""
        ctx += f"{i}. @{c['author']}{likes_str}: {c['text']}\n\n"

    if len(ctx) > 4000:
        ctx = ctx[:4000] + "\n[Comments truncated]\n"
    ctx += "[END COMMENTS]\n"
    return ctx


# ---------------------------------------------------------------------------
# Knowledge Base Integration (Qdrant)
# ---------------------------------------------------------------------------


async def index_to_knowledge_base(
    video_id: str,
    video_title: str,
    transcript_text: str,
    channel: str = "",
    qdrant_collection: str = "youtube_transcripts",
) -> Dict[str, Any]:
    """Index YouTube transcript to Qdrant vector DB for searchable knowledge."""
    try:
        from qdrant_client import QdrantClient
        from qdrant_client.http import models as qmodels

        client = QdrantClient("localhost", port=6333)

        # Create collection if not exists
        collections = client.get_collections().collections
        if not any(c.name == qdrant_collection for c in collections):
            client.create_collection(
                collection_name=qdrant_collection,
                vectors_config=qmodels.VectorParams(size=384, distance="Cosine"),
            )
            client.create_payload_index(
                collection_name=qdrant_collection,
                field_name="video_id",
                field_schema="keyword",
            )
            client.create_payload_index(
                collection_name=qdrant_collection,
                field_name="source",
                field_schema="keyword",
            )

        # Get embeddings via 9Router (falls back to Ollama)
        try:
            emb = await _call_9router_embeddings(transcript_text[:3000])
            vectors = [emb]
        except Exception as e:
            return {"success": False, "error": f"Embedding error: {e}"}

        point_id = f"yt_{video_id}_{int(time.time())}"
        client.upsert(
            collection_name=qdrant_collection,
            points=[
                qmodels.PointStruct(
                    id=abs(hash(point_id)) % (10**15),
                    vector=vectors[0],
                    payload={
                        "video_id": video_id,
                        "title": video_title,
                        "channel": channel,
                        "url": f"https://www.youtube.com/watch?v={video_id}",
                        "text": transcript_text[:5000],
                        "source": "youtube",
                        "indexed_at": datetime.now().isoformat(),
                    },
                )
            ],
        )

        return {"success": True, "collection": qdrant_collection, "video_id": video_id}

    except ImportError:
        logger.info("Qdrant client not installed — skipping knowledge base index")
        return {"success": False, "error": "Qdrant client not installed"}
    except Exception as e:
        logger.warning("Failed to index to knowledge base: %s", e)
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def _format_size(size_bytes: int) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"
