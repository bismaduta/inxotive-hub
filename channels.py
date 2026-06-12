"""
Channels — Discord <> Hub 2-way communication for INXOTIVE.

Provides:
  - send_to_discord()       — push message to Discord via webhook
  - send_to_telegram()      — push message to Telegram bot
  - broadcast()             — multi-channel fan-out
  - handle_discord_command() — route Discord slash-style commands to local handlers
  - process_discord_message()— process inbound Discord message, optionally via AI
  - forward_to_hub()        — push event into INXOTIVE event bus + optional SSE

All functions are async. Credentials are loaded from ~/.env_secrets.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------

logger = logging.getLogger("channels")

# ---------------------------------------------------------------------------
# Load credentials from ~/.env_secrets
# ---------------------------------------------------------------------------

_ENV_SECRETS = Path.home() / ".env_secrets"
if _ENV_SECRETS.exists():
    for _line in _ENV_SECRETS.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

EVENT_BUS_PATH = Path.home() / ".event_bus.json"

SEVERITY_EMOJI: Dict[str, str] = {
    "critical": "🚨",
    "warning": "⚠️",
    "info": "ℹ️",
    "success": "✅",
    "error": "❌",
    "debug": "🔍",
}

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _severity_prefix(severity: str) -> str:
    """Return emoji prefix for a given severity string."""
    return SEVERITY_EMOJI.get(severity, "ℹ️")


def _push_to_event_bus(source: str, event_type: str, message: str, severity: str = "info") -> None:
    """Append an event to the shared JSON event bus (synchronous, best-effort)."""
    try:
        events: List[dict] = []
        if EVENT_BUS_PATH.exists():
            try:
                raw = EVENT_BUS_PATH.read_text()
                if raw.strip():
                    events = json.loads(raw)
            except (json.JSONDecodeError, OSError):
                events = []
        events.append({
            "time": datetime.now().isoformat(),
            "source": source,
            "type": event_type,
            "message": message,
            "severity": severity,
        })
        events = events[-100:]  # keep latest 100
        EVENT_BUS_PATH.write_text(json.dumps(events, indent=2, ensure_ascii=False))
    except Exception as exc:
        logger.warning("Failed to write to event bus: %s", exc)


def _truncate(text: str, max_len: int = 1900) -> str:
    """Truncate text to fit Discord webhook limits, appending a notice."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 50] + f"\n\n*(... truncated, {len(text) - max_len + 50} chars omitted)*"


# ---------------------------------------------------------------------------
# 1. send_to_discord
# ---------------------------------------------------------------------------


