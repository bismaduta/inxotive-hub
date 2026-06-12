#!/usr/bin/env python3
"""
INXOTIVE Knowledge Base MCP Server
===================================
FastMCP server exposing Qdrant vector search (localhost:6333) as MCP tools.
Embedding via 9Router OpenAI-compatible /v1/embeddings endpoint.

Usage:
    pip install mcp httpx
    python mcp_knowledge_server.py
"""

import json
import os
import time
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Server init
# ---------------------------------------------------------------------------
mcp = FastMCP("inxotive-knowledge")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
QDRANT_BASE = "http://localhost:6333"
ROUTER_BASE = "http://localhost:20128"
ENV_SECRETS = os.path.expanduser("~/.env_secrets")

KNOWN_COLLECTIONS = ["youtube_transcripts", "documents", "knowledge"]
EMBEDDING_MODEL = "snowflake-arctic-embed2:latest"  # 9Router embedding model

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_env_secrets() -> dict[str, str]:
    """Load key=value pairs from ~/.env_secrets (no export prefix)."""
    secrets: dict[str, str] = {}
    if not os.path.isfile(ENV_SECRETS):
        return secrets
    try:
        with open(ENV_SECRETS) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                # Strip leading 'export ' if present
                if line.startswith("export "):
                    line = line[7:]
                if "=" in line:
                    key, _, val = line.partition("=")
                    secrets[key.strip()] = val.strip().strip('"').strip("'")
    except Exception:
        pass
    return secrets


def _router_headers() -> dict[str, str]:
    """Build auth headers for 9Router from env secrets."""
    secrets = _load_env_secrets()
    api_key = secrets.get("ROUTER_API_KEY", secrets.get("9ROUTER_API_KEY", ""))
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


async def _get_embedding(text: str) -> list[float] | None:
    """Get embedding vector from 9Router's OpenAI-compatible /v1/embeddings."""
    url = f"{ROUTER_BASE}/v1/embeddings"
    payload = {"model": EMBEDDING_MODEL, "input": text}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload, headers=_router_headers())
            resp.raise_for_status()
            data = resp.json()
            return data["data"][0]["embedding"]
    except httpx.TimeoutException:
        return None
    except httpx.HTTPStatusError as e:
        return None
    except Exception:
        return None


async def _qdrant_get(path: str) -> dict[str, Any] | None:
    """GET from Qdrant REST API."""
    url = f"{QDRANT_BASE}/{path.lstrip('/')}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.json()
    except httpx.ConnectError:
        return None
    except httpx.TimeoutException:
        return None
    except httpx.HTTPStatusError:
        return None


