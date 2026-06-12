#!/usr/bin/env python3
"""
INXOTIVE Market Data MCP Server
================================
FastMCP server exposing crypto market data, technical analysis, news, and
sentiment analysis as MCP tools for use by Claude and AI agents.

Endpoints mirror the existing app.py market endpoints, hitting the same APIs
(CoinGecko, alternative.me, RSS feeds).

Run:
    python mcp_market_server.py

Transport: stdio (compatible with any MCP host / INXOTIVE HUB).
"""

from mcp.server.fastmcp import FastMCP
import requests
import json
import os
from datetime import datetime
import math
import asyncio
import re
import logging
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Logging — stderr so it doesn't contaminate stdio transport
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="[mcp-market] %(levelname)s %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Load NINE_ROUTER_API_KEY from ~/.env_secrets
# ---------------------------------------------------------------------------

ENV_SECRETS = Path.home() / ".env_secrets"
if ENV_SECRETS.exists():
    for line in ENV_SECRETS.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

NINE_ROUTER_API_KEY = os.environ.get("NINE_ROUTER_API_KEY", "")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

COIN_MAP = {
    "btc": "bitcoin",
    "eth": "ethereum",
    "bnb": "binancecoin",
    "sol": "solana",
    "xrp": "ripple",
}

COINS = ["bitcoin", "ethereum", "binancecoin", "solana", "ripple"]

COIN_LABELS = {
    "bitcoin": "BTC — Bitcoin",
    "ethereum": "ETH — Ethereum",
    "binancecoin": "BNB — BNB",
    "solana": "SOL — Solana",
    "ripple": "XRP — XRP",
}

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) INXOTIVE-MCP/1.0"

# ---------------------------------------------------------------------------
# MCP Instance
# ---------------------------------------------------------------------------

mcp = FastMCP("inxotive-market")

# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════


def safe_float(val):
    """Safely coerce value to float, returning None for NaN/None."""
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return None
    return round(float(val), 4)


def fmt_price(val, prefix="$"):
    """Format a large number human-readable (B/M/K)."""
    if val is None:
        return "N/A"
    try:
        v = float(val)
    except (TypeError, ValueError):
        return "N/A"
    if abs(v) >= 1_000_000_000:
        return f"{prefix}{v / 1e9:.2f}B"
    elif abs(v) >= 1_000_000:
        return f"{prefix}{v / 1e6:.2f}M"
    elif abs(v) >= 1_000:
        return f"{prefix}{v:,.0f}"
    return f"{prefix}{v:.2f}"


def fmt_change(val):
    """Format a percentage change with + / - sign."""
    if val is None:
        return "N/A"
    return f"{val:+.2f}%"


async def _fetch(url, **kwargs):
    """Async wrapper around requests.get using asyncio.to_thread."""
    defaults = {"timeout": 15, "headers": {"User-Agent": USER_AGENT}}
    # Merge kwargs, letting callers override defaults
    for k, v in kwargs.items():
        if k == "headers" and isinstance(v, dict):
            defaults["headers"].update(v)
        else:
            defaults[k] = v
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, lambda: requests.get(url, **defaults)
    )


async def _fetch_json(url, **kwargs):
    """GET and parse JSON, returning None on any failure."""
    try:
        resp = await _fetch(url, **kwargs)
        if resp.status_code >= 400:
            logger.warning("HTTP %d from %s", resp.status_code, url)
            return None
        return resp.json()
    except requests.RequestException as e:
        logger.warning("Request failed: %s — %s", url, e)
        return None
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning("JSON decode failed for %s: %s", url, e)
        return None


# ═══════════════════════════════════════════════════════════════════════════
# Technical Analysis Helpers (pure math, no pandas/ta dependency)
# ═══════════════════════════════════════════════════════════════════════════


def _ema(data, period):
    """Exponential Moving Average — standard recursive formula."""
    if len(data) < period:
        return None
    multiplier = 2.0 / (period + 1)
    # SMA seed
    result = sum(data[:period]) / period
    for price in data[period:]:
        result = (price - result) * multiplier + result
    return result