async def send_to_discord(message: str, severity: str = "info") -> Dict[str, Any]:
    """
    Send a message to Discord via the configured webhook URL.

    Parameters
    ----------
    message  : str — the text payload.
    severity : str — one of 'critical', 'warning', 'info', 'success', 'error', 'debug'.
                     Controls the emoji prefix.

    Returns
    -------
    dict with keys: success (bool), status_code (int | None), error (str | None).
    """
    if not DISCORD_WEBHOOK:
        logger.warning("DISCORD_WEBHOOK not set — cannot send to Discord.")
        return {"success": False, "status_code": None, "error": "DISCORD_WEBHOOK not configured"}

    emoji = _severity_prefix(severity)
    payload = {
        "content": _truncate(f"{emoji} {message}"),
        # Allow Discord to render username / avatar if webhook is configured that way
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(DISCORD_WEBHOOK, json=payload)
            if resp.is_success:
                logger.info("Discord webhook OK (%d) — %s", resp.status_code, message[:80])
            else:
                logger.warning("Discord webhook returned %d: %s", resp.status_code, resp.text[:200])
            return {
                "success": resp.is_success,
                "status_code": resp.status_code,
                "error": None if resp.is_success else resp.text[:300],
            }
    except httpx.TimeoutException:
        logger.error("Discord webhook timed out after 15s")
        return {"success": False, "status_code": None, "error": "Timeout"}
    except httpx.RequestError as exc:
        logger.error("Discord webhook request failed: %s", exc)
        return {"success": False, "status_code": None, "error": str(exc)}
    except Exception as exc:
        logger.error("Unexpected error sending to Discord: %s", exc)
        return {"success": False, "status_code": None, "error": str(exc)}


# ---------------------------------------------------------------------------
# 2. send_to_telegram
# ---------------------------------------------------------------------------


async def send_to_telegram(message: str) -> Dict[str, Any]:
    """
    Send a message to Telegram via the Bot API.

    Requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to be set in environment
    (loaded from ~/.env_secrets).  If either is missing the call is a no-op.

    Returns
    -------
    dict with keys: success (bool), status_code (int | None), error (str | None).
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.debug("Telegram credentials incomplete — skipping.")
        return {
            "success": False,
            "status_code": None,
            "error": "TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not configured",
        }

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message[:4096],  # Telegram hard limit per message
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=payload)
            if resp.is_success:
                logger.info("Telegram send OK (%d)", resp.status_code)
            else:
                logger.warning("Telegram returned %d: %s", resp.status_code, resp.text[:200])
            return {
                "success": resp.is_success,
                "status_code": resp.status_code,
                "error": None if resp.is_success else resp.text[:300],
            }
    except httpx.TimeoutException:
        logger.error("Telegram API timed out after 15s")
        return {"success": False, "status_code": None, "error": "Timeout"}
    except httpx.RequestError as exc:
        logger.error("Telegram request failed: %s", exc)
        return {"success": False, "status_code": None, "error": str(exc)}
    except Exception as exc:
        logger.error("Unexpected error sending to Telegram: %s", exc)
        return {"success": False, "status_code": None, "error": str(exc)}


# ---------------------------------------------------------------------------
# 3. broadcast
# ---------------------------------------------------------------------------


async def broadcast(
    message: str,
    channels: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Send *message* to every channel listed in *channels*.

    Parameters
    ----------
    message  : str — the text payload.
    channels : list of strings, e.g. ``["discord", "telegram"]``.
               If ``None``, defaults to all configured channels.

    Returns
    -------
    dict mapping channel names (e.g. ``"discord"``, ``"telegram"``) to their
    respective result dicts.
    """
    if channels is None:
        channels = []
        if DISCORD_WEBHOOK:
            channels.append("discord")
        if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
            channels.append("telegram")
        if not channels:
            logger.info("broadcast() called with no channels configured — event bus only.")

    results: Dict[str, Any] = {}

    async def _send_discord() -> None:
        results["discord"] = await send_to_discord(message)

    async def _send_telegram() -> None:
        results["telegram"] = await send_to_telegram(message)

    tasks = []
    if "discord" in channels:
        tasks.append(_send_discord())
    if "telegram" in channels:
        tasks.append(_send_telegram())

    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
        # Convert exceptions to error dicts
        for ch in ("discord", "telegram"):
            if ch in results and isinstance(results[ch], Exception):
                results[ch] = {"success": False, "error": str(results[ch])}

    # Always push to the local event bus
    _push_to_event_bus("broadcast", "broadcast", message)

    return results


# ---------------------------------------------------------------------------
# 4. handle_discord_command
# ---------------------------------------------------------------------------

# Maps command names to handler coroutines  (no slash prefix in the dict key).

async def _cmd_status(args: Optional[Dict[str, Any]] = None) -> str:
    """Check service health via the local market-api /status endpoint."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get("http://localhost:8888/status")
            if resp.is_success:
                data = resp.json()
                parts = [f"**INXOTIVE SERVER STATUS**"]
                for svc, info in data.items():
                    status_icon = "✅" if info.get("status") == "ok" else "❌"
                    parts.append(f"{status_icon} **{svc}:** {info.get('status', 'unknown')}")
                return "\n".join(parts) if len(parts) > 1 else "Status tidak tersedia."
            return f"Status endpoint returned HTTP {resp.status_code}"
    except httpx.RequestError as exc:
        return f"Tidak bisa terhubung ke status endpoint: {exc}"
    except Exception as exc:
        return f"Error fetching status: {exc}"


async def _cmd_market(args: Optional[Dict[str, Any]] = None) -> str:
    """Fetch live market snapshot from /market."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get("http://localhost:8888/market")
            if resp.is_success:
                data = resp.json()
                crypto = data.get("crypto", {})
                fg = data.get("fear_greed", {})
                ts = data.get("timestamp", "—")
                lines = [f"**MARKET DATA** — {ts}", ""]
                for coin, info in crypto.items():
                    usd = info.get("usd", 0)
                    chg = info.get("usd_24h_change", 0)
                    arrow = "📈" if chg >= 0 else "📉"
                    lines.append(f"{arrow} **{coin.title()}:** ${usd:,.2f}  ({chg:+.2f}%)")
                lines.append("")
                fg_val = fg.get("value", "N/A")
                fg_label = fg.get("value_classification", "")
                lines.append(f"😱 **Fear & Greed:** {fg_val} — {fg_label}")
                trending = data.get("trending", "")
                if trending:
                    lines.append(f"🔥 {trending}")
                return "\n".join(lines)
            return f"Market endpoint returned HTTP {resp.status_code}"
    except httpx.RequestError as exc:
        return f"Tidak bisa fetch market data: {exc}"
    except Exception as exc:
        return f"Error fetching market: {exc}"


async def _cmd_brief(args: Optional[Dict[str, Any]] = None) -> str:
    """Fetch the /brief analysis prompt (TradeX)."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get("http://localhost:8888/brief")
            if resp.is_success:
                data = resp.json()
                prompt = data.get("prompt", "")
                # The prompt is the structured analysis — send it directly
                return f"**BRIEF ANALYSIS**\n\n{prompt[:1900]}"
            return f"Brief endpoint returned HTTP {resp.status_code}"
    except httpx.RequestError as exc:
        return f"Tidak bisa fetch brief: {exc}"
    except Exception as exc:
        return f"Error fetching brief: {exc}"


async def _cmd_analyze(args: Optional[Dict[str, Any]] = None) -> str:
    """Route to TradeX analysis via the local Ollama / chat endpoint.

    Expects args['coin'] (e.g. 'btc') or defaults to BTC.
    """
    coin = (args or {}).get("coin", "btc").strip().lower()
    coin_map = {"btc": "bitcoin", "eth": "ethereum", "bnb": "binancecoin",
                "sol": "solana", "xrp": "ripple"}
    coin_id = coin_map.get(coin, coin)

    try:
        # Fetch TA first
        async with httpx.AsyncClient(timeout=15.0) as client:
            ta_resp = await client.get(f"http://localhost:8888/ta/{coin}")
            if not ta_resp.is_success:
                return f"TA endpoint returned HTTP {ta_resp.status_code}"
            ta = ta_resp.json()

        # Build a concise analysis string
        tech = ta.get("technical", {})
        if "error" in tech:
            return f"Analisis {coin.upper()} error: {tech['error']}"

        lines = [
            f"**ANALISIS {coin.upper()}**",
            f"RSI: {tech.get('rsi', 'N/A')} ({tech.get('rsi_signal', 'N/A')})",
            f"MACD: {tech.get('macd_signal', 'N/A')}",
            f"Trend: {tech.get('trend', 'N/A')}",
            f"BB Upper: ${tech.get('bb_upper', 0):,}  Lower: ${tech.get('bb_lower', 0):,}",
            f"EMA20: ${tech.get('ema20', 0):,}  EMA50: ${tech.get('ema50', 0):,}",
            f"Support: ${tech.get('support', 0):,}  Resistance: ${tech.get('resistance', 0):,}",
            f"7d Change: {tech.get('change_7d', 0)}%",
        ]
        return "\n".join(lines)

    except httpx.RequestError as exc:
        return f"Tidak bisa fetch analisis {coin.upper()}: {exc}"
    except Exception as exc:
        return f"Error analyzing {coin.upper()}: {exc}"


async def _cmd_help(args: Optional[Dict[str, Any]] = None) -> str:
    """Return the list of available commands."""
    return (
        "**INXOTIVE CHANNELS COMMANDS**\n\n"
        "`/status`  — Cek status semua service\n"
        "`/market`  — Snapshot harga crypto\n"
        "`/brief`   — Analisis market lengkap (TradeX)\n"
        "`/analyze [coin]` — Analisis teknikal coin (default: btc)\n"
        "`/help`    — Tampilkan pesan ini\n"
        "`/ping`    — Pong!"
    )


async def _cmd_ping(args: Optional[Dict[str, Any]] = None) -> str:
    """Simple liveness check."""
    return "**Pong!** 🏓 INXOTIVE Channels aktif."


# Command registry  (lowercase, no leading slash)
_COMMAND_ROUTER: Dict[str, Any] = {
    "status": _cmd_status,
    "market": _cmd_market,
    "brief": _cmd_brief,
    "analyze": _cmd_analyze,
    "help": _cmd_help,
    "ping": _cmd_ping,
}


async def handle_discord_command(command: str, args: Optional[Dict[str, Any]] = None) -> str:
    """
    Process a Discord slash-style command and return a text response.

    Parameters
    ----------
    command : str — the command name, with or without a leading ``/``
                   (e.g. ``"/market"`` or ``"market"``).
    args    : dict, optional — additional arguments (e.g. ``{"coin": "eth"}``).

    Returns
    -------
    str — the text response to send back to Discord.
    """
    cmd = command.lstrip("/").strip().lower()
    handler = _COMMAND_ROUTER.get(cmd)
    if handler is None:
        return (
            f"Perintah `/{cmd}` tidak dikenal. "
            f"Ketik `/help` untuk daftar perintah yang tersedia."
        )

    try:
        result = await handler(args)
        return result
    except Exception as exc:
        logger.exception("Command /%s raised an exception", cmd)
        return f"Error menjalankan `/{cmd}`: {exc}"


# ---------------------------------------------------------------------------
# 5. process_discord_message
# ---------------------------------------------------------------------------

# Simple stop-words / agent-trigger detection
_AGENT_TRIGGERS = {
    "tradex": "tradex",
    "analisa": "tradex",
    "analisis": "tradex",
    "market": "tradex",
    "crypto": "tradex",
    "research": "researchx",
    "researchx": "researchx",
    "code": "opencode",
    "opencode": "opencode",
    "biz": "bizmind",
    "bizmind": "bizmind",
    "bisnis": "bizmind",
    "pharma": "dr_pharma",
    "dr pharma": "dr_pharma",
    "dokter": "dr_pharma",
    "obat": "dr_pharma",
    "farmasi": "dr_pharma",
    "web": "webdev",
    "webdev": "webdev",
    "website": "webdev",
    "flow": "flowbot",
    "flowbot": "flowbot",
}


def _detect_agent(content: str) -> Optional[str]:
    """Detect which INXOTIVE agent to route to based on message content."""
    lower = content.lower()
    for trigger, agent in _AGENT_TRIGGERS.items():
        if trigger in lower:
            return agent
    return None


async def _query_ollama(prompt: str, agent: str = "researchx") -> str:
    """Send a prompt to the local Ollama chat endpoint and return the reply."""
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                "http://localhost:8888/chat",
                json={
                    "messages": [{"role": "user", "content": prompt}],
                    "agent": agent,
                },
            )
            if resp.is_success:
                data = resp.json()
                return data.get("reply", "") or data.get("response", "") or "(tidak ada respons)"
            return f"Chat endpoint returned HTTP {resp.status_code}"
    except httpx.TimeoutException:
        return "⏱️ Waktu habis — model AI sedang sibuk, coba lagi nanti."
    except httpx.RequestError as exc:
        return f"Tidak bisa terhubung ke AI: {exc}"
    except Exception as exc:
        return f"Error querying AI: {exc}"


async def process_discord_message(content: str, author: str) -> Dict[str, Any]:
    """
    Process an incoming Discord message.

    - If the message starts with ``/``, it is treated as a command and routed
      to :func:`handle_discord_command`.
    - Otherwise the message is sent to the best-matching INXOTIVE AI agent
      (detected from content keywords) and the agent's reply is returned.

    Parameters
    ----------
    content : str — the raw message text from Discord.
    author  : str — the Discord username of the sender.

    Returns
    -------
    dict with keys:
        replied (bool) — whether an answer was produced.
        response (str) — the text to send back to Discord.
        source (str) — ``"command"``, ``"agent"``, or ``"none"``.
        agent (str | None) — the agent name if routed to one.
    """
    content = content.strip()
    if not content:
        return {"replied": False, "response": "", "source": "none", "agent": None}

    # Log inbound
    logger.info("Discord message from @%s: %.120s", author, content)
    _push_to_event_bus("discord", "inbound", f"@{author}: {content[:200]}")

    # ── Commands ──
    if content.startswith("/"):
        parts = content[1:].split(None, 1)
        cmd_str = parts[0] if parts else content[1:]
        rest = parts[1].strip() if len(parts) > 1 else ""
        args: Dict[str, str] = {}
        if rest:
            # Simple parsing: "coin=eth" or just a bare word assigned to "query"
            if "=" in rest:
                for pair in rest.split():
                    if "=" in pair:
                        k, v = pair.split("=", 1)
                        args[k.strip()] = v.strip()
            else:
                args["query"] = rest

        reply = await handle_discord_command(cmd_str, args)
        return {"replied": bool(reply), "response": reply, "source": "command", "agent": None}

    # ── Detect agent from content ──
    agent = _detect_agent(content)
    if agent:
        reply = await _query_ollama(content, agent=agent)
        return {"replied": bool(reply), "response": reply, "source": "agent", "agent": agent}

    # ── No agent match — route to default (researchx) ──
    reply = await _query_ollama(content, agent="researchx")
    return {"replied": bool(reply), "response": reply, "source": "agent", "agent": "researchx"}


# ---------------------------------------------------------------------------
# 6. forward_to_hub
# ---------------------------------------------------------------------------


async def forward_to_hub(event_type: str, payload: dict) -> bool:
    """
    Push an event into the INXOTIVE event bus and optionally notify connected
    SSE hub clients.

    The event is written to ``~/.event_bus.json`` (the same file used by
    ``app.py``'s ``push_event()``), so any component polling the bus will see
    it.  Future versions may push live SSE frames to hub subscribers directly.

    Parameters
    ----------
    event_type : str — a short event label (e.g. ``"price_alert"``,
                      ``"system_health"``, ``"discord_forward"``).
    payload    : dict — arbitrary JSON-serialisable data.

    Returns
    -------
    bool — ``True`` if the event was written successfully.
    """
    source = payload.get("source", "channels")
    severity = payload.get("severity", "info")
    message = payload.get("message", json.dumps(payload, ensure_ascii=False))

    try:
        _push_to_event_bus(source, event_type, message, severity)
        logger.info("Event forwarded to hub: %s — %.100s", event_type, message)

        # (Optional) In the future, push a live SSE frame to any connected hub
        # clients via an in-memory queue or Redis pub/sub.  The current
        # event-bus JSON file is sufficient for poll-based subscribers.
        # Example placeholder:
        #
        #   if _sse_clients:
        #       await asyncio.gather(
        #           *(client.put(f"data: {json.dumps({'event': event_type, 'payload': payload})}\n\n")
        #             for client in _sse_clients),
        #           return_exceptions=True,
        #       )

        return True
    except Exception as exc:
        logger.error("Failed to forward event to hub: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Convenience: combine all channel pushes for a single alert
# ---------------------------------------------------------------------------


async def alert_all_channels(
    message: str,
    severity: str = "info",
    event_type: str = "alert",
) -> Dict[str, Any]:
    """
    High-level helper: broadcast to all configured external channels AND push
    the event into the hub event bus in one call.

    Returns per-channel status dict (same shape as :func:`broadcast`).
    """
    # External channels
    chan_results = await broadcast(message)

    # Hub event bus
    await forward_to_hub(event_type, {
        "source": "alert",
        "severity": severity,
        "message": message,
    })

    return chan_results


# ---------------------------------------------------------------------------
# Module-level convenience: quick self-test when run directly
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[channels] %(levelname)s %(message)s")

    async def _selftest() -> None:
        print("=== Channels Self-Test ===\n")

        # 1. ping command
        print("1. /ping →", await handle_discord_command("ping"))

        # 2. /help
        print("\n2. /help →")
        print(await handle_discord_command("help"))

        # 3. /status
        print("\n3. /status →")
        print(await handle_discord_command("status"))

        # 4. /market
        print("\n4. /market →")
        print(await handle_discord_command("market"))

        # 5. forward_to_hub
        print("\n5. forward_to_hub (test event) →")
        ok = await forward_to_hub("selftest", {"source": "channels_selftest", "message": "Self-test OK", "severity": "info"})
        print(f"   Result: {ok}")
        print(f"   Event bus entries: ", end="")
        if EVENT_BUS_PATH.exists():
            events = json.loads(EVENT_BUS_PATH.read_text())
            print(f"{len(events)} total (last: {events[-1]['type']})")
        else:
            print("no file")

        # 6. process_discord_message
        print("\n6. process_discord_message('/status') →")
        result = await process_discord_message("/status", "selftest")
        print(f"   Source: {result['source']}, replied: {result['replied']}")
        print(f"   Response: {result['response'][:120]}...")

        print("\n=== Self-Test Complete ===")

    asyncio.run(_selftest())