async def _qdrant_post(path: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    """POST to Qdrant REST API."""
    url = f"{QDRANT_BASE}/{path.lstrip('/')}"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            return resp.json()
    except httpx.ConnectError:
        return None
    except httpx.TimeoutException:
        return None
    except httpx.HTTPStatusError:
        return None


def _fmt_points(
    points: list[dict[str, Any]], scores: bool = True
) -> str:
    """Format a list of Qdrant point results into a readable string."""
    if not points:
        return "  No results found."
    lines: list[str] = []
    for i, pt in enumerate(points, 1):
        payload = pt.get("payload", {})
        score = pt.get("score", pt.get("relevance", None))
        title = payload.get("title", payload.get("name", payload.get("source", "Untitled")))
        content = payload.get("content", payload.get("text", payload.get("description", "")))
        source = payload.get("source", payload.get("url", ""))
        doc_type = payload.get("doc_type", payload.get("type", ""))

        lines.append(f"  [{i}] {title}")
        if score is not None and scores:
            lines.append(f"      Score: {score:.4f}")
        if source:
            lines.append(f"      Source: {source}")
        if doc_type:
            lines.append(f"      Type: {doc_type}")
        if content:
            snippet = content[:300].replace("\n", " ")
            if len(content) > 300:
                snippet += "..."
            lines.append(f"      Snippet: {snippet}")
        lines.append("")
    return "\n".join(lines)


async def _scroll_collection(
    collection: str, limit: int = 10
) -> list[dict[str, Any]]:
    """Scroll (read all) points from a collection, newest first."""
    payload = {
        "limit": limit,
        "with_payload": True,
        "with_vector": False,
    }
    result = await _qdrant_post(f"/collections/{collection}/points/scroll", payload)
    if result is None:
        return []
    return result.get("result", {}).get("points", [])


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def search_knowledge_base(query: str, limit: int = 5) -> str:
    """Search the INXOTIVE knowledge base using semantic search. Returns relevant documents with scores."""
    # Get embedding
    vector = await _get_embedding(query)
    if vector is None:
        return (
            "**Error:** Could not generate embedding for your query.\n"
            f"Please check that 9Router is running at {ROUTER_BASE} and the embedding model `{EMBEDDING_MODEL}` is available."
        )

    dim = len(vector)
    results: list[tuple[str, float, dict[str, Any]]] = []

    for collection in KNOWN_COLLECTIONS:
        payload = {
            "vector": vector,
            "limit": limit,
            "with_payload": True,
            "with_vector": False,
        }
        data = await _qdrant_post(f"/collections/{collection}/points/search", payload)
        if data is None:
            continue
        points = data.get("result", [])
        for pt in points:
            results.append(
                (collection, pt.get("score", 0.0), pt.get("payload", {}))
            )

    # Sort by score descending
    results.sort(key=lambda x: x[1], reverse=True)
    results = results[:limit]

    if not results:
        return (
            f"**No results found** for: {query}\n\n"
            "Possible reasons:\n"
            "- Qdrant (localhost:6333) may be down — try `curl localhost:6333/collections`\n"
            "- The knowledge collections may be empty\n"
            "- The embedding model may not match the indexed vectors' dimension"
        )

    lines = [
        f"## Knowledge Base Results — \"{query}\"",
        f"Found {len(results)} result(s) across {len(KNOWN_COLLECTIONS)} collections.\n",
    ]
    for i, (col, score, payload) in enumerate(results, 1):
        title = payload.get("title", payload.get("name", payload.get("source", "Untitled")))
        content = payload.get("content", payload.get("text", payload.get("description", "")))
        source = payload.get("source", payload.get("url", ""))
        doc_type = payload.get("doc_type", payload.get("type", ""))

        lines.append(f"### [{i}] {title}  (score: {score:.4f})")
        lines.append(f"  **Collection:** `{col}`")
        if source:
            lines.append(f"  **Source:** {source}")
        if doc_type:
            lines.append(f"  **Type:** {doc_type}")
        if content:
            snippet = content[:500].replace("\n", " ")
            if len(content) > 500:
                snippet += "..."
            lines.append(f"  **Snippet:** {snippet}")
        lines.append("")

    return "\n".join(lines)


@mcp.tool()
async def list_knowledge_collections() -> str:
    """List all available collections in the Qdrant knowledge base."""
    data = await _qdrant_get("/collections")
    if data is None:
        return (
            "**Error:** Cannot connect to Qdrant at localhost:6333.\n\n"
            "Make sure Qdrant is running:\n"
            "  docker ps | grep qdrant\n"
            "  curl localhost:6333/collections\n"
        )

    collections = data.get("result", {})
    if not collections:
        return "No collections found in Qdrant."

    lines = [
        "## Qdrant Collections",
        f"Total: {len(collections)} collection(s)\n",
    ]
    for name, info in sorted(collections.items()):
        status = info.get("status", "?")
        vectors_count = info.get("vectors_count", 0)
        points_count = info.get("points_count", 0)
        # Some Qdrant versions nest differently
        if isinstance(info, dict) and "config" in info:
            params = info["config"].get("params", {})
            vectors = params.get("vectors", {})
            if isinstance(vectors, dict):
                dim = vectors.get("size", "?")
            else:
                dim = "?"
        else:
            dim = "?"

        lines.append(f"  **`{name}`**")
        lines.append(f"      Status: {status}")
        lines.append(f"      Points: {points_count or vectors_count}")
        lines.append(f"      Dimension: {dim}")
        lines.append("")

    return "\n".join(lines)


@mcp.tool()
async def get_collection_stats(collection: str = "") -> str:
    """Get stats for a specific collection or all collections: vector count, dimension, status."""
    if collection:
        # Specific collection
        data = await _qdrant_get(f"/collections/{collection}")
        if data is None:
            # Check if Qdrant is reachable at all
            alive = await _qdrant_get("/collections")
            if alive is None:
                return (
                    "**Error:** Cannot connect to Qdrant at localhost:6333.\n\n"
                    "Make sure Qdrant is running:\n"
                    "  docker ps | grep qdrant\n"
                    "  curl localhost:6333/collections\n"
                )
            return f"**Error:** Collection `{collection}` not found.\nAvailable collections: {', '.join(KNOWN_COLLECTIONS)}"

        result = data.get("result", {})
        status = result.get("status", "?")
        points_count = result.get("points_count", 0)
        vectors_count = result.get("vectors_count", 0)
        segments_count = result.get("segments_count", 0)

        # Extract dimension from config
        config = result.get("config", {})
        params = config.get("params", {})
        vectors = params.get("vectors", {})
        if isinstance(vectors, dict):
            dim = vectors.get("size", "?")
            distance = vectors.get("distance", "?")
        else:
            dim = "?"
            distance = "?"

        optimizer_status = result.get("optimizer_status", "?")
        disk_usage = result.get("disk_usage", None)

        lines = [
            f"## Collection: `{collection}`",
            f"  **Status:** {status}",
            f"  **Points:** {points_count or vectors_count}",
            f"  **Vectors:** {vectors_count}",
            f"  **Segments:** {segments_count}",
            f"  **Dimension:** {dim}",
            f"  **Distance:** {distance}",
            f"  **Optimizer:** {optimizer_status}",
        ]
        if disk_usage:
            lines.append(f"  **Disk Usage:** {disk_usage}")
        lines.append("")

        # Try to get a sample point to show payload structure
        scroll = await _scroll_collection(collection, limit=1)
        if scroll:
            payload = scroll[0].get("payload", {})
            lines.append("  **Sample payload keys:**")
            for key in list(payload.keys())[:10]:
                lines.append(f"    - `{key}`")
            lines.append("")

        return "\n".join(lines)

    else:
        # All collections
        data = await _qdrant_get("/collections")
        if data is None:
            return (
                "**Error:** Cannot connect to Qdrant at localhost:6333.\n\n"
                "Make sure Qdrant is running:\n"
                "  docker ps | grep qdrant\n"
                "  curl localhost:6333/collections\n"
            )

        collections = data.get("result", {})
        if not collections:
            return "No collections found in Qdrant."

        lines = [
            "## Collection Stats — All Collections",
            f"Total: {len(collections)} collection(s)\n",
        ]
        for name, info in sorted(collections.items()):
            status = info.get("status", "?")
            points = info.get("points_count", info.get("vectors_count", 0))
            config = info.get("config", {})
            params = config.get("params", {})
            vectors = params.get("vectors", {})
            dim = vectors.get("size", "?") if isinstance(vectors, dict) else "?"
            distance = vectors.get("distance", "?") if isinstance(vectors, dict) else "?"

            lines.append(f"  **`{name}`**")
            lines.append(f"      Status: {status}  |  Points: {points}  |  Dim: {dim}  |  Distance: {distance}")
            lines.append("")

        return "\n".join(lines)


@mcp.tool()
async def query_youtube_transcripts(query: str, limit: int = 3) -> str:
    """Search specifically in indexed YouTube transcripts."""
    # Get embedding
    vector = await _get_embedding(query)
    if vector is None:
        return (
            "**Error:** Could not generate embedding for your query.\n"
            f"Please check that 9Router is running at {ROUTER_BASE}."
        )

    payload = {
        "vector": vector,
        "limit": limit,
        "with_payload": True,
        "with_vector": False,
    }
    data = await _qdrant_post("/collections/youtube_transcripts/points/search", payload)
    if data is None:
        # Check if collection exists
        collections_data = await _qdrant_get("/collections")
        if collections_data is None:
            return (
                "**Error:** Cannot connect to Qdrant at localhost:6333.\n\n"
                "Make sure Qdrant is running:\n"
                "  docker ps | grep qdrant\n"
                "  curl localhost:6333/collections\n"
            )
        collections = collections_data.get("result", {})
        if "youtube_transcripts" not in collections:
            return (
                "**Collection `youtube_transcripts` does not exist.**\n\n"
                "Available collections:\n"
                + "\n".join(f"  - `{c}`" for c in collections.keys())
            )
        return "**Error:** Search request failed even though collection exists. Check Qdrant logs."

    points = data.get("result", [])
    if not points:
        return f"**No YouTube transcript results** for: {query}\n\nThe `youtube_transcripts` collection exists but no matches were found."

    lines = [
        f"## YouTube Transcripts — \"{query}\"",
        f"Found {len(points)} matching transcript(s).\n",
    ]
    for i, pt in enumerate(points, 1):
        payload = pt.get("payload", {})
        score = pt.get("score", 0.0)
        title = payload.get("title", "Untitled Video")
        video_id = payload.get("video_id", payload.get("source", ""))
        channel = payload.get("channel", payload.get("author", ""))
        content = payload.get("content", payload.get("text", ""))
        published = payload.get("published", payload.get("date", ""))
        duration = payload.get("duration", "")
        lang = payload.get("language", payload.get("lang", ""))

        lines.append(f"### [{i}] {title}")
        lines.append(f"  Score: {score:.4f}")
        if video_id:
            lines.append(f"  Video: https://youtube.com/watch?v={video_id}")
        if channel:
            lines.append(f"  Channel: {channel}")
        if published:
            lines.append(f"  Published: {published}")
        if duration:
            lines.append(f"  Duration: {duration}")
        if lang:
            lines.append(f"  Language: {lang}")
        if content:
            snippet = content[:400].replace("\n", " ")
            if len(content) > 400:
                snippet += "..."
            lines.append(f"  **Transcript snippet:** {snippet}")
        lines.append("")

    return "\n".join(lines)


@mcp.tool()
async def list_recently_indexed(limit: int = 10) -> str:
    """List recently indexed items across all knowledge collections."""
    all_points: list[tuple[str, dict[str, Any]]] = []

    for collection in KNOWN_COLLECTIONS:
        points = await _scroll_collection(collection, limit=max(10, limit))
        for pt in points:
            all_points.append((collection, pt))

    if not all_points:
        # Double-check if Qdrant is alive
        alive = await _qdrant_get("/collections")
        if alive is None:
            return (
                "**Error:** Cannot connect to Qdrant at localhost:6333.\n\n"
                "Make sure Qdrant is running:\n"
                "  docker ps | grep qdrant\n"
                "  curl localhost:6333/collections\n"
            )
        return (
            "**No items found** in any knowledge collection.\n"
            "All collections appear to be empty."
        )

    # Try to sort by timestamp if available
    def _timestamp(item: tuple[str, dict[str, Any]]) -> float:
        ts = item[1].get("payload", {}).get("timestamp", item[1].get("payload", {}).get("created_at", 0))
        if isinstance(ts, (int, float)):
            return float(ts)
        if isinstance(ts, str):
            try:
                return float(ts)
            except (ValueError, TypeError):
                return 0.0
        return 0.0

    all_points.sort(key=_timestamp, reverse=True)
    all_points = all_points[:limit]

    lines = [
        "## Recently Indexed Items",
        f"Showing the {len(all_points)} most recent item(s) across all collections.\n",
    ]
    for i, (col, pt) in enumerate(all_points, 1):
        payload = pt.get("payload", {})
        idx = pt.get("id", "?")
        title = payload.get("title", payload.get("name", payload.get("source", f"Point {idx}")))
        content = payload.get("content", payload.get("text", payload.get("description", "")))
        source = payload.get("source", payload.get("url", ""))
        doc_type = payload.get("doc_type", payload.get("type", ""))
        ts = payload.get("timestamp", payload.get("created_at", ""))

        lines.append(f"  [{i}] **{title}**  (`{col}`)")
        if ts:
            lines.append(f"      Timestamp: {ts}")
        if doc_type:
            lines.append(f"      Type: {doc_type}")
        if source:
            lines.append(f"      Source: {source}")
        if content:
            snippet = content[:200].replace("\n", " ")
            if len(content) > 200:
                snippet += "..."
            lines.append(f"      Snippet: {snippet}")
        lines.append("")

    # Summary per collection
    from collections import Counter
    counts = Counter(col for col, _ in all_points)
    lines.append("**Breakdown by collection:**")
    for col in KNOWN_COLLECTIONS:
        if counts[col]:
            lines.append(f"  - `{col}`: {counts[col]} item(s)")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    """Run the FastMCP knowledge server."""
    mcp.run()


if __name__ == "__main__":
    main()