def _rsi(data, period=14):
    """Relative Strength Index (Wilder's)."""
    if len(data) < period + 1:
        return None
    gains = losses = 0.0
    for i in range(1, period + 1):
        diff = data[-i] - data[-i - 1]
        if diff >= 0:
            gains += diff
        else:
            losses += abs(diff)
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 2)


def _bollinger(data, period=20):
    """Bollinger Bands — returns (upper, middle, lower)."""
    if len(data) < period:
        return None, None, None
    sma = sum(data[-period:]) / period
    variance = sum((x - sma) ** 2 for x in data[-period:]) / period
    std = math.sqrt(variance)
    return sma + 2 * std, sma, sma - 2 * std


def _macd(data):
    """MACD — returns (macd_line, signal_line, histogram)."""
    if len(data) < 26:
        return None, None, None
    macd_values = []
    for i in range(26, len(data) + 1):
        chunk = data[:i]
        e12 = _ema(chunk, 12)
        e26 = _ema(chunk, 26)
        if e12 is not None and e26 is not None:
            macd_values.append(e12 - e26)
    if len(macd_values) < 9:
        return None, None, None
    macd_line = macd_values[-1]
    signal = _ema(macd_values, 9)
    if signal is None:
        return None, None, None
    return macd_line, signal, macd_line - signal


def _rsi_interpret(val):
    """Return a short interpretation string for an RSI value."""
    if val is None:
        return "N/A"
    if val >= 70:
        return "OVERBOUGHT — potential reversal down"
    elif val >= 55:
        return "Mildly overbought, trend strong"
    elif val >= 45:
        return "NEUTRAL — no extreme"
    elif val >= 30:
        return "Mildly oversold, potential bounce"
    else:
        return "OVERSOLD — potential reversal up"


async def get_ta(coin_id: str) -> dict:
    """Fetch 30-day daily price data and compute full technical analysis.

    Returns a dict with all indicators, or {"error": "..."} on failure.
    """
    data = await _fetch_json(
        f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"
        "?vs_currency=usd&days=30&interval=daily"
    )
    if not data or "prices" not in data:
        return {"error": f"Failed to fetch price data for {coin_id}"}

    prices = [p[1] for p in data["prices"]]
    if len(prices) < 14:
        return {"error": f"Insufficient price data ({len(prices)} points, need >= 14)"}

    rsi_val = _rsi(prices, 14)
    rsi_sig = _rsi_interpret(rsi_val)

    macd_line, macd_sig, macd_hist = _macd(prices)
    macd_dir = "BULLISH" if macd_hist and macd_hist > 0 else "BEARISH"

    bb_upper, bb_mid, bb_lower = _bollinger(prices, 20)
    ema20 = _ema(prices, 20)
    ema50 = _ema(prices, 50) if len(prices) >= 50 else None

    if ema20 is not None and ema50 is not None:
        trend = "BULLISH" if ema20 > ema50 else "BEARISH"
    else:
        trend = "N/A (insufficient data for EMA50)"

    current_price = prices[-1]
    bb_position = "ABOVE upper band"
    if bb_upper and current_price <= bb_upper:
        bb_position = "AT upper band"
    if bb_mid and current_price <= bb_mid:
        bb_position = "BETWEEN middle and upper"
    if bb_mid and current_price <= bb_mid - (bb_upper - bb_mid) * 0.3:
        bb_position = "NEAR middle band"
    if bb_lower and current_price <= bb_mid - (bb_upper - bb_mid) * 0.7:
        bb_position = "BETWEEN middle and lower"
    if bb_lower and current_price <= bb_lower:
        bb_position = "AT/BELOW lower band"

    return {
        "current_price": current_price,
        "rsi": rsi_val,
        "rsi_signal": rsi_sig,
        "macd_line": safe_float(macd_line),
        "macd_signal_line": safe_float(macd_sig),
        "macd_histogram": safe_float(macd_hist),
        "macd_direction": macd_dir,
        "bb_upper": safe_float(bb_upper),
        "bb_middle": safe_float(bb_mid),
        "bb_lower": safe_float(bb_lower),
        "bb_position": bb_position,
        "ema20": safe_float(ema20),
        "ema50": safe_float(ema50),
        "trend": trend,
        "support": round(min(prices[-14:]), 2),
        "resistance": round(max(prices[-14:]), 2),
        "change_7d": (
            round((prices[-1] / prices[-7] - 1) * 100, 2)
            if len(prices) >= 7
            else 0
        ),
        "change_30d": (
            round((prices[-1] / prices[0] - 1) * 100, 2)
            if len(prices) >= 30
            else 0
        ),
    }


def _format_ta_block(coin_label: str, ta: dict) -> str:
    """Format a TA result dict into a readable text block."""
    if "error" in ta:
        return (
            f"**{coin_label}**\n"
            f"  Technical analysis unavailable: {ta['error']}"
        )

    lines = [
        f"**{coin_label}**",
        f"  Price: ${ta.get('current_price', 0):,.2f}",
        f"  RSI(14): {ta.get('rsi', 'N/A')} — {ta.get('rsi_signal', 'N/A')}",
        f"  MACD: {ta.get('macd_direction', 'N/A')} "
        f"(Histogram: {ta.get('macd_histogram', 'N/A')})",
        f"  Bollinger: Upper ${ta.get('bb_upper', 'N/A'):,} "
        f"| Mid ${ta.get('bb_middle', 'N/A'):,} "
        f"| Lower ${ta.get('bb_lower', 'N/A'):,}",
        f"  Position: {ta.get('bb_position', 'N/A')}",
        f"  EMA20: ${ta.get('ema20', 'N/A'):,} "
        f"| EMA50: ${ta.get('ema50', 'N/A'):,}",
        f"  Trend: {ta.get('trend', 'N/A')}",
        f"  Support: ${ta.get('support', 'N/A'):,} "
        f"| Resistance: ${ta.get('resistance', 'N/A'):,}",
    ]
    if ta.get("change_7d") is not None:
        lines.append(
            f"  7d: {ta['change_7d']:+.2f}% "
            f"| 30d: {ta.get('change_30d', 0):+.2f}%"
        )
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════
# Tools
# ═══════════════════════════════════════════════════════════════════════════


@mcp.tool()
async def get_market_prices() -> str:
    """Get current crypto prices: BTC, ETH, BNB, SOL, XRP in USD/IDR with 24h change, market cap, and volume."""
    logger.info("Fetching market prices...")

    try:
        # Fetch prices from CoinGecko
        url = (
            "https://api.coingecko.com/api/v3/simple/price"
            f"?ids={','.join(COINS)}"
            "&vs_currencies=usd,idr"
            "&include_24hr_change=true"
            "&include_market_cap=true"
            "&include_24hr_vol=true"
        )
        data = await _fetch_json(url)
        if not data:
            return (
                "**Error:** Failed to fetch market prices from CoinGecko.\n"
                "Possible reasons: CoinGecko API rate limited or network issue."
            )

        now = datetime.now().strftime("%Y-%m-%d %H:%M WIB")
        lines = [
            f"## Crypto Market Prices",
            f"*Updated: {now}*\n",
        ]

        for coin_id in COINS:
            info = data.get(coin_id, {})
            label = COIN_LABELS.get(coin_id, coin_id.title())
            usd = info.get("usd", 0)
            idr = info.get("idr", 0)
            change = info.get("usd_24h_change")
            mc = info.get("usd_market_cap")
            vol = info.get("usd_24h_vol")

            change_str = fmt_change(change)
            mc_str = fmt_price(mc, "$")
            vol_str = fmt_price(vol, "$")

            lines.append(f"**{label}**")
            lines.append(f"  Price: ${usd:,.2f} / Rp {idr:,.0f}")
            lines.append(f"  24h Change: {change_str}")
            lines.append(f"  Market Cap: {mc_str} | Volume 24h: {vol_str}")
            lines.append("")

        return "\n".join(lines).strip()

    except Exception as e:
        logger.exception("get_market_prices error")
        return f"**Error:** Unexpected error fetching market prices: {e}"


@mcp.tool()
async def get_technical_analysis(coin: str = "bitcoin") -> str:
    """Get technical analysis for a coin (bitcoin, ethereum, solana, ripple, binancecoin, or short codes like btc/eth/sol)."""
    # Normalize coin name
    coin_id = COIN_MAP.get(coin.lower().strip(), coin.lower().strip())
    label = COIN_LABELS.get(coin_id, coin_id.upper())

    logger.info("Fetching TA for %s (coin_id=%s)", coin, coin_id)

    try:
        ta = await get_ta(coin_id)
        if "error" in ta:
            return (
                f"**Technical Analysis — {label}**\n"
                f"\n"
                f"  Error: {ta['error']}\n"
                f"\n"
                f"Suggestions:\n"
                f"  - Check the coin name (try: bitcoin, ethereum, solana, ripple, bnb)\n"
                f"  - CoinGecko may be rate-limited — try again in a minute"
            )

        now = datetime.now().strftime("%Y-%m-%d %H:%M WIB")
        block = _format_ta_block(label, ta)

        # Price position context
        price = ta.get("current_price", 0)
        bb_upper = ta.get("bb_upper")
        bb_lower = ta.get("bb_lower")
        support = ta.get("support")
        resistance = ta.get("resistance")

        context_lines = ["", "**Market Context:**"]
        if support and price:
            dist_to_support = ((price - support) / price) * 100
            context_lines.append(
                f"  Price is {dist_to_support:.1f}% above support (${support:,})"
            )
        if resistance and price:
            dist_to_resistance = ((resistance - price) / price) * 100
            context_lines.append(
                f"  Price is {dist_to_resistance:.1f}% below resistance (${resistance:,})"
            )
        if bb_upper and bb_lower:
            bb_range = bb_upper - bb_lower
            bb_pos = ((price - bb_lower) / bb_range) * 100 if bb_range > 0 else 50
            context_lines.append(
                f"  Bollinger Band position: {bb_pos:.0f}% from lower to upper band"
            )

        return (
            f"## Technical Analysis — {label}\n"
            f"*30-day daily data | Updated: {now}*\n"
            f"\n"
            f"{block}\n"
            f"{chr(10).join(context_lines)}"
        )

    except Exception as e:
        logger.exception("get_technical_analysis error for %s", coin)
        return f"**Error:** Unexpected error analyzing {coin}: {e}"


@mcp.tool()
async def get_crypto_brief() -> str:
    """Get full crypto market brief: prices, technical analysis for BTC/ETH, fear-greed index, news summary, and trending coins — all in one comprehensive report."""
    logger.info("Compiling full crypto brief...")

    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M WIB")
        lines = [
            f"# INXOTIVE Crypto Market Brief",
            f"*Generated: {now}*\n",
        ]

        # ── Market Prices ──
        lines.append("## Market Prices")
        price_url = (
            "https://api.coingecko.com/api/v3/simple/price"
            f"?ids={','.join(COINS)}"
            "&vs_currencies=usd,idr"
            "&include_24hr_change=true"
            "&include_market_cap=true"
            "&include_24hr_vol=true"
        )
        price_data = await _fetch_json(price_url)
        if price_data:
            for coin_id in COINS:
                info = price_data.get(coin_id, {})
                label = COIN_LABELS.get(coin_id, coin_id.title())
                usd = info.get("usd", 0)
                change = info.get("usd_24h_change")
                mc = info.get("usd_market_cap")
                vol = info.get("usd_24h_vol")

                lines.append(
                    f"  **{label}:** ${usd:,.2f}  "
                    f"| {fmt_change(change)}  "
                    f"| MC: {fmt_price(mc, '$')}  "
                    f"| Vol: {fmt_price(vol, '$')}"
                )
        else:
            lines.append("  *Market prices unavailable*")
        lines.append("")

        # ── Fear & Greed ──
        lines.append("## Fear & Greed Index")
        fg_data = await _fetch_json(
            "https://api.alternative.me/fng/?limit=7"
        )
        if fg_data and "data" in fg_data:
            fg_list = fg_data["data"]
            current_fg = fg_list[0]
            fg_val = current_fg.get("value", "?")
            fg_class = current_fg.get("value_classification", "Unknown")
            fg_timestamp = datetime.fromtimestamp(
                int(current_fg.get("timestamp", 0))
            ).strftime("%Y-%m-%d") if current_fg.get("timestamp") else "?"

            lines.append(f"  **Current:** {fg_val}/100 — {fg_class}")
            lines.append(f"  **Date:** {fg_timestamp}")

            # 7-day trend
            if len(fg_list) >= 2:
                values = [int(h.get("value", 50)) for h in fg_list[:7]]
                trend_arrow = (
                    "membaik (rising)"
                    if values[0] > values[-1]
                    else "memburuk (falling)"
                    if values[0] < values[-1]
                    else "stabil"
                )
                trend_str = " → ".join(str(v) for v in reversed(values))
                lines.append(f"  **7-Day Trend:** {trend_str}")
                lines.append(f"  **Sentiment:** {trend_arrow}")
        else:
            lines.append("  *Fear & Greed unavailable*")
        lines.append("")

        # ── Technical Analysis (BTC & ETH) ──
        lines.append("## Technical Analysis")
        for coin_id in ["bitcoin", "ethereum"]:
            ta = await get_ta(coin_id)
            label = COIN_LABELS.get(coin_id, coin_id.title())
            lines.append(_format_ta_block(label, ta))
            lines.append("")
        lines.append("")

        # ── Trending Coins ──
        lines.append("## Trending Coins")
        trending_data = await _fetch_json(
            "https://api.coingecko.com/api/v3/search/trending"
        )
        if trending_data and "coins" in trending_data:
            trending_coins = [
                c["item"]
                for c in trending_data["coins"][:7]
            ]
            for i, c in enumerate(trending_coins, 1):
                name = c.get("name", "?")
                symbol = c.get("symbol", "?").upper()
                rank = c.get("market_cap_rank", "N/A")
                score = c.get("score", 0)
                lines.append(
                    f"  {i}. **{name}** ({symbol}) "
                    f"— Rank #{rank} | Score: {score}"
                )
        else:
            lines.append("  *Trending data unavailable*")
        lines.append("")

        # ── Latest News ──
        lines.append("## Latest News")
        news_items = await _fetch_news_items(5)
        if news_items:
            for i, item in enumerate(news_items, 1):
                title = item.get("title", "?")
                source = item.get("source", "?")
                lines.append(f"  {i}. **{title}**")
                lines.append(f"     Source: {source}")
                if item.get("description"):
                    lines.append(f"     {item['description'][:150]}")
                lines.append("")
        else:
            lines.append("  *News unavailable*")
        lines.append("")

        # ── Summary ──
        lines.append("---")
        lines.append(
            "*Data sourced from CoinGecko, alternative.me, "
            "CoinTelegraph, and CoinDesk.*"
        )

        return "\n".join(lines).strip()

    except Exception as e:
        logger.exception("get_crypto_brief error")
        return f"**Error:** Failed to compile crypto brief: {e}"


async def _fetch_news_items(limit: int = 5):
    """Fetch latest crypto news from RSS feeds."""
    news = []
    feeds = [
        "https://cointelegraph.com/rss",
        "https://coindesk.com/arc/outboundfeeds/rss/",
    ]
    try:
        for feed_url in feeds:
            resp = await _fetch(feed_url, timeout=10)
            if resp.status_code >= 400:
                continue
            try:
                root = ET.fromstring(resp.content)
            except ET.ParseError:
                continue
            items = root.findall(".//item")[:limit]
            for item in items:
                title_el = item.find("title")
                desc_el = item.find("description")
                link_el = item.find("link")
                pub_date_el = item.find("pubDate")

                title = (
                    re.sub(r"<[^>]+>", "", title_el.text or "").strip()
                    if title_el is not None
                    else ""
                )
                if not title:
                    continue

                desc = (
                    re.sub(r"<[^>]+>", "", (desc_el.text or ""))[:200]
                    if desc_el is not None
                    else ""
                )
                news.append({
                    "title": title,
                    "description": desc,
                    "url": link_el.text.strip() if link_el is not None and link_el.text else "",
                    "source": feed_url.split("/")[2],
                    "published": pub_date_el.text.strip() if pub_date_el is not None and pub_date_el.text else "",
                })
            if len(news) >= limit:
                break
    except Exception as e:
        logger.warning("News fetch error: %s", e)
    return news[:limit]


@mcp.tool()
async def get_fear_greed_index() -> str:
    """Get Crypto Fear & Greed Index with 7-day trend and historical values."""
    logger.info("Fetching Fear & Greed Index...")

    try:
        data = await _fetch_json(
            "https://api.alternative.me/fng/?limit=7"
        )
        if not data or "data" not in data or not data["data"]:
            return (
                "**Fear & Greed Index**\n"
                "\n"
                "  Data unavailable. The alternative.me API may be down."
            )

        fg_list = data["data"]
        current = fg_list[0]
        fg_val = current.get("value", "?")
        fg_class = current.get("value_classification", "Unknown")
        fg_ts = current.get("timestamp", "")

        date_str = (
            datetime.fromtimestamp(int(fg_ts)).strftime("%Y-%m-%d")
            if fg_ts
            else "?"
        )

        lines = [
            "## Crypto Fear & Greed Index\n",
            f"**Current Value:** {fg_val}/100 — {fg_class}",
            f"**Date:** {date_str}\n",
        ]

        # 7-day history
        if len(fg_list) >= 2:
            history = []
            for h in fg_list[:7]:
                val = h.get("value", "?")
                cls = h.get("value_classification", "")
                ts = h.get("timestamp", "")
                d = (
                    datetime.fromtimestamp(int(ts)).strftime("%d %b")
                    if ts
                    else "?"
                )
                history.append((d, val, cls))

            lines.append("**7-Day History:**")
            for d, val, cls in history:
                arrow = "🟢" if int(val) >= 50 else "🔴"
                lines.append(f"  {arrow} {d}: **{val}** — {cls}")
            lines.append("")

            # Trend analysis
            values = [int(h.get("value", 50)) for h in fg_list[:7]]
            first, last = values[-1], values[0]
            diff = last - first
            if diff > 3:
                trend = "Significantly improving — market sentiment is getting more greedy"
            elif diff > 0:
                trend = "Slightly improving"
            elif diff < -3:
                trend = "Significantly declining — fear is increasing"
            elif diff < 0:
                trend = "Slightly declining"
            else:
                trend = "Stable"

            avg_val = sum(values) / len(values)
            lines.append(f"**Trend:** {trend}")
            lines.append(f"**7-Day Average:** {avg_val:.0f}/100")
            lines.append(f"**Range:** {min(values)} – {max(values)}")

        # Classification legend
        lines.extend([
            "",
            "**Classification Legend:**",
            "  0-25: Extreme Fear (market panic)",
            "  26-46: Fear (caution)",
            "  47-54: Neutral",
            "  55-75: Greed (optimism)",
            "  76-100: Extreme Greed (possible top)",
        ])

        return "\n".join(lines)

    except Exception as e:
        logger.exception("get_fear_greed_index error")
        return f"**Error:** Failed to fetch Fear & Greed Index: {e}"


@mcp.tool()
async def get_crypto_news(limit: int = 5) -> str:
    """Get latest crypto news from CoinTelegraph and CoinDesk RSS feeds.

    Args:
        limit: Number of news items to return (max 10).
    """
    limit = max(1, min(limit, 10))
    logger.info("Fetching %d crypto news items...", limit)

    try:
        news = await _fetch_news_items(limit)

        if not news:
            return (
                "## Latest Crypto News\n"
                "\n"
                "  No news available at this time. RSS feeds may be temporarily "
                "unreachable.\n"
                "\n"
                "Suggestions:\n"
                "  - Try again in a few minutes\n"
                "  - Check connectivity: `curl https://cointelegraph.com/rss`"
            )

        now = datetime.now().strftime("%Y-%m-%d %H:%M WIB")
        lines = [
            f"## Latest Crypto News",
            f"*Fetched: {now}*\n",
        ]

        for i, item in enumerate(news, 1):
            title = item.get("title", "Untitled")
            source = item.get("source", "Unknown")
            desc = item.get("description", "")
            url = item.get("url", "")
            published = item.get("published", "")

            lines.append(f"**{i}. {title}**")
            lines.append(f"   {source.upper()}" + (f" | {published}" if published else ""))
            if desc:
                desc_clean = re.sub(r"\s+", " ", desc).strip()
                lines.append(f"   {desc_clean}")
            if url:
                lines.append(f"   {url}")
            lines.append("")

        lines.append(
            "---\n"
            "*Sources: CoinTelegraph, CoinDesk*"
        )

        return "\n".join(lines).strip()

    except Exception as e:
        logger.exception("get_crypto_news error")
        return f"**Error:** Failed to fetch crypto news: {e}"


@mcp.tool()
async def get_trending_coins() -> str:
    """Get trending coins on CoinGecko — top 10 most searched coins right now with market data."""
    logger.info("Fetching trending coins...")

    try:
        data = await _fetch_json(
            "https://api.coingecko.com/api/v3/search/trending"
        )
        if not data or "coins" not in data:
            return (
                "## Trending Coins\n"
                "\n"
                "  Data unavailable. CoinGecko API may be rate-limited.\n"
                "\n"
                "  Try again in a minute."
            )

        coins = data["coins"]
        if not coins:
            return (
                "## Trending Coins\n"
                "\n"
                "  No trending data returned by CoinGecko."
            )

        now = datetime.now().strftime("%Y-%m-%d %H:%M WIB")
        lines = [
            f"## Trending Coins on CoinGecko",
            f"*Fetched: {now}*\n",
        ]

        for i, c in enumerate(coins[:10], 1):
            item = c.get("item", {})
            name = item.get("name", "?")
            symbol = item.get("symbol", "?").upper()
            rank = item.get("market_cap_rank", "N/A")
            score = item.get("score", 0)
            price_btc = item.get("price_btc")
            thumb = item.get("thumb", "")

            # slug for more context
            slug = item.get("slug", "")

            lines.append(
                f"**{i}. {name}** ({symbol})"
            )
            lines.append(f"   Market Cap Rank: #{rank}")

            if price_btc is not None:
                # Convert BTC price to approximate USD
                lines.append(f"   Price: {price_btc:.8f} BTC")

            lines.append("")

        # Also show category breakdown
        if len(coins) > 10:
            lines.append(
                f"*{len(coins)} coins total in trending list*"
            )
        lines.append("")
        lines.append(
            "---\n"
            "*Data from CoinGecko trending search*"
        )

        return "\n".join(lines).strip()

    except Exception as e:
        logger.exception("get_trending_coins error")
        return f"**Error:** Failed to fetch trending coins: {e}"


# ═══════════════════════════════════════════════════════════════════════════
# Entry Point
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logger.info("Starting INXOTIVE Market MCP Server (stdio)...")
    mcp.run(transport="stdio")
